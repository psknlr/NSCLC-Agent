"""Regression: a clinician-reported stage IVB KRAS G12C case after chemo-IO
and a KRAS G12C inhibitor (v1.2.0).

Before the fix the governed pipeline stopped at needs_staging_workup:
"cT4N3M1c" was not extracted, a bare M1c was refused although M1c1 and M1c2
are both IVB (and the refusal dropped T4 and N3 with it), the shared result
of "EGFR、ALK、ROS1、RET、MET exon14、BRAF V600E：阴性" was never reached so
five drivers read POSITIVE, and the treatment history, the progression
ctDNA and the treated brain metastasis were all missed."""

from __future__ import annotations

from nsclc_agent.agentic import AgentSession
from nsclc_agent.agentic.toolbox import AgentToolbox
from nsclc_agent.conversation import (
    ConsultationSession, _extract_cns, extract_facts_deterministic as extract,
)
from nsclc_agent.interview.axes import _surgery_plausible
from nsclc_agent.knowledge.biomarkers import driver_status, first_line_actionable_drivers
from nsclc_agent.knowledge.sequencing import (
    _text_says_progression, history_summary, sequencing_context,
)
from nsclc_agent.llm.mock import MockLLMClient

CASE = """62岁男性，重度吸烟史 40 pack-years，因持续咳嗽、胸痛及体重下降 2个月就诊。胸部增强CT示：右上肺约5.8 cm肿块，侵犯纵隔胸膜，纵隔多站淋巴结肿大，并伴左肾上腺及多发骨转移。脑MRI发现右额叶单发约1.6 cm转移灶，无明显神经症状。ECOG PS 1。
支气管镜活检提示：肺腺癌，TTF-1(+)、Napsin A(+)。初始分期考虑 cT4N3M1c，IVB期。
分子检测：
- EGFR、ALK、ROS1、RET、MET exon14、BRAF V600E：阴性
- KRAS G12C：阳性
- STK11失活突变 + KEAP1突变
- TP53突变
- PD-L1 TPS：80%
- TMB：约11 mut/Mb
患者接受帕博利珠单抗 + 培美曲塞 + 卡铂一线治疗。4周期后肺原发灶缩小约35%，但左肾上腺病灶增大，并新出现2个肝转移灶，呈现明显异质性/混合反应。脑转移经SRS控制稳定。
肝转移灶再次活检仍为腺癌，但PD-L1降至约10%，ctDNA显示KRAS G12C丰度升高，并出现MET扩增。随后给予KRAS G12C抑制剂治疗，约3个月后肝转移部分缩小，但肺原发灶及胸膜病灶再次进展，并出现恶性胸腔积液。

下一步究竟采用KRAS G12C靶向联合策略、MET相关治疗、再次系统化疗、局部治疗寡进展灶，还是重新活检/液体活检后按照耐药机制重新分层？"""


def test_extraction_reads_the_whole_case():
    facts = extract(CASE)
    assert facts["tnm"] == {"t": "T4", "n": "N3", "m": "M1c", "prefix": "c"}
    drivers = facts["driver_mutations"]
    for gene in ("egfr", "alk", "ros1", "ret", "met", "braf"):
        assert driver_status(drivers[gene]) == "negative", (gene, drivers[gene])
    assert driver_status(drivers["kras"]) == "positive"
    assert first_line_actionable_drivers(facts) == []
    assert facts["ngs_done"] is True
    history = facts["treatment_history"]
    assert [e.get("status") for e in history] == ["progression", "progression"]
    assert history[0]["line"] == 1 and "帕博利珠单抗" in history[0]["agents"]
    assert history[1]["agents"] == ["KRAS G12C抑制剂"]
    assert facts["progression_ngs_done"] is True
    assert facts["progression_findings"]["met_amplification"] is True
    assert facts["cns_metastases"] == {"status": "present", "source": "narrative",
                                       "symptomatic": False, "treated": True,
                                       "burden": "limited"}


def test_governed_pipeline_stages_and_sequences_the_case():
    session = ConsultationSession(role="oncologist")
    result = session.turn(CASE)
    state = result.state
    assert state.staging["stage_group"] == "IVB"
    assert state.release_status != "needs_staging_workup"
    plan = state.outputs["treatment_plan"]
    assert plan["sequencing"]["line"] == 3
    assert plan["summary"].startswith("Stage IVB, line 3 after documented progression")
    ids = plan["regimen_ids"]
    assert "docetaxel_ramucirumab_second_line" in ids
    assert "sotorasib_subsequent_line" not in ids  # progressed on the class
    assert not any(r.startswith(("pembro", "osimertinib")) for r in ids)
    asked = " ".join(q for r in state.outputs["interview"]["rounds"]
                     for q in (x["question"] for x in r["questions"]))
    assert "FEV1" not in asked  # no resection work-up for M1 disease


def test_agent_mode_notes_and_tools_agree():
    session = AgentSession(MockLLMClient(), config={"autonomy": "assisted"})
    turn = session.turn(CASE)
    assert turn.engine_stage["stage_group"] == "IVB"
    box: AgentToolbox = session.toolbox
    assert box.stage_tnm("T4", "N3", "M1c")["data"]["stage_group"] == "IVB"
    context = box.later_line_options()["data"]["context"]
    assert context["line"] == 3
    assert [o["regimen_ids"][0] for o in context["options"]] == [
        "docetaxel_ramucirumab_second_line", "docetaxel_second_line"]
    assert any("MET amplification" in c for c in context["cautions"])


def test_post_kras_inhibitor_sequencing():
    facts = {"driver_mutations": {"kras": "KRAS G12C阳性"},
             "treatment_history": [
                 {"line": 1, "agents": ["pembrolizumab", "carboplatin"],
                  "status": "progression"},
                 {"line": 2, "agents": ["索托拉西布"], "status": "progression"}]}
    seq = sequencing_context("IVB", facts)
    assert "sotorasib_subsequent_line" not in [r for o in seq["options"]
                                               for r in o["regimen_ids"]]
    assert seq["workup"]  # no progression NGS on record yet
    assert "kras g12c抑制剂" not in history_summary(facts)
    # Still on a KRAS G12C inhibitor (exposed, no progression) while chemo-IO
    # progressed: sotorasib is not re-offered as if it were new.
    facts["treatment_history"][1]["status"] = "ongoing"
    seq = sequencing_context("IVB", facts)
    assert "sotorasib_subsequent_line" not in [r for o in seq["options"]
                                               for r in o["regimen_ids"]]


def test_negated_progression_and_operability():
    assert not _text_says_progression("奥希替尼治疗中，未见进展")
    assert not _text_says_progression("未见明显进展")
    assert _text_says_progression("肺原发灶及胸膜病灶再次进展")
    assert extract("奥希替尼治疗中，未见进展")["treatment_history"] == [
        {"agents": ["奥希替尼"]}]
    assert not _surgery_plausible({"tnm": {"m": "M1c"}}, "")
    assert not _surgery_plausible({"tnm.m": "M1b"}, "")
    assert _surgery_plausible({"tnm": {"m": "M0"}}, "")


def test_cns_extraction_respects_negation():
    assert _extract_cns("PET-CT和脑MRI确认无远处转移") == {"status": "absent"}
    assert _extract_cns("脑MRI阴性，肝转移") == {"status": "absent"}
    assert _extract_cns("PET-CT+脑MRI确认M0。") is None
    assert _extract_cns("多发脑转移，头痛")["burden"] == "extensive"


def test_gene_lists_never_turn_negative_results_positive():
    drivers = extract("EGFR、ALK、ROS1、RET、MET exon14、BRAF V600E：阴性")["driver_mutations"]
    assert {driver_status(v) for v in drivers.values()} == {"negative"}
    assert len(drivers) == 6
    # A list item with its own variant and no list-header colon is ambiguous:
    # left unrecorded rather than guessed.
    assert "egfr" not in extract("EGFR L858R、ALK阴性")["driver_mutations"]
    assert driver_status(extract("EGFR 19del, ALK negative")["driver_mutations"]["egfr"]) \
        == "positive"
