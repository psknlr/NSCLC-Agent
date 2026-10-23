"""The polish may reword a governed reply, never change what it says
(v1.4.1 red-team: a rewrite that swapped the drug, changed the stage, added
an efficacy figure or a trial, told the patient to skip the work-up or
dropped the disclaimer was accepted — only a new dose figure was caught)."""

import pytest

from nsclc_agent.agentic import provenance
from nsclc_agent.conversation import ConsultationSession, polish_reply
from nsclc_agent.llm.base import LLMResponse
from nsclc_agent.safety.rewrite import protected_lines, rewrite_changes

IVB = ("58-year-old man, lung adenocarcinoma cT2bN3M1c2 with bone and adrenal metastases, "
       "ECOG 0. Brain MRI clear. No hemoptysis, no headache, no leg weakness. PD-L1 TPS 70%. "
       "EGFR, ALK, ROS1, RET, MET, BRAF, KRAS, HER2 and NTRK all negative.")
RESECTED = ("62-year-old never-smoker. Lobectomy performed for a right upper lobe adenocarcinoma; "
            "pathology pT2aN1M0, R0 resection. EGFR exon 19 deletion present. ALK negative. "
            "PD-L1 TPS 30%.")
PREGNANT = ("34-year-old woman, lung adenocarcinoma cT3N2bM1c2 with liver and bone metastases, "
            "ECOG 1. She is currently 20 weeks pregnant. Brain MRI negative. No hemoptysis, no "
            "headache, no limb weakness. PD-L1 TPS 80%. EGFR, ALK, ROS1, RET, MET, BRAF, KRAS, "
            "HER2, NTRK: negative.")
WORKUP = "72岁女性，右肺腺癌，cT2aN2M0。ECOG 1。"
MULTIPLE = ("66岁男性，肺腺癌 cT3N2bM1c2，多发骨转移和多发肝转移，ECOG 1。脑MRI阴性。无咯血、无头痛、"
            "无肢体无力。PD-L1 TPS 10%。EGFR、ALK、ROS1、RET、MET、BRAF、KRAS、HER2、NTRK均阴性。")


def _turn(message, role):
    return ConsultationSession(role=role).turn(message)


def _changed(result, edit):
    return rewrite_changes(result.reply, edit(result.reply), keep=result.state.open_questions)


def _replace(old, new):
    def edit(text):
        assert old in text, old
        return text.replace(old, new, 1)
    return edit


def _append(extra):
    return lambda text: text + "\n" + extra


def _drop(needle):
    def edit(text):
        kept = [line for line in text.splitlines() if needle not in line]
        assert len(kept) < len(text.splitlines()), needle
        return "\n".join(kept)
    return edit


@pytest.mark.parametrize("message,role,edit,reason", [
    (IVB, "oncologist", _append("可联合贝伐珠单抗。"), "added a drug: bevacizumab"),
    (IVB, "oncologist", _replace("Pembrolizumab", "Cemiplimab"), "added a drug: cemiplimab"),
    (RESECTED, "patient", _replace("adjuvant osimertinib", "adjuvant gefitinib"),
     "added a drug: gefitinib"),
    (RESECTED, "patient", _append("DFS HR 0.17."), "added a number: 0.17"),
    (IVB, "oncologist", _append("中位PFS约10.3个月。"), "added a quantity"),
    (RESECTED, "oncologist", _append("参见 NeoADAURA 研究。"), "added a reference"),
    (IVB, "oncologist", _replace("KEYNOTE-024", "KEYNOTE-042"), "a reference"),
    (RESECTED, "patient", _replace("IIB", "IIIA"), "the stage"),
    (IVB, "oncologist", _replace("M1c2", "M1c1"), "a TNM descriptor"),
    (IVB, "oncologist", _replace("TPS≥50%", "TPS<50%"), "added a threshold"),
    (IVB, "oncologist", _replace("driver-negative", "driver-positive"), "a finding"),
    (MULTIPLE, "oncologist", _replace("肝（多发）", "肝（单发）"), "a finding"),
    (MULTIPLE, "oncologist", _replace("pemetrexed-platinum", "docetaxel"), "a drug"),
    (PREGNANT, "oncologist", _drop("PREGNANCY_CAUTION"), "protected line"),
    (PREGNANT, "patient", _drop("产科"), "protected line"),
    (IVB, "patient", _replace("有没有活动性自身免疫病", "是否有自身免疫病"), "protected line"),
    (WORKUP, "oncologist", _drop("ALK 状态"), "protected line"),
    (WORKUP, "oncologist", _replace("【未放行 / NOT RELEASED: needs_staging_workup】", ""),
     "NOT-RELEASED"),
    (IVB, "oncologist", _drop("预后（人群队列"), "protected line"),
    (IVB, "patient", _append("可以先观察，暂缓用药。"), "observation"),
    (RESECTED, "patient", _append("术后不需要再做辅助治疗。"), "directive (omit)"),
    (IVB, "patient", _append("Please don't worry: this is very treatable."), "directive"),
    (IVB, "patient", _append("坚持用药，多数人可以长期生存。"), "directive (prognosis)"),
    (RESECTED, "patient", _append("可以考虑减量服用。"), "directive (dosing)"),
    (IVB, "oncologist", _append("Switch to chemotherapy if the response is poor."),
     "directive (switch)"),
    (IVB, "patient", _append("也可以做手术。"), "added a treatment modality: surgery"),
    (RESECTED, "patient", _append("。".join(["患者教育材料请仔细阅读并与家人分享"] * 20)), "grew"),
])
def test_a_rewrite_that_changes_content_is_caught(message, role, edit, reason):
    found = _changed(_turn(message, role), edit)
    assert found and reason in found


FAITHFUL_PATIENT = """根据确定性分期引擎（AJCC/UICC 第9版），您的分期为 IIB。
这一分期的治疗路径取决于驱动基因状态：EGFR ex19del/L858R 可切除人群的标准做法，是手术后接受奥希替尼辅助靶向治疗（± chemo），依据为 ADAURA 研究；围手术期不使用 IO。
为了继续为您评估，请回答以下问题：
1. 最近有没有双腿无力加重、大小便控制不住，或会阴部麻木？
2. 有没有大量咯血、平卧时喘不上气、喉咙里有喘鸣声，或面部脖子肿胀发紫？
3. 如果正在化疗：有没有发烧（≥38℃）或寒战？
4. 做过覆盖 ROS1/BRAF/MET/RET/KRAS G12C/HER2 的扩展 NGS 吗？
[treatment_recommendation] 分期内的循证治疗建议 / stage-appropriate recommendation
（教学/研究用途；任何治疗决定请与您的主治团队确认。）
感谢您的耐心，我们会继续协助您。"""


def _reverse(text):
    return "\n".join(reversed(text.splitlines()))


@pytest.mark.parametrize("message,role,edit", [
    (IVB, "oncologist", lambda text: text),
    (IVB, "oncologist", _reverse),
    (IVB, "patient", lambda text: " ".join(text.splitlines())),
    (PREGNANT, "patient", _reverse),
    (WORKUP, "oncologist", _replace("为了继续，请回答：", "请先回答以下几个问题：")),
    (IVB, "oncologist", _replace("Pembrolizumab monotherapy", "帕博利珠单抗单药")),
    (IVB, "patient", _replace("Stage IVB: biomarker-directed systemic therapy.",
                              "Biomarker-directed systemic therapy is the pathway.")),
    (RESECTED, "patient", lambda text: FAITHFUL_PATIENT),
])
def test_a_faithful_rewrite_is_kept(message, role, edit):
    assert _changed(_turn(message, role), edit) is None


def test_protected_lines_are_the_safety_lines_and_questions():
    result = _turn(PREGNANT, "oncologist")
    lines = protected_lines(result.reply, result.state.open_questions)
    assert any("PREGNANCY_CAUTION" in line for line in lines)
    assert any(line.startswith("[treatment_recommendation]") for line in lines)
    assert all(not line.startswith("•") for line in lines)     # options may be reworded


@pytest.mark.parametrize("text,expected", [
    ("Pembrolizumab, then 帕博利珠单抗 again", {"pembrolizumab": 2}),
    ("Keytruda", {"pembrolizumab": 1}),
    ("也可考虑纳武利尤单抗联合伊匹木单抗", {"nivolumab": 1, "ipilimumab": 1}),
    ("Enhertu", {"trastuzumab deruxtecan": 1}),
    ("含铂双药化疗，when combined with radiotherapy", {}),
    ("zongertinib", {"zongertinib": 1}),
])
def test_drug_counts_fold_names_and_brands(text, expected):
    assert dict(provenance.drugs(text)) == expected


class _Rewrite:
    available, name, model = True, "rewrite", "rewrite-1"

    def __init__(self, text):
        self.text = text

    def chat(self, messages, **_):
        return LLMResponse(text=self.text, finish_reason="stop", model=self.model)


def test_polish_reply_keeps_the_deterministic_text_and_says_why():
    result = _turn(IVB, "patient")
    text, polished = polish_reply(_Rewrite(result.reply.replace("IVB", "IVA")), result.reply,
                                  state=result.state)
    assert (text, polished) == (result.reply, False)
    assert any(w.startswith("reply polish discarded: added the stage: IVA")
               for w in result.state.warnings)


def test_polish_reply_accepts_a_faithful_rewrite():
    result = _turn(RESECTED, "patient")
    text, polished = polish_reply(_Rewrite(FAITHFUL_PATIENT), result.reply, state=result.state)
    assert polished and text == FAITHFUL_PATIENT
