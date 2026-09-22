"""SSE 流式对话 + 回答反馈路由。

对话流程（_chat_events）：
  1. 会话准备（新建/首条消息作标题）
  2. run_agent_loop 逐事件转 SSE
  3. 收尾：持久化回答 + qa_metrics 度量
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import config
from ..approvals import NOTE_ROLE
from ..harness.loop import run_agent_loop
from ..harness.prompts import build_system_prompt
from ..harness.tools import default_registry
from ..harness.types import AgentConfig
from ..services import attachments as attachment_store
from ..services import storage
from ..services.deepseek import DeepSeekClient
from ..services.settings import load_deepseek, load_max_steps

logger = logging.getLogger("workbench.chat")

router = APIRouter(prefix="/api", tags=["chat"])


class ChatBody(BaseModel):
    conversationId: str | None = None
    message: str
    attachmentIds: list[str] = []
    """这一轮带的附件 id（用户 2026-09-22 下达的「用户能传图片/文件」）。

    刻意**不用** `str`：附件是**一组**，而 `list[str]` 的默认值必须是 `[]`
    而不是 `None`（漏传与传空必须同义，否则每个消费方都要写一遍兜底）。
    """


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


def _attachment_parts(att_id: str, name: str, *, inline: bool) -> list[dict]:
    """一个附件引用 → content parts（`inline=False` 时只回一句文本说明）。

    ## 为什么只有**最新一轮**才是 `inline=True`（用户 2026-09-22 拍板 A4）

    把图片或整份文档**每一轮都重发**会造成三件事同时发生：token 账单线性膨胀、
    上下文被同一份文件挤满、**前缀缓存失效**（每轮前缀都在变 ⇒ 命中率归零，成本 ×50，
    `cache.py:143` 有明确警告）。所以历史轮只留一句"引用过它"，
    细节由模型用 `attachment_list` / `attachment_read` 回去取。

    ## 形状（`inline=True`）

    - **图片** → `{"type": "image_url", "image_url": {"url": "data:<mime>;base64,…"}}`
      （实测 `deepseek-flash` 收这种形状并真的看懂图片：8×8 红色 PNG ⇒ 答"红色"，n=1）；
    - **文档** → 文本块：文件头（名字/类型/规模）+ 解析出的正文（**有上限**）+ 坐标目录 +
      解析器给出的 note（截断/扫描件/失败**一律如实说**，不许伪装成空文件）。
    """
    from ..services import attachments as attachment_store
    from ..services import parsing

    att = attachment_store.get(att_id)
    if att is None:
        return [{"type": "text", "text":
                 f"[附件 {att_id}：{name} —— **本地已找不到这份文件**（可能被删或被清理）。"
                 "不要凭它作答，先告诉用户。]"}]

    label = (f"{att.get('name')}"
             f"（{parsing.type_label(parsing.extension_of(str(att.get('name'))))}，"
             f"{(int(att.get('bytes') or 0)) / 1024:.0f} KB）")
    if not inline:
        return [{"type": "text", "text":
                 f"[附件 {att_id}：{label} —— 这份文件在**更早的轮次**里已交给过你，"
                 "正文没有重复发送。要看内容用 `attachment_read`（可按页 / 工作表 / 行范围取），"
                 "或 `attachment_search` 定位关键词。]"}]

    if att.get("kind") == "image":
        try:
            data = attachment_store.raw_path(att).read_bytes()
        except (OSError, ValueError) as exc:
            return [{"type": "text", "text": f"[附件 {att_id}：{label} —— 读文件失败：{exc}]"}]
        import base64

        mime = str(att.get("mime") or "image/png")
        return [
            {"type": "text", "text": f"[附件 {att_id}：{label} —— 这是用户交上来的图片，直接看它]"},
            {"type": "image_url",
             "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"}},
        ]

    body = attachment_store.parsed_text(att)
    budget = config.ATTACHMENTS_INLINE_CHARS
    clipped = body[:budget]
    lines = [
        f"[附件 {att_id}：{label}",
        f"  正文 {int(att.get('text_chars') or 0)} 字，本次内联 {len(clipped)} 字"
        + ("（**已截断**，其余用 `attachment_read` 按范围取）" if len(body) > budget else ""),
    ]
    locator = attachment_store.locator(att)
    if locator:
        lines.append(f"  坐标目录：{json.dumps(locator, ensure_ascii=False)[:1200]}")
    note = str(att.get("parse_note") or "")
    if note:
        lines.append(f"  ⚠ 解析说明：{note}")
    lines.append("]")
    return [{"type": "text", "text": "\n".join(lines) + "\n" + clipped}]


def _request_history(conversation_id: str, user_text: str) -> list[dict]:
    """**这一轮要发给模型的历史**：把用户消息落库，然后整段历史过一次扼流点。

    为什么把这两步**合成一个函数**：它们的顺序是承重的。分开写时，"先取历史 →
    过硬点 → 再把新消息原样追加"看起来同样自然，但那样**新消息会绕过附件展开**
    （图片只变成一个文件名），而单元测试发现不了——它们直接喂的就是硬点。
    2026-09-22 联调实测踩到：模型回"没有把像素送过来"，而 pytest 全绿。

    判据：`tests/test_attachments.py::test_the_newest_message_goes_through_the_choke_point`。
    """
    storage.insert_message(conversation_id, "user", user_text)
    return _model_history(storage.list_messages(conversation_id))


def _model_history(messages: list[dict]) -> list[dict]:
    """把会话历史翻成**模型能收的**形状。**这是唯一那个扼流点。**

    存在的理由只有一个：`note` 是**界面事件**（「用户在界面上选定了执行账号」，
    见 `approvals.NOTE_ROLE`）——它必须进模型的上下文，否则用户先在右下角选了账号、
    模型下一轮还是不知道用哪个；但 DeepSeek / OpenAI 只认 `system`/`user`/`assistant`/
    `tool`，把 `note` 原样发过去**整个请求会被 400 掉**（不是少一条，是全废）。

    翻译成 `user` 而不是 `system`：它在语义上是"用户做了这件事"，
    而且中途插一条 system 在多数实现里是被忽略或报错的。

    未知角色**丢掉并告警**——宁可少一条上下文，也不要发一个会让整轮对话 400 的角色。

    ## 附件在这里展开（2026-09-22）

    消息正文里的 `[附件 att-xxxxxxxx：名字]` 标记（`services.attachments.MARKER_RE`）：

    - 落在**最后一条 user 消息**上 ⇒ 展开成 content parts（图片 = image_url，文档 = 正文块）；
    - 落在**更早的轮次**上 ⇒ 只换成一句"这份文件已交过、正文不重复发送"。

    展开放在这里而不是"写消息时"，是因为这里**同时看得见整段历史**——
    "哪一条是最新一轮"只有在这个位置才是个确定的事实（`chat` 接口每次只拿到一条新消息）。
    """
    out: list[dict] = []
    newest_user = -1
    for index, row in enumerate(messages):
        if str(row.get("role") or "") == "user":
            newest_user = index
    for index, row in enumerate(messages):
        role = str(row.get("role") or "")
        content = str(row.get("content") or "")
        markers = attachment_store.iter_markers(content)
        if role == NOTE_ROLE:
            out.append({"role": "user", "content": f"（界面事件）{content}"})
            continue
        if role not in ("user", "assistant", "system"):
            logger.warning("会话历史里出现未知角色 %r，已跳过（发出去会让整轮请求失败）", role)
            continue
        if not markers:
            out.append({"role": role, "content": content})
            continue
        parts: list[dict] = []
        plain = attachment_store.MARKER_RE.sub("", content).strip()
        if plain:
            parts.append({"type": "text", "text": plain})
        inline = index == newest_user
        for att_id, name in markers:
            parts.extend(_attachment_parts(att_id, name, inline=inline))
        out.append({"role": role, "content": parts})
    return out


async def _chat_events(body: ChatBody) -> AsyncIterator[str]:
    cfg = load_deepseek()
    if not cfg.ready:
        yield _sse({"type": "error", "message": "请先在设置中配置 DeepSeek API Key。"})
        yield _sse({"type": "done"})
        return

    conversation_id, title_updated = _prepare_conversation(body)
    # 附件先绑到会话上（上传时可能还没有会话 id，见 routes_attachments 的说明），
    # 再把引用标记拼进**存下来的正文**——标记是"这一轮带了哪份文件"的唯一事实，
    # `_model_history` 与前端都按它工作（历史轮也能凭它说清"你当时给过我什么"）。
    markers: list[str] = []
    for att_id in list(dict.fromkeys(body.attachmentIds))[: config.ATTACHMENTS_MAX_PER_TURN]:
        att = attachment_store.attach_to_conversation(att_id, conversation_id)
        if att is None:
            logger.warning("附件 %s 不存在，已跳过（用户可能在另一个标签页删掉了它）", att_id)
            continue
        markers.append(attachment_store.reference_marker(att))
    user_text = body.message if not markers else f"{body.message}\n" + "\n".join(markers)
    history = _request_history(conversation_id, user_text)

    started = time.perf_counter()
    yield _sse({"type": "start", "conversationId": conversation_id})
    for att_id, name in attachment_store.iter_markers(user_text):
        yield _sse({"type": "attachment", "attachmentId": att_id, "name": name})

    result: dict = {"content": "", "error": None}
    async for sse in _stream_agent(history, cfg, result, conversation_id):
        yield sse

    error_message = result["error"]
    final_content = f"错误：{error_message}" if error_message else result["content"]
    if not final_content:
        final_content = "（模型未返回内容）"
    storage.insert_message(conversation_id, "assistant", final_content)

    metric_id = _record_metric(body.message, started)
    yield _sse({
        "type": "done",
        "conversationId": conversation_id,
        "content": final_content,
        "metricId": metric_id,
        "titleUpdated": title_updated,
        "latencyMs": int((time.perf_counter() - started) * 1000),
        "usage": result.get("usage") or {},
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
    result: dict,
    conversation_id: str = "",
) -> AsyncIterator[str]:
    """跑 Agent 循环并逐事件转发 SSE；把最终内容/错误写入 result 供调用方收尾。

    事件**原样透传**（token / thought / action / observation / approval_required /
    usage / final / error）——前端工具卡片靠 action/observation，缓存命中量靠 usage。
    """
    client = DeepSeekClient(cfg.api_key, model=cfg.model)
    agent_config = AgentConfig(
        temperature=cfg.temperature,
        system_prompt=build_system_prompt(),
        # 步数预算从设置读（默认 40）。**这里曾经什么都不传** —— 于是 `max_steps`
        # 永远是 `AgentConfig` 里的类默认值，用户没有任何手段调大它，
        # 而"稍微长一点的配置工厂任务"必然撞上限并**整轮被砍掉**（见
        # docs/FACTORY-AGENT-GAP.md 问题①）。
        max_steps=load_max_steps(),
    )

    assistant_content = ""
    try:
        async for event in run_agent_loop(
            history, client, default_registry(), agent_config,
            conversation_id=conversation_id,
        ):
            if event["type"] == "token":
                assistant_content += event["text"]
            elif event["type"] == "final":
                assistant_content = event["content"]
                result["content"] = assistant_content
            elif event["type"] == "error":
                result["error"] = event["message"]
            elif event["type"] == "usage":
                # 一起塞进 done 事件，前端不必额外记状态
                result["usage"] = event.get("usage") or {}
            yield _sse(event)
    except Exception as exc:  # noqa: BLE001 —— 循环外异常（网络/协议层）转为 error 事件
        result["error"] = str(exc)
        yield _sse({"type": "error", "message": f"错误：{result['error']}"})


def _record_metric(question: str, started: float) -> str | None:
    latency_ms = int((time.perf_counter() - started) * 1000)
    try:
        return storage.insert_qa_metric(question, "none", latency_ms)
    except Exception:  # noqa: BLE001 —— 度量失败不影响主流程
        return None
