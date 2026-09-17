"""Report perception — read photographed/scanned clinical documents.

Pathology reports, NGS panels, PD-L1 assays and written imaging reports are
how real cases arrive. This reader lets a user drop those images straight
into a run and have the structured facts proposed in seconds instead of
transcribed by hand — the acceleration — while keeping every safety property
of the imaging path:

* Extraction only ever **proposes**; the deterministic engine still stages,
  the rule engine still checks, and nothing model-read is treated as ground
  truth.
* TNM mentions are vocabulary-validated exactly like film reads.
* Facts are seeded ONLY where the case is missing them; an existing fact is
  cross-checked and a mismatch flagged (``REPORT_DISCORDANCE``), never
  overwritten.
* Every seeded field is recorded in ``facts["_report_proposed"]`` — and the
  runner keeps the dose channel shut while any Tier-A biomarker rests on a
  report proposal, because OCR of a photographed NGS report is not a
  confirmed molecular result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..llm.base import LLMError, extract_json
from .imaging import ImagingError, load_image_refs, validate_descriptor

#: Marker the offline mock keys on (parallel to IMAGING DESCRIPTOR EXTRACTION).
REPORT_MARKER = "CLINICAL DOCUMENT EXTRACTION"

REPORT_EXTRACTION_PROMPT = f"""\
=== {REPORT_MARKER} (NSCLC, educational pipeline) ===

You are shown photographed or scanned CLINICAL DOCUMENTS for one patient —
pathology reports, molecular/NGS panels, PD-L1 assays, or written radiology
reports. Extract the structured facts they state.

YOUR ROLE — and its hard limits:
  • TRANSCRIBE what the documents state; do NOT infer beyond the text.
  • You DO NOT assign a stage group and DO NOT recommend treatment.
  • Anything unreadable or absent → null, plus an "uncertainties" line naming
    what is missing.
  • Everything you output is MODEL-PROPOSED and UNVERIFIED pending the
    original documents.

For TNM descriptors use the exact 9th-edition vocabulary (T1a…T4 / N0,N1,
N2a,N2b,N3 / M0,M1a,M1b,M1c1,M1c2) or null; never bare "N2"/"T2".
For driver genes quote the report's own wording (e.g. "EGFR exon 19
deletion detected", "ALK: negative", "未检出突变").

Respond with ONLY a JSON object:
{{"document_types": ["pathology|molecular|pd_l1|imaging_report|lab|other"],
 "histologic_category": "adenocarcinoma|squamous|adenosquamous|NSCLC_NOS|other" | null,
 "driver_mutations": {{"egfr": "<verbatim>" | null, "alk": …, "ros1": …,
                      "kras": …, "braf": …, "met": …, "ret": …, "her2": …}},
 "pd_l1": {{"tps": <int> | null, "tc": <int> | null, "assay": "<name>" | null}},
 "candidate_t": …, "candidate_n": …, "candidate_m": …,
 "specimen": "<biopsy/resection source>" | null,
 "report_dates": ["YYYY-MM-DD", …],
 "key_findings": ["<short verbatim-anchored finding>", …],
 "uncertainties": ["<what is unreadable/missing>", …]}}
"""

_GENES = ("egfr", "alk", "ros1", "kras", "braf", "met", "ret", "her2")


@dataclass
class ReportFindings:
    """Model-PROPOSED, UNVERIFIED facts read from clinical documents."""

    document_types: list[str] = field(default_factory=list)
    histologic_category: Optional[str] = None
    driver_mutations: dict[str, str] = field(default_factory=dict)
    pd_l1: dict[str, Any] = field(default_factory=dict)
    candidate_t: Optional[str] = None
    candidate_n: Optional[str] = None
    candidate_m: Optional[str] = None
    specimen: Optional[str] = None
    report_dates: list[str] = field(default_factory=list)
    key_findings: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    rejected_descriptors: list[str] = field(default_factory=list)
    provider: Optional[str] = None
    model: Optional[str] = None
    n_images: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "MODEL_PROPOSED_UNVERIFIED",
            "requires_confirmation": True,
            "document_types": self.document_types,
            "histologic_category": self.histologic_category,
            "driver_mutations": self.driver_mutations,
            "pd_l1": self.pd_l1,
            "candidate_t": self.candidate_t,
            "candidate_n": self.candidate_n,
            "candidate_m": self.candidate_m,
            "specimen": self.specimen,
            "report_dates": self.report_dates,
            "key_findings": self.key_findings,
            "uncertainties": self.uncertainties,
            "rejected_descriptors": self.rejected_descriptors,
            "read_by": {"provider": self.provider, "model": self.model,
                        "images": self.n_images},
        }


class ReportReader:
    """Reads document images through a vision-capable client."""

    def __init__(self, llm: Any):
        if llm is None or not getattr(llm, "available", False):
            raise ImagingError("no model configured for report reading")
        if not getattr(llm, "supports_vision", False):
            raise ImagingError(
                f"provider {getattr(llm, 'name', '?')!r} is not vision-capable; "
                f"refusing to send document images to a text model")
        self.llm = llm

    def read(
        self,
        images: list[str],
        *,
        context: str = "",
        base_dir: Path | None = None,
        budget: Any | None = None,
    ) -> ReportFindings:
        if not images:
            raise ImagingError("no document images provided to the report reader")
        urls = load_image_refs(images, base_dir=base_dir)
        if budget is not None and not budget.reserve_llm():
            raise ImagingError("LLM budget exhausted before report reading")
        user_parts: list[dict[str, Any]] = [{
            "type": "text",
            "text": "Extract the structured facts from the attached "
                    "document(s) and return the JSON described above."
                    + (f"\n\nClinical context (may be incomplete):\n"
                       f"{context.strip()}" if context else ""),
        }]
        user_parts += [{"type": "image_url", "image_url": {"url": u}} for u in urls]
        try:
            response = self.llm.chat(
                [
                    {"role": "system", "content": REPORT_EXTRACTION_PROMPT},
                    {"role": "user", "content": user_parts},
                ],
                temperature=0.0, max_tokens=1800,
            )
        except LLMError as exc:
            if budget is not None:
                budget.refund_llm()
            raise ImagingError(f"report reading failed: {exc}") from exc
        if budget is not None:
            budget.charge_llm_tokens(response.total_tokens)
        payload = extract_json(response.text, None)
        if not isinstance(payload, dict):
            raise ImagingError(
                f"could not parse report findings from model reply: "
                f"{response.text[:200]!r}")
        return self._to_findings(payload, response.provider, response.model,
                                 len(urls))

    @staticmethod
    def _to_findings(data: dict[str, Any], provider: str, model: str,
                     n_images: int) -> ReportFindings:
        rejected: list[str] = []
        canon: dict[str, Optional[str]] = {}
        for kind in ("t", "n", "m"):
            value, rejection = validate_descriptor(
                kind, data.get(f"candidate_{kind}"))
            canon[kind] = value
            if rejection:
                rejected.append(rejection)

        def _text(value: Any) -> Optional[str]:
            text = str(value).strip() if value is not None else ""
            return text if text and text.lower() not in (
                "null", "none", "n/a", "na") else None

        drivers_raw = data.get("driver_mutations") or {}
        drivers = {
            gene: _text(drivers_raw.get(gene))
            for gene in _GENES if _text(drivers_raw.get(gene))
        }
        pd_l1_raw = data.get("pd_l1") or {}
        pd_l1: dict[str, Any] = {}
        for key in ("tps", "tc"):
            value = pd_l1_raw.get(key)
            if isinstance(value, (int, float)):
                pd_l1[key] = value
        if _text(pd_l1_raw.get("assay")):
            pd_l1["assay"] = _text(pd_l1_raw.get("assay"))

        return ReportFindings(
            document_types=[str(d) for d in data.get("document_types") or []],
            histologic_category=_text(data.get("histologic_category")),
            driver_mutations=drivers,
            pd_l1=pd_l1,
            candidate_t=canon["t"], candidate_n=canon["n"], candidate_m=canon["m"],
            specimen=_text(data.get("specimen")),
            report_dates=[str(d) for d in data.get("report_dates") or []],
            key_findings=[str(k) for k in data.get("key_findings") or []][:12],
            uncertainties=[str(u) for u in data.get("uncertainties") or []],
            rejected_descriptors=rejected,
            provider=provider, model=model, n_images=n_images,
        )


def _histology_matches(a: str, b: str) -> bool:
    """Equivalent-phrasing tolerance: equality or containment either way."""
    left = " ".join(str(a).lower().split())
    right = " ".join(str(b).lower().split())
    return left == right or left in right or right in left


def fold_report_facts(
    facts: dict[str, Any], findings: ReportFindings
) -> tuple[list[str], list[str]]:
    """Fold report-proposed facts into the case facts, safely.

    Missing facts are seeded (and tracked in ``facts["_report_proposed"]``);
    existing facts are only cross-checked, with mismatches flagged. Returns
    ``(seeded_field_paths, flags)``.
    """
    from ..knowledge.biomarkers import driver_status

    seeded: list[str] = []
    flags: list[str] = list(findings.rejected_descriptors)

    # Histology. Comparison is containment-normalized: "lung adenocarcinoma"
    # and "adenocarcinoma" are the same statement, not a discordance.
    have_hist = str(facts.get("histologic_category") or "").strip()
    if findings.histologic_category:
        if not have_hist:
            facts["histologic_category"] = findings.histologic_category
            seeded.append("histologic_category")
        elif not _histology_matches(have_hist, findings.histologic_category):
            flags.append(
                f"REPORT_DISCORDANCE[histology]: case says {have_hist} but the "
                f"report reads {findings.histologic_category} — the case value "
                f"is used; reconcile before use.")

    # Driver genes: seed only where the case has no interpretable status.
    drivers = facts.setdefault("driver_mutations", {})
    for gene, verbatim in findings.driver_mutations.items():
        have = drivers.get(gene)
        if driver_status(have) == "unknown":
            drivers[gene] = verbatim
            seeded.append(f"driver_mutations.{gene}")
        elif driver_status(have) != driver_status(verbatim):
            flags.append(
                f"REPORT_DISCORDANCE[{gene.upper()}]: case reads "
                f"{driver_status(have)} but the report reads "
                f"{driver_status(verbatim)} ({verbatim!r}) — the case value is "
                f"used; reconcile before use.")

    # PD-L1: seed missing values; an existing value that disagrees with the
    # report is a discordance, exactly like histology and drivers.
    pd_l1 = facts.setdefault("pd_l1", {})
    for key in ("tps", "tc", "assay"):
        if key not in findings.pd_l1:
            continue
        have = pd_l1.get(key)
        if have is None:
            pd_l1[key] = findings.pd_l1[key]
            seeded.append(f"pd_l1.{key}")
        elif str(have).strip().lower() != str(findings.pd_l1[key]).strip().lower():
            flags.append(
                f"REPORT_DISCORDANCE[pd_l1.{key}]: case says {have} but the "
                f"report reads {findings.pd_l1[key]} — the case value is "
                f"used; reconcile before use.")

    if seeded:
        proposed = facts.setdefault("_report_proposed", [])
        for field_path in seeded:
            if field_path not in proposed:
                proposed.append(field_path)
        flags.append(
            "REPORT_FACT_PROPOSED: fields seeded from uploaded document(s) ("
            + ", ".join(seeded)
            + ") — model-read, UNVERIFIED; the dose channel stays closed on "
            "these until the source documents are confirmed.")
    return seeded, flags
