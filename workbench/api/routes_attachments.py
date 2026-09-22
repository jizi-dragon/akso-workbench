"""附件接口：上传 / 列表 / 原文件 / 删除（2026-09-22，用户下达「核心 agent 能力增强」）。

## 为什么上传要先有会话 id（但允许没有）

前端在"新会话的第一条消息"时还没有会话 id（它由发言时创建）。所以这一组接口的约定是：

- `POST /api/attachments` 允许 `conversationId` 为空 ⇒ 存到 `_pending/` 下，**发言时再绑定**
  （`services.attachments.attach_to_conversation`，只允许"从未绑定 → 绑定"这一次迁移）；
- 已经有会话就按会话存，删会话时同一目录一起删。

## 为什么原文件要单独一个端点

图片要能在对话里**看见**（前端 `<img>`），而 `chat` 接口只回文本。
`GET /api/attachments/{id}/raw` 回原始字节，路径经 `raw_path()` 的包含关系复查——
**不接受任何来自 URL 的路径成分**（id 只用来查表，文件名来自表）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..services import attachments as store

router = APIRouter(prefix="/api/attachments", tags=["attachments"])

#: 上传时还没有会话 id ⇒ 落到"未绑定"的哨兵目录。
#: 口径的**唯一真源在服务层**（它同时决定"什么算未绑定"），这里只是转出口。
PENDING_CONVERSATION = store.PENDING_CONVERSATION
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 与 config.ATTACHMENTS_MAX_BYTES 同步（这里只是先拦一道）


class AttachmentOut(BaseModel):
    id: str
    name: str
    kind: str
    mime: str
    bytes: int
    status: str
    parseNote: str
    textChars: int
    locator: dict[str, Any]
    createdAt: int


def _out(row: dict[str, Any]) -> dict[str, Any]:
    """对外形状（camelCase，见 AGENTS §2 的"字段名双语义"）。"""
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "kind": row.get("kind"),
        "mime": row.get("mime"),
        "bytes": row.get("bytes"),
        "status": row.get("status"),
        "parseNote": row.get("parse_note") or "",
        "textChars": row.get("text_chars") or 0,
        "locator": store.locator(row),
        "createdAt": row.get("created_at"),
    }


@router.post("")
async def upload(file: UploadFile = File(...), conversationId: str = Form("")) -> dict:
    """收一个文件：校验 → 落盘 → 解析 → 入库。**同步 IO 全部走 to_thread**（规则 12）。"""
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"文件过大：{len(data) / 1048576:.1f} MB > "
                                 f"上限 {MAX_UPLOAD_BYTES / 1048576:.0f} MB")
    conversation = (conversationId or "").strip() or PENDING_CONVERSATION

    def run() -> dict[str, Any]:
        return store.store(conversation_id=conversation,
                           name=file.filename or "file", data=data)

    try:
        row = await asyncio.to_thread(run)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "attachment": _out(row), "marker": store.reference_marker(row)}


@router.get("")
async def list_attachments(conversationId: str = "") -> dict:
    conversation = (conversationId or "").strip() or PENDING_CONVERSATION
    rows = await asyncio.to_thread(store.list_for, conversation)
    return {"total": len(rows), "attachments": [_out(r) for r in rows]}


@router.get("/{att_id}/raw")
async def raw(att_id: str) -> FileResponse:
    row = await asyncio.to_thread(store.get, att_id)
    if row is None:
        raise HTTPException(404, f"附件不存在：{att_id}")
    try:
        path = store.raw_path(row)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not path.is_file():
        raise HTTPException(410, "附件文件已不在磁盘上（可能被清理过）")
    return FileResponse(path, media_type=str(row.get("mime") or "application/octet-stream"),
                        filename=str(row.get("stored_name") or path.name))


@router.delete("/{att_id}")
async def delete(att_id: str) -> dict:
    ok = await asyncio.to_thread(store.delete, att_id)
    if not ok:
        raise HTTPException(404, f"附件不存在：{att_id}")
    return {"ok": True, "id": att_id}
