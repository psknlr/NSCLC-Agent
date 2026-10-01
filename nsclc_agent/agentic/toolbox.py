"""The agent's toolbox: every clinical capability of the harness, exposed to a
model-led agent as an ordinary tool.

In agent mode the model is the clinician-in-the-loop's reasoning partner and
it LEADS: it decides what to look up, what to compute and what to recommend.
The deterministic kernel is still here — staging engine, trial registry,
regimen library, indication predicates, organ gates, CNS and later-line
modules, guideline KG, the 20-rule safety engine, even the whole governed
pipeline — but as instruments and second opinions the model consults, never
as gates it must pass. Nothing in this module blocks, rewrites or releases.

Each tool returns ``{"ok", "summary", "data"}``; ``data`` is what the model
reads (truncated by the session), ``summary`` is what the UI timeline shows.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Callable

from ..llm.base import ToolSpec

#: The terminal tool: the loop intercepts it instead of executing it.
SUBMIT_TOOL = "submit_consult"


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


_FACTS_OVERRIDE = {
    "type": "object",
    "description": "optional hypothetical facts merged over the case notes "
                   "for THIS call only (what-if reasoning; not recorded)",
}
_STAGE = {"type": "string", "description": "stage group, e.g. IIIB / IVA "
                                           "(defaults to the engine staging "
                                           "of the case notes' TNM)"}

TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "record_case_facts",
        "Write structured facts into the case notes (your working record of "
        "this patient; shown to the clinician as the case dossier and used "
        "as the default input of every other tool). Keys: age, sex, tnm "
        "{t,n,m,prefix}, histologic_category, driver_mutations {gene: report "
        "text}, pd_l1 {tps}, ecog_ps, ngs_done, resectability_category "
        "(RESECTABLE/UNRESECTABLE), operable, disease_extent, cns_metastases "
        "{status,symptomatic,treated,leptomeningeal,burden}, organ_function "
        "{renal:{crcl_ml_min}, hepatic:{bilirubin_uln}}, qtc_ms, "
        "comorbidities, medications, treatment_history [{line, agents, "
        "status}], progression_findings, progression_ngs_done. Values are "
        "validated; refused values come back as notes.",
        _obj({"facts": {"type": "object"},
              "note": {"type": "string", "description": "why (optional)"}},
             ["facts"]),
    ),
    ToolSpec(
        "stage_tnm",
        "AJCC/UICC 9th-edition staging engine. Returns the stage group, or a "
        "refusal naming the ambiguity (bare N2, M1c…) and the test that "
        "resolves it. The engine is a reference: if you disagree, say why.",
        _obj({"t": {"type": "string"}, "n": {"type": "string"},
              "m": {"type": "string"},
              "prefix": {"type": "string", "description": "c | p | yp"}},
             ["t", "n", "m"]),
    ),
    ToolSpec(
        "screen_emergency",
        "Clause-scoped, negation-aware oncologic-emergency screen (cord "
        "compression, SVC, massive hemoptysis, febrile neutropenia, raised "
        "ICP…). Returns hits, explicitly negated signals and the standard "
        "action pathway for any hit.",
        _obj({"text": {"type": "string",
                       "description": "narrative to screen (defaults to the "
                                      "whole conversation so far)"}}),
    ),
    ToolSpec(
        "assess_biomarkers",
        "Clause-scoped driver-report parser: per-gene status (positive / "
        "negative / unknown), EGFR variant classes, first-line and later-line "
        "actionable drivers.",
        _obj({"driver_mutations": {"type": "object",
                                   "description": "{gene: report text}; "
                                                  "defaults to the case notes"}}),
    ),
    ToolSpec(
        "search_trials",
        "Curated landmark-trial registry: populations, stage boundaries, "
        "driver constraints, key results, approvals, caveats. Pass a trial "
        "id/NCT or keywords.",
        _obj({"query": {"type": "string"}}, ["query"]),
    ),
    ToolSpec(
        "search_regimens",
        "Regimen library: drugs, setting, trial anchors, monitoring and "
        "gates (no doses). Pass a regimen_id, drug name or setting keywords.",
        _obj({"query": {"type": "string"}}, ["query"]),
    ),
    ToolSpec(
        "regimen_dosing",
        "Library reference dosing for a regimen (components, dose, route, "
        "schedule) plus this case's organ-function/comorbidity gate readout. "
        "Reference doses must still be verified against the label and the "
        "institution's protocol.",
        _obj({"regimen_id": {"type": "string"}}, ["regimen_id"]),
    ),
    ToolSpec(
        "check_indication",
        "Evaluate regimens against their declared trial populations (stage, "
        "histology, driver/variant, prior therapy, resectability) for this "
        "case: eligible / ineligible / unknown with the failing conditions.",
        _obj({"regimen_ids": {"type": "array", "items": {"type": "string"}},
              "stage_group": _STAGE, "facts_override": _FACTS_OVERRIDE},
             ["regimen_ids"]),
    ),
    ToolSpec(
        "check_organ_function",
        "Organ-function and comorbidity gates for regimens (renal, hepatic, "
        "QTc, ILD, autoimmune, bleeding…): failed and still-unknown gates.",
        _obj({"regimen_ids": {"type": "array", "items": {"type": "string"}},
              "facts_override": _FACTS_OVERRIDE}, ["regimen_ids"]),
    ),
    ToolSpec(
        "cns_assessment",
        "Brain-metastasis stratification for stage IV (status, symptoms, "
        "treated, leptomeningeal, burden) with local-therapy framings, "
        "cautions and workup.",
        _obj({"stage_group": _STAGE, "facts_override": _FACTS_OVERRIDE}),
    ),
    ToolSpec(
        "later_line_options",
        "After documented progression: next-line options by driver and prior "
        "exposure, resistance-mechanism branches, workup and honest coverage "
        "notes.",
        _obj({"stage_group": _STAGE, "facts_override": _FACTS_OVERRIDE}),
    ),
    ToolSpec(
        "protocol_sections",
        "Stage-specific clinical protocol module sections (stage0…stage4b). "
        "Pass the stage group or module key and optional keywords.",
        _obj({"stage": {"type": "string"},
              "query": {"type": "string"}}, ["stage"]),
    ),
    ToolSpec(
        "guideline_search",
        "Guideline knowledge graph: 2,960 machine-extracted recommendations "
        "from NCCN, ESMO, CSCO and Chinese guidelines (original grades, "
        "provenance, cross-region agreement; clinically unverified).",
        _obj({"query": {"type": "string"}, "stage": {"type": "string"},
              "gene": {"type": "string"}, "histology": {"type": "string"},
              "line": {"type": "string"}, "topic": {"type": "string"},
              "jurisdiction": {"type": "string", "description": "CN|US|EU"},
              "rec_id": {"type": "string"}}),
    ),
    ToolSpec(
        "prognosis",
        "Population (cohort) 5-year survival context for a stage group and "
        "directional prognostic factors — not an individual prediction.",
        _obj({"stage_group": _STAGE}),
    ),
    ToolSpec(
        "interaction_check",
        "Drug–drug interaction screen for a medication list (CYP3A4 with "
        "TKIs, QT, pemetrexed–NSAID, ICI cautions…).",
        _obj({"medications": {"type": "array", "items": {"type": "string"}}},
             ["medications"]),
    ),
    ToolSpec(
        "rule_review",
        "Run the deterministic 20-rule safety engine over a draft plan and "
        "get its findings. ADVISORY: the findings are a second opinion you "
        "weigh — accept and revise, or keep your plan and give the clinical "
        "reason.",
        _obj({"options": {"type": "array", "items": {"type": "object"},
                          "description": "[{name, regimen_ids, rationale}]"},
              "trial_refs": {"type": "array", "items": {"type": "string"}},
              "intent": {"type": "string"},
              "summary": {"type": "string"},
              "stage_group": _STAGE}, ["options"]),
    ),
    ToolSpec(
        "governed_reference",
        "Run the full deterministic governed pipeline (staging, routing, "
        "rule-mode planner, indication/organ gates, terminal critic) on the "
        "current case notes and conversation, and see what it would "
        "recommend and why. A reference opinion, not an instruction.",
        _obj({}),
    ),
    ToolSpec(
        "read_attachment",
        "Read the images/documents the clinician attached this turn with a "
        "vision model and get structured, UNCONFIRMED findings (imaging "
        "descriptors or report facts).",
        _obj({"kind": {"type": "string", "enum": ["imaging", "report"]},
              "focus": {"type": "string",
                        "description": "what to look for (optional)"}},
             ["kind"]),
    ),
    ToolSpec(
        "citation_verify",
        "Verify a citation (registry trial id or NCT offline; PMIDs live "
        "when network retrieval is enabled).",
        _obj({"reference": {"type": "string"}}, ["reference"]),
    ),
    ToolSpec(
        "pubmed_search",
        "PubMed search (live only when the operator enabled network "
        "retrieval; otherwise an explicit offline stub).",
        _obj({"query": {"type": "string"},
              "max_results": {"type": "integer", "minimum": 1,
                              "maximum": 10}}, ["query"]),
    ),
    ToolSpec(
        SUBMIT_TOOL,
        "Submit your consult conclusion for this turn. Call exactly once when "
        "you are done reasoning. `reply` is what the clinician reads (Chinese "
        "unless they wrote in another language; Markdown allowed). If the "
        "rule engine raises findings you will get them back once: revise, or "
        "answer each in rule_responses, then submit again.",
        _obj({
            "reply": {"type": "string"},
            "assessment": {"type": "string",
                           "description": "one-paragraph clinical judgement"},
            "stage_group": {"type": "string"},
            "tnm": {"type": "string"},
            "intent": {"type": "string",
                       "enum": ["curative", "palliative", "supportive",
                                "emergency", "undetermined"]},
            "options": {"type": "array", "items": _obj({
                "name": {"type": "string"},
                "rationale": {"type": "string"},
                "regimen_ids": {"type": "array", "items": {"type": "string"}},
                "evidence": {"type": "array", "items": {"type": "string"},
                             "description": "trial ids / rec ids / PMIDs"},
                "preferred": {"type": "boolean"},
            }, ["name"])},
            "workup": {"type": "array", "items": {"type": "string"}},
            "questions": {"type": "array", "items": {"type": "string"},
                          "description": "what you still need to know"},
            "warnings": {"type": "array", "items": {"type": "string"}},
            "rule_responses": {"type": "array", "items": _obj({
                "rule_id": {"type": "string"},
                "decision": {"type": "string",
                             "enum": ["accepted", "overridden"]},
                "reason": {"type": "string"},
            }, ["rule_id", "decision"])},
            "confidence": {"type": "string",
                           "enum": ["high", "moderate", "low"]},
        }, ["reply"]),
    ),
)

TOOL_NAMES = frozenset(spec.name for spec in TOOL_SPECS)


def _merge(base: dict[str, Any], over: dict[str, Any] | None) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class AgentToolbox:
    """Stateful tool executor for one agent session.

    State is the agent's *case notes* (``facts``) plus the attachments of
    the current turn. Tools default to the notes so the model does not have
    to repeat the case on every call; ``facts_override`` supports what-if
    reasoning without touching the record.
    """

    def __init__(self, *, facts: dict[str, Any] | None = None,
                 vision_llm: Any | None = None,
                 narrative: Callable[[], str] | None = None) -> None:
        from ..tools.registry import ToolRegistry

        self.registry = ToolRegistry()
        self.facts: dict[str, Any] = dict(facts or {})
        self.vision_llm = vision_llm
        self.attachments: dict[str, list[str]] = {"images": [], "reports": []}
        self._narrative = narrative or (lambda: "")
        self._impl: dict[str, Callable[..., dict[str, Any]]] = {
            "record_case_facts": self.record_case_facts,
            "stage_tnm": self.stage_tnm,
            "screen_emergency": self.screen_emergency,
            "assess_biomarkers": self.assess_biomarkers,
            "search_trials": self.search_trials,
            "search_regimens": self.search_regimens,
            "regimen_dosing": self.regimen_dosing,
            "check_indication": self.check_indication,
            "check_organ_function": self.check_organ_function,
            "cns_assessment": self.cns_assessment,
            "later_line_options": self.later_line_options,
            "protocol_sections": self.protocol_sections,
            "guideline_search": self.guideline_search,
            "prognosis": self.prognosis,
            "interaction_check": self.interaction_check,
            "rule_review": self.rule_review,
            "governed_reference": self.governed_reference,
            "read_attachment": self.read_attachment,
            "citation_verify": self.citation_verify,
            "pubmed_search": self.pubmed_search,
        }

    # ------------------------------------------------------------- dispatch
    @staticmethod
    def specs() -> list[ToolSpec]:
        return list(TOOL_SPECS)

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        impl = self._impl.get(name)
        if impl is None:
            return {"ok": False, "summary": f"unknown tool {name!r}",
                    "data": {"error": f"unknown tool {name!r}",
                             "available": sorted(self._impl)}}
        try:
            return impl(**(arguments if isinstance(arguments, dict) else {}))
        except TypeError as exc:
            return {"ok": False, "summary": f"bad arguments: {exc}",
                    "data": {"error": f"bad arguments: {exc}",
                             "hint": "check the tool's parameter schema"}}
        except Exception as exc:  # noqa: BLE001 - a tool bug is an observation
            return {"ok": False, "summary": f"{name} failed: {type(exc).__name__}",
                    "data": {"error": f"{type(exc).__name__}: {exc}"}}

    # -------------------------------------------------------------- helpers
    def _facts(self, override: dict[str, Any] | None = None) -> dict[str, Any]:
        return _merge(self.facts, override) if override else self.facts

    def engine_stage(self, facts: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """The engine's reading of the notes' TNM (None when incomplete)."""
        from ..staging import StagingError, stage_from_strings

        tnm = (facts if facts is not None else self.facts).get("tnm") or {}
        if not all(tnm.get(k) for k in ("t", "n", "m")):
            return None
        try:
            result = stage_from_strings(tnm["t"], tnm["n"], tnm["m"],
                                        prefix=tnm.get("prefix") or "c")
        except StagingError as exc:
            return {"staged": False, "refusal": str(exc)}
        return {"staged": True, **result.to_dict()}

    def _stage(self, stage_group: str | None,
               facts: dict[str, Any] | None = None) -> str | None:
        if stage_group:
            return str(stage_group).strip().upper()
        engine = self.engine_stage(facts)
        return engine.get("stage_group") if engine and engine.get("staged") else None

    @staticmethod
    def _tool(result: Any) -> dict[str, Any]:
        return {"ok": bool(result.ok), "summary": result.summary,
                "data": result.data if result.ok else
                {"error": result.error or result.summary, **(result.data or {})}}

    # ---------------------------------------------------------------- notes
    def record_case_facts(self, facts: dict[str, Any], note: str = "") -> dict[str, Any]:
        from ..conversation import merge_facts, sanitize_fact_payload

        cleaned, notes = sanitize_fact_payload(facts if isinstance(facts, dict) else {})
        changed, conflicts = merge_facts(self.facts, cleaned, overwrite=True)
        engine = self.engine_stage()
        return {
            "ok": True,
            "summary": (f"recorded {len(changed)} fact(s)"
                        + (f", {len(notes)} refused/ignored" if notes else "")),
            "data": {"changed": changed, "notes": notes + conflicts,
                     "case_notes": self.facts,
                     "engine_stage": engine},
        }

    # ------------------------------------------------------------- staging
    def stage_tnm(self, t: str, n: str, m: str, prefix: str = "c") -> dict[str, Any]:
        return self._tool(self.registry.stage_lookup(t, n, m, prefix or "c"))

    def screen_emergency(self, text: str = "") -> dict[str, Any]:
        from ..safety import emergencies

        result = emergencies.screen(text or self._narrative())
        data: dict[str, Any] = {"hard_hits": result.hard_hits,
                                "soft_hits": result.soft_hits,
                                "negated": result.negated}
        if result.hard_hits:
            data["standard_pathway"] = emergencies.action_plan(result)
        return {"ok": True,
                "summary": (f"{len(result.hard_hits)} emergency signal(s): "
                            + ", ".join(h["label"] for h in result.hard_hits)
                            if result.hard_hits else
                            f"no emergency signal ({len(result.soft_hits)} soft, "
                            f"{len(result.negated)} negated)"),
                "data": data}

    # ----------------------------------------------------------- knowledge
    def assess_biomarkers(self, driver_mutations: dict[str, Any] | None = None) -> dict[str, Any]:
        from ..knowledge import biomarkers as bm

        facts = ({"driver_mutations": driver_mutations}
                 if isinstance(driver_mutations, dict) else self.facts)
        drivers = bm._normalized_drivers(facts)
        status = {gene: {"report": str(value), "status": bm.driver_status(value),
                         "positive_evidence": bm.positive_evidence(value) or None}
                  for gene, value in drivers.items()}
        data = {"genes": status,
                "egfr_classes": sorted(bm.egfr_classes(facts)),
                "first_line_actionable": bm.first_line_actionable_drivers(facts),
                "later_line_actionable": bm.later_line_actionable_drivers(facts)}
        positives = [g.upper() for g, s in status.items() if s["status"] == "positive"]
        return {"ok": True,
                "summary": f"{len(status)} gene(s); positive: "
                           f"{', '.join(positives) or 'none'}",
                "data": data}

    def search_trials(self, query: str) -> dict[str, Any]:
        return self._tool(self.registry.trial_lookup(query))

    def search_regimens(self, query: str) -> dict[str, Any]:
        return self._tool(self.registry.regimen_lookup(query))

    def regimen_dosing(self, regimen_id: str) -> dict[str, Any]:
        detail = self.registry.regimen_detail(regimen_id)
        if not detail.ok:
            return self._tool(detail)
        gates = self.registry.dose_gate_check(regimen_id, self.facts)
        return {"ok": True,
                "summary": f"library dosing for {regimen_id}; {gates.summary}",
                "data": {"regimen": detail.data["regimen"],
                         "case_gates": gates.data.get("gates"),
                         "note": "library reference doses — verify against "
                                 "the label and institutional protocol"}}

    def check_indication(self, regimen_ids: list[str], stage_group: str | None = None,
                         facts_override: dict[str, Any] | None = None) -> dict[str, Any]:
        from ..knowledge.indications import evaluate_indication

        facts = self._facts(facts_override)
        stage = self._stage(stage_group, facts)
        reports = [evaluate_indication(str(r), stage, facts)
                   for r in (regimen_ids or [])]
        verdicts = ", ".join(f"{r['regimen_id']}={r['verdict']}" for r in reports)
        return {"ok": True, "summary": verdicts or "no regimens given",
                "data": {"stage_group": stage, "regimens": reports}}

    def check_organ_function(self, regimen_ids: list[str],
                             facts_override: dict[str, Any] | None = None) -> dict[str, Any]:
        from ..knowledge import regimens as regimen_lib
        from ..knowledge.organ_gates import failed_gates, unknown_gates

        facts = self._facts(facts_override)
        rows = []
        for rid in regimen_ids or []:
            if regimen_lib.get(str(rid)) is None:
                rows.append({"regimen_id": rid, "error": "unknown regimen id — "
                             "use search_regimens to find library ids"})
                continue
            try:
                rows.append({"regimen_id": rid,
                             "failed": failed_gates(str(rid), facts),
                             "unknown": unknown_gates(str(rid), facts)})
            except Exception as exc:  # noqa: BLE001 - unknown regimen etc.
                rows.append({"regimen_id": rid, "error": str(exc)})
        failed = sum(len(r.get("failed") or []) for r in rows)
        return {"ok": True,
                "summary": f"{len(rows)} regimen(s), {failed} failed gate(s)",
                "data": {"regimens": rows}}

    def cns_assessment(self, stage_group: str | None = None,
                       facts_override: dict[str, Any] | None = None) -> dict[str, Any]:
        from ..knowledge import biomarkers as bm
        from ..knowledge.cns import cns_status, cns_strategy

        facts = self._facts(facts_override)
        stage = self._stage(stage_group, facts)
        lead = (bm.first_line_actionable_drivers(facts) or [{}])[0].get("gene")
        strategy = cns_strategy(stage or "", facts, lead) if stage else None
        return {"ok": True,
                "summary": f"CNS {cns_status(facts).get('status')}"
                           + (" — stratified strategy available" if strategy else ""),
                "data": {"stage_group": stage, "reading": cns_status(facts),
                         "strategy": strategy}}

    def later_line_options(self, stage_group: str | None = None,
                           facts_override: dict[str, Any] | None = None) -> dict[str, Any]:
        from ..knowledge.sequencing import history_summary, sequencing_context

        facts = self._facts(facts_override)
        stage = self._stage(stage_group, facts)
        context = sequencing_context(stage or "", facts) if stage else None
        return {"ok": True,
                "summary": (f"line {context.get('line')}: "
                            f"{len(context.get('options') or [])} option(s)"
                            if context else "no documented progression / not stage IV"),
                "data": {"stage_group": stage, "history": history_summary(facts),
                         "context": context}}

    def protocol_sections(self, stage: str, query: str = "") -> dict[str, Any]:
        return self._tool(self.registry.protocol_lookup(stage, query))

    def guideline_search(self, query: str = "", stage: str | None = None,
                         gene: str | None = None, histology: str | None = None,
                         line: str | None = None, topic: str | None = None,
                         jurisdiction: str | None = None,
                         rec_id: str | None = None) -> dict[str, Any]:
        result = self.registry.guideline_lookup(
            query=query, stage=stage, gene=gene, histology=histology, line=line,
            topic=topic, jurisdiction=jurisdiction, rec_id=rec_id,
            case_facts=self.facts or None, case_stage=self._stage(stage))
        out = self._tool(result)
        hits = (out.get("data") or {}).get("hits")
        if isinstance(hits, list) and len(hits) > 8:
            out["data"] = dict(out["data"], hits=hits[:8],
                               truncated=f"{len(hits)} hits, first 8 shown")
        return out

    def prognosis(self, stage_group: str | None = None) -> dict[str, Any]:
        from ..knowledge.prognosis import prognosis_for

        stage = self._stage(stage_group)
        prefix = (self.facts.get("tnm") or {}).get("prefix") or "c"
        context = prognosis_for(stage, prefix, self.facts)
        return {"ok": True,
                "summary": (f"{stage}: ~{context.get('five_year_os_percent_approx')}% "
                            f"5-yr OS (cohort)" if context else "no cohort figure"),
                "data": context or {"note": "no stage or no honest figure"}}

    def interaction_check(self, medications: list[str]) -> dict[str, Any]:
        return self._tool(self.registry.interaction_check(list(medications or [])))

    # -------------------------------------------------------- second opinions
    def rule_review(self, options: list[dict[str, Any]],
                    trial_refs: list[str] | None = None, intent: str = "",
                    summary: str = "", stage_group: str | None = None) -> dict[str, Any]:
        findings = self.review_plan({"options": options, "trial_refs": trial_refs,
                                     "intent": intent, "assessment": summary,
                                     "stage_group": stage_group})
        return {"ok": True,
                "summary": (f"{len(findings)} advisory finding(s)"
                            if findings else "no rule findings"),
                "data": {"findings": findings,
                         "note": "advisory — weigh them, they do not bind you"}}

    def review_plan(self, consult: dict[str, Any]) -> list[dict[str, Any]]:
        """Rule-engine findings for a consult (advisory, never blocking)."""
        from ..safety import rules

        options = [o for o in (consult.get("options") or []) if isinstance(o, dict)]
        regimen_ids: list[str] = []
        for option in options:
            for rid in option.get("regimen_ids") or []:
                if str(rid) not in regimen_ids:
                    regimen_ids.append(str(rid))
        trial_refs = [str(t) for t in (consult.get("trial_refs") or [])]
        for option in options:
            for ref in option.get("evidence") or []:
                if str(ref) not in trial_refs:
                    trial_refs.append(str(ref))
        engine = self.engine_stage() or {}
        stage = str(consult.get("stage_group") or engine.get("stage_group") or "")
        staging = {"stage_group": stage,
                   "n_category": engine.get("n_category")
                   or (self.facts.get("tnm") or {}).get("n")}
        plan = {
            "intent": consult.get("intent") or "",
            "summary": " ".join(str(x) for x in (consult.get("assessment"),
                                                 consult.get("reply")) if x),
            "options": [{"name": o.get("name") or "",
                         "rationale": o.get("rationale") or "",
                         "regimen_ids": [str(r) for r in o.get("regimen_ids") or []]}
                        for o in options],
            "regimen_ids": regimen_ids,
            "trial_refs": [t for t in trial_refs if not t.upper().startswith("REC")],
            "workup_needed": list(consult.get("workup") or []),
            "uncertainties": list(consult.get("warnings") or []),
        }
        findings = [{"rule_id": v.rule_id, "severity": v.severity,
                     "message": v.message}
                    for v in rules.check_plan(staging, self.facts, plan)]
        if engine.get("staged") and consult.get("stage_group") and \
                str(consult["stage_group"]).upper() != str(engine["stage_group"]).upper():
            findings.append({
                "rule_id": "STAGE_DIFFERS_FROM_ENGINE", "severity": "warn",
                "message": f"you staged {consult['stage_group']}; the AJCC-9 "
                           f"engine reads {engine.get('tnm')} as "
                           f"{engine['stage_group']}"})
        return findings

    def governed_reference(self) -> dict[str, Any]:
        from ..case import Case
        from ..render import render
        from ..runner import NSCLCRunner

        tnm = self.facts.get("tnm") or {}
        facts = {k: v for k, v in self.facts.items() if k != "tnm"}
        case = Case(t=tnm.get("t"), n=tnm.get("n"), m=tnm.get("m"),
                    tnm_prefix=tnm.get("prefix") or "c",
                    presentation=self._narrative(), facts=facts)
        state = NSCLCRunner().run_case(case, role="oncologist")
        view = render(state, "oncologist")
        plan = view.get("treatment_plan") or {}
        audit = state.outputs.get("safety_audit") or {}
        return {"ok": True,
                "summary": f"governed pipeline: {state.release_status}"
                           + (f", stage {(view.get('staging') or {}).get('stage_group')}"
                              if (view.get("staging") or {}).get("stage_group") else ""),
                "data": {
                    "release_status": state.release_status,
                    "staging": view.get("staging"),
                    "plan": {k: plan.get(k) for k in (
                        "intent", "summary", "options", "regimen_ids",
                        "trial_refs", "workup_needed", "uncertainties")},
                    "violations": audit.get("violations") or [],
                    "open_questions": view.get("open_questions") or [],
                    "emergency_plan": view.get("emergency_plan"),
                    "note": "reference opinion of the deterministic pipeline",
                }}

    # -------------------------------------------------------------- vision
    def read_attachment(self, kind: str, focus: str = "") -> dict[str, Any]:
        from ..perception.imaging import ImagingError, ImagingReader
        from ..perception.reports import ReportReader

        refs = self.attachments.get("images" if kind == "imaging" else "reports") \
            or self.attachments.get("images") or self.attachments.get("reports") or []
        if not refs:
            return {"ok": False, "summary": "no attachment this turn",
                    "data": {"error": "the clinician attached nothing this turn"}}
        if self.vision_llm is None:
            return {"ok": False, "summary": "no vision model configured",
                    "data": {"error": "configure a vision-capable model to read "
                                      "attachments"}}
        try:
            reader = (ImagingReader if kind == "imaging" else ReportReader)(self.vision_llm)
            findings = reader.read(refs, context=focus)
        except ImagingError as exc:
            return {"ok": False, "summary": f"read failed: {exc}",
                    "data": {"error": str(exc)}}
        return {"ok": True,
                "summary": f"read {len(refs)} attachment(s) as {kind} — unconfirmed",
                "data": findings.to_dict()}

    # ------------------------------------------------------------ retrieval
    def citation_verify(self, reference: str) -> dict[str, Any]:
        return self._tool(self.registry.citation_verify(reference))

    def pubmed_search(self, query: str, max_results: int = 5) -> dict[str, Any]:
        return self._tool(self.registry.pubmed_search(query, max_results=max_results))


def compact(data: Any, limit: int = 6000) -> str:
    """JSON for the model, bounded: long observations are cut, never dropped."""
    text = json.dumps(data, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + f'… [truncated {len(text) - limit} chars]'
