"""Native Anthropic Messages API provider, on the official ``anthropic`` SDK.

Optional, like LiteLLM: the core stays dependency-free, and
``pip install "nsclc-agent[anthropic]"`` (or ``pip install anthropic``)
enables it. It runs natively only; the browser build has no SDK, so the
web page keeps its own providers.

What it adds over reaching Claude through an OpenAI-shaped gateway:

* **Reasoning carried across tool steps.** Claude's signed ``thinking``
  blocks ride on the assistant message (``_provider_content``) and are sent
  back verbatim, so the later steps of a consult keep the reasoning of the
  earlier ones. The session edits its history in a few places (an image
  becomes a placeholder after its turn, compaction, a resumed file), which
  invalidates the blocks after the edit; the request asks the API to drop
  those blocks instead of failing (``prefix_mismatch_behavior:
  "drop_block"``), and the response records how many it dropped.
* **Prompt caching.** The system prompt, the tool list and the history so
  far form one growing prefix, cached automatically at its last block, so
  each step of the loop re-reads the previous step's prefix at the cache
  price.
* **Adaptive thinking and effort.** Thinking is requested with summarized
  display (the trace shows the summary), and ``output_config.effort`` comes
  from ``ANTHROPIC_EFFORT``. Thinking counts toward ``max_tokens``, so every
  request gets at least ``min_output_tokens`` (16,000 by default) whatever a
  caller sized for a model that does not think.
* **Honest stops.** ``max_tokens`` is reported as truncation, and a tool
  call cut off mid-input is dropped rather than run. A ``refusal`` raises
  :class:`LLMError` naming its category; it is never passed off as an empty
  answer.

Sampling parameters are never sent: current models reject ``temperature``
(API version 2026-10-21 rejects it on every model), so the ``temperature``
argument of :meth:`AnthropicClient.chat` is accepted and ignored. Nor is a
tool call ever forced (``tool_choice`` stays ``auto``; newer models reject
``any``/``tool``); the agent loop already nudges a model that answers in
prose instead of calling its terminal tool.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .base import DECLINED, LLMError, LLMResponse, ToolCall, ToolSpec

DEFAULT_MODEL = "claude-opus-5-5"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
THINKING_MODES = ("adaptive", "omit")
#: Beta that lets a request set ``thinking.block_binding``.
THINKING_BINDING_BETA = "thinking-binding-controls-2026-08-01"
DEFAULT_MIN_OUTPUT_TOKENS = 16_000

#: Model families without adaptive thinking (they take a manual budget,
#: which this provider does not use), so thinking is not requested.
_NO_ADAPTIVE_RE = re.compile(
    r"claude-(?:3|2|instant|(?:opus|sonnet)-4-[0-5](?!\d)"
    r"|(?:opus|sonnet)-4(?:-\d{8})?$|haiku-[34])")
_DATA_URL_RE = re.compile(r"data:(image/(?:png|jpeg|gif|webp));base64,(.+)", re.S)
_STOP_TO_FINISH = {"end_turn": "stop", "stop_sequence": "stop",
                   "tool_use": "tool_calls", "max_tokens": "length",
                   "model_context_window_exceeded": "length"}
_BOUND_ELSEWHERE = "bound to a different conversation"


def supports_adaptive_thinking(model: str) -> bool:
    """False for the model families that predate adaptive thinking."""
    return not _NO_ADAPTIVE_RE.search(str(model or ""))


# ------------------------------------------------------------- conversion
def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text") or "") for part in content
                         if isinstance(part, dict) and part.get("type") == "text")
    return "" if content is None else json.dumps(content, ensure_ascii=False)


def _user_blocks(content: Any) -> list[dict[str, Any]]:
    """An OpenAI-shaped user content (text or parts) as Anthropic blocks."""
    if not isinstance(content, list):
        text = _text_of(content)
        return [{"type": "text", "text": text}] if text.strip() else []
    blocks: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and str(part.get("text") or "").strip():
            blocks.append({"type": "text", "text": str(part["text"])})
        elif part.get("type") == "image_url":
            url = str((part.get("image_url") or {}).get("url") or "")
            match = _DATA_URL_RE.match(url)
            if match:
                blocks.append({"type": "image", "source": {
                    "type": "base64", "media_type": match.group(1),
                    "data": match.group(2)}})
            elif url.startswith("https://"):
                blocks.append({"type": "image", "source": {"type": "url", "url": url}})
            else:
                blocks.append({"type": "text",
                               "text": "[an attached image in a format this model "
                                       "cannot read was left out]"})
    return blocks


def _tool_calls_of(message: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    calls = []
    for raw in message.get("tool_calls") or []:
        function = (raw or {}).get("function") or {}
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except ValueError:
                arguments = {}
        calls.append((str(raw.get("id") or ""), str(function.get("name") or ""),
                      arguments if isinstance(arguments, dict) else {}))
    return calls


def _native_blocks(message: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The provider's own blocks for this assistant message, if they still
    describe it: the same tool-call ids and the same visible text. Anything
    else (an edited message, a payload from another provider) falls back to
    the OpenAI-shaped fields."""
    native = message.get("_provider_content")
    if not (isinstance(native, dict) and native.get("provider") == "anthropic"
            and isinstance(native.get("content"), list)):
        return None
    blocks = [dict(b) for b in native["content"] if isinstance(b, dict) and b.get("type")]
    ids = [str(b.get("id")) for b in blocks if b.get("type") == "tool_use"]
    if ids != [cid for cid, _n, _a in _tool_calls_of(message)]:
        return None
    text = "".join(str(b.get("text") or "") for b in blocks if b.get("type") == "text")
    if text.strip() != _text_of(message.get("content")).strip():
        return None
    return blocks or None


def _assistant_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    native = _native_blocks(message)
    if native is not None:
        return native
    blocks: list[dict[str, Any]] = []
    text = _text_of(message.get("content"))
    if text.strip():
        blocks.append({"type": "text", "text": text})
    for cid, name, arguments in _tool_calls_of(message):
        blocks.append({"type": "tool_use", "id": cid, "name": name, "input": arguments})
    return blocks


def to_anthropic_messages(messages: list[dict[str, Any]]
                          ) -> tuple[str, list[dict[str, Any]]]:
    """(system prompt, Messages API history) from the OpenAI-shaped history
    the agent loop keeps.

    Leading system messages become the system prompt; a later one becomes a
    user note marked ``[system]`` (the harness does not write any). Tool
    messages become ``tool_result`` blocks at the head of the next user
    message. A tool call left without a result is answered "not executed",
    a result whose call is not in the history is dropped, and an empty
    assistant message is left out, so the history is always one the API
    accepts. The mapping depends on nothing but its input, so the same
    history always yields the same bytes (the prompt cache and the
    thinking-block binding both rely on that).
    """
    system: list[str] = []
    out: list[dict[str, Any]] = []
    pending: list[str] = []
    results: dict[str, dict[str, Any]] = {}
    notes: list[dict[str, Any]] = []

    def flush() -> None:
        content = [results.get(cid) or {
            "type": "tool_result", "tool_use_id": cid,
            "content": json.dumps({"status": "not executed"})} for cid in pending]
        content += notes
        if content:
            out.append({"role": "user", "content": content})
        pending.clear()
        results.clear()
        notes.clear()

    for message in messages:
        role = message.get("role")
        if role == "system" and not out and not notes and not pending:
            text = _text_of(message.get("content"))
            if text.strip():
                system.append(text)
        elif role == "system":
            text = _text_of(message.get("content"))
            if text.strip():
                notes.append({"type": "text", "text": f"[system] {text}"})
        elif role == "assistant":
            blocks = _assistant_blocks(message)
            if not blocks:
                continue
            flush()
            out.append({"role": "assistant", "content": blocks})
            pending.extend(str(b.get("id")) for b in blocks if b.get("type") == "tool_use")
        elif role == "tool":
            cid = str(message.get("tool_call_id") or "")
            if cid in pending and cid not in results:
                results[cid] = {"type": "tool_result", "tool_use_id": cid,
                                "content": _text_of(message.get("content")) or "{}"}
        else:
            notes.extend(_user_blocks(message.get("content")))
    flush()
    return "\n\n".join(system), out


def tool_definition(spec: ToolSpec) -> dict[str, Any]:
    schema = spec.parameters if isinstance(spec.parameters, dict) else {}
    return {"name": spec.name, "description": spec.description,
            "input_schema": {"type": "object", "properties": {}, **schema}}


def _strip_thinking(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The history without any thinking block (each turn keeps its text and
    tool calls): the documented one-time recovery when blocks were bound to
    a conversation the history no longer matches."""
    out: list[dict[str, Any]] = []
    for message in history:
        if message["role"] == "assistant":
            blocks = [b for b in message["content"]
                      if b.get("type") not in ("thinking", "redacted_thinking")]
            if not blocks:
                continue
            message = {**message, "content": blocks}
        if out and out[-1]["role"] == message["role"] == "user":
            out[-1] = {**out[-1], "content": out[-1]["content"] + message["content"]}
        else:
            out.append(message)
    return out


def _block_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return {k: v for k, v in block.items() if v is not None}
    to_dict = getattr(block, "to_dict", None)
    if callable(to_dict):
        return to_dict(mode="json", exclude_none=True)
    return {"type": getattr(block, "type", "unknown")}


def _count(usage: Any, name: str) -> int:
    value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# ----------------------------------------------------------------- client
class AnthropicClient:
    """The :class:`~nsclc_agent.llm.base.LLMClient` interface on the
    Messages API. ``client`` and ``api_errors`` exist for tests: any object
    with ``beta.messages.stream(**params)`` stands in for the SDK."""

    kind = "anthropic"

    def __init__(
        self,
        name: str = "anthropic",
        model: str = DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        effort: str | None = None,
        thinking: str = "adaptive",
        min_output_tokens: int | None = None,
        max_retries: int = 3,
        timeout: float | None = None,
        supports_vision: bool = True,
        client: Any = None,
        api_errors: tuple[type[BaseException], ...] | None = None,
    ) -> None:
        effort = (effort or "").strip().lower() or None
        if effort is not None and effort not in EFFORTS:
            raise LLMError(f"anthropic: effort {effort!r} is not one of {', '.join(EFFORTS)}")
        thinking = (thinking or "adaptive").strip().lower()
        if thinking not in THINKING_MODES:
            raise LLMError(f"anthropic: thinking {thinking!r} is not one of "
                           f"{', '.join(THINKING_MODES)}")
        if client is None:
            client, api_errors = self._sdk_client(api_key, base_url, max_retries, timeout)
        self._client = client
        self._api_errors = api_errors or ()
        self.name = name
        self.model = model
        self.effort = effort
        self.thinking = thinking if supports_adaptive_thinking(model) else "omit"
        self.min_output_tokens = (
            (DEFAULT_MIN_OUTPUT_TOKENS if self.thinking == "adaptive" else 0)
            if min_output_tokens is None else max(0, int(min_output_tokens)))
        self.supports_vision = supports_vision
        self.available = True

    @staticmethod
    def _sdk_client(api_key: str | None, base_url: str | None, max_retries: int,
                    timeout: float | None) -> tuple[Any, tuple[type[BaseException], ...]]:
        from ..platform_caps import IN_BROWSER

        if IN_BROWSER:
            raise LLMError("the anthropic provider needs the official SDK, which the "
                           "browser build does not have; use Poe or an "
                           "OpenAI-compatible gateway in the browser")
        try:
            import anthropic
        except ImportError as exc:
            raise LLMError("the anthropic provider requires the official SDK: "
                           "pip install anthropic") from exc
        kwargs: dict[str, Any] = {"max_retries": max(0, int(max_retries))}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        if timeout:
            kwargs["timeout"] = float(timeout)
        client = anthropic.Anthropic(**kwargs)
        if client.api_key is None and client.auth_token is None \
                and getattr(client, "credentials", None) is None:
            raise LLMError("anthropic: no credentials found; set ANTHROPIC_API_KEY "
                           "(or ANTHROPIC_AUTH_TOKEN, or sign in with `ant auth login`)")
        return client, (anthropic.AnthropicError,)

    @property
    def settings(self) -> dict[str, Any]:
        """What this client sends besides the conversation (for run records)."""
        return {"thinking": self.thinking, "effort": self.effort or "model default",
                "min_output_tokens": self.min_output_tokens,
                "prompt_caching": "automatic"}

    # ------------------------------------------------------------- request
    def request_params(self, messages: list[dict[str, Any]],
                       tools: list[ToolSpec] | None = None,
                       max_tokens: int = 1024) -> dict[str, Any]:
        """The Messages API request for this conversation (no I/O)."""
        system, history = to_anthropic_messages(messages)
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max(int(max_tokens or 0), self.min_output_tokens, 1),
            "messages": history,
            "cache_control": {"type": "ephemeral"},
        }
        if system:
            params["system"] = [{"type": "text", "text": system}]
        if tools:
            params["tools"] = [tool_definition(t) for t in tools]
        if self.thinking == "adaptive":
            params["thinking"] = {"type": "adaptive", "display": "summarized",
                                  "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
            params["betas"] = [THINKING_BINDING_BETA]
        if self.effort:
            params["output_config"] = {"effort": self.effort}
        return params

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        response_format_json: bool = False,
    ) -> LLMResponse:
        # ``temperature`` is not sent (see the module docstring), and the
        # callers that ask for JSON parse the reply liberally anyway.
        params = self.request_params(messages, tools, max_tokens)
        try:
            message = self._send(params)
        except self._api_errors as exc:
            if _BOUND_ELSEWHERE not in str(exc):
                raise LLMError(self._describe(exc)) from exc
            params["messages"] = _strip_thinking(params["messages"])
            try:
                message = self._send(params)
            except self._api_errors as retry_exc:
                raise LLMError(self._describe(retry_exc)) from retry_exc
        return self._parse(message)

    def _send(self, params: dict[str, Any]) -> Any:
        # Streamed even though only the final message is used: a long
        # thinking turn would otherwise run into the SDK's non-streaming
        # time limit. Tool inputs are not streamed eagerly, so the API still
        # validates each one before it reaches us.
        with self._client.beta.messages.stream(**params) as stream:
            return stream.get_final_message()

    def _describe(self, exc: BaseException) -> str:
        status = getattr(exc, "status_code", None)
        detail = getattr(exc, "message", None) or str(exc)
        where = f"HTTP {status}" if status else type(exc).__name__
        return f"{self.name}: {where}: {str(detail)[:600]}"

    # ------------------------------------------------------------ response
    def _parse(self, message: Any) -> LLMResponse:
        stop = str(getattr(message, "stop_reason", "") or "")
        if stop == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None)
            raise LLMError(f"{self.name}: {DECLINED} (stop_reason=refusal"
                           + (f", category={category})" if category else ")"))
        blocks = [_block_dict(b) for b in getattr(message, "content", None) or []]
        finish = _STOP_TO_FINISH.get(stop, stop)
        if finish == "length":
            # Never run a tool call whose input may have been cut off.
            blocks = [b for b in blocks if b.get("type") != "tool_use"]
        text = "".join(str(b.get("text") or "") for b in blocks if b.get("type") == "text")
        reasoning = "\n\n".join(str(b["thinking"]).strip() for b in blocks
                                if b.get("type") == "thinking" and str(b.get("thinking") or "").strip())
        calls = [ToolCall(str(b.get("name") or ""),
                          b.get("input") if isinstance(b.get("input"), dict) else {},
                          id=str(b.get("id") or ""))
                 for b in blocks if b.get("type") == "tool_use"]
        usage = getattr(message, "usage", None)
        read = _count(usage, "cache_read_input_tokens")
        write = _count(usage, "cache_creation_input_tokens")
        dropped = sum(1 for t in getattr(message, "input_transformations", None) or []
                      if (t.get("type") if isinstance(t, dict) else getattr(t, "type", "")
                          ) == "thinking_dropped")
        return LLMResponse(
            text=text,
            tool_calls=calls,
            prompt_tokens=_count(usage, "input_tokens") + read + write,
            completion_tokens=_count(usage, "output_tokens"),
            cache_read_tokens=read,
            cache_write_tokens=write,
            reasoning=reasoning,
            model=str(getattr(message, "model", "") or self.model),
            provider=self.name,
            finish_reason=finish,
            provider_content={"provider": "anthropic", "content": blocks} if blocks else None,
            raw={"id": str(getattr(message, "id", "") or ""), "stop_reason": stop,
                 "thinking_dropped": dropped},
        )
