"""Claim-level evidence entailment (red-team review §11).

Pinned here:

* each treatment-option claim cites only the trial rows that cover ITS
  regimens — the cCRT claim never carries the LAURA row and vice versa;
* regimen-free options are protocol_grounded and borrow no trial rows;
* the critic's claim guard independently surfaces dangling evidence ids,
  unsupported regimen-bearing claims, and citations borrowed from a
  different claim (support mismatch);
* the narrowing survives the parallel wave (temp-id remap) and state
  serialization round-trips the new claim fields.
"""

from __future__ import annotations

import json
from pathlib import Path

from nsclc_agent import Case, NSCLCRunner
from nsclc_agent.agents.critic import CriticAgent
from nsclc_agent.state import CaseRunState, EvidenceLevel

SN = "No hemoptysis, no leg weakness, no fever."

IIIB_EGFR = Case(
    t="T2b", n="N2b", m="M0",
    presentation=f"Multi-station N2b, unresectable per MDT. PET-CT/brain "
                 f"MRI M0. EGFR L858R. {SN}",
    facts={"driver_mutations": {"egfr": "L858R", "alk": "negative"},
           "histologic_category": "adenocarcinoma",
           "resectability_category": "UNRESECTABLE", "ecog_ps": 1})


def _trial_of(state, eid):
    return ((state.evidence[eid].payload or {}).get("trial") or {}).get(
        "trial_id")


def _claims_by_text(state, needle):
    return next(c for c in state.claims
                if c.kind == "treatment_option" and needle in c.text)


# ------------------------------------------------------------- narrowing

def test_each_option_claim_cites_only_its_own_trials():
    state = NSCLCRunner().run_case(IIIB_EGFR)
    ccrt = _claims_by_text(state, "chemoradiation")
    osi = _claims_by_text(state, "osimertinib")
    ccrt_trials = {_trial_of(state, e) for e in ccrt.evidence_ids}
    osi_trials = {_trial_of(state, e) for e in osi.evidence_ids}
    assert ccrt_trials == {"RTOG0617"}
    assert osi_trials == {"LAURA"}
    assert ccrt.support_relation == "trial_anchor"
    assert ccrt.subject["intervention_regimen_ids"] == ["ccrt_60gy"]
    assert ccrt.subject["population_stage"] == "IIIB"
    # No claim-level issues on the honest rule-mode plan.
    assert not [i for i in state.outputs["safety_audit"]["issues"]
                if i.startswith("CLAIM")]


def test_regimen_free_options_are_protocol_grounded():
    state = NSCLCRunner().run_case(Case(
        t="T1c", n="N0", m="M0",
        presentation=f"1.8 cm peripheral adenocarcinoma, operable. {SN}",
        facts={"driver_mutations": {"egfr": "negative", "alk": "negative"},
               "histologic_category": "adenocarcinoma", "operable": True}))
    surgery = _claims_by_text(state, "resection")
    assert surgery.support_relation == "protocol_grounded"
    assert surgery.evidence_ids == []
    assert not [i for i in state.outputs["safety_audit"]["issues"]
                if "CLAIM_UNSUPPORTED" in i]


def test_narrowing_survives_the_parallel_wave():
    state = NSCLCRunner(panel_concurrency=2).run_case(
        IIIB_EGFR, enable_panel=True)
    assert state.outputs["run_meta"]["execution"] == "parallel_wave"
    osi = _claims_by_text(state, "osimertinib")
    assert osi.evidence_ids, "wave temp ids were not remapped into claims"
    assert all(e in state.evidence for e in osi.evidence_ids)
    assert {_trial_of(state, e) for e in osi.evidence_ids} == {"LAURA"}


def test_golden_sweep_has_no_claim_issues():
    payload = json.loads(Path("nsclc_agent/eval/golden/cases.json")
                         .read_text(encoding="utf-8"))
    for entry in payload["cases"]:
        if "audit_plan" in entry:
            continue  # safety-net probes carry no runnable case
        state = NSCLCRunner().run_case(Case.from_dict(dict(entry["case"])))
        claim_issues = [i for i in
                        (state.outputs.get("safety_audit") or {})
                        .get("issues", [])
                        if i.startswith("CLAIM")]
        assert not claim_issues, (entry["id"], claim_issues)


# ------------------------------------------------------- critic independence

def _guard(state):
    return CriticAgent._claim_guard(state)


def test_dangling_evidence_is_loud():
    state = CaseRunState()
    state.add_claim("treatment_option", "Ghost therapy", ["E9999"],
                    subject={"intervention_regimen_ids": ["ccrt_60gy"]},
                    support_relation="trial_anchor")
    issues = _guard(state)
    assert any("CLAIM_DANGLING_EVIDENCE" in i and "E9999" in i
               for i in issues)


def test_unsupported_and_mismatched_claims_are_distinguished():
    state = CaseRunState()
    kg_row = state.add_evidence(EvidenceLevel.KG_EXTRACTED, "guideline_lookup",
                                "kg context", {})
    laura_row = state.add_evidence(
        EvidenceLevel.TRIAL, "trial_lookup", "LAURA",
        {"trial": {"trial_id": "LAURA",
                   "regimen_ids": ["osimertinib_consolidation"]}})
    # Only non-releasable support → unsupported.
    state.add_claim("treatment_option", "Consolidation osimertinib",
                    [kg_row],
                    subject={"intervention_regimen_ids":
                             ["osimertinib_consolidation"]},
                    support_relation="trial_anchor")
    # Releasable but borrowed from a different claim → mismatch.
    state.add_claim("treatment_option", "Definitive chemoradiation",
                    [laura_row],
                    subject={"intervention_regimen_ids": ["ccrt_60gy"]},
                    support_relation="trial_anchor")
    # Correctly entailed → silent.
    state.add_claim("treatment_option", "Consolidation osimertinib",
                    [laura_row],
                    subject={"intervention_regimen_ids":
                             ["osimertinib_consolidation"]},
                    support_relation="trial_anchor")
    issues = _guard(state)
    assert any("CLAIM_UNSUPPORTED[C0001]" in i for i in issues)
    assert any("CLAIM_SUPPORT_MISMATCH[C0002]" in i for i in issues)
    assert not any("C0003" in i for i in issues)


def test_claim_fields_round_trip_serialization():
    state = CaseRunState()
    state.add_claim("treatment_option", "X", [],
                    subject={"intervention_regimen_ids": ["ccrt_60gy"]},
                    support_relation="trial_anchor")
    rebuilt = CaseRunState.from_dict(state.to_dict())
    claim = rebuilt.claims[0]
    assert claim.subject == {"intervention_regimen_ids": ["ccrt_60gy"]}
    assert claim.support_relation == "trial_anchor"
