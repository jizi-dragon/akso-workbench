"""API 装配冒烟：应用可创建、新路由全部注册、模块体检端点可用。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from workbench.api import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


EXPECTED_PREFIXES = (
    "/api/conversations", "/api/settings", "/api/chat",
    "/api/modules", "/api/insight", "/api/factory", "/api/accounts", "/api/browser", "/api/agent",
)


def test_routes_registered() -> None:
    """用 OpenAPI schema 提取路径（兼容 FastAPI 0.14x 的 _IncludedRouter 包装）。"""
    paths = set(app.openapi()["paths"].keys())
    for prefix in EXPECTED_PREFIXES:
        assert any(p == prefix or p.startswith(prefix + "/") for p in paths), f"缺少路由前缀：{prefix}"


def test_index_served(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Akso Workbench" in resp.text


def test_modules_endpoint(client: TestClient) -> None:
    resp = client.get("/api/modules")
    assert resp.status_code == 200
    data = resp.json()
    ids = {m["id"] for m in data["modules"]}
    assert {"workbench", "accounts", "akso-cc", "akso-auto"} <= ids


def test_module_recheck(client: TestClient) -> None:
    resp = client.post("/api/modules/akso-cc/check")
    assert resp.status_code == 200
    assert resp.json()["status"] in {"ok", "degraded", "missing"}


def test_agent_tools_listed(client: TestClient) -> None:
    resp = client.get("/api/agent/tools")
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()["tools"]}
    assert {"login_platform", "read_config", "run_insight", "browser_status"} <= names
    assert "search_knowledge" not in names  # 知识库功能已下线


def test_agent_unknown_tool_404(client: TestClient) -> None:
    resp = client.post("/api/agent/invoke", json={"tool": "nope", "args": {}})
    assert resp.status_code == 404


def test_factory_confirm_guard(client: TestClient) -> None:
    """环境强制确认原则：未勾选 confirmed 必须被 409 拒绝。"""
    resp = client.post("/api/factory/run", json={
        "blueprint_id": "whatever", "command": "create",
        "account_id": "whatever", "confirmed": False,
    })
    assert resp.status_code == 409
    assert "强制确认" in resp.json()["detail"]
