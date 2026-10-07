"""Independent audit (v1.4.1): the deterministic kernel re-reads a model-led
consult AFTER it is submitted. It never returns to the model, never changes
the consult, and reads the model's notes with gaps filled from the
narrative."""

from __future__ import annotations

from nsclc_agent.agentic import AgentSession
from nsclc_agent.agentic.audit import audit, audit_facts
from nsclc_agent.agentic.commands import expand
from nsclc_agent.llm.base import LLMResponse, ToolCall
from nsclc_agent.llm.mock import MockLLMClient
from tests.test_case_kras_later_line import CASE


class Submit:
    name, model, available, supports_vision = "s", "s-1", True, False

    def __init__(self, consult):
        self.consult = consult
        self.calls = 0

    def chat(self, messages, **_):
        self.calls += 1
        return LLMResponse(text="", finish_reason="tool_calls",
                           tool_calls=[ToolCall("submit_consult", self.consult, id="c")])


WRONG = {"reply": "IVA，帕博利珠单抗单药。", "stage_group": "IVA", "tnm": "cT4N3M1c",
         "intent": "palliative", "biomarker_category": ["NSCL-38"],
         "options": [{"name": "Pembrolizumab", "regimen_ids": ["pembro_monotherapy"]}]}


def test_audit_reads_the_case_independently_and_names_each_disagreement():
    result = audit(WRONG, {}, CASE)
    ids = {f["rule_id"]: f["check"] for f in result["findings"]}
    assert ids["STAGE_DIFFERS_FROM_ENGINE"] == "stage"
    assert ids["CATEGORY_DIFFERS_FROM_CLASSIFIER"] == "biomarker_category"
    assert result["reference"]["stage"]["stage_group"] == "IVB"
    assert result["reference"]["suggested_m"]["m"] == "M1c2"
    assert result["reference"]["biomarker_category"]["codes"] == ["NSCL-26"]
    assert "tnm.m" in result["facts_filled_from_narrative"]


def test_a_pd_l1_category_while_markers_are_untested():
    notes = {"tnm": {"t": "T2a", "n": "N0", "m": "M1b"}, "pd_l1": {"tps": 70},
             "driver_mutations": {"egfr": "negative", "alk": "negative"}}
    result = audit({"stage_group": "IVA", "biomarker_category": ["NSCL-38"], "reply": "x"}, notes)
    hit = [f for f in result["findings"] if f["rule_id"] == "CATEGORY_BEFORE_TESTING"]
    assert hit and "ROS1" in hit[0]["message"]


def test_audit_never_reaches_the_model_and_leaves_the_consult_alone():
    llm = Submit(dict(WRONG))
    session = AgentSession(llm)
    turn = session.turn(CASE)
    calls = llm.calls
    before = dict(turn.consult)
    out = session.local_command("audit")
    assert llm.calls == calls  # no model call
    assert session.turns[-1]["consult"] == before
    assert "STAGE_DIFFERS_FROM_ENGINE" in out["text"] and "独立核对" in out["text"]
    assert out["data"]["findings"]


def test_a_correct_consult_audits_clean():
    session = AgentSession(MockLLMClient())
    session.turn(CASE)
    assert session.local_command("audit")["data"]["findings"] == []
    assert "No consult to audit yet" in AgentSession(MockLLMClient(), config={"language": "en"}) \
        .local_command("audit")["text"]


def test_the_model_notes_win_over_the_narrative():
    facts, filled = audit_facts({"ecog_ps": 2}, "ECOG 1，cT2aN0M0")
    assert facts["ecog_ps"] == 2 and "ecog_ps" not in filled and "tnm.t" in filled


def test_review_command_works_in_both_modes():
    prompt = expand("/review")["prompt"]
    assert "search_regimens" in prompt and "内核辅助模式下也可调用 rule_review" in prompt
    assert expand("/audit") == {"kind": "local", "command": "audit", "arg": ""}
