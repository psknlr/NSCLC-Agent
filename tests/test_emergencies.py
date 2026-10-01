"""Oncologic emergency screen: negation scope, escalation, action plan."""

import pytest

from nsclc_agent.safety import emergencies
from nsclc_agent.safety.emergencies import screen


def _screen(text):
    return emergencies.screen(text)


def test_hard_hit_cord_compression():
    result = _screen("双腿无力加重，今天尿潴留，会阴发麻")
    assert result.emergency
    assert {h["signal_id"] for h in result.hard_hits} == {"cord_compression"}


def test_negation_closes_not_raises():
    result = _screen("没有大咯血，大小便失禁也没有出现")
    assert not result.emergency
    assert "massive_hemoptysis" in result.negated
    assert "cord_compression" in result.negated


def test_clause_scoped_negation_does_not_suppress_other_clause():
    result = _screen("以前没有咯血。今天突然大咯血不止。")
    assert result.emergency
    assert any(h["signal_id"] == "massive_hemoptysis" for h in result.hard_hits)
    # The later assertion wins: the signal is not listed as negated.
    assert "massive_hemoptysis" not in result.negated


def test_third_party_suppressed():
    result = _screen("我父亲当年大咯血去世的，我自己只是咳嗽")
    assert not result.emergency


def test_hypothetical_suppressed():
    result = _screen("如果出现大咯血我该怎么办？")
    assert not result.emergency


def test_soft_hits_do_not_escalate():
    result = _screen("突发胸痛伴气促两小时")
    assert not result.emergency
    assert result.soft_hits


def test_english_narrative():
    result = _screen("New bilateral leg weakness and urinary retention since yesterday.")
    assert result.emergency


def test_fever_on_chemo():
    result = _screen("化疗后发烧39度，寒战明显")
    assert any(h["signal_id"] == "febrile_neutropenia" for h in result.hard_hits)


def test_action_plan_shape():
    result = _screen("大咯血不止")
    plan = emergencies.action_plan(result)
    assert plan["risk_judgement"] == "oncologic_emergency"
    assert plan["signals"] and plan["immediate_actions"]
    assert plan["do_not"] and plan["escalate_if"]


# ---------------------------------------------------------------- English screen
# (the English UI makes English narratives first-class input)

@pytest.mark.parametrize("text,signal", [
    ("sudden massive hemoptysis that will not stop", "massive_hemoptysis"),
    ("Febrile neutropenia on chemotherapy", "febrile_neutropenia"),
    ("fever 38.5 two days after chemotherapy", "febrile_neutropenia"),
    ("neutropenic fever", "febrile_neutropenia"),
    ("spinal cord compression", "cord_compression"),
    ("superior vena cava obstruction", "svc_syndrome"),
])
def test_english_emergencies_escalate(text, signal):
    assert signal in [h["signal_id"] for h in screen(text).hard_hits]


def test_english_negation_must_precede_the_symptom():
    assert screen("denies hemoptysis").negated == ["massive_hemoptysis"]
    assert screen("patient has not had any hemoptysis").negated == ["massive_hemoptysis"]
    assert not screen("massive hemoptysis that is not controlled").negated


def test_chemotherapy_is_not_a_third_party_cue():
    """'che-mother-apy' once matched the third-party cue 'mother'."""
    assert screen("massive hemoptysis during chemotherapy").hard_hits
    assert not screen("my mother had massive hemoptysis").hard_hits
