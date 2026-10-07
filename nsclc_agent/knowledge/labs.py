"""Laboratory values, organ function and organ damage (v1.4.0).

One schema for everything the body-fit checks read, shared by the chat
extractor, ``sanitize_fact_payload``, the structured form and the organ
gates:

``organ_function``
    ``hematologic`` {wbc, anc, plt (×10⁹/L), hb (g/L)}
    ``hepatic``     {alt_uln, ast_uln, bilirubin_uln (×ULN), alt_u_l, ast_u_l (U/L),
                     bilirubin_umol_l, albumin_g_l, child_pugh}
    ``renal``       {crcl_ml_min, egfr_ml_min, creatinine_umol_l}
    ``cardiac``     {lvef_pct, nyha}
    ``pulmonary``   {fev1_pct, dlco_pct} (% predicted); ``ppo_fev1_pct`` /
                    ``ppo_dlco_pct`` stay flat (the resection work-up axis)
    ``electrolytes`` {calcium_mmol_l (corrected), sodium_mmol_l, potassium_mmol_l}
plus the top-level ``weight_kg`` / ``height_cm`` and the comorbidity flags
in ``COMORBIDITIES``.

Values are stored in one unit per field (the narrative's mg/dL, g/dL and
/µL forms are converted on the way in) and range-checked; a value outside
its plausible range is refused, never clipped. Creatinine clearance is
never invented: ``crcl_estimate`` applies Cockcroft–Gault only when age,
weight and creatinine are all on record, and says so.
"""

from __future__ import annotations

import re
from typing import Any, Optional

#: block → field → (min, max, unit, label_zh)
ORGAN_FIELDS: dict[str, dict[str, tuple[float, float, str, str]]] = {
    "hematologic": {
        "wbc": (0, 500, "×10⁹/L", "白细胞"),
        "anc": (0, 100, "×10⁹/L", "中性粒细胞"),
        "hb": (20, 250, "g/L", "血红蛋白"),
        "plt": (0, 2000, "×10⁹/L", "血小板"),
    },
    "hepatic": {
        "alt_uln": (0, 100, "×ULN", "ALT"),
        "ast_uln": (0, 100, "×ULN", "AST"),
        "alt_u_l": (0, 10000, "U/L", "ALT"),
        "ast_u_l": (0, 10000, "U/L", "AST"),
        "bilirubin_uln": (0, 50, "×ULN", "总胆红素"),
        "bilirubin_umol_l": (0, 1000, "µmol/L", "总胆红素"),
        "albumin_g_l": (5, 70, "g/L", "白蛋白"),
    },
    "renal": {
        "crcl_ml_min": (0, 250, "mL/min", "肌酐清除率"),
        "egfr_ml_min": (0, 250, "mL/min/1.73m²", "eGFR"),
        "creatinine_umol_l": (10, 2000, "µmol/L", "血肌酐"),
    },
    "cardiac": {
        "lvef_pct": (5, 90, "%", "LVEF"),
        "nyha": (1, 4, "", "NYHA 分级"),
    },
    "pulmonary": {
        "fev1_pct": (5, 150, "% pred", "FEV1"),
        "dlco_pct": (5, 150, "% pred", "DLCO"),
    },
    "electrolytes": {
        "calcium_mmol_l": (1.0, 5.0, "mmol/L", "血钙（校正）"),
        "sodium_mmol_l": (100, 180, "mmol/L", "血钠"),
        "potassium_mmol_l": (1.5, 9.0, "mmol/L", "血钾"),
    },
}
#: Flat organ_function keys kept from earlier versions.
FLAT_ORGAN_FIELDS = {"ppo_fev1_pct": (5, 150), "ppo_dlco_pct": (5, 150)}

#: Comorbidity / organ-damage flags (true / false; neuropathy may be a grade).
COMORBIDITIES: dict[str, str] = {
    "ild": "间质性肺病", "active_autoimmune": "活动性自身免疫病",
    "autoimmune_disease": "自身免疫病史", "heart_failure": "心力衰竭",
    "coronary_artery_disease": "冠心病", "arrhythmia": "心律失常",
    "hypertension": "高血压", "diabetes": "糖尿病", "copd": "慢阻肺",
    "peripheral_neuropathy": "周围神经病变", "hearing_loss": "听力下降",
    "hbv": "乙肝（HBsAg+）", "hcv": "丙肝", "hiv": "HIV", "tuberculosis": "结核",
    "organ_transplant": "器官移植", "chronic_kidney_disease": "慢性肾病",
    "cirrhosis": "肝硬化",
}

#: Default upper limits of normal, used only when a value comes without
#: its ×ULN form — and every conclusion drawn from one says so.
DEFAULT_ULN = {"alt_u_l": 40.0, "ast_u_l": 40.0, "bilirubin_umol_l": 21.0}


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
    return float(match.group(0)) if match else None


def sanitize_organ_function(value: Any) -> tuple[dict[str, Any], list[str]]:
    """Range-checked organ_function. Blocks may also hold a verdict string
    ("normal", "CrCl 38") — those are kept for the gates to read."""
    notes: list[str] = []
    if not isinstance(value, dict):
        return {}, ["CHAT_FACT_IGNORED: organ_function must be an object"]
    out: dict[str, Any] = {}
    for block, raw in value.items():
        block = str(block)
        if raw is None:
            notes.append(f"CHAT_FACT_IGNORED: organ_function.{block} is null")
            continue
        if block in FLAT_ORGAN_FIELDS:
            lo, hi = FLAT_ORGAN_FIELDS[block]
            number = _number(raw)
            if number is None or not lo <= number <= hi:
                notes.append(f"CHAT_FACT_IGNORED: organ_function.{block} {raw!r} out of range")
                continue
            out[block] = number
            continue
        fields = ORGAN_FIELDS.get(block)
        if fields is None or not isinstance(raw, dict):
            out[block] = raw  # legacy verdict strings / unknown blocks pass through
            continue
        cleaned: dict[str, Any] = {}
        for key, sub in raw.items():
            key = str(key)
            if sub is None:
                continue
            if block == "hepatic" and key == "child_pugh":
                grade = str(sub).strip().upper()[:1]
                if grade in ("A", "B", "C"):
                    cleaned[key] = grade
                else:
                    notes.append(f"CHAT_FACT_IGNORED: organ_function.hepatic.child_pugh {sub!r}")
                continue
            if key not in fields:
                cleaned[key] = sub  # verdict / free-text companions pass through
                continue
            lo, hi, unit, _ = fields[key]
            number = _number(sub)
            if number is None or not lo <= number <= hi:
                notes.append(f"CHAT_FACT_IGNORED: organ_function.{block}.{key} {sub!r} "
                             f"outside {lo:g}–{hi:g} {unit}")
                continue
            cleaned[key] = int(number) if key == "nyha" else number
        if cleaned:
            out[block] = cleaned
    return out, notes


def sanitize_comorbidities(value: Any) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(value, dict):
        return {}, ["CHAT_FACT_IGNORED: comorbidities must be an object"]
    out: dict[str, Any] = {}
    notes: list[str] = []
    for key, raw in value.items():
        key = str(key)
        if raw is None:
            notes.append(f"CHAT_FACT_IGNORED: comorbidities.{key} is null")
            continue
        if key == "peripheral_neuropathy" and not isinstance(raw, bool):
            grade = _number(raw)
            if grade is not None and 0 <= grade <= 4:
                out[key] = int(grade)
                continue
        out[key] = raw
    return out, notes


# ----------------------------------------------------------------- CrCl

def crcl_estimate(facts: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Cockcroft–Gault creatinine clearance from age, weight, serum
    creatinine and sex — only when all three numbers are on record.

    Returns ``{"low": .., "high": .., "note": ..}``: with sex unknown the
    female (×0.85) and male readings bracket the answer; with sex known
    both are the same value."""
    renal = (facts.get("organ_function") or {}).get("renal")
    scr = _number(renal.get("creatinine_umol_l")) if isinstance(renal, dict) else None
    age, weight = _number(facts.get("age")), _number(facts.get("weight_kg"))
    if not (scr and age and weight):
        return None
    base = (140 - age) * weight / (72 * scr / 88.4)
    sex = str(facts.get("sex") or "").lower()
    if sex in ("female", "f", "女"):
        low = high = base * 0.85
        who = "female"
    elif sex in ("male", "m", "男"):
        low = high = base
        who = "male"
    else:
        low, high, who = base * 0.85, base, "sex not recorded: female–male range"
    return {"low": round(low, 1), "high": round(high, 1),
            "note": f"Cockcroft–Gault from creatinine {scr:g} µmol/L, age {age:g}, "
                    f"weight {weight:g} kg ({who})"}


# ----------------------------------------------------------- extraction

_V = r"[:：=]?\s*(?:为|是|约|of|is)?\s*(\d+(?:\.\d+)?)"
_LAB_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("renal", "crcl_ml_min", re.compile(r"(?:CrCl|CCr|Ccr|肌酐清除率|creatinine\s+clearance)\s*" + _V, re.I)),
    ("renal", "egfr_ml_min", re.compile(r"(?<![A-Za-z])eGFR\s*" + _V, re.I)),
    ("renal", "creatinine_umol_l", re.compile(
        r"(?:血肌酐|血清肌酐|肌酐(?!清除)|\bScr\b|\bCr(?![a-zA-Z])|\bcreatinine(?!\s+clearance))\s*"
        + _V + r"\s*(µmol/L|umol/L|μmol/L|mg/dL)?", re.I)),
    ("hematologic", "wbc", re.compile(r"(?:\bWBC\b|白细胞(?:计数)?)\s*" + _V + r"\s*(/µL|/uL|/mm3)?", re.I)),
    ("hematologic", "anc", re.compile(
        r"(?:\bANC\b|中性粒细胞(?:绝对值|计数)?|NEUT#?)\s*" + _V + r"\s*(/µL|/uL|/mm3)?", re.I)),
    ("hematologic", "hb", re.compile(r"(?:\bHb\b|\bHGB\b|血红蛋白|haemoglobin|hemoglobin)\s*" + _V
                                     + r"\s*(g/dL|g/L)?", re.I)),
    ("hematologic", "plt", re.compile(r"(?:\bPLT\b|血小板(?:计数)?|platelets?)\s*" + _V + r"\s*(/µL|/uL|/mm3)?",
                                      re.I)),
    ("hepatic", "alt", re.compile(r"(?:\bALT\b|谷丙转氨酶|丙氨酸氨基转移酶)\s*" + _V
                                  + r"\s*(U/L|IU/L|×\s*ULN|x\s*ULN|倍(?:正常上限|ULN)?)?", re.I)),
    ("hepatic", "ast", re.compile(r"(?:\bAST\b|谷草转氨酶|天冬氨酸氨基转移酶)\s*" + _V
                                  + r"\s*(U/L|IU/L|×\s*ULN|x\s*ULN|倍(?:正常上限|ULN)?)?", re.I)),
    ("hepatic", "bilirubin", re.compile(r"(?:总胆红素|\bTBIL\b|\bT-?Bil\b|胆红素|bilirubin)\s*" + _V
                                        + r"\s*(µmol/L|umol/L|μmol/L|mg/dL|×\s*ULN|x\s*ULN|倍)?", re.I)),
    ("hepatic", "albumin_g_l", re.compile(r"(?:白蛋白|\bALB\b|albumin)\s*" + _V + r"\s*(g/dL|g/L)?", re.I)),
    ("cardiac", "lvef_pct", re.compile(r"(?:LVEF|\bEF\b|射血分数)\s*" + _V + r"\s*%", re.I)),
    ("pulmonary", "fev1_pct", re.compile(r"(?<!ppo)(?<!ppo-)FEV1(?:\s*(?:%\s*pred(?:icted)?|占预计值(?:百分比)?))?\s*"
                                         + _V + r"\s*%", re.I)),
    ("pulmonary", "dlco_pct", re.compile(r"(?<!ppo)(?<!ppo-)DLCO(?:\s*(?:%\s*pred(?:icted)?|占预计值(?:百分比)?))?\s*"
                                         + _V + r"\s*%", re.I)),
    ("flat", "ppo_fev1_pct", re.compile(r"ppo-?FEV1\s*" + _V + r"\s*%", re.I)),
    ("flat", "ppo_dlco_pct", re.compile(r"ppo-?DLCO\s*" + _V + r"\s*%", re.I)),
    ("electrolytes", "calcium_mmol_l", re.compile(r"(?:校正血钙|校正钙|血清钙|血钙|corrected\s+calcium|calcium)\s*"
                                                  + _V + r"\s*(mmol/L|mg/dL)?", re.I)),
    ("electrolytes", "sodium_mmol_l", re.compile(r"(?:血钠|血清钠|\bNa\+?(?![A-Za-z])|sodium)\s*" + _V, re.I)),
    ("electrolytes", "potassium_mmol_l", re.compile(r"(?:血钾|血清钾|\bK\+(?![A-Za-z])|potassium)\s*" + _V, re.I)),
)
_ULN_UNIT_RE = re.compile(r"ULN|倍", re.I)
_QTC_RE = re.compile(r"QTc\s*" + _V + r"\s*(?:ms|毫秒)?", re.I)
_WEIGHT_RE = re.compile(r"(?:体重(?!下降|减轻|减少|丢失)|body\s+weight|\bweight(?!\s+loss))\s*" + _V
                        + r"\s*(?:kg|公斤|千克)", re.I)
_HEIGHT_RE = re.compile(r"(?:身高|height)\s*" + _V + r"\s*(?:cm|厘米)", re.I)
_SEX_RE = re.compile(r"(?:^|[\s，,。;；:：\d岁])(男|女)(?:性|患者|士|，|,|\s|\d|。)|\b(male|female|man|woman)\b",
                     re.I)
_CHILD_PUGH_RE = re.compile(r"Child[-\s]?Pugh\s*(?:分级|class|grade)?\s*[:：]?\s*([ABC])", re.I)
_NYHA_RE = re.compile(r"NYHA\s*(?:心功能)?\s*(?:分级|class)?\s*[:：]?\s*(IV|III|II|I|[1-4])(?:\s*级)?", re.I)
_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4}


def _convert(block: str, key: str, value: float, unit: str) -> tuple[str, float]:
    unit = (unit or "").lower()
    if key == "creatinine_umol_l" and ("mg" in unit or value < 20):
        return key, round(value * 88.4, 1)
    if key in ("wbc", "anc", "plt") and (unit.startswith("/") or (key != "plt" and value > 100)
                                         or (key == "plt" and value > 2000)):
        return key, round(value / 1000, 2)
    if key == "hb" and ("dl" in unit or value < 25):
        return key, round(value * 10, 1)
    if key == "albumin_g_l" and ("dl" in unit or value < 10):
        return key, round(value * 10, 1)
    if key in ("alt", "ast"):
        return (f"{key}_uln", value) if _ULN_UNIT_RE.search(unit) else (f"{key}_u_l", value)
    if key == "bilirubin":
        if _ULN_UNIT_RE.search(unit):
            return "bilirubin_uln", value
        if "mg" in unit:
            return "bilirubin_umol_l", round(value * 17.1, 1)
        return "bilirubin_umol_l", value
    if key == "calcium_mmol_l" and ("mg" in unit or value > 5):
        return key, round(value / 4.008, 2)
    return key, value


_NEGATED_RE = re.compile(r"(?:无|否认|没有|未见|未发现|不伴|\bno\b|\bdenies\b|\bwithout\b|negative\s+for)"
                         r"[^。；;，,\n]{0,8}$", re.I)
_COMORBID_RE: dict[str, re.Pattern[str]] = {
    "ild": re.compile(r"间质性肺(?:病|炎|疾病)|肺间质纤维化|特发性肺纤维化|\bILD\b|interstitial\s+lung\s+disease|"
                      r"pulmonary\s+fibrosis", re.I),
    "heart_failure": re.compile(r"心力衰竭|心衰|心功能不全|heart\s+failure|\bCHF\b|\bHFrEF\b", re.I),
    "coronary_artery_disease": re.compile(r"冠心病|冠状动脉(?:粥样硬化性)?心脏病|心肌梗死|心梗|支架植入|"
                                          r"coronary\s+(?:artery\s+)?disease|myocardial\s+infarction", re.I),
    "arrhythmia": re.compile(r"房颤|心房颤动|心律失常|atrial\s+fibrillation|arrhythmia", re.I),
    "hypertension": re.compile(r"高血压|hypertension", re.I),
    "diabetes": re.compile(r"糖尿病|diabetes", re.I),
    "copd": re.compile(r"慢阻肺|慢性阻塞性肺(?:疾)?病|\bCOPD\b|emphysema|肺气肿", re.I),
    "peripheral_neuropathy": re.compile(r"(?:周围|外周)神经病变|神经病变|peripheral\s+neuropathy", re.I),
    "hearing_loss": re.compile(r"听力(?:下降|减退|损失|受损|障碍)|耳聋|hearing\s+(?:loss|impairment)", re.I),
    "hbv": re.compile(r"乙肝|乙型肝炎|HBsAg\s*(?:\(\+\)|（\+）|\+|阳性|positive)|HBV(?:\s*DNA)?\s*(?:阳性|positive|携带)|"
                      r"hepatitis\s+B", re.I),
    "hcv": re.compile(r"丙肝|丙型肝炎|HCV\s*(?:抗体)?\s*(?:阳性|positive|\(\+\))|hepatitis\s+C", re.I),
    "hiv": re.compile(r"HIV\s*(?:感染|阳性|positive|\(\+\))|艾滋病", re.I),
    "tuberculosis": re.compile(r"(?:肺)?结核(?!节)|tuberculosis", re.I),
    "organ_transplant": re.compile(r"(?:肾|肝|心|肺|器官)移植|(?:kidney|liver|heart|lung|organ|renal)\s+transplant",
                                   re.I),
    "chronic_kidney_disease": re.compile(r"慢性肾(?:脏)?病|慢性肾功能不全|肾功能衰竭|\bCKD\b|chronic\s+kidney\s+disease",
                                         re.I),
    "cirrhosis": re.compile(r"肝硬化|cirrhosis", re.I),
    "autoimmune_disease": re.compile(r"自身免疫(?:性)?(?:疾)?病|类风湿(?:关节炎)?|系统性红斑狼疮|红斑狼疮|\bSLE\b|"
                                     r"炎症性肠病|克罗恩病|溃疡性结肠炎|autoimmune\s+disease|rheumatoid|lupus|"
                                     r"crohn|ulcerative\s+colitis", re.I),
}
_HBV_NEGATIVE_RE = re.compile(r"HBsAg\s*(?:阴性|\(-\)|（-）|negative)", re.I)
_EXPLICIT_NEGATIVE_AFTER_RE = re.compile(r"^[^。；;，,\n]{0,6}(?:阴性|\(-\)|（-）|negative)", re.I)
_NEUROPATHY_GRADE_RE = re.compile(r"(?:([1-4])\s*级|grade\s*([1-4])|G([1-4]))[^。；;，,\n]{0,6}(?:周围|外周)?神经病变|"
                                  r"神经病变[^。；;，,\n]{0,6}(?:([1-4])\s*级|grade\s*([1-4])|G([1-4]))", re.I)


def _comorbidity_value(text: str, pattern: re.Pattern[str]) -> Optional[bool]:
    readings: list[bool] = []
    for match in pattern.finditer(text):
        clause_start = max(text.rfind(ch, 0, match.start()) for ch in "。；;\n,，")
        before = text[clause_start + 1:match.start()]
        after = text[match.end():]
        if _NEGATED_RE.search(before) or _EXPLICIT_NEGATIVE_AFTER_RE.search(after):
            readings.append(False)
        elif re.search(r"可疑|疑似|待排|家族史|家族", before):
            continue
        else:
            readings.append(True)
    if not readings:
        return None
    return True if True in readings else False


def extract_labs(text: str) -> dict[str, Any]:
    """Lab values, body measures and comorbidities stated in a narrative."""
    text = text or ""
    organ: dict[str, Any] = {}
    facts: dict[str, Any] = {}
    for block, key, pattern in _LAB_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        value = float(match.group(1))
        unit = match.group(2) if (pattern.groups or 0) >= 2 else ""
        key, value = _convert(block, key, value, unit or "")
        if block == "flat":
            organ[key] = value
        else:
            organ.setdefault(block, {})[key] = value
    m = _CHILD_PUGH_RE.search(text)
    if m:
        organ.setdefault("hepatic", {})["child_pugh"] = m.group(1).upper()
    m = _NYHA_RE.search(text)
    if m:
        raw = m.group(1).upper()
        organ.setdefault("cardiac", {})["nyha"] = _ROMAN.get(raw) or int(raw)
    if organ:
        cleaned, _ = sanitize_organ_function(organ)
        if cleaned:
            facts["organ_function"] = cleaned
    m = _QTC_RE.search(text)
    if m and 300 <= float(m.group(1)) <= 700:
        facts["qtc_ms"] = float(m.group(1))
    m = _WEIGHT_RE.search(text)
    if m and 25 <= float(m.group(1)) <= 250:
        facts["weight_kg"] = float(m.group(1))
    m = _HEIGHT_RE.search(text)
    if m and 120 <= float(m.group(1)) <= 230:
        facts["height_cm"] = float(m.group(1))
    m = _SEX_RE.search(text)
    if m:
        word = (m.group(1) or m.group(2) or "").lower()
        facts["sex"] = "male" if word in ("男", "male", "man") else "female"
    comorbid: dict[str, Any] = {}
    for key, pattern in _COMORBID_RE.items():
        value = _comorbidity_value(text, pattern)
        if value is not None:
            comorbid[key] = value
    if "hbv" not in comorbid and _HBV_NEGATIVE_RE.search(text):
        comorbid["hbv"] = False
    m = _NEUROPATHY_GRADE_RE.search(text)
    if m and comorbid.get("peripheral_neuropathy") is not False:
        comorbid["peripheral_neuropathy"] = int(next(g for g in m.groups() if g))
    if comorbid:
        facts["comorbidities"] = comorbid
    return facts


# ------------------------------------------------------- supportive care

def supportive_notes(facts: dict[str, Any], lang: str = "zh") -> list[str]:
    """Lab- and comorbidity-driven measures that precede or accompany any
    systemic therapy (not regimen choices; the gates handle those)."""
    zh = lang == "zh"
    organ = facts.get("organ_function") or {}
    comorbid = facts.get("comorbidities") or {}
    notes: list[str] = []

    def block(name: str) -> dict[str, Any]:
        value = organ.get(name)
        return value if isinstance(value, dict) else {}

    calcium = _number(block("electrolytes").get("calcium_mmol_l"))
    if calcium is not None and calcium > 2.75:
        severe = calcium >= 3.5
        notes.append(
            (f"高钙血症（校正钙 {calcium:g} mmol/L{'，重度' if severe else ''}）：静脉水化，"
             f"双膦酸盐或地舒单抗，停用含钙/维生素 D 制剂，查 PTH/PTHrP；"
             f"{'属急症，当天处理' if severe else '复查并监测肾功能'}")
            if zh else
            (f"Hypercalcaemia (corrected calcium {calcium:g} mmol/L{', severe' if severe else ''}): "
             f"IV hydration, a bisphosphonate or denosumab, stop calcium/vitamin D, check PTH/PTHrP; "
             f"{'an emergency, treat today' if severe else 'recheck with renal function'}"))
    sodium = _number(block("electrolytes").get("sodium_mmol_l"))
    if sodium is not None and sodium < 130:
        notes.append(
            f"低钠血症（{sodium:g} mmol/L）：评估容量状态与 SIADH（小细胞成分时尤需警惕），纠正速度不超过每日 8–10 mmol/L"
            if zh else
            f"Hyponatraemia ({sodium:g} mmol/L): assess volume status and SIADH; correct by no more "
            f"than 8–10 mmol/L per day")
    hb = _number(block("hematologic").get("hb"))
    if hb is not None and hb < 100:
        notes.append(
            f"贫血（Hb {hb:g} g/L）：查铁代谢、B12/叶酸与出血来源；Hb < 70–80 g/L 或有症状时考虑输血"
            if zh else
            f"Anaemia (Hb {hb:g} g/L): iron studies, B12/folate and a bleeding source; transfuse "
            f"when Hb < 70–80 g/L or symptomatic")
    albumin = _number(block("hepatic").get("albumin_g_l"))
    if albumin is not None and albumin < 30:
        notes.append(
            f"低白蛋白（{albumin:g} g/L）：营养评估与干预；提示预后较差、药物蛋白结合改变"
            if zh else
            f"Hypoalbuminaemia ({albumin:g} g/L): nutrition assessment and support; a poor-prognosis "
            f"marker that also alters drug binding")
    if comorbid.get("hbv") is True:
        notes.append(
            "乙肝（HBsAg+）：化疗/免疫/靶向治疗前查 HBV DNA，启动恩替卡韦或替诺福韦预防再激活并定期监测"
            if zh else
            "Hepatitis B (HBsAg+): HBV DNA before chemotherapy/immunotherapy/targeted therapy; start "
            "entecavir or tenofovir prophylaxis against reactivation and monitor")
    if comorbid.get("hcv") is True:
        notes.append("丙肝：查 HCV RNA，与肝病科共同管理" if zh else
                     "Hepatitis C: HCV RNA and co-management with hepatology")
    if comorbid.get("organ_transplant") is True:
        notes.append(
            "器官移植受者：免疫检查点抑制剂有移植物排斥风险，须与移植团队共同决策"
            if zh else
            "Organ-transplant recipient: checkpoint inhibitors risk graft rejection; decide with the "
            "transplant team")
    if comorbid.get("tuberculosis") is True:
        notes.append("结核病史：免疫治疗/激素使用前评估潜伏或活动性结核" if zh else
                     "Tuberculosis history: assess latent or active TB before immunotherapy or steroids")
    return notes
