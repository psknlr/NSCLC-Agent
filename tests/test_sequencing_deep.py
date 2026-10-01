"""Resistance-mechanism-directed and third-line sequencing (v0.6.0).

Pinned here:

* progression findings are "asked and recorded", never inferred: the
  fact's presence means the resistance question was asked, and each
  unlisted mechanism is false-as-recorded;
* small-cell transformation switches the biology — platinum-etoposide
  treats the transformed clone and the EGFR-directed second line is
  correctly ABSENT (retrospective evidence grade stated, not
  laundered);
* MET-amplified resistance proposes the INSIGHT-2 continuation and the
  same-drug warn fires ON PURPOSE — it is the documentation demand for
  continuing a progressed drug, not an error;
* C797S adds the honest caution (no approved fourth-generation TKI)
  while the chemo backbone stays the evidence-based next line;
* the third line (TKI and platinum both exhausted) proposes Dato-DXd
  (TROPION-Lung05) with the REVEL fallback, and the prior-platinum
  predicate blocks Dato-DXd from skipping the platinum line;
* beyond docetaxel the corpus says so instead of inventing a regimen.
"""

from __future__ import annotations

from nsclc_agent import Case, NSCLCRunner
from nsclc_agent.knowledge.indications import (
    ELIGIBLE,
    INELIGIBLE,
    UNKNOWN,
    evaluate_indication,
)
from nsclc_agent.knowledge.sequencing import (
    progression_findings,
    sequencing_context,
)
from nsclc_agent.safety import rules

SN = "No hemoptysis, no leg weakness, no fever."
BASE = {"driver_mutations": {"egfr": "L858R", "alk": "negative"},
        "histologic_category": "adenocarcinoma"}
_OSI = [{"line": 1, "agents": ["osimertinib"], "status": "progression"}]


def _rids(seq):
    return [r for o in seq["options"] for r in o["regimen_ids"]]


# ------------------------------------------------------------- the findings

def test_findings_are_asked_and_recorded():
    absent = progression_findings({})
    assert not absent["available"]
    asked = progression_findings({"progression_findings": {
        "met_amplification": True}})
    assert asked["available"]
    assert asked["met_amplification"]
    assert asked["small_cell_transformation"] is False  # false-as-recorded


# ------------------------------------------------------------- the branches

def test_transformation_switches_the_biology():
    seq = sequencing_context("IVB", dict(
        BASE, treatment_history=_OSI,
        progression_findings={"small_cell_transformation": True}))
    rids = _rids(seq)
    assert rids == ["platinum_etoposide_transformation"]
    assert "amivantamab_chemo_subsequent" not in rids
    assert any("change of disease biology" in c for c in seq["cautions"])


def test_met_amp_proposes_the_continuation_and_owns_the_flag():
    seq = sequencing_context("IVB", dict(
        BASE, treatment_history=_OSI,
        progression_findings={"met_amplification": True}))
    rids = _rids(seq)
    assert rids[0] == "tepotinib_osimertinib_met_amp"
    assert "amivantamab_chemo_subsequent" in rids  # backbone stays
    assert not seq["workup"]  # the question was asked
    # The critic flags the continuation — by design.
    found = rules.check_plan(
        {"stage_group": "IVB"},
        {"tnm": {}, **BASE, "treatment_history": _OSI},
        {"regimen_ids": ["tepotinib_osimertinib_met_amp"], "options": []})
    assert any(v.rule_id == "PROGRESSION_SAME_DRUG" and v.severity == "warn"
               for v in found)


def test_c797s_gets_the_honest_caution_not_a_phantom_tki():
    seq = sequencing_context("IVB", dict(
        BASE, treatment_history=_OSI,
        progression_findings={"c797s": True}))
    assert any("fourth-generation" in c for c in seq["cautions"])
    assert "platinum_pemetrexed_post_tki" in _rids(seq)


def test_third_line_dato_dxd_after_tki_and_platinum():
    history = _OSI + [{"line": 2, "agents": ["carboplatin", "pemetrexed"],
                       "status": "progression"}]
    seq = sequencing_context("IVB", dict(BASE, treatment_history=history))
    rids = _rids(seq)
    assert rids[0] == "dato_dxd_egfr_subsequent"
    assert "docetaxel_ramucirumab_second_line" in rids
    assert seq["line"] == 3


def test_beyond_docetaxel_the_corpus_says_so():
    seq = sequencing_context("IVB", {
        "driver_mutations": {"egfr": "negative", "alk": "negative"},
        "treatment_history": [
            {"line": 1, "agents": ["pembrolizumab", "carboplatin"],
             "status": "progression"},
            {"line": 2, "agents": ["docetaxel"], "status": "progression"}]})
    assert not seq["options"]
    assert any("goals-of-care" in c for c in seq["cautions"])


# ------------------------------------------------------------ the predicate

def test_prior_platinum_predicate():
    tnm = {"t": "T2a", "n": "N0", "m": "M1c1"}
    assert evaluate_indication(
        "dato_dxd_egfr_subsequent", "IVB",
        {**BASE, "tnm": tnm, "treatment_history": _OSI}
    )["verdict"] == INELIGIBLE
    with_platinum = _OSI + [{"line": 2,
                             "agents": ["carboplatin", "pemetrexed"],
                             "status": "progression"}]
    assert evaluate_indication(
        "dato_dxd_egfr_subsequent", "IVB",
        {**BASE, "tnm": tnm, "treatment_history": with_platinum}
    )["verdict"] == ELIGIBLE
    assert evaluate_indication(
        "dato_dxd_egfr_subsequent", "IVB", {**BASE, "tnm": tnm}
    )["verdict"] == UNKNOWN


def test_critic_blocks_the_platinum_skip():
    found = rules.check_plan(
        {"stage_group": "IVB"},
        {"tnm": {}, **BASE, "treatment_history": _OSI},
        {"regimen_ids": ["dato_dxd_egfr_subsequent"], "options": []})
    assert any(v.rule_id == "INDICATION_PREDICATE" and v.severity == "block"
               for v in found)


# -------------------------------------------------------------- the pipeline

def test_transformation_pipeline_releases_sclc_backbone_only():
    state = NSCLCRunner().run_case(Case(
        t="T2a", n="N0", m="M1c1",
        presentation=f"Adenocarcinoma, progression on osimertinib, "
                     f"re-biopsy shows small cell transformation. "
                     f"Brain MRI negative. {SN}",
        facts={**BASE, "ngs_done": True, "ecog_ps": 1,
               "treatment_history": _OSI,
               "progression_findings": {
                   "small_cell_transformation": True}}))
    plan = state.outputs["treatment_plan"]
    assert plan["regimen_ids"] == ["platinum_etoposide_transformation"]
    assert state.release_status == "treatment_recommendation"
    assert not [i for i in state.outputs["safety_audit"]["issues"]
                if i.startswith("CLAIM")]


def test_exon20ins_cannot_reach_the_insight2_continuation():
    """The continuation regimen joined the classical-EGFR family: the
    variant-mismatch net covers it like the rest."""
    found = rules.check_plan(
        {"stage_group": "IVB"},
        {"tnm": {}, "driver_mutations": {"egfr": "exon 20 insertion"},
         "histologic_category": "adenocarcinoma"},
        {"regimen_ids": ["tepotinib_osimertinib_met_amp"], "options": []})
    assert any(v.rule_id == "EGFR_VARIANT_MISMATCH" and v.severity == "block"
               for v in found)
