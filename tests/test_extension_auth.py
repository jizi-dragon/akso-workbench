"""`/extension/*` 访问令牌的门禁测试（P0-1）。

## 这个文件守的是什么

`GET /extension/snapshot` 会**同时**下发 Fernet 密钥与全部密文凭据，而整条 `/extension/*`
此前**没有任何鉴权**——CODEBASE_OVERVIEW 的 **R1**：本机任意进程一次 GET 即等于拿到全部
测试账号的明文口令。这是全仓唯一的"拿到即等于拿到口令"单点。

修复方式 = 按**路径前缀**统一要求 `X-Workbench-Token`（`workbench/api/extension_auth.py`，
ASGI 中间件而非 route 依赖，理由见该模块文档串）。

## 本文件钉住的四件事

1. **默认放行靠宽限、严格模式必须真拦**——`grace` 是为了"桌面升级了、用户还没点重新加载
   扩展"这段过渡，**不是**永久开口；`grace=0` 时必须 401。
2. **豁免面必须最小**——只有 `/extension/hello`（自举，只回令牌与版本号）与
   `/extension/health`（只读徽标判据）。**任何人往豁免里加东西，这条会红。**
3. **`/extension/*` 下不得再有"给桌面 UI 用、不碰凭据"的端点**——那类端点拿不到令牌、
   只能靠开豁免，会把"整个前缀需要令牌"退化成人工维护的清单。
   `launch-chrome` / `setup-helper` / `update-state` 已因此搬到 `/api/shell/*`。
4. **401 必须带机器可读 code**——扩展端靠它决定"重新握手并重试"，
   否则 401 会被当普通网络错误吞掉，表现为"快捷登录莫名失灵"（静默故障）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from workbench.api import app
from workbench.api import extension_auth as auth
from workbench.api import routes_extension as ext

#: 令牌头名必须与扩展端 `desktop-auth.ts::TOKEN_HEADER` **逐字一致**（不区分大小写）
TOKEN_HEADER = "X-Workbench-Token"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_auth_state():
    """令牌与宽限窗口起点都**落库**，而 db 是会话级共享 → 会跨用例串味。

    实测形态：某个用例先以"无令牌 + 宽限开着"跑过一次，落库了窗口起点；
    后续用例即便 `grace_seconds()` 设为 24h，`grace_active()` 也会因"窗口起点在很久以前"
    而返回 False → 本该 200 的变成 401。这类失败看着像鉴权坏了，其实是测试互相污染。

    另外删掉令牌，保证 `get_token()` 真的走"生成并落库"那条路径。
    """
    from workbench import db

    auth.reset_auth_gap_for_tests()
    for key in (auth.GRACE_STARTED_KEY, auth.TOKEN_SETTING_KEY):
        db.execute("DELETE FROM settings WHERE key = ?", (key,))
    yield
    auth.reset_auth_gap_for_tests()
    for key in (auth.GRACE_STARTED_KEY, auth.TOKEN_SETTING_KEY):
        db.execute("DELETE FROM settings WHERE key = ?", (key,))


@pytest.fixture()
def token() -> str:
    return auth.get_token()


@pytest.fixture()
def strict(monkeypatch: pytest.MonkeyPatch) -> None:
    """关闭宽限期 = 严格模式。

    用 monkeypatch 而不是环境变量：`grace_seconds()` 每次调用都读环境变量，
    但 `grace_active()` 还要看落库的窗口起点，两处都要可控才叫"确定性"。
    """
    monkeypatch.setattr(auth, "grace_seconds", lambda: 0)
    monkeypatch.setattr(auth, "grace_active", lambda now=None: False)


@pytest.fixture()
def grace_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "grace_seconds", lambda: 3600)
    monkeypatch.setattr(auth, "grace_active", lambda now=None: True)


# --------------------------------------------------------------- 令牌本体


def test_token_is_stable_across_calls(token: str) -> None:
    """令牌必须**跨重启稳定**（落 settings 表）。

    若每次进程启动都换令牌，装在用户 Chrome 里的扩展会拿旧令牌狂刷 401——
    那看起来像"扩展坏了"，而用户唯一的出路是重新加载扩展。
    """
    assert token and len(token) == auth.TOKEN_BYTES * 2  # hex
    assert auth.get_token() == token


def test_token_is_not_revealed_in_describe(token: str) -> None:
    """`describe()` 是给体检/自检用的，**不得**包含令牌本身。"""
    described = auth.describe()
    assert "token" not in described or described.get("token") is None
    assert token not in str(described)


def test_describe_reports_enforcement_state() -> None:
    described = auth.describe()
    assert described["authRequired"] is True
    assert described["guardedPrefix"] == "/extension/"
    assert "/extension/hello" in described["exemptPaths"]


# --------------------------------------------------------------- 豁免面最小


def test_exempt_surface_is_minimal() -> None:
    """**豁免清单必须只有两条。** 这条红 = 有人扩大了无鉴权面，先问为什么。

    每个豁免都是"任何本机进程都能无令牌访问"的端点，必须能说清它为何不含敏感数据。
    """
    assert auth.EXEMPT_PATHS == {"/extension/hello", "/extension/health"}, (
        f"豁免面被扩大到 {sorted(auth.EXEMPT_PATHS)}——新增豁免前请读 extension_auth 的模块文档串"
    )


def test_guarded_prefix_covers_whole_namespace() -> None:
    """`/extension/` 整段被守（不是逐条路由列表）——"新增路由自动被守住"是结构保证。"""
    assert auth.GUARDED_PREFIX == "/extension/"
    assert auth.is_guarded("/extension/snapshot") is True
    assert auth.is_guarded("/extension/whatever-added-later") is True
    assert auth.is_guarded("/extension/hello") is False
    assert auth.is_guarded("/extension/health") is False
    assert auth.is_guarded("/api/accounts") is False  # 别的命名空间不受影响


def test_extension_blueprint_has_no_ui_only_routes() -> None:
    """`/extension/*` 下不得再有**给桌面 UI 用、且不碰凭据**的端点。

    那类端点拿不到令牌（令牌只发给扩展），只能靠开豁免——而豁免一开，
    "整个前缀需要令牌"就退化成一份人工维护的清单，正是本仓最忌讳的失效方式。
    它们已搬到 `/api/shell/*`（`routes_shell.py`）。
    """
    from workbench.api import routes_shell

    shell_paths = {r.path for r in routes_shell.router.routes}
    assert "/api/shell/launch-chrome" in shell_paths
    assert "/api/shell/setup-helper" in shell_paths
    assert "/api/shell/update-state" in shell_paths

    ext_paths = {r.path for r in ext.router.routes}
    for moved in ("/extension/launch-chrome", "/extension/setup-helper", "/extension/update-state"):
        assert moved not in ext_paths, f"{moved} 不应再挂在扩展蓝图下"


# --------------------------------------------------------------- 严格模式真拦


@pytest.mark.parametrize("path", ["/extension/snapshot", "/extension/commands", "/extension/state"])
def test_strict_mode_rejects_get_without_token(client: TestClient, strict: None, path: str) -> None:
    assert client.get(path).status_code == 401


def test_strict_mode_rejects_post_without_token(client: TestClient, strict: None) -> None:
    assert client.post("/extension/ack", json={"seqs": [1]}).status_code == 401


def test_strict_mode_rejects_wrong_token(client: TestClient, strict: None) -> None:
    assert client.get("/extension/snapshot", headers={TOKEN_HEADER: "not-the-token"}).status_code == 401


def test_correct_token_is_accepted(client: TestClient, strict: None, token: str) -> None:
    resp = client.get("/extension/snapshot", headers={TOKEN_HEADER: token})
    assert resp.status_code == 200
    assert resp.json()["format"] == "workbench-snapshot"


def test_exempt_paths_stay_open_in_strict_mode(client: TestClient, strict: None) -> None:
    """自举端点与健康判据在严格模式下仍免令牌——否则扩展永远拿不到令牌（死锁）。"""
    assert client.get("/extension/hello").status_code == 200
    assert client.get("/extension/health").status_code == 200


def test_other_namespaces_are_not_gated(client: TestClient, strict: None) -> None:
    """令牌只守 `/extension/*`：桌面 UI 自己的端点不受影响（改了会当场打断整个界面）。"""
    assert client.get("/api/health").status_code == 200


# --------------------------------------------------------------- 宽限期语义


def test_grace_period_allows_old_extension(client: TestClient, grace_on: None) -> None:
    """宽限期内不带令牌照常放行。

    这是**刻意**的：令牌是新协议要求，已装在用户 Chrome 里的**旧扩展不会带令牌**。
    硬失败会表现为"快捷登录静默失灵、且没有任何提示"——正是本仓反复踩的那类静默故障。
    """
    assert client.get("/extension/snapshot").status_code == 200


def test_grace_start_marker_is_persisted_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """窗口起点**只记一次**且落库：进程重启不重置，否则等于永久开窗。"""
    from workbench.services.storage import set_setting

    set_setting(auth.GRACE_STARTED_KEY, str(1700000000))
    monkeypatch.setattr(auth, "grace_seconds", lambda: 100)
    assert auth.grace_started_at() == 1700000000
    # 窗口早过了：不应被"重新开始计时"
    auth.start_grace_if_needed()
    assert auth.grace_started_at() == 1700000000
    assert auth.grace_active(now=1700000000 + 101) is False
    assert auth.grace_active(now=1700000000 + 99) is True


def test_grace_zero_disables_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "grace_seconds", lambda: 0)
    assert auth.grace_active() is False


# --------------------------------------------------------------- 401 的可用性契约


def test_401_body_carries_machine_readable_code(client: TestClient, strict: None) -> None:
    """**401 必须带 code**：扩展端据此"重新握手并重试一次"。

    少了它，401 会被扩展的 catch 当普通网络错误吞掉 → 用户看到的是
    "快捷登录莫名失灵"，而不是"扩展需要重新加载"（静默故障）。
    """
    body = client.get("/extension/snapshot").json()
    assert body["code"] == auth.AUTH_ERROR_CODE
    assert "code" in body and body["hello"] == "/extension/hello"
    assert body["action"], "必须给出人话处置（点一次『重新加载』）"
    assert "detail" in body


def test_rejection_codes_distinguish_missing_from_bad() -> None:
    """`missing` 与 `bad` 要分开：前者是"扩展没升级"，后者是"令牌被换了"，处置不同。"""
    assert auth.rejection_body("missing_token")["detail"] != auth.rejection_body("bad_token")["detail"]
    assert auth.rejection_body("bad_origin")["detail"]


# --------------------------------------------------------------- 自举端点内容最小


def test_hello_returns_only_bootstrap_fields(client: TestClient) -> None:
    """自举端点是**唯一**免令牌的实质端点，必须保持最小。

    它多一个字段，就等于把 R1 重新打开一部分——所以这里按字段集合断言，
    而不是只看"有没有 token"。
    """
    data = client.get("/extension/hello").json()
    assert set(data) == {"format", "version", "token", "desktopVersion", "header"}
    assert data["format"] == "workbench-extension-hello"
    assert data["header"].lower() == auth.TOKEN_HEADER
    # 绝不含账号/凭据字段
    assert not {"accounts", "boxes", "fernetKey", "sites"} & set(data)


def test_hello_token_matches_header_expectation(client: TestClient, strict: None) -> None:
    """走一遍扩展的真实自举流程：hello 拿令牌 → 用该令牌访问受保护端点。"""
    tok = client.get("/extension/hello").json()["token"]
    assert client.get("/extension/snapshot", headers={TOKEN_HEADER: tok}).status_code == 200
    # 而它确实是**必需**的
    assert client.get("/extension/snapshot").status_code == 401


# --------------------------------------------------------------- 来源允许清单


def test_origin_allowlist_empty_means_no_origin_check(monkeypatch: pytest.MonkeyPatch, strict: None) -> None:
    """默认不配 allowlist = 不做来源校验（桌面壳目前不声明扩展 id）。

    **如实记录**：来源校验是可选的第二层，不是主防线；主防线是令牌。
    """
    monkeypatch.delenv("WORKBENCH_EXTENSION_ORIGIN", raising=False)
    assert auth.origin_allowed(None) is True
    assert auth.origin_allowed("https://evil.example.com") is True  # 未配置时不拦


def test_origin_allowlist_blocks_when_configured(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, strict: None, token: str
) -> None:
    monkeypatch.setenv("WORKBENCH_EXTENSION_ORIGIN", "chrome-extension://abcdefghijklmnop")
    ok = "chrome-extension://abcdefghijklmnop"
    assert client.get("/extension/snapshot", headers={TOKEN_HEADER: token, "Origin": ok}).status_code == 200
    bad = client.get(
        "/extension/snapshot",
        headers={TOKEN_HEADER: token, "Origin": "https://evil.example.com"},
    )
    assert bad.status_code == 401
    assert bad.json()["code"] == auth.AUTH_ERROR_CODE
    # 该端点本身也不能被当作"来源校验的旁路"
    assert client.get("/extension/snapshot", headers={TOKEN_HEADER: token}).status_code == 401


# --------------------------------------------------- 静默故障可见性（防"用户毫无线索"）


def test_tokenless_access_becomes_visible_in_health(client: TestClient, grace_on: None) -> None:
    """**这条是"防静默故障"的关键**：旧扩展不带令牌访问时，桌面 UI 必须能知道。

    grace 窗口过后，没重新加载扩展的用户会开始收到 401；而扩展侧对失败是**静默吞掉**的
    （`sync.ts` 的离线回退：桌面不可达是常态）→ 用户只会看到"快捷登录不灵了"，
    没有任何线索指向"去点一次『重新加载』"。
    故中间件把"无令牌访问"记下来，经**免鉴权的** `/extension/health` 告诉桌面 UI。
    """
    assert client.get("/extension/health").json()["authBlocked"] is False
    client.get("/extension/snapshot")  # 旧扩展的典型行为：不带令牌
    health = client.get("/extension/health").json()
    assert health["authBlocked"] is True
    assert health["tokenlessRequests"] >= 1


def test_health_itself_does_not_count_as_a_tokenless_gap(client: TestClient, grace_on: None) -> None:
    """`/extension/health` 是免鉴权的徽标判据，桌面 UI 每 3s 打一次。

    若它被算进 gap，`authBlocked` 会被 UI 自己的轮询永久点亮、提示条再也关不掉。
    """
    for _ in range(3):
        client.get("/extension/health")
    assert client.get("/extension/health").json()["tokenlessRequests"] == 0


def test_no_gap_when_extension_uses_token(client: TestClient, strict: None, token: str) -> None:
    """新扩展会自动自举令牌——它不该被误报成"需要重新加载"。"""
    client.get("/extension/snapshot", headers={TOKEN_HEADER: token})
    assert client.get("/extension/health").json()["authBlocked"] is False
