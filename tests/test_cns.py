"""CNS metastasis stratification (v0.4.0) — the red-team corpus gap.

Pinned here:

* fact normalization is honest: unstated = unknown, never assumed; a
  bare string is tolerated for presence/absence; a negative
  brain-imaging statement in the narrative seeds ``absent`` while a
  POSITIVE imaging statement seeds nothing (prose never asserts disease);
* the stage-IV strategy stratifies instead of prescribing: unknown CNS
  → brain-MRI workup and a provisional posture; asymptomatic untreated
  + CNS-active TKI → upfront systemic with the surveillance caution;
  driver-negative + brain mets → the local-therapy option; symptomatic
  untreated → CNS-directed care LEADS the option list; leptomeningeal →
  the dedicated referral and an honest boundary, no padded strategy;
* the critic blocks independently: a systemic-only plan over
  symptomatic untreated brain mets (or LM) is CNS_UNTREATED_SYMPTOMATIC
  regardless of who authored it, and brain mets recorded against an M0
  TNM is a named contradiction (CNS_TNM_INCONSISTENT), never a silent
  repair.
"""

from __future__ import annotations

from nsclc_agent import Case, NSCLCRunner
from nsclc_agent.knowledge.cns import cns_status, cns_strategy, tnm_conflict
from nsclc_agent.safety import rules

SN = "No hemoptysis, no leg weakness, no fever."


def _check(stage, facts, plan):
    return rules.check_plan({"stage_group": stage}, {"tnm": {}, **facts}, plan)


# ---------------------------------------------------------- normalization

def test_unstated_is_unknown_never_assumed():
    reading = cns_status({})
    assert reading["status"] == "unknown"
    assert reading["symptomatic"] is None
    assert reading["leptomeningeal"] is None


def test_string_and_dict_forms():
    assert cns_status({"cns_metastases": "absent"})["status"] == "absent"
    reading = cns_status({"cns_metastases": {
        "status": "present", "symptomatic": True, "treated": False,
        "burden": "多发", "leptomeningeal": False}})
    assert reading["status"] == "present"
    assert reading["symptomatic"] is True
    assert reading["burden"] == "extensive"


def test_tnm_conflict_only_on_m0_with_present():
    facts = {"cns_metastases": {"status": "present"},
             "tnm": {"t": "T2a", "n": "N0", "m": "M0"}}
    assert tnm_conflict(facts)
    facts["tnm"]["m"] = "M1c1"
    assert tnm_conflict(facts) is None
    assert tnm_conflict({"tnm": {"m": "M0"}}) is None  # unknown ≠ present


# ------------------------------------------------------------- strategy

def test_unknown_cns_routes_to_workup():
    strategy = cns_strategy("IVA", {}, "EGFR")
    assert any("Brain MRI" in w for w in strategy["workup"])
    assert not strategy["options"]


def test_absent_and_non_iv_add_nothing():
    assert cns_strategy("IVA", {"cns_metastases": "absent"}, None) is None
    assert cns_strategy("IIIA", {}, None) is None


def test_cns_active_tki_defers_local_therapy():
    strategy = cns_strategy("IVB", {"cns_metastases": {
        "status": "present", "symptomatic": False, "treated": False}},
        "EGFR")
    assert not strategy["options"]  # no local-first option forced in
    assert any("CNS-active" in c for c in strategy["cautions"])


def test_driver_negative_gets_the_local_therapy_option():
    strategy = cns_strategy("IVB", {"cns_metastases": {
        "status": "present", "symptomatic": False, "treated": False}},
        None)
    assert any("CNS-directed local therapy" in o["name"]
               for o in strategy["options"])


def test_symptomatic_leads_and_leptomeningeal_is_a_boundary():
    strategy = cns_strategy("IVB", {"cns_metastases": {
        "status": "present", "symptomatic": True, "treated": False,
        "leptomeningeal": True}}, "EGFR")
    names = [o["name"] for o in strategy["options"]]
    assert names[0].startswith("CNS-directed local therapy first")
    assert any("neuro-oncology" in n for n in names)
    assert any("NOT encoded" in note for note in strategy["honest_notes"])
    # No option carries a dose numeric and none names a systemic drug.
    assert not any(any(ch.isdigit() for ch in n) for n in names)


# ------------------------------------------------------------ the critic

_SYMPTOMATIC = {"driver_mutations": {"egfr": "negative", "alk": "negative"},
                "histologic_category": "squamous", "pd_l1": {"tps": 5},
                "cns_metastases": {"status": "present", "symptomatic": True,
                                   "treated": False}}


def test_systemic_only_over_symptomatic_cns_is_blocked():
    found = _check("IVB", _SYMPTOMATIC,
                   {"regimen_ids": ["pembro_carbo_taxane"], "options": []})
    assert any(v.rule_id == "CNS_UNTREATED_SYMPTOMATIC"
               and v.severity == "block" for v in found)


def test_cns_addressed_in_plan_is_not_blocked():
    found = _check("IVB", _SYMPTOMATIC, {
        "regimen_ids": ["pembro_carbo_taxane"],
        "options": [{"name": "CNS-directed local therapy first "
                             "(neurosurgery / radiation oncology)",
                     "regimen_ids": []}]})
    assert not any(v.rule_id == "CNS_UNTREATED_SYMPTOMATIC" for v in found)


def test_asymptomatic_or_treated_or_no_regimens_do_not_block():
    calm = dict(_SYMPTOMATIC,
                cns_metastases={"status": "present", "symptomatic": False,
                                "treated": False})
    found = _check("IVB", calm,
                   {"regimen_ids": ["pembro_carbo_taxane"], "options": []})
    assert not any(v.rule_id == "CNS_UNTREATED_SYMPTOMATIC" for v in found)
    found = _check("IVB", _SYMPTOMATIC, {"regimen_ids": [], "options": []})
    assert not any(v.rule_id == "CNS_UNTREATED_SYMPTOMATIC" for v in found)


def test_leptomeningeal_counts_as_dangerous_even_asymptomatic():
    facts = dict(_SYMPTOMATIC, cns_metastases={
        "status": "present", "symptomatic": False, "treated": False,
        "leptomeningeal": True})
    found = _check("IVB", facts,
                   {"regimen_ids": ["pembro_carbo_taxane"], "options": []})
    assert any(v.rule_id == "CNS_UNTREATED_SYMPTOMATIC"
               and "leptomeningeal" in v.message for v in found)


def test_m0_contradiction_is_a_named_warn():
    found = rules.check_plan(
        {"stage_group": "IIIA"},
        {"tnm": {"t": "T2a", "n": "N2a", "m": "M0"},
         "cns_metastases": {"status": "present"}},
        {"regimen_ids": [], "options": []})
    assert any(v.rule_id == "CNS_TNM_INCONSISTENT" and v.severity == "warn"
               for v in found)


# ----------------------------------------------------------- the pipeline

def test_negative_imaging_statement_seeds_absent_but_positive_seeds_nothing():
    state = NSCLCRunner().run_case(Case(
        t="T2a", n="N0", m="M1b",
        presentation=f"Adenocarcinoma, adrenal met, brain MRI negative. "
                     f"EGFR L858R. {SN}",
        facts={"driver_mutations": {"egfr": "L858R", "alk": "negative"},
               "histologic_category": "adenocarcinoma", "ngs_done": True,
               "ecog_ps": 1}))
    assert cns_status(state.facts)["status"] == "absent"
    assert not any("Brain MRI" in w for w in
                   state.outputs["treatment_plan"]["workup_needed"])

    state = NSCLCRunner().run_case(Case(
        t="T2a", n="N0", m="M1c1",
        presentation=f"Adenocarcinoma. Brain MRI shows two new lesions. "
                     f"EGFR L858R. {SN}",
        facts={"driver_mutations": {"egfr": "L858R", "alk": "negative"},
               "histologic_category": "adenocarcinoma", "ngs_done": True,
               "ecog_ps": 1}))
    # Prose never asserts disease: status stays unknown, workup asks.
    assert cns_status(state.facts)["status"] == "unknown"
    assert any("Brain MRI" in w for w in
               state.outputs["treatment_plan"]["workup_needed"])


def test_stage_iv_without_cns_status_gets_the_workup():
    state = NSCLCRunner().run_case(Case(
        t="T2a", n="N0", m="M1b",
        presentation=f"Adenocarcinoma, adrenal met. EGFR L858R. {SN}",
        facts={"driver_mutations": {"egfr": "L858R", "alk": "negative"},
               "histologic_category": "adenocarcinoma", "ngs_done": True,
               "ecog_ps": 1}))
    plan = state.outputs["treatment_plan"]
    assert any("Brain MRI" in w for w in plan["workup_needed"])
    assert any("CNS status is not on record" in u
               for u in plan["uncertainties"])


def test_symptomatic_pipeline_leads_with_cns_and_still_releases():
    state = NSCLCRunner().run_case(Case(
        t="T3", n="N2b", m="M1c2",
        presentation=f"Squamous, multiple brain metastases with headache "
                     f"controlled on steroids. {SN}",
        facts={"driver_mutations": {"egfr": "negative", "alk": "negative"},
               "histologic_category": "squamous", "ngs_done": True,
               "ecog_ps": 1, "pd_l1": {"tps": 5},
               "cns_metastases": {"status": "present", "symptomatic": True,
                                  "treated": False, "burden": "extensive"}}))
    plan = state.outputs["treatment_plan"]
    assert plan["options"][0]["name"].startswith(
        "CNS-directed local therapy first")
    assert state.release_status == "treatment_recommendation"
    violations = state.outputs["safety_audit"]["violations"]
    assert not any(v["rule_id"] == "CNS_UNTREATED_SYMPTOMATIC"
                   for v in violations)
    assert state.outputs["treatment_plan"]["cns"]["reading"][
        "symptomatic"] is True
