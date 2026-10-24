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

REGISTRY_VERSION = "2026-10"


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
    #: A pooled entry that enrolled either of two drivers names both
    #: ("ROS1|NTRK", STARTRK): either one covers the population.
    driver_required: Optional[str] = None
    #: Variant-class refinement of driver_required, where the enrollment was
    #: variant-defined: "egfr_ex19del_l858r" (FLAURA/ADAURA/LAURA/MARIPOSA),
    #: "egfr_exon20ins" (PAPILLON), "egfr_uncommon", "met_ex14",
    #: "braf_v600e", "kras_g12c", "erbb2_tkd" (HER2 tyrosine-kinase-domain
    #: mutations: Beamion LUNG-1, SOHO-01). None = any positive result of
    #: the gene qualifies.
    driver_class_required: Optional[str] = None
    #: TNM edition the trial enrolled under. Every registry entry to date
    #: predates AJCC/UICC 9 — stage-boundary checks back-map the current
    #: case to this edition before calling an application an extrapolation.
    tnm_edition: int = 8
    #: True when known EGFR/ALK alterations were excluded — the perioperative
    #: immunotherapy trials, EMPOWER-Lung 1 and IMpower110. The claim-level
    #: population check reads this.
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
            "driver_class_required": self.driver_class_required,
            "tnm_edition": self.tnm_edition,
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
#: "IB (≥4 cm)–IIIA" under AJCC 7 = 8th/9th-edition IIA–IIIA.
_EARLY_GE4CM = ("IIA", "IIB", "IIIA")
_RESECTABLE_II_IIIB = ("IIA", "IIB", "IIIA", "IIIB")
_STAGE_III = ("IIIA", "IIIB", "IIIC")
_STAGE_IV = ("IVA", "IVB")

TRIALS: tuple[Trial, ...] = (
    # ------------------------------------------------ adjuvant targeted therapy
    Trial(
        "ADAURA", "ADAURA (adjuvant osimertinib)", "NCT02511106", "adjuvant",
        _s(*_EARLY), histology="nonsquamous", driver_required="EGFR", driver_class_required="egfr_ex19del_l858r",
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
        _s(*_EARLY_GE4CM), driver_required="ALK",
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
        _s(*_EARLY_GE4CM), egfr_alk_excluded=True,
        regimen_ids=("nivo_chemo_neoadjuvant",),
        results=(
            "EFS HR 0.63 (97.38% CI 0.43–0.91); pCR 24.0% vs 2.2%",
            "Final OS analysis: OS HR 0.72 (95% CI 0.523–0.998); 5-year OS 65% vs 55%",
            "Exploratory: 5-year OS 95% with pCR vs 56% without",
            "No decrease in surgical feasibility (83% vs 75% resected)",
        ),
        source="NEJM 2022;386:1973 (EFS/pCR); NEJM 2025 final OS analysis",
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
        _s(*_EARLY_GE4CM),
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
        _s(*_STAGE_III), driver_required="EGFR", driver_class_required="egfr_ex19del_l858r",
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
        _s(*_STAGE_IV), driver_required="EGFR", driver_class_required="egfr_ex19del_l858r",
        regimen_ids=("osimertinib_first_line",),
        results=("PFS HR 0.46 (95% CI 0.37–0.57); OS HR 0.80 (95.05% CI 0.64–1.00)",
                 "CNS subset: CNS PFS HR 0.48 (95% CI 0.26–0.86); CNS ORR "
                 "66% vs 43% (measurable and/or non-measurable CNS lesions)"),
        source="NEJM 2018;378:113 (PFS); NEJM 2020;382:41 (OS); "
               "JCO 2018;36:3290 (CNS subset)",
        approval="FDA 2018-04 first-line osimertinib, metastatic EGFR ex19del/L858R "
                 "(no histology restriction in the label)",
        enrollment_note="Advanced/metastatic EGFR+, predominantly adenocarcinoma "
                        "enrolled; CNS-active",
        keywords=("osimertinib", "EGFR", "first line"),
    ),
    Trial(
        "FLAURA2", "FLAURA2 (osimertinib + platinum-pemetrexed)", "NCT04035486", "first_line",
        _s(*_STAGE_IV), histology="nonsquamous", driver_required="EGFR", driver_class_required="egfr_ex19del_l858r",
        regimen_ids=("osimertinib_chemo_first_line",),
        results=("PFS HR 0.62 (95% CI 0.49–0.79) vs osimertinib alone; higher toxicity",),
        source="NEJM 2023;389:1935",
        approval="FDA 2024-02 osimertinib + platinum-pemetrexed, metastatic EGFR+",
        enrollment_note="Option for high burden / CNS disease; weigh added chemo toxicity",
        keywords=("osimertinib", "chemotherapy", "combination"),
    ),
    Trial(
        "MARIPOSA", "MARIPOSA (amivantamab + lazertinib)", "NCT04487080", "first_line",
        _s(*_STAGE_IV), driver_required="EGFR", driver_class_required="egfr_ex19del_l858r",
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
        results=("PFS HR 0.28 (95% CI 0.19–0.41) at the interim analysis; "
                 "HR 0.27 (95% CI 0.18–0.39) at 3 years",
                 "5-year PFS 60% vs 8% (HR 0.19, 95% CI 0.13–0.27)",
                 "Intracranial response 82% vs 23% with baseline measurable "
                 "CNS disease"),
        source="NEJM 2020;383:2018 (interim); Lancet Respir Med 2023 "
               "(3-year); JCO 2024;42:3400 (5-year)",
        approval="FDA 2021-03 first-line lorlatinib, metastatic ALK+",
        enrollment_note="High CNS activity; CNS-penetrant; watch lipids and neurocognitive effects",
        keywords=("lorlatinib", "ALK", "first line"),
    ),
    Trial(
        "ALEX", "ALEX (first-line alectinib vs crizotinib)", "NCT02075840",
        "first_line",
        _s(*_STAGE_IV), driver_required="ALK",
        regimen_ids=("alectinib_first_line",),
        results=("Investigator-assessed PFS HR 0.47 (95% CI 0.34–0.65); "
                 "independent review 25.7 vs 10.4 months (HR 0.50, 95% CI "
                 "0.36–0.70)",
                 "CNS progression 12% vs 45% (cause-specific HR 0.16, 95% CI "
                 "0.10–0.28)",
                 "5-year OS 62.5% vs 45.5% (HR 0.67, 95% CI 0.46–0.98; "
                 "immature, 37% of events)"),
        source="NEJM 2017;377:829 (Peters et al.); Ann Oncol 2020 (Mok et "
               "al., 5-year update)",
        approval="FDA 2017-11 first-line alectinib, metastatic ALK+ NSCLC",
        enrollment_note="Untreated advanced ALK+; comparator crizotinib",
        caveats=("No head-to-head comparison with lorlatinib — CROWN and "
                 "ALEX each beat crizotinib; the choice between them is an "
                 "MDT question (CNS disease, toxicity profile)",),
        keywords=("alectinib", "ALK", "first line", "ALEX"),
    ),
    Trial(
        "ALTA1L", "ALTA-1L (first-line brigatinib vs crizotinib)",
        "NCT02737501", "first_line",
        _s(*_STAGE_IV), driver_required="ALK",
        regimen_ids=("brigatinib_first_line",),
        results=("Final analysis: independent-review PFS 24.0 vs 11.1 months "
                 "(HR 0.48, 95% CI 0.35–0.66)",
                 "OS HR 0.81 (95% CI 0.53–1.22), not significant",
                 "Baseline brain metastases: intracranial PFS 24.0 vs 5.5 "
                 "months (HR 0.29)"),
        source="ESMO 2021 final analysis; J Thorac Oncol 2021;16:2091 "
               "(Camidge et al., final results)",
        approval="FDA 2020-05 first-line brigatinib, metastatic ALK+ NSCLC",
        enrollment_note="ALK-inhibitor-naive advanced ALK+; up to one prior "
                        "chemotherapy allowed",
        caveats=("Avoid strong or moderate CYP3A inducers (label: rifampin "
                 "cuts brigatinib exposure by about 80%)",
                 "Early pulmonary events in the first week — the 7-day "
                 "lead-in dose exists for this"),
        keywords=("brigatinib", "ALK", "first line", "ALTA-1L"),
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
        results=("OS HR 0.69 (96.71% CI 0.55–0.87) at the interim analysis; "
                 "HR 0.66 (95% CI 0.55–0.80) with longer follow-up",),
        source="Lancet Oncol 2021;22:198",
        approval="FDA 2020-05 first-line, metastatic NSCLC without EGFR/ALK",
        keywords=("nivolumab", "ipilimumab", "dual immunotherapy"),
    ),
    Trial(
        "EMPOWER_LUNG1", "EMPOWER-Lung 1 (cemiplimab mono, PD-L1≥50%)",
        "NCT03088540", "first_line",
        _s(*_STAGE_IV), egfr_alk_excluded=True,
        regimen_ids=("cemiplimab_monotherapy",),
        results=("PD-L1 ≥50%: OS HR 0.57 (95% CI 0.42–0.77); PFS 8.2 vs "
                 "5.7 months (HR 0.54, 95% CI 0.43–0.68) vs platinum "
                 "doublet",),
        source="Lancet 2021;397:592 (Sezer et al.)",
        approval="FDA 2021-02 first-line cemiplimab, advanced NSCLC with "
                 "PD-L1 TPS ≥50% and no EGFR/ALK/ROS1 alteration",
        enrollment_note="PD-L1 ≥50%; EGFR/ALK/ROS1 excluded; crossover "
                        "allowed",
        caveats=("An alternative to pembrolizumab monotherapy in the same "
                 "population, not an addition to it",),
        keywords=("cemiplimab", "monotherapy", "PD-L1 high"),
    ),
    Trial(
        "IMPOWER110", "IMpower110 (atezolizumab mono, high PD-L1)",
        "NCT02409342", "first_line",
        _s(*_STAGE_IV), egfr_alk_excluded=True,
        regimen_ids=("atezolizumab_monotherapy",),
        results=("High PD-L1, EGFR/ALK wild-type: OS 20.2 vs 13.1 months "
                 "(HR 0.59, 95% CI 0.40–0.89) vs platinum-based "
                 "chemotherapy",),
        source="NEJM 2020;383:1328 (Herbst et al.)",
        approval="FDA 2020-05 first-line atezolizumab, metastatic NSCLC "
                 "with high PD-L1 (TC ≥50% or IC ≥10%, SP142 assay) and no "
                 "EGFR/ALK alteration",
        enrollment_note="PD-L1 ≥1% enrolled; the benefit and the approval "
                        "are the high-PD-L1 wild-type group",
        caveats=("High PD-L1 by immune-cell staining (IC ≥10%) also "
                 "qualifies under the label; this library models the "
                 "tumor-cell route only (TPS ≥50%)",),
        keywords=("atezolizumab", "monotherapy", "PD-L1 high"),
    ),
    Trial(
        "JMDB", "JMDB (cisplatin-pemetrexed vs cisplatin-gemcitabine)",
        "NCT00087711", "first_line",
        _s(*_STAGE_IV), histology="nonsquamous",
        regimen_ids=("pemetrexed_platinum_first_line",),
        results=("Overall non-inferior: OS 10.3 vs 10.3 months (HR 0.94, "
                 "95% CI 0.84–1.05)",
                 "Adenocarcinoma: OS 12.6 vs 10.9 months, favoring "
                 "pemetrexed; squamous: 10.8 vs 9.4 months, favoring "
                 "gemcitabine — the basis of histology-directed pemetrexed"),
        source="J Clin Oncol 2008;26:3543 (Scagliotti et al.)",
        approval="FDA 2008 pemetrexed + cisplatin, first-line locally "
                 "advanced or metastatic nonsquamous NSCLC",
        enrollment_note="Chemotherapy-naive stage IIIB–IV; pre-dates "
                        "immunotherapy — today the chemotherapy-only "
                        "backbone for patients who cannot receive a "
                        "checkpoint inhibitor",
        caveats=("Cisplatin was the partner; carboplatin substitution is "
                 "an MDT/pharmacist decision",),
        keywords=("pemetrexed", "cisplatin", "nonsquamous", "chemotherapy"),
    ),
    Trial(
        "PARAMOUNT", "PARAMOUNT (pemetrexed continuation maintenance)",
        "NCT00789373", "first_line",
        _s(*_STAGE_IV), histology="nonsquamous",
        regimen_ids=("pemetrexed_platinum_first_line",),
        results=("Maintenance pemetrexed after 4 cycles of "
                 "cisplatin-pemetrexed: PFS HR 0.62 (95% CI 0.49–0.79); OS "
                 "13.9 vs 11.0 months (HR 0.78, 95% CI 0.64–0.96)",),
        source="J Clin Oncol 2013;31:2895 (Paz-Ares et al., final OS)",
        approval="FDA 2012-10 label update: continuation maintenance "
                 "pemetrexed after four cycles of platinum-based first-line "
                 "chemotherapy, nonsquamous NSCLC",
        enrollment_note="No progression and ECOG 0–1 after 4 induction "
                        "cycles",
        keywords=("pemetrexed", "maintenance", "nonsquamous"),
    ),
    Trial(
        "CA031", "CA031 (carboplatin + nab-paclitaxel vs paclitaxel)",
        "NCT00540514", "first_line",
        _s(*_STAGE_IV),
        regimen_ids=("carbo_nab_paclitaxel_first_line",),
        results=("ORR 33% vs 25% (squamous 41% vs 24%); PFS 6.3 vs 5.8 and "
                 "OS 12.1 vs 11.2 months, neither significant",),
        source="J Clin Oncol 2012;30:2055 (Socinski et al.)",
        approval="FDA 2012-10 nab-paclitaxel + carboplatin, first-line "
                 "locally advanced or metastatic NSCLC",
        enrollment_note="Untreated stage IIIB–IV, all histologies; "
                        "pre-dates immunotherapy — today the "
                        "chemotherapy-only option for squamous disease when "
                        "a checkpoint inhibitor cannot be given",
        keywords=("nab-paclitaxel", "carboplatin", "squamous",
                  "chemotherapy"),
    ),
    # -------------------------- stage IV, driver-directed beyond EGFR/ALK
    Trial(
        "PAPILLON", "PAPILLON (amivantamab + chemo, EGFR exon20 insertion)",
        "NCT04538664", "first_line",
        _s(*_STAGE_IV), histology="nonsquamous", driver_required="EGFR",
        driver_class_required="egfr_exon20ins",
        regimen_ids=("amivantamab_chemo_first_line",),
        results=("PFS HR 0.40 (95% CI 0.30–0.53) vs chemotherapy alone",),
        source="NEJM 2023;389:2039",
        approval="FDA 2024-03 amivantamab-vmjw + carboplatin-pemetrexed, "
                 "first-line metastatic EGFR exon 20 insertion NSCLC",
        enrollment_note="EGFR exon 20 insertion ONLY — classical-sensitizing "
                        "EGFR is a different population (FLAURA)",
        caveats=("Osimertinib is NOT standard for exon 20 insertions",),
        keywords=("amivantamab", "exon 20 insertion", "EGFR"),
    ),
    Trial(
        "LUXLUNG_UNCOMMON", "LUX-Lung 2/3/6 pooled (afatinib, uncommon EGFR)",
        "NCT00525148", "first_line",
        _s(*_STAGE_IV), driver_required="EGFR",
        driver_class_required="egfr_uncommon",
        regimen_ids=("afatinib_uncommon_first_line",),
        results=("Pooled ORR ~71% for G719X/L861Q/S768I; markedly lower "
                 "activity in exon 20 insertions and de novo T790M",),
        source="Lancet Oncol 2015;16:830 (pooled post-hoc)",
        approval="FDA 2018-01 afatinib label broadened to "
                 "G719X/L861Q/S768I (non-resistant uncommon mutations)",
        enrollment_note="Uncommon-sensitizing subgroup analysis — "
                        "prospective evidence is thinner than FLAURA",
        caveats=("Osimertinib has separate uncommon-mutation data; either is "
                 "reasonable — but neither equals the FLAURA population",),
        keywords=("afatinib", "uncommon", "G719X", "L861Q", "S768I"),
    ),
    Trial(
        "TRIDENT1", "TRIDENT-1 (repotrectinib, ROS1 fusion)",
        "NCT03093116", "first_line",
        _s(*_STAGE_IV), driver_required="ROS1",
        regimen_ids=("repotrectinib_first_line",),
        results=("ORR 79% TKI-naïve (durable, CNS-active); ORR 38% "
                 "TKI-pretreated",),
        source="NEJM 2024;390:118",
        approval="FDA 2023-11 repotrectinib, locally advanced/metastatic "
                 "ROS1+ NSCLC; taletrectinib approved 2025-06 "
                 "(entrectinib/crizotinib remain options)",
        enrollment_note="ROS1 fusion required; ICI monotherapy is not a "
                        "substitute regardless of PD-L1",
        keywords=("repotrectinib", "ROS1", "fusion", "taletrectinib"),
    ),
    Trial(
        "TRUST", "TRUST-I/TRUST-II (taletrectinib, ROS1 fusion)",
        "NCT04919811", "first_line",
        _s(*_STAGE_IV), driver_required="ROS1",
        regimen_ids=("taletrectinib_first_line",),
        results=("TKI-naive ORR 90% (95% CI 83–95; TRUST-I) and 85% (73–93; "
                 "TRUST-II)",
                 "After one prior ROS1 TKI: ORR 52% (39–64; TRUST-I) and 62% "
                 "(46–75; TRUST-II)"),
        source="J Clin Oncol 2025;43:1920 (pooled TRUST-I/II); FDA approval "
               "summary 2025-06",
        approval="FDA 2025-06 taletrectinib, locally advanced or metastatic "
                 "ROS1+ NSCLC (TKI-naive or after a ROS1 TKI)",
        enrollment_note="TRUST-I (NCT04395677, China) and TRUST-II "
                        "(NCT04919811, global); single-arm phase 2",
        caveats=("Single-arm response-rate evidence — no randomized "
                 "comparison with other ROS1 TKIs",),
        keywords=("taletrectinib", "ROS1", "fusion", "TRUST"),
    ),
    Trial(
        "PROFILE1001", "PROFILE 1001 (crizotinib, ROS1 cohort)",
        "NCT00585195", "first_line",
        _s(*_STAGE_IV), driver_required="ROS1",
        regimen_ids=("crizotinib_ros1_first_line",),
        results=("ORR 66% (95% CI 51–79) by independent review, n=50 (the "
                 "label analysis)",
                 "Updated: ORR 72% (95% CI 58–83), median PFS 19.3 months, "
                 "n=53"),
        source="NEJM 2014;371:1963 (Shaw et al.); Ann Oncol 2019;30:1121 "
               "(updated survival)",
        approval="FDA 2016-03 crizotinib, metastatic ROS1+ NSCLC",
        enrollment_note="Expansion cohort of a phase 1 study; ROS1 by FISH "
                        "mostly",
        caveats=("Limited CNS activity compared with the newer ROS1 TKIs",),
        keywords=("crizotinib", "ROS1", "fusion", "PROFILE 1001"),
    ),
    Trial(
        "STARTRK2", "STARTRK-2 with ALKA-372-001/STARTRK-1 (entrectinib, "
        "ROS1 or NTRK fusion)", "NCT02568267", "first_line",
        _s(*_STAGE_IV), driver_required="ROS1|NTRK",
        regimen_ids=("entrectinib_ros1_first_line",
                     "entrectinib_ntrk_first_line"),
        results=("ROS1 NSCLC (integrated analysis, n=53): ORR 77% (95% CI "
                 "64–88), median duration of response 24.6 months; "
                 "intracranial response 55% (11/20)",
                 "NTRK fusion solid tumors (n=54): ORR 57%, median duration "
                 "of response 10.4 months"),
        source="Lancet Oncol 2020;21:261 (Drilon et al., ROS1); FDA approval "
               "summary 2019-08 (NTRK)",
        approval="FDA 2019-08 entrectinib: metastatic ROS1+ NSCLC; NTRK "
                 "fusion+ solid tumors (accelerated)",
        enrollment_note="Pooled ALKA-372-001, STARTRK-1 (NCT02097810) and "
                        "STARTRK-2; TKI-naive for the ROS1 efficacy "
                        "population",
        caveats=("Label: assess QT interval and electrolytes before "
                 "starting",
                 "No responses after prior crizotinib in an early phase 1 "
                 "series (0/6) — a small sample, but entrectinib is not the "
                 "post-crizotinib answer"),
        keywords=("entrectinib", "ROS1", "NTRK", "fusion", "STARTRK"),
    ),
    Trial(
        "LIBRETTO431", "LIBRETTO-431 (selpercatinib, RET fusion)",
        "NCT04194944", "first_line",
        _s(*_STAGE_IV), driver_required="RET",
        regimen_ids=("selpercatinib_first_line",),
        results=("PFS 24.8 vs 11.2 months, HR 0.46 (95% CI 0.31–0.70) vs "
                 "chemo±pembrolizumab (pembrolizumab-ITT population, interim "
                 "analysis); CNS-active",),
        source="NEJM 2023;389:1839 (LIBRETTO-431)",
        approval="FDA regular approval 2022-09 selpercatinib, RET fusion+ "
                 "NSCLC (accelerated 2020); pralsetinib per ARROW is the "
                 "alternative",
        enrollment_note="Randomized against chemo±IO — directly answers the "
                        "'high PD-L1, RET+' question in favor of the TKI",
        keywords=("selpercatinib", "RET", "fusion"),
    ),
    Trial(
        "ARROW", "ARROW (pralsetinib, RET fusion)", "NCT03037385",
        "first_line",
        _s(*_STAGE_IV), driver_required="RET",
        regimen_ids=("pralsetinib_first_line",),
        results=("Treatment-naive ORR 78% (95% CI 68–85; n=107), median "
                 "duration of response 13.4 months",
                 "Platinum-pretreated ORR 63% (95% CI 54–71; n=130), median "
                 "duration of response 38.8 months"),
        source="Lancet Oncol 2021;22:959 (Gainor et al., interim); FDA "
               "regular-approval summary 2023-08",
        approval="FDA 2023-08 regular approval (accelerated 2020-09) "
                 "pralsetinib, metastatic RET fusion+ NSCLC",
        enrollment_note="Single-arm phase 1/2; no randomized comparison "
                        "with chemotherapy (contrast LIBRETTO-431)",
        keywords=("pralsetinib", "RET", "fusion", "ARROW"),
    ),
    Trial(
        "GEOMETRY", "GEOMETRY mono-1 (capmatinib, MET exon 14 skipping)",
        "NCT02414139", "first_line",
        _s(*_STAGE_IV), driver_required="MET",
        driver_class_required="met_ex14",
        regimen_ids=("capmatinib_first_line",),
        results=("ORR 68% treatment-naïve, 41% pretreated (MET ex14)",),
        source="NEJM 2020;383:944",
        approval="FDA 2020-05 capmatinib, metastatic MET exon 14 skipping "
                 "NSCLC (tepotinib per VISION is the alternative)",
        enrollment_note="MET exon 14 skipping only — MET amplification is a "
                        "different (unproven first-line) question",
        keywords=("capmatinib", "MET", "exon 14", "tepotinib"),
    ),
    Trial(
        "VISION", "VISION (tepotinib, MET exon 14 skipping)", "NCT02864992",
        "first_line",
        _s(*_STAGE_IV), driver_required="MET",
        driver_class_required="met_ex14",
        regimen_ids=("tepotinib_first_line",),
        results=("Treatment-naive ORR 57% (95% CI 49–65; n=164)",
                 "Previously treated ORR 45% (95% CI 37–53; n=149)"),
        source="NEJM 2020;383:931 (Paik et al.); FDA traditional-approval "
               "summary 2024-02",
        approval="FDA 2024-02 traditional approval (accelerated 2021-02) "
                 "tepotinib, metastatic MET exon 14 skipping NSCLC",
        enrollment_note="MET exon 14 skipping only (tissue or plasma)",
        keywords=("tepotinib", "MET", "exon 14", "VISION"),
    ),
    Trial(
        "BRF113928", "BRF113928 (dabrafenib + trametinib, BRAF V600E)",
        "NCT01336634", "first_line",
        _s(*_STAGE_IV), driver_required="BRAF",
        driver_class_required="braf_v600e",
        regimen_ids=("dabrafenib_trametinib_first_line",),
        results=("ORR 64% (23/36) treatment-naïve; median duration of "
                 "response 10.4 mo; median PFS ~10.8 mo",
                 "Median OS 17.3 mo in the treatment-naïve cohort (updated "
                 "analysis)"),
        source="Lancet Oncol 2017;18:1307; J Thorac Oncol 2022;17:103 "
               "(updated survival)",
        approval="FDA 2017-06 dabrafenib + trametinib, metastatic BRAF "
                 "V600E NSCLC (encorafenib + binimetinib per PHAROS is the "
                 "alternative)",
        enrollment_note="V600E only — non-V600 BRAF is not an indication",
        keywords=("dabrafenib", "trametinib", "BRAF", "V600E"),
    ),
    Trial(
        "PHAROS", "PHAROS (encorafenib + binimetinib, BRAF V600E)",
        "NCT03915951", "first_line",
        _s(*_STAGE_IV), driver_required="BRAF",
        driver_class_required="braf_v600e",
        regimen_ids=("encorafenib_binimetinib_first_line",),
        results=("Treatment-naive ORR 75% (95% CI 62–85; n=59)",
                 "Previously treated ORR 46% (95% CI 30–63; n=39), median "
                 "duration of response 16.7 months"),
        source="J Clin Oncol 2023;41:3700 (Riely et al.)",
        approval="FDA 2023-10 encorafenib + binimetinib, metastatic BRAF "
                 "V600E NSCLC",
        enrollment_note="Single-arm phase 2; prior BRAF or MEK inhibitor "
                        "not allowed",
        caveats=("Binimetinib: LVEF before starting (safety not established "
                 "below 50%); encorafenib: dose-dependent QTc prolongation",),
        keywords=("encorafenib", "binimetinib", "BRAF", "V600E", "PHAROS"),
    ),
    Trial(
        "NAVIGATE", "NAVIGATE/pooled (larotrectinib, NTRK fusion)",
        "NCT02576431", "first_line",
        _s(*_STAGE_IV), driver_required="NTRK",
        regimen_ids=("larotrectinib_first_line",),
        results=("Tumor-agnostic pooled ORR ~75%; durable, CNS-active "
                 "(entrectinib per STARTRK is the alternative)",),
        source="NEJM 2018;378:731; Lancet Oncol 2020;21:531",
        approval="FDA 2018-11 larotrectinib, NTRK fusion+ solid tumors "
                 "(tumor-agnostic)",
        keywords=("larotrectinib", "NTRK", "fusion", "entrectinib"),
    ),
    Trial(
        "BEAMION_LUNG1", "Beamion LUNG-1 (zongertinib, HER2 TKD mutation)",
        "NCT04886804", "first_line",
        _s(*_STAGE_IV), histology="nonsquamous", driver_required="ERBB2",
        driver_class_required="erbb2_tkd",
        regimen_ids=("zongertinib_her2_tkd",),
        results=("Treatment-naive TKD-mutant (n=72): ORR 76% (95% CI "
                 "65–85); 64% of responders ≥6 months, 44% ≥12 months",
                 "Previously treated with chemotherapy, no HER2-directed "
                 "therapy (n=71): ORR 75%",
                 "Non-TKD HER2 mutations (n=20): ORR 30% — outside the "
                 "label"),
        source="NEJM 2025;392:2321 (Heymach et al., previously treated); "
               "NEJM 2026 (doi:10.1056/NEJMoa2516969, first line); FDA "
               "approval summaries 2025-08 and 2026-02",
        approval="FDA 2025-08 (previously treated) and 2026-02 (first "
                 "line) accelerated: zongertinib, unresectable or "
                 "metastatic non-squamous NSCLC with HER2 TKD-activating "
                 "mutations",
        enrollment_note="HER2 tyrosine-kinase-domain mutations "
                        "(e.g. exon 20 insertions); HER2 amplification or "
                        "overexpression is a different population",
        caveats=("Accelerated approval on response rate — confirmatory "
                 "trial pending",
                 "Label warnings: hepatotoxicity, left-ventricular "
                 "dysfunction (LVEF before starting), ILD/pneumonitis"),
        keywords=("zongertinib", "HER2", "ERBB2", "TKD", "Beamion"),
    ),
    Trial(
        "SOHO01", "SOHO-01 (sevabertinib, HER2 TKD mutation)",
        "NCT05099172", "first_line",
        _s(*_STAGE_IV), histology="nonsquamous", driver_required="ERBB2",
        driver_class_required="erbb2_tkd",
        regimen_ids=("sevabertinib_her2_tkd",),
        results=("Treatment-naive (n=69): ORR 75% (95% CI 64–85); 73% of "
                 "responders ≥6 months, 38% ≥12 months",
                 "Previously treated, no prior HER2-directed therapy (cohort "
                 "D, n=81): ORR 64% (95% CI 53–75), median PFS 8.3 months"),
        source="NEJM 2025 (Le et al.; doi:10.1056/NEJMoa2511065); FDA "
               "approval summaries 2025-11 and 2026-09",
        approval="FDA 2025-11 (previously treated) and 2026-09 (first "
                 "line) accelerated: sevabertinib, locally advanced or "
                 "metastatic non-squamous NSCLC with HER2 TKD-activating "
                 "mutations",
        enrollment_note="HER2 tyrosine-kinase-domain mutations; phase 1/2, "
                        "single-arm cohorts",
        caveats=("Accelerated approval on response rate — confirmatory "
                 "trial pending",
                 "Label warnings: diarrhea, hepatotoxicity, ILD/pneumonitis, "
                 "left-ventricular dysfunction"),
        keywords=("sevabertinib", "HER2", "ERBB2", "TKD", "SOHO"),
    ),
    Trial(
        "DESTINY_LUNG02", "DESTINY-Lung02 (T-DXd, HER2-mutant, later line)",
        "NCT04644237", "subsequent",
        _s(*_STAGE_IV), driver_required="ERBB2",
        regimen_ids=("tdxd_subsequent_line",),
        results=("ORR ~49% at 5.4 mg/kg in previously treated "
                 "HER2-mutant NSCLC; ILD is the key toxicity",),
        source="JCO 2023;41:4852",
        approval="FDA 2022-08 accelerated: trastuzumab deruxtecan, "
                 "previously treated HER2-mutant NSCLC",
        enrollment_note="LATER LINE — do not front-load T-DXd; first line "
                        "for HER2-mutant disease is chemo±IO, with a HER2 "
                        "TKI (zongertinib/sevabertinib, accelerated "
                        "approvals) for non-squamous TKD-activating "
                        "mutations",
        keywords=("trastuzumab deruxtecan", "T-DXd", "HER2", "ERBB2"),
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
    # ------------------------------------------------ later-line sequencing
    Trial(
        "MARIPOSA2", "MARIPOSA-2 (amivantamab + chemo post-osimertinib)",
        "NCT04988295", "subsequent",
        _s(*_STAGE_IV), driver_required="EGFR",
        driver_class_required="egfr_ex19del_l858r",
        regimen_ids=("amivantamab_chemo_subsequent",),
        results=("PFS 6.3 vs 4.2 months, HR 0.48 (95% CI 0.36–0.64) vs "
                 "chemotherapy alone after osimertinib progression; OS not "
                 "significant at the second interim analysis",),
        source="Ann Oncol 2024;35:77",
        approval="FDA 2024-09 amivantamab + carboplatin-pemetrexed, EGFR "
                 "ex19del/L858R after progression on an EGFR TKI",
        enrollment_note="AFTER osimertinib progression — not first line; "
                        "watch infusion reactions and VTE",
        keywords=("amivantamab", "MARIPOSA-2", "post-osimertinib",
                  "resistance"),
    ),
    Trial(
        "KEYNOTE789", "KEYNOTE-789 (chemo ± pembrolizumab post-TKI)",
        "NCT03515837", "subsequent",
        _s(*_STAGE_IV), histology="nonsquamous", driver_required="EGFR",
        regimen_ids=("platinum_pemetrexed_post_tki",),
        results=("NEGATIVE for the IO question: adding pembrolizumab to "
                 "chemotherapy post-TKI did not significantly improve PFS "
                 "or OS (final OS 15.9 vs 14.7 months, HR 0.84, 95% CI "
                 "0.69–1.02; prespecified boundary not met)",),
        source="J Clin Oncol 2024 (Yang et al.; doi:10.1200/JCO.23.02747)",
        approval="Cited as the evidence that chemo-IO is NOT the default "
                 "after EGFR-TKI progression; the chemo backbone itself "
                 "is the standard arm",
        enrollment_note="EGFR-mutant, progression on prior EGFR TKI",
        keywords=("KEYNOTE-789", "post-TKI", "chemotherapy",
                  "negative trial"),
    ),
    Trial(
        "LORLATINIB_P2", "Lorlatinib phase 2 (post second-generation ALK)",
        "NCT01970865", "subsequent",
        _s(*_STAGE_IV), driver_required="ALK",
        regimen_ids=("lorlatinib_post_second_gen",),
        results=("ORR ~40% and intracranial ORR ~57% after ≥1 "
                 "second-generation ALK TKI (pooled EXP cohorts)",),
        source="Lancet Oncol 2018;19:1654",
        approval="FDA 2018-11 lorlatinib, ALK+ after progression on "
                 "alectinib/ceritinib (or crizotinib plus one other)",
        enrollment_note="Post second-generation TKI cohorts — distinct "
                        "from the CROWN first-line population",
        keywords=("lorlatinib", "ALK", "resistance", "later line"),
    ),
    Trial(
        "REVEL", "REVEL (docetaxel ± ramucirumab, second line)",
        "NCT01168973", "subsequent",
        _s(*_STAGE_IV),
        regimen_ids=("docetaxel_ramucirumab_second_line",
                     "docetaxel_second_line"),
        results=("OS 10.5 vs 9.1 months (HR 0.86, 95% CI 0.75–0.98) for "
                 "docetaxel + ramucirumab vs docetaxel, all histologies",),
        source="Lancet 2014;384:665",
        approval="FDA 2014-12 ramucirumab + docetaxel, metastatic NSCLC "
                 "with progression on or after platinum-based therapy",
        enrollment_note="Second line after platinum-based therapy; "
                        "pre-dates the chemo-IO first-line era — applied "
                        "today after chemo-IO progression by convention",
        caveats=("Enrolled before first-line chemo-IO was standard — the "
                 "post-IO second-line population is an accepted "
                 "convention, not the enrolled one",),
        keywords=("docetaxel", "ramucirumab", "REVEL", "second line"),
    ),
    Trial(
        "AURA3", "AURA3 (osimertinib vs platinum-pemetrexed, acquired "
        "T790M)", "NCT02151981", "subsequent",
        _s(*_STAGE_IV), driver_required="EGFR",
        driver_class_required="egfr_t790m",
        regimen_ids=("osimertinib_t790m_subsequent",),
        results=("PFS 10.1 vs 4.4 months (HR 0.30, 95% CI 0.23–0.41) "
                 "over platinum-pemetrexed",),
        source="NEJM 2017;376:629",
        approval="FDA 2015-11 accelerated / 2017-03 regular: osimertinib, "
                 "EGFR T790M after EGFR-TKI progression",
        enrollment_note="Acquired T790M after progression on a "
                        "first/second-generation EGFR TKI",
        keywords=("osimertinib", "T790M", "AURA3", "resistance"),
    ),
    Trial(
        "MARCOUX_SERIES",
        "Marcoux et al. (SCLC-transformed EGFR-mutant NSCLC, "
        "retrospective)", "N/A (retrospective)", "subsequent",
        _s(*_STAGE_IV), driver_required="EGFR",
        regimen_ids=("platinum_etoposide_transformation",),
        results=("Retrospective multicenter series (n=67): "
                 "platinum-etoposide response ~54% in transformed "
                 "disease; median OS from transformation ~10.9 months",),
        source="JCO 2019;37:278",
        approval="NOT a trial — retrospective evidence; the standard is "
                 "extrapolated from SCLC practice and framed by the "
                 "thoracic tumor board",
        enrollment_note="Histologically confirmed small-cell "
                        "transformation required — a re-biopsy finding, "
                        "never inferred from imaging",
        caveats=("Retrospective series, not a randomized standard — the "
                 "evidence grade is stated, not laundered",),
        keywords=("small-cell transformation", "SCLC", "etoposide",
                  "Marcoux"),
    ),
    Trial(
        "INSIGHT2", "INSIGHT 2 (tepotinib + osimertinib, MET-amplified "
        "resistance)", "NCT03940703", "subsequent",
        _s(*_STAGE_IV), driver_required="EGFR",
        regimen_ids=("tepotinib_osimertinib_met_amp",),
        results=("Phase 2: ORR ~50% (FISH MET-amplified cohort) after "
                 "first-line osimertinib progression",),
        source="Lancet Oncol 2024;25:989 (Wu et al.; "
               "doi:10.1016/S1470-2045(24)00270-5)",
        approval="NOT approved — phase 2 evidence; guideline-listed "
                 "option with MDT/trial framing",
        enrollment_note="MET amplification on the progression biopsy "
                        "required; osimertinib CONTINUES — the "
                        "same-drug flag is by design",
        caveats=("Phase 2 single-arm — frame as mechanism-directed "
                 "option, not standard of care",),
        keywords=("tepotinib", "MET amplification", "INSIGHT",
                  "resistance", "osimertinib"),
    ),
    Trial(
        "TROPION_LUNG05", "TROPION-Lung05 (datopotamab deruxtecan, "
        "EGFR-mutant, later line)", "NCT04484142", "subsequent",
        _s(*_STAGE_IV), driver_required="EGFR",
        regimen_ids=("dato_dxd_egfr_subsequent",),
        results=("ORR ~45% in EGFR-mutant NSCLC after EGFR-directed "
                 "therapy and platinum-based chemotherapy (pooled with "
                 "TROPION-Lung01 for the approval)",),
        source="JCO 2025 (TROPION-Lung05); FDA review of pooled "
               "TROPION-Lung05/01",
        approval="FDA 2025-06 accelerated: datopotamab deruxtecan, "
                 "EGFR-mutant NSCLC after prior EGFR-directed therapy "
                 "AND platinum-based chemotherapy",
        enrollment_note="Requires BOTH prior EGFR-directed therapy and "
                        "prior platinum — a third-line option, never "
                        "earlier; stomatitis and ILD are the key "
                        "toxicities",
        caveats=("Accelerated approval on response rate — confirmatory "
                 "data pending",),
        keywords=("datopotamab", "Dato-DXd", "TROP2", "ADC", "EGFR"),
    ),
    Trial(
        "CODEBREAK100", "CodeBreaK 100 (sotorasib, KRAS G12C, later line)",
        "NCT03600883", "subsequent",
        _s(*_STAGE_IV), driver_required="KRAS",
        driver_class_required="kras_g12c",
        regimen_ids=("sotorasib_subsequent_line",),
        results=("ORR 37.1%, median PFS 6.8 months in previously treated "
                 "KRAS G12C NSCLC (phase 2)",),
        source="NEJM 2021;384:2371",
        approval="FDA 2021-05 accelerated: sotorasib, KRAS G12C NSCLC "
                 "after at least one prior systemic therapy",
        enrollment_note="LATER LINE — first-line for KRAS G12C remains "
                        "chemo±IO; adagrasib (KRYSTAL-1/KRYSTAL-12) is the "
                        "alternative",
        keywords=("sotorasib", "KRAS", "G12C", "CodeBreaK"),
    ),
    Trial(
        "KRYSTAL1", "KRYSTAL-1 (adagrasib, KRAS G12C, later line)",
        "NCT03785249", "subsequent",
        _s(*_STAGE_IV), driver_required="KRAS",
        driver_class_required="kras_g12c",
        regimen_ids=("adagrasib_subsequent_line",),
        results=("ORR 42.9% (48/112), median duration of response 8.5 "
                 "months, median PFS 6.5 months, median OS 12.6 months "
                 "after platinum chemotherapy and a PD-(L)1 inhibitor",),
        source="NEJM 2022;387:120 (Jänne et al.)",
        approval="FDA 2022-12 accelerated: adagrasib, KRAS G12C NSCLC after "
                 "at least one prior systemic therapy",
        enrollment_note="LATER LINE — first line for KRAS G12C remains "
                        "chemo±IO",
        caveats=("Label: QTc prolongation; avoid strong CYP3A4 inhibitors "
                 "until steady state",),
        keywords=("adagrasib", "KRAS", "G12C", "KRYSTAL"),
    ),
    Trial(
        "KRYSTAL12", "KRYSTAL-12 (adagrasib vs docetaxel, KRAS G12C)",
        "NCT04685135", "subsequent",
        _s(*_STAGE_IV), driver_required="KRAS",
        driver_class_required="kras_g12c",
        regimen_ids=("adagrasib_subsequent_line",),
        results=("PFS 5.5 vs 3.8 months (HR 0.58, 95% CI 0.45–0.76) and ORR "
                 "32% vs 9% over docetaxel after platinum chemotherapy and "
                 "a PD-(L)1 inhibitor",),
        source="Lancet 2025;406:615 (Barlesi et al.)",
        approval="Phase 3 confirmatory trial for the 2022 accelerated "
                 "approval",
        enrollment_note="Previously treated KRAS G12C; randomized 2:1",
        keywords=("adagrasib", "KRAS", "G12C", "KRYSTAL-12", "docetaxel"),
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
    "TRUST I": "TRUST", "TRUST II": "TRUST", "TRUST 1": "TRUST",
    "TRUST 2": "TRUST", "NCT04395677": "TRUST",
    "STARTRK": "STARTRK2", "STARTRK 1": "STARTRK2", "ALKA 372 001": "STARTRK2",
    "NCT02097810": "STARTRK2",
    "SOHO 1": "SOHO01",
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
        if compact == _compact(tid):  # "DESTINY-Lung02" → DESTINY_LUNG02
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


# ---------------------------------------------------------------------------
# Population entailment (claim-level semantic check)
# ---------------------------------------------------------------------------

#: driver_class_required → (signature gene, tags that satisfy it, tags
#: whose co-occurrence removes the case from that enrollment population
#: even when a satisfying tag is present — the egfr_classical discipline).
_CLASS_CHECKS: dict = {
    "egfr_ex19del_l858r": ("egfr", frozenset({"ex19del", "l858r"}),
                           frozenset({"exon20ins", "c797s"})),
    "egfr_exon20ins": ("egfr", frozenset({"exon20ins"}), frozenset()),
    "egfr_uncommon": ("egfr", frozenset({"g719x", "l861q", "s768i"}),
                      frozenset({"exon20ins", "c797s"})),
    "met_ex14": ("met", frozenset({"ex14_skipping"}), frozenset()),
    "braf_v600e": ("braf", frozenset({"v600e"}), frozenset()),
    "kras_g12c": ("kras", frozenset({"g12c"}), frozenset()),
    "egfr_t790m": ("egfr", frozenset({"t790m"}),
                   frozenset({"exon20ins", "c797s"})),
    "erbb2_tkd": ("erbb2", frozenset({"tkd"}), frozenset()),
}

#: trial histology restriction → subject histologies that contradict it.
#: Unknown/NOS histology contradicts nothing here — unknowns are the
#: indication layer's workup problem, not a citation mismatch.
_HISTOLOGY_CONFLICTS = {
    "nonsquamous": {"squamous"},
    "squamous": {"adenocarcinoma", "non_squamous"},
}


def population_mismatches(trial: dict, subject: dict, *,
                          declared_extrapolations=frozenset()) -> list[str]:
    """Why this trial row does NOT cover the claim's population.

    Empty list = covered. Dict-in/dict-out so it grades serialized ledger
    rows and parallel-wave buffer rows alike; every check is skipped when
    the subject does not carry the corresponding population field (a
    hand-built claim without population facts is graded structurally
    only). The stage check is edition-aware — the same 8th-edition
    back-mapping the plan rule uses — and a stage-only gap for a trial
    declared as an extrapolation is honored here too: declared at plan
    level means warned at plan level, not re-flagged per claim. Driver
    and histology mismatches are never excused by a stage declaration.
    """
    reasons: list[str] = []
    trial_id = str(trial.get("trial_id") or "")

    stage = str(subject.get("population_stage") or "")
    groups = set(trial.get("stage_groups") or [])
    if stage and groups and stage not in groups:
        legacy = None
        if int(trial.get("tnm_edition") or 8) == 8:
            from ..staging.legacy8 import eighth_edition_group

            tnm = subject.get("population_tnm") or {}
            legacy = eighth_edition_group(
                tnm.get("t"), tnm.get("n"), tnm.get("m"))
        if legacy not in groups and trial_id not in declared_extrapolations:
            reasons.append(
                f"stage {stage} outside enrolled "
                f"{'/'.join(sorted(groups))}")

    signature = subject.get("population_drivers")
    if isinstance(signature, dict):
        check = _CLASS_CHECKS.get(str(trial.get("driver_class_required")
                                      or ""))
        if check:
            gene, satisfying, disqualifying = check
            tags = set(signature.get(gene) or [])
            if not (tags & satisfying) or (tags & disqualifying):
                reasons.append(
                    f"population ({'/'.join(sorted(tags)) or f'no {gene}'}) "
                    f"is not the {trial['driver_class_required']} "
                    f"enrollment class")
        elif trial.get("driver_required"):
            genes = [g.strip().lower()
                     for g in str(trial["driver_required"]).split("|")]
            if not any(gene in signature for gene in genes):
                shown = str(trial["driver_required"]).replace("|", "/")
                reasons.append(f"population is not {shown}-positive")
        if trial.get("egfr_alk_excluded") \
                and ({"egfr", "alk"} & set(signature)):
            reasons.append("the trial excluded EGFR/ALK-altered disease")

    histology = str(subject.get("population_histology") or "").lower()
    conflicts = _HISTOLOGY_CONFLICTS.get(
        str(trial.get("histology") or "any"))
    if histology and conflicts and histology in conflicts:
        reasons.append(
            f"{histology} histology cited on a "
            f"{trial.get('histology')}-only trial")
    return reasons
