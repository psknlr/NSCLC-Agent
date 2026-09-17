"""End-to-end runner behavior: offline rule mode, mock agentic mode,
emergency short-circuit, dose channel gating, checkpoints, replay."""

import json
from pathlib import Path

import pytest

from nsclc_agent import Case, NSCLCRunner, render, resume_run
from nsclc_agent.journal import Journal
from nsclc_agent.llm.mock import MockLLMClient

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "cases"

SCREEN_NEG = ("No hemoptysis, no leg weakness, no fever on treatment, "
              "no facial swelling.")


def _case_iiib_egfr():
    return Case(
        t="T4", n="N2b", m="M0",
        presentation=f"Unresectable multi-station N2b adenocarcinoma. PET-CT "
                     f"and brain MRI confirm M0. EGFR L858R. ECOG 1. {SCREEN_NEG}",
        question="Definitive management?",
        facts={"driver_mutations": {"egfr": "L858R", "alk": "negative"},
               "histologic_category": "adenocarcinoma",
               "resectability_category": "UNRESECTABLE", "ecog_ps": 1},
    )


def test_rule_mode_iiib_egfr():
    state = NSCLCRunner().run_case(_case_iiib_egfr())
    assert state.staging["stage_group"] == "IIIB"
    assert state.routing["module_key"] == "stage3b"
    plan = state.outputs["treatment_plan"]
    assert "osimertinib_consolidation" in plan["regimen_ids"]
    assert "durva_consolidation" not in plan["regimen_ids"]
    assert plan["citations"], "rule plan must anchor its regimens in the ledger"
    assert state.release_status == "treatment_recommendation"
    assert not state.safety_issues


def test_missing_m_never_defaults_to_curative():
    """The v0.1 headline defect, closed."""
    state = NSCLCRunner().run_case(Case(
        t="T2a", n="N0",
        presentation=f"No metastatic workup yet. {SCREEN_NEG}"))
    assert state.release_status == "needs_staging_workup"
    assert any("STAGING_ERROR" in f for f in state.flags)
    workup = json.dumps(state.outputs["workup_plan"], ensure_ascii=False)
    assert "brain MRI" in workup


def test_edition_mismatch_refused():
    state = NSCLCRunner().run_case(Case(
        t="T1a", n="N1", m="M0", staging_system="AJCC8",
        presentation=f"Old record staged under the 8th edition. {SCREEN_NEG}"))
    assert any("STAGING_ERROR" in f and "9th edition" in f for f in state.flags)
    assert state.release_status == "needs_staging_workup"


def test_stage_mismatch_flagged_computed_wins():
    state = NSCLCRunner().run_case(Case(
        t="T2b", n="N2b", m="M0", stage_group="IIIA",
        presentation=f"Adenocarcinoma. EGFR negative, ALK negative. {SCREEN_NEG}",
        facts={"driver_mutations": {"egfr": "negative", "alk": "negative"},
               "histologic_category": "adenocarcinoma"}))
    assert state.staging["stage_group"] == "IIIB"
    assert any("STAGE_MISMATCH" in f for f in state.flags)


def test_emergency_short_circuits_everything():
    state = NSCLCRunner().run_case(Case(
        presentation="肺癌病史，突然大咯血不止", question="怎么办"))
    assert state.risk_mode == "emergency"
    assert state.release_status == "emergency_action_plan"
    assert "treatment_plan" not in state.outputs
    assert "safety_audit" in state.outputs  # the critic still ran
    view = render(state, "patient")
    assert view["emergency_plan"]["risk_judgement"] == "oncologic_emergency"


def test_mock_agentic_pipeline():
    runner = NSCLCRunner(llm=MockLLMClient(), vision_llm=MockLLMClient(vision=True))
    state = runner.run_case(Case(
        t="T1c", n="N0", m="M0",
        presentation=f"2.8 cm RUL adenocarcinoma, PET-CT/brain MRI negative. "
                     f"{SCREEN_NEG}",
        facts={"histologic_category": "adenocarcinoma",
               "driver_mutations": {"egfr": "negative", "alk": "negative"}}))
    assert state.planner_mode == "llm"
    assert state.staging["stage_group"] == "IA3"
    assert state.outputs["treatment_plan"]["origin"] == "llm_tool_loop"
    assert state.outputs["treatment_plan"]["citations"]


def test_dose_planning_requires_role_and_opt_in():
    # Patient role: skipped even with opt-in.
    state = NSCLCRunner().run_case(_case_iiib_egfr(), role="patient",
                                   allow_dose_planning=True)
    assert "dose_plan" not in state.outputs
    # Oncologist without opt-in: skipped.
    state = NSCLCRunner().run_case(_case_iiib_egfr(), role="oncologist")
    assert "dose_plan" not in state.outputs


def test_dose_planning_happy_path():
    state = NSCLCRunner().run_case(_case_iiib_egfr(), role="oncologist",
                                   allow_dose_planning=True)
    dose_plan = state.outputs["dose_plan"]
    expanded = {r["regimen_id"] for r in dose_plan["regimens"]}
    assert "osimertinib_consolidation" in expanded
    assert dose_plan["requires_tumor_board_approval"] is True
    assert state.release_status == "draft_for_tumor_board"


def test_dose_planning_blocked_when_red_flags_unanswered():
    case = _case_iiib_egfr()
    case.presentation = ("Unresectable multi-station N2b adenocarcinoma, "
                         "EGFR L858R, PET-CT and brain MRI confirm M0.")
    runner = NSCLCRunner()
    state = runner.run_case(case, role="oncologist", allow_dose_planning=True)
    # The emergency screen axes were never answered → blocked interview verdict
    # under a prescriptive run → the dose channel must not open.
    assert "dose_plan" not in state.outputs
    assert any("dose planning skipped" in w for w in state.warnings)


def test_patient_view_never_contains_doses():
    state = NSCLCRunner().run_case(_case_iiib_egfr(), role="oncologist",
                                   allow_dose_planning=True)
    view = render(state, "patient")
    blob = json.dumps(view, ensure_ascii=False)
    assert "80 mg" not in blob and "dose_plan" not in view


def test_checkpoint_and_resume(tmp_path):
    runner = NSCLCRunner(checkpoint_dir=tmp_path)
    state = runner.run_case(Case(
        t="T2b", n="N2b", m="M0",
        presentation=f"Unresectable IIIB, biomarkers pending. {SCREEN_NEG}",
        facts={"histologic_category": "adenocarcinoma",
               "driver_mutations": {"egfr": "not_tested", "alk": "not_tested"},
               "resectability_category": "UNRESECTABLE"}))
    assert state.outputs["treatment_plan"]["intent"] == "workup"
    latest = tmp_path / f"{state.run_id}.latest.json"
    assert latest.exists()
    # Resume with the biomarker answers: the plan should now commit.
    resumed = resume_run(latest, new_facts={
        "driver_mutations": {"egfr": "L858R", "alk": "negative"}})
    plan = resumed.outputs["treatment_plan"]
    assert plan["intent"] != "workup"
    assert "osimertinib_consolidation" in plan["regimen_ids"]


def test_journal_record_then_faithful_replay(tmp_path):
    path = tmp_path / "case.jsonl"
    recorder = NSCLCRunner(journal=Journal(path, mode="record"))
    original = recorder.run_case(_case_iiib_egfr())
    assert original.release_status == "treatment_recommendation"

    replayer = NSCLCRunner(journal=Journal.load(path))
    replayed = replayer.run_case(_case_iiib_egfr())
    assert replayed.release_status == original.release_status
    assert replayed.outputs["treatment_plan"]["regimen_ids"] == \
        original.outputs["treatment_plan"]["regimen_ids"]
    assert replayed.outputs["run_meta"]["journal"]["replayed"] > 0


def test_journal_replay_divergence_fails_closed(tmp_path):
    path = tmp_path / "case.jsonl"
    recorder = NSCLCRunner(journal=Journal(path, mode="record"))
    recorder.run_case(_case_iiib_egfr())

    # Replay with a *different* case: the calls diverge, and the run must
    # fail closed rather than impersonate a review of the original.
    different = _case_iiib_egfr()
    different.facts["driver_mutations"] = {"egfr": "negative", "alk": "negative"}
    replayer = NSCLCRunner(journal=Journal.load(path))
    replayed = replayer.run_case(different)
    assert replayed.release_status == "failed_closed"
    assert any("replay divergence" in i for i in replayed.safety_issues)


@pytest.mark.parametrize("case_file", sorted(EXAMPLES.glob("*.json")))
def test_example_cases_run_clean(case_file):
    case = Case.from_dict(json.loads(case_file.read_text(encoding="utf-8")))
    runner = NSCLCRunner(case_base_dir=case_file.resolve().parent)
    state = runner.run_case(case)
    assert state.release_status != "failed_closed", state.safety_issues
    assert state.routing.get("module_key")
