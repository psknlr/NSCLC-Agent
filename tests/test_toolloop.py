"""The ReAct tool loop: containment gates, repair turn, truncation, citations."""

import json

from nsclc_agent.agents.toolloop import ToolLoop
from nsclc_agent.llm.base import LLMResponse, ToolCall
from nsclc_agent.skills import SkillRegistry
from nsclc_agent.state import CaseRunState
from nsclc_agent.tools import CapabilityBroker, ToolHealth, ToolRegistry


class ScriptedLLM:
    """Plays back a scripted sequence of responses."""

    name = "scripted"
    model = "scripted"
    available = True

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


def _mk(state=None, llm=None):
    state = state or CaseRunState(complaint="test")
    skills = SkillRegistry.discover()
    broker = CapabilityBroker(
        "oncologist", "routine", budget=state.budget,
        skill_registry=skills, active_skill="nsclc.treatment",
        health=ToolHealth(),
    )
    loop = ToolLoop(
        llm, ToolRegistry(), broker, state,
        agent_name="TreatmentAgent", skill_id="nsclc.treatment",
        skill_spec=skills.get("nsclc.treatment"),
    )
    return loop, state


VALID_PLAN = {
    "intent": "curative", "summary": "ok",
    "options": [{"name": "x", "regimen_ids": [], "rationale": "y"}],
    "regimen_ids": [],
}


def _final(payload, **kw):
    return LLMResponse(text=json.dumps(payload), finish_reason="stop", **kw)


def _tool_call(name, arguments):
    return LLMResponse(finish_reason="tool_calls",
                       tool_calls=[ToolCall(name, arguments, id="c1")])


def test_happy_path_with_citation():
    llm = ScriptedLLM([
        _tool_call("trial_lookup", {"query": "PACIFIC"}),
        None,  # replaced below once we know the evidence id
    ])

    # Two-phase: run once to learn the evidence id convention (E0001).
    plan = dict(VALID_PLAN)
    plan["citations"] = ["E0001"]
    llm.responses[1] = _final(plan)
    loop, state = _mk(llm=llm)
    result = loop.run("objective", {}, "TreatmentPlan")
    assert result.ok and result.mode == "llm_tool_loop"
    assert result.evidence_ids == ["E0001"]
    assert result.citations == ["E0001"]
    assert "E0001" in state.evidence


def test_unknown_citations_filtered():
    plan = dict(VALID_PLAN)
    plan["citations"] = ["E9999", "made_up"]
    loop, _ = _mk(llm=ScriptedLLM([_final(plan)]))
    result = loop.run("objective", {}, "TreatmentPlan")
    assert result.ok
    assert result.citations == []


def test_dose_in_output_no_retry():
    bad = dict(VALID_PLAN)
    bad["summary"] = "pembrolizumab 200 mg q3w"
    llm = ScriptedLLM([_final(bad), _final(VALID_PLAN)])
    loop, state = _mk(llm=llm)
    result = loop.run("objective", {}, "TreatmentPlan")
    assert not result.ok
    assert result.mode == "dose_in_output"
    assert llm.calls == 1  # no second chance for a dose leak
    assert any("dose" in w or "fallback" in w for w in state.warnings)


def test_schema_violation_gets_one_repair():
    llm = ScriptedLLM([
        _final({"wrong": "shape"}),
        _final(VALID_PLAN),
    ])
    loop, _ = _mk(llm=llm)
    result = loop.run("objective", {}, "TreatmentPlan")
    assert result.ok and result.repairs == 1


def test_prose_gets_one_repair_then_fails():
    llm = ScriptedLLM([
        LLMResponse(text="Let me think about this case...", finish_reason="stop"),
        LLMResponse(text="Still prose, not JSON.", finish_reason="stop"),
    ])
    loop, _ = _mk(llm=llm)
    result = loop.run("objective", {}, "TreatmentPlan")
    assert not result.ok and result.mode == "invalid_output"
    assert result.repairs == 1


def test_truncation_is_a_failure_not_a_result():
    llm = ScriptedLLM([
        LLMResponse(text='{"intent": "cur', finish_reason="length"),
    ])
    loop, state = _mk(llm=llm)
    result = loop.run("objective", {}, "TreatmentPlan")
    assert not result.ok and result.mode == "output_truncated"
    assert llm.calls == 1  # truncation is not repairable at the same ceiling


def test_hallucinated_tool_name_recoverable():
    llm = ScriptedLLM([
        _tool_call("summon_oncologist", {}),
        _final(VALID_PLAN),
    ])
    loop, state = _mk(llm=llm)
    result = loop.run("objective", {}, "TreatmentPlan")
    assert result.ok
    # The hallucinated call produced no ledger entry.
    assert not state.evidence


def test_forbidden_tool_denied_by_broker():
    llm = ScriptedLLM([
        _tool_call("regimen_detail", {"regimen_id": "osimertinib_adjuvant"}),
        _final(VALID_PLAN),
    ])
    loop, state = _mk(llm=llm)
    result = loop.run("objective", {}, "TreatmentPlan")
    assert result.ok
    # The denial is recorded as a failed evidence event, not a dose payload.
    denied = [e for e in state.evidence.values() if e.source == "regimen_detail"]
    assert denied and all("80 mg" not in json_dump(e.payload) for e in denied)


def json_dump(payload):
    return json.dumps(payload, ensure_ascii=False)


def test_tool_schemas_filtered_to_skill():
    loop, _ = _mk(llm=ScriptedLLM([]))
    visible = {s.name for s in loop.allowed_tool_specs()}
    assert "trial_lookup" in visible
    assert "regimen_detail" not in visible
    assert "dose_gate_check" not in visible


def test_budget_exhaustion_stops_loop():
    state = CaseRunState(complaint="x")
    state.budget.max_llm_calls = 0
    loop, _ = _mk(state=state, llm=ScriptedLLM([_final(VALID_PLAN)]))
    result = loop.run("objective", {}, "TreatmentPlan")
    assert not result.ok and result.mode == "llm_budget_exhausted"


def test_no_llm_reports_unavailable():
    loop, _ = _mk(llm=None)
    result = loop.run("objective", {}, "TreatmentPlan")
    assert result.mode == "llm_unavailable"
