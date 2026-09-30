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

import re
from typing import Any, Optional

from .biomarkers import (
    _normalized_drivers,
    driver_status,
    egfr_classes,
    egfr_classical,
    first_line_actionable_drivers,
)

_PROGRESSION = {"progression", "progressed", "pd", "进展", "耐药"}

#: Free-text progression tokens. English tokens are word-bounded so
#: "PD-L1", "updated" or "PDL1" never read as progression; Chinese tokens
#: are substrings. A negated mention ("no progression", "未进展") is not
#: progression.
_PROGRESSION_TEXT_RE = re.compile(
    r"(?<![\w-])(?:progress(?:ion|ed|ive)?|pd)(?![\w-])|进展|耐药", re.I)
_NEGATED_PROGRESSION_RE = re.compile(
    r"(?:\bno\s+|\bwithout\s+|\bnot\s+|未|无|没有)"
    r"(?:evidence\s+of\s+|disease\s+|明显)?(?:progress\w*|pd\b|进展)",
    re.I)

#: Chinese (and brand) names → the generic English name the regimen
#: library uses, so a history recorded as "奥希替尼" still matches the
#: osimertinib component of a re-proposed regimen.
_AGENT_ALIASES = {
    "奥希替尼": "osimertinib", "泰瑞沙": "osimertinib",
    "吉非替尼": "gefitinib", "易瑞沙": "gefitinib",
    "厄洛替尼": "erlotinib", "特罗凯": "erlotinib",
    "阿法替尼": "afatinib", "达可替尼": "dacomitinib",
    "埃克替尼": "icotinib", "阿美替尼": "aumolertinib",
    "伏美替尼": "furmonertinib",
    "克唑替尼": "crizotinib", "阿来替尼": "alectinib",
    "布格替尼": "brigatinib", "塞瑞替尼": "ceritinib",
    "洛拉替尼": "lorlatinib", "恩沙替尼": "ensartinib",
    "培美曲塞": "pemetrexed", "卡铂": "carboplatin", "顺铂": "cisplatin",
    "紫杉醇": "paclitaxel", "多西他赛": "docetaxel",
    "帕博利珠": "pembrolizumab", "阿替利珠": "atezolizumab",
    "纳武利尤": "nivolumab", "度伐利尤": "durvalumab",
}

#: Agent keywords (lowercase substring match against recorded agents).
_THIRD_GEN_EGFR = ("osimertinib", "奥希替尼", "lazertinib", "amivantamab",
                   "aumolertinib", "furmonertinib")
#: First/second-generation EGFR TKIs: progression on these is the
#: T790M question (AURA3), not the post-osimertinib question.
_EARLY_GEN_EGFR = ("gefitinib", "erlotinib", "afatinib", "dacomitinib",
                   "icotinib")
_SECOND_GEN_ALK = ("alectinib", "brigatinib", "阿来替尼", "布格替尼",
                   "ceritinib", "ensartinib")
_LORLATINIB = ("lorlatinib", "洛拉替尼")
_IO_AGENTS = ("pembrolizumab", "atezolizumab", "nivolumab", "cemiplimab",
              "durvalumab", "帕博利珠", "阿替利珠", "纳武利尤",
              "immunotherapy", "chemo-io", "chemoimmunotherapy")
_CHEMO_AGENTS = ("pemetrexed", "carboplatin", "cisplatin", "paclitaxel",
                 "nab-paclitaxel", "培美曲塞", "卡铂", "顺铂", "紫杉醇",
                 "platinum", "chemotherapy", "化疗")

#: Driver-directed agents per first-line actionable driver. A patient
#: who progressed on chemotherapy but never received the driver's
#: targeted agent is NOT in a later-line targeted setting: the
#: driver-directed first-line therapy is still the next line.
DRIVER_DIRECTED_AGENTS: dict[str, tuple[str, ...]] = {
    "EGFR": _THIRD_GEN_EGFR + _EARLY_GEN_EGFR,
    "ALK": _SECOND_GEN_ALK + _LORLATINIB + ("crizotinib",),
    "ROS1": ("crizotinib", "entrectinib", "repotrectinib", "taletrectinib",
             "lorlatinib"),
    "RET": ("selpercatinib", "pralsetinib"),
    "MET": ("capmatinib", "tepotinib", "crizotinib", "savolitinib"),
    "BRAF": ("dabrafenib", "trametinib", "encorafenib", "binimetinib"),
    "NTRK": ("larotrectinib", "entrectinib", "repotrectinib"),
}
#: Every targeted agent above — the post-TKI chemotherapy backbone's
#: prior-therapy requirement.
ANY_TKI = tuple(sorted({a for agents in DRIVER_DIRECTED_AGENTS.values()
                        for a in agents}))


def _normalize_agent(agent: Any) -> list[str]:
    """Lowercased agent label plus its generic alias when one applies."""
    text = str(agent or "").strip().lower()
    if not text:
        return []
    out = [text]
    for alias, generic in _AGENT_ALIASES.items():
        if alias in text and generic not in text:
            out.append(generic)
    return out


def _text_says_progression(text: str) -> bool:
    if not _PROGRESSION_TEXT_RE.search(text):
        return False
    remaining = _NEGATED_PROGRESSION_RE.sub(" ", text)
    return bool(_PROGRESSION_TEXT_RE.search(remaining))


def treatment_history(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalized history entries: {line, agents (lowercase), status}."""
    out: list[dict[str, Any]] = []
    for raw in facts.get("treatment_history") or []:
        if isinstance(raw, str):
            text = raw.strip()
            status = "progression" if _text_says_progression(text) else None
            out.append({"line": None, "agents": _normalize_agent(text),
                        "status": status})
            continue
        if not isinstance(raw, dict):
            continue
        agents = raw.get("agents") or raw.get("regimens") \
            or raw.get("regimen") or raw.get("drugs") or []
        if isinstance(agents, str):
            agents = [agents]
        normalized = [n for a in agents for n in _normalize_agent(a)]
        status_raw = str(raw.get("status") or "").strip().lower()
        progressed = status_raw in _PROGRESSION or (
            bool(status_raw) and _text_says_progression(status_raw))
        out.append({
            "line": raw.get("line"),
            "agents": normalized,
            "status": "progression" if progressed else (status_raw or None),
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


def exposed_to(facts: dict[str, Any], keywords: tuple[str, ...]) -> bool:
    """Any recorded exposure (whatever the outcome) to a matching agent."""
    return any(any(k in agent for k in keywords)
               for entry in treatment_history(facts)
               for agent in entry["agents"])


def progressed_agents(facts: dict[str, Any]) -> list[str]:
    """Every recorded agent with explicit progression (lowercase)."""
    agents: list[str] = []
    for entry in treatment_history(facts):
        if entry["status"] == "progression":
            agents.extend(entry["agents"])
    return agents


def progressed_drugs_in(regimen_id: str, facts: dict[str, Any]) -> list[str]:
    """Components of this regimen the record documents progression on."""
    from . import regimens as regimen_lib

    regimen = regimen_lib.get(regimen_id)
    progressed = [a for a in progressed_agents(facts) if a]
    if regimen is None or not progressed:
        return []
    hits: set[str] = set()
    for component in regimen.components:
        drug = component.drug.lower()
        for agent in progressed:
            if drug in agent or agent in drug:
                hits.add(drug)
    return sorted(hits)


def driver_therapy_progressed(facts: dict[str, Any]) -> bool:
    """True when the disease progressed on the directed therapy of a
    first-line actionable driver on record — the only history that
    moves a driver-positive case out of the first-line question."""
    for driver in first_line_actionable_drivers(facts):
        agents = DRIVER_DIRECTED_AGENTS.get(driver["gene"])
        if agents and progressed_on(facts, agents):
            return True
    return False


def history_summary(facts: dict[str, Any]) -> str:
    parts = []
    for entry in treatment_history(facts):
        label = "/".join(entry["agents"]) or "unspecified"
        if entry["status"]:
            label += f" ({entry['status']})"
        parts.append(label)
    return "; ".join(parts)


def _option(name: str, regimen_ids: list[str], rationale: str, *,
            continuation: bool = False) -> dict:
    out = {"name": name, "regimen_ids": regimen_ids, "rationale": rationale}
    if continuation:
        # Deliberately continues a progressed drug (mechanism-directed);
        # the planner lets it through and the critic's same-drug warn is
        # its documentation demand.
        out["continuation"] = True
    return out


def progression_findings(facts: dict[str, Any]) -> dict[str, Any]:
    """Normalized resistance-mechanism findings from the progression
    re-biopsy / plasma NGS. Presence of the fact means the question was
    ASKED; every unstated mechanism is False-as-recorded, not unknown —
    the report either found it or it did not."""
    raw = facts.get("progression_findings")
    if not isinstance(raw, dict):
        return {"available": False, "met_amplification": False,
                "c797s": False, "small_cell_transformation": False,
                "other": ""}
    return {
        "available": True,
        "met_amplification": bool(raw.get("met_amplification")),
        "c797s": bool(raw.get("c797s")),
        "small_cell_transformation": bool(
            raw.get("small_cell_transformation")),
        "other": str(raw.get("other") or ""),
    }


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
    numbered = [e["line"] for e in history if isinstance(e["line"], int)]
    progressions = sum(1 for e in history if e["status"] == "progression")
    line = max(max(numbered, default=0), progressions) + 1

    # A driver-positive patient who progressed on chemotherapy (or
    # chemo-IO) but never received the driver's targeted agent is NOT in
    # a later-line targeted setting — the driver-directed first-line
    # therapy is still the next line. Sequencing defers to the first-line
    # table instead of offering docetaxel over an unused TKI.
    actionable = first_line_actionable_drivers(facts)
    lead = actionable[0] if actionable else None
    if lead and not exposed_to(
            facts, DRIVER_DIRECTED_AGENTS.get(lead["gene"], ())):
        return {
            "line": line, "defer_to_first_line": True, "options": [],
            "workup": [], "honest_notes": [],
            "cautions": [
                f"Progression is documented ({history_summary(facts)}), "
                f"but no {lead['gene']}-directed therapy has been given: "
                f"the driver-directed first-line therapy IS the next "
                f"line — chemotherapy/immunotherapy sequencing does not "
                f"apply until it has been used."],
        }

    egfr_positive = driver_status(drivers.get("egfr")) == "positive"
    alk_positive = driver_status(drivers.get("alk")) == "positive"
    early_gen_only = progressed_on(facts, _EARLY_GEN_EGFR) \
        and not progressed_on(facts, _THIRD_GEN_EGFR)

    if egfr_positive and (progressed_on(facts, _THIRD_GEN_EGFR)
                          or early_gen_only):
        findings = progression_findings(facts)
        t790m = "t790m" in egfr_classes(facts)
        if early_gen_only and t790m \
                and not findings["small_cell_transformation"]:
            options.append(_option(
                "Osimertinib (acquired T790M)",
                ["osimertinib_t790m_subsequent"],
                "T790M-positive progression on a first/second-generation "
                "EGFR TKI: AURA3 (osimertinib over platinum-pemetrexed)"))
            honest.append(
                "Post-osimertinib resistance after this line is the "
                "MARIPOSA-2 question — it is handled when that "
                "progression is recorded.")
        elif findings["small_cell_transformation"]:
            # Different disease biology: the EGFR-directed options do
            # not apply to the transformed clone.
            options.append(_option(
                "Platinum-etoposide (small-cell transformation)",
                ["platinum_etoposide_transformation"],
                "Histologic small-cell transformation: treat the "
                "transformed clone with SCLC-style chemotherapy "
                "(retrospective multicenter evidence — Marcoux series); "
                "whether to continue the EGFR TKI alongside is an MDT "
                "decision for any co-existing adenocarcinoma clone"))
            cautions.append(
                "Small-cell transformation is a change of disease "
                "biology, not a new EGFR resistance mutation — "
                "EGFR-directed second-line options (amivantamab-chemo) "
                "do not address the transformed clone.")
            honest.append(
                "Transformation management beyond the platinum-etoposide "
                "backbone (TKI continuation, radiotherapy integration, "
                "mixed-histology dosing) is NOT encoded — thoracic "
                "tumor board.")
        elif early_gen_only and not progressed_on(facts, _CHEMO_AGENTS):
            # First/second-generation TKI progression: the T790M question.
            if not facts.get("progression_ngs_done") \
                    and not findings["available"]:
                workup.append(
                    "T790M testing AT PROGRESSION (plasma ctDNA first; "
                    "tissue re-biopsy if plasma is negative) — "
                    "T790M-positive disease goes to osimertinib (AURA3); "
                    "the options below are provisional until it is known")
            options.append(_option(
                "Platinum-pemetrexed chemotherapy",
                ["platinum_pemetrexed_post_tki"],
                "T790M-negative progression on a first/second-generation "
                "EGFR TKI: platinum-pemetrexed; continuing the "
                "first-generation TKI alongside chemotherapy did not help "
                "(IMPRESS)"))
        elif progressed_on(facts, _CHEMO_AGENTS):
            # Third line: TKI and platinum both exhausted.
            options.append(_option(
                "Datopotamab deruxtecan (TROP2 ADC)",
                ["dato_dxd_egfr_subsequent"],
                "EGFR-mutant after EGFR-directed therapy AND "
                "platinum-based chemotherapy: TROPION-Lung05 "
                "(accelerated approval)"))
            options.append(_option(
                "Docetaxel ± ramucirumab",
                ["docetaxel_ramucirumab_second_line",
                 "docetaxel_second_line"],
                "Post-platinum single-agent standard (REVEL enrolled "
                "EGFR-mutant patients)"))
            honest.append(
                "Beyond this line the corpus has no encoded standard — "
                "trial enrollment and goals-of-care are the visit's "
                "primary questions.")
        else:
            if not facts.get("progression_ngs_done") \
                    and not findings["available"]:
                workup.append(
                    "Re-biopsy or plasma NGS AT PROGRESSION — MET "
                    "amplification, C797S and histologic (small-cell) "
                    "transformation each change the next line; the "
                    "options below are provisional until the resistance "
                    "mechanism question has been asked")
            if findings["met_amplification"]:
                options.append(_option(
                    "Tepotinib + osimertinib (MET amplification)",
                    ["tepotinib_osimertinib_met_amp"],
                    "MET-amplification-driven resistance: INSIGHT 2 "
                    "(phase 2) — a mechanism-directed CONTINUATION of "
                    "osimertinib plus MET inhibition; the same-drug "
                    "flag on this plan is the documentation demand, "
                    "not an error", continuation=True))
            if findings["c797s"]:
                cautions.append(
                    "C797S on the progression report: no approved "
                    "fourth-generation TKI — allele configuration "
                    "(cis/trans with T790M) changes the theoretical "
                    "options and belongs to the molecular tumor board; "
                    "the chemotherapy-backbone options below remain the "
                    "evidence-based next line.")
            if egfr_classical(facts):
                options.append(_option(
                    "Amivantamab + platinum-pemetrexed",
                    ["amivantamab_chemo_subsequent"],
                    "Post-osimertinib progression, EGFR ex19del/L858R: "
                    "MARIPOSA-2 (enrolled irrespective of resistance "
                    "mechanism)"))
            options.append(_option(
                "Platinum-pemetrexed chemotherapy",
                ["platinum_pemetrexed_post_tki"],
                "Post-TKI chemotherapy backbone; adding pembrolizumab "
                "did not improve OS in this population (KEYNOTE-789) — "
                "chemo-IO is not the default here"))
            honest.append(
                "Mechanism-directed coverage is exactly two findings "
                "deep (transformation → platinum-etoposide; MET-amp → "
                "INSIGHT-2 continuation): fourth-generation TKIs and "
                "other combinations are NOT encoded — molecular tumor "
                "board on the progression NGS result.")
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
    elif progressed_on(facts, ("docetaxel", "多西他赛")):
        # Beyond the encoded lines: the honest boundary IS the answer.
        cautions.append(
            "Progression beyond docetaxel: this corpus has no encoded "
            "later line — clinical-trial screening, best supportive "
            "care and a goals-of-care conversation are the "
            "evidence-based next steps, and saying so beats inventing "
            "a regimen.")
        honest.append(
            "Post-docetaxel salvage (further ADCs, rechallenge "
            "strategies) is NOT encoded.")
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
        "Sequencing corpus is teaching-scale: coverage ends at the "
        "named branches (mechanism-directed two findings deep, EGFR "
        "third line, chemo-IO second line) — rechallenge criteria and "
        "further salvage lines are not encoded.")
    return {"line": line, "options": options, "workup": workup,
            "cautions": cautions, "honest_notes": honest}
