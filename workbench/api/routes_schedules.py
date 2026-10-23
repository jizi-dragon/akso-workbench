"""定时任务管理面：CRUD + 手动触发（只读 Agent 回合）。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import schedules
from ..scheduler import execute

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


class ScheduleBody(BaseModel):
    title: str
    prompt: str
    intervalS: int


class SchedulePatch(BaseModel):
    title: str | None = None
    prompt: str | None = None
    intervalS: int | None = None
    enabled: bool | None = None


@router.get("")
def list_schedules() -> dict:
    return {"schedules": schedules.list_all()}


@router.post("")
def create_schedule(body: ScheduleBody) -> dict:
    try:
        return schedules.create(body.title, body.prompt, body.intervalS)
    except schedules.ScheduleError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/{task_id}")
def patch_schedule(task_id: str, body: SchedulePatch) -> dict:
    try:
        return schedules.update(task_id, title=body.title, prompt=body.prompt,
                                interval_s=body.intervalS, enabled=body.enabled)
    except schedules.ScheduleError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/{task_id}")
def delete_schedule(task_id: str) -> dict:
    try:
        schedules.delete(task_id)
    except schedules.ScheduleError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True}


@router.post("/{task_id}/run")
async def run_schedule(task_id: str) -> dict:
    task = schedules.get(task_id)
    if task is None:
        raise HTTPException(404, f"定时任务不存在：{task_id}")
    try:
        status, error, conversation_id = await asyncio.to_thread(execute, task)
    except Exception as exc:  # noqa: BLE001 —— 手动触发失败要可读
        raise HTTPException(502, f"定时任务执行失败：{exc}") from exc
    schedules.mark_run(task_id, status=status, error=error, conversation_id=conversation_id)
    return {"status": status, "error": error, "conversationId": conversation_id}
