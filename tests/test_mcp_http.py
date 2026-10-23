"""MCP Streamable HTTP：初始化、会话头、JSON/SSE 响应与错误分类。

不依赖真实网络：`httpx.MockTransport` 扮演 MCP server。
"""

from __future__ import annotations

import json

import httpx
import pytest

from workbench.services import mcp


def _cfg(**overrides) -> mcp.ServerConfig:
    base = {
        "name": "http_fake",
        "transport": "streamable-http",
        "url": "https://mcp.test/mcp",
    }
    base.update(overrides)
    return mcp.ServerConfig(**base)


def _install(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    real = httpx.Client
    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return real(transport=transport, **kwargs)

    monkeypatch.setattr(mcp.httpx, "Client", factory)


def test_config_accepts_url_without_command() -> None:
    cfg = mcp._parse_server("web", {"url": "https://mcp.test/mcp", "headers": {"X-Token": "t"}})
    assert cfg.transport == "streamable-http"
    assert cfg.url == "https://mcp.test/mcp"
    assert cfg.headers == {"X-Token": "t"}
    assert cfg.command == ""


def test_config_rejects_url_and_command_together() -> None:
    with pytest.raises(ValueError, match="不接受 command"):
        mcp._parse_server("bad", {"url": "https://mcp.test/mcp", "command": "npx"})


def test_config_rejects_relative_url() -> None:
    with pytest.raises(ValueError, match="http"):
        mcp._parse_server("bad", {"transport": "http", "url": "not-a-url"})


def test_config_rejects_url_userinfo() -> None:
    with pytest.raises(ValueError, match="用户名"):
        mcp._parse_server("bad", {"transport": "http", "url": "https://u:p@mcp.test/mcp"})


def test_initialize_sets_session_and_calls_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(request)
        method = payload["method"]
        if method == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": payload["id"],
                      "result": {"protocolVersion": "2025-06-18",
                                 "serverInfo": {"name": "fake", "version": "1"},
                                 "instructions": "先读文档再写。"}},
                headers={"mcp-session-id": "sess-1"},
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"],
                                             "result": {"tools": [{"name": "echo",
                                                                     "inputSchema": {"type": "object"}}]}})
        if method == "tools/call":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"],
                                             "result": {"content": [{"type": "text", "text": "hi"}]}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": {}})

    _install(monkeypatch, handler)
    client = mcp.McpClient(_cfg())
    client.start()
    try:
        assert client.connected
        assert client.instructions == "先读文档再写。"
        assert client.list_tools()[0]["name"] == "echo"
        assert client.call_tool("echo", {"text": "x"}) == "hi"
        # initialize 的响应头把会话固定下来；后续请求必须带上它。
        tool_request = next(r for r in seen if json.loads(r.content)["method"] == "tools/list")
        assert tool_request.headers["mcp-session-id"] == "sess-1"
        assert tool_request.headers["accept"] == "application/json, text/event-stream"
    finally:
        client.close()


def test_sse_response_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "initialize":
            body = {"jsonrpc": "2.0", "id": payload["id"], "result": {"protocolVersion": "2025-06-18"}}
            data = "\n".join(f"data: {line}" for line in json.dumps(body).splitlines())
            return httpx.Response(200, text=f"event: message\n{data}\n\n",
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(202)

    _install(monkeypatch, handler)
    client = mcp.McpClient(_cfg())
    client.start()
    try:
        assert client.protocol_version == "2025-06-18"
    finally:
        client.close()


def test_http_error_is_typed_as_network(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, lambda _request: httpx.Response(502, text="bad gateway"))
    client = mcp.McpClient(_cfg())
    with pytest.raises(mcp.McpError) as caught:
        client.start()
    assert caught.value.kind == "network"
    assert client.connected is False


def test_mcp_instructions_are_appended_to_system_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    from workbench import mcp_registry
    from workbench.api import routes_chat

    class FakeRegistry:
        @staticmethod
        def instructions_block() -> str:
            return "## 外部 MCP server 说明\n先读文档。"

    monkeypatch.setattr(mcp_registry, "get_registry", lambda: FakeRegistry())
    prompt = routes_chat._system_prompt()
    assert "外部 MCP server 说明" in prompt
    assert "先读文档。" in prompt
