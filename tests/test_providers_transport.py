"""Provider transport: Poe / MiniMax / Azure wire behavior without network.

Live verification (2026-09) against the real endpoints, recorded in the
README: Poe's public catalog lists ``claude-sonnet-4.5`` and
``gemini-3.1-pro`` (tools + image input); an invalid key yields Poe HTTP 401
and MiniMax in-band ``base_resp`` 1004; both answer browser CORS preflights.
These tests pin the client-side handling of each of those shapes.
"""

from __future__ import annotations

import io
import json
import sys
import types

import pytest

from nsclc_agent.llm import openai_compatible as oc
from nsclc_agent.llm.base import LLMError
from nsclc_agent.llm.providers import (
    AzureOpenAIClient,
    MiniMaxClient,
    PoeClient,
    ping_client,
    poe_model_check,
)

OK_BODY = json.dumps({
    "model": "m", "choices": [{"message": {"content": "ok"},
                               "finish_reason": "stop"}]})


class Scripted:
    """Replaces ``_post`` with a script of (status, body) or exceptions."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = 0

    def __call__(self, payload):
        self.calls += 1
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(oc.time, "sleep", lambda s: None)


def _client(cls=PoeClient, **kw):
    if cls is PoeClient:
        return PoeClient("poe", "claude-sonnet-4.5", api_key="k", **kw)
    if cls is MiniMaxClient:
        return MiniMaxClient("minimax", "MiniMax-M3", api_key="k",
                             base_url="https://api.minimax.io/v1", **kw)
    return AzureOpenAIClient("azure", "dep", api_key="k",
                             endpoint="https://r.openai.azure.com", **kw)


def test_poe_wire_shape():
    client = _client()
    assert client._endpoint() == "https://api.poe.com/v1/chat/completions"
    assert client._headers()["Authorization"] == "Bearer k"
    body = client._payload([{"role": "user", "content": "x"}], None, 0, 8, False)
    assert body["model"] == "claude-sonnet-4.5"


def test_azure_uses_api_key_header():
    headers = _client(AzureOpenAIClient)._headers()
    assert headers["api-key"] == "k" and "Authorization" not in headers


def test_success_parses(monkeypatch):
    client = _client()
    monkeypatch.setattr(client, "_post", Scripted((200, OK_BODY)))
    assert client.chat([{"role": "user", "content": "hi"}]).text == "ok"


def test_auth_error_is_not_retried(monkeypatch):
    client = _client()
    script = Scripted((401, '{"error": {"type": "authentication_error"}}'))
    monkeypatch.setattr(client, "_post", script)
    with pytest.raises(LLMError, match="HTTP 401"):
        client.chat([{"role": "user", "content": "hi"}])
    assert script.calls == 1


def test_rate_limit_and_transport_errors_retry(monkeypatch):
    client = _client()
    script = Scripted((429, "slow down"),
                      oc._TransportError("reset"), (200, OK_BODY))
    monkeypatch.setattr(client, "_post", script)
    assert client.chat([{"role": "user", "content": "hi"}]).text == "ok"
    assert script.calls == 3


def test_retries_are_bounded(monkeypatch):
    client = _client()
    client.max_retries = 2
    script = Scripted(*[(503, "down")] * 3)
    monkeypatch.setattr(client, "_post", script)
    with pytest.raises(LLMError, match="HTTP 503"):
        client.chat([{"role": "user", "content": "hi"}])
    assert script.calls == 3


def test_non_json_body_fails_loudly(monkeypatch):
    client = _client()
    monkeypatch.setattr(client, "_post", Scripted((200, "<html>")))
    with pytest.raises(LLMError, match="non-JSON"):
        client.chat([{"role": "user", "content": "hi"}])


def _minimax_error(code, msg):
    return json.dumps({"base_resp": {"status_code": code, "status_msg": msg}})


def test_minimax_in_band_auth_error_raises_without_retry(monkeypatch):
    client = _client(MiniMaxClient)
    script = Scripted((200, _minimax_error(1004, "login fail")))
    monkeypatch.setattr(client, "_post", script)
    with pytest.raises(LLMError, match="MiniMax error 1004: login fail"):
        client.chat([{"role": "user", "content": "hi"}])
    assert script.calls == 1


def test_minimax_in_band_rate_limit_retries(monkeypatch):
    client = _client(MiniMaxClient)
    script = Scripted((200, _minimax_error(1002, "rate limit")),
                      (200, OK_BODY))
    monkeypatch.setattr(client, "_post", script)
    assert client.chat([{"role": "user", "content": "hi"}]).text == "ok"
    assert script.calls == 2


def test_minimax_success_with_zero_base_resp(monkeypatch):
    client = _client(MiniMaxClient)
    body = json.loads(OK_BODY)
    body["base_resp"] = {"status_code": 0, "status_msg": "success"}
    monkeypatch.setattr(client, "_post", Scripted((200, json.dumps(body))))
    assert client.chat([{"role": "user", "content": "hi"}]).text == "ok"


def test_minimax_endpoint_and_group_id():
    client = MiniMaxClient("minimax", "MiniMax-M3", api_key="k",
                           base_url="https://api.minimaxi.com/v1",
                           group_id="g1")
    assert client._endpoint().startswith(
        "https://api.minimaxi.com/v1/text/chatcompletion_v2")
    assert client._endpoint().endswith("?GroupId=g1")


def test_ping_client_reports_errors(monkeypatch):
    client = _client()
    monkeypatch.setattr(client, "_post", Scripted((401, "bad key")))
    result = ping_client(client)
    assert result["ok"] is False and "401" in result["error"]
    monkeypatch.setattr(client, "_post", Scripted((200, OK_BODY)))
    assert ping_client(client) == {"ok": True, "reply": "ok", "model": "m"}


# ------------------------------------------------------------ Poe catalog

_CATALOG = {"data": [
    {"id": "claude-sonnet-4.5", "supported_features": ["tools"],
     "architecture": {"input_modalities": ["text", "image"]}},
    {"id": "gemini-3.1-pro", "supported_features": ["tools"],
     "architecture": {"input_modalities": ["text", "image"]}},
    {"id": "minimax-m3", "supported_features": [],
     "architecture": {"input_modalities": ["text"]}},
]}


def _fake_urlopen(payload):
    def opener(url, timeout=None):
        assert url.endswith("/models")
        return io.BytesIO(json.dumps(payload).encode("utf-8"))
    return opener


def test_poe_model_check_exact_and_case_suggestion(monkeypatch):
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(_CATALOG))
    exact = poe_model_check("claude-sonnet-4.5")
    assert exact["catalog_reachable"] and exact["exact"]
    assert exact["tools"] and exact["image_input"]
    wrong_case = poe_model_check("Claude-Sonnet-4.5")
    assert not wrong_case["exact"]
    assert wrong_case["suggestion"] == "claude-sonnet-4.5"
    text_only = poe_model_check("minimax-m3")
    assert text_only["exact"] and not text_only["image_input"]
    missing = poe_model_check("gpt-2")
    assert missing["catalog_reachable"] and not missing["exact"]
    assert missing["suggestion"] is None


def test_poe_model_check_offline_is_not_an_exception(monkeypatch):
    import urllib.request

    def boom(url, timeout=None):
        raise OSError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    result = poe_model_check("claude-sonnet-4.5")
    assert result["catalog_reachable"] is False and "offline" in result["error"]


# ------------------------------------------------------- browser transport

class _FakeXHR:
    status = 200
    responseText = OK_BODY
    raise_on_send = None
    last = None

    @classmethod
    def new(cls):
        inst = cls()
        inst.headers = {}
        _FakeXHR.last = inst
        return inst

    def open(self, method, url, async_):
        self.method, self.url, self.async_ = method, url, async_

    def setRequestHeader(self, key, value):
        self.headers[key] = value

    def send(self, body=None):
        if self.raise_on_send:
            raise self.raise_on_send
        self.body = body


@pytest.fixture
def fake_js(monkeypatch):
    module = types.ModuleType("js")
    module.XMLHttpRequest = _FakeXHR
    monkeypatch.setitem(sys.modules, "js", module)
    _FakeXHR.status, _FakeXHR.responseText = 200, OK_BODY
    _FakeXHR.raise_on_send = None
    return _FakeXHR


def test_browser_post_is_synchronous_and_carries_headers(fake_js):
    status, body = oc._browser_post(
        "https://api.poe.com/v1/chat/completions",
        {"Authorization": "Bearer k"}, b'{"x": 1}', 30)
    xhr = fake_js.last
    assert (status, body) == (200, OK_BODY)
    assert xhr.async_ is False and xhr.method == "POST"
    assert xhr.headers == {"Authorization": "Bearer k"}
    assert xhr.body == '{"x": 1}'


def test_browser_post_status_zero_is_a_transport_error(fake_js):
    fake_js.status = 0
    with pytest.raises(oc._TransportError, match="CORS"):
        oc._browser_post("https://x", {}, b"{}", 5)
    fake_js.status = 200
    fake_js.raise_on_send = RuntimeError("NetworkError")
    with pytest.raises(oc._TransportError, match="NetworkError"):
        oc._browser_post("https://x", {}, b"{}", 5)


def test_client_uses_browser_transport_in_browser(monkeypatch, fake_js):
    import nsclc_agent.platform_caps as caps

    monkeypatch.setattr(caps, "IN_BROWSER", True)
    assert oc._ssl_context() is None
    client = _client(MiniMaxClient)
    fake_js.responseText = _minimax_error(1004, "login fail")
    with pytest.raises(LLMError, match="1004"):
        client.chat([{"role": "user", "content": "hi"}])
    assert fake_js.last.url.endswith("/text/chatcompletion_v2")


def test_poe_catalog_check_uses_xhr_in_browser(monkeypatch, fake_js):
    import nsclc_agent.platform_caps as caps

    monkeypatch.setattr(caps, "IN_BROWSER", True)
    fake_js.responseText = json.dumps(_CATALOG)
    result = poe_model_check("gemini-3.1-pro")
    assert result["exact"] and result["image_input"]
    assert fake_js.last.method == "GET" and fake_js.last.async_ is False
