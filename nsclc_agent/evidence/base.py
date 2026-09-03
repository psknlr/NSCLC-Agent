"""Evidence retrieval interface.

The protocol modules ask the model to emit ``tool_call`` steps naming
``pubmed_search``/``guideline_search``/… — but nothing ever executed them, so
every PMID, NCT number and hazard ratio in the output was recalled from the
model's weights and presented in a field that looks like a citation. That is
the most dangerous failure mode in an "evidence-based" system, because a
fabricated identifier is indistinguishable from a real one at a glance.

This layer closes the gap without pretending to more than it does:

* When a retriever is configured, the queries run *before* the reasoning call
  and the real records are injected into the prompt. The model is told to cite
  only from that list, and anything else it names must be labelled unverified.
* When no retriever is configured, that fact is injected too — explicitly — and
  the run is flagged ``EVIDENCE_NOT_RETRIEVED``. Silence is not an option: the
  reader of a result must always be able to tell which mode produced it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


class RetrievalError(RuntimeError):
    """Raised when a retrieval backend fails irrecoverably."""


@dataclass
class EvidenceRecord:
    """One retrieved source. Identifiers here were actually returned by a
    search backend — they are not model output."""

    source_type: str          # "PMID" | "NCT" | "DOI" | "GUIDELINE" | "OTHER"
    source_id: str
    title: str = ""
    date: Optional[str] = None
    journal: Optional[str] = None
    url: Optional[str] = None
    snippet: Optional[str] = None
    retrieved_by: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "title": self.title,
            "date": self.date,
            "journal": self.journal,
            "url": self.url,
            "snippet": self.snippet,
            "retrieved_by": self.retrieved_by,
            "provenance": "RETRIEVED_VERIFIED_IDENTIFIER",
        }

    def cite(self) -> str:
        bits = [f"{self.source_type}:{self.source_id}"]
        if self.title:
            bits.append(self.title)
        if self.journal or self.date:
            bits.append(" ".join(x for x in (self.journal, self.date) if x))
        return " — ".join(bits)


@dataclass
class RetrievalResult:
    """Everything one retrieval pass produced, including what it could not do."""

    records: list[EvidenceRecord] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    backend: str = "none"
    enabled: bool = False

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "backend": self.backend,
            "queries": list(self.queries),
            "n_records": len(self.records),
            "records": [r.to_dict() for r in self.records],
            "errors": list(self.errors),
        }


class Retriever(ABC):
    """A source of verifiable evidence identifiers."""

    name: str = "base"
    enabled: bool = True

    @abstractmethod
    def search(self, query: str, *, limit: int = 5) -> list[EvidenceRecord]:
        """Return records matching a query. Must not raise on empty results."""

    def search_many(self, queries: list[str], *,
                    limit: int = 5) -> RetrievalResult:
        result = RetrievalResult(backend=self.name, enabled=self.enabled,
                                 queries=list(queries))
        seen: set[tuple[str, str]] = set()
        for query in queries:
            try:
                for record in self.search(query, limit=limit):
                    key = (record.source_type, record.source_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    result.records.append(record)
            except Exception as exc:  # a dead backend must not kill the run
                result.errors.append(f"{self.name}: {query!r}: {exc}")
        return result


class NullRetriever(Retriever):
    """The honest default: retrieves nothing and says so.

    Used when no retriever is configured. It exists so that "no retrieval
    happened" is a *recorded state* rather than an absence, which is what lets
    the prompt and the flags tell the two modes apart.
    """

    name = "none"
    enabled = False

    def search(self, query: str, *, limit: int = 5) -> list[EvidenceRecord]:
        return []


def evidence_block(result: RetrievalResult, *, lang: str = "en") -> str:
    """Render retrieval output as a prompt block with the right instruction."""
    if not result.enabled:
        return (
            "=== EVIDENCE RETRIEVAL: NOT PERFORMED ===\n"
            "No literature retrieval backend is configured for this run, so "
            "NO tool call in your output was actually executed. Any PMID, NCT "
            "number, DOI, hazard ratio or approval date you produce comes from "
            "recall, not from a search.\n"
            "Therefore: mark every entry in a `sources` array with "
            "\"provenance\": \"MODEL_RECALL_UNVERIFIED\", do not present "
            "identifiers you are not certain of, and prefer naming the trial "
            "(e.g. \"the ADAURA adjuvant osimertinib trial\") over emitting a "
            "possibly-wrong identifier. Record retrieval as an information gap."
        )
    if not result.records:
        return (
            "=== EVIDENCE RETRIEVAL: PERFORMED, NO RESULTS ===\n"
            f"Backend {result.backend!r} ran "
            f"{len(result.queries)} query/queries and returned no records"
            + (f" (errors: {'; '.join(result.errors)})" if result.errors
               else "")
            + ".\nTreat this as an information gap: mark any `sources` entry "
              "\"provenance\": \"MODEL_RECALL_UNVERIFIED\" and say that "
              "retrieval returned nothing."
        )
    lines = [
        "=== RETRIEVED EVIDENCE (verified identifiers) ===",
        f"Backend {result.backend!r} returned {len(result.records)} record(s) "
        f"for: {'; '.join(result.queries)}",
        "These identifiers were returned by a live search and may be cited "
        "directly with \"provenance\": \"RETRIEVED_VERIFIED_IDENTIFIER\". "
        "Anything you cite that is NOT in this list must carry "
        "\"provenance\": \"MODEL_RECALL_UNVERIFIED\".",
        "",
    ]
    lines += [f"  [{i}] {r.cite()}" for i, r in enumerate(result.records, 1)]
    if result.errors:
        lines.append("")
        lines.append("Retrieval errors: " + "; ".join(result.errors))
    return "\n".join(lines)
