"""Red-team benchmark: integrity of the frozen held-out split, refusal of
vacuous probes, and a smoke run of every track."""

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nsclc_agent.cli import main  # noqa: E402
from nsclc_agent.eval import redteam  # noqa: E402


def test_test_split_matches_frozen_digest():
    """test.json is held out: any edit after the freeze shows up here."""
    assert redteam.test_set_intact()
    frozen = redteam.manifest()
    assert frozen["test"]["probes"] == len(redteam.load_split("test")["probes"])


@pytest.mark.parametrize("split", redteam.SPLITS)
def test_splits_validate(split):
    payload = redteam.load_split(split)
    assert redteam.validate_probes(payload) == []
    kinds = {p["kind"] for p in payload["probes"]}
    tracks = {p["track"] for p in payload["probes"]}
    assert kinds == set(redteam.KINDS) and tracks == set(redteam.TRACKS)


def test_dev_and_test_do_not_share_probes():
    dev = redteam.load_split("dev")["probes"]
    test = redteam.load_split("test")["probes"]
    assert not {p["id"] for p in dev} & {p["id"] for p in test}

    def body(p):
        return json.dumps({k: v for k, v in p.items() if k not in ("id", "title")},
                          sort_keys=True, ensure_ascii=False)

    assert not {body(p) for p in dev} & {body(p) for p in test}


def _probe(split, pid):
    return next(p for p in redteam.load_split(split)["probes"] if p["id"] == pid)


@pytest.mark.parametrize("mutate,needle", [
    (lambda p: p["expect"].update(findings_anyy=["X"]), "unknown expect key"),
    (lambda p: p["expect"].update(findings_any=[]), "findings_any is empty"),
    (lambda p: p.update(kind="maybe"), "attack or control"),
    (lambda p: p.pop("consult"), "need a consult object"),
])
def test_validation_refuses_vacuous_consult_probes(mutate, needle):
    probe = copy.deepcopy(_probe("dev", "d-consult-01"))
    mutate(probe)
    problems = redteam.validate_probes({"format": redteam.FORMAT, "probes": [probe]})
    assert any(needle in p for p in problems), problems


def test_validation_refuses_unknown_genes_and_empty_requirements():
    probe = copy.deepcopy(_probe("dev", "d-chat-01"))
    probe["expect"]["drivers_not_positive"] = ["brafv600e"]
    probe["expect"]["released_requires"] = {}
    problems = redteam.validate_probes({"format": redteam.FORMAT, "probes": [probe]})
    assert any("unknown genes" in p for p in problems)
    assert any("released_requires" in p for p in problems)


def test_polish_transform_that_matches_nothing_is_an_error_not_a_pass():
    probe = copy.deepcopy(_probe("dev", "d-polish-04"))
    probe["transform"] = [{"op": "drop_lines", "contains": "no such line anywhere"}]
    result = redteam.run_probe(probe)
    assert not result["passed"] and result["error"]


@pytest.mark.parametrize("pid", [
    "d-consult-10", "d-inj-19", "d-audit-10", "d-polish-11", "d-gov-05", "d-chat-07",
])
def test_every_track_runs_offline(pid):
    result = redteam.run_probe(_probe("dev", pid))
    assert result["error"] is None, result
    assert result["passed"], result["failures"]


def test_baseline_records_the_frozen_code():
    baseline = json.loads((redteam.REDTEAM_DIR / "baseline.json").read_text(encoding="utf-8"))
    frozen = redteam.manifest()["frozen_against"]
    assert baseline["code"] == frozen
    for split in redteam.SPLITS:
        ids = {p["id"] for p in redteam.load_split(split)["probes"]}
        assert set(baseline["splits"][split]["passed"]) == ids


def test_dev_split_stays_green():
    """Regression floor: every dev probe passes (v1.5.0: 101/101, from a
    39/101 baseline). The held-out split is run to report, never here."""
    entry = redteam.run_redteam("dev")["splits"]["dev"]
    failed = [r["id"] for r in entry["results"] if not r["passed"]]
    assert failed == [], failed


def test_cli_redteam_dev(capsys, tmp_path):
    out = tmp_path / "rt.json"
    assert main(["redteam", "--split", "dev", "--out", str(out)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["dev"]["total"] == len(redteam.load_split("dev")["probes"])
    assert json.loads(out.read_text(encoding="utf-8"))["splits"]["dev"]["results"]


def test_tampered_test_split_is_not_reported_as_held_out(tmp_path):
    for name in ("dev.json", "test.json", "MANIFEST.json"):
        (tmp_path / name).write_bytes((redteam.REDTEAM_DIR / name).read_bytes())
    payload = json.loads((tmp_path / "test.json").read_text(encoding="utf-8"))
    payload["probes"] = payload["probes"][:3]
    (tmp_path / "test.json").write_text(json.dumps(payload, ensure_ascii=False),
                                        encoding="utf-8")
    report = redteam.run_redteam("test", directory=tmp_path)
    assert report["splits"]["test"]["test_set_intact"] is False
    assert "NOT a held-out score" in report["splits"]["test"]["warning"]
