"""`/extension/*` 的访问令牌（P0-1：关闭"本机任意进程一次 GET 拿到全部账号明文"）。

## 这个模块关闭的是什么

`GET /extension/snapshot` 会**同时**下发 Fernet 密钥与全部密文凭据（`routes_extension.py`），
而整条 `/extension/*` 此前**没有任何鉴权**——CODEBASE_OVERVIEW 的 **R1**。
一次 GET 即等价于"本机全部测试账号的明文口令"，这是全仓唯一的"拿到即等于拿到口令"单点。

## 分层设计（各层挡住什么，写清楚免得高估）

| 层 | 挡住 | **挡不住** |
|---|---|---|
| 令牌头 `X-Workbench-Token`（必需） | 任何**不知道令牌**的本机 HTTP 客户端；以及**浏览器里的网页**——自定义头会触发 CORS 预检，服务端不授予跨源许可，浏览器**根本不会发出**那个请求 | 能读 `%APPDATA%\\Workbench\\workbench.db` 的本机进程（令牌就在 `settings` 表里，与 Fernet 密钥同处） |
| `Origin` 允许清单（配置后生效） | 伪造非扩展来源的请求 | 本机进程可以任意伪造 `Origin` 头 |
| 备用窗口 `grace`（默认 24h） | —— | **刻意留的**：桌面升级后、用户还没点"重新加载扩展"之前，不带令牌也能用（见下） |

> ⚠ **必须说清楚的真相**：本机同用户进程**总能**拿到令牌——它要么直接读库，
> 要么调一次 `POST /extension/hello`（该端点必须免鉴权，否则扩展无法自举）。
> 所以令牌**不是**"防住本机攻击者"的边界，它把攻击面从
> **"任何能发 HTTP 的东西"**（网页、其他应用、随手 curl、被 NSIS 之外的安装器塞进来的脚本）
> 收窄到 **"愿意读我们数据库或调我们自举端点的本机进程"**——后者与直接读库同级，
> 而那一步**本来就能拿到 Fernet 密钥**。结论：令牌消灭的是"零知识的一次 GET"，
> 不是"本机已被攻陷"。**不要**把它写成"凭据现在安全了"。
> （同族判断见 PITFALLS #23：Fernet 密文挡的是网络窃听，不是本机实例串号。）

## 为什么需要 grace 窗口（而不是一上来就硬失败）

令牌是**新加的协议要求**。已装在用户 Chrome 里的**旧扩展不会带令牌**，
若立刻硬失败，表现为"快捷登录静默失灵、且没有任何提示"——正是本仓反复踩的那类静默故障。
故：**桌面更新后 `grace` 小时内**，无令牌请求照常放行并**留一条 warning**（提示用户重新加载扩展）；
窗口过后严格拒绝。窗口起点落库（`settings` 表），**不是**进程启动时刻——
否则每次重启桌面都会重新开窗，等于永久开着。

## 与 ADR-0013 的关系

这是**协议标识**变更：写出端（服务端）发新的 `token`/`authRequired` 字段，
读取端（扩展）**兼容两种**——拿不到 `token` 字段（旧桌面端）就不带令牌继续跑。
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
from typing import Any

logger = logging.getLogger("workbench.extension_auth")

#: 令牌请求头。**不叫 Authorization**：本机回环上还有别的客户端（工具脚本、前端页），
#: 用一个自有名字更醒目，也避免与将来的真实鉴权方案撞名。
TOKEN_HEADER = "x-workbench-token"

#: 401 的机器可读错误码：扩展端据此判定"该重新握手了"，而不是把它当普通网络错误吞掉。
AUTH_ERROR_CODE = "extension_auth_required"

#: 需要令牌的路径前缀（本模块只守这里，见模块文档串"分层设计"）
GUARDED_PREFIX = "/extension/"

#: 自举端点：扩展先调它拿令牌，才能访问需要令牌的端点（由中间件就地应答）
HELLO_PATH = "/extension/hello"

#: 免鉴权路径（**刻意**的例外，新增前请先读模块文档串）
#: - `/extension/hello`：自举端点，扩展必须能免令牌调到它，否则死锁；
#:   它只回令牌与版本号，**不含任何账号数据**（见 `hello_payload()`）。
#: - `/extension/health`：桌面 UI 的"扩展在不在"判据（四态徽标 + 安装引导闩锁），
#:   无账号数据、无指令面；桌面 UI 与工具脚本都依赖它。
EXEMPT_PATHS = frozenset({HELLO_PATH, "/extension/health"})

#: 令牌在 settings 表的键
TOKEN_SETTING_KEY = "extension_access_token"
#: grace 窗口起点（epoch 秒，字符串存表）
GRACE_STARTED_KEY = "extension_auth_grace_started_at"

#: 令牌熵（字节 → hex 长度 64）。与 Fernet 密钥同量级。
TOKEN_BYTES = 32

DEFAULT_GRACE_SECONDS = 24 * 3600


def grace_seconds() -> int:
    """grace 窗口长度（秒）。`WORKBENCH_EXTENSION_AUTH_GRACE=0` 可关闭窗口（严格模式）。

    刻意做成"可配"而不是写死：现场排障时"扩展连不上"需要一个立刻可达的严格模式来分诊，
    而平时需要宽限。0 = 严格。
    """
    raw = os.environ.get("WORKBENCH_EXTENSION_AUTH_GRACE")
    if raw is None:
        return DEFAULT_GRACE_SECONDS
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        logger.warning("WORKBENCH_EXTENSION_AUTH_GRACE 不是整数（%r），按默认 %ds", raw, DEFAULT_GRACE_SECONDS)
        return DEFAULT_GRACE_SECONDS


def allowed_origins() -> list[str]:
    """`Origin` 允许清单（逗号分隔，可为空 = 不做来源校验）。

    `WORKBENCH_EXTENSION_ORIGIN=chrome-extension://abcdef...` 时**强制**来源匹配。
    桌面壳目前不声明扩展 id（用户手装的扩展 id 由 manifest 的公钥决定、跨机一致但不在本仓配置里），
    故默认留空——**只做令牌校验**，并在模块文档串里如实写明"来源校验是可选的第二层"。
    """
    raw = os.environ.get("WORKBENCH_EXTENSION_ORIGIN") or ""
    return [o.strip() for o in raw.split(",") if o.strip()]


# ---------------------------------------------------------------- 令牌本体


def get_token() -> str:
    """读令牌；不存在则**生成并落库**（幂等）。

    为什么落库而不是进程内存态：它必须**跨重启稳定**——否则每次桌面重启，
    装在用户 Chrome 里的扩展就拿着过期令牌狂刷 401，而那看起来像"扩展坏了"。
    """
    from ..services.storage import get_setting, set_setting

    token = get_setting(TOKEN_SETTING_KEY)
    if token:
        return token
    token = secrets.token_hex(TOKEN_BYTES)
    set_setting(TOKEN_SETTING_KEY, token)
    logger.info("已生成 /extension/* 访问令牌（落 settings.%s，跨重启稳定）", TOKEN_SETTING_KEY)
    return token


def token_matches(candidate: str | None) -> bool:
    """常量时间比较（避免按字节比较泄漏前缀）。"""
    if not candidate:
        return False
    return secrets.compare_digest(candidate, get_token())


def origin_allowed(origin: str | None) -> bool:
    """来源校验：**清单为空 = 不做校验**（返回 True，见 `allowed_origins()` 的说明）。"""
    allow = allowed_origins()
    if not allow:
        return True
    return bool(origin) and origin in allow


# ---------------------------------------------------------------- grace 窗口


def grace_started_at() -> int | None:
    from ..services.storage import get_setting

    raw = get_setting(GRACE_STARTED_KEY)
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


def start_grace_if_needed() -> int | None:
    """首次放行时记下窗口起点并返回它；窗口已开过则返回既有起点。

    起点**只记一次**：进程重启不重置（否则等于永久开着窗口）。
    """
    from ..services.storage import set_setting

    started = grace_started_at()
    if started is None:
        started = int(time.time())
        set_setting(GRACE_STARTED_KEY, str(started))
        window = grace_seconds()
        logger.warning(
            "/extension/* 访问令牌已启用，但给旧扩展留了 %d 小时宽限（至 %s）——"
            "请在桌面端「账号中心」按提示点一次扩展卡片上的『重新加载』。",
            window // 3600,
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started + window)),
        )
    return started


def grace_active(now: int | None = None) -> bool:
    window = grace_seconds()
    if window <= 0:
        return False
    started = grace_started_at()
    if started is None:
        # 还没放过任何请求：此刻开始计时算"激活中"，由 start_grace_if_needed 落库
        return True
    return (now if now is not None else int(time.time())) - started < window


def grace_remaining_seconds(now: int | None = None) -> int:
    window = grace_seconds()
    if window <= 0:
        return 0
    started = grace_started_at() or (now if now is not None else int(time.time()))
    return max(0, window - ((now if now is not None else int(time.time())) - started))


# ---------------------------------------------------------------- 判定（纯函数 + 一处副作用）


def is_guarded(path: str) -> bool:
    """该路径是否需要令牌。"""
    if not path.startswith(GUARDED_PREFIX):
        return False
    return path not in EXEMPT_PATHS


def check_request(path: str, token: str | None, origin: str | None) -> tuple[bool, str]:
    """核心判定。返回 `(是否放行, 拒绝原因)`——**纯函数**（宽限期的落库在调用方），便于单测。

    拒绝原因取值：`""`（放行）/ `bad_origin` / `missing_token` / `bad_token`。
    `""` 有两种语义：**令牌正确**（`token_ok=True`）或**靠宽限放行**——
    调用方用 `token_matches(token)` 自行区分（中间件靠它决定要不要开窗）。
    """
    if not is_guarded(path):
        return True, ""
    if not origin_allowed(origin):
        return False, "bad_origin"
    if token_matches(token):
        return True, ""
    # 没带（或带错）令牌：宽限期内放行，否则拒绝
    if grace_active():
        return True, ""
    return False, ("missing_token" if not token else "bad_token")


def _start_grace_once() -> None:
    """只在**确实有请求要放行**时落库窗口起点（纯只读的体检不该开窗）。"""
    if grace_seconds() > 0 and grace_started_at() is None:
        start_grace_if_needed()


def rejection_body(reason: str) -> dict[str, Any]:
    """401 响应体。**含机器可读 code**：扩展端据此决定"重新握手并重试"，
    而不是把它当普通网络故障静默吞掉（那会表现为"快捷登录莫名失灵"）。"""
    hint = {
        "missing_token": "请求缺少访问令牌——扩展可能未重新加载",
        "bad_token": "访问令牌不匹配——桌面端可能已轮换令牌",
        "bad_origin": "来源不被允许（WORKBENCH_EXTENSION_ORIGIN 允许清单）",
    }.get(reason, "未知原因")
    return {
        "detail": f"/extension/* 需要访问令牌：{hint}",
        "code": AUTH_ERROR_CODE,
        "hello": "/extension/hello",
        "action": "在桌面端「账号中心」按提示点一次扩展卡片上的『重新加载』",
    }


def describe() -> dict[str, Any]:
    """给体检/自检用的**不含令牌本身**的状态描述。"""
    return {
        "authRequired": True,
        "guardedPrefix": GUARDED_PREFIX,
        "exemptPaths": sorted(EXEMPT_PATHS),
        "originAllowlist": allowed_origins(),
        "graceSeconds": grace_seconds(),
        "graceActive": grace_active(),
        "graceRemainingSeconds": grace_remaining_seconds(),
        "tokenConfigured": bool(config_has_token()),
    }


# ---------------------------------------------------------------- 无令牌访问的可见性
#
# ## 为什么需要它（这条是"防静默故障"的最后一环）
#
# grace 窗口过后，**没重新加载扩展**的用户会开始收到 401。而扩展侧对失败是**静默吞掉**的
# （`sync.ts` 的离线回退：桌面不可达是常态）——于是用户看到的是"快捷登录不灵了"，
# 却没有任何线索指向"去点一次『重新加载』"。这正是本仓最忌讳的一类故障。
#
# 所以把"有人在不带令牌敲门"记下来，并经**免鉴权的 `/extension/health`** 暴露给桌面 UI，
# 由账号中心复用既有的「扩展版本过旧」提示条——文案改成"需要重新加载"。
_auth_gap: dict[str, Any] = {"count": 0, "at": 0.0, "lastPath": ""}


def report_auth_gap(path: str) -> None:
    """记一次"请求没带（或带错）令牌"。由中间件在放行/拒绝两条路径上都调用。"""
    import time as _time

    _auth_gap["count"] = int(_auth_gap["count"]) + 1
    _auth_gap["at"] = _time.time()
    _auth_gap["lastPath"] = path


def auth_gap_stats() -> dict[str, Any]:
    """无令牌访问的累计信息。`active=True` = 桌面 UI 该提示用户重新加载扩展了。"""
    return {
        "tokenlessRequests": int(_auth_gap["count"]),
        "lastAt": int(float(_auth_gap["at"]) * 1000) if _auth_gap["at"] else 0,
        "lastPath": str(_auth_gap["lastPath"]),
        "active": bool(_auth_gap["count"]),
    }


def reset_auth_gap_for_tests() -> None:
    """清计数（仅供测试：模块级内存态在用例间会串）。"""
    _auth_gap.update({"count": 0, "at": 0.0, "lastPath": ""})


def config_has_token() -> bool:
    from ..services.storage import get_setting

    return bool(get_setting(TOKEN_SETTING_KEY))


# ---------------------------------------------------------------- ASGI 中间件


def hello_payload() -> dict[str, Any]:
    """自举端点响应：**只回令牌与版本号，不含任何账号数据**。

    这是整条链路上**唯一**放行无令牌请求的实质端点（另一个豁免是 `/health`，那是个只读徽标判据）。
    它的存在是自举的必然：扩展必须先拿到令牌，才能访问需要令牌的端点。
    因此它**必须**保持最小——任何人往这里加一个便利字段，都等于把 R1 重新打开一部分。
    """
    from .. import __version__

    return {
        "format": "workbench-extension-hello",
        "version": 1,
        "token": get_token(),
        "desktopVersion": __version__,
        "header": TOKEN_HEADER,
    }


class ExtensionAuthMiddleware:
    """守 `/extension/*` 的纯 ASGI 中间件。

    **为什么用中间件而不是 route 的 `Depends`**：FastAPI 的依赖是**逐路由**声明的，
    漏加一条就静默开一个口子（而"新增路由忘了加依赖"正是这类防护最常见的失效方式）。
    中间件按**路径前缀**统一判定，"新加的路由自动被守住"是结构保证。
    代价：豁免路径集中在 `EXEMPT_PATHS` 一处，新增豁免必须显式登记（刻意的）。

    自举端点 `/extension/hello` 在这里就地应答（不进路由表）：它是**鉴权层自己的**
    引导入口，放在被它保护的蓝图里会让"谁守这条路径"变得难以追。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path == HELLO_PATH:
            await self._send_json(send, 200, hello_payload())
            return
        if not is_guarded(path):
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        token = headers.get(TOKEN_HEADER)
        origin = headers.get("origin")
        ok, reason = check_request(path, token, origin)
        # 走到这里 `path` 必然是被守的（豁免路径在上面 `is_guarded` 那一步就返回了），
        # 所以下面的 gap 统计**只**记录"本该带令牌的端点被无令牌访问"——
        # 那才是"扩展没重新加载"的证据。若把豁免路径也算进来，
        # `authBlocked` 会被桌面 UI 自己每 3s 一次的 `/extension/health` 轮询永久点亮，
        # 提示条再也关不掉。
        if ok:
            if not token_matches(token):
                report_auth_gap(path)
                _start_grace_once()
            await self.app(scope, receive, send)
            return

        report_auth_gap(path)
        logger.warning(
            "拒绝无令牌访问 %s（原因=%s，来源=%s）——若这是扩展在跑，请在账号中心点一次『重新加载』",
            path, reason, origin or "(无 Origin 头)",
        )
        await self._send_json(send, 401, rejection_body(reason))

    @staticmethod
    async def _send_json(send: Any, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                # 明确不授予跨源读取：网页侧的预检也拿不到许可（双保险）
                (b"cache-control", b"no-store"),
            ],
        })
        await send({"type": "http.response.body", "body": body})
