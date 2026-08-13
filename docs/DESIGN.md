# Design & Architecture

This document explains how the NSCLC-Agent is structured, why the boundaries
sit where they do, and how to extend it.

## 1. Design goals

1. **Verifiable staging, not guessed staging.** The stage group is the pivot of
   the whole pipeline. Compute it symbolically and treat it as authoritative;
   the LLM must never re-derive or override it.
2. **Stage-specialized reasoning.** Each stage band has genuinely different
   decision logic (resectability gate, consolidation-agent-by-driver,
   oligometastatic LCT, performance-status gate …). Route to the module that
   encodes that logic rather than one monolithic prompt.
3. **Backend-agnostic.** Teaching and testing require swapping models freely,
   so clinical logic is fully decoupled from the inference backend.
4. **Runs anywhere, offline-first.** Core depends only on the standard library;
   an offline mock backend keeps the whole pipeline exercisable with no keys or
   network (essential for CI and classroom use).
5. **Auditable.** Every run emits staging provenance, migration notes, routing
   decisions and flags — the record that makes generated data trustworthy.

## 2. How it maps to an active-perception / POMDP framing

The broader research vision frames NSCLC staging as *active information
gathering under partial observability*: the true stage is a hidden state,
each investigation is a costly/noisy observation, and the agent decides what to
check next before committing. This repository implements the parts of that
vision that are concrete and verifiable today, and leaves clean seams for the
rest:

| POMDP concept | Realized here as | Extension seam |
|---|---|---|
| Hidden state `S` (true TNM) | `TNM` descriptors | — |
| Verifiable definitive staging | `staging/tnm.py` (symbolic engine) | stable |
| Guideline-consistent policy | protocol modules + router | add modules |
| Observation model `P(o\|s,a)` — imaging | `imaging.py` — vision reader proposes descriptors from films | per-test sensitivity/specificity metadata |
| Observation model — interview | `consult/extract.py` — regex pass, then a model pass, with ambiguity preserved | richer clinical NLP |
| Action selection / VOI | `consult/planner.py` — stage-conditional weights, gates, blocking descriptors | expected-value-of-information over outcome models |
| Stopping rule | `is_sufficient()` — stop when nothing unasked can change the recommendation | calibrated decision-boundary test |
| Belief `b(s)` over stages | single computed stage; unresolved descriptors held as gaps rather than guessed | replace point stage with a distribution |
| Uncertainty gate / escalation | `flags` + `MODULE_UNAVAILABLE` / `IMAGING_DISCORDANCE` / `CONSULT_INCOMPLETE` / MDT triggers | calibrated UQ gate before `commit` |
| Evidence grounding | `evidence/` — retrieval before reasoning, or a recorded "not retrieved" | guideline + label corpora alongside PubMed |
| Audit trail | `AgentResult.to_dict()` incl. `imaging`, `evidence`, `consult` provenance | hash-chained event log |

The important property is that the **staging engine is already the
"deterministic, verifiable definitive-staging module"** the vision calls for —
the highest-hallucination-risk step is handled symbolically, and everything
else is layered on top without contaminating it.

## 2b. The consultation layer (自主问诊)

`consult/` is the active-information-gathering half of that framing, and it is
deliberately **model-free where it decides**. Choosing what is still worth
asking is a policy question about guideline structure, not a generation task:
keeping it in Python makes every question auditable (each one carries the
decision it was asked for), reproducible across backends, and testable offline
— the same argument the staging engine makes for stage groups.

- `slots.py` — the schema. Each fact carries a base weight, **per-stage
  overrides** (PD-L1 is worth 10 in stage I, where the module says it is not
  decision-relevant, and 95 in stage IV) and an optional **gate** (driver
  testing is suppressed once histology is squamous; CCRT feasibility appears
  only once the tumour is unresectable).
- `planner.py` — ordering and stopping. Staging descriptors get a blocking
  bonus so they are resolved before anything downstream is scored. Sufficiency
  is judged on *validity*, not presence: a descriptor the engine would refuse
  counts as unknown, so the loop cannot declare itself ready on a `T2`/`N2` it
  will fail to stage later. It ends when no relevant unknown scores at or
  above the sufficiency threshold.
- `extract.py` — two passes. Deterministic patterns first (they work offline
  and are reproducible), the model second, deterministic wins on conflict.
  Ambiguous descriptors are recorded as notes, never canonicalised: `"N2"` in a
  reply must not become `N2a` any more than it may in the staging engine, and
  a model that returns one anyway is rejected with
  `MODEL_DESCRIPTOR_REJECTED`. Two rules earn their keep here, because
  violating either produces a *silently wrong* clinical value rather than an
  error: **negation is checked before any finding is recorded** (reading "no
  malignant pleural effusion" as M1a stages a curative patient as IVA), and
  **ASCII keywords are word-bounded** ("operable" inside "inoperable" inverts
  the stage III fork; "nos" inside "diagnosis" invents a histology).
- `session.py` — serialisable state. A consultation can be paused, stored,
  resumed in another process and replayed in a test without a model. A later
  round may *correct* an earlier fact — refusing that made a mis-extraction
  permanent and reported the correction as "no new facts" — and the previous
  value is kept in `provenance` so the correction stays auditable.

What the consultation did **not** learn is carried forward rather than
discarded: `CONSULT_INCOMPLETE`, `result.consult.outstanding`, and a prompt
block instructing the model to emit those as `information_gap` steps.

## 2c. The evidence layer

The protocol modules ask for `tool_call` steps, but nothing executed them, so
identifiers in `sources` were model recall presented in a citation-shaped
field. `evidence/` makes the two states distinguishable rather than pretending
to solve retrieval: with a backend configured, deterministic queries built from
the **computed stage** (never from model output — otherwise a hallucinated
claim could steer retrieval and then appear supported by it) run *before* the
reasoning call and the real records are injected as citable; without one, the
prompt says so and the run is flagged `EVIDENCE_NOT_RETRIEVED`.

## 2a. The perception layer (reading films)

Real staging starts from images, not from a T/N/M someone typed in. The
perception layer closes that gap while *preserving* the verifiable-staging
property. Its one inviolable rule:

> The vision model **proposes** radiographic descriptors. It **does not** assign
> the stage group. The deterministic engine still does that.

`imaging.py` sends the films to a vision-capable backend (Gemini via the Poe
API by default) under an extraction prompt that (a) forbids naming a stage
group, (b) forces the exact 9th-edition vocabulary, and (c) requires `null` +
an `uncertainties` note for anything indeterminate (e.g. single- vs
multi-station N2). The returned `ImagingFindings` are always stamped
`MODEL_PROPOSED_UNVERIFIED` and folded into the case by `agent._ingest_imaging`:

- **Case already has T/N/M** (human/pathologic) → the proposal is only a
  *cross-check*; a mismatch raises `IMAGING_DISCORDANCE[…]` and the case value
  is kept for staging.
- **Case is missing a descriptor** → it is *seeded* from the proposal and
  flagged `RADIOGRAPHIC_TNM_PROPOSED` (provisional cTNM), then the engine stages
  it exactly as any other input.
- **Descriptor still unresolved** → `NEXT_STEP_SUGGESTED` names the test that
  would resolve it (EBUS for N, PET-CT + brain MRI for M …) — the first,
  rule-based slice of value-of-information planning.

The reasoning model receives the findings as *labeled, unverified context* in
the user turn — never as the images themselves — so the reasoning backend need
not be multimodal, and perception stays cleanly separable from reasoning. A
failed read degrades gracefully (`IMAGING_READ_FAILED`) instead of aborting the
run. DICOM is out of scope for the stdlib core: export slices to PNG/JPEG (or
pass an `https` URL) first.

## 3. Module boundaries

```
nsclc_agent/
  staging/        pure, deterministic, no I/O, no LLM  ← verifiable core
    tnm.py          TNM normalization + 9th-ed. stage table
    router.py       stage group → module key (+ label normalization)
    selftest.py     authoritative expectation table (source of truth)
  consult/        pure, deterministic policy; no I/O  ← the interview
    slots.py        what to know, what each fact decides, stage weights
    planner.py      VOI ordering + the stopping rule
    extract.py      reply → slot values (regex first, model second)
    session.py      serialisable consultation state
  evidence/       literature retrieval (or a recorded absence of it)
    base.py         Retriever ABC, EvidenceRecord, prompt blocks
    pubmed.py       NCBI E-utilities over the standard library
  prompts/        protocol modules (Markdown) + loader
  providers/      backend abstraction
    base.py         LLMProvider ABC, Message (+ multimodal), LLMResponse, params
    openai_compatible.py   stdlib HTTP for OpenAI-shaped APIs (text + vision)
    poe.py / minimax.py / azure.py    thin subclasses
    litellm_provider.py    optional SDK backend
    mock.py         offline deterministic stub (+ mock vision read)
    registry.py     config dict → provider instance
  imaging.py      perception layer: films → proposed descriptors (vision)
  case.py         case input model (+ images) + user-turn rendering
  config.py       YAML/JSON config loading (+ built-in mock default)
  agent.py        orchestration: (ask →) (read films →) resolve_stage →
                  route → retrieve evidence → complete
  cli.py          argparse front-end
```

Dependencies flow strictly downward: `staging` depends on nothing; `providers`
depends on nothing in the clinical layer; `agent` composes them. This is what
lets the staging engine be trusted and unit-tested in isolation, and lets
backends be added without touching clinical logic.

## 4. Why the staging engine rejects ambiguity

`T1`, `T2`, `N2`, `M1c` and an **empty M** are **refused, not guessed**. In the
9th edition the sub-distinction changes the stage group (`T2aN2a` = IIIA but
`T2aN2b` = IIIB; `M1c1`/`M1c2` are both IVB but carry different prognosis and
were split for a reason) or the substage (`T1a`/`T1b`/`T1c` → IA1/IA2/IA3).
Silently collapsing them would defeat the point of a *verifiable* engine, so
the engine raises `StagingError` with an actionable message and the agent turns
it into a `STAGING_ERROR` flag rather than proceeding.

An empty M is the subtlest of these: reading "not stated" as M0 turns an
incomplete work-up into a curative-intent stage group. The engine refuses it,
and when the agent stages without one anyway it records `M_ASSUMED_M0` plus a
descriptor note marking the group provisional. The same discipline runs through
the consultation extractor, which records `"N2"` in a reply as an ambiguity
note rather than canonicalising it.

## 5. Prompt assembly

`agent.build_messages()` produces two turns:

- **system** = the full protocol module (unmodified) **+** a
  `DETERMINISTIC STAGING` preamble stating the computed stage group, edition,
  migrations and the instruction *"treat this as authoritative; do not
  re-derive"*.
- **user** = the case: free-text presentation + any structured `fields` +
  the explicit question.

Injecting the verified stage into the system prompt is the mechanism that keeps
the model reasoning inside a correct stage without letting it wander into a
staging decision it is bad at.

## 6. Provider layer

All backends implement `LLMProvider.complete(messages) -> LLMResponse`. Poe,
MiniMax and Azure are OpenAI-shaped and share `OpenAICompatibleProvider`, which
does request/parse over the Python standard library (`urllib`), honoring
`SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` and system proxies. LiteLLM is an optional
SDK-backed provider that unifies 100+ providers via the `model` string. The
`mock` provider returns a deterministic JSON stub so the pipeline runs offline.

**Vision** rides the same surface: a `Message` may carry `images`, and
`to_openai()` emits the multimodal *content-parts* array that OpenAI-compatible
vision endpoints expect — so no vision-specific transport is needed. A provider
declares itself multimodal with `vision: true` in config (`supports_vision`),
which lets the agent auto-select the film reader. The mock recognises the
imaging-extraction prompt and returns an empty, honest findings stub so the
whole perception path is testable offline.

Adding a backend = subclass `LLMProvider` (or `OpenAICompatibleProvider`) and
register a branch in `providers/registry.py`.

## 7. Extending stage coverage

Every treatment-bearing stage group (I → IVB) ships with a module today; only
occult carcinoma (TX N0) is intentionally unrouted. Adding or specializing a
module (e.g. a dedicated Stage 0 / AIS module, or splitting the two IIIA arms
— currently both live inside `stage3a.md` behind its resectability gate — into
separate files) is a four-step, localized change:

1. Drop `prompts/<key>.md` (the system-prompt protocol) into `prompts/`.
2. Register it in `prompts/__init__.py::MODULES` with its stage groups.
3. Point the relevant stage groups at it in
   `staging/router.py::_STAGE_TO_MODULE` (e.g. `"IIIA": "stage3a"`).
4. Add an example case and a routing test.

No changes to the staging engine or provider layer are needed — that separation
is the whole point. (Stages I and IIIA were added exactly this way, touching
only the prompt loader, router, tests and examples.)

## 8. Testing strategy

- `test_staging.py` — the 9th-edition table, all migrations, ambiguity
  rejection, normalization. `selftest.py::EXPECTATIONS` is the single source of
  truth shared by the CLI `selftest` and pytest.
- `test_router.py` — routing availability and prompt loading.
- `test_providers.py` — factory, URL/header construction for each backend,
  env-var secret resolution, response parsing (no network).
- `test_agent.py` — end-to-end through the mock, including stage/label mismatch,
  IIIA fallback, and every shipped example case.
- `test_imaging.py` — the perception layer: image loading, JSON extraction, the
  propose→verify contract (seed missing descriptors, flag discordance, keep the
  case value authoritative), descriptor-vocabulary validation, the next-step
  hint, and graceful read failure — all via a fake in-process vision provider.
- `test_consult.py` — the consultation: slot schema invariants, VOI ordering
  and gating, the stopping rule, deterministic extraction (both languages),
  ambiguity preservation, session serialisation, and end-to-end interviews.
- `test_evidence.py` — retrieval config, deterministic query planning, the
  three prompt states, and the guarantee that queries come from the computed
  stage rather than from model output.
- `test_cli.py` — the argparse front-end, in-process, including the
  consultation and the error exit codes.

All 353 tests run fully offline.
