"""钉钉知识库同步路由：手动触发 / 状态与度量 / 开关配置。"""

from __future__ import annotations

import json
import time

from fastapi import APIRouter
from pydantic import BaseModel

from ..services import dingtalk_sync, storage

router = APIRouter(prefix="/api/sync/dingtalk", tags=["sync"])

SYNCED_KEY = dingtalk_sync.SYNCED_KEY


class SyncBody(BaseModel):
    workspaceIds: list[str] | None = None
    maxDocs: int | None = None


class SyncConfigBody(BaseModel):
    enabled: bool | None = None
    intervalHours: float | None = None


@router.post("")
async def sync_now(body: SyncBody) -> dict:
    """手动触发一次增量同步（同步执行；量大时建议先配 workspaceIds 或 maxDocs 试跑）。"""
    started = time.perf_counter()
    report = await dingtalk_sync.sync_dingtalk(body.workspaceIds, body.maxDocs)
    return {**report, "elapsedMs": int((time.perf_counter() - started) * 1000)}


@router.get("/status")
def sync_status() -> dict:
    last_raw = storage.get_setting(SYNCED_KEY)
    last = None
    if last_raw:
        try:
            last = json.loads(last_raw)
        except ValueError:
            last = None
    return {
        "lastSync": last,
        "stats": storage.sync_stats(),
        "enabled": storage.get_setting("dingtalkSyncEnabled") == "true",
        "intervalHours": _interval_hours(),
    }


@router.put("/config")
def update_config(body: SyncConfigBody) -> dict:
    if body.enabled is not None:
        storage.set_setting("dingtalkSyncEnabled", "true" if body.enabled else "false")
    if body.intervalHours is not None:
        storage.set_setting("dingtalkSyncIntervalHours", str(max(0.5, body.intervalHours)))
    return {"ok": True, "enabled": storage.get_setting("dingtalkSyncEnabled") == "true",
            "intervalHours": _interval_hours()}


def _interval_hours() -> float:
    try:
        return float(storage.get_setting("dingtalkSyncIntervalHours") or 6)
    except ValueError:
        return 6.0
