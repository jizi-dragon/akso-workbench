"""工作区快照管理面：列表 / 创建 / 恢复。

只操作**沙箱工作区根**，不接受任意路径——否则这个端点会变成"读/写任意目录"的洞。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from .. import checkpoints
from ..sandbox.layout import workspace_root

router = APIRouter(prefix="/api/checkpoints", tags=["checkpoints"])


@router.get("")
def list_checkpoints() -> dict:
    return {"checkpoints": checkpoints.list_captures(), "root": str(workspace_root())}


@router.post("")
async def create_checkpoint() -> dict:
    try:
        capture = await asyncio.to_thread(checkpoints.capture, workspace_root())
    except checkpoints.CheckpointError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"checkpoint": capture.to_dict(), "checkpoints": checkpoints.list_captures()}


@router.post("/{capture_id}/restore")
async def restore_checkpoint(capture_id: str) -> dict:
    try:
        result = await asyncio.to_thread(checkpoints.restore, capture_id)
    except checkpoints.CheckpointError as exc:
        raise HTTPException(404, str(exc)) from exc
    return result
