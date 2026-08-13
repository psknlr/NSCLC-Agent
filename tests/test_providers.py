"""Tests for the provider layer and factory."""

import json

import pytest

from nsclc_agent.providers import build_provider, GenerationParams, Message, ProviderError
from nsclc_agent.providers.mock import MockProvider
from nsclc_agent.providers.azure import AzureOpenAIProvider
from nsclc_agent.providers.poe import PoeProvider
from nsclc_agent.providers.minimax import MiniMaxProvider


def test_mock_provider_returns_json():
    prov = MockProvider()
    resp = prov.complete([
        Message("system", "STAGE IIIB MODULE"),
        Message("user", "hello"),
    ])
    data = json.loads(resp.content)
    assert data["_mock"] is True
    assert resp.provider == "mock"


def test_build_mock_from_config():
    prov = build_provider("mock", {"type": "mock"})
    assert prov.kind == "mock"


def test_build_unknown_type_raises():
    with pytest.raises(ProviderError):
        build_provider("x", {"type": "does-not-exist"})


def test_build_missing_type_raises():
    with pytest.raises(ProviderError):
        build_provider("x", {})


def test_azure_url_construction():
    prov = build_provider("azure", {
        "type": "azure",
        "endpoint": "https://r.openai.azure.com/",
        "deployment": "gpt-4o",
        "api_version": "2024-10-21",
        "api_key": "secret",
    })
    assert isinstance(prov, AzureOpenAIProvider)
    url = prov._endpoint()
    assert url == (
        "https://r.openai.azure.com/openai/deployments/gpt-4o/"
        "chat/completions?api-version=2024-10-21"
    )
    headers = prov._headers()
    assert headers["api-key"] == "secret"
    # model must NOT be in the body for Azure
    body = prov._payload([Message("user", "hi")], prov.params)
    assert "model" not in body


def test_poe_defaults_and_bearer():
    prov = build_provider("poe", {
        "type": "poe", "model": "GPT-4o", "api_key": "k",
    })
    assert isinstance(prov, PoeProvider)
    assert prov._endpoint() == "https://api.poe.com/v1/chat/completions"
    assert prov._headers()["Authorization"] == "Bearer k"
    assert prov._payload([Message("user", "hi")], prov.params)["model"] == "GPT-4o"


def test_minimax_group_id_in_url():
    prov = build_provider("minimax", {
        "type": "minimax", "model": "MiniMax-Text-01",
        "api_key": "k", "group_id": "g123",
    })
    assert isinstance(prov, MiniMaxProvider)
    assert "GroupId=g123" in prov._endpoint()
    assert prov._endpoint().endswith("/text/chatcompletion_v2?GroupId=g123")


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("MY_POE_KEY", "from-env")
    prov = build_provider("poe", {
        "type": "poe", "model": "GPT-4o", "api_key_env": "MY_POE_KEY",
    })
    assert prov.api_key == "from-env"


def test_missing_key_raises():
    with pytest.raises(ProviderError):
        build_provider("poe", {"type": "poe", "model": "GPT-4o",
                               "api_key_env": "DEFINITELY_UNSET_VAR_XYZ"})


def test_openai_compatible_parse_content_parts():
    prov = MiniMaxProvider("m", "MiniMax-Text-01", GenerationParams(),
                           api_key="k")
    data = {
        "model": "MiniMax-Text-01",
        "choices": [{"message": {"content": [{"text": "a"}, {"text": "b"}]},
                     "finish_reason": "stop"}],
        "usage": {"total_tokens": 3},
    }
    resp = prov._parse(data)
    assert resp.content == "ab"
    assert resp.usage["total_tokens"] == 3


# --- generation-parameter merging -----------------------------------------

def test_extra_is_merged_not_replaced():
    """A per-provider `extra` block must extend the global one."""
    from nsclc_agent.providers.base import GenerationParams
    from nsclc_agent.providers.registry import _gen_params
    defaults = GenerationParams(temperature=0.2, max_tokens=100,
                                extra={"seed": 7, "user": "global"})
    p = _gen_params({"generation": {"extra": {"user": "local"}}}, defaults)
    assert p.extra == {"seed": 7, "user": "local"}
    assert p.temperature == 0.2 and p.max_tokens == 100


def test_merged_ignores_none_overrides():
    from nsclc_agent.providers.base import GenerationParams
    p = GenerationParams(temperature=0.5, max_tokens=10)
    assert p.merged(temperature=None).temperature == 0.5
    assert p.merged(temperature=0.0).temperature == 0.0


# --- transport options ------------------------------------------------------

def test_max_tokens_field_is_configurable():
    from nsclc_agent.providers.base import GenerationParams, Message
    from nsclc_agent.providers.registry import build_provider
    prov = build_provider("p", {
        "type": "poe", "model": "m", "api_key": "k",
        "max_tokens_field": "max_completion_tokens",
    }, defaults=GenerationParams(max_tokens=123))
    body = prov._payload([Message("user", "hi")], prov.params)
    assert body["max_completion_tokens"] == 123
    assert "max_tokens" not in body


def test_invalid_max_tokens_field_rejected():
    from nsclc_agent.providers.base import GenerationParams, ProviderError
    from nsclc_agent.providers.registry import build_provider
    with pytest.raises(ProviderError):
        build_provider("p", {"type": "poe", "model": "m", "api_key": "k",
                             "max_tokens_field": "nope"},
                       defaults=GenerationParams())


def test_retry_options_reach_the_provider():
    from nsclc_agent.providers.base import GenerationParams
    from nsclc_agent.providers.registry import build_provider
    prov = build_provider("p", {"type": "poe", "model": "m", "api_key": "k",
                                "max_retries": 5, "retry_backoff": 0.5},
                          defaults=GenerationParams())
    assert prov.max_retries == 5
    assert prov.retry_backoff == 0.5


def test_rate_limit_is_retried_then_succeeds(monkeypatch):
    """A 429 must be retried with backoff instead of failing the case."""
    import urllib.error
    from nsclc_agent.providers.base import GenerationParams, Message
    from nsclc_agent.providers.registry import build_provider

    prov = build_provider("p", {"type": "poe", "model": "m", "api_key": "k",
                                "max_retries": 2, "retry_backoff": 0.0},
                          defaults=GenerationParams())
    slept = []
    monkeypatch.setattr(prov, "_sleep", slept.append)
    calls = {"n": 0}

    def fake_post(body):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(
                "u", 429, "Too Many Requests", {"Retry-After": "0"}, None)
        return {"choices": [{"message": {"content": "ok"},
                             "finish_reason": "stop"}], "model": "m"}

    monkeypatch.setattr(prov, "_post", fake_post)
    assert prov.complete([Message("user", "hi")]).content == "ok"
    assert calls["n"] == 2 and len(slept) == 1


def test_non_retryable_status_fails_immediately(monkeypatch):
    import urllib.error
    from nsclc_agent.providers.base import GenerationParams, Message, ProviderError
    from nsclc_agent.providers.registry import build_provider

    prov = build_provider("p", {"type": "poe", "model": "m", "api_key": "k",
                                "max_retries": 3, "retry_backoff": 0.0},
                          defaults=GenerationParams())
    calls = {"n": 0}

    def fake_post(body):
        calls["n"] += 1
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(prov, "_post", fake_post)
    monkeypatch.setattr(prov, "_sleep", lambda s: None)
    with pytest.raises(ProviderError):
        prov.complete([Message("user", "hi")])
    assert calls["n"] == 1


def test_max_tokens_field_switches_on_400(monkeypatch):
    """A model that wants max_completion_tokens gets it without a config change."""
    import urllib.error
    from nsclc_agent.providers.base import GenerationParams, Message
    from nsclc_agent.providers.registry import build_provider

    prov = build_provider("p", {"type": "poe", "model": "m", "api_key": "k"},
                          defaults=GenerationParams(max_tokens=64))
    seen = []

    class _Body:
        def read(self):
            return (b"Unsupported parameter: 'max_tokens' is not supported "
                    b"with this model. Use 'max_completion_tokens' instead.")

    def fake_post(body):
        seen.append(dict(body))
        if "max_tokens" in body:
            err = urllib.error.HTTPError("u", 400, "Bad Request", {}, None)
            err.read = _Body().read
            raise err
        return {"choices": [{"message": {"content": "ok"},
                             "finish_reason": "stop"}], "model": "m"}

    monkeypatch.setattr(prov, "_post", fake_post)
    monkeypatch.setattr(prov, "_sleep", lambda s: None)
    assert prov.complete([Message("user", "hi")]).content == "ok"
    assert seen[0]["max_tokens"] == 64
    assert seen[1]["max_completion_tokens"] == 64
    assert prov.max_tokens_field == "max_completion_tokens"
