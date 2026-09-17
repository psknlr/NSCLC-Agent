"""Deterministic eligibility evaluation for KG population criteria.

The extracted criteria (``crit``) are noisy in three specific, observed ways:

* the normalized ``val`` can contradict the source span it was extracted
  from (``val='squamous_cell_carcinoma'`` with ``span='nonsquamous NSCLC'``;
  ``val='resectable'`` with ``span='不可切除'``);
* ``assumed=True`` criteria were inferred from section headings, not stated
  by the guideline sentence;
* whole criteria are sometimes mis-typed (a fusion landing under
  ``histology``).

So this evaluator follows three rules, in place of naive hard matching:

1. **Span first.** Requirements are re-derived from the source span and
   feature text; the normalized ``val`` is a tiebreaker, and when the two
   readings conflict the criterion abstains (``not_evaluable /
   conflicting_extraction``) — it never produces a verdict.
2. **Abstention over guessing.** Only seven feature types with recoverable
   semantics are evaluated (stage_group, histology, gene_variant,
   performance_status, pdl1, resectability, age). Everything else, every
   unparseable value, and every exotic operator abstains, visibly.
3. **Assumed is never confident.** A criterion inferred from a heading, an
   ``exclude``/negated polarity, or a flipped operator can annotate
   (``possible_mismatch``) but can never make the confident
   ``population_mismatch`` verdict that selection logic acts on.

The verdicts are *annotations*: for ``llm_extracted`` entries they inform
ranking and context selection only. Hard use (dropping a mismatched entry,
flagging a matching caution) is reserved for entries whose criteria a
clinician has verified — see the curation workflow in
:mod:`nsclc_agent.knowledge.guideline_kg`.
"""

from __future__ import annotations

import re
from typing import Any

#: Verdicts for one criterion.
MATCH = "match"
MISMATCH = "mismatch"
UNKNOWN = "unknown_case_fact"
NOT_EVALUABLE = "not_evaluable"

#: Overall verdicts for one recommendation against one case.
CONSISTENT = "consistent"
POSSIBLE_MISMATCH = "possible_mismatch"
POPULATION_MISMATCH = "population_mismatch"
INSUFFICIENT = "insufficient_case_data"
NOT_EVALUATED = "not_evaluated"

_EVALUABLE_FTS = ("stage_group", "histology", "gene_variant",
                  "performance_status", "pdl1", "resectability", "age")

#: Operators whose reading is "the feature must be present / equal".
_POSITIVE_OPS = frozenset({"=", "IN", "PRESENT", "HAS_VARIANT",
                           "HAS_VARIANT_CATEGORY"})
#: Operators whose reading is inverted. Evaluated, but never confident.
_FLIP_OPS = frozenset({"!=", "NOT_IN", "ABSENT"})

# Not \b: CJK neighbours are word characters to Python's re, so "IV期"
# has no \b after V. Letter-lookarounds give the boundary we actually mean.
_STAGE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z])(IV|III|II|I)([ABC]\d?|[123])?(?![A-Za-z])|(?<![0-9])0期")
_GENE_RE = re.compile(
    r"\b(EGFR|ALK|ROS1|KRAS|BRAF|MET|RET|ERBB2|HER2|NTRK\d?|NRG1)\b", re.I)
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_RANGE_RE = re.compile(r"(\d)\s*[-~–]\s*(\d)")


def _texts(crit: dict[str, Any]) -> tuple[str, str, str]:
    return (str(crit.get("span") or ""), str(crit.get("feature") or ""),
            str(crit.get("val") or ""))


def _abstain(reason: str) -> tuple[str, str]:
    return NOT_EVALUABLE, reason


# ------------------------------------------------------------------ stage
def _stage_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    lowered = text.lower()
    for m in _STAGE_TOKEN_RE.finditer(text):
        if m.group(0) == "0期":
            tokens.add("0")
            continue
        base = m.group(1)
        tokens.add(base)
        if m.group(2):
            tokens.add(base + m.group(2)[:1].upper())
    if re.search(r"metasta|转移性|iv期", lowered):
        tokens.add("metastatic")
    if re.search(r"locally.advanced|局部晚期", lowered):
        tokens.add("locally_advanced")
    if re.search(r"\badvanced\b|晚期", lowered):
        tokens.add("advanced")
    if re.search(r"\bearly\b|早期", lowered):
        tokens.add("early_stage")
    if re.search(r"\ball\b|所有|各期|any stage", lowered):
        tokens.add("all")
    return tokens


def _eval_stage(crit: dict[str, Any], stage_group: str | None) -> tuple[str, str]:
    from .guideline_kg import _stage_keys

    span, feature, val = _texts(crit)
    required = _stage_tokens(span) | _stage_tokens(feature) | _stage_tokens(val)
    if not required:
        return _abstain("no stage tokens recoverable")
    if not stage_group:
        return UNKNOWN, "case stage not computed"
    case_keys = _stage_keys(stage_group)
    hit = bool(required & case_keys) or "all" in required
    return (MATCH, f"case {stage_group} ∈ {sorted(required)}") if hit else \
        (MISMATCH, f"case {stage_group} ∉ {sorted(required)}")


# -------------------------------------------------------------- histology
def _classify_histology(text: str) -> str | None:
    lowered = text.lower()
    if not lowered.strip():
        return None
    if "腺鳞" in text or "adenosquamous" in lowered:
        return "adenosquamous"
    if re.search(r"non.?squamous|nonsquamous|非鳞", lowered):
        return "non_squamous"
    if re.search(r"squamous|鳞癌|鳞状", lowered):
        return "squamous"
    if re.search(r"adenocarcinoma|腺癌", lowered):
        return "adenocarcinoma"
    if re.search(r"\bsclc\b|小细胞", lowered):
        return "sclc"
    if re.search(r"\bnsclc\b|non.?small|非小细胞|all histolog|任何组织学|"
                 r"histology.?independent|nsclc_nos", lowered):
        return "any_nsclc"
    return None


def _eval_histology(crit: dict[str, Any], facts: dict[str, Any]) -> tuple[str, str]:
    span, feature, val = _texts(crit)
    from_text = _classify_histology(span) or _classify_histology(feature)
    from_val = _classify_histology(val)
    if from_text and from_val and from_text != from_val \
            and "any_nsclc" not in (from_text, from_val):
        # The observed extraction fault: span says nonsquamous, val says
        # squamous_cell_carcinoma. Nobody gets a verdict out of that.
        return _abstain(f"conflicting_extraction ({from_text} vs {from_val})")
    required = from_text or from_val
    if required is None:
        return _abstain("histology requirement not recoverable")
    case = _classify_histology(str(facts.get("histologic_category") or ""))
    if required == "any_nsclc":
        return MATCH, "any NSCLC histology"
    if case is None:
        return UNKNOWN, "case histology not recorded"
    if case == "any_nsclc":
        return UNKNOWN, "case histology is unspecified NSCLC (NOS)"
    if required == "non_squamous":
        if case in ("adenocarcinoma", "non_squamous"):
            return MATCH, f"case {case} is non-squamous"
        if case == "squamous":
            return MISMATCH, "requires non-squamous; case is squamous"
        return UNKNOWN, f"non-squamous status of {case} not asserted"
    if required == case:
        return MATCH, f"case {case}"
    if required == "sclc":
        return MISMATCH, "requires SCLC; this harness stages NSCLC"
    if case == "adenosquamous":
        # Mixed histology: never confidently in or out of a pure-histology
        # population.
        return UNKNOWN, "adenosquamous vs pure-histology criterion"
    return MISMATCH, f"requires {required}; case is {case}"


# ------------------------------------------------------------ gene variant
_WILDTYPE_RE = re.compile(
    r"wild.?type|(?<![A-Za-z])WT(?![A-Za-z])|野生型|阴性|negative|"
    r"no (?:actionable )?driver|无驱动|driver.?negative|不伴", re.I)


def _eval_gene(crit: dict[str, Any], facts: dict[str, Any]) -> tuple[str, str]:
    from .biomarkers import driver_status

    span, feature, val = _texts(crit)
    op = str(crit.get("op") or "")
    text = f"{span} {feature} {val}"
    wants_negative = op == "ABSENT" or bool(_WILDTYPE_RE.search(text))
    gene = str(crit.get("gene") or "").upper()
    if not gene:
        m = _GENE_RE.search(f"{feature} {span}")
        gene = m.group(1).upper() if m else ""
    drivers = facts.get("driver_mutations") or {}
    statuses = {g: driver_status(str(v)) for g, v in drivers.items()}
    if not gene:
        # "no actionable driver" / "driver positive" without a named gene.
        if not statuses:
            return UNKNOWN, "no driver testing on record"
        positives = sorted(g for g, s in statuses.items() if s == "positive")
        if wants_negative:
            if positives:
                return MISMATCH, f"requires driver-negative; case has {positives}"
            if all(s == "negative" for s in statuses.values()):
                return MATCH, "all tested drivers negative"
            return UNKNOWN, "driver panel incomplete"
        if positives:
            return MATCH, f"driver-positive ({positives})"
        return (MISMATCH, "requires a driver; all tested negative") \
            if all(s == "negative" for s in statuses.values()) \
            else (UNKNOWN, "driver panel incomplete")
    aliases = {"HER2": "ERBB2", "ERBB2": "HER2"}
    status = statuses.get(gene.lower())
    if status is None and gene in aliases:
        status = statuses.get(aliases[gene].lower())
    if status is None or status == "unknown":
        return UNKNOWN, f"{gene} status not on record"
    if wants_negative:
        return (MATCH, f"{gene} negative") if status == "negative" else \
            (MISMATCH, f"requires {gene}-negative; case is positive")
    if op in _POSITIVE_OPS:
        # Gene-level match: a specific-variant criterion (exon20ins,
        # uncommon…) is satisfied at gene level and annotated as such —
        # variant-level adjudication is not attempted from extractions.
        return (MATCH, f"{gene} positive (gene-level)") \
            if status == "positive" else \
            (MISMATCH, f"requires {gene} alteration; case is negative")
    return _abstain(f"operator {op!r} not interpreted for gene criteria")


# ------------------------------------------------------------------ ECOG
def _eval_ps(crit: dict[str, Any], facts: dict[str, Any]) -> tuple[str, str]:
    span, feature, val = _texts(crit)
    ecog = facts.get("ecog_ps")
    text = f"{val} {span}"
    m = _RANGE_RE.search(text)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
    else:
        nums = [int(n) for n in _NUM_RE.findall(val) if 0 <= int(n) <= 4]
        if not nums:
            return _abstain(f"no ECOG bounds in {val!r}")
        op = str(crit.get("op") or "=")
        if op == "<=":
            lo, hi = 0, nums[0]
        elif op == ">=":
            lo, hi = nums[0], 4
        elif op in ("=", "<", ">"):
            lo = hi = nums[0]
            if op == "<":
                lo, hi = 0, nums[0] - 1
            elif op == ">":
                lo, hi = nums[0] + 1, 4
        else:
            return _abstain(f"ECOG operator {op!r} without a range")
    if not isinstance(ecog, int):
        return UNKNOWN, "ECOG not recorded"
    return (MATCH, f"ECOG {ecog} in {lo}-{hi}") if lo <= ecog <= hi else \
        (MISMATCH, f"ECOG {ecog} outside {lo}-{hi}")


# ----------------------------------------------------------------- PD-L1
def _eval_pdl1(crit: dict[str, Any], facts: dict[str, Any]) -> tuple[str, str]:
    span, feature, val = _texts(crit)
    if crit.get("neg") or re.search(r"regardless|any|无论|不限", f"{span} {val}",
                                    re.I):
        return MATCH, "any PD-L1 level"
    m = re.match(r"(>=|<=|>|<)\s*(\d+(?:\.\d+)?)", val)
    if not m:
        return _abstain(f"PD-L1 threshold not recoverable from {val!r}")
    op, threshold = m.group(1), float(m.group(2))
    tps = (facts.get("pd_l1") or {}).get("tps")
    if not isinstance(tps, (int, float)):
        return UNKNOWN, "PD-L1 TPS not recorded"
    ok = {"(>=)": tps >= threshold, "(<=)": tps <= threshold,
          "(>)": tps > threshold, "(<)": tps < threshold}[f"({op})"]
    return (MATCH, f"TPS {tps} {op} {threshold:g}") if ok else \
        (MISMATCH, f"TPS {tps} not {op} {threshold:g}")


# ---------------------------------------------------------- resectability
def _classify_resect(text: str) -> str | None:
    lowered = text.lower()
    if not lowered.strip():
        return None
    if re.search(r"术后|postoperative|resected|r0|切除术后", lowered):
        return "POSTOP"
    if re.search(r"不可切除|不适宜手术|unresectable|inoperable", lowered):
        return "UNRESECTABLE"
    if re.search(r"可切除|适宜手术|resectable|suitable for surgery|可手术",
                 lowered):
        return "RESECTABLE"
    return None


def _eval_resect(crit: dict[str, Any], facts: dict[str, Any]) -> tuple[str, str]:
    span, feature, val = _texts(crit)
    from_text = _classify_resect(span) or _classify_resect(feature)
    from_val = _classify_resect(val)
    if from_text and from_val and from_text != from_val:
        # Observed: val='resectable' with span='不可切除'.
        return _abstain(f"conflicting_extraction ({from_text} vs {from_val})")
    required = from_text or from_val
    if required is None:
        return _abstain("resectability requirement not recoverable")
    if required == "POSTOP":
        scenario = str(facts.get("clinical_scenario") or "")
        if re.search(r"术后|postop|resected", scenario, re.I):
            return MATCH, "postoperative case"
        return UNKNOWN, "postoperative status not asserted"
    case = str(facts.get("resectability_category") or "").upper()
    if case not in ("RESECTABLE", "UNRESECTABLE"):
        return UNKNOWN, "resectability not recorded"
    return (MATCH, case.lower()) if case == required else \
        (MISMATCH, f"requires {required.lower()}; case {case.lower()}")


# -------------------------------------------------------------------- age
def _eval_age(crit: dict[str, Any], facts: dict[str, Any]) -> tuple[str, str]:
    val = str(crit.get("val") or "")
    nums = _NUM_RE.findall(val)
    if not nums:
        return _abstain(f"no age bound in {val!r}")
    bound = float(nums[0])
    age = facts.get("age")
    if not isinstance(age, (int, float)):
        return UNKNOWN, "age not recorded"
    op = str(crit.get("op") or "=")
    ok = {">=": age >= bound, "<=": age <= bound, ">": age > bound,
          "<": age < bound, "=": age == bound}.get(op)
    if ok is None:
        return _abstain(f"age operator {op!r} not interpreted")
    return (MATCH, f"age {age} {op} {bound:g}") if ok else \
        (MISMATCH, f"age {age} not {op} {bound:g}")


# ------------------------------------------------------------- evaluation
def evaluate_criterion(
    crit: dict[str, Any], facts: dict[str, Any], stage_group: str | None,
) -> dict[str, Any]:
    ft = str(crit.get("ft") or "")
    op = str(crit.get("op") or "")
    if ft == "stage_group":
        verdict, why = _eval_stage(crit, stage_group)
    elif ft == "histology":
        verdict, why = _eval_histology(crit, facts)
    elif ft == "gene_variant":
        verdict, why = _eval_gene(crit, facts)
    elif ft == "performance_status":
        verdict, why = _eval_ps(crit, facts)
    elif ft == "pdl1":
        verdict, why = _eval_pdl1(crit, facts)
    elif ft == "resectability":
        verdict, why = _eval_resect(crit, facts)
    elif ft == "age":
        verdict, why = _eval_age(crit, facts)
    else:
        verdict, why = _abstain(f"feature type {ft!r} not evaluated")

    flipped = op in _FLIP_OPS and ft not in ("gene_variant",)  # gene handles ABSENT
    if flipped and verdict in (MATCH, MISMATCH):
        verdict = MISMATCH if verdict == MATCH else MATCH
        why = f"inverted ({op}): {why}"
    excluded = str(crit.get("pol") or "include") == "exclude" \
        or bool(crit.get("neg")) and ft != "pdl1"
    if excluded and verdict in (MATCH, MISMATCH):
        verdict = MISMATCH if verdict == MATCH else MATCH
        why = f"exclusion criterion: {why}"

    #: A verdict is confident only when nothing about the criterion needed
    #: guessing: explicitly stated, positively phrased, cleanly parsed.
    confident = (
        verdict in (MATCH, MISMATCH)
        and not crit.get("assumed")
        and not flipped and not excluded
        and "conflicting_extraction" not in why
    )
    return {
        "feature_type": ft,
        "feature": str(crit.get("feature") or ""),
        "source_span": str(crit.get("span") or ""),
        "verdict": verdict,
        "why": why,
        "assumed": bool(crit.get("assumed")),
        "confident": confident,
    }


def evaluate_rec(
    rec: dict[str, Any], facts: dict[str, Any], stage_group: str | None,
) -> dict[str, Any]:
    """All criteria of one recommendation against one case.

    Returns ``{"verdict", "criteria", "confident_mismatches", "matches",
    "unknowns", "not_evaluable"}``. The overall verdict:

    * ``population_mismatch`` — at least one CONFIDENT mismatch;
    * ``possible_mismatch`` — mismatches exist, none confident;
    * ``consistent`` — at least one match, no mismatch;
    * ``insufficient_case_data`` — evaluable criteria, all unknown;
    * ``not_evaluated`` — nothing evaluable.
    """
    details = [evaluate_criterion(c, facts, stage_group)
               for c in rec.get("crit") or []]
    mismatches = [d for d in details if d["verdict"] == MISMATCH]
    confident_mismatches = [d for d in mismatches if d["confident"]]
    matches = [d for d in details if d["verdict"] == MATCH]
    unknowns = [d for d in details if d["verdict"] == UNKNOWN]
    if confident_mismatches:
        verdict = POPULATION_MISMATCH
    elif mismatches:
        verdict = POSSIBLE_MISMATCH
    elif matches:
        verdict = CONSISTENT
    elif unknowns:
        verdict = INSUFFICIENT
    else:
        verdict = NOT_EVALUATED
    return {
        "verdict": verdict,
        "criteria": details,
        "confident_mismatches": len(confident_mismatches),
        "matches": len(matches),
        "unknowns": len(unknowns),
        "not_evaluable": sum(1 for d in details
                             if d["verdict"] == NOT_EVALUABLE),
    }
