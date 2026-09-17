"""Oncologic emergency screen — clause-scoped, negation-aware, escalates on doubt.

The YaoBi red-flag design transplanted to thoracic oncology: hard signals
escalate immediately to a fixed action plan; soft signals route to urgent
in-person assessment rather than being dropped; a *negated* mention closes the
question instead of re-raising it ("没有咯血" is an answer, not a hit).

Suppression is clause-scoped and needs an explicit cue — negation, third-party
("我父亲当时…"), or hypothetical ("如果出现…") — because a whole-sentence
negation match would silently clear a real signal mentioned two clauses later.
When cues conflict, the screen escalates: a false emergency costs an urgent
review; a missed cord compression costs a spinal cord.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Clause boundaries for Chinese and English clinical narrative.
_CLAUSE_SPLIT = re.compile(r"[。！？!?；;，,\n]")

#: Negation detection is REGEX-based and evaluated on the clause with the
#: matched symptom span masked out. Both properties are load-bearing:
#: substring matching read the 无 inside 双腿无力 ("bilateral leg weakness")
#: and the not inside "cannot lie flat" as denials, silently suppressing the
#: canonical Chinese cord-compression presentation. 无 therefore only counts
#: with a lookahead excluding 无力/无法/无奈 (ability-negation compounds, not
#: symptom denial), English cues carry word boundaries, and the symptom term
#: itself is removed before the clause is inspected.
_NEGATION_RE = re.compile(
    r"没有|否认|不曾|未见|未出现|没出现|不存在|无明显|无(?!力|法|奈)|"
    r"\b(?:no|not|denies|denied|without|absent)\b|negative for",
    re.IGNORECASE,
)
_THIRD_PARTY_CUES = ("父亲", "母亲", "家人", "朋友", "同事", "father", "mother", "family member")
_HYPOTHETICAL_CUES = ("如果", "万一", "要是", "担心会", "怕会", "if i", "what if", "in case")


@dataclass(frozen=True)
class EmergencyPattern:
    signal_id: str
    label: str
    #: Substrings (lowercased) any of which raises the signal within a clause.
    cues: tuple[str, ...]
    #: "hard" escalates to emergency_action_plan; "soft" → urgent assessment.
    weight: str
    action: str
    #: Broader terms recognised ONLY inside a negated clause, to close the
    #: screening question. "没有咯血" answers the hemoptysis screen even though
    #: a bare positive "咯血" would not, on its own, be a massive-hemoptysis
    #: emergency — raising and closing legitimately need different specificity.
    screen_terms: tuple[str, ...] = ()


PATTERNS: tuple[EmergencyPattern, ...] = (
    EmergencyPattern(
        "cord_compression", "脊髓压迫 / cord compression",
        ("双腿无力", "下肢无力加重", "大小便失禁", "尿潴留", "鞍区麻木", "会阴麻木",
         "saddle anesthesia", "bilateral leg weakness", "urinary retention",
         "bowel incontinence", "new leg weakness"),
        "hard",
        "立即急诊：全脊柱增强MRI（24小时内），神经外科/放疗科会诊，按当地方案启动地塞米松；"
        "Immediate ED referral: whole-spine MRI within 24h, neurosurgery/RT consult, dexamethasone per protocol.",
        screen_terms=('腿无力', '腿没力', '大小便', '会阴', '鞍区', 'leg weakness', 'bladder', 'bowel', 'saddle'),
    ),
    EmergencyPattern(
        "svc_syndrome", "上腔静脉综合征 / SVC syndrome",
        ("颜面肿胀", "面部肿胀", "颈静脉怒张", "面部发紫", "晨起脸肿", "上肢肿胀伴气促",
         "facial swelling", "facial plethora", "distended neck veins", "svc"),
        "hard",
        "急诊评估气道与静脉回流；胸部增强CT，肿瘤科+介入/放疗急会诊，评估支架/紧急放疗；"
        "Urgent airway/venous assessment, contrast CT, consider stenting or urgent RT.",
        screen_terms=('面部肿胀', '脸肿', '颈静脉', 'facial swelling', 'face swelling', 'neck vein'),
    ),
    EmergencyPattern(
        "massive_hemoptysis", "大咯血 / massive hemoptysis",
        ("大咯血", "咯血不止", "咯出大量鲜血", "满口鲜血", "massive hemoptysis",
         "coughing up large amounts of blood"),
        "hard",
        "急诊：患侧卧位保护健侧肺，气道保护，急诊支气管镜/介入栓塞评估；"
        "ED now: lateral decubitus (bleeding side down), airway protection, urgent bronchoscopy/IR embolization.",
        screen_terms=('咯血', 'hemoptysis', 'coughing up blood', 'blood in sputum'),
    ),
    EmergencyPattern(
        "airway_obstruction", "中央气道梗阻 / central airway obstruction",
        ("喘鸣", "端坐呼吸", "不能平卧伴气促", "窒息感", "stridor", "cannot lie flat",
         "orthopnea with stridor"),
        "hard",
        "急诊气道评估；介入支气管镜（支架/消融）与放疗科急会诊；"
        "Emergency airway assessment; interventional bronchoscopy and urgent RT consult.",
        screen_terms=('喘鸣', 'stridor', '端坐呼吸', 'orthopnea'),
    ),
    EmergencyPattern(
        "febrile_neutropenia", "化疗期发热 / fever on chemotherapy",
        ("化疗后发烧", "化疗期间发热", "打完化疗发烧", "fever on chemotherapy",
         "fever after chemo", "febrile neutropenia"),
        "hard",
        "按中性粒细胞减少性发热处理：1小时内急诊，血培养后经验性广谱抗生素，不得等待血象结果在家观察；"
        "Treat as febrile neutropenia: ED within 1h, cultures then empiric broad-spectrum antibiotics.",
        screen_terms=('发烧', '发热', '寒战', 'fever', 'rigors', 'chills'),
    ),
    EmergencyPattern(
        "hypercalcemia", "高钙危象线索 / symptomatic hypercalcemia",
        ("嗜睡伴多尿", "意识模糊加重", "极度口渴伴便秘", "confusion with polyuria",
         "drowsy and dehydrated"),
        "soft",
        "急查血钙/白蛋白/肾功能；补液为先，双膦酸盐/地舒单抗按结果；"
        "Urgent corrected calcium and renal panel; fluids first, antiresorptives per result.",
    ),
    EmergencyPattern(
        "pe_suspect", "肺栓塞线索 / suspected PE",
        ("突发胸痛伴气促", "突然喘不上气", "单侧腿肿伴胸痛", "sudden pleuritic chest pain",
         "sudden shortness of breath", "unilateral leg swelling"),
        "soft",
        "急诊评估：Wells评分、D-二聚体/CTPA；肿瘤患者为高危人群；"
        "Urgent assessment: Wells score, D-dimer/CTPA — cancer patients are high-risk.",
    ),
    EmergencyPattern(
        "brain_mets_signs", "颅内转移体征 / new CNS signs",
        ("新发剧烈头痛伴呕吐", "抽搐", "癫痫发作", "一侧肢体突然无力", "口齿不清",
         "new seizure", "worst headache with vomiting", "sudden one-sided weakness"),
        "soft",
        "急诊头颅增强MRI；按占位效应决定地塞米松与神外/放疗会诊；"
        "Urgent contrast brain MRI; dexamethasone and neurosurgery/RT consult per mass effect.",
        screen_terms=('抽搐', '癫痫', '剧烈头痛', 'seizure', 'severe headache'),
    ),
)


@dataclass
class ScreenResult:
    hard_hits: list[dict] = field(default_factory=list)
    soft_hits: list[dict] = field(default_factory=list)
    #: Signals explicitly negated by the narrative — closed questions.
    negated: list[str] = field(default_factory=list)

    @property
    def emergency(self) -> bool:
        return bool(self.hard_hits)

    def to_dict(self) -> dict:
        return {
            "hard_hits": self.hard_hits,
            "soft_hits": self.soft_hits,
            "negated": self.negated,
            "emergency": self.emergency,
        }


def _clause_suppressed(clause: str, matched_terms: tuple[str, ...] = ()) -> str | None:
    """Return the suppression kind for a clause, or None if it stands.

    ``matched_terms`` are the symptom substrings that raised/screened the
    pattern; they are masked out before negation is evaluated so a negation
    character *inside the symptom itself* (双腿无力) can never suppress it.
    Suppression is per-clause so "以前没有咯血，但今天咯了一大口" keeps its
    second clause.
    """
    lowered = clause.lower()
    if any(cue in lowered for cue in _THIRD_PARTY_CUES):
        return "third_party"
    if any(cue in lowered for cue in _HYPOTHETICAL_CUES):
        return "hypothetical"
    masked = lowered
    for term in matched_terms:
        masked = masked.replace(term.lower(), "□")
    if _NEGATION_RE.search(masked):
        return "negation"
    return None


def screen(narrative: str) -> ScreenResult:
    """Screen a clinical narrative for oncologic emergencies."""
    result = ScreenResult()
    text = str(narrative or "")
    seen_hits: set[str] = set()
    seen_negated: set[str] = set()
    for clause in _CLAUSE_SPLIT.split(text):
        clause = clause.strip()
        if not clause:
            continue
        lowered = clause.lower()
        for pattern in PATTERNS:
            matched = tuple(
                term for term in (*pattern.cues, *pattern.screen_terms)
                if term in lowered)
            raised = any(cue in lowered for cue in pattern.cues)
            if not matched:
                continue
            suppression = _clause_suppressed(clause, matched)
            if suppression == "negation":
                # A negated mention *answers* the screening question.
                if pattern.signal_id not in seen_negated:
                    seen_negated.add(pattern.signal_id)
                    result.negated.append(pattern.signal_id)
                continue
            if suppression in ("third_party", "hypothetical"):
                continue
            if not raised:
                continue  # a broad screen term alone never raises a signal
            if pattern.signal_id in seen_hits:
                continue
            seen_hits.add(pattern.signal_id)
            hit = {
                "signal_id": pattern.signal_id,
                "label": pattern.label,
                "clause": clause[:120],
                "action": pattern.action,
            }
            (result.hard_hits if pattern.weight == "hard" else result.soft_hits).append(hit)
    # A signal both negated and later asserted stands: present-tense assertion
    # wins, so drop it from the negated list.
    result.negated = [s for s in result.negated if s not in seen_hits]
    return result


def action_plan(result: ScreenResult) -> dict:
    """The fixed emergency action plan. Never rephrased by a model."""
    return {
        "risk_judgement": "oncologic_emergency",
        "signals": [h["label"] for h in result.hard_hits],
        "immediate_actions": [h["action"] for h in result.hard_hits],
        "do_not": [
            "不要等待门诊常规排队 / Do not wait for a routine clinic slot",
            "不要在家观察过夜 / Do not observe overnight at home",
            "不要在急诊评估前开始新的全身抗肿瘤治疗 / Do not start new systemic therapy before emergency assessment",
        ],
        "escalate_if": [
            "意识状态变化 / any change in consciousness",
            "呼吸困难加重 / worsening dyspnea",
            "新发瘫痪或大小便失禁 / new paralysis or incontinence",
        ],
        "note": "此行动计划由确定性规则生成，措辞为安全关键内容，不经模型改写。",
    }
