"""Independent audit of a model-led consult — outside the decision loop.

In full autonomy the model decides the stage, the treatment intent, the
biomarker category and the plan, and no deterministic program is consulted
while it does. This module is the deterministic kernel reading the SAME
case independently, AFTER the consult is submitted, for the clinician (the
``/audit`` command) and for evaluation (``paper/analysis.py``). Nothing here
returns to the model, changes its consult or gates anything.

What is compared:

* stage — the model's stage group against the AJCC/UICC 9th-edition engine
  reading of the recorded TNM;
* metastatic sites — the M descriptor against the sites on record, and CNS
  metastases against M0 (via the rule engine);
* biomarker category — the model's NSCL codes against the marker-level
  classifier, including a PD-L1 category assigned while actionable markers
  are still untested;
* the plan — the 20 safety rules (driver-directed first line, indication
  predicates, organ gates, N3 surgery, dose scan …);
* emergencies — a screened emergency the conclusion does not put first.

The facts audited are the model's case notes, gaps filled from the
narrative by the deterministic extractor (never overwriting what the model
recorded), so a fact the model left out of its notes is still read.
"""

from __future__ import annotations

import copy
from typing import Any

CHECKS = ("stage", "metastasis", "biomarker_category", "rules", "emergency")


def audit_facts(notes: dict[str, Any], narrative: str = "") -> tuple[dict[str, Any], list[str]]:
    """The model's notes with gaps filled from the narrative."""
    from ..conversation import extract_facts_deterministic, merge_facts, sanitize_fact_payload

    facts = copy.deepcopy({k: v for k, v in (notes or {}).items() if not str(k).startswith("_")})
    if not narrative:
        return facts, []
    extracted, _ = sanitize_fact_payload(extract_facts_deterministic(narrative))
    filled, _conflicts = merge_facts(facts, extracted, overwrite=False)
    return facts, filled


def _category_finding(consult: dict[str, Any], facts: dict[str, Any], stage: str,
                      lang: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    from ..knowledge.biomarker_categories import PDL1_NEGATIVE, PDL1_POSITIVE, classify

    model = [str(c) for c in consult.get("biomarker_category") or []]
    if not (stage.startswith("IV") or model):
        return None, None
    reference = classify(facts)
    ref = {k: reference[k] for k in ("status", "codes", "missing", "summary_zh", "summary_en")}
    en = lang == "en"
    pd_l1 = {PDL1_POSITIVE.code, PDL1_NEGATIVE.code}
    untested = reference["status"] in ("incomplete", "pd_l1_pending")
    if model and untested and set(model) & pd_l1:
        message = (f"The model assigned {', '.join(model)}, a PD-L1 category, while the table "
                   f"still needs: {', '.join(reference['missing'])}" if en else
                   f"模型归入 {'、'.join(model)}（PD-L1 类别），但分类表仍需：" + "、".join(reference["missing"]))
        return {"rule_id": "CATEGORY_BEFORE_TESTING", "severity": "warn",
                "message": message}, ref
    if model and set(model) != set(reference["codes"]):
        reading = ", ".join(reference["codes"]) if reference["codes"] else (
            "no category yet" if en else "暂不能归类")
        message = (f"The model assigned {', '.join(model)}; the marker-level classifier reads "
                   f"the test results as {reading}" if en else
                   f"模型归入 {'、'.join(model)}；按检测结果逐项判读为 "
                   + ("、".join(reference["codes"]) or reading))
        return {"rule_id": "CATEGORY_DIFFERS_FROM_CLASSIFIER", "severity": "warn",
                "message": message}, ref
    if not model and reference["codes"] and stage.startswith("IV"):
        message = (f"No biomarker category given; the test results read as "
                   f"{', '.join(reference['codes'])}" if en else
                   "未给出生物标志物分类；按检测结果为 " + "、".join(reference["codes"]))
        return {"rule_id": "CATEGORY_NOT_GIVEN", "severity": "info", "message": message}, ref
    return None, ref


def audit(consult: dict[str, Any], notes: dict[str, Any], narrative: str = "",
          *, role: str = "oncologist", lang: str = "zh",
          emergency: dict[str, Any] | None = None) -> dict[str, Any]:
    """Findings of the independent audit plus the kernel's own reading.
    ``emergency`` overrides the narrative screen (evaluation harnesses)."""
    from ..knowledge.metastases import suggest_m
    from ..safety import emergencies
    from .hooks import _emergency_addressed
    from .toolbox import AgentToolbox

    consult = consult or {}
    facts, filled = audit_facts(notes, narrative)
    box = AgentToolbox(facts=facts, narrative=lambda: narrative)  # kernel-assisted reading
    engine = box.engine_stage() or {}
    findings: list[dict[str, Any]] = []

    stage_finding = box.stage_finding(consult, lang=lang)
    if stage_finding:
        findings.append({**stage_finding, "check": "stage"})
    stage = str(consult.get("stage_group") or engine.get("stage_group") or "").upper()

    category, category_ref = _category_finding(consult, facts, stage, lang)
    if category:
        findings.append({**category, "check": "biomarker_category"})

    for finding in box.rule_findings(consult):
        check = "metastasis" if finding.get("rule_id") in (
            "METASTASIS_TNM_INCONSISTENT", "CNS_TNM_INCONSISTENT") else "rules"
        findings.append({**finding, "check": check})

    if emergency is None:
        screen = emergencies.screen(narrative or "")
        if screen.hard_hits:
            emergency = {"signals": [h["label"] for h in screen.hard_hits]}
    if emergency:
        result = _emergency_addressed({"consult": consult, "state": {"emergency": emergency},
                                       "lang": lang})
        for finding in (result.findings if result else []):
            findings.append({**finding, "check": "emergency"})

    suggestion = suggest_m(facts)
    order = {"block": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: order.get(f.get("severity"), 3))
    return {
        "findings": findings,
        "checked": list(CHECKS),
        "reference": {
            "stage": {k: engine.get(k) for k in ("staged", "stage_group", "tnm", "refusal")
                      if engine.get(k) is not None} or None,
            "suggested_m": suggestion if suggestion["candidates"] else None,
            "biomarker_category": category_ref,
            "emergency": emergency,
        },
        "facts_filled_from_narrative": filled,
        "role": role,
    }


def render(result: dict[str, Any], lang: str = "zh") -> str:
    """Markdown for the clinician."""
    en = lang == "en"
    ref = result.get("reference") or {}
    lines = ["**Independent audit** (deterministic kernel, after the consult; not returned to "
             "the model)" if en else "**独立核对**（确定性内核在会诊结束后独立核对；不回传模型、不改动结论）"]
    stage = ref.get("stage") or {}
    if stage.get("staged"):
        lines.append((f"- Engine stage: {stage['stage_group']} ({stage.get('tnm', '')})" if en else
                      f"- 引擎分期：{stage['stage_group']}（{stage.get('tnm', '')}）"))
    elif stage.get("refusal"):
        lines.append(("- Engine stage: refused — " if en else "- 引擎分期：拒绝 —— ")
                     + str(stage["refusal"])[:160])
    m = ref.get("suggested_m") or {}
    if m:
        hint = m.get("m") or " / ".join(m.get("candidates") or [])
        lines.append(f"- Sites suggest {hint} ({m.get('basis')})" if en else
                     f"- 按转移部位提示 {hint}（{m.get('basis_zh') or m.get('basis')}）")
    category = ref.get("biomarker_category") or {}
    if category:
        lines.append(("- Biomarker category: " + category.get("summary_en", "")) if en else
                     ("- 生物标志物分类：" + category.get("summary_zh", "")))
    findings = result.get("findings") or []
    if findings:
        lines.append(f"\n{len(findings)} finding(s):" if en else f"\n{len(findings)} 条发现：")
        lines += [f"- [{f.get('severity')}] `{f.get('rule_id')}` {f.get('message')}" for f in findings]
    else:
        lines.append("\nNo findings." if en else "\n无发现。")
    return "\n".join(lines)
