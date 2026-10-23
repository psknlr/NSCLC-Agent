"""Evidence library v1.6.0: the regimens, trials and gates added with it.

Pinned here:

* HER2 (ERBB2) is classified, never guessed: a tyrosine-kinase-domain
  (TKD) mutation qualifies for zongertinib/sevabertinib, amplification,
  overexpression and extracellular variants do not, and a bare "positive"
  stays unclassified; trastuzumab deruxtecan needs a HER2 mutation of
  either kind (DESTINY-Lung02), never amplification alone;
* the new dose gates read the variant (KRAS G12C, HER2 TKD) and the
  histology class ("non-squamous" and 鳞癌 were both misread by substring
  tests), and the HER2 TKIs' LVEF check asks for review without failing;
* "non-squamous" is extracted, reconciled and gap-checked as non-squamous;
* alternatives for one decision (which ALK TKI) are listed together and a
  dropped alternative leaves no trace in the option's name or rationale;
* a transplant or active autoimmune disease puts chemotherapy without a
  checkpoint inhibitor first, with the critic's caution kept;
* the later-line ROS1, KRAS G12C and HER2 branches, the new interaction
  rules, trial aliases and either-driver trial populations.
"""

from __future__ import annotations

import re

from nsclc_agent import Case, NSCLCRunner
from nsclc_agent.conversation import extract_facts_deterministic
from nsclc_agent.knowledge import interactions as ddi
from nsclc_agent.knowledge import regimens as regimen_lib
from nsclc_agent.knowledge.biomarkers import (
    _normalized_drivers,
    her2_class,
    her2_is_mutation,
    her2_variant_class,
)
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
from nsclc_agent.knowledge.sequencing import sequencing_context
from nsclc_agent.knowledge.trials import (
    TRIALS_BY_ID,
    population_mismatches,
    resolve_trial_id,
)
from nsclc_agent.perception.reports import _histology_matches
from nsclc_agent.safety import rules
from nsclc_agent.skills import SkillRegistry
from nsclc_agent.tools import CapabilityBroker, ToolHealth, ToolRegistry

SN = "No hemoptysis, no leg weakness, no fever."
NEG = {g: "negative" for g in ("egfr", "alk", "ros1", "ret", "met", "braf",
                               "ntrk", "kras", "erbb2")}
_CHEMO_IO = [{"line": 1, "agents": ["pembrolizumab", "pemetrexed",
                                    "carboplatin"], "status": "progression"}]
_NEW_REGIMENS = (
    "alectinib_first_line", "brigatinib_first_line", "cemiplimab_monotherapy",
    "atezolizumab_monotherapy", "pemetrexed_platinum_first_line",
    "carbo_nab_paclitaxel_first_line", "taletrectinib_first_line",
    "entrectinib_ros1_first_line", "entrectinib_ntrk_first_line",
    "crizotinib_ros1_first_line", "pralsetinib_first_line",
    "tepotinib_first_line", "encorafenib_binimetinib_first_line",
    "zongertinib_her2_tkd", "sevabertinib_her2_tkd",
    "adagrasib_subsequent_line",
)


def _check(stage, facts, plan):
    return rules.check_plan({"stage_group": stage}, {"tnm": {}, **facts}, plan)


def _run(facts):
    return NSCLCRunner().run_case(Case(
        t="T2a", n="N2a", m="M1c1",
        presentation=f"Adenocarcinoma. Brain MRI negative. {SN}",
        facts={"histologic_category": "adenocarcinoma", "ecog_ps": 1,
               "ngs_done": True, **facts}))


def _dose_gates(regimen_id, facts):
    broker = CapabilityBroker(
        "oncologist", "routine", skill_registry=SkillRegistry.discover(),
        active_skill="nsclc.dose_planning", health=ToolHealth())
    result = ToolRegistry().call(broker, "dose_gate_check",
                                 regimen_id=regimen_id, facts=facts)
    return {g["gate"]: g["status"] for g in result.data["gates"]}


def _sequence(drivers, *, histology="adenocarcinoma", history=None):
    return sequencing_context("IVB", {
        "driver_mutations": {**NEG, **drivers},
        "histologic_category": histology,
        "treatment_history": history or _CHEMO_IO})


def _rids(seq):
    return [o["regimen_ids"] for o in seq["options"]]


# ----------------------------------------------------------------- HER2

def test_her2_variant_classes():
    for value, expected in (
            ("A775_G776insYVMA", "tkd"), ("exon 20 insertion", "tkd"),
            ("G776delinsVC", "tkd"), ("p.Leu755Ser", "tkd"),
            ("HER2 20号外显子插入", "tkd"),
            ("exon 20 insertion with amplification", "tkd"),
            ("amplification", "non_tkd"), ("HER2扩增", "non_tkd"),
            ("IHC 3+", "non_tkd"), ("S310F", "non_tkd"),
            ("positive", "unclassified"),
            ("negative", None), (None, None)):
        assert her2_variant_class(value) == expected, value


def test_her2_mutation_is_not_amplification():
    for value, expected in (
            ("A775_G776insYVMA", True), ("S310F", True),
            ("HER2 mutation", True), ("HER2 mutant", True),
            ("HER2突变", True), ("amplification and S310F", True),
            ("ERBB2 amplification", False), ("IHC 3+", False),
            ("HER2扩增", False), ("positive", None), ("negative", None)):
        assert her2_is_mutation(value) is expected, value


def test_an_empty_alias_key_does_not_erase_a_result():
    for drivers in ({"her2": "exon 20 insertion", "erbb2": None},
                    {"erbb2": None, "her2": "exon 20 insertion"}):
        facts = {"driver_mutations": drivers}
        assert _normalized_drivers(facts) == {"erbb2": "exon 20 insertion"}
        assert her2_class(facts) == "tkd"


def test_her2_tki_needs_a_tkd_mutation_in_nonsquamous_disease():
    for her2, histology, expected in (
            ("A775_G776insYVMA", "adenocarcinoma", ELIGIBLE),
            ("A775_G776insYVMA", "squamous", INELIGIBLE),
            ("ERBB2 amplification", "adenocarcinoma", INELIGIBLE),
            ("positive", "adenocarcinoma", UNKNOWN)):
        for rid in ("zongertinib_her2_tkd", "sevabertinib_her2_tkd"):
            verdict = evaluate_indication(rid, "IVB", {
                "histologic_category": histology,
                "driver_mutations": {**NEG, "erbb2": her2}})
            assert verdict["verdict"] == expected, (rid, her2, histology)


def test_trastuzumab_deruxtecan_needs_a_her2_mutation():
    def verdict(her2):
        return evaluate_indication("tdxd_subsequent_line", "IVB", {
            "histologic_category": "adenocarcinoma",
            "driver_mutations": {**NEG, "erbb2": her2},
            "treatment_history": _CHEMO_IO})["verdict"]

    assert verdict("A775_G776insYVMA") == ELIGIBLE
    assert verdict("S310F") == ELIGIBLE  # non-TKD, still a mutation
    assert verdict("ERBB2 amplification") == INELIGIBLE
    assert verdict("positive") == UNKNOWN


# ------------------------------------------------------------ dose gates

def test_kras_g12c_dose_gate_reads_the_variant():
    def gate(kras):
        return _dose_gates("adagrasib_subsequent_line", {
            "histologic_category": "adenocarcinoma",
            "driver_mutations": {"kras": kras}})["kras_g12c_positive"]

    assert gate("G12C") == "pass"
    assert gate("G12D") == "fail"
    assert gate("negative") == "fail"
    assert gate("positive") == "unverified"


def test_her2_tkd_dose_gate_reads_the_class():
    def gate(her2):
        return _dose_gates("zongertinib_her2_tkd", {
            "histologic_category": "adenocarcinoma",
            "driver_mutations": {"erbb2": her2}})["erbb2_tkd_confirmed"]

    assert gate("A775_G776insYVMA") == "pass"
    assert gate("amplification") == "fail"
    assert gate("negative") == "fail"
    assert gate("positive") == "unverified"


def test_nonsquamous_dose_gate_reads_the_histology_class():
    def gate(histology):
        return _dose_gates("pemetrexed_platinum_first_line", {
            "histologic_category": histology})["nonsquamous"]

    assert gate("non-squamous") == "pass"
    assert gate("adenosquamous") == "pass"
    assert gate("鳞癌") == "fail"
    assert gate("squamous cell carcinoma") == "fail"


def test_her2_tki_lvef_check_reviews_but_never_fails():
    def status(facts):
        return evaluate_gate("lvef_monitoring", facts)["status"]

    assert status({"organ_function": {"cardiac": {"lvef_pct": 60}}}) == "pass"
    assert status({"organ_function": {"cardiac": {"lvef_pct": 40}}}) \
        == "unverified"
    assert status({}) == "unverified"
    assert failed_gates("zongertinib_her2_tkd", {
        "organ_function": {"cardiac": {"lvef_pct": 40}}}) == []


def test_component_gates_of_the_new_regimens():
    def gates(rid):
        return set(regimen_gates(regimen_lib.get(rid)))

    assert "no_strong_cyp3a4_inducer" in gates("brigatinib_first_line")
    # Alectinib's label needs no change with inducers: no gate.
    assert "no_strong_cyp3a4_inducer" not in gates("alectinib_first_line")
    assert "qtc_baseline" in gates("entrectinib_ros1_first_line")
    assert "lvef_baseline" in gates("encorafenib_binimetinib_first_line")
    assert {"lvef_monitoring"} <= gates("zongertinib_her2_tkd") \
        & gates("sevabertinib_her2_tkd")


def test_new_regimens_keep_doses_in_the_dose_channel():
    for rid in _NEW_REGIMENS:
        regimen = regimen_lib.get(rid)
        assert regimen is not None, rid
        assert not re.search(r"\d+\s*mg", repr(regimen.summary())), rid
        assert regimen.detail()["components"], rid
        verdict = evaluate_indication(rid, "IVB", {})
        assert verdict["declared"], rid


# ------------------------------------------------------------- histology

def test_non_squamous_is_extracted_as_non_squamous():
    for text in ("Non-squamous NSCLC, EGFR negative.", "非鳞癌，EGFR阴性。"):
        assert extract_facts_deterministic(text)["histologic_category"] \
            == "non_squamous", text
    assert extract_facts_deterministic("squamous cell carcinoma")[
        "histologic_category"] == "squamous"


def test_report_reconciliation_keeps_squamous_apart():
    # Containment read "squamous" inside "non-squamous" as a match.
    assert not _histology_matches("squamous", "non-squamous")
    assert not _histology_matches("squamous cell carcinoma", "non-squamous")
    assert not _histology_matches("squamous", "adenocarcinoma")
    assert _histology_matches("adenocarcinoma", "adenocarcinoma of the lung")
    assert _histology_matches("squamous", "squamous cell carcinoma")


def test_biomarker_gap_reads_the_histology_class():
    def gap(histology, rid):
        return any(v.rule_id == "BIOMARKER_GAP" for v in _check(
            "IVB", {"histologic_category": histology},
            {"regimen_ids": [rid]}))

    # "non_squamous" contains "squamous": the old substring test skipped it.
    assert gap("non_squamous", "pembro_pemetrexed_platinum")
    # 鳞癌 is pure squamous: EGFR/ALK testing is optional there.
    assert not gap("鳞癌", "pembro_carbo_taxane")


# ---------------------------------------------------------------- planner

def test_alk_alternatives_and_a_dropped_alternative_leaves_no_trace():
    plan = _run({"driver_mutations": {**NEG, "alk": "EML4-ALK"}}).outputs[
        "treatment_plan"]
    assert plan["regimen_ids"][:3] == ["lorlatinib_first_line",
                                       "alectinib_first_line",
                                       "brigatinib_first_line"]
    # Rifampicin fails lorlatinib's and brigatinib's inducer gate.
    state = _run({"driver_mutations": {**NEG, "alk": "EML4-ALK"},
                  "medications": ["rifampicin"]})
    plan = state.outputs["treatment_plan"]
    assert plan["regimen_ids"] == ["alectinib_first_line"]
    option = next(o for o in plan["options"]
                  if o["regimen_ids"] == ["alectinib_first_line"])
    text = f"{option['name']} {option['rationale']}".lower()
    assert "alectinib" in text
    assert "lorlatinib" not in text and "brigatinib" not in text
    assert state.release_status == "treatment_recommendation"


def test_tps_50_plan_names_pembrolizumab_only():
    plan = _run({"driver_mutations": dict(NEG),
                 "pd_l1": {"tps": 80}}).outputs["treatment_plan"]
    assert plan["regimen_ids"] == ["pembro_monotherapy"]


def test_transplant_puts_chemotherapy_without_ici_first():
    state = _run({"driver_mutations": dict(NEG), "pd_l1": {"tps": 10},
                  "comorbidities": {"organ_transplant": True}})
    plan = state.outputs["treatment_plan"]
    assert plan["regimen_ids"][0] == "pemetrexed_platinum_first_line"
    assert "pembro_pemetrexed_platinum" in plan["regimen_ids"]
    assert plan["mdt_referral"] is True
    fired = {(v["rule_id"], v["severity"])
             for v in state.outputs["safety_audit"]["violations"]}
    assert ("ICI_COMORBIDITY_CAUTION", "warn") in fired


def test_active_autoimmune_squamous_gets_carboplatin_nab_paclitaxel_first():
    state = NSCLCRunner().run_case(Case(
        t="T2a", n="N2a", m="M1c1",
        presentation=f"Squamous cell carcinoma. Brain MRI negative. {SN}",
        facts={"histologic_category": "squamous", "ecog_ps": 1,
               "driver_mutations": {"egfr": "negative", "alk": "negative"},
               "pd_l1": {"tps": 5},
               "comorbidities": {"active_autoimmune": True}}))
    plan = state.outputs["treatment_plan"]
    assert plan["regimen_ids"][0] == "carbo_nab_paclitaxel_first_line"


# ------------------------------------------------------------- sequencing

def test_ros1_after_crizotinib_then_after_repotrectinib():
    seq = _sequence({"ros1": "CD74-ROS1"}, history=[
        {"line": 1, "agents": ["crizotinib"], "status": "progression"}])
    assert _rids(seq)[0] == ["repotrectinib_first_line",
                             "taletrectinib_first_line"]
    seq = _sequence({"ros1": "CD74-ROS1"}, history=[
        {"line": 1, "agents": ["repotrectinib"], "status": "progression"}])
    assert _rids(seq) == [["platinum_pemetrexed_post_tki"]]
    assert any("not encoded" in n.lower() for n in seq["honest_notes"])


def test_kras_g12c_after_chemo_io_offers_both_inhibitors():
    assert _rids(_sequence({"kras": "G12C"}))[0] == [
        "sotorasib_subsequent_line", "adagrasib_subsequent_line"]


def test_her2_later_line_by_class():
    tkd = _rids(_sequence({"erbb2": "A775_G776insYVMA"}))
    assert tkd[:2] == [["zongertinib_her2_tkd", "sevabertinib_her2_tkd"],
                       ["tdxd_subsequent_line"]]
    squamous = _rids(_sequence({"erbb2": "A775_G776insYVMA"},
                               histology="squamous"))
    assert ["zongertinib_her2_tkd", "sevabertinib_her2_tkd"] not in squamous
    unclassified = _sequence({"erbb2": "positive"})
    assert ["tdxd_subsequent_line"] in _rids(unclassified)
    assert any("variant class" in c for c in unclassified["cautions"])
    amplified = _sequence({"erbb2": "ERBB2 amplification"})
    assert ["tdxd_subsequent_line"] not in _rids(amplified)
    assert any("IHC 3+" in c for c in amplified["cautions"])


# ------------------------------------------------- interactions, trials

def test_new_interaction_rules():
    def hits(*meds):
        return {(h["rule_id"], h["severity"]) for h in ddi.check(list(meds))}

    assert ("brigatinib_cyp3a4_inducer", "major") in hits("brigatinib",
                                                          "rifampin")
    assert ("alectinib_cyp3a4_inducer", "monitor") in hits("alectinib",
                                                           "利福平")
    assert any(rule == "qt_prolonging_tki"
               for rule, _ in hits("entrectinib", "ondansetron"))
    assert any(rule == "ici_systemic_steroid"
               for rule, _ in hits("cemiplimab", "dexamethasone"))


def test_new_trial_references_resolve():
    for reference, expected in (
            ("EMPOWER-Lung 1", "EMPOWER_LUNG1"), ("IMpower110", "IMPOWER110"),
            ("Beamion LUNG-1", "BEAMION_LUNG1"), ("the SOHO-01 trial", "SOHO01"),
            ("KRYSTAL-12", "KRYSTAL12"), ("TRUST-II", "TRUST"),
            ("NCT04919811", "TRUST"), ("STARTRK-2", "STARTRK2"),
            ("ALTA-1L", "ALTA1L"), ("PROFILE 1001", "PROFILE1001"),
            # Registry ids with an underscore did not resolve from their
            # usual spelling before.
            ("DESTINY-Lung02", "DESTINY_LUNG02"),
            ("TROPION-Lung05", "TROPION_LUNG05")):
        assert resolve_trial_id(reference) == expected, reference


def _row(trial_id):
    import dataclasses

    return dataclasses.asdict(TRIALS_BY_ID[trial_id])


def test_either_driver_trial_population():
    row = _row("STARTRK2")
    assert population_mismatches(row, {"population_drivers": {"ros1": []}}) \
        == []
    assert population_mismatches(row, {"population_drivers": {"ntrk": []}}) \
        == []
    assert population_mismatches(row, {"population_drivers": {"alk": []}}) \
        == ["population is not ROS1/NTRK-positive"]


def test_her2_tki_trials_need_the_tkd_class():
    row = _row("BEAMION_LUNG1")
    assert population_mismatches(
        row, {"population_drivers": {"erbb2": ["tkd"]}}) == []
    assert population_mismatches(
        row, {"population_drivers": {"erbb2": ["non_tkd"]}})
    assert population_mismatches(
        row, {"population_drivers": {"erbb2": ["unclassified"]}})


def test_pd_l1_monotherapy_trials_excluded_egfr_alk():
    for trial_id in ("EMPOWER_LUNG1", "IMPOWER110"):
        assert population_mismatches(_row(trial_id), {
            "population_drivers": {"egfr": ["l858r"]}}) \
            == ["the trial excluded EGFR/ALK-altered disease"]
