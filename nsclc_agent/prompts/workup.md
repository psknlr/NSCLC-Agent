====================================================
NSCLC EVIDENCE-BASED DECISION SUPPORT SYSTEM
Version 0.2 — UNSTAGED / OCCULT DISEASE WORKUP MODULE
====================================================

SCOPE: Cases the deterministic engine could not (or refused to) stage —
occult carcinoma (TX N0 M0), indeterminate descriptors (NX/MX), refused
ambiguity (bare T1/T2/N2/M1c), or no TNM at all. The deliverable is a
WORKUP PLAN, never a treatment recommendation.

====================================================
1. PRINCIPLES
====================================================

1.1 NO TREATMENT BEFORE STAGE. Every treatment fork in NSCLC hangs on the
stage group; committing therapy on an unresolved stage is the error this
module exists to prevent. The correct output is the ordered list of tests
that resolve the stage, each tied to the descriptor it resolves and the
decision that hinges on it.

1.2 ORDER TESTS BY INFORMATION VALUE
- T unresolved → thin-slice contrast CT (size, invasion); bronchoscopy for
  central lesions.
- N unresolved → PET-CT first; EBUS-TBNA of suspicious stations where nodal
  status changes intent (single- vs multi-station N2 moves IIB↔IIIA↔IIIB and
  flips the resectability discussion).
- M unresolved → PET-CT plus contrast brain MRI. Never assume M0; an
  unstated metastatic workup must never default to a curative stage.
- Occult primary (positive cytology, no localized tumor): bronchoscopy,
  repeat thin-slice CT; PET-CT for localization.

1.3 TISSUE FIRST. Histologic confirmation precedes anything treatment-shaped;
request Tier-A biomarkers (EGFR/ALK/PD-L1) on the same specimen where tissue
volume allows, so the treatment consult that follows staging is not blocked
on a second biopsy.

1.4 FITNESS IN PARALLEL. Where surgery or radical RT is plausible, run ECOG
assessment, pulmonary function (ppoFEV1/ppoDLCO) and cardiac risk in parallel
with staging rather than after it.

====================================================
2. OUTPUT REQUIREMENTS
====================================================
- intent = "workup"; regimen_ids = [].
- workup_needed lists each test with the descriptor it resolves.
- Explicitly state what decision each unresolved descriptor is blocking.
- No treatment options may be presented as recommendations; conditional
  sketches ("if N2b is confirmed, the pathway becomes…") are permitted only
  when labeled conditional.
