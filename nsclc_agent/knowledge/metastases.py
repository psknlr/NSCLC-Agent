"""Metastatic sites beyond the brain (AJCC 9th edition M descriptors).

The brain has its own record (``cns_metastases``, see ``cns.py``); every
other site lives in ``facts["metastatic_sites"]`` as ``{site: status}``:

* thoracic (M1a): ``contralateral_lung``, ``pleura`` (pleural nodules /
  dissemination), ``pleural_effusion`` (malignant), ``pericardial`` (nodules
  or malignant effusion);
* extrathoracic (M1b / M1c): ``bone``, ``liver``, ``adrenal``,
  ``distant_lymph_nodes`` (non-regional: retroperitoneal, axillary …),
  ``other`` (peritoneum, skin, kidney …).

Status is ``absent``, ``present`` (count not stated), ``single`` or
``multiple``. The record keeps what was documented; ``suggest_m`` reads
the sites the way the staging manual defines M and ``tnm_conflict`` names
a recorded M that the sites contradict — it never repairs the descriptor
(staging stays with whoever staged: the engine or, in full autonomy, the
model). Local invasion of the mediastinal or visceral pleura is a T
descriptor and is never read as a pleural metastasis.
"""

from __future__ import annotations

import re
from typing import Any, Optional

ABSENT, PRESENT, SINGLE, MULTIPLE = "absent", "present", "single", "multiple"
STATUSES = (ABSENT, PRESENT, SINGLE, MULTIPLE)

THORACIC_SITES = ("contralateral_lung", "pleura", "pleural_effusion", "pericardial")
EXTRATHORACIC_SITES = ("bone", "liver", "adrenal", "distant_lymph_nodes", "other")
SITES = THORACIC_SITES + EXTRATHORACIC_SITES

SITE_LABEL_ZH = {
    "contralateral_lung": "对侧肺", "pleura": "胸膜结节/播散", "pleural_effusion": "恶性胸腔积液",
    "pericardial": "心包结节/恶性心包积液", "bone": "骨", "liver": "肝", "adrenal": "肾上腺",
    "distant_lymph_nodes": "远处淋巴结", "other": "其他部位",
}
SITE_LABEL_EN = {
    "contralateral_lung": "contralateral lung", "pleura": "pleural nodules",
    "pleural_effusion": "malignant pleural effusion", "pericardial": "pericardial",
    "bone": "bone", "liver": "liver", "adrenal": "adrenal",
    "distant_lymph_nodes": "distant lymph nodes", "other": "other",
}

_BOOL_STATUS = {True: PRESENT, False: ABSENT}
_STATUS_ALIASES = {
    "yes": PRESENT, "positive": PRESENT, "有": PRESENT, "是": PRESENT, "阳性": PRESENT,
    "no": ABSENT, "none": ABSENT, "negative": ABSENT, "无": ABSENT, "否": ABSENT, "阴性": ABSENT,
    "solitary": SINGLE, "单发": SINGLE, "单个": SINGLE, "oligo": SINGLE,
    "多发": MULTIPLE, "several": MULTIPLE, "diffuse": MULTIPLE, "弥漫": MULTIPLE,
}


def normalize_status(value: Any) -> Optional[str]:
    """A site status from a fact value, or None when it is not one."""
    if isinstance(value, bool):
        return _BOOL_STATUS[value]
    if isinstance(value, int) and not isinstance(value, bool):
        return ABSENT if value == 0 else SINGLE if value == 1 else MULTIPLE
    text = str(value or "").strip().lower()
    if text in STATUSES:
        return text
    return _STATUS_ALIASES.get(text)


def sanitize_sites(value: Any) -> tuple[dict[str, str], list[str]]:
    """Validated ``{site: status}`` plus notes for what was dropped."""
    notes: list[str] = []
    if not isinstance(value, dict):
        return {}, ["CHAT_FACT_IGNORED: metastatic_sites must be an object {site: status}"]
    out: dict[str, str] = {}
    for site, raw in value.items():
        site = str(site).strip().lower()
        if site == "brain":
            notes.append("CHAT_FACT_IGNORED: metastatic_sites.brain — brain metastases "
                         "go to cns_metastases")
            continue
        if site not in SITES:
            notes.append(f"CHAT_FACT_IGNORED: metastatic_sites.{site} is not a known site "
                         f"({', '.join(SITES)})")
            continue
        status = normalize_status(raw)
        if status is None:
            notes.append(f"CHAT_FACT_IGNORED: metastatic_sites.{site} {raw!r} is not one of "
                         f"{', '.join(STATUSES)}")
            continue
        out[site] = status
    return out, notes


# --------------------------------------------------------------- extraction

#: Each site's mention. The pleura pattern needs a lesion word right after
#: 胸膜 ("胸膜结节/转移/播散/病灶"), so "侵犯纵隔胸膜" (T4 invasion) never
#: matches; effusions count only when called malignant or cytology-positive.
_SITE_RE: dict[str, re.Pattern[str]] = {
    "bone": re.compile(
        r"骨(?:骼|质)?(?:多发|广泛)?(?:转移|破坏)|(?:椎体|肋骨|骨盆|股骨|胸椎|腰椎|颈椎)[^。；;，,\n]{0,6}转移|"
        r"\b(?:bone|osseous|skeletal|vertebral)\s+(?:metasta\w*|lesions?)", re.I),
    "liver": re.compile(
        r"肝(?:脏|内)?[^。；;，,\n、及和与]{0,6}?转移|\b(?:liver|hepatic)\s+(?:metasta\w*|lesions?)", re.I),
    # An adrenal or contralateral nodule alone may be benign: only a
    # metastasis counts.
    "adrenal": re.compile(
        r"肾上腺[^。；;\n]{0,12}?转移|\badrenal\s+(?:gland\s+)?metasta\w*", re.I),
    "contralateral_lung": re.compile(
        r"(?:对侧肺|对侧肺叶|双肺|两肺)[^。；;，,\n]{0,8}?转移|"
        r"\bcontralateral\s+(?:lung|lobe)\s+metasta\w*|bilateral\s+(?:lung|pulmonary)\s+metasta",
        re.I),
    "pleura": re.compile(
        r"胸膜(?:多发)?(?:转移|结节|播散|种植|病灶|病变)|"
        r"\bpleural\s+(?:metasta\w*|nodules?|nodularity|dissemination|implants?|seeding|lesions?)", re.I),
    "pleural_effusion": re.compile(
        r"恶性胸腔积液|恶性胸水|胸(?:腔积液|水)[^。；;\n]{0,12}(?:找到|查见|见到|发现)?(?:癌细胞|腺癌细胞|肿瘤细胞)|"
        r"\bmalignant\s+pleural\s+effusion|pleural\s+effusion[^.;\n]{0,24}(?:cytology\s+positive|malignant cells)",
        re.I),
    "pericardial": re.compile(
        r"恶性心包积液|心包(?:转移|结节|播散)|\bmalignant\s+pericardial\s+effusion|pericardial\s+(?:metasta\w*|nodules?)",
        re.I),
    "distant_lymph_nodes": re.compile(
        r"(?:腹膜后|腹腔|腋窝|腹股沟|远处|肝门|胰周|颈部(?!锁骨))淋巴结[^。；;，,\n]{0,6}?(?:转移|肿大)|"
        r"\b(?:retroperitoneal|abdominal|axillary|inguinal|distant|non-?regional)\s+(?:lymph\s+node|nodal)\s*"
        r"(?:metasta\w*|disease|involvement)?", re.I),
    "other": re.compile(
        r"(?:腹膜(?!后)|皮肤|皮下|肾脏|肾(?!上腺)|脾|胰腺|肌肉|软组织|眼|卵巢)[^。；;，,\n]{0,4}?转移|"
        r"\b(?:peritoneal|skin|subcutaneous|renal|splenic|pancreatic|soft[- ]tissue)\s+metasta\w*", re.I),
}
#: Whole-site negatives that name no lesion ("骨扫描阴性").
_SITE_ABSENT_RE: dict[str, re.Pattern[str]] = {
    "bone": re.compile(r"骨扫描(?:阴性|未见(?:异常|转移)?|正常)|\bbone\s+scan\s+(?:negative|normal|unremarkable)",
                       re.I),
}
_NEGATION_RE = re.compile(r"(?:无|未见|没有|未发现|排除|否认|未提示|\bno\b|\bwithout\b|negative\s+for)"
                          r"[^。；;，,\n]{0,8}$", re.I)
_UNSURE_AFTER_RE = re.compile(r"^\s*[？?]|^[^。；;，,\n]{0,3}[，,]?\s*(?:性质)?(?:待定|待排|待查|不除外|可能|考虑良性)", re.I)
_NEGATED_AFTER_RE = re.compile(r"^[^。；;，,\n]{0,4}(?:阴性|未见|\(-\)|（-）|negative)", re.I)
_UNSURE_RE = re.compile(r"(?:可疑|疑似|待排|不除外|待除外|可能|\?|？|\bpossible\b|\bsuspected\b|"
                        r"\bequivocal\b|\bindeterminate\b)[^。；;，,\n]{0,6}$", re.I)
_MULTIPLE_RE = re.compile(r"多发|多处|多个|弥漫|广泛|双侧|两侧|数个|[2-9２-９]\s*(?:个|处|枚|灶)|[两二三四五六七八九十]\s*(?:个|处|枚|灶)|"
                          r"\bmultiple\b|\bseveral\b|\bdiffuse\b|\bbilateral\b|\bnumerous\b|\b[2-9]\s+(?:lesions|foci)", re.I)
_SINGLE_RE = re.compile(r"单发|孤立|单个|单一|一个|一处|1\s*(?:个|处|枚|灶)|左侧?(?=肾上腺)|右侧?(?=肾上腺)|单侧|"
                        r"\bsolitary\b|\bsingle\b|\bisolated\b|\bone\b|\b(?:left|right)\s+adrenal", re.I)
#: Cuts the clause before a mention at the last joiner, so "左肾上腺及多发骨
#: 转移" gives the bone mention "多发" and the adrenal mention "左".
_JOINER_RE = re.compile(r"[及和与、并伴]|\band\b|,|，|;|；", re.I)
_DISTANT_ABSENT_RE = re.compile(
    r"(?:无|未见|没有|未发现)(?:明显)?(?:其他|其它)?远处转移|\bno\s+(?:evidence\s+of\s+)?distant\s+metasta", re.I)


def _segment_before(text: str, start: int) -> str:
    clause_start = max(text.rfind(ch, 0, start) for ch in "。；;\n")
    before = text[clause_start + 1:start]
    cut = None
    for cut in _JOINER_RE.finditer(before):
        pass
    return before[cut.end():] if cut else before


def _site_status(text: str, match: re.Match[str]) -> Optional[str]:
    clause_start = max(text.rfind(ch, 0, match.start()) for ch in "。；;\n,，")
    before_clause = text[clause_start + 1:match.start()]
    mention = match.group(0)
    if _NEGATION_RE.search(before_clause) or _NEGATED_AFTER_RE.search(text[match.end():]) \
            or re.search(r"无|未见|没有|未发现|未提示", mention):
        return ABSENT
    if _UNSURE_RE.search(before_clause) or re.search(r"可疑|疑似|待排|不除外", mention) \
            or _UNSURE_AFTER_RE.search(text[match.end():]):
        return None
    # The count belongs to this site only: the mention is cut at its first
    # joiner ("肾上腺及多发骨转移" counts as "肾上腺" for the adrenal).
    own = _JOINER_RE.split(mention, maxsplit=1)[0]
    segment = _segment_before(text, match.start()) + own
    after = re.match(r"[^。；;，,\n、及和与]{0,6}", text[match.end():]).group(0)
    if _MULTIPLE_RE.search(segment) or _MULTIPLE_RE.search(after):
        return MULTIPLE
    if _SINGLE_RE.search(segment) or _SINGLE_RE.search(after):
        return SINGLE
    return PRESENT


_RANK = {ABSENT: 0, PRESENT: 1, SINGLE: 2, MULTIPLE: 3}

#: Sites enumerated before one shared "转移" ("肝、骨多发转移").
_LIST_WORDS = {"肝": "liver", "骨": "bone", "肾上腺": "adrenal", "腹膜": "other",
               "胸膜": "pleura", "对侧肺": "contralateral_lung"}
_LIST_RE = re.compile(
    r"((?:肝|骨|肾上腺|腹膜|胸膜|对侧肺|脑)(?:\s*[、及和与/]\s*(?:肝|骨|肾上腺|腹膜|胸膜|对侧肺|脑))+)"
    r"\s*(多发|广泛|弥漫)?\s*转移")


_EN_LIST_WORDS = {"liver": "liver", "hepatic": "liver", "bone": "bone", "osseous": "bone",
                  "skeletal": "bone", "adrenal": "adrenal", "peritoneal": "other",
                  "pleural": "pleura", "contralateral lung": "contralateral_lung"}
_EN_LIST_RE = re.compile(
    r"\b((?:multiple\s+)?(?:liver|hepatic|bone|osseous|skeletal|adrenal|brain|peritoneal|pleural|"
    r"contralateral lung)(?:\s*(?:,|and|or|&|/)\s*(?:and\s+|or\s+)?(?:liver|hepatic|bone|osseous|skeletal|"
    r"adrenal|brain|peritoneal|pleural|contralateral lung))+)\s+metasta", re.I)


def _list_readings(text: str) -> tuple[dict[str, list[str]], list[tuple[int, int]]]:
    """Readings of enumerated site lists, and their spans (a single-site
    mention inside a list is the list's, not its own)."""
    out: dict[str, list[str]] = {}
    spans: list[tuple[int, int]] = []
    for match in _EN_LIST_RE.finditer(text):
        spans.append((match.start(), match.end()))
        before = text[max(0, match.start() - 24):match.start()]
        after = text[match.end():match.end() + 16]
        if re.search(r"\b(?:no|without|negative for)\b[^.;,]*$", before, re.I):
            status = ABSENT
        elif re.search(r"\b(?:possible|suspected|equivocal)\b[^.;,]*$", before, re.I) \
                or re.match(r"\w*\s*\?", after):
            continue
        else:
            status = MULTIPLE if re.match(r"multiple", match.group(1), re.I) else PRESENT
        for word in re.split(r"\s*(?:,|\band\b|\bor\b|&|/)\s*", re.sub(r"^multiple\s+", "", match.group(1),
                                                                 flags=re.I)):
            site = _EN_LIST_WORDS.get(word.strip().lower())
            if site:
                out.setdefault(site, []).append(status)
    for match in _LIST_RE.finditer(text):
        spans.append((match.start(), match.end()))
        clause_start = max(text.rfind(ch, 0, match.start()) for ch in "。；;\n,，")
        if _NEGATION_RE.search(text[clause_start + 1:match.start()]):
            status = ABSENT
        elif _UNSURE_RE.search(text[clause_start + 1:match.start()]):
            continue
        else:
            status = MULTIPLE if match.group(2) else PRESENT
        for word in re.split(r"\s*[、及和与/]\s*", match.group(1)):
            site = _LIST_WORDS.get(word)
            if site:
                out.setdefault(site, []).append(status)
    return out, spans


def extract_sites(text: str) -> dict[str, str]:
    """Metastatic sites as documented in a narrative (brain excluded).

    A site mentioned more than once keeps its strongest reading (two liver
    lesions reported later turn "present" into "multiple"); a negated
    mention records ``absent`` only when nothing positive is said."""
    text = text or ""
    out: dict[str, str] = {}
    listed, spans = _list_readings(text)
    for site, pattern in _SITE_RE.items():
        readings = [s for s in (_site_status(text, m) for m in pattern.finditer(text)
                                if not any(a <= m.start() < b for a, b in spans)) if s]
        readings += listed.get(site, [])
        if site in _SITE_ABSENT_RE and _SITE_ABSENT_RE[site].search(text):
            readings.append(ABSENT)
        if not readings:
            continue
        positive = [s for s in readings if s != ABSENT]
        out[site] = max(positive, key=_RANK.get) if positive else ABSENT
    return out


# ------------------------------------------------------------ M descriptor

def _brain_reading(facts: dict[str, Any]) -> Optional[str]:
    cns = facts.get("cns_metastases")
    status = cns.get("status") if isinstance(cns, dict) else cns
    if str(status or "").lower() != "present":
        return None
    burden = str(cns.get("burden") or "").lower() if isinstance(cns, dict) else ""
    if burden in ("limited", "single", "solitary"):
        return SINGLE
    if burden in ("extensive", "multiple"):
        return MULTIPLE
    return PRESENT


def site_readings(facts: dict[str, Any]) -> dict[str, str]:
    """All sites on record, the brain included (from ``cns_metastases``)."""
    sites, _ = sanitize_sites(facts.get("metastatic_sites") or {})
    brain = _brain_reading(facts)
    if brain:
        sites["brain"] = brain
    return sites


def suggest_m(facts: dict[str, Any]) -> dict[str, Any]:
    """The M descriptor the recorded sites imply.

    Returns ``{"m": "M1a" | "M1b" | "M1c1" | "M1c2" | None, "candidates":
    [...], "basis": str}``. ``m`` is None when the sites cannot fix it —
    nothing recorded, or one extrathoracic system with the lesion count
    unstated (M1b vs M1c1: the stage group differs, IVA vs IVB)."""
    sites = site_readings(facts)
    present = {s: v for s, v in sites.items() if v != ABSENT}
    extra = {s: v for s, v in present.items() if s not in THORACIC_SITES}
    thoracic = [s for s in present if s in THORACIC_SITES]

    def zh(names: Any) -> str:
        return "、".join(SITE_LABEL_ZH.get(n, "脑" if n == "brain" else n) for n in names)

    def out(m: Optional[str], candidates: list[str], basis: str, basis_zh: str) -> dict[str, Any]:
        return {"m": m, "candidates": candidates, "basis": basis, "basis_zh": basis_zh}

    if len(extra) >= 2:
        return out("M1c2", ["M1c2"],
                   f"extrathoracic metastases in {len(extra)} organ systems ({', '.join(extra)})",
                   f"胸腔外 {len(extra)} 个器官系统转移（{zh(extra)}）")
    if len(extra) == 1:
        (site, status), = extra.items()
        if status == MULTIPLE:
            return out("M1c1", ["M1c1"],
                       f"multiple extrathoracic metastases in one organ system ({site})",
                       f"单一器官系统多发转移（{zh([site])}）")
        if status == SINGLE:
            return out("M1b", ["M1b"], f"single extrathoracic metastasis ({site})",
                       f"胸腔外单发转移（{zh([site])}）")
        return out(None, ["M1b", "M1c1"],
                   f"extrathoracic metastasis in one organ system ({site}), count not stated",
                   f"单一器官系统转移（{zh([site])}），病灶数目未写明")
    if thoracic:
        return out("M1a", ["M1a"],
                   f"intrathoracic metastatic disease only ({', '.join(thoracic)})",
                   f"仅胸腔内转移（{zh(thoracic)}）")
    return out(None, [], "no metastatic site on record", "未记录转移部位")


def _recorded_m(facts: dict[str, Any]) -> str:
    m = str((facts.get("tnm") or {}).get("m") or "").upper().replace(" ", "")
    return re.sub(r"^(?:YP|YC|RP|RC|C|P|R|Y)(?=M)", "", m)


_M_RANK = {"M0": 0, "M1A": 1, "M1B": 2, "M1C": 3, "M1C1": 3, "M1C2": 4}


def _understaged(recorded: str, candidates: list[str]) -> bool:
    """The recorded M sits BELOW every reading the sites allow. A recorded M
    above the sites is not a contradiction: the record may simply not list
    every site. Bare M1c covers M1c1 and M1c2."""
    if recorded not in _M_RANK:
        return False  # M1, MX: nothing specific to contradict
    floor = min(_M_RANK.get(c.upper(), 0) for c in candidates)
    rank = _M_RANK[recorded]
    if recorded == "M1C" and floor == 4:
        return False
    return rank < floor


def tnm_conflict(facts: dict[str, Any]) -> Optional[str]:
    """A recorded M descriptor BELOW what the recorded sites show, named —
    never repaired. Brain-only contradictions with M0 are ``cns.py``'s."""
    recorded = _recorded_m(facts)
    if not recorded:
        return None
    sites = facts.get("metastatic_sites")
    if not isinstance(sites, dict) or not sites:
        return None
    suggestion = suggest_m(facts)
    if not suggestion["candidates"] or not _understaged(recorded, suggestion["candidates"]):
        return None
    if recorded == "M0":
        listed = [s for s, v in sanitize_sites(sites)[0].items() if v != ABSENT]
        return (f"Metastatic sites are on record ({', '.join(listed)}) but the TNM "
                f"descriptors say M0 — {suggestion['basis']} reads as "
                f"{'/'.join(suggestion['candidates'])}. Reconcile the imaging and the "
                f"descriptors before treating either as true.")
    return (f"Recorded {recorded.replace('M1C', 'M1c').replace('M1A', 'M1a').replace('M1B', 'M1b')} "
            f"but the recorded sites read as at least {'/'.join(suggestion['candidates'])} "
            f"({suggestion['basis']}). AJCC 9: M1a intrathoracic; M1b single "
            f"extrathoracic lesion; M1c1 multiple lesions in one organ system; M1c2 "
            f"multiple organ systems. Reconcile before staging rests on it.")


# --------------------------------------------------------- supportive care

def supportive_notes(facts: dict[str, Any], lang: str = "zh") -> list[str]:
    """Site-driven supportive measures for advanced disease (not regimens)."""
    sites = site_readings(facts)
    zh = lang == "zh"
    notes: list[str] = []
    if sites.get("bone", ABSENT) != ABSENT:
        notes.append(
            "骨转移：评估骨相关事件风险（承重骨、病理性骨折、脊髓压迫）；考虑骨改良药物"
            "（唑来膦酸或地舒单抗），用药前口腔评估并补充钙与维生素 D；疼痛或承重骨病灶考虑姑息放疗"
            if zh else
            "Bone metastases: assess skeletal-event risk (weight-bearing bone, fracture, cord "
            "compression); consider a bone-modifying agent (zoledronic acid or denosumab) after a "
            "dental review with calcium and vitamin D; palliative RT for painful or weight-bearing lesions")
    if sites.get("pleural_effusion", ABSENT) != ABSENT:
        notes.append(
            "恶性胸腔积液：有症状者引流；反复积液考虑留置胸腔导管（IPC）或胸膜固定术"
            if zh else
            "Malignant pleural effusion: drain if symptomatic; for recurrence consider an indwelling "
            "pleural catheter or pleurodesis")
    if sites.get("pericardial", ABSENT) != ABSENT:
        notes.append(
            "心包受累：超声心动图评估心包积液与填塞风险，必要时心包穿刺/开窗"
            if zh else
            "Pericardial involvement: echocardiography for effusion and tamponade risk; "
            "pericardiocentesis or a window when needed")
    if sites.get("liver", ABSENT) != ABSENT:
        notes.append(
            "肝转移：按肝功能（胆红素、ALT/AST）核对每个药物的肝功能门槛"
            if zh else
            "Liver metastases: check each drug's hepatic threshold against bilirubin and ALT/AST")
    if sites.get("adrenal", ABSENT) != ABSENT:
        notes.append(
            "肾上腺转移：双侧受累时警惕肾上腺皮质功能不全（乏力、低钠、低血压）"
            if zh else
            "Adrenal metastases: with bilateral involvement watch for adrenal insufficiency "
            "(fatigue, hyponatraemia, hypotension)")
    return notes
