"""Tool runtime: one ``Tool`` abstraction for every capability the agent can
call — clinical instruments, planning, delegation to specialists, memory,
MCP servers, and the terminal ``submit_*`` tool.

This is the shape mainstream agent runtimes share (Claude Code, Grok CLI,
OpenAI Agents SDK): a tool is a JSON-schema'd function with a handler and a
few execution properties; a ``Toolset`` is what one agent may see. Each
agent — the lead or a specialist sub-agent — gets its own filtered toolset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from ..llm.base import ToolSpec

#: Handler contract: return {"ok": bool, "summary": str, "data": Any}.
Handler = Callable[..., dict[str, Any]]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Handler | None = None
    #: clinical | planning | delegation | memory | mcp | control
    category: str = "clinical"
    #: Read-only tools may run concurrently within one model step;
    #: state-mutating ones (case notes, plan) run in call order.
    parallel_safe: bool = True
    #: Terminal tools end the loop; the loop intercepts them.
    terminal: bool = False
    #: Short label for timelines (UI); falls back to the name.
    label: str = ""

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.parameters)

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.handler is None:
            return {"ok": False, "summary": f"{self.name} has no handler",
                    "data": {"error": "not executable"}}
        try:
            out = self.handler(**(arguments if isinstance(arguments, dict) else {}))
        except TypeError as exc:
            return {"ok": False, "summary": f"bad arguments: {exc}",
                    "data": {"error": f"bad arguments: {exc}",
                             "hint": "check the tool's parameter schema"}}
        except Exception as exc:  # noqa: BLE001 - a tool bug is an observation
            return {"ok": False, "summary": f"{self.name} failed: {type(exc).__name__}",
                    "data": {"error": f"{type(exc).__name__}: {exc}"}}
        if not isinstance(out, dict) or "ok" not in out:
            return {"ok": True, "summary": f"{self.name} done", "data": out}
        out.setdefault("summary", "")
        out.setdefault("data", None)
        return out


@dataclass
class Toolset:
    """An ordered, name-unique collection of tools."""

    tools: dict[str, Tool] = field(default_factory=dict)

    @classmethod
    def of(cls, *groups: Iterable[Tool]) -> "Toolset":
        toolset = cls()
        for group in groups:
            for tool in group:
                toolset.add(tool)
        return toolset

    def add(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def names(self) -> list[str]:
        return list(self.tools)

    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self.tools.values()]

    def only(self, names: Iterable[str]) -> "Toolset":
        wanted = set(names)
        return Toolset({n: t for n, t in self.tools.items() if n in wanted})

    def without(self, names: Iterable[str]) -> "Toolset":
        drop = set(names)
        return Toolset({n: t for n, t in self.tools.items() if n not in drop})

    def describe(self) -> list[dict[str, str]]:
        return [{"name": t.name, "category": t.category,
                 "label": t.label or t.name,
                 "description": t.description.split(". ")[0][:160]}
                for t in self.tools.values()]


def obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema
