"""Regimen library — the deterministic dose channel.

The YaoBi lesson, transplanted to oncology: **the model never originates a
dose.** Systemic-therapy dosing in NSCLC is standardized per label, so doses
live here as data, keyed by ``regimen_id``. Reasoning agents see regimens
*without numerics* (``summary()``); the dose-bearing form (``detail()``) is
reachable only through the deterministic dose-planning path, which also runs
the renal/hepatic/interaction gates. The tool loop rejects any model output
carrying a dose pattern, so a number that never passed those gates cannot
appear in a released plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

LIBRARY_VERSION = "2026-08"


@dataclass(frozen=True)
class Component:
    drug: str
    dose: str            # numeric dosing string — only exposed via detail()
    route: str = "IV"
    schedule: str = ""
    duration: str = ""
    notes: str = ""


@dataclass(frozen=True)
class Regimen:
    regimen_id: str
    name: str
    setting: str
    components: tuple[Component, ...]
    #: Trial ids anchoring this regimen (must exist in the trial registry).
    trial_ids: tuple[str, ...] = ()
    #: True when the regimen contains an immune checkpoint inhibitor — the
    #: EGFR/ALK exclusion and ILD/autoimmune caution rules read this.
    contains_ici: bool = False
    monitoring: tuple[str, ...] = ()
    dose_gates: tuple[str, ...] = ()
    label_note: str = ""

    def summary(self) -> dict:
        """Numeric-free view for reasoning agents: drugs, schedule shape, gates."""
        return {
            "regimen_id": self.regimen_id,
            "name": self.name,
            "setting": self.setting,
            "drugs": [c.drug for c in self.components],
            "contains_ici": self.contains_ici,
            "trial_ids": list(self.trial_ids),
            "monitoring": list(self.monitoring),
            "dose_gates": list(self.dose_gates),
            "label_note": self.label_note,
        }

    def detail(self) -> dict:
        """Full dose-bearing view — deterministic dose channel only."""
        payload = self.summary()
        payload["components"] = [
            {
                "drug": c.drug, "dose": c.dose, "route": c.route,
                "schedule": c.schedule, "duration": c.duration, "notes": c.notes,
            }
            for c in self.components
        ]
        return payload


def _C(drug: str, dose: str, **kw) -> Component:
    return Component(drug, dose, **kw)


REGIMENS: tuple[Regimen, ...] = (
    # -------------------------------------------------------- targeted, adjuvant
    Regimen(
        "osimertinib_adjuvant", "Adjuvant osimertinib (ADAURA)", "adjuvant",
        (_C("osimertinib", "80 mg", route="PO", schedule="once daily", duration="3 years"),),
        trial_ids=("ADAURA",),
        monitoring=("QTc at baseline and on treatment", "ILD/pneumonitis symptoms", "LVEF if cardiac risk"),
        dose_gates=("qtc_baseline", "ild_history"),
        label_note="Resected IB–IIIA EGFR ex19del/L858R, after recovery from surgery ± adjuvant chemo",
    ),
    Regimen(
        "alectinib_adjuvant", "Adjuvant alectinib (ALINA)", "adjuvant",
        (_C("alectinib", "600 mg", route="PO", schedule="twice daily with food", duration="2 years"),),
        trial_ids=("ALINA",),
        monitoring=("LFTs q2w × 3 months then monthly", "CPK", "bradycardia"),
        dose_gates=("hepatic_baseline",),
        label_note="Resected IB(≥4 cm)–IIIA ALK-positive",
    ),
    # -------------------------------------------------------- perioperative IO
    Regimen(
        "nivo_chemo_neoadjuvant", "Neoadjuvant nivolumab + platinum doublet (CheckMate 816)",
        "neoadjuvant",
        (
            _C("nivolumab", "360 mg", schedule="q3w × 3 cycles"),
            _C("platinum-doublet chemotherapy", "per histology-appropriate doublet", schedule="q3w × 3 cycles",
               notes="histology-appropriate platinum doublet"),
        ),
        trial_ids=("CHECKMATE816",),
        contains_ici=True,
        monitoring=("irAE screen each cycle", "surgical window ~6 weeks post-cycle 3"),
        dose_gates=("egfr_alk_negative", "autoimmune_screen", "ecog_0_1"),
        label_note="3 cycles then surgery; no adjuvant IO in this paradigm",
    ),
    Regimen(
        "pembro_perioperative", "Perioperative pembrolizumab (KEYNOTE-671)", "perioperative",
        (
            _C("pembrolizumab", "200 mg", schedule="q3w × 4 neoadjuvant, × 13 adjuvant"),
            _C("cisplatin-based chemotherapy", "per doublet", schedule="q3w × 4 neoadjuvant"),
        ),
        trial_ids=("KEYNOTE671",),
        contains_ici=True,
        monitoring=("irAE screen", "thyroid function", "cortisol if fatigue"),
        dose_gates=("egfr_alk_negative", "autoimmune_screen"),
        label_note="Resectable II–IIIB(N2); PD-L1 not required",
    ),
    Regimen(
        "durva_perioperative", "Perioperative durvalumab (AEGEAN)", "perioperative",
        (
            _C("durvalumab", "1500 mg", schedule="q3w × 4 neoadjuvant, then q4w × 12 adjuvant"),
            _C("platinum-based chemotherapy", "per doublet", schedule="q3w × 4 neoadjuvant"),
        ),
        trial_ids=("AEGEAN",),
        contains_ici=True,
        monitoring=("irAE screen",),
        dose_gates=("egfr_alk_negative", "autoimmune_screen"),
        label_note="Resectable IIA–IIIB(N2) without EGFR/ALK alterations",
    ),
    Regimen(
        "nivo_perioperative", "Perioperative nivolumab (CheckMate 77T)", "perioperative",
        (
            _C("nivolumab", "360 mg", schedule="q3w × 4 neoadjuvant, then 480 mg q4w × 1 year adjuvant"),
            _C("platinum-doublet chemotherapy", "per doublet", schedule="q3w × 4 neoadjuvant"),
        ),
        trial_ids=("CHECKMATE77T",),
        contains_ici=True,
        dose_gates=("egfr_alk_negative", "autoimmune_screen"),
        label_note="Resectable IIA–IIIB(N2)",
    ),
    # -------------------------------------------------------- adjuvant chemo/IO
    Regimen(
        "adjuvant_platinum_doublet", "Adjuvant cisplatin-based doublet", "adjuvant",
        (
            _C("cisplatin", "75 mg/m²", schedule="day 1 q3w × 4 cycles",
               notes="carboplatin AUC 5 substitution when cisplatin-ineligible"),
            _C("vinorelbine or pemetrexed (nonsquamous)", "per label", schedule="per doublet"),
        ),
        trial_ids=("LACE",),
        monitoring=("renal function pre-cycle", "neuropathy", "cytopenias"),
        dose_gates=("renal_function", "hearing_neuropathy_baseline"),
        label_note="Resected II–III; selected IB(≥4 cm)/high-risk",
    ),
    Regimen(
        "atezolizumab_adjuvant", "Adjuvant atezolizumab (IMpower010)", "adjuvant",
        (_C("atezolizumab", "1200 mg", schedule="q3w", duration="1 year"),),
        trial_ids=("IMPOWER010",), contains_ici=True,
        dose_gates=("pd_l1_tc_ge_1", "egfr_alk_negative", "autoimmune_screen"),
        label_note="II–IIIA PD-L1 TC≥1% after resection and platinum chemo (FDA)",
    ),
    Regimen(
        "pembro_adjuvant", "Adjuvant pembrolizumab (KEYNOTE-091)", "adjuvant",
        (_C("pembrolizumab", "200 mg", schedule="q3w", duration="~1 year (18 cycles)"),),
        trial_ids=("KEYNOTE091",), contains_ici=True,
        dose_gates=("egfr_alk_negative", "autoimmune_screen"),
        label_note="IB(≥4 cm)–IIIA after resection ± chemo, irrespective of PD-L1",
    ),
    # -------------------------------------------------------- unresectable III
    Regimen(
        "ccrt_60gy", "Definitive concurrent chemoradiation (60 Gy/30 fx)", "definitive_crt",
        (
            _C("thoracic radiotherapy", "60 Gy in 30 fractions", route="RT",
               notes="do NOT escalate to 74 Gy (RTOG 0617)"),
            _C("concurrent platinum doublet", "cisplatin-etoposide or weekly carboplatin-paclitaxel",
               schedule="concurrent with RT"),
        ),
        trial_ids=("RTOG0617",),
        monitoring=("esophagitis", "pneumonitis", "weekly CBC"),
        dose_gates=("ecog_0_2", "pulmonary_reserve", "encompassable_volume"),
        label_note="Unresectable stage III, concurrent preferred over sequential when fit",
    ),
    Regimen(
        "durva_consolidation", "Consolidation durvalumab (PACIFIC)", "consolidation",
        (_C("durvalumab", "1500 mg q4w (or 10 mg/kg q2w)", duration="up to 12 months"),),
        trial_ids=("PACIFIC",), contains_ici=True,
        monitoring=("pneumonitis — overlaps radiation pneumonitis window",),
        dose_gates=("no_progression_post_crt", "start_within_42_days", "egfr_alk_negative_or_laura",
                    "pd_l1_framework_stated"),
        label_note="After cCRT without progression; FDA any PD-L1, EMA PD-L1≥1%; never concurrent with CRT",
    ),
    Regimen(
        "osimertinib_consolidation", "Consolidation osimertinib (LAURA)", "consolidation",
        (_C("osimertinib", "80 mg", route="PO", schedule="once daily", duration="until progression"),),
        trial_ids=("LAURA",),
        dose_gates=("egfr_positive", "no_progression_post_crt"),
        label_note="Unresectable III EGFR ex19del/L858R after definitive CRT — instead of durvalumab",
    ),
    # -------------------------------------------------------- stage IV
    Regimen(
        "osimertinib_first_line", "First-line osimertinib (FLAURA)", "first_line",
        (_C("osimertinib", "80 mg", route="PO", schedule="once daily", duration="until progression"),),
        trial_ids=("FLAURA",),
        dose_gates=("egfr_positive", "qtc_baseline"),
        label_note="Metastatic EGFR ex19del/L858R",
    ),
    Regimen(
        "osimertinib_chemo_first_line", "Osimertinib + platinum-pemetrexed (FLAURA2)", "first_line",
        (
            _C("osimertinib", "80 mg", route="PO", schedule="once daily"),
            _C("pemetrexed", "500 mg/m²", schedule="q3w × 4 then maintenance"),
            _C("cisplatin or carboplatin", "75 mg/m² / AUC 5", schedule="q3w × 4"),
        ),
        trial_ids=("FLAURA2",),
        dose_gates=("egfr_positive", "renal_function", "b12_folate_started"),
        label_note="Metastatic EGFR+ — option for high burden/CNS disease",
    ),
    Regimen(
        "amivantamab_lazertinib", "Amivantamab + lazertinib (MARIPOSA)", "first_line",
        (
            _C("amivantamab", "1050 mg (<80 kg) / 1400 mg (≥80 kg)", schedule="weekly × 4 then q2w"),
            _C("lazertinib", "240 mg", route="PO", schedule="once daily"),
        ),
        trial_ids=("MARIPOSA",),
        monitoring=("infusion reactions (step-up dosing)", "VTE — prophylactic anticoagulation first 4 months",
                    "rash/paronychia"),
        dose_gates=("egfr_positive",),
        label_note="Metastatic EGFR ex19del/L858R alternative to osimertinib",
    ),
    Regimen(
        "lorlatinib_first_line", "First-line lorlatinib (CROWN)", "first_line",
        (_C("lorlatinib", "100 mg", route="PO", schedule="once daily", duration="until progression"),),
        trial_ids=("CROWN",),
        monitoring=("lipid panel", "neurocognitive/mood effects", "AV block"),
        dose_gates=("alk_positive", "no_strong_cyp3a4_inducer"),
        label_note="Metastatic ALK+; strong CYP3A4 inducers contraindicated (hepatotoxicity)",
    ),
    Regimen(
        "pembro_monotherapy", "Pembrolizumab monotherapy (KEYNOTE-024)", "first_line",
        (_C("pembrolizumab", "200 mg q3w or 400 mg q6w", duration="up to 2 years"),),
        trial_ids=("KEYNOTE024",), contains_ici=True,
        dose_gates=("pd_l1_tps_ge_50", "egfr_alk_negative", "autoimmune_screen"),
        label_note="Metastatic PD-L1 TPS≥50% without EGFR/ALK",
    ),
    Regimen(
        "pembro_pemetrexed_platinum", "Pembrolizumab + pemetrexed-platinum (KEYNOTE-189)", "first_line",
        (
            _C("pembrolizumab", "200 mg", schedule="q3w, up to 35 cycles"),
            _C("pemetrexed", "500 mg/m²", schedule="q3w, continues as maintenance"),
            _C("cisplatin or carboplatin", "75 mg/m² / AUC 5", schedule="q3w × 4"),
        ),
        trial_ids=("KEYNOTE189",), contains_ici=True,
        monitoring=("renal function (pemetrexed)", "irAE screen"),
        dose_gates=("egfr_alk_negative", "nonsquamous", "renal_function", "b12_folate_started",
                    "autoimmune_screen"),
        label_note="Metastatic nonsquamous without EGFR/ALK, any PD-L1",
    ),
    Regimen(
        "pembro_carbo_taxane", "Pembrolizumab + carboplatin-taxane (KEYNOTE-407)", "first_line",
        (
            _C("pembrolizumab", "200 mg", schedule="q3w"),
            _C("carboplatin", "AUC 6", schedule="q3w × 4"),
            _C("paclitaxel or nab-paclitaxel", "200 mg/m² q3w / 100 mg/m² weekly", schedule="× 4 cycles"),
        ),
        trial_ids=("KEYNOTE407",), contains_ici=True,
        dose_gates=("egfr_alk_negative", "autoimmune_screen"),
        label_note="Metastatic squamous NSCLC",
    ),
    Regimen(
        "nivo_ipi_chemo", "Nivolumab + ipilimumab + 2 cycles chemo (CheckMate 9LA)", "first_line",
        (
            _C("nivolumab", "360 mg", schedule="q3w"),
            _C("ipilimumab", "1 mg/kg", schedule="q6w"),
            _C("platinum doublet", "per histology", schedule="× 2 cycles only"),
        ),
        trial_ids=("CHECKMATE9LA",), contains_ici=True,
        dose_gates=("egfr_alk_negative", "autoimmune_screen"),
        label_note="Metastatic NSCLC without EGFR/ALK",
    ),
    # -------------------------------------------------------- local therapy
    Regimen(
        "sbrt_definitive", "Definitive SBRT/SABR (medically inoperable stage I)", "definitive_rt",
        (_C("SBRT", "e.g. 48–54 Gy in 3–5 fractions by size/location", route="RT",
            notes="ultracentral tumors need risk-adapted fractionation"),),
        trial_ids=(),
        dose_gates=("inoperable_or_declines_surgery", "peripheral_vs_central_assessed"),
        label_note="Stage I medically inoperable / declines surgery; biopsy confirmation when safe",
    ),
    Regimen(
        "sbrt_oligomet_lct", "Local consolidative therapy for oligometastatic disease", "local_consolidative",
        (_C("SBRT / surgery to all residual sites", "per-site risk-adapted", route="RT/OR"),),
        trial_ids=("GOMEZ_LCT",),
        dose_gates=("oligometastatic_confirmed", "no_progression_on_systemic"),
        label_note="≤3 sites, controlled on first-line systemic therapy, MDT decision",
    ),
)

REGIMENS_BY_ID: dict[str, Regimen] = {r.regimen_id: r for r in REGIMENS}


def get(regimen_id: str) -> Optional[Regimen]:
    return REGIMENS_BY_ID.get(str(regimen_id).strip())


def search(query: str, *, limit: int = 6) -> list[Regimen]:
    terms = [t for t in str(query).lower().replace(",", " ").split() if t]
    if not terms:
        return []
    scored: list[tuple[int, Regimen]] = []
    for regimen in REGIMENS:
        haystack = " ".join((
            regimen.regimen_id, regimen.name.lower(), regimen.setting,
            " ".join(c.drug.lower() for c in regimen.components),
            " ".join(regimen.trial_ids).lower(),
        ))
        score = sum(1 for term in terms if term in haystack)
        if score:
            scored.append((score, regimen))
    scored.sort(key=lambda pair: (-pair[0], pair[1].regimen_id))
    return [regimen for _, regimen in scored[:limit]]
