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

from .. import config, mcp_registry
from ..approvals import NOTE_ROLE
from ..harness.loop import run_agent_loop
from ..harness.prompts import build_system_prompt
from ..harness.stop import StopToken
from ..harness.tools import default_registry
from ..harness.types import AgentConfig
from ..services import attachments as attachment_store
from ..services import storage
from ..services.deepseek import DeepSeekClient
from ..services.settings import load_deepseek, load_max_steps
from ..services.storage import MESSAGE_KIND_DEFAULT, MESSAGE_KIND_ERROR, TOOL_ALLOWLIST_KEY

logger = logging.getLogger("workbench.chat")

router = APIRouter(prefix="/api", tags=["chat"])

# ———— 取消登记表（计划 §5.6） ————
#
# ★ **进程内、按会话 id 索引、不用全局单例**（计划点名）。理由不是洁癖：
# 一个模块级单例会让"上一条用例留下的取消状态"泄进下一条用例，
# 而那种失败**看起来像是"循环自己停了"** —— 归因会烧掉一整轮排查（PITFALLS #71 同族）。
#
# 生命周期与"一条 SSE 连接"对齐：`_chat_events` 进入时登记、`finally` 里摘掉。
# 摘掉这一步是承重的 —— 不摘的话这张表会随会话数无限增长，
# 而"某个会话停止后永远停着"会让它下一轮**一上来就被取消**。
_STOP_TOKENS: dict[str, StopToken] = {}


def _register_stop(conversation_id: str) -> StopToken:
    """为一条对话登记一枚取消令牌（重复登记则**换新**，见下）。"""
    token = StopToken(conversation_id)
    _STOP_TOKENS[conversation_id] = token
    return token


def _release_stop(conversation_id: str, token: StopToken) -> None:
    """摘掉令牌。**只摘自己那一枚** —— 同一会话可能有另一条连接已经登记了新令牌。"""
    if _STOP_TOKENS.get(conversation_id) is token:
        _STOP_TOKENS.pop(conversation_id, None)


def stop_conversation(conversation_id: str) -> bool:
    """请求停止某个会话的进行中回合。返回"是否真的有一轮在跑"。

    ★ 返回 `False` 不是错误：用户可能在回合**刚结束**时点了停止（网络延迟、
    或者他手快）。那时如实回一句"这一轮已经结束了"比报 404 有用得多 ——
    404 会让他以为"停止功能坏了"。
    """
    token = _STOP_TOKENS.get(conversation_id)
    if token is None:
        return False
    if not token.request():
        logger.info("重复的停止请求（会话 %s）—— 界面可能没有及时给出反馈",
                    conversation_id)
    return True


class ChatBody(BaseModel):
    conversationId: str | None = None
    message: str
    attachmentIds: list[str] = []
    """这一轮带的附件 id（用户 2026-09-22 下达的「用户能传图片/文件」）。

    刻意**不用** `str`：附件是**一组**，而 `list[str]` 的默认值必须是 `[]`
    而不是 `None`（漏传与传空必须同义，否则每个消费方都要写一遍兜底）。
    """

    plan: bool = False
    """计划模式：只允许 READ 工具；写/危险工具按"未执行"回填。"""

    allowed_tools: list[str] | None = None
    """本回合工具白名单（None = 不额外收窄；空数组 = 一个都不给）。"""

    plugins: list[str] | None = None
    """本轮允许模型看到的**插件白名单**（计划 §7.2）。`None` = 全部。

    ## 它为什么存在

    工具 schema 是**每一轮请求**都要发的前缀里最贵的一块（18 个工具约 2,192 token/轮），
    而且是每一轮都发。让调用方按当前场景传一个子集，是唯一能在**不删功能**的前提下
    把这块成本降下来的手段。

    ## 它**不是**安全边界

    ⚠️ 读清楚这一条再改它：白名单只决定"模型**看得见**哪些工具"，
    **不决定"能调用哪些"**。真实闸门是 `ToolHub.execute` 里那道
    （未知工具 → 观测、风险工具 → 没有令牌不执行）。所以传一个子集
    **不会**让任何危险操作变得安全，不传也**不会**让它变得危险。
    把它写成"权限控制"是本文件最容易被误读的一处。

    ## `None` 与 `[]` **不同义**（这里刻意与 `attachmentIds` 相反）

    - `None` = 不裁剪（全部插件）；
    - `[]` = 一个插件都不给 ⇒ 模型没有任何工具可用。

    `attachmentIds` 那边"漏传与传空同义"是对的（空附件条与没有附件条是一回事），
    但这里不同：漏传的语义是"我不关心"，传空的语义是"我明确要求它没有工具"。
    把两者合并会让**一个写错的调用方静默地把模型的手脚全砍掉**。
    """


class FeedbackBody(BaseModel):
    metricId: str
    helpful: bool


class StopBody(BaseModel):
    conversationId: str
    """要停止哪个会话的进行中回合。

    **按会话 id，不按"当前会话"** —— 服务端没有"当前会话"这个概念（那是界面的状态），
    而且用户可能在停止请求在途时切了会话。
    """


@router.post("/chat/stop")
def stop(body: StopBody) -> dict:
    """停止某个会话正在进行的一轮（计划 §5.6）。

    **协作式**：它只是置一个位，循环会在下一个检查点（模型调用前 / 工具调用前 /
    等待确认期间）收尾。所以响应立刻返回，而"真的停了"由 SSE 流里的 `cancelled`
    事件确认 —— 前端不该把这里的 200 当成"已经停了"。

    ⚠️ **它不撤销已经执行过的工具调用**。文案由 `loop._cancelled_message` 统一给。
    """
    running = stop_conversation(body.conversationId)
    return {
        "ok": True,
        "running": running,
        "message": ("已请求停止，做完了手上这一步就会停。"
                    if running else "这一轮已经结束了。"),
    }


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


def _system_prompt() -> str:
    """基线系统提示词 + 已装载 MCP server 的外部说明（有上限、失败不影响对话）。"""
    prompt = build_system_prompt()
    try:
        block = mcp_registry.get_registry().instructions_block()
    except Exception:  # noqa: BLE001 —— MCP 是增强能力，不能拖垮对话
        logger.warning("读取 MCP instructions 失败，按无 MCP 说明继续", exc_info=True)
        return prompt
    return f"{prompt}\n\n{block}" if block else prompt


def _tool_allowlist(explicit: list[str] | None) -> tuple[str, ...] | None:
    """请求显式白名单优先；否则读全局设置（空 = 不限制）。"""
    if explicit is not None:
        return tuple(explicit)
    raw = storage.get_setting(TOOL_ALLOWLIST_KEY)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, list):
        return None
    return tuple(str(item) for item in parsed)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _attachment_parts(att_id: str, name: str, *, inline: bool) -> list[dict]:
    """一个附件引用 → content parts（`inline=False` 时只回一句文本说明）。

    ## 为什么只有**最新一轮**才是 `inline=True`（用户 2026-09-22 拍板 A4）

    把图片或整份文档**每一轮都重发**会造成三件事同时发生：token 账单线性膨胀、
    上下文被同一份文件挤满、**前缀缓存失效**（每轮前缀都在变 ⇒ 命中率归零，成本 ×50，
    `cache.py:191` 有明确警告）。所以历史轮只留一句"引用过它"，
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
        # 旧轮次的那句话**必须按类型分开写**（2026-09-22 真机联调抓到）：
        # 原来图片与文档共用一句「已交过你、正文没有重复发送、要看内容用 attachment_read」，
        # 而**图片根本没有"正文"可读**——模型读到这句会得出"我那时并没有真的看到它"，
        # 于是在下一轮**主动撤回自己刚才答对的内容**：真机实录「前两轮关于色块和形状的图片，
        # 我并没有真的读取附件内容，回答是瞎猜的」。它答对了（纯蓝 / 正方形都不是能猜出来的），
        # 这句提示却让它把自己的正确感知否掉——**一句不准确的引用话术，代价是用户对整条链的信任**。
        if att.get("kind") == "image":
            return [{"type": "text", "text":
                     f"[附件 {att_id}：{label} —— **你在那一轮看过它的像素**，本轮不重复发送"
                     "（同一张图每轮重发会让账单和上下文一起涨）。现在你看不到它的细节了；"
                     "确实需要再看一次，就告诉用户把这张图**重新贴一遍**"
                     "（`attachment_read` 只能给出文件名与类型，给不了像素）。]"}]
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
            # 这句提示要**短、且不要提起"能不能读"**（2026-09-22 真机量的）：
            # 曾经写过一版带解释的长句（"不要用 attachment_list 的 textChars 判断它能不能读…"），
            # 结果那一轮的回答变成「看不出颜色——这个文件只有 168 字节、textChars=0，
            # 像是空文件或损坏的图」——**提示自己把"读不出"这个念头递给了模型**。
            # 现在只留一句正向指令；"图片没有文本、像素直接在下面"那句挪进了
            # `attachment_list` 的输出里（只有模型真的去调那个工具时才会看到）。
            {"type": "text", "text": f"[附件 {att_id}：{label} —— 用户交上来的图片，见图]"},
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
        # ★ `kind != "message"` 的行**绝不进模型上下文**（迁移 23 / 计划 §5.4b）。
        # 错误记录是**我们写的**（"错误：网络连接失败"），它带着 `role="assistant"`
        # 只是为了让界面照常渲染成气泡 —— 原样发给模型，等于让它以为**自己说过**
        # 那句它从没说过的话，于是据此继续推理（"我好像已经回答过了"）。
        # 过滤放在这里而不是 `storage.list_messages`：**这是唯一那个扼流点**，
        # 而界面需要看见错误（见 `list_messages` 的文档串）。
        if str(row.get("kind") or MESSAGE_KIND_DEFAULT) != MESSAGE_KIND_DEFAULT:
            continue
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
    completed = False
    # 取消令牌与这条 SSE 连接同生命周期（计划 §5.6 的"进程内登记表"）。
    # `_STOP_TOKENS` 那份唯一的用途就是让 `POST /api/chat/stop` 找到它。
    stop_token = _register_stop(conversation_id)
    try:
        async for sse in _stream_agent(history, cfg, result, conversation_id, stop_token,
                                       body.plugins, body.plan, body.allowed_tools):
            yield sse
        completed = True
    finally:
        # ★ 摘掉令牌（承重）：不摘的话这张表随会话数无限增长，
        # 而"某个会话停止后令牌永远停着"会让它**下一轮一上来就被取消**。
        _release_stop(conversation_id, stop_token)
        # ★ 收尾必须在 `finally`（计划 §5.4c）：消费方断开（关页面 / 切会话 / 网络断）
        # 时 `async for` 被 `aclose()`，**正常路径的收尾代码根本不会执行** ⇒
        # 库里留下"用户说了话、助手那侧什么都没有"的**半截会话** ——
        # 刷新后那一轮凭空消失，而用户明明看着它跑过。
        #
        # ⚠️ 这里**不发 `done` 事件** —— 与 `harness/loop._accounted` 同一条理由：
        # `finally` 可能正因 `GeneratorExit` 而执行，`yield` 会抛
        # `RuntimeError: async generator ignored GeneratorExit`。
        # **落库是账本的事，`done` 事件是界面的事。**
        final_content, final_kind = _turn_final_text(result, completed=completed)
        storage.insert_message(conversation_id, "assistant", final_content, kind=final_kind)

    # 只有正常跑完才走到这里（`GeneratorExit` 会径直穿过去）
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


def _turn_final_text(result: dict, *, completed: bool) -> tuple[str, str]:
    """这一轮该落库的正文与它的 `kind`。返回 `(content, kind)`。

    ## 四条口径（每一条都对应一次真实踩坑）

    1. **绝不落空串**：`（模型未返回内容）` 这条兜底早就在（前端也认它），
       **复用它**而不是新造一句 —— 两处文案漂开会让用户看到两种"什么都没发生"。
    2. **中止要标注**：半截正文后面补 `（已中止）`。不标的话，用户刷新后看到一句
       戛然而止的话，会以为**是模型答坏了** —— 而真实原因是他自己关掉了页面。
    3. **错误单独记 `kind`**：见迁移 23 与 `_model_history` 的过滤。
       错误**优先于**中止标注 —— 一次失败的网络调用，说清"它失败了"比"它被中止了"有用。
    4. ★ **停止也要标注，而且与中止分开**（计划 §5.6）：`（已停止）` ≠ `（已中止）`。
       前者是**用户自己按的**，后者是**连接断了**。两者对用户的含义完全不同：
       看到"已中止"他会怀疑网络，看到"已停止"他知道是自己干的。
       **事件流里的 `cancelled` 事件刷新后就没了**，所以标注必须落进库 ——
       否则刷新之后那一轮看起来就是"模型答到一半不说了"。
    """
    error_message = result.get("error")
    if error_message:
        return f"错误：{error_message}", MESSAGE_KIND_ERROR
    content = str(result.get("content") or "")
    if not content:
        content = "（模型未返回内容）"
    if result.get("cancelled"):
        content += "（已停止）"
    elif not completed:
        content += "（已中止）"
    return content, MESSAGE_KIND_DEFAULT


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
    should_stop=None,
    plugins: list[str] | None = None,
    plan: bool = False,
    allowed_tools: list[str] | None = None,
) -> AsyncIterator[str]:
    """跑 Agent 循环并逐事件转发 SSE；把最终内容/错误写入 result 供调用方收尾。

    事件**原样透传**（token / thought / action / observation / approval_required /
    usage / final / error / **cancelled**）——前端工具卡片靠 action/observation，
    缓存命中量靠 usage，停止靠 cancelled。
    """
    client = DeepSeekClient(cfg.api_key, model=cfg.model)
    agent_config = AgentConfig(
        temperature=cfg.temperature,
        system_prompt=_system_prompt() + (
            "\n\n## 计划模式\n你当前处于计划模式：只允许调用只读工具。"
            "先给出清晰的执行计划与风险，等用户确认后再执行；不要调用写工具。"
            if plan else ""
        ),
        plan_mode=plan,
        allowed_tools=_tool_allowlist(allowed_tools),
        # 步数预算从设置读（默认 40）。**这里曾经什么都不传** —— 于是 `max_steps`
        # 永远是 `AgentConfig` 里的类默认值，用户没有任何手段调大它，
        # 而"稍微长一点的多步任务"必然撞上限并**整轮被砍掉**。
        max_steps=load_max_steps(),
    )

    assistant_content = ""
    try:
        async for event in run_agent_loop(
            history, client, default_registry(), agent_config,
            conversation_id=conversation_id,
            should_stop=should_stop,
            plugins=plugins,
        ):
            if event["type"] == "token":
                assistant_content += event["text"]
                # ★ **每帧同步一份到 `result`**，而不是等收尾时再补。
                #
                # 为什么不能省：中止时收尾读的是 `result`，而 `final` 事件永远不会到达。
                # "在本生成器的 `finally` 里补一下"是**行不通**的 —— 实测（2026-09-24）：
                #
                #     外层 finally 跑时，内层还挂在它上次 yield 的地方；
                #     内层的 finally 是在 `aclose()` **返回之后**才跑的。
                #
                # 原因：被打断的 `async for` **不会**关闭内层生成器（`with` 才会），
                # 它只是挂在那里，等到被垃圾回收时才由事件循环 finalize。
                # ⇒ **跨生成器的"收尾顺序"不能靠 `finally` 保证，只能靠"边发生边记"。**
                result["content"] = assistant_content
            elif event["type"] == "final":
                assistant_content = event["content"]
                result["content"] = assistant_content
            elif event["type"] == "error":
                result["error"] = event["message"]
            elif event["type"] == "cancelled":
                # ★ 记下来，好让收尾给落库的正文补 `（已停止）`。
                # `cancelled` 事件本身**刷新后就没了**（事件不落库），
                # 所以"用户按了停止"这件事必须由正文自己带着 ——
                # 否则刷新之后那一轮看起来就是"模型答到一半不说了"。
                result["cancelled"] = True
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
