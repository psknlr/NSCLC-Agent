"""Tests for the argparse front-end — offline, via the mock provider.

The CLI had no coverage at all, which is how a front-end drifts from the
library behind it. These run ``main()`` in-process and assert on exit codes and
captured output.
"""

import json
from pathlib import Path

import pytest

from nsclc_agent.cli import main

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "cases"


def run(capsys, *argv):
    """Invoke the CLI and return (exit_code, stdout, stderr)."""
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --- stage / route ---------------------------------------------------------

def test_stage_prints_group_and_module(capsys):
    code, out, _ = run(capsys, "stage", "T2b", "N2b", "M0")
    assert code == 0
    assert "Stage IIIB" in out
    assert "module: stage3b" in out
    assert "upstaged" in out


def test_stage_json_output_is_machine_readable(capsys):
    code, out, _ = run(capsys, "stage", "T1a", "N0", "M0", "--json")
    assert code == 0
    data = json.loads(out)
    assert data["stage_group"] == "IA1"
    assert data["module"]["key"] == "stage1"


def test_stage_rejects_ambiguous_input(capsys):
    code, _, err = run(capsys, "stage", "T2", "N2", "M0")
    assert code == 1
    assert "Ambiguous" in err


def test_stage_rejects_bare_t1(capsys):
    code, _, err = run(capsys, "stage", "T1", "N0", "M0")
    assert code == 1
    assert "T1a" in err


def test_route_accepts_a_freeform_label(capsys):
    code, out, _ = run(capsys, "route", "stage 3b")
    assert code == 0
    assert json.loads(out)["module_key"] == "stage3b"


def test_route_refuses_an_ambiguous_family(capsys):
    code, out, _ = run(capsys, "route", "IV")
    assert code == 0
    data = json.loads(out)
    assert data["available"] is False
    assert "ambiguous" in data["note"].lower()


# --- modules / providers / slots -------------------------------------------

def test_modules_lists_every_module(capsys):
    code, out, _ = run(capsys, "modules")
    assert code == 0
    for key in ("stage1", "stage2", "stage3a", "stage3b", "stage3c",
                "stage4a", "stage4b"):
        assert key in out


def test_providers_reports_the_builtin_default(capsys):
    code, out, _ = run(capsys, "providers")
    assert code == 0
    assert "mock" in out
    assert "Default provider: mock" in out


def test_slots_explains_why_each_question_is_asked(capsys):
    code, out, _ = run(capsys, "slots", "--stage-group", "IIIB", "--lang", "en")
    assert code == 0
    assert "why :" in out
    assert "t_category" in out and "resectability" in out


def test_slots_reweights_by_stage(capsys):
    _, stage1_out, _ = run(capsys, "slots", "--stage-group", "IA1",
                           "--lang", "en")
    _, stage4_out, _ = run(capsys, "slots", "--stage-group", "IVB",
                           "--lang", "en")
    # PD-L1 is not decision-relevant in stage I but is in stage IV.
    assert "· pd_l1" in stage1_out
    assert "  pd_l1" in stage4_out


# --- selftest --------------------------------------------------------------

def test_selftest_passes(capsys):
    code, out, _ = run(capsys, "selftest")
    assert code == 0
    assert "34/34 passed" in out


# --- run -------------------------------------------------------------------

def test_run_from_flags(capsys):
    code, out, _ = run(capsys, "run", "--t", "T2b", "--n", "N2b", "--m", "M0",
                       "--question", "plan?")
    assert code == 0
    assert "Stage: IIIB" in out
    assert "Module: (none)" not in out


def test_run_reports_the_evidence_state(capsys):
    code, out, _ = run(capsys, "run", "--t", "T2b", "--n", "N2b", "--m", "M0")
    assert code == 0
    assert "Evidence: NOT retrieved" in out


def test_run_flags_an_assumed_m0(capsys):
    code, out, _ = run(capsys, "run", "--t", "T2b", "--n", "N2b")
    assert code == 0
    assert "M_ASSUMED_M0" in out


def test_run_dry_run_makes_no_model_call(capsys):
    code, out, _ = run(capsys, "run", "--t", "T4", "--n", "N3", "--m", "M0",
                       "--dry-run")
    assert code == 0
    assert "DRY_RUN" in out
    assert "Provider: (none)" in out


def test_run_from_case_file(capsys):
    case = EXAMPLES / "stage3b_unresectable_egfr.json"
    code, out, _ = run(capsys, "run", "--case", str(case), "--json")
    assert code == 0
    data = json.loads(out)
    assert data["staging"]["stage_group"] == "IIIB"
    assert data["disclaimer"]


def test_run_returns_nonzero_on_an_unstageable_case(capsys):
    code, _, _ = run(capsys, "run", "--presentation", "no staging info")
    assert code == 1


# --- batch -----------------------------------------------------------------

def test_batch_writes_one_result_per_case(capsys, tmp_path):
    out_dir = tmp_path / "out"
    code, out, _ = run(capsys, "batch", str(EXAMPLES), "-o", str(out_dir))
    assert code == 0
    written = sorted(p.name for p in out_dir.glob("*.result.json"))
    assert len(written) == len(list(EXAMPLES.glob("*.json")))
    data = json.loads((out_dir / written[0]).read_text())
    assert "staging" in data and "disclaimer" in data


def test_batch_on_an_empty_directory_errors(capsys, tmp_path):
    code, _, err = run(capsys, "batch", str(tmp_path))
    assert code == 1
    assert "No .json case files" in err


# --- consult ---------------------------------------------------------------

def test_consult_asks_the_blocking_questions_first(capsys):
    code, out, _ = run(capsys, "consult", "--no-interactive", "--ask-only",
                       "--lang", "en", "--presentation", "68F, LUL mass")
    assert code == 0
    assert "T category" in out
    assert "why" in out.lower()


def test_consult_scripted_reaches_a_recommendation(capsys):
    code, out, _ = run(
        capsys, "consult", "--no-interactive", "--lang", "en", "--dry-run",
        "--presentation", "68F LUL mass", "--question", "pathway?",
        "--answers",
        "Adenocarcinoma, T2b, multi-station mediastinal nodes N2b, "
        "PET-CT and brain MRI show no distant metastasis.",
        "ECOG 1, EGFR negative, ALK negative, PD-L1 TPS 40%.",
        "MDT says unresectable.",
    )
    assert code == 0
    assert "Stage: IIIB" in out
    assert "Consultation: 3 round(s)" in out


def test_consult_does_not_reask_what_the_opening_stated(capsys):
    code, out, _ = run(capsys, "consult", "--no-interactive", "--ask-only",
                       "--lang", "en",
                       "--presentation", "Adenocarcinoma cT2bN2bM0, ECOG 1")
    assert code == 0
    assert "T category" not in out


def test_consult_refuses_to_recommend_without_staging(capsys):
    code, _, err = run(capsys, "consult", "--no-interactive", "--lang", "en",
                       "--presentation", "68F, cough", "--max-rounds", "1",
                       "--answers", "I don't know")
    assert code == 1
    assert "Cannot produce a recommendation" in err


def test_consult_reports_what_it_never_learned(capsys):
    code, out, _ = run(
        capsys, "consult", "--no-interactive", "--lang", "en", "--dry-run",
        "--max-rounds", "1", "--presentation", "Adenocarcinoma cT2bN2bM0",
        "--answers", "not sure",
    )
    assert code == 0
    assert "still unknown" in out
    assert "decision-relevant gap" in out


def test_consult_session_round_trips_through_a_file(capsys, tmp_path):
    path = tmp_path / "s.json"
    run(capsys, "consult", "--no-interactive", "--ask-only", "--lang", "en",
        "--session", str(path), "--presentation", "Adenocarcinoma cT2bN2bM0")
    saved = json.loads(path.read_text())
    assert saved["known"]["t_category"] == "T2b"
    # Resuming reads the file back rather than starting over.
    code, out, _ = run(capsys, "consult", "--no-interactive", "--ask-only",
                       "--lang", "en", "--session", str(path))
    assert code == 0
    assert "T category" not in out


def test_consult_seeded_from_a_case_file(capsys):
    case = EXAMPLES / "stage3b_unresectable_egfr.json"
    code, out, _ = run(capsys, "consult", "--no-interactive", "--ask-only",
                       "--lang", "en", "--case", str(case))
    assert code == 0
    assert "T category" not in out


# --- misc ------------------------------------------------------------------

def test_missing_config_file_exits_two(capsys):
    with pytest.raises(SystemExit) as exc:
        run(capsys, "providers", "-c", "/nonexistent/config.yaml")
    assert exc.value.code == 2


def test_no_subcommand_is_an_error(capsys):
    with pytest.raises(SystemExit):
        main([])


# --- example directories keep their contracts ------------------------------

CONSULTS = Path(__file__).resolve().parent.parent / "examples" / "consults"


def test_consult_starters_are_deliberately_unstageable(capsys):
    """examples/consults/ holds inputs for `consult`, not for `run`."""
    for case in CONSULTS.glob("*.json"):
        code, _, _ = run(capsys, "run", "--case", str(case))
        assert code == 1, f"{case.name} should not be runnable as-is"


def test_a_consult_starter_reaches_a_stage_through_the_interview(capsys):
    case = CONSULTS / "incomplete_referral.json"
    code, out, _ = run(
        capsys, "consult", "--no-interactive", "--lang", "en", "--dry-run",
        "--case", str(case), "--answers",
        "Adenocarcinoma on repeat biopsy. 4.5 cm RLL mass, no pleural "
        "invasion, so T2b.",
        "EBUS sampled 4R and 7 — both positive, so multi-station N2b. "
        "PET-CT and brain MRI negative.",
        "ECOG 1, EGFR negative, ALK negative, PD-L1 TPS 60%. "
        "MDT says unresectable.",
    )
    assert code == 0
    assert "Stage: IIIB" in out
    assert "Module: stage3b" in out


def test_consult_accepts_a_case_with_only_a_stage_label(capsys, tmp_path):
    case = tmp_path / "label_only.json"
    case.write_text(json.dumps({
        "id": "LABEL-ONLY", "stage_group": "IIIB",
        "presentation": "Stage IIIB on the referral letter; TNM not recorded.",
        "question": "pathway?",
    }))
    code, out, _ = run(capsys, "consult", "--no-interactive", "--lang", "en",
                       "--dry-run", "--case", str(case))
    assert code == 0
    assert "Stage: IIIB" in out
    assert "Module: stage3b" in out


def test_a_resumed_session_keeps_its_language(capsys, tmp_path):
    path = tmp_path / "s.json"
    run(capsys, "consult", "--no-interactive", "--ask-only", "--lang", "en",
        "--session", str(path), "--presentation", "68F LUL mass")
    # Resume without --lang: the flag defaults to zh, the session must not.
    code, out, _ = run(capsys, "consult", "--no-interactive", "--ask-only",
                       "--session", str(path))
    assert code == 0
    assert "↳ why:" in out
    assert "为什么问" not in out
