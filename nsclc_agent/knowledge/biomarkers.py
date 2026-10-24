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
have first-line driver-directed pathways; KRAS G12C is actionable in later
lines (chemo±IO remains first-line standard). HER2 (ERBB2) tyrosine-kinase-
domain (TKD) mutations gained first-line HER2 TKI options in 2026
(zongertinib, sevabertinib — accelerated approvals on single-arm response
data), so the planner offers them beside chemo±IO; HER2 still informs but
does not veto first-line chemo-immunotherapy, and only the TKD class
qualifies (amplification and extracellular/transmembrane mutations do not).
"""

from __future__ import annotations

import re
from typing import Any

#: Clause-level vocabulary. A report value is split into segments (";",
#: "。", parentheses, newlines) and then sub-clauses (",", "and", "伴"…);
#: each sub-clause is classified on its own, so a negated SECONDARY
#: variant ("T790M not detected") never negates the gene, and a gene is
#: positive only on positive evidence — never by default (v0.7.1 audit:
#: "L858R detected; T790M not detected" read as EGFR-negative and
#: released immunotherapy; "No ALK rearrangement" read as ALK-positive).
_NEG_RE = re.compile(
    r"\bnegative\b|\bneg\b|not\s+detected|not\s+identified|not\s+found|"
    r"not\s+present|none\s+detected|\bno\b|\bwithout\b|absence\s+of|"
    r"\babsent\b|wild[\s-]?type|\bwt\b|\bfalse\b|^\s*\(?-\)?\s*$|\(-\)|"
    r"阴性|陰性|野生型|未检出|未检测到|未见|未发现|无突变|^\s*无\s*$|"
    r"(?<!法)无(?=突变|融合|重排|扩增|改变|变异)|"
    r"无\s*(?=(?:egfr|alk|ros1|kras|braf|met|ret|her2|erbb2|ntrk|nrg1)(?![a-z]))",
    re.I)
_FAIL_RE = re.compile(
    r"\bfailed\b|\bfailure\b|\bqns\b|insufficient|inadequate|equivocal|"
    r"indeterminate|inconclusive|not\s+evaluable|无法检测|样本不足|不合格|"
    r"失败|不确定",
    re.I)
_UNKNOWN_RE = re.compile(
    r"not[_\s]+tested|untested|\bunknown\b|pending|awaiting|in\s+process|"
    r"未检测(?!到)|待回报|待测|不详|送检中",
    re.I)
_STRONG_POS_RE = re.compile(
    r"\bpositive\b|\bpos\b|\btrue\b|\byes\b|阳性|\(\+\)|\+", re.I)
_WEAK_POS_RE = re.compile(
    r"\bdetected\b|\bidentified\b|\bpresent\b|\bfound\b|(?<!未)检出|"
    r"(?<!未)检测到", re.I)
#: Alteration evidence: protein/cDNA changes (one- and three-letter
#: HGVS), exon references, fusion partner notation and alteration words.
_VARIANT_RE = re.compile(
    r"\b[a-z]\d{2,4}(?:[a-z]\b|del|ins|dup|fs|\*|_[a-z]\d+)|"
    r"\bp\.\(?[a-z]{1,3}\d+|\bc\.[\d_+\-]+|"
    r"\bex(?:on)?\s*_?\d+|\d+\s*号?\s*外显子|外显子\s*\d+|"
    r"\b\d{1,2}\s*(?:del|ins)\b|\b(?:del|ins)\s*\d{1,2}\b|"
    r"\b[a-z][a-z0-9]{1,6}-(?:alk|ros1|ret|ntrk\d?)\b|"
    r"\b(?:alk|ros1|ret|ntrk\d?)-[a-z0-9]{2,}\b|"
    r"fusion|rearrang|mutat|mutant|amplif|deletion|insertion|skip|splice|"
    r"突变|融合|重排|扩增|缺失|插入|跳跃|剪切",
    re.I)
_SEGMENT_SPLIT_RE = re.compile(r"[;；。\n|()（）\[\]]")
_SUBCLAUSE_SPLIT_RE = re.compile(
    r"[,，、]|\band\b|\bwith\b|\bbut\b|\bwhile\b|及|和|伴|但", re.I)


def _classify_subclause(text: str) -> dict[str, Any]:
    neg = _NEG_RE.search(text)
    variant = _VARIANT_RE.search(text)
    strong = _STRONG_POS_RE.search(text)
    if _FAIL_RE.search(text) or _UNKNOWN_RE.search(text):
        return {"kind": "unknown"}
    if neg and strong:
        return {"kind": "conflict"}
    if neg:
        # Forward scope ("no T790M", "negative for ex19del, L858R") reaches
        # the bare variants AFTER it; backward scope ("T790M negative")
        # may reach bare variants BEFORE it — which is ambiguous.
        forward = variant is None or neg.start() <= variant.start()
        return {"kind": "negative", "forward": forward}
    if strong or _WEAK_POS_RE.search(text):
        return {"kind": "positive"}
    if variant:
        return {"kind": "bare"}
    return {"kind": "none"}


def _segment_verdict(segment: str) -> tuple[str, list[str]]:
    """(verdict, positive sub-clauses) for one segment."""
    subs = [x.strip() for x in _SUBCLAUSE_SPLIT_RE.split(segment)
            if x and x.strip()]
    kinds = [_classify_subclause(x) for x in subs]
    positive_text: list[str] = []
    ambiguous = False
    forward_neg = False
    for i, (sub, kind) in enumerate(zip(subs, kinds)):
        k = kind["kind"]
        if k == "negative":
            forward_neg = kind["forward"]
            continue
        if k in ("positive", "conflict", "unknown"):
            forward_neg = False
            if k == "positive":
                positive_text.append(sub)
            elif k == "conflict":
                ambiguous = True
            continue
        if k == "bare":
            if forward_neg:
                continue  # inside "negative for …, …" scope
            later_backward = any(
                kk["kind"] == "negative" and not kk["forward"]
                for kk in kinds[i + 1:])
            if later_backward:
                ambiguous = True  # "ex19del and L858R not detected"?
                continue
            positive_text.append(sub)
    if positive_text:
        return "positive", positive_text
    if ambiguous:
        return "ambiguous", []
    if any(k["kind"] == "negative" for k in kinds):
        return "negative", []
    return "unknown", []


def _parse_driver(value: Any) -> tuple[str, str]:
    """(status, positive-evidence text) for one driver report value."""
    if value is True:
        return "positive", ""
    if value is False:
        return "negative", ""
    if isinstance(value, dict):
        value = "; ".join(str(v) for v in value.values() if v is not None)
    elif isinstance(value, (list, tuple, set)):
        value = "; ".join(str(v) for v in value if v is not None)
    text = str(value or "").strip().lower()
    if not text or text in ("none", "null", "n/a", "na", "nan"):
        return "unknown", ""
    verdicts: list[str] = []
    evidence: list[str] = []
    for segment in _SEGMENT_SPLIT_RE.split(text):
        if not segment.strip():
            continue
        verdict, positive = _segment_verdict(segment)
        verdicts.append(verdict)
        evidence.extend(positive)
    if "positive" in verdicts:
        return "positive", "; ".join(evidence)
    if "ambiguous" in verdicts:
        return "unknown", ""  # fail-safe: the report needs a human read
    if "negative" in verdicts:
        return "negative", ""
    return "unknown", ""


def driver_status(value: Any) -> str:
    """Classify one driver-gene report value: 'positive' | 'negative' |
    'unknown'. Positive requires positive evidence; ambiguous reports and
    assay failures are unknown, never guessed."""
    return _parse_driver(value)[0]


def positive_evidence(value: Any) -> str:
    """The sub-clauses of a report value judged positive — the ONLY text
    variant-class patterns may read ("L858R; C797S not detected" must
    not yield a C797S class)."""
    status, evidence = _parse_driver(value)
    if status != "positive":
        return ""
    return evidence or str(value)


def gene_status(facts: dict[str, Any], gene: str) -> str:
    key = _canonical_gene(gene)
    return driver_status(_normalized_drivers(facts).get(key))


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
        r"ex(?:on)?\s*_?19|19\s*del|del\s*19|19\s*号?\s*外显子|外显子\s*19|"
        r"19缺失|e7(?:4[5-9])_[a-z]?\d{3}|glu\s*7(?:4[5-9])|"
        r"c\.22(?:3\d|4\d|5\d)_22\d{2}del", re.I)),
    ("l858r", re.compile(r"l\s*858\s*r|leu\s*858\s*arg|c\.2573t>g", re.I)),
    ("g719x", re.compile(r"g\s*719|gly\s*719", re.I)),
    ("l861q", re.compile(r"l\s*861\s*q|leu\s*861\s*gln", re.I)),
    ("s768i", re.compile(r"s\s*768\s*i|ser\s*768\s*ile", re.I)),
    ("exon20ins", re.compile(
        r"(?:ex(?:on)?\s*_?20|20\s*号?\s*外显子).{0,20}?(?:ins|插入|dup)"
        r"|(?:ins|插入).{0,12}(?:ex(?:on)?\s*_?20|20\s*号?\s*外显子)"
        r"|ex20ins", re.I)),
    ("t790m", re.compile(r"t\s*790\s*m|thr\s*790\s*met|c\.2369c>t", re.I)),
    ("c797s", re.compile(r"c\s*797\s*s|cys\s*797\s*ser", re.I)),
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
    as "sensitizing status unconfirmed", never as classical. Only the
    POSITIVE sub-clauses are read: a negated secondary variant is not a
    class.
    """
    text = positive_evidence(value)
    if not text and driver_status(value) != "positive":
        return frozenset()
    classes = {name for name, pattern in _EGFR_CLASS_PATTERNS
               if pattern.search(text)}
    return frozenset(classes) if classes else frozenset({"unclassified"})


def egfr_classes(facts: dict[str, Any]) -> frozenset[str]:
    return egfr_variant_classes(_normalized_drivers(facts).get("egfr"))


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
#: Known driver genes; a report key like "ROS1_fusion", "ALK
#: rearrangement" or "EGFR mutation" normalizes to its leading gene.
_GENE_KEY_RE = re.compile(
    r"^(egfr|alk|ros1|ret|met|braf|ntrk[123]?|erbb2|her2|kras|nrg1)(?![a-z0-9])")


def _canonical_gene(key: Any) -> str:
    text = str(key).strip().lower()
    match = _GENE_KEY_RE.match(text)
    gene = match.group(1) if match else text
    return _GENE_ALIASES.get(gene, gene)

_MET_EX14_RE = re.compile(
    r"ex(?:on)?\s*_?14|14\s*skip|14\s*号?\s*外显子|14跳跃|跳跃|剪切|"
    r"splice|c\.30(?:28|82)|d\s*1010", re.I)
_BRAF_V600_RE = re.compile(r"v\s*600|val\s*600|c\.1799t>a", re.I)
_KRAS_G12C_RE = re.compile(r"g\s*12\s*c|gly\s*12\s*cys|c\.34g>t", re.I)

#: HER2 (ERBB2) classes. The HER2 TKIs (zongertinib, sevabertinib) are
#: approved for tyrosine-kinase-domain (TKD) activating mutations only.
#: TKD: exon 20 insertions/duplications (A775_G776insYVMA, G776delinsVC,
#: P780_Y781insGSP, G778_P780dup), an explicit "TKD"/kinase-domain report,
#: or a protein change at codons 720–987 (the kinase domain, UniProt
#: P04626: L755S, D769Y, V777L, V842I …). Not TKD: amplification,
#: overexpression (IHC/FISH) and extracellular or transmembrane/
#: juxtamembrane mutations (codons < 700: S310F/Y, V659E, G660D, R678Q).
#: A positive report naming neither stays unclassified — never guessed.
_HER2_TKD_RE = re.compile(
    r"(?:ex(?:on)?\s*_?20|20\s*号?\s*外显子).{0,20}?(?:ins|dup|插入)"
    r"|(?:ins|dup|插入).{0,12}(?:ex(?:on)?\s*_?20|20\s*号?\s*外显子)"
    r"|ex20ins|yvma|\btkd\b|kinase[\s-]+domain|激酶区", re.I)
_HER2_NON_TKD_RE = re.compile(
    r"amplif|扩增|copy[\s-]+number|overexpress|过表达|ihc\s*[23]\s*\+|"
    r"\bfish\b", re.I)
#: A protein change's codon ("a775", "p.leu755ser", "s310f"); cDNA
#: positions (c.2324_2325ins12) and exon numbers never match.
_PROTEIN_CODON_RE = re.compile(r"(?<![a-z0-9.])(?:p\.)?\(?[a-z]{1,3}(\d{3,4})(?!\d)",
                               re.I)


def _normalized_drivers(facts: dict[str, Any]) -> dict[str, Any]:
    """Driver values keyed by canonical gene. Several keys for one gene
    ("egfr" + "EGFR_T790M") are MERGED as clauses, never overwritten —
    a later negative key must not erase an earlier positive one, and an
    empty alias key ("her2": "exon 20 insertion", "erbb2": None) must not
    erase a recorded result."""
    out: dict[str, Any] = {}
    for gene, value in (facts.get("driver_mutations") or {}).items():
        key = _canonical_gene(gene)
        if key in out and value is None:
            continue
        if key in out and out[key] is not None:
            out[key] = f"{_as_text(out[key])}; {_as_text(value)}"
        else:
            out[key] = value
    return out


def _as_text(value: Any) -> str:
    if value is True:
        return "positive"
    if value is False:
        return "negative"
    return str(value)


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
    if value is not None and _MET_EX14_RE.search(positive_evidence(value)):
        found.append({"gene": "MET", "value": str(value),
                      "classes": ["ex14_skipping"]})
    value = positive("braf")
    if value is not None and _BRAF_V600_RE.search(positive_evidence(value)):
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
            signature[gene] = (["ex14_skipping"] if _MET_EX14_RE.search(
                positive_evidence(value)) else [])
        elif gene == "braf":
            signature[gene] = (["v600e"] if _BRAF_V600_RE.search(
                positive_evidence(value)) else [])
        elif gene == "kras":
            signature[gene] = (["g12c"] if _KRAS_G12C_RE.search(
                positive_evidence(value)) else [])
        elif gene == "erbb2":
            signature[gene] = [her2_variant_class(value) or "unclassified"]
        else:
            signature[gene] = []
    return signature


def her2_variant_class(value: Any) -> str | None:
    """``"tkd"`` | ``"non_tkd"`` | ``"unclassified"`` for a positive ERBB2
    report value; None when the value is not positive. A TKD alteration
    anywhere in the report wins (co-amplification does not remove a TKD
    mutation from the HER2 TKI population)."""
    text = positive_evidence(value)
    if not text and driver_status(value) != "positive":
        return None
    codons = [int(m.group(1)) for m in _PROTEIN_CODON_RE.finditer(text)]
    if _HER2_TKD_RE.search(text) or any(720 <= c <= 987 for c in codons):
        return "tkd"
    if _HER2_NON_TKD_RE.search(text) or any(c < 700 for c in codons):
        return "non_tkd"
    return "unclassified"


def her2_class(facts: dict[str, Any]) -> str | None:
    """The case's HER2 class (see :func:`her2_variant_class`)."""
    return her2_variant_class(_normalized_drivers(facts).get("erbb2"))


#: A mutation named as such ("HER2 mutation", "突变", "S310F", "ins"/"dup").
_HER2_MUTATION_WORD_RE = re.compile(r"mutat|mutant|突变|\bins|\bdup|delins",
                                    re.I)


def her2_is_mutation(value: Any) -> bool | None:
    """True for a positive ERBB2 report that names a mutation (TKD or not —
    DESTINY-Lung02 enrolled activating mutations of either kind), False for
    amplification or overexpression alone, None when a positive report
    says neither (and for a value that is not positive)."""
    variant = her2_variant_class(value)
    if variant is None:
        return None
    if variant == "tkd":
        return True
    text = positive_evidence(value)
    if _PROTEIN_CODON_RE.search(text) or _HER2_MUTATION_WORD_RE.search(text):
        return True
    if _HER2_NON_TKD_RE.search(text):
        return False
    return None


def later_line_actionable_drivers(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """Actionable in later lines only: informs, never vetoes first-line."""
    drivers = _normalized_drivers(facts)
    found: list[dict[str, Any]] = []
    value = drivers.get("erbb2")
    if value is not None and driver_status(value) == "positive":
        cls = her2_variant_class(value)
        note = {
            "tkd": "TKD mutation — first-line HER2 TKIs (zongertinib/"
                   "sevabertinib, accelerated approvals, non-squamous) beside "
                   "chemo±IO; T-DXd in later lines (DESTINY-Lung02)",
            "non_tkd": "not a TKD mutation (amplification/overexpression or "
                       "extracellular/transmembrane variant) — the HER2 TKI "
                       "approvals do not apply; later-line T-DXd needs an "
                       "activating HER2 mutation (DESTINY-Lung02), and HER2 "
                       "IHC 3+ falls under its tumor-agnostic approval (not "
                       "encoded)",
        }.get(cls, "variant class not reported — the exact ERBB2 alteration "
                   "(TKD vs non-TKD) decides HER2 TKI eligibility; first line "
                   "stays chemo±IO until it is known; T-DXd in later lines")
        found.append({"gene": "HER2/ERBB2", "value": str(value),
                      "class": cls, "note": note})
    value = drivers.get("kras")
    if value is not None and driver_status(value) == "positive" \
            and _KRAS_G12C_RE.search(positive_evidence(value)):
        found.append({"gene": "KRAS G12C", "value": str(value),
                      "note": "sotorasib/adagrasib in subsequent lines; "
                              "first-line remains chemo±IO"})
    value = drivers.get("nrg1")
    if value is not None and driver_status(value) == "positive":
        found.append({"gene": "NRG1", "value": str(value),
                      "note": "zenocutuzumab after prior systemic therapy "
                              "(eNRGy; FDA 2024) — not a library regimen; "
                              "first-line follows the driver-negative table"})
    return found
