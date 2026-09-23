"""Shared driver ontology: status AND alteration class, one reading.

Two lessons shaped this module, both from review:

1. Three private copies of positive/negative parsing drifted ("negative for
   mutation" read positive). Status is decided ONCE here, by substring
   evidence with precedence negative > unknown > positive.
2. **"Positive" is not a treatment decision.** External clinical red-team
   review demonstrated that collapsing EGFR to a boolean let the planner
   release first-line osimertinib for an *exon 20 insertion* — and the
   safety rules, built on the same boolean, agreed. EGFR ex19del/L858R,
   uncommon sensitizing (G719X/L861Q/S768I), exon20 insertions, T790M and
   C797S are DIFFERENT treatment populations (FLAURA/ADAURA/LAURA enrolled
   ex19del/L858R; exon20 insertions take amivantamab + chemotherapy per
   PAPILLON). The variant-class layer below is therefore shared vocabulary
   for planner and rule engine alike — same ontology, independently applied
   logic, so a class the planner mishandles is still caught by the rules.

The first-line actionable-driver set follows the current ASCO living
guideline: EGFR, ALK, ROS1, RET, MET exon 14 skipping, BRAF V600E and NTRK
have first-line driver-directed pathways; HER2 (ERBB2) mutation and KRAS
G12C are actionable in later lines (chemo±IO remains first-line standard),
so they inform but do not veto first-line chemo-immunotherapy.
"""

from __future__ import annotations

import re
from typing import Any

_NEGATIVE_MARKERS = (
    "negative", "not detected", "no mutation", "wild type", "wildtype", "wt",
    "none detected", "not present", "absent", "阴性", "野生型", "未检出", "无突变",
)
_UNKNOWN_MARKERS = (
    "not_tested", "not tested", "untested", "unknown", "pending", "awaiting",
    "in process", "未检测", "待回报", "待测", "不详",
)


def driver_status(value: Any) -> str:
    """Classify one driver-gene report value: 'positive' | 'negative' | 'unknown'."""
    text = str(value or "").strip().lower()
    if not text or text in ("none", "null", "n/a", "na"):
        return "unknown"
    if any(marker in text for marker in _NEGATIVE_MARKERS):
        return "negative"
    if any(marker in text for marker in _UNKNOWN_MARKERS):
        return "unknown"
    return "positive"


def gene_status(facts: dict[str, Any], gene: str) -> str:
    drivers = facts.get("driver_mutations") or {}
    value = drivers.get(gene.lower(), drivers.get(gene.upper()))
    return driver_status(value)


def driver_positive(facts: dict[str, Any], *genes: str) -> bool:
    return any(gene_status(facts, g) == "positive" for g in genes)


def driver_negative(facts: dict[str, Any], *genes: str) -> bool:
    return all(gene_status(facts, g) == "negative" for g in genes)


def driver_unknown(facts: dict[str, Any], gene: str) -> bool:
    return gene_status(facts, gene) == "unknown"


# ---------------------------------------------------------------------------
# EGFR alteration classes
# ---------------------------------------------------------------------------

#: Ordered (class, pattern). A value can carry several classes
#: ("L858R + T790M" → {l858r, t790m}); patterns are bilingual and tolerant
#: of report phrasing. `exon20ins` requires an insertion word so
#: "exon 20 T790M" never reads as an insertion.
_EGFR_CLASS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ex19del", re.compile(
        r"ex(?:on)?\s*_?19|19\s*del|del\s*19|19外显子|19缺失", re.I)),
    ("l858r", re.compile(r"l\s*858\s*r", re.I)),
    ("g719x", re.compile(r"g\s*719", re.I)),
    ("l861q", re.compile(r"l\s*861\s*q", re.I)),
    ("s768i", re.compile(r"s\s*768\s*i", re.I)),
    ("exon20ins", re.compile(
        r"(?:ex(?:on)?\s*_?20|20外显子).{0,20}?(?:ins|插入|dup)"
        r"|(?:ins|插入).{0,12}(?:ex(?:on)?\s*_?20|20外显子)"
        r"|ex20ins", re.I)),
    ("t790m", re.compile(r"t\s*790\s*m", re.I)),
    ("c797s", re.compile(r"c\s*797\s*s", re.I)),
)

#: The FLAURA / ADAURA / LAURA / MARIPOSA population.
EGFR_CLASSICAL_SENSITIZING = frozenset({"ex19del", "l858r"})
#: Distinct evidence base (afatinib pooled analyses; osimertinib data
#: separate from FLAURA) — never silently equated with classical.
EGFR_UNCOMMON_SENSITIZING = frozenset({"g719x", "l861q", "s768i"})


def egfr_variant_classes(value: Any) -> frozenset[str]:
    """Alteration classes named by one EGFR report value.

    Empty when the value is not positive; ``{"unclassified"}`` when it is
    positive but names no recognizable class — the planner must treat that
    as "sensitizing status unconfirmed", never as classical.
    """
    if driver_status(value) != "positive":
        return frozenset()
    text = str(value or "")
    classes = {name for name, pattern in _EGFR_CLASS_PATTERNS
               if pattern.search(text)}
    return frozenset(classes) if classes else frozenset({"unclassified"})


def egfr_classes(facts: dict[str, Any]) -> frozenset[str]:
    drivers = facts.get("driver_mutations") or {}
    value = drivers.get("egfr", drivers.get("EGFR"))
    return egfr_variant_classes(value)


def egfr_classical(facts: dict[str, Any]) -> bool:
    """True only for the qualifying FLAURA/ADAURA/LAURA population: a
    classical sensitizing alteration present AND no co-occurring class
    (exon20 insertion, C797S) that removes the case from that population."""
    classes = egfr_classes(facts)
    return bool(classes & EGFR_CLASSICAL_SENSITIZING) \
        and not (classes & {"exon20ins", "c797s"})


# ---------------------------------------------------------------------------
# The first-line actionable-driver plane
# ---------------------------------------------------------------------------

_GENE_ALIASES = {
    "her2": "erbb2", "ntrk1": "ntrk", "ntrk2": "ntrk", "ntrk3": "ntrk",
    "met_ex14": "met", "metex14": "met",
}

_MET_EX14_RE = re.compile(r"ex(?:on)?\s*_?14|14\s*skip|14跳跃|跳跃", re.I)
_BRAF_V600_RE = re.compile(r"v\s*600", re.I)
_KRAS_G12C_RE = re.compile(r"g\s*12\s*c", re.I)


def _normalized_drivers(facts: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for gene, value in (facts.get("driver_mutations") or {}).items():
        key = str(gene).strip().lower()
        out[_GENE_ALIASES.get(key, key)] = value
    return out


def first_line_actionable_drivers(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """Positive drivers with a FIRST-LINE driver-directed pathway.

    Variant-aware where the indication is variant-defined: MET counts only
    for exon 14 skipping (amplification is a different question), BRAF only
    for V600. Order is the planner's priority order. HER2 and KRAS G12C are
    deliberately absent — see the module docstring.
    """
    drivers = _normalized_drivers(facts)
    found: list[dict[str, Any]] = []

    def positive(gene: str) -> Any | None:
        value = drivers.get(gene)
        return value if value is not None \
            and driver_status(value) == "positive" else None

    value = positive("egfr")
    if value is not None:
        found.append({"gene": "EGFR", "value": str(value),
                      "classes": sorted(egfr_variant_classes(value))})
    for gene, label in (("alk", "ALK"), ("ros1", "ROS1"), ("ret", "RET")):
        value = positive(gene)
        if value is not None:
            found.append({"gene": label, "value": str(value), "classes": []})
    value = positive("met")
    if value is not None and _MET_EX14_RE.search(str(value)):
        found.append({"gene": "MET", "value": str(value),
                      "classes": ["ex14_skipping"]})
    value = positive("braf")
    if value is not None and _BRAF_V600_RE.search(str(value)):
        found.append({"gene": "BRAF", "value": str(value),
                      "classes": ["v600e"]})
    value = positive("ntrk")
    if value is not None:
        found.append({"gene": "NTRK", "value": str(value), "classes": []})
    return found


def population_signature(facts: dict[str, Any]) -> dict[str, list[str]]:
    """Positive drivers as a JSON-safe signature for claim subjects.

    Keys are normalized gene names, values the variant-class tags the
    ontology can derive (empty when the gene is positive but class-free).
    Genes that are negative or untested are absent — absence means "not
    positive here", never "not tested" (the workup posture for untested
    genes is the indication layer's job, not the claim subject's).
    """
    signature: dict[str, list[str]] = {}
    for gene, value in _normalized_drivers(facts).items():
        if driver_status(value) != "positive":
            continue
        if gene == "egfr":
            signature[gene] = sorted(egfr_variant_classes(value))
        elif gene == "met":
            signature[gene] = (["ex14_skipping"]
                               if _MET_EX14_RE.search(str(value)) else [])
        elif gene == "braf":
            signature[gene] = (["v600e"]
                               if _BRAF_V600_RE.search(str(value)) else [])
        elif gene == "kras":
            signature[gene] = (["g12c"]
                               if _KRAS_G12C_RE.search(str(value)) else [])
        else:
            signature[gene] = []
    return signature


def later_line_actionable_drivers(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """Actionable in later lines only: informs, never vetoes first-line."""
    drivers = _normalized_drivers(facts)
    found: list[dict[str, Any]] = []
    value = drivers.get("erbb2")
    if value is not None and driver_status(value) == "positive":
        found.append({"gene": "HER2/ERBB2", "value": str(value),
                      "note": "T-DXd in later lines (DESTINY-Lung02); "
                              "first-line remains chemo±IO"})
    value = drivers.get("kras")
    if value is not None and driver_status(value) == "positive" \
            and _KRAS_G12C_RE.search(str(value)):
        found.append({"gene": "KRAS G12C", "value": str(value),
                      "note": "sotorasib/adagrasib in subsequent lines; "
                              "first-line remains chemo±IO"})
    return found
