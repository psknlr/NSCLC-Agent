"""Comorbidity, fitness and pregnancy cautions (v1.4.1 red-team findings).

A kidney-transplant recipient was released pembrolizumab, ECOG 3 a
chemo-immunotherapy doublet, ILD osimertinib and a pregnant patient
pembrolizumab — each with no finding, and the chat reply never showed a
caution even when one fired. "No history of ILD, autoimmune disease or
organ transplant" read as a transplant recipient."""

import pytest

from nsclc_agent.conversation import ConsultationSession, compose_reply
from nsclc_agent.knowledge.labs import extract_labs
from nsclc_agent.safety.rules import check_plan

STAGING_IVB = {"stage_group": "IVB", "n_category": "N2b"}
DRIVER_NEG = {"driver_mutations": {g: "negative" for g in (
    "egfr", "alk", "ros1", "ret", "met", "braf", "kras", "erbb2", "ntrk")},
    "histologic_category": "adenocarcinoma", "pd_l1": {"tps": 80}, "ecog_ps": 1}
EGFR_POS = {**DRIVER_NEG, "driver_mutations": {**DRIVER_NEG["driver_mutations"],
                                               "egfr": "L858R"}}


def _ids(facts, regimens):
    return {v.rule_id for v in check_plan(STAGING_IVB, facts,
                                          {"regimen_ids": regimens, "options": []})}


def _with(base, **comorbid):
    return {**base, "comorbidities": comorbid}


@pytest.mark.parametrize("facts,regimens,rule", [
    (_with(DRIVER_NEG, organ_transplant=True), ["pembro_monotherapy"], "ICI_COMORBIDITY_CAUTION"),
    ({**DRIVER_NEG, "ecog_ps": 3}, ["pembro_pemetrexed_platinum"], "PS_GATE"),
    ({**DRIVER_NEG, "ecog_ps": 4}, ["pembro_monotherapy"], "PS_GATE"),
    (_with(EGFR_POS, ild=True), ["osimertinib_first_line"], "ILD_PNEUMONITIS_RISK"),
    (_with(DRIVER_NEG, pregnancy=True), ["pembro_monotherapy"], "PREGNANCY_CAUTION"),
])
def test_caution_fires(facts, regimens, rule):
    assert rule in _ids(facts, regimens)


@pytest.mark.parametrize("facts,regimens,rule", [
    # no checkpoint inhibitor planned for the transplant recipient
    (_with(EGFR_POS, organ_transplant=True), ["osimertinib_first_line"], "ICI_COMORBIDITY_CAUTION"),
    # an explicit "no" is not a "yes"
    (_with(DRIVER_NEG, organ_transplant=False, ild=False, pregnancy=False),
     ["pembro_monotherapy"], "ICI_COMORBIDITY_CAUTION"),
    (_with(DRIVER_NEG, pregnancy=False), ["pembro_monotherapy"], "PREGNANCY_CAUTION"),
    # targeted therapy for a driver stays an option at poor PS
    ({**EGFR_POS, "ecog_ps": 3}, ["osimertinib_first_line"], "PS_GATE"),
    ({**DRIVER_NEG, "ecog_ps": 2}, ["pembro_monotherapy"], "PS_GATE"),
    (_with(EGFR_POS, ild=False), ["osimertinib_first_line"], "ILD_PNEUMONITIS_RISK"),
])
def test_caution_stays_quiet(facts, regimens, rule):
    assert rule not in _ids(facts, regimens)


@pytest.mark.parametrize("text,expected", [
    ("No history of ILD, autoimmune disease or organ transplant.",
     {"ild": False, "autoimmune_disease": False, "organ_transplant": False}),
    ("No known history of kidney transplant.", {"organ_transplant": False}),
    ("无间质性肺病、自身免疫病及器官移植史。",
     {"ild": False, "autoimmune_disease": False, "organ_transplant": False}),
    # a full-width comma ends the negation; so does anything asserted
    ("无高血压，糖尿病10年。", {"hypertension": False, "diabetes": True}),
    ("No ILD, kidney transplant 2015, on tacrolimus.", {"ild": False, "organ_transplant": True}),
    ("Without ILD, kidney transplant recipient.", {"ild": False, "organ_transplant": True}),
    ("No ILD but a kidney transplant in 2015.", {"ild": False, "organ_transplant": True}),
    # someone else's history says nothing about the patient
    ("No family history of lupus. Mother has rheumatoid arthritis.", None),
])
def test_comorbidity_negation_scope(text, expected):
    assert extract_labs(text).get("comorbidities") == expected


@pytest.mark.parametrize("text,expected", [
    ("She is currently 20 weeks pregnant.", True),
    ("Pregnant at 22 weeks of gestation.", True),
    ("妊娠20周。", True),
    ("孕24周，发现肺腺癌。", True),
    ("Pregnancy test positive.", True),
    ("Postmenopausal, not pregnant.", False),
    ("Non-pregnant.", False),
    ("Urine pregnancy test was negative.", False),
    ("β-hCG negative.", False),
    ("未怀孕。", False),
    ("尿妊娠试验阴性。", False),
    ("Pregnancy: no.", False),
    ("History of gestational diabetes; two prior pregnancies.", None),
    ("Counselled to avoid pregnancy during treatment.", None),
    ("Pregnancy status unknown.", None),
    ("Is she pregnant?", None),
    ("She wishes to become pregnant in the future.", None),
    ("Her daughter is pregnant.", None),
    ("既往妊娠3次。", None),
])
def test_pregnancy_reading(text, expected):
    assert (extract_labs(text).get("comorbidities") or {}).get("pregnancy") is expected


NARRATIVE = ("34-year-old woman, lung adenocarcinoma cT3N2bM1c2 with liver and bone "
             "metastases, ECOG 1. She is currently 20 weeks pregnant. Brain MRI negative. "
             "No hemoptysis, no headache, no limb weakness. PD-L1 TPS 80%. EGFR, ALK, ROS1, "
             "RET, MET, BRAF, KRAS, HER2, NTRK: negative.")


@pytest.mark.parametrize("role,needle,absent", [
    ("oncologist", "PREGNANCY_CAUTION", None),
    ("patient", "产科", "PREGNANCY_CAUTION"),
])
def test_released_plan_carries_its_caution_into_the_reply(role, needle, absent):
    result = ConsultationSession(role=role).turn(NARRATIVE)
    assert result.state.release_status == "treatment_recommendation"
    assert needle in result.reply
    if absent:
        assert absent not in result.reply   # a patient gets the topic, not the rule text
    assert compose_reply(result.state, role=role) == result.reply
