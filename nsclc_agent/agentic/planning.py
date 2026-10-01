"""The consult plan: a live checklist the agent writes for itself.

Mainstream coding agents (Claude Code's TodoWrite, Codex's update_plan,
Grok CLI's todo lists) plan multi-step work in the open and tick items off
as they go. Here the plan is the work-up of a case — confirm staging, read
the drivers, check indications, consult specialists, weigh the evidence —
and it is part of the case: it survives turns, the clinician sees it live,
and the dossier shows it.
"""

from __future__ import annotations

from typing import Any, Callable

from .tools import Tool, obj

STATUSES = ("pending", "in_progress", "completed", "cancelled")


class Plan:
    def __init__(self, items: list[dict[str, Any]] | None = None,
                 on_change: Callable[[list[dict[str, Any]]], None] | None = None) -> None:
        self.items: list[dict[str, Any]] = [self._clean(i) for i in items or []
                                            if isinstance(i, dict)]
        self.explanation = ""
        self.on_change = on_change

    @staticmethod
    def _clean(item: dict[str, Any]) -> dict[str, Any]:
        status = str(item.get("status") or "pending")
        return {"content": str(item.get("content") or item.get("step") or "").strip()[:200],
                "status": status if status in STATUSES else "pending"}

    def update(self, items: list[dict[str, Any]], explanation: str = "") -> dict[str, Any]:
        cleaned = [self._clean(i) for i in items or [] if isinstance(i, dict)]
        cleaned = [i for i in cleaned if i["content"]][:20]
        in_progress = [i for i in cleaned if i["status"] == "in_progress"]
        if not cleaned:
            return {"ok": False, "summary": "empty plan",
                    "data": {"error": "give at least one step"}}
        self.items = cleaned
        self.explanation = str(explanation or "")[:400]
        if self.on_change:
            self.on_change(self.items)
        done = sum(1 for i in cleaned if i["status"] == "completed")
        note = None
        if len(in_progress) > 1:
            note = "keep exactly one step in_progress at a time"
        return {"ok": True,
                "summary": f"计划 {done}/{len(cleaned)} 完成"
                           + (f" · 进行中：{in_progress[0]['content']}" if in_progress else ""),
                "data": {"items": cleaned, **({"note": note} if note else {})},
                "_ui": {"plan": cleaned}}

    def tool(self) -> Tool:
        return Tool(
            "update_plan",
            "Write or update your consult plan — a short checklist of the "
            "work-up you are doing for this case (e.g. confirm staging, read "
            "drivers, check indications, consult specialists, weigh evidence, "
            "answer). Send the WHOLE list each time with each step's status "
            "(pending / in_progress / completed / cancelled); keep one step "
            "in_progress. Use it for anything beyond a trivial question; the "
            "clinician watches it live.",
            obj({"items": {"type": "array", "items": obj({
                     "content": {"type": "string"},
                     "status": {"type": "string", "enum": list(STATUSES)}},
                     ["content", "status"])},
                 "explanation": {"type": "string"}}, ["items"]),
            handler=self.update, category="planning", parallel_safe=False,
            label="更新会诊计划")
