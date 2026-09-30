"""Organ-function and comorbidity gates — one evaluator, three consumers.

The last deterministic corpus gap (red-team): a plan can be perfectly
staged, driver-matched and evidence-entailed and still be wrong for THIS
body — CrCl 38 does not take a pemetrexed backbone, a bilirubin above
normal does not take docetaxel, a hemoptysis history does not take
ramucirumab. This module gives those judgments ONE deterministic home,
consumed three ways:

* the **dose channel** (``dose_gate_check``) — quantitative evaluation
  replaces the coarse "normal/adequate" strings;
* the **critic** (``ORGAN_FUNCTION_GATE``) — a regimen whose organ gate
  FAILS on the recorded facts blocks at recommendation time, whoever
  authored the plan;
* the **planner** — organ gates with nothing on record become named
  workup items on the final plan ("what number do I need, and why").

Discipline: quantitative facts first (``organ_function.renal.crcl_ml_min``),
qualitative strings tolerated, absence is UNKNOWN — unknown never blocks
a recommendation (recommending is not dosing; the dose channel holds
that line by refusing unverified gates), and unknown never passes
either. Thresholds are label-derived teaching values, cited in the note
they emit; the pharmacist's review is not replaced, it is fed.
"""

from __future__ import annotations

from typing import Any, Optional

from . import regimens as regimen_lib

#: Strong CYP3A4 inducers (same keyword family the DDI pack uses).
_CYP3A4_INDUCERS = ("rifampin", "rifampicin", "carbamazepine", "phenytoin",
                    "st john", "enzalutamide", "利福平", "卡马西平", "苯妥英")

_PASS_STRINGS = {"normal", "adequate", "ok", "正常", "充分"}
_FAIL_STRINGS = {"impaired", "inadequate", "abnormal", "受损", "不全"}


def _gate(gate: str, status: str, note: str) -> dict[str, str]:
    return {"gate": gate, "status": status, "note": note}


def _organ(facts: dict[str, Any], key: str) -> Any:
    return (facts.get("organ_function") or {}).get(key)


def _string_verdict(gate: str, value: Any) -> Optional[dict[str, str]]:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in _PASS_STRINGS:
        return _gate(gate, "pass", f"{gate}: recorded as {text}")
    if text in _FAIL_STRINGS:
        return _gate(gate, "fail", f"{gate}: recorded as {text}")
    return _gate(gate, "unverified", f"{gate}: {text!r} — not a recognized "
                                     f"verdict, treat as unresolved")


def _eval_renal(facts: dict[str, Any]) -> dict[str, str]:
    raw = _organ(facts, "renal")
    if isinstance(raw, dict):
        crcl = raw.get("crcl_ml_min", raw.get("egfr_ml_min"))
        if isinstance(crcl, (int, float)):
            if crcl >= 45:
                return _gate("renal_function", "pass",
                             f"CrCl {crcl:g} mL/min ≥ 45 (pemetrexed label "
                             f"threshold)")
            return _gate("renal_function", "fail",
                         f"CrCl {crcl:g} mL/min < 45 — below the pemetrexed "
                         f"label threshold; the backbone must change, not "
                         f"the dose")
        raw = raw.get("verdict")
    if isinstance(raw, (int, float)):
        return _eval_renal({"organ_function": {"renal":
                                               {"crcl_ml_min": raw}}})
    verdict = _string_verdict("renal_function", raw)
    return verdict or _gate("renal_function", "unverified",
                            "no renal function on record — creatinine "
                            "clearance needed before a pemetrexed/platinum "
                            "backbone is dosed")


def _eval_hepatic(facts: dict[str, Any]) -> dict[str, str]:
    raw = _organ(facts, "hepatic")
    if isinstance(raw, dict):
        child = str(raw.get("child_pugh") or "").strip().upper()
        if child in ("B", "C"):
            return _gate("hepatic_baseline", "fail",
                         f"Child-Pugh {child} — outside the taxane/TKI "
                         f"evidence populations")
        bili = raw.get("bilirubin_uln")
        if isinstance(bili, (int, float)):
            if bili <= 1.0:
                return _gate("hepatic_baseline", "pass",
                             f"bilirubin {bili:g}×ULN within normal")
            return _gate("hepatic_baseline", "fail",
                         f"bilirubin {bili:g}×ULN — docetaxel is generally "
                         f"contraindicated above 1×ULN (label)")
        if child == "A":
            return _gate("hepatic_baseline", "pass", "Child-Pugh A")
        raw = raw.get("verdict")
    verdict = _string_verdict("hepatic_baseline", raw)
    return verdict or _gate("hepatic_baseline", "unverified",
                            "no hepatic function on record — bilirubin and "
                            "transaminases needed before a taxane is dosed")


def _eval_qtc(facts: dict[str, Any]) -> dict[str, str]:
    raw = _organ(facts, "qtc")
    qtc = raw.get("qtc_ms") if isinstance(raw, dict) else raw
    if qtc is None:
        qtc = facts.get("qtc_ms")
    if isinstance(qtc, (int, float)):
        if qtc >= 500:
            return _gate("qtc_baseline", "fail",
                         f"QTc {qtc:g} ms ≥ 500 — the label's interruption "
                         f"threshold is already exceeded at baseline")
        if qtc > 480:
            return _gate("qtc_baseline", "unverified",
                         f"QTc {qtc:g} ms — correct electrolytes, repeat "
                         f"ECG, cardiology input before a QT-prolonging TKI")
        return _gate("qtc_baseline", "pass", f"QTc {qtc:g} ms ≤ 480")
    verdict = _string_verdict("qtc_baseline", raw)
    return verdict or _gate("qtc_baseline", "unverified",
                            "no baseline ECG on record — QTc needed before "
                            "a QT-prolonging TKI is dosed")


def _eval_ild(facts: dict[str, Any]) -> dict[str, str]:
    comorbid = facts.get("comorbidities") or {}
    if "ild" in comorbid:
        if comorbid.get("ild"):
            return _gate("ild_history", "fail",
                         "interstitial lung disease on record — ILD is the "
                         "key toxicity of this regimen class (ADC/ICI); "
                         "these patients were excluded from the trials")
        return _gate("ild_history", "pass", "ILD screen negative on record")
    return _gate("ild_history", "unverified",
                 "ILD history not on record — screen before an ILD-prone "
                 "regimen (ADC/ICI)")


def _eval_bleeding(facts: dict[str, Any]) -> dict[str, str]:
    risk = facts.get("bleeding_risk")
    if isinstance(risk, dict):
        hits = [k for k in ("hemoptysis_history", "cavitating_central_lesion",
                            "major_vessel_invasion", "recent_gi_bleed")
                if risk.get(k)]
        if hits:
            return _gate("bleeding_risk", "fail",
                         f"bleeding risk on record ({', '.join(hits)}) — "
                         f"ramucirumab is the wrong component; the "
                         f"docetaxel-monotherapy alternative exists for "
                         f"exactly this record")
        if any(k in risk for k in ("hemoptysis_history",
                                   "cavitating_central_lesion")):
            return _gate("bleeding_risk", "pass",
                         "bleeding-risk screen negative on record")
    if str(facts.get("emergency_airway_screen") or "").startswith("positive"):
        return _gate("bleeding_risk", "fail",
                     "airway emergency signal on record (hemoptysis "
                     "channel) — anti-angiogenic components are "
                     "contraindicated until resolved")
    return _gate("bleeding_risk", "unverified",
                 "bleeding-risk screen not on record (hemoptysis history, "
                 "cavitation, vessel invasion) — required before "
                 "ramucirumab")


def _eval_cyp3a4(facts: dict[str, Any]) -> dict[str, str]:
    meds = facts.get("medications")
    if meds is None:
        return _gate("no_strong_cyp3a4_inducer", "unverified",
                     "medication list not on record — strong CYP3A4 "
                     "inducers gut TKI exposure")
    med_text = " ".join(str(m).lower() for m in meds) \
        if isinstance(meds, (list, tuple)) else str(meds).lower()
    hits = sorted({k for k in _CYP3A4_INDUCERS if k in med_text})
    if hits:
        return _gate("no_strong_cyp3a4_inducer", "fail",
                     f"strong CYP3A4 inducer on the medication list "
                     f"({', '.join(hits)}) — switch or wash out before "
                     f"the TKI, never dose through it")
    return _gate("no_strong_cyp3a4_inducer", "pass",
                 "no strong CYP3A4 inducer on the medication list")


def _eval_b12_folate(facts: dict[str, Any]) -> dict[str, str]:
    started = facts.get("b12_folate_started")
    if started is True:
        return _gate("b12_folate_started", "pass",
                     "B12/folate supplementation started")
    if started is False:
        return _gate("b12_folate_started", "fail",
                     "B12/folate not started — required before pemetrexed")
    return _gate("b12_folate_started", "unverified",
                 "B12/folate supplementation status not on record")


def _eval_pulmonary(facts: dict[str, Any]) -> dict[str, str]:
    verdict = _string_verdict("pulmonary_reserve",
                              _organ(facts, "pulmonary"))
    return verdict or _gate("pulmonary_reserve", "unverified",
                            "pulmonary function tests not on record")


def _eval_hearing(facts: dict[str, Any]) -> dict[str, str]:
    verdict = _string_verdict("hearing_neuropathy_baseline",
                              _organ(facts, "hearing_neuropathy"))
    return verdict or _gate("hearing_neuropathy_baseline", "unverified",
                            "baseline audiometry/neuropathy assessment "
                            "not on record (cisplatin)")


_EVALUATORS = {
    "renal_function": _eval_renal,
    "hepatic_baseline": _eval_hepatic,
    "qtc_baseline": _eval_qtc,
    "ild_history": _eval_ild,
    "bleeding_risk": _eval_bleeding,
    "no_strong_cyp3a4_inducer": _eval_cyp3a4,
    "b12_folate_started": _eval_b12_folate,
    "pulmonary_reserve": _eval_pulmonary,
    "hearing_neuropathy_baseline": _eval_hearing,
}

#: The gates this module owns.
ORGAN_GATES = frozenset(_EVALUATORS)


def evaluate_gate(gate: str,
                  facts: dict[str, Any]) -> Optional[dict[str, str]]:
    """Evaluate one organ/comorbidity gate, or None when the gate id is
    not this module's (biomarker and protocol gates live elsewhere)."""
    evaluator = _EVALUATORS.get(gate)
    return evaluator(facts or {}) if evaluator else None


def failed_gates(regimen_id: str,
                 facts: dict[str, Any]) -> list[dict[str, str]]:
    """Organ gates of this regimen that FAIL on the recorded facts."""
    regimen = regimen_lib.get(regimen_id)
    if regimen is None:
        return []
    out = []
    for gate in regimen.dose_gates:
        result = evaluate_gate(gate, facts)
        if result and result["status"] == "fail":
            out.append(result)
    return out


def unknown_gates(regimen_id: str, facts: dict[str, Any]) -> list[str]:
    """Workup items for this regimen's organ gates with nothing on
    record — "which number is missing, and why it matters"."""
    regimen = regimen_lib.get(regimen_id)
    if regimen is None:
        return []
    out = []
    for gate in regimen.dose_gates:
        result = evaluate_gate(gate, facts)
        if result and result["status"] == "unverified":
            out.append(f"{result['note']} [{regimen_id}]")
    return out
