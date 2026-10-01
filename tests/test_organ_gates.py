"""Organ-function and comorbidity gates (v0.7.0).

Pinned here:

* one deterministic evaluator serves three consumers — the dose
  channel's gate check, the critic's ORGAN_FUNCTION_GATE block, and
  the planner's organ-gate ledger;
* quantitative first: CrCl < 45 fails the pemetrexed gate with the
  label threshold in the note; bilirubin above ULN and Child-Pugh B/C
  fail hepatic; QTc ≥ 500 fails and 481–499 is "resolve first";
  hemoptysis history fails the ramucirumab component while docetaxel
  monotherapy stays clean; ILD history fails the ADC gate; a strong
  CYP3A4 inducer on the medication list fails the TKI gate;
* UNKNOWN never blocks a recommendation and never passes a dose gate —
  recommending is not dosing; pending gates live in the plan's
  organ-gate ledger, not in workup_needed (they must not flip the
  release status of every plan without lab values);
* the planner drops an organ-failed regimen loudly and routes to the
  MDT — the dose is never "adjusted around" a failed gate.
"""

from __future__ import annotations

from nsclc_agent import Case, NSCLCRunner
from nsclc_agent.knowledge.organ_gates import (
    ORGAN_GATES,
    evaluate_gate,
    failed_gates,
    unknown_gates,
)
from nsclc_agent.safety import rules

SN = "No hemoptysis, no leg weakness, no fever."


def _check(stage, facts, plan):
    return rules.check_plan({"stage_group": stage}, {"tnm": {}, **facts}, plan)


# ------------------------------------------------------------ the evaluator

def test_renal_quantitative_and_string_forms():
    fail = evaluate_gate("renal_function",
                         {"organ_function": {"renal": {"crcl_ml_min": 38}}})
    assert fail["status"] == "fail" and "45" in fail["note"]
    ok = evaluate_gate("renal_function",
                       {"organ_function": {"renal": {"crcl_ml_min": 72}}})
    assert ok["status"] == "pass"
    legacy = evaluate_gate("renal_function",
                           {"organ_function": {"renal": "normal"}})
    assert legacy["status"] == "pass"
    assert evaluate_gate("renal_function", {})["status"] == "unverified"


def test_hepatic_and_qtc_thresholds():
    assert evaluate_gate("hepatic_baseline", {"organ_function": {
        "hepatic": {"bilirubin_uln": 2.0}}})["status"] == "fail"
    assert evaluate_gate("hepatic_baseline", {"organ_function": {
        "hepatic": {"child_pugh": "B"}}})["status"] == "fail"
    assert evaluate_gate("hepatic_baseline", {"organ_function": {
        "hepatic": {"bilirubin_uln": 0.8}}})["status"] == "pass"
    assert evaluate_gate("qtc_baseline",
                         {"qtc_ms": 510})["status"] == "fail"
    assert evaluate_gate("qtc_baseline",
                         {"qtc_ms": 490})["status"] == "unverified"
    assert evaluate_gate("qtc_baseline",
                         {"qtc_ms": 430})["status"] == "pass"


def test_cyp3a4_medication_list():
    assert evaluate_gate("no_strong_cyp3a4_inducer", {
        "medications": ["metformin", "rifampin"]})["status"] == "fail"
    assert evaluate_gate("no_strong_cyp3a4_inducer", {
        "medications": ["metformin"]})["status"] == "pass"
    assert evaluate_gate("no_strong_cyp3a4_inducer",
                         {})["status"] == "unverified"


def test_non_organ_gates_return_none():
    assert evaluate_gate("egfr_positive", {}) is None
    assert "egfr_positive" not in ORGAN_GATES


# ------------------------------------------------------------- the critic

def test_crcl_38_blocks_the_pemetrexed_backbone():
    found = _check("IVA", {
        "driver_mutations": {"egfr": "negative", "alk": "negative"},
        "histologic_category": "adenocarcinoma",
        "organ_function": {"renal": {"crcl_ml_min": 38}}},
        {"regimen_ids": ["pembro_pemetrexed_platinum"], "options": []})
    assert any(v.rule_id == "ORGAN_FUNCTION_GATE" and v.severity == "block"
               for v in found)


def test_bleeding_risk_blocks_ramucirumab_not_docetaxel_mono():
    facts = {"driver_mutations": {"egfr": "negative", "alk": "negative"},
             "histologic_category": "adenocarcinoma",
             "bleeding_risk": {"hemoptysis_history": True},
             "treatment_history": [{"line": 1,
                                    "agents": ["pembrolizumab",
                                               "carboplatin", "pemetrexed"],
                                    "status": "progression"}]}
    found = _check("IVB", facts, {"regimen_ids":
                                  ["docetaxel_ramucirumab_second_line"],
                                  "options": []})
    assert any(v.rule_id == "ORGAN_FUNCTION_GATE" for v in found)
    found = _check("IVB", facts, {"regimen_ids": ["docetaxel_second_line"],
                                  "options": []})
    assert not any(v.rule_id == "ORGAN_FUNCTION_GATE" for v in found)


def test_ild_history_blocks_the_adcs():
    facts = {"driver_mutations": {"erbb2": "YVMA insertion",
                                  "egfr": "negative", "alk": "negative"},
             "histologic_category": "adenocarcinoma",
             "comorbidities": {"ild": True},
             "treatment_history": [{"line": 1,
                                    "agents": ["carboplatin", "pemetrexed"],
                                    "status": "progression"}]}
    found = _check("IVB", facts, {"regimen_ids": ["tdxd_subsequent_line"],
                                  "options": []})
    assert any(v.rule_id == "ORGAN_FUNCTION_GATE" and v.severity == "block"
               for v in found)


def test_unknown_never_blocks_the_recommendation():
    found = _check("IVA", {
        "driver_mutations": {"egfr": "negative", "alk": "negative"},
        "histologic_category": "adenocarcinoma"},
        {"regimen_ids": ["pembro_pemetrexed_platinum"], "options": []})
    assert not any(v.rule_id == "ORGAN_FUNCTION_GATE" for v in found)


# ------------------------------------------------------------ the planner

def test_planner_drops_the_organ_failed_backbone_loudly():
    state = NSCLCRunner().run_case(Case(
        t="T2a", n="N0", m="M1b",
        presentation=f"Adenocarcinoma, adrenal met, brain MRI negative. {SN}",
        facts={"driver_mutations": {"egfr": "negative", "alk": "negative"},
               "histologic_category": "adenocarcinoma", "ngs_done": True,
               "ecog_ps": 1,
               "organ_function": {"renal": {"crcl_ml_min": 38}}}))
    plan = state.outputs["treatment_plan"]
    assert "pembro_pemetrexed_platinum" not in plan["regimen_ids"]
    assert plan["mdt_referral"] is True
    assert any("器官功能" in u for u in plan["uncertainties"])
    violations = state.outputs["safety_audit"]["violations"]
    assert not any(v["rule_id"] == "ORGAN_FUNCTION_GATE"
                   for v in violations)  # nothing failed SHIPPED


def test_pending_gates_live_in_the_ledger_not_in_workup():
    state = NSCLCRunner().run_case(Case(
        t="T2a", n="N0", m="M1b",
        presentation=f"Adenocarcinoma, adrenal met, brain MRI negative. {SN}",
        facts={"driver_mutations": {"egfr": "negative", "alk": "negative"},
               "histologic_category": "adenocarcinoma", "ngs_done": True,
               "ecog_ps": 1,
               "organ_function": {"renal": {"crcl_ml_min": 72}}}))
    plan = state.outputs["treatment_plan"]
    assert state.release_status == "treatment_recommendation"
    ledger = plan["organ_gates"]
    assert ledger["failed"] == []
    assert any("B12/folate" in p for p in ledger["pending"])
    # Pending gates must NOT flip the release via workup_needed.
    assert not any("B12" in w for w in plan["workup_needed"])


def test_helpers_read_the_regimen_gate_list():
    facts = {"organ_function": {"renal": {"crcl_ml_min": 38}}}
    assert any(f["gate"] == "renal_function"
               for f in failed_gates("pembro_pemetrexed_platinum", facts))
    assert any("B12/folate" in u
               for u in unknown_gates("pembro_pemetrexed_platinum", facts))
    assert failed_gates("nonexistent_regimen", facts) == []
