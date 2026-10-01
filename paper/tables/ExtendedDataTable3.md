**Extended Data Table 3 | The deterministic safety rules**

| Finding | Severity* | What the rule checks | Audit probes (n)† |
|---|---|---|---|
| N3_NO_SURGERY | block | No resection proposed for N3 disease | 2 |
| DRIVER_EXCLUDES_PERIOP_IO | block/warn | No perioperative or adjuvant immunotherapy with an EGFR or ALK alteration | 0 |
| EGFR_VARIANT_MISMATCH | block/warn | An EGFR-directed regimen matches the reported variant class (exon 20, C797S, uncommon) | 2 |
| EGFR_III_CONSOLIDATION | block | EGFR-mutant unresectable stage III: osimertinib (LAURA), not durvalumab, consolidation | 1 |
| NO_CONCURRENT_DURVALUMAB | block | Durvalumab follows chemoradiation; it is never concurrent | 1 |
| NO_RT_DOSE_ESCALATION | block | Thoracic radiotherapy dose above the definitive standard (RTOG 0617) | 0 |
| TRIAL_EDITION_MIGRATION | warn | A cited trial enrolled the case's stage; edition migration and extrapolation flagged | 0 |
| TRIAL_STAGE_EXTRAPOLATION | warn | (same rule) | 0 |
| TRIAL_STAGE_BOUNDARY | block | (same rule) | 2 |
| STAGE0_NO_SYSTEMIC | block | No systemic therapy for stage 0 | 0 |
| INDICATION_UNDECLARED | block | Every regimen meets its declared, machine-executable indication; undeclared ones flagged | 1 |
| INDICATION_PREDICATE | block/warn | (same rule) | 11 |
| DRIVER_FIRST_LINE | block/warn | Stage IV with an actionable driver: driver-directed first line, not immunotherapy | 2 |
| OPTION_DRUG_UNBOUND | block | An option named after a systemic drug carries a library regimen | 2 |
| CONSOLIDATION_WITHOUT_CRT | block | Consolidation (PACIFIC, LAURA) only after chemoradiation | 1 |
| PROGRESSION_SAME_DRUG | warn | Re-proposing a drug the disease progressed on | 3 |
| CNS_UNTREATED_SYMPTOMATIC | block | Symptomatic untreated brain or leptomeningeal metastases are addressed first | 2 |
| CNS_TNM_INCONSISTENT | warn | CNS metastases on record while the descriptors say M0 | 1 |
| ORGAN_FUNCTION_GATE | block | Organ-function and comorbidity gates of each regimen | 3 |
| ICI_COMORBIDITY_CAUTION | warn | Checkpoint inhibitor with interstitial lung disease or active autoimmune disease | 0 |
| PS_GATE | warn | Performance status fits concurrent chemoradiation or perioperative therapy | 0 |
| BIOMARKER_GAP | block | Tier-A biomarkers are known before systemic therapy | 0 |
| DOSE_IN_MODEL_OUTPUT | block | No dose figures in model-authored text | 0 |

The 20 rules raise 23 finding identifiers; '(same rule)' marks a further identifier raised by the rule in the row above.
*Severity is the rule's own grading. In governed mode a block finding stops release (after the bounded repair loop); in agent mode every finding is advisory and returns to the model, which must revise or justify an override.
†Gold-standard audit probes (n = 28; deliberately unsafe plans given directly to the rule engine) that raised the finding; a probe may raise several.
CNS, central nervous system; ICI, immune checkpoint inhibitor.
