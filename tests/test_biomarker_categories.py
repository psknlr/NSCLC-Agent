"""Biomarker categories of advanced NSCLC, NSCL-21 … NSCL-39 (v1.4.0).

The category follows the test results marker by marker: a gene-level
"positive" is not a category (KRAS G12D is not NSCL-26, MET amplification
is not NSCL-34, HER2 amplification is not NSCL-36), and the PD-L1
categories are assigned only once all twelve actionable markers are
NEGATIVE — untested never counts as negative."""

from __future__ import annotations

import pytest

from nsclc_agent import webapi
from nsclc_agent.agentic import AgentSession
from nsclc_agent.agentic.prompts import lead_prompt
from nsclc_agent.conversation import ConsultationSession, extract_facts_deterministic as extract
from nsclc_agent.knowledge.biomarker_categories import CATEGORIES, TARGETABLE, classify, marker_status
from nsclc_agent.llm.base import LLMResponse, ToolCall
from nsclc_agent.llm.mock import MockLLMClient

ALL_NEGATIVE = {g: "negative" for g in ("egfr", "kras", "alk", "ros1", "braf", "ntrk", "met",
                                        "ret", "erbb2", "nrg1")}


def drivers(**changes: str) -> dict:
    return {"driver_mutations": {**ALL_NEGATIVE, **changes}}


def test_the_table_has_fourteen_categories_in_order():
    assert [c.code for c in CATEGORIES] == [
        "NSCL-21", "NSCL-24", "NSCL-25", "NSCL-26", "NSCL-27", "NSCL-30", "NSCL-32",
        "NSCL-33", "NSCL-34", "NSCL-35", "NSCL-36", "NSCL-37", "NSCL-38", "NSCL-39"]
    assert len(TARGETABLE) == 12
    assert {c["code"] for c in webapi.catalog()["biomarker_categories"]} == {c.code for c in CATEGORIES}


@pytest.mark.parametrize("change,code", [
    ({"egfr": "exon 19 deletion"}, "NSCL-21"),
    ({"egfr": "L858R"}, "NSCL-21"),
    ({"egfr": "G719X"}, "NSCL-24"),
    ({"egfr": "L861Q"}, "NSCL-24"),
    ({"egfr": "S768I"}, "NSCL-24"),
    ({"egfr": "exon 20 insertion"}, "NSCL-25"),
    ({"kras": "G12C"}, "NSCL-26"),
    ({"alk": "EML4-ALK fusion"}, "NSCL-27"),
    ({"ros1": "CD74-ROS1 fusion"}, "NSCL-30"),
    ({"braf": "V600E"}, "NSCL-32"),
    ({"ntrk": "ETV6-NTRK3 fusion"}, "NSCL-33"),
    ({"met": "exon 14 skipping"}, "NSCL-34"),
    ({"ret": "KIF5B-RET fusion"}, "NSCL-35"),
    ({"erbb2": "exon 20 insertion (YVMA)"}, "NSCL-36"),
    ({"nrg1": "CD74-NRG1 fusion"}, "NSCL-37"),
])
def test_each_actionable_alteration_has_its_category(change, code):
    result = classify(drivers(**change))
    assert result["status"] == "targetable" and result["codes"] == [code]


def test_pd_l1_categories_only_after_every_marker_is_negative():
    assert classify({**drivers(), "pd_l1": {"tps": 60}})["codes"] == ["NSCL-38"]
    assert classify({**drivers(), "pd_l1": {"tps": 1}})["codes"] == ["NSCL-38"]
    assert classify({**drivers(), "pd_l1": {"tps": 0}})["codes"] == ["NSCL-39"]
    pending = classify(drivers())
    assert pending["status"] == "pd_l1_pending" and pending["missing"] == ["PD-L1 (TPS)"]
    # one marker untested: no PD-L1 category, whatever the TPS
    partial = {"driver_mutations": {**ALL_NEGATIVE}, "pd_l1": {"tps": 80}}
    del partial["driver_mutations"]["nrg1"]
    result = classify(partial)
    assert result["status"] == "incomplete" and result["codes"] == []
    assert result["missing"] == ["NRG1 fusion"]


@pytest.mark.parametrize("change,marker,status", [
    ({"kras": "G12D"}, "kras_g12c", "negative"),
    ({"braf": "G469A"}, "braf_v600e", "negative"),
    ({"met": "MET amplification"}, "met_ex14", "unknown"),
    ({"erbb2": "HER2 amplification"}, "erbb2", "unknown"),
    ({"egfr": "positive"}, "egfr_common", "unknown"),
    ({"egfr": "T790M"}, "egfr_uncommon", "unknown"),
])
def test_marker_level_not_gene_level(change, marker, status):
    assert marker_status(drivers(**change))[marker] == status


def test_kras_g12d_with_everything_else_negative_reads_by_pd_l1():
    assert classify({**drivers(kras="G12D"), "pd_l1": {"tps": 20}})["codes"] == ["NSCL-38"]


def test_extraction_reads_ntrk_erbb2_and_nrg1():
    facts = extract("NGS：NTRK1 融合阳性；ERBB2（HER2）exon 20 插入突变；NRG1 融合阴性；KRAS G12C 阴性")
    assert set(facts["driver_mutations"]) >= {"ntrk", "erbb2", "nrg1", "kras"}
    result = classify(facts)
    assert result["codes"] == ["NSCL-33", "NSCL-36"]


def test_governed_plan_and_reply_carry_the_category():
    session = ConsultationSession(role="oncologist")
    result = session.turn("68岁男性，右肺腺癌 cT2aN2bM1b，单发骨转移，ECOG 1。NGS：KRAS G12C 阳性，"
                          "EGFR、ALK、ROS1、BRAF V600E、NTRK、MET 14、RET、ERBB2、NRG1 均阴性。PD-L1 TPS 30%。")
    category = result.state.outputs["treatment_plan"]["biomarker_category"]
    assert category["codes"] == ["NSCL-26"]
    assert "生物标志物分类：NSCL-26" in result.reply


def test_both_prompts_carry_the_table_and_submit_normalises_codes():
    for autonomy in ("full", "assisted"):
        prompt = lead_prompt("oncologist", roster=[], mcp_tools=[], instructions="",
                             autonomy=autonomy)
        assert "NSCL-37：NRG1 基因融合阳性" in prompt and "NSCL-39" in prompt

    class Scripted:
        name, model, available, supports_vision = "s", "s-1", True, False

        def chat(self, messages, **_):
            return LLMResponse(text="", finish_reason="tool_calls", tool_calls=[ToolCall(
                "submit_consult", {"reply": "ok", "intent": "palliative",
                                   "biomarker_category": ["nscl 26", "NSCL-26", "NSCL-99", "x"],
                                   "biomarker_rationale": "KRAS G12C"}, id="c")])

    turn = AgentSession(Scripted()).turn("x")
    assert turn.consult["biomarker_category"] == ["NSCL-26"]
    assert AgentSession(Scripted()).turn("y").consult["biomarker_rationale"] == "KRAS G12C"


def test_offline_mock_reports_the_category():
    turn = AgentSession(MockLLMClient()).turn(
        "右肺腺癌 cT4N3M1c2，多发骨转移，肝转移。EGFR L858R 阳性。ECOG 1。")
    assert turn.consult["biomarker_category"] == ["NSCL-21"]
    assert "生物标志物分类" in turn.reply
