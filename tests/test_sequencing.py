"""Later-line sequencing (v0.5.0) — the red-team corpus gap.

Pinned here:

* history normalization is honest: sequencing triggers ONLY on explicit
  progression — prior exposure without a stated outcome is exposure,
  not progression;
* the post-osimertinib branch proposes MARIPOSA-2 and the
  KEYNOTE-789-informed chemo backbone, routes the resistance-mechanism
  question to workup when unasked (provisional posture), and never
  re-proposes the failed TKI;
* ALK sequencing knows the difference between post-second-generation
  (→ lorlatinib) and post-lorlatinib (→ chemo, no established next
  TKI); post-chemo-IO knows docetaxel ± ramucirumab and surfaces KRAS
  G12C / HER2 options in the line where they actually apply;
* unmapped progression routes to the molecular tumor board, never to a
  guess;
* the critic warns on same-drug rechallenge (PROGRESSION_SAME_DRUG)
  without false-alarming on agent-disjoint next lines, and
  DRIVER_FIRST_LINE becomes line-aware — post-progression chemo-IO in
  driver-positive disease warns with the KEYNOTE-789 message instead
  of mislabeling the question "first-line".
"""

from __future__ import annotations

from nsclc_agent import Case, NSCLCRunner
from nsclc_agent.knowledge.indications import ELIGIBLE, evaluate_indication
from nsclc_agent.knowledge.sequencing import (
    progressed_on,
    sequencing_context,
    treatment_history,
)
from nsclc_agent.safety import rules

SN = "No hemoptysis, no leg weakness, no fever."

_OSI_PROGRESSED = {"driver_mutations": {"egfr": "L858R", "alk": "negative"},
                   "histologic_category": "adenocarcinoma",
                   "treatment_history": [{"line": 1,
                                          "agents": ["osimertinib"],
                                          "status": "progression"}]}


def _check(stage, facts, plan):
    return rules.check_plan({"stage_group": stage}, {"tnm": {}, **facts}, plan)


# ------------------------------------------------------------- the history

def test_explicit_progression_only():
    exposure_only = {"treatment_history": [
        {"line": 1, "agents": ["osimertinib"]}]}  # no status
    assert not progressed_on(exposure_only, ("osimertinib",))
    assert sequencing_context("IVA", exposure_only) is None
    assert progressed_on(_OSI_PROGRESSED, ("osimertinib",))
    # String entries are tolerated, Chinese status words included.
    zh = {"treatment_history": ["奥希替尼 进展"]}
    assert treatment_history(zh)[0]["status"] == "progression"


def test_non_stage_iv_has_no_sequencing_context():
    assert sequencing_context("IIIA", _OSI_PROGRESSED) is None


# ------------------------------------------------------------- the branches

def test_post_osimertinib_branch():
    seq = sequencing_context("IVB", dict(_OSI_PROGRESSED,
                                         progression_ngs_done=True))
    rids = [r for o in seq["options"] for r in o["regimen_ids"]]
    assert "amivantamab_chemo_subsequent" in rids
    assert "platinum_pemetrexed_post_tki" in rids
    assert seq["line"] == 2
    assert not seq["workup"]  # resistance question already asked
    # Without the progression NGS the same branch goes provisional.
    seq = sequencing_context("IVB", _OSI_PROGRESSED)
    assert any("AT PROGRESSION" in w for w in seq["workup"])


def test_alk_knows_post_second_gen_from_post_lorlatinib():
    base = {"driver_mutations": {"egfr": "negative",
                                 "alk": "EML4-ALK fusion"},
            "histologic_category": "adenocarcinoma"}
    seq = sequencing_context("IVB", dict(
        base, treatment_history=[{"line": 1, "agents": ["alectinib"],
                                  "status": "progression"}]))
    assert ["lorlatinib_post_second_gen"] == seq["options"][0]["regimen_ids"]
    seq = sequencing_context("IVB", dict(
        base, treatment_history=[{"line": 2, "agents": ["lorlatinib"],
                                  "status": "progression"}]))
    rids = [r for o in seq["options"] for r in o["regimen_ids"]]
    assert "platinum_pemetrexed_post_tki" in rids
    assert "lorlatinib_post_second_gen" not in rids
    assert seq["line"] == 3


def test_post_chemoio_branch_surfaces_line_matched_driver_options():
    facts = {"driver_mutations": {"egfr": "negative", "alk": "negative",
                                  "kras": "G12C", "her2": "YVMA insertion"},
             "histologic_category": "adenocarcinoma",
             "treatment_history": [{"line": 1,
                                    "agents": ["pembrolizumab",
                                               "pemetrexed", "carboplatin"],
                                    "status": "progression"}]}
    seq = sequencing_context("IVB", facts)
    rids = [r for o in seq["options"] for r in o["regimen_ids"]]
    assert rids.index("sotorasib_subsequent_line") \
        < rids.index("docetaxel_ramucirumab_second_line")
    assert "tdxd_subsequent_line" in rids
    assert "docetaxel_second_line" in rids


def test_unmapped_progression_routes_to_tumor_board():
    seq = sequencing_context("IVB", {
        "driver_mutations": {"egfr": "negative", "alk": "negative"},
        "treatment_history": [{"line": 1, "agents": ["gefitinib"],
                               "status": "progression"}]})
    assert not seq["options"]
    assert any("molecular tumor board" in c for c in seq["cautions"])


def test_treatment_history_satisfies_prior_systemic_declarations():
    verdict = evaluate_indication(
        "amivantamab_chemo_subsequent", "IVB",
        dict(_OSI_PROGRESSED, tnm={"t": "T2a", "n": "N0", "m": "M1c1"}))
    assert verdict["verdict"] == ELIGIBLE


# --------------------------------------------------------------- the critic

def test_same_drug_rechallenge_warns_and_disjoint_does_not():
    found = _check("IVB", _OSI_PROGRESSED,
                   {"regimen_ids": ["osimertinib_first_line"], "options": []})
    assert any(v.rule_id == "PROGRESSION_SAME_DRUG" and v.severity == "warn"
               for v in found)
    found = _check("IVB", _OSI_PROGRESSED,
                   {"regimen_ids": ["amivantamab_chemo_subsequent"],
                    "options": []})
    assert not any(v.rule_id == "PROGRESSION_SAME_DRUG" for v in found)


def test_driver_first_line_is_line_aware():
    naive = {"driver_mutations": {"egfr": "L858R", "alk": "negative"},
             "histologic_category": "adenocarcinoma"}
    plan = {"regimen_ids": ["pembro_pemetrexed_platinum"], "options": []}
    found = _check("IVB", naive, plan)
    assert any(v.rule_id == "DRIVER_FIRST_LINE" and v.severity == "block"
               for v in found)
    found = _check("IVB", _OSI_PROGRESSED, plan)
    hits = [v for v in found if v.rule_id == "DRIVER_FIRST_LINE"]
    assert hits and hits[0].severity == "warn"
    assert "KEYNOTE-789" in hits[0].message


# -------------------------------------------------------------- the pipeline

def test_post_osimertinib_pipeline_releases_the_next_line():
    state = NSCLCRunner().run_case(Case(
        t="T2a", n="N0", m="M1c1",
        presentation=f"Adenocarcinoma, progression on first-line "
                     f"osimertinib. Brain MRI negative. {SN}",
        facts=dict(_OSI_PROGRESSED, ngs_done=True, ecog_ps=1,
                   progression_ngs_done=True)))
    plan = state.outputs["treatment_plan"]
    assert state.release_status == "treatment_recommendation"
    assert "amivantamab_chemo_subsequent" in plan["regimen_ids"]
    assert "osimertinib_first_line" not in plan["regimen_ids"]
    assert plan["sequencing"]["line"] == 2
    assert "MARIPOSA2" in plan["trial_refs"]
    issues = state.outputs["safety_audit"]["issues"]
    assert not [i for i in issues if i.startswith("CLAIM")]


def test_progression_without_rebiopsy_goes_provisional():
    state = NSCLCRunner().run_case(Case(
        t="T2a", n="N0", m="M1c1",
        presentation=f"Adenocarcinoma, progression on osimertinib. "
                     f"Brain MRI negative. {SN}",
        facts=dict(_OSI_PROGRESSED, ngs_done=True, ecog_ps=1)))
    assert state.release_status == "needs_more_information"
    assert any("AT PROGRESSION" in w for w in
               state.outputs["treatment_plan"]["workup_needed"])
