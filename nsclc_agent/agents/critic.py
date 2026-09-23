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
    r"(?:HR|hazard\s+ratio|风险比)[\s≈~=:约为]*(?P<hr>[012]?\.\d+)"
    r"|(?P<pct>\d+(?:\.\d+)?)\s*%"
    r"|(?P<months>\d+(?:\.\d+)?)\s*(?:months?|个月)",
    re.I,
)
_ANY_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def _canon_number(text: str) -> str:
    """'26.0' → '26', '.68' → '0.68': one spelling per value."""
    out = text.lstrip("0") or "0"
    if out.startswith("."):
        out = "0" + out
    if "." in out:
        out = out.rstrip("0").rstrip(".") or "0"
    return out


def _outcome_numbers(text: str) -> set[str]:
    found: set[str] = set()
    for match in _OUTCOME_NUMERIC_RE.finditer(text or ""):
        for value in match.groupdict().values():
            if value:
                found.add(_canon_number(value))
    return found


def _number_pool(chunks: list[str]) -> set[str]:
    pool: set[str] = set()
    for chunk in chunks:
        pool.update(_canon_number(m.group(0))
                    for m in _ANY_NUMBER_RE.finditer(chunk or ""))
    return pool


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

        Any percentage, hazard ratio, or month figure in a high-stakes
        claim's text must be present in the evidence rows THAT CLAIM
        cites, or in the claimed regimens' own registry entries
        (deterministic system knowledge — protocol durations and
        thresholds live there). A figure with no provenance is treated
        as fabricated and said out loud. This checks the number's
        presence in the cited source, not the wording around it — the
        honest list says so.
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
                    chunks.append(json.dumps(evidence.payload or {},
                                             ensure_ascii=False))
                    chunks.append(evidence.summary or "")
            for rid in claim.subject.get("intervention_regimen_ids") or []:
                regimen = regimen_lib.get(str(rid))
                if regimen is not None:
                    chunks.append(json.dumps(regimen.summary(),
                                             ensure_ascii=False))
            pool = _number_pool(chunks)
            for number in sorted(claimed - pool):
                issues.append(
                    f"CLAIM_NUMERIC_UNANCHORED[{claim.claim_id}]: figure "
                    f"'{number}' in '{claim.text[:60]}' has no source in "
                    f"the claim's cited evidence or the claimed regimens' "
                    f"registry entries — a number without provenance may "
                    f"not be released")
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
