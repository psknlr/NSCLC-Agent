"""Machine-executable indication predicates: one declaration, three call
sites.

Pinned here:

* every library regimen carries a declaration (a new regimen without one
  fails this suite AND warns at audit time);
* three-valued evaluation with unknown routed to workup, never to a guess;
* the planner's decision table and the declarations agree on every golden
  case (zero table/declaration divergences — the sweep that keeps them
  honest as both grow);
* the critic independently blocks out-of-population regimens — including
  errors no prior rule could see (pembrolizumab monotherapy at TPS 20%)
  and model-hallucinated regimen ids;
* declared extrapolations and TNM edition migrations are honored, not
  re-blocked.
"""

from __future__ import annotations

import json
from pathlib import Path

from nsclc_agent import Case, NSCLCRunner
from nsclc_agent.knowledge.indications import (
    ELIGIBLE,
    INELIGIBLE,
    UNKNOWN,
    evaluate_indication,
    undeclared_regimens,
)
from nsclc_agent.safety import rules

SN = "No hemoptysis, no leg weakness, no fever."
GOLDEN = Path("nsclc_agent/eval/golden/cases.json")


def _check(stage, facts, regimen_ids, plan_extra=None):
    plan = {"regimen_ids": regimen_ids, "options": [], **(plan_extra or {})}
    return rules.check_plan({"stage_group": stage}, {"tnm": {}, **facts}, plan)


# ---------------------------------------------------------------- coverage

def test_every_regimen_is_declared():
    assert undeclared_regimens() == []


# ------------------------------------------------------------- three values

def test_three_valued_evaluation():
    ok = evaluate_indication("osimertinib_first_line", "IVA", {
        "driver_mutations": {"egfr": "L858R"},
        "histologic_category": "adenocarcinoma"})
    assert ok["verdict"] == ELIGIBLE

    bad = evaluate_indication("osimertinib_first_line", "IVA", {
        "driver_mutations": {"egfr": "exon 20 insertion"}})
    assert bad["verdict"] == INELIGIBLE
    assert any("ex19del/L858R" in f for f in bad["failed_conditions"])

    unknown = evaluate_indication("pembro_pemetrexed_platinum", "IVA", {
        "driver_mutations": {"egfr": "not_tested", "alk": "not_tested"},
        "histologic_category": "adenocarcinoma"})
    assert unknown["verdict"] == UNKNOWN
    assert any("Tier-A" in u for u in unknown["unknown_conditions"])


def test_pd_l1_threshold_and_prior_therapy_conditions():
    assert evaluate_indication("pembro_monotherapy", "IVA", {
        "driver_mutations": {"egfr": "negative", "alk": "negative"},
        "histologic_category": "adenocarcinoma",
        "pd_l1": {"tps": 20}})["verdict"] == INELIGIBLE
    assert evaluate_indication("pembro_monotherapy", "IVA", {
        "driver_mutations": {"egfr": "negative", "alk": "negative"},
        "histologic_category": "adenocarcinoma"})["verdict"] == UNKNOWN
    assert evaluate_indication("atezolizumab_adjuvant", "IIB", {
        "driver_mutations": {"egfr": "negative", "alk": "negative"},
        "histologic_category": "adenocarcinoma",
        "pd_l1": {"tc": 0}})["verdict"] == INELIGIBLE
    # Later-line regimen without a treatment history: unknown, not a guess.
    assert evaluate_indication("tdxd_subsequent_line", "IVA", {
        "driver_mutations": {"erbb2": "YVMA insertion"},
        "histologic_category": "adenocarcinoma"})["verdict"] == UNKNOWN


def test_stage_condition_is_edition_aware():
    verdict = evaluate_indication("osimertinib_adjuvant", "IIIB", {
        "driver_mutations": {"egfr": "L858R"},
        "histologic_category": "adenocarcinoma",
        "tnm": {"t": "T2b", "n": "N2b", "m": "M0"}})
    assert verdict["verdict"] == ELIGIBLE
    assert verdict["edition_migration"]
    # Same 9th-edition group, different descriptors: IIIB in both editions
    # stays ineligible (the declared-extrapolation path handles it).
    verdict = evaluate_indication("osimertinib_adjuvant", "IIIB", {
        "driver_mutations": {"egfr": "L858R"},
        "histologic_category": "adenocarcinoma",
        "tnm": {"t": "T4", "n": "N2a", "m": "M0"}})
    assert verdict["verdict"] == INELIGIBLE


def test_squamous_tier_a_leniency():
    """Squamous with untested EGFR/ALK: chemo-IO is not held hostage."""
    verdict = evaluate_indication("pembro_carbo_taxane", "IVB", {
        "driver_mutations": {"egfr": "not_tested", "alk": "not_tested"},
        "histologic_category": "squamous"})
    assert verdict["verdict"] == ELIGIBLE


# -------------------------------------------------- table↔declaration sweep

def test_decision_table_never_diverges_from_declarations():
    """Run every golden case: the planner must never have to drop one of
    its own proposals at the predicate gate."""
    payload = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for entry in payload["cases"]:
        if "audit_plan" in entry:
            continue  # safety-net probes carry no runnable case
        state = NSCLCRunner().run_case(Case.from_dict(dict(entry["case"])))
        plan = state.outputs.get("treatment_plan") or {}
        divergences = [u for u in plan.get("uncertainties") or []
                       if "适应证谓词判定不适用" in u]
        assert not divergences, (entry["id"], divergences)


def test_planner_unknown_routes_to_workup_as_provisional():
    state = NSCLCRunner().run_case(Case(
        t="T2a", n="N0", m="M1b",
        presentation=f"Metastatic disease, brain MRI negative. Histology "
                     f"pending. {SN}",
        facts={"driver_mutations": {"egfr": "negative", "alk": "negative"},
               "ngs_done": True, "ecog_ps": 1}))
    plan = state.outputs["treatment_plan"]
    assert plan.get("provisional_regimens")
    assert any(p["regimen_id"] == "pembro_pemetrexed_platinum"
               for p in plan["provisional_regimens"])
    assert any("Resolve for" in w for w in plan["workup_needed"])
    violations = state.outputs["safety_audit"]["violations"]
    assert any(v["rule_id"] == "INDICATION_PREDICATE"
               and v["severity"] == "warn" for v in violations)
    # Unknown routes BACK to information-gathering, and never to blocked:
    # the provisional plan plus open questions is the correct posture.
    assert state.release_status in ("treatment_recommendation",
                                    "needs_more_information")
    assert state.release_status != "blocked"


# --------------------------------------------------- critic independence

def test_critic_blocks_tps20_pembro_monotherapy():
    """An error NO prior rule could see: KEYNOTE-024 requires TPS≥50."""
    found = _check("IVA", {"driver_mutations": {"egfr": "negative",
                                                "alk": "negative"},
                           "histologic_category": "adenocarcinoma",
                           "pd_l1": {"tps": 20}},
                   ["pembro_monotherapy"])
    assert any(v.rule_id == "INDICATION_PREDICATE" and v.severity == "block"
               for v in found)


def test_critic_warns_on_hallucinated_regimen():
    found = _check("IVA", {"driver_mutations": {"egfr": "negative",
                                                "alk": "negative"},
                           "histologic_category": "adenocarcinoma"},
                   ["magic_pill_9000"])
    assert any(v.rule_id == "INDICATION_UNDECLARED" for v in found)


def test_critic_blocks_wrong_histology_backbone():
    """Squamous case on the nonsquamous pemetrexed backbone."""
    found = _check("IVB", {"driver_mutations": {"egfr": "negative",
                                                "alk": "negative"},
                           "histologic_category": "squamous",
                           "pd_l1": {"tps": 10}},
                   ["pembro_pemetrexed_platinum"])
    assert any(v.rule_id == "INDICATION_PREDICATE" and v.severity == "block"
               for v in found)


def test_declared_extrapolation_is_not_predicate_blocked():
    state = NSCLCRunner().run_case(Case(
        t="T4", n="N2a", m="M0",
        presentation=f"T4N2a, MDT judges R0 achievable, operable. "
                     f"PET-CT/brain MRI M0. EGFR L858R. {SN}",
        facts={"driver_mutations": {"egfr": "L858R", "alk": "negative"},
               "histologic_category": "adenocarcinoma",
               "resectability_category": "RESECTABLE", "ecog_ps": 1}))
    plan = state.outputs["treatment_plan"]
    assert "osimertinib_adjuvant" in plan["regimen_ids"]
    assert plan["extrapolations"]
    violations = state.outputs["safety_audit"]["violations"]
    assert not any(v["rule_id"] == "INDICATION_PREDICATE"
                   and v["severity"] == "block" for v in violations)
    assert any(v["rule_id"] == "TRIAL_STAGE_EXTRAPOLATION"
               for v in violations)


def test_indication_report_is_published_for_the_final_plan():
    state = NSCLCRunner().run_case(Case(
        t="T2a", n="N0", m="M1b",
        presentation=f"Adenocarcinoma, adrenal met, brain MRI negative. "
                     f"EGFR L858R. {SN}",
        facts={"driver_mutations": {"egfr": "L858R", "alk": "negative"},
               "histologic_category": "adenocarcinoma", "ngs_done": True,
               "ecog_ps": 1}))
    report = state.outputs["indication_report"]
    by_rid = {r["regimen_id"]: r for r in report["regimens"]}
    assert by_rid["osimertinib_first_line"]["verdict"] == ELIGIBLE
    assert all(r["declared"] for r in report["regimens"])
