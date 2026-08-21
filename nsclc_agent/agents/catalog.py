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
from ..perception import ImagingError, ImagingReader, fold_into_case
from ..prompts import load_module
from ..safety import emergencies
from ..staging import StagingError, route, normalize_stage_group, stage_from_strings
from ..state import CaseRunState, EvidenceLevel
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
    skill_id = "nsclc.perception"

    def __init__(self, llm: Any | None = None, *, base_dir: Path | None = None) -> None:
        self.llm = llm
        self.base_dir = base_dir

    def run(self, state: CaseRunState, tools: Any, broker: Any) -> None:
        refs = [img.get("ref") for img in state.images if img.get("ref")]
        if not refs:
            return
        try:
            reader = ImagingReader(self.llm)
        except ImagingError as exc:
            state.flag(f"NO_VISION_PROVIDER: {exc} — films not read")
            return
        try:
            findings = reader.read(
                [str(r) for r in refs],
                context=state.complaint,
                base_dir=self.base_dir,
                budget=state.budget,
            )
        except ImagingError as exc:
            state.flag(f"IMAGING_READ_FAILED: {exc}")
            return
        tnm = dict(state.facts.get("tnm") or {})
        seeded, flags = fold_into_case(
            tnm.get("t"), tnm.get("n"), tnm.get("m"), findings)
        for kind, value in seeded.items():
            tnm[kind] = value
        state.facts["tnm"] = tnm
        for flag in flags:
            state.flag(flag)
        eid = state.add_evidence(
            EvidenceLevel.MODEL, "imaging_reader",
            f"proposed cT={findings.candidate_t} cN={findings.candidate_n} "
            f"cM={findings.candidate_m} ({findings.confidence})",
            findings.to_dict(),
        )
        state.outputs["imaging"] = findings.to_dict()
        state.trace("PerceptionAgent", "read", evidence_ids=[eid],
                    output_summary=f"{findings.n_images} image(s) read")


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

def _driver_value(facts: dict[str, Any], gene: str) -> str:
    return str((facts.get("driver_mutations") or {}).get(gene) or "").lower()


def _driver_positive(facts: dict[str, Any], gene: str) -> bool:
    value = _driver_value(facts, gene)
    return bool(value) and value not in (
        "negative", "wild type", "wildtype", "wt", "not detected", "none",
        "阴性", "not_tested", "unknown", "pending")


def _driver_unknown(facts: dict[str, Any], gene: str) -> bool:
    return _driver_value(facts, gene) in ("", "not_tested", "unknown", "pending")


def _nonsquamous(facts: dict[str, Any]) -> bool:
    hist = str(facts.get("histologic_category") or "").lower()
    return not hist or "squamous" not in hist or "adeno" in hist


def _resectability(facts: dict[str, Any]) -> str:
    return str(facts.get("resectability_category")
               or facts.get("resectability") or "").upper()


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
        plan["options"].append({"name": name, "regimen_ids": regimen_ids,
                                "rationale": rationale})
        for rid in regimen_ids:
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
            "AIS/MIA: complete (often sublobar) resection is typically curative")
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
        if stage_group == "IB" and egfr:
            opt("Adjuvant osimertinib after resection", ["osimertinib_adjuvant"],
                "ADAURA covers resected IB EGFR ex19del/L858R")
        plan["summary"] = "Stage I: operability decides surgery vs SBRT."
        return plan

    if stage_group in ("IIA", "IIB", "IIIA"):
        resect = _resectability(facts)
        unresectable = resect == "UNRESECTABLE"
        if unresectable:
            opt("Definitive concurrent chemoradiation", ["ccrt_60gy"],
                "Unresectable locally advanced disease")
            if egfr:
                opt("Consolidation osimertinib", ["osimertinib_consolidation"],
                    "EGFR-mutated unresectable III → LAURA, not durvalumab")
            else:
                opt("Consolidation durvalumab", ["durva_consolidation"],
                    "PACIFIC consolidation after cCRT without progression")
            plan["summary"] = f"Stage {stage_group} unresectable: cCRT + consolidation by driver."
            return plan
        if egfr:
            opt("Surgery → adjuvant osimertinib (± chemo)", ["osimertinib_adjuvant"],
                "EGFR+ resectable: targeted adjuvant standard; no perioperative IO")
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
            if egfr or alk:
                rid = "osimertinib_adjuvant" if egfr else "alectinib_adjuvant"
                trial = "ADAURA" if egfr else "ALINA"
                opt(f"Surgery → adjuvant {'osimertinib' if egfr else 'alectinib'}",
                    [rid], f"Driver-positive resectable IIIB — {trial} extrapolated beyond IIIA")
                plan["extrapolations"].append({
                    "trial_id": trial,
                    "justification": f"{trial} enrolled up to IIIA; resectable "
                                     f"IIIB use is a documented extrapolation "
                                     f"per MDT decision",
                })
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
        if egfr:
            opt("Consolidation osimertinib", ["osimertinib_consolidation"],
                "EGFR+ unresectable III → LAURA, not durvalumab")
        else:
            opt("Consolidation durvalumab", ["durva_consolidation"],
                "PACIFIC; start ≤42 days post-CRT; never concurrent")
        plan["summary"] = f"Stage {stage_group}: definitive cCRT + consolidation by driver."
        return plan

    if stage_group in ("IVA", "IVB"):
        plan["intent"] = "palliative"
        if egfr:
            opt("First-line osimertinib", ["osimertinib_first_line"],
                "EGFR ex19del/L858R: FLAURA")
        elif alk:
            opt("First-line lorlatinib", ["lorlatinib_first_line"],
                "ALK+: CROWN")
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
            # Anchor the rule plan's regimens in the ledger so its claims are
            # citable evidence like any other.
            evidence_ids: list[str] = []
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
                    if trial_id not in plan["trial_refs"]:
                        plan["trial_refs"].append(trial_id)
            plan["citations"] = evidence_ids
            state.outputs["treatment_plan"] = plan

        for option in state.outputs["treatment_plan"].get("options") or []:
            state.add_claim(
                "treatment_option",
                str(option.get("name") or "")[:200],
                state.outputs["treatment_plan"].get("citations") or [],
                origin=state.outputs["treatment_plan"].get("origin", "rule"),
            )
        state.trace(
            "TreatmentAgent", "plan",
            output_summary=f"{len(state.outputs['treatment_plan'].get('options') or [])} "
                           f"option(s), origin="
                           f"{state.outputs['treatment_plan'].get('origin')}",
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
            detail = tools.call(broker, "regimen_detail", regimen_id=rid)
            if detail.ok:
                eid = state.add_evidence(
                    detail.resolved_level(), "regimen_detail", detail.summary,
                    detail.data, source_version=detail.source_version)
                expanded.append({**detail.data.get("regimen", {}), "evidence_id": eid})
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
