"""快捷登录插件：账号库 + 托管浏览器会话（quick-login 能力内化后的形态）。

## 两条通道，别混用

| 工具 | 走哪条通道 | 什么时候用 |
|---|---|---|
| `accounts_login` | 原生 httpx（`egmp.client.login`）——**不开浏览器** | 只想确认"这个环境的账号还能不能连上"、预热 token |
| `accounts_browser_open` | 托管 Chromium（`browser_pool`）——**开真窗口** | 要以该身份真的去页面上操作（且五重节奏门控的自动登录会跑） |

用户自己的 Chrome（经 MV3 扩展）**不在这条链路上**：那条是"数据面 → 指令队列 → 扩展执行"，
属于人手动触发的场景（Alt+Q 轮盘）。Agent 能驱动的是内置 Chromium 那条（ADR-0002）。

## 风险分级

- `accounts_browser_open` = WRITE：它会真开一个窗口、写登录态到 `runtime/browser-states/`。
- `accounts_browser_close` = WRITE：会关掉用户正在用的窗口（**可能是他手动开的**），
  所以描述里必须写明这一点，且过确认门。
- 查询类（`accounts_login` 只验证连通）不过门，但它会**联网到真机**，所以仍要显式账号。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from pydantic import BaseModel

from .. import plugin_api as pa

PLUGIN_ID = "accounts"


# ---------------------------------------------------------------- 参数模型


class LoginArgs(BaseModel):
    account_id: str = pa.Arg("统一账号库里的账号 id（不确定就先调 accounts_list）")


class OpenArgs(BaseModel):
    account_id: str = pa.Arg("要以其身份打开平台窗口的账号 id")
    headful: bool = pa.Arg("是否显示可见窗口（默认 true；false = 后台无头）", default=True)


class SessionsArgs(BaseModel):
    account_id: str = pa.Arg("指定账号则只看它的会话；空 = 全部在线会话", default="")


class CloseArgs(BaseModel):
    account_id: str = pa.Arg("要关闭的托管会话所属账号 id")


async def _in_thread(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(lambda: fn(*args, **kwargs))


# ---------------------------------------------------------------- 工具实现


async def list_accounts(_: BaseModel, ctx: pa.ToolContext) -> str:
    """账号清单（**只有 id/用户名/环境/分配池，永不含口令**）。"""
    from ..services import accounts as accounts_svc

    def query() -> list[dict[str, Any]]:
        return [
            {
                "account_id": r.get("id"),
                "username": r.get("username"),
                "env": r.get("env_name"),
                "base_url": r.get("env_base_url"),
                "pool": r.get("pool"),
                "box": r.get("box"),
            }
            for r in accounts_svc.list_accounts()
        ]

    rows = await _in_thread(query)
    return json.dumps({"total": len(rows), "accounts": rows}, ensure_ascii=False, default=str)


async def login(args: LoginArgs, ctx: pa.ToolContext) -> str:
    """原生登录验证（不开浏览器；55min token 缓存）。"""
    from ..services import accounts as accounts_svc
    from ..services.egmp.client import login as egmp_login
    from ..services.egmp.kernel.urls import normalize_base_url

    account_id = str(pa.require(args.account_id, "account_id 不能为空")).strip()
    account = await _in_thread(accounts_svc.get_account, account_id)
    if not account:
        raise ValueError(f"账号不存在：{account_id}（用 accounts_list 查可用账号）")
    base_url = normalize_base_url(account.get("env_base_url") or "")
    if not base_url:
        raise ValueError(f"账号 {account['username']} 所属平台环境未配置 baseUrl（去账号中心补齐）")

    def call() -> str:
        password = accounts_svc.reveal_password(account_id)
        return egmp_login(base_url, account["username"], password, str(account.get("env_id") or ""))

    token = await _in_thread(call)
    return json.dumps(
        {
            "ok": True,
            "account_id": account_id,
            "username": account.get("username"),
            "env": account.get("env_name"),
            "baseUrl": base_url,
            "tokenHead": (token or "")[:8] + "…",   # **只回前 8 位**：凭据不落对话
        },
        ensure_ascii=False,
    )


async def browser_open(args: OpenArgs, ctx: pa.ToolContext) -> str:
    """启动/聚焦托管会话（会真开窗口 + 跑自动登录节奏门控）。"""
    from ..services.browser_pool import BrowserError, get_pool

    account_id = str(pa.require(args.account_id, "account_id 不能为空")).strip()

    def call() -> dict:
        return get_pool().open_account(account_id, headful=args.headful)

    try:
        snapshot = await _in_thread(call)
    except BrowserError as exc:
        raise ValueError(f"托管会话启动失败：{exc}") from exc
    return json.dumps(
        {
            "opened": True,
            "note": "已在内置 Chromium 中以该身份打开；重复调用 = 聚焦其窗口",
            "session": snapshot,
        },
        ensure_ascii=False, default=str,
    )


async def browser_sessions(args: SessionsArgs, ctx: pa.ToolContext) -> str:
    """托管会话状态墙（在线窗口 + 登录态/令牌概要）。"""
    from ..services.browser_pool import get_pool

    account_id = args.account_id.strip()
    if account_id:
        snap = await _in_thread(get_pool().session, account_id)
        return json.dumps({"account_id": account_id, "session": snap}, ensure_ascii=False, default=str)
    return json.dumps({"sessions": await _in_thread(get_pool().list_sessions)},
                      ensure_ascii=False, default=str)


async def browser_close(args: CloseArgs, ctx: pa.ToolContext) -> str:
    """关闭某账号的托管会话（登录态会持久化，下次免密直达）。"""
    from ..services.browser_pool import get_pool

    account_id = str(pa.require(args.account_id, "account_id 不能为空")).strip()
    closed = await _in_thread(get_pool().close_account, account_id)
    return json.dumps(
        {
            "closed": bool(closed),
            "detail": "登录态已持久化，下次免密直达" if closed else "该账号没有活动会话（可能已经关了）",
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------- 插件声明


PLUGIN = pa.Plugin(
    id=PLUGIN_ID,
    title="快捷登录",
    description="账号库清单、平台登录验证、托管浏览器会话（quick-login 能力内化）",
    version="1.0.0",
    tools=[
        pa.ToolSpec(
            name="accounts_list",
            description="列出工作台账号库里的账号（id/用户名/环境/分配池）；不含口令",
            args_model=pa.ZERO_ARGS,
            handler=list_accounts,
            risk=pa.Risk.READ,
            trust=pa.Trust.LOCAL,
            timeout_s=15,
        ),
        pa.ToolSpec(
            name="accounts_login",
            description="用账号库的账号验证平台登录连通并预热 token（httpx 直连，不开浏览器）",
            args_model=LoginArgs,
            handler=login,
            risk=pa.Risk.READ,
            trust=pa.Trust.LOCAL,
            timeout_s=90,
            audit_extra=pa.audit_env_key("account_id"),
        ),
        pa.ToolSpec(
            name="accounts_browser_open",
            description="在内置 Chromium 里以该账号身份打开平台（自动登录 + 会话持久化）；会开真窗口",
            args_model=OpenArgs,
            handler=browser_open,
            risk=pa.Risk.WRITE,
            trust=pa.Trust.LOCAL,
            timeout_s=180,
            audit_extra=pa.audit_env_key("account_id", "headful"),
            # ★ 可撤销性（计划 §8.3）：它**开出一个资源**，反向操作就是关掉它。
            # 配方给的是**具体的**那一条指令（哪个账号的窗口），不是"应该能关掉"。
            reversibility=pa.Reversibility.UNDOABLE,
            undo_recipe=lambda args: {
                "action": "accounts_browser_close",
                "accountId": getattr(args, "account_id", ""),
                "说明": "关掉这次开出来的那个账号窗口（登录态保留）",
            },
        ),
        pa.ToolSpec(
            name="accounts_browser_sessions",
            description="托管浏览器会话状态墙（单账号或全部在线会话）",
            args_model=SessionsArgs,
            handler=browser_sessions,
            risk=pa.Risk.READ,
            trust=pa.Trust.LOCAL,
            timeout_s=20,
            audit_extra=pa.audit_env_key("account_id"),
        ),
        pa.ToolSpec(
            name="accounts_browser_close",
            description=(
                "关闭某账号的托管浏览器窗口（登录态保留）。注意：用户手动开的窗口也会被关掉"
            ),
            args_model=CloseArgs,
            handler=browser_close,
            risk=pa.Risk.WRITE,
            trust=pa.Trust.LOCAL,
            timeout_s=30,
            audit_extra=pa.audit_env_key("account_id"),
            # ★ 可撤销性：**关掉一个我们自己开的资源，没有需要撤的残留** ——
            # 它释放的东西（窗口）随时能再开一个，而且关它**不写平台**
            # （描述里那句"登录态保留"是关键：令牌与会话文件都不动）。
            # ⚠️ 这一档问的是"**有没有需要撤销的痕迹**"，不是"能不能回到一模一样的状态"
            # —— 重新开出来的是**新窗口**，但没有任何东西被留在平台上或文件里。
            reversibility=pa.Reversibility.REVERSIBLE,
        ),
    ],
)


def register(hub: pa.ToolHub) -> None:
    hub.add_plugin(PLUGIN)
