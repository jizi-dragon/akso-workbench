"""快捷登录插件：账号库 + 平台登录验证（quick-login 能力内化后的形态）。

## 2026-10-19：托管浏览器整体下线

用户确认未来不再需要 UI 自动登录 / 页面抓取 / 截图。`browser_pool`、`autologin`、
Monitor 与 `accounts_browser_*` 工具已删除；本插件只保留两条**不碰浏览器**的能力：

| 工具 | 走哪条通道 | 什么时候用 |
|---|---|---|
| `accounts_list` | 本地 SQLite | 查账号清单（永不含口令） |
| `accounts_login` | 原生 httpx（`egmp.client.login`） | 确认"这个环境的账号还能不能连上"、预热 token |

## 风险分级

- `accounts_login` 只验证连通，不写平台；但它会**联网到真机**，所以仍要显式账号。
- 账号凭据仍由 `services/accounts.py` 的密文 + 本机密钥保管，不因删浏览器而改变。
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


# ---------------------------------------------------------------- 插件声明


PLUGIN = pa.Plugin(
    id=PLUGIN_ID,
    title="快捷登录",
    description="账号库清单与平台登录验证（quick-login 能力内化；不依赖浏览器）",
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
    ],
)


def register(hub: pa.ToolHub) -> None:
    hub.add_plugin(PLUGIN)
