"""Later-line sequencing — deterministic, teaching-scale, honest.

The first-line decision table stops being the question the moment the
disease progresses on therapy; until v0.5.0 the system simply had no
later-line view (red-team corpus gap). This module gives the planner
and the critic ONE shared reading of the treatment history and a
stratified next-line context:

* **History is structured and honest.** ``facts["treatment_history"]``
  is a list of ``{line, agents/regimens, status}`` entries (bare strings
  tolerated). Sequencing triggers only on an EXPLICIT ``progression``
  status — prior exposure without a stated outcome is prior exposure,
  not progression, and the module says what it is missing.
* **Resistance workup before resistance guessing.** EGFR progression
  without a progression re-biopsy/plasma NGS on record routes to
  workup: MET amplification, C797S and histologic transformation each
  change the answer, so the options stay provisional until the
  mechanism question was at least asked.
* **The corpus is named, and small.** Post-osimertinib (MARIPOSA-2,
  chemo per the KEYNOTE-789 lesson), post-second-generation-ALK
  (lorlatinib), post-lorlatinib (chemo — no established next TKI),
  post-chemo-IO (docetaxel ± ramucirumab per REVEL; sotorasib for
  KRAS G12C on record; T-DXd for HER2). Everything else routes to the
  molecular tumor board rather than to a guess.
"""

from __future__ import annotations

from typing import Any, Optional

from .biomarkers import driver_status, egfr_classical, _normalized_drivers

_PROGRESSION = {"progression", "progressed", "pd", "进展", "耐药"}

#: Agent keywords (lowercase substring match against recorded agents).
_THIRD_GEN_EGFR = ("osimertinib", "奥希替尼", "lazertinib", "amivantamab")
_SECOND_GEN_ALK = ("alectinib", "brigatinib", "阿来替尼", "布格替尼",
                   "ceritinib")
_LORLATINIB = ("lorlatinib", "洛拉替尼")
_IO_AGENTS = ("pembrolizumab", "atezolizumab", "nivolumab", "cemiplimab",
              "durvalumab", "帕博利珠", "阿替利珠", "纳武利尤",
              "immunotherapy", "chemo-io", "chemoimmunotherapy")
_CHEMO_AGENTS = ("pemetrexed", "carboplatin", "cisplatin", "paclitaxel",
                 "nab-paclitaxel", "培美曲塞", "卡铂", "顺铂", "紫杉醇",
                 "platinum", "chemotherapy", "化疗")


def treatment_history(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalized history entries: {line, agents (lowercase), status}."""
    out: list[dict[str, Any]] = []
    for raw in facts.get("treatment_history") or []:
        if isinstance(raw, str):
            text = raw.strip().lower()
            status = "progression" if any(p in text for p in _PROGRESSION) \
                else None
            out.append({"line": None, "agents": [text], "status": status})
            continue
        if not isinstance(raw, dict):
            continue
        agents = raw.get("agents") or raw.get("regimens") \
            or raw.get("regimen") or raw.get("drugs") or []
        if isinstance(agents, str):
            agents = [agents]
        status_raw = str(raw.get("status") or "").strip().lower()
        out.append({
            "line": raw.get("line"),
            "agents": [str(a).strip().lower() for a in agents],
            "status": "progression" if status_raw in _PROGRESSION
            else (status_raw or None),
        })
    return out


def progressed_on(facts: dict[str, Any], keywords: tuple[str, ...]) -> bool:
    """True only for EXPLICIT progression on a matching agent."""
    for entry in treatment_history(facts):
        if entry["status"] != "progression":
            continue
        for agent in entry["agents"]:
            if any(k in agent for k in keywords):
                return True
    return False


def progressed_agents(facts: dict[str, Any]) -> list[str]:
    """Every recorded agent with explicit progression (lowercase)."""
    agents: list[str] = []
    for entry in treatment_history(facts):
        if entry["status"] == "progression":
            agents.extend(entry["agents"])
    return agents


def history_summary(facts: dict[str, Any]) -> str:
    parts = []
    for entry in treatment_history(facts):
        label = "/".join(entry["agents"]) or "unspecified"
        if entry["status"]:
            label += f" ({entry['status']})"
        parts.append(label)
    return "; ".join(parts)


def _option(name: str, regimen_ids: list[str], rationale: str) -> dict:
    return {"name": name, "regimen_ids": regimen_ids, "rationale": rationale}


def sequencing_context(stage_group: str,
                       facts: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Next-line context after explicit progression, or None while the
    case is first-line (no progression on record). Options carry regimen
    ids for the planner's indication gate; everything is dose-free."""
    if stage_group not in ("IVA", "IVB"):
        return None
    history = treatment_history(facts)
    if not any(e["status"] == "progression" for e in history):
        return None

    options: list[dict[str, Any]] = []
    workup: list[str] = []
    cautions: list[str] = []
    honest: list[str] = []
    drivers = _normalized_drivers(facts)
    line = max((e["line"] or 0 for e in history), default=0) + 1 or 2

    egfr_positive = driver_status(drivers.get("egfr")) == "positive"
    alk_positive = driver_status(drivers.get("alk")) == "positive"

    if egfr_positive and progressed_on(facts, _THIRD_GEN_EGFR):
        if not facts.get("progression_ngs_done"):
            workup.append(
                "Re-biopsy or plasma NGS AT PROGRESSION — MET "
                "amplification, C797S and histologic (small-cell) "
                "transformation each change the next line; the options "
                "below are provisional until the resistance mechanism "
                "question has been asked")
        if egfr_classical(facts):
            options.append(_option(
                "Amivantamab + platinum-pemetrexed",
                ["amivantamab_chemo_subsequent"],
                "Post-osimertinib progression, EGFR ex19del/L858R: "
                "MARIPOSA-2"))
        options.append(_option(
            "Platinum-pemetrexed chemotherapy",
            ["platinum_pemetrexed_post_tki"],
            "Post-TKI chemotherapy backbone; adding pembrolizumab did "
            "not improve OS in this population (KEYNOTE-789) — chemo-IO "
            "is not the default here"))
        honest.append(
            "Resistance-mechanism-directed strategies (MET-amp "
            "combinations, transformation regimens, fourth-generation "
            "TKIs) are NOT encoded — molecular tumor board on the "
            "progression NGS result.")
    elif alk_positive and progressed_on(facts, _LORLATINIB):
        options.append(_option(
            "Platinum-pemetrexed chemotherapy",
            ["platinum_pemetrexed_post_tki"],
            "Post-lorlatinib ALK+: no established next-line TKI — "
            "chemotherapy is the evidence-based next step"))
        honest.append(
            "Post-lorlatinib resistance-mutation-directed selection is "
            "NOT encoded; report compound mutations to the molecular "
            "tumor board.")
    elif alk_positive and progressed_on(facts, _SECOND_GEN_ALK):
        options.append(_option(
            "Lorlatinib", ["lorlatinib_post_second_gen"],
            "Progression on a second-generation ALK TKI: lorlatinib "
            "(third-generation, CNS-penetrant)"))
    elif progressed_on(facts, _IO_AGENTS) or progressed_on(facts,
                                                           _CHEMO_AGENTS):
        kras = drivers.get("kras")
        if kras is not None and driver_status(kras) == "positive" \
                and "g12c" in str(kras).lower().replace(" ", ""):
            options.append(_option(
                "Sotorasib (KRAS G12C)", ["sotorasib_subsequent_line"],
                "KRAS G12C after first-line chemo-IO: CodeBreaK 100 "
                "(adagrasib is the alternative)"))
        erbb2 = drivers.get("erbb2")
        if erbb2 is not None and driver_status(erbb2) == "positive":
            options.append(_option(
                "Trastuzumab deruxtecan (HER2)", ["tdxd_subsequent_line"],
                "HER2(ERBB2)-mutant, previously treated: DESTINY-Lung02"))
        options.append(_option(
            "Docetaxel + ramucirumab", ["docetaxel_ramucirumab_second_line"],
            "Progression on first-line (chemo-)immunotherapy: REVEL"))
        options.append(_option(
            "Docetaxel monotherapy", ["docetaxel_second_line"],
            "When ramucirumab is contraindicated (bleeding risk, "
            "cavitating central lesions)"))
    else:
        cautions.append(
            "Progression is on record but the prior agents do not map "
            "to an encoded sequencing branch — molecular tumor board "
            "review; no next-line regimen is proposed from this corpus.")

    cautions.append(
        f"Later-line setting (documented progression on: "
        f"{history_summary(facts)}): the first-line decision table does "
        f"not apply; goals-of-care and trial-enrollment discussion "
        f"belong in this visit.")
    honest.append(
        "Sequencing corpus is teaching-scale: third-line and beyond, "
        "rechallenge strategies and antibody-drug-conjugate salvage "
        "lines are not encoded.")
    return {"line": line, "options": options, "workup": workup,
            "cautions": cautions, "honest_notes": honest}
