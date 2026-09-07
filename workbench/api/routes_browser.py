"""托管浏览器路由（阶段 2B）：一键启动账号浏览器 + 自动登录 + 状态墙。

知识迁移映射：quick-login navigation.ts（打开直达 URL）+ parallel-session.ts
open() 的 URL 决策（有登录态直达首页，无登录态进 /login 自动填充）。
tab-title.ts 的标题改写不迁 —— 标题由状态墙轮询 page.title() 呈现。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import browser_pool
from ..services.browser_pool import BrowserError, get_pool

router = APIRouter(prefix="/api/browser", tags=["browser"])


class OpenIn(BaseModel):
    account_id: str = Field(..., min_length=1)
    headful: bool = Field(False, description="有头模式（真窗口调试用）")


@router.post("/open")
def open_account(body: OpenIn) -> dict:
    """一键启动托管浏览器：建 context → 自动登录（节奏门控）→ 会话快照。"""
    try:
        return get_pool().open_account(body.account_id, headful=body.headful)
    except BrowserError as exc:
        code = 404 if "账号不存在" in str(exc) else 503
        raise HTTPException(code, str(exc)) from exc


@router.post("/close/{account_id}")
def close_account(account_id: str) -> dict:
    if not get_pool().close_account(account_id):
        return {"closed": False, "detail": "该账号无活动会话"}
    return {"closed": True}


@router.get("/sessions")
def list_sessions() -> dict:
    return {"sessions": get_pool().list_sessions()}


@router.get("/sessions/{account_id}")
def session_detail(account_id: str) -> dict:
    snap = get_pool().session(account_id)
    if snap is None:
        raise HTTPException(404, f"该账号无活动会话：{account_id}")
    return snap


@router.get("/sessions/{account_id}/token")
def session_token(account_id: str) -> dict:
    """最近捕获的 JWT（captureToken 简化版，内存态）。"""
    token = get_pool().token_of(account_id)
    return {"account_id": account_id, "has_token": token is not None, "token_head": token[:16] + "…" if token else None}


@router.get("/check")
def check() -> dict:
    """chromium 依赖体检。"""
    return browser_pool.warmup_check()
