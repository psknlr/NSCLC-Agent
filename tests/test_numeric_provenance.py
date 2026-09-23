"""Numeric provenance: outcome figures are never free (v0.3.5).

Pinned here:

* the extractor targets outcome-shaped figures ONLY — percentages,
  hazard ratios, month spans; doses, TNM descriptors, stage labels and
  trial-name digits are other guards' business and raise nothing here;
* a survival/efficacy figure in a claim's text that appears nowhere in
  the evidence rows THAT CLAIM cites (nor in the claimed regimens' own
  registry entries) is named `CLAIM_NUMERIC_UNANCHORED` — a fabricated
  number cannot ride out on a well-cited claim;
* anchored figures are silent, one spelling per value ('26.0%' matches
  a payload 26), and the honest rule-mode pipeline is clean end to end.
"""

from __future__ import annotations

from nsclc_agent import Case, NSCLCRunner
from nsclc_agent.agents.critic import (
    CriticAgent,
    _canon_number,
    _outcome_numbers,
)
from nsclc_agent.state import CaseRunState, EvidenceLevel

SN = "No hemoptysis, no leg weakness, no fever."


# ------------------------------------------------------------- extraction

def test_extractor_targets_outcome_shapes_only():
    assert _outcome_numbers(
        "5-y OS 57.4% vs 43.5%, HR 0.68, median PFS 38.6 months"
    ) == {"57.4", "43.5", "0.68", "38.6"}
    # Doses, descriptors, stage labels, trial names, editions: not ours.
    assert _outcome_numbers(
        "RTOG0617: definitive chemoradiation 60 Gy, stage IIIB, "
        "T2b N2b M0, 8th edition, KEYNOTE-189") == set()


def test_canonicalization_is_one_spelling_per_value():
    assert _canon_number("26.0") == "26"
    assert _canon_number(".68") == "0.68"
    assert _canon_number("010") == "10"
    assert _canon_number("0") == "0"
    assert _outcome_numbers("OS ~26.0%") == {"26"}


# ------------------------------------------------------------ the guard

def _laura_state():
    state = CaseRunState()
    row = state.add_evidence(
        EvidenceLevel.TRIAL, "trial_lookup", "LAURA",
        {"trial": {"trial_id": "LAURA",
                   "regimen_ids": ["osimertinib_consolidation"],
                   "results": ["PFS 39.1 vs 5.6 months, HR 0.16"]}})
    return state, row


def test_fabricated_figure_on_a_well_cited_claim_is_flagged():
    state, row = _laura_state()
    state.add_claim(
        "treatment_option",
        "Consolidation osimertinib improves five-year OS to 47%", [row],
        subject={"intervention_regimen_ids": ["osimertinib_consolidation"]},
        support_relation="trial_anchor")
    issues = CriticAgent._numeric_guard(state)
    assert any("CLAIM_NUMERIC_UNANCHORED[C0001]" in i and "'47'" in i
               for i in issues)


def test_anchored_figures_are_silent():
    state, row = _laura_state()
    state.add_claim(
        "treatment_option",
        "Consolidation osimertinib (median PFS 39.1 months, HR 0.16)",
        [row],
        subject={"intervention_regimen_ids": ["osimertinib_consolidation"]},
        support_relation="trial_anchor")
    assert CriticAgent._numeric_guard(state) == []


def test_cohort_figure_anchors_the_prognosis_claim():
    state = CaseRunState()
    cohort = state.add_evidence(
        EvidenceLevel.COHORT, "prognosis_table", "stage IIIB cohort",
        {"stage_group": "IIIB", "five_year_os_percent_approx": 26})
    state.add_claim(
        "prognosis_context", "Stage IIIB cohort five-year OS ~26.0%",
        [cohort], subject={"population_stage": "IIIB"},
        support_relation="population_statistic")
    assert CriticAgent._numeric_guard(state) == []
    # ...and a padded figure with the same citation is not silent.
    state.add_claim(
        "prognosis_context", "Stage IIIB cohort five-year OS ~40%",
        [cohort], subject={"population_stage": "IIIB"},
        support_relation="population_statistic")
    issues = CriticAgent._numeric_guard(state)
    assert any("CLAIM_NUMERIC_UNANCHORED[C0002]" in i and "'40'" in i
               for i in issues)


def test_uncited_claim_with_a_figure_is_flagged():
    state = CaseRunState()
    state.add_claim(
        "treatment_option", "Surgery cures 90% of such cases", [],
        subject={"intervention_regimen_ids": []},
        support_relation="protocol_grounded")
    issues = CriticAgent._numeric_guard(state)
    assert any("'90'" in i for i in issues)


def test_regimen_registry_anchors_protocol_figures():
    """A duration drawn from the claimed regimen's own registry entry is
    deterministic system knowledge, not a fabrication."""
    state = CaseRunState()
    state.add_claim(
        "treatment_option",
        "Neoadjuvant nivolumab + chemotherapy over ~6 months window", [],
        subject={"intervention_regimen_ids": ["nivo_chemo_neoadjuvant"]},
        support_relation="trial_anchor")
    issues = CriticAgent._numeric_guard(state)
    assert not any("'6'" in i for i in issues)


# ------------------------------------------------------------ the pipeline

def test_rule_mode_pipeline_is_clean_and_guard_runs():
    state = NSCLCRunner().run_case(Case(
        t="T2b", n="N2b", m="M0",
        presentation=f"Multi-station N2b, unresectable per MDT. PET-CT/brain "
                     f"MRI M0. EGFR L858R. {SN}",
        facts={"driver_mutations": {"egfr": "L858R", "alk": "negative"},
               "histologic_category": "adenocarcinoma",
               "resectability_category": "UNRESECTABLE", "ecog_ps": 1}))
    audit = state.outputs["safety_audit"]
    assert "numeric_provenance" in audit["checks_run"]
    assert not [i for i in audit["issues"]
                if "CLAIM_NUMERIC_UNANCHORED" in i]
    # The prognosis claim really does carry a figure — the guard had
    # something to verify, this is not a vacuous pass.
    prognosis = next(c for c in state.claims
                     if c.kind == "prognosis_context")
    assert _outcome_numbers(prognosis.text)
