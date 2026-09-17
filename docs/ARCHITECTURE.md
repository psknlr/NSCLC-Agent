# NSCLC-Agent v0.2 Architecture

This document explains how the fused framework is structured, which invariants
the control plane enforces, and where each design element came from
(NSCLC-Agent v0.1's verifiable clinical core vs YaoBi-Harness's containment
architecture).

## 1. Layering

```
   ┌───────────────────────────────────────────────────────────┐
   │ delivery: render() per role (patient/oncologist/researcher)│
   └────────────────────────────┬──────────────────────────────┘
                 cognition (replaceable, advisory)
   ┌───────────────────────────────────────────────────────────┐
   │ PlannerAgent(LLM) · ToolLoop per autonomous skill          │
   │ InterviewLoop(ask_case_question) · MDT PanelAgent          │
   │ PerceptionAgent (vision) · CriticAgent LLM additions       │
   └────────────────────────────┬──────────────────────────────┘
                                │  proposals only
   ┌────────────────────────────▼──────────────────────────────┐
   │ control plane (never bypassable)                           │
   │  plan validator · CapabilityBroker · SkillRegistry         │
   │  AdequacyJudge · Budget (locked) · ToolHealth breaker      │
   │  Evidence ledger (tool-declared grades) · CitationGuard    │
   │  safety rule engine · release-status machine · Journal     │
   └────────────────────────────┬──────────────────────────────┘
   ┌────────────────────────────▼──────────────────────────────┐
   │ deterministic core                                         │
   │  TNM-9 engine · stage router · trial registry              │
   │  regimen library (dose channel) · DDI pack                 │
   │  emergency screen · interview axes · protocol modules      │
   └───────────────────────────────────────────────────────────┘
```

Dependencies flow strictly downward. `staging/` and `knowledge/` depend on
nothing above them; `agents/` composes; `runner.py` orchestrates.

## 2. Control-plane invariants

1. **The stage is never the model's.** Only `StagingAgent` writes
   `state.staging`, and it only ever writes what the symbolic engine
   computed. The engine refuses ambiguity (bare T1/T2/N2/M1c, missing M,
   NX/MX, non-AJCC9 editions) with messages that name the resolving test —
   a refusal is the seed of the workup plan, not a dead end.
2. **The critic is terminal and unconditional.** `NSCLCRunner.run` invokes
   `CriticAgent` in a `finally`; it observes failed-closed runs and runs
   where every clinical task was skipped.
3. **Policy before budget.** `CapabilityBroker.allow` checks breaker health →
   risk mode → role → skill grant, and only then the budget; the budget is
   charged only after a call actually executes, so a denied call can never
   drain it.
4. **Skills fail closed.** No declared skill (or a skill absent from the
   registry) = no tool rights. Empty `allowed_tools` means "no tools".
5. **Evidence grade is declared by the producing tool.** Stubs land as
   `stub_not_for_clinical_use`; model output as `model_reasoning`. Neither
   can support a released claim (`NON_RELEASABLE_LEVELS`), and the
   CitationGuard enforces that at audit time.
6. **Doses live in exactly one place.** The regimen library's `detail()` is
   reachable only through the dose channel (`nsclc.dose_planning` skill,
   oncologist role, explicit opt-in, non-blocked interview). Reasoning
   skills see `summary()` (numeric-free); the tool loop rejects model output
   matching the dose regex with **no** repair turn; the rule engine re-scans
   the final plan (library identifiers scrubbed first).
7. **Asking has rule-decided scope.** Axis tiers decide what is *required*
   (RED_FLAG always; STAGING before treatment; BIOMARKER before systemic
   commitment, with the squamous carve-out); the model decides wording and
   order; the `AdequacyJudge` decides when asking may stop. A `blocked`
   verdict (red-flag axis unanswered) is never waivable and holds the dose
   channel shut even in single-pass batch runs.
8. **The panel is conservative and reproducible.** Members run concurrently
   into private `MemberScope`s, merged in convened order so evidence ids are
   roster-determined; synthesis takes the maximum urgency, unions concerns,
   preserves dissent verbatim; no member can reach a dose tool (skill grant
   ∩ broker, checked independently).
9. **A journal supplies data, never permission.** Authorisation is re-derived
   live before a recorded result is consulted. Divergences are latched on
   the journal object and the runner fails closed at the end — an agent's
   broad `except` cannot convert a failed replay into a "fell back to rules"
   note. `llm_available` is recorded in the meta so a run recorded without a
   model replays without one.
10. **Truncation is a failure mode.** `finish_reason == "length"` becomes
    `output_truncated`, never a parsed-looking result; protocol modules are
    served through `protocol_lookup` in sections instead of being inlined
    into the prompt, and each module declares `min_output_tokens`.
11. **Parallelism never costs reproducibility.** The Treatment∥Panel wave
    runs each agent against a `_WaveScope`; scopes merge in task order, so
    evidence ids depend on the plan, not the scheduler (tested: parallel and
    serial runs produce identical ledgers). Journaled runs are always serial
    — the journal is a single ordered lane. The wave panel reviews the case
    WITHOUT the treatment plan in context (independent, anchor-free), and
    `run_meta.execution` records which mode ran.
12. **A report photo is a proposal, not a chart.** The report reader seeds
    only *missing* facts, tracks every seeded path in
    `facts["_report_proposed"]`, cross-checks (never overwrites) existing
    values, vocabulary-validates any TNM mention, and the dose channel stays
    shut while a Tier-A biomarker rests on a report proposal — confirming
    the source document and resuming is what re-opens it. The vision client
    auto-selects (Poe→Gemini on a bare `POE_API_KEY`) so attaching an image
    is the entire integration; auto-selection is recorded provenance.
13. **Chat is not a generation channel.** (`ConsultationSession`, the ported
    YaoBi conversation layer.) Every turn is a complete audited run over the
    accumulated narrative and facts — same runner, broker, ledger, terminal
    critic. Fact intake is allowlisted and engine-validated on every path
    (free text, model extraction, the explicit facts parameter): sign-off
    keys, release status and `_`-prefixed guard keys are unreachable from
    chat, and a bare `N2` typed into chat is refused exactly as everywhere
    else. Free text fills gaps only (`CHAT_FACT_CONFLICT` — the record beats
    hearsay); explicit operator facts overwrite, and landing on a
    `_report_proposed` path — even restating it verbatim — is the logged
    confirmation (`PROPOSED_FACT_CONFIRMED`) that re-opens the dose channel.
    The guard itself is re-applied by the session on every turn
    (`run_case(internal_facts=…)` after the external-input strip), so it
    survives the conversation, not just one run. The emergency screen runs
    on the full narrative each turn and its script is never rephrased; the
    outgoing reply is dose-scanned unconditionally, and a polish pass that
    introduces a dose numeric is discarded wholesale.
14. **Plan reuse is fingerprinted, re-anchored and re-audited.** A
    pure-question turn (no changed decision facts, no new attachments) hands
    the previous plan to the TreatmentAgent with a byte-stable fingerprint of
    the facts that produced it; the agent reuses it only on an exact match,
    re-adds the cached evidence rows to *this* run's ledger with their
    original tool-declared grades (citation ids are ledger-local and never
    travel), and the terminal critic re-audits the reused plan in full. The
    cache is consumed before the decision, so a critic-requested repair pass
    always re-plans for real. Session memory (one `InterviewLoop`, read
    attachment refs, accumulated facts) is what makes later turns fast:
    images are read once per session, closed axes are never re-asked.
    Sessions persist (`save`/`load`, CLI `chat --session`): the file is
    harness-authored state in the same trust class as a checkpoint — it
    carries memory (facts with the `_report_proposed` guard, read refs,
    the plan cache with its evidence rows, the interview transcript and
    stall history) and **never authorization**; every resumed turn still
    passes the same broker, gates and terminal critic live.
15. **The guideline KG informs; it never authorizes, doses, or vetoes.**
    The shipped computable-guideline store (6 guidelines, 2,960
    machine-extracted recommendations, 147 cross-region agreement
    clusters) is served through `guideline_lookup` under three rules.
    Its curation status IS its evidence grade: `llm_extracted` content
    lands as `kg_llm_extracted`, a NON-RELEASABLE level — the KG points at
    trial IDs and PMIDs whose verification (`trial_lookup` /
    `citation_verify`) is what produces releasable support, and a
    clinician-verified entry upgrades to GUIDELINE automatically. Every
    served payload is deep-scrubbed with the rule engine's own `DOSE_RE`
    (field-by-field scrubbing leaked through the retrieval keys once; the
    boundary test now sweeps every served string of every entry). And the
    quoted prose lives in `outputs["guideline_context"]`, never inside the
    plan: the rule engine scans the plan as the system's own words, so a
    quoted "durvalumab … after concurrent CRT" cannot false-trip
    NO_CONCURRENT_DURVALUMAB — while everything inside the plan stays
    scanned, so a model cannot smuggle prose past the critic under the
    same key. Negative knowledge (`do_not_recommend`/`avoid`/
    `contraindicated`) is surfaced as case-matched cautions for humans to
    weigh; blocking power remains exclusively with the deterministic rule
    engine.
16. **Criteria evaluate; humans adjudicate; hard use is earned by review.**
    The KG's extracted population criteria are deterministically evaluated
    against the case (`kg_eligibility`) under three noise rules derived
    from observed extraction faults: the source span outranks the
    normalized value, a conflict between them abstains rather than
    guesses, and an assumed/negated/flipped criterion can never produce
    the confident mismatch that selection acts on. For `llm_extracted`
    entries the verdicts are annotation and ranking only — a confidently
    mismatched extraction is de-ranked but stays visible, labelled. The
    per-entry curation workflow (`kg-review`) is what unlocks hard use:
    reviews live in an append-only ledger, content-hash-pinned to exactly
    the entry reviewed (content change voids the review — the journal
    discipline applied to knowledge), reversible by appending, loud on
    corruption. A `clinician_verified` entry is served at guideline grade
    (releasable), outranks unreviewed extraction, and its now-trusted
    criteria gain two — and only two — hard effects: a confident
    population mismatch excludes it from case context *visibly*
    (`excluded_verified_mismatch`), and a case-matching verified caution
    raises a `KG_VERIFIED_CAUTION` flag. Both are advisory surfaces;
    blocking power still belongs exclusively to the rule engine, and the
    dose scrub applies to verified content unchanged.
17. **Prognosis is population context; hypotheses change nothing real.**
    "Survival prediction" is served honestly: published stage-cohort
    figures (IASLC staging-project database, per-figure approximation and
    edition-migration caveats, unreported groups left blank) at the
    releasable `published_cohort_statistics` grade, plus DIRECTIONAL
    prognostic modifiers whose effect sizes stay in the trial registry —
    anchored via `trial_lookup`, cited, never restated or recombined into
    a per-patient number. The figures are clinician-facing: the patient
    view never carries them, and a patient asking about survival receives
    a supportive, numberless pointer to their treating team. What-if
    scenarios (`ConsultationSession.what_if`, chat `/whatif`) run the
    full audited pipeline over a hypothetical copy: session memory,
    the interview loop, the plan cache and the `_report_proposed` guard
    are untouched (a hypothesis is not a confirmation), the dose channel
    never opens in a scenario, and hypothetical emergency phrasing obeys
    the screen's hypothetical suppression while stated events escalate
    inside the scenario only.
18. **"Positive" is not a treatment decision.** External clinical red-team
    review proved the failure mode this invariant closes: planner and
    critic shared a boolean gene model and jointly released osimertinib
    for an EGFR exon20 insertion and pembrolizumab for ROS1+ disease.
    The driver ontology (`knowledge/biomarkers.py`) makes variant classes
    first-class — EGFR ex19del/L858R vs uncommon vs exon20ins vs
    T790M/C797S; MET counts only as exon-14 skipping, BRAF only as V600 —
    and the full first-line actionable plane
    (EGFR/ALK/ROS1/RET/MET-ex14/BRAF-V600E/NTRK) is enforced twice from
    the same vocabulary with independent logic: the planner routes each
    class to its evidence population (unclassified fails toward the
    molecular tumor board, never toward a guessed drug), and the rule
    engine blocks variant-mismatched regimens (`EGFR_VARIANT_MISMATCH`)
    and ICI-first plans over any actionable driver (`DRIVER_FIRST_LINE`)
    even when the planner is the one that erred. Trial boundaries are
    TNM-edition-aware (`staging/legacy8.py`): a case whose descriptors
    fall inside the trial's 8th-edition enrollment is an edition
    migration (note), not an extrapolation (block) — while the 9th-edition
    engine remains the sole authority for the case's actual stage. And
    prose never redefines disease concepts: AIS=Tis=stage 0,
    MIA=T1mi=IA1, from one source of truth (`staging/concepts.py`).

## 3. LLM containment table

| Capability | Model may | Model may not |
|---|---|---|
| Planning | propose a task graph (tolerant shapes) | invent agents, cycle deps, schedule dose/panel agents without authority, skip StagingAgent |
| Staging | query `stage_lookup` hypotheticals | write `state.staging`; override or re-derive the run's stage |
| Tool use | choose tools/arguments within its skill; self-correct recoverable errors | see or reach a tool outside the skill; skip the broker; trip the breaker with argument typos |
| Treatment | draft the plan, cite ledger evidence ids, declare extrapolations | emit dose numerics; cite unverifiable trials silently; cross trial stage boundaries undeclared |
| Interview | word/order/deepen questions; add axes; propose completion | skip a required axis; embed advice or doses in questions; decide that asking stops |
| Panel | reason in a speciality view; raise urgency; dissent | reach dose tools; lower another member's urgency; write release status |
| Vision | propose descriptors within the engine vocabulary | assign a stage; propose refused descriptors (rejected at ingestion); stand in for the radiologist (`requires_confirmation` schema-pinned true) |
| Critique | add issues | remove or downgrade any rule-engine finding |
| Chat | extract allowlisted facts from a turn; polish a released reply | set sign-off/guard keys; overwrite the record from free text; rephrase the emergency script; introduce dose numerics (polish discarded); add clinical content |
| Doses | nothing | anything |

## 4. The safety rule engine

Twelve deterministic rules run over (engine staging, structured facts, parsed
plan): `N3_NO_SURGERY`, `DRIVER_EXCLUDES_PERIOP_IO`, `EGFR_III_CONSOLIDATION`
(LAURA vs PACIFIC), `NO_CONCURRENT_DURVALUMAB` (PACIFIC-2),
`NO_RT_DOSE_ESCALATION` (RTOG 0617), `TRIAL_STAGE_BOUNDARY` (data-driven from
the trial registry; a *declared* extrapolation downgrades to a warn),
`STAGE0_NO_SYSTEMIC`, `DRIVER_FIRST_LINE`, `ICI_COMORBIDITY_CAUTION`,
`PS_GATE`, `BIOMARKER_GAP`, `DOSE_IN_MODEL_OUTPUT`. Block-severity violations
set `release_status = blocked` and issue bounded repair requests.

These are the rules the v0.1 prompts stated in prose and hoped for; here the
critic executes them on every run, including the deterministic path's own
output (the rule-mode planner must satisfy its own rule engine — tested).

## 5. Evidence and citations

Ledger grades: `observed_fact` < `pathology_confirmed` /
`deterministic_staging` / `registered_trial` / `guideline_or_label` /
`live_retrieval` / `tool_result`, with `model_reasoning`, `stub…`, `failed…`
non-releasable. `citation_verify` resolves registry trial ids and their NCTs
offline; PMIDs and foreign NCTs verify live only when the operator sets
`NSCLC_AGENT_ONLINE=1`. The CriticAgent verifies every `trial_refs` entry and
requires a regimen-bearing plan to cite releasable ledger evidence.

## 6. Run loop

```
run_case → IntakeAgent (emergency screen; sets risk_mode; closes negated axes)
  ├─ emergency → EmergencyAgent (fixed script) ── critic ── finalize
  └─ routine  → PlannerAgent (LLM proposal validated | deterministic default)
                → execute tasks (interview → perception → staging → treatment
                                 → panel? → dose?) with per-agent brokers
                → CriticAgent → repair loop (≤ budget.max_loops)
                → finalize (release ladder + run_meta with module sha256)
```

Checkpoints per node; `resume_run` reopens unfinished work so new facts
(biomarker results, answered questions) move a run forward — but a
failed-closed run stays failed closed.

## 7. Testing strategy

- `test_staging.py` — the stage table + the refusal table (both are contract).
- `test_rules.py` — every rule, both directions (fires + stays silent).
- `test_tools.py` — broker order, fail-closed grants, dose-channel isolation,
  honest stubs, alias resolution, recoverable-vs-failed calls.
- `test_toolloop.py` — scripted-LLM containment: dose leak no-retry, one
  repair turn, truncation failure, hallucinated tools, citation filtering.
- `test_interview.py` / `test_emergencies.py` — axis coverage, blocked
  unwaivability, question validation gates, clause-scoped negation.
- `test_journal.py` — replay fidelity, divergence latch, re-derived authority.
- `test_panel_planner.py` — conservative merge, roster-deterministic ledger,
  plan shape tolerance + wholesale rejection.
- `test_runner.py` — end-to-end: rule mode, mock-agentic mode, emergency
  short-circuit, dose gating, checkpoint/resume, journal replay divergence.
- `eval/` — 16 golden decision cases with machine-checkable expectations;
  the same expectations grade any configured real model.

All 271 tests run fully offline (including the adversarial-review regression suite in test_hardening.py).
