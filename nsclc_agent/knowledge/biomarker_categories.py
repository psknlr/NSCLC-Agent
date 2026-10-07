"""Biomarker categories of advanced NSCLC (NCCN-style NSCL-21 … NSCL-39).

The test results decide which treatment category a patient belongs to.
Fourteen categories: twelve actionable alterations, then PD-L1 ≥1% or
<1% once every one of those twelve is NEGATIVE. The table is shared
reference knowledge: the governed planner and the kernel tools compute it,
and in full autonomy the model is given the same table and classifies the
case itself.

Classification is marker-level, not gene-level: KRAS G12D is negative for
the G12C category, MET amplification says nothing about exon 14 skipping,
HER2 amplification or IHC is not an ERBB2 mutation, an EGFR report that
names no variant class cannot be placed. Anything the report does not
settle is UNKNOWN, and a PD-L1 category is only assigned once nothing is
unknown — "not tested" is never read as "negative".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .biomarkers import (
    EGFR_CLASSICAL_SENSITIZING, EGFR_UNCOMMON_SENSITIZING, _BRAF_V600_RE,
    _KRAS_G12C_RE, _MET_EX14_RE, _normalized_drivers, driver_status,
    egfr_variant_classes, positive_evidence,
)

POSITIVE, NEGATIVE, UNKNOWN = "positive", "negative", "unknown"


@dataclass(frozen=True)
class Category:
    code: str
    marker: str
    label_zh: str
    label_en: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "marker": self.marker,
                "label_zh": self.label_zh, "label_en": self.label_en}


#: The twelve actionable alterations, in table order.
TARGETABLE: tuple[Category, ...] = (
    Category("NSCL-21", "egfr_common", "EGFR 19号外显子缺失或 L858R 突变阳性",
             "EGFR exon 19 deletion or L858R mutation positive"),
    Category("NSCL-24", "egfr_uncommon", "EGFR S768I、L861Q 和/或 G719X 突变阳性",
             "EGFR S768I, L861Q and/or G719X mutation positive"),
    Category("NSCL-25", "egfr_ex20ins", "EGFR 20号外显子插入突变阳性",
             "EGFR exon 20 insertion mutation positive"),
    Category("NSCL-26", "kras_g12c", "KRAS G12C 突变阳性", "KRAS G12C mutation positive"),
    Category("NSCL-27", "alk", "ALK 基因融合阳性", "ALK gene fusion positive"),
    Category("NSCL-30", "ros1", "ROS1 基因融合阳性", "ROS1 gene fusion positive"),
    Category("NSCL-32", "braf_v600e", "BRAF V600E 突变阳性", "BRAF V600E mutation positive"),
    Category("NSCL-33", "ntrk", "NTRK1/2/3 基因融合阳性", "NTRK1/2/3 gene fusion positive"),
    Category("NSCL-34", "met_ex14", "MET 14号外显子跳跃突变阳性",
             "MET exon 14 skipping mutation positive"),
    Category("NSCL-35", "ret", "RET 基因融合阳性", "RET gene fusion positive"),
    Category("NSCL-36", "erbb2", "ERBB2（HER2）突变阳性", "ERBB2 (HER2) mutation positive"),
    Category("NSCL-37", "nrg1", "NRG1 基因融合阳性", "NRG1 gene fusion positive"),
)
PDL1_POSITIVE = Category("NSCL-38", "pd_l1", "PD-L1 ≥1%，且上述可靶向生物标志物均为阴性",
                         "PD-L1 ≥1% and negative for all the actionable biomarkers above")
PDL1_NEGATIVE = Category("NSCL-39", "pd_l1", "PD-L1 <1%，且上述可靶向生物标志物均为阴性",
                         "PD-L1 <1% and negative for all the actionable biomarkers above")
CATEGORIES: tuple[Category, ...] = TARGETABLE + (PDL1_POSITIVE, PDL1_NEGATIVE)
BY_CODE = {c.code: c for c in CATEGORIES}

#: Marker → what to test when it is unknown (the work-up the table needs).
MARKER_TEST = {
    "egfr_common": "EGFR (exon 19 / L858R)", "egfr_uncommon": "EGFR (G719X / L861Q / S768I)",
    "egfr_ex20ins": "EGFR exon 20 insertion", "kras_g12c": "KRAS G12C", "alk": "ALK fusion",
    "ros1": "ROS1 fusion", "braf_v600e": "BRAF V600E", "ntrk": "NTRK1/2/3 fusion",
    "met_ex14": "MET exon 14 skipping", "ret": "RET fusion", "erbb2": "ERBB2 (HER2) mutation",
    "nrg1": "NRG1 fusion",
}

#: A named variant other than the category-defining one (KRAS G12D, BRAF
#: G469A / class 2–3, "non-V600"): the gene is positive, the marker is not.
_OTHER_KRAS_RE = re.compile(r"g\s*12\s*[advrs](?![a-z])|g\s*13|q\s*61|a\s*146|k\s*117", re.I)
_OTHER_BRAF_RE = re.compile(r"(?!v\s*600)\b[a-z]\s*\d{3}\s*[a-z]\b|非\s*v600|non-?v600|class\s*[23]", re.I)
_HER2_NOT_MUTATION_RE = re.compile(r"扩增|amplif|过表达|overexpress|ihc|免疫组化|3\+|2\+", re.I)
_HER2_MUTATION_RE = re.compile(r"突变|mutat|插入|ins|yvma|exon\s*20|20\s*号?外显子|p\.", re.I)


def _status(value: Any) -> str:
    return driver_status(value) if value is not None else UNKNOWN


def marker_status(facts: dict[str, Any]) -> dict[str, str]:
    """positive / negative / unknown for each of the twelve markers."""
    drivers = _normalized_drivers(facts or {})
    out: dict[str, str] = {}

    egfr = drivers.get("egfr")
    st = _status(egfr)
    if st == POSITIVE:
        classes = egfr_variant_classes(egfr)
        placed = classes - {"t790m", "c797s", "unclassified"}
        if not placed:
            # EGFR positive but no category-defining class named (or only
            # an acquired-resistance class): the report needs a human read.
            out.update(egfr_common=UNKNOWN, egfr_uncommon=UNKNOWN, egfr_ex20ins=UNKNOWN)
        else:
            out["egfr_common"] = POSITIVE if classes & EGFR_CLASSICAL_SENSITIZING else NEGATIVE
            out["egfr_uncommon"] = POSITIVE if classes & EGFR_UNCOMMON_SENSITIZING else NEGATIVE
            out["egfr_ex20ins"] = POSITIVE if "exon20ins" in classes else NEGATIVE
    else:
        out.update(egfr_common=st, egfr_uncommon=st, egfr_ex20ins=st)

    def variant(gene: str, defining: re.Pattern[str], other: re.Pattern[str] | None) -> str:
        value = drivers.get(gene)
        st = _status(value)
        if st != POSITIVE:
            return st
        evidence = positive_evidence(value)
        if defining.search(evidence):
            return POSITIVE
        if other is not None and other.search(evidence):
            return NEGATIVE
        return UNKNOWN

    out["kras_g12c"] = variant("kras", _KRAS_G12C_RE, _OTHER_KRAS_RE)
    out["braf_v600e"] = variant("braf", _BRAF_V600_RE, _OTHER_BRAF_RE)
    out["met_ex14"] = variant("met", _MET_EX14_RE, None)
    for gene in ("alk", "ros1", "ntrk", "ret", "nrg1"):
        out[gene] = _status(drivers.get(gene))
    her2 = drivers.get("erbb2")
    st = _status(her2)
    if st == POSITIVE:
        evidence = positive_evidence(her2)
        if _HER2_NOT_MUTATION_RE.search(evidence) and not _HER2_MUTATION_RE.search(evidence):
            st = UNKNOWN  # amplification / IHC reported, mutation status not
    out["erbb2"] = st
    return out


def _tps(facts: dict[str, Any]) -> float | None:
    raw = (facts.get("pd_l1") or {}).get("tps") if isinstance(facts.get("pd_l1"), dict) else None
    try:
        return float(raw) if raw is not None and not isinstance(raw, bool) else None
    except (TypeError, ValueError):
        return None


def classify(facts: dict[str, Any]) -> dict[str, Any]:
    """The case's biomarker category (or categories) with what is missing.

    status: ``targetable`` (≥1 actionable alteration), ``pd_l1`` (all twelve
    negative, PD-L1 known), ``pd_l1_pending`` (all negative, PD-L1 not on
    record) or ``incomplete`` (some markers untested / unreadable)."""
    markers = marker_status(facts)
    positive = [c for c in TARGETABLE if markers[c.marker] == POSITIVE]
    missing = [c for c in TARGETABLE if markers[c.marker] == UNKNOWN]
    tps = _tps(facts)
    if positive:
        status, chosen = "targetable", positive
    elif missing:
        status, chosen = "incomplete", []
    elif tps is None:
        status, chosen = "pd_l1_pending", []
    else:
        status, chosen = "pd_l1", [PDL1_POSITIVE if tps >= 1 else PDL1_NEGATIVE]
    to_test = [MARKER_TEST[c.marker] for c in missing]
    if status == "pd_l1_pending":
        to_test.append("PD-L1 (TPS)")
    return {
        "status": status,
        "codes": [c.code for c in chosen],
        "categories": [c.to_dict() for c in chosen],
        "markers": markers,
        "missing": to_test,
        "pd_l1_tps": tps,
        "summary_zh": summary(status, chosen, to_test, "zh"),
        "summary_en": summary(status, chosen, to_test, "en"),
    }


def summary(status: str, chosen: list[Category], to_test: list[str], lang: str) -> str:
    if chosen:
        labels = "；".join(f"{c.code}（{c.label_zh}）" for c in chosen) if lang == "zh" else \
            "; ".join(f"{c.code} ({c.label_en})" for c in chosen)
        return labels
    if lang == "zh":
        return ("可靶向生物标志物尚未全部明确，待检：" + "、".join(to_test)
                if status == "incomplete" else "可靶向生物标志物均为阴性，待补 PD-L1（TPS）")
    return ("Actionable biomarkers not all settled; still to test: " + ", ".join(to_test)
            if status == "incomplete" else "All actionable biomarkers negative; PD-L1 (TPS) pending")


def table(lang: str = "zh") -> list[dict[str, str]]:
    """The category table for prompts and the interface."""
    return [{"code": c.code, "label": c.label_zh if lang == "zh" else c.label_en}
            for c in CATEGORIES]


def prompt_table(lang: str = "zh") -> str:
    return "\n".join(f"- {row['code']}：{row['label']}" if lang == "zh"
                     else f"- {row['code']}: {row['label']}" for row in table(lang))
