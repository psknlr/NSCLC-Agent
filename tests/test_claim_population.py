"""Semantic population entailment for claims (v0.3.4).

The structural layer (v0.3.2) proved a citation covers the claimed
REGIMEN; this layer proves it covers the claimed POPULATION. Pinned here:

* a right-regimen citation for the wrong population is named
  (`CLAIM_POPULATION_MISMATCH`): LAURA on a stage-IV claim, FLAURA on an
  exon20ins population, a classical-EGFR trial on a C797S co-occurring
  case, a nonsquamous-only trial on a squamous claim, an
  EGFR/ALK-excluded perioperative-IO trial on an EGFR-positive claim;
* the stage check is edition-aware (8th-edition back-mapping — a TNM
  renaming is not a mismatch) and honors declared extrapolations,
  while driver and histology mismatches are NEVER excused by a stage
  declaration;
* hand-built claims without population fields are graded structurally
  only (no false alarms on partial subjects);
* a population-statistic prognosis claim must rest on cohort-grade
  evidence (`CLAIM_STATISTIC_SOURCE`), and pipeline claims carry the
  population facts that make all of this checkable.
"""

from __future__ import annotations

from nsclc_agent import Case, NSCLCRunner
from nsclc_agent.agents.critic import CriticAgent
from nsclc_agent.knowledge.biomarkers import population_signature
from nsclc_agent.knowledge.trials import TRIALS_BY_ID, population_mismatches
from nsclc_agent.state import CaseRunState, EvidenceLevel

SN = "No hemoptysis, no leg weakness, no fever."


def _trial(trial_id):
    return TRIALS_BY_ID[trial_id].to_dict()


def _subject(**over):
    base = {
        "intervention_regimen_ids": ["osimertinib_consolidation"],
        "population_stage": "IIIB",
        "population_tnm": {"t": "T2b", "n": "N2b", "m": "M0"},
        "population_histology": "adenocarcinoma",
        "population_drivers": {"egfr": ["l858r"]},
    }
    base.update(over)
    return base


# ------------------------------------------------------------ the matcher

def test_wrong_stage_is_a_mismatch():
    reasons = population_mismatches(
        _trial("LAURA"), _subject(population_stage="IVA",
                                  population_tnm={"t": "T2a", "n": "N0",
                                                  "m": "M1b"}))
    assert any("stage IVA outside" in r for r in reasons)


def test_edition_migration_is_not_a_mismatch():
    """9th-edition IIIB whose descriptors are 8th-edition IIIA: ADAURA
    covers it — a renamed group, not a new population."""
    assert population_mismatches(_trial("ADAURA"), _subject()) == []


def test_declared_extrapolation_is_honored_for_stage_only():
    subject = _subject(population_tnm={"t": "T4", "n": "N2a", "m": "M0"})
    assert any("stage" in r for r in
               population_mismatches(_trial("ADAURA"), subject))
    assert population_mismatches(
        _trial("ADAURA"), subject,
        declared_extrapolations={"ADAURA"}) == []
    # ...but a declaration never excuses a DRIVER mismatch.
    subject["population_drivers"] = {"egfr": ["exon20ins"]}
    assert population_mismatches(_trial("ADAURA"), subject,
                                 declared_extrapolations={"ADAURA"})


def test_wrong_variant_class_is_a_mismatch():
    reasons = population_mismatches(
        _trial("FLAURA"),
        _subject(population_stage="IVA",
                 population_tnm={"t": "T2a", "n": "N0", "m": "M1b"},
                 population_drivers={"egfr": ["exon20ins"]}))
    assert any("egfr_ex19del_l858r" in r for r in reasons)


def test_cooccurring_resistance_class_disqualifies_classical():
    reasons = population_mismatches(
        _trial("FLAURA"),
        _subject(population_stage="IVA",
                 population_tnm={"t": "T2a", "n": "N0", "m": "M1b"},
                 population_drivers={"egfr": ["c797s", "l858r"]}))
    assert any("enrollment class" in r for r in reasons)


def test_gene_level_driver_requirement():
    subject = _subject(population_stage="IVA",
                       population_tnm={"t": "T2a", "n": "N0", "m": "M1b"},
                       population_drivers={})
    assert any("ROS1-positive" in r for r in
               population_mismatches(_trial("TRIDENT1"), subject))
    subject["population_drivers"] = {"ros1": []}
    assert population_mismatches(_trial("TRIDENT1"), subject) == []


def test_egfr_alk_excluded_trial_on_egfr_positive_claim():
    reasons = population_mismatches(
        _trial("KEYNOTE671"),
        _subject(population_stage="IIIA",
                 population_tnm={"t": "T2a", "n": "N2a", "m": "M0"}))
    assert any("excluded EGFR/ALK" in r for r in reasons)


def test_histology_conflict_and_unknown_leniency():
    squamous = _subject(population_histology="squamous",
                        population_drivers={})
    reasons = population_mismatches(_trial("ADAURA"), squamous)
    assert any("squamous histology" in r for r in reasons)
    unknown = _subject(population_histology=None, population_drivers={})
    assert not any("histology" in r for r in
                   population_mismatches(_trial("ADAURA"), unknown))


def test_partial_subjects_are_graded_structurally_only():
    """A hand-built claim without population fields raises nothing —
    the semantic layer only judges what the subject actually asserts."""
    assert population_mismatches(
        _trial("LAURA"),
        {"intervention_regimen_ids": ["osimertinib_consolidation"]}) == []


# ------------------------------------------------------ critic integration

def _laura_row(state):
    return state.add_evidence(
        EvidenceLevel.TRIAL, "trial_lookup", "LAURA",
        {"trial": _trial("LAURA")})


def test_critic_flags_right_regimen_wrong_population():
    state = CaseRunState()
    row = _laura_row(state)
    state.add_claim(
        "treatment_option", "Consolidation osimertinib", [row],
        subject=_subject(population_stage="IVA",
                         population_tnm={"t": "T2a", "n": "N0", "m": "M1b"}),
        support_relation="trial_anchor")
    issues = CriticAgent._claim_guard(state)
    assert any("CLAIM_POPULATION_MISMATCH[C0001]" in i and "LAURA" in i
               for i in issues)


def test_critic_honors_plan_declared_extrapolation():
    state = CaseRunState()
    row = _laura_row(state)
    state.outputs["treatment_plan"] = {"extrapolations": [
        {"trial_id": "LAURA", "justification": "tumor board rationale"}]}
    state.add_claim(
        "treatment_option", "Consolidation osimertinib", [row],
        subject=_subject(population_stage="IVA",
                         population_tnm={"t": "T2a", "n": "N0", "m": "M1b"}),
        support_relation="trial_anchor")
    assert not [i for i in CriticAgent._claim_guard(state)
                if "CLAIM_POPULATION_MISMATCH" in i]


def test_statistic_claim_requires_cohort_grade_source():
    state = CaseRunState()
    trial_row = _laura_row(state)
    state.add_claim(
        "prognosis_context", "Stage IIIB cohort five-year OS ~26%",
        [trial_row],
        subject={"population_stage": "IIIB"},
        support_relation="population_statistic")
    issues = CriticAgent._claim_guard(state)
    assert any("CLAIM_STATISTIC_SOURCE[C0001]" in i for i in issues)

    cohort_row = state.add_evidence(
        EvidenceLevel.COHORT, "prognosis_table", "stage IIIB cohort figure",
        {"stage_group": "IIIB"})
    state.add_claim(
        "prognosis_context", "Stage IIIB cohort five-year OS ~26%",
        [cohort_row],
        subject={"population_stage": "IIIB"},
        support_relation="population_statistic")
    issues = CriticAgent._claim_guard(state)
    assert not any("C0002" in i for i in issues)


# ------------------------------------------------------------ the pipeline

def test_pipeline_claims_carry_population_facts():
    state = NSCLCRunner().run_case(Case(
        t="T2b", n="N2b", m="M0",
        presentation=f"Multi-station N2b, unresectable per MDT. PET-CT/brain "
                     f"MRI M0. EGFR L858R. {SN}",
        facts={"driver_mutations": {"egfr": "L858R", "alk": "negative"},
               "histologic_category": "adenocarcinoma",
               "resectability_category": "UNRESECTABLE", "ecog_ps": 1}))
    option = next(c for c in state.claims if c.kind == "treatment_option"
                  and c.subject.get("intervention_regimen_ids"))
    assert option.subject["population_drivers"] == {"egfr": ["l858r"]}
    assert option.subject["population_histology"] == "adenocarcinoma"
    assert option.subject["population_tnm"] == {"t": "T2b", "n": "N2b",
                                                "m": "M0"}
    assert not [i for i in state.outputs["safety_audit"]["issues"]
                if "CLAIM_POPULATION_MISMATCH" in i
                or "CLAIM_STATISTIC_SOURCE" in i]


def test_population_signature_normalization():
    sig = population_signature({"driver_mutations": {
        "EGFR": "L858R + C797S", "alk": "negative", "MET": "exon 14 skipping",
        "BRAF": "V600E", "KRAS": "not tested", "HER2": "YVMA insertion"}})
    assert sig == {"egfr": ["c797s", "l858r"], "met": ["ex14_skipping"],
                   "braf": ["v600e"], "erbb2": []}
