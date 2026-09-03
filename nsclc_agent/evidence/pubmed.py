"""PubMed retriever over NCBI E-utilities (stdlib only, no key required).

``esearch`` returns PMIDs for a query, ``esummary`` turns them into titles,
journals and dates. NCBI asks for a ``tool``/``email`` pair and throttles
anonymous callers to roughly 3 requests/second; both are handled here.

The point of this backend is not comprehensiveness — it is that a PMID reaching
the prompt came back from PubMed rather than from the model.
"""

from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from .base import EvidenceRecord, Retriever

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_MIN_INTERVAL = 0.35  # ~3 requests/second for anonymous callers


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if ca and os.path.isfile(ca):
        try:
            ctx.load_verify_locations(ca)
        except OSError:  # pragma: no cover - defensive
            pass
    return ctx


class PubMedRetriever(Retriever):
    name = "pubmed"
    enabled = True

    def __init__(
        self,
        *,
        email: Optional[str] = None,
        api_key: Optional[str] = None,
        tool: str = "nsclc-agent",
        timeout: float = 20.0,
        default_years: Optional[int] = 5,
    ):
        self.email = email
        self.api_key = api_key
        self.tool = tool
        self.timeout = timeout
        self.default_years = default_years
        self._ctx = _ssl_context()
        self._last_call = 0.0

    # -- transport ----------------------------------------------------------

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last_call
        if gap < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - gap)
        self._last_call = time.monotonic()

    def _get(self, endpoint: str, params: dict[str, Any]) -> dict:
        params = {**params, "retmode": "json", "tool": self.tool}
        if self.email:
            params["email"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key
        url = f"{_EUTILS}/{endpoint}?{urllib.parse.urlencode(params)}"
        self._throttle()
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(
            req, timeout=self.timeout, context=self._ctx
        ) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # -- retrieval ----------------------------------------------------------

    def search(self, query: str, *, limit: int = 5) -> list[EvidenceRecord]:
        term = query.strip()
        if not term:
            return []
        if self.default_years:
            term = f"({term}) AND (\"last {self.default_years} years\"[PDat])"
        found = self._get("esearch.fcgi", {
            "db": "pubmed", "term": term, "retmax": max(1, int(limit)),
            "sort": "relevance",
        })
        ids = ((found.get("esearchresult") or {}).get("idlist")) or []
        if not ids:
            return []
        summary = self._get("esummary.fcgi",
                            {"db": "pubmed", "id": ",".join(ids)})
        result = (summary.get("result") or {})
        records: list[EvidenceRecord] = []
        for pmid in ids:
            item = result.get(pmid) or {}
            records.append(EvidenceRecord(
                source_type="PMID",
                source_id=str(pmid),
                title=str(item.get("title") or "").strip(),
                date=str(item.get("pubdate") or "").strip() or None,
                journal=str(item.get("source") or "").strip() or None,
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                retrieved_by=self.name,
            ))
        return records
