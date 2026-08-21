"""Role-scoped rendering: what each audience is allowed to see.

The delivered answer and the operator audit are different artifacts. A patient
view never contains dose detail, the evidence ledger, or internal flags; a
researcher view contains aggregates, not narrative. The full state is reached
only through ``audit()``, which callers must treat as operator-only.
"""

from __future__ import annotations

from typing import Any

from .state import CaseRunState

_STATUS_EXPLANATION = {
    "emergency_action_plan": "肿瘤急症处理路径 / oncologic emergency pathway",
    "needs_more_information": "需要补充信息后才能给出建议 / more information needed",
    "needs_staging_workup": "分期未完成，先做分期检查 / staging workup required",
    "insufficient_evidence": "证据不足以支撑建议 / insufficient evidence to release",
    "treatment_recommendation": "分期内的循证治疗建议 / stage-appropriate recommendation",
    "draft_for_tumor_board": "剂量草案待MDT/医师签核 / dose draft awaiting tumor board",
    "approved_by_tumor_board": "已签核 / approved",
    "blocked": "存在安全规则拦截，未放行 / blocked by a safety rule",
    "failed_closed": "运行故障关闭 / failed closed",
}


def render(state: CaseRunState, role: str | None = None) -> dict[str, Any]:
    role = role or state.role
    if role == "patient":
        return _patient_view(state)
    if role == "researcher":
        return _researcher_view(state)
    return _oncologist_view(state)


def _base(state: CaseRunState) -> dict[str, Any]:
    return {
        "run_id": state.run_id,
        "release_status": state.release_status,
        "release_status_explained": _STATUS_EXPLANATION.get(
            state.release_status, state.release_status),
    }


def _patient_view(state: CaseRunState) -> dict[str, Any]:
    view = _base(state)
    if state.release_status == "emergency_action_plan":
        # The emergency script is delivered verbatim — never model-reworded.
        view["emergency_plan"] = state.outputs.get("emergency_plan")
        return view
    plan = state.outputs.get("treatment_plan") or {}
    view["summary"] = plan.get("summary", "")
    view["options"] = [
        {"name": o.get("name"), "rationale": o.get("rationale")}
        for o in plan.get("options") or []
    ]
    view["questions_for_you"] = list(state.open_questions)
    view["next_tests"] = [
        step.get("test") for step in
        (state.outputs.get("workup_plan") or {}).get("steps") or []
    ]
    view["note"] = (
        "本内容为教学/研究用途的决策支持，不构成医疗建议；任何治疗决定请与您的"
        "主治团队确认。/ Educational decision support — confirm all decisions "
        "with your treating team."
    )
    return view


def _oncologist_view(state: CaseRunState) -> dict[str, Any]:
    view = _base(state)
    view["staging"] = state.staging
    view["routing"] = state.routing
    view["treatment_plan"] = state.outputs.get("treatment_plan")
    view["dose_plan"] = state.outputs.get("dose_plan")
    view["panel"] = state.outputs.get("panel")
    view["workup_plan"] = state.outputs.get("workup_plan")
    view["emergency_plan"] = state.outputs.get("emergency_plan")
    view["interview"] = state.outputs.get("interview")
    view["imaging"] = state.outputs.get("imaging")
    view["open_questions"] = list(state.open_questions)
    view["flags"] = list(state.flags)
    view["safety_issues"] = list(state.safety_issues)
    view["warnings"] = list(state.warnings)
    view["citations"] = [
        {
            "evidence_id": eid,
            "level": e.level,
            "source": e.source,
            "summary": e.summary,
            "source_version": e.source_version,
        }
        for eid, e in state.evidence.items()
    ]
    return view


def _researcher_view(state: CaseRunState) -> dict[str, Any]:
    view = _base(state)
    view["aggregates"] = {
        "stage_group": state.staging.get("stage_group"),
        "module": state.routing.get("module_key"),
        "planner_mode": state.planner_mode,
        "evidence_counts": _level_counts(state),
        "n_options": len((state.outputs.get("treatment_plan") or {}).get("options") or []),
        "n_safety_issues": len(state.safety_issues),
        "n_flags": len(state.flags),
        "budget": state.budget.snapshot(),
    }
    return view


def _level_counts(state: CaseRunState) -> dict[str, int]:
    counts: dict[str, int] = {}
    for evidence in state.evidence.values():
        counts[evidence.level] = counts.get(evidence.level, 0) + 1
    return counts


def audit(state: CaseRunState) -> dict[str, Any]:
    """The operator-only full record."""
    return state.to_dict()
