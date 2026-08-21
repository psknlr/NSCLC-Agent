"""Interview layer: axes, adequacy verdicts, the ask loop's five gates."""

import json

from nsclc_agent.interview import (
    AXES_BY_ID, AdequacyJudge, InterviewLoop, coverage, plan_next,
    required_open_axes, workup_plan,
)
from nsclc_agent.llm.base import LLMResponse, ToolCall
from nsclc_agent.state import Budget

FULL_SCREEN = {
    "emergency_neuro_screen": "negative_by_history",
    "emergency_airway_screen": "negative_by_history",
    "emergency_fever_screen": "negative_by_history",
}
STAGED = {
    "tnm": {"t": "T2a", "n": "N0", "m": "M0"},
    "histologic_category": "adenocarcinoma",
}


def _facts(**extra):
    facts = dict(FULL_SCREEN)
    facts.update(STAGED)
    facts.update(extra)
    return facts


# --------------------------------------------------------------------- axes

def test_red_flag_axes_always_required():
    open_axes = required_open_axes({}, "腰痛")
    assert {a.axis_id for a in open_axes} >= {
        "onc_emergency_neuro", "onc_emergency_airway", "onc_emergency_infection"}


def test_answered_screen_closes_red_flags():
    open_axes = required_open_axes(_facts(), "presentation")
    assert not any(a.tier == "RED_FLAG" for a in open_axes)


def test_staging_axes_close_on_tnm():
    report = coverage(_facts(), "case")
    assert "t_resolution" in report["answered"]
    assert "m_resolution" in report["answered"]
    report2 = coverage(dict(FULL_SCREEN), "case")
    assert "m_resolution" in report2["open"]


def test_biomarker_axes_required_only_when_prescriptive():
    facts = _facts()
    open_default = {a.axis_id for a in required_open_axes(facts, "x")}
    open_rx = {a.axis_id for a in required_open_axes(facts, "x", prescriptive=True)}
    assert "egfr_status" not in open_default
    assert "egfr_status" in open_rx


def test_squamous_carveout_for_driver_axes():
    facts = _facts(histologic_category="squamous")
    open_rx = {a.axis_id for a in required_open_axes(facts, "x", prescriptive=True)}
    assert "egfr_status" not in open_rx
    assert "pdl1_status" in open_rx


def test_plan_next_orders_by_tier():
    plan = plan_next({}, "case", limit=4)
    tiers = [AXES_BY_ID[a].tier for a in plan.axis_ids]
    assert tiers[0] == "RED_FLAG"


def test_workup_plan_names_resolving_tests():
    steps = workup_plan(dict(FULL_SCREEN), "case")
    by_axis = {s["axis_id"]: s for s in steps}
    assert "brain MRI" in by_axis["m_resolution"]["test"]
    assert "EBUS" in by_axis["n_resolution"]["test"]


# ----------------------------------------------------------------- adequacy

def test_blocked_verdict_when_red_flags_never_answered():
    judge = AdequacyJudge()
    verdict = None
    for round_index in range(4):
        verdict = judge.judge({}, "chest pain", rounds_used=round_index)
    assert verdict.verdict == "blocked"
    assert not verdict.may_proceed


def test_achieved_when_everything_closed():
    facts = _facts(ecog_ps=1, medications=[], smoking_history="never",
                   weight_loss="none", goals_of_care="cure",
                   comorbidities={"active_autoimmune": False, "ild": False},
                   ngs_done=True,
                   organ_function={"ppo_fev1_pct": 80},
                   pd_l1={"tps": 30},
                   driver_mutations={"egfr": "negative", "alk": "negative"})
    verdict = AdequacyJudge().judge(facts, "resected adenocarcinoma")
    assert verdict.verdict == "achieved"


def test_stall_proceeds_with_deficit_when_not_blocking():
    facts = _facts()  # red flags closed; non-required axes stay open
    judge = AdequacyJudge(stall_threshold=3)
    verdict = None
    for round_index in range(5):
        verdict = judge.judge(facts, "case", rounds_used=round_index)
    assert verdict.verdict in ("stalled", "achieved", "cap_reached")
    if verdict.verdict == "stalled":
        assert verdict.may_proceed and verdict.deficit


def test_cap_reached_fails_open_without_red_flags():
    facts = _facts()
    judge = AdequacyJudge(max_rounds=2, stall_threshold=99)
    verdict = judge.judge(facts, "case", rounds_used=2)
    assert verdict.verdict in ("cap_reached", "achieved")


# --------------------------------------------------------------------- loop

class AskingLLM:
    """Answers ask rounds with a scripted ask_case_question call.

    The interview prompt itself mentions the reviewer, so the discriminator
    is the composer prompt's own marker (问诊智能体), not "审核者".
    """

    name = "asker"
    model = "asker"
    available = True

    def __init__(self, questions):
        self.questions = questions

    def chat(self, messages, **kwargs):
        system = messages[0]["content"]
        if "问诊智能体" not in system:  # the adequacy verifier's turn
            return LLMResponse(text=json.dumps(
                {"adequate": False, "missing_axes": [], "reason": "",
                 "contradictions": []}))
        return LLMResponse(
            finish_reason="tool_calls",
            tool_calls=[ToolCall("ask_case_question",
                                 {"questions": self.questions}, id="a1")],
        )


def test_probe_bank_fallback_without_model():
    loop = InterviewLoop(None)
    round_ = loop.next_round({}, "cough and weight loss")
    assert round_.composer == "probe_bank"
    assert round_.questions
    assert all(q.origin == "probe_bank" for q in round_.questions)


def test_model_questions_validated_and_advice_rejected():
    llm = AskingLLM([
        {"axis_id": "m_resolution", "question": "PET-CT做了吗？"},
        {"axis_id": "made_up_axis", "question": "随便问问？"},
        {"axis_id": "ecog_ps", "question": "建议你服用奥希替尼，另外体力如何？"},
        {"axis_id": "egfr_status", "question": "先吃 80 mg 再说，EGFR结果呢？"},
    ])
    loop = InterviewLoop(llm)
    round_ = loop.next_round(_facts(), "case", budget=Budget())
    kept = {q.axis_id for q in round_.questions if q.origin == "llm"}
    assert "m_resolution" in kept
    assert "made_up_axis" not in kept
    assert "ecog_ps" not in kept          # advice rejected
    assert "egfr_status" not in kept      # dose rejected
    assert any("未知问诊轴" in r for r in round_.rejected)
    assert any("诊疗建议" in r for r in round_.rejected)
    assert any("剂量" in r for r in round_.rejected)


def test_skipped_required_axis_backfilled():
    llm = AskingLLM([
        {"axis_id": "smoking_history", "question": "吸烟吗？"},
    ])
    loop = InterviewLoop(llm)
    # Red flags open and required — the model ignored them.
    round_ = loop.next_round({}, "chest pain", budget=Budget())
    axes = {q.axis_id for q in round_.questions}
    assert any(AXES_BY_ID[a].tier == "RED_FLAG" for a in axes)
    assert any("必答轴被模型遗漏" in r for r in round_.rejected)


def test_model_complete_claim_is_only_a_proposal():
    class ClaimsDone(AskingLLM):
        def chat(self, messages, **kwargs):
            system = messages[0]["content"]
            if "问诊智能体" not in system:
                return LLMResponse(text=json.dumps(
                    {"adequate": True, "missing_axes": [], "reason": "done",
                     "contradictions": []}))
            return LLMResponse(
                finish_reason="tool_calls",
                tool_calls=[ToolCall("ask_case_question",
                                     {"questions": [], "interview_complete": True},
                                     id="a1")])

    loop = InterviewLoop(ClaimsDone([]))
    round_ = loop.next_round({}, "chest pain", budget=Budget())
    # Required red-flag axes are open, so the judge does NOT accept completion.
    assert round_.verdict.verdict == "not_achieved"
    assert round_.questions  # probe bank filled in
