"""Live evidence retrieval: PubMed, ClinicalTrials.gov, openFDA.

This is the layer whose *absence* was the worst defect of v0.1 — the prompts
demanded retrieval the code could not perform, so models performed it from
memory. Here retrieval is real when the operator enables the network, and
**honestly stubbed** when not: an offline result carries ``is_stub=True`` and
lands in the ledger as ``stub_not_for_clinical_use``, which the citation guard
refuses to accept for high-stakes claims. A stub can therefore never be
laundered into support.

Enable live retrieval with ``NSCLC_AGENT_ONLINE=1``. All HTTP is stdlib, with
timeouts and one bounded retry, honoring ``SSL_CERT_FILE``/
``REQUESTS_CA_BUNDLE`` for proxied environments.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
CTGOV_STUDY = "https://clinicaltrials.gov/api/v2/studies/{nct}"
OPENFDA_LABEL = "https://api.fda.gov/drug/label.json"

_TIMEOUT = float(os.environ.get("NSCLC_AGENT_HTTP_TIMEOUT", "20"))

_NCT_RE = re.compile(r"^NCT\d{8}$", re.IGNORECASE)
_PMID_RE = re.compile(r"^\d{4,9}$")


def online() -> bool:
    return os.environ.get("NSCLC_AGENT_ONLINE", "").strip() in ("1", "true", "yes")


class RetrievalError(RuntimeError):
    """Transport-level retrieval failure — retryable, never silently absorbed."""


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if ca and os.path.isfile(ca):
        try:
            ctx.load_verify_locations(ca)
        except ssl.SSLError:  # pragma: no cover - defensive
            pass
    return ctx


def _get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "nsclc-agent/0.2"})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT, context=_ssl_context()) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None  # a definitive "not found" is an answer, not a failure
        raise RetrievalError(f"HTTP {exc.code} from {url.split('?')[0]}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RetrievalError(f"{type(exc).__name__} from {url.split('?')[0]}") from exc


def _offline_stub(what: str, query: str) -> dict[str, Any]:
    return {
        "stub": True,
        "note": (
            f"Live {what} retrieval is disabled (set NSCLC_AGENT_ONLINE=1 to "
            f"enable). This placeholder is graded stub_not_for_clinical_use "
            f"and cannot support a released claim."
        ),
        "query": query,
    }


# ------------------------------------------------------------------- PubMed

def pubmed_search(query: str, *, max_results: int = 5) -> dict[str, Any]:
    """Search PubMed; returns PMIDs with titles/journals/dates."""
    if not online():
        return _offline_stub("PubMed", query)
    found = _get_json(PUBMED_ESEARCH, {
        "db": "pubmed", "term": query, "retmax": max(1, min(int(max_results), 20)),
        "retmode": "json", "sort": "relevance",
    })
    ids = ((found or {}).get("esearchresult") or {}).get("idlist") or []
    if not ids:
        return {"stub": False, "query": query, "results": []}
    summaries = _get_json(PUBMED_ESUMMARY, {
        "db": "pubmed", "id": ",".join(ids), "retmode": "json",
    })
    records = (summaries or {}).get("result") or {}
    results = []
    for pmid in ids:
        record = records.get(pmid) or {}
        results.append({
            "pmid": pmid,
            "title": record.get("title", ""),
            "journal": record.get("fulljournalname", ""),
            "pubdate": record.get("pubdate", ""),
        })
    return {"stub": False, "query": query, "results": results}


# ------------------------------------------------------ citation verification

def verify_citation(reference: str) -> dict[str, Any]:
    """Verify one citation: a registry trial id, an NCT number, or a PMID.

    Registry ids and registry-known NCTs verify **offline** against the curated
    trial registry; PMIDs and unknown NCTs need the network. An unverifiable
    citation is reported as such — never silently passed.
    """
    from ..knowledge.trials import TRIALS_BY_ID, resolve_trial_id

    ref = str(reference).strip()
    resolved = resolve_trial_id(ref)
    if resolved:
        trial = TRIALS_BY_ID[resolved]
        return {
            "reference": ref, "verified": True, "method": "trial_registry",
            "trial_id": resolved, "nct": trial.nct, "source": trial.source,
        }
    upper = ref.upper()
    if _NCT_RE.match(upper):
        if not online():
            return {
                "reference": ref, "verified": False, "method": "offline",
                "note": "NCT not in the built-in registry and live lookup is disabled",
            }
        study = _get_json(CTGOV_STUDY.format(nct=upper), {"fields": "IdentificationModule"})
        if study is None:
            return {"reference": ref, "verified": False, "method": "clinicaltrials_gov",
                    "note": "NCT number not found on ClinicalTrials.gov"}
        ident = ((study.get("protocolSection") or {}).get("identificationModule") or {})
        return {
            "reference": ref, "verified": True, "method": "clinicaltrials_gov",
            "title": ident.get("briefTitle", ""),
        }
    if _PMID_RE.match(ref):
        if not online():
            return {
                "reference": ref, "verified": False, "method": "offline",
                "note": "PMID verification requires NSCLC_AGENT_ONLINE=1",
            }
        summaries = _get_json(PUBMED_ESUMMARY, {"db": "pubmed", "id": ref, "retmode": "json"})
        record = ((summaries or {}).get("result") or {}).get(ref) or {}
        if record and not record.get("error"):
            return {"reference": ref, "verified": True, "method": "pubmed",
                    "title": record.get("title", "")}
        return {"reference": ref, "verified": False, "method": "pubmed",
                "note": "PMID not found"}
    return {"reference": ref, "verified": False, "method": "unrecognized",
            "note": "not a registry trial id, NCT number, or PMID"}


# ------------------------------------------------------------------ openFDA

def label_lookup(drug: str) -> dict[str, Any]:
    """Fetch indication/warnings excerpts from the openFDA label endpoint."""
    if not online():
        return _offline_stub("openFDA label", drug)
    payload = _get_json(OPENFDA_LABEL, {
        "search": f'openfda.generic_name:"{drug}"', "limit": 1,
    })
    results = (payload or {}).get("results") or []
    if not results:
        return {"stub": False, "drug": drug, "found": False}
    record = results[0]
    def _first(key: str) -> str:
        value = record.get(key)
        return str(value[0])[:1500] if isinstance(value, list) and value else ""
    return {
        "stub": False, "drug": drug, "found": True,
        "indications": _first("indications_and_usage"),
        "warnings": _first("warnings_and_cautions") or _first("warnings"),
        "version": (record.get("effective_time") or ""),
    }
