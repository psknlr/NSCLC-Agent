"""The run loop: intake → plan → act → observe → repair, with a terminal gate.

Guarantees this runner provides regardless of the plan it is given:

* the safety critic runs on **every** path — in a ``finally``, never as a
  dependency-gated task — so failed-closed and skipped runs are audited too;
* an oncologic emergency short-circuits to the fixed action plan; nothing
  treatment-shaped runs after it;
* every tool call is brokered against a skill that must exist (fail-closed);
* dose planning requires the oncologist role, an explicit opt-in, and a
  non-blocked interview verdict;
* repair loops are bounded by the budget; unresolved blockers stay visible;
* state checkpoints after each node and can be resumed from disk;
* a replay that diverged fails closed instead of impersonating a review.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .agents import (
    CriticAgent,
    DosePlanAgent,
    EmergencyAgent,
    IntakeAgent,
    InterviewAgent,
    PanelAgent,
    PerceptionAgent,
    PlannerAgent,
    StagingAgent,
    TreatmentAgent,
)
from .agents.planner import AGENT_CATALOG
from .case import Case
from .interview import InterviewLoop
from .journal import Journal, JournaledLLM
from .llm.base import NullLLMClient
from .llm.providers import describe_client
from .prompts import MODULES, load_module
from .skills import SkillRegistry
from .state import CaseRunState, Task
from .tools import CapabilityBroker, ToolHealth, ToolRegistry

#: Agents that may only run for an oncologist who explicitly opted in.
PRESCRIPTIVE_AGENTS = {"DosePlanAgent"}


class _BrokerFactory:
    """Bound factory the panel uses to mint per-member brokers."""

    def __init__(self, runner: "NSCLCRunner", state: CaseRunState) -> None:
        self._runner = runner
        self._state = state

    def __call__(self, skill_id: str) -> CapabilityBroker:
        return self._runner._broker_for_skill(self._state, skill_id)

    def skill_spec(self, skill_id: str) -> Any:
        return self._runner.skill_registry.get(skill_id)


class NSCLCRunner:
    """Task-driven runner with bounded self-repair loops."""

    def __init__(
        self,
        tools: ToolRegistry | None = None,
        *,
        llm: Any | None = None,
        vision_llm: Any | None = None,
        skill_registry: SkillRegistry | None = None,
        journal: Journal | None = None,
        checkpoint_dir: str | Path | None = None,
        panel_concurrency: int = 4,
        case_base_dir: Path | None = None,
        interview_loop: Any | None = None,
    ) -> None:
        self.tools = tools or ToolRegistry()
        self.skill_registry = skill_registry or SkillRegistry.discover()
        self.checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else None
        self.journal = journal
        base_llm = llm or NullLLMClient()
        if self.journal is not None:
            # The journal is a single ordered lane; concurrent panel members
            # would record in completion order and replay would diverge on
            # scheduling alone. Journaled runs therefore convene sequentially.
            panel_concurrency = 1
            self.llm = JournaledLLM(
                base_llm, self.journal,
                offline=self.journal.mode == "replay"
                and not getattr(base_llm, "available", False),
            )
            if vision_llm is not None or self.journal.mode == "replay":
                vision_llm = JournaledLLM(
                    vision_llm or NullLLMClient(), self.journal,
                    offline=self.journal.mode == "replay"
                    and not getattr(vision_llm, "available", False),
                    meta_prefix="vision",
                )
            if self.journal.mode == "record":
                self.journal.write_meta({
                    "llm_model": getattr(base_llm, "model", "none"),
                    "llm_provider": getattr(base_llm, "name", "none"),
                    "llm_available": bool(getattr(base_llm, "available", False)),
                    "vision_model": getattr(
                        getattr(vision_llm, "inner", vision_llm), "model", "none"),
                    "vision_provider": getattr(
                        getattr(vision_llm, "inner", vision_llm), "name", "none"),
                    "vision_available": bool(getattr(vision_llm, "available", False)),
                    "vision_supports_vision": bool(
                        getattr(vision_llm, "supports_vision", False)),
                    "panel_concurrency": panel_concurrency,
                })
        else:
            self.llm = base_llm
        self.vision_llm = vision_llm
        #: Explicit loop = a continuing conversation whose stall history must
        #: span turns. Absent that, every run gets a fresh loop (see run()).
        self._interview_loop_override = interview_loop
        self.health = ToolHealth()
        self.agents: dict[str, Any] = {
            "IntakeAgent": IntakeAgent(self.llm),
            "EmergencyAgent": EmergencyAgent(self.llm),
            "InterviewAgent": InterviewAgent(self.llm, loop=interview_loop),
            "PerceptionAgent": PerceptionAgent(self.vision_llm,
                                               base_dir=case_base_dir),
            "StagingAgent": StagingAgent(),
            "TreatmentAgent": TreatmentAgent(self.llm,
                                             skill_registry=self.skill_registry),
            "PanelAgent": PanelAgent(self.llm, concurrency=panel_concurrency),
            "DosePlanAgent": DosePlanAgent(),
            "CriticAgent": CriticAgent(self.llm),
        }
        self.planner = PlannerAgent(self.llm)

    # ----------------------------------------------------------------- plumbing
    def _broker(self, state: CaseRunState, agent_name: str) -> CapabilityBroker:
        skill_id = getattr(self.agents.get(agent_name), "skill_id", "") or None
        return self._make_broker(state, skill_id)

    def _broker_for_skill(self, state: CaseRunState, skill_id: str) -> CapabilityBroker:
        return self._make_broker(state, skill_id or None)

    def _make_broker(self, state: CaseRunState, skill_id: str | None) -> CapabilityBroker:
        return CapabilityBroker(
            state.role, state.risk_mode,
            budget=state.budget,
            skill_registry=self.skill_registry,
            active_skill=skill_id,
            health=self.health,
            journal=self.journal,
        )

    def _checkpoint(self, state: CaseRunState, node: str) -> None:
        if not self.checkpoint_dir:
            return
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        payload = state.to_dict()
        payload["_checkpoint"] = {"node": node, "loop_index": state.loop_index}
        for name in (f"{state.run_id}.{node}.json", f"{state.run_id}.latest.json"):
            (self.checkpoint_dir / name).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8")

    @staticmethod
    def load_checkpoint(path: str | Path) -> CaseRunState:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        payload.pop("_checkpoint", None)
        return CaseRunState.from_dict(payload)

    # -------------------------------------------------------------- case entry
    def run_case(
        self,
        case: Case,
        *,
        role: str = "oncologist",
        allow_dose_planning: bool = False,
        enable_panel: bool = False,
    ) -> CaseRunState:
        state = CaseRunState(
            complaint=case.narrative(),
            role=role,  # type: ignore[arg-type]
            facts=dict(case.facts),
            images=[{"kind": "radiology", "ref": ref} for ref in case.images],
            enable_panel=enable_panel,
            allow_dose_planning=allow_dose_planning,
        )
        state.facts["tnm"] = {
            "t": case.t, "n": case.n, "m": case.m, "prefix": case.tnm_prefix,
        }
        state.facts["staging_system"] = case.staging_system
        if case.stage_group:
            state.facts["stage_group"] = case.stage_group
        if case.case_id:
            state.facts["case_id"] = case.case_id
        return self.run(state)

    # --------------------------------------------------------------------- run
    def run(self, state: CaseRunState, *, resumed: bool = False) -> CaseRunState:
        # Per-run scoping: interview stall history and the circuit breaker are
        # run-local state. On one long-lived runner they contaminated later,
        # unrelated cases — three identical cases in a row hit the stall
        # detector and the third came back BLOCKED. An explicitly provided
        # loop (a continuing conversation) is deliberately reused.
        self.health = ToolHealth()
        loop = self._interview_loop_override or InterviewLoop(self.llm)
        self.agents["InterviewAgent"] = InterviewAgent(self.llm, loop=loop)
        try:
            if not resumed:
                self._bootstrap(state)
            else:
                self._reopen_for_resume(state)
            if state.release_status != "failed_closed":
                if state.risk_mode == "emergency":
                    self.agents["EmergencyAgent"].run(
                        state, self.tools, self._broker(state, "EmergencyAgent"))
                    self._checkpoint(state, "EmergencyAgent")
                else:
                    if not state.tasks:
                        self.planner.run(
                            state,
                            allow_dose_planning=state.allow_dose_planning)
                        self._checkpoint(state, "PlannerAgent")
                    self._run_loops(state)
        except Exception as exc:  # noqa: BLE001 - a crash must still fail closed
            state.fail_closed(
                f"runtime error, failed closed: {type(exc).__name__}: {exc}")
        finally:
            # The critic is unconditional: it observes every run, including
            # failed-closed ones and ones that skipped every clinical task.
            # The finally body is itself guarded — a journal divergence (or
            # any crash) inside the terminal audit must still leave a
            # failed-closed, finalized state rather than escaping the runner.
            try:
                if "safety_audit" not in state.outputs:
                    self._critic(state)
            except Exception as exc:  # noqa: BLE001
                state.fail_closed(
                    f"terminal audit crashed, failed closed: "
                    f"{type(exc).__name__}: {exc}")
                state.outputs.setdefault("safety_audit", {
                    "checks_run": ["crashed"], "issues": [str(exc)],
                    "violations": [], "repair_requests": [],
                })
            try:
                self._check_replay_fidelity(state)
                self._finalize(state)
            except Exception as exc:  # noqa: BLE001
                state.fail_closed(
                    f"finalization crashed, failed closed: "
                    f"{type(exc).__name__}: {exc}")
        return state

    # ------------------------------------------------------------------- nodes
    def _bootstrap(self, state: CaseRunState) -> None:
        self.agents["IntakeAgent"].run(
            state, self.tools, self._broker(state, "IntakeAgent"))
        self._checkpoint(state, "IntakeAgent")

    def _run_loops(self, state: CaseRunState) -> None:
        while True:
            self._execute_tasks(state)
            if state.release_status == "failed_closed":
                return
            repairs = self._critic(state)
            if not repairs or not state.budget.can_loop("repair"):
                if repairs:
                    state.warn(
                        f"repair budget exhausted with unresolved findings — "
                        f"left for human review")
                return
            state.budget.count_loop("repair")
            state.loop_index += 1
            if not self._reset_for_repair(state, repairs):
                return
            state.outputs.pop("safety_audit", None)

    def _execute_tasks(self, state: CaseRunState) -> None:
        interview_blocked = self._interview_blocked(state)
        for task in state.tasks:
            if task.status not in ("pending", "repair_requested"):
                continue
            if task.agent in PRESCRIPTIVE_AGENTS:
                if not (state.role == "oncologist" and state.allow_dose_planning):
                    task.status = "skipped_not_authorized"
                    continue
                if interview_blocked:
                    task.status = "skipped_blocked_interview"
                    state.warn(
                        "dose planning skipped: emergency screening axes are "
                        "still unanswered (blocked interview verdict)")
                    continue
            if not self._deps_ok(state, task):
                task.status = "skipped_dependency"
                continue
            agent = self.agents.get(task.agent)
            if agent is None or task.agent not in AGENT_CATALOG:
                task.status = "failed"
                state.fail_closed(f"unknown or unregistered task agent: {task.agent}")
                return
            if task.agent == "PanelAgent":
                agent.run(state, self.tools, _BrokerFactory(self, state))
            else:
                agent.run(state, self.tools, self._broker(state, task.agent))
            task.status = ("failed" if state.release_status == "failed_closed"
                           else "ok")
            self._checkpoint(state, task.agent)
            if state.release_status == "failed_closed":
                return
            # An interview verdict may have just turned blocked.
            interview_blocked = self._interview_blocked(state)

    def _deps_ok(self, state: CaseRunState, task: Task) -> bool:
        status = {t.task_id: t.status for t in state.tasks}
        return all(status.get(dep) == "ok" for dep in task.depends_on)

    @staticmethod
    def _interview_blocked(state: CaseRunState) -> bool:
        """True while any unwaivable (red-flag) axis is still open.

        Not just the terminal ``blocked`` verdict: in a single-pass batch run
        the verdict is ``not_achieved`` with ``blocking_axes`` populated, and
        the dose channel must stay shut for exactly the same reason.
        """
        verdict = ((state.outputs.get("interview") or {}).get("verdict") or {})
        return verdict.get("verdict") == "blocked" or bool(
            verdict.get("blocking_axes"))

    def _critic(self, state: CaseRunState) -> list[dict[str, str]]:
        self.agents["CriticAgent"].run(
            state, self.tools, self._broker(state, "CriticAgent"))
        self._checkpoint(state, "CriticAgent")
        return (state.outputs.get("safety_audit") or {}).get("repair_requests", [])

    def _reset_for_repair(self, state: CaseRunState,
                          repairs: list[dict[str, str]]) -> bool:
        reasons = {r.get("agent"): r.get("reason", "")
                   for r in repairs if r.get("agent")}
        indexes = [i for i, t in enumerate(state.tasks) if t.agent in reasons]
        if not indexes:
            return False
        first = min(indexes)
        for index, task in enumerate(state.tasks):
            if index < first:
                continue
            if task.agent in reasons:
                task.status = "repair_requested"
                task.repair_reason = reasons[task.agent]
            elif task.status in ("ok", "skipped_dependency"):
                task.status = "pending"
        return True

    # ---------------------------------------------------------------- finalize
    def _reopen_for_resume(self, state: CaseRunState) -> None:
        """Re-open unfinished work so new facts can move a resumed run forward.

        A failed-closed run stays failed closed — resume is not an escape
        hatch from a safety decision.
        """
        if state.release_status == "failed_closed":
            return
        reopenable = {"skipped_dependency", "skipped_not_authorized",
                      "skipped_blocked_interview", "repair_requested",
                      "failed", "pending"}
        for task in state.tasks:
            if task.status in reopenable:
                task.status = "pending"
            elif task.agent in ("InterviewAgent", "StagingAgent",
                                "TreatmentAgent") and task.status == "ok":
                # New facts legitimately change these outcomes.
                task.status = "pending"
        state.outputs.pop("safety_audit", None)

    def _check_replay_fidelity(self, state: CaseRunState) -> None:
        journal = self.journal
        if journal is None or journal.mode != "replay":
            return
        if journal.diverged:
            first = journal.divergences[0]
            state.fail_closed(
                f"replay divergence at journal entry {first['seq']}: recorded "
                f"{first['recorded']}, issued {first['issued']} — the case, "
                f"code or model changed; this replay cannot stand as a review "
                f"of the original decision"
            )
        elif journal.live_after_exhaustion:
            state.warn(
                f"replay journal exhausted; {journal.live_after_exhaustion} "
                f"call(s) ran live — this result is not a pure offline replay")

    def _finalize(self, state: CaseRunState) -> None:
        state.safety_issues = list(dict.fromkeys(state.safety_issues))
        plan = state.outputs.get("treatment_plan") or {}
        terminal = ("failed_closed", "emergency_action_plan", "blocked",
                    "draft_for_tumor_board", "approved_by_tumor_board",
                    "needs_staging_workup")
        if state.release_status not in terminal:
            issues = set((state.outputs.get("safety_audit") or {}).get("issues") or [])
            unreleasable = any(
                i.startswith(("CITATIONS_NOT_RELEASABLE", "NO_CITATIONS"))
                for i in issues)
            if not state.staging:
                state.release_status = "needs_staging_workup"
            elif plan.get("intent") == "workup" or plan.get("workup_needed"):
                state.release_status = "needs_more_information"
            elif plan.get("options"):
                state.release_status = (
                    "insufficient_evidence" if unreleasable
                    else "treatment_recommendation")
            elif state.open_questions:
                state.release_status = "needs_more_information"
            else:
                state.release_status = "insufficient_evidence"

        module_key = state.routing.get("module_key")
        module_meta = None
        if module_key and module_key in MODULES:
            module = load_module(module_key)
            module_meta = {"key": module.key, "sha256": module.sha256[:16],
                           "label": module.label}
        state.outputs["run_meta"] = {
            "planner_mode": state.planner_mode,
            "llm": describe_client(self.llm),
            "vision": describe_client(self.vision_llm) if self.vision_llm
            else {"provider": "none"},
            "loops_used": state.loop_index + 1,
            "budget": state.budget.snapshot(),
            "tool_health": self.health.snapshot(),
            "protocol_module": module_meta,
            "journal": self.journal.summary() if self.journal else {"mode": "off"},
        }


def resume_run(
    checkpoint: str | Path,
    *,
    new_facts: dict[str, Any] | None = None,
    **runner_kwargs: Any,
) -> CaseRunState:
    """Resume a checkpointed run, optionally folding in newly supplied facts."""
    state = NSCLCRunner.load_checkpoint(checkpoint)
    if new_facts:
        state.facts.update(new_facts)
    runner = NSCLCRunner(**runner_kwargs)
    return runner.run(state, resumed=True)
