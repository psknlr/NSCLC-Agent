"""LLM task planning: the model proposes, the validator disposes.

The model may propose a task graph — reorder, drop optional steps, add the
panel. It may not: invent agents, schedule prescriptive agents in emergency
mode, create dependency cycles, or exceed an agent's skill. Any invalid
proposal is discarded **wholesale** and the deterministic plan is used; the
rejection reason is recorded so ``planner_mode: rule`` is diagnosable
(``llm_not_configured`` / ``llm_plan_rejected:…`` / ``llm_plan_unparseable`` /
``llm_budget_exhausted`` / ``llm_error:…``).

Shape tolerance follows the "liberal shape, strict content" rule: plans may
arrive as ``tasks`` / ``plan`` / ``steps`` / a bare list; agents as ``agent`` /
``agent_name`` / ``name``; dependencies as ``depends_on`` / ``dependencies`` /
``after``. Bare strings are accepted only on an exact agent-catalog hit —
fuzzy-matching an agent name would be guessing meaning, the one thing the
parser must never do.
"""

from __future__ import annotations

import json
from typing import Any

from ..llm.base import LLMError, extract_json
from ..state import CaseRunState, Task

#: The complete set of schedulable agents. The planner itself is not in it, so
#: it can never schedule itself.
AGENT_CATALOG: dict[str, str] = {
    "InterviewAgent": "Close open enquiry axes (questions to patient/physician).",
    "PerceptionAgent": "Read attached radiology films into proposed descriptors.",
    "StagingAgent": "Deterministic AJCC-9 staging of the case descriptors.",
    "TreatmentAgent": "Stage-routed treatment reasoning (autonomous tool loop).",
    "PanelAgent": "Multidisciplinary panel of speciality subagents.",
    "DosePlanAgent": "Deterministic dose channel (oncologist + opt-in only).",
}

#: Agents that must not appear in an emergency-mode plan.
EMERGENCY_EXCLUDED = {"TreatmentAgent", "PanelAgent", "DosePlanAgent"}

#: The deterministic order dependencies must respect.
_CANONICAL_ORDER = ["InterviewAgent", "PerceptionAgent", "StagingAgent",
                    "TreatmentAgent", "PanelAgent", "DosePlanAgent"]

PLANNER_SYSTEM_PROMPT = """You plan the task graph for one NSCLC case run.

Available agents (use EXACTLY these names):
{catalog}

Rules:
- StagingAgent must run before TreatmentAgent/PanelAgent/DosePlanAgent.
- PerceptionAgent only when images are attached.
- InterviewAgent when required enquiry axes are open.
- PanelAgent only when the case is genuinely contested (resectability, intent).
- DosePlanAgent only if the run allows dose planning.
- No cycles; list a task's dependencies by task_id.

Output ONLY JSON: {{"tasks": [{{"task_id": "T1", "agent": "...",
"objective": "...", "depends_on": []}}, ...]}}"""

_TASK_CONTAINERS = ("tasks", "plan", "task_plan", "steps", "graph", "task_graph", "nodes")
_AGENT_KEYS = ("agent", "agent_name", "name", "agent_id")
_ID_KEYS = ("task_id", "id", "step_id", "node_id")
_DEP_KEYS = ("depends_on", "dependencies", "depends", "after", "requires")


def default_plan(state: CaseRunState, *, allow_dose_planning: bool = False) -> list[Task]:
    """The deterministic plan — complete and safe with no model at all."""
    tasks: list[Task] = []

    def add(agent: str, objective: str, deps: list[str]) -> str:
        task_id = f"T{len(tasks) + 1}"
        tasks.append(Task(task_id, agent, objective, depends_on=deps, origin="rule"))
        return task_id

    interview = add("InterviewAgent", "Close open enquiry axes", [])
    prior = [interview]
    if state.images:
        perception = add("PerceptionAgent", "Read attached films into proposed descriptors", [])
        prior.append(perception)
    staging = add("StagingAgent", "Deterministically stage the case", list(prior))
    add("TreatmentAgent", "Stage-routed treatment reasoning", [staging])
    if state.enable_panel:
        add("PanelAgent", "Convene the MDT panel", [staging])
    if allow_dose_planning and state.role == "oncologist":
        add("DosePlanAgent", "Expand regimen ids through the dose channel",
            [f"T{len(tasks)}"])
    return tasks


def _first_key(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in item:
            return item[key]
    return None


def _extract_task_list(payload: Any) -> list[Any] | None:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for container in _TASK_CONTAINERS:
            value = payload.get(container)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):  # one level of nesting: {"plan": {"tasks": []}}
                for inner in _TASK_CONTAINERS:
                    nested = value.get(inner)
                    if isinstance(nested, list):
                        return nested
    return None


def parse_plan(payload: Any) -> tuple[list[Task] | None, str]:
    """Parse a model plan proposal tolerantly. Returns (tasks, reason)."""
    raw_tasks = _extract_task_list(payload)
    if raw_tasks is None:
        return None, "no recognisable task container"
    tasks: list[Task] = []
    for index, item in enumerate(raw_tasks, start=1):
        if isinstance(item, str):
            # Bare string: accept only on an exact catalog hit.
            if item in AGENT_CATALOG:
                tasks.append(Task(f"T{index}", item, AGENT_CATALOG[item]))
                continue
            return None, f"bare string {item!r} is not an exact agent name"
        if not isinstance(item, dict):
            return None, f"task {index} is neither object nor agent name"
        agent = _first_key(item, _AGENT_KEYS)
        if not isinstance(agent, str) or not agent:
            return None, f"task {index} names no agent"
        task_id = _first_key(item, _ID_KEYS) or f"T{index}"
        deps = _first_key(item, _DEP_KEYS) or []
        if isinstance(deps, str):
            deps = [deps]
        objective = str(item.get("objective") or item.get("goal")
                        or AGENT_CATALOG.get(agent, ""))
        tasks.append(Task(str(task_id), agent, objective,
                          depends_on=[str(d) for d in deps], origin="llm"))
    return (tasks, "") if tasks else (None, "empty task list")


def validate_plan(
    tasks: list[Task],
    state: CaseRunState,
    *,
    allow_dose_planning: bool,
) -> tuple[bool, str]:
    """Reject a proposal wholesale on any violation."""
    ids = [t.task_id for t in tasks]
    if len(ids) != len(set(ids)):
        return False, "duplicate task ids"
    known_ids = set(ids)
    for task in tasks:
        if task.agent not in AGENT_CATALOG:
            return False, f"unknown agent {task.agent!r}"
        if state.risk_mode == "emergency" and task.agent in EMERGENCY_EXCLUDED:
            return False, f"{task.agent} not schedulable in emergency mode"
        if task.agent == "DosePlanAgent" and not (
            allow_dose_planning and state.role == "oncologist"
        ):
            return False, "DosePlanAgent requires oncologist role + opt-in"
        if task.agent == "PerceptionAgent" and not state.images:
            return False, "PerceptionAgent scheduled with no images attached"
        for dep in task.depends_on:
            if dep not in known_ids:
                return False, f"task {task.task_id} depends on unknown {dep!r}"
    # Cycle check (Kahn).
    indegree = {t.task_id: 0 for t in tasks}
    for task in tasks:
        for dep in task.depends_on:
            indegree[task.task_id] += 1
    queue = [tid for tid, deg in indegree.items() if deg == 0]
    seen = 0
    dependents = {t.task_id: [u.task_id for u in tasks if t.task_id in u.depends_on]
                  for t in tasks}
    while queue:
        current = queue.pop()
        seen += 1
        for nxt in dependents[current]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    if seen != len(tasks):
        return False, "dependency cycle"
    # Ordering: anything downstream of staging must actually depend on it.
    staging_ids = {t.task_id for t in tasks if t.agent == "StagingAgent"}
    if any(t.agent in ("TreatmentAgent", "PanelAgent", "DosePlanAgent") for t in tasks):
        if not staging_ids:
            return False, "treatment-bearing plan without StagingAgent"
        closure = _transitive_deps(tasks)
        for task in tasks:
            if task.agent in ("TreatmentAgent", "PanelAgent", "DosePlanAgent"):
                if not (closure[task.task_id] & staging_ids):
                    return False, f"{task.agent} does not depend on StagingAgent"
    return True, ""


def _transitive_deps(tasks: list[Task]) -> dict[str, set[str]]:
    direct = {t.task_id: set(t.depends_on) for t in tasks}
    closure: dict[str, set[str]] = {}

    def expand(tid: str, trail: set[str]) -> set[str]:
        if tid in closure:
            return closure[tid]
        out = set()
        for dep in direct.get(tid, ()):
            if dep in trail:
                continue  # cycle guard; cycle detection already ran
            out.add(dep)
            out |= expand(dep, trail | {tid})
        closure[tid] = out
        return out

    for tid in direct:
        expand(tid, set())
    return closure


class PlannerAgent:
    """Plans the run: model proposal validated hard, deterministic fallback."""

    skill_id = ""  # the planner calls no tools

    def __init__(self, llm: Any | None = None) -> None:
        self.llm = llm

    def run(self, state: CaseRunState, *, allow_dose_planning: bool = False) -> None:
        note = "llm_not_configured"
        if self.llm is not None and getattr(self.llm, "available", False):
            if not state.budget.reserve_llm():
                note = "llm_budget_exhausted"
            else:
                note = self._try_llm_plan(state, allow_dose_planning)
        if note == "llm_plan_accepted":
            state.planner_mode = "llm"
        else:
            state.planner_mode = "rule"
            state.tasks = default_plan(state, allow_dose_planning=allow_dose_planning)
            if note not in ("llm_not_configured",):
                state.warn(f"planner: {note}")
        state.outputs["plan"] = {
            "planner_mode": state.planner_mode,
            "note": note,
            "tasks": [
                {"task_id": t.task_id, "agent": t.agent, "objective": t.objective,
                 "depends_on": t.depends_on, "origin": t.origin}
                for t in state.tasks
            ],
        }
        state.trace("PlannerAgent", "plan", output_summary=note)

    def _try_llm_plan(self, state: CaseRunState, allow_dose_planning: bool) -> str:
        context = {
            "complaint": state.complaint[:2000],
            "risk_mode": state.risk_mode,
            "role": state.role,
            "has_images": bool(state.images),
            "panel_enabled": state.enable_panel,
            "dose_planning_allowed": bool(
                allow_dose_planning and state.role == "oncologist"),
            "facts_keys": sorted(state.facts.keys()),
        }
        try:
            response = self.llm.chat(
                [
                    {"role": "system", "content": PLANNER_SYSTEM_PROMPT.format(
                        catalog="\n".join(f"- {k}: {v}" for k, v in AGENT_CATALOG.items()))},
                    {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                ],
                temperature=0.0, max_tokens=900, response_format_json=True,
            )
            state.budget.charge_llm_tokens(response.total_tokens)
        except (LLMError, Exception) as exc:  # noqa: BLE001
            state.budget.refund_llm()
            return f"llm_error:{type(exc).__name__}"
        payload = extract_json(response.text, None)
        if payload is None:
            return "llm_plan_unparseable:no_json"
        tasks, reason = parse_plan(payload)
        if tasks is None:
            return f"llm_plan_unparseable:{reason}"
        ok, rejection = validate_plan(tasks, state,
                                      allow_dose_planning=allow_dose_planning)
        if not ok:
            return f"llm_plan_rejected:{rejection}"
        state.tasks = tasks
        return "llm_plan_accepted"
