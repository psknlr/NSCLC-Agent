"""MCP client (Model Context Protocol, Streamable HTTP transport).

Mainstream agent CLIs extend their toolbox with MCP servers; so does
NSCLC-Agent: an institution can expose its own tools (a formulary, a
trial-matching service, a PACS/EMR gateway…) and the agent discovers and
calls them next to the built-in clinical tools. Tools appear as
``mcp__<server>__<tool>``.

JSON-RPC 2.0 over HTTP POST; responses may be ``application/json`` or an
SSE stream. Natively the transport is urllib; in the browser build it is a
synchronous XHR from the worker, so the server must allow CORS (and expose
``Mcp-Session-Id`` if it uses sessions).
"""

from __future__ import annotations

import json
import re
from typing import Any

from .tools import Tool

PROTOCOL_VERSION = "2025-06-18"


class MCPError(RuntimeError):
    pass


def _http_post(url: str, headers: dict[str, str], body: bytes,
               timeout: float) -> tuple[int, dict[str, str], str]:
    from ..platform_caps import IN_BROWSER

    if IN_BROWSER:
        from js import XMLHttpRequest  # type: ignore[import-not-found]

        xhr = XMLHttpRequest.new()
        xhr.open("POST", url, False)
        for key, value in headers.items():
            xhr.setRequestHeader(key, value)
        try:
            xhr.send(body.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise MCPError(f"request failed ({exc}) — network or CORS") from exc
        if int(xhr.status) == 0:
            raise MCPError("request blocked (status 0) — network or CORS")
        out = {}
        for name in ("content-type", "mcp-session-id"):
            value = xhr.getResponseHeader(name)
            if value:
                out[name] = str(value)
        return int(xhr.status), out, str(xhr.responseText)

    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return (resp.status, {k.lower(): v for k, v in resp.headers.items()},
                    resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        return (exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()},
                exc.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MCPError(f"connection failed: {exc}") from exc


def _parse_body(content_type: str, text: str) -> list[dict[str, Any]]:
    if "text/event-stream" in (content_type or ""):
        messages, data = [], []
        for line in text.splitlines() + [""]:
            if line.startswith("data:"):
                data.append(line[5:].strip())
            elif not line.strip() and data:
                try:
                    messages.append(json.loads("\n".join(data)))
                except json.JSONDecodeError:
                    pass
                data = []
        return messages
    if not text.strip():
        return []
    parsed = json.loads(text)
    return parsed if isinstance(parsed, list) else [parsed]


class MCPClient:
    def __init__(self, name: str, url: str, *, headers: dict[str, str] | None = None,
                 timeout: float = 30.0) -> None:
        self.name = re.sub(r"[^a-zA-Z0-9_-]", "_", str(name or "server"))[:24] or "server"
        self.url = str(url)
        self.headers = {str(k): str(v) for k, v in (headers or {}).items()}
        self.timeout = timeout
        self.session_id: str | None = None
        self.server_info: dict[str, Any] = {}
        self._id = 0

    def _rpc(self, method: str, params: dict[str, Any] | None = None, *,
             notify: bool = False) -> dict[str, Any]:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notify:
            self._id += 1
            message["id"] = self._id
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": PROTOCOL_VERSION, **self.headers}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        status, resp_headers, text = _http_post(
            self.url, headers, json.dumps(message).encode("utf-8"), self.timeout)
        if resp_headers.get("mcp-session-id"):
            self.session_id = resp_headers["mcp-session-id"]
        if notify:
            if status >= 400:
                raise MCPError(f"{method}: HTTP {status}")
            return {}
        if status >= 400:
            raise MCPError(f"{method}: HTTP {status} {text[:200]}")
        for reply in _parse_body(resp_headers.get("content-type", ""), text):
            if reply.get("id") == message["id"]:
                if "error" in reply:
                    error = reply["error"] or {}
                    raise MCPError(f"{method}: {error.get('message') or error}")
                return reply.get("result") or {}
        raise MCPError(f"{method}: no response for request {message['id']}")

    def initialize(self) -> dict[str, Any]:
        from .. import __version__

        result = self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "nsclc-agent", "version": __version__}})
        self.server_info = result.get("serverInfo") or {}
        try:
            self._rpc("notifications/initialized", notify=True)
        except MCPError:
            pass  # some servers do not acknowledge notifications
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor = None
        for _ in range(10):
            result = self._rpc("tools/list", {"cursor": cursor} if cursor else {})
            tools += [t for t in result.get("tools") or [] if isinstance(t, dict)]
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        texts = [c.get("text", "") for c in result.get("content") or []
                 if isinstance(c, dict) and c.get("type") == "text"]
        data: dict[str, Any] = {"content": "\n".join(texts)}
        if result.get("structuredContent") is not None:
            data["structured"] = result["structuredContent"]
        error = bool(result.get("isError"))
        return {"ok": not error,
                "summary": (("error: " if error else "") + data["content"][:140]) or "done",
                "data": data}

    def as_tools(self) -> list[Tool]:
        out = []
        for spec in self.list_tools():
            remote = str(spec.get("name") or "")
            if not remote:
                continue
            local = f"mcp__{self.name}__{re.sub(r'[^a-zA-Z0-9_-]', '_', remote)}"[:64]
            schema = spec.get("inputSchema") or {"type": "object", "properties": {}}

            def handler(_remote: str = remote, **arguments: Any) -> dict[str, Any]:
                try:
                    return self.call_tool(_remote, arguments)
                except MCPError as exc:
                    return {"ok": False, "summary": str(exc), "data": {"error": str(exc)}}

            out.append(Tool(local, f"[MCP · {self.name}] "
                            + str(spec.get("description") or remote)[:900],
                            schema, handler=handler, category="mcp",
                            parallel_safe=False,
                            label=f"{self.name} · {spec.get('title') or remote}"))
        return out


def connect(servers: list[dict[str, Any]], *, timeout: float = 20.0
            ) -> tuple[list[Tool], list[dict[str, Any]]]:
    """Connect to every enabled server; failures become status rows."""
    tools: list[Tool] = []
    status: list[dict[str, Any]] = []
    for config in servers or []:
        if not isinstance(config, dict) or config.get("enabled") is False:
            continue
        client = MCPClient(config.get("name") or "server", config.get("url") or "",
                           headers=config.get("headers") or {}, timeout=timeout)
        try:
            client.initialize()
            found = client.as_tools()
            tools += found
            status.append({"name": client.name, "ok": True, "tools": [t.name for t in found],
                           "server": client.server_info})
        except (MCPError, ValueError, json.JSONDecodeError) as exc:
            status.append({"name": client.name, "ok": False, "error": str(exc)})
    return tools, status
