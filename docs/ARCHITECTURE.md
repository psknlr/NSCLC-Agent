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

## 2. Two modes

The harness runs in one of two modes.

* **Governed mode** (the only mode without a model, selectable with one):
  the invariants of §3 are hard guarantees — the model proposes, the
  control plane disposes.
* **Agent mode** (`nsclc_agent/agentic/`, the default once a model is
  connected): the model LEADS, inside an agent runtime built on the same
  patterns as mainstream agent CLIs (Claude Code, Grok CLI, Codex, the
  OpenAI Agents SDK) and specialised for a multidisciplinary consult.

### 2.1 Agent runtime (v1.0)

```
            user message ──► user_prompt_submit hooks (emergency screen, note seeding)
                                   │  context for the model · alerts for the clinician
                                   ▼
 ┌────────────────────────── lead agent (run_loop) ──────────────────────────┐
 │  system prompt = role + roster + MCP tools + memory (NSCLC.md), rebuilt    │
 │  every turn · user content = message + hook context + plan + case notes    │
 │                                                                             │
 │  model ─► thinking / text / tool calls ─► observations ─► model …          │
 │            │ read-only tools run concurrently (natively), writers in order  │
 │            ├─ clinical tools (20, AgentToolbox)  ── post_tool_use hooks     │
 │            ├─ update_plan (live consult plan)        (evidence ledger)      │
 │            ├─ delegate ──► specialist sub-agent (run_loop, own context,     │
 │            │               own read-only toolset, submit_report) × N ∥      │
 │            ├─ remember (memory proposal → clinician decides)               │
 │            ├─ mcp__<server>__<tool> (Streamable HTTP MCP servers)          │
 │            └─ submit_consult (terminal) ──► stop hooks: rule engine, stage  │
 │                consistency, citation provenance, dose provenance,           │
 │                emergency addressed ─► findings back to the model once:      │
 │                revise, or answer each in rule_responses                     │
 └─────────────────────────────────────────────────────────────────────────────┘
        checkpoint before every turn (messages, notes, plan, ledger) → rewind
        context estimate → model-written compaction near the window
        every step → typed events (CLI timeline · stream-json · web trace)
```

| Concern | Design |
|---|---|
| Tools | `Tool` (schema, handler, `parallel_safe`, `terminal`, label) and per-agent `Toolset`s. A failing tool is an observation, never an exception. |
| Loop | One `run_loop` for the lead and every specialist: reasoning capture (`reasoning_content`, `<think>`), concurrent read-only calls, ordered writers, terminal tool last, one nudge for plain-text answers (a plain answer is still an answer), truncation continue, `Cancelled` with `repair_history` so the transcript stays valid for every provider; the trace keeps call order whatever order tools finished in. |
| Planning | `update_plan` — a checklist that survives turns and is put in front of the model each turn. |
| Sub-agents | `delegate` runs a specialist with its own system prompt, a fresh context (task + case notes) and an allow-listed read-only toolset; it cannot write notes, delegate or submit. Only its structured report enters the lead's context; its full trace is kept as `children` of the delegate step. Custom specialists are front-matter Markdown files. |
| Hooks | `user_prompt_submit` / `post_tool_use` / `stop`, each individually switchable. Every hook is advisory; a crashing hook becomes a `HOOK_ERROR` finding. |
| Memory & context | Instruction files in every system prompt; `remember` only proposes. Provider-neutral token estimate; compaction summarises older turns with the model (deterministic fallback) and drops checkpoints before the cut. |
| Checkpoints | Taken before every turn; `rewind(turn)` restores messages, notes, plan, ledger and memory proposals, and returns the rewound message for the composer. |
| Commands | One definition for CLI and web: prompt commands expand to an instruction for the agent, local commands run in the session (`local_command`). |
| MCP | JSON-RPC over HTTP POST, JSON or SSE replies, `Mcp-Session-Id`; urllib natively, synchronous XHR in the browser worker. |
| Concurrency | Threads natively (parallel tools and specialists, thread-safe usage accounting, serialised stream-json output); strictly serial in Pyodide (`platform_caps.THREADS_AVAILABLE`). |
| Interruption | CLI: Ctrl-C ends the turn (`KeyboardInterrupt` → cancelled, history repaired). Web: the page terminates and restarts the worker; the case resumes from its last exported session. |
| Authority | Role and runtime configuration come from the surface; a session file (format `nsclc-agent-session/2`, `/1` still readable) supplies data only: facts and checkpoint facts are re-validated, the system prompt is rebuilt, inconsistent checkpoints are dropped. |

### 2.1a Decision mode (v1.3)

`AgentConfig.autonomy` decides who makes the clinical decisions in agent mode.

| | `full` (default) | `assisted` |
|---|---|---|
| Stage, TNM, treatment intent, plan | the model decides | the model decides; the kernel is offered as a second opinion |
| Kernel decision tools (`stage_tnm`, `assess_biomarkers`, `check_indication`, `check_organ_function`, `cns_assessment`, `later_line_options`, `rule_review`, `governed_reference`, `screen_emergency`) | not offered (lead and specialists) | offered |
| Information tools (trial registry, regimen library and reference doses, guideline KB, clinical pathways, PubMed, citations, prognosis cohorts, interactions, attachments) | offered | offered |
| Kernel hooks (`fact_seed`, `stage_consistency`, `rule_review`) | off by default | on |
| Safety and provenance hooks (`emergency_screen`, `evidence_ledger`, `citation_provenance`, `dose_provenance`, `emergency_addressed`) | on | on |
| Engine stage in the case notes / turn result | never | shown next to the model's stage |
| Tools' default stage | the `stage_group` the model recorded | the engine's staging of the notes |

`submit_consult` carries the model's `stage_group`, `tnm`, `stage_rationale`,
`intent` (curative / palliative / supportive / emergency / undetermined) and
`intent_rationale`; a missing or unknown intent is recorded as
`undetermined`, never inferred. Explicit hook settings always win over the
autonomy defaults. Surfaces: web settings 「决策方式」, CLI `--autonomy`.

### 2.2 Languages (v1.1)

Localisation happens at the display edge and never inside the clinical kernel.
`nsclc_agent/i18n/en.json` is one dictionary shared by the browser and by
Python. The browser copy is placed next to the app by `web/build.py`. Both
sides apply the same steps: exact phrases first, then word-order rules, then
longest-first substring phrases, then the English half of bilingual kernel
strings. Model output and clinician input are never translated (`raw()` in
the web app).

In English mode, the runtime appends an "Output language" directive to the
lead and specialist system prompts. Hooks, local commands, plan, memory and
delegate summaries, and the mock model are bilingual. The emergency screen
treats English negation NegEx-style (the cue must precede the symptom) and
matches third-party and hypothetical cues on word boundaries.

## 3. Control-plane invariants (governed mode)

1. **The stage is never the model's.** Only `StagingAgent` writes
   `state.staging`, and it only ever writes what the symbolic engine
   computed. The engine refuses ambiguity (missing M, NX/MX, non-AJCC9
   editions, and a bare T1/T2/N2/M1/M1c whose subcategories would stage
   differently) with messages that name the resolving test — a refusal is
   the seed of the workup plan, not a dead end. A bare family whose
   subcategories all give the same group is staged with a note (bare M1c
   is IVB whether M1c1 or M1c2), and the record keeps the descriptor as
   documented.
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
19. **A regimen's population is declared once, machine-executably.** Every
    library regimen carries an indication predicate
    (`knowledge/indications.py`: stage with 8th-edition back-mapping,
    histology, driver class, no-actionable-driver, PD-L1 thresholds,
    resectability, operability, oligometastatic status, prior therapy)
    evaluated in three-valued logic. **Unknown routes to workup, never to
    a guess**: the planner keeps such an option only as provisional with
    the missing facts pushed into the workup list; the critic warns with
    the facts named. The single declaration is evaluated at three points —
    the planner's `opt()` gate (ineligible dropped loudly as a
    table↔declaration divergence; a declared extrapolation is honored),
    the published `outputs["indication_report"]`, and the critic rule
    `INDICATION_PREDICATE` (ineligible → block; an undeclared — including
    hallucinated — regimen id → warn). A sweep test runs every golden
    case and asserts the decision table never has to drop its own
    proposal, so table and declarations cannot drift apart silently.
    This closed audit gaps no prior rule saw: pembrolizumab monotherapy
    at TPS 20%, squamous disease on a pemetrexed backbone.
20. **A citation supports one claim, not the whole plan.** Claims carry a
    structured subject (intervention regimen ids, population stage,
    intent) and a support relation; each treatment-option claim cites
    only the trial-registry rows whose trial covers its own regimens
    (structural entailment, deterministic), regimen-free options are
    protocol-grounded and borrow nothing, and prognosis claims are
    population statistics. The critic's claim guard verifies the
    relation per claim: dangling evidence ids, regimen-bearing claims
    with no releasable entailed support, and citations borrowed from a
    different claim are each named (`CLAIM_DANGLING_EVIDENCE` /
    `CLAIM_UNSUPPORTED` / `CLAIM_SUPPORT_MISMATCH`). Entailment is
    two-layered: the structural layer proves a citation covers the
    claimed REGIMEN; the population-semantic layer (v0.3.4) proves it
    covers the claimed POPULATION. Claim subjects carry the population's
    facts (stage + TNM descriptors, histology, a driver signature of
    positive genes with variant-class tags), and every entailed trial
    row is checked against them: stage within enrollment (edition-aware
    via the same 8th-edition back-mapping the plan rule uses, honoring
    plan-declared extrapolations for stage ONLY — a stage declaration
    never excuses a driver or histology mismatch), driver class
    (including co-occurring resistance classes that remove the case
    from a classical enrollment), gene-level driver requirements,
    EGFR/ALK-excluded trials, and histology restrictions
    (`CLAIM_POPULATION_MISMATCH`). Population-statistic prognosis
    claims must rest on cohort-grade evidence — a trial row is not a
    survival statistic's source (`CLAIM_STATISTIC_SOURCE`). The
    narrowing holds across the parallel wave's temp-id remap and the
    conversation layer's plan reuse; a golden-set sweep pins the honest
    rule-mode baseline at zero claim issues. Evidence TEXT is still not
    semantically verified against claim wording — the honest list says
    so.

21. **The eval grades the safety net itself, and failures are classified
    clinical events.** Golden entries with `audit_plan` bypass the
    planner and feed a deliberately wrong (or deliberately fine) crafted
    plan straight to the rule engine, with `violations_required` /
    `violations_forbidden` expectations — a required block that does not
    fire is counted as `unsafe_release` and reported as its own rate,
    the single number a safety harness must keep at zero. Every eval
    failure carries one of nine taxonomy classes (major_harmful,
    unsafe_release, overblocking, false_alarm, omission, missing_workup,
    incorrect_release, staging_error, routing_error), so "the suite is
    red" always says which kind of clinical event happened. Human
    adjudication of golden cases lives in an append-only ledger that is
    the deliberate inverse of the KG curation ledger: verdicts are
    content-hash-pinned to the case (edits void them) and NEVER
    last-wins — every adjudicator's verdict coexists, disagreements are
    named in the eval report, and disagree/needs_revision require notes.
    Knowledge upgrades converge; clinical judgment preserves dissent.

22. **Outcome figures are never free.** Every outcome-shaped number in a
    high-stakes claim's text — a percentage, a hazard ratio, a month
    span — must be present in the evidence rows that claim cites, or in
    the claimed regimens' own registry entries (deterministic system
    knowledge: protocol durations, thresholds). A figure with no
    provenance is `CLAIM_NUMERIC_UNANCHORED`: a fabricated number
    cannot ride out on a well-cited claim. The extractor targets
    outcome shapes only — doses belong to the dose channel, TNM
    descriptors and stage labels and trial-name digits are structural —
    so structural numerics never false-alarm. The guard checks the
    number's PRESENCE in the cited source, not the wording around it;
    a real number attached to the wrong endpoint is still invisible,
    and the honest list says so.

23. **The CNS is stratified, never assumed — and never ignored.** One
    deterministic reading of the CNS facts (`knowledge/cns.py`) serves
    both the planner and the critic: unstated is unknown (a stage-IV
    plan without CNS status routes a brain MRI to workup and goes
    provisional), a negative brain-imaging statement in prose may seed
    `absent` under the same setdefault discipline as the emergency
    screen's negations, and a POSITIVE imaging statement seeds nothing
    — prose never asserts disease. Strategy stratifies instead of
    prescribing: CNS-active first-line agents may defer local therapy
    with surveillance named; driver-negative brain mets carry the
    local-therapy option; symptomatic untreated disease puts
    CNS-directed care at the head of the option list; leptomeningeal
    disease is an honest boundary whose recommendation IS the
    specialist referral. The critic enforces independently:
    systemic-only planning over symptomatic untreated brain metastases
    (or LM) blocks (`CNS_UNTREATED_SYMPTOMATIC`) whoever authored the
    plan, and brain metastases recorded against an M0 TNM is a named
    contradiction (`CNS_TNM_INCONSISTENT`), never a silent repair —
    the staging engine remains the only staging authority.
    Fractionation, radiosurgery selection and steroid dosing stay in
    the neuro-oncology MDT's channel; nothing here emits them.

24. **Progression ends the first-line table's authority.** One shared
    reading of the treatment history (`knowledge/sequencing.py`) gates
    the switch: sequencing triggers only on EXPLICIT progression —
    exposure without a stated outcome is exposure — and once it
    triggers, the first-line decision table no longer applies. The
    sequencing corpus is small and named (post-osimertinib MARIPOSA-2
    with the KEYNOTE-789 negative result encoded as the reason
    chemo-IO is not the default; post-second-generation-ALK lorlatinib
    distinct from its CROWN first-line population; post-lorlatinib
    chemotherapy with "no established next TKI" said out loud;
    post-chemo-IO REVEL, with KRAS G12C and HER2 options surfacing in
    the line where they apply); progression that maps to none of it
    routes to the molecular tumor board, never to a guess. The EGFR
    resistance-mechanism question (re-biopsy/plasma NGS) is workup
    before options, and the critic is line-aware: re-proposing an
    agent the disease progressed on warns (PROGRESSION_SAME_DRUG —
    rechallenge is a justified strategy, not a default), and
    DRIVER_FIRST_LINE stops mislabeling post-progression questions as
    first-line while still flagging ICI in driver-positive disease
    with the KEYNOTE-789 message. A structured history satisfies the
    prior-systemic indication conditions directly — one fact channel,
    no double entry.

25. **Resistance mechanisms are findings, not guesses.** The
    progression re-biopsy/plasma NGS result is a structured fact
    (`progression_findings`) whose presence means the question was
    ASKED; every unlisted mechanism is false-as-recorded, never
    unknown. Mechanism-directed coverage is exactly two findings deep
    and says so: small-cell transformation switches the biology
    (platinum-etoposide treats the transformed clone, the
    EGFR-directed second line is deliberately absent, and the
    retrospective evidence grade is stated, not laundered); MET
    amplification proposes the INSIGHT-2 continuation, on which the
    same-drug warn fires BY DESIGN — it is the documentation demand
    for continuing a progressed drug, and the continuation regimen
    joins the classical-EGFR family so the variant-mismatch net covers
    it. C797S gets an honest caution (no approved fourth-generation
    TKI) while the chemotherapy backbone stays the evidence-based next
    line — no phantom TKI is ever proposed. The third line requires
    its record: Dato-DXd's declaration carries `requires_prior_platinum`,
    so a plan that skips the platinum line is blocked by the predicate,
    and beyond docetaxel the corpus recommends the honest boundary
    itself — trial screening, best supportive care, goals of care.

26. **The population fits; the body must too — and unknown is neither
    pass nor fail.** Organ-function and comorbidity gates live in ONE
    deterministic evaluator (`knowledge/organ_gates.py`) with three
    consumers: the dose channel's gate check (quantitative thresholds
    replace coarse strings, the label threshold cited in every note),
    the critic's ORGAN_FUNCTION_GATE (a regimen whose gate FAILS on
    the recorded facts blocks at recommendation time, whoever authored
    the plan), and the planner (an organ-failed backbone is dropped
    loudly at the proposal gate and routed to the MDT/pharmacist — the
    dose is never "adjusted around" a failed gate at any layer).
    Unknown never blocks a recommendation and never passes a dose
    gate: recommending is not dosing, so pending gates live in the
    plan's organ-gate ledger — the named list of numbers the dose
    channel will demand — and deliberately not in workup_needed, where
    they would flip the release status of every plan without lab
    values. Thresholds are label-derived teaching values, not an
    institution's protocol, and the honest list says so.

27. **One reading of every fact; no authority from text or files.**
    (v0.7.1 full adversarial audit.) A driver result is parsed ONCE, by
    one clause-scoped parser that every consumer uses (planner, critic,
    indication predicates, chat extractor, prognosis, KG, interview
    axes): negation binds to the variant it sits next to, positive
    requires positive evidence, assay failures and ambiguous reports
    are unknown, and variant classes are read only from positive
    clauses. Anything a plan NAMES must be auditable: an option naming a
    drug must bind it to a library regimen id, unknown ids block, and
    every option's own id list is audited. Content findings gate
    release — claim/numeric guard issues downgrade the status (and
    withdraw a dose draft), and a plan the harness did not release is
    never shown to a patient. No file grants authority: a session file
    carries memory but never role, dose permission or a plan cache; a
    journal proves a run but may not author a deterministic result
    (local tools are re-executed on replay and a mismatch fails
    closed); a curation-ledger upgrade needs a named reviewer, and an
    adjudicator is one person however their name is spelled. Doses are
    scanned after NFKC normalization, whole ranges at a time, through
    one shared scanner.

28. **The browser build is the same harness, not a port.** (v0.8.0,
    GitHub Pages web app by IMPF-AI.) The web app runs this package
    UNCHANGED under Pyodide (WebAssembly Python) in a Web Worker and
    reaches it only through `webapi.call(name, json) -> json` — no
    JavaScript re-implementation of staging, rules, gates or release,
    so every invariant above holds in the page by construction (the
    golden eval runs green inside the browser and in CI under the same
    Pyodide release, `tests/pyodide_smoke.mjs`). Runtime differences
    are scheduling and transport only, behind `platform_caps`: no
    threads → the Treatment∥Panel wave and panel fan-out take their
    serial path (ledger-identical by invariant); no sockets → model
    calls are a synchronous XHR from the worker straight to the
    provider the visitor chose (Poe and MiniMax answer CORS; keys live
    only in worker memory, never in the page, the URL or GitHub). The
    bridge keeps the CLI's contracts: a patient-role run returns — and
    exports — only the patient view; a session import takes role and
    dose permission from the call, never the file; clients are built
    from explicit settings, never the environment. Nothing may import
    a module Pyodide does not ship (`ssl`, `sqlite3`…) at module level
    — a test runs the package with those imports poisoned.

## 4. LLM containment table (governed mode)

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

## 5. The safety rule engine

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

## 6. Evidence and citations

Ledger grades: `observed_fact` < `pathology_confirmed` /
`deterministic_staging` / `registered_trial` / `guideline_or_label` /
`live_retrieval` / `tool_result`, with `model_reasoning`, `stub…`, `failed…`
non-releasable. `citation_verify` resolves registry trial ids and their NCTs
offline; PMIDs and foreign NCTs verify live only when the operator sets
`NSCLC_AGENT_ONLINE=1`. The CriticAgent verifies every `trial_refs` entry and
requires a regimen-bearing plan to cite releasable ledger evidence.

## 7. Run loop

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

## 8. Testing strategy

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
