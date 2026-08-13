"""Evidence retrieval: make cited identifiers verifiable instead of recalled."""

from __future__ import annotations

import os
from typing import Any, Optional

from .base import (
    EvidenceRecord,
    NullRetriever,
    RetrievalError,
    RetrievalResult,
    Retriever,
    evidence_block,
)
from .pubmed import PubMedRetriever

KNOWN_RETRIEVERS = ("none", "pubmed")

__all__ = [
    "EvidenceRecord",
    "NullRetriever",
    "PubMedRetriever",
    "RetrievalError",
    "RetrievalResult",
    "Retriever",
    "build_retriever",
    "evidence_block",
    "query_plan",
    "KNOWN_RETRIEVERS",
]


def build_retriever(cfg: Optional[dict[str, Any]]) -> Retriever:
    """Build a retriever from an ``evidence:`` config block.

    An absent or disabled block yields :class:`NullRetriever`, which retrieves
    nothing *and records that it retrieved nothing* — the state the prompt and
    the flags key on.
    """
    if not cfg:
        return NullRetriever()
    kind = str(cfg.get("type") or cfg.get("backend") or "none").lower()
    if cfg.get("enabled") is False or kind == "none":
        return NullRetriever()
    if kind == "pubmed":
        api_key = cfg.get("api_key")
        env = cfg.get("api_key_env")
        if not api_key and env:
            api_key = os.environ.get(env) or None
        return PubMedRetriever(
            email=cfg.get("email"),
            api_key=api_key,
            tool=cfg.get("tool", "nsclc-agent"),
            timeout=float(cfg.get("timeout", 20.0)),
            default_years=cfg.get("years", 5),
        )
    raise RetrievalError(
        f"Unknown evidence backend {kind!r}. Known: "
        f"{', '.join(KNOWN_RETRIEVERS)}"
    )


#: Query templates keyed by stage band. Deterministic rather than model-written,
#: so the same case always retrieves the same evidence set.
_STAGE_QUERIES: dict[str, tuple[str, ...]] = {
    "0": ("NSCLC stage I adenocarcinoma in situ management guideline",),
    "I": (
        "stage I non-small cell lung cancer lobectomy versus segmentectomy",
        "stage I NSCLC medically inoperable SBRT outcomes",
    ),
    "II": (
        "resected stage II non-small cell lung cancer adjuvant chemotherapy",
        "adjuvant immunotherapy resected NSCLC randomized trial",
    ),
    "III": (
        "stage III non-small cell lung cancer resectable perioperative "
        "immunotherapy randomized",
        "unresectable stage III NSCLC concurrent chemoradiotherapy "
        "consolidation randomized",
    ),
    "IV": (
        "metastatic non-small cell lung cancer first-line treatment "
        "randomized trial",
        "oligometastatic NSCLC local consolidative therapy randomized",
    ),
}

_BIOMARKER_QUERIES: tuple[tuple[str, str], ...] = (
    ("egfr", "EGFR mutated non-small cell lung cancer osimertinib randomized"),
    ("alk", "ALK positive non-small cell lung cancer targeted therapy "
            "randomized"),
)


def query_plan(
    stage_group: Optional[str],
    facts: Optional[dict[str, Any]] = None,
    *,
    limit: int = 3,
) -> list[str]:
    """Deterministic retrieval queries for a case.

    Built from the *computed* stage band and the *known* biomarker facts, never
    from model output — so retrieval cannot be steered by a hallucinated claim.
    """
    from ..consult.slots import stage_band  # local: avoids a package cycle

    facts = facts or {}
    band = stage_band(stage_group)
    queries = list(_STAGE_QUERIES.get(band, ()))
    for key, template in _BIOMARKER_QUERIES:
        value = str(facts.get(key) or "").lower()
        if value and value not in ("negative", "not_tested", "wild",
                                   "wild-type", "unknown"):
            queries.append(template)
    return queries[:max(1, limit)]
