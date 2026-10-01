"""Memory and context management.

* **Instructions memory** — like CLAUDE.md / GROK.md: institution protocols
  and user preferences in plain Markdown, loaded into every system prompt
  (``NSCLC.md`` in the working directory or ``~/.nsclc-agent/``; in the web
  app, the 记忆 panel). The agent can PROPOSE additions with ``remember``;
  the clinician decides whether they are saved.
* **Context accounting** — a provider-neutral token estimate (CJK ≈ 1
  token/char, other text ≈ 4 chars/token) against the model's window.
* **Compaction** — when the conversation approaches the window, older turns
  are summarised BY THE MODEL into a clinical summary (facts, decisions,
  open questions, preferences); a deterministic summary is the fallback.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .tools import Tool, obj

_CJK_RE = re.compile(r"[　-鿿가-힯＀-￯]")

INSTRUCTION_FILES = ("NSCLC.md", ".nsclc-agent/NSCLC.md")


def estimate_tokens(value: Any) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False,
                                                           default=str)
    cjk = len(_CJK_RE.findall(text))
    return cjk + (len(text) - cjk + 3) // 4


def load_instructions(cwd: Path | None = None, home: Path | None = None) -> str:
    """Concatenate the user (~/.nsclc-agent/NSCLC.md) and project
    (./NSCLC.md, ./.nsclc-agent/NSCLC.md) instruction files."""
    parts = []
    home = home or Path.home()
    candidates = [home / ".nsclc-agent" / "NSCLC.md"]
    base = cwd or Path.cwd()
    candidates += [base / name for name in INSTRUCTION_FILES]
    seen = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        text = path.read_text(encoding="utf-8").strip()
        if text:
            parts.append(f"<!-- {path} -->\n{text[:8000]}")
    return "\n\n".join(parts)


class MemoryNotes:
    """Proposals the agent makes with ``remember`` (the clinician saves them)."""

    def __init__(self, language: str = "zh") -> None:
        self.proposals: list[str] = []
        self.language = language

    def remember(self, note: str, scope: str = "preference") -> dict[str, Any]:
        note = str(note or "").strip()[:500]
        if not note:
            return {"ok": False, "summary": "empty note", "data": {"error": "empty"}}
        self.proposals.append(note)
        return {"ok": True,
                "summary": (f"proposed for memory ({scope}): {note[:60]}"
                            if self.language == "en" else f"提议记住（{scope}）：{note[:60]}"),
                "data": {"status": "proposed — the clinician decides whether to save it"},
                "_ui": {"memory": note}}

    def tool(self) -> Tool:
        return Tool(
            "remember",
            "Propose a durable note for the institution/user memory — a "
            "preference or local protocol that should apply to FUTURE consults "
            "(e.g. 'prefers CSCO guideline ranking', 'osimertinib not on local "
            "formulary'). Not for case facts (use record_case_facts). The "
            "clinician confirms before it is saved.",
            obj({"note": {"type": "string"},
                 "scope": {"type": "string", "enum": ["preference", "protocol", "formulary"]}},
                ["note"]),
            handler=self.remember, category="memory", parallel_safe=False,
            label="Propose for memory" if self.language == "en" else "提议写入记忆")


COMPACT_PROMPT = """你是会诊记录压缩器。把下面的多轮会诊记录压缩成一份供主诊智能体继续工作的摘要（中文，Markdown 要点）：
1. 病例事实（分期、组织学、驱动基因、PD-L1、PS、器官功能、治疗史等）
2. 已经做出的判断与方案，以及理由
3. 各专科意见要点与分歧
4. 规则引擎提示及处理情况
5. 尚未解决的问题、待补检查
6. 医生表达的偏好
只写记录中出现过的内容，不要新增临床内容。"""


def transcript_text(turns: list[dict[str, Any]], limit: int = 30000) -> str:
    lines = []
    for i, t in enumerate(turns, 1):
        lines.append(f"### 第 {i} 轮\n医生：{t.get('message', '')}\n智能体结论：{t.get('reply', '')}")
    text = "\n\n".join(lines)
    return text[-limit:]


def deterministic_summary(turns: list[dict[str, Any]]) -> str:
    out = ["【此前会诊摘要】"]
    for t in turns:
        out.append(f"医生：{str(t.get('message', ''))[:400]}")
        out.append(f"结论：{str(t.get('reply', ''))[:800]}")
    return "\n".join(out)


def summarize(llm: Any, turns: list[dict[str, Any]]) -> tuple[str, bool]:
    """(summary, by_model). Falls back to a deterministic digest."""
    try:
        response = llm.chat([{"role": "system", "content": COMPACT_PROMPT},
                             {"role": "user", "content": transcript_text(turns)}],
                            temperature=0.0, max_tokens=2000)
        text = re.sub(r"<think>.*?</think>", "", response.text or "", flags=re.DOTALL).strip()
        if len(text) > 40:
            return "【此前会诊摘要（模型压缩）】\n" + text, True
    except Exception:  # noqa: BLE001 - compaction must never kill a consult
        pass
    return deterministic_summary(turns), False
