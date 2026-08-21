"""Deterministic treatment-plan safety rules — the checks that were prompts in
v0.1 and are code here.

Every rule takes the run's authoritative context — the engine-computed staging,
the structured case facts, and the *parsed* model plan — and returns typed
violations. The critic runs this engine unconditionally; a ``block`` violation
prevents release and can request a bounded repair loop. The model can add
issues; it can never remove one of these.

The rules encode boundaries the protocol modules state in prose:

* N3 disease is not surgical.
* Known EGFR/ALK alterations exclude perioperative/adjuvant immunotherapy.
* EGFR-mutated unresectable stage III consolidates with osimertinib (LAURA),
  not durvalumab (PACIFIC EGFR subgroup: no benefit).
* Durvalumab is never concurrent with chemoradiation (PACIFIC-2 negative).
* Thoracic RT is not dose-escalated past ~66 Gy (RTOG 0617: 74 Gy harmed OS).
* A regimen is used inside its trial's stage boundaries, or the plan must
  declare the extrapolation explicitly with justification.
* Stage 0 (AIS/MIA) receives no systemic therapy.
* Stage IV with an actionable driver gets driver-directed first-line therapy.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..knowledge import regimens as regimen_lib
from ..knowledge.trials import PERIOP_ADJUVANT_IO_TRIALS, TRIALS_BY_ID, resolve_trial_id

#: Dose patterns a *model* output may never contain (mg, mg/m², AUC, Gy…).
#: Doses enter plans only through the deterministic regimen library.
DOSE_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:mg/m2|mg/m²|mg/kg|mg\b|毫克|g\b|克|Gy\b|戈瑞)|AUC\s*\d",
    re.IGNORECASE,
)

_SURGERY_RE = re.compile(
    r"lobectomy|pneumonectomy|segmentectomy|surgical resection|resection as primary|"
    r"upfront surgery|肺叶切除|全肺切除|肺段切除|手术切除|直接手术",
    re.IGNORECASE,
)
_CONCURRENT_DURVA_RE = re.compile(
    r"(?:concurrent|simultaneous|同期|同步)[^.。;；]{0,40}durvalumab|"
    r"durvalumab[^.。;；]{0,40}(?:concurrent(?:ly)?\s+with\s+(?:chemo)?radi|同期|同步放化疗)",
    re.IGNORECASE,
)
_RT_DOSE_RE = re.compile(r"(\d{2}(?:\.\d+)?)\s*Gy", re.IGNORECASE)


@dataclass
class Violation:
    rule_id: str
    severity: str  # "block" | "warn"
    message: str
    #: Agent whose output should be recomputed if a repair loop is granted.
    repair_agent: str = "TreatmentAgent"

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id, "severity": self.severity,
            "message": self.message, "repair_agent": self.repair_agent,
        }


@dataclass
class PlanContext:
    """Everything the rule engine reads. Built once by the critic."""

    stage_group: str = ""
    n_category: str = ""
    facts: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------- derived views
    @property
    def plan_text(self) -> str:
        return json.dumps(self.plan, ensure_ascii=False) if self.plan else ""

    @property
    def regimen_ids(self) -> list[str]:
        return [str(r) for r in self.plan.get("regimen_ids") or []]

    @property
    def trial_refs(self) -> list[str]:
        """Trial ids referenced by the plan (resolved through the alias map)."""
        refs: list[str] = []
        for raw in self.plan.get("trial_refs") or []:
            resolved = resolve_trial_id(str(raw))
            refs.append(resolved or str(raw))
        # Regimens carry their anchors too.
        for rid in self.regimen_ids:
            regimen = regimen_lib.get(rid)
            if regimen:
                refs.extend(regimen.trial_ids)
        return sorted(set(refs))

    @property
    def declared_extrapolations(self) -> set[str]:
        out: set[str] = set()
        for item in self.plan.get("extrapolations") or []:
            if isinstance(item, dict) and item.get("trial_id") and item.get("justification"):
                resolved = resolve_trial_id(str(item["trial_id"]))
                if resolved:
                    out.add(resolved)
        return out

    def driver_positive(self, *genes: str) -> bool:
        drivers = self.facts.get("driver_mutations") or {}
        for gene in genes:
            value = str(drivers.get(gene.lower()) or drivers.get(gene.upper()) or "").lower()
            if value and value not in ("negative", "wild type", "wildtype", "wt",
                                       "not detected", "none", "阴性", "not_tested",
                                       "unknown", "pending"):
                return True
        return False

    def driver_status_known(self, gene: str) -> bool:
        drivers = self.facts.get("driver_mutations") or {}
        value = str(drivers.get(gene.lower()) or drivers.get(gene.upper()) or "").lower()
        return bool(value) and value not in ("not_tested", "unknown", "pending", "")


# --------------------------------------------------------------------- rules

def _rule_n3_no_surgery(ctx: PlanContext) -> list[Violation]:
    if ctx.n_category != "N3":
        return []
    if any("surgery" in rid or "perioperative" in rid or "neoadjuvant" in rid
           for rid in ctx.regimen_ids) or _SURGERY_RE.search(ctx.plan_text):
        return [Violation(
            "N3_NO_SURGERY", "block",
            "N3 disease (contralateral mediastinal / supraclavicular nodes) is "
            "unresectable; the plan proposes surgical resection. Definitive "
            "chemoradiation + consolidation is the curative-intent pathway.",
        )]
    return []


def _rule_driver_excludes_periop_io(ctx: PlanContext) -> list[Violation]:
    if not ctx.driver_positive("egfr", "alk"):
        return []
    out: list[Violation] = []
    for rid in ctx.regimen_ids:
        regimen = regimen_lib.get(rid)
        if regimen and regimen.contains_ici and regimen.setting in (
            "neoadjuvant", "perioperative", "adjuvant",
        ):
            out.append(Violation(
                "DRIVER_EXCLUDES_PERIOP_IO", "block",
                f"Known EGFR/ALK alteration with perioperative/adjuvant "
                f"immunotherapy ({regimen.name}): these trials excluded "
                f"EGFR/ALK+ disease and retrospective IO benefit is absent — "
                f"use targeted adjuvant standards (osimertinib/alectinib).",
            ))
    for tid in ctx.trial_refs:
        if tid in PERIOP_ADJUVANT_IO_TRIALS and not any(
            v.rule_id == "DRIVER_EXCLUDES_PERIOP_IO" for v in out
        ):
            out.append(Violation(
                "DRIVER_EXCLUDES_PERIOP_IO", "block",
                f"Plan anchors to {tid} in an EGFR/ALK-positive case — that "
                f"trial excluded (or showed no benefit in) driver-positive disease.",
            ))
    return out


def _rule_egfr_iii_consolidation(ctx: PlanContext) -> list[Violation]:
    if ctx.stage_group not in ("IIIA", "IIIB", "IIIC"):
        return []
    if not ctx.driver_positive("egfr"):
        return []
    uses_durva = "durva_consolidation" in ctx.regimen_ids or "PACIFIC" in ctx.trial_refs
    uses_osi = ("osimertinib_consolidation" in ctx.regimen_ids
                or "LAURA" in ctx.trial_refs)
    if uses_durva and not uses_osi:
        return [Violation(
            "EGFR_III_CONSOLIDATION", "block",
            "EGFR-mutated unresectable stage III: consolidation should be "
            "osimertinib (LAURA, PFS HR 0.16), not durvalumab — the PACIFIC "
            "EGFR subgroup showed no clear benefit.",
        )]
    return []


def _rule_no_concurrent_durvalumab(ctx: PlanContext) -> list[Violation]:
    if _CONCURRENT_DURVA_RE.search(ctx.plan_text):
        return [Violation(
            "NO_CONCURRENT_DURVALUMAB", "block",
            "Durvalumab given concurrently with chemoradiation — PACIFIC-2 was "
            "negative; durvalumab is consolidation only, started after CRT.",
        )]
    return []


def _rule_rt_dose(ctx: PlanContext) -> list[Violation]:
    out: list[Violation] = []
    for match in _RT_DOSE_RE.finditer(ctx.plan_text):
        dose = float(match.group(1))
        if dose > 66:
            out.append(Violation(
                "NO_RT_DOSE_ESCALATION", "block",
                f"Thoracic RT dose {dose:g} Gy exceeds the definitive standard — "
                f"RTOG 0617 showed 74 Gy *worsened* survival vs 60 Gy/30 fx.",
            ))
            break
    return out


def _rule_trial_stage_boundary(ctx: PlanContext) -> list[Violation]:
    if not ctx.stage_group:
        return []
    out: list[Violation] = []
    declared = ctx.declared_extrapolations
    for tid in ctx.trial_refs:
        trial = TRIALS_BY_ID.get(tid)
        if trial is None or not trial.stage_groups:
            continue
        if ctx.stage_group in trial.stage_groups:
            continue
        if tid in declared:
            out.append(Violation(
                "TRIAL_STAGE_EXTRAPOLATION", "warn",
                f"{tid} applied outside its enrolled stages "
                f"({'/'.join(sorted(trial.stage_groups))}) for stage "
                f"{ctx.stage_group} — declared as an extrapolation; keep the "
                f"uncertainty statement in the delivered plan.",
            ))
        else:
            out.append(Violation(
                "TRIAL_STAGE_BOUNDARY", "block",
                f"{tid} is applied to stage {ctx.stage_group} but enrolled "
                f"{'/'.join(sorted(trial.stage_groups))}. Either choose a "
                f"stage-covering regimen or declare the extrapolation "
                f"explicitly with justification.",
            ))
    return out


def _rule_stage0_no_systemic(ctx: PlanContext) -> list[Violation]:
    if ctx.stage_group != "0":
        return []
    systemic = [rid for rid in ctx.regimen_ids
                if regimen_lib.get(rid) and regimen_lib.get(rid).setting
                in ("adjuvant", "neoadjuvant", "perioperative", "first_line")]
    if systemic or re.search(r"adjuvant (?:chemo|immuno)|辅助化疗|辅助免疫", ctx.plan_text, re.IGNORECASE):
        return [Violation(
            "STAGE0_NO_SYSTEMIC", "block",
            "Stage 0 (AIS/MIA): complete resection (often sublobar) is curative "
            "— adjuvant/systemic therapy has no role and adds only harm.",
        )]
    return []


def _rule_driver_first_line(ctx: PlanContext) -> list[Violation]:
    if ctx.stage_group not in ("IVA", "IVB"):
        return []
    if not ctx.driver_positive("egfr", "alk"):
        return []
    targeted = any(
        rid in ("osimertinib_first_line", "osimertinib_chemo_first_line",
                "amivantamab_lazertinib", "lorlatinib_first_line")
        for rid in ctx.regimen_ids
    )
    ici_first = any(
        (regimen_lib.get(rid) or None) and regimen_lib.get(rid).contains_ici
        and regimen_lib.get(rid).setting == "first_line"
        for rid in ctx.regimen_ids
    )
    if ici_first and not targeted:
        return [Violation(
            "DRIVER_FIRST_LINE", "block",
            "Stage IV with an actionable EGFR/ALK driver: first-line therapy "
            "should be driver-directed (osimertinib / lorlatinib…), not "
            "chemo-immunotherapy — ICI efficacy is poor in driver-positive "
            "disease and sequencing ICI before a TKI raises toxicity.",
        )]
    return []


def _rule_ici_comorbidity(ctx: PlanContext) -> list[Violation]:
    comorbid = ctx.facts.get("comorbidities") or {}
    risky = comorbid.get("ild") or comorbid.get("active_autoimmune") \
        or comorbid.get("autoimmune_disease")
    if not risky:
        return []
    if any((regimen_lib.get(rid) or None) and regimen_lib.get(rid).contains_ici
           for rid in ctx.regimen_ids):
        return [Violation(
            "ICI_COMORBIDITY_CAUTION", "warn",
            "Checkpoint inhibitor planned with ILD / active autoimmune disease "
            "on record — document the risk-benefit discussion and monitoring "
            "plan; these patients were excluded from the registration trials.",
        )]
    return []


def _rule_ps_gate(ctx: PlanContext) -> list[Violation]:
    ecog = ctx.facts.get("ecog_ps")
    if not isinstance(ecog, int) or ecog < 3:
        return []
    aggressive = ("ccrt_60gy" in ctx.regimen_ids
                  or any("perioperative" in rid or "neoadjuvant" in rid
                         for rid in ctx.regimen_ids))
    if aggressive:
        return [Violation(
            "PS_GATE", "warn",
            f"ECOG PS {ecog} with concurrent CRT / perioperative therapy planned "
            f"— registration trials required PS 0–1 (PS 2 at most); document "
            f"the fitness rationale or de-escalate.",
        )]
    return []


def _rule_biomarker_before_systemic(ctx: PlanContext) -> list[Violation]:
    """Tier-A biomarkers must be known before committing systemic therapy."""
    if ctx.stage_group in ("0", "Occult", ""):
        return []
    histology = str(ctx.facts.get("histologic_category") or "").lower()
    planned_systemic = any(
        (regimen_lib.get(rid) or None) is not None
        and regimen_lib.get(rid).setting in
        ("adjuvant", "neoadjuvant", "perioperative", "first_line", "consolidation")
        for rid in ctx.regimen_ids
    )
    if not planned_systemic:
        return []
    if histology and "squamous" in histology and "adeno" not in histology:
        return []  # pure squamous: EGFR/ALK testing optional per guideline
    missing = [g for g in ("egfr", "alk") if not ctx.driver_status_known(g)]
    if missing:
        return [Violation(
            "BIOMARKER_GAP", "block",
            f"Systemic therapy committed with Tier-A biomarkers untested "
            f"({', '.join(g.upper() for g in missing)}): contemporary standard "
            f"requires EGFR/ALK (and PD-L1 where ICI is considered) before "
            f"finalizing — decisions are provisional pending testing.",
            repair_agent="TreatmentAgent",
        )]
    return []


def _rule_dose_scan(ctx: PlanContext) -> list[Violation]:
    """Model-authored fields may not carry dose numerics.

    The deterministic dose channel (``dose_plan`` output) is exempt — that is
    the *only* place numbers may live. Library identifiers (``ccrt_60gy``…)
    are scrubbed before scanning: an id is a reference into the deterministic
    library, not an authored numeric.
    """
    scrubbed = {k: v for k, v in ctx.plan.items() if k != "dose_plan"}
    blob = json.dumps(scrubbed, ensure_ascii=False)
    for rid in regimen_lib.REGIMENS_BY_ID:
        blob = blob.replace(rid, "")
    if DOSE_RE.search(blob):
        return [Violation(
            "DOSE_IN_MODEL_OUTPUT", "block",
            "A dose numeric appears in model-authored plan content. Doses enter "
            "plans only through the deterministic regimen library (dose_plan).",
        )]
    return []


RULES = (
    _rule_n3_no_surgery,
    _rule_driver_excludes_periop_io,
    _rule_egfr_iii_consolidation,
    _rule_no_concurrent_durvalumab,
    _rule_rt_dose,
    _rule_trial_stage_boundary,
    _rule_stage0_no_systemic,
    _rule_driver_first_line,
    _rule_ici_comorbidity,
    _rule_ps_gate,
    _rule_biomarker_before_systemic,
    _rule_dose_scan,
)


def check_plan(
    staging: dict[str, Any],
    facts: dict[str, Any],
    plan: dict[str, Any],
) -> list[Violation]:
    """Run every rule; returns violations, blockers first."""
    ctx = PlanContext(
        stage_group=str(staging.get("stage_group") or ""),
        n_category=str(staging.get("n_category") or ""),
        facts=facts or {},
        plan=plan or {},
    )
    violations: list[Violation] = []
    for rule in RULES:
        violations.extend(rule(ctx))
    violations.sort(key=lambda v: 0 if v.severity == "block" else 1)
    return violations
