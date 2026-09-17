"""The guideline knowledge graph: served contained.

Pinned here:

* the shipped store loads and answers filtered, deterministic queries;
* every served string is dose-scrubbed — including exactly the extracted
  texts that DO carry dose numerics in the raw store;
* machine-extracted content is graded ``kg_llm_extracted`` and that grade is
  non-releasable; a (future) clinician-verified result upgrades to GUIDELINE;
* stage matching parses roman-numeral families (IIIB matches III, never II);
* the rule-mode TreatmentAgent attaches KG context OUTSIDE the plan prose —
  a quoted ESMO sentence about durvalumab after concurrent CRT must not trip
  NO_CONCURRENT_DURVALUMAB (confirmed false-block during integration);
* negative knowledge is served as cautions and never blocks anything.
"""

from __future__ import annotations

import json

import pytest

from nsclc_agent import Case, NSCLCRunner
from nsclc_agent.knowledge.guideline_kg import (
    GuidelineKG,
    _stage_keys,
    load_default,
)
from nsclc_agent.safety.rules import DOSE_RE
from nsclc_agent.state import NON_RELEASABLE_LEVELS, EvidenceLevel


@pytest.fixture(scope="module")
def kg() -> GuidelineKG:
    store = load_default()
    assert store is not None and store.available
    return store


def _egfr_iiib_case() -> Case:
    return Case(
        t="T4", n="N2b", m="M0",
        presentation="不可切除多站N2腺癌，PET-CT+脑MRI确认M0。"
                     "无咯血、无下肢无力、无发热。",
        facts={"driver_mutations": {"egfr": "L858R", "alk": "negative"},
               "histologic_category": "adenocarcinoma",
               "resectability_category": "UNRESECTABLE", "ecog_ps": 1})


# ------------------------------------------------------------------- store

def test_store_loads_with_six_guidelines(kg):
    info = kg.describe()
    assert info["recommendations"] == 2960
    assert len(info["guidelines"]) == 6
    assert info["curation_status"] == "llm_extracted"
    assert "unverified" in info["warning"]


def test_search_is_filtered_and_relevant(kg):
    hits = kg.search("osimertinib adjuvant", gene="EGFR", stage="IIB")
    assert hits
    assert any("simertinib" in h["recommendation"] for h in hits)
    for hit in hits:
        assert hit["curation_status"] == "llm_extracted"
        assert hit["provenance"]["page"]
        genes = hit["population"].get("genes") or []
        assert not genes or "EGFR" in {g.upper() for g in genes}


def test_stage_families_parse_as_roman_numerals():
    assert "III" in _stage_keys("IIIB")
    assert "II" not in _stage_keys("IIIB")
    assert "I" not in _stage_keys("IIIB")
    assert {"IA", "I", "early_stage"} <= _stage_keys("IA3")
    assert {"IV", "metastatic", "M1c"} <= _stage_keys("IVB")
    assert "M1c" not in _stage_keys("IVA")


def test_negative_direction_alias_serves_cautions(kg):
    hits = kg.search("", stage="IIIB", direction="negative", limit=6)
    assert hits
    assert all(h["direction"] in ("do_not_recommend", "avoid",
                                  "contraindicated") for h in hits)


def test_cluster_view_exposes_machine_comparison(kg):
    cluster_id = next(iter(kg.clusters_by_id))
    cluster = kg.cluster(cluster_id)
    assert cluster["agreement"] in ("full", "partial", "disagreement")
    assert cluster["members"]
    assert "未经复核" in cluster["note"]
    assert not DOSE_RE.search(json.dumps(cluster, ensure_ascii=False,
                                         default=str))


# ------------------------------------------------------------ dose boundary

def test_every_dose_bearing_rec_is_served_scrubbed(kg):
    """Exactly the raw records that DO carry dose numerics come out clean —
    including their source passages."""
    dose_recs = [
        rec for rec in kg.recs
        if DOSE_RE.search(json.dumps(
            [rec.get("txt"), rec.get("norm"), rec.get("q"), rec.get("acts")],
            ensure_ascii=False))
    ]
    assert dose_recs, "store unexpectedly carries no dose-bearing extractions"
    for rec in dose_recs:
        served = json.dumps(kg.get(rec["id"]), ensure_ascii=False, default=str)
        assert not DOSE_RE.search(served), rec["id"]


def test_no_served_field_of_any_rec_carries_a_dose(kg):
    """The airtight sweep: every served string of all 2,960 recommendations,
    whatever field it hides in (the retrieval keys leaked once)."""
    blob = json.dumps([kg._serve(rec) for rec in kg.recs],
                      ensure_ascii=False, default=str)
    match = DOSE_RE.search(blob)
    assert not match, blob[max(0, match.start() - 120):match.end() + 40]


# ------------------------------------------------------------------ grading

def test_llm_extracted_grade_is_non_releasable(kg):
    hits = kg.search("durvalumab", stage="IIIB")
    level = kg.level_for(hits)
    assert level == EvidenceLevel.KG_EXTRACTED.value
    assert level in NON_RELEASABLE_LEVELS


def test_clinician_verified_hits_upgrade_to_guideline(kg):
    verified = [{"curation_status": "clinician_verified"}]
    assert kg.level_for(verified) == EvidenceLevel.GUIDELINE.value
    mixed = verified + [{"curation_status": "llm_extracted"}]
    assert kg.level_for(mixed) == EvidenceLevel.KG_EXTRACTED.value


# ------------------------------------------------- rule-mode plan enrichment

def test_rule_mode_plan_carries_kg_context_outside_the_plan():
    state = NSCLCRunner().run_case(_egfr_iiib_case())
    plan = state.outputs["treatment_plan"]
    context = state.outputs["guideline_context"]
    # Quoted prose lives OUTSIDE the plan; the plan carries ids only.
    assert "guideline_context" not in plan
    assert plan["guideline_refs"]["supporting"]
    assert context["supporting"] and context["cautions"]
    assert context["curation_status"] == "llm_extracted"
    # KG evidence rows are in the ledger at their non-releasable grade and
    # cited by the plan — alongside the releasable trial anchors.
    kg_eids = {eid for eid, e in state.evidence.items()
               if e.level == EvidenceLevel.KG_EXTRACTED.value}
    assert kg_eids and kg_eids <= set(plan["citations"])
    trial_eids = {eid for eid, e in state.evidence.items()
                  if e.level == EvidenceLevel.TRIAL.value}
    assert trial_eids & set(plan["citations"])


def test_quoted_esmo_durvalumab_text_does_not_false_block():
    """Regression: 'durvalumab … after concurrent CRT' quoted from ESMO must
    not read as the plan proposing concurrent durvalumab."""
    state = NSCLCRunner().run_case(_egfr_iiib_case())
    assert state.release_status == "treatment_recommendation"
    assert not any("NO_CONCURRENT_DURVALUMAB" in issue
                   for issue in state.outputs["safety_audit"]["issues"])
    # The quoted context really does contain the trigger words — the test
    # is only meaningful while it does.
    blob = json.dumps(state.outputs["guideline_context"], ensure_ascii=False)
    assert "durvalumab" in blob.lower() and "concurrent" in blob.lower()


def test_cautions_are_advisory_never_blocking():
    state = NSCLCRunner().run_case(_egfr_iiib_case())
    cautions = state.outputs["guideline_context"]["cautions"]
    assert cautions
    for caution in cautions:
        assert not any(caution["rec_id"] in issue
                       for issue in state.safety_issues)


def test_oncologist_view_and_reply_surface_context_patient_does_not():
    from nsclc_agent.conversation import compose_reply
    from nsclc_agent.render import render

    state = NSCLCRunner().run_case(_egfr_iiib_case())
    assert render(state, "oncologist")["guideline_context"]
    assert "guideline_context" not in render(state, "patient")
    assert "指南KG" in compose_reply(state, role="oncologist")
    assert "指南KG" not in compose_reply(state, role="patient")


def test_kg_context_on_llm_path_and_stable_across_reused_turns():
    """The context attaches on every plan path — and a conversation that
    reuses its plan does not grow the citation list turn over turn."""
    from nsclc_agent.conversation import ConsultationSession
    from nsclc_agent.llm.mock import MockLLMClient

    sess = ConsultationSession(llm=MockLLMClient(), role="oncologist")
    r1 = sess.turn("68岁女性，不可切除IIIB期肺腺癌，cT4N2bM0，"
                   "EGFR L858R阳性，ECOG 1，无咯血无头痛无骨痛。")
    assert r1.state.outputs["guideline_context"]["supporting"]
    r2 = sess.turn("跨地区指南有分歧吗？")
    r3 = sess.turn("好的，继续。")
    assert r2.plan_reused and r3.plan_reused
    assert r3.state.outputs["guideline_context"]["supporting"]
    n2 = len(r2.state.outputs["treatment_plan"]["citations"])
    n3 = len(r3.state.outputs["treatment_plan"]["citations"])
    assert n3 == n2


# --------------------------------------------------------------------- tool

def test_tool_errors_are_recoverable(kg):
    from nsclc_agent.skills import SkillRegistry
    from nsclc_agent.tools.base import CapabilityBroker, ToolHealth
    from nsclc_agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    broker = CapabilityBroker(
        "oncologist", "routine", skill_registry=SkillRegistry.discover(),
        active_skill="nsclc.treatment", health=ToolHealth())
    missing = registry.call(broker, "guideline_lookup", rec_id="REC_NOPE")
    assert not missing.ok and missing.recoverable
    empty = registry.call(broker, "guideline_lookup")
    assert not empty.ok and empty.recoverable
    one = registry.call(broker, "guideline_lookup",
                        rec_id=kg.recs[0]["id"])
    assert one.ok and one.data["hits"][0]["source_passages"]


# ---------------------------------------------------------------------- CLI

def test_cli_kg_commands(kg, capsys):
    from nsclc_agent.cli import main

    assert main(["kg", "--info"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["recommendations"] == 2960

    assert main(["kg", "osimertinib", "--gene", "EGFR",
                 "--stage", "IIB"]) == 0
    hits = json.loads(capsys.readouterr().out)
    assert hits and hits[0]["curation_status"] == "llm_extracted"

    assert main(["kg", "--show", hits[0]["rec_id"]]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["rec_id"] == hits[0]["rec_id"]

    assert main(["kg"]) == 2  # no query, no filter
