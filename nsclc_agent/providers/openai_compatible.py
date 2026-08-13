"""Base class for OpenAI-compatible chat/completions backends.

Poe and MiniMax both expose OpenAI-shaped chat endpoints, and Azure OpenAI is
OpenAI-shaped with the deployment carried in the URL. This base implements the
shared HTTP request/parse logic on the Python standard library so the common
backends need no third-party dependency. Subclasses customize the endpoint
URL, auth headers and (rarely) the request payload.
"""

from __future__ import annotations

import json
import ssl
import os
import random
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from .base import (
    GenerationParams,
    LLMProvider,
    LLMResponse,
    Message,
    ProviderError,
)


def _ssl_context() -> ssl.SSLContext:
    """Build an SSL context honoring SSL_CERT_FILE / REQUESTS_CA_BUNDLE.

    This lets the client work behind corporate / agent proxies that present a
    custom CA bundle without disabling verification.
    """
    ctx = ssl.create_default_context()
    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if ca and os.path.isfile(ca):
        try:
            ctx.load_verify_locations(ca)
        except ssl.SSLError:  # pragma: no cover - defensive
            pass
    return ctx


#: HTTP statuses worth retrying: rate limiting and transient server errors.
_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})

#: Reasoning-tier models (o-series, GPT-5-class) reject ``max_tokens`` and want
#: ``max_completion_tokens``. We start with whichever the config asks for and
#: switch once, automatically, when the API says so.
_TOKEN_FIELDS = ("max_tokens", "max_completion_tokens")


class OpenAICompatibleProvider(LLMProvider):
    kind = "openai_compatible"

    def __init__(
        self,
        name: str,
        model: str,
        params: GenerationParams,
        *,
        api_key: str,
        base_url: str,
        chat_path: str = "/chat/completions",
        timeout: float = 120.0,
        auth_scheme: str = "bearer",  # "bearer" | "api-key" | "none"
        extra_headers: Optional[dict[str, str]] = None,
        send_model_in_body: bool = True,
        supports_vision: bool = False,
        max_retries: int = 3,
        retry_backoff: float = 1.0,
        max_tokens_field: str = "max_tokens",
    ):
        super().__init__(name, model, params)
        if not api_key and auth_scheme != "none":
            raise ProviderError(
                f"Provider {name!r} has no API key. Set the configured "
                f"api_key_env environment variable."
            )
        if max_tokens_field not in _TOKEN_FIELDS:
            raise ProviderError(
                f"Provider {name!r}: max_tokens_field must be one of "
                f"{', '.join(_TOKEN_FIELDS)}, got {max_tokens_field!r}"
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.chat_path = chat_path
        self.timeout = timeout
        self.auth_scheme = auth_scheme
        self.extra_headers = extra_headers or {}
        self.send_model_in_body = send_model_in_body
        self.supports_vision = supports_vision
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff = float(retry_backoff)
        self.max_tokens_field = max_tokens_field
        self._ctx = _ssl_context()

    # -- hooks subclasses may override --------------------------------------

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

    def _payload(self, messages: list[Message], p: GenerationParams) -> dict:
        body: dict[str, Any] = {
            "messages": [m.to_openai() for m in messages],
            "temperature": p.temperature,
            self.max_tokens_field: p.max_tokens,
        }
        if self.send_model_in_body:
            body["model"] = self.model
        if p.top_p is not None:
            body["top_p"] = p.top_p
        body.update(p.extra)
        return body

    def _parse(self, data: dict) -> LLMResponse:
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
            finish = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                f"Unexpected response shape from {self.name}: "
                f"{json.dumps(data)[:500]}"
            ) from exc
        if isinstance(content, list):  # some APIs return content parts
            content = "".join(
                part.get("text", "") for part in content
                if isinstance(part, dict)
            )
        return LLMResponse(
            content=content or "",
            provider=self.name,
            model=data.get("model", self.model),
            usage=data.get("usage", {}) or {},
            finish_reason=finish,
            raw=data,
        )

    # -- transport ----------------------------------------------------------

    def _sleep(self, seconds: float) -> None:  # pragma: no cover - timing
        time.sleep(seconds)

    def _retry_delay(self, attempt: int, retry_after: Optional[str]) -> float:
        """Honor ``Retry-After`` when present, else exponential backoff."""
        if retry_after:
            try:
                return min(60.0, max(0.0, float(retry_after)))
            except ValueError:
                pass
        return self.retry_backoff * (2 ** attempt) + random.uniform(0, 0.25)

    def _post(self, body: dict) -> dict:
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self._endpoint(), data=payload, headers=self._headers(),
            method="POST",
        )
        with urllib.request.urlopen(
            req, timeout=self.timeout, context=self._ctx
        ) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def complete(
        self, messages: list[Message], *, params: Optional[GenerationParams] = None
    ) -> LLMResponse:
        p = params or self.params
        body = self._payload(messages, p)
        token_field_switched = False
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                return self._parse(self._post(body))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                # A model that wants max_completion_tokens instead of
                # max_tokens: switch the field once and retry immediately
                # rather than failing a whole batch on a naming difference.
                other = _other_token_field(self.max_tokens_field)
                if (exc.code == 400 and not token_field_switched
                        and other in detail):
                    body[other] = body.pop(self.max_tokens_field, p.max_tokens)
                    self.max_tokens_field = other
                    token_field_switched = True
                    continue
                last_error = ProviderError(
                    f"{self.name}: HTTP {exc.code} from {self._endpoint()}: "
                    f"{detail[:800]}"
                )
                if exc.code not in _RETRYABLE_STATUS:
                    raise last_error from exc
                retry_after = exc.headers.get("Retry-After") if exc.headers \
                    else None
            except urllib.error.URLError as exc:
                last_error = ProviderError(
                    f"{self.name}: connection error to {self._endpoint()}: "
                    f"{exc.reason}"
                )
                retry_after = None
            except json.JSONDecodeError as exc:
                raise ProviderError(
                    f"{self.name}: non-JSON response from {self._endpoint()}"
                ) from exc

            if attempt >= self.max_retries:
                break
            self._sleep(self._retry_delay(attempt, retry_after))

        assert last_error is not None
        raise ProviderError(
            f"{last_error} (gave up after {self.max_retries + 1} attempt(s))"
        )


def _other_token_field(current: str) -> str:
    return _TOKEN_FIELDS[1] if current == _TOKEN_FIELDS[0] else _TOKEN_FIELDS[0]
