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

import re
from typing import Any, Optional

from . import regimens as regimen_lib

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _num(value: Any) -> Optional[float]:
    """A number from a fact value: numeric as-is, the first number in a
    string ("CrCl 38 mL/min" → 38.0), else None. Booleans are not
    numbers here."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = _NUMBER_RE.search(value)
        if match:
            return float(match.group(0))
    return None

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


def _crcl(facts: dict[str, Any]) -> tuple[Optional[float], Optional[float], str]:
    """Creatinine clearance on record: (low, high, source). A measured or
    reported CrCl/eGFR is one number; Cockcroft–Gault from creatinine,
    age and weight is a range when sex is not recorded."""
    raw = _organ(facts, "renal")
    if isinstance(raw, dict):
        crcl = _num(raw.get("crcl_ml_min", raw.get("egfr_ml_min")))
        if crcl is not None:
            return crcl, crcl, f"CrCl {crcl:g} mL/min"
        from .labs import crcl_estimate

        estimate = crcl_estimate(facts)
        if estimate:
            return estimate["low"], estimate["high"], estimate["note"]
        raw = raw.get("verdict")
    if _num(raw) is not None:
        return _num(raw), _num(raw), f"CrCl {_num(raw):g} mL/min"
    return None, None, str(raw or "")


def _renal_threshold(facts: dict[str, Any], gate: str, threshold: float,
                     fail_note: str, missing_note: str,
                     below: str = "fail") -> dict[str, str]:
    low, high, source = _crcl(facts)
    if low is not None and high is not None:
        shown = f"{low:g}" if low == high else f"{low:g}–{high:g}"
        if low >= threshold:
            return _gate(gate, "pass", f"{source}: CrCl {shown} mL/min ≥ {threshold:g}")
        if high < threshold:
            return _gate(gate, below, f"{source}: CrCl {shown} mL/min < {threshold:g} — {fail_note}")
        return _gate(gate, "unverified", f"{source}: CrCl {shown} mL/min straddles {threshold:g} "
                                         f"— record sex or a measured CrCl")
    verdict = _string_verdict(gate, source) if source else None
    return verdict or _gate(gate, "unverified", missing_note)


def _eval_renal(facts: dict[str, Any]) -> dict[str, str]:
    return _renal_threshold(
        facts, "renal_function", 45,
        "below the pemetrexed label threshold; the backbone must change, not the dose",
        "no renal function on record — creatinine clearance needed before a "
        "pemetrexed/platinum backbone is dosed")


def _eval_renal_cisplatin(facts: dict[str, Any]) -> dict[str, str]:
    # Cisplatin-ineligible is a substitution (carboplatin), not a reason to
    # drop the pathway: the dose channel refuses cisplatin, the plan stays.
    return _renal_threshold(
        facts, "renal_cisplatin", 60,
        "cisplatin-ineligible; substitute carboplatin (MDT/pharmacist), do not dose cisplatin",
        "no renal function on record — CrCl ≥ 60 mL/min needed before cisplatin",
        below="unverified")


def _eval_hematologic(facts: dict[str, Any]) -> dict[str, str]:
    raw = _organ(facts, "hematologic")
    if not isinstance(raw, dict):
        verdict = _string_verdict("hematologic_baseline", raw)
        return verdict or _gate("hematologic_baseline", "unverified",
                                "no blood count on record — ANC ≥ 1.5 and platelets "
                                "≥ 100 ×10⁹/L needed before cytotoxic or ADC therapy")
    anc, plt, hb = _num(raw.get("anc")), _num(raw.get("plt")), _num(raw.get("hb"))
    low = []
    if anc is not None and anc < 1.5:
        low.append(f"ANC {anc:g} < 1.5 ×10⁹/L")
    if plt is not None and plt < 100:
        low.append(f"platelets {plt:g} < 100 ×10⁹/L")
    if hb is not None and hb < 80:
        low.append(f"Hb {hb:g} < 80 g/L")
    if low:
        # Cytopenia delays a cycle and asks why; it does not change the
        # regimen. The dose channel refuses until counts recover.
        return _gate("hematologic_baseline", "unverified",
                     "; ".join(low) + " — hold the cycle, find the cause (marrow "
                     "infiltration, prior therapy, bleeding), recheck before dosing")
    if anc is not None and plt is not None:
        return _gate("hematologic_baseline", "pass",
                     f"ANC {anc:g}, platelets {plt:g} ×10⁹/L"
                     + (f", Hb {hb:g} g/L" if hb is not None else ""))
    return _gate("hematologic_baseline", "unverified",
                 "blood count incomplete — ANC and platelets both needed before dosing")


def _eval_lvef(facts: dict[str, Any]) -> dict[str, str]:
    raw = _organ(facts, "cardiac")
    lvef = _num(raw.get("lvef_pct")) if isinstance(raw, dict) else _num(raw)
    nyha = _num(raw.get("nyha")) if isinstance(raw, dict) else None
    if lvef is not None:
        if lvef < 50:
            return _gate("lvef_baseline", "fail",
                         f"LVEF {lvef:g}% < 50% — outside the trial populations of this "
                         f"cardiotoxic component (trastuzumab deruxtecan / trametinib)")
        return _gate("lvef_baseline", "pass", f"LVEF {lvef:g}% ≥ 50%")
    if nyha is not None and nyha >= 3:
        return _gate("lvef_baseline", "fail",
                     f"NYHA class {nyha:g} heart failure — cardiology review and an echo "
                     f"before a cardiotoxic component")
    if (facts.get("comorbidities") or {}).get("heart_failure"):
        return _gate("lvef_baseline", "unverified",
                     "heart failure on record without an LVEF — echocardiogram/MUGA first")
    return _gate("lvef_baseline", "unverified",
                 "no baseline LVEF on record — echocardiogram/MUGA before "
                 "trastuzumab deruxtecan or trametinib")


def _ratio(raw: dict[str, Any], key: str) -> tuple[Optional[float], bool]:
    """A ×ULN value: recorded as such, or from the absolute value over the
    default ULN (``assumed`` True — the conclusion must say so)."""
    from .labs import DEFAULT_ULN

    direct = _num(raw.get(f"{key}_uln"))
    if direct is not None:
        return direct, False
    absolute_key = "bilirubin_umol_l" if key == "bilirubin" else f"{key}_u_l"
    absolute = _num(raw.get(absolute_key))
    if absolute is not None:
        return round(absolute / DEFAULT_ULN[absolute_key], 2), True
    return None, False


def _eval_hepatic(facts: dict[str, Any]) -> dict[str, str]:
    raw = _organ(facts, "hepatic")
    if isinstance(raw, dict):
        child = str(raw.get("child_pugh") or "").strip().upper()
        if child in ("B", "C"):
            return _gate("hepatic_baseline", "fail",
                         f"Child-Pugh {child} — outside the taxane/TKI "
                         f"evidence populations")
        verdicts: list[dict[str, str]] = []
        bili, assumed = _ratio(raw, "bilirubin")
        if bili is not None:
            tag = " (assumed ULN 21 µmol/L — confirm the lab's range)" if assumed else ""
            if bili <= 1.0:
                verdicts.append(_gate("hepatic_baseline", "pass",
                                      f"bilirubin {bili:g}×ULN within normal{tag}"))
            elif assumed and bili <= 1.5:
                verdicts.append(_gate("hepatic_baseline", "unverified",
                                      f"bilirubin ≈{bili:g}×ULN{tag} — above 1×ULN docetaxel "
                                      f"is contraindicated; confirm against the lab's ULN"))
            else:
                verdicts.append(_gate("hepatic_baseline", "fail",
                                      f"bilirubin {bili:g}×ULN{tag} — docetaxel is generally "
                                      f"contraindicated above 1×ULN (label)"))
        transaminase = [(name, *_ratio(raw, key)) for name, key in (("ALT", "alt"), ("AST", "ast"))]
        transaminase = [(n, v, a) for n, v, a in transaminase if v is not None]
        if transaminase:
            name, worst, assumed = max(transaminase, key=lambda item: item[1])
            tag = " (assumed ULN 40 U/L)" if assumed else ""
            if worst > 5:
                verdicts.append(_gate("hepatic_baseline", "fail",
                                      f"{name} {worst:g}×ULN{tag} > 5×ULN — grade ≥3 hepatotoxicity "
                                      f"range; outside every label population"))
            elif worst > 1.5:
                verdicts.append(_gate("hepatic_baseline", "unverified",
                                      f"{name} {worst:g}×ULN{tag} — docetaxel label: do not dose with "
                                      f"AST/ALT > 1.5×ULN plus ALP > 2.5×ULN; check ALP and the "
                                      f"liver-metastasis context"))
            else:
                verdicts.append(_gate("hepatic_baseline", "pass",
                                      f"{name} {worst:g}×ULN{tag} ≤ 1.5×ULN"))
        for status in ("fail", "unverified", "pass"):
            hit = [v for v in verdicts if v["status"] == status]
            if hit:
                return _gate("hepatic_baseline", status, "; ".join(v["note"] for v in hit))
        if child == "A":
            return _gate("hepatic_baseline", "pass", "Child-Pugh A")
        raw = raw.get("verdict")
    verdict = _string_verdict("hepatic_baseline", raw)
    return verdict or _gate("hepatic_baseline", "unverified",
                            "no hepatic function on record — bilirubin and "
                            "transaminases needed before a taxane is dosed")


def _eval_qtc(facts: dict[str, Any]) -> dict[str, str]:
    raw = _organ(facts, "qtc")
    qtc = _num(raw.get("qtc_ms") if isinstance(raw, dict) else raw)
    if qtc is None:
        qtc = _num(facts.get("qtc_ms"))
    if qtc is not None:
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
    organ = facts.get("organ_function") or {}
    raw = organ.get("pulmonary")
    values = {}
    if isinstance(raw, dict):
        values.update({k: _num(raw.get(k)) for k in ("fev1_pct", "dlco_pct")})
    values.update({k: _num(organ.get(k)) for k in ("ppo_fev1_pct", "ppo_dlco_pct")})
    values = {k: v for k, v in values.items() if v is not None}
    if values:
        shown = ", ".join(f"{k.replace('_pct', '').upper().replace('PPO_', 'ppo')} {v:g}%"
                          for k, v in values.items())
        worst = min(values.values())
        if worst < 40:
            return _gate("pulmonary_reserve", "unverified",
                         f"{shown} — below 40% predicted: high risk of radiation "
                         f"pneumonitis / postoperative failure; exercise testing and a "
                         f"pulmonology / MDT decision before this treatment")
        if worst < 60:
            return _gate("pulmonary_reserve", "pass",
                         f"{shown} — reduced but ≥ 40% predicted; plan with the reserve in mind")
        return _gate("pulmonary_reserve", "pass", f"{shown} ≥ 60% predicted")
    verdict = _string_verdict("pulmonary_reserve", raw)
    return verdict or _gate("pulmonary_reserve", "unverified",
                            "pulmonary function tests not on record")


def _eval_hearing(facts: dict[str, Any]) -> dict[str, str]:
    verdict = _string_verdict("hearing_neuropathy_baseline",
                              _organ(facts, "hearing_neuropathy"))
    if verdict:
        return verdict
    comorbid = facts.get("comorbidities") or {}
    hearing = comorbid.get("hearing_loss")
    neuropathy = comorbid.get("peripheral_neuropathy")
    grade = neuropathy if isinstance(neuropathy, int) and not isinstance(neuropathy, bool) else None
    found = []
    if hearing is True:
        found.append("hearing loss")
    if neuropathy is True:
        found.append("peripheral neuropathy")
    elif grade is not None and grade >= 2:
        found.append(f"grade {grade} peripheral neuropathy")
    if found:
        # Like CrCl < 60: a carboplatin substitution, never a dropped pathway.
        return _gate("hearing_neuropathy_baseline", "unverified",
                     f"{' and '.join(found)} on record — cisplatin-ineligible; substitute "
                     f"carboplatin (MDT/pharmacist), do not dose cisplatin")
    if hearing is False and (neuropathy is False or grade is not None):
        return _gate("hearing_neuropathy_baseline", "pass",
                     "no hearing loss or ≥ grade 2 neuropathy on record")
    return _gate("hearing_neuropathy_baseline", "unverified",
                 "baseline audiometry/neuropathy assessment not on record (cisplatin)")


_EVALUATORS = {
    "renal_function": _eval_renal,
    "renal_cisplatin": _eval_renal_cisplatin,
    "hematologic_baseline": _eval_hematologic,
    "lvef_baseline": _eval_lvef,
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

#: Component drug → the organ gates its label implies. Derived per
#: regimen so a hand-maintained gate list can never silently omit one
#: (the v0.7.1 audit found 11 regimens missing component-implied gates,
#: docetaxel's hepatic warning among them). Conditional components
#: ("cisplatin or carboplatin") imply nothing — the alternative exists.
_COMPONENT_GATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pemetrexed", ("renal_function", "b12_folate_started")),
    ("cisplatin", ("renal_function", "renal_cisplatin", "hearing_neuropathy_baseline")),
    ("docetaxel", ("hepatic_baseline",)),
    ("ramucirumab", ("bleeding_risk",)),
    ("osimertinib", ("qtc_baseline",)),
    ("lorlatinib", ("no_strong_cyp3a4_inducer",)),
    ("deruxtecan", ("ild_history",)),
    ("trastuzumab", ("lvef_baseline",)),
    ("trametinib", ("lvef_baseline",)),
)

#: Gates every alternative of a conditional component shares ("cisplatin or
#: carboplatin" is myelosuppressive either way): blood counts before any
#: cytotoxic or antibody–drug-conjugate component.
_CLASS_GATES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("pemetrexed", "carboplatin", "cisplatin", "platin", "docetaxel", "paclitaxel",
      "etoposide", "vinorelbine", "gemcitabine", "chemotherapy", "deruxtecan"),
     ("hematologic_baseline",)),
)


def regimen_gates(regimen: Any) -> tuple[str, ...]:
    """The regimen's declared gates plus every component-implied organ
    gate, declared order first."""
    gates = list(regimen.dose_gates)
    for component in regimen.components:
        drug = component.drug.lower()
        for keywords, implied in _CLASS_GATES:
            if any(k in drug for k in keywords):
                gates.extend(g for g in implied if g not in gates)
        if " or " in drug:
            continue
        for keyword, implied in _COMPONENT_GATES:
            if keyword in drug:
                gates.extend(g for g in implied if g not in gates)
    return tuple(gates)


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
    for gate in regimen_gates(regimen):
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
    for gate in regimen_gates(regimen):
        result = evaluate_gate(gate, facts)
        if result and result["status"] == "unverified":
            out.append(f"{result['note']} [{regimen_id}]")
    return out
