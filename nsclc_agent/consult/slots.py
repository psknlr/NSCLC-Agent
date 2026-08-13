"""The consultation slot schema — what an NSCLC work-up actually needs to know.

Each :class:`Slot` is one fact the agent may ask for, annotated with the
*decision it changes*. That annotation is what turns history-taking into
value-of-information ordering rather than a questionnaire: a slot is worth
asking about exactly when its answer can still move the recommendation.

Three properties keep the ordering honest:

``base_weight``
    How much the slot can move a decision at all. Staging descriptors (without
    which nothing can be staged) sit at the top; goals-of-care and demographics
    sit at the bottom.
``stage_weights``
    The same fact is not equally decisive at every stage. PD-L1 changes the
    consolidation and first-line decisions in stage III/IV but is explicitly
    *not* decision-relevant in stage I, so it is worth ~85 there and ~10 here.
``gate``
    Some slots only become relevant once something else is known — nodal
    station mapping matters only once the case is N2, R status only after a
    resection. A gated slot is never asked before its precondition holds.

Questions are carried in English and Chinese; the consultation picks by
``lang``. The clinical content is identical in both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

#: Stage bands used for weighting. "pre" = staging not yet resolved.
STAGE_BANDS = ("pre", "0", "I", "II", "III", "IV")


def stage_band(stage_group: Optional[str]) -> str:
    """Map a computed stage group to the band used for slot weighting."""
    if not stage_group:
        return "pre"
    s = str(stage_group).upper()
    if s.startswith("OCCULT"):
        return "pre"
    if s == "0":
        return "0"
    if s.startswith("IV"):
        return "IV"
    if s.startswith("III"):
        return "III"
    if s.startswith("II"):
        return "II"
    if s.startswith("I"):
        return "I"
    return "pre"


@dataclass(frozen=True)
class Slot:
    """One fact the consultation can ask for."""

    key: str
    question_en: str
    question_zh: str
    #: the decision this fact changes — shown to the user so a question never
    #: looks arbitrary, and used in the transcript as the reason it was asked
    impact_en: str
    impact_zh: str
    category: str
    base_weight: int
    #: per-band overrides of ``base_weight``; missing bands use the base value
    stage_weights: dict[str, int] = field(default_factory=dict)
    #: only ask once this predicate over the known facts holds
    gate: Optional[Callable[[dict[str, Any]], bool]] = None
    #: slots whose presence makes this one redundant
    satisfied_by: tuple[str, ...] = ()

    def weight(self, band: str) -> int:
        return self.stage_weights.get(band, self.base_weight)

    def question(self, lang: str = "zh") -> str:
        return self.question_zh if lang == "zh" else self.question_en

    def impact(self, lang: str = "zh") -> str:
        return self.impact_zh if lang == "zh" else self.impact_en

    def is_relevant(self, known: dict[str, Any], band: str) -> bool:
        if self.key in known and known[self.key] not in (None, ""):
            return False
        if any(k in known for k in self.satisfied_by):
            return False
        if self.gate is not None and not self.gate(known):
            return False
        return self.weight(band) > 0


# --------------------------------------------------------------------------- #
# Gates                                                                       #
# --------------------------------------------------------------------------- #

def _is_n2(known: dict) -> bool:
    n = str(known.get("n_category") or "").upper()
    return n.startswith("N2")


def _node_positive_or_unknown(known: dict) -> bool:
    n = str(known.get("n_category") or "").upper()
    return (not n) or n not in ("N0",)


def _postop(known: dict) -> bool:
    return str(known.get("clinical_scenario") or "").upper().startswith("POSTOP")


def _non_squamous_or_unknown(known: dict) -> bool:
    h = str(known.get("histology") or "").lower()
    return h != "squamous"


def _metastatic(known: dict) -> bool:
    m = str(known.get("m_category") or "").upper()
    return m.startswith("M1")


def _unresectable(known: dict) -> bool:
    r = str(known.get("resectability") or "").lower()
    return r in ("unresectable", "medically_inoperable", "declined_surgery")


# --------------------------------------------------------------------------- #
# The schema                                                                  #
# --------------------------------------------------------------------------- #

SLOTS: tuple[Slot, ...] = (
    # ---------------- staging descriptors: nothing runs without these -------
    Slot(
        key="t_category",
        question_en="What is the T category (Tis, T1a/T1b/T1c, T2a/T2b, T3, "
                    "T4)? If unknown, the primary tumour's size in mm and "
                    "whether it invades the pleura, chest wall or mediastinum "
                    "is enough.",
        question_zh="原发灶的 T 分期是多少（Tis、T1a/T1b/T1c、T2a/T2b、T3、T4）？"
                    "如果不清楚，告诉我肿瘤最大径（mm）以及是否侵犯胸膜、胸壁或纵隔即可。",
        impact_en="Sets the stage group together with N and M; nothing can be "
                  "staged or routed without it.",
        impact_zh="与 N、M 共同决定分期；没有它无法分期、无法选择治疗模块。",
        category="staging",
        base_weight=100,
    ),
    Slot(
        key="n_category",
        question_en="What is the N category (N0, N1, N2a, N2b, N3)? N2a is a "
                    "single mediastinal station, N2b is multiple stations — "
                    "the distinction changes the stage group.",
        question_zh="淋巴结 N 分期是多少（N0、N1、N2a、N2b、N3）？N2a 指单站纵隔淋巴结，"
                    "N2b 指多站——这个区别会直接改变分期。",
        impact_en="Decides IIIA vs IIIB in the 9th edition and whether surgery "
                  "is on the table at all.",
        impact_zh="在第 9 版里决定 IIIA 还是 IIIB，也决定还能不能考虑手术。",
        category="staging",
        base_weight=100,
    ),
    Slot(
        key="m_category",
        question_en="Has distant metastasis been excluded (M0), or is there "
                    "M1a/M1b/M1c disease? Which imaging was used — PET-CT, "
                    "brain MRI?",
        question_zh="远处转移排除了吗（M0），还是已经有 M1a/M1b/M1c？做了哪些检查——"
                    "PET-CT、头颅 MRI？",
        impact_en="Separates curative from palliative intent — the single "
                  "largest fork in the pathway.",
        impact_zh="区分根治性与姑息性治疗——整条路径上最大的分叉。",
        category="staging",
        base_weight=100,
    ),
    Slot(
        key="n2_stations",
        question_en="Which mediastinal stations are involved (IASLC numbers, "
                    "e.g. 4R, 7)? How were they confirmed — EBUS-TBNA, "
                    "mediastinoscopy, PET only?",
        question_zh="具体哪几组纵隔淋巴结受累（IASLC 站号，如 4R、7）？是怎么确认的——"
                    "EBUS-TBNA、纵隔镜，还是只有 PET？",
        impact_en="Refines radiotherapy targets and the surgical plan, and "
                  "lets the N2a/N2b call be checked. The single- vs "
                  "multi-station decision itself is already carried by the N "
                  "descriptor, so this is refinement, not a blocker.",
        impact_zh="用于细化放疗靶区和手术方案，并核对 N2a/N2b 的判断。"
                  "单站/多站这个决策本身已经由 N 描述符承载，"
                  "所以这是补充信息，不是阻塞项。",
        category="staging",
        # Deliberately below the sufficiency threshold: this slot is only ever
        # gated on an *already known* N2a/N2b, so the decision it would inform
        # has been made. Weighting it as a blocker would stall every N2
        # consultation on detail it does not need.
        base_weight=60,
        stage_weights={"III": 65},
        gate=_is_n2,
    ),
    # ---------------- histology: gates every downstream branch --------------
    Slot(
        key="histology",
        question_en="What is the histology — adenocarcinoma, squamous cell, "
                    "adenosquamous, large cell, or NSCLC-NOS? How was it "
                    "obtained (biopsy, cytology, resection)?",
        question_zh="病理类型是什么——腺癌、鳞癌、腺鳞癌、大细胞癌，还是 NSCLC-NOS？"
                    "标本来源是活检、细胞学还是手术切除？",
        impact_en="Decides which driver panel is mandatory and which "
                  "chemotherapy backbone is safe (pemetrexed and bevacizumab "
                  "are non-squamous decisions).",
        impact_zh="决定必须做哪些驱动基因检测，以及化疗骨架是否安全"
                  "（培美曲塞、贝伐珠单抗都是非鳞癌的决定）。",
        category="pathology",
        base_weight=95,
    ),
    # ---------------- fitness ----------------------------------------------
    Slot(
        key="ecog_ps",
        question_en="What is the ECOG performance status (0-4)?",
        question_zh="ECOG 体力状态评分是多少（0-4）？",
        impact_en="Gates systemic therapy and concurrent chemoradiotherapy; "
                  "PS ≥2 changes what is offerable at every stage.",
        impact_zh="决定能否耐受全身治疗和同步放化疗；PS ≥2 会改变各期能给的方案。",
        category="fitness",
        base_weight=90,
    ),
    Slot(
        key="comorbidities",
        question_en="Any significant comorbidity — COPD, interstitial lung "
                    "disease, cardiac disease, autoimmune disease?",
        question_zh="有没有重要合并症——COPD、间质性肺病、心脏病、自身免疫病？",
        impact_en="ILD and autoimmune disease are immunotherapy and radiation "
                  "safety questions, not background detail.",
        impact_zh="间质性肺病和自身免疫病直接关系到免疫治疗与放疗的安全性，"
                  "不是背景资料。",
        category="fitness",
        base_weight=60,
        stage_weights={"III": 75, "IV": 70},
    ),
    Slot(
        key="age",
        question_en="How old is the patient, and what is their sex?",
        question_zh="患者年龄和性别？",
        impact_en="Modifies operability and regimen intensity, rarely the "
                  "pathway itself.",
        impact_zh="影响手术耐受性和方案强度，通常不改变整条路径。",
        category="demographics",
        base_weight=40,
    ),
    Slot(
        key="smoking",
        question_en="Smoking history — never, former or current, and roughly "
                    "how many pack-years?",
        question_zh="吸烟史——从不、已戒还是仍在吸，大约多少包年？",
        impact_en="Shifts the prior probability of a driver mutation; does not "
                  "replace testing.",
        impact_zh="影响驱动基因突变的先验概率，但不能替代基因检测。",
        category="demographics",
        base_weight=30,
    ),
    # ---------------- biomarkers: stage-conditional -------------------------
    Slot(
        key="egfr",
        question_en="EGFR status — mutated (which alteration: 19del, L858R, "
                    "exon 20 insertion…), wild-type, or not tested?",
        question_zh="EGFR 状态——突变（具体哪种：19 外显子缺失、L858R、20 外显子插入……）、"
                    "野生型，还是没做？",
        impact_en="Decides adjuvant osimertinib after resection, consolidation "
                  "osimertinib after chemoradiation, and first-line therapy in "
                  "stage IV. Also a reason NOT to give immunotherapy.",
        impact_zh="决定术后是否用奥希替尼辅助、放化疗后是否用奥希替尼巩固、"
                  "以及 IV 期一线方案；也是不用免疫治疗的理由。",
        category="biomarker",
        base_weight=85,
        stage_weights={"pre": 60, "0": 5, "I": 25, "II": 85, "III": 95,
                       "IV": 100},
        gate=_non_squamous_or_unknown,
    ),
    Slot(
        key="alk",
        question_en="ALK status — rearranged, negative, or not tested?",
        question_zh="ALK 状态——重排阳性、阴性，还是没做？",
        impact_en="Changes first-line therapy in stage IV and is a reason to "
                  "avoid immunotherapy; evidence after chemoradiation is thin, "
                  "which itself must be stated.",
        impact_zh="改变 IV 期一线方案，也是避免免疫治疗的理由；"
                  "放化疗后的证据薄弱，这一点本身也需要说明。",
        category="biomarker",
        base_weight=75,
        stage_weights={"pre": 50, "0": 5, "I": 20, "II": 55, "III": 90,
                       "IV": 100},
        gate=_non_squamous_or_unknown,
    ),
    Slot(
        key="other_drivers",
        question_en="Any other driver result — ROS1, BRAF V600E, KRAS G12C, "
                    "MET exon 14, RET, NTRK, HER2? Was a broad NGS panel run?",
        question_zh="还有其他驱动基因结果吗——ROS1、BRAF V600E、KRAS G12C、"
                    "MET 14 外显子跳跃、RET、NTRK、HER2？做过大 panel NGS 吗？",
        impact_en="Each has a licensed targeted option in advanced disease; a "
                  "single-gene test is not a negative panel.",
        impact_zh="晚期这些都有获批的靶向药；只测了单基因不等于 panel 阴性。",
        category="biomarker",
        base_weight=55,
        stage_weights={"pre": 40, "0": 5, "I": 10, "II": 35, "III": 65,
                       "IV": 90},
        gate=_non_squamous_or_unknown,
    ),
    Slot(
        key="pd_l1",
        question_en="PD-L1 tumour proportion score (%), and which assay?",
        question_zh="PD-L1 TPS 是多少（%）？用的哪个抗体平台？",
        impact_en="Decides immunotherapy monotherapy vs combination in stage "
                  "IV and gates adjuvant atezolizumab; explicitly NOT "
                  "decision-relevant in stage I.",
        impact_zh="决定 IV 期是免疫单药还是联合，也决定辅助阿替利珠单抗的适应证；"
                  "在 I 期明确不影响决策。",
        category="biomarker",
        base_weight=70,
        stage_weights={"pre": 45, "0": 0, "I": 10, "II": 60, "III": 85,
                       "IV": 95},
    ),
    # ---------------- stage-band specific -----------------------------------
    Slot(
        key="resectability",
        question_en="Has an MDT judged the tumour resectable, and is the "
                    "patient medically operable? What was the basis?",
        question_zh="MDT 判断肿瘤可切除吗？患者能耐受手术吗？依据是什么？",
        impact_en="The dominant fork in stage III: perioperative/adjuvant "
                  "strategy versus definitive chemoradiation.",
        impact_zh="III 期最主要的分叉：围手术期/辅助策略 还是 根治性放化疗。",
        category="pathway",
        base_weight=70,
        stage_weights={"pre": 45, "0": 10, "I": 60, "II": 85, "III": 95,
                       "IV": 20},
    ),
    Slot(
        key="pulmonary_reserve",
        question_en="Pulmonary reserve — predicted postoperative FEV1 and "
                    "DLCO (ppoFEV1 / ppoDLCO, % predicted)? Any cardiac risk "
                    "assessment?",
        question_zh="肺功能储备——预计术后 FEV1 和 DLCO（ppoFEV1 / ppoDLCO，占预计值 %）？"
                    "有没有做心脏风险评估？",
        impact_en="Separates lobectomy from sublobar resection from SBRT in "
                  "early-stage disease.",
        impact_zh="在早期病变中区分肺叶切除、亚肺叶切除和 SBRT。",
        category="fitness",
        base_weight=45,
        stage_weights={"pre": 30, "0": 55, "I": 90, "II": 80, "III": 60,
                       "IV": 5},
    ),
    Slot(
        key="ccrt_feasibility",
        question_en="Is concurrent chemoradiotherapy feasible — is the "
                    "irradiated volume tolerable (V20, mean lung dose) and the "
                    "patient fit enough?",
        question_zh="能做同步放化疗吗——照射体积是否可耐受（V20、平均肺剂量），"
                    "患者体力状态够吗？",
        impact_en="Decides concurrent versus sequential chemoradiation versus "
                  "radiotherapy alone.",
        impact_zh="决定同步放化疗、序贯放化疗还是单纯放疗。",
        category="pathway",
        base_weight=30,
        stage_weights={"pre": 15, "0": 0, "I": 10, "II": 25, "III": 85,
                       "IV": 10},
        gate=_unresectable,
    ),
    Slot(
        key="brain_imaging",
        question_en="Has the brain been imaged — MRI preferred? Any "
                    "intracranial metastasis, and is it symptomatic?",
        question_zh="做过头颅影像吗（首选 MRI）？有没有颅内转移，有没有症状？",
        impact_en="Brain metastases change local therapy, systemic agent "
                  "choice (CNS penetration) and urgency.",
        impact_zh="脑转移会改变局部治疗、全身药物选择（是否入脑）和治疗紧迫性。",
        category="staging",
        base_weight=60,
        stage_weights={"pre": 55, "0": 5, "I": 20, "II": 45, "III": 80,
                       "IV": 95},
    ),
    Slot(
        key="metastatic_burden",
        question_en="How many metastatic sites and organ systems are involved, "
                    "and are they all amenable to local therapy "
                    "(oligometastatic)?",
        question_zh="转移灶有几处、累及几个器官系统？是否都能做局部治疗（寡转移）？",
        impact_en="Single vs multiple organ systems separates M1b/M1c1 from "
                  "M1c2, and oligometastatic disease justifies local "
                  "consolidative therapy.",
        impact_zh="单器官还是多器官系统区分 M1b/M1c1 与 M1c2；"
                  "寡转移才有局部巩固治疗的指征。",
        category="staging",
        base_weight=40,
        stage_weights={"pre": 25, "0": 0, "I": 0, "II": 5, "III": 15,
                       "IV": 90},
        gate=_metastatic,
    ),
    Slot(
        key="resection_status",
        question_en="What was the resection status (R0/R1/R2), the procedure "
                    "performed, and was the nodal dissection adequate?",
        question_zh="切缘状态是 R0/R1/R2？做的是什么术式？淋巴结清扫是否充分？",
        impact_en="R1/R2 changes the postoperative plan entirely; every "
                  "adjuvant trial required a complete resection.",
        impact_zh="R1/R2 会完全改变术后方案；所有辅助治疗研究都要求完整切除。",
        category="pathology",
        base_weight=50,
        stage_weights={"pre": 30, "0": 40, "I": 70, "II": 85, "III": 85,
                       "IV": 10},
        gate=_postop,
    ),
    Slot(
        key="prior_treatment",
        question_en="What treatment has already been given — surgery, "
                    "radiation, systemic therapy — and what was the response?",
        question_zh="已经接受过哪些治疗——手术、放疗、全身治疗？疗效如何？",
        impact_en="Determines whether this is a first-line decision at all, "
                  "and what is still available.",
        impact_zh="决定这到底是不是一线决策，以及还有哪些方案可用。",
        category="history",
        base_weight=65,
    ),
    Slot(
        key="symptoms",
        question_en="What symptoms are present — cough, haemoptysis, chest "
                    "pain, dyspnoea, weight loss, bone pain, neurological "
                    "symptoms?",
        question_zh="目前有哪些症状——咳嗽、咯血、胸痛、气促、体重下降、骨痛、神经症状？",
        impact_en="Flags urgency (cord compression, SVC obstruction, "
                  "symptomatic brain metastasis) that outranks the elective "
                  "pathway.",
        impact_zh="提示需要优先处理的急症（脊髓压迫、上腔静脉综合征、"
                  "有症状的脑转移），其优先级高于择期路径。",
        category="history",
        base_weight=55,
    ),
    Slot(
        key="goals_of_care",
        question_en="What matters most to the patient — maximum disease "
                    "control, avoiding surgery, quality of life, treatment "
                    "burden? Any option they have already declined?",
        question_zh="患者最看重什么——最大程度控制疾病、避免手术、生活质量、"
                    "还是治疗负担？有没有已经明确拒绝的方案？",
        impact_en="A patient who declines surgery makes an otherwise resectable "
                  "case a definitive-radiotherapy case.",
        impact_zh="患者拒绝手术，就会把一个本可切除的病例变成根治性放疗的病例。",
        category="preference",
        base_weight=50,
    ),
)

SLOTS_BY_KEY: dict[str, Slot] = {s.key: s for s in SLOTS}

#: Slots the deterministic staging engine consumes directly.
STAGING_SLOTS = ("t_category", "n_category", "m_category")

#: Below this weight a missing fact is treated as refinement, not as something
#: worth spending a consultation round on.
SUFFICIENCY_THRESHOLD = 70


def get_slot(key: str) -> Slot:
    try:
        return SLOTS_BY_KEY[key]
    except KeyError as exc:
        raise KeyError(
            f"Unknown slot {key!r}. Known: {', '.join(sorted(SLOTS_BY_KEY))}"
        ) from exc
