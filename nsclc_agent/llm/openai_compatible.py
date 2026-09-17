"""OpenAI-compatible chat transport on the standard library.

Azure OpenAI, Poe and MiniMax are all OpenAI-shaped; this base implements the
shared request/parse logic — including **tool calling** and **finish_reason**
surfacing, the two capabilities whose absence defined v0.1 — plus bounded
retry with exponential backoff for transport-class failures (429/5xx/timeouts),
honoring ``SSL_CERT_FILE``/``REQUESTS_CA_BUNDLE`` for proxied environments.
"""

from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from typing import Any

from .base import LLMError, LLMResponse, ToolCall, ToolSpec

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if ca and os.path.isfile(ca):
        try:
            ctx.load_verify_locations(ca)
        except ssl.SSLError:  # pragma: no cover - defensive
            pass
    return ctx


class OpenAICompatibleClient:
    kind = "openai_compatible"

    def __init__(
        self,
        name: str,
        model: str,
        *,
        api_key: str,
        base_url: str,
        chat_path: str = "/chat/completions",
        timeout: float = 120.0,
        auth_scheme: str = "bearer",  # bearer | api-key | none
        extra_headers: dict[str, str] | None = None,
        send_model_in_body: bool = True,
        supports_vision: bool = False,
        max_retries: int = 3,
    ) -> None:
        if not api_key and auth_scheme != "none":
            raise LLMError(
                f"provider {name!r} has no API key — set the configured "
                f"environment variable"
            )
        self.name = name
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.chat_path = chat_path
        self.timeout = timeout
        self.auth_scheme = auth_scheme
        self.extra_headers = extra_headers or {}
        self.send_model_in_body = send_model_in_body
        self.supports_vision = supports_vision
        self.max_retries = max(0, int(max_retries))
        self.available = True
        self._ctx = _ssl_context()

    # ------------------------------------------------------------------ hooks
    def _endpoint(self) -> str:
        return f"{self.base_url}{self.chat_path}"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.auth_scheme == "bearer":
            headers["Authorization"] = f"Bearer {self.api_key}"
        elif self.auth_scheme == "api-key":
            headers["api-key"] = self.api_key
        headers.update(self.extra_headers)
        return headers

    def _payload(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec] | None,
        temperature: float,
        max_tokens: int,
        response_format_json: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.send_model_in_body:
            body["model"] = self.model
        if tools:
            body["tools"] = [t.to_openai() for t in tools]
        if response_format_json:
            body["response_format"] = {"type": "json_object"}
        return body

    # -------------------------------------------------------------- transport
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        response_format_json: bool = False,
    ) -> LLMResponse:
        payload = json.dumps(self._payload(
            messages, tools, temperature, max_tokens, response_format_json,
        )).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(
                self._endpoint(), data=payload, headers=self._headers(),
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout, context=self._ctx
                ) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                return self._parse(data)
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")[:600]
                last_error = LLMError(
                    f"{self.name}: HTTP {exc.code} from {self._endpoint()}: {body}")
                if exc.code not in _RETRYABLE_STATUS:
                    raise last_error from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = LLMError(
                    f"{self.name}: connection error to {self._endpoint()}: {exc}")
            except json.JSONDecodeError as exc:
                raise LLMError(
                    f"{self.name}: non-JSON response from {self._endpoint()}") from exc
            if attempt < self.max_retries:
                time.sleep(min(2.0 ** attempt, 8.0))
        raise last_error or LLMError(f"{self.name}: request failed")

    def _parse(self, data: dict[str, Any]) -> LLMResponse:
        try:
            choice = data["choices"][0]
            message = choice.get("message") or {}
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(
                f"{self.name}: unexpected response shape: "
                f"{json.dumps(data)[:400]}"
            ) from exc
        content = message.get("content")
        if isinstance(content, list):  # some APIs return content parts
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict))
        tool_calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            function = (raw or {}).get("function") or {}
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            tool_calls.append(ToolCall(
                name=str(function.get("name") or ""),
                arguments=arguments if isinstance(arguments, dict) else {},
                id=str(raw.get("id") or ""),
            ))
        usage = data.get("usage") or {}
        return LLMResponse(
            text=content or "",
            tool_calls=tool_calls,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            model=str(data.get("model") or self.model),
            provider=self.name,
            finish_reason=str(choice.get("finish_reason") or ""),
            raw=data,
        )
