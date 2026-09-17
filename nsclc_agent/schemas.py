"""Structural contracts for agent outputs.

Skills name an ``output_schema``; this module makes that name mean something.
Validation is deliberately shallow — required keys and broad types — because
the goal is to catch an agent (or LLM) silently emitting a differently-shaped
payload, not to re-implement a type system. Clinical *content* checks live in
:mod:`nsclc_agent.safety.rules`, which is the right layer for them.
"""

from __future__ import annotations

from typing import Any

#: schema name -> {field: (required, accepted python types)}
SCHEMAS: dict[str, dict[str, tuple[bool, tuple[type, ...]]]] = {
    "IntakeSummary": {
        "risk_mode": (True, (str,)),
        "screening": (True, (dict,)),
        "missing_information": (False, (list,)),
    },
    "TaskPlan": {
        "tasks": (True, (list,)),
        "planner_mode": (True, (str,)),
    },
    "EmergencyActionPlan": {
        "risk_judgement": (True, (str,)),
        "signals": (True, (list,)),
        "immediate_actions": (True, (list,)),
        "do_not": (True, (list,)),
        "escalate_if": (True, (list,)),
    },
    "InterviewProgress": {
        "coverage": (True, (dict,)),
        "questions": (True, (list,)),
        "verdict": (True, (dict,)),
        "rounds_used": (False, (int,)),
    },
    #: The perception layer's proposal. ``requires_confirmation`` must be true —
    #: a model image read never stands in for the radiologist or pathology.
    "ImagingFindings": {
        "modality": (False, (str,)),
        "candidate_t": (False, (str,)),
        "candidate_n": (False, (str,)),
        "candidate_m": (False, (str,)),
        "nodal_stations": (False, (list,)),
        "measurable_lesions": (False, (list,)),
        "metastatic_sites": (False, (list,)),
        "confidence": (False, (str,)),
        "uncertainties": (False, (list,)),
        "requires_confirmation": (True, (bool,)),
    },
    #: The treatment agent's structured plan — the payload the safety rule
    #: engine checks. ``regimen_ids`` reference the deterministic library;
    #: model-authored fields must stay dose-free.
    "TreatmentPlan": {
        "intent": (True, (str,)),           # curative | palliative | workup
        "summary": (True, (str,)),
        "options": (True, (list,)),          # [{name, regimen_ids, rationale, line}]
        "regimen_ids": (True, (list,)),
        "trial_refs": (False, (list,)),
        "extrapolations": (False, (list,)),  # [{trial_id, justification}]
        "workup_needed": (False, (list,)),
        "mdt_referral": (False, (bool,)),
        "uncertainties": (False, (list,)),
        "contraindications": (False, (list,)),
        "follow_up": (False, (dict,)),
    },
    #: One MDT panel member's opinion. ``urgency`` is required because a member
    #: that cannot state an urgency has not done the one job the panel needs.
    "PanelOpinion": {
        "urgency": (True, (str,)),           # routine | expedited | urgent
        "key_findings": (True, (list,)),
        "concerns": (True, (list,)),
        "recommend_next": (True, (list,)),
        "dissent": (False, (str,)),
    },
    "PanelSynthesis": {
        "urgency": (True, (str,)),
        "consensus": (True, (list,)),
        "disagreements": (True, (list,)),
        "members": (True, (list,)),
    },
    "DosePlan": {
        "regimens": (True, (list,)),
        "gates_checked": (True, (list,)),
        "interactions": (True, (list,)),
        "requires_tumor_board_approval": (True, (bool,)),
    },
    "SafetyAudit": {
        "checks_run": (True, (list,)),
        "issues": (False, (list,)),
        "violations": (False, (list,)),
        "repair_requests": (False, (list,)),
    },
}

#: Urgency vocabulary shared by the panel schemas; anything else is rejected
#: as a value error rather than silently ordered.
PANEL_URGENCIES = ("routine", "expedited", "urgent")


def validate(schema_name: str, payload: Any) -> tuple[bool, list[str]]:
    """Validate ``payload`` against a named schema.

    An unknown schema name is a manifest bug and is reported as a failure
    rather than passing silently.
    """
    if not schema_name:
        return True, []
    schema = SCHEMAS.get(schema_name)
    if schema is None:
        return False, [f"unknown output_schema {schema_name!r}"]
    if not isinstance(payload, dict):
        return False, [f"{schema_name}: payload is not a mapping"]
    problems: list[str] = []
    for field_name, (required, types) in schema.items():
        if field_name not in payload:
            if required:
                problems.append(f"{schema_name}: missing required field {field_name!r}")
            continue
        value = payload[field_name]
        if value is None:
            if required:
                problems.append(f"{schema_name}: field {field_name!r} is null")
            continue
        if not isinstance(value, types):
            expected = "/".join(t.__name__ for t in types)
            problems.append(
                f"{schema_name}: field {field_name!r} should be {expected}, "
                f"got {type(value).__name__}"
            )

    # Value checks that shape validation cannot express.
    if schema_name == "ImagingFindings" and payload.get("requires_confirmation") is False:
        problems.append(
            "ImagingFindings: requires_confirmation must be true — a model image "
            "read never replaces the radiologist's report or pathology"
        )
    if schema_name in ("PanelOpinion", "PanelSynthesis"):
        urgency = str(payload.get("urgency", ""))
        if urgency and urgency not in PANEL_URGENCIES:
            problems.append(
                f"{schema_name}: urgency {urgency!r} not in {PANEL_URGENCIES}"
            )
    return not problems, problems
