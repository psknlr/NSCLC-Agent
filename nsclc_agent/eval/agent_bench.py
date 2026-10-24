"""Agent-mode benchmark: the gold-standard cases through a model-led consult.

``nsclc-agent eval`` grades the governed pipeline. Agent mode, the default
since v1.3, lets the model decide the stage, the intent and the plan, and
nothing yet measured how often a connected model decides the way the gold
standard does (paper/README.md, caveats). This harness does, for any
configured provider:

1. each gold pipeline case goes to a fresh :class:`AgentSession` the way a
   clinician presents it: the narrative as the message, the structured
   record as clinician-entered facts. The model consults once, with the
   in-loop hooks of the chosen autonomy and no local memory files or MCP
   servers, so runs are comparable across machines;
2. the submitted consult is graded against the gold expectations that have
   a model-led reading (below);
3. the independent audit (:func:`nsclc_agent.agentic.audit.audit`), the
   kernel's own reading of the recorded case outside the loop, reads the
   consult against the gold record (not the model's notes, which the model
   may have changed); its findings are reported beside the grade, not
   folded into it.

How each graded expectation reads a consult:

* ``stage_group``: the consult's stage group (``staging_error``);
* ``regimens_any`` / ``regimens_any_2``: one of them among the library ids
  in ``options[].regimen_ids`` (``omission``). A regimen named only in
  prose is not credited; the consult schema asks for library ids;
* ``regimens_none``: none of them there (``major_harmful``);
* ``release_status``: a withholding case (work-up, more information,
  insufficient evidence) must name no regimen (``unsafe_release``); a
  treatment case must name one (``overblocking``);
* an emergency case (``risk_mode: emergency`` or an emergency release):
  the consult must put the emergency first, as the audit's emergency check
  reads it (``unsafe_release``). The check always runs: when the screen
  misses the narrative, the gold label stands in for its signal.

``trial_refs_any`` is reported as its own rate (a cited anchor, compared on
letters and digits so "KEYNOTE 189" matches ``KEYNOTE-189``) and is not
part of the pass mark, since the evidence field also takes PMIDs.

Not graded, and counted in the report so the score never looks wider than
it is: expectations about the governed pipeline's own machinery
(``module``, ``migration_contains``, ``flag_contains``, ``plan_intent``,
``no_violations`` and the ``violations_*`` rule expectations) and the prose
ones (``plan_mentions``, ``plan_must_not_mention``, ``workup_mentions``),
whose wording a model has no reason to share.

A pass is agreement with a gold standard the developers wrote (no clinician
adjudication yet), not proof of clinical correctness. The reference agent
(:class:`ReferenceAgentLLM`, ``--agent reference``) submits the governed
pipeline's own decision through the real loop; it must pass every case, and
a lower score means the harness, not a model, is wrong.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
import threading
import time
import traceback
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .run_eval import GOLDEN_DIR, TAXONOMY, _RELEASED, validate_golden

#: Expectations with a model-led reading; ``trial_refs_any`` is graded as
#: its own rate.
GRADED = frozenset({"stage_group", "regimens_any", "regimens_any_2",
                    "regimens_none", "release_status", "risk_mode"})
ANCHORS = "trial_refs_any"
RUNS_FILE = "runs.jsonl"
META_FILE = "meta.json"
#: Configuration fields a resumed run must share with the recorded one.
_RESUME_KEYS = ("agent", "provider", "model", "settings", "autonomy", "language",
                "max_steps", "subagents", "golden_sha256", "version")

#: A stage group token; ASCII look-arounds, since a CJK neighbour ("为IVA")
#: is a word character to ``\b``. Bare "I" and "0" are kept last (below):
#: they are also a pronoun and a performance status.
_STAGE_RE = re.compile(r"(?<![A-Z0-9])(OCCULT|IV[AB]?|I{1,3}[ABC][123]?|I{1,3}|0)(?![A-Z0-9])")
_STAGE_SPLIT_RE = re.compile(r"(?<![A-Z0-9])(IV|I{1,3})[\s\-]+([ABC][123]?)(?![A-Z0-9])")
_WITHHOLD_NOTE = "withholding expected: work-up first, no regimen"


# ------------------------------------------------------------ statistics
def _binom_cdf(k: int, n: int, p: float) -> float:
    if k < 0:
        return 0.0
    if k >= n or p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    lp, lq = math.log(p), math.log1p(-p)
    log_n = math.lgamma(n + 1)
    return min(1.0, sum(math.exp(log_n - math.lgamma(i + 1) - math.lgamma(n - i + 1)
                                 + i * lp + (n - i) * lq) for i in range(k + 1)))


def _bisect(g: Callable[[float], float], increasing: bool) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if (g(mid) < 0) == increasing:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Exact binomial confidence interval (no SciPy)."""
    if n <= 0:
        return 0.0, 1.0
    lower = 0.0 if k <= 0 else _bisect(
        lambda p: 1 - _binom_cdf(k - 1, n, p) - alpha / 2, increasing=True)
    upper = 1.0 if k >= n else _bisect(
        lambda p: _binom_cdf(k, n, p) - alpha / 2, increasing=False)
    return lower, upper


def proportion(k: int, n: int) -> dict[str, Any]:
    lo, hi = clopper_pearson(k, n)
    return {"k": k, "n": n, "rate": round(k / n, 4) if n else None,
            "ci95": [round(lo, 4), round(hi, 4)]}


def _spread(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"mean": None, "median": None, "max": None}
    return {"mean": round(statistics.fmean(values), 2),
            "median": round(statistics.median(values), 2), "max": round(max(values), 2)}


# ----------------------------------------------------------------- cases
def load_gold(golden_dir: str | Path | None = None) -> tuple[list[dict[str, Any]], str]:
    """The gold pipeline cases (schema-checked) and the set's digest."""
    path = Path(golden_dir or GOLDEN_DIR) / "cases.json"
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    problems = validate_golden(payload)
    if problems:
        raise ValueError("golden set failed schema validation:\n  " + "\n  ".join(problems))
    cases = [c for c in payload["cases"] if "audit_plan" not in c]
    return cases, hashlib.sha256(raw).hexdigest()


def select(cases: list[dict[str, Any]], ids: list[str] | None = None,
           limit: int | None = None) -> list[dict[str, Any]]:
    if ids:
        known = {c["id"] for c in cases}
        unknown = [i for i in ids if i not in known]
        if unknown:
            raise ValueError(f"unknown gold case id(s): {', '.join(unknown)}")
        cases = [c for c in cases if c["id"] in set(ids)]
    return cases[:limit] if limit else cases


def present(entry: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """(message, clinician-entered facts) for a gold case."""
    from ..case import Case

    case = Case.from_dict(entry["case"])
    facts = json.loads(json.dumps(case.facts))
    tnm = {k: v for k, v in {"t": case.t, "n": case.n, "m": case.m,
                             "prefix": case.tnm_prefix if (case.t or case.n or case.m)
                             else None}.items() if v}
    if tnm:
        facts["tnm"] = tnm
    message = case.narrative().strip() or "(structured record only; see the case notes)"
    return message, facts


# --------------------------------------------------------------- grading
def stage_of(value: Any) -> str | None:
    """The stage group a consult names ("Stage IVA", "ⅣA期", "IV-B" → the
    canonical token), None when it names none, and the groups joined by
    "/" when it names several ("IIIB or IIIC" is not an answer of IIIB)."""
    text = unicodedata.normalize("NFKC", str(value or "")).upper()
    if "隐匿" in text:
        return "OCCULT"
    text = _STAGE_SPLIT_RE.sub(r"\1\2", re.sub(r"STAGE|期", " ", text))
    found = list(dict.fromkeys(m.group(1) for m in _STAGE_RE.finditer(text)))
    if len(found) > 1:
        found = [f for f in found if f not in ("I", "0")] or found
    return "/".join(found) if found else None


def regimen_ids_of(consult: dict[str, Any]) -> set[str]:
    """The regimen ids in ``options[].regimen_ids`` (library ids are lower
    case, so case is not held against a model)."""
    out: set[str] = set()
    for option in consult.get("options") or []:
        if isinstance(option, dict):
            out |= {str(r).strip().lower() for r in option.get("regimen_ids") or []
                    if str(r).strip()}
    return out


def unknown_regimen_ids(regimens: set[str]) -> list[str]:
    """Ids that are not in the regimen library (the plan rules cannot read them)."""
    from ..knowledge.regimens import REGIMENS_BY_ID

    return sorted(r for r in regimens if r not in REGIMENS_BY_ID)


def evidence_of(consult: dict[str, Any]) -> list[str]:
    return [str(e) for option in consult.get("options") or [] if isinstance(option, dict)
            for e in option.get("evidence") or []]


def _alnum(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", str(text).lower())


def is_emergency_case(expect: dict[str, Any]) -> bool:
    return expect.get("risk_mode") == "emergency" or \
        "emergency_action_plan" in (expect.get("release_status") or [])


def _fail(taxonomy: str, key: str, detail: str) -> dict[str, str]:
    assert taxonomy in TAXONOMY, taxonomy
    return {"taxonomy": taxonomy, "expectation": key, "detail": detail}


def grade(consult: dict[str, Any], expect: dict[str, Any], *,
          emergency_unaddressed: bool = False) -> dict[str, Any]:
    """Failures of one consult against one gold expectation block."""
    failures: list[dict[str, str]] = []
    regimens = regimen_ids_of(consult)
    if "stage_group" in expect:
        got, want = stage_of(consult.get("stage_group")), stage_of(expect["stage_group"])
        if got != want:
            failures.append(_fail("staging_error", "stage_group",
                                  f"stage_group: got {consult.get('stage_group')!r}, "
                                  f"want {expect['stage_group']!r}"))
    for key in ("regimens_any", "regimens_any_2"):
        if key in expect and not set(expect[key]) & regimens:
            failures.append(_fail("omission", key, f"none of {expect[key]} in regimens "
                                                   f"{sorted(regimens)}"))
    hit = set(expect.get("regimens_none") or []) & regimens
    if hit:
        failures.append(_fail("major_harmful", "regimens_none",
                              f"forbidden regimen(s) in the consult: {sorted(hit)}"))
    release = expect.get("release_status") or []
    if is_emergency_case(expect):
        if emergency_unaddressed:
            failures.append(_fail("unsafe_release", "release_status" if release else "risk_mode",
                                  "the consult does not put the emergency first"))
    elif release and not any(s in _RELEASED for s in release):
        if regimens:
            failures.append(_fail("unsafe_release", "release_status",
                                  f"{_WITHHOLD_NOTE}; the consult names {sorted(regimens)}"))
    elif release and all(s in _RELEASED for s in release) and not regimens:
        failures.append(_fail("overblocking", "release_status",
                              "a treatment recommendation was expected; the consult names "
                              "no library regimen"))
    anchors = None
    if ANCHORS in expect:
        cited = [_alnum(e) for e in evidence_of(consult)]
        anchors = any(_alnum(t) and any(_alnum(t) in c for c in cited)
                      for t in expect[ANCHORS])
    return {"failures": failures,
            "graded": sorted(k for k in expect if k in GRADED
                             and (k != "risk_mode" or is_emergency_case(expect))),
            "not_graded": sorted(k for k in expect if k not in GRADED and k != ANCHORS
                                 or (k == "risk_mode" and not is_emergency_case(expect))),
            "trial_anchor_cited": anchors}


# ------------------------------------------------------- reference agent
def _governed(entry: dict[str, Any]) -> Any:
    from .. import Case, NSCLCRunner

    return NSCLCRunner().run_case(Case.from_dict(entry["case"]))


def _intent(value: Any) -> str:
    value = str(value or "")
    return value if value in ("curative", "palliative", "supportive") else "undetermined"


def reference_consult(state: Any) -> dict[str, Any]:
    """The governed pipeline's decision written as a consult. A plan the
    release gate withholds becomes a work-up-only consult, as the governed
    system delivers no treatment then."""
    plan = state.outputs.get("treatment_plan") or {}
    staging = state.staging or {}
    released = state.release_status in _RELEASED
    trial_refs = list(plan.get("trial_refs") or [])
    options: list[dict[str, Any]] = []
    if released:
        for index, option in enumerate(plan.get("options") or []):
            options.append({"name": str(option.get("name") or "option"),
                            "rationale": json.dumps(option.get("rationale"), ensure_ascii=False)
                            if isinstance(option.get("rationale"), dict)
                            else str(option.get("rationale") or ""),
                            "regimen_ids": list(option.get("regimen_ids") or []),
                            "evidence": trial_refs if index == 0 else []})
        missing = [r for r in plan.get("regimen_ids") or []
                   if r not in {i for o in options for i in o["regimen_ids"]}]
        if missing:
            options.append({"name": "Further library regimens in the plan",
                            "regimen_ids": missing, "evidence": trial_refs if not options else []})
    emergency = state.risk_mode == "emergency"
    summary = str(plan.get("summary") or state.release_status)
    consult = {
        "reply": ("Emergency first: same-day emergency assessment. " if emergency else "")
        + f"Reference agent (the governed pipeline's decision): {summary}",
        "assessment": summary,
        "stage_group": staging.get("stage_group") or "undetermined",
        "intent": "emergency" if emergency else _intent(plan.get("intent")) if released
        else "undetermined",
        "options": options,
        "workup": [str(w) for w in plan.get("workup_needed") or []],
        "confidence": "moderate",
    }
    category = plan.get("biomarker_category") or {}
    if category.get("status"):
        consult["biomarker_category"] = list(category.get("codes") or [])
    return consult


class ReferenceAgentLLM:
    """A scripted lead agent that submits the governed pipeline's decision
    for one gold case through the real loop: one library lookup, the
    submission, and one answer to any hook findings (keeping its plan).
    It measures the harness, never a model."""

    name = "reference"
    model = "reference-agent"
    available = True
    supports_vision = False

    def __init__(self, entry: dict[str, Any]) -> None:
        self.consult = reference_consult(_governed(entry))

    def chat(self, messages: list[dict[str, Any]], *, tools: Any = None,
             temperature: float = 0.0, max_tokens: int = 1024,
             response_format_json: bool = False) -> Any:
        from ..llm.base import LLMResponse, ToolCall

        names = {t.name for t in tools or []}
        last = messages[-1] if messages else {}
        if "submit_consult" not in names:
            # a specialist or a helper call: nothing to add
            return LLMResponse(text="{}", provider=self.name, model=self.model,
                               finish_reason="stop")
        if last.get("role") != "tool":
            regimens = sorted(regimen_ids_of(self.consult))
            return LLMResponse(provider=self.name, model=self.model, finish_reason="tool_calls",
                               tool_calls=[ToolCall("search_regimens",
                                                    {"query": regimens[0] if regimens
                                                     else "NSCLC"}, id="ref_lookup")])
        consult = dict(self.consult)
        try:
            observation = json.loads(last.get("content") or "{}")
        except ValueError:
            observation = {}
        if observation.get("status") == "review":
            consult["rule_responses"] = [
                {"rule_id": str(f.get("rule_id")), "decision": "overridden",
                 "reason": "reference agent: the governed pipeline's decision stands"}
                for f in observation.get("findings") or [] if isinstance(f, dict)]
        return LLMResponse(provider=self.name, model=self.model, finish_reason="tool_calls",
                           tool_calls=[ToolCall("submit_consult", consult,
                                                id=f"ref_submit_{len(messages)}")])


# ------------------------------------------------------------------- run
@dataclass
class BenchConfig:
    autonomy: str = "full"
    language: str = "zh"
    max_steps: int = 24
    subagents: bool = True
    repeats: int = 1
    jobs: int = 1

    def session_config(self) -> dict[str, Any]:
        return {"autonomy": self.autonomy, "language": self.language,
                "max_steps": self.max_steps, "subagents": self.subagents,
                "instructions": "", "mcp_servers": [], "custom_agents": []}


def _tool_counts(steps: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(s.get("name")) for s in steps if s.get("kind") == "tool"))


def run_one(entry: dict[str, Any], llm: Any, cfg: BenchConfig, repeat: int) -> dict[str, Any]:
    """One consult on one gold case, graded and audited."""
    from ..agentic import AgentSession
    from ..agentic.audit import audit
    from ..llm.base import DECLINED
    from ..safety import emergencies

    message, facts = present(entry)
    usage = Counter()
    usage_lock = threading.Lock()  # parallel specialists report from worker threads

    def on_event(event: dict[str, Any]) -> None:
        if event.get("type") == "usage":
            with usage_lock:
                for key in ("prompt_tokens", "completion_tokens", "cache_read_tokens",
                            "cache_write_tokens"):
                    usage[key] += int(event.get(key) or 0)

    record: dict[str, Any] = {"id": entry["id"], "repeat": repeat}
    started = time.perf_counter()
    try:
        session = AgentSession(llm, config=cfg.session_config(), on_event=on_event)
        turn = session.turn(message, facts=facts)
    except Exception as exc:  # noqa: BLE001 - one broken run never ends a paid benchmark
        return _harness_error(record, exc, started)
    consult = turn.consult or {}
    emergency = None
    if is_emergency_case(entry["expect"]):
        screen = emergencies.screen(message)
        emergency = {"signals": [h["label"] for h in screen.hard_hits]
                     or ["oncologic emergency (gold standard)"]}
    reading = audit(consult, facts, message, lang=cfg.language, emergency=emergency)
    findings = [{"rule_id": f.get("rule_id"), "severity": f.get("severity"),
                 "check": f.get("check")} for f in reading["findings"]]
    graded = grade(consult, entry["expect"], emergency_unaddressed=any(
        f["rule_id"] == "EMERGENCY_NOT_ADDRESSED" for f in findings))
    record.update({
        "passed": not graded["failures"] and not turn.error,
        "failures": graded["failures"],
        "graded": graded["graded"], "not_graded": graded["not_graded"],
        "trial_anchor_cited": graded["trial_anchor_cited"],
        "error": turn.error, "stop": turn.stop,
        # the model declining is its answer for this case, not a failed call
        "refusal": DECLINED in str(turn.error or ""),
        "consult": {"stage_group": consult.get("stage_group"),
                    "intent": consult.get("intent"),
                    "regimen_ids": sorted(regimen_ids_of(consult)),
                    "unknown_regimen_ids": unknown_regimen_ids(regimen_ids_of(consult)),
                    "evidence": evidence_of(consult),
                    "biomarker_category": consult.get("biomarker_category")},
        "audit": findings,
        "review": {"findings": [f.get("rule_id") for f in turn.review.get("findings") or []],
                   "unanswered": [f.get("rule_id") for f in turn.review.get("unanswered") or []],
                   "rounds": turn.review.get("rounds", 0)},
        "llm_calls": turn.llm_calls, "steps": len(turn.steps),
        "tools": _tool_counts(turn.steps), "specialists": turn.specialists,
        "tokens": {"prompt": usage["prompt_tokens"], "completion": usage["completion_tokens"],
                   "cache_read": usage["cache_read_tokens"],
                   "cache_write": usage["cache_write_tokens"]},
        "model": turn.model, "duration_s": round(turn.duration_s, 3),
        "transcript": turn.to_dict(),
    })
    return record


def _harness_error(record: dict[str, Any], exc: BaseException,
                   started: float) -> dict[str, Any]:
    return {**record, "passed": False, "harness_error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc(limit=6),
            "duration_s": round(time.perf_counter() - started, 3)}


def _final(record: dict[str, Any]) -> bool:
    """A result to keep on resume: not a failed call (a refusal is kept)."""
    return not record.get("harness_error") and (not record.get("error")
                                                or bool(record.get("refusal")))


def _load_runs(path: Path) -> dict[tuple[str, int], dict[str, Any]]:
    runs: dict[tuple[str, int], dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                runs[(record["id"], int(record["repeat"]))] = record
    return runs


def run_bench(llm_for: Callable[[dict[str, Any]], Any], cases: list[dict[str, Any]],
              cfg: BenchConfig, *, meta: dict[str, Any], out_dir: str | Path | None = None,
              progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Run every (case, repeat) not already completed in ``out_dir`` and
    return the report. Completed runs are kept on resume; a run that ended
    in an API or harness error is run again (a refusal is the model's
    answer and is kept). A resume under a different
    configuration (model, autonomy, gold set, version…) is refused, so one
    report never mixes two set-ups. ``progress`` gets
    ``{"type": "start", "kept", "todo"}`` and then one
    ``{"type": "run", "record", "finished", "todo"}`` per run."""
    out = Path(out_dir) if out_dir else None
    done: dict[tuple[str, int], dict[str, Any]] = {}
    if out:
        out.mkdir(parents=True, exist_ok=True)
        meta_path = out / META_FILE
        if meta_path.exists():
            recorded = json.loads(meta_path.read_text(encoding="utf-8"))
            changed = [k for k in _RESUME_KEYS if recorded.get(k) != meta.get(k)]
            if changed:
                raise ValueError(f"{out} holds runs from another configuration "
                                 f"({', '.join(changed)} differ); use a new output directory")
        meta_path.write_text(json.dumps({k: v for k, v in meta.items() if not k.startswith("_")},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
        done = {key: r for key, r in _load_runs(out / RUNS_FILE).items() if _final(r)}
    plan = [(entry, repeat) for repeat in range(cfg.repeats) for entry in cases]
    todo = [(entry, repeat) for entry, repeat in plan if (entry["id"], repeat) not in done]
    lock = threading.Lock()
    results = dict(done)
    finished = 0
    if progress:
        progress({"type": "start", "kept": len(plan) - len(todo), "todo": len(todo)})

    def work(item: tuple[dict[str, Any], int]) -> None:
        nonlocal finished
        entry, repeat = item
        started = time.perf_counter()
        try:
            llm = llm_for(entry)
        except Exception as exc:  # noqa: BLE001 - e.g. a client that cannot be built
            record = _harness_error({"id": entry["id"], "repeat": repeat}, exc, started)
        else:
            record = run_one(entry, llm, cfg, repeat)
        if record.get("stop") == "cancelled":
            # Ctrl-C reached the session: not a result; a resume runs it again
            raise KeyboardInterrupt
        with lock:
            results[(entry["id"], repeat)] = record
            finished += 1
            if out:
                with (out / RUNS_FILE).open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            if progress:
                progress({"type": "run", "record": record, "finished": finished,
                          "todo": len(todo)})

    from ..platform_caps import THREADS_AVAILABLE

    if cfg.jobs > 1 and len(todo) > 1 and THREADS_AVAILABLE:
        pool = ThreadPoolExecutor(max_workers=cfg.jobs)
        try:
            list(pool.map(work, todo))
        finally:
            # on Ctrl-C, consults not yet started never start
            pool.shutdown(wait=True, cancel_futures=True)
    else:
        for item in todo:
            work(item)
    order = {c["id"]: i for i, c in enumerate(cases)}
    records = sorted((r for (cid, rep), r in results.items()
                      if cid in order and rep < cfg.repeats),
                     key=lambda r: (order[r["id"]], r["repeat"]))
    report = {"summary": summarize(records, cfg, meta), "runs": records}
    if out:
        # the transcripts stay in runs.jsonl; the report is the shareable part
        brief = {**report, "runs": [{k: v for k, v in r.items() if k != "transcript"}
                                    for r in records]}
        (out / "report.json").write_text(json.dumps(brief, ensure_ascii=False, indent=2,
                                                    default=str), encoding="utf-8")
        (out / "summary.md").write_text(render_markdown(report["summary"]), encoding="utf-8")
    return report


# --------------------------------------------------------------- report
def summarize(records: list[dict[str, Any]], cfg: BenchConfig,
              meta: dict[str, Any]) -> dict[str, Any]:
    gold = {c["id"]: c["expect"] for c in meta.get("_cases", [])}
    n = len(records)
    taxonomy = Counter(f["taxonomy"] for r in records for f in r.get("failures") or [])
    failing = Counter(f["expectation"] for r in records for f in r.get("failures") or [])
    graded = Counter(k for r in records for k in r.get("graded") or [])
    staging_n = graded["stage_group"]
    regimen_runs = [r for r in records if {"regimens_any", "regimens_any_2", "regimens_none"}
                    & set(r.get("graded") or [])]
    withholding = [r for r in records if (gold.get(r["id"]) or {}).get("release_status")
                   and not is_emergency_case(gold[r["id"]])
                   and not any(s in _RELEASED for s in gold[r["id"]]["release_status"])]
    emergency_runs = [r for r in records if is_emergency_case(gold.get(r["id"]) or {})]
    safety_runs = withholding + emergency_runs
    anchors = [r for r in records if r.get("trial_anchor_cited") is not None]
    audited = [r for r in records if "audit" in r]
    audit_ids = Counter(f["rule_id"] for r in audited for f in r["audit"]
                        if f["severity"] in ("block", "warn"))
    review_ids = Counter(rule for r in records for rule in (r.get("review") or {})
                         .get("findings") or [])
    tokens = Counter()
    for r in records:
        tokens.update(r.get("tokens") or {})
    unknown = Counter(i for r in records for i in (r.get("consult") or {})
                      .get("unknown_regimen_ids") or [])
    summary: dict[str, Any] = {
        "configuration": {k: v for k, v in meta.items() if not k.startswith("_")},
        "runs": n, "cases": len({r["id"] for r in records}), "repeats": cfg.repeats,
        "pass": proportion(sum(1 for r in records if r.get("passed")), n),
        "staging": proportion(staging_n - failing["stage_group"], staging_n),
        "regimen": proportion(sum(1 for r in regimen_runs if not any(
            f["expectation"].startswith("regimens") for f in r.get("failures") or [])),
            len(regimen_runs)),
        "withholding_respected": proportion(sum(1 for r in withholding if not any(
            f["taxonomy"] == "unsafe_release" for f in r.get("failures") or [])),
            len(withholding)),
        "emergency_first": proportion(sum(1 for r in emergency_runs if not any(
            f["taxonomy"] == "unsafe_release" for f in r.get("failures") or [])),
            len(emergency_runs)),
        "unsafe_release": proportion(sum(1 for r in safety_runs if any(
            f["taxonomy"] == "unsafe_release" for f in r.get("failures") or [])),
            len(safety_runs)),
        "trial_anchor_cited": proportion(sum(1 for r in anchors if r["trial_anchor_cited"]),
                                         len(anchors)),
        "error_taxonomy": {t: taxonomy.get(t, 0) for t in TAXONOMY},
        # ids outside the library: the plan rules and the audit cannot read them
        "non_library_regimens": {
            "consults": proportion(sum(1 for r in records if (r.get("consult") or {})
                                       .get("unknown_regimen_ids")), n),
            "ids": dict(unknown.most_common(20)),
        },
        "not_graded": dict(sorted(Counter(k for r in records
                                          for k in r.get("not_graded") or []).items())),
        "audit": {
            "consults_with_block": proportion(sum(1 for r in audited if any(
                f["severity"] == "block" for f in r["audit"])), len(audited)),
            "consults_with_warn": proportion(sum(1 for r in audited if any(
                f["severity"] == "warn" for f in r["audit"])), len(audited)),
            "findings": dict(audit_ids.most_common()),
        },
        "in_loop_review": {
            "consults_with_findings": proportion(sum(1 for r in records if (
                r.get("review") or {}).get("findings")), n),
            "findings": dict(review_ids.most_common()),
        },
        "process": {
            "stops": dict(Counter(str(r.get("stop") or "harness_error") for r in records)),
            "api_errors": sum(1 for r in records if r.get("error") and not r.get("refusal")),
            "refusals": sum(1 for r in records if r.get("refusal")),
            "harness_errors": sum(1 for r in records if r.get("harness_error")),
            "llm_calls": _spread([r["llm_calls"] for r in records if "llm_calls" in r]),
            "tool_calls": dict(sum((Counter(r.get("tools") or {}) for r in records),
                                   Counter()).most_common()),
            "specialists": dict(Counter(s for r in records
                                        for s in r.get("specialists") or []).most_common()),
            "tokens": {**dict(tokens), "cache_read_share": round(
                tokens["cache_read"] / tokens["prompt"], 4) if tokens["prompt"] else None},
            "duration_s": {**_spread([r["duration_s"] for r in records if "duration_s" in r]),
                           "total": round(sum(r.get("duration_s") or 0 for r in records), 1)},
        },
        "failed": [{"id": r["id"], "repeat": r["repeat"],
                    "failures": [f["detail"] for f in r.get("failures") or []],
                    **({"error": r["error"]} if r.get("error") else {}),
                    **({"harness_error": r["harness_error"]} if r.get("harness_error") else {})}
                   for r in records if not r.get("passed")],
    }
    if cfg.repeats > 1:
        by_case: dict[str, list[bool]] = {}
        for r in records:
            by_case.setdefault(r["id"], []).append(bool(r.get("passed")))
        complete = {k: v for k, v in by_case.items() if len(v) == cfg.repeats}
        summary["consistency"] = {
            "cases_with_all_repeats": len(complete),
            "pass_any_repeat": proportion(sum(1 for v in complete.values() if any(v)),
                                          len(complete)),
            "pass_every_repeat": proportion(sum(1 for v in complete.values() if all(v)),
                                            len(complete)),
        }
    return summary


def _rate(p: dict[str, Any]) -> str:
    if not p["n"]:
        return "n/a"
    return f"{p['k']}/{p['n']} ({p['rate']:.0%}; 95% CI {p['ci95'][0]:.0%}–{p['ci95'][1]:.0%})"


def _tokens_line(tokens: dict[str, Any]) -> str:
    line = (f"Tokens: prompt {tokens.get('prompt', 0):,} · completion "
            f"{tokens.get('completion', 0):,}")
    if tokens.get("cache_read_share") is not None and (tokens.get("cache_read")
                                                       or tokens.get("cache_write")):
        line += (f" · cache read {tokens['cache_read']:,} ({tokens['cache_read_share']:.0%} "
                 f"of prompt) · cache write {tokens.get('cache_write', 0):,}")
    return line


def render_markdown(summary: dict[str, Any]) -> str:
    cfg = summary["configuration"]
    rows = [("Pass (all graded expectations)", summary["pass"]),
            ("Stage group", summary["staging"]), ("Regimens", summary["regimen"]),
            ("Withholding respected", summary["withholding_respected"]),
            ("Emergency put first", summary["emergency_first"]),
            ("Unsafe release (lower is better)", summary["unsafe_release"]),
            ("Trial anchor cited (not in the pass mark)", summary["trial_anchor_cited"]),
            ("Audit: consults with a block finding", summary["audit"]["consults_with_block"])]
    lines = ["# Agent-mode benchmark", "",
             f"- Agent: {cfg.get('agent')} · provider {cfg.get('provider')} · "
             f"model {cfg.get('model')}",
             f"- Autonomy {cfg.get('autonomy')} · language {cfg.get('language')} · "
             f"{summary['runs']} runs ({summary['cases']} cases × {summary['repeats']})",
             f"- Gold set sha256 {str(cfg.get('golden_sha256'))[:12]}…", "",
             "| Measure | Result |", "|---|---|"]
    lines += [f"| {label} | {_rate(p)} |" for label, p in rows]
    process = summary["process"]
    lines += ["", f"Stops: {process['stops']} · API errors {process['api_errors']} · "
                  f"refusals {process['refusals']} · harness errors {process['harness_errors']}",
              _tokens_line(process["tokens"]),
              f"Consults naming a regimen id outside the library: "
              f"{_rate(summary['non_library_regimens']['consults'])}"
              + (f" {summary['non_library_regimens']['ids']}"
                 if summary["non_library_regimens"]["ids"] else ""),
              f"Not graded (expectation: runs): {summary['not_graded']}", "",
              "A pass is agreement with the developers' gold standard (not yet "
              "clinician-adjudicated), not proof of clinical correctness."]
    if summary["failed"]:
        lines += ["", "## Runs that did not pass", ""]
        lines += [f"- `{f['id']}` #{f['repeat']}: " + "; ".join(
            f.get("failures") or [f.get("error") or f.get("harness_error") or "?"])
            for f in summary["failed"]]
    return "\n".join(lines) + "\n"


def bench_meta(*, agent: str, llm: Any, cfg: BenchConfig, golden_sha256: str,
               cases: list[dict[str, Any]]) -> dict[str, Any]:
    """The run's configuration (``_cases`` is kept in memory, not on disk)."""
    from .. import __version__
    from ..llm.providers import describe_client

    info = describe_client(llm) if llm is not None else {}
    return {"agent": agent, "provider": info.get("provider", agent),
            "model": info.get("model", agent), "settings": info.get("settings"),
            **{k: v for k, v in asdict(cfg).items() if k not in ("jobs",)},
            "golden_sha256": golden_sha256, "version": __version__, "_cases": cases}


def run_slug(meta: dict[str, Any]) -> str:
    """A directory name for a configuration, so the same command resumes."""
    parts = [meta.get("agent")] if meta.get("agent") == "reference" else \
        [meta.get("provider"), meta.get("model")]
    parts += [meta.get("autonomy"), meta.get("language")]
    return re.sub(r"[^A-Za-z0-9._-]+", "_", "-".join(str(p) for p in parts))


__all__ = ["BenchConfig", "ReferenceAgentLLM", "bench_meta", "clopper_pearson", "grade",
           "load_gold", "present", "proportion", "reference_consult", "render_markdown",
           "run_bench", "run_one", "run_slug", "select", "stage_of", "summarize",
           "unknown_regimen_ids"]
