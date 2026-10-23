"""快捷登录插件：账号库 + 平台登录验证（quick-login 能力内化后的形态）。

## 2026-10-19：托管浏览器整体下线

用户确认未来不再需要 UI 自动登录 / 页面抓取 / 截图。`browser_pool`、`autologin`、
Monitor 与 `accounts_browser_*` 工具已删除；本插件只保留两条**不碰浏览器**的能力：

| 工具 | 走哪条通道 | 什么时候用 |
|---|---|---|
| `accounts_list` | 云端账号库（已登录）/ 本地 SQLite | 查账号清单（永不含口令） |
| `accounts_login` | 原生 httpx（`egmp.client.login`） | 确认"这个环境的账号还能不能连上"、预热 token；云端账号 id 也可用 |

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
    """账号清单（**只有 id/用户名/环境/分配池，永不含口令**）。

    已登录云端时以**云端账号库**为事实源（账号中心页面展示的就是它）；
    未登录才回落到本地 SQLite。这样 agent 看到的清单与用户看到的一致。
    """
    from ..services import accounts as accounts_svc
    from ..services import vault

    def query() -> tuple[list[dict[str, Any]], str]:
        if vault.status()["loggedIn"]:
            lib = vault.library()
            return [
                {
                    "account_id": r.get("id"),
                    "username": r.get("username"),
                    "env": r.get("siteName"),
                    "base_url": r.get("baseUrl"),
                    "pool": "",
                    "box": r.get("box"),
                    "role": r.get("role") or "",
                    "tags": r.get("tags") or [],
                    "has_password": bool(r.get("hasPassword")),
                    "source": "cloud",
                }
                for r in lib.get("accounts") or []
            ], "cloud"
        return [
            {
                "account_id": r.get("id"),
                "username": r.get("username"),
                "env": r.get("env_name"),
                "base_url": r.get("env_base_url"),
                "pool": r.get("pool"),
                "box": r.get("box"),
                "source": "local",
            }
            for r in accounts_svc.list_accounts()
        ], "local"

    rows, source = await _in_thread(query)
    return json.dumps({"total": len(rows), "source": source, "accounts": rows},
                      ensure_ascii=False, default=str)


async def login(args: LoginArgs, ctx: pa.ToolContext) -> str:
    """原生登录验证（不开浏览器；55min token 缓存）。

    先找本地账号；本地没有且云端已登录时，用云端账号 id 解析凭据。
    密码只在本次调用的内存里出现，**不进入返回值/日志**。
    """
    from ..services import accounts as accounts_svc
    from ..services import vault
    from ..services.egmp.client import login as egmp_login
    from ..services.egmp.kernel.urls import normalize_base_url

    account_id = str(pa.require(args.account_id, "account_id 不能为空")).strip()
    account = await _in_thread(accounts_svc.get_account, account_id)
    password = ""
    if account:
        base_url = normalize_base_url(account.get("env_base_url") or "")
        username = account.get("username") or ""
        env_name = account.get("env_name") or ""
        env_id = str(account.get("env_id") or "")
        if not base_url:
            raise ValueError(f"账号 {username} 所属平台环境未配置 baseUrl（去账号中心补齐）")

        def reveal() -> str:
            return accounts_svc.reveal_password(account_id)

        password = await _in_thread(reveal)
    else:
        if not vault.status()["loggedIn"]:
            raise ValueError(f"账号不存在：{account_id}（用 accounts_list 查可用账号）")
        creds = await _in_thread(vault.cloud_credentials, account_id)
        base_url = normalize_base_url(creds.get("base_url") or "")
        username = creds.get("username") or ""
        env_name = creds.get("env_name") or ""
        env_id = str(creds.get("env_id") or "")
        password = creds.get("password") or ""
        if not base_url:
            raise ValueError(f"云端账号 {username or account_id} 没有可用的平台地址")

    def call() -> str:
        return egmp_login(base_url, username, password, env_id)

    token = await _in_thread(call)
    return json.dumps(
        {
            "ok": True,
            "account_id": account_id,
            "username": username,
            "env": env_name,
            "baseUrl": base_url,
            "tokenHead": (token or "")[:8] + "…",   # **只回前 8 位**：凭据不落对话
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------- 插件声明


PLUGIN = pa.Plugin(
    id=PLUGIN_ID,
    title="快捷登录",
    description="账号库清单与平台登录验证（云端优先、本地回落；不依赖浏览器）",
    version="1.0.0",
    tools=[
        pa.ToolSpec(
            name="accounts_list",
            description="列出账号库的账号（已登录云端时列云端；含 id/用户名/环境，不含口令）",
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
