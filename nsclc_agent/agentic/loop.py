"""The agent loop — one implementation for the lead agent and every
specialist sub-agent.

    model → (thinking, text, tool calls) → run tools → observations → model …

until the model calls its terminal tool (``submit_consult`` /
``submit_report``), answers in plain text, runs out of steps, or is
cancelled. Mainstream-runtime properties:

* tool calls in one model step run **concurrently** when they are
  read-only and threads exist (natively); state-mutating tools keep call
  order; the browser build runs everything serially;
* reasoning is captured from ``reasoning_content`` / ``<think>`` blocks;
* a plain-text answer is accepted, after one nudge toward the terminal tool;
* truncated output is continued, not swallowed;
* the history is always valid: every assistant tool call gets a tool
  message, even when the run is interrupted (``repair_history``);
* everything is reported through ``emit`` as typed events tagged with the
  agent that produced them.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .tools import Toolset

_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)

#: Observation size the model sees per tool call.
OBSERVATION_LIMIT = 6000

Emit = Callable[[dict[str, Any]], None]
#: Terminal handler: (arguments) -> (observation, done)
Terminal = Callable[[dict[str, Any]], tuple[dict[str, Any], bool]]


class Cancelled(Exception):
    """Raised inside the loop when the caller asked to stop."""


@dataclass
class LoopResult:
    steps: list[dict[str, Any]] = field(default_factory=list)
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    #: terminal | text | limit | cancelled
    stop: str = ""
    text: str = ""


def compact(data: Any, limit: int = OBSERVATION_LIMIT) -> str:
    """JSON for the model, bounded: long observations are cut, never dropped."""
    text = json.dumps(data, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + f"… [truncated {len(text) - limit} chars]"


def split_reasoning(response: Any) -> tuple[str, str]:
    """(thinking, visible text) from a provider response."""
    text = response.text or ""
    thinking = ""
    raw = getattr(response, "raw", None) or {}
    try:
        message = (raw.get("choices") or [{}])[0].get("message") or {}
    except (AttributeError, IndexError, TypeError):
        message = {}
    for key in ("reasoning_content", "reasoning"):
        if isinstance(message.get(key), str) and message[key].strip():
            thinking = message[key].strip()
            break
    found = _THINK_RE.findall(text)
    if found:
        thinking = "\n".join([thinking] + [f.strip() for f in found]).strip()
        text = _THINK_RE.sub("", text)
    return thinking, text.strip()


def assistant_message(response: Any, step: int) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": response.text or "",
        "tool_calls": [{
            "id": call.id or f"call_{step}_{i}_{call.name}",
            "type": "function",
            "function": {"name": call.name,
                         "arguments": json.dumps(call.arguments, ensure_ascii=False)},
        } for i, call in enumerate(response.tool_calls)],
    }


def repair_history(messages: list[dict[str, Any]], note: str = "interrupted") -> int:
    """Answer any assistant tool call that has no tool message (an
    interrupted step), so the history stays valid for every provider."""
    answered = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
    repaired = 0
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.get("role") != "assistant" or not message.get("tool_calls"):
            continue
        missing = [tc["id"] for tc in message["tool_calls"] if tc["id"] not in answered]
        if not missing:
            break
        insert_at = index + 1
        while insert_at < len(messages) and messages[insert_at].get("role") == "tool":
            insert_at += 1
        for call_id in missing:
            messages.insert(insert_at, {"role": "tool", "tool_call_id": call_id,
                                        "content": json.dumps({"status": note})})
            insert_at += 1
            repaired += 1
        break
    return repaired


def run_loop(
    llm: Any,
    messages: list[dict[str, Any]],
    toolset: Toolset,
    *,
    emit: Emit,
    terminal: Terminal,
    terminal_name: str,
    agent: str = "lead",
    max_steps: int = 16,
    temperature: float = 0.2,
    max_tokens: int = 6000,
    parallel: bool = False,
    cancelled: Callable[[], bool] = lambda: False,
    nudge: str = "",
    post_tool: Callable[[str, dict[str, Any], dict[str, Any]], str | None] | None = None,
    base_event: dict[str, Any] | None = None,
    result: LoopResult | None = None,
) -> LoopResult:
    """Run one agent until it submits, answers, exhausts steps or is
    cancelled. ``messages`` is extended in place; pass ``result`` to keep
    the partial trace when the run is interrupted."""
    tag = dict(base_event or {}, agent=agent)
    result = result if result is not None else LoopResult()
    specs = toolset.specs()
    nudged = not nudge

    def say(event: dict[str, Any]) -> None:
        emit({**tag, **event})

    for step in range(max_steps):
        if cancelled():
            result.stop = "cancelled"
            raise Cancelled()
        say({"type": "llm_call", "step": step + 1})
        response = llm.chat(messages, tools=specs, temperature=temperature,
                            max_tokens=max_tokens)
        result.calls += 1
        result.prompt_tokens += response.prompt_tokens
        result.completion_tokens += response.completion_tokens
        result.model = response.model or result.model
        say({"type": "usage", "prompt_tokens": response.prompt_tokens,
             "completion_tokens": response.completion_tokens})
        thinking, visible = split_reasoning(response)
        if thinking:
            result.steps.append({"kind": "thinking", "text": thinking, "agent": agent})
            say({"type": "thinking", "text": thinking})

        if not response.tool_calls:
            messages.append({"role": "assistant", "content": response.text or ""})
            if visible:
                result.text = visible
            if response.truncated:
                messages.append({"role": "user", "content":
                                 "[系统] 你的上一条输出被截断。请更简洁地继续：直接调用工具，"
                                 f"或调用 {terminal_name} 提交。"})
                continue
            if not nudged:
                nudged = True
                result.steps.append({"kind": "message", "text": visible, "agent": agent})
                say({"type": "message", "text": visible})
                messages.append({"role": "user", "content": f"[系统] {nudge}"})
                continue
            result.stop = "text"
            return result

        if visible:
            result.steps.append({"kind": "message", "text": visible, "agent": agent})
            say({"type": "message", "text": visible})
        assistant = assistant_message(response, step)
        messages.append(assistant)
        calls = [(tc["id"], call) for tc, call in zip(assistant["tool_calls"],
                                                       response.tool_calls)]
        observations: dict[str, str] = {}
        entries: dict[str, dict[str, Any]] = {}
        done = False

        # 1) ordinary tools — concurrently where safe
        ordinary = [(cid, c) for cid, c in calls if c.name != terminal_name]
        for cid, call in ordinary:
            say({"type": "tool_call", "id": cid, "name": call.name, "args": call.arguments})
        safe = [(cid, c) for cid, c in ordinary
                if (toolset.get(c.name) is None or toolset.get(c.name).parallel_safe)]
        serial = [(cid, c) for cid, c in ordinary if (cid, c) not in safe]

        def execute(cid: str, call: Any) -> tuple[str, str]:
            t0 = time.perf_counter()
            tool = toolset.get(call.name)
            if tool is None:
                out = {"ok": False, "summary": f"unknown tool {call.name!r}",
                       "data": {"error": f"unknown tool {call.name!r}",
                                "available": toolset.names()}}
            else:
                out = tool.run(call.arguments)
            ms = round((time.perf_counter() - t0) * 1000)
            ui = out.pop("_ui", None) or {}
            entry = {"kind": "tool", "id": cid, "name": call.name,
                     "args": call.arguments, "ok": bool(out.get("ok")),
                     "summary": out.get("summary", ""), "ms": ms, "agent": agent,
                     **ui}
            entries[cid] = entry
            say({"type": "tool_result", **{k: v for k, v in entry.items()
                                           if k != "children"}})
            payload = {"ok": entry["ok"], "summary": entry["summary"],
                       "data": out.get("data")}
            if post_tool is not None:
                extra = post_tool(call.name, call.arguments, out)
                if extra:
                    payload["hook"] = extra
            return cid, compact(payload)

        try:
            if parallel and len(safe) > 1:
                from concurrent.futures import ThreadPoolExecutor

                pool = ThreadPoolExecutor(max_workers=min(6, len(safe)))
                try:
                    for cid, text in pool.map(lambda pair: execute(*pair), safe):
                        observations[cid] = text
                except BaseException:
                    # Ctrl-C / cancel: do not wait for running specialists —
                    # they stop at their next step via ``cancelled``.
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise
                pool.shutdown(wait=True)
            else:
                for cid, call in safe:
                    if cancelled():
                        raise Cancelled()
                    observations[cid] = execute(cid, call)[1]
            for cid, call in serial:
                if cancelled():
                    raise Cancelled()
                observations[cid] = execute(cid, call)[1]
        finally:
            # the trace keeps CALL order, whatever order tools finished in
            result.steps.extend(entries[cid] for cid, _c in ordinary if cid in entries)

        # 2) the terminal tool, last
        for cid, call in calls:
            if call.name != terminal_name:
                continue
            observation, finished = terminal(call.arguments)
            observations[cid] = compact(observation)
            done = done or finished

        for cid, _call in calls:
            messages.append({"role": "tool", "tool_call_id": cid,
                             "content": observations.get(cid, json.dumps({"status": "skipped"}))})
        if done:
            result.stop = "terminal"
            return result

    result.steps.append({"kind": "limit", "agent": agent,
                         "text": f"reached the step budget ({max_steps})"})
    result.stop = "limit"
    return result
