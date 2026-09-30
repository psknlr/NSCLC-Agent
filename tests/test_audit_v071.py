"""v0.7.1 full adversarial audit — regressions for every confirmed finding
outside sequencing/CNS/organ gates (those live in
test_audit_sequencing_cns_organ.py).

Grouped by surface: the driver parser, the rule engine, the release
gates and views, the dose scanner, the conversation layer, the evidence
guards, the eval harness and the ledgers, and journal replay.
"""

from __future__ import annotations

import json

import pytest

from nsclc_agent import Case, ConsultationSession, NSCLCRunner, render
from nsclc_agent.agents.critic import CriticAgent, _outcome_numbers
from nsclc_agent.conversation import extract_facts_deterministic as extract
from nsclc_agent.eval.adjudication import (
    adjudication_summary,
    append_adjudication,
)
from nsclc_agent.eval.run_eval import validate_golden
from nsclc_agent.knowledge.biomarkers import driver_status, egfr_variant_classes
from nsclc_agent.knowledge.trials import TRIALS_BY_ID
from nsclc_agent.safety import rules
from nsclc_agent.safety.rules import DOSE_RE, dose_in_payload, redact_doses
from nsclc_agent.state import CaseRunState, EvidenceLevel

SN = "No hemoptysis, no leg weakness, no fever."
NEG = {"egfr": "negative", "alk": "negative"}


def _check(stage, facts, plan, n_category=None):
    staging = {"stage_group": stage}
    if n_category:
        staging["n_category"] = n_category
    return rules.check_plan(staging, {"tnm": {}, **facts},
                            {"regimen_ids": [], "options": [], **plan})


def _blocks(found):
    return {v.rule_id for v in found if v.severity == "block"}


# ------------------------------------------------------------ driver parser

@pytest.mark.parametrize("value,status,classes", [
    # A negated SECONDARY variant never negates the gene.
    ("L858R detected; T790M not detected", "positive", ["l858r"]),
    ("19外显子缺失阳性，T790M阴性", "positive", ["ex19del"]),
    ("L858R positive (plasma ctDNA wt)", "positive", ["l858r"]),
    ("Exon 19 deletion; C797S not detected", "positive", ["ex19del"]),
    ("EGFR L858R, no T790M", "positive", ["l858r"]),
    ("negative for exon 19 deletion, L858R and T790M", "negative", []),
    # Positive only on positive evidence — never by default.
    ("No ALK rearrangement", "negative", []),
    ("(-)", "negative", []), ("wild-type", "negative", []),
    ("neg", "negative", []), ("未见突变", "negative", []),
    ("未检测到突变", "negative", []), (False, "negative", []),
    ("failed", "unknown", []), ("QNS", "unknown", []),
    ("insufficient tissue", "unknown", []), ("equivocal", "unknown", []),
    # HGVS and Chinese exon forms.
    ("p.E746_A750del", "positive", ["ex19del"]),
    ("p.Leu858Arg", "positive", ["l858r"]),
    ("19号外显子缺失", "positive", ["ex19del"]),
    ("20号外显子插入突变", "positive", ["exon20ins"]),
])
def test_driver_parser(value, status, classes):
    assert driver_status(value) == status
    assert sorted(egfr_variant_classes(value)) == classes


def test_negated_secondary_variant_never_releases_immunotherapy():
    state = NSCLCRunner().run_case(Case(
        t="T2a", n="N2a", m="M1c1",
        presentation=f"Adenocarcinoma. Brain MRI negative. ECOG 1. {SN}",
        facts={"driver_mutations": {"egfr": "L858R detected; T790M not "
                                            "detected", "alk": "negative"},
               "histologic_category": "adenocarcinoma", "pd_l1": {"tps": 80},
               "ecog_ps": 1, "ngs_done": True}))
    rids = state.outputs["treatment_plan"]["regimen_ids"]
    assert "osimertinib_first_line" in rids
    assert "pembro_monotherapy" not in rids


def test_gene_keys_and_notations_are_normalized():
    for driver, expected in (({"braf": "p.Val600Glu"},
                              "dabrafenib_trametinib_first_line"),
                             ({"met": "c.3028+1G>T splice site"},
                              "capmatinib_first_line"),
                             ({"ROS1_fusion": "CD74-ROS1"},
                              "repotrectinib_first_line")):
        state = NSCLCRunner().run_case(Case(
            t="T2a", n="N2a", m="M1c1",
            presentation=f"Adenocarcinoma. Brain MRI negative. {SN}",
            facts={"driver_mutations": {**NEG, **driver},
                   "histologic_category": "adenocarcinoma",
                   "pd_l1": {"tps": 80}, "ecog_ps": 1, "ngs_done": True}))
        assert state.outputs["treatment_plan"]["regimen_ids"] == [expected]


# ------------------------------------------------------------- rule engine

_EGFR = {"driver_mutations": {"egfr": "L858R", "alk": "negative"},
         "histologic_category": "adenocarcinoma"}


def test_free_text_and_unknown_ids_cannot_carry_a_drug():
    assert "OPTION_DRUG_UNBOUND" in _blocks(_check("IVB", _EGFR, {"options": [
        {"name": "Pembrolizumab monotherapy", "regimen_ids": []}]}))
    assert "OPTION_DRUG_UNBOUND" in _blocks(_check("IVB", _EGFR, {
        "regimen_ids": ["osimertinib_first_line"],
        "options": [{"name": "Osimertinib + pembrolizumab combination",
                     "regimen_ids": ["osimertinib_first_line"]}]}))
    assert "INDICATION_UNDECLARED" in _blocks(_check(
        "IVB", _EGFR, {"regimen_ids": ["pembrolizumab_monotherapy"]}))
    # Whitespace/case padding and option-only ids are audited too.
    assert "DRIVER_FIRST_LINE" in _blocks(_check(
        "IVB", _EGFR, {"regimen_ids": [" Pembro_Monotherapy "]}))
    assert "DRIVER_FIRST_LINE" in _blocks(_check("IVB", _EGFR, {
        "options": [{"name": "x", "regimen_ids": ["pembro_monotherapy"]}]}))
    # A negated drug name in an option is not a proposal.
    assert not _blocks(_check("IVB", _EGFR, {
        "regimen_ids": ["osimertinib_first_line"],
        "options": [{"name": "Osimertinib (not pembrolizumab)",
                     "regimen_ids": ["osimertinib_first_line"]}]}))


def test_n3_surgery_wording_and_negated_controls():
    n3 = {**_EGFR, "driver_mutations": NEG, "tnm": {"n": "N3"}}
    for text in ("Induction chemoradiation followed by surgery",
                 "then thoracotomy if downstaged",
                 "Refer to thoracic surgery for operative management"):
        assert "N3_NO_SURGERY" in _blocks(_check(
            "IIIC", n3, {"regimen_ids": ["ccrt_60gy"], "options": [
                {"name": text, "regimen_ids": ["ccrt_60gy"]}]}, "N3")), text
    for text in ("Definitive chemoradiation; surgery is not indicated",
                 "Non-surgical definitive chemoradiation"):
        assert "N3_NO_SURGERY" not in _blocks(_check(
            "IIIC", n3, {"regimen_ids": ["ccrt_60gy"], "options": [
                {"name": text, "regimen_ids": ["ccrt_60gy"]}]}, "N3")), text


def test_cns_option_negation_and_body_sbrt_do_not_count():
    facts = {**_EGFR, "cns_metastases": {"status": "present",
                                         "symptomatic": True,
                                         "treated": False}}
    for name in ("Neuro-oncology consult not needed",
                 "SRS not needed, defer local therapy",
                 "Stereotactic body RT to adrenal"):
        assert "CNS_UNTREATED_SYMPTOMATIC" in _blocks(_check(
            "IVB", facts, {"regimen_ids": ["osimertinib_first_line"],
                           "options": [{"name": name, "regimen_ids": []}]}))


def test_blank_extrapolation_justification_is_not_a_declaration():
    plan = {"regimen_ids": ["osimertinib_consolidation"],
            "extrapolations": [{"trial_id": "LAURA", "justification": " "}]}
    assert "TRIAL_STAGE_BOUNDARY" in _blocks(_check("IVA", _EGFR, plan))
    # …and a declaration for a trial the plan does not use grants nothing.
    assert rules.declared_extrapolation_trials({
        "regimen_ids": ["ccrt_60gy"],
        "extrapolations": [{"trial_id": "LAURA",
                            "justification": "x" * 40}]}) == set()


def test_predicates_match_their_registration_trials():
    neg = {"driver_mutations": NEG, "histologic_category": "adenocarcinoma",
           "pd_l1": {"tps": 10}}
    assert "INDICATION_PREDICATE" in _blocks(_check(
        "IB", neg, {"regimen_ids": ["pembro_adjuvant"]}))
    assert "INDICATION_PREDICATE" in _blocks(_check(
        "IIIA", {**neg, "resectability_category": "UNRESECTABLE"},
        {"regimen_ids": ["pembro_adjuvant"]}))
    assert "CONSOLIDATION_WITHOUT_CRT" in _blocks(_check(
        "IIIB", {**neg, "resectability_category": "UNRESECTABLE"},
        {"regimen_ids": ["durva_consolidation"]}))
    assert not _blocks(_check(
        "IIIB", {**neg, "resectability_category": "UNRESECTABLE"},
        {"regimen_ids": ["ccrt_60gy", "durva_consolidation"]}))


# ------------------------------------------------------------ dose scanner

def test_dose_scanner_hardening():
    for text in ("pembrolizumab 200-mg every 3 weeks", "帕博利珠单抗 ２００ｍｇ",
                 "200 mgs q3w", "carboplatin AUC=5", "400 ug", "180 cGy",
                 "靶区剂量（60~66）Gy", "74-Gy", "5 ㎎"):
        assert DOSE_RE.search(text), text
    for text in ("ccrt_60gy", "TPS≥50%", "IB (≥4 cm)", "G12C", "ECOG 1"):
        assert not dose_in_payload(text), text
    assert redact_doses("RT 45 to 50.4 Gy after ccrt_60gy", "[X]") == \
        "RT [X] after ccrt_60gy"


def test_every_served_kg_recommendation_is_dose_free():
    from nsclc_agent.knowledge.guideline_kg import load_default

    kg = load_default()
    if kg is None:
        pytest.skip("guideline KG not shipped in this build")
    leaks = [rec["id"] for rec in kg.recs
             if DOSE_RE.search(json.dumps(kg.get(rec["id"]),
                                          ensure_ascii=False))]
    assert leaks == []


# ---------------------------------------------------- release gates, views

def test_dose_draft_never_opens_on_a_plan_with_open_workup():
    state = NSCLCRunner().run_case(Case(
        t="T2a", n="N0", m="M1b",
        presentation=f"Adenocarcinoma, adrenal met. {SN}",  # no CNS status
        facts={"driver_mutations": NEG, "histologic_category":
               "adenocarcinoma", "ngs_done": True, "ecog_ps": 1,
               "pd_l1": {"tps": 80}}),
        role="oncologist", allow_dose_planning=True)
    assert state.release_status == "needs_more_information"
    assert "dose_plan" not in state.outputs


def test_blocked_plan_never_reaches_the_patient():
    state = CaseRunState()
    state.release_status = "blocked"
    state.outputs["treatment_plan"] = {"summary": "Pembrolizumab now",
                                       "options": [{"name": "Pembro"}]}
    view = render(state, "patient")
    assert view["summary"] == "" and view["options"] == []
    # Unknown role strings fail closed to the patient view.
    assert "treatment_plan" not in render(state, "Patient")
    assert "treatment_plan" not in render(state, "患者")


# ------------------------------------------------------ conversation layer

def test_chat_extractor_never_invents_or_flips_results():
    assert extract("ALK阴性。EGFR结果未出，KRAS阴性。")["driver_mutations"] \
        == {"alk": "ALK阴性", "kras": "KRAS阴性"}
    assert "egfr" not in (extract("EGFR pending, KRAS negative")
                          .get("driver_mutations") or {})
    drivers = extract("EGFR L858R，T790M阳性，ALK阴性")["driver_mutations"]
    assert egfr_variant_classes(drivers["egfr"]) == {"l858r", "t790m"}
    assert driver_status(extract("EGFR/ALK阴性")["driver_mutations"]
                         ["egfr"]) == "negative"
    assert extract("EGFR p.E746_A750del. ECOG 1")["driver_mutations"]
    for text, want in (("tumor is not resectable", "UNRESECTABLE"),
                       ("non-resectable per MDT", "UNRESECTABLE"),
                       ("是否可切除尚待MDT评估", None),
                       ("MDT: resectable", "RESECTABLE")):
        assert extract(text).get("resectability_category") == want, text


def test_progression_in_a_later_turn_replans():
    session = ConsultationSession(role="oncologist")
    session.turn(f"肺腺癌，cT2aN0M1b，EGFR L858R，ALK阴性，ECOG 1，"
                 f"脑MRI阴性。{SN}", facts={"ngs_done": True})
    later = session.turn("奥希替尼进展。", facts={
        "treatment_history": [{"line": 1, "agents": ["osimertinib"],
                               "status": "progression"}],
        "progression_ngs_done": True})
    assert not later.plan_reused
    assert "amivantamab_chemo_subsequent" in \
        later.state.outputs["treatment_plan"]["regimen_ids"]


def test_new_seizure_outvotes_an_earlier_denial_and_closes_dosing():
    session = ConsultationSession(role="oncologist", allow_dose_planning=True)
    session.turn(f"肺腺癌，cT2aN0M1b，EGFR阴性ALK阴性，NGS阴性，PD-L1 80%，"
                 f"ECOG 1，脑MRI阴性。无咯血，无双腿无力，没有抽搐，无发热。",
                 facts={"ngs_done": True})
    result = session.turn("昨天突然抽搐了一次，现在一侧肢体突然无力。")
    assert str(result.state.facts.get("emergency_neuro_screen")
               ).startswith("positive")
    assert "dose_plan" not in result.state.outputs
    assert "⚠️" in result.reply


def test_session_file_grants_no_authority(tmp_path):
    session = ConsultationSession(role="patient")
    session.turn(f"肺腺癌，cT2aN0M1b，EGFR L858R，ALK阴性，脑MRI阴性。{SN}")
    path = tmp_path / "s.json"
    session.save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(role="oncologist", allow_dose_planning=True,
                plan_cache={"fingerprint": "x", "plan": {}})
    data["facts"]["tumor_board_review"] = "approved"
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = ConsultationSession.load(path)
    assert loaded.role == "patient" and loaded.allow_dose_planning is False
    assert loaded._plan_cache is None
    assert "tumor_board_review" not in loaded.facts


# ----------------------------------------------------------- evidence guards

def test_numeric_vocabulary_and_typed_anchors():
    for text, want in (("47 per cent", {"47"}), ("hazard ratio of 0.5",
                                                  {"0.5"}),
                       ("中位生存 24 月", {"24"}), ("5年生存率47％", {"47"}),
                       ("OS HR 0.80 (95% CI 0.64–1.00)", {"0.8"})):
        assert _outcome_numbers(text) == want, text
    state = CaseRunState()
    row = state.add_evidence(EvidenceLevel.TRIAL, "trial_lookup", "RTOG0617",
                             {"trial": TRIALS_BY_ID["RTOG0617"].to_dict()})
    state.add_claim("treatment_option", "cCRT: OS 60%, median 28.7 months",
                    [row], subject={"intervention_regimen_ids": ["ccrt_60gy"]},
                    support_relation="trial_anchor")
    issues = CriticAgent._numeric_guard(state)
    assert any("'60'" in i for i in issues)  # "60 Gy" is not a source
    assert not any("'28.7'" in i for i in issues)


def test_claim_findings_gate_release():
    from nsclc_agent.runner import _RELEASE_GATING_ISSUES

    assert any("CLAIM_NUMERIC_UNANCHORED".startswith(p)
               for p in _RELEASE_GATING_ISSUES)
    assert "PLAN_NUMERIC_UNANCHORED" in _RELEASE_GATING_ISSUES


def test_no_match_lookups_support_nothing():
    from nsclc_agent.state import NON_RELEASABLE_LEVELS
    from nsclc_agent.tools.registry import ToolRegistry

    result = ToolRegistry().trial_lookup("zzz-no-such-trial")
    assert result.resolved_level() in NON_RELEASABLE_LEVELS
    result = ToolRegistry().regimen_lookup("zzz-no-such-regimen")
    assert result.resolved_level() in NON_RELEASABLE_LEVELS


# ----------------------------------------------------- eval and the ledgers

def test_golden_schema_validation_refuses_vacuous_checks():
    base = {"id": "x", "case": {}, "expect": {"regimens_none": ["ccrt_60gy"]}}
    assert validate_golden({"cases": [base]}) == []
    for expect in ({"regimens_nonee": ["ccrt_60gy"]},
                   {"regimens_none": "ccrt_60gy"},
                   {"violations_forbidden": ["NOT_A_RULE"]},
                   {"regimens_any": ["not_a_regimen"]}):
        assert validate_golden({"cases": [dict(base, expect=expect)]}), expect
    audit = {"id": "a", "audit_plan": {}, "expect": {
        "release_status": ["blocked"]}}
    assert validate_golden({"cases": [audit]})


def test_withholding_expected_but_released_is_unsafe():
    from nsclc_agent.eval.run_eval import _release_taxonomy

    assert _release_taxonomy("treatment_recommendation",
                             ["needs_staging_workup"]) == "unsafe_release"


def test_adjudicator_identity_and_invalid_events(tmp_path, monkeypatch):
    path = tmp_path / "adj.jsonl"
    monkeypatch.setenv("NSCLC_ADJUDICATION", str(path))
    cases = json.loads(__import__("nsclc_agent.eval.run_eval", fromlist=["x"])
                       .GOLDEN_DIR.joinpath("cases.json")
                       .read_text(encoding="utf-8"))["cases"]
    cid = cases[0]["id"]
    append_adjudication(path, cases=cases, case_id=cid, verdict="agree",
                        adjudicator="Dr. Li Wei")
    append_adjudication(path, cases=cases, case_id=cid, verdict="agree",
                        adjudicator="dr.  li wei ")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"case_id": cid, "verdict": "LGTM",
                                 "adjudicator": "Dr. X",
                                 "content_hash": "h"}) + "\n")
        handle.write('{"case_id": "' + cid + '", "verdict": "disag')
    summary = adjudication_summary(cases, path)
    assert summary["per_case"][cid]["adjudicators"] == 1
    assert summary["dual_adjudicated"] == 0
    assert len(summary["ledger_problems"]) == 2


# -------------------------------------------------------------- journal

def test_tampered_journal_result_diverges(tmp_path):
    from nsclc_agent.journal import Journal, JournalDivergence
    from nsclc_agent.skills import SkillRegistry
    from nsclc_agent.state import Budget
    from nsclc_agent.tools.base import CapabilityBroker, ToolHealth
    from nsclc_agent.tools.registry import ToolRegistry

    path = tmp_path / "run.jsonl"
    registry, skills = ToolRegistry(), SkillRegistry.discover()

    def broker(journal):
        return CapabilityBroker("oncologist", "routine", budget=Budget(),
                                skill_registry=skills,
                                active_skill="nsclc.dose_planning",
                                health=ToolHealth(), journal=journal)

    assert registry.call(broker(Journal(path, mode="record")),
                         "regimen_detail",
                         regimen_id="osimertinib_first_line").ok
    entry = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    component = entry["result"]["data"]["regimen"]["components"][0]
    component["dose"] = "2000 mg daily"  # the edit a tamperer would make
    path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    replay = Journal.load(path)
    with pytest.raises(JournalDivergence):
        registry.call(broker(replay), "regimen_detail",
                      regimen_id="osimertinib_first_line")
    assert replay.diverged
