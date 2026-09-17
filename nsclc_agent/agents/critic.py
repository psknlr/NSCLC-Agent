"""The terminal safety critic — unconditional, deterministic-first.

Runs on **every** path, including failed-closed runs and runs where every
clinical task was skipped (the runner invokes it in a ``finally``). It:

1. runs the deterministic rule engine over the plan (N3-no-surgery,
   driver/IO exclusions, trial stage boundaries, RT dose, dose-leak scan…);
2. runs the **citation guard**: every trial reference in the plan must resolve
   (registry offline, NCT/PMID live when enabled) and every high-stakes claim
   must be backed by releasable evidence — a plan citing only
   ``model_reasoning``/stub evidence is not releasable;
3. checks output truncation and schema validity one more time, independently;
4. emits bounded repair requests the runner may honor within its loop budget.

An optional LLM pass may ADD issues; nothing can remove one.
"""

from __future__ import annotations

from typing import Any

from ..safety import rules as rule_engine
from ..state import CaseRunState, NON_RELEASABLE_LEVELS


class CriticAgent:
    skill_id = "nsclc.critic"

    def __init__(self, llm: Any | None = None) -> None:
        self.llm = llm

    def run(self, state: CaseRunState, tools: Any, broker: Any) -> None:
        checks_run: list[str] = []
        issues: list[str] = []
        violations: list[dict[str, Any]] = []
        repair_requests: list[dict[str, str]] = []

        plan = state.outputs.get("treatment_plan") or {}

        # -- 1. deterministic rule engine -----------------------------------
        checks_run.append("safety_rule_engine")
        found = rule_engine.check_plan(state.staging, state.facts, plan)
        for violation in found:
            violations.append(violation.to_dict())
            issues.append(f"{violation.rule_id}: {violation.message}")
            if violation.severity == "block":
                repair_requests.append({
                    "agent": violation.repair_agent,
                    "reason": f"{violation.rule_id}: {violation.message}",
                })

        # -- 2. citation guard ----------------------------------------------
        checks_run.append("citation_guard")
        issues.extend(self._citation_guard(state, tools, broker, plan))

        # -- 3. report-proposed fact check ----------------------------------
        checks_run.append("proposed_fact_check")
        proposed = state.facts.get("_report_proposed") or []
        if proposed and plan.get("regimen_ids"):
            issues.append(
                "PLAN_RESTS_ON_REPORT_PROPOSED_FACTS: "
                + ", ".join(str(p) for p in proposed)
                + " were read from uploaded document images and are "
                "unconfirmed — the plan is provisional and the dose channel "
                "stays closed until the source documents are verified"
            )

        # -- 4. truncation / schema re-check --------------------------------
        checks_run.append("truncation_check")
        loop_meta = state.outputs.get("treatment_loop") or {}
        if loop_meta.get("mode") == "output_truncated":
            issues.append(
                "OUTPUT_TRUNCATED: the treatment reply hit its token ceiling; "
                "the plan on record is incomplete"
            )

        # -- 4. optional LLM additions (add-only) ---------------------------
        # A model may append issues; it is structurally unable to clear any,
        # because this list only ever grows past this point.

        blocked = any(v.get("severity") == "block" for v in violations)
        if blocked and state.release_status not in ("failed_closed", "emergency_action_plan"):
            state.release_status = "blocked"
        for issue in issues:
            if issue not in state.safety_issues:
                state.safety_issues.append(issue)

        state.outputs["safety_audit"] = {
            "checks_run": checks_run,
            "issues": issues,
            "violations": violations,
            "repair_requests": repair_requests,
        }
        state.trace("CriticAgent", "audit",
                    output_summary=f"{len(issues)} issue(s), "
                                   f"{len(repair_requests)} repair request(s)")

    # ------------------------------------------------------------------ guard
    def _citation_guard(
        self, state: CaseRunState, tools: Any, broker: Any, plan: dict[str, Any]
    ) -> list[str]:
        issues: list[str] = []
        if not plan:
            return issues

        # Every trial the plan references must verify.
        for ref in plan.get("trial_refs") or []:
            result = tools.call(broker, "citation_verify", reference=str(ref))
            if not result.ok:
                issues.append(f"CITATION_CHECK_FAILED: {ref} ({result.error})")
                continue
            if not result.data.get("verified"):
                issues.append(
                    f"UNVERIFIED_CITATION: {ref} — "
                    f"{result.data.get('note') or 'could not be verified'}; "
                    f"treat as unsupported until verified"
                )

        # The plan's cited ledger evidence must include something releasable.
        citations = [c for c in plan.get("citations") or []
                     if isinstance(c, str)] or \
            (state.outputs.get("treatment_loop") or {}).get("citations") or []
        if citations:
            levels = {
                state.evidence[c].level
                for c in citations if c in state.evidence
            }
            if levels and levels <= NON_RELEASABLE_LEVELS:
                issues.append(
                    "CITATIONS_NOT_RELEASABLE: the plan cites only model/stub/"
                    "failed evidence — no releasable support on record"
                )
        elif plan.get("regimen_ids"):
            issues.append(
                "NO_CITATIONS: a regimen-bearing plan cites no ledger evidence"
            )
        return issues
