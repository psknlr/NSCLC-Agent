"""Provider-neutral LLM interface for the NSCLC harness.

The model is an *advisory* component inside a hard control plane: it may
propose a task plan, choose tools within its skill's grant, compose interview
questions and draft treatment reasoning — but it can never assign a stage
group, originate a dose value, clear a rule-detected safety issue, or widen a
capability. Every provider therefore only has to implement
:meth:`LLMClient.chat`.

``extract_json`` follows the "liberal in shape, strict in content" rule: the
*shape* of a model reply (code fences, prose margins, trailing commas,
single-quoted dicts) is tolerated because every consumer validates the
*content* afterwards — schemas check fields and types, the plan validator
checks the agent catalog, the broker checks capability, the dose gate scans
for numerics. Rejecting a payload over a trailing comma buys no safety and
silently downgrades the whole model-driven path to its deterministic fallback.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMError(RuntimeError):
    """Raised for transport/protocol failures. Always caught by callers."""


@dataclass
class ToolSpec:
    """OpenAI-style function schema, shared by every supported provider."""

    name: str
    description: str
    parameters: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    #: Provider-assigned id; echoed back on the matching ``role: "tool"`` message.
    id: str = ""


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    provider: str = ""
    #: ``stop`` | ``length`` | ``tool_calls`` | provider-specific. ``length``
    #: means the reply was truncated — a run must flag that, never swallow it.
    finish_reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length"

    def json(self, default: Any = None) -> Any:
        """Best-effort JSON extraction from the response text."""
        return extract_json(self.text, default)

    def to_journal(self) -> dict[str, Any]:
        """Serialisable form for the call journal (drops the raw payload)."""
        return {
            "text": self.text,
            "tool_calls": [
                {"name": c.name, "arguments": c.arguments, "id": c.id}
                for c in self.tool_calls
            ],
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "model": self.model,
            "provider": self.provider,
            "finish_reason": self.finish_reason,
        }

    @classmethod
    def from_journal(cls, payload: Any) -> "LLMResponse":
        if not isinstance(payload, dict):
            return cls(text="", provider="journal", finish_reason="error")
        return cls(
            text=str(payload.get("text") or ""),
            tool_calls=[
                ToolCall(
                    name=str(c.get("name") or ""),
                    arguments=c.get("arguments") if isinstance(c.get("arguments"), dict) else {},
                    id=str(c.get("id") or ""),
                )
                for c in payload.get("tool_calls") or []
                if isinstance(c, dict)
            ],
            prompt_tokens=int(payload.get("prompt_tokens") or 0),
            completion_tokens=int(payload.get("completion_tokens") or 0),
            model=str(payload.get("model") or ""),
            provider=str(payload.get("provider") or "journal"),
            finish_reason=str(payload.get("finish_reason") or ""),
        )


class LLMClient(Protocol):
    """Minimal chat interface every provider adapter implements."""

    name: str
    model: str
    available: bool

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        response_format_json: bool = False,
    ) -> LLMResponse:
        ...


class NullLLMClient:
    """Default client: no model configured, so the harness stays deterministic.

    Returning an empty response (rather than raising) is what makes the whole
    system degrade to the rule-based path when no provider is configured.
    """

    name = "null"
    model = "none"
    available = False

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        response_format_json: bool = False,
    ) -> LLMResponse:
        return LLMResponse(text="", provider="null", model="none", finish_reason="stop")


#: Trailing comma before a closing brace/bracket — the single most common model
#: JSON slip, and strictly syntactic: no content is ambiguous, so repairing it
#: changes nothing about what the model said.
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def extract_json(text: str, default: Any = None) -> Any:
    """Parse JSON from a model reply, tolerating the ways models really answer.

    Handled: code fences (with/without a language tag), prose before/after the
    object, trailing commas, Python-dict-literal quoting. Not handled, on
    purpose: anything requiring a guess about meaning.
    """
    if not text:
        return default
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        candidate = candidate.split("\n", 1)[1] if "\n" in candidate else candidate
        if candidate.lstrip().startswith("json"):
            candidate = candidate.lstrip()[4:]

    for attempt in _json_candidates(candidate):
        parsed = _loads_tolerant(attempt)
        if parsed is not None:
            return parsed
    return default


def _json_candidates(candidate: str) -> list[str]:
    """The whole string, then the widest brace- and bracket-delimited spans."""
    spans = [candidate]
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = candidate.find(opener), candidate.rfind(closer)
        if 0 <= start < end:
            spans.append(candidate[start : end + 1])
    return spans


def _loads_tolerant(text: str) -> Any:
    """Strict JSON, then trailing-comma repair, then a Python literal.

    ``ast.literal_eval`` only parses literals and evaluates nothing, so it
    cannot execute a model response; ``json.loads`` is always tried first, so
    well-formed JSON never takes that path.
    """
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    repaired = _TRAILING_COMMA_RE.sub(r"\1", text)
    if repaired != text:
        try:
            return json.loads(repaired)
        except (ValueError, TypeError):
            pass
    try:
        value = ast.literal_eval(repaired)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return None
    return value if isinstance(value, (dict, list)) else None
