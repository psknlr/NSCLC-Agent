"""Browser bridge (``nsclc_agent.webapi``) — the GitHub Pages web app runs the
package unchanged under Pyodide and reaches every capability through this
JSON-in/JSON-out surface, so its contract is tested natively here."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from nsclc_agent import webapi

ROOT = Path(__file__).resolve().parent.parent


def call(name, **payload):
    out = json.loads(webapi.call(name, json.dumps(payload)))
    return out


def ok(name, **payload):
    out = call(name, **payload)
    assert out["ok"], out.get("error")
    return out["result"]


@pytest.fixture(autouse=True)
def fresh_state():
    webapi.configure_llm("none")
    webapi._STATE.update({"session": None, "last_state": None,
                          "last_role": "patient"})
    yield
    webapi.configure_llm("none")


def test_bridge_never_throws():
    assert call("no_such_api") == {"ok": False,
                                   "error": "unknown API 'no_such_api'"}
    bad = json.loads(webapi.call("stage", "[1, 2]"))
    assert not bad["ok"] and "JSON object" in bad["error"]
    unexpected = call("stage", t="T1a", n="N0", m="M0", bogus=1)
    assert not unexpected["ok"] and "TypeError" in unexpected["error"]
    garbage = json.loads(webapi.call("info", "{not json"))
    assert not garbage["ok"]


def test_info_counts_and_rules():
    info = ok("info")
    counts = info["counts"]
    assert counts["rules"] >= 20 and counts["regimens"] >= 40
    assert counts["audit_probes"] > 0 and counts["golden_cases"] > counts["audit_probes"]
    assert "OPTION_DRUG_UNBOUND" in info["rule_ids"]
    assert info["llm"]["mode"] == "governed"
    assert set(info["providers"]) >= {"poe", "minimax", "azure", "mock", "none"}


def test_list_and_null_results_pass_through_the_envelope():
    assert isinstance(ok("examples"), list)
    assert isinstance(ok("golden_cases"), list)
    assert ok("kg_get", rec_id="NO-SUCH-REC") is None


def test_catalog_never_exposes_component_dosing():
    """The catalog is the library's summary surface: names, settings and
    anchors. Component dose/schedule strings stay in the dose channel."""
    from nsclc_agent.knowledge.regimens import REGIMENS

    catalog = ok("catalog")
    assert catalog["regimens"] and catalog["trials"]
    for entry in catalog["regimens"].values():
        assert set(entry) == {"name", "setting", "trial_ids", "contains_ici",
                              "label_note"}
    blob = json.dumps(catalog, ensure_ascii=False)
    for regimen in REGIMENS:
        for component in regimen.components:
            if len(component.dose) > 6:
                assert component.dose not in blob, regimen.regimen_id


def test_stage_and_refusal():
    good = ok("stage", t="T2b", n="N2b", m="M0")
    assert good["refused"] is False and good["stage_group"] == "IIIB"
    assert good["module"]["module_key"]
    refused = ok("stage", t="T2b", n="N2", m="M0")
    assert refused["refused"] is True and refused["reason"]


@pytest.mark.parametrize("example", webapi.EXAMPLES, ids=lambda e: e["id"])
def test_every_example_runs_governed(example):
    result = ok("run_case", **example["case"], role="oncologist")
    assert result["release_status"] in {
        "treatment_recommendation", "needs_more_information",
        "emergency_action_plan"}
    assert "oncologist" in result["views"]
    if example["id"] == "emergency":
        assert result["release_status"] == "emergency_action_plan"
    if example["id"] == "renal":
        assert result["release_status"] == "needs_more_information"
    if example["id"] == "iv_exon20":
        rids = result["views"]["oncologist"]["treatment_plan"]["regimen_ids"]
        assert not any("osimertinib" in r for r in rids)


def test_patient_role_payload_is_the_patient_view_only():
    case = webapi.EXAMPLES[0]["case"]
    result = ok("run_case", **case, role="patient")
    assert set(result["views"]) == {"patient"}
    for key in ("violations", "claims", "evidence", "run_meta", "tasks"):
        assert key not in result
    exported = ok("export_last")
    assert set(exported) == {"release_status", "views"}
    assert set(exported["views"]) == {"patient"}


def test_unknown_role_is_patient():
    result = ok("run_case", **webapi.EXAMPLES[0]["case"], role="admin")
    assert result["role"] == "patient" and set(result["views"]) == {"patient"}


def test_oncologist_export_is_the_full_record():
    ok("run_case", **webapi.EXAMPLES[0]["case"], role="oncologist")
    record = ok("export_last")
    assert record["release_status"] and "evidence" in record


def test_dose_channel_needs_explicit_opt_in():
    case = webapi.EXAMPLES[0]["case"]
    plain = ok("run_case", **case, role="oncologist")
    assert plain["release_status"] != "draft_for_tumor_board"
    assert not plain["views"]["oncologist"].get("dose_plan")
    patient = ok("run_case", **case, role="patient", allow_dose_planning=True)
    assert patient["release_status"] != "draft_for_tumor_board"


def test_configure_llm_validation_and_modes():
    for provider, extra in (("poe", {}), ("minimax", {}),
                            ("azure", {"api_key": "k"})):
        out = call("configure_llm", provider=provider, **extra)
        assert not out["ok"] and "LLMError" in out["error"]
    assert not call("configure_llm", provider="openrouter")["ok"]

    mock = ok("configure_llm", provider="mock")
    assert mock["llm"]["available"] and mock["mode"] == "agent"
    assert ok("ping_llm")["ok"] is True

    poe = ok("configure_llm", provider="poe", api_key="x")
    assert poe["llm"]["model"] == "claude-sonnet-4.5"
    assert poe["vision"]["model"] == "gemini-3.1-pro"
    minimax = ok("configure_llm", provider="minimax", api_key="x",
                 region="global")
    assert minimax["llm"]["model"] == "MiniMax-M3"
    assert minimax["vision"] == {"provider": "none"}
    assert webapi._STATE["llm"].base_url == "https://api.minimax.io/v1"
    ok("configure_llm", provider="minimax", api_key="x")
    assert webapi._STATE["llm"].base_url == "https://api.minimaxi.com/v1"

    none = ok("configure_llm", provider="none")
    assert none["mode"] == "governed"
    assert ok("ping_llm") == {"ok": False, "error": "no provider configured"}


def test_configure_llm_drops_the_bound_session():
    ok("chat_new", role="oncologist")
    assert webapi._STATE["session"] is not None
    ok("configure_llm", provider="mock")
    assert webapi._STATE["session"] is None


def test_mock_panel_run_is_governed():
    ok("configure_llm", provider="mock")
    result = ok("run_case", **webapi.EXAMPLES[1]["case"], role="oncologist",
                enable_panel=True)
    assert result["release_status"]
    assert "safety_rule_engine" in result["checks_run"] or result["checks_run"]


def test_chat_turn_whatif_export_import_roundtrip():
    ok("chat_new", role="oncologist")
    turn = ok("chat_turn", message=(
        "65岁男性，肺腺癌，cT2aN1M0，ECOG 1，EGFR阴性，ALK阴性，PD-L1 60%，"
        "脑MRI阴性，无咯血无骨痛无头痛。"))
    assert turn["reply"] and turn["turns"] == 1 and not turn["what_if"]
    assert all(not k.startswith("_") for k in turn["session_facts"])

    whatif = ok("chat_whatif", description="PD-L1 10%",
                facts={"pd_l1": {"tps": 10}})
    assert whatif["what_if"]
    # the scenario never writes back: facts are exactly the pre-what-if ones
    assert whatif["session_facts"] == turn["session_facts"]
    assert whatif["session_facts"]["pd_l1"]["tps"] == 60

    exported = ok("chat_export")
    kinds = [t.get("kind") for t in exported["transcript"]]
    assert kinds.count("what_if") == 1
    resumed = ok("chat_import", data=exported, role="patient")
    assert resumed["role"] == "patient"
    assert resumed["facts"]["pd_l1"]["tps"] == 60


def test_chat_import_takes_no_authority_from_the_file():
    ok("chat_new", role="oncologist", allow_dose_planning=True)
    ok("chat_turn", message="cT2aN0M1b 肺腺癌，脑MRI阴性。")
    exported = ok("chat_export")
    exported["role"] = "oncologist"
    exported["allow_dose_planning"] = True
    resumed = ok("chat_import", data=exported)  # call defaults: oncologist
    session = webapi._STATE["session"]
    assert session.allow_dose_planning is False
    ok("chat_import", data=exported, role="not-a-role")
    assert webapi._STATE["session"].role == "patient"
    assert resumed["turns"] == 1


def test_patient_chat_turn_has_no_clinician_payload():
    ok("chat_new", role="patient")
    turn = ok("chat_turn", message="cT2aN0M1b 肺腺癌，脑MRI阴性，EGFR阴性。")
    assert set(turn["views"]) == {"patient"} and "violations" not in turn
    assert set(ok("export_last")["views"]) == {"patient"}


def test_kg_surface():
    info = ok("kg_info")
    assert info["recommendations"] > 1000
    hits = ok("kg_search", query="osimertinib", limit=5)["hits"]
    assert 0 < len(hits) <= 5
    rec = ok("kg_get", rec_id=hits[0]["rec_id"])
    assert rec and rec["rec_id"] == hits[0]["rec_id"]
    assert len(ok("kg_search", query="osimertinib", limit=999)["hits"]) <= 40


def test_audit_plan_matches_the_golden_probes():
    probes = [c for c in ok("golden_cases") if c["kind"] == "audit"]
    assert probes
    for probe in probes:
        result = ok("audit_plan", staging=probe["audit_staging"],
                    facts=probe["audit_facts"], plan=probe["audit_plan"])
        fired = {(v["rule_id"], v["severity"]) for v in result["violations"]}
        ids = {rule for rule, _ in fired}
        expect = probe["expect"] or {}
        for want in expect.get("violations_required") or []:
            if want.get("severity"):
                assert (want["rule_id"], want["severity"]) in fired, probe["id"]
            else:
                assert want["rule_id"] in ids, probe["id"]
        for banned in expect.get("violations_forbidden") or []:
            assert banned not in ids, probe["id"]


def test_eval_through_the_bridge_is_all_green():
    report = ok("run_eval")
    summary = report["summary"]
    assert summary["all_passed"] and summary["passed"] == summary["total"]
    assert summary["unsafe_release_rate"].startswith("0/")


def test_package_imports_without_ssl_or_sqlite(monkeypatch):
    """Pyodide ships without ``ssl``/``sqlite3``: nothing may import them
    at module level."""
    import subprocess

    code = (
        "import sys\n"
        "for m in ('ssl', '_ssl', 'sqlite3', '_sqlite3', 'lzma', '_lzma',"
        " 'multiprocessing'):\n"
        "    sys.modules[m] = None\n"
        "import json\n"
        "from nsclc_agent import webapi\n"
        "out = json.loads(webapi.call('run_case', json.dumps("
        "dict(webapi.EXAMPLES[0]['case'], role='oncologist'))))\n"
        "assert out['ok'], out\n"
        "print(out['result']['release_status'])\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "treatment_recommendation"


def test_browser_flags_force_serial_scheduling(monkeypatch):
    import nsclc_agent.platform_caps as caps
    from nsclc_agent import NSCLCRunner

    monkeypatch.setattr(caps, "THREADS_AVAILABLE", False)
    assert NSCLCRunner().parallel_tasks is False
    monkeypatch.setattr(caps, "THREADS_AVAILABLE", True)
    assert NSCLCRunner().parallel_tasks is True


def test_site_build(tmp_path):
    import importlib.util
    import zipfile

    spec = importlib.util.spec_from_file_location("web_build",
                                                  ROOT / "web" / "build.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    out = tmp_path / "_site"
    meta = build.build(out)
    from nsclc_agent import __version__

    assert meta["version"] == __version__
    for name in ("index.html", "worker.js", "assets/app.js", "assets/app.css",
                 "favicon.svg", "build.json", ".nojekyll", "nsclc_agent.zip"):
        assert (out / name).exists(), name
    assert not (out / "build.py").exists()
    assert json.loads((out / "build.json").read_text())["hash"] == meta["hash"]
    with zipfile.ZipFile(out / "nsclc_agent.zip") as archive:
        names = archive.namelist()
    assert "nsclc_agent/webapi.py" in names
    assert "nsclc_agent/eval/golden/cases.json" in names
    assert not any(n.endswith(".pyc") or "__pycache__" in n for n in names)
    assert any(n.startswith("nsclc_agent/knowledge/") and n.endswith(".jsonl")
               or n.endswith(".json") for n in names)
    # deterministic: same sources → same hash (cache-busting stays stable)
    assert build.build(tmp_path / "again")["hash"] == meta["hash"]
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "IMPF-AI" in html and "研发" in html


def test_pyodide_version_pinned_consistently():
    import re

    worker = (ROOT / "web" / "worker.js").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(
        encoding="utf-8")
    in_worker = re.search(r'PYODIDE_VERSION = "([\d.]+)"', worker).group(1)
    in_ci = re.search(r'PYODIDE_VERSION: "([\d.]+)"', workflow).group(1)
    assert in_worker == in_ci
