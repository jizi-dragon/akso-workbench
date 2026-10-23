"""MCP 客户端 / 注册层 / 管理面的契约测试（假 server，不联网）。

## 为什么需要这一份

MCP 是本仓 2026-10-19 新增的能力，也是 WorkBuddy 对标里**唯一从零开始**的一块。
它同时碰到：子进程、JSON-RPC、动态工具注册、环境变量边界、失败隔离。
每一块都不能只靠"手工连一个真 server 试试"——那既不可重复，也看不见环境泄漏。

假 server 是 `tests/fixtures/mcp_echo_server.py`：只实现本仓用到的四个方法。
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from workbench import mcp_registry
from workbench import plugin_api as pa
from workbench.api import routes_mcp
from workbench.services import mcp

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mcp_echo_server.py"


def _write_config(tmp_path: Path, servers: dict) -> Path:
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": servers}, ensure_ascii=False), encoding="utf-8")
    return path


def _fake_server(**extra) -> dict:
    item = {
        "command": sys.executable,
        "args": ["-u", str(FIXTURE)],
        "risk": "read",
        "timeout_s": 5,
    }
    item.update(extra)
    return item


@pytest.fixture()
def cfg_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把配置路径指到 tmp；注册表与 `load_config()` 都必须看同一个路径。"""
    path = _write_config(tmp_path, {"fake": _fake_server()})
    monkeypatch.setattr(mcp, "config_path", lambda: path)
    return path


# ---------------------------------------------------------------- 客户端


def test_client_handshake_lists_tools_and_calls_one(cfg_path: Path) -> None:
    servers, errors = mcp.load_config()
    assert errors == [] and "fake" in servers
    client = mcp.McpClient(servers["fake"])
    try:
        client.start()
        assert client.server_info.get("name") == "fake-mcp"
        tools = client.list_tools()
        assert {t["name"] for t in tools} == {"echo", "add", "env_probe", "fail", "slow", "notify_change"}
        assert client.call_tool("echo", {"text": "你好"}) == "你好"
        assert client.call_tool("add", {"a": 2, "b": 3}) == "5"
    finally:
        client.close()


def test_tool_error_is_raised_as_mcp_error(cfg_path: Path) -> None:
    servers, _ = mcp.load_config()
    client = mcp.McpClient(servers["fake"])
    try:
        client.start()
        with pytest.raises(mcp.McpError, match="坏了"):
            client.call_tool("fail", {})
    finally:
        client.close()


def test_host_environment_is_not_inherited(
    cfg_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """规则 13：宿主环境变量不能整份传给子进程。"""
    monkeypatch.setenv("MCP_FAKE_SECRET", "should-not-leak")
    servers, _ = mcp.load_config()
    client = mcp.McpClient(servers["fake"])
    try:
        client.start()
        assert client.call_tool("env_probe", {}) == "no"
    finally:
        client.close()


def test_configured_env_is_passed_explicitly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_config(tmp_path, {"fake": _fake_server(env={"MCP_FAKE_SECRET": "explicit"})})
    monkeypatch.setattr(mcp, "config_path", lambda: path)
    servers, _ = mcp.load_config()
    client = mcp.McpClient(servers["fake"])
    try:
        client.start()
        assert client.call_tool("env_probe", {}) == "yes"
    finally:
        client.close()


def test_tool_list_follows_next_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    """`tools/list` 的分页游标必须跟随；否则工具多的 server 只露出一页。"""
    cfg = mcp.ServerConfig(name="x", command="x")
    client = mcp.McpClient(cfg)
    pages = [
        {"tools": [{"name": "a"}], "nextCursor": "p2"},
        {"tools": [{"name": "b"}]},
    ]
    seen: list[dict] = []

    def fake_request(method: str, params: dict, *, timeout: float) -> dict:
        seen.append(params)
        return pages[len(seen) - 1]

    monkeypatch.setattr(client, "_request", fake_request)
    assert [tool["name"] for tool in client.list_tools()] == ["a", "b"]
    assert seen == [{}, {"cursor": "p2"}]


def test_bad_config_entries_are_isolated(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {
        "good": _fake_server(),
        "bad name": _fake_server(),
        "missing-command": {"args": []},
        "bad-risk": _fake_server(risk="root"),
    })
    servers, errors = mcp.load_config(path)
    assert set(servers) == {"good"}
    assert len(errors) == 3


# ---------------------------------------------------------------- 注册层


def test_registry_registers_tools_and_executes(cfg_path: Path) -> None:
    hub = pa.ToolHub()
    registry = mcp_registry.McpRegistry(hub)
    try:
        state = registry.reload()
        assert state["errors"] == []
        assert state["servers"][0]["connected"] is True
        assert state["servers"][0]["toolCount"] == 6
        assert {s.name for s in hub.specs()} >= {"mcp__fake__echo", "mcp__fake__add"}

        inv = asyncio.run(hub.execute("mcp__fake__echo", {"text": "hi"}))
        assert inv.status is pa.ToolStatus.OK
        assert "hi" in inv.content
        assert "外部" in inv.content and "不可信" in inv.content, "MCP 结果必须带 UNTRUSTED 前缀（I18）"
    finally:
        registry.close()


def test_registry_defaults_to_write_risk_and_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """配置不写 risk 时默认 write；裸 hub 没有确认门 ⇒ 拒执行。"""
    server = _fake_server()
    server.pop("risk")  # 走缺省
    path = _write_config(tmp_path, {"fake": server})
    monkeypatch.setattr(mcp, "config_path", lambda: path)
    hub = pa.ToolHub()
    registry = mcp_registry.McpRegistry(hub)
    try:
        registry.reload()
        inv = asyncio.run(hub.execute("mcp__fake__echo", {"text": "hi"}))
        assert inv.status is pa.ToolStatus.REJECTED
    finally:
        registry.close()


def test_one_bad_server_does_not_block_the_good_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_config(tmp_path, {
        "fake": _fake_server(),
        "broken": {"command": "definitely-not-a-real-command-xyz", "args": []},
    })
    monkeypatch.setattr(mcp, "config_path", lambda: path)
    hub = pa.ToolHub()
    registry = mcp_registry.McpRegistry(hub)
    try:
        state = registry.reload()
        by_name = {s["name"]: s for s in state["servers"]}
        assert by_name["fake"]["connected"] is True
        assert by_name["broken"]["connected"] is False
        assert "启动" in by_name["broken"]["error"] or "not found" in by_name["broken"]["error"].lower()
        assert hub.get("mcp__fake__echo") is not None
    finally:
        registry.close()


def test_reload_removes_stale_tools(cfg_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = pa.ToolHub()
    registry = mcp_registry.McpRegistry(hub)
    try:
        registry.reload()
        assert hub.get("mcp__fake__echo") is not None

        # 配置改成空 → 重连后旧工具必须消失（否则卸载后仍能被模型看到）
        _write_config(cfg_path.parent, {})
        registry.reload()
        assert hub.get("mcp__fake__echo") is None
    finally:
        registry.close()


# ---------------------------------------------------------------- 管理面


def test_status_endpoint_uses_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = SimpleNamespace(
        status=lambda: {"servers": [], "errors": [], "tools": 0},
        reload=lambda: {"servers": [], "errors": [], "tools": 0, "reloaded": True},
    )
    monkeypatch.setattr(mcp_registry, "get_registry", lambda: fake)
    assert routes_mcp.list_servers()["tools"] == 0
    assert asyncio.run(routes_mcp.reload_servers())["reloaded"] is True


def test_status_endpoint_maps_failure_to_503(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom():
        raise RuntimeError("注册表坏了")

    monkeypatch.setattr(mcp_registry, "get_registry", boom)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        routes_mcp.list_servers()
    assert ei.value.status_code == 503


def test_reconnect_route_maps_unknown_server_to_404(monkeypatch: pytest.MonkeyPatch) -> None:
    class Registry:
        def reconnect(self, _name: str):
            raise ValueError("没有这个 MCP server")

        def status(self):
            return {"servers": []}

    monkeypatch.setattr(mcp_registry, "get_registry", lambda: Registry())
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        asyncio.run(routes_mcp.reconnect_server("nope"))
    assert ei.value.status_code == 404


# ---------------------------------------------------------------- 配置边界


def test_mcp_json_is_a_sensitive_path() -> None:
    """配置里可能有 server token，只读工具不能把它整份交给模型。"""
    from workbench import config

    assert config.is_sensitive_path(Path("C:/x/Workbench/mcp.json")) is True


# ---------------------------------------------------------------- 稳定性 / 规范性


def test_ping_and_tools_changed_notification(cfg_path: Path) -> None:
    servers, _ = mcp.load_config()
    client = mcp.McpClient(servers["fake"])
    try:
        client.start()
        client.ping(timeout=2)
        client.call_tool("notify_change", {})
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not client.tools_changed.is_set():
            time.sleep(0.01)
        assert client.tools_changed.is_set(), "tools/list_changed 通知没有被识别"
    finally:
        client.close()


def test_ping_method_not_found_is_not_a_failure(
    cfg_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没实现 ping 的 server 不应每 30 秒被重启一次。"""
    servers, _ = mcp.load_config()
    client = mcp.McpClient(servers["fake"])

    def unsupported(*_a, **_k):
        raise mcp.McpError("MCP ping 失败：Method not found", code=-32601, kind="rpc")

    monkeypatch.setattr(client, "_request", unsupported)
    client.ping(timeout=0.1)  # 不抛 = 当作健康


def test_concurrent_calls_are_serialized_not_corrupted(cfg_path: Path) -> None:
    servers, _ = mcp.load_config()
    client = mcp.McpClient(servers["fake"])
    results: list[str] = []
    errors: list[str] = []

    def worker(text: str) -> None:
        try:
            results.append(client.call_tool("echo", {"text": text}))
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))

    try:
        client.start()
        threads = [threading.Thread(target=worker, args=(f"v{i}",)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert errors == []
        assert sorted(results) == [f"v{i}" for i in range(4)]
    finally:
        client.close()


def test_registry_reports_protocol_and_server_version(cfg_path: Path) -> None:
    hub = pa.ToolHub()
    registry = mcp_registry.McpRegistry(hub)
    try:
        state = registry.reload()
        server = state["servers"][0]
        assert server["protocolVersion"] == "2024-11-05"
        assert server["serverName"] == "fake-mcp"
        assert server["serverVersion"] == "1.0.0"
        assert "echo" in server["toolNames"]
        assert server["lastPingAt"] > 0
    finally:
        registry.close()


def test_health_check_reconnects_after_process_death(cfg_path: Path) -> None:
    hub = pa.ToolHub()
    registry = mcp_registry.McpRegistry(hub)
    try:
        registry.reload()
        client = registry._clients["fake"]
        client._proc.kill()
        client._proc.wait(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and client.connected:
            time.sleep(0.02)
        assert not client.connected

        state = registry.health_check()
        server = state["servers"][0]
        assert server["connected"] is True, server
        assert server["restarts"] >= 1
        assert hub.get("mcp__fake__echo") is not None
    finally:
        registry.close()


def test_health_check_refreshes_on_tools_changed(cfg_path: Path) -> None:
    hub = pa.ToolHub()
    registry = mcp_registry.McpRegistry(hub)
    try:
        registry.reload()
        registry._clients["fake"].call_tool("notify_change", {})
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not registry._clients["fake"].tools_changed.is_set():
            time.sleep(0.01)
        state = registry.health_check()
        assert state["servers"][0]["restarts"] >= 1
        assert hub.get("mcp__fake__echo") is not None
    finally:
        registry.close()


def test_per_tool_risk_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_config(tmp_path, {
        "fake": _fake_server(risk={"default": "write", "tools": {"echo": "read"}}),
    })
    monkeypatch.setattr(mcp, "config_path", lambda: path)
    hub = pa.ToolHub()
    registry = mcp_registry.McpRegistry(hub)
    try:
        registry.reload()
        assert hub.get("mcp__fake__echo").risk is pa.Risk.READ
        assert hub.get("mcp__fake__add").risk is pa.Risk.WRITE
    finally:
        registry.close()


def test_monitor_is_idempotent_and_stops(cfg_path: Path) -> None:
    hub = pa.ToolHub()
    registry = mcp_registry.McpRegistry(hub)
    try:
        registry.reload()
        registry.start_monitor(interval_s=3600)
        first = registry._monitor_thread
        registry.start_monitor(interval_s=3600)
        assert registry._monitor_thread is first, "重复启动会多出一个巡检线程"
    finally:
        registry.close()
