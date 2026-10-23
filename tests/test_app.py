"""API 装配冒烟：应用可创建、新路由全部注册、模块体检端点可用。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from workbench.api import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def probe_write():
    """临时注册一个 WRITE 工具，专测确认门。

    2026-10-19 内置浏览器下线后，产品里**没有 WRITE 工具**了；确认门仍必须测，
    但它测的是 `ToolHub` 的机制，不是某个账号工具。用 installed 层注册、结束后卸载，
    不污染 `/api/agent/selfcheck` 的核心工具口径。
    """
    from workbench import plugin_api as pa
    from workbench.hub import get_hub

    async def handler(_args, _ctx):
        return "probe-ok"

    hub = get_hub()
    hub.register_installed(pa.Plugin(
        id="probe-write-test",
        title="确认门探针",
        description="仅测试用；不写平台、不碰文件",
        tools=[pa.ToolSpec(
            name="probe_write",
            description="测试确认门的写工具（无副作用）",
            args_model=pa.ZERO_ARGS,
            handler=handler,
            risk=pa.Risk.WRITE,
            trust=pa.Trust.LOCAL,
            reversibility=pa.Reversibility.IRREVERSIBLE,
            timeout_s=5,
        )],
        capability_id="probe-write-test",
    ))
    yield "probe_write"
    hub.deregister_capability("probe-write-test")


EXPECTED_PREFIXES = (
    "/api/conversations", "/api/settings", "/api/chat",
    "/api/modules", "/api/insight", "/api/accounts", "/api/agent",
)


def test_routes_registered() -> None:
    """用 OpenAPI schema 提取路径（兼容 FastAPI 0.14x 的 _IncludedRouter 包装）。"""
    paths = set(app.openapi()["paths"].keys())
    for prefix in EXPECTED_PREFIXES:
        assert any(p == prefix or p.startswith(prefix + "/") for p in paths), f"缺少路由前缀：{prefix}"


def test_index_served(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Workbench" in resp.text


def test_module_display_name_is_object_analysis(client: TestClient) -> None:
    """模块的**显示名**：`对象分析`（2026-09-22 用户下达的更名）。

    为什么值得一条判据：这个名字散落在 **6 个文件**里——侧边栏、页面的 `<title>` 与
    `<h1>`、工具清单弹窗里的**插件标题**、几条给人看的提示。改漏一处**不会报错**，
    只会让用户在两个地方看到两个名字——而这正是"改名"这类需求唯一会出的错。
    同族：`test_old_module_id_is_gone`。

    ⚠️ **只改显示名**：模块 id（`insight`）、工具名（`insight_*`）、落盘目录（`…/洞察`）
    都**不动**——它们是模型面与审计面的事实源，改了就断掉历史留痕。

    > 2026-09-22：`配置工厂 → 配置` 那一半随**配置工厂整体移除**一并消失（模块、路由、
    > 工具、页面全删）。本用例因此只剩 `对象分析` 一项，`nav-factory` 也不该再存在——
    > 下面那条"旧链接不得复活"的断言就是这次删除的守门人。
    """
    index = client.get("/static/index.html").text
    # 侧边栏那一项：按**链接块**断言（缩进与换行都在，整块比字符串包含更准）
    insight_block = index.split('id="nav-insight"')[1].split("</a>")[0]
    assert insight_block.strip().endswith("对象分析"), insight_block[-40:]

    html = client.get("/static/pages/insight.html").text
    assert "<h1>对象分析</h1>" in html
    assert "<title>对象分析 · Workbench</title>" in html

    # 工具清单弹窗里显示的是**插件标题**（`chat-tools.js` 的 palette-plugin-title）
    plugins = {p["id"]: p["title"] for p in client.get("/api/agent/plugins").json()["plugins"]}
    assert plugins["insight"] == "对象分析", plugins

    # 旧名不许再出现在**界面文件**里（注释不算——那是给读代码的人看的）
    for path in ("/static/index.html", "/static/pages/insight.html",
                 "/static/pages/accounts.html", "/static/js/insight.js"):
        text = client.get(path).text
        assert "配置工厂" not in text, path
        assert "平台洞察" not in text, path

    # 工厂面整体下线：导航不留死链、页面不再可达
    assert "nav-factory" not in index, "配置工厂的导航入口没删干净"
    assert "pages/factory.html" not in index, "配置工厂的导航链接没删干净"
    assert client.get("/static/pages/factory.html").status_code == 404
    assert client.get("/static/js/factory.js").status_code == 404


def test_modules_endpoint(client: TestClient) -> None:
    resp = client.get("/api/modules")
    assert resp.status_code == 200
    data = resp.json()
    ids = {m["id"] for m in data["modules"]}
    # 模块 id 是本工作台自己的名字（2026-09-14 `akso-cc`→`insight`）；
    # **文件名**才是"对应哪个来源项目"的引用真源。
    # 2026-09-22：`factory` 模块随配置工厂整体移除，`adapters/akso-auto.json` 一并删除。
    assert {"workbench", "accounts", "insight"} <= ids
    assert "factory" not in ids, "配置工厂模块应随实现一并消失"
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
    assert {"insight_run", "insight_read_config", "accounts_login",
            "get_current_time"} <= names
    assert "search_knowledge" not in names  # 知识库功能已下线
    assert not {n for n in names if n.startswith("factory_")}, "配置工厂的工具应随插件一并消失"

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
    assert {"builtin", "insight", "accounts"} <= ids
    assert "factory" not in ids, "配置工厂插件应已移除"
    assert data["failures"] == []


def test_agent_selfcheck(client: TestClient) -> None:
    """启动自检可见：必需工具齐、无装配失败（打包漏收插件时这里会红）。"""
    resp = client.get("/api/agent/selfcheck")
    assert resp.status_code == 200
    data = resp.json()
    assert data["missingRequired"] == []
    assert data["ok"] is True
    # ⚠️ **产品里现在一个 DANGEROUS 工具都没有**（2026-09-22）。
    #
    # 唯一的那个是 `factory_create`（"会在平台上真的建对象、而平台侧没有撤销键"），
    # 它随配置工厂整体移除。这不是缺陷，但是一个**必须被看见的事实**：
    # 确认门的 `Risk.DANGEROUS` 分支、审批弹窗的高危样式从此只剩测试构造的样本在走。
    # 一旦将来接入任何"不可逆地改生产数据"的工具，它必须是 DANGEROUS，
    # 而这条断言（0）会在那一刻变红——提醒作者回来读这段注释，而不是顺手把 0 改大。
    assert data["riskCounts"]["dangerous"] == 0, (
        "出现了 DANGEROUS 级工具：请确认它真的不可逆，并更新本断言的注释"
    )
    assert data["riskCounts"]["read"] > 0
    # 2026-10-22：memory_add / memory_delete 是 WRITE 工具（过确认门）。
    assert data["riskCounts"]["write"] == 2


def test_agent_unknown_tool_404(client: TestClient) -> None:
    resp = client.post("/api/agent/invoke", json={"tool": "nope", "args": {}})
    assert resp.status_code == 404


# ---------------------------------------------------------------- 确认门（HTTP 面）


def test_write_tool_via_http_needs_approval(client: TestClient, probe_write: str) -> None:
    """风险工具走 HTTP 也必须先确认：409 + pendingId，**不是**直接执行。

    这条是"分级是安全属性"的端到端判据——页面的勾选框不算数，执行侧才算数。
    """
    from workbench import approvals

    approvals.reset_store()
    resp = client.post("/api/agent/invoke", json={
        "tool": probe_write, "args": {},
    })
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["pendingId"]
    assert detail["risk"] == "write"
    assert "确认" in detail["message"]

    # 待确认清单里能查到它（界面靠这个端点渲染确认卡片）
    listed = client.get("/api/agent/pending").json()["pending"]
    assert [p["id"] for p in listed] == [detail["pendingId"]]
    assert listed[0]["tool"] == probe_write


def test_approved_token_passes_the_gate_once(client: TestClient, probe_write: str) -> None:
    """换令牌 → 重投 → 过门（失败在探针自己的返回，而不是"需要确认"）。"""
    from workbench import approvals

    approvals.reset_store()
    first = client.post("/api/agent/invoke", json={
        "tool": probe_write, "args": {},
    }).json()["detail"]
    token = client.post(f"/api/agent/pending/{first['pendingId']}/approve").json()["approvalToken"]

    retried = client.post("/api/agent/invoke", json={
        "tool": probe_write, "args": {},
        "approvalToken": token,
    })
    assert retried.status_code != 409, "带有效令牌不该再被确认门拦下"

    # 令牌一次性：重放同一次调用必须重新申请确认
    replay = client.post("/api/agent/invoke", json={
        "tool": probe_write, "args": {},
        "approvalToken": token,
    })
    assert replay.status_code == 409


def test_reject_flow_marks_pending(client: TestClient, probe_write: str) -> None:
    from workbench import approvals

    approvals.reset_store()
    pending_id = client.post("/api/agent/invoke", json={
        "tool": probe_write, "args": {},
    }).json()["detail"]["pendingId"]

    rejected = client.post(f"/api/agent/pending/{pending_id}/reject",
                           json={"reason": "现在不要执行写操作"})
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert client.post(f"/api/agent/pending/{pending_id}/approve").status_code == 409


# ---------------------------------------------------------------- 模型主动确认（HTTP 面）


def test_model_confirmation_via_http_returns_409_without_waiting(
    client: TestClient, probe_write: str,
) -> None:
    """模型发起的实时确认走 HTTP：**409 + 确认内容，且本端点不替用户等**。

    对话链路里这个工具由 `harness/loop.py` 就地等答复；HTTP 面没有"就地等"这回事，
    所以必须如实返回 409 让调用方自己去看 `/pending`。两种错法都要防：
    把它当成功（调用方以为用户同意了）、或在请求里挂 5 分钟替用户等。
    """
    from workbench import approvals

    approvals.reset_store()
    resp = client.post("/api/agent/invoke", json={
        "tool": "request_user_confirmation",
        "args": {
            "question": "把 3 个对象提交到生产，是否继续？",
            "details": "可回滚",
            "target_tool": probe_write,
            "target_args": {},
        },
    })
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["status"] == "needs_confirmation"
    assert detail["pendingId"]
    assert detail["confirm"]["question"].startswith("把 3 个对象提交到生产")
    assert detail["confirm"]["target"] == {
        "tool": probe_write, "risk": "write", "args": {},
    }

    # 待确认清单里能查到，且**挂在目标工具头上**（不是确认工具自己）——
    # 这条是"用户同意的是那一次具体调用"的可查证据
    listed = client.get("/api/agent/pending").json()["pending"]
    assert [p["id"] for p in listed] == [detail["pendingId"]]
    assert listed[0]["tool"] == probe_write

    # 同意之后再调目标工具，参数一致 → 直接执行（探针无副作用）
    client.post(f"/api/agent/pending/{detail['pendingId']}/approve")
    retried = client.post("/api/agent/invoke", json={
        "tool": probe_write, "args": {},
    })
    assert retried.status_code != 409, "预先批准没有生效——指纹或认领通道断了"


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
