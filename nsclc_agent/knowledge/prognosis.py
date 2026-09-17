"""Prognosis context: published cohort statistics + directional factors.

What "survival prediction" honestly means inside this harness:

* **Population statistics, never individual prediction.** The table encodes
  the stage-by-stage five-year overall survival of the IASLC staging-project
  cohorts (Goldstraw et al., J Thorac Oncol 2016 — the 8th-edition database,
  the last with fully published per-group figures; the 9th-edition project,
  Rami-Porta et al. 2024, regrouped some populations, see the caveats).
  Every figure is a cohort average, marked approximate, tied to its source.
  No arithmetic is ever run on these numbers to produce a per-patient
  estimate — that would be fabrication wearing a decimal point.
* **Modifiers are directional, not numeric.** Known prognostic factors
  found in the case facts (performance status, weight loss, driver status
  with modern targeted therapy, PD-L1 with immunotherapy, consolidation
  after CRT) are listed with a direction and a rationale; where a landmark
  trial quantifies the effect, the modifier carries the trial id so the
  runner can anchor it in the evidence ledger through ``trial_lookup`` —
  the numbers stay in the registry entry, cited, not recomputed.
* **Edition migration is disclosed.** This harness stages by AJCC/UICC 9;
  a case whose stage group migrated between editions is flagged: the
  8th-edition cohort under the same label is not the same population.

The patient view never receives these figures unprompted — prognosis
communication is a clinical conversation, not a table dump; the reply layer
offers a supportive pointer instead (see :mod:`nsclc_agent.conversation`).
"""

from __future__ import annotations

from typing import Any

#: Five-year overall survival by stage group, IASLC 8th-edition database
#: (Goldstraw et al., JTO 2016;11:39-51). Keyed by classification basis:
#: clinical (c) and pathological (p) cohorts differ systematically.
_SOURCE = ("IASLC staging project, 8th-edition database — Goldstraw et al., "
           "J Thorac Oncol 2016;11(1):39-51 (approximate published figures)")

_FIVE_YEAR_OS: dict[str, dict[str, int]] = {
    "c": {
        "IA1": 92, "IA2": 83, "IA3": 77, "IB": 68,
        "IIA": 60, "IIB": 53,
        "IIIA": 36, "IIIB": 26, "IIIC": 13,
        "IVA": 10, "IVB": 0,
    },
    "p": {
        "IA1": 90, "IA2": 85, "IA3": 80, "IB": 73,
        "IIA": 65, "IIB": 56,
        "IIIA": 41, "IIIB": 24, "IIIC": 12,
    },
}

_CAVEATS = (
    "人群队列统计，不是个体预测 / cohort averages, not an individual "
    "prediction — individual prognosis depends on factors no staging table "
    "carries",
    "队列为第8版分期项目（2016 发表，患者多为 1999-2010 治疗）；靶向与免疫"
    "治疗时代的同分期生存已系统性改善",
    "本系统按第9版分期；第9版重新分组（N2a/N2b、M1c 细分）导致同名分期组"
    "人群与第8版不完全可比（IASLC 第9版数据库：Rami-Porta et al., "
    "J Thorac Oncol 2024）",
)


def _positive(facts: dict[str, Any], gene: str) -> bool:
    from .biomarkers import driver_status

    value = (facts.get("driver_mutations") or {}).get(gene)
    return value is not None and driver_status(str(value)) == "positive"


def _modifiers(stage_group: str, facts: dict[str, Any]) -> list[dict[str, Any]]:
    """Directional prognostic factors present in THIS case's facts.

    Deterministic, additive-free: no composite score, no adjusted number.
    ``anchor_trials`` names registry entries whose published outcomes
    quantify the effect — the runner cites those, this module does not
    restate their numbers.
    """
    out: list[dict[str, Any]] = []
    metastatic = stage_group.startswith("IV")
    stage_iii = stage_group.startswith("III")

    ecog = facts.get("ecog_ps")
    if isinstance(ecog, int) and ecog >= 2:
        out.append({
            "factor": f"ECOG PS {ecog}",
            "direction": "adverse",
            "rationale": "体能状态是各分期最强的独立预后因素之一；PS≥2 人群"
                         "在多数注册试验中被排除，队列数字对其高估",
        })
    weight_loss = str(facts.get("weight_loss") or "")
    if weight_loss and not weight_loss.startswith(("no", "无", "none")):
        out.append({
            "factor": "明显体重下降",
            "direction": "adverse",
            "rationale": "非自主体重下降与较差总生存相关（经典预后因素）",
        })
    if metastatic and _positive(facts, "egfr"):
        out.append({
            "factor": "EGFR 敏感突变（IV期）",
            "direction": "favorable_with_treatment",
            "rationale": "奥希替尼一线治疗显著延长生存——数字见注册表条目",
            "anchor_trials": ["FLAURA"],
        })
    if metastatic and _positive(facts, "alk"):
        out.append({
            "factor": "ALK 融合（IV期）",
            "direction": "favorable_with_treatment",
            "rationale": "ALK-TKI 一线治疗下长期无进展生存——数字见注册表条目",
            "anchor_trials": ["CROWN"],
        })
    tps = (facts.get("pd_l1") or {}).get("tps")
    if (metastatic and isinstance(tps, (int, float)) and tps >= 50
            and not (_positive(facts, "egfr") or _positive(facts, "alk"))):
        out.append({
            "factor": f"PD-L1 TPS {tps:g}%（≥50%，无驱动）",
            "direction": "favorable_with_treatment",
            "rationale": "帕博利珠单抗单药一线生存获益——数字见注册表条目",
            "anchor_trials": ["KEYNOTE024"],
        })
    if stage_iii and str(facts.get("resectability_category") or "").upper() \
            == "UNRESECTABLE" and not (_positive(facts, "egfr")):
        out.append({
            "factor": "不可切除III期，CRT 后巩固免疫可及",
            "direction": "favorable_with_treatment",
            "rationale": "同步放化疗后度伐利尤单抗巩固改善总生存——"
                         "数字见注册表条目",
            "anchor_trials": ["PACIFIC"],
        })
    if stage_iii and str(facts.get("resectability_category") or "").upper() \
            == "UNRESECTABLE" and _positive(facts, "egfr"):
        out.append({
            "factor": "不可切除III期 EGFR 突变，CRT 后奥希替尼巩固可及",
            "direction": "favorable_with_treatment",
            "rationale": "LAURA：CRT 后奥希替尼巩固显著延长无进展生存"
                         "——数字见注册表条目",
            "anchor_trials": ["LAURA"],
        })
    return out


def prognosis_for(
    stage_group: str | None,
    prefix: str,
    facts: dict[str, Any],
    migration_notes: list[str] | None = None,
) -> dict[str, Any] | None:
    """Cohort prognosis context for a computed stage group, or None.

    Returns None when unstaged, or when the table has nothing honest to
    say for this group (occult / stage 0 have no published per-group OS in
    the staging-project papers — absence is stated, not padded).
    """
    if not stage_group:
        return None
    stage = str(stage_group).upper()
    basis = "p" if str(prefix or "c").startswith(("p", "yp")) else "c"
    table = _FIVE_YEAR_OS.get(basis) or {}
    figure = table.get(stage)
    fallback_basis = None
    if figure is None and basis == "p":
        # The pathological cohort has no stage-IV rows (resected series);
        # fall back to the clinical cohort, and say so.
        figure = _FIVE_YEAR_OS["c"].get(stage)
        fallback_basis = "c"
    result: dict[str, Any] = {
        "stage_group": stage,
        "classification_basis": "pathological" if basis == "p" else "clinical",
        "cohort": _SOURCE,
        "modifiers": _modifiers(stage, facts),
        "caveats": list(_CAVEATS),
        "individual_prediction": False,
    }
    if figure is not None:
        result["five_year_os_percent_approx"] = figure
        if fallback_basis:
            result["caveats"].insert(
                0, "该分期组无病理分期队列数字，引用的是临床分期队列")
    else:
        result["five_year_os_percent_approx"] = None
        result["caveats"].insert(
            0, f"分期组 {stage} 在分期项目发表数据中无单独生存数字——"
               f"不作填补")
    if migration_notes:
        result["edition_migration"] = list(migration_notes)
        result["caveats"].insert(
            0, "此病例的分期组在第8→9版之间发生迁移：同名分期组的历史队列"
               "人群与之不同，跨版本可比性受限，谨慎解读")
    return result
