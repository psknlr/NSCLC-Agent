"""CNS metastasis stratification — deterministic, teaching-scale, honest.

Brain metastases change stage-IV strategy fundamentally, and the coarse
"M1 is M1" model missed all of it (red-team corpus gap). This module
gives the planner and the critic ONE shared, deterministic reading of
the CNS facts:

* **Structured facts first.** ``facts["cns_metastases"]`` is a dict
  (``status`` / ``symptomatic`` / ``treated`` / ``burden`` /
  ``leptomeningeal``); a bare string is tolerated for presence/absence.
  Anything unstated is ``None`` — unknown, never assumed. A negative
  brain-imaging statement in the narrative may seed ``status: absent``
  (the same setdefault discipline as the emergency screen's negations);
  nothing else is ever inferred from prose.
* **Strategy is stratified, not prescribed.** Symptomatic or untreated ×
  driver status × burden select which OPTIONS and cautions the plan must
  carry (CNS-directed local therapy is a protocol-grounded option — SRS
  vs whole-brain framing is named, fractionation and dosing are the
  radiation oncologist's channel, never emitted here). Leptomeningeal
  disease is its own branch and an honest boundary: named, routed to
  specialist care, never padded with figures this corpus does not hold.
* **Consistency is checked, not repaired.** CNS metastases with an M0
  TNM is a contradiction between the descriptors and the facts — the
  reading names it and the critic flags it; nobody silently "fixes" the
  stage (the staging engine remains the only staging authority).

The acute intracranial presentation (seizure, herniation signs) is the
emergency screen's short-circuit and never reaches this layer.
"""

from __future__ import annotations

import re
from typing import Any, Optional

_PRESENT = {"present", "yes", "true", "positive", "有", "存在"}
_ABSENT = {"absent", "no", "false", "negative", "none", "无", "阴性"}
_LIMITED = {"limited", "oligo", "few", "1-3", "局限"}
_EXTENSIVE = {"extensive", "multiple", "many", "diffuse", "多发", "广泛"}


def _tri(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in _PRESENT or text in {"1"}:
        return True
    if text in _ABSENT or text in {"0"}:
        return False
    return None


def cns_status(facts: dict[str, Any]) -> dict[str, Any]:
    """Normalized CNS reading. Every unstated field is None (unknown)."""
    raw = facts.get("cns_metastases")
    if raw is None:
        return {"status": "unknown", "symptomatic": None, "treated": None,
                "burden": None, "leptomeningeal": None, "source": None}
    if not isinstance(raw, dict):
        present = _tri(raw)
        status = ("present" if present else
                  "absent" if present is False else "unknown")
        return {"status": status, "symptomatic": None, "treated": None,
                "burden": None, "leptomeningeal": None, "source": "fact"}
    status_raw = str(raw.get("status") or "").strip().lower()
    status = ("present" if status_raw in _PRESENT else
              "absent" if status_raw in _ABSENT else "unknown")
    burden_raw = str(raw.get("burden") or "").strip().lower()
    burden = ("limited" if burden_raw in _LIMITED else
              "extensive" if burden_raw in _EXTENSIVE else None)
    return {
        "status": status,
        "symptomatic": _tri(raw.get("symptomatic")),
        "treated": _tri(raw.get("treated")),
        "burden": burden,
        "leptomeningeal": _tri(raw.get("leptomeningeal")),
        "source": str(raw.get("source") or "fact"),
    }


def tnm_conflict(facts: dict[str, Any]) -> Optional[str]:
    """CNS metastases present while the TNM descriptors say M0: a
    contradiction to NAME, never to repair — the staging engine is the
    only staging authority and it staged what it was given."""
    reading = cns_status(facts)
    if reading["status"] != "present":
        return None
    m = str((facts.get("tnm") or {}).get("m") or "").upper().replace(" ", "")
    # Descriptor prefixes (c/p/yp/r) do not change the category.
    m = re.sub(r"^(?:YP|YC|RP|RC|C|P|R|Y)(?=M)", "", m)
    if m == "M0":
        return ("CNS metastases are on record but the TNM descriptors say "
                "M0 — brain metastases are distant disease (at least M1b). "
                "Reconcile the imaging and the descriptors before treating "
                "either as true.")
    return None


#: Drivers whose first-line agent has meaningful CNS activity in the
#: registry's own evidence (FLAURA CNS subset; CROWN/ALEX intracranial
#: efficacy). Membership is deliberately conservative and gene-level —
#: the variant class was already enforced upstream.
CNS_ACTIVE_FIRST_LINE = {"EGFR", "ALK", "RET", "NTRK"}


def cns_strategy(stage_group: str, facts: dict[str, Any],
                 lead_gene: str | None) -> Optional[dict[str, Any]]:
    """Stratified CNS context for a stage-IV plan, or None when it adds
    nothing (non-IV stage, or CNS confirmed absent).

    Returns ``{reading, options, cautions, workup, honest_notes}`` where
    options are regimen-free (protocol-grounded local-therapy and
    consult framings), cautions join the plan's uncertainties and
    honest_notes name what this corpus does NOT cover. Dose-free by
    construction."""
    if stage_group not in ("IVA", "IVB"):
        return None
    reading = cns_status(facts)
    if reading["status"] == "absent":
        return None

    options: list[dict[str, Any]] = []
    cautions: list[str] = []
    workup: list[str] = []
    honest: list[str] = []

    if reading["status"] == "unknown":
        workup.append(
            "Brain MRI with contrast (CNS staging) — stage-IV baseline; "
            "CNS status changes both the systemic choice and the need "
            "for CNS-directed local therapy")
        cautions.append(
            "CNS status is not on record: systemic options are stated "
            "for the extracranial disease; an undetected brain lesion "
            "would restratify this plan.")
        return {"reading": reading, "options": options, "cautions": cautions,
                "workup": workup, "honest_notes": honest}

    # status == "present" -------------------------------------------------
    if reading["leptomeningeal"]:
        options.append({
            "name": "Leptomeningeal disease: dedicated neuro-oncology "
                    "referral", "regimen_ids": [], "cns_directed": True,
            "rationale": "LM is its own disease entity (CSF cytology / "
                         "contrast MRI axis confirmation, intrathecal and "
                         "escalated-TKI questions) — outside this system's "
                         "corpus; specialist pathway required"})
        honest.append(
            "Leptomeningeal disease strategy (intrathecal therapy, "
            "escalated-TKI dosing questions) is NOT encoded here — the "
            "recommendation is the referral itself.")
    if reading["symptomatic"] and not reading["treated"]:
        options.insert(0, {
            "name": "CNS-directed local therapy first "
                    "(neurosurgery / radiation oncology)",
            "regimen_ids": [], "cns_directed": True,
            "rationale": "Symptomatic untreated brain metastases: local "
                         "control and symptom relief (surgical resection "
                         "or radiosurgery per burden and location; "
                         "corticosteroid management per neuro team) "
                         "precede or accompany systemic therapy — "
                         "systemic-only planning is unsafe here"})
        cautions.append(
            "Symptomatic untreated CNS disease: sequencing (local first "
            "vs concurrent) is an MDT decision; do not start "
            "systemic-only therapy on this record.")
    elif reading["status"] == "present" and not reading["treated"]:
        # Asymptomatic, untreated.
        if lead_gene in CNS_ACTIVE_FIRST_LINE:
            cautions.append(
                f"Asymptomatic untreated brain metastases with a "
                f"CNS-active first-line agent ({lead_gene}-directed): "
                f"upfront systemic therapy with deferred local therapy "
                f"is a recognized strategy — requires agreed MRI "
                f"surveillance and neuro/radiation-oncology review.")
        else:
            options.insert(0, {
                "name": "CNS-directed local therapy (SRS for limited "
                        "burden; whole-brain approach for extensive "
                        "burden — radiation oncology)",
                "regimen_ids": [], "cns_directed": True,
                "rationale": "Driver-negative systemic options have "
                             "limited intracranial coverage; local "
                             "therapy timing is decided with radiation "
                             "oncology"})
            cautions.append(
                "Brain metastases with a driver-negative systemic plan: "
                "intracranial control rests on local therapy — confirm "
                "its place in the sequence before releasing the "
                "systemic start date.")
    if reading["burden"] is None and not reading["leptomeningeal"]:
        workup.append(
            "CNS lesion count/size/location (from the brain MRI report) "
            "— burden decides SRS vs whole-brain framing")
    honest.append(
        "CNS stratification here is teaching-scale: SRS/WBRT selection, "
        "fractionation, steroid dosing and surgical candidacy are the "
        "neuro-oncology MDT's channel, not this system's.")
    return {"reading": reading, "options": options, "cautions": cautions,
            "workup": workup, "honest_notes": honest}
