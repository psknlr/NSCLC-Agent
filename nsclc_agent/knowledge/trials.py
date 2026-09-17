"""Curated registry of landmark NSCLC trials — the deterministic evidence core.

This is what turns "trial stage boundary discipline" from a prompt exhortation
into a machine-checkable rule: every entry declares the stage groups, histology
and driver constraints under which the regimen applies, and
:mod:`nsclc_agent.safety.rules` checks a proposed plan against them. The same
entries back the ``trial_lookup`` tool, so the model's citations resolve to
registry entries with stable identifiers instead of free-floating claims.

Curation rules:

* Numbers are from the primary publications / regulatory actions named in
  ``source``; when a value is disputed or immature it is stated qualitatively.
* ``stage_groups`` is the set of 9th-edition stage groups where use is
  *on-evidence*; the trial's own enrollment edition is preserved in
  ``enrollment_note`` because most landmark trials enrolled under AJCC 7/8.
  Using a regimen outside ``stage_groups`` is a boundary violation unless the
  plan explicitly declares it an extrapolation with justification.
* This registry is a teaching corpus, not a substitute for a licensed
  guideline database. Entries carry ``registered_trial`` evidence grade; a
  configured knowledge store may supersede them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

REGISTRY_VERSION = "2026-08"


@dataclass(frozen=True)
class Trial:
    trial_id: str
    name: str
    nct: str
    setting: str  # adjuvant|neoadjuvant|perioperative|consolidation|first_line|radiotherapy|local_consolidative
    #: 9th-edition stage groups where use is on-evidence.
    stage_groups: frozenset[str]
    #: "any" | "nonsquamous" | "squamous"
    histology: str = "any"
    #: Driver alteration the population must carry ("EGFR", "ALK"), or None.
    driver_required: Optional[str] = None
    #: True when known EGFR/ALK alterations were excluded — the perioperative
    #: immunotherapy trials. The rule engine reads this.
    egfr_alk_excluded: bool = False
    regimen_ids: tuple[str, ...] = ()
    #: Key results as short strings; every numeric is tied to `source`.
    results: tuple[str, ...] = ()
    source: str = ""
    approval: str = ""
    enrollment_note: str = ""
    caveats: tuple[str, ...] = ()
    keywords: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "trial_id": self.trial_id, "name": self.name, "nct": self.nct,
            "setting": self.setting,
            "stage_groups": sorted(self.stage_groups),
            "histology": self.histology,
            "driver_required": self.driver_required,
            "egfr_alk_excluded": self.egfr_alk_excluded,
            "regimen_ids": list(self.regimen_ids),
            "results": list(self.results),
            "source": self.source, "approval": self.approval,
            "enrollment_note": self.enrollment_note,
            "caveats": list(self.caveats),
        }


def _s(*groups: str) -> frozenset[str]:
    return frozenset(groups)


_EARLY = ("IB", "IIA", "IIB", "IIIA")
_RESECTABLE_II_IIIB = ("IIA", "IIB", "IIIA", "IIIB")
_STAGE_III = ("IIIA", "IIIB", "IIIC")
_STAGE_IV = ("IVA", "IVB")

TRIALS: tuple[Trial, ...] = (
    # ------------------------------------------------ adjuvant targeted therapy
    Trial(
        "ADAURA", "ADAURA (adjuvant osimertinib)", "NCT02511106", "adjuvant",
        _s(*_EARLY), histology="nonsquamous", driver_required="EGFR",
        regimen_ids=("osimertinib_adjuvant",),
        results=(
            "DFS HR 0.17 (99.06% CI 0.11–0.26) in stage II–IIIA (primary); "
            "HR 0.20 (99.12% CI 0.14–0.30) overall IB–IIIA",
            "OS HR 0.49 (95.03% CI 0.34–0.70) overall population; "
            "0.49 (95.03% CI 0.33–0.73) in stage II–IIIA",
        ),
        source="NEJM 2020;383:1711 (primary DFS); NEJM 2023;389:137 (OS)",
        approval="FDA 2020-12 adjuvant osimertinib, resected IB–IIIA EGFR ex19del/L858R",
        enrollment_note="Enrolled by AJCC 7th edition IB–IIIA after complete resection ± adjuvant chemo",
        caveats=("Not evidence for stage IIIB — use beyond IIIA is extrapolation",
                 "Requires EGFR ex19del or L858R"),
        keywords=("osimertinib", "EGFR", "adjuvant", "resected"),
    ),
    Trial(
        "ALINA", "ALINA (adjuvant alectinib)", "NCT03456076", "adjuvant",
        _s(*_EARLY), driver_required="ALK",
        regimen_ids=("alectinib_adjuvant",),
        results=("DFS HR 0.24 (95% CI 0.13–0.45) in stage II–IIIA (primary); "
                 "HR 0.24 (95% CI 0.13–0.43) in the ITT incl. IB ≥4 cm",),
        source="NEJM 2024;390:1265",
        approval="FDA 2024-04 adjuvant alectinib, resected IB(≥4 cm)–IIIA ALK+",
        enrollment_note="Enrolled by AJCC 8th edition IB(≥4 cm)–IIIA",
        caveats=("Chemotherapy-free design: alectinib replaced adjuvant chemo",),
        keywords=("alectinib", "ALK", "adjuvant"),
    ),
    # ------------------------------------------------ neoadjuvant / perioperative IO
    Trial(
        "CHECKMATE816", "CheckMate 816 (neoadjuvant nivolumab + chemo)",
        "NCT02998528", "neoadjuvant",
        _s(*_EARLY), egfr_alk_excluded=True,
        regimen_ids=("nivo_chemo_neoadjuvant",),
        results=(
            "EFS HR 0.63 (97.38% CI 0.43–0.91); pCR 24.0% vs 2.2%",
            "OS HR 0.72 (95% CI 0.523–0.998) at 5-year analysis; pCR patients 5-yr OS ~95%",
            "No decrease in surgical feasibility (83% vs 75% resected)",
        ),
        source="NEJM 2022;386:1973 (EFS/pCR); NEJM 2025 5-year OS analysis",
        approval="FDA 2022-03 neoadjuvant nivolumab + platinum-doublet, ≥4 cm or node-positive resectable NSCLC",
        enrollment_note="Enrolled IB(≥4 cm)–IIIA by AJCC 7th edition; 3 cycles, no adjuvant IO",
        caveats=("Known EGFR/ALK alterations excluded",
                 "IIIB is outside enrollment — perioperative regimens cover resectable IIIB(N2) instead"),
        keywords=("nivolumab", "neoadjuvant", "pCR", "chemo-immunotherapy"),
    ),
    Trial(
        "KEYNOTE671", "KEYNOTE-671 (perioperative pembrolizumab)",
        "NCT03425643", "perioperative",
        _s(*_RESECTABLE_II_IIIB), egfr_alk_excluded=True,
        regimen_ids=("pembro_perioperative",),
        results=(
            "EFS HR 0.58 (95% CI 0.46–0.72)",
            "OS HR 0.72 (95% CI 0.56–0.93, p=0.00517) — first perioperative trial with significant OS",
        ),
        source="NEJM 2023;389:491 (EFS); Lancet 2024;404:1240 (OS)",
        approval="FDA 2023-10 perioperative pembrolizumab, resectable II–IIIB(N2) NSCLC",
        enrollment_note="Enrolled II–IIIB(N2) by AJCC 8th edition; neoadjuvant ×4 + adjuvant ×13",
        caveats=("EGFR/ALK-positive patients: use targeted adjuvant standards instead",),
        keywords=("pembrolizumab", "perioperative", "resectable"),
    ),
    Trial(
        "AEGEAN", "AEGEAN (perioperative durvalumab)", "NCT03800134", "perioperative",
        _s(*_RESECTABLE_II_IIIB), egfr_alk_excluded=True,
        regimen_ids=("durva_perioperative",),
        results=("EFS HR 0.68 (95% CI 0.53–0.88); pCR 17.2% vs 4.3%",),
        source="NEJM 2023;389:1672",
        approval="FDA 2024-08 perioperative durvalumab, resectable IIA–IIIB(N2) NSCLC without EGFR/ALK alterations",
        enrollment_note="Enrolled IIA–IIIB(N2) by AJCC 8th edition; EGFR/ALK excluded from mITT",
        keywords=("durvalumab", "perioperative"),
    ),
    Trial(
        "CHECKMATE77T", "CheckMate 77T (perioperative nivolumab)",
        "NCT04025879", "perioperative",
        _s(*_RESECTABLE_II_IIIB), egfr_alk_excluded=True,
        regimen_ids=("nivo_perioperative",),
        results=("EFS HR 0.58 (97.36% CI 0.42–0.81)",),
        source="NEJM 2024;390:1756",
        approval="FDA 2024-10 perioperative nivolumab, resectable IIA–IIIB(N2) NSCLC",
        enrollment_note="Enrolled IIA–IIIB(N2) by AJCC 8th edition",
        keywords=("nivolumab", "perioperative"),
    ),
    # ------------------------------------------------ adjuvant immunotherapy
    Trial(
        "IMPOWER010", "IMpower010 (adjuvant atezolizumab)", "NCT02486718", "adjuvant",
        _s("IIA", "IIB", "IIIA"),
        regimen_ids=("atezolizumab_adjuvant",),
        results=("DFS HR 0.66 (95% CI 0.50–0.88) in PD-L1 TC≥1% stage II–IIIA",),
        source="Lancet 2021;398:1344",
        approval="FDA 2021-10 adjuvant atezolizumab, II–IIIA PD-L1 TC≥1% after resection + platinum chemo",
        enrollment_note="Enrolled IB(≥4 cm)–IIIA by AJCC 7th; approval limited to II–IIIA PD-L1 TC≥1%",
        caveats=("PD-L1 TC≥1% required by the FDA label (TC≥50% in the EMA label)",
                 "EGFR/ALK: exploratory subgroups showed no benefit — prefer targeted adjuvant therapy"),
        keywords=("atezolizumab", "adjuvant", "PD-L1"),
    ),
    Trial(
        "KEYNOTE091", "KEYNOTE-091/PEARLS (adjuvant pembrolizumab)",
        "NCT02504372", "adjuvant",
        _s(*_EARLY),
        regimen_ids=("pembro_adjuvant",),
        results=("DFS HR 0.76 (95% CI 0.63–0.91) in the ITT population, irrespective of PD-L1",),
        source="Lancet Oncol 2022;23:1274",
        approval="FDA 2023-01 adjuvant pembrolizumab, IB(≥4 cm)–IIIA following "
                 "resection AND platinum-based chemotherapy",
        enrollment_note="Enrolled IB(≥4 cm)–IIIA by AJCC 7th, adjuvant chemo optional "
                        "in-trial (~86% received it); the FDA label requires it. "
                        "ITT benefit — PD-L1 not required",
        caveats=("EGFR/ALK-positive: prefer targeted adjuvant standards",),
        keywords=("pembrolizumab", "adjuvant"),
    ),
    Trial(
        "LACE", "LACE meta-analysis (adjuvant platinum chemotherapy)", "", "adjuvant",
        _s("IIA", "IIB", "IIIA", "IIIB"),
        regimen_ids=("adjuvant_platinum_doublet",),
        results=("5-year absolute OS benefit ~5.4% (HR 0.89, 95% CI 0.82–0.96); driven by stage II–III",),
        source="J Clin Oncol 2008;26:3552 (LACE pooled analysis)",
        approval="Guideline standard (NCCN/ESMO) for resected II–III; selected IB with high-risk features",
        enrollment_note="Pooled cisplatin-based trials, AJCC 5/6-era staging",
        keywords=("adjuvant chemotherapy", "cisplatin", "vinorelbine"),
    ),
    # ------------------------------------------------ unresectable stage III
    Trial(
        "PACIFIC", "PACIFIC (consolidation durvalumab)", "NCT02125461", "consolidation",
        _s(*_STAGE_III),
        regimen_ids=("durva_consolidation",),
        results=(
            "PFS HR 0.52 (95% CI 0.42–0.65); OS HR 0.68 (99.73% CI 0.47–0.997)",
            "5-year OS 42.9% vs 33.4%",
        ),
        source="NEJM 2017;377:1919 (PFS); NEJM 2018;379:2342 (OS); JCO 2022;40:1301 (5-year)",
        approval="FDA 2018-02 (any PD-L1); EMA restricts to PD-L1 ≥1%",
        enrollment_note="Unresectable stage III (AJCC 7th), no progression after platinum-based cCRT; start ≤42 days post-CRT",
        caveats=(
            "Post-hoc EGFR subgroup showed no clear benefit — see LAURA for EGFR+",
            "Never given concurrently with CRT (PACIFIC-2 negative)",
        ),
        keywords=("durvalumab", "consolidation", "chemoradiation", "unresectable"),
    ),
    Trial(
        "LAURA", "LAURA (osimertinib after definitive CRT)", "NCT03521154", "consolidation",
        _s(*_STAGE_III), driver_required="EGFR",
        regimen_ids=("osimertinib_consolidation",),
        results=("PFS 39.1 vs 5.6 months, HR 0.16 (95% CI 0.10–0.24)",),
        source="NEJM 2024;391:585",
        approval="FDA 2024-09 osimertinib after CRT, unresectable III EGFR ex19del/L858R",
        enrollment_note="Unresectable stage III EGFR+ without progression after definitive CRT; treatment until progression",
        caveats=("Replaces durvalumab for EGFR-mutated unresectable stage III",),
        keywords=("osimertinib", "EGFR", "consolidation", "unresectable"),
    ),
    Trial(
        "PACIFIC2", "PACIFIC-2 (concurrent durvalumab + cCRT — negative)",
        "NCT03519971", "consolidation",
        frozenset(),  # negative trial: applies nowhere; exists to block the design
        regimen_ids=(),
        results=("Concurrent durvalumab with cCRT did not improve PFS — do not administer concurrently",),
        source="Reported 2024 (ELCC); primary endpoint not met",
        approval="None — negative trial",
        enrollment_note="Negative-design anchor: durvalumab is consolidation therapy only",
        keywords=("durvalumab", "concurrent", "negative"),
    ),
    Trial(
        "RTOG0617", "RTOG 0617 (60 Gy vs 74 Gy cCRT)", "NCT00533949", "radiotherapy",
        _s(*_STAGE_III),
        regimen_ids=("ccrt_60gy",),
        results=("74 Gy arm had *worse* OS than 60 Gy (median 20.3 vs 28.7 months, HR 1.38)",),
        source="Lancet Oncol 2015;16:187",
        approval="Standard: 60 Gy in 30 fractions with concurrent platinum doublet; do not dose-escalate",
        enrollment_note="Stage III cCRT dose-escalation trial; escalation harmed survival",
        keywords=("radiotherapy", "60 Gy", "dose escalation"),
    ),
    # ------------------------------------------------ stage IV, driver-positive
    Trial(
        "FLAURA", "FLAURA (first-line osimertinib)", "NCT02296125", "first_line",
        _s(*_STAGE_IV), driver_required="EGFR",
        regimen_ids=("osimertinib_first_line",),
        results=("PFS HR 0.46 (95% CI 0.37–0.57); OS HR 0.80 (95.05% CI 0.64–1.00)",),
        source="NEJM 2018;378:113 (PFS); NEJM 2020;382:41 (OS)",
        approval="FDA 2018-04 first-line osimertinib, metastatic EGFR ex19del/L858R "
                 "(no histology restriction in the label)",
        enrollment_note="Advanced/metastatic EGFR+, predominantly adenocarcinoma "
                        "enrolled; CNS-active",
        keywords=("osimertinib", "EGFR", "first line"),
    ),
    Trial(
        "FLAURA2", "FLAURA2 (osimertinib + platinum-pemetrexed)", "NCT04035486", "first_line",
        _s(*_STAGE_IV), histology="nonsquamous", driver_required="EGFR",
        regimen_ids=("osimertinib_chemo_first_line",),
        results=("PFS HR 0.62 (95% CI 0.49–0.79) vs osimertinib alone; higher toxicity",),
        source="NEJM 2023;389:1935",
        approval="FDA 2024-02 osimertinib + platinum-pemetrexed, metastatic EGFR+",
        enrollment_note="Option for high burden / CNS disease; weigh added chemo toxicity",
        keywords=("osimertinib", "chemotherapy", "combination"),
    ),
    Trial(
        "MARIPOSA", "MARIPOSA (amivantamab + lazertinib)", "NCT04487080", "first_line",
        _s(*_STAGE_IV), driver_required="EGFR",
        regimen_ids=("amivantamab_lazertinib",),
        results=("PFS HR 0.70 (95% CI 0.58–0.85) vs osimertinib; OS benefit reported at later analysis",),
        source="NEJM 2024;391:1486; OS update 2025",
        approval="FDA 2024-08 first-line amivantamab-vmjw + lazertinib, metastatic EGFR ex19del/L858R",
        enrollment_note="Higher toxicity (infusion reactions, VTE — prophylactic anticoagulation advised)",
        keywords=("amivantamab", "lazertinib", "EGFR"),
    ),
    Trial(
        "CROWN", "CROWN (first-line lorlatinib)", "NCT03052608", "first_line",
        _s(*_STAGE_IV), driver_required="ALK",
        regimen_ids=("lorlatinib_first_line",),
        results=("PFS HR 0.27 (95% CI 0.18–0.39); 5-year PFS ~60% (HR 0.19 at long-term follow-up)",),
        source="NEJM 2020;383:2018; JCO 2024 long-term update",
        approval="FDA 2021-03 first-line lorlatinib, metastatic ALK+",
        enrollment_note="High CNS activity; CNS-penetrant; watch lipids and neurocognitive effects",
        keywords=("lorlatinib", "ALK", "first line"),
    ),
    # ------------------------------------------------ stage IV, driver-negative
    Trial(
        "KEYNOTE024", "KEYNOTE-024 (pembrolizumab mono, PD-L1≥50%)",
        "NCT02142738", "first_line",
        _s(*_STAGE_IV),
        regimen_ids=("pembro_monotherapy",),
        results=("OS HR 0.60 (95% CI 0.41–0.89); 5-year OS 31.9% vs 16.3%",),
        source="NEJM 2016;375:1823; JCO 2021;39:2339 (5-year)",
        approval="FDA 2016-10 first-line pembrolizumab, metastatic PD-L1 TPS≥50% without EGFR/ALK",
        enrollment_note="Requires PD-L1 TPS ≥50%; EGFR/ALK excluded",
        caveats=("Not for EGFR/ALK-positive disease",),
        keywords=("pembrolizumab", "monotherapy", "PD-L1 high"),
    ),
    Trial(
        "KEYNOTE189", "KEYNOTE-189 (pembrolizumab + pemetrexed-platinum)",
        "NCT02578680", "first_line",
        _s(*_STAGE_IV), histology="nonsquamous",
        regimen_ids=("pembro_pemetrexed_platinum",),
        results=("OS HR 0.49 (95% CI 0.38–0.64) at primary analysis; benefit across PD-L1 strata",),
        source="NEJM 2018;378:2078; JCO 2023 5-year update",
        approval="FDA 2017-05/2018-08 first-line, metastatic nonsquamous without EGFR/ALK",
        enrollment_note="EGFR/ALK excluded; pemetrexed maintenance continues",
        keywords=("pembrolizumab", "pemetrexed", "nonsquamous"),
    ),
    Trial(
        "KEYNOTE407", "KEYNOTE-407 (pembrolizumab + carboplatin-taxane)",
        "NCT02775435", "first_line",
        _s(*_STAGE_IV), histology="squamous",
        regimen_ids=("pembro_carbo_taxane",),
        results=("OS HR 0.64 (95% CI 0.49–0.85)",),
        source="NEJM 2018;379:2040",
        approval="FDA 2018-10 first-line, metastatic squamous NSCLC",
        keywords=("pembrolizumab", "squamous"),
    ),
    Trial(
        "CHECKMATE9LA", "CheckMate 9LA (nivolumab + ipilimumab + 2-cycle chemo)",
        "NCT03215706", "first_line",
        _s(*_STAGE_IV),
        regimen_ids=("nivo_ipi_chemo",),
        results=("OS HR 0.66 (96.71% CI 0.55–0.80)",),
        source="Lancet Oncol 2021;22:198",
        approval="FDA 2020-05 first-line, metastatic NSCLC without EGFR/ALK",
        keywords=("nivolumab", "ipilimumab", "dual immunotherapy"),
    ),
    Trial(
        "GOMEZ_LCT", "Gomez et al. (local consolidative therapy, oligometastatic)",
        "NCT01725165", "local_consolidative",
        _s("IVA"),
        regimen_ids=("sbrt_oligomet_lct",),
        results=("Randomized phase II: LCT improved PFS (14.2 vs 4.4 mo) and OS (41.2 vs 17.0 mo)",),
        source="Lancet Oncol 2016;17:1672; JCO 2019;37:1558 (OS update)",
        approval="Phase II evidence — offer within MDT/trial framework, after response to first-line systemic therapy",
        enrollment_note="≤3 metastases without progression after first-line systemic therapy",
        caveats=("Phase II sample size — frame as consolidative option, not universal standard",),
        keywords=("oligometastatic", "SBRT", "local consolidative therapy"),
    ),
)

TRIALS_BY_ID: dict[str, Trial] = {t.trial_id: t for t in TRIALS}

#: Trials whose regimens embed perioperative/adjuvant immunotherapy — the set
#: the EGFR/ALK exclusion rule checks against.
PERIOP_ADJUVANT_IO_TRIALS = frozenset(
    t.trial_id for t in TRIALS
    if t.setting in ("neoadjuvant", "perioperative")
    or t.trial_id in ("IMPOWER010", "KEYNOTE091")
)

#: Alias map so free-text references resolve to registry ids.
_ALIASES = {
    "CHECKMATE 816": "CHECKMATE816", "CM816": "CHECKMATE816", "CHECKMATE-816": "CHECKMATE816",
    "KEYNOTE 671": "KEYNOTE671", "KEYNOTE-671": "KEYNOTE671", "KN671": "KEYNOTE671",
    "CHECKMATE 77T": "CHECKMATE77T", "CHECKMATE-77T": "CHECKMATE77T", "CM77T": "CHECKMATE77T",
    "KEYNOTE 091": "KEYNOTE091", "KEYNOTE-091": "KEYNOTE091", "PEARLS": "KEYNOTE091",
    "IMPOWER 010": "IMPOWER010", "IMPOWER-010": "IMPOWER010",
    "KEYNOTE 024": "KEYNOTE024", "KEYNOTE-024": "KEYNOTE024",
    "KEYNOTE 189": "KEYNOTE189", "KEYNOTE-189": "KEYNOTE189",
    "KEYNOTE 407": "KEYNOTE407", "KEYNOTE-407": "KEYNOTE407",
    "CHECKMATE 9LA": "CHECKMATE9LA", "CHECKMATE-9LA": "CHECKMATE9LA",
    "PACIFIC-2": "PACIFIC2", "PACIFIC 2": "PACIFIC2",
    "RTOG 0617": "RTOG0617", "RTOG-0617": "RTOG0617",
    "FLAURA 2": "FLAURA2", "FLAURA-2": "FLAURA2",
}


#: Wrapper words a free-text reference may carry ("the CheckMate-816 trial").
_WRAPPER_WORDS = frozenset({"THE", "TRIAL", "STUDY", "REGIMEN", "PER", "试验", "研究"})

def _compact(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum())


def resolve_trial_id(reference: str) -> Optional[str]:
    """Resolve a free-text trial reference to a registry id, or None.

    Tolerates wrapper words ("the CheckMate-816 trial"), case, hyphens and
    spacing — a reference the resolver drops silently also drops the safety
    checks keyed on it, so resolution errs toward matching.
    """
    words = [w for w in str(reference).upper().replace("-", " ").split()
             if w not in _WRAPPER_WORDS]
    key = " ".join(words)
    if key in TRIALS_BY_ID:
        return key
    if key in _ALIASES:
        return _ALIASES[key]
    compact = _compact(key)
    if not compact:
        return None
    for tid in TRIALS_BY_ID:
        if compact == tid:
            return tid
    for alias, tid in _ALIASES.items():
        if compact == _compact(alias):
            return tid
    for trial in TRIALS:
        if trial.nct and compact == trial.nct.upper():
            return trial.trial_id
    return None


def search(query: str, *, limit: int = 5) -> list[Trial]:
    """Keyword search over the registry (name, keywords, setting, drivers)."""
    terms = [t for t in str(query).lower().replace(",", " ").split() if t]
    if not terms:
        return []
    scored: list[tuple[int, Trial]] = []
    for trial in TRIALS:
        haystack = " ".join((
            trial.trial_id.lower(), trial.name.lower(), trial.setting,
            trial.histology, (trial.driver_required or "").lower(),
            " ".join(trial.keywords).lower(), " ".join(trial.results).lower(),
            " ".join(sorted(trial.stage_groups)).lower(),
        ))
        score = sum(1 for term in terms if term in haystack)
        if score:
            scored.append((score, trial))
    scored.sort(key=lambda pair: (-pair[0], pair[1].trial_id))
    return [trial for _, trial in scored[:limit]]
