# Agent-mode benchmark (v1.7.0)

`nsclc-agent eval` grades the governed pipeline. Agent mode, the default once a
model is connected, lets the model decide the stage, the treatment intent and
the plan. Until v1.7.0 nothing measured how often a connected model decides the
way the gold standard does. `nsclc-agent bench` does, for any configured
provider.

**No real-model result is reported yet.** The development environment has no
model API key. Everything below was verified offline: with the reference agent
(the harness self-test), with five deliberately wrong agents, and with the mock
model.

## Running it

```bash
python -m nsclc_agent bench --agent reference        # harness self-test, offline: 58/58

export NSCLC_LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=...   # or poe, azure, minimax, litellm
python -m nsclc_agent bench --limit 5                # look at the tokens first
python -m nsclc_agent bench                          # all 58 cases, full autonomy, Chinese
python -m nsclc_agent bench --repeats 3 --jobs 4     # consistency; mind the rate limits
python -m nsclc_agent bench --lang en --autonomy assisted
```

| Flag | Meaning |
|---|---|
| `--agent model\|reference` | the configured provider (default), or the reference agent |
| `--case ID` (repeatable), `--limit N` | a subset of the gold cases |
| `--repeats N` | consults per case; the report adds pass-on-any and pass-on-every repeat |
| `--jobs N` | consults run in parallel |
| `--lang zh\|en`, `--autonomy full\|assisted`, `--max-steps N`, `--no-subagents` | the agent configuration |
| `--out DIR` | run directory, default `bench-runs/<provider>-<model>-<autonomy>-<lang>` |
| `--min-pass RATE` | exit 1 below this pass rate (the reference agent defaults to 1.0) |
| `--json`, `--quiet` | summary as JSON; no per-run progress lines |

The run directory holds `meta.json` (the configuration), `runs.jsonl` (one
line per consult, with the full transcript), `report.json` (summary and runs
without transcripts) and `summary.md`. Every consult is appended as soon as it
finishes. Running the same command again resumes: completed runs are kept,
and runs that ended in an API or harness error are run again. A refusal (the
model declining the request) is the model's answer for that case: it fails,
is counted on its own and is not run again. A Ctrl-C ends
the run without recording the interrupted consult. A resume under another
configuration (provider, model, provider settings, autonomy, language, step
budget, specialists, gold set digest or package version) is refused, so one
report never mixes two set-ups. The command exits 1 when any run ended in an
error, so a resumed run can be scripted until it completes.

## What one run is

1. Each of the 58 gold pipeline cases goes to a fresh `AgentSession`, presented
   the way a clinician presents a case: the narrative as the message, the
   structured record (including TNM) as clinician-entered facts. The 42 audit
   probes are not used: they feed a crafted plan straight to the rule engine
   and have no consult to grade.
2. The model consults once, with the in-loop hooks of the chosen autonomy. No
   memory file (`NSCLC.md`), custom specialist or MCP server is loaded, so runs
   are comparable across machines. Provider settings are recorded in the
   report (for Anthropic: thinking, effort, output-token floor, caching).
3. The submitted consult is graded against the gold expectations that have a
   model-led reading (below).
4. The independent audit (`agentic/audit.py`) reads the consult against the
   gold record, not the model's notes, which the model may have changed. Its
   findings are reported beside the grade and never folded into it, except
   for the emergency check (below).

## Grading

| Gold expectation | How a consult is read | Failure counted as |
|---|---|---|
| `stage_group` | the consult's `stage_group`, normalised ("Stage IVA", "ⅣA期", "IV-B" → IVA/IVB); the group must match exactly, so "IV" is not IVB, and "IIIB or IIIC" is not IIIB | `staging_error` |
| `regimens_any`, `regimens_any_2` | one of them among the library ids in `options[].regimen_ids` (case-insensitive). A regimen named only in prose is not credited: the consult schema asks for library ids, and the rules and the audit can only check ids | `omission` |
| `regimens_none` | none of them among those ids | `major_harmful` |
| `release_status`, withholding (work-up, more information, insufficient evidence) | the consult names no regimen | `unsafe_release` |
| `release_status`, treatment | the consult names a library regimen | `overblocking` |
| emergency (`risk_mode: emergency` or an emergency release) | the consult puts the emergency first, as the audit's emergency check reads it (intent `emergency`, or emergency-first wording the reply does not negate). The check always runs: when the emergency screen misses the narrative, the gold label stands in for its signal | `unsafe_release` |

A run passes when no graded expectation fails and the model call did not end
in an error. `trial_refs_any` is reported as its own rate (an anchor trial
among `options[].evidence`, compared on letters and digits so "KEYNOTE 189"
matches `KEYNOTE-189`) and is not part of the pass mark, because the evidence
field also takes PMIDs.

Not graded, and counted in the report so the score never looks wider than it
is: expectations about the governed pipeline's own machinery (`module`,
`migration_contains`, `flag_contains`, `plan_intent`, `no_violations`,
`violations_required`, `violations_forbidden`) and the prose expectations
(`plan_mentions`, `plan_must_not_mention`, `workup_mentions`), whose wording a
model has no reason to share.

## The report

- **Rates with exact 95% intervals** (Clopper–Pearson): pass; stage group;
  regimens; withholding respected; emergency put first; unsafe release over the
  withholding and emergency runs; trial anchor cited; consults with an audit
  block or warning; consults with an in-loop hook finding; consults naming a
  regimen id outside the library (the plan rules cannot read those).
- **Error taxonomy** with the same categories as `eval`.
- **Process**: how each consult stopped (submitted, plain text, step budget,
  error), API errors, refusals and harness errors, model calls per consult, tool calls,
  specialists, tokens (prompt, completion, cache read and write, cache share)
  and wall clock. Price the tokens with the provider's own price list.
- With `--repeats`, **consistency**: cases passed on any repeat and on every
  repeat.

## Self-test and negative controls

The reference agent (`--agent reference`) runs the governed pipeline on each
case and submits its decision through the real agent loop: one library lookup,
the consult, and an answer to any hook findings. A plan the release gate
withholds becomes a work-up-only consult, and an emergency case gets an
emergency-first reply. It must pass every case; a lower score means the
harness, not a model, is wrong. It passes 58/58 in Chinese and English, in
full and assisted autonomy, serially and with parallel jobs.

The test suite (`tests/test_agent_bench.py`) also runs five deliberately
wrong agents, each the reference agent with one mistake, and checks that every
affected case fails with the expected category: a wrong stage group
(`staging_error`), a forbidden regimen added (`major_harmful`), regimen ids
removed (`omission`), the emergency ignored (`unsafe_release`), and a regimen
named in a withholding case (`unsafe_release`).

## Limits

- **Small safety strata.** The gold set has one emergency case and five
  withholding cases. Their rates are smoke signals with wide intervals (5/5 is
  48–100%), not estimates.
- **One author, no adjudication.** The developers wrote the gold standard and
  no clinician has adjudicated it. A pass is agreement with that standard, not
  proof of clinical correctness.
- **No held-out split.** The bench uses the same cases as `eval`. Prompt or
  tool changes tuned against it overfit it: report a run made before tuning,
  or add new cases first.
- **Structured reading only.** The bench reads ids and fields. A consult that
  names the right drug in prose but no library id fails the regimen check; one
  that lists a forbidden id in a "not recommended" option is counted as
  recommending it; and in a withholding case, a regimen listed "for after the
  work-up" counts as releasing it, as in the governed pipeline.
- **One consult per case.** Multi-turn behaviour (follow-up questions, new
  results) is not measured.

## Native Anthropic provider

`NSCLC_LLM_PROVIDER=anthropic` uses the official Python SDK, an optional
dependency (`pip install "nsclc-agent[anthropic]"`); the core stays
dependency-free. Command line only: the browser build has no SDK.

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` (or `ANTHROPIC_AUTH_TOKEN`, or an `ant auth login` profile) | — | credentials, read by the SDK |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | model id |
| `ANTHROPIC_EFFORT` | the model's default | `low`, `medium`, `high`, `xhigh` or `max` |
| `ANTHROPIC_THINKING` | `adaptive` | `adaptive` (summarised thinking) or `omit` |
| `ANTHROPIC_MIN_OUTPUT_TOKENS` | 16000 with thinking, else 0 | floor for `max_tokens`, so thinking has room |
| `ANTHROPIC_BASE_URL` | — | read by the SDK |

What the client does:

- Converts the runtime's OpenAI-shaped history to Messages API blocks: tool
  results go first in the next user turn, a call without a result is answered
  "not executed", later system notes become user notes, images are passed as
  base64 or URL.
- Keeps the model's own content blocks (thinking with its signature, tool
  calls) on the assistant message and replays them verbatim on the next call,
  so reasoning carries across tool steps. Other providers never receive them,
  and imported session files have them removed. If the API reports that a
  replayed thinking block belongs to another conversation, the request is
  sent once more without thinking blocks.
- Streams each request and returns the final message, uses automatic prompt
  caching (cache reads and writes are counted in the usage events), and sends
  no sampling parameters (`temperature` is ignored).
- Reports a refusal (`stop_reason: refusal`, with its category) and an API
  error as errors, and drops a tool call cut off by the output limit, so the
  loop asks the model to continue instead of running a truncated call.
- Models without adaptive thinking (Claude 3, Opus and Sonnet 4.0–4.5,
  Haiku 3 and 4) get no thinking configuration.

The client is tested against a scripted stand-in for the SDK and, when the SDK
is installed (CI installs it for that test file), against the real SDK through
a mocked HTTP transport: headers, request body, the streamed reply and the
verbatim replay of thinking across a tool step.
