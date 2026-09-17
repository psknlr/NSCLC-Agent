"""The computable-guideline knowledge graph — served contained.

Six guidelines (NCCN 5.2026, ESMO 2025 early/LA, ESMO 2023 oncogene-addicted
and non-oncogene, CSCO 2025, CN integrated TCM-WM 2023), 2,960 machine-
extracted recommendations with population criteria, actions, original grades
and cross-region agreement clusters, shipped as a compressed store in package
data.

Three containment rules, derived from the KG's own banner ("machine-extracted,
clinically unverified — not for clinical use") and this harness's evidence
doctrine:

* **Grade is declared here, at the source.** Every recommendation whose
  ``curation_status`` is ``llm_extracted`` (today: all of them) is served at
  :data:`EvidenceLevel.KG_EXTRACTED` — a NON-RELEASABLE grade. The KG can
  inform, contextualize, and point at trial IDs / PMIDs that the model can
  then verify through ``trial_lookup`` / ``citation_verify`` (whose results
  ARE releasable); it can never on its own support a released claim. A future
  clinician-verified entry upgrades to GUIDELINE automatically.
* **Doses never leave the store.** 88 recommendations carry dose numerics in
  their extracted text; every served string is scrubbed with the same
  ``DOSE_RE`` the rule engine uses. Doses live in exactly one place — the
  deterministic dose channel — and a guideline lookup is not it.
* **Negative knowledge is surfaced, not enforced.** ``do_not_recommend`` /
  ``avoid`` / ``contraindicated`` entries matching the case are returned as
  *cautions* for the reasoning layer and the tumor board to weigh. Only the
  deterministic 12-rule engine blocks — an unverified extraction must not
  acquire veto power over a plan.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from ..state import EvidenceLevel

_DATA_PATH = Path(__file__).parent / "data" / "guideline_kg.json.gz"

#: The curation ledger: append-only JSONL, one review event per line, last
#: entry per rec_id wins. Default lives next to the store so reviews are
#: git-versioned alongside the content they attest to; deployments override
#: with the environment variable.
CURATION_ENV = "NSCLC_KG_CURATION"
_CURATION_DEFAULT = Path(__file__).parent / "data" / "curation.jsonl"

CURATION_STATUSES = ("clinician_verified", "rejected", "needs_correction",
                     "llm_extracted")

VERIFY_ATTESTATION = (
    "本人已对照来源段落逐项核对：推荐文本、原始分级、人群判据、动作与"
    "文献引用均与指南原文一致。/ I checked the recommendation text, "
    "original grade, population criteria, actions and references against "
    "the source passages."
)

_DOSE_PLACEHOLDER = "〔剂量已隐去，见确定性剂量通道〕"

#: Directions that constitute negative knowledge (served as cautions).
NEGATIVE_DIRECTIONS = frozenset({
    "do_not_recommend", "avoid", "contraindicated",
})

_TOKEN_RE = re.compile(r"[a-z0-9一-鿿]+")


def _scrub(text: str) -> str:
    """Remove dose numerics from any string leaving the store."""
    if not text:
        return text
    from ..safety.rules import DOSE_RE  # local: knowledge<->safety layering

    return DOSE_RE.sub(_DOSE_PLACEHOLDER, text)


def _scrub_deep(value: Any) -> Any:
    """Scrub every string in a served payload, wherever it hides.

    Field-by-field scrubbing was tried first and leaked: the retrieval keys
    carry free text too ("previous high-dose RT (e.g., 60 Gy)" as an rk
    value, found by the boundary test). Identifiers, grades and stage labels
    carry no dose units, so a deep pass over everything is both airtight
    and lossless.
    """
    if isinstance(value, str):
        return _scrub(value)
    if isinstance(value, list):
        return [_scrub_deep(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_deep(v) for k, v in value.items()}
    return value


def _stage_keys(stage_group: str | None) -> set[str]:
    """Expand a computed stage group into the KG's stage vocabulary.

    ``IIIB`` matches recommendations keyed ``IIIB``, ``III``,
    ``locally_advanced`` … and never ``II`` — the base group is parsed as a
    roman numeral, not a string prefix.
    """
    if not stage_group:
        return set()
    stage = str(stage_group).upper()
    match = re.match(r"^(IV|III|II|I|0)", stage)
    base = match.group(1) if match else stage
    keys = {stage, base, "all", "all stages"}
    # Sub-groups also match their letter family: IA3 → IA.
    sub = re.match(r"^((?:IV|III|II|I)[A-C])", stage)
    if sub:
        keys.add(sub.group(1))
    if base == "IV":
        keys.update({"metastatic", "advanced"})
        if stage in ("IVB", "IVC"):
            keys.add("M1c")
    elif base == "III":
        keys.update({"locally_advanced", "advanced"})
    elif base in ("I", "II", "0"):
        keys.add("early_stage")
    return keys


# --------------------------------------------------------------- curation

def rec_content_hash(rec: dict[str, Any]) -> str:
    """Content address of one recommendation as stored.

    A review is pinned to exactly this content: rebuild the store, change
    the entry, and every verification of it is void until re-reviewed —
    a review never carries over to content its reviewer did not see.
    """
    return hashlib.sha256(
        json.dumps(rec, sort_keys=True, ensure_ascii=False,
                   separators=(",", ":")).encode("utf-8")).hexdigest()


def default_curation_path() -> Path:
    override = os.environ.get(CURATION_ENV, "").strip()
    return Path(override) if override else _CURATION_DEFAULT


def load_curation(path: str | Path) -> dict[str, dict[str, Any]]:
    """Read the ledger; last entry per rec_id wins (that is how a
    verification is revoked: append a later entry). A malformed interior
    line is corruption and raises; a torn final line (crash mid-append)
    is tolerated."""
    file_path = Path(path)
    if not file_path.is_file():
        return {}
    entries: dict[str, dict[str, Any]] = {}
    lines = file_path.read_text(encoding="utf-8").splitlines()
    for line_no, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            if line_no == len(lines):
                break
            raise ValueError(
                f"corrupt curation ledger line {line_no}: {exc}") from exc
        if not isinstance(payload, dict) or not payload.get("rec_id"):
            raise ValueError(f"curation ledger line {line_no}: not a review "
                             f"entry")
        entries[str(payload["rec_id"])] = payload
    return entries


def append_curation(
    path: str | Path,
    *,
    kg: "GuidelineKG",
    rec_id: str,
    status: str,
    reviewer: str,
    notes: str = "",
) -> dict[str, Any]:
    """Append one review event. ``status`` must be a known status,
    ``reviewer`` must be non-empty, and the rec must exist — the entry is
    hash-pinned to the content being reviewed."""
    if status not in CURATION_STATUSES:
        raise ValueError(f"unknown curation status {status!r}; "
                         f"expected one of {CURATION_STATUSES}")
    if not reviewer.strip():
        raise ValueError("a review needs a named reviewer")
    rec = kg.recs_by_id.get(str(rec_id))
    if rec is None:
        raise ValueError(f"no recommendation {rec_id!r} in the KG")
    if status == "rejected" and not notes.strip():
        raise ValueError("a rejection needs notes saying what is wrong")
    entry: dict[str, Any] = {
        "rec_id": str(rec_id),
        "status": status,
        "reviewer": reviewer.strip(),
        "reviewed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "content_hash": rec_content_hash(rec),
        "notes": notes.strip(),
    }
    if status == "clinician_verified":
        entry["attestation"] = VERIFY_ATTESTATION
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _case_genes(facts: dict[str, Any]) -> set[str]:
    from .biomarkers import driver_status

    genes: set[str] = set()
    for gene, value in (facts.get("driver_mutations") or {}).items():
        if driver_status(str(value)) == "positive":
            genes.add(str(gene).upper())
    return genes


class GuidelineKG:
    """Query layer over the shipped guideline knowledge graph."""

    def __init__(
        self,
        payload: dict[str, Any],
        curation: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.meta = payload.get("meta") or {}
        self.source = str(payload.get("source") or "guideline_kg")
        self.warning = str(payload.get("warning") or "")
        self.default_status = str(
            payload.get("curation_status_default") or "llm_extracted")
        self.gvs: dict[str, dict[str, Any]] = payload.get("gvs") or {}
        self.recs: list[dict[str, Any]] = payload.get("recs") or []
        self.passages: dict[str, dict[str, Any]] = payload.get("passages") or {}
        self.clusters_by_id: dict[str, dict[str, Any]] = {
            str(c.get("id")): c for c in payload.get("clusters") or []
        }
        self.recs_by_id: dict[str, dict[str, Any]] = {
            str(r.get("id")): r for r in self.recs
        }
        self.curation: dict[str, dict[str, Any]] = dict(curation or {})

    # -------------------------------------------------------------- curation
    def curation_state(
        self, rec: dict[str, Any]
    ) -> tuple[str, dict[str, Any] | None, bool]:
        """Resolve one rec's status: ``(status, entry, void)``.

        ``void=True`` means a review exists but its content hash no longer
        matches the stored rec — the content changed after review, so the
        review does not apply and the status falls back to the default.
        """
        entry = self.curation.get(str(rec.get("id")))
        if entry is None:
            return self.default_status, None, False
        if entry.get("content_hash") != rec_content_hash(rec):
            return self.default_status, entry, True
        status = str(entry.get("status") or self.default_status)
        if status not in CURATION_STATUSES:
            return self.default_status, entry, True
        return status, entry, False

    def curation_stats(self) -> dict[str, int]:
        counts = {"clinician_verified": 0, "rejected": 0,
                  "needs_correction": 0, "void": 0}
        for rec in self.recs:
            status, entry, void = self.curation_state(rec)
            if void:
                counts["void"] += 1
            elif status in counts:
                counts[status] += 1
        counts["unreviewed"] = len(self.recs) - sum(counts.values())
        return counts

    # ----------------------------------------------------------------- meta
    @property
    def available(self) -> bool:
        return bool(self.recs)

    def describe(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "recommendations": len(self.recs),
            "guidelines": {
                gv_id: f"{gv.get('label')} ({gv.get('region')})"
                for gv_id, gv in self.gvs.items()
            },
            "clusters": len(self.clusters_by_id),
            "curation_status": self.default_status,
            "curation": self.curation_stats(),
            "warning": self.warning,
        }

    # ---------------------------------------------------------------- serve
    def _serve(self, rec: dict[str, Any]) -> dict[str, Any]:
        """One recommendation, provenance-complete and dose-scrubbed."""
        gv = self.gvs.get(str(rec.get("gv"))) or {}
        status, entry, void = self.curation_state(rec)
        trials: list[str] = []
        pmids: list[str] = []
        for item in rec.get("ev") or []:
            trials.extend(str(t) for t in item.get("trials") or [])
            if item.get("pmid"):
                pmids.append(str(item["pmid"]))
        cluster = self.clusters_by_id.get(str(rec.get("cluster") or ""))
        served = {
            "rec_id": rec.get("id"),
            "guideline": gv.get("label") or rec.get("gv"),
            "region": gv.get("region"),
            "guideline_version": gv.get("version"),
            "direction": rec.get("dir"),
            "priority": rec.get("prio"),
            "grade": {
                "strength_original": rec.get("so"),
                "evidence_original": rec.get("eo"),
                "strength": rec.get("sn"),
                "evidence": rec.get("en"),
                "scheme": rec.get("gs"),
                "scheme_source": rec.get("gsrc"),
            },
            "topic": rec.get("topic"),
            "line": rec.get("line"),
            "question": _scrub(str(rec.get("q") or "")),
            "recommendation": _scrub(str(rec.get("norm") or rec.get("txt") or "")),
            "recommendation_source_text": _scrub(str(rec.get("txt") or "")),
            "population": rec.get("rk") or {},
            "actions": [
                _scrub(str(a.get("name") or "")) for a in rec.get("acts") or []
                if a.get("name")
            ],
            "verifiable_refs": {"trials": sorted(set(trials)),
                                "pmids": sorted(set(pmids))},
            "provenance": {
                "page": rec.get("page"), "section": rec.get("sec"),
                "passages": list(rec.get("psg") or []),
                "extraction_confidence": rec.get("conf"),
            },
            "curation_status": status,
            "note": (
                "临床医师已复核，与指南原文核对一致"
                if status == "clinician_verified" else
                "机器抽取、未经临床复核 — 仅作上下文，不构成放行依据；"
                "可验证的支持请经 trial_lookup / citation_verify 落台账"),
        }
        if entry is not None:
            served["curation"] = {
                "status": status,
                "reviewer": entry.get("reviewer"),
                "reviewed_at": entry.get("reviewed_at"),
                "notes": entry.get("notes"),
                "void": void,
            }
            if void:
                served["curation"]["void_reason"] = (
                    "content changed after review — the review does not "
                    "apply; re-review required")
        if cluster is not None:
            served["cross_region"] = {
                "cluster_id": cluster.get("id"),
                "agreement": cluster.get("agree"),
                "regions": cluster.get("jurs"),
                "summary": str(cluster.get("summary") or ""),
            }
        # The final boundary: nothing dose-bearing leaves the store, in any
        # field — see _scrub_deep for why field-by-field was not enough.
        return _scrub_deep(served)

    def level_for(self, hits: list[dict[str, Any]]) -> str:
        """The grade this result carries: verified-only results are
        guideline-grade; anything machine-extracted degrades the whole
        result to the non-releasable KG grade."""
        if hits and all(
            h.get("curation_status") == "clinician_verified" for h in hits
        ):
            return EvidenceLevel.GUIDELINE.value
        return EvidenceLevel.KG_EXTRACTED.value

    # ---------------------------------------------------------------- search
    def search(
        self,
        query: str = "",
        *,
        topic: str | None = None,
        stage: str | None = None,
        gene: str | None = None,
        histology: str | None = None,
        line: str | None = None,
        jurisdiction: str | None = None,
        direction: str | None = None,
        limit: int = 8,
        case_facts: dict[str, Any] | None = None,
        case_stage: str | None = None,
        excluded_verified_mismatch: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Deterministic filtered search over the recommendations.

        Filters are recall-oriented: a recommendation whose retrieval keys
        are silent on a dimension is kept (extraction gaps must not hide
        recommendations), one that names a conflicting value is dropped.
        Results are score-ranked, ties broken by rec id for stability.

        With ``case_facts`` (and optionally ``case_stage``), each hit gains
        an ``eligibility`` block from the deterministic criteria evaluator
        and hits are stably re-ranked by eligibility verdict — annotation
        and ordering, never a hidden filter for machine-extracted entries:
        a population-mismatched extraction still appears, labelled, when
        the list is not full without it. Two exclusions are earned by
        human review: rejected entries (a clinician said the extraction is
        wrong; ``get()`` still shows them), and — in case-evaluated
        queries only — clinician-verified entries whose trusted criteria
        confidently mismatch the case, whose ids are reported through
        ``excluded_verified_mismatch`` so the exclusion stays visible.
        """
        tokens = set(_TOKEN_RE.findall((query or "").lower()))
        stage_keys = _stage_keys(stage)
        scored: list[tuple[float, str, dict[str, Any]]] = []
        for rec in self.recs:
            curation_status = self.curation_state(rec)[0]
            if curation_status == "rejected":
                continue
            rk = rec.get("rk") or {}
            if topic and rec.get("topic") != topic:
                continue
            if direction == "negative":
                # Aggregate alias: any do-not/avoid/contraindicated entry.
                if rec.get("dir") not in NEGATIVE_DIRECTIONS:
                    continue
            elif direction and rec.get("dir") != direction:
                continue
            if jurisdiction and jurisdiction.upper() not in (
                    [str(j).upper() for j in rec.get("jur") or []]):
                continue
            if line and rec.get("line") not in ("", "any", line):
                continue
            if gene:
                rec_genes = {str(g).upper() for g in rk.get("genes") or []}
                if rec_genes and gene.upper() not in rec_genes:
                    continue
            if stage_keys:
                rec_stages = {str(s) for s in rk.get("stage_groups") or []}
                if rec_stages and not (rec_stages & stage_keys):
                    continue
            if histology:
                # 'nsclc'/'all' are wildcard keys (the rec covers every
                # NSCLC histology), not histologies to containment-match —
                # treating them literally silently dropped pan-NSCLC recs
                # from every histology-filtered search.
                rec_hist = {str(h) for h in rk.get("histologies") or []} \
                    - {"nsclc", "nsclc_nos", "all", "any"}
                if rec_hist and not any(
                        histology.lower() in h.lower() or h.lower() in histology.lower()
                        for h in rec_hist):
                    continue
            score = 0.0
            if tokens:
                haystack = " ".join((
                    str(rec.get("q") or ""), str(rec.get("norm") or ""),
                    str(rec.get("txt") or ""), str(rec.get("sec") or ""),
                )).lower()
                matched = sum(1 for t in tokens if t in haystack)
                if not matched:
                    continue
                score += matched / len(tokens)
            if gene and gene.upper() in {
                    str(g).upper() for g in rk.get("genes") or []}:
                score += 0.5
            if stage_keys and {str(s) for s in rk.get("stage_groups") or []} \
                    & stage_keys:
                score += 0.5
            if rec.get("prio") == "preferred":
                score += 0.25
            if rec.get("gsrc") == "explicit":
                score += 0.1
            if curation_status == "clinician_verified":
                # Reviewed knowledge outranks unreviewed extraction: what a
                # clinician has checked is served first — which is also what
                # lets a verified caution reach the case-context window.
                score += 1.5
            scored.append((-score, str(rec.get("id")), rec))
        scored.sort()
        limit = max(1, min(limit, 25))
        if case_facts is None:
            return [self._serve(rec) for _, _, rec in scored[:limit]]

        from .kg_eligibility import evaluate_rec

        window = [rec for _, _, rec in scored[:limit * 3]]
        annotated = []
        for rec in window:
            served = self._serve(rec)
            # Criterion source spans are raw guideline text — the same
            # dose-boundary as every other served string applies.
            served["eligibility"] = _scrub_deep(
                evaluate_rec(rec, case_facts, case_stage))
            annotated.append(served)
        # Verified-mismatch exclusion happens BEFORE the cut, or a dropped
        # entry that ranked below the cut would vanish without a trace.
        annotated, dropped = self.drop_verified_mismatches(annotated)
        if excluded_verified_mismatch is not None:
            excluded_verified_mismatch.extend(dropped)
        rank = {"consistent": 0, "insufficient_case_data": 1,
                "not_evaluated": 1, "possible_mismatch": 2,
                "population_mismatch": 3}
        annotated.sort(key=lambda h: rank.get(
            h["eligibility"]["verdict"], 1))  # stable: score order kept
        return annotated[:limit]

    # -------------------------------------------------------------- get/show
    def get(self, rec_id: str) -> dict[str, Any] | None:
        rec = self.recs_by_id.get(str(rec_id))
        if rec is None:
            return None
        served = self._serve(rec)
        served["source_passages"] = _scrub_deep([
            {"passage_id": pid, "page": (self.passages.get(pid) or {}).get("p"),
             "text": _scrub(str((self.passages.get(pid) or {}).get("txt") or ""))[:2000]}
            for pid in rec.get("psg") or [] if pid in self.passages
        ])
        return served

    def cluster(self, cluster_id: str) -> dict[str, Any] | None:
        cluster = self.clusters_by_id.get(str(cluster_id))
        if cluster is None:
            return None
        members = [
            self._serve(self.recs_by_id[m])
            for m in cluster.get("members") or [] if m in self.recs_by_id
        ]
        return _scrub_deep({
            "cluster_id": cluster.get("id"),
            "question": str(cluster.get("q") or ""),
            "population": cluster.get("pop"),
            "agreement": cluster.get("agree"),
            "agreement_score": cluster.get("score"),
            "differs_on": cluster.get("dims"),
            "regions": cluster.get("jurs"),
            "per_region_counts": cluster.get("per"),
            "shared_components": str(cluster.get("shared") or ""),
            "summary": str(cluster.get("summary") or ""),
            "members": members,
            "note": "机器聚类与比较，未经复核 — 分歧提示仅供人工权衡",
        })

    # -------------------------------------------------------------- for_case
    @staticmethod
    def drop_verified_mismatches(
        hits: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Hard population exclusion — reserved for verified entries.

        An ``llm_extracted`` mismatch is annotation and ranking only (the
        criteria might be mis-extracted). Once a clinician has verified an
        entry, its criteria are trusted, and serving it as case context
        despite a confident population mismatch would be wrong — those are
        dropped, and their ids returned so the exclusion stays visible.
        """
        kept: list[dict[str, Any]] = []
        dropped: list[str] = []
        for hit in hits:
            verdict = (hit.get("eligibility") or {}).get("verdict")
            if (hit.get("curation_status") == "clinician_verified"
                    and verdict == "population_mismatch"):
                dropped.append(str(hit.get("rec_id")))
            else:
                kept.append(hit)
        return kept, dropped

    def for_case(
        self,
        facts: dict[str, Any],
        stage_group: str | None,
        *,
        limit: int = 4,
    ) -> dict[str, Any]:
        """Case-matched context: supporting recommendations + cautions.

        Purely deterministic (retrieval-key matching, criteria-evaluated
        eligibility ranking, stable ordering); used by the TreatmentAgent
        to attach guideline context — and the case-relevant negative
        knowledge — to a drafted plan.
        """
        genes = _case_genes(facts)
        histology = str(facts.get("histologic_category") or "") or None
        excluded: list[str] = []
        common = dict(stage=stage_group, histology=histology,
                      topic="systemic_treatment",
                      case_facts=facts, case_stage=stage_group,
                      excluded_verified_mismatch=excluded)
        supporting: list[dict[str, Any]] = []
        seen: set[str] = set()
        for g in sorted(genes) or [None]:  # type: ignore[list-item]
            for hit in self.search(gene=g, limit=limit, **common):
                if hit["rec_id"] not in seen:
                    seen.add(hit["rec_id"])
                    supporting.append(hit)
        supporting = supporting[:limit]
        cautions: list[dict[str, Any]] = []
        for dir_ in sorted(NEGATIVE_DIRECTIONS):
            for hit in self.search(stage=stage_group, histology=histology,
                                   direction=dir_, limit=3,
                                   case_facts=facts, case_stage=stage_group,
                                   excluded_verified_mismatch=excluded):
                if hit["rec_id"] not in seen:
                    seen.add(hit["rec_id"])
                    cautions.append(hit)
        return {
            "supporting": supporting,
            "cautions": cautions[:limit],
            "excluded_verified_mismatch": sorted(set(excluded)),
            "curation_status": self.default_status,
            "note": ("KG 上下文为机器抽取、未经复核（逐条复核后升级）：不构成"
                     "放行依据，cautions 仅提示人工权衡，不触发拦截；"
                     "eligibility 为确定性判据标注"),
        }


_DEFAULT: GuidelineKG | None = None
_DEFAULT_TRIED = False


def load_default() -> GuidelineKG | None:
    """Lazy singleton over the shipped store; None when the data file is
    absent (the tool then stubs exactly as before the KG existed).

    The curation ledger rides along. A corrupt ledger RAISES instead of
    being silently ignored: dropping it would resurface human-rejected
    entries and drop verifications without anyone noticing — a curation
    fault must be fixed, not worked around.
    """
    global _DEFAULT, _DEFAULT_TRIED
    if _DEFAULT_TRIED:
        return _DEFAULT
    _DEFAULT_TRIED = True
    if not _DATA_PATH.is_file():
        return None
    try:
        with gzip.open(_DATA_PATH, "rt", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError, EOFError):
        _DEFAULT = None
        return None
    curation = load_curation(default_curation_path())
    _DEFAULT = GuidelineKG(payload, curation=curation)
    return _DEFAULT


def reload_default() -> GuidelineKG | None:
    """Drop the singleton and load fresh — after a review is appended."""
    global _DEFAULT, _DEFAULT_TRIED
    _DEFAULT = None
    _DEFAULT_TRIED = False
    return load_default()
