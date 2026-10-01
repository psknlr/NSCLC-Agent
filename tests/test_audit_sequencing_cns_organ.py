"""Adversarial-audit regressions: sequencing, CNS, organ gates (v0.7.1).

Every test here pins a defect the full adversarial review reproduced
against v0.7.0:

* driver-positive patients who progressed on chemotherapy but never
  received their targeted agent were routed to docetaxel — the TKI was
  silently skipped (sequencing now defers to the first-line driver table);
* DRIVER_FIRST_LINE downgraded to warn after ANY progression — it now
  requires progression on the driver's targeted therapy;
* later-line predicates accepted "previously treated with anything"
  (MARIPOSA-2 after chemo only, lorlatinib-post-second-gen after chemo
  only) — they now require progression on the specific prior agents;
* free-text history read "PD-L1" / "updated" as progression and ignored
  negation ("no progression");
* an empty agent string made every regimen a same-drug rechallenge, and
  Chinese drug names evaded the same-drug rule;
* first/second-generation EGFR TKI progression had no T790M → AURA3 path;
* positive brain-MRI reports ("unremarkable except for 3 metastases",
  "未见出血，可见多发转移") seeded CNS status "absent";
* the CNS block was disarmed by a marker word ANYWHERE in the plan
  ("SRS not indicated") instead of a CNS-directed option;
* cM0/pM0 escaped the CNS/TNM contradiction check;
* organ gates read "CrCl 38 mL/min" as unknown, and 11 regimens were
  missing component-implied gates (docetaxel's hepatic warning among
  them) because gate lists were hand-maintained.
"""

from __future__ import annotations

from nsclc_agent import Case, NSCLCRunner
from nsclc_agent.agents.catalog import _cns_negative_imaging
from nsclc_agent.knowledge import regimens as regimen_lib
from nsclc_agent.knowledge.cns import tnm_conflict
from nsclc_agent.knowledge.indications import (
    ELIGIBLE,
    INELIGIBLE,
    UNKNOWN,
    evaluate_indication,
)
from nsclc_agent.knowledge.organ_gates import (
    evaluate_gate,
    failed_gates,
    regimen_gates,
)
from nsclc_agent.knowledge.sequencing import (
    sequencing_context,
    treatment_history,
)
from nsclc_agent.safety import rules

SN = "No hemoptysis, no leg weakness, no fever."
_BASE = {"histologic_category": "adenocarcinoma", "ngs_done": True,
         "ecog_ps": 1}
_CHEMO_PD = [{"line": 1, "agents": ["carboplatin", "pemetrexed"],
              "status": "progression"}]
_TNM = {"t": "T2a", "n": "N0", "m": "M1c1"}


def _run(facts, presentation):
    return NSCLCRunner().run_case(Case(
        t="T2a", n="N0", m="M1c1",
        presentation=f"{presentation} Brain MRI negative. {SN}",
        facts=facts))


def _check(facts, regimen_ids, **plan):
    return rules.check_plan({"stage_group": "IVB"},
                            {"tnm": {}, **facts},
                            {"regimen_ids": regimen_ids, "options": [],
                             **plan})


# ------------------------------------------------ TKI-naive after chemo

def test_egfr_positive_tki_naive_after_chemo_gets_the_tki():
    state = _run({**_BASE, "driver_mutations": {"egfr": "L858R",
                                                "alk": "negative"},
                  "treatment_history": _CHEMO_PD},
                 "EGFR L858R found after first-line chemotherapy.")
    plan = state.outputs["treatment_plan"]
    assert "osimertinib_first_line" in plan["regimen_ids"]
    assert not any(r.startswith("docetaxel") for r in plan["regimen_ids"])
    # The chemo-containing FLAURA2 combination is dropped loudly: the
    # disease progressed on pemetrexed.
    assert "osimertinib_chemo_first_line" not in plan["regimen_ids"]
    assert any("no EGFR-directed therapy has been given" in u
               for u in plan["uncertainties"])
    assert state.release_status == "treatment_recommendation"


def test_alk_positive_tki_naive_after_chemo_gets_the_tki():
    state = _run({**_BASE, "driver_mutations": {"egfr": "negative",
                                                "alk": "EML4-ALK"},
                  "treatment_history": _CHEMO_PD},
                 "ALK fusion found after chemotherapy.")
    assert state.outputs["treatment_plan"]["regimen_ids"] == [
        "lorlatinib_first_line"]


def test_driver_first_line_stays_a_block_after_chemo_only_progression():
    facts = {"driver_mutations": {"egfr": "L858R", "alk": "negative"},
             "histologic_category": "adenocarcinoma",
             "treatment_history": _CHEMO_PD}
    found = _check(facts, ["pembro_pemetrexed_platinum"])
    assert any(v.rule_id == "DRIVER_FIRST_LINE" and v.severity == "block"
               for v in found)
    # After progression on the TKI itself it is the KEYNOTE-789 warn.
    facts["treatment_history"] = [{"line": 1, "agents": ["osimertinib"],
                                   "status": "progression"}]
    found = _check(facts, ["pembro_pemetrexed_platinum"])
    assert any(v.rule_id == "DRIVER_FIRST_LINE" and v.severity == "warn"
               for v in found)


def test_later_line_predicates_require_the_specific_prior_agent():
    chemo_only = {"driver_mutations": {"egfr": "L858R"},
                  "histologic_category": "adenocarcinoma", "tnm": _TNM,
                  "treatment_history": [{"line": 1, "agents": ["carboplatin"],
                                         "status": "progression"}]}
    assert evaluate_indication("amivantamab_chemo_subsequent", "IVB",
                               chemo_only)["verdict"] == INELIGIBLE
    alk = dict(chemo_only, driver_mutations={"alk": "EML4-ALK"})
    assert evaluate_indication("lorlatinib_post_second_gen", "IVB",
                               alk)["verdict"] == INELIGIBLE
    post_osi = dict(chemo_only, treatment_history=[
        {"line": 1, "agents": ["osimertinib"], "status": "progression"}])
    assert evaluate_indication("amivantamab_chemo_subsequent", "IVB",
                               post_osi)["verdict"] == ELIGIBLE
    no_history = {k: v for k, v in chemo_only.items()
                  if k != "treatment_history"}
    assert evaluate_indication("amivantamab_chemo_subsequent", "IVB",
                               no_history)["verdict"] == UNKNOWN


# ---------------------------------------------------- history parsing

def test_free_text_history_parsing():
    statuses = [treatment_history({"treatment_history": [text]})[0]["status"]
                for text in ("pembrolizumab (PD-L1 80%), ongoing response",
                             "carboplatin, updated 2025",
                             "osimertinib, no progression",
                             "奥希替尼 未进展",
                             "gefitinib, progressive disease",
                             "吉非替尼 进展", "erlotinib PD")]
    assert statuses == [None, None, None, None,
                        "progression", "progression", "progression"]


def test_same_drug_rule_edge_cases():
    blank = {"driver_mutations": {"egfr": "negative", "alk": "negative"},
             "histologic_category": "adenocarcinoma",
             "treatment_history": [{"line": 1, "agents": [""],
                                    "status": "progression"}]}
    assert not any(v.rule_id == "PROGRESSION_SAME_DRUG"
                   for v in _check(blank, ["docetaxel_second_line"]))
    chinese = {"driver_mutations": {"egfr": "L858R", "alk": "negative"},
               "histologic_category": "adenocarcinoma",
               "treatment_history": [{"line": 1, "agents": ["奥希替尼"],
                                      "status": "进展"}]}
    assert any(v.rule_id == "PROGRESSION_SAME_DRUG"
               for v in _check(chinese, ["osimertinib_first_line"]))


# ------------------------------------------------ early-gen EGFR → T790M

def test_first_gen_progression_with_t790m_goes_to_aura3():
    state = _run({**_BASE, "driver_mutations": {"egfr": "L858R + T790M",
                                                "alk": "negative"},
                  "treatment_history": [{"line": 1, "agents": ["gefitinib"],
                                         "status": "progression"}]},
                 "Progression on gefitinib; plasma NGS T790M.")
    plan = state.outputs["treatment_plan"]
    assert plan["regimen_ids"] == ["osimertinib_t790m_subsequent"]
    assert "AURA3" in plan["trial_refs"]
    assert state.release_status == "treatment_recommendation"


def test_first_gen_progression_untested_asks_for_t790m():
    seq = sequencing_context("IVB", {
        "driver_mutations": {"egfr": "L858R", "alk": "negative"},
        "treatment_history": [{"line": 1, "agents": ["erlotinib"],
                               "status": "progression"}]})
    assert any("T790M testing" in w for w in seq["workup"])
    assert [o["regimen_ids"] for o in seq["options"]] == [
        ["platinum_pemetrexed_post_tki"]]


def test_line_number_without_explicit_lines():
    seq = sequencing_context("IVB", {
        "driver_mutations": {"egfr": "L858R", "alk": "negative"},
        "treatment_history": ["osimertinib progression"]})
    assert seq["line"] == 2


# --------------------------------------------------------------- CNS

def test_positive_brain_reports_never_seed_absent():
    assert not _cns_negative_imaging(
        "Brain MRI unremarkable except for 3 enhancing metastases.")
    assert not _cns_negative_imaging(
        "Brain MRI: multiple metastases, margins not clear.")
    assert not _cns_negative_imaging("脑MRI未见出血，可见多发转移灶。")
    assert _cns_negative_imaging(
        "Single adrenal metastasis, brain MRI negative.")
    assert _cns_negative_imaging("Brain MRI negative for metastases.")
    assert _cns_negative_imaging("脑MRI未见转移。")


def test_cns_block_needs_a_cns_option_not_a_cns_word():
    facts = {"driver_mutations": {"egfr": "negative", "alk": "negative"},
             "histologic_category": "squamous", "pd_l1": {"tps": 5},
             "cns_metastases": {"status": "present", "symptomatic": True,
                                "treated": False}}
    found = _check(facts, ["pembro_carbo_taxane"],
                   uncertainties=["SRS not indicated at this time."],
                   workup_needed=["whole-brain MRI follow-up"])
    assert any(v.rule_id == "CNS_UNTREATED_SYMPTOMATIC"
               and v.severity == "block" for v in found)


def test_prefixed_m0_is_still_a_contradiction():
    for m in ("cM0", "pM0", "ypM0"):
        assert tnm_conflict({"cns_metastases": {"status": "present"},
                             "tnm": {"m": m}})
    assert tnm_conflict({"cns_metastases": {"status": "present"},
                         "tnm": {"m": "cM1b"}}) is None


# ----------------------------------------------------------- organ gates

def test_organ_numbers_are_read_out_of_strings():
    for value in ("38", "38 mL/min", "CrCl 38", {"crcl_ml_min": "38"}):
        assert evaluate_gate("renal_function", {"organ_function": {
            "renal": value}})["status"] == "fail", value
    assert evaluate_gate("qtc_baseline", {"organ_function": {
        "qtc": "510 ms"}})["status"] == "fail"
    assert evaluate_gate("renal_function", {"organ_function": {
        "renal": "normal"}})["status"] == "pass"


def test_every_component_implied_gate_is_enforced():
    implied = {"pemetrexed": "renal_function", "docetaxel":
               "hepatic_baseline", "ramucirumab": "bleeding_risk",
               "osimertinib": "qtc_baseline", "deruxtecan": "ild_history"}
    for regimen in regimen_lib.REGIMENS:
        gates = regimen_gates(regimen)
        for component in regimen.components:
            drug = component.drug.lower()
            if " or " in drug:
                continue
            for keyword, gate in implied.items():
                if keyword in drug:
                    assert gate in gates, (regimen.regimen_id, gate)


def test_docetaxel_hepatic_gate_blocks():
    facts = {"driver_mutations": {"egfr": "negative", "alk": "negative"},
             "histologic_category": "adenocarcinoma",
             "organ_function": {"hepatic": {"bilirubin_uln": 2.0}},
             "treatment_history": [{"line": 1,
                                    "agents": ["pembrolizumab",
                                               "carboplatin", "pemetrexed"],
                                    "status": "progression"}]}
    assert failed_gates("docetaxel_second_line", facts)
    assert any(v.rule_id == "ORGAN_FUNCTION_GATE" and v.severity == "block"
               for v in _check(facts, ["docetaxel_second_line"]))
