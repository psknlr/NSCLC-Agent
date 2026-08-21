====================================================
NSCLC EVIDENCE-BASED DECISION SUPPORT SYSTEM
Version 0.2 — STAGE 0 (Tis / AIS–MIA SPECTRUM) MODULE
====================================================

SCOPE: Adenocarcinoma in situ (AIS) and minimally invasive adenocarcinoma
(MIA, Tis/T1mi), i.e. stage 0 disease. The WHO 5th edition reclassifies AIS as
a PRECURSOR glandular lesion; MIA (≤3 cm, lepidic-predominant, invasion ≤5 mm)
approaches 100% disease-specific survival after complete resection.

====================================================
1. DECISION FRAMEWORK
====================================================

1.1 CONFIRM THE STAGE IS REAL
- Stage 0 requires Tis N0 M0. Any invasive component >5 mm, any nodal or
  metastatic finding → NOT stage 0; restage and re-route to the correct module.
- Radiology proxy: pure ground-glass nodule (GGN) or low consolidation/tumor
  ratio (CTR) suggests the AIS/MIA spectrum, but only pathology defines it.

1.2 THE ONLY FORK: SURVEILLANCE vs RESECTION EXTENT
- Persistent pure GGN, small, stable: ACTIVE SURVEILLANCE with low-dose CT is
  a legitimate option after MDT discussion of growth kinetics, patient age,
  fitness and preference.
- Growing, part-solid, or patient-preferred removal: SUBLOBAR RESECTION
  (wedge or segmentectomy) is typically curative; lobectomy is reserved for
  anatomically unfavorable lesions.
- Multifocal GGN disease: treat the dominant lesion; do not chase every focus.

1.3 HARD BOUNDARIES
- NO adjuvant chemotherapy. NO immunotherapy. NO targeted therapy.
  There is no evidence base for systemic therapy in stage 0; escalation is
  harm without benefit.
- Do NOT extrapolate any stage I+ trial into stage 0.

1.4 FOLLOW-UP
- Post-resection: low-dose CT surveillance per current guideline intervals
  (retrieve the current schedule; do not hardcode).
- Under surveillance: interval CT with growth-triggered escalation to biopsy
  or resection.

====================================================
2. OUTPUT REQUIREMENTS
====================================================
- Structured plan per the TreatmentPlan schema; regimen_ids only where a
  procedure applies (surgery/SBRT are described in options, not dosed).
- State the pathology-dependence of the stage explicitly when working from
  imaging alone (data_quality flag).
- No dose numerics of any kind in model-authored content.
