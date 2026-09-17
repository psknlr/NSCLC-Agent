"""Regression suite for the adversarial-review findings.

Every test here is a confirmed attack or defect from the three review passes
(clinical data / staging, safety-rule red team, control plane), pinned so it
cannot come back.
"""

import json

import pytest

from nsclc_agent import Case, NSCLCRunner
from nsclc_agent.journal import Journal, JournaledLLM
from nsclc_agent.knowledge.biomarkers import driver_status
from nsclc_agent.knowledge import regimens as regimen_lib
from nsclc_agent.knowledge.trials import TRIALS_BY_ID, resolve_trial_id
from nsclc_agent.llm.base import NullLLMClient
from nsclc_agent.llm.mock import MockLLMClient
from nsclc_agent.safety import emergencies
from nsclc_agent.safety.rules import check_plan
from nsclc_agent.staging import StagingError, normalize_stage_group, stage_from_strings

DRIVER_NEG = {"driver_mutations": {"egfr": "negative", "alk": "negative"},
              "histologic_category": "adenocarcinoma"}
EGFR_POS = {"driver_mutations": {"egfr": "L858R", "alk": "negative"},
            "histologic_category": "adenocarcinoma"}
IIIB = {"stage_group": "IIIB", "n_category": "N2b"}
IIIC_N3 = {"stage_group": "IIIC", "n_category": "N3"}
IVB = {"stage_group": "IVB", "n_category": "N2b"}


def _blocks(staging, facts, plan):
    return {v.rule_id for v in check_plan(staging, facts, plan)
            if v.severity == "block"}


# ------------------------------------------------- emergency self-negation

def test_leg_weakness_cue_does_not_self_negate():
    """双腿无力 contains 无 — the cue must not suppress itself."""
    assert emergencies.screen("医生我最近双腿无力越来越重").emergency


def test_cannot_lie_flat_does_not_self_negate():
    assert emergencies.screen("I cannot lie flat and feel like choking").emergency


def test_wufa_compound_is_not_denial():
    assert emergencies.screen("今天开始出现大小便失禁而且无法控制").emergency
    assert emergencies.screen("双腿无力，无法行走").emergency


def test_true_negation_still_closes():
    result = emergencies.screen("没有双腿无力，大小便正常，没有咯血")
    assert not result.emergency
    assert "cord_compression" in result.negated


# --------------------------------------------------- dose-scan hardening

@pytest.mark.parametrize("leak", [
    "osimertinib 80 milligrams once daily",
    "give eighty milligrams of osimertinib",
    "pembrolizumab 200 mcg", "80 μg dose", "5000 IU daily",
])
def test_dose_scan_catches_unit_variants(leak):
    plan = {"summary": leak, "regimen_ids": [], "options": []}
    assert "DOSE_IN_MODEL_OUTPUT" in _blocks(IVB, DRIVER_NEG, plan)


# --------------------------------------------------- surgery synonym gaps

@pytest.mark.parametrize("phrase", [
    "wedge resection of the primary", "surgical excision of the tumor",
    "sleeve resection planned", "手术根治切除", "R0 resection is achievable",
    "recommend proceeding with resection",
])
def test_n3_surgery_synonyms_blocked(phrase):
    plan = {"summary": phrase, "regimen_ids": [], "options": []}
    assert "N3_NO_SURGERY" in _blocks(IIIC_N3, DRIVER_NEG, plan)


def test_n3_negated_surgery_recommendation_clean():
    plan = {"summary": "N3 disease：不建议行手术，应行根治性同步放化疗以外科会诊备查",
            "regimen_ids": ["ccrt_60gy"], "options": []}
    # 不建议行手术 is the CORRECT statement — it must not trip the rule.
    assert "N3_NO_SURGERY" not in _blocks(IIIC_N3, DRIVER_NEG, plan)


# ------------------------------------------- concurrent durvalumab gaps

@pytest.mark.parametrize("phrase", [
    "durvalumab together with radiation",
    "durvalumab during chemoradiation",
    "durvalumab given alongside CRT",
    "同步度伐利尤单抗放疗",
    "cCRT with concurrent durvalumab",
])
def test_concurrent_durvalumab_phrasings_blocked(phrase):
    plan = {"summary": phrase, "regimen_ids": [], "options": []}
    assert "NO_CONCURRENT_DURVALUMAB" in _blocks(IIIB, DRIVER_NEG, plan)


def test_consolidation_after_crt_clean():
    plan = {"summary": "Definitive cCRT, then consolidation durvalumab started "
                       "within six weeks after completion.",
            "regimen_ids": ["ccrt_60gy", "durva_consolidation"], "options": []}
    assert "NO_CONCURRENT_DURVALUMAB" not in _blocks(IIIB, DRIVER_NEG, plan)


# ------------------------------------------------------- RT dose arithmetic

def test_rt_total_in_fractions_is_not_multiplied():
    plan = {"summary": "60 Gy in 30 fractions", "regimen_ids": [], "options": []}
    assert "NO_RT_DOSE_ESCALATION" not in _blocks(IIIB, DRIVER_NEG, plan)


def test_rt_per_fraction_arithmetic_blocked():
    plan = {"summary": "2 Gy per fraction x 37 fractions to the primary",
            "regimen_ids": [], "options": []}
    assert "NO_RT_DOSE_ESCALATION" in _blocks(IIIB, DRIVER_NEG, plan)


def test_rt_three_digit_dose_blocked_even_in_dose_plan():
    plan = {"regimen_ids": [], "options": [],
            "dose_plan": {"rt": "escalate to 100 Gy"}}
    assert "NO_RT_DOSE_ESCALATION" in _blocks(IIIB, DRIVER_NEG, plan)


def test_rt_prohibition_note_is_not_a_proposal():
    detail = regimen_lib.get("ccrt_60gy").detail()
    plan = {"regimen_ids": ["ccrt_60gy"], "options": [],
            "dose_plan": {"regimens": [detail]}}
    # The library's own "do NOT escalate to 74 Gy" caution must stay clean.
    assert "NO_RT_DOSE_ESCALATION" not in _blocks(IIIB, DRIVER_NEG, plan)


# ------------------------------------------------------- trial-ref mangling

@pytest.mark.parametrize("ref,expected", [
    ("the CheckMate-816 trial", "CHECKMATE816"),
    ("CM 816", "CHECKMATE816"),
    ("the PACIFIC trial", "PACIFIC"),
    ("keynote 671 study", "KEYNOTE671"),
])
def test_resolve_trial_id_tolerates_wrappers(ref, expected):
    assert resolve_trial_id(ref) == expected


def test_mangled_trial_ref_still_triggers_driver_rule():
    plan = {"regimen_ids": [], "trial_refs": ["the CheckMate-816 trial"],
            "options": []}
    assert "DRIVER_EXCLUDES_PERIOP_IO" in _blocks(
        {"stage_group": "IIIA", "n_category": "N2a"}, EGFR_POS, plan)


# ------------------------------------------------------- driver-status reads

@pytest.mark.parametrize("value,status", [
    ("negative for mutation", "negative"),
    ("EGFR野生型", "negative"),
    ("no mutation detected", "negative"),
    ("L858R", "positive"),
    ("exon 19 deletion detected", "positive"),
    ("阳性", "positive"),
    ("pending", "unknown"),
    ("awaiting NGS", "unknown"),
    (None, "unknown"),
])
def test_driver_status_vocabulary(value, status):
    assert driver_status(value) == status


def test_verbose_negative_does_not_block_chemo_io():
    facts = {"driver_mutations": {"egfr": "negative for mutation",
                                  "alk": "not detected by FISH"},
             "histologic_category": "adenocarcinoma"}
    plan = {"regimen_ids": ["pembro_pemetrexed_platinum"], "options": []}
    assert "DRIVER_FIRST_LINE" not in _blocks(IVB, facts, plan)


# ---------------------------------------------------------- staging labels

@pytest.mark.parametrize("label", ["IVC", "IC", "IIC", "0A"])
def test_nonexistent_stage_groups_refused(label):
    with pytest.raises(StagingError):
        normalize_stage_group(label)


def test_bare_family_labels_refused_as_ambiguous():
    with pytest.raises(StagingError, match="sub-stage"):
        normalize_stage_group("Stage III")
    with pytest.raises(StagingError, match="IA1"):
        normalize_stage_group("IA")


def test_t1mi_first_class_with_note():
    result = stage_from_strings("T1mi", "N0", "M0")
    assert result.stage_group == "IA1"
    assert result.tnm.t == "T1mi"
    assert any("MIA" in note for note in result.descriptor_notes)


@pytest.mark.parametrize("edition", ["AJCC 9", "9th edition", "UICC9", "AJCC/UICC 9"])
def test_edition_gate_accepts_spelled_variants(edition):
    assert stage_from_strings("T2a", "N0", "M0", edition=edition).stage_group == "IB"


def test_fullwidth_letters_fold():
    assert stage_from_strings("ＴＩＳ", "N0", "M0").stage_group == "0"
    assert stage_from_strings("Ｔ１Ａ", "Ｎ０", "Ｍ０").stage_group == "IA1"


# ------------------------------------------------------- clinical data pins

def test_adaura_results_label_both_populations():
    results = " ".join(TRIALS_BY_ID["ADAURA"].results)
    assert "0.17" in results and "II–IIIA" in results
    assert "0.20" in results and "overall" in results


def test_keynote091_label_requires_chemo():
    assert "AND platinum-based chemotherapy" in TRIALS_BY_ID["KEYNOTE091"].approval
    assert "platinum-based" in regimen_lib.get("pembro_adjuvant").label_note


def test_flaura_mariposa_no_histology_restriction():
    assert TRIALS_BY_ID["FLAURA"].histology == "any"
    assert TRIALS_BY_ID["MARIPOSA"].histology == "any"


# ---------------------------------------------------------- control plane

def test_journal_rerecord_truncates_stale_file(tmp_path):
    path = tmp_path / "case.jsonl"
    Journal(path, mode="record").record("tool", "a", {}, {"ok": True})
    second = Journal(path, mode="record")
    second.record("tool", "b", {}, {"ok": True})
    loaded = Journal.load(path)
    assert len(loaded.entries) == 1
    assert loaded.entries[0].label == "b"


def test_terminal_audit_crash_fails_closed_not_raises(tmp_path):
    path = tmp_path / "case.jsonl"
    case = Case(t="T2a", n="N0", m="M1b",
                presentation="Single adrenal met. No hemoptysis, no leg "
                             "weakness, no fever.",
                facts={"driver_mutations": {"egfr": "Ex19del", "alk": "negative"},
                       "histologic_category": "adenocarcinoma",
                       "ngs_done": True})
    recorder = NSCLCRunner(journal=Journal(path, mode="record"))
    recorder.run_case(case)
    # Tamper an entry hash: the replay diverges inside the terminal critic.
    lines = path.read_text().splitlines()
    for index, line in enumerate(lines):
        record = json.loads(line)
        if record.get("label") == "citation_verify":
            record["req_hash"] = "0" * 64
            lines[index] = json.dumps(record, ensure_ascii=False)
            break
    path.write_text("\n".join(lines) + "\n")
    replayer = NSCLCRunner(journal=Journal.load(path))
    state = replayer.run_case(case)  # must not raise
    assert state.release_status == "failed_closed"
    assert "run_meta" in state.outputs


def test_vision_reads_are_journaled_and_replayable(tmp_path):
    path = tmp_path / "vision.jsonl"
    image = tmp_path / "scan.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfakebytes")
    case = Case(t="T2a", n=None, m=None,
                presentation="Staging films attached. No hemoptysis, no leg "
                             "weakness, no fever.",
                images=[str(image)],
                facts={"histologic_category": "adenocarcinoma",
                       "driver_mutations": {"egfr": "negative", "alk": "negative"}})
    recorder = NSCLCRunner(journal=Journal(path, mode="record"),
                           vision_llm=MockLLMClient(vision=True))
    original = recorder.run_case(case)
    assert original.outputs.get("imaging")
    # Replay with NO vision provider: the read must come from the journal.
    replayer = NSCLCRunner(journal=Journal.load(path))
    replayed = replayer.run_case(case)
    assert replayed.release_status != "failed_closed"
    assert replayed.outputs.get("imaging") == original.outputs.get("imaging")


def test_independent_cases_do_not_share_stall_history():
    runner = NSCLCRunner()
    case_payload = dict(
        t="T2b", n="N2b", m="M0",
        presentation="Unresectable N2b adenocarcinoma. PET-CT and brain MRI "
                     "confirm M0.",  # red-flag axes deliberately unanswered
        facts={"driver_mutations": {"egfr": "L858R", "alk": "negative"},
               "histologic_category": "adenocarcinoma",
               "resectability_category": "UNRESECTABLE"},
    )
    verdicts = []
    for _ in range(3):
        state = runner.run_case(Case(**case_payload))
        verdicts.append(
            ((state.outputs.get("interview") or {}).get("verdict") or {}).get("verdict"))
    # Before the fix the third identical-but-independent case came back
    # "blocked" via stall history leaked across runs.
    assert verdicts == ["not_achieved"] * 3


def test_dose_gate_untested_biomarkers_not_a_pass():
    from nsclc_agent.skills import SkillRegistry
    from nsclc_agent.state import Budget
    from nsclc_agent.tools import CapabilityBroker, ToolHealth, ToolRegistry

    broker = CapabilityBroker(
        "oncologist", "routine", budget=Budget(),
        skill_registry=SkillRegistry.discover(),
        active_skill="nsclc.dose_planning", health=ToolHealth())
    result = ToolRegistry().call(
        broker, "dose_gate_check", regimen_id="pembro_pemetrexed_platinum",
        facts={"driver_mutations": {"egfr": "not_tested", "alk": "negative"}})
    gate = next(g for g in result.data["gates"] if g["gate"] == "egfr_alk_negative")
    assert gate["status"] == "unverified"
    assert "incomplete" in gate["note"]


def test_budget_refund_on_llm_failure():
    from nsclc_agent.agents.toolloop import ToolLoop
    from nsclc_agent.skills import SkillRegistry
    from nsclc_agent.state import CaseRunState
    from nsclc_agent.tools import CapabilityBroker, ToolHealth, ToolRegistry

    class ExplodingLLM:
        name = "boom"
        model = "boom"
        available = True

        def chat(self, messages, **kwargs):
            raise RuntimeError("transport down")

    state = CaseRunState(complaint="x")
    skills = SkillRegistry.discover()
    loop = ToolLoop(
        ExplodingLLM(), ToolRegistry(),
        CapabilityBroker("oncologist", "routine", budget=state.budget,
                         skill_registry=skills, active_skill="nsclc.treatment",
                         health=ToolHealth()),
        state, agent_name="TreatmentAgent", skill_id="nsclc.treatment",
        skill_spec=skills.get("nsclc.treatment"))
    loop.run("objective", {}, "TreatmentPlan")
    assert state.budget.used_llm_calls == 0  # the failed reservation came back


def test_eval_ships_inside_the_package():
    from nsclc_agent.eval import run_eval

    report = run_eval()
    assert report["summary"]["all_passed"], report["summary"]["failed_ids"]
