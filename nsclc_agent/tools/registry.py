"""Executable tool implementations plus their LLM-facing schemas.

Every tool declares its own evidence grade in its :class:`ToolResult`; the
registry's :meth:`call` is the only executor and always goes through the
broker. The journal hook lives here too: authorisation runs *before* the
journal is consulted, so a replay re-derives every policy decision live — the
journal supplies data, never permission.
"""

from __future__ import annotations

from typing import Any, Callable

from ..knowledge import interactions as ddi
from ..knowledge import regimens as regimen_lib
from ..knowledge import trials as trial_lib
from ..llm.base import ToolSpec
from ..safety import emergencies
from ..staging import StagingError, stage_from_strings
from ..state import EvidenceLevel
from . import retrieval
from .base import CapabilityBroker, ToolResult


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "stage_lookup",
        "Deterministically stage a TNM triple under the AJCC/UICC 9th edition. "
        "The engine may REFUSE ambiguous descriptors (bare T1/T2/N2/M1c, missing M) "
        "— the refusal names the test that resolves the ambiguity. You may query "
        "hypotheticals, but the run's authoritative stage is fixed and not yours to change.",
        _obj({
            "t": {"type": "string", "description": "T descriptor, e.g. T2a"},
            "n": {"type": "string", "description": "N descriptor, e.g. N2a (9th ed. splits N2a/N2b)"},
            "m": {"type": "string", "description": "M descriptor, e.g. M0 / M1b / M1c1; must be stated"},
            "prefix": {"type": "string", "description": "c | p | yp (default c)"},
        }, ["t", "n", "m"]),
    ),
    ToolSpec(
        "trial_lookup",
        "Search the curated registry of landmark NSCLC trials (stage boundaries, "
        "driver constraints, key results, approvals). Pass a trial name/id or "
        "keywords. Cite the returned evidence ids for every regimen you anchor.",
        _obj({
            "query": {"type": "string", "description": "trial id (ADAURA, KEYNOTE671…), NCT, or keywords"},
        }, ["query"]),
    ),
    ToolSpec(
        "regimen_lookup",
        "Search the regimen library. Returns drugs, setting, gates and trial "
        "anchors — WITHOUT dose numerics. Reference regimens by regimen_id in "
        "your plan; doses attach later through the deterministic dose channel.",
        _obj({
            "query": {"type": "string", "description": "regimen_id, drug name, or setting keywords"},
        }, ["query"]),
    ),
    ToolSpec(
        "protocol_lookup",
        "Retrieve sections of the stage-specific clinical protocol module "
        "(v3.3). Pass the stage group or module key, plus optional keywords to "
        "select sections. Use this instead of guessing protocol details.",
        _obj({
            "stage": {"type": "string", "description": "stage group (IIIB) or module key (stage3b)"},
            "query": {"type": "string", "description": "optional keywords to filter sections"},
        }, ["stage"]),
    ),
    ToolSpec(
        "guideline_lookup",
        "Look up licensed guideline/pharmacopoeia content when a knowledge "
        "store is configured. Without one, returns an honestly-graded stub "
        "that cannot support a released claim.",
        _obj({"query": {"type": "string"}}, ["query"]),
    ),
    ToolSpec(
        "pubmed_search",
        "Live PubMed search (requires operator-enabled network). Offline, "
        "returns a stub graded stub_not_for_clinical_use.",
        _obj({
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
        }, ["query"]),
    ),
    ToolSpec(
        "citation_verify",
        "Verify a citation: a registry trial id or its NCT verifies offline; "
        "PMIDs and other NCTs verify live when the network is enabled. An "
        "unverifiable citation stays unverified — it is never assumed real.",
        _obj({"reference": {"type": "string"}}, ["reference"]),
    ),
    ToolSpec(
        "interaction_check",
        "Screen a medication list against the built-in oncology interaction "
        "rule pack (TKI-CYP3A4, pemetrexed-NSAID, QT, ICI cautions…).",
        _obj({
            "medications": {"type": "array", "items": {"type": "string"}},
        }, ["medications"]),
    ),
    ToolSpec(
        "emergency_pathway",
        "Return the fixed action pathway for an oncologic emergency signal "
        "(cord_compression, svc_syndrome, massive_hemoptysis…).",
        _obj({"signal_id": {"type": "string"}}, ["signal_id"]),
    ),
    ToolSpec(
        "regimen_detail",
        "DOSE CHANNEL ONLY: full dose-bearing regimen detail from the library.",
        _obj({"regimen_id": {"type": "string"}}, ["regimen_id"]),
    ),
    ToolSpec(
        "dose_gate_check",
        "DOSE CHANNEL ONLY: evaluate a regimen's gates (driver status, PD-L1, "
        "renal, autoimmune…) against the case facts.",
        _obj({
            "regimen_id": {"type": "string"},
            "facts": {"type": "object"},
        }, ["regimen_id"]),
    ),
)

TOOL_NAMES = frozenset(spec.name for spec in TOOL_SPECS)


def tool_specs() -> list[ToolSpec]:
    return list(TOOL_SPECS)


class ToolRegistry:
    """Tool implementations + brokered, journaled, retried dispatch."""

    def __init__(self, *, knowledge_store: Any | None = None, max_attempts: int = 2) -> None:
        #: Optional licensed knowledge store; absent → guideline_lookup stubs.
        self.knowledge_store = knowledge_store
        self.max_attempts = max(1, max_attempts)
        self.tools: dict[str, Callable[..., ToolResult]] = {
            "stage_lookup": self.stage_lookup,
            "trial_lookup": self.trial_lookup,
            "regimen_lookup": self.regimen_lookup,
            "protocol_lookup": self.protocol_lookup,
            "guideline_lookup": self.guideline_lookup,
            "pubmed_search": self.pubmed_search,
            "citation_verify": self.citation_verify,
            "interaction_check": self.interaction_check,
            "emergency_pathway": self.emergency_pathway,
            "regimen_detail": self.regimen_detail,
            "dose_gate_check": self.dose_gate_check,
        }

    # ------------------------------------------------------------- dispatching
    def call(self, broker: CapabilityBroker, name: str, **kwargs: Any) -> ToolResult:
        """Authorise, replay-or-execute (with bounded retry), and account."""
        allowed, reason = broker.allow(name)
        if not allowed:
            # Policy denials and budget exhaustion are not tool failures: they
            # must not trip the breaker or consume budget.
            return ToolResult(name, False, reason, {"denied_reason": reason}, error=reason)
        if name not in self.tools:
            return ToolResult(name, False, "unknown_tool", error="unknown_tool",
                              recoverable=True)

        journal = getattr(broker, "journal", None)
        if journal is not None:
            hit, recorded = journal.next_result("tool", name, kwargs)
            if hit:
                broker.charge()  # a replayed call still consumes the run budget
                replayed = ToolResult.from_journal(name, recorded)
                if replayed.ok:
                    broker.health.record_success(name)
                elif not replayed.recoverable:
                    broker.health.record_failure(name)
                return replayed

        final = self._execute_with_retry(name, kwargs)
        broker.charge()
        if final.ok:
            broker.health.record_success(name)
        elif not final.recoverable:
            broker.health.record_failure(name)
        if journal is not None:
            journal.record("tool", name, kwargs, final.to_journal())
        return final

    def _execute_with_retry(self, name: str, kwargs: dict[str, Any]) -> ToolResult:
        impl = self.tools[name]
        last: ToolResult | None = None
        for _attempt in range(self.max_attempts):
            try:
                last = impl(**kwargs)
            except TypeError as exc:
                # The caller (usually the model) passed wrong arguments — a
                # prompting problem, not a clinical event.
                return ToolResult(name, False, f"bad_arguments: {exc}",
                                  error=str(exc), recoverable=True)
            except retrieval.RetrievalError as exc:
                last = ToolResult(name, False, f"retrieval_failed: {exc}",
                                  error=str(exc), retryable=True)
            except Exception as exc:  # noqa: BLE001 - a tool bug must not kill the run
                return ToolResult(name, False, f"tool_crashed: {type(exc).__name__}",
                                  error=str(exc))
            if last.ok or not last.retryable:
                return last
        return last  # exhausted retries on a retryable failure

    # ----------------------------------------------------------------- staging
    def stage_lookup(self, t: str, n: str, m: str, prefix: str = "c") -> ToolResult:
        try:
            result = stage_from_strings(t, n, m, prefix=prefix or "c")
        except StagingError as exc:
            # A refusal is a *correct answer* — the ambiguity plus its
            # resolution — not a tool failure.
            return ToolResult(
                "stage_lookup", True,
                f"engine refused: {exc}",
                {"staged": False, "refusal": str(exc)},
                evidence_level=EvidenceLevel.STAGING_ENGINE.value,
            )
        return ToolResult(
            "stage_lookup", True,
            f"{result.tnm} → stage {result.stage_group} ({result.edition})",
            {"staged": True, **result.to_dict()},
            evidence_level=EvidenceLevel.STAGING_ENGINE.value,
        )

    # --------------------------------------------------------------- knowledge
    def trial_lookup(self, query: str) -> ToolResult:
        resolved = trial_lib.resolve_trial_id(query)
        if resolved:
            trial = trial_lib.TRIALS_BY_ID[resolved]
            return ToolResult(
                "trial_lookup", True, f"{trial.trial_id}: {trial.name}",
                {"match": "exact", "trial": trial.to_dict()},
                evidence_level=EvidenceLevel.TRIAL.value,
                source_version=trial_lib.REGISTRY_VERSION,
            )
        hits = trial_lib.search(query)
        if not hits:
            return ToolResult(
                "trial_lookup", True, f"no registry match for {query!r}",
                {"match": "none", "trials": [],
                 "note": "not in the curated registry — verify via citation_verify/pubmed_search "
                         "before citing, or state the claim qualitatively"},
                evidence_level=EvidenceLevel.TOOL.value,
            )
        return ToolResult(
            "trial_lookup", True,
            f"{len(hits)} registry match(es): " + ", ".join(t.trial_id for t in hits),
            {"match": "search", "trials": [t.to_dict() for t in hits]},
            evidence_level=EvidenceLevel.TRIAL.value,
            source_version=trial_lib.REGISTRY_VERSION,
        )

    def regimen_lookup(self, query: str) -> ToolResult:
        regimen = regimen_lib.get(query)
        found = [regimen] if regimen else regimen_lib.search(query)
        return ToolResult(
            "regimen_lookup", True,
            f"{len(found)} regimen(s): " + ", ".join(r.regimen_id for r in found)
            if found else f"no regimen match for {query!r}",
            {"regimens": [r.summary() for r in found]},
            source_version=regimen_lib.LIBRARY_VERSION,
        )

    def protocol_lookup(self, stage: str, query: str = "") -> ToolResult:
        from ..prompts import find_sections

        sections, module_key = find_sections(stage, query)
        if module_key is None:
            return ToolResult(
                "protocol_lookup", True,
                f"no protocol module for {stage!r}",
                {"sections": [], "note": f"unknown stage/module {stage!r}"},
            )
        return ToolResult(
            "protocol_lookup", True,
            f"{len(sections)} section(s) from module {module_key}",
            {"module": module_key, "sections": sections},
        )

    def guideline_lookup(self, query: str) -> ToolResult:
        if self.knowledge_store is not None:
            hits = self.knowledge_store.search(query)
            return ToolResult(
                "guideline_lookup", True, f"{len(hits)} guideline hit(s)",
                {"hits": hits},
                evidence_level=EvidenceLevel.GUIDELINE.value,
            )
        return ToolResult(
            "guideline_lookup", True,
            "no licensed knowledge store configured — placeholder returned",
            {"hits": [], "note": "configure a knowledge store for guideline-grade evidence"},
            is_stub=True,
        )

    # --------------------------------------------------------------- retrieval
    def pubmed_search(self, query: str, max_results: int = 5) -> ToolResult:
        payload = retrieval.pubmed_search(query, max_results=max_results)
        if payload.get("stub"):
            return ToolResult("pubmed_search", True, "offline stub (network disabled)",
                              payload, is_stub=True)
        return ToolResult(
            "pubmed_search", True,
            f"{len(payload.get('results') or [])} PubMed result(s)",
            payload, evidence_level=EvidenceLevel.RETRIEVAL.value,
        )

    def citation_verify(self, reference: str) -> ToolResult:
        payload = retrieval.verify_citation(reference)
        verified = bool(payload.get("verified"))
        level = (
            EvidenceLevel.TRIAL.value
            if payload.get("method") == "trial_registry"
            else EvidenceLevel.RETRIEVAL.value
        )
        return ToolResult(
            "citation_verify", True,
            f"{reference}: {'VERIFIED' if verified else 'NOT VERIFIED'} "
            f"({payload.get('method')})",
            payload,
            evidence_level=level if verified else EvidenceLevel.TOOL.value,
        )

    # ------------------------------------------------------------------ safety
    def interaction_check(self, medications: list[str]) -> ToolResult:
        hits = ddi.check(medications or [])
        worst = hits[0]["severity"] if hits else "none"
        return ToolResult(
            "interaction_check", True,
            f"{len(hits)} interaction(s), worst: {worst}",
            {"hits": hits, "medications": medications},
            source_version=ddi.PACK_VERSION,
        )

    def emergency_pathway(self, signal_id: str) -> ToolResult:
        pattern = next(
            (p for p in emergencies.PATTERNS if p.signal_id == signal_id), None
        )
        if pattern is None:
            return ToolResult(
                "emergency_pathway", False, f"unknown signal {signal_id!r}",
                {"known": sorted(p.signal_id for p in emergencies.PATTERNS)},
                error="unknown_signal", recoverable=True,
            )
        return ToolResult(
            "emergency_pathway", True, pattern.label,
            {"signal_id": pattern.signal_id, "label": pattern.label,
             "weight": pattern.weight, "action": pattern.action},
        )

    # ------------------------------------------------------------ dose channel
    def regimen_detail(self, regimen_id: str) -> ToolResult:
        regimen = regimen_lib.get(regimen_id)
        if regimen is None:
            return ToolResult("regimen_detail", False, f"unknown regimen {regimen_id!r}",
                              error="unknown_regimen", recoverable=True)
        return ToolResult(
            "regimen_detail", True, f"dose detail for {regimen.regimen_id}",
            {"regimen": regimen.detail()},
            source_version=regimen_lib.LIBRARY_VERSION,
        )

    def dose_gate_check(self, regimen_id: str, facts: dict[str, Any] | None = None) -> ToolResult:
        regimen = regimen_lib.get(regimen_id)
        if regimen is None:
            return ToolResult("dose_gate_check", False, f"unknown regimen {regimen_id!r}",
                              error="unknown_regimen", recoverable=True)
        facts = facts or {}
        drivers = facts.get("driver_mutations") or {}
        pd_l1 = facts.get("pd_l1") or {}
        comorbid = facts.get("comorbidities") or {}

        def driver_positive(gene: str) -> bool:
            value = str(drivers.get(gene) or "").lower()
            return bool(value) and value not in (
                "negative", "wild type", "wildtype", "wt", "not detected",
                "none", "阴性", "not_tested", "unknown", "pending", "")

        results: list[dict[str, Any]] = []
        for gate in regimen.dose_gates:
            status, note = "unverified", "no matching fact on record"
            if gate == "egfr_alk_negative":
                if driver_positive("egfr") or driver_positive("alk"):
                    status, note = "fail", "EGFR/ALK alteration on record"
                elif drivers.get("egfr") and drivers.get("alk"):
                    status, note = "pass", "EGFR/ALK negative on record"
            elif gate == "egfr_positive":
                status, note = ("pass", "EGFR alteration on record") if driver_positive("egfr") \
                    else ("fail", "no EGFR alteration on record")
            elif gate == "alk_positive":
                status, note = ("pass", "ALK alteration on record") if driver_positive("alk") \
                    else ("fail", "no ALK alteration on record")
            elif gate == "pd_l1_tps_ge_50":
                tps = pd_l1.get("tps")
                if isinstance(tps, (int, float)):
                    status, note = ("pass", f"TPS {tps}%") if tps >= 50 else ("fail", f"TPS {tps}% < 50%")
            elif gate == "pd_l1_tc_ge_1":
                tc = pd_l1.get("tc", pd_l1.get("tps"))
                if isinstance(tc, (int, float)):
                    status, note = ("pass", f"TC {tc}%") if tc >= 1 else ("fail", f"TC {tc}% < 1%")
            elif gate == "autoimmune_screen":
                if comorbid.get("active_autoimmune") or comorbid.get("autoimmune_disease"):
                    status, note = "fail", "active autoimmune disease on record"
                elif "autoimmune_disease" in comorbid or "active_autoimmune" in comorbid:
                    status, note = "pass", "autoimmune screen negative"
            elif gate == "renal_function":
                renal = str((facts.get("organ_function") or {}).get("renal") or "").lower()
                if renal in ("normal", "adequate"):
                    status, note = "pass", "renal function adequate on record"
                elif renal:
                    status, note = "fail", f"renal function: {renal}"
            elif gate == "ecog_0_1":
                ecog = facts.get("ecog_ps")
                if isinstance(ecog, int):
                    status, note = ("pass", f"ECOG {ecog}") if ecog <= 1 else ("fail", f"ECOG {ecog}")
            elif gate == "ecog_0_2":
                ecog = facts.get("ecog_ps")
                if isinstance(ecog, int):
                    status, note = ("pass", f"ECOG {ecog}") if ecog <= 2 else ("fail", f"ECOG {ecog}")
            elif gate == "nonsquamous":
                hist = str(facts.get("histologic_category") or "").lower()
                if hist:
                    ok_hist = "squamous" not in hist or "adeno" in hist
                    status, note = ("pass", hist) if ok_hist else ("fail", hist)
            results.append({"gate": gate, "status": status, "note": note})

        failed = [g for g in results if g["status"] == "fail"]
        unverified = [g for g in results if g["status"] == "unverified"]
        return ToolResult(
            "dose_gate_check", True,
            f"{regimen_id}: {len(failed)} gate(s) failed, {len(unverified)} unverified",
            {"regimen_id": regimen_id, "gates": results,
             "all_clear": not failed and not unverified},
        )
