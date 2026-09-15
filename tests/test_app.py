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
    # 两个业务模块的 id 是本工作台自己的名字（2026-09-14 `akso-cc`→`insight`、
    # 2026-09-15 `akso-auto`→`factory`）；它们的**存档文件名**仍是 adapters/akso-*.json，
    # 那是"对应哪个来源项目"的引用真源，两者不一致是刻意的。
    assert {"workbench", "accounts", "insight", "factory"} <= ids
    assert not {i for i in ids if i.startswith("akso-")}, "上游项目名不得再作为模块 id 暴露"


def test_module_recheck(client: TestClient) -> None:
    resp = client.post("/api/modules/insight/check")
    assert resp.status_code == 200
    assert resp.json()["status"] in {"ok", "degraded", "missing"}


def test_old_module_id_is_gone(client: TestClient) -> None:
    """改名要彻底：旧 id 必须 404，而不是留一个"还能用"的第二名字。

    留第二名字的代价：模块维度的统计/筛选会静默漏行，而这正是本轮
    `insight_runs.module` 一列先后出现过三个值（`akso-cc` / `egmp-native` / `insight`）、
    `blueprint_jobs.module` 出现过两个值（`akso-auto` / `egmp-native`）的成因。
    改名就一次改干净。
    """
    for old in ("akso-cc", "akso-auto"):
        assert client.get(f"/api/modules/{old}").status_code == 404, old
        assert client.post(f"/api/modules/{old}/check").status_code == 404, old


def test_agent_tools_listed(client: TestClient) -> None:
    """工具清单来自**工具插件中枢**（2026-09-15 收口）。

    此前这里断言的是 `routes_agent.TOOLS` 那四个域工具名（login_platform / read_config /
    run_insight / browser_status）——那套表与 `harness/tools.py` 的三个基础工具并存且
    互不可见，对话里的模型一个平台工具都看不到。现在两者合为 `plugin_api.ToolHub`，
    工具名随插件命名规范（`<plugin>_<动作>`）。
    """
    resp = client.get("/api/agent/tools")
    assert resp.status_code == 200
    data = resp.json()
    names = {t["name"] for t in data["tools"]}
    assert {"insight_run", "insight_read_config", "factory_create", "accounts_login",
            "get_current_time"} <= names
    assert "search_knowledge" not in names  # 知识库功能已下线

    # 每个工具都必须带上插件归属与风险分级——没有分级的工具进不了确认门
    for tool in data["tools"]:
        assert tool["risk"] in {"read", "write", "dangerous"}, tool
        assert tool["plugin"], tool


def test_agent_plugins_listed(client: TestClient) -> None:
    """三大业务 + 内置能力都以插件形式装配，且装配无失败。"""
    resp = client.get("/api/agent/plugins")
    assert resp.status_code == 200
    data = resp.json()
    ids = {p["id"] for p in data["plugins"]}
    assert {"builtin", "insight", "factory", "accounts"} <= ids
    assert data["failures"] == []


def test_agent_selfcheck(client: TestClient) -> None:
    """启动自检可见：必需工具齐、无装配失败（打包漏收插件时这里会红）。"""
    resp = client.get("/api/agent/selfcheck")
    assert resp.status_code == 200
    data = resp.json()
    assert data["missingRequired"] == []
    assert data["ok"] is True
    assert data["riskCounts"]["dangerous"] >= 1   # factory_create 必须在


def test_agent_unknown_tool_404(client: TestClient) -> None:
    resp = client.post("/api/agent/invoke", json={"tool": "nope", "args": {}})
    assert resp.status_code == 404


# ---------------------------------------------------------------- 确认门（HTTP 面）


def test_write_tool_via_http_needs_approval(client: TestClient) -> None:
    """风险工具走 HTTP 也必须先确认：409 + pendingId，**不是**直接执行。

    这条是"分级是安全属性"的端到端判据——页面的勾选框不算数，执行侧才算数。
    """
    from workbench import approvals

    approvals.reset_store()
    resp = client.post("/api/agent/invoke", json={
        "tool": "accounts_browser_close", "args": {"account_id": "ghost"},
    })
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["pendingId"]
    assert detail["risk"] == "write"
    assert "确认" in detail["message"]

    # 待确认清单里能查到它（界面靠这个端点渲染确认卡片）
    listed = client.get("/api/agent/pending").json()["pending"]
    assert [p["id"] for p in listed] == [detail["pendingId"]]
    assert listed[0]["tool"] == "accounts_browser_close"


def test_approved_token_passes_the_gate_once(client: TestClient) -> None:
    """换令牌 → 重投 → 过门（失败在"账号不存在"这一步，而不是"需要确认"）。"""
    from workbench import approvals

    approvals.reset_store()
    first = client.post("/api/agent/invoke", json={
        "tool": "accounts_browser_close", "args": {"account_id": "ghost"},
    }).json()["detail"]
    token = client.post(f"/api/agent/pending/{first['pendingId']}/approve").json()["approvalToken"]

    retried = client.post("/api/agent/invoke", json={
        "tool": "accounts_browser_close", "args": {"account_id": "ghost"},
        "approvalToken": token,
    })
    assert retried.status_code != 409, "带有效令牌不该再被确认门拦下"

    # 令牌一次性：重放同一次调用必须重新申请确认
    replay = client.post("/api/agent/invoke", json={
        "tool": "accounts_browser_close", "args": {"account_id": "ghost"},
        "approvalToken": token,
    })
    assert replay.status_code == 409


def test_reject_flow_marks_pending(client: TestClient) -> None:
    from workbench import approvals

    approvals.reset_store()
    pending_id = client.post("/api/agent/invoke", json={
        "tool": "accounts_browser_open", "args": {"account_id": "ghost"},
    }).json()["detail"]["pendingId"]

    rejected = client.post(f"/api/agent/pending/{pending_id}/reject",
                           json={"reason": "现在不要开窗口"})
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert client.post(f"/api/agent/pending/{pending_id}/approve").status_code == 409


def test_audit_detail_endpoint(client: TestClient) -> None:
    """审计按 id 单查（列表刻意不 SELECT *，细节走这条）。"""
    from workbench import db

    row_id = "audit-detail-1"
    db.execute(
        "INSERT INTO agent_audit (id, tool, args, status, result_summary, duration_ms, "
        "created_at, plugin, risk, meta) VALUES (?, 'get_current_time', '{}', 'ok', 'x', 1, 1, "
        "'builtin', 'read', '{}')",
        (row_id,),
    )
    resp = client.get(f"/api/agent/audit/{row_id}")
    assert resp.status_code == 200
    assert resp.json()["tool"] == "get_current_time"


def test_audit_list_omits_args(client: TestClient) -> None:
    """列表不带 args（可能含凭据片段），只给摘要与元数据。"""
    rows = client.get("/api/agent/audit").json()["audit"]
    assert rows and all("args" not in row for row in rows)


def test_factory_confirm_guard(client: TestClient) -> None:
    """环境强制确认原则：未勾选 confirmed 必须被 409 拒绝。"""
    resp = client.post("/api/factory/run", json={
        "blueprint_id": "whatever", "command": "create",
        "account_id": "whatever", "confirmed": False,
    })
    assert resp.status_code == 409
    assert "强制确认" in resp.json()["detail"]
