"""会话与消息路由。"""

from __future__ import annotations

from fastapi import APIRouter

from ..services import storage

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("")
def list_conversations() -> dict:
    return {"conversations": storage.list_conversations()}


@router.post("")
def create_conversation() -> dict:
    return storage.create_conversation()


@router.delete("/{conv_id}")
def delete_conversation(conv_id: str) -> dict:
    storage.delete_conversation(conv_id)
    return {"ok": True}


@router.get("/{conv_id}/messages")
def list_messages(conv_id: str) -> dict:
    return {"messages": storage.list_messages(conv_id)}
