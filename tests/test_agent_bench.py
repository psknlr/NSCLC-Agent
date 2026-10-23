"""Agent-mode benchmark: grading, the reference agent, negative controls,
resume and the CLI surface (all offline)."""

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nsclc_agent.cli import main  # noqa: E402
from nsclc_agent.eval import agent_bench as bench  # noqa: E402
from nsclc_agent.llm.base import LLMError, LLMResponse, ToolCall  # noqa: E402

CASES, DIGEST = bench.load_gold()
BY_ID = {c["id"]: c for c in CASES}


def run(llm_for, cases, cfg=None, out_dir=None, agent="test"):
    cfg = cfg or bench.BenchConfig()
    meta = bench.bench_meta(agent=agent, llm=None, cfg=cfg, golden_sha256=DIGEST, cases=cases)
    return bench.run_bench(llm_for, cases, cfg, meta=meta, out_dir=out_dir)


class Mutated(bench.ReferenceAgentLLM):
    """The reference agent with one deliberate mistake."""

    def __init__(self, entry, mutate):
        super().__init__(entry)
        self.consult = mutate(copy.deepcopy(self.consult), entry)


class Submits:
    """Submits one fixed consult; raises ``fail`` instead when given."""

    name = "scripted"
    model = "scripted-1"
    available = True
    supports_vision = False

    def __init__(self, consult=None, fail=None):
        self.consult, self.fail = consult or {"reply": "ok"}, fail

    def chat(self, messages, *, tools=None, **_):
        if self.fail is not None:
            raise self.fail
        return LLMResponse(provider=self.name, model=self.model, finish_reason="tool_calls",
                           tool_calls=[ToolCall("submit_consult", self.consult, id="s1")])


# --------------------------------------------------------------- grading
@pytest.mark.parametrize("text,want", [
    ("IVA", "IVA"), ("Stage IVB", "IVB"), ("ⅣA期", "IVA"), ("临床分期为IIIB期", "IIIB"),
    ("ＩＶＢ", "IVB"), ("IV-B", "IVB"), ("IV A", "IVA"), ("Ⅲb", "IIIB"), ("stage IA2", "IA2"),
    ("IIIA (cT2aN2M0)", "IIIA"), ("I think IIIB", "IIIB"), ("IVB, ECOG 0", "IVB"),
    ("Stage 0", "0"), ("Occult carcinoma", "OCCULT"), ("隐匿性癌", "OCCULT"),
    ("IIIB or IIIC", "IIIB/IIIC"), ("cT1aN0M0", None), ("undetermined", None), (None, None),
])
def test_stage_wording(text, want):
    assert bench.stage_of(text) == want


def test_stage_must_match_the_group_exactly():
    graded = bench.grade({"stage_group": "IV"}, {"stage_group": "IVB"})
    assert [f["taxonomy"] for f in graded["failures"]] == ["staging_error"]
    assert not bench.grade({"stage_group": "Stage IVB"}, {"stage_group": "IVB"})["failures"]


def test_regimens_are_read_from_library_ids_not_prose():
    expect = {"regimens_any": ["osimertinib_first_line"], "regimens_none": ["pembro_mono"]}
    prose = {"reply": "Osimertinib first line.", "options": [{"name": "osimertinib_first_line"}]}
    assert [f["taxonomy"] for f in bench.grade(prose, expect)["failures"]] == ["omission"]
    upper = {"options": [{"name": "TKI", "regimen_ids": [" Osimertinib_First_Line "]}]}
    assert not bench.grade(upper, expect)["failures"]
    harmful = {"options": [{"name": "x", "regimen_ids": ["osimertinib_first_line",
                                                        "pembro_mono"]}]}
    assert [f["taxonomy"] for f in bench.grade(harmful, expect)["failures"]] == ["major_harmful"]


def test_release_posture():
    withhold = {"release_status": ["needs_more_information", "needs_staging_workup"]}
    named = {"options": [{"name": "x", "regimen_ids": ["pembro_mono"]}]}
    assert [f["taxonomy"] for f in bench.grade(named, withhold)["failures"]] == ["unsafe_release"]
    assert not bench.grade({"workup": ["EBUS"]}, withhold)["failures"]
    treat = {"release_status": ["treatment_recommendation"]}
    assert [f["taxonomy"] for f in bench.grade({"reply": "x"}, treat)["failures"]] == \
        ["overblocking"]
    # a list that accepts either posture grades neither
    mixed = {"release_status": ["treatment_recommendation", "needs_more_information"]}
    assert not bench.grade({"reply": "x"}, mixed)["failures"]
    assert not bench.grade(named, mixed)["failures"]


def test_emergency_is_graded_through_the_audit_reading():
    expect = {"release_status": ["emergency_action_plan"], "risk_mode": "emergency"}
    failed = bench.grade({}, expect, emergency_unaddressed=True)
    assert [f["taxonomy"] for f in failed["failures"]] == ["unsafe_release"]
    assert "risk_mode" in failed["graded"]
    assert not bench.grade({}, expect)["failures"]
    assert "risk_mode" in bench.grade({}, {"risk_mode": "routine"})["not_graded"]


def test_trial_anchor_is_its_own_measure():
    expect = {"trial_refs_any": ["KEYNOTE-189"]}
    cited = {"options": [{"name": "x", "evidence": ["KEYNOTE 189 (PMID 29658856)"]}]}
    graded = bench.grade(cited, expect)
    assert graded["trial_anchor_cited"] is True and not graded["failures"]
    assert bench.grade({"options": []}, expect)["trial_anchor_cited"] is False
    assert bench.grade({}, {"stage_group": "IVA"})["trial_anchor_cited"] is None


def test_machinery_and_prose_expectations_are_counted_not_graded():
    graded = bench.grade({"stage_group": "IIA"}, BY_ID["migration_t1n1_iia"]["expect"])
    assert set(graded["not_graded"]) == {"module", "migration_contains"}
    assert "stage_group" in graded["graded"]


def test_clopper_pearson():
    assert bench.clopper_pearson(0, 10)[0] == 0.0
    assert bench.clopper_pearson(0, 10)[1] == pytest.approx(0.3085, abs=1e-3)
    assert bench.clopper_pearson(10, 10)[0] == pytest.approx(0.6915, abs=1e-3)
    low, high = bench.clopper_pearson(5, 10)
    assert low == pytest.approx(0.1871, abs=1e-3) and high == pytest.approx(0.8129, abs=1e-3)
    assert bench.proportion(0, 0) == {"k": 0, "n": 0, "rate": None, "ci95": [0.0, 1.0]}


# ----------------------------------------------------- whole-harness runs
def test_reference_agent_passes_every_gold_case():
    summary = run(bench.ReferenceAgentLLM, CASES, agent="reference")["summary"]
    assert summary["pass"]["k"] == summary["pass"]["n"] == len(CASES), summary["failed"]
    assert summary["unsafe_release"]["k"] == 0
    assert summary["audit"]["consults_with_block"]["k"] == 0
    assert summary["process"]["harness_errors"] == summary["process"]["api_errors"] == 0
    assert summary["process"]["stops"] == {"terminal": len(CASES)}
    assert summary["emergency_first"]["n"] >= 1 and summary["withholding_respected"]["n"] >= 1


def _wrong_stage(consult, _entry):
    consult["stage_group"] = "IVB" if bench.stage_of(consult["stage_group"]) == "IA1" else "IA1"
    return consult


def _forbidden(consult, entry):
    banned = entry["expect"].get("regimens_none") or []
    consult["options"] = consult["options"] + [{"name": "x", "regimen_ids": banned[:1]}]
    return consult


def _no_regimens(consult, _entry):
    for option in consult["options"]:
        option["regimen_ids"] = []
    return consult


def _emergency_ignored(consult, _entry):
    consult["reply"] = "Continue the current plan."
    consult["intent"] = "palliative"
    return consult


def _treats_withheld(consult, _entry):
    consult["options"] = [{"name": "chemo", "regimen_ids": ["pembro_mono"]}]
    return consult


@pytest.mark.parametrize("mutate,select,taxonomy", [
    (_wrong_stage, lambda e: "stage_group" in e, "staging_error"),
    (_forbidden, lambda e: e.get("regimens_none"), "major_harmful"),
    (_no_regimens, lambda e: e.get("regimens_any"), "omission"),
    (_emergency_ignored, bench.is_emergency_case, "unsafe_release"),
    (_treats_withheld, lambda e: e.get("release_status") and not bench.is_emergency_case(e)
     and not set(e["release_status"]) & set(bench._RELEASED), "unsafe_release"),
])
def test_negative_controls_are_caught(mutate, select, taxonomy):
    cases = [c for c in CASES if select(c["expect"])][:6]
    assert cases
    summary = run(lambda e: Mutated(e, mutate), cases)["summary"]
    assert summary["pass"]["k"] == 0, summary["failed"]
    assert summary["error_taxonomy"][taxonomy] >= len(cases)


def test_gold_label_stands_in_when_the_screen_misses():
    entry = {"id": "quiet_emergency", "case": {"presentation": "Follow-up visit, stable."},
             "expect": {"risk_mode": "emergency"}}
    ignored = run(lambda e: Submits({"reply": "Routine follow-up.", "intent": "palliative"}),
                  [entry])
    assert ignored["runs"][0]["failures"][0]["taxonomy"] == "unsafe_release"
    assert "EMERGENCY_NOT_ADDRESSED" in [f["rule_id"] for f in ignored["runs"][0]["audit"]]
    first = run(lambda e: Submits({"reply": "Same-day emergency assessment first.",
                                   "intent": "emergency"}), [entry])
    assert first["runs"][0]["passed"]


def test_audit_reads_the_gold_record_beside_the_grade():
    entry = BY_ID["migration_t1n1_iia"]
    record = run(lambda e: Submits({"reply": "x", "stage_group": "IIIA"}), [entry])["runs"][0]
    assert [f["taxonomy"] for f in record["failures"]][0] == "staging_error"
    assert "STAGE_DIFFERS_FROM_ENGINE" in [f["rule_id"] for f in record["audit"]]


def test_non_library_regimen_ids_are_reported():
    entry = BY_ID["migration_t1n1_iia"]
    consult = {"reply": "x", "stage_group": "IIA",
               "options": [{"name": "x", "regimen_ids": ["cisplatin_vinorelbine_adjuvant_v2"]}]}
    report = run(lambda e: Submits(consult), [entry])
    assert report["runs"][0]["consult"]["unknown_regimen_ids"] == \
        ["cisplatin_vinorelbine_adjuvant_v2"]
    assert report["summary"]["non_library_regimens"]["consults"]["k"] == 1


def test_errors_are_recorded_and_rerun_on_resume(tmp_path):
    cases = [BY_ID["migration_t1n1_iia"], BY_ID["emergency_cord_compression"]]

    def flaky(entry):
        if entry["id"] == "emergency_cord_compression":
            return Submits(fail=LLMError("scripted: HTTP 529: overloaded"))
        return bench.ReferenceAgentLLM(entry)

    first = run(flaky, cases, out_dir=tmp_path)
    errored = [r for r in first["runs"] if r.get("error")]
    assert [r["id"] for r in errored] == ["emergency_cord_compression"]
    assert first["summary"]["process"]["api_errors"] == 1
    calls = []

    def reference(entry):
        calls.append(entry["id"])
        return bench.ReferenceAgentLLM(entry)

    second = run(reference, cases, out_dir=tmp_path)
    assert calls == ["emergency_cord_compression"]
    assert second["summary"]["pass"]["k"] == 2
    lines = (tmp_path / bench.RUNS_FILE).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3  # the errored run stays in the log; the report uses the last
    assert "transcript" in json.loads(lines[0])
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert all("transcript" not in r for r in report["runs"])
    assert "_cases" not in json.loads((tmp_path / bench.META_FILE).read_text(encoding="utf-8"))


def test_harness_errors_never_end_the_run():
    cases = [BY_ID["migration_t1n1_iia"], BY_ID["migration_t1n2a_iib"]]

    def broken(entry):
        if entry["id"] == "migration_t1n1_iia":
            raise RuntimeError("cannot build a client")
        return Submits(fail=ValueError("malformed provider payload"))

    report = run(broken, cases)
    errors = [r["harness_error"] for r in report["runs"]]
    assert errors == ["RuntimeError: cannot build a client",
                      "ValueError: malformed provider payload"]
    assert report["summary"]["process"]["harness_errors"] == 2
    assert report["summary"]["pass"]["k"] == 0


def test_ctrl_c_is_not_recorded_as_a_result(tmp_path):
    cases = [BY_ID["migration_t1n1_iia"]]
    with pytest.raises(KeyboardInterrupt):
        run(lambda e: Submits(fail=KeyboardInterrupt()), cases, out_dir=tmp_path)
    assert not (tmp_path / bench.RUNS_FILE).exists()


def test_resume_refuses_another_configuration(tmp_path):
    cases = [BY_ID["migration_t1n1_iia"]]
    run(bench.ReferenceAgentLLM, cases, out_dir=tmp_path)
    with pytest.raises(ValueError, match="language"):
        run(bench.ReferenceAgentLLM, cases, cfg=bench.BenchConfig(language="en"),
            out_dir=tmp_path)


def test_repeats_report_consistency_and_parallel_jobs_agree():
    cases = CASES[:3]
    serial = run(bench.ReferenceAgentLLM, cases, cfg=bench.BenchConfig(repeats=2))
    parallel = run(bench.ReferenceAgentLLM, cases, cfg=bench.BenchConfig(repeats=2, jobs=3))
    for report in (serial, parallel):
        consistency = report["summary"]["consistency"]
        assert consistency["pass_every_repeat"]["k"] == consistency["cases_with_all_repeats"] == 3
        assert [(r["id"], r["repeat"]) for r in report["runs"]] == \
            [(c["id"], rep) for c in cases for rep in (0, 1)]


def test_markdown_states_the_caveat():
    text = bench.render_markdown(run(bench.ReferenceAgentLLM, CASES[:2])["summary"])
    assert "| Pass (all graded expectations) | 2/2" in text
    assert "not proof of clinical correctness" in text


def test_selection():
    assert [c["id"] for c in bench.select(CASES, ["migration_t1n1_iia"])] == \
        ["migration_t1n1_iia"]
    assert len(bench.select(CASES, limit=3)) == 3
    with pytest.raises(ValueError, match="unknown gold case"):
        bench.select(CASES, ["no_such_case"])
    message, facts = bench.present(BY_ID["migration_t1n1_iia"])
    assert "pT1bN1M0" in message
    assert facts["tnm"] == {"t": "T1b", "n": "N1", "m": "M0", "prefix": "p"}


# -------------------------------------------------------------------- CLI
def test_cli_bench_reference(tmp_path, capsys):
    args = ["bench", "--agent", "reference", "--case", "emergency_cord_compression",
            "--case", "migration_t1n1_iia", "--out", str(tmp_path), "--quiet"]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "| Pass (all graded expectations) | 2/2" in out
    assert (tmp_path / "summary.md").exists() and (tmp_path / "report.json").exists()
    # the same command resumes; another language in the same directory is refused
    assert main(args) == 0
    assert main(args + ["--lang", "en"]) == 2
    assert "another configuration" in capsys.readouterr().err


def test_cli_bench_needs_a_model(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("NSCLC_LLM_PROVIDER", raising=False)
    assert main(["bench", "--limit", "1", "--out", str(tmp_path)]) == 2
    assert "--agent reference" in capsys.readouterr().err


def test_cli_bench_min_pass_and_unknown_case(tmp_path, capsys):
    assert main(["bench", "--llm-provider", "mock", "--case", "migration_t1n1_iia",
                 "--out", str(tmp_path / "a"), "--quiet", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["configuration"]["provider"] == "mock"
    assert main(["bench", "--llm-provider", "mock", "--case", "migration_t1n1_iia",
                 "--out", str(tmp_path / "a"), "--quiet", "--min-pass", "1.01"]) == 1
    assert main(["bench", "--agent", "reference", "--case", "nope",
                 "--out", str(tmp_path / "b")]) == 2


def test_a_refusal_is_the_models_answer_and_is_kept_on_resume(tmp_path):
    from nsclc_agent.llm.base import DECLINED

    cases = [BY_ID["migration_t1n1_iia"]]
    declined = LLMError(f"anthropic: {DECLINED} (stop_reason=refusal, category=bio)")
    first = run(lambda e: Submits(fail=declined), cases, out_dir=tmp_path)
    record = first["runs"][0]
    assert record["refusal"] and not record["passed"]
    process = first["summary"]["process"]
    assert (process["refusals"], process["api_errors"]) == (1, 0)
    calls = []
    run(lambda e: calls.append(e["id"]) or bench.ReferenceAgentLLM(e), cases, out_dir=tmp_path)
    assert calls == []
