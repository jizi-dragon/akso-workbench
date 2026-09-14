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
    "/api/update",
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
    # 洞察模块的 id 是 `insight`（2026-09-14 由上游项目名 `akso-cc` 改来）；
    # 它的**存档文件名**仍是 adapters/akso-cc.json，两者不一致是刻意的。
    assert {"workbench", "accounts", "insight", "akso-auto"} <= ids
    assert "akso-cc" not in ids, "上游项目名不得再作为模块 id 暴露"


def test_module_recheck(client: TestClient) -> None:
    resp = client.post("/api/modules/insight/check")
    assert resp.status_code == 200
    assert resp.json()["status"] in {"ok", "degraded", "missing"}


def test_old_module_id_is_gone(client: TestClient) -> None:
    """改名要彻底：旧 id 必须 404，而不是留一个"还能用"的第二名字。

    留第二名字的代价：模块维度的统计/筛选会静默漏行，而这正是本轮
    `insight_runs.module` 一列先后出现过三个值（`akso-cc` / `egmp-native` / `insight`）
    的成因。改名就一次改干净。
    """
    assert client.get("/api/modules/akso-cc").status_code == 404
    assert client.post("/api/modules/akso-cc/check").status_code == 404


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
