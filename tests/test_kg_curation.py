"""Eligibility evaluation + the per-entry curation workflow.

Pinned here:

* the criteria evaluator is span-first, abstains on extraction conflicts,
  and never lets an assumed/flipped/excluded criterion produce the
  confident mismatch that selection acts on;
* the observed noise cases (val contradicting span, "EGFR WT" abbreviation,
  CJK stage boundaries) evaluate correctly;
* eligibility output honors the dose boundary like every served string;
* the curation ledger is append-only with last-entry-wins, content-hash
  pinned (content change voids a review), and loud on corruption;
* a verification upgrades serving to guideline grade (releasable) and makes
  the entry's criteria trusted: verified population mismatches are excluded
  from case context, verified case-matching cautions raise a visible,
  advisory flag; rejection hides an entry from serving;
* the whole loop works end-to-end through the runner and the CLI.
"""

from __future__ import annotations

import json

import pytest

from nsclc_agent import Case, NSCLCRunner
from nsclc_agent.knowledge import guideline_kg as kg_mod
from nsclc_agent.knowledge.guideline_kg import (
    append_curation,
    load_curation,
    load_default,
    rec_content_hash,
    reload_default,
)
from nsclc_agent.knowledge.kg_eligibility import (
    evaluate_criterion,
    evaluate_rec,
)
from nsclc_agent.safety.rules import DOSE_RE
from nsclc_agent.state import EvidenceLevel

CASE_FACTS = {
    "histologic_category": "adenocarcinoma", "ecog_ps": 1,
    "driver_mutations": {"egfr": "L858R", "alk": "negative"},
    "resectability_category": "UNRESECTABLE", "pd_l1": {"tps": 60},
    "age": 66,
}

CASE = Case(
    t="T4", n="N2b", m="M0",
    presentation="不可切除多站N2腺癌，PET-CT+脑MRI确认M0。"
                 "无咯血、无下肢无力、无发热。",
    facts={"driver_mutations": {"egfr": "L858R", "alk": "negative"},
           "histologic_category": "adenocarcinoma",
           "resectability_category": "UNRESECTABLE", "ecog_ps": 1})


@pytest.fixture()
def curated(tmp_path, monkeypatch):
    """A fresh, isolated curation ledger; the default singleton is restored
    afterwards so no other test sees this ledger."""
    ledger = tmp_path / "curation.jsonl"
    monkeypatch.setenv(kg_mod.CURATION_ENV, str(ledger))
    kg = reload_default()
    yield kg, ledger
    monkeypatch.delenv(kg_mod.CURATION_ENV, raising=False)
    reload_default()


def _crit(**kwargs):
    base = {"ft": "", "feature": "", "op": "=", "val": "", "unit": "",
            "gene": "", "assumed": False, "pol": "include", "neg": False,
            "span": "", "depth": 0}
    base.update(kwargs)
    return base


# --------------------------------------------------------------- evaluator

def test_stage_tokens_cross_cjk_boundaries():
    c = _crit(ft="stage_group", span="IV期", val="")
    out = evaluate_criterion(c, {}, "IVA")
    assert out["verdict"] == "match"
    out = evaluate_criterion(c, {}, "IIB")
    assert out["verdict"] == "mismatch" and out["confident"]


def test_conflicting_extraction_abstains():
    # The observed fault: span says nonsquamous, val says squamous.
    c = _crit(ft="histology", span="nonsquamous NSCLC",
              val="squamous_cell_carcinoma")
    out = evaluate_criterion(c, {"histologic_category": "squamous"}, None)
    assert out["verdict"] == "not_evaluable"
    assert "conflicting_extraction" in out["why"]
    # Same for resectability: span 不可切除, val resectable.
    c = _crit(ft="resectability", span="不可切除", val="resectable")
    out = evaluate_criterion(
        c, {"resectability_category": "RESECTABLE"}, None)
    assert out["verdict"] == "not_evaluable"


def test_assumed_criteria_never_confident():
    c = _crit(ft="histology", span="鳞癌", val="squamous_cell_carcinoma",
              assumed=True)
    out = evaluate_criterion(
        c, {"histologic_category": "adenocarcinoma"}, None)
    assert out["verdict"] == "mismatch" and not out["confident"]
    rec = {"crit": [c]}
    assert evaluate_rec(rec, {"histologic_category": "adenocarcinoma"},
                        None)["verdict"] == "possible_mismatch"


def test_gene_wildtype_including_wt_abbreviation():
    facts = {"driver_mutations": {"egfr": "L858R"}}
    for span in ("EGFR wild-type", "EGFR WT", "EGFR野生型"):
        c = _crit(ft="gene_variant", gene="EGFR", span=span,
                  op="HAS_VARIANT")
        out = evaluate_criterion(c, facts, None)
        assert out["verdict"] == "mismatch", span
    c = _crit(ft="gene_variant", gene="EGFR", op="HAS_VARIANT",
              span="EGFR mutation")
    assert evaluate_criterion(c, facts, None)["verdict"] == "match"
    assert evaluate_criterion(
        c, {"driver_mutations": {}}, None)["verdict"] == "unknown_case_fact"


def test_ecog_pdl1_age_and_unparseable_values():
    assert evaluate_criterion(
        _crit(ft="performance_status", val="ECOG BETWEEN0-1"),
        {"ecog_ps": 2}, None)["verdict"] == "mismatch"
    assert evaluate_criterion(
        _crit(ft="performance_status", val="good"),
        {"ecog_ps": 1}, None)["verdict"] == "not_evaluable"
    assert evaluate_criterion(
        _crit(ft="pdl1", op=">=", val=">=50.0"),
        {"pd_l1": {"tps": 60}}, None)["verdict"] == "match"
    assert evaluate_criterion(
        _crit(ft="pdl1", val="", neg=True, span="regardless of PD-L1"),
        {}, None)["verdict"] == "match"
    assert evaluate_criterion(
        _crit(ft="age", op=">=", val="18"), {"age": 66},
        None)["verdict"] == "match"
    assert evaluate_criterion(
        _crit(ft="prior_treatment", op="RECEIVED", val="x"),
        {}, None)["verdict"] == "not_evaluable"


def test_egfr_wildtype_durvalumab_recs_mismatch_egfr_positive_case():
    """The LAURA/PACIFIC population split, derived from criteria alone."""
    kg = load_default()
    for rid in ("REC_EU_000506", "REC_EU_000797"):
        out = evaluate_rec(kg.recs_by_id[rid], CASE_FACTS, "IIIB")
        assert out["verdict"] == "population_mismatch", rid
        assert out["confident_mismatches"] >= 1


def test_eligibility_annotation_ranks_but_never_hides_unverified():
    """A confident-mismatch entry is re-ranked behind consistent ones but
    stays in the results, labelled — deterministic mini-store probe."""
    from nsclc_agent.knowledge.guideline_kg import GuidelineKG

    def rec(rec_id, question, crit):
        return {"id": rec_id, "gv": "GV", "jur": ["CN"], "dir": "recommend",
                "prio": "", "so": "", "eo": "", "sn": "", "en": "",
                "gs": "", "gsrc": "", "sk": "", "tier": "", "topic": "t",
                "intent": "t", "line": "", "q": question, "txt": "",
                "norm": question, "page": 1, "sec": "", "psg": [],
                "cluster": "", "conf": 0.9, "crit": crit, "acts": [],
                "ev": [], "rk": {}}

    mini = GuidelineKG({
        "gvs": {"GV": {"label": "Mini", "region": "CN", "version": "1"}},
        "recs": [
            # Higher text score, but a confident population mismatch.
            rec("R_MM", "alpha beta", [
                _crit(ft="gene_variant", gene="EGFR", op="HAS_VARIANT",
                      span="EGFR wild-type")]),
            # Lower text score, consistent.
            rec("R_OK", "alpha", [
                _crit(ft="gene_variant", gene="EGFR", op="HAS_VARIANT",
                      span="EGFR mutation")]),
        ],
    })
    plain = mini.search("alpha beta")
    assert [h["rec_id"] for h in plain] == ["R_MM", "R_OK"]  # score order
    hits = mini.search("alpha beta", case_facts=CASE_FACTS)
    assert [h["rec_id"] for h in hits] == ["R_OK", "R_MM"]  # re-ranked
    assert hits[1]["eligibility"]["verdict"] == "population_mismatch"


def test_eligibility_block_is_dose_scrubbed():
    kg = load_default()
    # Recs whose criterion spans carry dose text exist (the rk leak class);
    # served eligibility must still be clean.
    hits = kg.search("radiotherapy dose", topic="radiotherapy",
                     case_facts=CASE_FACTS, case_stage="IIIB", limit=10)
    blob = json.dumps([h.get("eligibility") for h in hits],
                      ensure_ascii=False, default=str)
    assert not DOSE_RE.search(blob)


# ----------------------------------------------------------- curation core

def test_verify_upgrades_revoke_downgrades(curated):
    kg, ledger = curated
    rid = "REC_EU_000505"
    append_curation(ledger, kg=kg, rec_id=rid, status="clinician_verified",
                    reviewer="Dr. Test (肿瘤内科)")
    kg = reload_default()
    served = kg.get(rid)
    assert served["curation_status"] == "clinician_verified"
    assert served["curation"]["reviewer"] == "Dr. Test (肿瘤内科)"
    assert kg.level_for([served]) == EvidenceLevel.GUIDELINE.value
    # Last entry wins: revoke by appending.
    append_curation(ledger, kg=kg, rec_id=rid, status="llm_extracted",
                    reviewer="Dr. Test (肿瘤内科)")
    kg = reload_default()
    assert kg.get(rid)["curation_status"] == "llm_extracted"
    assert len(load_curation(ledger)) == 1  # one rec, latest state


def test_content_change_voids_a_review(curated):
    kg, ledger = curated
    rid = "REC_EU_000505"
    append_curation(ledger, kg=kg, rec_id=rid, status="clinician_verified",
                    reviewer="Dr. Test")
    kg = reload_default()
    rec = kg.recs_by_id[rid]
    rec["norm"] = "CHANGED " + rec["norm"]
    status, entry, void = kg.curation_state(rec)
    assert void and status == "llm_extracted"
    assert kg.get(rid)["curation"]["void"]
    assert "re-review" in kg.get(rid)["curation"]["void_reason"]


def test_rejected_entries_hidden_from_search_visible_in_get(curated):
    kg, ledger = curated
    rid = "REC_EU_000505"
    append_curation(ledger, kg=ledger and kg, rec_id=rid, status="rejected",
                    reviewer="Dr. Test", notes="推荐文本与原文不符")
    kg = reload_default()
    hits = kg.search("durvalumab consolidation", stage="IIIB", limit=25)
    assert rid not in {h["rec_id"] for h in hits}
    served = kg.get(rid)  # the audit trail stays reachable
    assert served["curation_status"] == "rejected"
    assert served["curation"]["notes"] == "推荐文本与原文不符"


def test_ledger_validation_and_corruption(curated, tmp_path):
    kg, ledger = curated
    with pytest.raises(ValueError, match="named reviewer"):
        append_curation(ledger, kg=kg, rec_id="REC_EU_000505",
                        status="clinician_verified", reviewer="  ")
    with pytest.raises(ValueError, match="notes"):
        append_curation(ledger, kg=kg, rec_id="REC_EU_000505",
                        status="rejected", reviewer="Dr. T")
    with pytest.raises(ValueError, match="unknown curation status"):
        append_curation(ledger, kg=kg, rec_id="REC_EU_000505",
                        status="blessed", reviewer="Dr. T")
    with pytest.raises(ValueError, match="no recommendation"):
        append_curation(ledger, kg=kg, rec_id="REC_NOPE",
                        status="clinician_verified", reviewer="Dr. T")
    # Interior corruption is loud; a torn final line is tolerated.
    bad = tmp_path / "bad.jsonl"
    bad.write_text('not json\n{"rec_id": "REC_EU_000505"}\n',
                   encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt curation ledger"):
        load_curation(bad)
    torn = tmp_path / "torn.jsonl"
    entry = json.dumps({"rec_id": "REC_EU_000505", "status": "rejected",
                        "content_hash": "x"})
    torn.write_text(entry + '\n{"rec_id": "REC_', encoding="utf-8")
    assert load_curation(torn)["REC_EU_000505"]["status"] == "rejected"


# ------------------------------------------------------ end-to-end effects

def test_verified_row_is_releasable_in_a_run(curated):
    kg, ledger = curated
    append_curation(ledger, kg=kg, rec_id="REC_EU_000505",
                    status="clinician_verified", reviewer="Dr. Test")
    reload_default()
    state = NSCLCRunner().run_case(CASE)
    rows = {eid: e for eid, e in state.evidence.items()
            if e.source == "guideline_lookup"}
    levels = {e.level for e in rows.values()}
    assert EvidenceLevel.GUIDELINE.value in levels     # verified partition
    assert EvidenceLevel.KG_EXTRACTED.value in levels  # extracted partition
    verified_eids = {eid for eid, e in rows.items()
                     if e.level == EvidenceLevel.GUIDELINE.value}
    plan = state.outputs["treatment_plan"]
    assert verified_eids <= set(plan["citations"])
    statuses = {h["curation_status"]
                for h in state.outputs["guideline_context"]["supporting"]}
    assert "clinician_verified" in statuses


def test_verified_population_mismatch_excluded_from_context(curated):
    kg, ledger = curated
    # REC_EU_000506: EGFR-WT durvalumab consolidation — a confident
    # population mismatch for this EGFR-positive case. Verify it: its
    # criteria become trusted, and it must drop out of the case context.
    append_curation(ledger, kg=kg, rec_id="REC_EU_000506",
                    status="clinician_verified", reviewer="Dr. Test")
    reload_default()
    state = NSCLCRunner().run_case(CASE)
    context = state.outputs["guideline_context"]
    served_ids = {h["rec_id"] for h in
                  (context.get("supporting") or [])
                  + (context.get("cautions") or [])}
    assert "REC_EU_000506" in context.get("excluded_verified_mismatch", [])
    assert "REC_EU_000506" not in served_ids


def test_verified_matching_caution_raises_advisory_flag(curated):
    kg, ledger = curated
    # REC_EU_000435: avoid consolidation durvalumab after cCRT in EGFR+
    # unresectable stage III — consistent with this case. Verified, it
    # must surface as a visible flag; advisory only, release unaffected.
    append_curation(ledger, kg=kg, rec_id="REC_EU_000435",
                    status="clinician_verified", reviewer="Dr. Test")
    reload_default()
    state = NSCLCRunner().run_case(CASE)
    assert any(f.startswith("KG_VERIFIED_CAUTION[REC_EU_000435]")
               for f in state.flags)
    assert state.release_status == "treatment_recommendation"
    blob = " ".join(state.flags)
    assert not DOSE_RE.search(blob)


def test_unverified_context_stays_non_releasable(curated):
    """With an empty ledger nothing is releasable — the baseline holds."""
    reload_default()
    state = NSCLCRunner().run_case(CASE)
    levels = {e.level for e in state.evidence.values()
              if e.source == "guideline_lookup"}
    assert levels == {EvidenceLevel.KG_EXTRACTED.value}
    assert not any(f.startswith("KG_VERIFIED_CAUTION") for f in state.flags)


# ---------------------------------------------------------------------- CLI

def test_cli_review_workflow(curated, capsys):
    from nsclc_agent.cli import main

    kg, ledger = curated
    assert main(["kg-review", "--queue", "--limit", "5"]) == 0
    out = capsys.readouterr().out
    assert "REC_" in out

    assert main(["kg-review", "REC_EU_000505"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["rec_id"] == "REC_EU_000505" and shown["source_passages"]

    assert main(["kg-review", "REC_EU_000505", "--verify",
                 "--reviewer", "Dr. CLI (肿瘤内科)"]) == 0
    entry = json.loads(capsys.readouterr().out)
    assert entry["status"] == "clinician_verified"
    assert entry["content_hash"] == rec_content_hash(
        load_default().recs_by_id["REC_EU_000505"])

    assert main(["kg-review", "--status"]) == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["curation"]["clinician_verified"] == 1

    # Refusals exit non-zero and record nothing.
    assert main(["kg-review", "REC_EU_000796", "--reject",
                 "--reviewer", "Dr. CLI"]) == 2  # notes required
    capsys.readouterr()
    assert main(["kg-review"]) == 2
    capsys.readouterr()


def test_cli_kg_facts_annotation(curated, capsys):
    from nsclc_agent.cli import main

    rc = main(["kg", "durvalumab", "--stage", "IIIB",
               "--facts", json.dumps(CASE_FACTS)])
    assert rc == 0
    hits = json.loads(capsys.readouterr().out)
    assert all("eligibility" in h for h in hits)
