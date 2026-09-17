"""Eval suite smoke + CLI surface."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nsclc_agent.cli import main  # noqa: E402
from nsclc_agent.eval.run_eval import run_eval  # noqa: E402


def test_golden_eval_all_pass():
    report = run_eval()
    assert report["summary"]["all_passed"], report["summary"]["failed_ids"]
    assert report["summary"]["total"] >= 14


def test_cli_stage(capsys):
    assert main(["stage", "T2b", "N2b", "M0"]) == 0
    out = capsys.readouterr().out
    assert "IIIB" in out and "upstaged" in out


def test_cli_stage_refusal(capsys):
    assert main(["stage", "T2a", "N2", "M0"]) == 1
    assert "N2a" in capsys.readouterr().err


def test_cli_selftest(capsys):
    assert main(["selftest"]) == 0
    assert "passed" in capsys.readouterr().out


def test_cli_modules(capsys):
    assert main(["modules"]) == 0
    out = capsys.readouterr().out
    assert "stage3b" in out and "sha256" in out


def test_cli_screen_emergency(capsys):
    assert main(["screen", "突然大咯血不止"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["emergency"]


def test_cli_run_offline(capsys):
    code = main([
        "run", "--t", "T4", "--n", "N3", "--m", "M0",
        "--presentation",
        "Squamous, biopsy-confirmed N3, encompassable, ECOG 1. "
        "PET-CT/brain MRI negative. No hemoptysis, no weakness, no fever.",
        "--question", "Definitive management?",
        "--facts", json.dumps({
            "histologic_category": "squamous",
            "driver_mutations": {"egfr": "negative", "alk": "negative"},
            "pd_l1": {"tps": 15}, "ecog_ps": 1}),
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["staging"]["stage_group"] == "IIIC"
    assert payload["release_status"] == "treatment_recommendation"


def test_cli_run_without_structured_facts_defers_systemic(capsys):
    """Free-text 'EGFR negative' is not a structured fact — the plan defers."""
    code = main([
        "run", "--t", "T4", "--n", "N3", "--m", "M0",
        "--presentation",
        "Adenocarcinoma, EGFR/ALK negative per outside note. No hemoptysis, "
        "no weakness, no fever.",
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["release_status"] == "needs_more_information"
    assert payload["treatment_plan"]["intent"] == "workup"


def test_cli_axes(capsys):
    assert main(["axes", "--tier", "STAGING"]) == 0
    out = capsys.readouterr().out
    assert "m_resolution" in out and "EBUS" in out
