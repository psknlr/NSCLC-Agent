"""Enquiry axes for NSCLC: what must be known before what may be decided.

An *axis* is one line of clinical enquiry — "M status resolved", "EGFR/ALK
tested", "ECOG performance status". Each declares the facts that close it, the
tier that decides what its openness blocks, and a bank of professionally-worded
probes. This is the value-of-information layer v0.1 sketched as a three-line
``_next_step_hint``: axes tie every gap to the test that resolves it and to the
decision that hangs on it.

Division of labour (the YaoBi rule, unchanged):

============================  ==============================================
Decided by rule (here)        Decided by the model
============================  ==============================================
which axes are *required*     what to ask, in what words, in what order
which axes are *relevant*     how deeply to probe one axis
that a RED_FLAG axis is       which probe to reuse verbatim and which to
never skippable               rewrite for this particular case
============================  ==============================================

Tiers:

``RED_FLAG``  — oncologic-emergency screening; unanswered blocks any release
                beyond an emergency plan.
``STAGING``   — descriptor-resolving workup; unanswered → needs_staging_workup
                and blocks treatment recommendations.
``BIOMARKER`` — Tier-A biomarkers; unanswered blocks systemic-therapy plans.
``FITNESS``   — PS/organ function; needed before aggressive-therapy plans.
``CONTEXT``   — goals, history; shapes the plan, never gates it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

TIERS = ("RED_FLAG", "STAGING", "BIOMARKER", "FITNESS", "CONTEXT")

Predicate = Callable[[dict[str, Any], str], bool]


@dataclass(frozen=True)
class Axis:
    axis_id: str
    label: str
    tier: str
    #: Fact keys that, once present (non-None), close this axis. ``all`` of
    #: them: an axis listing two facts needs both.
    closes: tuple[str, ...] = ()
    #: Professionally-worded probes. First entry is the plainest phrasing.
    probes: tuple[str, ...] = ()
    #: What the answer changes — fed to the model so it can explain itself,
    #: and shown in the audit trail.
    rationale: str = ""
    #: The test/action that resolves this axis (the VOI payload).
    resolving_test: str = ""
    applies_when: Optional[Predicate] = None

    def relevant(self, facts: dict[str, Any], complaint: str) -> bool:
        if self.applies_when is None:
            return True
        try:
            return bool(self.applies_when(facts, complaint or ""))
        except Exception:  # noqa: BLE001 - a broken predicate must not hide an axis
            return True

    def satisfied(self, facts: dict[str, Any]) -> bool:
        if not self.closes:
            return False
        flat = _flatten(facts)
        return all(flat.get(key) is not None for key in self.closes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis_id": self.axis_id, "label": self.label, "tier": self.tier,
            "closes": list(self.closes), "probes": list(self.probes),
            "rationale": self.rationale, "resolving_test": self.resolving_test,
        }


def _flatten(facts: dict[str, Any]) -> dict[str, Any]:
    """Flatten one level of the nested fact blocks axes reference."""
    flat = dict(facts)
    for block in ("driver_mutations", "pd_l1", "workup", "organ_function",
                  "comorbidities", "tnm"):
        nested = facts.get(block)
        if isinstance(nested, dict):
            for key, value in nested.items():
                flat.setdefault(f"{block}.{key}", value)
    return flat


# ------------------------------------------------------------------ predicates

def _driver_unknown(gene: str) -> Predicate:
    def check(facts: dict[str, Any], _c: str) -> bool:
        value = str((facts.get("driver_mutations") or {}).get(gene) or "").lower()
        return value in ("", "not_tested", "unknown", "pending")
    return check


def _nonsquamous(facts: dict[str, Any], _c: str) -> bool:
    hist = str(facts.get("histologic_category") or "").lower()
    return not hist or "squamous" not in hist or "adeno" in hist


def _n2_possible(facts: dict[str, Any], complaint: str) -> bool:
    n = str(facts.get("tnm.n") or facts.get("n") or "").upper()
    text = complaint.lower()
    return n.startswith("N2") or n in ("", "NX") or "mediastinal" in text or "纵隔" in text


def _surgery_plausible(facts: dict[str, Any], _c: str) -> bool:
    stage = str(facts.get("stage_group") or "")
    return stage in ("", "IA1", "IA2", "IA3", "IB", "IIA", "IIB", "IIIA", "IIIB")


AXES: tuple[Axis, ...] = (
    # ------------------------------------------------------------ RED_FLAG tier
    Axis(
        "onc_emergency_neuro", "脊髓压迫/颅内征象 · cord compression / CNS signs",
        "RED_FLAG",
        closes=("emergency_neuro_screen",),
        probes=(
            "最近有没有双腿无力加重、大小便控制不住，或会阴部麻木？",
            "有没有新发的剧烈头痛、呕吐、抽搐或一侧肢体突然无力？",
            "Any new leg weakness, bladder/bowel changes, saddle numbness, "
            "seizures or sudden one-sided weakness?",
        ),
        rationale="脊髓压迫与症状性脑转移需数小时内处理，漏诊代价不可逆。",
        resolving_test="whole-spine / brain contrast MRI within 24h if positive",
    ),
    Axis(
        "onc_emergency_airway", "气道/大血管急症 · airway & SVC screen", "RED_FLAG",
        closes=("emergency_airway_screen",),
        probes=(
            "有没有大量咯血、平卧时喘不上气、喉咙里有喘鸣声，或面部脖子肿胀发紫？",
            "Any massive hemoptysis, stridor, inability to lie flat, or "
            "face/neck swelling with distended veins?",
        ),
        rationale="大咯血、中央气道梗阻与上腔静脉综合征是急诊介入指征。",
        resolving_test="ED assessment; urgent bronchoscopy / IR / RT consult if positive",
    ),
    Axis(
        "onc_emergency_infection", "治疗期发热 · fever on treatment", "RED_FLAG",
        closes=("emergency_fever_screen",),
        probes=(
            "如果正在化疗：有没有发烧（≥38℃）或寒战？",
            "If on chemotherapy: any fever (≥38°C) or rigors?",
        ),
        rationale="化疗期发热按中性粒细胞减少性发热处理，1小时内需要抗生素。",
        resolving_test="CBC + cultures + empiric antibiotics within 1h if positive",
    ),
    # ------------------------------------------------------------- STAGING tier
    Axis(
        "t_resolution", "T 描述符已解决 · T descriptor resolved", "STAGING",
        closes=("tnm.t",),
        probes=(
            "病灶最大径是多少毫米？是否侵犯胸壁、纵隔或大血管（薄层增强CT测量）？",
            "What is the lesion's greatest dimension on thin-slice CT, and is "
            "there chest-wall/mediastinal invasion?",
        ),
        rationale="T 亚级（T1a/b/c、T2a/b）直接改变 IA1–IIA 的分期与术式讨论。",
        resolving_test="thin-slice contrast CT; bronchoscopy for central lesions",
    ),
    Axis(
        "n_resolution", "N 描述符已解决 · N descriptor resolved", "STAGING",
        closes=("tnm.n",),
        probes=(
            "PET-CT 上有哪些可疑淋巴结站？做过 EBUS-TBNA 吗，单站还是多站阳性？",
            "Which nodal stations are suspicious on PET-CT, and has EBUS-TBNA "
            "confirmed single- vs multi-station involvement?",
        ),
        rationale="第9版将 N2 拆分为 N2a/N2b：单站与多站直接移动 IIB↔IIIA↔IIIB 并改变可切除性讨论。",
        resolving_test="PET-CT then EBUS-TBNA station mapping",
    ),
    Axis(
        "m_resolution", "M 描述符已解决 · M descriptor resolved", "STAGING",
        closes=("tnm.m",),
        probes=(
            "PET-CT 和头颅增强 MRI 做了吗？结果是否除外远处转移？",
            "Have PET-CT and contrast brain MRI been completed, and do they "
            "exclude distant metastasis?",
        ),
        rationale="未完成转移灶检查绝不能默认 M0——那会把未分期病人送进根治通路。",
        resolving_test="PET-CT + contrast brain MRI",
    ),
    Axis(
        "tissue_diagnosis", "组织学确诊 · tissue diagnosis", "STAGING",
        closes=("histologic_category",),
        probes=(
            "有病理结果吗？腺癌、鳞癌还是其他？取材方式是什么？",
            "Is there a tissue diagnosis (adenocarcinoma / squamous / other), "
            "and how was it obtained?",
        ),
        rationale="组织学决定生物标志物策略与化疗骨架（培美曲塞禁用于鳞癌）。",
        resolving_test="biopsy (bronchoscopic / CT-guided / EBUS) with histology",
    ),
    # ------------------------------------------------------------ BIOMARKER tier
    Axis(
        "egfr_status", "EGFR 状态 · EGFR status", "BIOMARKER",
        closes=("driver_mutations.egfr",),
        probes=(
            "EGFR 检测做了吗？结果是 19del、L858R、其他突变还是阴性？",
            "Has EGFR testing been done — ex19del, L858R, other, or negative?",
        ),
        rationale="EGFR 状态翻转辅助（ADAURA）、巩固（LAURA vs PACIFIC）与一线（FLAURA）三个决策点，"
                  "并排除围术期免疫治疗。",
        resolving_test="tissue NGS or validated PCR; plasma ctDNA if tissue-poor",
        applies_when=_nonsquamous,
    ),
    Axis(
        "alk_status", "ALK 状态 · ALK status", "BIOMARKER",
        closes=("driver_mutations.alk",),
        probes=("ALK 融合检测做了吗？结果如何？",
                "Has ALK testing been done, and what is the result?"),
        rationale="ALK 阳性改变辅助（ALINA）与一线（CROWN）方案，并排除围术期免疫治疗。",
        resolving_test="IHC/FISH or NGS",
        applies_when=_nonsquamous,
    ),
    Axis(
        "pdl1_status", "PD-L1 表达 · PD-L1 expression", "BIOMARKER",
        closes=("pd_l1.tps",),
        probes=("PD-L1 TPS 是多少？用的哪个抗体平台？",
                "What is the PD-L1 TPS, and on which validated assay?"),
        rationale="TPS≥50% 解锁帕博利珠单抗单药；TC≥1% 决定辅助阿替利珠单抗标签。",
        resolving_test="validated PD-L1 IHC (22C3/SP263…)",
    ),
    Axis(
        "broader_ngs", "扩展 NGS · broader NGS panel", "BIOMARKER",
        closes=("ngs_done",),
        probes=(
            "做过覆盖 ROS1/BRAF/MET/RET/KRAS G12C/HER2 的扩展 NGS 吗？",
            "Has broader NGS (ROS1, BRAF, MET ex14, RET, KRAS G12C, HER2) been done?",
        ),
        rationale="IV 期非鳞癌一线前应完成扩展驱动基因谱；Tier-B 缺失不阻断但要标记。",
        resolving_test="comprehensive NGS panel",
        applies_when=_nonsquamous,
    ),
    # -------------------------------------------------------------- FITNESS tier
    Axis(
        "ecog_ps", "体能状态 · ECOG performance status", "FITNESS",
        closes=("ecog_ps",),
        probes=(
            "日常活动怎么样——能正常工作、能自理但需要休息、还是半天以上卧床？",
            "How is daily function — working normally, self-caring with rest, "
            "or in bed more than half the day?",
        ),
        rationale="PS 是同步放化疗、围术期治疗与双药化疗的准入门槛（试验多要求 0–1）。",
        resolving_test="ECOG assessment at visit",
    ),
    Axis(
        "pulmonary_reserve", "肺功能储备 · pulmonary reserve", "FITNESS",
        closes=("organ_function.ppo_fev1_pct",),
        probes=(
            "肺功能做了吗？预计术后 FEV1 和 DLCO 百分比是多少？",
            "Are PFTs done — what are the ppoFEV1 and ppoDLCO percentages?",
        ),
        rationale="ppoFEV1/ppoDLCO 决定可手术性与放疗野的耐受。",
        resolving_test="PFTs with ppoFEV1/ppoDLCO calculation",
        applies_when=_surgery_plausible,
    ),
    Axis(
        "comorbidity_io_gate", "免疫治疗合并症 · ICI comorbidity screen", "FITNESS",
        closes=("comorbidities.active_autoimmune", "comorbidities.ild"),
        probes=(
            "有没有活动性自身免疫病（如系统性红斑狼疮、类风湿）或间质性肺病史？",
            "Any active autoimmune disease or history of interstitial lung disease?",
        ),
        rationale="活动性自身免疫病与 ILD 改变检查点抑制剂的风险收益。",
        resolving_test="history + autoimmune serology / HRCT where indicated",
    ),
    Axis(
        "medications", "现用药物 · current medications", "FITNESS",
        closes=("medications",),
        probes=(
            "目前长期服用哪些药物（包括抗凝药、抗癫痫药、保健品）？",
            "What regular medications (anticoagulants, antiepileptics, "
            "supplements) is the patient taking?",
        ),
        rationale="CYP3A4 诱导剂、NSAIDs、QT 药物与 TKI/培美曲塞存在需处理的相互作用。",
        resolving_test="medication reconciliation",
    ),
    # -------------------------------------------------------------- CONTEXT tier
    Axis(
        "smoking_history", "吸烟史 · smoking history", "CONTEXT",
        closes=("smoking_history",),
        probes=("吸烟情况如何——从不、已戒还是在吸？累计多少包年？",
                "Smoking status — never/former/current, and how many pack-years?"),
        rationale="影响驱动基因概率先验、围手术期风险与戒烟干预。",
        resolving_test="history",
    ),
    Axis(
        "weight_loss", "体重下降 · weight loss", "CONTEXT",
        closes=("weight_loss",),
        probes=("近半年体重有没有不明原因下降？下降了多少公斤？",
                "Any unexplained weight loss in the past 6 months, and how much?"),
        rationale="显著体重下降改变预后评估与治疗强度选择。",
        resolving_test="history",
    ),
    Axis(
        "goals_of_care", "治疗目标 · goals of care", "CONTEXT",
        closes=("goals_of_care",),
        probes=(
            "对治疗强度和生活质量，患者本人最看重什么？",
            "What matters most to the patient about treatment intensity and "
            "quality of life?",
        ),
        rationale="IV期与高龄患者的方案选择必须对齐患者目标。",
        resolving_test="goals-of-care conversation",
    ),
)

AXES_BY_ID: dict[str, Axis] = {axis.axis_id: axis for axis in AXES}


# ------------------------------------------------------------------- coverage

def required_axes(
    facts: dict[str, Any],
    complaint: str,
    *,
    prescriptive: bool = False,
) -> list[Axis]:
    """The axes that are *required* to be closed, by rule.

    RED_FLAG axes are always required. STAGING axes are required before any
    treatment recommendation. BIOMARKER axes are required when a systemic-
    therapy decision is on the table (``prescriptive``) — with the histology
    carve-out for pure squamous disease handled by each axis's predicate.
    """
    required: list[Axis] = []
    for axis in AXES:
        if not axis.relevant(facts, complaint):
            continue
        if axis.tier == "RED_FLAG":
            required.append(axis)
        elif axis.tier == "STAGING":
            required.append(axis)
        elif axis.tier == "BIOMARKER" and prescriptive:
            if axis.axis_id in ("egfr_status", "alk_status", "pdl1_status"):
                required.append(axis)
    return required


def required_open_axes(
    facts: dict[str, Any],
    complaint: str,
    *,
    prescriptive: bool = False,
) -> list[Axis]:
    return [
        axis for axis in required_axes(facts, complaint, prescriptive=prescriptive)
        if not axis.satisfied(facts)
    ]


def coverage(facts: dict[str, Any], complaint: str, *, prescriptive: bool = False) -> dict[str, Any]:
    answered: list[str] = []
    open_axes: dict[str, dict[str, Any]] = {}
    for axis in AXES:
        if not axis.relevant(facts, complaint):
            continue
        if axis.satisfied(facts):
            answered.append(axis.axis_id)
        else:
            open_axes[axis.axis_id] = {
                "label": axis.label, "tier": axis.tier,
                "resolving_test": axis.resolving_test,
            }
    required_open = [a.axis_id for a in required_open_axes(
        facts, complaint, prescriptive=prescriptive)]
    return {
        "answered": answered,
        "open": open_axes,
        "required_open": required_open,
        "red_flag_open": [
            a for a in required_open if AXES_BY_ID[a].tier == "RED_FLAG"
        ],
    }


@dataclass
class ProbePlan:
    axis_ids: list[str] = field(default_factory=list)
    required_open: list[str] = field(default_factory=list)
    suggested_probes: dict[str, list[str]] = field(default_factory=dict)


_TIER_ORDER = {tier: index for index, tier in enumerate(TIERS)}


def plan_next(
    facts: dict[str, Any],
    complaint: str,
    *,
    prescriptive: bool = False,
    limit: int = 4,
) -> ProbePlan:
    """Deterministic pick of the next axes to close, most consequential first."""
    plan = ProbePlan()
    open_axes = [
        axis for axis in AXES
        if axis.relevant(facts, complaint) and not axis.satisfied(facts)
    ]
    open_axes.sort(key=lambda axis: _TIER_ORDER.get(axis.tier, 99))
    required = {a.axis_id for a in required_open_axes(
        facts, complaint, prescriptive=prescriptive)}
    plan.required_open = [a.axis_id for a in open_axes if a.axis_id in required]
    for axis in open_axes:
        if len(plan.axis_ids) >= limit:
            break
        plan.axis_ids.append(axis.axis_id)
        plan.suggested_probes[axis.axis_id] = list(axis.probes)
    return plan


def workup_plan(facts: dict[str, Any], complaint: str) -> list[dict[str, str]]:
    """The VOI deliverable: each open STAGING/BIOMARKER axis with its test."""
    out: list[dict[str, str]] = []
    for axis in AXES:
        if axis.tier not in ("STAGING", "BIOMARKER"):
            continue
        if not axis.relevant(facts, complaint) or axis.satisfied(facts):
            continue
        out.append({
            "axis_id": axis.axis_id,
            "gap": axis.label,
            "test": axis.resolving_test,
            "why": axis.rationale,
        })
    return out
