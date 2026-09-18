"""扩展同步蓝图（`api/routes_extension.py`）的契约与回归。

## 为什么补这一份（T1，2026-09-17）

`/extension/*` 是**桌面 ↔ 扩展**之间的唯一数据与指令通道，却一直**没有任何 pytest 覆盖**——
本文件落地前，全仓对它的回归只有 `node tools/verify_extension_sync.mjs` 一条离线脚本
（那条脚本还会起真实服务、读真实账号库，因此进不了 CI 的 pytest 门禁）。
后果是这一整块最容易漂移的地方全靠人记：**它同时是凭据下发口（快照里带 Fernet 密钥）、
指令队列的宿主、以及"扩展是否需要重新加载"的判定源**。

本文件钉住三件事：
1. **快照契约**：字段名与形状（`passwordEnc` 驼峰写错会让扩展端**静默丢弃全部账号**，
   0.2.3 实锤断点）——所以断言写在字段级，不只看 HTTP 200；
2. **指令队列语义**：游标过滤、跨重启单调递增、按水位线回收（C4 回归）；
3. **体检口径**：`/health` 的四态判定与 `extStale` 比较。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from workbench import db  # 供 _clean_queue 清 settings（令牌/宽限起点跨用例串味）
from workbench.api import app
from workbench.api import routes_extension as ext
from workbench.services import accounts as accounts_svc

# 测试用的稳定站点标识：一律用文档占位域名，绝不写真实客户环境（见 docs 的脱敏纪律）
ENV_A = "https://demo-val.example.com"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_queue():
    """指令队列是**模块级内存态**，用例之间会互相串（也会串到别的测试文件）。

    顺带清 `_seq`，逼 `_next_seq()` 重新走"从 settings 表恢复"那条路径——
    否则"跨重启单调递增"这条契约会被内存缓存掩盖，测了等于没测。

    另外清掉**访问令牌与宽限窗口起点**（P0-1）：两者都落库，而 db 是会话级共享，
    不清的话本文件的用例会受"别的文件先跑过一次无令牌请求"影响（详见 test_extension_auth）。
    """
    from workbench.api import extension_auth as auth

    def _reset() -> None:
        ext._reset_command_queue_for_tests()
        for key in (auth.GRACE_STARTED_KEY, auth.TOKEN_SETTING_KEY):
            db.execute("DELETE FROM settings WHERE key = ?", (key,))

    _reset()
    yield
    _reset()


def _dispatch(cmd_type: str = "par.open", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return ext.dispatch_command(cmd_type, payload if payload is not None else {"accountId": "acct-1"})


def _make_account(username: str, base_url: str = ENV_A) -> dict[str, Any]:
    """建环境 + 账号（走 service 层，与 tests/test_accounts.py 同一套入口）。"""
    env = accounts_svc.create_env(name=f"env-{username}", base_url=base_url)
    return accounts_svc.create_account(env_id=env["id"], username=username, password="placeholder-pw")


# --------------------------------------------------------------------- 快照契约


def test_snapshot_shape_and_camelcase_keys(client: TestClient) -> None:
    """快照字段名是**跨语言协议**，不是内部实现细节。

    `envBaseUrl`（驼峰）曾经写成下划线 → 扩展端 host 恒空 → **静默丢弃全部账号**
    （0.2.3 实锤断点）。故此处按字段名逐条断言，而不是只断言 200。
    """
    data = client.get("/extension/snapshot").json()
    assert data["format"] == "workbench-snapshot"
    assert data["version"] == 1
    assert isinstance(data["snapshotId"], str) and data["snapshotId"]
    assert "fernetKey" in data, "扩展端需要密钥才能解密凭据（与备份文件同语义）"
    assert set(data) >= {"accounts", "boxes", "sites", "desktopVersion"}

    account = _make_account("tester")
    data = client.get("/extension/snapshot").json()
    entry = next((a for a in data["accounts"] if a["desktopId"] == account["id"]), None)
    assert entry is not None, "新建账号必须出现在下发的快照里"
    # 逐字段钉住协议名（驼峰）
    assert set(entry) == {"desktopId", "host", "scheme", "tabName", "username", "passwordEnc", "box"}
    assert entry["host"] == "demo-val.example.com", "host 必须由 envBaseUrl 解析出来且非空"
    assert entry["scheme"] == "https"
    assert entry["username"] == "tester"
    assert entry["passwordEnc"], "凭据必须以密文形态下发"
    assert "placeholder-pw" not in str(data), "快照里不得出现明文口令"


def test_snapshot_id_changes_only_when_content_changes(client: TestClient) -> None:
    """`snapshotId` 是扩展端幂等跳过的依据（内容哈希），不能每次都变。"""
    first = client.get("/extension/snapshot").json()["snapshotId"]
    assert client.get("/extension/snapshot").json()["snapshotId"] == first
    _make_account("content-change")
    assert client.get("/extension/snapshot").json()["snapshotId"] != first


# --------------------------------------------------------------------- 指令队列


def test_dispatch_then_fetch_filters_by_cursor(client: TestClient) -> None:
    """`after` 是扩展端持久化的游标：只回它还没见过的指令（否则每 2s 重复执行）。"""
    first = _dispatch("wheel.toggle", {})
    second = _dispatch("par.open", {"accountId": "acct-2"})

    all_cmds = client.get("/extension/commands?after=0").json()
    assert [c["seq"] for c in all_cmds["commands"]] == [first["seq"], second["seq"]]
    assert all_cmds["cursor"] == second["seq"]
    assert all_cmds["longPoll"] is False, "wait=0 时不得声称自己在长轮询"

    after_first = client.get(f"/extension/commands?after={first['seq']}").json()
    assert [c["seq"] for c in after_first["commands"]] == [second["seq"]]


def test_command_seq_is_monotonic_across_restart(client: TestClient) -> None:
    """序号落 settings 表：服务端重启后若从 1 重来，新指令会被扩展端游标永久吞掉（0.2.4 实锤）。"""
    first = _dispatch()
    # 模拟进程重启：内存态清空（_seq 也清），但 settings 表保留
    ext._reset_command_queue_for_tests()
    second = _dispatch()
    assert second["seq"] > first["seq"], "序号必须跨重启单调递增（落 settings 表）"


def test_push_command_endpoint_writes_to_queue(client: TestClient) -> None:
    """桌面侧的 HTTP 入口（账号中心/轮盘）与 `dispatch_command` 是同一条队列。"""
    resp = client.post("/extension/commands", json={"type": "par.open", "payload": {"accountId": "acct-9"}})
    assert resp.status_code == 200
    cmds = client.get("/extension/commands?after=0").json()["commands"]
    assert any(c["type"] == "par.open" and c["payload"].get("accountId") == "acct-9" for c in cmds)


# ------------------------------------------------------- C4 回归：队列必须能排空


def test_ack_watermark_drains_queue_even_when_client_acks_a_prefix(client: TestClient) -> None:
    """**C4 回归**：扩展端只 ack **最大 cursor**，服务端必须按水位线回收。

    旧实现要求"队列内**每一条**都在已 ack 集合里"，而客户端只上报一个 cursor：
    于是 `_commands.clear()` **恒不执行**，队列与已 ack 集合只增不减
    （每次 `/commands` 都要重扫全部历史指令）。修复前本用例在最后两条断言上 FAIL。
    """
    first = _dispatch("wheel.toggle", {})
    second = _dispatch("par.open", {"accountId": "acct-1"})

    # 客户端消费到 first 就 ack（还没取到 second）——这正是旧实现卡死的形态
    acked = client.post("/extension/ack", json={"seqs": [first["seq"]]}).json()
    assert acked["pending"] == 1, "first 应被回收，队列里只剩 second"
    assert [c["seq"] for c in client.get("/extension/commands?after=0").json()["commands"]] == [second["seq"]]

    # 再消费到 second：队列必须彻底排空
    acked = client.post("/extension/ack", json={"seqs": [second["seq"]]}).json()
    assert acked["pending"] == 0, "全部消费后队列必须清空（旧实现在此恒为 2）"
    assert client.get("/extension/commands?after=0").json()["commands"] == []


def test_ack_watermark_never_goes_backwards(client: TestClient) -> None:
    """乱序/重试可能 ack 一个更小的值：水位线只增不减，已回收的指令不得复活。"""
    first = _dispatch()
    second = _dispatch()
    client.post("/extension/ack", json={"seqs": [second["seq"]]})
    client.post("/extension/ack", json={"seqs": [first["seq"]]})  # 迟到的旧 ack
    assert client.get("/extension/commands?after=0").json()["commands"] == []


def test_ack_with_empty_seqs_is_a_noop(client: TestClient) -> None:
    _dispatch()
    client.post("/extension/ack", json={})
    assert len(client.get("/extension/commands?after=0").json()["commands"]) == 1


# --------------------------------------------------------------------- 健康判定


def test_health_reports_disconnected_when_no_report(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ext, "_STATE", {})
    monkeypatch.setitem(ext._ext_meta, "extVersion", "")
    monkeypatch.setitem(ext._ext_meta, "at", 0.0)
    data = client.get("/extension/health").json()
    assert data["connected"] is False
    assert data["reportedAccounts"] == 0
    assert data["extStale"] is False, "扩展未上报版本时不得做旧版判定（会误报打扰用户）"


def test_health_marks_old_extension_as_stale(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """桌面端与扩展**同号升版**：扩展版本低于桌面端 ⇒ 需要用户点一次「重新加载」。"""
    monkeypatch.setattr(ext, "_STATE", {})
    monkeypatch.setitem(ext._ext_meta, "extVersion", "0.0.1")
    monkeypatch.setitem(ext._ext_meta, "at", ext.now_ms())
    data = client.get("/extension/health").json()
    assert data["connected"] is True, "只有版本、没有账号映射的上报也算连上了"
    assert data["extStale"] is True

    monkeypatch.setitem(ext._ext_meta, "extVersion", data["desktopVersion"])
    assert client.get("/extension/health").json()["extStale"] is False


def test_state_report_is_ttl_scoped(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """执行面状态只认 TTL 内的条目（扩展离线后徽标必须自动回落）。"""
    monkeypatch.setattr(ext, "_STATE", {})
    client.post("/extension/state", json={"items": [{"desktopId": "d1", "tabs": 2, "hasToken": True}]})
    items = client.get("/extension/state").json()["items"]
    assert len(items) == 1 and items[0]["desktopId"] == "d1" and items[0]["hasToken"] is True

    # 把上报时刻推到 TTL 之外
    monkeypatch.setitem(ext._STATE["d1"], "at", ext.now_ms() - ext._STATE_TTL_MS - 1)
    assert client.get("/extension/state").json()["items"] == []


# ------------------------------------------- R1：本蓝图**无鉴权**（P0-1 已修，此处守回归）
def test_extension_blueprint_requires_access_token(client: TestClient) -> None:
    """**R1 回归哨兵**（P0-1，2026-09-17 修）。

    本条前身是"记录 `/extension/*` 无鉴权这一已知风险"的断言——按设计，
    **加上鉴权时它必须失败**，以此强迫改动者看到 R1 并一并更新文档。
    现在它已翻转成**守卫**：一旦有人把中间件摘掉（或把 `/extension/` 从守备前缀里挪走），
    这里会立刻红，而不是让"本机任意进程一次 GET 拿到全部账号明文"静默回来。

    详细门禁（严格模式真拦、豁免面最小、401 带机器可读 code）见 `tests/test_extension_auth.py`。
    """
    from workbench.api import extension_auth as auth

    # 结构面：整个前缀被守
    assert auth.GUARDED_PREFIX == "/extension/"
    # 被豁免的路径**只允许**是这两个：`/health` 是本蓝图里的只读徽标判据（无账号数据、无指令面），
    # `/hello` 由中间件就地应答、根本不在本路由表里。多出任何一条 = 有人扩大了无鉴权面。
    exempt_in_blueprint = {r.path for r in ext.router.routes if r.path in auth.EXEMPT_PATHS}
    assert exempt_in_blueprint <= {"/extension/health"}, (
        f"扩展蓝图里出现了预期外的豁免路径：{sorted(exempt_in_blueprint - {'/extension/health'})}"
    )
    # 行为面：中间件确实装在 app 上（拿掉它会红在这里）
    middleware_names = {m.cls.__name__ for m in app.user_middleware}
    assert "ExtensionAuthMiddleware" in middleware_names, (
        "app 上找不到 ExtensionAuthMiddleware——/extension/* 又变回无鉴权了（R1 回归）"
    )
    # 而受保护的那些端点确实不在豁免里
    guarded = {r.path for r in ext.router.routes} - auth.EXEMPT_PATHS
    assert "/extension/snapshot" in guarded and "/extension/commands" in guarded
