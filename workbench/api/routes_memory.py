"""Agent 长期记忆 API（v1）：列出 / 新增 / 删除事实。

- 不提供"修改"：事实改了就换 revision 的语义还没做对，先只支持删除重加。
- 不提供"自动注入"：检索由 `memory_search` 工具按需触发（见 `workbench/memory.py`）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import memory

router = APIRouter(prefix="/api", tags=["memory"])


class MemoryIn(BaseModel):
    title: str = Field(..., max_length=memory.MAX_TITLE)
    body: str = Field(..., max_length=memory.MAX_BODY)
    scope: str = Field("project", max_length=16)
    type: str = Field("project", max_length=16)
    keywords: str = Field("", max_length=memory.MAX_KEYWORDS)
    expiresAt: int | None = None


@router.get("/memory")
def list_memories(scope: str = "", limit: int = 100) -> dict:
    """列出记忆（按更新时间倒序）。`scope` 空 = 全部。"""
    try:
        rows = memory.list_memories(scope=scope or None, limit=limit)
    except memory.MemoryError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"memories": rows, "count": len(rows)}


@router.post("/memory")
def add_memory(body: MemoryIn) -> dict:
    """新增一条事实。**不用于指令**（指令写进 AGENTS/CORE-ARCHITECTURE）。"""
    try:
        return memory.add(
            title=body.title, body=body.body, scope=body.scope, type=body.type,
            keywords=body.keywords, expires_at=body.expiresAt)
    except memory.MemoryError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/memory/{memory_id}")
def delete_memory(memory_id: str) -> dict:
    if not memory.delete(memory_id):
        raise HTTPException(404, "记忆不存在")
    return {"ok": True, "id": memory_id}
