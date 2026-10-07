"""Full autonomy (the default since v1.3.0): the model decides the stage,
the treatment intent and the plan. The kernel's decision tools are not
offered, no hook compares the model with the kernel, and the case notes
never echo an engine stage. Kernel-assisted mode stays one setting away."""

from __future__ import annotations

from nsclc_agent.agentic import AgentSession
from nsclc_agent.agentic.hooks import KERNEL_HOOKS
from nsclc_agent.agentic.prompts import SPECIALIST_AUTONOMY
from nsclc_agent.agentic.session import AgentConfig, runtime_catalog
from nsclc_agent.agentic.toolbox import KERNEL_TOOLS
from nsclc_agent.llm.base import LLMResponse, ToolCall
from nsclc_agent.llm.mock import MockLLMClient


class Scripted:
    """Replays tool calls; records every system prompt it was given."""

    name, model, available, supports_vision = "scripted", "scripted-1", True, False

    def __init__(self, *responses: LLMResponse) -> None:
        self.responses = list(responses)
        self.systems: list[str] = []
        self.tool_names: list[set[str]] = []

    def chat(self, messages, *, tools=None, **_):
        self.systems.append(messages[0]["content"])
        self.tool_names.append({t.name for t in tools or []})
        return self.responses.pop(0) if self.responses else LLMResponse(text="done")


def call(name: str, args: dict, cid: str = "c") -> LLMResponse:
    return LLMResponse(text="", finish_reason="tool_calls", tool_calls=[ToolCall(name, args, id=cid)])


def test_full_autonomy_is_the_default_and_offers_no_kernel_decisions():
    assert AgentConfig().autonomy == "full"
    assert AgentConfig.from_dict({"autonomy": "nonsense"}).autonomy == "full"
    session = AgentSession(MockLLMClient())
    names = set(session._toolset()[0].names())
    assert not names & KERNEL_TOOLS
    assert {"record_case_facts", "search_trials", "search_regimens", "guideline_search",
            "pubmed_search", "submit_consult", "delegate"} <= names
    assert not any(session.hooks.enabled[h] for h in KERNEL_HOOKS)
    assert all(session.hooks.enabled[h] for h in
               ("emergency_screen", "citation_provenance", "dose_provenance",
                "emergency_addressed"))
    catalog = runtime_catalog()
    assert not {t["name"] for t in catalog["tools"]} & KERNEL_TOOLS
    assistant = runtime_catalog({"autonomy": "assisted"})
    assert KERNEL_TOOLS <= {t["name"] for t in assistant["tools"]}


def test_the_model_stage_and_intent_stand_without_any_kernel_finding():
    llm = Scripted(
        call("record_case_facts", {"facts": {"tnm": {"t": "T4", "n": "N3", "m": "M1c"},
                                             "stage_group": "IVB"}}),
        call("submit_consult", {"reply": "IVB，姑息性系统治疗。", "stage_group": "IIIC",
                                "tnm": "cT4N3M1c", "stage_rationale": "model reading",
                                "intent": "palliative", "intent_rationale": "M1c"}))
    session = AgentSession(llm)
    turn = session.turn("右上肺腺癌 cT4N3M1c")
    assert "你的决策" in llm.systems[0] and "stage_tnm" not in llm.tool_names[0]
    notes_call = next(s for s in turn.steps if s.get("name") == "record_case_facts")
    assert notes_call["ok"]
    # no engine stage anywhere the model or the clinician reads
    assert turn.engine_stage is None
    out = session.toolbox.record_case_facts({"ecog_ps": 1})
    assert "engine_stage" not in out["data"]
    # the model staged IIIC against its own notes: no STAGE_DIFFERS finding
    assert turn.review["findings"] == []
    assert turn.consult["stage_group"] == "IIIC" and turn.consult["intent"] == "palliative"
    # tools default to the stage the MODEL recorded, never the engine's
    session.toolbox.record_case_facts({"stage_group": "IIIA"})
    assert session.toolbox.prognosis()["summary"].startswith("IIIA")


def test_missing_intent_is_recorded_as_undetermined_not_inferred():
    turn = AgentSession(Scripted(call("submit_consult", {"reply": "ok", "intent": "maybe"}))).turn("x")
    assert turn.consult["intent"] == "undetermined"


def test_specialists_are_told_they_decide_and_lose_kernel_tools():
    llm = Scripted(
        call("delegate", {"agent": "radiology", "task": "stage it"}),
        call("submit_report", {"report": "IVB"}),
        call("submit_consult", {"reply": "ok", "intent": "palliative"}))
    AgentSession(llm).turn("x")
    specialist_system = llm.systems[1]
    assert SPECIALIST_AUTONOMY.strip() in specialist_system
    assert "stage_tnm" not in llm.tool_names[1] and "submit_report" in llm.tool_names[1]


def test_switching_to_assisted_restores_kernel_tools_and_hooks():
    session = AgentSession(MockLLMClient())
    session.configure({"autonomy": "assisted"})
    assert KERNEL_TOOLS <= set(session._toolset()[0].names())
    assert all(session.hooks.enabled[h] for h in KERNEL_HOOKS)
    # an explicit hook choice wins over the autonomy default
    session.configure({"autonomy": "full", "hooks": {"rule_review": True}})
    assert session.hooks.enabled["rule_review"] and not session.hooks.enabled["fact_seed"]


def test_offline_mock_demonstrates_autonomy_honestly():
    turn = AgentSession(MockLLMClient()).turn("cT2bN2bM0 肺腺癌，EGFR 阴性，ALK 阴性，ECOG 1。")
    tools = [s.get("name") for s in turn.steps if s["kind"] == "tool"]
    assert "governed_reference" not in tools and "record_case_facts" in tools
    assert turn.consult["stage_group"] == "IIIB"
    assert turn.consult["intent"] in ("curative", "undetermined")
    assert turn.consult["stage_rationale"] and turn.consult["intent_rationale"]
    assert "接入真实模型后，这些都由模型自主判断" in turn.reply
