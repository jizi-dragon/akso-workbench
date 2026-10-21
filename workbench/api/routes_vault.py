"""云端账号库的登录与状态端点（设备授权）。

## 为什么把设备流的"两拍"拆成两个端点

```
POST /api/vault/login/start   -> 开局，拿 deviceCode + verificationUrl
   （界面拿 verificationUrl 去开系统浏览器）
POST /api/vault/login/poll    -> 问"批准了吗"
   （界面按服务端给的 interval 轮询，能取消、能显示"等待中…"）
```

合成一个端点就得在里面 `sleep` 轮询到成功或超时 —— 那样**界面在整段时间里
什么都做不了**（不能显示进度、不能取消），而且一个长连接会占着工作线程。

★ `deviceCode` **只在两次调用之间由前端持有**，桌面端**不落库、不入日志**。
它是换令牌的凭据；落库就等于在磁盘上多躺一份会话，而本仓已经有
"凭据不落明文"这条纪律（规则 19）—— 不落盘比加密落盘更干净。

★ 所有对云端的调用都是**同步阻塞**的（`requests`/`urllib`），所以这里每个
`async def` 里的调用都走 `asyncio.to_thread`。不这么做的话，表现是
**整个服务卡住**而不是报错 —— 这正是本仓不变量 I11 要防的，而它有结构化守卫
（`tests/test_invariant_i11.py` 的规则 S：`async def` 里的嵌套同步 `def`
不得被直接调用）。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import vault

router = APIRouter(prefix="/api/vault", tags=["vault"])


class PollBody(BaseModel):
    #: 开局时拿到的长随机串。**逐字段显式建模**（规则 21）：漏字段要 422，
     #: 而不是静默地把 `None` 当成 deviceCode 发出去换回一个看不懂的 400。
    deviceCode: str = Field(min_length=8, max_length=128)


class StartBody(BaseModel):
    clientName: str = Field(default="桌面工作台", max_length=60)


@router.get("/status")
async def vault_status() -> dict:
    """当前有没有登录、登的是谁、连的哪个地址。

    ★ **不打网络**（见 `vault.status` 的注释）：这个端点会被页面频繁调用，
    每次验证令牌就是每次一次网络往返，而断网时它会**卡住首屏**。
    """
    return await asyncio.to_thread(vault.status)


@router.post("/login/start")
async def vault_login_start(body: StartBody | None = None) -> dict:
    """设备流第一拍。返回的 `verificationUrl` 交给界面去开系统浏览器。"""
    name = (body.clientName if body else "") or "桌面工作台"
    try:
        return await asyncio.to_thread(vault.device_start, name)
    except vault.VaultError as exc:
        # 502：**上游**不可用，不是本服务的错。用 500 会让排查的人先来查这里。
        raise HTTPException(502, str(exc)) from exc


@router.post("/login/poll")
async def vault_login_poll(body: PollBody) -> dict:
    """设备流第二拍。批准的那一刻**顺手把会话存下来**（加密）。

    ★ 存会话放在**服务端这一侧**而不是让前端再调一次接口：
    前端多一次调用就多一个"拿到了令牌但没存上"的窗口，而那个窗口里的表现是
    "授权成功了，但界面还是没登录" —— 用户只会再点一次，于是多出一个会话。
    """
    try:
        resp = await asyncio.to_thread(vault.device_poll, body.deviceCode)
    except vault.VaultError as exc:
        raise HTTPException(502, str(exc)) from exc
    if resp.get("status") == "approved":
        await asyncio.to_thread(vault.save_session, resp["token"], resp.get("user") or {})
        # ★ 令牌只留在本机（Fernet 密文）；**不能回给浏览器**。
        # 前端只需要知道 approved，拿到 token 只会多一个泄漏面。
        resp = {key: value for key, value in resp.items() if key != "token"}
    return resp


@router.post("/refresh")
async def vault_refresh() -> dict:
    """用当前会话拉一次最新个人信息，同时**验证会话还有效**。"""
    try:
        prof = await asyncio.to_thread(vault.fetch_profile)
    except vault.VaultError as exc:
        raise HTTPException(401, str(exc)) from exc
    return {"profile": prof}


@router.get("/library")
async def vault_library(refresh: bool = False) -> dict:
    """云端账号库的**只读展示视图**（脱敏、5 分钟缓存、断网回落）。

    - `refresh=true` 绕过缓存（页面上的「同步数据」）；
    - 断网时若本地有缓存，仍返回 200，但带 `stale=true` + `error`；
    - 会话失效返回 **401**（不回落旧数据 —— 那会假装还登录着）。
    """
    try:
        data = await asyncio.to_thread(vault.library, refresh)
    except vault.VaultError as exc:
        raise HTTPException(401 if vault.is_auth_error(exc) else 502, str(exc)) from exc
    data["profile"] = await asyncio.to_thread(vault.profile)
    data["baseUrl"] = await asyncio.to_thread(vault.base_url)
    return data


@router.post("/push")
async def vault_push() -> dict:
    """把本机账号库并上云端（仅在已登录时可用）。

    云端契约见 akso-cloud `docs/SYNC-CONTRACT.md` §4–§5：
    带 `baseRevision` 的 merge；409 时不重试，提示用户先刷新再推。
    """
    try:
        return await asyncio.to_thread(vault.push_local)
    except vault.VaultError as exc:
        raise HTTPException(409 if exc.status == 409 else 502, str(exc)) from exc


@router.post("/logout")
async def vault_logout() -> dict:
    """退出。先让服务端吊销，再清本地（顺序见 `vault.logout` 的注释）。"""
    await asyncio.to_thread(vault.logout)
    return {"ok": True}
