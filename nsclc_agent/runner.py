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
from concurrent.futures import ThreadPoolExecutor
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

#: Agents that may execute concurrently in one wave. Deliberately narrow: the
#: pair must have disjoint state writes (different ``outputs`` keys, no shared
#: fact mutation) and each is a long model-driven loop, so overlapping them is
#: where the wall-clock actually goes. TreatmentAgent and PanelAgent qualify;
#: Interview/Perception do NOT (perception seeds ``facts.tnm`` that the
#: interview's coverage report reads, so running them together would make
#: coverage depend on thread timing).
PARALLEL_SAFE_AGENTS = frozenset({"TreatmentAgent", "PanelAgent"})


class _BrokerFactory:
    """Bound factory the panel uses to mint per-member brokers."""

    def __init__(self, runner: "NSCLCRunner", state: CaseRunState) -> None:
        self._runner = runner
        self._state = state

    def __call__(self, skill_id: str) -> CapabilityBroker:
        return self._runner._broker_for_skill(self._state, skill_id)

    def skill_spec(self, skill_id: str) -> Any:
        return self._runner.skill_registry.get(skill_id)


class _WaveScope:
    """Per-task buffer for ledger-touching mutations during a parallel wave.

    The MemberScope idea generalized: each concurrently-running agent works
    against a proxy that buffers ``add_evidence`` / ``add_claim`` / ``flag`` /
    ``warn`` / ``trace``; buffers merge into the real state **in task order**
    after the wave, so evidence ids depend on the plan, never on which thread
    finished first. Temp ids handed out during the wave are remapped
    everywhere they landed (outputs, claims, traces) at merge time.
    """

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.evidence: list[tuple[str, dict[str, Any]]] = []
        self.claims: list[dict[str, Any]] = []
        self.flags: list[str] = []
        self.warnings: list[str] = []
        self.traces: list[tuple[tuple, dict]] = []

    def add_evidence(self, level, source, summary, payload=None, *,
                     ok=True, error=None, source_version=None) -> str:
        temp_id = f"__{self.tag}E{len(self.evidence) + 1:03d}__"
        self.evidence.append((temp_id, {
            "level": level, "source": source, "summary": summary,
            "payload": payload, "ok": ok, "error": error,
            "source_version": source_version,
        }))
        return temp_id

    def add_claim(self, kind, text, evidence_ids=None, *,
                  confidence=0.5, origin="rule") -> str:
        self.claims.append({
            "kind": kind, "text": text,
            "evidence_ids": list(evidence_ids or []),
            "confidence": confidence, "origin": origin,
        })
        return f"__{self.tag}C{len(self.claims):03d}__"

    def flag(self, message: str) -> None:
        if message not in self.flags:
            self.flags.append(message)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def trace(self, *args, **kwargs) -> None:
        self.traces.append((args, kwargs))


class _ScopedState:
    """Delegating proxy: ledger mutations go to the wave scope, everything
    else to the real state. Only used for agents in PARALLEL_SAFE_AGENTS,
    whose remaining writes (their own ``outputs`` keys) are disjoint."""

    _SCOPED = ("add_evidence", "add_claim", "flag", "warn", "trace")

    def __init__(self, state: CaseRunState, scope: _WaveScope) -> None:
        object.__setattr__(self, "_state", state)
        object.__setattr__(self, "_scope", scope)

    def __getattr__(self, name: str):
        if name in self._SCOPED:
            return getattr(object.__getattribute__(self, "_scope"), name)
        return getattr(object.__getattribute__(self, "_state"), name)

    def __setattr__(self, name: str, value) -> None:
        setattr(object.__getattribute__(self, "_state"), name, value)


def _deep_remap(value: Any, mapping: dict[str, str]) -> Any:
    """Rewrite wave temp ids wherever they landed, including inside prose,
    dict keys and tuples."""
    if isinstance(value, str):
        for temp, real in mapping.items():
            if temp in value:
                value = real if value == temp else value.replace(temp, real)
        return value
    if isinstance(value, list):
        return [_deep_remap(item, mapping) for item in value]
    if isinstance(value, tuple):
        return tuple(_deep_remap(item, mapping) for item in value)
    if isinstance(value, dict):
        return {_deep_remap(key, mapping): _deep_remap(item, mapping)
                for key, item in value.items()}
    return value


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
        parallel_tasks: bool = True,
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
                    "vision_auto_selected": bool(
                        getattr(vision_llm, "auto_selected", False)),
                    "panel_concurrency": panel_concurrency,
                })
        else:
            self.llm = base_llm
        self.vision_llm = vision_llm
        #: Whether the safe long-running pair (Treatment ∥ Panel) may overlap.
        #: Forced off by an active journal (single ordered lane).
        self.parallel_tasks = bool(parallel_tasks)
        self._executed_parallel = False
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
        # Deep copy: the perception layer seeds nested dicts
        # (driver_mutations, pd_l1, tnm) in place, and a shallow copy would
        # write those proposals THROUGH to the caller's Case object — a
        # second run of the same Case would then carry model-seeded facts
        # with the `_report_proposed` guard stripped. Confirmed exploitable
        # in review; the deep copy is load-bearing.
        import copy

        facts = copy.deepcopy(case.facts)
        # Internal bookkeeping keys are never accepted from external input — a
        # case file carrying `_report_proposed: "zz"` must not crash (or
        # pre-seed) the guard machinery.
        for key in [k for k in facts if str(k).startswith("_")]:
            facts.pop(key, None)
        state = CaseRunState(
            complaint=case.narrative(),
            role=role,  # type: ignore[arg-type]
            facts=facts,
            images=(
                [{"kind": "radiology", "ref": ref} for ref in case.images]
                + [{"kind": "report", "ref": ref} for ref in case.reports]
            ),
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
        self._executed_parallel = False
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
        """Execute the task graph, overlapping the safe long-running pair.

        Semantics: repeatedly gather runnable tasks. When BOTH members of the
        PARALLEL_SAFE pair are runnable together (and no journal is active —
        the journal is a single ordered lane), they run concurrently, each
        against a :class:`_WaveScope`, and the scopes merge in task order so
        the evidence ledger stays deterministic. Everything else runs one at
        a time in plan order, exactly as before.
        """
        while True:
            skipped_any = False
            runnable: list[Task] = []
            for task in state.tasks:
                if task.status not in ("pending", "repair_requested"):
                    continue
                verdict = self._authorize_task(state, task)
                if verdict == "skip":
                    skipped_any = True
                    continue
                if verdict == "defer":
                    continue
                if self._deps_ok(state, task):
                    runnable.append(task)
            if runnable:
                wave = [t for t in runnable if t.agent in PARALLEL_SAFE_AGENTS]
                if len(wave) >= 2 and self._parallel_ok():
                    self._run_wave(state, wave)
                    self._executed_parallel = True
                else:
                    self._run_one(state, runnable[0])
                if state.release_status == "failed_closed":
                    return
                continue
            if skipped_any:
                continue  # statuses changed; rescan may unblock nothing → exit next pass
            # Nothing runnable, nothing newly skipped: the rest can never run.
            for task in state.tasks:
                if task.status in ("pending", "repair_requested"):
                    task.status = "skipped_dependency"
            return

    def _authorize_task(self, state: CaseRunState, task: Task) -> str:
        """Apply skip gates. Returns 'run' | 'skip' (status set) | 'defer'."""
        if task.agent in PRESCRIPTIVE_AGENTS:
            if not (state.role == "oncologist" and state.allow_dose_planning):
                task.status = "skipped_not_authorized"
                return "skip"
            # The dose channel never runs ahead of an unfinished interview:
            # its verdict is what proves the emergency screen was answered.
            if any(t.agent == "InterviewAgent"
                   and t.status in ("pending", "repair_requested")
                   for t in state.tasks):
                return "defer"
            if self._interview_blocked(state):
                task.status = "skipped_blocked_interview"
                state.warn(
                    "dose planning skipped: emergency screening axes are "
                    "still unanswered (blocked interview verdict)")
                return "skip"
            if self._dose_blocked_by_proposed_facts(state):
                task.status = "skipped_unconfirmed_facts"
                state.warn(
                    "dose planning skipped: the plan rests on Tier-A facts "
                    "proposed from an uploaded report — confirm the source "
                    "documents before any dose-bearing draft")
                return "skip"
        return "run"

    @staticmethod
    def _dose_blocked_by_proposed_facts(state: CaseRunState) -> bool:
        """Report/film-extracted facts may draft a plan, never dose it.

        ANY unconfirmed proposed fact closes the channel — not just
        driver/PD-L1: seeded histology flips the chemo backbone (pemetrexed
        is histology-gated) and seeded TNM flips the whole intent, so there
        is no "safe" subset of model-read facts to dose on.
        """
        return bool(state.facts.get("_report_proposed"))

    def _parallel_ok(self) -> bool:
        return self.parallel_tasks and self.journal is None

    def _run_one(self, state: CaseRunState, task: Task) -> None:
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

    def _run_wave(self, state: CaseRunState, wave: list[Task]) -> None:
        """Run the safe pair concurrently; merge scopes deterministically.

        In a wave the panel reviews the case WITHOUT the treatment plan in
        its context — enforced by passing an explicit ``None`` context, not
        by timing — an independent, anchor-free read whose synthesis still
        lands beside the plan. ``run_meta.execution`` records the mode.

        Failure semantics: a crashed member's scope is DISCARDED — a half
        gathered evidence trail must not enter the permanent ledger — and
        only the crashed task is marked failed; the run then fails closed
        with the crash recorded. The single checkpoint is written after the
        whole wave has merged and every status is final, so a resume never
        sees a half-merged wave.
        """
        scopes: dict[str, _WaveScope] = {}
        failures: dict[str, str] = {}

        def execute(task: Task) -> None:
            scope = scopes[task.task_id]
            proxied = _ScopedState(state, scope)
            try:
                agent = self.agents[task.agent]
                if task.agent == "PanelAgent":
                    agent.run(proxied, self.tools, _BrokerFactory(self, state),
                              treatment_plan_context=None)
                else:
                    agent.run(proxied, self.tools,
                              self._broker(state, task.agent))
            except Exception as exc:  # noqa: BLE001 - merge decides the outcome
                failures[task.task_id] = f"{type(exc).__name__}: {exc}"

        ordered = sorted(wave, key=lambda t: state.tasks.index(t))
        for task in ordered:
            scopes[task.task_id] = _WaveScope(task.task_id)
        with ThreadPoolExecutor(max_workers=len(ordered)) as pool:
            list(pool.map(execute, ordered))

        # Merge healthy scopes in TASK order — ids depend on the plan, not
        # the scheduler. Crashed scopes are dropped wholesale.
        mapping: dict[str, str] = {}
        for task in ordered:
            if task.task_id in failures:
                continue
            scope = scopes[task.task_id]
            for temp_id, record in scope.evidence:
                real_id = state.add_evidence(
                    record["level"], record["source"], record["summary"],
                    record["payload"], ok=record["ok"], error=record["error"],
                    source_version=record["source_version"])
                mapping[temp_id] = real_id
            for claim in scope.claims:
                state.add_claim(
                    claim["kind"], _deep_remap(claim["text"], mapping),
                    [mapping.get(e, e) for e in claim["evidence_ids"]],
                    confidence=claim["confidence"], origin=claim["origin"])
            for message in scope.flags:
                state.flag(_deep_remap(message, mapping))
            for message in scope.warnings:
                state.warn(_deep_remap(message, mapping))
            for trace_args, trace_kwargs in scope.traces:
                trace_args = _deep_remap(tuple(trace_args), mapping)
                trace_kwargs = _deep_remap(dict(trace_kwargs), mapping)
                state.trace(*trace_args, **trace_kwargs)
        if mapping:
            state.outputs = _deep_remap(state.outputs, mapping)

        # Statuses: each task judged on ITS OWN outcome; a crashed partner
        # does not turn a healthy member's status into "failed".
        for task in ordered:
            task.status = "failed" if task.task_id in failures else "ok"
        for task in ordered:
            if task.task_id in failures:
                state.fail_closed(
                    f"{task.agent} crashed in wave: {failures[task.task_id]} "
                    f"(its partial evidence was discarded)")
        # One checkpoint for the whole wave, after every status is final.
        self._checkpoint(
            state, "wave_" + "_".join(t.agent for t in ordered))

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
                      "skipped_blocked_interview", "skipped_unconfirmed_facts",
                      "repair_requested", "failed", "pending"}
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
            "execution": ("parallel_wave" if self._executed_parallel
                          else "serial"),
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
