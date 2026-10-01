"""Live terminal rendering of agent events (the CLI's timeline).

Lead-agent steps are flush left; a specialist's steps are prefixed with its
title, so parallel sub-agents stay readable when their lines interleave.
"""

from __future__ import annotations

import json
import threading
from typing import Any, TextIO

_PLAN_MARK = {"pending": "☐", "in_progress": "◐", "completed": "☑", "cancelled": "✕"}


class TerminalView:
    def __init__(self, stream: TextIO, *, color: bool | None = None,
                 labels: dict[str, str] | None = None) -> None:
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
            self._line({**event, "depth": 1}, f"┌ 接受会诊：{event.get('task', '')}", "35")
        elif kind == "subagent_end":
            self._line({**event, "depth": 1},
                       "└ 已提交专科意见" + (f"（把握度 {event['confidence']}）"
                                        if event.get("confidence") else "")
                       if event.get("ok") else "└ 会诊失败", "35")
        elif kind == "thinking":
            self._line(event, "· 思考：" + str(event.get("text") or "")[:220]
                       .replace("\n", " "), "2")
        elif kind == "message":
            self._line(event, str(event.get("text") or "")[:220].replace("\n", " "))
        elif kind == "tool_call":
            name = event.get("name", "")
            args = event.get("args") or {}
            if name == "delegate":
                who = self.titles.get(str(args.get("agent")), args.get("agent"))
                self._line(event, f"⇢ 请{who}会诊：{args.get('task', '')}", "36")
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
            self._line(event, f"▤ 会诊计划 {done}/{len(items)}", "1")
            for item in items:
                mark = _PLAN_MARK.get(item.get("status"), "☐")
                style = "2" if item.get("status") in ("completed", "cancelled") else ""
                self._line(event, f"  {mark} {item.get('content', '')}", style)
        elif kind == "hook":
            self._line(event, f"◇ {str(event.get('text'))[:220]}", "2")
        elif kind == "alert":
            emergency = event.get("emergency") or {}
            self._line(event, "⚠ 急症筛查：" + "、".join(emergency.get("signals") or []), "33")
        elif kind == "review":
            findings = event.get("findings") or []
            if findings:
                self._line(event, f"⚖ hooks 复核：{len(findings)} 条参考意见"
                                  f"（未回应 {event.get('unanswered', 0)}）", "33")
                for finding in findings[:8]:
                    self._line(event, f"  [{finding.get('severity')}] {finding.get('rule_id')}"
                                      f" — {str(finding.get('message'))[:120]}", "33")
            else:
                self._line(event, "⚖ hooks 复核：无意见", "2")
        elif kind == "compact_start":
            self._line(event, f"⊜ 正在压缩 {event.get('turns')} 轮早期会诊…", "2")
        elif kind == "compact":
            self._line(event, f"⊜ 上下文已压缩：{event.get('before')} → {event.get('after')} tokens", "2")
        elif kind == "mcp":
            for server in event.get("servers") or []:
                if server.get("ok"):
                    self._line(event, f"⧉ MCP {server['name']}：{len(server.get('tools') or [])} 个工具")
                else:
                    self._line(event, f"⧉ MCP {server['name']} 连接失败：{server.get('error')}", "31")
        elif kind == "cancelled":
            self._line(event, "■ 已停止", "33")
        elif kind == "error":
            self._line(event, f"✗ {event.get('message')}", "31")
