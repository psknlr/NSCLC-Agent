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

All 209 tests run fully offline.
