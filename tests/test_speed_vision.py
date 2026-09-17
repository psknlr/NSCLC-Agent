"""Regression suite for the consultation-speed + instant-vision upgrade.

Covers: the Treatment∥Panel parallel wave (determinism, remap, failure
containment, journal exclusion), Poe-Gemini vision auto-detection, report
reading with guarded fact seeding, and the CLI speed surfaces.
"""

import json
from pathlib import Path

import pytest

from nsclc_agent import Case, NSCLCRunner
from nsclc_agent.cli import main
from nsclc_agent.journal import Journal
from nsclc_agent.llm.base import LLMResponse, ToolCall
from nsclc_agent.llm.mock import MockLLMClient
from nsclc_agent.llm.providers import build_vision_client, describe_client
from nsclc_agent.perception.reports import ReportFindings, fold_report_facts

SCREEN_NEG = "No hemoptysis, no leg weakness, no fever on treatment."


def _panel_case() -> Case:
    return Case(
        t="T4", n="N2a", m="M0",
        presentation=f"T4N2a adenocarcinoma, resectability contested. "
                     f"PET-CT/brain MRI negative. {SCREEN_NEG}",
        facts={"driver_mutations": {"egfr": "negative", "alk": "negative"},
               "histologic_category": "adenocarcinoma", "pd_l1": {"tps": 40},
               "resectability_category": "RESECTABLE", "ecog_ps": 0})


def _ledger(state):
    return [(eid, e.source, e.level) for eid, e in sorted(state.evidence.items())]


# ------------------------------------------------------------- parallel wave

def test_parallel_wave_runs_and_is_ledger_identical_to_serial():
    ledgers, outputs = [], []
    for parallel in (True, False):
        runner = NSCLCRunner(llm=MockLLMClient(), parallel_tasks=parallel)
        state = runner.run_case(_panel_case(), enable_panel=True)
        assert state.release_status == "treatment_recommendation"
        assert state.outputs["run_meta"]["execution"] == (
            "parallel_wave" if parallel else "serial")
        assert state.outputs["panel"]["answered"] == 5
        ledgers.append(_ledger(state))
        outputs.append(state.outputs["treatment_plan"])
    assert ledgers[0] == ledgers[1]
    assert outputs[0]["regimen_ids"] == outputs[1]["regimen_ids"]


def test_parallel_wave_citations_remapped_to_real_ids():
    runner = NSCLCRunner(llm=MockLLMClient())
    state = runner.run_case(_panel_case(), enable_panel=True)
    blob = json.dumps(state.outputs, ensure_ascii=False)
    assert "__T" not in blob  # no wave temp ids leaked
    for citation in state.outputs["treatment_plan"].get("citations") or []:
        assert citation in state.evidence


def test_wave_repeated_runs_are_deterministic():
    ledgers = []
    for _ in range(3):
        state = NSCLCRunner(llm=MockLLMClient()).run_case(
            _panel_case(), enable_panel=True)
        ledgers.append(_ledger(state))
    assert ledgers[0] == ledgers[1] == ledgers[2]


def test_journaled_run_never_parallel(tmp_path):
    path = tmp_path / "j.jsonl"
    runner = NSCLCRunner(llm=MockLLMClient(),
                         journal=Journal(path, mode="record"))
    state = runner.run_case(_panel_case(), enable_panel=True)
    assert state.outputs["run_meta"]["execution"] == "serial"
    # And the recording replays faithfully.
    replayer = NSCLCRunner(journal=Journal.load(path))
    replayed = replayer.run_case(_panel_case(), enable_panel=True)
    assert replayed.release_status == state.release_status


def test_wave_member_crash_fails_closed():
    class CrashyPanelLLM(MockLLMClient):
        def chat(self, messages, **kwargs):
            system = messages[0].get("content") or ""
            if "panel" in str(system).lower() and "THORACIC" in str(system):
                raise RuntimeError("panel member transport down")
            return super().chat(messages, **kwargs)

    # A member crash degrades the panel (loudly), never the whole run — the
    # ToolLoop catches provider errors; a WAVE-level crash means an agent
    # itself raised. Simulate that by making the whole PanelAgent raise.
    runner = NSCLCRunner(llm=MockLLMClient())

    class Boom:
        skill_id = "nsclc.panel"

        def run(self, *a, **k):
            raise RuntimeError("agent exploded")

    runner.agents["PanelAgent"] = Boom()
    state = runner.run_case(_panel_case(), enable_panel=True)
    assert state.release_status == "failed_closed"
    assert any("wave" in issue for issue in state.safety_issues)


def test_serial_flag_and_no_panel_stay_serial():
    state = NSCLCRunner(llm=MockLLMClient()).run_case(_panel_case())
    assert state.outputs["run_meta"]["execution"] == "serial"


# --------------------------------------------------------- vision autoconfig

def test_vision_autodetects_poe_gemini(monkeypatch):
    monkeypatch.delenv("NSCLC_VISION_PROVIDER", raising=False)
    monkeypatch.setenv("POE_API_KEY", "test-key")
    client = build_vision_client()
    info = describe_client(client)
    assert info["auto_selected"] is True
    assert info["vision"] is True
    assert "Gemini" in info["model"]


def test_vision_explicit_provider_beats_autodetect(monkeypatch):
    monkeypatch.setenv("POE_API_KEY", "test-key")
    monkeypatch.setenv("NSCLC_VISION_PROVIDER", "mock")
    client = build_vision_client()
    assert client.name == "mock"


def test_vision_model_env_overrides_default(monkeypatch):
    monkeypatch.delenv("NSCLC_VISION_PROVIDER", raising=False)
    monkeypatch.setenv("POE_API_KEY", "test-key")
    monkeypatch.setenv("NSCLC_VISION_MODEL", "Gemini-3.1-Pro")
    assert build_vision_client().model == "Gemini-3.1-Pro"


def test_no_credentials_means_no_reader(monkeypatch):
    for var in ("NSCLC_VISION_PROVIDER", "POE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert build_vision_client() is None


# ----------------------------------------------------------- report reading

class ReportVision:
    name = "fake-gemini"
    model = "fake-gemini"
    available = True
    supports_vision = True

    def __init__(self, payload):
        self.payload = payload

    def chat(self, messages, **kwargs):
        system = messages[0]["content"]
        if "CLINICAL DOCUMENT EXTRACTION" in system:
            return LLMResponse(text=json.dumps(self.payload))
        from nsclc_agent.perception.imaging import mock_findings_payload

        return LLMResponse(text=mock_findings_payload())


REPORT_PAYLOAD = {
    "document_types": ["molecular", "pd_l1"],
    "histologic_category": "adenocarcinoma",
    "driver_mutations": {"egfr": "exon 19 deletion detected", "alk": "negative"},
    "pd_l1": {"tps": 55, "assay": "22C3"},
    "candidate_t": None, "candidate_n": "N2", "candidate_m": None,
    "specimen": "EBUS-TBNA", "report_dates": ["2026-08-01"],
    "key_findings": ["EGFR ex19del"], "uncertainties": [],
}


def _report_case(tmp_path, **fact_overrides) -> Case:
    img = tmp_path / "report.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    facts = {"histologic_category": "adenocarcinoma"}
    facts.update(fact_overrides)
    return Case(
        t="T2a", n="N2a", m="M1b",
        presentation=f"Single adrenal met, brain MRI negative. {SCREEN_NEG}",
        reports=[str(img)], facts=facts)


def test_report_seeds_missing_facts_and_accelerates_plan(tmp_path):
    runner = NSCLCRunner(vision_llm=ReportVision(REPORT_PAYLOAD))
    state = runner.run_case(_report_case(tmp_path))
    assert "driver_mutations.egfr" in state.facts["_report_proposed"]
    # The seeded EGFR result immediately drives the correct first-line plan.
    assert "osimertinib_first_line" in state.outputs["treatment_plan"]["regimen_ids"]
    assert any("REPORT_FACT_PROPOSED" in f for f in state.flags)
    # Bare "N2" from the document is vocabulary-rejected, not seeded.
    assert any("IMAGING_DESCRIPTOR_REJECTED[N]" in f for f in state.flags)
    assert state.facts["tnm"]["n"] == "N2a"  # case value untouched


def test_report_proposed_facts_block_dose_channel(tmp_path):
    runner = NSCLCRunner(vision_llm=ReportVision(REPORT_PAYLOAD))
    state = runner.run_case(_report_case(tmp_path), role="oncologist",
                            allow_dose_planning=True)
    assert "dose_plan" not in state.outputs
    dose_tasks = [t for t in state.tasks if t.agent == "DosePlanAgent"]
    assert dose_tasks and dose_tasks[0].status == "skipped_unconfirmed_facts"
    assert any("PLAN_RESTS_ON_REPORT_PROPOSED_FACTS" in i
               for i in state.outputs["safety_audit"]["issues"])


def test_report_never_overwrites_existing_facts(tmp_path):
    runner = NSCLCRunner(vision_llm=ReportVision(REPORT_PAYLOAD))
    state = runner.run_case(_report_case(
        tmp_path,
        driver_mutations={"egfr": "negative", "alk": "negative"}))
    # Case says EGFR negative; report says ex19del → discordance, no overwrite.
    assert state.facts["driver_mutations"]["egfr"] == "negative"
    assert any("REPORT_DISCORDANCE[EGFR]" in f for f in state.flags)
    assert "_report_proposed" not in state.facts or not any(
        p.startswith("driver_mutations.egfr")
        for p in state.facts["_report_proposed"])


def test_confirmed_facts_reopen_dose_channel(tmp_path):
    """The acceleration loop closes: confirm the report → resume → dose."""
    checkpoints = tmp_path / "ckpt"
    runner = NSCLCRunner(vision_llm=ReportVision(REPORT_PAYLOAD),
                         checkpoint_dir=checkpoints)
    state = runner.run_case(_report_case(tmp_path), role="oncologist",
                            allow_dose_planning=True)
    assert "dose_plan" not in state.outputs
    from nsclc_agent import resume_run

    latest = checkpoints / f"{state.run_id}.latest.json"
    resumed = resume_run(
        latest,
        new_facts={"driver_mutations": {"egfr": "Ex19del (confirmed)",
                                        "alk": "negative"},
                   "_report_proposed": []})
    assert "dose_plan" in resumed.outputs
    assert resumed.release_status == "draft_for_tumor_board"


def test_fold_report_facts_pure():
    facts = {"histologic_category": "squamous"}
    findings = ReportFindings(
        histologic_category="adenocarcinoma",
        driver_mutations={"egfr": "L858R detected"},
        pd_l1={"tps": 10})
    seeded, flags = fold_report_facts(facts, findings)
    assert "driver_mutations.egfr" in seeded
    assert any("REPORT_DISCORDANCE[histology]" in f for f in flags)
    assert facts["histologic_category"] == "squamous"  # not overwritten


# ------------------------------------------------------------------ CLI

def test_cli_read_command(tmp_path, capsys):
    scans = tmp_path / "scans"
    scans.mkdir()
    (scans / "b.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (scans / "a.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (scans / "notes.txt").write_text("not an image")
    code = main(["read", "--images", str(scans), "--vision-provider", "mock"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["imaging"]["read_by"]["images"] == 2  # dir expanded, txt skipped


def test_cli_read_requires_a_backend(monkeypatch, capsys):
    for var in ("NSCLC_VISION_PROVIDER", "POE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    code = main(["read", "--images", "x.png"])
    assert code == 2
    assert "POE_API_KEY" in capsys.readouterr().err


def test_cli_batch_parallel_jobs(tmp_path, capsys):
    examples = Path(__file__).resolve().parent.parent / "examples" / "cases"
    code = main(["batch", str(examples), "--jobs", "4",
                 "-o", str(tmp_path / "out")])
    assert code == 0
    out = capsys.readouterr().out
    # Output order is deterministic (input order) even with 4 workers.
    lines = [l.split(":")[0] for l in out.splitlines() if ".json:" in l]
    assert lines == sorted(lines)


def test_run_meta_reports_auto_vision(monkeypatch, tmp_path):
    monkeypatch.delenv("NSCLC_VISION_PROVIDER", raising=False)
    monkeypatch.setenv("POE_API_KEY", "test-key")
    vision = build_vision_client()
    runner = NSCLCRunner(vision_llm=vision)
    state = runner.run_case(Case(
        t="T1a", n="N0", m="M0",
        presentation=f"Small nodule. {SCREEN_NEG}",
        facts={"histologic_category": "adenocarcinoma",
               "driver_mutations": {"egfr": "negative", "alk": "negative"}}))
    assert state.outputs["run_meta"]["vision"].get("auto_selected") is True


# ------------------------------------------- adversarial-review regressions

def test_wave_temp_ids_never_reach_claim_text_or_warnings():
    """Temp ids leaked into claim text / warnings / trace summaries before."""
    state = NSCLCRunner(llm=MockLLMClient()).run_case(
        _panel_case(), enable_panel=True)
    blob = json.dumps(
        {"claims": [(c.text, c.evidence_ids) for c in state.claims],
         "warnings": state.warnings, "flags": state.flags,
         "traces": [(t.action, t.output_summary, t.evidence_ids)
                    for t in state.traces]},
        ensure_ascii=False)
    assert "__T" not in blob


def test_wave_panel_context_is_explicitly_independent():
    """The wave panel must not read the mid-wave treatment plan (race +
    anchor); the serial panel still sees the finished plan."""
    captured = {}

    class Capturing(MockLLMClient):
        def chat(self, messages, **kwargs):
            system = str(messages[0].get("content") or "")
            if "multidisciplinary panel" in system or "panel" in system.lower():
                for m in messages:
                    if m.get("role") == "user":
                        try:
                            payload = json.loads(m["content"])
                        except Exception:
                            continue
                        if "treatment_plan_so_far" in payload:
                            captured.setdefault("values", []).append(
                                payload["treatment_plan_so_far"])
            return super().chat(messages, **kwargs)

    NSCLCRunner(llm=Capturing()).run_case(_panel_case(), enable_panel=True)
    assert captured["values"], "panel members never saw a context payload"
    assert all(v is None for v in captured["values"])


def test_planner_rejects_duplicate_agents():
    from nsclc_agent.agents.planner import parse_plan, validate_plan
    from nsclc_agent.state import CaseRunState

    tasks, _ = parse_plan({"tasks": [
        {"task_id": "T1", "agent": "StagingAgent"},
        {"task_id": "T2", "agent": "TreatmentAgent", "depends_on": ["T1"]},
        {"task_id": "T3", "agent": "TreatmentAgent", "depends_on": ["T1"]},
    ]})
    ok, reason = validate_plan(tasks, CaseRunState(complaint="x"),
                               allow_dose_planning=False)
    assert not ok and "duplicate agent" in reason


def test_wave_crash_discards_partial_evidence_and_keeps_healthy_status():
    runner = NSCLCRunner(llm=MockLLMClient())

    class Boom:
        skill_id = "nsclc.panel"

        def run(self, state, *a, **k):
            state.add_evidence("model_reasoning", "boom", "partial before crash")
            raise RuntimeError("agent exploded")

    runner.agents["PanelAgent"] = Boom()
    state = runner.run_case(_panel_case(), enable_panel=True)
    assert state.release_status == "failed_closed"
    assert not any(e.source == "boom" for e in state.evidence.values())
    statuses = {t.agent: t.status for t in state.tasks}
    assert statuses["PanelAgent"] == "failed"
    assert statuses["TreatmentAgent"] == "ok"  # healthy partner keeps its own outcome


def test_run_case_never_mutates_the_callers_case(tmp_path):
    """The shallow-copy escape: seeded facts wrote through to the Case."""
    case = _report_case(tmp_path)
    before = json.dumps(case.facts, sort_keys=True, default=str)
    runner = NSCLCRunner(vision_llm=ReportVision(REPORT_PAYLOAD))
    state1 = runner.run_case(case, role="oncologist", allow_dose_planning=True)
    assert json.dumps(case.facts, sort_keys=True, default=str) == before
    # Second run of the SAME Case object: guards fully intact.
    state2 = NSCLCRunner(vision_llm=ReportVision(REPORT_PAYLOAD)).run_case(
        case, role="oncologist", allow_dose_planning=True)
    for state in (state1, state2):
        assert "dose_plan" not in state.outputs
        assert any(t.status == "skipped_unconfirmed_facts"
                   for t in state.tasks if t.agent == "DosePlanAgent")


def test_seeded_histology_alone_blocks_dose_channel(tmp_path):
    payload = dict(REPORT_PAYLOAD)
    payload["driver_mutations"] = {}
    payload["pd_l1"] = {}
    payload["candidate_n"] = None
    img = tmp_path / "hist.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    case = Case(t="T3", n="N2b", m="M1c2",
                presentation=f"Polymetastatic. {SCREEN_NEG}",
                reports=[str(img)],
                facts={"driver_mutations": {"egfr": "negative",
                                            "alk": "negative"},
                       "pd_l1": {"tps": 30}})  # histology missing → seeded
    state = NSCLCRunner(vision_llm=ReportVision(payload)).run_case(
        case, role="oncologist", allow_dose_planning=True)
    assert state.facts.get("_report_proposed") == ["histologic_category"]
    assert "dose_plan" not in state.outputs


def test_seeded_tnm_from_report_blocks_dose_channel(tmp_path):
    payload = dict(REPORT_PAYLOAD)
    payload["driver_mutations"] = {}
    payload["pd_l1"] = {}
    payload["candidate_n"] = None
    payload["candidate_m"] = "M1c2"
    img = tmp_path / "m.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    case = Case(t="T3", n="N2b", m=None,  # M missing → seeded from report
                presentation=f"Adenocarcinoma. {SCREEN_NEG}",
                reports=[str(img)],
                facts={"histologic_category": "adenocarcinoma",
                       "driver_mutations": {"egfr": "negative",
                                            "alk": "negative"},
                       "pd_l1": {"tps": 30}})
    state = NSCLCRunner(vision_llm=ReportVision(payload)).run_case(
        case, role="oncologist", allow_dose_planning=True)
    assert state.staging.get("stage_group") == "IVB"  # seeding accelerated staging
    assert "tnm.m" in (state.facts.get("_report_proposed") or [])
    assert "dose_plan" not in state.outputs  # but never dosing


def test_pd_l1_discordance_flagged():
    from nsclc_agent.perception.reports import fold_report_facts

    facts = {"pd_l1": {"tps": 5}}
    _, flags = fold_report_facts(
        facts, ReportFindings(pd_l1={"tps": 90}))
    assert facts["pd_l1"]["tps"] == 5
    assert any("REPORT_DISCORDANCE[pd_l1.tps]" in f for f in flags)


def test_histology_containment_is_not_discordance():
    from nsclc_agent.perception.reports import fold_report_facts

    facts = {"histologic_category": "lung adenocarcinoma"}
    _, flags = fold_report_facts(
        facts, ReportFindings(histologic_category="adenocarcinoma"))
    assert not any("REPORT_DISCORDANCE" in f for f in flags)


def test_internal_keys_stripped_from_case_files():
    """A case file smuggling `_report_proposed: "zz"` crashed the run."""
    case = Case.from_dict({
        "t": "T2a", "n": "N0", "m": "M0",
        "presentation": f"Small tumor. {SCREEN_NEG}",
        "_report_proposed": "zz",
        "histologic_category": "adenocarcinoma",
        "driver_mutations": {"egfr": "negative", "alk": "negative"}})
    state = NSCLCRunner().run_case(case)
    assert state.release_status != "failed_closed"
    assert state.facts.get("_report_proposed") in (None, [])


def test_vision_off_switch(monkeypatch):
    monkeypatch.setenv("POE_API_KEY", "test-key")
    monkeypatch.setenv("NSCLC_VISION_PROVIDER", "none")
    assert build_vision_client() is None
    monkeypatch.setenv("NSCLC_VISION_PROVIDER", "off")
    assert build_vision_client() is None


def test_pdf_refused_with_guidance(tmp_path):
    from nsclc_agent.perception.imaging import ImagingError, load_image_ref

    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    with pytest.raises(ImagingError, match="PNG/JPEG"):
        load_image_ref(str(pdf))


def test_case_file_report_directories_expand(tmp_path, capsys):
    scans = tmp_path / "docs"
    scans.mkdir()
    (scans / "p1.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (scans / "note.pdf").write_bytes(b"%PDF fake")
    case_file = tmp_path / "case.json"
    case_file.write_text(json.dumps({
        "t": "T2a", "n": "N0", "m": "M0",
        "presentation": f"x. {SCREEN_NEG}",
        "reports": ["docs"],
        "histologic_category": "adenocarcinoma",
        "driver_mutations": {"egfr": "negative", "alk": "negative"}}))
    code = main(["run", "--case", str(case_file),
                 "--vision-provider", "mock"])
    assert code == 0
    err = capsys.readouterr().err
    assert "note.pdf" in err  # skipped file is named, not silent


def test_cli_read_partial_failure_keeps_successes(tmp_path, capsys):
    good = tmp_path / "scan.png"
    good.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    code = main(["read", "--images", str(good),
                 "--reports", str(tmp_path / "missing.png"),
                 "--vision-provider", "mock"])
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert "imaging" in payload  # the successful film read survives
    assert payload["errors"] and "missing.png" in payload["errors"][0]


def test_journaled_auto_vision_keeps_provenance(monkeypatch, tmp_path):
    monkeypatch.delenv("NSCLC_VISION_PROVIDER", raising=False)
    monkeypatch.setenv("POE_API_KEY", "test-key")
    vision = build_vision_client()
    runner = NSCLCRunner(vision_llm=vision,
                         journal=Journal(tmp_path / "j.jsonl", mode="record"))
    assert describe_client(runner.vision_llm).get("auto_selected") is True
    assert runner.journal.meta.get("vision_auto_selected") is True
