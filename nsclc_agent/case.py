"""Case input model.

A case carries the referrer's facts — TNM descriptors with their
classification prefix, biomarkers, fitness, comorbidities — plus the free-text
presentation and the question. Facts here are *observed* evidence; anything a
model proposes later (a vision read, an extracted fact) is folded in through
the controlled ingestion paths, never written silently.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Case:
    """A single NSCLC case to be staged, routed and reasoned about."""

    case_id: Optional[str] = None
    t: Optional[str] = None
    n: Optional[str] = None
    m: Optional[str] = None
    #: Classification prefix for the TNM triple: c / p / yp / yc / r / a.
    tnm_prefix: str = "c"
    stage_group: Optional[str] = None
    staging_system: str = "AJCC9"
    presentation: str = ""
    question: str = ""
    #: Radiology film references (paths / data: URLs / https URLs) for the
    #: perception layer. Never the bytes themselves.
    images: list[str] = field(default_factory=list)
    #: Photographed/scanned clinical documents (pathology, NGS, PD-L1,
    #: written imaging reports) for the report reader.
    reports: list[str] = field(default_factory=list)
    #: Structured facts: driver_mutations, pd_l1, ecog_ps, comorbidities,
    #: medications, histologic_category, workup {...}, resectability…
    facts: dict[str, Any] = field(default_factory=dict)

    _KNOWN = {
        "case_id", "t", "n", "m", "tnm_prefix", "stage_group", "staging_system",
        "presentation", "question", "images", "reports",
    }
    _ALIASES = {
        "id": "case_id",
        "t_category": "t",
        "n_category": "n",
        "m_category": "m",
        "prefix": "tnm_prefix",
    }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Case":
        core: dict[str, Any] = {k: data[k] for k in cls._KNOWN if k in data}
        for alias, target in cls._ALIASES.items():
            if alias in data and target not in core:
                core[target] = data[alias]
        facts = {
            k: v for k, v in data.items()
            if k not in cls._KNOWN and k not in cls._ALIASES
        }
        # `facts`/`fields` nested blocks merge into the flat facts dict.
        for nested_key in ("facts", "fields"):
            nested = facts.pop(nested_key, None)
            if isinstance(nested, dict):
                facts.update(nested)
        core.setdefault("staging_system", "AJCC9")
        for key in ("images", "reports"):
            if isinstance(core.get(key), str):
                core[key] = [core[key]]
            elif core.get(key) is None:
                core.pop(key, None)
        return cls(facts=facts, **core)

    # ------------------------------------------------------------------ helpers
    def has_images(self) -> bool:
        return bool(self.images or self.reports)

    def has_tnm(self) -> bool:
        return bool(self.t and self.n)

    def narrative(self) -> str:
        """Everything textual the screens and the interview read."""
        parts = [self.presentation or ""]
        if self.question:
            parts.append(self.question)
        return "\n".join(p for p in parts if p)

    def render_user_message(self) -> str:
        """Compose the user turn: presentation + structured facts + question."""
        parts: list[str] = []
        if self.presentation:
            parts.append(self.presentation.strip())
        if self.facts:
            parts.append(
                "Structured case data:\n"
                + json.dumps(self.facts, ensure_ascii=False, indent=2)
            )
        if self.question:
            parts.append(self.question.strip())
        if not parts:
            parts.append(
                "Provide the evidence-based decision-support analysis for this "
                "case following the module framework and output schema."
            )
        return "\n\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "t": self.t, "n": self.n, "m": self.m,
            "tnm_prefix": self.tnm_prefix,
            "stage_group": self.stage_group,
            "staging_system": self.staging_system,
            "presentation": self.presentation,
            "question": self.question,
            "images": list(self.images),
            "reports": list(self.reports),
            "facts": dict(self.facts),
        }
