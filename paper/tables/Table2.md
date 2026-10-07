**Table 2 | Hooks: the in-loop checks of agent mode**

| Hook | Runs when | Check | Output | Full autonomy |
|---|---|---|---|---|
| Emergency screen | Prompt submitted | Clause-scoped, negation-aware oncologic-emergency screen on every message; a hit puts the standard pathway in front of the model and alerts the clinician. | Alert; context for the model | On |
| Case-note seeding | Prompt submitted | Deterministically extracts unambiguous facts (TNM, age, ECOG, drivers…) from the message into the case notes; the model can correct them. | Context for the model | Off* |
| Evidence ledger | After each tool call | Records every trial / guideline / literature id a tool actually returned and every regimen-dose lookup, for the provenance checks. | None (records references and dose lookups) | On |
| Rule-engine review | Consult submitted | The 20 deterministic safety rules review the plan (driver-directed first line, no surgery for N3, indications, organ function, doses…). | Rule identifiers (20 rules)† | Off* |
| Stage consistency | Consult submitted | Flags a stage that differs from the AJCC/UICC 9th-edition staging engine. | STAGE_DIFFERS_FROM_ENGINE | Off* |
| Citation provenance | Consult submitted | Flags cited trials / guideline items that no tool returned and no local registry or knowledge base can resolve (hallucinated citations). | UNVERIFIED_CITATION | On |
| Dose provenance | Consult submitted | Flags dose figures given without a regimen-library lookup, or doses addressed to a patient. | DOSE_NOT_FROM_LIBRARY; DOSE_TO_PATIENT | On |
| Emergency first | Consult submitted | Flags a conclusion that does not put a screened emergency first (high priority). | EMERGENCY_NOT_ADDRESSED | On |

Every finding is returned to the model once; the model revises its plan or records a clinical reason (rule_responses). No hook blocks, rewrites or withholds output.
*Compares the model with the deterministic kernel, so it is off by default in full autonomy and on in kernel-assisted mode; any hook can be switched individually. In full autonomy the same kernel checks run after the consult as the independent audit, which is never returned to the model.
†For example DRIVER_FIRST_LINE, INDICATION_PREDICATE, N3_NO_SURGERY, ORGAN_FUNCTION_GATE.
