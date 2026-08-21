"""Skills: simultaneously the *authorisation* the broker enforces and the
*procedure* the model reads.

A skill declares which tools an agent may call (``allowed_tools`` minus
``forbidden_tools``), the output contract (``output_schema``), whether the
agent runs as a model-driven tool loop (``autonomous``) and the instructions
that become its system prompt. An agent with no skill, or a skill not in the
registry, has **no tool rights** — fail closed. An empty ``allowed_tools``
means "no tools", never "all tools".

The table is code rather than YAML so the stdlib-only property of the core
holds; a deployment can overlay site-specific skills from a JSON file, and a
same-id overlay *replaces* the built-in, so a hospital can pin its own
protocol without forking the package.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class SkillSpec:
    skill_id: str
    description: str
    allowed_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    output_schema: str = ""
    #: Which run output key this skill's agent writes, for contract validation.
    output_key: str = ""
    autonomous: bool = False
    #: Roles that may run this skill at all.
    roles: tuple[str, ...] = ("patient", "oncologist", "researcher")
    instructions: str = ""

    def tool_names(self) -> set[str]:
        return set(self.allowed_tools) - set(self.forbidden_tools)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id, "description": self.description,
            "allowed_tools": list(self.allowed_tools),
            "forbidden_tools": list(self.forbidden_tools),
            "output_schema": self.output_schema, "output_key": self.output_key,
            "autonomous": self.autonomous, "roles": list(self.roles),
            "instructions": self.instructions,
        }


_REASONING_TOOLS = (
    "stage_lookup", "trial_lookup", "regimen_lookup", "protocol_lookup",
    "guideline_lookup", "pubmed_search", "citation_verify", "interaction_check",
)

BUILTIN_SKILLS: tuple[SkillSpec, ...] = (
    SkillSpec(
        "nsclc.intake",
        "Parse the case, run the oncologic-emergency screen, set the risk mode.",
        allowed_tools=(), output_schema="IntakeSummary", output_key="intake",
    ),
    SkillSpec(
        "nsclc.interview",
        "Active history/workup enquiry: compose questions that close open axes.",
        allowed_tools=(), output_schema="InterviewProgress", output_key="interview",
    ),
    SkillSpec(
        "nsclc.perception",
        "Read radiology films into PROPOSED cTNM descriptors. Never assigns a "
        "stage; proposals are validated against the descriptor vocabulary and "
        "cross-checked against human/pathologic values.",
        allowed_tools=(), output_schema="ImagingFindings", output_key="imaging",
    ),
    SkillSpec(
        "nsclc.staging",
        "Deterministic AJCC/UICC 9th-edition staging. The engine assigns the "
        "stage group; no model is involved.",
        allowed_tools=("stage_lookup",), output_key="staging",
    ),
    SkillSpec(
        "nsclc.treatment",
        "Stage-routed treatment reasoning inside the verified stage. Gather "
        "evidence through tools, cite evidence ids, name regimens by "
        "regimen_id only — never write dose numerics.",
        allowed_tools=_REASONING_TOOLS,
        forbidden_tools=("regimen_detail", "dose_gate_check"),
        output_schema="TreatmentPlan", output_key="treatment_plan",
        autonomous=True,
        instructions=(
            "You reason WITHIN the deterministic stage given in your context — "
            "never re-derive or override it. Work histology-first, then "
            "Tier-A biomarkers (EGFR/ALK/PD-L1), then stage-appropriate "
            "options. Anchor every regimen to its trial via trial_lookup and "
            "cite the returned evidence ids. Respect trial stage boundaries; "
            "if you must extrapolate, declare it in `extrapolations` with a "
            "justification. Name regimens by regimen_id from regimen_lookup. "
            "State uncertainties honestly; recommend MDT referral when "
            "resectability or intent is genuinely contested."
        ),
    ),
    SkillSpec(
        "nsclc.panel",
        "One MDT panel member's speciality read of the case. No dose tools; "
        "urgency is mandatory.",
        allowed_tools=("stage_lookup", "trial_lookup", "regimen_lookup",
                       "protocol_lookup", "interaction_check"),
        forbidden_tools=("regimen_detail", "dose_gate_check"),
        output_schema="PanelOpinion", output_key="panel",
        autonomous=True,
        roles=("oncologist", "researcher"),
    ),
    SkillSpec(
        "nsclc.dose_planning",
        "Deterministic dose channel: expand regimen_ids into dose-bearing "
        "drafts through the library and its gates. No model involvement.",
        allowed_tools=("regimen_detail", "dose_gate_check", "interaction_check"),
        output_schema="DosePlan", output_key="dose_plan",
        roles=("oncologist",),
    ),
    SkillSpec(
        "nsclc.critic",
        "Terminal safety audit: run the rule engine, verify citations, check "
        "truncation and schema compliance. May add issues, never remove them.",
        allowed_tools=("citation_verify",),
        output_schema="SafetyAudit", output_key="safety_audit",
    ),
)


class SkillRegistry:
    """Skill lookup + policy enforcement, with optional site overlays."""

    def __init__(self, specs: Optional[dict[str, SkillSpec]] = None) -> None:
        self.specs: dict[str, SkillSpec] = specs if specs is not None else {
            s.skill_id: s for s in BUILTIN_SKILLS
        }

    @classmethod
    def discover(cls, overlay_path: str | Path | None = None) -> "SkillRegistry":
        registry = cls()
        if overlay_path:
            registry.load_overlay(overlay_path)
        return registry

    def load_overlay(self, path: str | Path) -> int:
        """Load site skills from JSON; same skill_id replaces the built-in."""
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        skills = payload.get("skills") if isinstance(payload, dict) else payload
        count = 0
        for entry in skills or []:
            if not isinstance(entry, dict) or not entry.get("skill_id"):
                continue
            spec = SkillSpec(
                skill_id=str(entry["skill_id"]),
                description=str(entry.get("description") or ""),
                allowed_tools=tuple(entry.get("allowed_tools") or ()),
                forbidden_tools=tuple(entry.get("forbidden_tools") or ()),
                output_schema=str(entry.get("output_schema") or ""),
                output_key=str(entry.get("output_key") or ""),
                autonomous=bool(entry.get("autonomous")),
                roles=tuple(entry.get("roles") or ("patient", "oncologist", "researcher")),
                instructions=str(entry.get("instructions") or ""),
            )
            self.specs[spec.skill_id] = spec
            count += 1
        return count

    # -------------------------------------------------------------- enforcement
    def enforce(self, skill_id: str, role: str, tools: list[str]) -> tuple[bool, list[str]]:
        """May ``role``, under ``skill_id``, call ``tools``? Fail closed."""
        spec = self.specs.get(skill_id)
        if spec is None:
            return False, [f"unknown_skill:{skill_id}"]
        problems: list[str] = []
        if role not in spec.roles:
            problems.append(f"role_{role}_not_permitted_for_{skill_id}")
        granted = spec.tool_names()
        for tool in tools:
            if tool not in granted:
                problems.append(f"tool_{tool}_not_granted_by_{skill_id}")
        return not problems, problems

    def get(self, skill_id: str) -> Optional[SkillSpec]:
        return self.specs.get(skill_id)

    def list(self) -> list[SkillSpec]:
        return [self.specs[k] for k in sorted(self.specs)]
