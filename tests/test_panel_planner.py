"""MDT panel merge semantics + planner proposal validation."""

import json

from nsclc_agent.agents.panel import PANEL_URGENCY_ORDER, PanelAgent, PERSONAS
from nsclc_agent.agents.planner import (
    PlannerAgent, default_plan, parse_plan, validate_plan,
)
from nsclc_agent.llm.base import LLMResponse, ToolCall
from nsclc_agent.skills import SkillRegistry
from nsclc_agent.state import CaseRunState
from nsclc_agent.tools import ToolRegistry


class _Factory:
    """Broker factory standing in for the runner's."""

    def __init__(self, state):
        self.skills = SkillRegistry.discover()
        self.state = state
        from nsclc_agent.tools import CapabilityBroker, ToolHealth

        self.health = ToolHealth()
        self._broker_cls = CapabilityBroker

    def __call__(self, skill_id):
        return self._broker_cls(
            self.state.role, self.state.risk_mode, budget=self.state.budget,
            skill_registry=self.skills, active_skill=skill_id,
            health=self.health)

    def skill_spec(self, skill_id):
        return self.skills.get(skill_id)


class PanelLLM:
    """One member says urgent with dissent; the rest say routine."""

    name = "panel"
    model = "panel"
    available = True

    def chat(self, messages, **kwargs):
        system = messages[0]["content"]
        if not any(m.get("role") == "tool" for m in messages):
            return LLMResponse(
                finish_reason="tool_calls",
                tool_calls=[ToolCall("trial_lookup", {"query": "PACIFIC"}, id="c")])
        urgent = "THORACIC SURGEON" in system
        return LLMResponse(text=json.dumps({
            "urgency": "urgent" if urgent else "routine",
            "key_findings": ["finding"],
            "concerns": (["invasive mediastinal staging incomplete"]
                         if urgent else []),
            "recommend_next": ["EBUS station mapping"],
            "dissent": "not resectable as presented" if urgent else "",
            "citations": [],
        }))


def _state():
    state = CaseRunState(complaint="T4N2a case, resectability contested")
    state.staging = {"stage_group": "IIIB", "tnm": "cT4N2aM0",
                     "edition": "AJCC/UICC 9th edition", "n_category": "N2a"}
    return state


def test_panel_conservative_merge_and_dissent():
    state = _state()
    agent = PanelAgent(PanelLLM(), concurrency=1)
    agent.run(state, ToolRegistry(), _Factory(state))
    panel = state.outputs["panel"]
    assert panel["urgency"] == "urgent"          # max, not majority
    assert panel["answered"] == len(PERSONAS)
    assert any("not resectable" in d for d in panel["disagreements"])
    assert "invasive mediastinal staging incomplete" in panel["concerns"]


def test_panel_evidence_ids_deterministic_under_concurrency():
    ledgers = []
    for concurrency in (1, 4):
        state = _state()
        PanelAgent(PanelLLM(), concurrency=concurrency).run(
            state, ToolRegistry(), _Factory(state))
        ledgers.append([
            (eid, e.source) for eid, e in sorted(state.evidence.items())])
    assert ledgers[0] == ledgers[1]


def test_panel_without_model_degrades_loudly():
    state = _state()
    PanelAgent(None).run(state, ToolRegistry(), _Factory(state))
    assert state.outputs["panel"]["answered"] == 0
    assert any("no member" in w for w in state.warnings)


# ------------------------------------------------------------------ planner

def test_parse_plan_shape_tolerance():
    shapes = [
        {"tasks": [{"task_id": "T1", "agent": "StagingAgent", "objective": "s"}]},
        {"plan": [{"id": "T1", "name": "StagingAgent"}]},
        {"steps": [{"step_id": "T1", "agent_name": "StagingAgent"}]},
        [{"task_id": "T1", "agent": "StagingAgent"}],
        {"plan": {"tasks": [{"task_id": "T1", "agent": "StagingAgent"}]}},
        ["StagingAgent"],
    ]
    for shape in shapes:
        tasks, reason = parse_plan(shape)
        assert tasks is not None, (shape, reason)
        assert tasks[0].agent == "StagingAgent"


def test_parse_plan_dependency_aliases_preserved():
    tasks, _ = parse_plan({"tasks": [
        {"id": "T1", "agent": "StagingAgent"},
        {"id": "T2", "agent": "TreatmentAgent", "dependencies": ["T1"]},
    ]})
    assert tasks[1].depends_on == ["T1"]


def test_parse_plan_rejects_fuzzy_bare_strings():
    tasks, reason = parse_plan(["StagingAgnt"])
    assert tasks is None and "exact" in reason


def test_validate_plan_rejects_unknown_agent():
    tasks, _ = parse_plan({"tasks": [{"task_id": "T1", "agent": "DoseInventor"}]})
    ok, reason = validate_plan(tasks, CaseRunState(complaint="x"),
                               allow_dose_planning=False)
    assert not ok and "unknown agent" in reason


def test_validate_plan_rejects_cycles():
    tasks, _ = parse_plan({"tasks": [
        {"task_id": "T1", "agent": "StagingAgent", "depends_on": ["T2"]},
        {"task_id": "T2", "agent": "TreatmentAgent", "depends_on": ["T1"]},
    ]})
    ok, reason = validate_plan(tasks, CaseRunState(complaint="x"),
                               allow_dose_planning=False)
    assert not ok and "cycle" in reason


def test_validate_plan_requires_staging_before_treatment():
    tasks, _ = parse_plan({"tasks": [
        {"task_id": "T1", "agent": "TreatmentAgent"},
    ]})
    ok, reason = validate_plan(tasks, CaseRunState(complaint="x"),
                               allow_dose_planning=False)
    assert not ok and "StagingAgent" in reason


def test_validate_plan_rejects_unauthorized_dose_agent():
    tasks, _ = parse_plan({"tasks": [
        {"task_id": "T1", "agent": "StagingAgent"},
        {"task_id": "T2", "agent": "DosePlanAgent", "depends_on": ["T1"]},
    ]})
    state = CaseRunState(complaint="x", role="patient")
    ok, reason = validate_plan(tasks, state, allow_dose_planning=True)
    assert not ok and "oncologist" in reason


def test_rejected_proposal_falls_back_wholesale():
    class BadPlanner:
        name = "bad"
        model = "bad"
        available = True

        def chat(self, messages, **kwargs):
            return LLMResponse(text=json.dumps({"tasks": [
                {"task_id": "T1", "agent": "DoseInventor"}]}))

    state = CaseRunState(complaint="x")
    PlannerAgent(BadPlanner()).run(state)
    assert state.planner_mode == "rule"
    assert state.outputs["plan"]["note"].startswith("llm_plan_rejected")
    agents = [t.agent for t in state.tasks]
    assert "StagingAgent" in agents and "TreatmentAgent" in agents


def test_default_plan_orders_dependencies():
    state = CaseRunState(complaint="x")
    tasks = default_plan(state)
    by_agent = {t.agent: t for t in tasks}
    staging = by_agent["StagingAgent"]
    treatment = by_agent["TreatmentAgent"]
    assert staging.task_id in treatment.depends_on
    assert PANEL_URGENCY_ORDER["urgent"] > PANEL_URGENCY_ORDER["routine"]
