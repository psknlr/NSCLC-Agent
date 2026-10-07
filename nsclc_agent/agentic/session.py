"""The agent runtime: a model-led consultation session.

``AgentSession`` is the orchestrator of agent mode, built the way
mainstream agent runtimes are (Claude Code, Grok CLI, Codex, the OpenAI
Agents SDK) and specialised for a multidisciplinary cancer consult:

* **one loop** (:mod:`.loop`) runs the lead agent and every specialist —
  model → thinking/text/tool calls → observations → model … until the
  terminal tool, a plain answer, the step budget or a cancel;
* **tools** (:mod:`.tools`) — the clinical instruments of the
  deterministic kernel, ``update_plan`` (a live consult plan),
  ``delegate`` (MDT specialists as sub-agents with their own context and
  toolset), ``remember`` (memory proposals), MCP server tools, and the
  terminal ``submit_consult``;
* **hooks** (:mod:`.hooks`) at ``user_prompt_submit`` / ``post_tool_use``
  / ``stop`` hold every clinical safety net — emergency screen, evidence
  ledger, rule engine, stage consistency, citation and dose provenance —
  and all of them are ADVISORY: findings go back to the model, which
  revises or answers each with a clinical reason;
* **memory** (:mod:`.memory`) — institution/user instructions (NSCLC.md)
  in every system prompt; token accounting; model-written compaction;
* **checkpoints** — every turn can be rewound (case notes, plan, history);
* **events** — every step streams through ``on_event`` (CLI timeline,
  ``--output-format stream-json``, the web app's live trace).

Authority stays with the caller: the role and the configuration come from
the surface, never from a session file; the system prompt is rebuilt every
turn, never restored.
"""

from __future__ import annotations

import copy
import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from ..llm.base import LLMError
from .hooks import KERNEL_HOOKS, HookRunner, builtin_hooks
from .loop import Cancelled, LoopResult, repair_history, run_loop
from .memory import MemoryNotes, estimate_tokens, summarize
from .planning import Plan
from .prompts import ROLE_TEXT, lead_prompt
from .subagents import BUILTIN_AGENTS, AgentDefinition, SubAgentRunner
from .toolbox import KERNEL_TOOLS, SUBMIT_SPEC, SUBMIT_TOOL, AgentToolbox
from .tools import Tool, Toolset

SESSION_FORMAT = "nsclc-agent-session/2"
_READABLE_FORMATS = ("nsclc-agent-session/1", SESSION_FORMAT)

_SYSTEM_PREFIX = "[系统]"
_SUMMARY_PREFIX = "【此前会诊摘要"
_NUDGE = {"zh": "请调用 submit_consult 提交本轮结论（reply 可直接使用你刚才的回答）。",
          "en": "Please call submit_consult to submit this turn's conclusion (the reply can "
                "reuse the answer you just gave)."}
LANGUAGES = ("zh", "en")
#: "full": the model decides stage, intent and plan; kernel decision tools
#: and kernel-comparison hooks are off. "assisted": the deterministic kernel
#: is offered as tools and advisory hooks (the v1.x behaviour).
AUTONOMY = ("full", "assisted")
INTENTS = ("curative", "palliative", "supportive", "emergency", "undetermined")


def _t(lang: str, zh: str, en: str) -> str:
    return en if lang == "en" else zh


@dataclass
class AgentConfig:
    """Runtime settings a surface (CLI flags, web settings) chooses."""

    max_steps: int = 24
    max_review_rounds: int = 1
    temperature: float = 0.2
    max_tokens: int = 6000
    #: The model's context window (tokens) and the fill ratio that
    #: triggers automatic compaction.
    context_window: int = 128_000
    compact_at: float = 0.75
    #: Institution protocols / user preferences (NSCLC.md, the 记忆 panel).
    instructions: str = ""
    #: Hook name -> enabled (missing names use the hook's default).
    hooks: dict[str, bool] = field(default_factory=dict)
    subagents: bool = True
    custom_agents: list[dict[str, Any]] = field(default_factory=list)
    disabled_agents: list[str] = field(default_factory=list)
    #: [{"name", "url", "headers"?, "enabled"?}] — Streamable HTTP servers.
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    #: Run read-only tool calls concurrently (None = when threads exist).
    parallel: bool | None = None
    #: Language the clinician reads: "zh" or "en" (prompts, hooks, commands).
    language: str = "zh"
    #: Who decides: "full" (the model) or "assisted" (kernel tools + hooks).
    autonomy: str = "full"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AgentConfig":
        data = dict(data or {})

        def num(key: str, kind: type, low: float, high: float) -> Any:
            default = getattr(cls, key)
            try:
                value = kind(data.get(key, default))
            except (TypeError, ValueError):
                value = default
            return kind(min(max(value, low), high))

        hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
        parallel = data.get("parallel")
        return cls(
            max_steps=num("max_steps", int, 2, 64),
            max_review_rounds=num("max_review_rounds", int, 0, 3),
            temperature=num("temperature", float, 0.0, 1.5),
            max_tokens=num("max_tokens", int, 256, 32_000),
            context_window=num("context_window", int, 8_000, 2_000_000),
            compact_at=num("compact_at", float, 0.3, 0.95),
            instructions=str(data.get("instructions") or "")[:20_000],
            hooks={str(k): bool(v) for k, v in hooks.items()},
            subagents=bool(data.get("subagents", True)),
            custom_agents=[a for a in data.get("custom_agents") or []
                           if isinstance(a, dict)][:12],
            disabled_agents=[str(a) for a in data.get("disabled_agents") or []],
            mcp_servers=[s for s in data.get("mcp_servers") or []
                         if isinstance(s, dict) and s.get("url")][:8],
            parallel=None if parallel is None else bool(parallel),
            language=data.get("language") if data.get("language") in LANGUAGES else "zh",
            autonomy=data.get("autonomy") if data.get("autonomy") in AUTONOMY else "full",
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentTurn:
    """One turn of a model-led consult."""

    reply: str
    consult: dict[str, Any]
    steps: list[dict[str, Any]]
    review: dict[str, Any]
    emergency: dict[str, Any] | None
    facts: dict[str, Any]
    engine_stage: dict[str, Any] | None
    llm_calls: int
    tokens: dict[str, int]
    model: str
    duration_s: float
    error: str | None = None
    message: str = ""
    turn: int = 0
    #: terminal | text | limit | cancelled | error
    stop: str = ""
    plan: list[dict[str, Any]] = field(default_factory=list)
    memory: list[str] = field(default_factory=list)
    specialists: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    compacted: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": "agent", "reply": self.reply, "consult": self.consult,
            "steps": self.steps, "review": self.review,
            "emergency": self.emergency, "facts": self.facts,
            "engine_stage": self.engine_stage, "llm_calls": self.llm_calls,
            "tokens": self.tokens, "model": self.model,
            "duration_s": round(self.duration_s, 3), "error": self.error,
            "message": self.message, "turn": self.turn, "stop": self.stop,
            "plan": self.plan, "memory": self.memory,
            "specialists": self.specialists, "context": self.context,
            "usage": self.usage, "compacted": self.compacted,
        }


@dataclass
class _TurnState:
    submission: dict[str, Any] | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)
    review_rounds: int = 0


_LEGACY_KWARGS = ("max_steps", "max_review_rounds", "temperature", "max_tokens")


class AgentSession:
    """A multi-turn, model-led consultation."""

    def __init__(self, llm: Any, *, role: str = "oncologist",
                 vision_llm: Any | None = None,
                 config: AgentConfig | dict[str, Any] | None = None,
                 on_event: Callable[[dict[str, Any]], None] | None = None,
                 **overrides: Any) -> None:
        if llm is None or not getattr(llm, "available", False):
            raise LLMError("agent mode needs a configured model")
        unknown = set(overrides) - set(_LEGACY_KWARGS)
        if unknown:
            raise TypeError(f"unexpected argument(s): {', '.join(sorted(unknown))}")
        base = config.to_dict() if isinstance(config, AgentConfig) else dict(config or {})
        self.config = AgentConfig.from_dict({**base, **overrides})
        self.llm = llm
        self.role = role if role in ROLE_TEXT else "patient"
        self.vision_llm = vision_llm if vision_llm is not None else (
            llm if getattr(llm, "supports_vision", False) else None)
        self.on_event = on_event
        self.narrative: list[str] = []
        self.turns: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.toolbox = AgentToolbox(vision_llm=self.vision_llm,
                                    narrative=lambda: "\n".join(self.narrative))
        self.plan = Plan(on_change=lambda items: self._emit({"type": "plan",
                                                             "items": items}))
        self.memory = MemoryNotes(language=self.config.language)
        self.plan.language = self.config.language
        self.state: dict[str, Any] = {"evidence_seen": set(), "dosing_seen": set(),
                                      "emergency": None}
        self.usage = {"turns": 0, "calls": 0, "prompt": 0, "completion": 0,
                      "subagent_calls": 0}
        self.checkpoints: list[dict[str, Any]] = []
        self.compactions = 0
        self.mcp_tools: list[Tool] | None = None
        self.mcp_status: list[dict[str, Any]] = []
        self._cancel = False
        self._apply_config()

    # ------------------------------------------------------------ settings
    def _apply_config(self) -> None:
        from ..platform_caps import THREADS_AVAILABLE

        cfg = self.config
        self.parallel = THREADS_AVAILABLE if cfg.parallel is None else (
            cfg.parallel and THREADS_AVAILABLE)
        self.lang = cfg.language
        if hasattr(self, "plan"):
            self.plan.language = self.memory.language = cfg.language
        self.autonomous = cfg.autonomy == "full"
        self.toolbox.autonomous = self.autonomous
        self.hooks = HookRunner(builtin_hooks(), hook_defaults(cfg))
        disabled = set(cfg.disabled_agents)
        definitions = {d.name: d for d in BUILTIN_AGENTS if d.name not in disabled}
        for raw in cfg.custom_agents:
            try:
                custom = AgentDefinition.from_dict(raw)
            except (ValueError, TypeError):
                continue
            if custom.name not in disabled:
                definitions[custom.name] = custom
        self.definitions = list(definitions.values())

    def configure(self, config: AgentConfig | dict[str, Any]) -> dict[str, Any]:
        """Change settings mid-consult (hooks, specialists, memory, MCP…)."""
        old_servers = self.config.mcp_servers
        self.config = config if isinstance(config, AgentConfig) else \
            AgentConfig.from_dict({**self.config.to_dict(), **dict(config or {})})
        if self.config.mcp_servers != old_servers:
            self.mcp_tools = None
        self._apply_config()
        return self.config.to_dict()

    def cancel(self) -> None:
        """Ask the running turn to stop at the next checkpoint."""
        self._cancel = True

    # ---------------------------------------------------------------- state
    @property
    def facts(self) -> dict[str, Any]:
        return self.toolbox.facts

    def _emit(self, event: dict[str, Any]) -> None:
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:  # noqa: BLE001 - a UI hook never breaks a consult
                pass

    def _mcp(self) -> list[Tool]:
        if self.mcp_tools is None:
            self.mcp_tools, self.mcp_status = [], []
            if self.config.mcp_servers:
                from .mcp import connect

                self.mcp_tools, self.mcp_status = connect(self.config.mcp_servers)
                self._emit({"type": "mcp", "servers": self.mcp_status})
        return self.mcp_tools

    def _submit_tool(self) -> Tool:
        return Tool(SUBMIT_SPEC.name, SUBMIT_SPEC.description, SUBMIT_SPEC.parameters,
                    category="control", parallel_safe=False, terminal=True,
                    label=_t(self.lang, "提交会诊结论", "Submit conclusion"))

    def _toolset(self) -> tuple[Toolset, SubAgentRunner | None]:
        mcp = self._mcp()
        exclude = KERNEL_TOOLS if self.autonomous else frozenset()
        groups: list[list[Tool]] = [self.toolbox.as_tools(lang=self.lang, exclude=exclude),
                                    [self.plan.tool()]]
        runner = None
        if self.config.subagents and self.definitions:
            runner = SubAgentRunner(
                self.llm, Toolset.of(self.toolbox.as_tools(read_only=True, lang=self.lang,
                                                           exclude=exclude), mcp),
                definitions=self.definitions, case_notes=lambda: self.facts,
                emit=self._emit, cancelled=lambda: self._cancel,
                parallel=self.parallel, temperature=self.config.temperature,
                max_tokens=min(4000, self.config.max_tokens),
                instructions=self.config.instructions, post_tool=self._post_tool,
                language=self.lang, autonomous=self.autonomous)
            groups.append([runner.tool()])
        groups += [[self.memory.tool()], mcp, [self._submit_tool()]]
        return Toolset.of(*groups), runner

    def _system(self) -> dict[str, Any]:
        roster = self.definitions if self.config.subagents else []
        return {"role": "system",
                "content": lead_prompt(self.role, roster=roster,
                                       mcp_tools=[t.name for t in self._mcp()],
                                       instructions=self.config.instructions,
                                       language=self.lang,
                                       autonomy=self.config.autonomy)}

    def _refresh_system(self) -> None:
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0] = self._system()
        else:
            self.messages.insert(0, self._system())

    def context(self) -> dict[str, Any]:
        tokens = estimate_tokens(self.messages)
        window = self.config.context_window
        return {"tokens": tokens, "window": window,
                "ratio": round(tokens / window, 4) if window else 0.0,
                "compact_at": self.config.compact_at,
                "compactions": self.compactions}

    # ------------------------------------------------------------------ turn
    def turn(self, message: str, *, facts: dict[str, Any] | None = None,
             images: list[str] | None = None,
             reports: list[str] | None = None) -> AgentTurn:
        started = time.perf_counter()
        self._cancel = False
        message = str(message or "").strip()
        self._refresh_system()
        compacted = self._maybe_compact()
        index = len(self.turns)
        self._checkpoint(message)
        self.narrative.append(message)
        self.toolbox.attachments = {"images": list(images or []),
                                    "reports": list(reports or [])}
        toolset, runner = self._toolset()
        self._emit({"type": "turn_start", "message": message, "turn": index})

        recorded: list[str] = []
        if facts:
            out = self.toolbox.record_case_facts(facts, note="clinician-entered")
            recorded = out["data"]["changed"] + out["data"]["notes"]

        loop = LoopResult()
        contexts = self._prompt_hooks(message, loop)
        self.messages.append({"role": "user",
                              "content": self._user_content(message, facts, recorded,
                                                            contexts)})
        state = _TurnState()
        error: str | None = None
        try:
            run_loop(
                self.llm, self.messages, toolset, emit=self._emit,
                terminal=lambda arguments: self._on_submit(arguments, state, loop),
                terminal_name=SUBMIT_TOOL, agent="lead",
                max_steps=self.config.max_steps,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens, parallel=self.parallel,
                cancelled=lambda: self._cancel, nudge=_NUDGE[self.lang],
                post_tool=self._post_tool, result=loop)
        except (Cancelled, KeyboardInterrupt):
            self._cancel = True
            loop.stop = "cancelled"
            self._emit({"type": "cancelled"})
        except LLMError as exc:
            error = str(exc)
            loop.stop = "error"
            self._emit({"type": "error", "message": error})
        finally:
            repair_history(self.messages,
                           "interrupted by the clinician" if loop.stop == "cancelled"
                           else "not executed")
            self._strip_images()

        consult = self._final_consult(state, loop)
        reply = str(consult.get("reply") or "").strip()
        if not reply:
            reply = (_t(self.lang, f"本轮模型调用失败：{error}",
                        f"The model call failed this turn: {error}") if error else
                     _t(self.lang, "（已停止）本轮会诊被中断。",
                        "(Stopped) This turn was interrupted.") if loop.stop == "cancelled" else
                     _t(self.lang, "模型未提交结论（步数用尽）。请补充信息或重试。",
                        "The model did not submit a conclusion (step budget used up). "
                        "Add information or try again."))
        responses = [r for r in consult.get("rule_responses") or [] if isinstance(r, dict)]
        answered = {str(r.get("rule_id")) for r in responses}
        review = {"findings": state.findings, "responses": responses,
                  "unanswered": [f for f in state.findings
                                 if f["rule_id"] not in answered],
                  "rounds": state.review_rounds,
                  # the stop hooks that actually ran (they differ by autonomy)
                  "checked": [h.name for h in self.hooks.hooks
                              if h.event == "stop" and self.hooks.enabled.get(h.name)]}

        sub = runner.usage if runner else {"calls": 0, "prompt": 0, "completion": 0}
        self.usage["turns"] += 1
        self.usage["calls"] += loop.calls + sub["calls"]
        self.usage["prompt"] += loop.prompt_tokens + sub["prompt"]
        self.usage["completion"] += loop.completion_tokens + sub["completion"]
        self.usage["subagent_calls"] += sub["calls"]
        proposals = self.memory.proposals[self.checkpoints[-1]["proposals"]:]
        specialists = list(dict.fromkeys(runner.consulted)) if runner else []
        self.turns.append({"message": message, "reply": reply, "consult": consult,
                           "review": review, "error": error, "stop": loop.stop,
                           "specialists": specialists})
        result = AgentTurn(
            reply=reply, consult=consult, steps=loop.steps, review=review,
            emergency=self.state.get("emergency"),
            facts=json.loads(json.dumps(self.facts, default=str)),
            engine_stage=None if self.autonomous else self.toolbox.engine_stage(),
            llm_calls=loop.calls + sub["calls"],
            tokens={"prompt": loop.prompt_tokens + sub["prompt"],
                    "completion": loop.completion_tokens + sub["completion"]},
            model=loop.model or getattr(self.llm, "model", ""),
            duration_s=time.perf_counter() - started, error=error,
            message=message, turn=index, stop=loop.stop,
            plan=copy.deepcopy(self.plan.items), memory=proposals,
            specialists=specialists, context=self.context(),
            usage=dict(self.usage), compacted=compacted)
        self._emit({"type": "turn_end", "error": error, "stop": loop.stop,
                    "context": result.context})
        return result

    def _prompt_hooks(self, message: str, loop: LoopResult) -> list[str]:
        """user_prompt_submit hooks: context for the model, alerts for the
        clinician (both shown in the trace)."""
        self.state["emergency"] = None
        ctx = {"message": message, "narrative": message, "facts": self.toolbox.facts,
               "state": self.state, "lang": self.lang}
        contexts = []
        for hook, outcome in self.hooks.run("user_prompt_submit", ctx):
            if outcome.alert:
                self._emit({"type": "alert", "emergency": outcome.alert,
                            "hook": hook.name})
            if outcome.context:
                contexts.append(outcome.context)
                step = {"kind": "hook", "name": hook.name, "title": hook.title,
                        "text": outcome.context, "agent": "lead"}
                loop.steps.append(step)
                self._emit({"type": "hook", **step})
        return contexts

    def _post_tool(self, name: str, arguments: dict[str, Any],
                   out: dict[str, Any]) -> str | None:
        ctx = {"tool": name, "args": arguments, "result": out, "state": self.state,
               "lang": self.lang}
        notes = [o.context for _h, o in self.hooks.run("post_tool_use", ctx) if o.context]
        return "\n".join(notes) or None

    def _on_submit(self, arguments: dict[str, Any], state: _TurnState,
                   loop: LoopResult) -> tuple[dict[str, Any], bool]:
        consult = dict(arguments) if isinstance(arguments, dict) else {}
        # Treatment intent is always the model's stated decision; a missing
        # or unknown value is recorded as undetermined, never inferred.
        if consult.get("intent") not in INTENTS:
            consult["intent"] = "undetermined"
        # Biomarker categories: the model's codes, kept only when they are
        # real table codes ("NSCL-26", "nscl 26" …); nothing is inferred.
        from ..knowledge.biomarker_categories import BY_CODE

        raw = consult.get("biomarker_category")
        raw = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
        codes = []
        for item in raw:
            match = re.search(r"NSCL\s*-?\s*(\d{2})", str(item), re.I)
            code = f"NSCL-{match.group(1)}" if match else ""
            if code in BY_CODE and code not in codes:
                codes.append(code)
        if raw or "biomarker_category" in consult:
            consult["biomarker_category"] = codes
        if not str(consult.get("reply") or "").strip():
            return {"status": "invalid",
                    "error": "submit_consult needs a non-empty reply"}, False
        state.submission = consult
        ctx = {"consult": consult, "facts": self.toolbox.facts, "toolbox": self.toolbox,
               "state": self.state, "role": self.role, "lang": self.lang}
        findings: list[dict[str, Any]] = []
        seen: set[str] = set()
        for hook, outcome in self.hooks.run("stop", ctx):
            for finding in outcome.findings:
                if finding.get("rule_id") in seen:
                    continue
                seen.add(finding.get("rule_id"))
                findings.append({**finding, "hook": hook.name})
        state.findings = findings
        answered = {str(r.get("rule_id")) for r in consult.get("rule_responses") or []
                    if isinstance(r, dict)}
        unanswered = [f for f in findings if f["rule_id"] not in answered]
        loop.steps.append({"kind": "submit", "agent": "lead", "findings": len(findings),
                           "unanswered": len(unanswered),
                           "round": state.review_rounds})
        self._emit({"type": "review", "findings": findings,
                    "unanswered": len(unanswered), "round": state.review_rounds})
        if unanswered and state.review_rounds < self.config.max_review_rounds:
            state.review_rounds += 1
            return {
                "status": "review",
                "findings": unanswered,
                "instruction": _t(
                    self.lang,
                    "这是 hooks（规则引擎、分期一致性、引用/剂量溯源、急症优先）"
                    "的复核意见，供你参考，不是硬约束。请逐条判断：采纳就修改"
                    "方案；不采纳就在 rule_responses 写明临床理由。然后再次调用 "
                    "submit_consult。",
                    "These are the hooks' review findings (rule engine, stage consistency, "
                    "citation/dose provenance, emergency first). They are advisory, not hard "
                    "constraints. Weigh each one: if you accept it, revise the plan; if not, "
                    "give your clinical reason in rule_responses. Then call submit_consult "
                    "again."),
            }, False
        return {"status": "accepted"}, True

    @staticmethod
    def _final_consult(state: _TurnState, loop: LoopResult) -> dict[str, Any]:
        """A structured submission stands; otherwise the latest plain answer
        is the answer (a plain-text reply is a valid reply)."""
        consult = state.submission
        if loop.stop in ("text", "limit", "cancelled") and loop.text and (
                consult is None or set(consult) <= {"reply"}):
            consult = {"reply": loop.text}
        return consult or {}

    # --------------------------------------------------------------- input
    def _user_content(self, message: str, facts: dict[str, Any] | None,
                      recorded: list[str], contexts: list[str]) -> Any:
        parts = [message or "（无文字，仅附件/结构化事实）"]
        if facts:
            parts.append("【医生录入的结构化事实（已写入病例笔记）】\n"
                         + json.dumps(facts, ensure_ascii=False))
            notes = [n for n in recorded if isinstance(n, str) and ":" in n]
            if notes:
                parts.append("【录入校验提示】" + "；".join(notes))
        parts += contexts
        if self.plan.items:
            parts.append("【当前会诊计划】\n" + "\n".join(
                f"- [{i['status']}] {i['content']}" for i in self.plan.items))
        attachments = self.toolbox.attachments
        vision = getattr(self.llm, "supports_vision", False)
        if attachments["images"] or attachments["reports"]:
            parts.append(f"【本轮附件】{len(attachments['images'])} 张影像、"
                         f"{len(attachments['reports'])} 份报告图片"
                         + ("（已直接附上，也可用 read_attachment 结构化读取）" if vision
                            else "（可用 read_attachment 读取）"))
        parts.append("【当前病例笔记】" + json.dumps(self.facts, ensure_ascii=False,
                                                default=str))
        text = "\n\n".join(parts)
        refs = attachments["images"] + attachments["reports"]
        if refs and vision:
            from ..perception.imaging import ImagingError, load_image_refs

            try:
                urls = load_image_refs(refs)
            except ImagingError:
                urls = []
            if urls:
                return [{"type": "text", "text": text}] + [
                    {"type": "image_url", "image_url": {"url": u}} for u in urls]
        return text

    def _strip_images(self) -> None:
        """Images ride along for the turn they were sent in, then become a
        placeholder — history never re-uploads base64 slabs."""
        for message in self.messages:
            content = message.get("content")
            if isinstance(content, list):
                texts = [p.get("text", "") for p in content
                         if isinstance(p, dict) and p.get("type") == "text"]
                images = sum(1 for p in content if isinstance(p, dict)
                             and p.get("type") == "image_url")
                message["content"] = "\n".join(texts) + (
                    f"\n[{images} 张附件图片已在当轮查看]" if images else "")

    # ------------------------------------------------------ context window
    def _turn_starts(self) -> list[int]:
        return [i for i, m in enumerate(self.messages)
                if m.get("role") == "user" and isinstance(m.get("content"), str)
                and not m["content"].startswith((_SYSTEM_PREFIX, _SUMMARY_PREFIX))]

    def _maybe_compact(self) -> dict[str, Any] | None:
        cfg = self.config
        if estimate_tokens(self.messages) <= cfg.compact_at * cfg.context_window:
            return None
        outcome = self.compact()
        return outcome if outcome.get("ok") else None

    def compact(self, keep_last: int = 1) -> dict[str, Any]:
        """Summarise all but the last ``keep_last`` turns into one message
        (written by the model; deterministic fallback). Checkpoints before
        the cut are dropped — rewinding across a compaction is impossible."""
        starts = self._turn_starts()
        keep_last = max(0, int(keep_last))
        if len(starts) <= keep_last or len(self.turns) <= keep_last:
            return {"ok": False, "reason": "没有可压缩的早期轮次"}
        before = estimate_tokens(self.messages)
        cut = starts[-keep_last] if keep_last else len(self.messages)
        older = self.turns[:len(self.turns) - keep_last]
        self._emit({"type": "compact_start", "turns": len(older)})
        summary, by_model = summarize(self.llm, older)
        if by_model:
            self.usage["calls"] += 1
        system = self.messages[0] if self.messages and \
            self.messages[0].get("role") == "system" else self._system()
        self.messages = [system, {"role": "user", "content": summary},
                         {"role": "assistant",
                          "content": _t(self.lang, "好的，我已了解此前的会诊经过，会在此基础上继续。",
                                        "Understood — I have the earlier consult and will "
                                        "continue from it.")},
                         *self.messages[cut:]]
        self.checkpoints = []
        self.compactions += 1
        info = {"ok": True, "by_model": by_model, "turns": len(older),
                "before": before, "after": estimate_tokens(self.messages)}
        self._emit({"type": "compact", **info})
        return info

    # --------------------------------------------------------- checkpoints
    def _checkpoint(self, message: str) -> None:
        self.checkpoints.append({
            "turn": len(self.turns), "message": message,
            "messages": len(self.messages), "narrative": len(self.narrative),
            "facts": copy.deepcopy(self.toolbox.facts),
            "plan": copy.deepcopy(self.plan.items),
            "evidence_seen": sorted(self.state["evidence_seen"]),
            "dosing_seen": sorted(self.state["dosing_seen"]),
            "proposals": len(self.memory.proposals),
        })

    def rewind(self, turn: int | None = None) -> dict[str, Any]:
        """Restore the session to just BEFORE ``turn`` (0-based; default:
        the last turn): history, case notes, plan and evidence ledger.
        Returns the rewound message so a surface can put it back in the
        composer."""
        if not self.checkpoints:
            raise ValueError("没有可回退的检查点（会诊尚未开始，或早期轮次已被压缩）")
        if turn is None:
            turn = self.checkpoints[-1]["turn"]
        point = next((c for c in self.checkpoints if c["turn"] == int(turn)), None)
        if point is None:
            available = [c["turn"] for c in self.checkpoints]
            raise ValueError(f"第 {int(turn) + 1} 轮没有检查点（可回退：" +
                             "、".join(str(t + 1) for t in available) + "）")
        removed = self.turns[point["turn"]:]
        del self.messages[point["messages"]:]
        del self.narrative[point["narrative"]:]
        del self.turns[point["turn"]:]
        self.toolbox.facts.clear()
        self.toolbox.facts.update(copy.deepcopy(point["facts"]))
        self.plan.items = copy.deepcopy(point["plan"])
        self.state = {"evidence_seen": set(point["evidence_seen"]),
                      "dosing_seen": set(point["dosing_seen"]), "emergency": None}
        del self.memory.proposals[point["proposals"]:]
        self.checkpoints = [c for c in self.checkpoints if c["turn"] < point["turn"]]
        self._emit({"type": "rewind", "turn": point["turn"]})
        return {"turn": point["turn"], "turns": len(self.turns),
                "removed": len(removed), "message": point["message"],
                "facts": json.loads(json.dumps(self.facts, default=str)),
                "plan": copy.deepcopy(self.plan.items)}

    # ------------------------------------------------------------ describe
    def info(self) -> dict[str, Any]:
        from .commands import listing

        toolset, _ = self._toolset()
        return {
            "role": self.role, "turns": len(self.turns),
            "config": self.config.to_dict(), "parallel": self.parallel,
            "tools": toolset.describe(),
            "agents": [d.to_dict(self.lang) for d in self.definitions],
            "subagents": self.config.subagents,
            "hooks": self.hooks.describe(self.lang),
            "mcp": self.mcp_status,
            "plan": copy.deepcopy(self.plan.items),
            "usage": dict(self.usage), "context": self.context(),
            "checkpoints": [{"turn": c["turn"], "message": c["message"][:120]}
                            for c in self.checkpoints],
            "instructions": self.config.instructions,
            "builtin_agents": [d.to_dict(self.lang) for d in BUILTIN_AGENTS],
            "commands": listing(self.lang),
        }

    def local_command(self, name: str, arg: str = "") -> dict[str, Any]:
        """Run a LOCAL slash command (see :mod:`.commands`); returns
        ``{"command", "text" (Markdown), "data"}``. ``/clear`` is the
        surface's job (it starts a new session)."""
        from .commands import listing

        name = str(name or "").strip().lower()
        lang = self.lang
        en = lang == "en"
        if name == "compact":
            data = self.compact()
            if not data.get("ok"):
                text = _t(lang, data.get("reason", "无需压缩"), "Nothing to compact yet.")
            elif en:
                text = (f"Compacted {data['turns']} earlier turn(s) "
                        f"({'model-written summary' if data['by_model'] else 'deterministic summary'}); "
                        f"context {data['before']} → {data['after']} tokens.")
            else:
                text = (f"已压缩 {data['turns']} 轮早期会诊（{'模型撰写摘要' if data['by_model'] else '确定性摘要'}），"
                        f"上下文 {data['before']} → {data['after']} tokens。")
        elif name == "rewind":
            target = None
            if str(arg).strip():
                try:
                    target = int(str(arg).strip()) - 1
                except ValueError as exc:
                    raise ValueError(_t(lang, "用法：/rewind [轮次]，例如 /rewind 2",
                                        "Usage: /rewind [turn], e.g. /rewind 2")) from exc
            data = self.rewind(target)
            text = _t(lang, f"已回退到第 {data['turn'] + 1} 轮之前（撤销 {data['removed']} 轮）。",
                      f"Rewound to before turn {data['turn'] + 1} ({data['removed']} turn(s) undone).")
        elif name == "usage":
            ctx = self.context()
            u = self.usage
            data = {"usage": dict(u), "context": ctx}
            text = _t(lang,
                      f"模型调用 {u['calls']} 次（其中专科子智能体 {u['subagent_calls']} 次），"
                      f"输入 {u['prompt']} / 输出 {u['completion']} tokens；上下文约 {ctx['tokens']} / "
                      f"{ctx['window']} tokens（{ctx['ratio']:.0%}），已压缩 {self.compactions} 次。",
                      f"{u['calls']} model calls ({u['subagent_calls']} by specialist sub-agents), "
                      f"{u['prompt']} prompt / {u['completion']} completion tokens; context about "
                      f"{ctx['tokens']} / {ctx['window']} tokens ({ctx['ratio']:.0%}); compacted "
                      f"{self.compactions} time(s).")
        elif name == "agents":
            data = {"agents": [d.to_dict(lang) for d in self.definitions],
                    "enabled": self.config.subagents}
            head = _t(lang, "专科子智能体" + ("" if self.config.subagents else "（已停用）") + "：",
                      "Specialist sub-agents" + ("" if self.config.subagents else " (off)") + ":")
            text = head + "\n" + "\n".join(
                f"- **{a['title']}** `{a['name']}` — {a['description']}" for a in data["agents"])
        elif name == "tools":
            toolset, _ = self._toolset()
            data = {"tools": toolset.describe()}
            text = _t(lang, "可调用的工具：", "Tools the agent can call:") + "\n" + "\n".join(
                f"- `{t['name']}`（{t['label']}，{t['category']}）" if not en else
                f"- `{t['name']}` ({t['label']}, {t['category']})" for t in data["tools"])
        elif name == "hooks":
            data = {"hooks": self.hooks.describe(lang)}
            text = _t(lang, "Hooks（均为参考意见，不是硬约束）：",
                      "Hooks (all advisory, never hard constraints):") + "\n" + "\n".join(
                f"- {'✓' if h['enabled'] else '✗'} **{h['title']}** `{h['name']}` "
                f"[{h['event']}] — {h['description']}" for h in data["hooks"])
        elif name == "memory":
            data = {"instructions": self.config.instructions,
                    "proposals": list(self.memory.proposals)}
            if self.config.instructions.strip():
                text = _t(lang, "已载入的机构规范与用户偏好：",
                          "Loaded institution protocols and preferences:") + \
                    "\n\n" + self.config.instructions
            else:
                text = _t(lang, "尚未载入记忆（NSCLC.md / 记忆面板）。",
                          "No memory loaded yet (NSCLC.md / the Memory panel).")
            if self.memory.proposals:
                text += _t(lang, "\n\n本次会诊中智能体提议记住：\n",
                           "\n\nProposed by the agent in this consult:\n") + "\n".join(
                    f"- {p}" for p in self.memory.proposals)
        elif name == "help":
            data = {"commands": listing(lang)}
            text = _t(lang, "命令：", "Commands:") + "\n" + "\n".join(
                f"- `/{c['name']}{(' ' + c['args']) if c['args'] else ''}` — {c['description']}"
                for c in data["commands"])
        else:
            raise ValueError(_t(lang, f"/{name} 不是本地命令", f"/{name} is not a local command"))
        return {"command": name, "text": text, "data": data}

    # ------------------------------------------------------- serialization
    def to_dict(self) -> dict[str, Any]:
        return {"format": SESSION_FORMAT, "mode": "agent",
                "facts": self.facts, "narrative": self.narrative,
                "turns": self.turns, "messages": self.messages[1:],
                "plan": self.plan.items,
                "ledger": {"evidence_seen": sorted(self.state["evidence_seen"]),
                           "dosing_seen": sorted(self.state["dosing_seen"])},
                "checkpoints": self.checkpoints, "usage": self.usage,
                "compactions": self.compactions}

    @classmethod
    def load(cls, data: dict[str, Any], llm: Any, *, role: str = "oncologist",
             vision_llm: Any | None = None, **kwargs: Any) -> "AgentSession":
        """Resume a session. Authority (role, configuration) comes from the
        CALLER, never from the file; the system prompt is rebuilt, never
        restored; facts are re-validated."""
        if not isinstance(data, dict) or data.get("format") not in _READABLE_FORMATS:
            raise ValueError("not an NSCLC-Agent agent session")
        session = cls(llm, role=role, vision_llm=vision_llm, **kwargs)
        session.toolbox.facts.update(_clean_facts(data.get("facts")))
        session.narrative = [str(x) for x in data.get("narrative") or []]
        session.turns = [t for t in data.get("turns") or [] if isinstance(t, dict)]
        messages = [m for m in data.get("messages") or []
                    if isinstance(m, dict) and m.get("role") in ("user", "assistant", "tool")]
        session.messages = [session._system(), *messages]
        repair_history(session.messages, "not executed")
        session.plan.items = Plan(data.get("plan") if isinstance(data.get("plan"), list)
                                  else []).items
        ledger = data.get("ledger") if isinstance(data.get("ledger"), dict) else {}
        for key in ("evidence_seen", "dosing_seen"):
            session.state[key] = {str(x) for x in ledger.get(key) or []}
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        for key in session.usage:
            try:
                session.usage[key] = max(0, int(usage.get(key, 0)))
            except (TypeError, ValueError):
                pass
        session.compactions = int(data.get("compactions") or 0) \
            if str(data.get("compactions") or "0").isdigit() else 0
        session.checkpoints = _clean_checkpoints(data.get("checkpoints"), session)
        return session


def _clean_facts(raw: Any) -> dict[str, Any]:
    from ..conversation import merge_facts, sanitize_fact_payload

    cleaned, _ = sanitize_fact_payload(dict(raw) if isinstance(raw, dict) else {})
    out: dict[str, Any] = {}
    merge_facts(out, cleaned, overwrite=True)
    return out


def _clean_checkpoints(raw: Any, session: AgentSession) -> list[dict[str, Any]]:
    """Checkpoints from a file are re-validated; any inconsistency drops
    them all (the session still loads, it just cannot rewind)."""
    out: list[dict[str, Any]] = []
    try:
        for item in raw or []:
            point = {"turn": int(item["turn"]), "message": str(item.get("message") or ""),
                     "messages": int(item["messages"]), "narrative": int(item["narrative"]),
                     "facts": _clean_facts(item.get("facts")),
                     "plan": Plan(item.get("plan") or []).items,
                     "evidence_seen": [str(x) for x in item.get("evidence_seen") or []],
                     "dosing_seen": [str(x) for x in item.get("dosing_seen") or []],
                     "proposals": 0}
            if not (0 <= point["turn"] < len(session.turns) + 1
                    and 1 <= point["messages"] <= len(session.messages)
                    and 0 <= point["narrative"] <= len(session.narrative)):
                return []
            out.append(point)
    except (KeyError, TypeError, ValueError):
        return []
    return out


def hook_defaults(cfg: AgentConfig) -> dict[str, bool]:
    """Enabled map for :class:`HookRunner`: in full autonomy the hooks that
    compare the model with the kernel default to off; explicit settings
    always win."""
    base = {name: False for name in KERNEL_HOOKS} if cfg.autonomy == "full" else {}
    return {**base, **cfg.hooks}


def runtime_catalog(config: AgentConfig | dict[str, Any] | None = None) -> dict[str, Any]:
    """What a consult would run with — tools, specialists, hooks, commands —
    without a model or a session (settings panels, ``/help`` before the
    first turn)."""
    from .commands import listing

    cfg = config if isinstance(config, AgentConfig) else AgentConfig.from_dict(config)
    toolbox = AgentToolbox()
    disabled = set(cfg.disabled_agents)
    agents = {d.name: d for d in BUILTIN_AGENTS if d.name not in disabled}
    for raw in cfg.custom_agents:
        try:
            custom = AgentDefinition.from_dict(raw)
        except (ValueError, TypeError):
            continue
        if custom.name not in disabled:
            agents[custom.name] = custom
    lang = cfg.language
    exclude = KERNEL_TOOLS if cfg.autonomy == "full" else frozenset()
    tools = Toolset.of(toolbox.as_tools(lang=lang, exclude=exclude),
                       [Plan(language=lang).tool(), MemoryNotes(language=lang).tool()]).describe()
    if cfg.subagents and agents:
        tools.append({"name": "delegate", "category": "delegation",
                      "label": _t(lang, "请专科会诊", "Specialist consult"),
                      "description": "Delegate a focused question to a specialist sub-agent"})
    tools.append({"name": SUBMIT_TOOL, "category": "control",
                  "label": _t(lang, "提交会诊结论", "Submit conclusion"),
                  "description": "Submit the consult conclusion (terminal)"})
    return {"role": None, "turns": 0, "config": cfg.to_dict(), "tools": tools,
            "agents": [d.to_dict(lang) for d in agents.values()],
            "builtin_agents": [d.to_dict(lang) for d in BUILTIN_AGENTS],
            "subagents": cfg.subagents,
            "hooks": HookRunner(builtin_hooks(), hook_defaults(cfg)).describe(lang),
            "mcp": [], "plan": [], "usage": {}, "context": {},
            "checkpoints": [], "instructions": cfg.instructions,
            "commands": listing(lang)}
