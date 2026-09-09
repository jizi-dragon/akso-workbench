"""Monitor 监听路由：在托管会话（内置 Chromium）上启动/停止/查询录制。

浏览器分配政策（用户定稿）：
- 人工快捷登录/打开 → 用户自己的 Chrome（quick-login 扩展，/extension/* 指令）；
- 监听/自动化 → 应用内置 Chromium（browser_pool，cdp 模式 = Electron 壳的 Chromium）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services.browser_pool import BrowserError, get_pool

router = APIRouter(prefix="/api/monitor", tags=["monitor"])


class MonitorBody(BaseModel):
    account_id: str


@router.post("/start")
def monitor_start(body: MonitorBody) -> dict[str, Any]:
    try:
        return get_pool().monitor_start(body.account_id)
    except BrowserError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/stop")
def monitor_stop(body: MonitorBody) -> dict[str, Any]:
    try:
        return get_pool().monitor_stop(body.account_id)
    except BrowserError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/status/{account_id}")
def monitor_status(account_id: str) -> dict[str, Any]:
    snap = get_pool().session(account_id)
    if snap is None:
        return {"monitoring": False}
    return {
        "monitoring": snap.get("monitoring", False),
        "log": snap.get("monitor_log"),
        "session_status": snap.get("status"),
    }
