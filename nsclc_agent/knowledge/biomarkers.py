"""Shared driver-status interpretation.

One reading, used by the rule engine, the deterministic planner and the dose
gates — three private copies drifted in review: "negative for mutation" read
as *positive* because the negative vocabulary was an exact-match set. Status
is now decided by substring evidence with the precedence
negative > unknown > positive, so a value that *names* negativity anywhere
("negative for EGFR mutation", "EGFR野生型") is negative, an explicitly
pending value is unknown, and only a value that asserts something beyond
those reads positive.
"""

from __future__ import annotations

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
