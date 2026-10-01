"""Live terminal rendering of agent events (the CLI's timeline).

Lead-agent steps are flush left; a specialist's steps are prefixed with its
title, so parallel sub-agents stay readable when their lines interleave.
"""

from __future__ import annotations

import json
import threading
from typing import Any, TextIO

_PLAN_MARK = {"pending": "☐", "in_progress": "◐", "completed": "☑", "cancelled": "✕"}

_TEXT = {
    "zh": {"accept": "┌ 接受会诊：", "done": "└ 已提交专科意见", "conf": "（把握度 {}）",
           "failed": "└ 会诊失败", "think": "· 思考：", "ask": "⇢ 请{}会诊：", "plan": "▤ 会诊计划",
           "emergency": "⚠ 急症筛查：", "sep": "、",
           "review": "⚖ hooks 复核：{} 条参考意见（未回应 {}）", "no_review": "⚖ hooks 复核：无意见",
           "compacting": "⊜ 正在压缩 {} 轮早期会诊…", "compacted": "⊜ 上下文已压缩：{} → {} tokens",
           "mcp_ok": "⧉ MCP {}：{} 个工具", "mcp_bad": "⧉ MCP {} 连接失败：{}", "stopped": "■ 已停止"},
    "en": {"accept": "┌ consult accepted: ", "done": "└ specialist opinion submitted",
           "conf": " (confidence {})", "failed": "└ consult failed", "think": "· thinking: ",
           "ask": "⇢ consulting {}: ", "plan": "▤ consult plan", "emergency": "⚠ emergency screen: ",
           "sep": ", ", "review": "⚖ hooks review: {} advisory finding(s) ({} unanswered)",
           "no_review": "⚖ hooks review: no findings",
           "compacting": "⊜ compacting {} earlier turn(s)…",
           "compacted": "⊜ context compacted: {} → {} tokens",
           "mcp_ok": "⧉ MCP {}: {} tool(s)", "mcp_bad": "⧉ MCP {} failed to connect: {}",
           "stopped": "■ stopped"},
}


class TerminalView:
    def __init__(self, stream: TextIO, *, color: bool | None = None,
                 labels: dict[str, str] | None = None, lang: str = "zh") -> None:
        self.t = _TEXT["en" if lang == "en" else "zh"]
        self.lang = lang
        self.stream = stream
        self.color = stream.isatty() if color is None else color
        self.labels = dict(labels or {})
        self.titles: dict[str, str] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------- styling
    def _c(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def _line(self, event: dict[str, Any], text: str, style: str = "") -> None:
        agent = event.get("agent") or "lead"
        prefix = "  "
        if event.get("depth") or (agent != "lead" and event.get("type", "").startswith("subagent")):
            prefix = "  │ " + self._c("35", f"[{self.titles.get(agent, agent)}] ")
        with self._lock:
            print(prefix + (self._c(style, text) if style else text), file=self.stream,
                  flush=True)

    @staticmethod
    def _short(value: Any, limit: int = 100) -> str:
        text = json.dumps(value, ensure_ascii=False, default=str)
        return text if len(text) <= limit else text[:limit] + "…"

    # --------------------------------------------------------------- events
    def __call__(self, event: dict[str, Any]) -> None:  # noqa: C901 - a dispatch table
        kind = event.get("type")
        if kind == "subagent_start":
            self.titles[event.get("agent", "")] = event.get("title") or event.get("agent")
            self._line({**event, "depth": 1}, self.t["accept"] + str(event.get("task", "")), "35")
        elif kind == "subagent_end":
            self._line({**event, "depth": 1},
                       self.t["done"] + (self.t["conf"].format(event["confidence"])
                                         if event.get("confidence") else "")
                       if event.get("ok") else self.t["failed"], "35")
        elif kind == "thinking":
            self._line(event, self.t["think"] + str(event.get("text") or "")[:220]
                       .replace("\n", " "), "2")
        elif kind == "message":
            self._line(event, str(event.get("text") or "")[:220].replace("\n", " "))
        elif kind == "tool_call":
            name = event.get("name", "")
            args = event.get("args") or {}
            if name == "delegate":
                who = self.titles.get(str(args.get("agent")), args.get("agent"))
                self._line(event, self.t["ask"].format(who) + str(args.get("task", "")), "36")
            elif name == "update_plan":
                pass  # the plan event renders it
            else:
                self._line(event, f"→ {self.labels.get(name, name)} {self._short(args)}", "36")
        elif kind == "tool_result":
            if event.get("name") == "update_plan":
                return
            mark = self._c("32", "✓") if event.get("ok") else self._c("31", "✗")
            ms = f" ({event['ms']} ms)" if event.get("ms") else ""
            self._line(event, f"  {mark} {event.get('summary', '')}{ms}")
        elif kind == "plan":
            items = event.get("items") or []
            done = sum(1 for i in items if i.get("status") == "completed")
            self._line(event, f"{self.t['plan']} {done}/{len(items)}", "1")
            for item in items:
                mark = _PLAN_MARK.get(item.get("status"), "☐")
                style = "2" if item.get("status") in ("completed", "cancelled") else ""
                self._line(event, f"  {mark} {item.get('content', '')}", style)
        elif kind == "hook":
            self._line(event, f"◇ {str(event.get('text'))[:220]}", "2")
        elif kind == "alert":
            emergency = event.get("emergency") or {}
            signals = emergency.get("signals") or []
            if self.lang == "en":
                from ..i18n import english_half

                signals = [english_half(str(x)) for x in signals]
            self._line(event, self.t["emergency"] + self.t["sep"].join(signals), "33")
        elif kind == "review":
            findings = event.get("findings") or []
            if findings:
                self._line(event, self.t["review"].format(len(findings),
                                                          event.get("unanswered", 0)), "33")
                for finding in findings[:8]:
                    self._line(event, f"  [{finding.get('severity')}] {finding.get('rule_id')}"
                                      f" — {str(finding.get('message'))[:120]}", "33")
            else:
                self._line(event, self.t["no_review"], "2")
        elif kind == "compact_start":
            self._line(event, self.t["compacting"].format(event.get("turns")), "2")
        elif kind == "compact":
            self._line(event, self.t["compacted"].format(event.get("before"), event.get("after")), "2")
        elif kind == "mcp":
            for server in event.get("servers") or []:
                if server.get("ok"):
                    self._line(event, self.t["mcp_ok"].format(server["name"],
                                                              len(server.get("tools") or [])))
                else:
                    self._line(event, self.t["mcp_bad"].format(server["name"], server.get("error")), "31")
        elif kind == "cancelled":
            self._line(event, self.t["stopped"], "33")
        elif kind == "error":
            self._line(event, f"✗ {event.get('message')}", "31")
