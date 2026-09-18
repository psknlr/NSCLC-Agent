"""The concrete agents: rule-based bodies with model-driven upgrades.

Every agent follows the same contract: ``run(state, tools, broker)`` mutates
the state through the controlled paths (evidence ledger, outputs, flags) and
degrades to a deterministic body when the model is absent, over budget or
rejected by a gate. The deterministic bodies are not stubs — they produce the
clinically-shaped conservative output, which is what makes the harness
runnable, testable and teachable fully offline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..interview import InterviewLoop, coverage, workup_plan
from ..knowledge import regimens as regimen_lib
from ..knowledge.biomarkers import (
    EGFR_UNCOMMON_SENSITIZING,
    egfr_classes,
    egfr_classical,
    first_line_actionable_drivers,
    later_line_actionable_drivers,
)
from ..perception import ImagingError, ImagingReader, fold_into_case
from ..prompts import load_module
from ..safety import emergencies
from ..staging import StagingError, route, normalize_stage_group, stage_from_strings
from ..state import CaseRunState, EvidenceLevel, decision_fingerprint
from .toolloop import ToolLoop

#: Signal-id → the enquiry axis fact an explicit narrative answer closes.
_SCREEN_AXIS_FACTS = {
    "cord_compression": "emergency_neuro_screen",
    "brain_mets_signs": "emergency_neuro_screen",
    "svc_syndrome": "emergency_airway_screen",
    "massive_hemoptysis": "emergency_airway_screen",
    "airway_obstruction": "emergency_airway_screen",
    "febrile_neutropenia": "emergency_fever_screen",
}


# --------------------------------------------------------------------- intake

class IntakeAgent:
    skill_id = "nsclc.intake"

    def __init__(self, llm: Any | None = None) -> None:
        self.llm = llm

    def run(self, state: CaseRunState, tools: Any, broker: Any) -> None:
        screen = emergencies.screen(state.complaint)
        if screen.emergency:
            state.risk_mode = "emergency"
        # An explicit narrative answer (positive or negated) closes the
        # corresponding screening axis — "没有咯血" is an answer, and without
        # this a fully cooperative history reads as "screen never done".
        for signal in screen.negated:
            fact = _SCREEN_AXIS_FACTS.get(signal)
            if fact:
                state.facts.setdefault(fact, "negative_by_history")
        for hit in screen.hard_hits + screen.soft_hits:
            fact = _SCREEN_AXIS_FACTS.get(hit["signal_id"])
            if fact:
                state.facts.setdefault(fact, f"positive:{hit['signal_id']}")
        eid = state.add_evidence(
            EvidenceLevel.OBSERVED, "emergency_screen",
            f"{len(screen.hard_hits)} hard / {len(screen.soft_hits)} soft "
            f"signal(s); {len(screen.negated)} negated",
            screen.to_dict(),
        )
        state.outputs["intake"] = {
            "risk_mode": state.risk_mode,
            "screening": screen.to_dict(),
            "missing_information": list(state.missing_information),
        }
        state.trace("IntakeAgent", "screen", evidence_ids=[eid],
                    output_summary=f"risk_mode={state.risk_mode}")


class EmergencyAgent:
    """Fixed action plan for oncologic emergencies. Never model-worded."""

    skill_id = "nsclc.intake"

    def __init__(self, llm: Any | None = None) -> None:
        self.llm = llm

    def run(self, state: CaseRunState, tools: Any, broker: Any) -> None:
        screen_dict = (state.outputs.get("intake") or {}).get("screening") or {}
        screen = emergencies.ScreenResult(
            hard_hits=screen_dict.get("hard_hits") or [],
            soft_hits=screen_dict.get("soft_hits") or [],
            negated=screen_dict.get("negated") or [],
        )
        plan = emergencies.action_plan(screen)
        state.outputs["emergency_plan"] = plan
        state.release_status = "emergency_action_plan"
        state.add_claim(
            "emergency", "Oncologic emergency pathway activated",
            origin="rule", confidence=0.95,
        )
        state.trace("EmergencyAgent", "action_plan",
                    output_summary=", ".join(plan["signals"]))


# ------------------------------------------------------------------ interview

class InterviewAgent:
    skill_id = "nsclc.interview"

    def __init__(self, llm: Any | None = None, *, loop: InterviewLoop | None = None) -> None:
        self.llm = llm
        #: One loop per conversation so stall detection spans turns.
        self.loop = loop or InterviewLoop(llm)

    def run(self, state: CaseRunState, tools: Any, broker: Any) -> None:
        prescriptive = bool(state.allow_dose_planning)
        round_ = self.loop.next_round(
            state.facts, state.complaint,
            role=state.role, risk_mode=state.risk_mode,
            prescriptive=prescriptive, budget=state.budget,
        )
        verdict = round_.verdict
        questions = [q.question for q in round_.questions]
        allowed = state.budget.reserve_questions(len(questions))
        state.open_questions = questions[:allowed] if allowed else questions[:0]
        if verdict is not None:
            state.missing_information = [
                label for label in verdict.to_dict().get("missing_labels", [])
            ]
        state.outputs["interview"] = self.loop.summary(
            state.facts, state.complaint, prescriptive=prescriptive)
        if verdict is not None and verdict.verdict == "blocked":
            state.flag(
                "RED_FLAG_SCREEN_OPEN: emergency screening axes unanswered — "
                "dose-bearing release is blocked until they are answered"
            )
        state.trace(
            "InterviewAgent", "round",
            output_summary=f"verdict={verdict.verdict if verdict else '?'}, "
                           f"{len(state.open_questions)} question(s)",
        )


# ----------------------------------------------------------------- perception

class PerceptionAgent:
    """Reads attached radiology films AND clinical-document images.

    Both readers run through the same vision client (auto-selected from the
    environment when possible — see ``build_vision_client``), both produce
    MODEL-graded, never-releasable evidence, and both fold their proposals
    into the case through the guarded seeding paths.
    """

    skill_id = "nsclc.perception"

    def __init__(self, llm: Any | None = None, *, base_dir: Path | None = None) -> None:
        self.llm = llm
        self.base_dir = base_dir

    def run(self, state: CaseRunState, tools: Any, broker: Any) -> None:
        films = [str(img["ref"]) for img in state.images
                 if img.get("ref") and img.get("kind", "radiology") != "report"]
        reports = [str(img["ref"]) for img in state.images
                   if img.get("ref") and img.get("kind") == "report"]
        if not films and not reports:
            return
        if films:
            self._read_films(state, films)
        if reports:
            self._read_reports(state, reports)

    # ------------------------------------------------------------------ films
    def _read_films(self, state: CaseRunState, refs: list[str]) -> None:
        try:
            reader = ImagingReader(self.llm)
        except ImagingError as exc:
            state.flag(f"NO_VISION_PROVIDER: {exc} — films not read")
            return
        try:
            findings = reader.read(
                refs, context=state.complaint,
                base_dir=self.base_dir, budget=state.budget,
            )
        except ImagingError as exc:
            state.flag(f"IMAGING_READ_FAILED: {exc}")
            return
        self._fold_tnm(state, findings)
        eid = state.add_evidence(
            EvidenceLevel.MODEL, "imaging_reader",
            f"proposed cT={findings.candidate_t} cN={findings.candidate_n} "
            f"cM={findings.candidate_m} ({findings.confidence})",
            findings.to_dict(),
        )
        state.outputs["imaging"] = findings.to_dict()
        state.trace("PerceptionAgent", "read_films", evidence_ids=[eid],
                    output_summary=f"{findings.n_images} film(s) read")

    # ---------------------------------------------------------------- reports
    def _read_reports(self, state: CaseRunState, refs: list[str]) -> None:
        from ..perception.reports import ReportReader, fold_report_facts

        try:
            reader = ReportReader(self.llm)
        except ImagingError as exc:
            state.flag(f"NO_VISION_PROVIDER: {exc} — documents not read")
            return
        try:
            findings = reader.read(
                refs, context=state.complaint,
                base_dir=self.base_dir, budget=state.budget,
            )
        except ImagingError as exc:
            state.flag(f"REPORT_READ_FAILED: {exc}")
            return
        seeded, flags = fold_report_facts(state.facts, findings)
        for flag in flags:
            state.flag(flag)
        self._fold_tnm(state, findings)
        eid = state.add_evidence(
            EvidenceLevel.MODEL, "report_reader",
            f"read {len(findings.document_types) or '?'} document type(s); "
            f"seeded {len(seeded)} fact(s)",
            findings.to_dict(),
        )
        state.outputs["report_findings"] = findings.to_dict()
        state.trace("PerceptionAgent", "read_reports", evidence_ids=[eid],
                    output_summary=f"{findings.n_images} document(s), "
                                   f"{len(seeded)} fact(s) seeded")

    def _fold_tnm(self, state: CaseRunState, findings: Any) -> None:
        tnm = dict(state.facts.get("tnm") or {})
        seeded, flags = fold_into_case(
            tnm.get("t"), tnm.get("n"), tnm.get("m"), findings)
        for kind, value in seeded.items():
            tnm[kind] = value
        state.facts["tnm"] = tnm
        if seeded:
            # A model-proposed descriptor may stage and draft, never dose:
            # track it with the same guard the report facts use, so the dose
            # channel stays shut until a human confirms the seeded cTNM.
            proposed = state.facts.setdefault("_report_proposed", [])
            for kind in seeded:
                path = f"tnm.{kind}"
                if path not in proposed:
                    proposed.append(path)
        for flag in flags:
            state.flag(flag)


# -------------------------------------------------------------------- staging

class StagingAgent:
    """The one agent no model may stand in for."""

    skill_id = "nsclc.staging"

    def __init__(self, llm: Any | None = None) -> None:
        self.llm = llm  # unused; kept for a uniform constructor

    def run(self, state: CaseRunState, tools: Any, broker: Any) -> None:
        tnm = state.facts.get("tnm") or {}
        t, n, m = tnm.get("t"), tnm.get("n"), tnm.get("m")
        prefix = str(tnm.get("prefix") or "c")
        edition = str(state.facts.get("staging_system") or "AJCC9")
        label = state.facts.get("stage_group")

        if t and n is not None:
            try:
                result = stage_from_strings(
                    str(t), str(n), str(m or ""), prefix=prefix, edition=edition)
            except StagingError as exc:
                state.flag(f"STAGING_ERROR: {exc}")
                self._unstaged(state, reason=str(exc))
                return
            staged = result.to_dict()
            if label:
                try:
                    given = normalize_stage_group(str(label))
                except StagingError:
                    given = str(label)
                if given != result.stage_group:
                    state.flag(
                        f"STAGE_MISMATCH: provided {label} but TNM "
                        f"{result.tnm} computes to {result.stage_group} "
                        f"(computed value used)"
                    )
            state.staging = staged
            eid = state.add_evidence(
                EvidenceLevel.STAGING_ENGINE, "staging_engine",
                f"{result.tnm} → stage {result.stage_group}", staged,
            )
            state.routing = route(result.stage_group).to_dict()
            state.add_claim(
                "staging",
                f"Stage {result.stage_group} ({result.edition}) from {result.tnm}",
                [eid], confidence=0.98, origin="rule",
            )
            state.trace("StagingAgent", "stage", evidence_ids=[eid],
                        output_summary=f"stage={result.stage_group}")
            return

        if label:
            try:
                group = normalize_stage_group(str(label))
            except StagingError as exc:
                state.flag(f"STAGING_ERROR: {exc}")
                self._unstaged(state, reason=str(exc))
                return
            state.flag(
                "STAGE_FROM_LABEL: no TNM provided; using the given stage "
                "group without deterministic verification"
            )
            state.staging = {
                "stage_group": group, "tnm": None,
                "edition": "label (unverified)",
            }
            state.routing = route(group).to_dict()
            state.trace("StagingAgent", "label", output_summary=f"stage={group}")
            return

        state.flag("STAGE_UNRESOLVED: no TNM and no stage_group provided")
        self._unstaged(state, reason="no descriptors provided")

    def _unstaged(self, state: CaseRunState, *, reason: str) -> None:
        """Route to the workup module with a VOI plan — never a dead end."""
        state.staging = {}
        state.routing = route("Occult").to_dict()
        plan = workup_plan(state.facts, state.complaint)
        state.outputs["workup_plan"] = {"reason": reason, "steps": plan}
        state.release_status = "needs_staging_workup"
        state.trace("StagingAgent", "unstaged", output_summary=reason)


# ------------------------------------------------------------------ treatment

def _driver_positive(facts: dict[str, Any], gene: str) -> bool:
    from ..knowledge.biomarkers import gene_status

    return gene_status(facts, gene) == "positive"


def _driver_unknown(facts: dict[str, Any], gene: str) -> bool:
    from ..knowledge.biomarkers import gene_status

    return gene_status(facts, gene) == "unknown"


def _nonsquamous(facts: dict[str, Any]) -> bool:
    hist = str(facts.get("histologic_category") or "").lower()
    return not hist or "squamous" not in hist or "adeno" in hist


def _resectability(facts: dict[str, Any]) -> str:
    return str(facts.get("resectability_category")
               or facts.get("resectability") or "").upper()


def _consolidation_by_driver(plan: dict[str, Any], opt: Any,
                             facts: dict[str, Any], egfr: bool) -> None:
    """Post-cCRT consolidation, variant-aware.

    LAURA enrolled EGFR ex19del/L858R — a non-classical EGFR alteration is
    outside both LAURA and (per the subgroup signal) PACIFIC's benefit, so
    the consolidation decision goes to the MDT instead of a guessed drug.
    """
    if egfr and egfr_classical(facts):
        opt("Consolidation osimertinib", ["osimertinib_consolidation"],
            "EGFR ex19del/L858R unresectable III → LAURA, not durvalumab")
    elif egfr:
        plan["mdt_referral"] = True
        plan["uncertainties"].append(
            f"EGFR {'/'.join(sorted(egfr_classes(facts)))} after cCRT: "
            f"LAURA applies to ex19del/L858R only, and durvalumab benefit "
            f"in EGFR+ disease is unclear — consolidation strategy is an "
            f"individualized MDT decision, none is auto-proposed.")
    else:
        opt("Consolidation durvalumab", ["durva_consolidation"],
            "PACIFIC; start ≤42 days post-CRT; never concurrent")


def deterministic_plan(stage_group: str, facts: dict[str, Any]) -> dict[str, Any]:
    """The rule-mode treatment plan: conservative, library-anchored, dose-free.

    Not a model stub — this is the deterministic decision table distilled from
    the protocol modules, and it must itself pass the safety rule engine.
    """
    pd_l1 = facts.get("pd_l1") or {}
    tps = pd_l1.get("tps")
    egfr, alk = _driver_positive(facts, "egfr"), _driver_positive(facts, "alk")
    plan: dict[str, Any] = {
        "intent": "curative", "summary": "", "options": [],
        "regimen_ids": [], "trial_refs": [], "extrapolations": [],
        "workup_needed": [], "mdt_referral": False, "uncertainties": [],
        "origin": "rule",
    }

    def opt(name: str, regimen_ids: list[str], rationale: str) -> None:
        """Add an option — through the indication-predicate gate.

        The decision table SELECTS candidates; the declared predicates
        decide whether each may actually be proposed: ``ineligible`` is
        dropped loudly (a table/declaration divergence to fix, never a
        silent recommendation), ``unknown`` stays provisional with the
        missing facts routed to workup, and a stage mismatch that the plan
        has explicitly declared as an extrapolation is honored as such.
        """
        from ..knowledge.indications import (
            INELIGIBLE, UNKNOWN as IND_UNKNOWN, evaluate_indication,
        )

        kept: list[str] = []
        for rid in regimen_ids:
            verdict = evaluate_indication(rid, stage_group, facts)
            if verdict["verdict"] == INELIGIBLE:
                failed = verdict["failed_conditions"]
                stage_only = all(f.startswith("stage:") for f in failed)
                declared = {
                    str(e.get("trial_id")) for e in plan["extrapolations"]
                    if isinstance(e, dict)
                }
                regimen = regimen_lib.get(rid)
                if stage_only and regimen and declared & set(regimen.trial_ids):
                    kept.append(rid)  # declared extrapolation: allowed, audited
                    continue
                plan["uncertainties"].append(
                    f"决策表提案 {rid} 被适应证谓词判定不适用（"
                    + "; ".join(failed)
                    + "）——已剔除；请修正决策表或声明。")
                continue
            kept.append(rid)
            if verdict["verdict"] == IND_UNKNOWN:
                provisional = plan.setdefault("provisional_regimens", [])
                provisional.append({
                    "regimen_id": rid,
                    "pending": verdict["unknown_conditions"],
                })
                for missing in verdict["unknown_conditions"]:
                    line = f"Resolve for {rid}: {missing}"
                    if line not in plan["workup_needed"]:
                        plan["workup_needed"].append(line)
                plan["uncertainties"].append(
                    f"{rid} 为暂定推荐：待补齐 "
                    + "；".join(verdict["unknown_conditions"]))
            if verdict["edition_migration"]:
                note = (f"{rid}: applied via 8th-edition descriptor mapping "
                        f"(TNM edition migration, not an extrapolation)")
                if note not in plan["uncertainties"]:
                    plan["uncertainties"].append(note)
        if regimen_ids and not kept:
            return  # every regimen failed its predicate — no option
        plan["options"].append({"name": name, "regimen_ids": kept,
                                "rationale": rationale})
        for rid in kept:
            if rid not in plan["regimen_ids"]:
                plan["regimen_ids"].append(rid)

    # Tier-A biomarkers gate systemic commitment for non-squamous disease.
    needs_systemic = stage_group not in ("0", "IA1", "IA2", "IA3", "Occult", "")
    if needs_systemic and _nonsquamous(facts) and (
        _driver_unknown(facts, "egfr") or _driver_unknown(facts, "alk")
    ):
        plan["intent"] = "workup"
        plan["summary"] = (
            "Tier-A biomarkers (EGFR/ALK) are untested in non-squamous disease "
            "— systemic-therapy selection is deferred until they return."
        )
        plan["workup_needed"] = [
            "EGFR testing (tissue NGS/PCR; plasma if tissue-poor)",
            "ALK testing (IHC/FISH or NGS)",
            "PD-L1 TPS on a validated assay",
        ]
        plan["uncertainties"] = ["All treatment options are provisional pending biomarkers."]
        return plan

    if stage_group == "0":
        opt("Sublobar resection", [],
            "AIS (Tis, stage 0): complete (often sublobar) resection is typically curative — MIA is T1mi → IA1 and never reaches this branch")
        opt("Active surveillance", [],
            "Pure GGN, stable: surveillance per MDT is legitimate")
        plan["summary"] = "Stage 0: resection extent vs surveillance; no systemic therapy."
        return plan

    if stage_group in ("IA1", "IA2", "IA3", "IB"):
        operable = facts.get("operable")
        if operable is False:
            opt("Definitive SBRT/SABR", ["sbrt_definitive"],
                "Medically inoperable stage I: risk-adapted SBRT")
        else:
            opt("Anatomic resection + nodal evaluation", [],
                "Operable stage I: surgery is the definitive modality")
        if stage_group == "IB" and egfr and egfr_classical(facts):
            opt("Adjuvant osimertinib after resection", ["osimertinib_adjuvant"],
                "ADAURA covers resected IB EGFR ex19del/L858R")
        elif stage_group == "IB" and egfr:
            plan["mdt_referral"] = True
            plan["uncertainties"].append(
                f"Resected IB with EGFR "
                f"{'/'.join(sorted(egfr_classes(facts)))}: outside the "
                f"ADAURA population — adjuvant TKI not standard; MDT.")
        plan["summary"] = "Stage I: operability decides surgery vs SBRT."
        return plan

    if stage_group in ("IIA", "IIB", "IIIA"):
        resect = _resectability(facts)
        unresectable = resect == "UNRESECTABLE"
        if unresectable:
            opt("Definitive concurrent chemoradiation", ["ccrt_60gy"],
                "Unresectable locally advanced disease")
            _consolidation_by_driver(plan, opt, facts, egfr)
            plan["summary"] = f"Stage {stage_group} unresectable: cCRT + consolidation by driver."
            return plan
        if egfr and egfr_classical(facts):
            opt("Surgery → adjuvant osimertinib (± chemo)", ["osimertinib_adjuvant"],
                "EGFR ex19del/L858R resectable: targeted adjuvant standard "
                "(ADAURA); no perioperative IO")
        elif egfr:
            # Non-classical EGFR (exon20ins/uncommon/unclassified): ADAURA
            # does not apply, and the perioperative-IO trials excluded
            # EGFR+ disease — adjuvant chemo + MDT, not a guessed TKI.
            opt("Surgery → adjuvant chemotherapy; targeted adjuvant "
                "individualized", ["adjuvant_platinum_doublet"],
                f"EGFR {'/'.join(sorted(egfr_classes(facts)))}: outside the "
                f"ADAURA population — adjuvant TKI is not standard; "
                f"chemo per LACE, molecular tumor board for the rest")
            plan["mdt_referral"] = True
        elif alk:
            opt("Surgery → adjuvant alectinib", ["alectinib_adjuvant"],
                "ALK+ resectable: ALINA")
        else:
            postop = str(facts.get("clinical_scenario") or "").upper() == "POSTOP_RESECTED"
            if postop:
                regimens = ["adjuvant_platinum_doublet"]
                rationale = "Resected node-positive: adjuvant platinum doublet (LACE)"
                tc = pd_l1.get("tc", tps)
                if isinstance(tc, (int, float)) and tc >= 1 and stage_group in ("IIA", "IIB", "IIIA"):
                    regimens.append("atezolizumab_adjuvant")
                    rationale += "; then adjuvant atezolizumab (PD-L1 TC≥1%, IMpower010)"
                opt("Adjuvant chemotherapy (± atezolizumab)", regimens, rationale)
            else:
                opt("Perioperative pembrolizumab + chemotherapy",
                    ["pembro_perioperative"],
                    "Driver-negative resectable: perioperative chemo-IO preferred "
                    "(KEYNOTE-671)")
        plan["mdt_referral"] = stage_group == "IIIA"
        plan["summary"] = f"Stage {stage_group}: resectable pathway by driver status."
        return plan

    if stage_group in ("IIIB", "IIIC"):
        resect = _resectability(facts)
        n_cat = str((facts.get("tnm") or {}).get("n") or "").upper()
        surgical_candidate = (
            stage_group == "IIIB" and resect == "RESECTABLE" and n_cat != "N3"
        )
        if surgical_candidate:
            if egfr and not egfr_classical(facts):
                opt("Surgery → adjuvant chemotherapy; targeted adjuvant "
                    "individualized", ["adjuvant_platinum_doublet"],
                    f"EGFR {'/'.join(sorted(egfr_classes(facts)))}: outside "
                    f"the ADAURA population and periop-IO trials excluded "
                    f"EGFR+ — chemo per LACE, molecular tumor board")
                plan["mdt_referral"] = True
                plan["summary"] = f"Resectable {stage_group}: non-classical " \
                                  f"EGFR — chemo + MDT pathway."
                return plan
            if egfr or alk:
                rid = "osimertinib_adjuvant" if egfr else "alectinib_adjuvant"
                trial = "ADAURA" if egfr else "ALINA"
                # Edition-aware: many "IIIB" cases are 8th-edition IIIA that
                # the 9th edition renamed (T2N2b etc.) — those are inside
                # the trial's enrollment, not an extrapolation.
                from ..knowledge.trials import TRIALS_BY_ID
                from ..staging.legacy8 import eighth_edition_group

                tnm = facts.get("tnm") or {}
                legacy = eighth_edition_group(tnm.get("t"), tnm.get("n"),
                                              tnm.get("m"))
                entry = TRIALS_BY_ID.get(trial)
                migrated = bool(legacy and entry
                                and legacy in entry.stage_groups)
                if migrated:
                    opt(f"Surgery → adjuvant "
                        f"{'osimertinib' if egfr else 'alectinib'}",
                        [rid],
                        f"Driver-positive resectable IIIB — 8th-edition "
                        f"{legacy} under {trial}'s enrollment (TNM edition "
                        f"migration, not an extrapolation)")
                    plan["uncertainties"].append(
                        f"{trial}: case is 9th-edition {stage_group} but "
                        f"maps to {legacy} under the trial's 8th-edition "
                        f"enrollment — edition migration noted.")
                else:
                    # Declare BEFORE opt(): the predicate gate honors a
                    # declared extrapolation; an undeclared one is dropped.
                    plan["extrapolations"].append({
                        "trial_id": trial,
                        "justification": f"{trial} enrolled up to IIIA; "
                                         f"resectable IIIB use is a "
                                         f"documented extrapolation per MDT "
                                         f"decision",
                    })
                    opt(f"Surgery → adjuvant "
                        f"{'osimertinib' if egfr else 'alectinib'}",
                        [rid], f"Driver-positive resectable IIIB — {trial} "
                               f"extrapolated beyond IIIA")
            else:
                opt("Perioperative pembrolizumab + chemotherapy",
                    ["pembro_perioperative"],
                    "KEYNOTE-671 covers resectable IIIB(N2)")
            plan["mdt_referral"] = True
            plan["summary"] = f"Resectable {stage_group}: perioperative pathway."
            return plan
        opt("Definitive concurrent chemoradiation", ["ccrt_60gy"],
            "N3 / unresectable locally advanced disease: cCRT is the "
            "curative-intent pathway")
        _consolidation_by_driver(plan, opt, facts, egfr)
        plan["summary"] = f"Stage {stage_group}: definitive cCRT + consolidation by driver."
        return plan

    if stage_group in ("IVA", "IVB"):
        plan["intent"] = "palliative"
        # Driver-directed first: the actionable-driver plane is
        # variant-aware — "EGFR positive" is not a treatment decision
        # (red-team confirmed exon20ins was being released onto FLAURA).
        drivers = first_line_actionable_drivers(facts)
        lead = drivers[0] if drivers else None
        if lead and lead["gene"] == "EGFR":
            classes = set(lead["classes"])
            if "exon20ins" in classes:
                opt("Amivantamab + carboplatin-pemetrexed",
                    ["amivantamab_chemo_first_line"],
                    "EGFR exon 20 insertion: PAPILLON — osimertinib is NOT "
                    "standard for this variant class")
            elif egfr_classical(facts):
                opt("First-line osimertinib", ["osimertinib_first_line"],
                    "EGFR ex19del/L858R: FLAURA")
                opt("Osimertinib + platinum-pemetrexed",
                    ["osimertinib_chemo_first_line"],
                    "FLAURA2 option — weigh for high disease burden / CNS "
                    "disease against added chemo toxicity")
                opt("Amivantamab + lazertinib", ["amivantamab_lazertinib"],
                    "MARIPOSA option — PFS/OS benefit vs osimertinib at "
                    "higher toxicity (VTE, infusion reactions); MDT/patient "
                    "preference")
            elif classes & EGFR_UNCOMMON_SENSITIZING \
                    and not (classes & {"c797s"}):
                opt("Afatinib (uncommon-sensitizing EGFR)",
                    ["afatinib_uncommon_first_line"],
                    "G719X/L861Q/S768I: LUX-Lung pooled data — a different "
                    "population from FLAURA; osimertinib is a reasonable "
                    "alternative on separate evidence")
            elif classes == {"t790m"}:
                opt("First-line osimertinib (de novo T790M)",
                    ["osimertinib_first_line"],
                    "De novo T790M: osimertinib retains activity; confirm "
                    "no co-occurring resistance alteration")
            else:
                # Unclassified / resistance-pattern EGFR: fail toward the
                # molecular tumor board, never toward a guessed TKI or ICI.
                plan["mdt_referral"] = True
                plan["workup_needed"].append(
                    "EGFR variant classification (exact alteration + "
                    "sensitizing status) — molecular tumor board review")
                plan["uncertainties"].append(
                    f"EGFR positive but variant class "
                    f"{'/'.join(sorted(classes)) or 'unreported'} does not "
                    f"map to a first-line indication — no systemic regimen "
                    f"is proposed until the variant is classified.")
        elif lead and lead["gene"] == "ALK":
            opt("First-line lorlatinib", ["lorlatinib_first_line"],
                "ALK+: CROWN")
        elif lead and lead["gene"] == "ROS1":
            opt("First-line ROS1 TKI (repotrectinib)",
                ["repotrectinib_first_line"],
                "ROS1 fusion: TRIDENT-1; entrectinib/crizotinib/"
                "taletrectinib are alternatives — ICI monotherapy is not a "
                "substitute regardless of PD-L1")
        elif lead and lead["gene"] == "RET":
            opt("First-line selpercatinib", ["selpercatinib_first_line"],
                "RET fusion: LIBRETTO-431 beat chemo±pembrolizumab "
                "head-to-head — PD-L1 level does not redirect to ICI")
        elif lead and lead["gene"] == "MET":
            opt("First-line capmatinib", ["capmatinib_first_line"],
                "MET exon 14 skipping: GEOMETRY (tepotinib per VISION is "
                "the alternative)")
        elif lead and lead["gene"] == "BRAF":
            opt("First-line dabrafenib + trametinib",
                ["dabrafenib_trametinib_first_line"],
                "BRAF V600E: BRF113928")
        elif lead and lead["gene"] == "NTRK":
            opt("First-line larotrectinib", ["larotrectinib_first_line"],
                "NTRK fusion: tumor-agnostic evidence (entrectinib is the "
                "CNS-active alternative)")
        elif isinstance(tps, (int, float)) and tps >= 50:
            opt("Pembrolizumab monotherapy", ["pembro_monotherapy"],
                "PD-L1 TPS≥50% driver-negative: KEYNOTE-024")
        elif _nonsquamous(facts):
            opt("Pembrolizumab + pemetrexed-platinum",
                ["pembro_pemetrexed_platinum"],
                "Driver-negative nonsquamous: KEYNOTE-189")
        else:
            opt("Pembrolizumab + carboplatin-taxane", ["pembro_carbo_taxane"],
                "Driver-negative squamous: KEYNOTE-407")
        for later in later_line_actionable_drivers(facts):
            plan["uncertainties"].append(
                f"{later['gene']} on record: {later['note']}.")
        # Panel completeness per current guidelines: EGFR/ALK alone is no
        # longer an adequate driver assessment for stage IV non-squamous.
        if _nonsquamous(facts) and not drivers and not facts.get("ngs_done"):
            extended = [g.upper() for g in ("ros1", "ret", "met", "braf",
                                            "ntrk", "erbb2", "kras")
                        if _driver_unknown(facts, g)]
            if extended:
                plan["workup_needed"].append(
                    "Broad multigene panel (tissue/plasma NGS) covering "
                    + "/".join(extended)
                    + " — EGFR/ALK alone is not a complete driver assessment")
                plan["uncertainties"].append(
                    "Systemic options are provisional: the extended driver "
                    "panel (" + "/".join(extended) + ") is not on record and "
                    "a positive result would change first-line therapy.")
        if stage_group == "IVA" and str(
            facts.get("disease_extent") or ""
        ).upper() == "OLIGOMETASTATIC":
            opt("Local consolidative therapy after systemic response",
                ["sbrt_oligomet_lct"],
                "Oligometastatic IVA: LCT per Gomez phase II, MDT framing")
        plan["options"].append({
            "name": "Early palliative-care integration", "regimen_ids": [],
            "rationale": "Improves outcomes alongside systemic therapy"})
        ecog = facts.get("ecog_ps")
        if isinstance(ecog, int) and ecog >= 3 and not (egfr or alk):
            plan["uncertainties"].append(
                "ECOG ≥3 driver-negative: best supportive care is a legitimate "
                "primary recommendation; goals-of-care discussion required.")
        plan["summary"] = f"Stage {stage_group}: biomarker-directed systemic therapy."
        return plan

    # Occult / unknown → workup.
    plan["intent"] = "workup"
    plan["summary"] = "Stage unresolved: complete the staging workup first."
    plan["workup_needed"] = [step["test"] for step in workup_plan(facts, "")]
    return plan


class TreatmentAgent:
    skill_id = "nsclc.treatment"

    def __init__(self, llm: Any | None = None, *, skill_registry: Any | None = None) -> None:
        self.llm = llm
        self.skill_registry = skill_registry

    def run(self, state: CaseRunState, tools: Any, broker: Any) -> None:
        stage_group = str(state.staging.get("stage_group") or "")
        module_key = state.routing.get("module_key")
        module = load_module(module_key) if module_key else None

        # Conversation-layer fast path: a pure-question turn hands in the
        # previous turn's plan with the fingerprint of the facts that produced
        # it. Byte-identical decision facts → reuse the plan and skip the
        # expensive tool-loop; the critic still re-audits it in full. The
        # cache is consumed FIRST so a critic repair pass (which re-enters
        # this method) always re-plans for real.
        cache, state.plan_cache = state.plan_cache, None
        if cache is not None and isinstance(cache.get("plan"), dict) \
                and cache.get("fingerprint") == decision_fingerprint(state.facts):
            import copy

            plan = copy.deepcopy(cache["plan"])
            plan["reused_from_previous_turn"] = True
            plan.pop("guideline_refs", None)  # re-attached fresh below
            # The cached citations point at the PREVIOUS run's ledger. The
            # cache carries the evidence rows themselves — re-add them to
            # THIS run's ledger with their original tool-declared grades and
            # remap the citation ids; a citation whose row did not travel is
            # dropped, never left dangling.
            id_map: dict[str, str] = {}
            for row in cache.get("evidence") or []:
                if not isinstance(row, dict):
                    continue
                if row.get("source") == "guideline_lookup":
                    # KG context rows are not carried over: the context is
                    # re-attached fresh below, and carrying them too would
                    # grow the citation list by two rows every reused turn.
                    continue
                payload = dict(row.get("payload") or {})
                new_id = state.add_evidence(
                    str(row.get("level") or ""), str(row.get("source") or ""),
                    str(row.get("summary") or ""), payload,
                    ok=bool(payload.pop("tool_ok", True)),
                    error=payload.pop("tool_error", None),
                    source_version=row.get("source_version"))
                old_id = row.get("evidence_id")
                if old_id:
                    id_map[str(old_id)] = new_id
            plan["citations"] = [
                id_map[c] for c in plan.get("citations") or []
                if isinstance(c, str) and c in id_map
            ]
            if not plan["citations"] and plan.get("regimen_ids"):
                # Old-style cache without evidence rows: anchor the regimens
                # from the registry instead of releasing an unbacked plan.
                plan["citations"] = self._anchor_regimens(
                    state, tools, broker, plan)
            state.outputs["treatment_plan"] = plan
            self._attach_kg_context(state, tools, broker, plan, stage_group)
            self._attach_prognosis(state, tools, broker, stage_group)
            self._attach_indications(state, stage_group)
            self._claim_options(state)
            state.trace(
                "TreatmentAgent", "plan_reused",
                input_summary=f"fingerprint {cache['fingerprint'][:12]}…",
                output_summary="previous-turn plan reused (facts unchanged); "
                               "tool-loop skipped, critic re-audits",
            )
            return

        staging_block = self._staging_block(state)
        spec = self.skill_registry.get(self.skill_id) if self.skill_registry else None
        loop_result = None
        if spec is not None and spec.autonomous:
            loop = ToolLoop(
                self.llm, tools, broker, state,
                agent_name="TreatmentAgent", skill_id=self.skill_id,
                skill_spec=spec, staging_block=staging_block,
                max_tokens=max(4096, module.min_output_tokens if module else 4096),
            )
            context = {
                "complaint": state.complaint[:3000],
                "facts": state.facts,
                "stage_decision_core": module.core if module else "",
                "imaging_findings": state.outputs.get("imaging"),
                "open_information_gaps": state.missing_information,
            }
            loop_result = loop.run(
                "Produce the stage-appropriate, evidence-cited treatment plan.",
                context, "TreatmentPlan",
            )
            state.outputs["treatment_loop"] = loop_result.to_dict()

        if loop_result is not None and loop_result.ok and loop_result.output:
            plan = dict(loop_result.output)
            plan["origin"] = "llm_tool_loop"
            plan["citations"] = loop_result.citations
            state.outputs["treatment_plan"] = plan
        else:
            plan = deterministic_plan(stage_group, state.facts)
            plan["citations"] = self._anchor_regimens(state, tools, broker, plan)
            state.outputs["treatment_plan"] = plan

        # Whatever produced the plan — tool loop, rule fallback, or a reused
        # cache — the case-matched KG context is attached the same way.
        self._attach_kg_context(state, tools, broker,
                                state.outputs["treatment_plan"], stage_group)
        self._attach_prognosis(state, tools, broker, stage_group)
        self._attach_indications(state, stage_group)
        self._claim_options(state)
        state.trace(
            "TreatmentAgent", "plan",
            output_summary=f"{len(state.outputs['treatment_plan'].get('options') or [])} "
                           f"option(s), origin="
                           f"{state.outputs['treatment_plan'].get('origin')}",
        )

    @staticmethod
    def _attach_kg_context(
        state: CaseRunState, tools: Any, broker: Any,
        plan: dict[str, Any], stage_group: str,
    ) -> None:
        """Attach guideline-KG context to a rule-mode plan, contained.

        Two brokered ``guideline_lookup`` calls (supporting + negative
        knowledge) land in the ledger at their tool-declared grade —
        ``kg_llm_extracted``, NON-RELEASABLE — so the context can inform the
        reply and the tumor board without ever becoming release support.
        Cautions are advisory by design: an unverified extraction must not
        acquire veto power, so nothing here flags or blocks. Runs on every
        plan path — tool loop, rule fallback, reused cache — so the
        oncologist view always carries the same case-matched context; the
        model additionally holds the same tool for its own queries.

        The quoted prose lives in ``state.outputs["guideline_context"]``,
        NOT inside the plan: the rule engine scans the plan as the system's
        own words, and a quoted ESMO sentence like "durvalumab … after
        concurrent CRT" must not read as the plan proposing concurrent
        durvalumab (confirmed false-block in testing). The plan carries only
        rec ids and ledger citations — and because everything inside the
        plan stays scanned, a model cannot smuggle prose past the critic by
        inventing a key of the same name.
        """
        if not stage_group:
            return  # unstaged runs are workup-mode; broad hits are noise
        from ..knowledge.biomarkers import driver_status

        from ..state import EvidenceLevel as EL

        genes = sorted(
            str(g).upper()
            for g, v in (state.facts.get("driver_mutations") or {}).items()
            if driver_status(str(v)) == "positive"
        )
        histology = str(state.facts.get("histologic_category") or "") or None
        context: dict[str, Any] = {}
        evidence_ids: list[str] = []
        excluded: list[str] = []
        calls = (
            ("supporting", {"stage": stage_group, "histology": histology,
                            "gene": genes[0] if genes else None,
                            "topic": "systemic_treatment"}),
            ("cautions", {"stage": stage_group, "histology": histology,
                          "direction": "negative"}),
        )
        for key, kwargs in calls:
            result = tools.call(
                broker, "guideline_lookup",
                case_facts=state.facts, case_stage=stage_group,
                **{k: v for k, v in kwargs.items() if v})
            if not result.ok or result.is_stub:
                continue
            hits = result.data.get("hits") or []
            # Hard population exclusion is earned by human review: the KG
            # drops clinician-verified entries whose trusted criteria
            # confidently mismatch this case, and names them.
            excluded.extend(
                str(r) for r in
                result.data.get("excluded_verified_mismatch") or [])
            if not hits:
                continue
            # Evidence rows split by curation status: verified content is
            # guideline-grade (releasable); machine-extracted content keeps
            # its non-releasable grade. One result, two honest rows.
            partitions = (
                ("clinician_verified", EL.GUIDELINE.value,
                 [h for h in hits
                  if h.get("curation_status") == "clinician_verified"]),
                ("llm_extracted", EL.KG_EXTRACTED.value,
                 [h for h in hits
                  if h.get("curation_status") != "clinician_verified"]),
            )
            entries: list[dict[str, Any]] = []
            for label, level, part in partitions:
                if not part:
                    continue
                eid = state.add_evidence(
                    level, "guideline_lookup",
                    f"{len(part)} {key} KG rec(s), {label}",
                    {"hits": part}, source_version=result.source_version)
                evidence_ids.append(eid)
                entries += [
                    {"rec_id": h.get("rec_id"),
                     "guideline": h.get("guideline"),
                     "direction": h.get("direction"),
                     "grade": (h.get("grade") or {}).get("strength_original"),
                     "recommendation": h.get("recommendation"),
                     "cross_region": (h.get("cross_region") or {}).get("agreement"),
                     "curation_status": h.get("curation_status"),
                     "eligibility": (h.get("eligibility") or {}).get("verdict"),
                     "evidence_id": eid}
                    for h in part
                ]
                if key == "cautions" and label == "clinician_verified":
                    # Verified negative knowledge that fits this case is
                    # worth a visible flag. Advisory: flags never block —
                    # veto power stays with the deterministic rule engine.
                    for h in part:
                        if (h.get("eligibility") or {}).get("verdict") \
                                == "consistent":
                            state.flag(
                                f"KG_VERIFIED_CAUTION[{h.get('rec_id')}]: "
                                f"{str(h.get('recommendation'))[:160]}")
            context[key] = entries[:4]
        if excluded:
            context["excluded_verified_mismatch"] = sorted(set(excluded))
        if not any(context.get(k) for k in ("supporting", "cautions")):
            return
        context["curation_status"] = "llm_extracted"
        context["note"] = ("KG 上下文为机器抽取、未经临床复核：仅供权衡，"
                           "不构成放行依据，也不触发拦截")
        state.outputs["guideline_context"] = context
        plan["guideline_refs"] = {
            key: [h["rec_id"] for h in context.get(key) or []
                  if isinstance(h, dict)]
            for key in ("supporting", "cautions")
            if context.get(key)
        }
        plan.setdefault("citations", [])
        plan["citations"] = list(plan["citations"]) + evidence_ids
        state.trace(
            "TreatmentAgent", "kg_context",
            output_summary=f"{len(context.get('supporting') or [])} supporting"
                           f" + {len(context.get('cautions') or [])} caution "
                           f"KG rec(s) attached (kg_llm_extracted)",
            evidence_ids=evidence_ids,
        )

    @staticmethod
    def _attach_indications(state: CaseRunState, stage_group: str) -> None:
        """Per-regimen indication verdicts for the plan on record.

        The same declarations the planner gated on and the critic will
        audit against, evaluated once more over the FINAL plan (which may
        be model-authored) and published for the oncologist view — the
        clinician sees why each regimen is in population, provisional, or
        (for a model plan the critic is about to block) out of it.
        """
        from ..knowledge.indications import evaluate_indication

        plan = state.outputs.get("treatment_plan") or {}
        rids = [str(r) for r in plan.get("regimen_ids") or []]
        if not rids:
            return
        state.outputs["indication_report"] = {
            "regimens": [evaluate_indication(rid, stage_group, state.facts)
                         for rid in rids],
            "note": "声明式适应证判定（eligible/ineligible/unknown）——"
                    "unknown 表示病例缺少判定所需事实，已列入补检建议",
        }

    @staticmethod
    def _attach_prognosis(
        state: CaseRunState, tools: Any, broker: Any, stage_group: str,
    ) -> None:
        """Attach cohort prognosis context, evidence-anchored.

        The figures are published stage-cohort statistics
        (``published_cohort_statistics`` — releasable as cited population
        context); modifier trials are anchored through ``trial_lookup`` so
        their outcome numbers stay registry entries, cited not restated.
        Prose and figures live in ``outputs["prognosis"]``, outside the
        plan (the rule engine scans the plan as the system's own words),
        and the patient view never receives the table (see render).
        """
        if not stage_group or "prognosis" in state.outputs:
            return
        from ..knowledge.prognosis import prognosis_for

        context = prognosis_for(
            stage_group, str(state.staging.get("prefix") or
                             (state.facts.get("tnm") or {}).get("prefix")
                             or "c"),
            state.facts,
            migration_notes=state.staging.get("migration_notes") or [],
        )
        if context is None:
            return
        evidence_ids = [state.add_evidence(
            EvidenceLevel.COHORT, "prognosis_table",
            f"stage {stage_group} 5-year OS cohort figure "
            f"({context['classification_basis']} basis)",
            {k: context[k] for k in ("stage_group", "classification_basis",
                                     "five_year_os_percent_approx", "cohort")},
            source_version=context["cohort"])]
        for modifier in context["modifiers"]:
            for trial_ref in modifier.get("anchor_trials") or []:
                result = tools.call(broker, "trial_lookup", query=trial_ref)
                if result.ok and result.data.get("match") == "exact":
                    eid = state.add_evidence(
                        result.resolved_level(), "trial_lookup",
                        result.summary, result.data,
                        source_version=result.source_version)
                    evidence_ids.append(eid)
                    modifier["evidence_id"] = eid
        context["evidence_ids"] = evidence_ids
        state.outputs["prognosis"] = context
        state.add_claim(
            "prognosis_context",
            f"Stage {stage_group} cohort five-year OS "
            f"~{context['five_year_os_percent_approx']}% "
            f"({context['classification_basis']}; population statistic, "
            f"not an individual prediction)"
            if context["five_year_os_percent_approx"] is not None else
            f"Stage {stage_group}: no per-group cohort survival figure "
            f"published; not padded",
            evidence_ids, origin="rule",
            subject={"population_stage": stage_group,
                     "classification_basis": context["classification_basis"]},
            support_relation="population_statistic")
        state.trace(
            "TreatmentAgent", "prognosis_context",
            output_summary=f"cohort figure + {len(context['modifiers'])} "
                           f"directional modifier(s)",
            evidence_ids=evidence_ids)

    @staticmethod
    def _anchor_regimens(
        state: CaseRunState, tools: Any, broker: Any, plan: dict[str, Any]
    ) -> list[str]:
        """Anchor the plan's regimens in THIS run's ledger so its claims are
        citable evidence like any other. Cheap: registry lookups, no model."""
        evidence_ids: list[str] = []
        trial_refs = plan.setdefault("trial_refs", [])
        for rid in plan.get("regimen_ids") or []:
            result = tools.call(broker, "trial_lookup",
                                query=(regimen_lib.get(rid).trial_ids[0]
                                       if regimen_lib.get(rid) and regimen_lib.get(rid).trial_ids
                                       else rid))
            if result.ok and result.data.get("match") == "exact":
                eid = state.add_evidence(
                    result.resolved_level(), "trial_lookup",
                    result.summary, result.data,
                    source_version=result.source_version)
                evidence_ids.append(eid)
                trial_id = result.data["trial"]["trial_id"]
                if trial_id not in trial_refs:
                    trial_refs.append(trial_id)
        return evidence_ids

    @staticmethod
    def _claim_options(state: CaseRunState) -> None:
        """One claim per treatment option, bound to ITS evidence.

        Claim-level, not plan-level (red-team review §11): the cCRT claim
        must not carry the LAURA row and the osimertinib claim must not
        carry the RTOG-0617 row just because both landed in one citation
        pool. Each option's claim cites only the trial-anchor rows whose
        trial covers one of the option's own regimens; a regimen-free
        option (surgery, surveillance, palliative-care integration) is
        ``protocol_grounded`` — its authority is the routed protocol
        module, and it does not borrow trial rows it is not entitled to.
        """
        plan = state.outputs.get("treatment_plan") or {}
        stage_group = str(state.staging.get("stage_group") or "")

        # evidence_id → the trial its row certifies (registry lookups only).
        # In a parallel wave this agent's own rows still sit in the wave
        # buffer under temp ids — scan both; the merge remaps temp ids in
        # claim evidence lists and subjects alike.
        trial_rows: dict[str, dict[str, Any]] = {}
        for eid, evidence in state.evidence.items():
            if evidence.source != "trial_lookup":
                continue
            trial = (evidence.payload or {}).get("trial") or {}
            if trial.get("trial_id"):
                trial_rows[eid] = trial
        scope = getattr(state, "_scope", None)
        for temp_id, record in getattr(scope, "evidence", None) or []:
            if record.get("source") != "trial_lookup":
                continue
            trial = (record.get("payload") or {}).get("trial") or {}
            if trial.get("trial_id"):
                trial_rows[temp_id] = trial

        for option in plan.get("options") or []:
            regimen_ids = [str(r) for r in option.get("regimen_ids") or []]
            trial_ids = {
                tid for rid in regimen_ids
                for tid in (regimen_lib.get(rid).trial_ids
                            if regimen_lib.get(rid) else ())
            }
            entailed = [
                eid for eid, trial in trial_rows.items()
                if set(trial.get("regimen_ids") or []) & set(regimen_ids)
                or trial.get("trial_id") in trial_ids
            ]
            state.add_claim(
                "treatment_option",
                str(option.get("name") or "")[:200],
                entailed,
                origin=plan.get("origin", "rule"),
                subject={
                    "intervention_regimen_ids": regimen_ids,
                    "population_stage": stage_group,
                    "intent": plan.get("intent"),
                },
                support_relation="trial_anchor" if regimen_ids
                else "protocol_grounded",
            )

    @staticmethod
    def _staging_block(state: CaseRunState) -> str:
        if not state.staging:
            return "(unstaged — workup mode)"
        lines = [
            "=== DETERMINISTIC STAGING (computed by the symbolic engine, not by you) ===",
            f"TNM: {state.staging.get('tnm')}",
            f"Stage group: {state.staging.get('stage_group')} "
            f"({state.staging.get('edition')})",
        ]
        for note in state.staging.get("migration_notes") or []:
            lines.append(f"8th→9th migration: {note}")
        lines.append("Treat this stage as authoritative. Do NOT re-derive it.")
        return "\n".join(lines)


# ----------------------------------------------------------------- dose plan

class DosePlanAgent:
    """Deterministic dose channel — the only place numbers attach to plans."""

    skill_id = "nsclc.dose_planning"

    def __init__(self, llm: Any | None = None) -> None:
        self.llm = llm  # unused; uniform constructor

    def run(self, state: CaseRunState, tools: Any, broker: Any) -> None:
        plan = state.outputs.get("treatment_plan") or {}
        regimen_ids = [str(r) for r in plan.get("regimen_ids") or []]
        if not regimen_ids:
            state.warn("dose planning requested but the plan names no regimens")
            return
        expanded: list[dict[str, Any]] = []
        gates_checked: list[dict[str, Any]] = []
        excluded: list[dict[str, str]] = []
        for rid in regimen_ids:
            gate = tools.call(broker, "dose_gate_check", regimen_id=rid,
                              facts=state.facts)
            if not gate.ok:
                excluded.append({"regimen_id": rid, "reason": gate.error or "gate check failed"})
                continue
            gates_checked.append({"regimen_id": rid, **gate.data})
            failed = [g for g in gate.data.get("gates", []) if g["status"] == "fail"]
            if failed:
                excluded.append({
                    "regimen_id": rid,
                    "reason": "; ".join(f"{g['gate']}: {g['note']}" for g in failed),
                })
                continue
            unverified = [g for g in gate.data.get("gates", [])
                          if g["status"] == "unverified"]
            detail = tools.call(broker, "regimen_detail", regimen_id=rid)
            if detail.ok:
                eid = state.add_evidence(
                    detail.resolved_level(), "regimen_detail", detail.summary,
                    detail.data, source_version=detail.source_version)
                entry = {**detail.data.get("regimen", {}), "evidence_id": eid}
                if unverified:
                    # Carried forward loudly: the tumor board resolves these
                    # before any approval — a silent inclusion would read as
                    # a passed gate in the audit trail.
                    entry["gates_unverified"] = [
                        {"gate": g["gate"], "note": g["note"]} for g in unverified]
                expanded.append(entry)
        interactions = tools.call(
            broker, "interaction_check",
            medications=[c["drug"] for r in expanded for c in r.get("components", [])]
            + [str(m) for m in state.facts.get("medications") or []],
        )
        state.outputs["dose_plan"] = {
            "regimens": expanded,
            "gates_checked": gates_checked,
            "excluded": excluded,
            "interactions": interactions.data.get("hits", []) if interactions.ok else [],
            "requires_tumor_board_approval": True,
        }
        if expanded:
            state.release_status = "draft_for_tumor_board"
        state.trace("DosePlanAgent", "expand",
                    output_summary=f"{len(expanded)} regimen(s) expanded, "
                                   f"{len(excluded)} excluded")
