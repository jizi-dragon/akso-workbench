"""SSE 流式对话 + 回答反馈路由。

对话流程（_chat_events）：
  1. 会话准备（新建/首条消息作标题）
  2. 三层检索 → 上下文块注入系统提示词（记忆注入）
  3. run_agent_loop 逐事件转 SSE
  4. 收尾：持久化回答 + qa_metrics 度量
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..harness.loop import run_agent_loop
from ..harness.prompts import build_system_prompt
from ..harness.types import AgentConfig
from ..services import storage
from ..services.deepseek import DeepSeekClient
from ..services.settings import load_deepseek
from .routes_knowledge import _search, registry

router = APIRouter(prefix="/api", tags=["chat"])


class ChatBody(BaseModel):
    conversationId: str | None = None
    message: str


class FeedbackBody(BaseModel):
    metricId: str
    helpful: bool


@router.post("/chat/stream")
async def chat_stream(body: ChatBody) -> StreamingResponse:
    return StreamingResponse(
        _chat_events(body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/feedback")
def feedback(body: FeedbackBody) -> dict:
    storage.update_qa_feedback(body.metricId, 1 if body.helpful else 0)
    return {"ok": True}


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def _chat_events(body: ChatBody) -> AsyncIterator[str]:
    cfg = load_deepseek()
    if not cfg.ready:
        yield _sse({"type": "error", "message": "请先在设置中配置 DeepSeek API Key。"})
        yield _sse({"type": "done"})
        return

    conversation_id, title_updated = _prepare_conversation(body)
    history = storage.list_messages(conversation_id)
    storage.insert_message(conversation_id, "user", body.message)
    history = [*history, {"role": "user", "content": body.message}]

    started = time.perf_counter()
    try:
        search_result = await _search(body.message)
    except Exception:  # noqa: BLE001 —— 检索失败不阻塞对话
        search_result = {"hits": [], "contextBlock": "", "hitKind": "none"}
    hit_kind = search_result["hitKind"]

    yield _sse({"type": "start", "conversationId": conversation_id, "hitKind": hit_kind})

    result: dict = {"content": "", "error": None}
    async for sse in _stream_agent(history, cfg, search_result["contextBlock"], result):
        yield sse

    error_message = result["error"]
    final_content = f"错误：{error_message}" if error_message else result["content"]
    if not final_content:
        final_content = "（模型未返回内容）"
    storage.insert_message(conversation_id, "assistant", final_content)

    metric_id = _record_metric(body.message, hit_kind, started)
    yield _sse({
        "type": "done",
        "conversationId": conversation_id,
        "content": final_content,
        "metricId": metric_id,
        "titleUpdated": title_updated,
        "hitKind": hit_kind,
        "latencyMs": int((time.perf_counter() - started) * 1000),
    })


def _prepare_conversation(body: ChatBody) -> tuple[str, bool]:
    """确保会话存在；首条用户消息自动作为会话标题。返回 (会话ID, 标题是否更新)。"""
    conversation_id = body.conversationId
    if not conversation_id:
        conversation_id = storage.create_conversation()["id"]
    title_updated = False
    if storage.count_user_messages(conversation_id) == 0:
        storage.update_conversation_title(conversation_id, body.message.strip()[:30] or "新会话")
        title_updated = True
    return conversation_id, title_updated


async def _stream_agent(
    history: list[dict],
    cfg,
    knowledge_context: str,
    result: dict,
) -> AsyncIterator[str]:
    """跑 Agent 循环并逐事件转发 SSE；把最终内容/错误写入 result 供调用方收尾。"""
    client = DeepSeekClient(cfg.api_key, model=cfg.model)
    agent_config = AgentConfig(
        temperature=cfg.temperature,
        system_prompt=build_system_prompt(knowledge_context),
    )

    assistant_content = ""
    try:
        async for event in run_agent_loop(history, client, registry, agent_config):
            if event["type"] == "token":
                assistant_content += event["text"]
            elif event["type"] == "final":
                assistant_content = event["content"]
                result["content"] = assistant_content
            elif event["type"] == "error":
                result["error"] = event["message"]
            yield _sse(event)
    except Exception as exc:  # noqa: BLE001 —— 循环外异常（网络/协议层）转为 error 事件
        result["error"] = str(exc)
        yield _sse({"type": "error", "message": f"错误：{result['error']}"})


def _record_metric(question: str, hit_kind: str, started: float) -> str | None:
    latency_ms = int((time.perf_counter() - started) * 1000)
    try:
        return storage.insert_qa_metric(question, hit_kind, latency_ms)
    except Exception:  # noqa: BLE001 —— 度量失败不影响主流程
        return None
