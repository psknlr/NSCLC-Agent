"""Concrete provider adapters: Azure OpenAI, Poe, MiniMax, LiteLLM.

All four share the OpenAI wire shape. Azure carries the deployment in the URL
with an ``api-key`` header; MiniMax has region-split endpoints; Poe bots are
addressed by bot name; LiteLLM (optional dependency) unifies 100+ providers.
"""

from __future__ import annotations

import os
from typing import Any

from .base import LLMError, LLMResponse, ToolCall, ToolSpec
from .openai_compatible import OpenAICompatibleClient

AZURE_DEFAULT_API_VERSION = "2024-10-21"
POE_DEFAULT_BASE_URL = "https://api.poe.com/v1"
MINIMAX_CHINA_BASE_URL = "https://api.minimaxi.com/v1"
MINIMAX_GLOBAL_BASE_URL = "https://api.minimax.io/v1"
MINIMAX_DEPRECATED_HOSTS = ("api.minimax.chat",)


class AzureOpenAIClient(OpenAICompatibleClient):
    kind = "azure"

    def __init__(
        self,
        name: str,
        deployment: str,
        *,
        api_key: str,
        endpoint: str,
        api_version: str = AZURE_DEFAULT_API_VERSION,
        timeout: float = 120.0,
        supports_vision: bool = False,
    ) -> None:
        if not endpoint:
            raise LLMError(
                f"Azure provider {name!r} requires an endpoint "
                f"(https://<resource>.openai.azure.com)"
            )
        endpoint = endpoint.rstrip("/")
        super().__init__(
            name, deployment,
            api_key=api_key,
            base_url=f"{endpoint}/openai/deployments/{deployment}",
            chat_path=f"/chat/completions?api-version={api_version}",
            timeout=timeout,
            auth_scheme="api-key",
            send_model_in_body=False,
            supports_vision=supports_vision,
        )


class PoeClient(OpenAICompatibleClient):
    kind = "poe"

    def __init__(
        self,
        name: str,
        model: str,
        *,
        api_key: str,
        base_url: str = POE_DEFAULT_BASE_URL,
        timeout: float = 120.0,
        supports_vision: bool = False,
    ) -> None:
        super().__init__(
            name, model, api_key=api_key, base_url=base_url,
            timeout=timeout, auth_scheme="bearer",
            supports_vision=supports_vision,
        )


class MiniMaxClient(OpenAICompatibleClient):
    kind = "minimax"

    def __init__(
        self,
        name: str,
        model: str,
        *,
        api_key: str,
        base_url: str = MINIMAX_CHINA_BASE_URL,
        group_id: str | None = None,
        timeout: float = 120.0,
        supports_vision: bool = False,
    ) -> None:
        if any(host in base_url for host in MINIMAX_DEPRECATED_HOSTS):
            raise LLMError(
                f"MiniMax endpoint {base_url!r} is deprecated. Use "
                f"{MINIMAX_CHINA_BASE_URL} (China) or "
                f"{MINIMAX_GLOBAL_BASE_URL} (international)."
            )
        chat_path = "/text/chatcompletion_v2"
        if group_id:
            chat_path += f"?GroupId={group_id}"
        super().__init__(
            name, model, api_key=api_key, base_url=base_url,
            chat_path=chat_path, timeout=timeout, auth_scheme="bearer",
            supports_vision=supports_vision,
        )


class LiteLLMClient:
    """Optional SDK-backed adapter unifying 100+ providers by model string."""

    kind = "litellm"

    def __init__(
        self,
        name: str,
        model: str,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        supports_vision: bool = False,
    ) -> None:
        try:
            import litellm  # noqa: F401
        except ImportError as exc:
            raise LLMError(
                "the litellm provider requires `pip install litellm`"
            ) from exc
        self.name = name
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.supports_vision = supports_vision
        self.available = True

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        response_format_json: bool = False,
    ) -> LLMResponse:
        import json as _json

        import litellm

        kwargs: dict[str, Any] = {
            "model": self.model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if tools:
            kwargs["tools"] = [t.to_openai() for t in tools]
        if response_format_json:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            completion = litellm.completion(**kwargs)
        except Exception as exc:  # noqa: BLE001 - normalise to LLMError
            raise LLMError(f"litellm: {type(exc).__name__}: {exc}") from exc
        choice = completion.choices[0]
        message = choice.message
        tool_calls = []
        for raw in getattr(message, "tool_calls", None) or []:
            arguments = raw.function.arguments
            if isinstance(arguments, str):
                try:
                    arguments = _json.loads(arguments)
                except _json.JSONDecodeError:
                    arguments = {}
            tool_calls.append(ToolCall(
                name=raw.function.name,
                arguments=arguments if isinstance(arguments, dict) else {},
                id=getattr(raw, "id", "") or "",
            ))
        usage = getattr(completion, "usage", None)
        return LLMResponse(
            text=message.content or "",
            tool_calls=tool_calls,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            model=getattr(completion, "model", self.model) or self.model,
            provider=self.name,
            finish_reason=str(getattr(choice, "finish_reason", "") or ""),
        )


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def build_client(provider: str | None = None, *, model: str | None = None) -> Any:
    """Build a client from environment configuration.

    No provider configured → :class:`NullLLMClient` (fully deterministic run).
    A provider explicitly named but incompletely configured raises, so a typo
    cannot masquerade as "normal rule output".
    """
    from .base import NullLLMClient

    provider = (provider or _env("NSCLC_LLM_PROVIDER")).lower()
    if not provider:
        return NullLLMClient()
    if provider == "azure":
        key, endpoint = _env("AZURE_OPENAI_API_KEY"), _env("AZURE_OPENAI_ENDPOINT")
        deployment = model or _env("AZURE_OPENAI_DEPLOYMENT")
        if not (key and endpoint and deployment):
            raise LLMError(
                "azure requires AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT "
                "and AZURE_OPENAI_DEPLOYMENT"
            )
        return AzureOpenAIClient(
            "azure", deployment, api_key=key, endpoint=endpoint,
            api_version=_env("AZURE_OPENAI_API_VERSION") or AZURE_DEFAULT_API_VERSION,
            supports_vision=_env("NSCLC_LLM_VISION") == "1",
        )
    if provider == "poe":
        key = _env("POE_API_KEY")
        if not key:
            raise LLMError("poe requires POE_API_KEY")
        return PoeClient(
            "poe", model or _env("POE_MODEL") or "Claude-Sonnet-4.5",
            api_key=key, base_url=_env("POE_BASE_URL") or POE_DEFAULT_BASE_URL,
            supports_vision=_env("NSCLC_LLM_VISION") == "1",
        )
    if provider == "minimax":
        key = _env("MINIMAX_API_KEY")
        if not key:
            raise LLMError("minimax requires MINIMAX_API_KEY")
        region = (_env("MINIMAX_REGION") or "china").lower()
        base = _env("MINIMAX_BASE_URL") or (
            MINIMAX_GLOBAL_BASE_URL if region == "global" else MINIMAX_CHINA_BASE_URL)
        return MiniMaxClient(
            "minimax", model or _env("MINIMAX_MODEL") or "MiniMax-M3",
            api_key=key, base_url=base,
            group_id=_env("MINIMAX_GROUP_ID") or None,
        )
    if provider == "litellm":
        litellm_model = model or _env("LITELLM_MODEL")
        if not litellm_model:
            raise LLMError("litellm requires LITELLM_MODEL")
        return LiteLLMClient(
            "litellm", litellm_model,
            api_key=_env("LITELLM_API_KEY") or None,
            api_base=_env("LITELLM_BASE_URL") or None,
            supports_vision=_env("NSCLC_LLM_VISION") == "1",
        )
    if provider == "mock":
        from .mock import MockLLMClient

        return MockLLMClient()
    raise LLMError(
        f"unknown provider {provider!r}: expected azure|poe|minimax|litellm|mock"
    )


def build_vision_client(*, provider: str | None = None, model: str | None = None) -> Any:
    """Build the film-reading client from NSCLC_VISION_* configuration.

    Returns None when nothing is configured — the perception agent then flags
    ``NO_VISION_PROVIDER`` and skips, rather than feeding images to a text model.
    """
    provider = (provider or _env("NSCLC_VISION_PROVIDER")).lower()
    if not provider:
        return None
    if provider == "mock":
        from .mock import MockLLMClient

        return MockLLMClient(vision=True)
    client = build_client(provider, model=model or _env("NSCLC_VISION_MODEL") or None)
    client.supports_vision = True
    return client


def describe_client(client: Any) -> dict[str, Any]:
    return {
        "provider": getattr(client, "name", "none"),
        "model": getattr(client, "model", "none"),
        "available": bool(getattr(client, "available", False)),
        "vision": bool(getattr(client, "supports_vision", False)),
    }
