"""Distilled per-stage decision cores.

The full v3.3 protocol modules (40–77k characters each) are shipped intact in
this package and served *on demand* through the ``protocol_lookup`` tool. What
goes into the treatment agent's system prompt is this distillation — the
decision skeleton and the hard boundaries — so the prompt budget goes to
reasoning and retrieved evidence instead of to a wall of static text the model
would skim anyway. Every numeric claim beyond these skeletons must come from a
tool call (trial registry, protocol section, retrieval), which is what makes
the final plan's numbers traceable.

Each core is reviewed prose, not generated at runtime.
"""

from __future__ import annotations

STAGE_CORES: dict[str, str] = {
    "stage0": """\
STAGE 0 (Tis — AIS/MIA spectrum) DECISION CORE
1. AIS is a precursor lesion (WHO 5th ed.); MIA (≤3 cm, lepidic-predominant,
   ≤5 mm invasion) approaches 100% disease-specific survival after resection.
2. The decision fork is surveillance vs resection extent — NOT systemic therapy.
   Sublobar resection (wedge/segmentectomy) is typically definitive; pure GGN
   with low consolidation/tumor ratio may be actively surveilled per MDT.
3. HARD BOUNDARY: no adjuvant chemotherapy, no immunotherapy, no targeted
   therapy for stage 0 — escalation is harm without benefit.
4. If any invasive component >5 mm, node positivity or M disease is found,
   the case is NOT stage 0: restage and re-route.""",
    "stage1": """\
STAGE I (IA1–IB, N0 M0) DECISION CORE
1. The dominant fork is OPERABILITY: resectable + medically operable →
   anatomic resection (lobar or intentional segmentectomy for ≤2 cm
   peripheral) with systematic nodal evaluation. Medically inoperable or
   declines surgery → definitive SBRT/SABR (risk-adapted for central lesions).
2. Confirm the stage is real: N0 requires adequate nodal assessment; M0
   requires completed workup. If staging is incomplete, say so — do not assume.
3. Adjuvant therapy is the EXCEPTION in stage I:
   • EGFR ex19del/L858R after resection: adjuvant osimertinib (ADAURA covers
     IB and up; IA is NOT covered).
   • Watch the ≥4 cm threshold most adjuvant IO/chemo evidence used: a
     current-edition IB tumor of >3–<4 cm does NOT reach it, while an
     exactly-4-cm IB does — check the trial registry before recommending
     anything adjuvant; high-risk-feature chemo is a discussion, not a
     default.
   • PD-L1 is NOT a stage I treatment driver.
4. Ground-glass/subsolid lesions and AIS/MIA behave indolently — do not
   over-treat; surveillance schedules come from the protocol module.""",
    "stage2": """\
STAGE II (IIA–IIB) DECISION CORE
1. Curative intent, surgery-first or perioperative-systemic pathways — the
   fork is timing of systemic therapy, not whether (node-positive or ≥4 cm
   disease benefits from systemic treatment).
2. Histology first, then Tier-A biomarkers (EGFR/ALK/PD-L1) BEFORE committing:
   • EGFR+ resected: adjuvant osimertinib (ADAURA), ± adjuvant chemo.
   • ALK+ resected: adjuvant alectinib (ALINA) — replaces chemo in that trial.
   • Driver-negative, pre-surgery: perioperative chemo-IO (KEYNOTE-671 /
     AEGEAN / CheckMate 77T cover II; CheckMate 816 neoadjuvant-only for
     ≥4 cm/node-positive) is preferred over surgery-first when feasible.
   • Driver-negative, already resected: adjuvant platinum doublet (LACE);
     then atezolizumab (PD-L1 TC≥1%, IMpower010) or pembrolizumab
     (KEYNOTE-091) per label.
3. HARD BOUNDARY: EGFR/ALK alterations exclude perioperative/adjuvant IO.
4. Inoperable stage II → definitive (chemo)radiation pathway, not observation.""",
    "stage3a": """\
STAGE IIIA DECISION CORE
1. FIRST establish the clinical scenario — postop-resected / perioperative-
   resectable / neoadjuvant-only / unresected-or-unclear. Do not force a
   postoperative plan on an unresected patient.
2. Resectability is an MDT judgment (single-station N2a favors surgery-
   containing pathways; bulky/multi-station N2 argues definitive CRT).
   Invasive mediastinal staging (EBUS) before committing — N2a vs N2b
   changes both the stage and the pathway.
3. Resectable, driver-negative → perioperative chemo-IO (KEYNOTE-671, AEGEAN,
   CheckMate 77T) or neoadjuvant-only CheckMate 816; surgery within the
   protocol window; document pathologic response (pCR/MPR, ypTNM).
4. Resectable, EGFR+/ALK+ → surgery → adjuvant osimertinib/alectinib
   (± chemo). NO perioperative IO.
5. Unresectable → definitive cCRT (60 Gy/30 fx, concurrent platinum doublet;
   never 74 Gy) → consolidation durvalumab (driver-negative; PACIFIC; not
   concurrent) or osimertinib (EGFR+; LAURA).
6. Adjuvant IO after resection+chemo: atezolizumab needs PD-L1 TC≥1%
   (IMpower010 label); pembrolizumab (KEYNOTE-091) is PD-L1-agnostic.""",
    "stage3b": """\
STAGE IIIB DECISION CORE
1. Resectability gate first, after invasive mediastinal staging: N3 is
   UNRESECTABLE, full stop. T4N2 needs MDT; most IIIB is managed as
   unresectable.
2. UNRESECTABLE path: definitive cCRT (60 Gy/30 fx + platinum doublet,
   concurrent preferred; no dose escalation — RTOG 0617) → consolidation:
   • driver-negative → durvalumab up to 12 months (PACIFIC; start ≤42 days;
     FDA any PD-L1, EMA ≥1%; never concurrent — PACIFIC-2 negative);
   • EGFR ex19del/L858R → osimertinib until progression (LAURA), NOT
     durvalumab.
3. RESECTABLE IIIB(N2) only via IIIB-covering perioperative regimens
   (KEYNOTE-671 / AEGEAN / CheckMate 77T). CheckMate 816, ADAURA, ALINA,
   IMpower010, KEYNOTE-091 do NOT cover IIIB — using them is an
   extrapolation that must be declared.
4. EGFR/ALK+ resectable: surgery → adjuvant TKI is itself an extrapolation
   beyond IIIA — declare it; if unresectable, LAURA applies directly.
5. M0 must be confirmed (PET-CT + brain MRI) before any curative-intent
   commitment; exclude the oligometastatic mimic.""",
    "stage3c": """\
STAGE IIIC (T3–4 N3) DECISION CORE
1. N3 disease: surgery has no role. The curative-intent question is whether
   definitive concurrent chemoradiation is FEASIBLE (encompassable volume,
   PS 0–2, adequate pulmonary reserve, confirmed M0).
2. cCRT-feasible → 60 Gy/30 fx + concurrent platinum doublet → consolidation
   by driver (durvalumab PACIFIC / osimertinib LAURA for EGFR+). Curative
   intent is real: 5-year OS ≈ 40% era.
3. Not feasible (volume/PS/comorbidity) → sequential CRT, or palliative-intent
   systemic therapy per the stage IV framework with honest intent labeling.
4. Confirm N3 pathologically when it changes intent (supraclavicular node
   biopsy is often easiest). PET-CT + brain MRI mandatory.
5. HARD BOUNDARIES: no surgical pathway; no dose escalation; consolidation
   agent by driver status; never durvalumab concurrent with RT.""",
    "stage4a": """\
STAGE IVA (M1a / M1b) DECISION CORE
1. Biomarkers BEFORE first-line therapy: histology → EGFR/ALK (and broader
   NGS: ROS1, BRAF, MET, RET, KRAS G12C, HER2…) → PD-L1. Committing
   chemo-IO before driver results is the classic error — a driver changes
   everything.
2. Driver-positive → matched first-line targeted therapy (osimertinib FLAURA
   ± chemo FLAURA2, or amivantamab-lazertinib MARIPOSA for EGFR;
   lorlatinib CROWN for ALK). ICIs are for driver-negative disease.
3. Driver-negative → PD-L1-stratified: TPS≥50% pembrolizumab mono
   (KEYNOTE-024) or chemo-IO; otherwise histology-matched chemo-IO
   (KEYNOTE-189 nonsquamous / KEYNOTE-407 squamous / CheckMate 9LA).
4. OLIGOMETASTATIC (M1b single lesion; limited M1a) is the IVA-specific
   question: after response/stability on first-line systemic therapy,
   consider local consolidative therapy to all sites (Gomez phase II — frame
   as MDT/trial-context option) plus definitive local therapy to the primary
   in selected cases. Brain metastases: SRS-first for limited lesions.
5. Symptom-directed care from day one (effusion management for M1a,
   bone-directed agents, palliative-care integration).""",
    "stage4b": """\
STAGE IVB (M1c1 / M1c2) DECISION CORE
1. Polymetastatic disease: systemic therapy is the backbone; local therapy is
   symptom-directed, not consolidative-by-default. M1c2 (multi-organ) carries
   independently poorer prognosis than M1c1 — weigh treatment burden honestly.
2. Same biomarker-first discipline as IVA: driver-positive → targeted therapy
   regardless of PD-L1; driver-negative → PD-L1-stratified chemo-IO by
   histology (KEYNOTE-189/407, CheckMate 9LA, KEYNOTE-024 for TPS≥50%).
3. PS 2 → dose-attenuated or mono-therapy options with explicit goals-of-care
   discussion; PS 3–4 driver-negative → best supportive care is a legitimate
   and often correct recommendation; PS alone does not bar TKIs for
   driver-positive disease (performance may recover on treatment).
4. Screen and treat oncologic emergencies and symptomatic sites (brain, cord,
   bone) BEFORE or alongside systemic start; hypercalcemia and VTE are common.
5. Early palliative-care integration improves outcomes — recommend it
   explicitly; document goals of care.""",
    "workup": """\
UNSTAGED / OCCULT DISEASE DECISION CORE
1. No treatment recommendation may be made before the stage is resolved — the
   deliverable here is the WORKUP PLAN, ordered by information value:
   • T unresolved → thin-slice contrast CT; bronchoscopy for central lesions.
   • N unresolved → PET-CT, then EBUS-TBNA of suspicious stations (single- vs
     multi-station N2 changes IIB↔IIIA↔IIIB).
   • M unresolved → PET-CT + contrast brain MRI. Never assume M0.
   • Occult primary (TX N0): bronchoscopy + repeat thin-slice CT localization.
2. Tissue diagnosis precedes everything treatment-shaped: histology, then
   Tier-A biomarkers on the same specimen where volume allows.
3. Fitness workup in parallel where surgery/RT is plausible: ECOG, PFTs
   (ppoFEV1/ppoDLCO), cardiac risk.
4. Output states plainly WHICH descriptor each test resolves and what decision
   hinges on it.""",
}

#: module key → minimum output tokens its schema realistically needs. The
#: runner refuses to call a model with a ceiling below this — the v0.1 bug
#: where a 4096-token ceiling silently truncated every structured plan.
MIN_OUTPUT_TOKENS: dict[str, int] = {
    "stage0": 2000, "stage1": 3000, "stage2": 3000, "stage3a": 4000,
    "stage3b": 4000, "stage3c": 3000, "stage4a": 3000, "stage4b": 3000,
    "workup": 1500,
}
