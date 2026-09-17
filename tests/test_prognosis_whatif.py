"""Prognosis context + conversational what-if scenarios.

Pinned here:

* the survival table serves published stage-cohort figures with
  provenance, approximation and edition-migration caveats — and refuses to
  pad groups the publications never reported; nothing anywhere computes an
  individual prediction;
* modifiers are directional and trial-anchored (numbers stay in the
  registry, cited); the anchors land in the ledger as releasable evidence;
* role scoping: the oncologist reply/view carries the figures, the patient
  view never does — a patient asking about survival gets a supportive,
  numberless pointer;
* what_if runs the full audited pipeline on a hypothetical variant while
  leaving EVERY piece of session memory untouched, never confirming the
  report-proposed guard, and never opening the dose channel; an emergency
  hypothesis escalates inside the scenario without latching the session.
"""

from __future__ import annotations

import re

import pytest

from nsclc_agent import Case, NSCLCRunner
from nsclc_agent.conversation import ConsultationSession
from nsclc_agent.knowledge.prognosis import prognosis_for
from nsclc_agent.llm.mock import MockLLMClient
from nsclc_agent.render import render
from nsclc_agent.state import NON_RELEASABLE_LEVELS, EvidenceLevel

SCREEN_NEG = "无咯血、无下肢无力、无发热。"
T1_MSG = (f"62岁女性，不可切除IIIB期肺腺癌，cT4N2bM0，EGFR L858R阳性，"
          f"ECOG 1，PD-L1 60%。{SCREEN_NEG}")

IIIB_CASE = Case(
    t="T4", n="N2b", m="M0",
    presentation=f"不可切除多站N2腺癌，PET-CT+脑MRI确认M0。{SCREEN_NEG}",
    facts={"driver_mutations": {"egfr": "L858R", "alk": "negative"},
           "histologic_category": "adenocarcinoma",
           "resectability_category": "UNRESECTABLE", "ecog_ps": 1})


# ------------------------------------------------------------------- table

def test_table_serves_cohort_figures_with_provenance():
    out = prognosis_for("IIIB", "c", {})
    assert out["five_year_os_percent_approx"] == 26
    assert out["classification_basis"] == "clinical"
    assert "Goldstraw" in out["cohort"]
    assert out["individual_prediction"] is False
    assert any("不是个体预测" in c for c in out["caveats"])
    # Pathological cohorts differ systematically.
    assert prognosis_for("IIIB", "p", {})["five_year_os_percent_approx"] == 24
    assert prognosis_for("IIIB", "yp", {})["classification_basis"] \
        == "pathological"
    # IVB's published clinical figure is 0 — served, not suppressed.
    assert prognosis_for("IVB", "c", {})["five_year_os_percent_approx"] == 0


def test_table_refuses_to_pad_unreported_groups():
    out = prognosis_for("0", "c", {})
    assert out["five_year_os_percent_approx"] is None
    assert any("不作填补" in c for c in out["caveats"])
    assert prognosis_for(None, "c", {}) is None
    assert prognosis_for("", "c", {}) is None
    # Pathological stage IV has no resected-cohort row: clinical fallback,
    # said out loud.
    out = prognosis_for("IVA", "p", {})
    assert out["five_year_os_percent_approx"] == 10
    assert any("临床分期队列" in c for c in out["caveats"])


def test_migration_caveat_is_disclosed():
    out = prognosis_for("IIB", "c", {}, migration_notes=["T1N2a was IIIA"])
    assert out["edition_migration"] == ["T1N2a was IIIA"]
    assert any("迁移" in c for c in out["caveats"])


def test_modifiers_are_directional_and_case_derived():
    facts = {"ecog_ps": 2, "weight_loss": "5kg in 2 months",
             "driver_mutations": {"egfr": "ex19del"}}
    mods = prognosis_for("IVB", "c", facts)["modifiers"]
    by_factor = {m["factor"]: m for m in mods}
    assert any("ECOG" in f for f in by_factor)
    assert all(m["direction"] in ("adverse", "favorable_with_treatment")
               for m in mods)
    egfr = next(m for m in mods if "EGFR" in m["factor"])
    assert egfr["anchor_trials"] == ["FLAURA"]
    # PD-L1 modifier is suppressed when a driver is present…
    facts["pd_l1"] = {"tps": 80}
    mods = prognosis_for("IVB", "c", facts)["modifiers"]
    assert not any("PD-L1" in m["factor"] for m in mods)
    # …and offered when there is none.
    mods = prognosis_for(
        "IVB", "c",
        {"pd_l1": {"tps": 80}, "driver_mutations": {"egfr": "negative"}},
    )["modifiers"]
    assert any(m.get("anchor_trials") == ["KEYNOTE024"] for m in mods)
    # Unresectable III + EGFR → LAURA, not PACIFIC.
    mods = prognosis_for(
        "IIIB", "c",
        {"resectability_category": "UNRESECTABLE",
         "driver_mutations": {"egfr": "L858R"}})["modifiers"]
    anchors = [t for m in mods for t in m.get("anchor_trials") or []]
    assert "LAURA" in anchors and "PACIFIC" not in anchors


# ------------------------------------------------------------ run wiring

def test_run_attaches_prognosis_with_releasable_anchored_evidence():
    state = NSCLCRunner().run_case(IIIB_CASE)
    prognosis = state.outputs["prognosis"]
    assert prognosis["five_year_os_percent_approx"] == 26
    cohort_rows = [e for e in state.evidence.values()
                   if e.source == "prognosis_table"]
    assert cohort_rows
    assert cohort_rows[0].level == EvidenceLevel.COHORT.value
    assert cohort_rows[0].level not in NON_RELEASABLE_LEVELS
    # The LAURA modifier is anchored as registry evidence and referenced.
    laura = next(m for m in prognosis["modifiers"]
                 if "LAURA" in (m.get("anchor_trials") or []))
    assert laura["evidence_id"] in state.evidence
    assert state.evidence[laura["evidence_id"]].level \
        == EvidenceLevel.TRIAL.value
    claim = next(c for c in state.claims if c.kind == "prognosis_context")
    assert set(claim.evidence_ids) <= set(state.evidence)
    # Prognosis prose lives outside the plan; release unaffected.
    assert "prognosis" not in state.outputs["treatment_plan"]
    assert state.release_status == "treatment_recommendation"


def test_role_scoping_of_prognosis():
    state = NSCLCRunner().run_case(IIIB_CASE)
    assert render(state, "oncologist")["prognosis"][
        "five_year_os_percent_approx"] == 26
    assert "prognosis" not in render(state, "patient")
    assert render(state, "researcher")["aggregates"][
        "cohort_5y_os_percent"] == 26


# ----------------------------------------------------------- conversation

def test_oncologist_reply_carries_figures_patient_gets_support():
    onc = ConsultationSession(llm=MockLLMClient(), role="oncologist")
    reply = onc.turn(T1_MSG).reply
    assert "5年总生存约 26%" in reply
    assert "非个体预测" in reply

    pat = ConsultationSession(llm=MockLLMClient(), role="patient")
    pat.turn(T1_MSG)
    ask = pat.turn("我还能活多久？")
    assert "与您的主治团队" in ask.reply
    assert not re.search(r"\d+\s*%", ask.reply)  # no survival percentages
    assert "prognosis" not in ask.view


# ---------------------------------------------------------------- what-if

def _session() -> ConsultationSession:
    sess = ConsultationSession(llm=MockLLMClient(), role="oncologist")
    sess.turn(T1_MSG)
    return sess


def test_what_if_compares_without_touching_session_memory():
    sess = _session()
    facts_before = dict(sess.facts)
    narrative_before = list(sess.narrative)
    cache_before = sess._plan_cache
    asked_before = list(sess.interview_loop.asked_questions)
    turns_before = len(sess.turns)

    result = sess.what_if("如果其实是可切除的 cT2aN1M0 呢")
    assert "IIIB → IIB" in result.reply
    assert "约26% → 约53%" in result.reply
    assert "不写入会诊记录" in result.reply
    assert result.state.staging["stage_group"] == "IIB"

    assert sess.facts == facts_before
    assert sess.narrative == narrative_before
    assert sess._plan_cache is cache_before
    assert sess.interview_loop.asked_questions == asked_before
    assert len(sess.turns) == turns_before
    assert sess.transcript[-1]["kind"] == "what_if"
    # The untouched cache still serves the next real turn.
    assert sess.turn("好的，继续按当前方案讨论。").plan_reused


def test_what_if_never_opens_dose_and_never_confirms_guard():
    sess = _session()
    sess.facts["_report_proposed"] = ["pd_l1.tps"]
    result = sess.what_if("复核确认 PD-L1", facts={"pd_l1": {"tps": 60}})
    assert result.state.facts.get("_report_proposed") == ["pd_l1.tps"]
    assert sess.facts["_report_proposed"] == ["pd_l1.tps"]
    assert any(n.startswith("WHAT_IF_GUARD_KEPT") for n in result.notes)
    assert not any(n.startswith("PROPOSED_FACT_CONFIRMED")
                   for n in result.notes)
    assert not result.state.allow_dose_planning
    assert "dose_plan" not in result.state.outputs


def test_what_if_emergency_escalates_in_scenario_only():
    sess = _session()
    # "如果大咯血" is hypothetical phrasing — the screen's hypothetical
    # suppression correctly leaves it routine (same as a chat turn asking
    # "what if"). A scenario STATING the event escalates inside the run.
    hypothetical = sess.what_if("如果突然大咯血呢")
    assert hypothetical.state.release_status != "emergency_action_plan"
    result = sess.what_if("患者突然大咯血不止")
    assert result.state.release_status == "emergency_action_plan"
    assert "对比不适用" in result.reply
    # The hypothesis is not in the session narrative: the next real turn
    # does not latch the emergency pathway.
    follow = sess.turn("目前情况稳定，继续讨论方案。")
    assert follow.state.release_status != "emergency_action_plan"


def test_what_if_requires_a_baseline():
    sess = ConsultationSession(llm=MockLLMClient(), role="oncologist")
    with pytest.raises(ValueError, match="baseline"):
        sess.what_if("如果是IV期呢")


def test_cli_chat_whatif_scripted(capsys):
    import json

    from nsclc_agent.cli import main

    rc = main(["chat", "--llm-provider", "mock", "--role", "oncologist",
               "--json", "-m", T1_MSG,
               "-m", "/whatif 如果其实是可切除的 cT2aN1M0 呢"])
    assert rc == 0
    lines = [json.loads(line) for line in
             capsys.readouterr().out.strip().splitlines()]
    assert lines[-1]["kind"] == "what_if"
    assert "IIIB → IIB" in lines[-1]["reply"]
