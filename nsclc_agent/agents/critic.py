"""The terminal safety critic — unconditional, deterministic-first.

Runs on **every** path, including failed-closed runs and runs where every
clinical task was skipped (the runner invokes it in a ``finally``). It:

1. runs the deterministic rule engine over the plan (N3-no-surgery,
   driver/IO exclusions, trial stage boundaries, RT dose, dose-leak scan…);
2. runs the **citation guard**: every trial reference in the plan must resolve
   (registry offline, NCT/PMID live when enabled) and every high-stakes claim
   must be backed by releasable evidence — a plan citing only
   ``model_reasoning``/stub evidence is not releasable;
3. checks output truncation and schema validity one more time, independently;
4. emits bounded repair requests the runner may honor within its loop budget.

An optional LLM pass may ADD issues; nothing can remove one.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..safety import rules as rule_engine
from ..state import CaseRunState, NON_RELEASABLE_LEVELS

# Outcome-shaped figures: percentages, hazard ratios, month spans. Doses
# are the dose channel's problem (DOSE_SCAN); TNM descriptors, stage
# labels and trial names carry digits but none of these shapes.
_OUTCOME_NUMERIC_RE = re.compile(
    r"(?:\bHR\b|hazard\s+ratio|危险比|风险比)\s*(?:[,:=≈~]|of|is|was|为|约为|约)?"
    r"\s*(?P<hr>\d*\.?\d+)"
    r"|(?P<pct>\d+(?:\.\d+)?)\s*(?:%|percent\b|per\s*cent\b|pct\b)"
    r"|(?P<months>\d+(?:\.\d+)?)\s*(?:months?\b|mos?\b|个月|月)",
    re.I,
)
_ANY_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
#: Confidence-interval expressions: their bounds are never a claim's
#: figure and never an anchor ("95% CI 0.37–0.57").
_CI_RE = re.compile(
    r"\(?\s*95(?:\.\d+)?\s*%\s*(?:CI|置信区间)[^);；]*\)?", re.I)
#: Identifiers and bibliographic numbers that must never anchor a figure.
_IDENTIFIER_RE = re.compile(
    r"NCT\d+|\b(?:19|20)\d{2}\b|\d+\s*:\s*\d+(?:[–-]\d+)?|;\s*\d+", re.I)


def _canon_number(text: str) -> str:
    """'26.0' → '26', '.68' → '0.68': one spelling per value."""
    out = text.lstrip("0") or "0"
    if out.startswith("."):
        out = "0" + out
    if "." in out:
        out = out.rstrip("0").rstrip(".") or "0"
    return out


def _outcome_numbers(text: str) -> set[str]:
    from ..safety.rules import normalize_for_scan

    cleaned = _CI_RE.sub(" ", normalize_for_scan(text or ""))
    found: set[str] = set()
    for match in _OUTCOME_NUMERIC_RE.finditer(cleaned):
        for value in match.groupdict().values():
            if value:
                found.add(_canon_number(value))
    return found


def _number_pool(chunks: list[str]) -> set[str]:
    from ..safety.rules import DOSE_RE

    pool: set[str] = set()
    for chunk in chunks:
        # A dose ("60 Gy"), a CI bound or an identifier is never the
        # source of an outcome figure.
        text = _IDENTIFIER_RE.sub(" ", _CI_RE.sub(" ", DOSE_RE.sub(
            " ", chunk or "")))
        pool.update(_canon_number(m.group(0))
                    for m in _ANY_NUMBER_RE.finditer(text))
    return pool


def _evidence_anchor_text(evidence: Any) -> list[str]:
    """The TYPED text a row may anchor figures with: a trial row's result
    strings, a cohort row's figure, otherwise its summary. Never the whole
    payload JSON — NCT ids, page numbers, TNM editions and CI bounds made
    almost any number "sourced" (v0.7.1 audit)."""
    payload = evidence.payload or {}
    trial = payload.get("trial") if isinstance(payload, dict) else None
    if isinstance(trial, dict):
        return [str(r) for r in trial.get("results") or []]
    if isinstance(payload, dict) \
            and payload.get("five_year_os_percent_approx") is not None:
        return [str(payload["five_year_os_percent_approx"])]
    return [str(evidence.summary or "")]


def _regimen_anchor_text(regimen: Any) -> list[str]:
    """Protocol text of a claimed regimen (label note, monitoring) — never
    its id ("ccrt_60gy" must not anchor "OS 60%")."""
    return [regimen.label_note or ""] + list(regimen.monitoring or ())


class CriticAgent:
    skill_id = "nsclc.critic"

    def __init__(self, llm: Any | None = None) -> None:
        self.llm = llm

    def run(self, state: CaseRunState, tools: Any, broker: Any) -> None:
        checks_run: list[str] = []
        issues: list[str] = []
        violations: list[dict[str, Any]] = []
        repair_requests: list[dict[str, str]] = []

        plan = state.outputs.get("treatment_plan") or {}

        # -- 1. deterministic rule engine -----------------------------------
        checks_run.append("safety_rule_engine")
        found = rule_engine.check_plan(state.staging, state.facts, plan)
        for violation in found:
            violations.append(violation.to_dict())
            issues.append(f"{violation.rule_id}: {violation.message}")
            if violation.severity == "block":
                repair_requests.append({
                    "agent": violation.repair_agent,
                    "reason": f"{violation.rule_id}: {violation.message}",
                })

        # -- 2. citation guard ----------------------------------------------
        checks_run.append("citation_guard")
        issues.extend(self._citation_guard(state, tools, broker, plan))

        # -- 2b. claim guard: per-claim entailment, not a shared pool ------
        checks_run.append("claim_guard")
        issues.extend(self._claim_guard(state))

        # -- 2c. numeric provenance: outcome figures are never free --------
        checks_run.append("numeric_provenance")
        issues.extend(self._numeric_guard(state))

        # -- 3. report-proposed fact check ----------------------------------
        checks_run.append("proposed_fact_check")
        proposed = state.facts.get("_report_proposed") or []
        if proposed and plan.get("regimen_ids"):
            issues.append(
                "PLAN_RESTS_ON_REPORT_PROPOSED_FACTS: "
                + ", ".join(str(p) for p in proposed)
                + " were read from uploaded document images and are "
                "unconfirmed — the plan is provisional and the dose channel "
                "stays closed until the source documents are verified"
            )

        # -- 4. truncation / schema re-check --------------------------------
        checks_run.append("truncation_check")
        loop_meta = state.outputs.get("treatment_loop") or {}
        if loop_meta.get("mode") == "output_truncated":
            issues.append(
                "OUTPUT_TRUNCATED: the treatment reply hit its token ceiling; "
                "the plan on record is incomplete"
            )

        # -- 4. optional LLM additions (add-only) ---------------------------
        # A model may append issues; it is structurally unable to clear any,
        # because this list only ever grows past this point.

        blocked = any(v.get("severity") == "block" for v in violations)
        if blocked and state.release_status not in ("failed_closed", "emergency_action_plan"):
            state.release_status = "blocked"
        for issue in issues:
            if issue not in state.safety_issues:
                state.safety_issues.append(issue)

        state.outputs["safety_audit"] = {
            "checks_run": checks_run,
            "issues": issues,
            "violations": violations,
            "repair_requests": repair_requests,
        }
        state.trace("CriticAgent", "audit",
                    output_summary=f"{len(issues)} issue(s), "
                                   f"{len(repair_requests)} repair request(s)")

    # ------------------------------------------------------------ claim guard
    @staticmethod
    def _claim_guard(state: CaseRunState) -> list[str]:
        """Per-claim support verification (red-team review §11).

        The plan-level guard asks "does the citation pool contain something
        releasable"; this asks, for every high-stakes claim, "does THIS
        claim's evidence actually support THIS subject". Entailment is
        deterministic and two-layered: structurally, a treatment-option
        claim is supported by a releasable trial-registry row whose trial
        covers one of the claimed intervention's regimens — a LAURA row
        cannot endorse the cCRT claim, and a claim citing ids the ledger
        does not hold is a broken audit trail, said out loud. Semantically,
        every entailed row is then checked against the claim's OWN
        population facts (stage within enrollment, edition-aware; driver
        class; histology): a right-regimen citation for the wrong
        population — LAURA cited on a stage-IV claim, FLAURA on an
        exon20ins population — is `CLAIM_POPULATION_MISMATCH`, and a
        population-statistic claim resting on anything but cohort-grade
        evidence is `CLAIM_STATISTIC_SOURCE`.
        """
        from ..knowledge import regimens as regimen_lib
        from ..knowledge import trials as trial_lib
        from ..knowledge.trials import resolve_trial_id
        from ..state import EvidenceLevel

        plan = state.outputs.get("treatment_plan") or {}
        declared = frozenset(
            resolved
            for item in plan.get("extrapolations") or []
            if isinstance(item, dict) and item.get("trial_id")
            and item.get("justification")
            and (resolved := resolve_trial_id(str(item["trial_id"])))
        )

        issues: list[str] = []
        for claim in state.claims:
            if claim.kind not in ("treatment_option", "prognosis_context"):
                continue
            dangling = [c for c in claim.evidence_ids
                        if c not in state.evidence]
            if dangling:
                issues.append(
                    f"CLAIM_DANGLING_EVIDENCE[{claim.claim_id}]: cites "
                    f"{', '.join(dangling)} — not in this run's ledger")
            rows = [state.evidence[c] for c in claim.evidence_ids
                    if c in state.evidence]
            releasable = [e for e in rows
                          if e.level not in NON_RELEASABLE_LEVELS]
            regimens = {str(r) for r in
                        claim.subject.get("intervention_regimen_ids") or []}
            if claim.kind == "treatment_option" and regimens:
                trial_ids = {
                    tid for rid in regimens
                    for tid in (regimen_lib.get(rid).trial_ids
                                if regimen_lib.get(rid) else ())
                }
                entailed = []
                for evidence in releasable:
                    trial = (evidence.payload or {}).get("trial") or {}
                    if set(trial.get("regimen_ids") or []) & regimens \
                            or trial.get("trial_id") in trial_ids:
                        entailed.append(evidence)
                if not entailed:
                    if releasable:
                        issues.append(
                            f"CLAIM_SUPPORT_MISMATCH[{claim.claim_id}]: "
                            f"'{claim.text[:60]}' cites releasable evidence, "
                            f"but none of it covers the claimed intervention "
                            f"({'/'.join(sorted(regimens))}) — a citation "
                            f"borrowed from a different claim is not support")
                    else:
                        issues.append(
                            f"CLAIM_UNSUPPORTED[{claim.claim_id}]: "
                            f"regimen-bearing claim '{claim.text[:60]}' has "
                            f"no releasable evidence entailing "
                            f"{'/'.join(sorted(regimens))}")
                # Semantic layer: the right regimen citation can still be
                # the wrong population — verify each entailed row against
                # the claim's own population facts.
                for evidence in entailed:
                    trial = (evidence.payload or {}).get("trial") or {}
                    mismatches = trial_lib.population_mismatches(
                        trial, claim.subject,
                        declared_extrapolations=declared)
                    if mismatches:
                        issues.append(
                            f"CLAIM_POPULATION_MISMATCH[{claim.claim_id}]: "
                            f"{trial.get('trial_id')} does not cover this "
                            f"claim's population — "
                            f"{'; '.join(mismatches)}")
            elif claim.kind == "prognosis_context":
                if rows and not releasable:
                    issues.append(
                        f"CLAIM_UNSUPPORTED[{claim.claim_id}]: prognosis "
                        f"claim rests only on non-releasable evidence")
                elif claim.support_relation == "population_statistic" \
                        and releasable and not any(
                            e.level == EvidenceLevel.COHORT.value
                            or e.level == EvidenceLevel.COHORT
                            for e in releasable):
                    issues.append(
                        f"CLAIM_STATISTIC_SOURCE[{claim.claim_id}]: a "
                        f"population-statistic claim must rest on "
                        f"cohort-grade evidence — trial rows or guideline "
                        f"text are not a survival statistic's source")
        return issues

    # ---------------------------------------------------- numeric provenance
    @staticmethod
    def _numeric_guard(state: CaseRunState) -> list[str]:
        """Outcome figures are never free.

        Every outcome-shaped figure (percentage, hazard ratio, month span)
        in a high-stakes claim must be present in the TYPED anchor text of
        the rows THAT CLAIM cites (trial result strings, the cohort
        figure) or in the claimed regimens' protocol text — CI bounds and
        identifiers never anchor. For a model-authored plan the same check
        runs over every string the model wrote (summary, rationales,
        uncertainties), anchored against everything the plan cites plus
        the case's own recorded numbers. Presence in the source is
        checked, not the wording around it — the honest list says so.
        """
        from ..knowledge import regimens as regimen_lib

        issues: list[str] = []
        for claim in state.claims:
            if claim.kind not in ("treatment_option", "prognosis_context"):
                continue
            claimed = _outcome_numbers(claim.text)
            if not claimed:
                continue
            chunks: list[str] = []
            for eid in claim.evidence_ids:
                evidence = state.evidence.get(eid)
                if evidence is not None:
                    chunks.extend(_evidence_anchor_text(evidence))
            for rid in claim.subject.get("intervention_regimen_ids") or []:
                regimen = regimen_lib.get(str(rid))
                if regimen is not None:
                    chunks.extend(_regimen_anchor_text(regimen))
            pool = _number_pool(chunks)
            for number in sorted(claimed - pool):
                issues.append(
                    f"CLAIM_NUMERIC_UNANCHORED[{claim.claim_id}]: figure "
                    f"'{number}' in '{claim.text[:60]}' has no source in "
                    f"the claim's cited evidence or the claimed regimens' "
                    f"protocol text — a number without provenance may not "
                    f"be released")

        plan = state.outputs.get("treatment_plan") or {}
        if plan and plan.get("origin", "rule") != "rule":
            authored: list[str] = [str(plan.get("summary") or "")]
            for option in plan.get("options") or []:
                if isinstance(option, dict):
                    authored.append(str(option.get("name") or ""))
                    authored.append(str(option.get("rationale") or ""))
            authored.extend(str(u) for u in plan.get("uncertainties") or [])
            authored.extend(str(u) for u in plan.get("follow_up") or [])
            claimed = set().union(*(_outcome_numbers(t) for t in authored))
            if claimed:
                chunks = []
                cited = set(plan.get("citations") or [])
                for claim in state.claims:
                    cited.update(claim.evidence_ids)
                for eid in cited:
                    evidence = state.evidence.get(eid)
                    if evidence is not None:
                        chunks.extend(_evidence_anchor_text(evidence))
                for rid in plan.get("regimen_ids") or []:
                    regimen = regimen_lib.get(str(rid).strip().lower())
                    if regimen is not None:
                        chunks.extend(_regimen_anchor_text(regimen))
                # The patient's own recorded numbers (TPS, age, CrCl…) may
                # be restated; so may the declared indication thresholds.
                chunks.append(json.dumps(state.facts, ensure_ascii=False,
                                         default=str))
                from ..knowledge.indications import INDICATIONS

                for rid in plan.get("regimen_ids") or []:
                    ind = INDICATIONS.get(str(rid).strip().lower())
                    if ind is not None:
                        chunks.append(f"{ind.pd_l1_tps_ge} {ind.pd_l1_tc_ge}")
                pool = _number_pool(chunks)
                for number in sorted(claimed - pool):
                    issues.append(
                        f"PLAN_NUMERIC_UNANCHORED: figure '{number}' in the "
                        f"model-authored plan text has no source in the "
                        f"plan's cited evidence, its regimens' protocol "
                        f"text or the case record")
        return issues

    # ------------------------------------------------------------------ guard
    def _citation_guard(
        self, state: CaseRunState, tools: Any, broker: Any, plan: dict[str, Any]
    ) -> list[str]:
        issues: list[str] = []
        if not plan:
            return issues

        # Every trial the plan references must verify.
        for ref in plan.get("trial_refs") or []:
            result = tools.call(broker, "citation_verify", reference=str(ref))
            if not result.ok:
                issues.append(f"CITATION_CHECK_FAILED: {ref} ({result.error})")
                continue
            if not result.data.get("verified"):
                issues.append(
                    f"UNVERIFIED_CITATION: {ref} — "
                    f"{result.data.get('note') or 'could not be verified'}; "
                    f"treat as unsupported until verified"
                )

        # The plan's cited ledger evidence must include something releasable.
        citations = [c for c in plan.get("citations") or []
                     if isinstance(c, str)] or \
            (state.outputs.get("treatment_loop") or {}).get("citations") or []
        if citations:
            levels = {
                state.evidence[c].level
                for c in citations if c in state.evidence
            }
            if levels and levels <= NON_RELEASABLE_LEVELS:
                issues.append(
                    "CITATIONS_NOT_RELEASABLE: the plan cites only model/stub/"
                    "failed evidence — no releasable support on record"
                )
        elif plan.get("regimen_ids"):
            issues.append(
                "NO_CITATIONS: a regimen-bearing plan cites no ledger evidence"
            )
        return issues
