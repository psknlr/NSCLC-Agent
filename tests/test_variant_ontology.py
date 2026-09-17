"""The variant ontology + driver control plane — red-team review fixes.

The external clinical red team demonstrated three formally-released errors,
all from one root cause: planner and critic shared a boolean gene-positive
model. Pinned here:

* EGFR variant classes are first-class (ex19del/L858R vs uncommon vs
  exon20ins vs T790M/C797S), and "positive" alone routes to nobody's TKI;
* ROS1/RET/MET-ex14/BRAF-V600E/NTRK are inside the treatment control
  plane: planner routes them to their TKIs, and the rule engine
  INDEPENDENTLY blocks ICI-first plans for any of them;
* the same ontology, applied independently, means a simulated planner bug
  (osimertinib forced onto exon20ins; pembrolizumab forced onto ROS1+) is
  caught by the critic — the shared-blind-spot failure mode is closed;
* trial stage boundaries are TNM-edition-aware: an 8th-edition-compatible
  case is a migration note, not an extrapolation block;
* AIS=Tis=stage 0 and MIA=T1mi=IA1 everywhere (semantic drift closed).
"""

from __future__ import annotations

from nsclc_agent import Case, NSCLCRunner
from nsclc_agent.knowledge.biomarkers import (
    egfr_classical,
    egfr_variant_classes,
    first_line_actionable_drivers,
    later_line_actionable_drivers,
)
from nsclc_agent.safety import rules
from nsclc_agent.staging.legacy8 import eighth_edition_group

SN = "No hemoptysis, no leg weakness, no fever."


def _run(facts, t="T2a", n="N0", m="M1b", **case_kw):
    case = Case(t=t, n=n, m=m,
                presentation=f"Adenocarcinoma, metastatic workup complete, "
                             f"brain MRI negative. {SN}",
                facts={"histologic_category": "adenocarcinoma",
                       "ngs_done": True, "ecog_ps": 1, **facts}, **case_kw)
    return NSCLCRunner().run_case(case)


# ------------------------------------------------------------- classification

def test_egfr_variant_classes():
    assert egfr_variant_classes("Ex19del") == {"ex19del"}
    assert egfr_variant_classes("exon 19 deletion") == {"ex19del"}
    assert egfr_variant_classes("L858R") == {"l858r"}
    assert egfr_variant_classes("exon 20 insertion") == {"exon20ins"}
    assert egfr_variant_classes("ex20ins") == {"exon20ins"}
    assert egfr_variant_classes("20外显子插入突变") == {"exon20ins"}
    assert egfr_variant_classes("G719X") == {"g719x"}
    assert egfr_variant_classes("L858R + T790M") == {"l858r", "t790m"}
    # exon 20 point mutations are NOT insertions.
    assert egfr_variant_classes("exon 20 T790M") == {"t790m"}
    assert egfr_variant_classes("mutation positive") == {"unclassified"}
    assert egfr_variant_classes("negative") == frozenset()
    assert egfr_variant_classes("wild type") == frozenset()


def test_egfr_classical_excludes_disqualifying_coalterations():
    assert egfr_classical({"driver_mutations": {"egfr": "L858R"}})
    assert egfr_classical({"driver_mutations": {"egfr": "ex19del + T790M"}})
    assert not egfr_classical({"driver_mutations": {"egfr": "exon 20 insertion"}})
    assert not egfr_classical(
        {"driver_mutations": {"egfr": "L858R + exon20 insertion"}})
    assert not egfr_classical({"driver_mutations": {"egfr": "G719X"}})
    assert not egfr_classical({"driver_mutations": {"egfr": "positive"}})


def test_first_line_actionable_plane_is_variant_aware():
    found = first_line_actionable_drivers({"driver_mutations": {
        "ros1": "CD74-ROS1 fusion", "egfr": "negative"}})
    assert [d["gene"] for d in found] == ["ROS1"]
    # MET amplification is not the exon-14 indication.
    assert not first_line_actionable_drivers(
        {"driver_mutations": {"met": "high-level amplification"}})
    assert first_line_actionable_drivers(
        {"driver_mutations": {"met": "MET exon 14 skipping"}})
    # BRAF non-V600 is not an indication.
    assert not first_line_actionable_drivers(
        {"driver_mutations": {"braf": "G469A"}})
    # NTRK gene-key aliases resolve.
    assert first_line_actionable_drivers(
        {"driver_mutations": {"ntrk1": "TPM3-NTRK1 fusion"}})
    # HER2 / KRAS G12C are later-line: absent from the first-line plane.
    assert not first_line_actionable_drivers(
        {"driver_mutations": {"her2": "YVMA insertion", "kras": "G12C"}})
    later = later_line_actionable_drivers(
        {"driver_mutations": {"her2": "YVMA insertion", "kras": "G12C"}})
    assert {d["gene"] for d in later} == {"HER2/ERBB2", "KRAS G12C"}


# ----------------------------------------------- red-team blockers, end-to-end

def test_exon20ins_iv_routes_to_amivantamab_not_osimertinib():
    state = _run({"driver_mutations": {"egfr": "exon 20 insertion",
                                       "alk": "negative"},
                  "pd_l1": {"tps": 80}})
    plan = state.outputs["treatment_plan"]
    assert "amivantamab_chemo_first_line" in plan["regimen_ids"]
    assert "osimertinib_first_line" not in plan["regimen_ids"]
    assert "pembro_monotherapy" not in plan["regimen_ids"]
    assert state.release_status == "treatment_recommendation"


def test_exon20ins_iii_gets_no_laura_and_no_auto_durva():
    state = _run({"driver_mutations": {"egfr": "EGFR exon20 insertion",
                                       "alk": "negative"},
                  "resectability_category": "UNRESECTABLE"},
                 t="T2b", n="N2b", m="M0")
    plan = state.outputs["treatment_plan"]
    assert "ccrt_60gy" in plan["regimen_ids"]
    assert "osimertinib_consolidation" not in plan["regimen_ids"]
    assert "durva_consolidation" not in plan["regimen_ids"]
    assert plan["mdt_referral"]


def test_ros1_and_ret_route_to_tki_despite_high_pdl1():
    for gene, value, rid in (("ros1", "CD74-ROS1 fusion",
                              "repotrectinib_first_line"),
                             ("ret", "KIF5B-RET fusion",
                              "selpercatinib_first_line")):
        state = _run({"driver_mutations": {"egfr": "negative",
                                           "alk": "negative", gene: value},
                      "pd_l1": {"tps": 80}})
        plan = state.outputs["treatment_plan"]
        assert rid in plan["regimen_ids"], gene
        assert "pembro_monotherapy" not in plan["regimen_ids"], gene


def test_unclassified_egfr_fails_toward_mdt_not_a_guessed_tki():
    state = _run({"driver_mutations": {"egfr": "mutation detected",
                                       "alk": "negative"}})
    plan = state.outputs["treatment_plan"]
    assert not plan["regimen_ids"]  # no guessed drug
    assert plan["mdt_referral"]
    assert any("variant" in u.lower() for u in plan["uncertainties"])


# ------------------------------------------- critic independence (same probes)

def _check(stage, facts, regimen_ids):
    return rules.check_plan({"stage_group": stage},
                            {"tnm": {}, **facts},
                            {"regimen_ids": regimen_ids, "options": []})


def test_critic_blocks_simulated_planner_bugs_independently():
    ids = [v.rule_id for v in _check(
        "IVA", {"driver_mutations": {"egfr": "exon 20 insertion",
                                     "alk": "negative"}},
        ["osimertinib_first_line"])]
    assert "EGFR_VARIANT_MISMATCH" in ids
    ids = [(v.rule_id, v.severity) for v in _check(
        "IVA", {"driver_mutations": {"egfr": "negative", "alk": "negative",
                                     "ros1": "fusion positive"}},
        ["pembro_monotherapy"])]
    assert ("DRIVER_FIRST_LINE", "block") in ids
    ids = [v.rule_id for v in _check(
        "IIIB", {"driver_mutations": {"egfr": "ex20ins", "alk": "negative"}},
        ["ccrt_60gy", "osimertinib_consolidation"])]
    assert "EGFR_VARIANT_MISMATCH" in ids


def test_variant_mismatch_severities_are_graded():
    # Uncommon-sensitizing on a classical regimen: warn, not block.
    found = _check("IVA", {"driver_mutations": {"egfr": "G719X",
                                                "alk": "negative"}},
                   ["osimertinib_first_line"])
    mm = [v for v in found if v.rule_id == "EGFR_VARIANT_MISMATCH"]
    assert mm and mm[0].severity == "warn"
    # C797S: block.
    found = _check("IVA", {"driver_mutations": {"egfr": "C797S",
                                                "alk": "negative"}},
                   ["osimertinib_first_line"])
    mm = [v for v in found if v.rule_id == "EGFR_VARIANT_MISMATCH"]
    assert mm and mm[0].severity == "block"
    # Classical: silent.
    found = _check("IVA", {"driver_mutations": {"egfr": "L858R",
                                                "alk": "negative"}},
                   ["osimertinib_first_line"])
    assert not any(v.rule_id == "EGFR_VARIANT_MISMATCH" for v in found)


def test_controls_do_not_overblock():
    # MET amplification (not ex14) and KRAS G12C: chemo-IO first line is
    # correct — the driver rule must not fire.
    for facts in ({"driver_mutations": {"met": "amplification",
                                        "egfr": "negative", "alk": "negative"}},
                  {"driver_mutations": {"kras": "G12C", "egfr": "negative",
                                        "alk": "negative"}}):
        found = _check("IVB", facts, ["pembro_pemetrexed_platinum"])
        assert not any(v.rule_id == "DRIVER_FIRST_LINE" for v in found)


# --------------------------------------------------- TNM edition awareness

def test_eighth_edition_back_mapping():
    assert eighth_edition_group("T2b", "N2b", "M0") == "IIIA"
    assert eighth_edition_group("T1b", "N1", "M0") == "IIB"
    assert eighth_edition_group("T1c", "N2a", "M0") == "IIIA"
    assert eighth_edition_group("T4", "N2a", "M0") == "IIIB"
    assert eighth_edition_group("T2a", "N0", "M1c1") == "IVB"
    assert eighth_edition_group("Tis", "N0", "M0") == "0"
    assert eighth_edition_group("T2a", "N0", None) is None
    assert eighth_edition_group("T9", "N0", "M0") is None


def test_edition_migration_is_a_note_not_an_extrapolation():
    # T2bN2b = 9th-ed IIIB but 8th-ed IIIA — inside ADAURA's enrollment.
    state = _run({"driver_mutations": {"egfr": "L858R", "alk": "negative"},
                  "resectability_category": "RESECTABLE"},
                 t="T2b", n="N2b", m="M0")
    plan = state.outputs["treatment_plan"]
    assert "osimertinib_adjuvant" in plan["regimen_ids"]
    assert not plan["extrapolations"]  # migration, not extrapolation
    violations = state.outputs["safety_audit"]["violations"]
    assert any(v["rule_id"] == "TRIAL_EDITION_MIGRATION" for v in violations)
    assert not any(v["rule_id"] == "TRIAL_STAGE_BOUNDARY" for v in violations)
    assert state.release_status == "treatment_recommendation"
    # Control: T4N2a is IIIB in BOTH editions — still a declared
    # extrapolation, not silently blessed.
    state = _run({"driver_mutations": {"egfr": "L858R", "alk": "negative"},
                  "resectability_category": "RESECTABLE"},
                 t="T4", n="N2a", m="M0")
    assert state.outputs["treatment_plan"]["extrapolations"]


# ------------------------------------------------------- planner tightening

def test_nonclassical_egfr_resectable_gets_chemo_not_adaura():
    state = _run({"driver_mutations": {"egfr": "exon 20 insertion",
                                       "alk": "negative"},
                  "resectability_category": "RESECTABLE",
                  "clinical_scenario": "POSTOP_RESECTED"},
                 t="T3", n="N2a", m="M0")
    plan = state.outputs["treatment_plan"]
    assert "osimertinib_adjuvant" not in plan["regimen_ids"]
    assert "adjuvant_platinum_doublet" in plan["regimen_ids"]
    assert "pembro_perioperative" not in plan["regimen_ids"]


def test_her2_and_kras_inform_without_vetoing_first_line():
    state = _run({"driver_mutations": {"egfr": "negative", "alk": "negative",
                                       "her2": "YVMA insertion"}})
    plan = state.outputs["treatment_plan"]
    assert "pembro_pemetrexed_platinum" in plan["regimen_ids"]
    assert "tdxd_subsequent_line" not in plan["regimen_ids"]
    assert any("T-DXd" in u for u in plan["uncertainties"])
    assert state.release_status == "treatment_recommendation"


def test_panel_completeness_annotates_narrow_negative_workup():
    state = _run({"driver_mutations": {"egfr": "negative", "alk": "negative"},
                  "pd_l1": {"tps": 60}, "ngs_done": False})
    plan = state.outputs["treatment_plan"]
    assert any("multigene" in w.lower() for w in plan["workup_needed"])
    assert any("provisional" in u.lower() for u in plan["uncertainties"])
    # Annotation, not a gate: the interim recommendation still stands.
    assert "pembro_monotherapy" in plan["regimen_ids"]


# ------------------------------------------------------------ semantic drift

def test_mia_is_ia1_everywhere():
    from nsclc_agent.prompts import load_module
    from nsclc_agent.staging.concepts import LESION_CONCEPTS, lesion_stage

    assert lesion_stage("AIS") == "0"
    assert lesion_stage("MIA") == "IA1"
    assert LESION_CONCEPTS["MIA"]["t_descriptor"] == "T1mi"
    # The engine agrees (it always did) …
    from nsclc_agent.staging import stage_from_strings

    assert stage_from_strings("T1mi", "N0", "M0").stage_group == "IA1"
    # …and the protocol prose no longer contradicts it.
    text = load_module("stage0").full_text
    assert "IA1" in text and "NOT stage 0" in text


def test_dose_gates_are_variant_aware():
    from nsclc_agent.skills import SkillRegistry
    from nsclc_agent.tools.base import CapabilityBroker, ToolHealth
    from nsclc_agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    broker = CapabilityBroker(
        "oncologist", "routine", skill_registry=SkillRegistry.discover(),
        active_skill="nsclc.dose_planning", health=ToolHealth())

    def gate(regimen_id, egfr_value):
        result = registry.call(
            broker, "dose_gate_check", regimen_id=regimen_id,
            facts={"driver_mutations": {"egfr": egfr_value}})
        return {g["gate"]: g["status"] for g in result.data["gates"]}

    assert gate("osimertinib_first_line",
                "L858R")["egfr_classical_sensitizing"] == "pass"
    assert gate("osimertinib_first_line",
                "exon 20 insertion")["egfr_classical_sensitizing"] == "fail"
    assert gate("osimertinib_first_line",
                "G719X")["egfr_classical_sensitizing"] == "unverified"
    assert gate("amivantamab_chemo_first_line",
                "exon 20 insertion")["egfr_exon20ins"] == "pass"
    assert gate("amivantamab_chemo_first_line",
                "L858R")["egfr_exon20ins"] == "fail"
