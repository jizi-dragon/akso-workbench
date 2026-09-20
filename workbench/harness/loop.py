"""Agent 循环引擎：call → tool → observe → repeat（事件流驱动）。

与传输层解耦：本模块只 yield 事件（见 types.EventType），
SSE / WebSocket / 测试断言都直接消费同一事件序列。

## 工具链（2026-09-15 的收口）

工具来自**进程级工具中枢** `plugin_api.ToolHub`（由 `plugins/` 四个插件装配）。
此前这里拿的是 `harness/tools.py` 自己的三个基础工具，于是"对话里的模型只能聊天、
看不到任何平台能力"——工具中枢的意义就在这里。

## 缓存（2026-09-15）

每次请求的 messages 必须满足 **append-only**，否则 DeepSeek 的上下文缓存必然 miss
（官方要求"完整匹配一个缓存前缀单元"）。本模块的保证：

    [system] + [冻结摘要(如果有)] + [原文历史] + [新消息]
    └──────── 这部分逐字节与上一轮相同 ────────┘   └ 只追加 ┘

关键点：**摘要一旦冻结就不再重算**（`cache.plan_prefix`）；超预算时也只把
"摘要边界之后、最近 N 轮之前"那一段并进摘要（前缀变一次，之后连续多轮重新稳定）。
改造前每轮都重新调 LLM 生成摘要——前缀每轮都变，缓存命中率恒为 0。

## 事件契约（前端 SSE 直接消费，改这里必须同步 chat.js）

    token*           正文增量
    thought?         工具调用轮次里模型的叙述
    action           发起工具调用 {call, tool:{name,risk,plugin}, auditId}
    observation      工具结果   {result:{ok,summary,...}, invocation:{status,pendingId,...}}
    approval_required 风险工具被确认门拦下 {pendingId, tool, risk, summary}
    usage             本轮 token 用量与**缓存命中量**（一次回答只发一条，收尾时）
    final            最终回答
    error            终止性错误
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator

from .. import cache, logging_setup
from ..plugin_api import Risk, ToolContext, ToolHub, ToolStatus
from ..services.deepseek import DeepSeekClient
from .safety import SafetyGate
from .types import AgentConfig, make_event

logger = logging.getLogger("workbench.harness")

SUMMARY_SYSTEM_PROMPT = "你是对话摘要助手。请将以下对话压缩为简洁摘要，保留关键事实、用户意图与结论。"


class UsageMeter:
    """把"这次回答花了多少 token、缓存命中多少"记下来（发事件 + 落库）。

    为什么要跨调用累计：一次回答可能有**多次** LLM 调用（工具轮次、摘要重算），
    只记最后一次会让命中率看起来比真实值高——而摘要那次调用**几乎必然全 miss**
    （它的输入是历史原文），恰恰是最该被看见的开销。
    """

    def __init__(self, conversation_id: str = "", model: str = "") -> None:
        self.stats = cache.UsageStats(conversation_id=conversation_id, model=model)
        self._started = time.perf_counter()

    def feed(self, usage: dict | None, *, summary: bool = False) -> None:
        if not usage:
            return
        self.stats.add(usage)
        if summary:
            self.stats.summary_calls += 1

    def finish(self) -> dict:
        self.stats.latency_ms = int((time.perf_counter() - self._started) * 1000)
        return self.stats.to_api()


async def run_agent_loop(
    messages: list[dict],
    client: DeepSeekClient,
    registry: ToolHub,
    config: AgentConfig,
    *,
    conversation_id: str = "",
    plugins: list[str] | None = None,
    meter: UsageMeter | None = None,
) -> AsyncIterator[dict]:
    """运行 Agent 循环，逐事件 yield。

    事件序列：token* → (thought? → action → observation)* → usage? → final | error

    `plugins` 为插件白名单（None = 全部工具）。给未来"按页面裁剪工具面"留的口子——
    工具 schema 是真实的 token 成本，全量注入约 2k token/轮。
    """
    safety = SafetyGate(max_steps=config.max_steps)
    meter = meter or UsageMeter(conversation_id, getattr(client, "model", ""))
    conversation = await _build_context(messages, client, config, conversation_id, meter)

    # 整个循环（含它内部发起的工具调用）都带 `conversationId`。
    # 注意：`contextvars` 在 `async for` 的每次迭代里**不会被自动传播**到
    # `registry.execute` 内部的同步代码——工具调用那一段由 `ToolHub._audit`
    # 自己再 bind 一次（那里同时补上 `auditId` / `tool`），两边合起来才是完整链路。
    with logging_setup.bind(conversationId=conversation_id):
        logger.info("Agent 循环开始：%d 条历史消息，%d 个工具可用，步数预算 %d",
                    len(conversation), len(registry.specs()), config.max_steps)
        while True:
            if safety.exhausted:
                # "为什么这次没答完"最需要这条：前端的错误提示很短，日志要留下预算与轮次
                logger.warning("步数预算耗尽，终止本轮（max_steps=%d）", config.max_steps)
                yield make_event("error", message="已达到步数预算上限，已终止本轮")
                usage = meter.finish()
                meter.stats.persist()
                yield make_event("usage", usage=usage)
                return

            async for event in _one_model_turn(
                conversation, client, registry, config, safety,
                conversation_id=conversation_id, plugins=plugins, meter=meter,
            ):
                yield event
                if event["type"] in ("final", "error"):
                    # 用量在**收尾时**发一条：命中量要等所有 LLM 调用（含摘要重算）都结束
                    # 才有完整答案；提前发会让前端显示一个偏低的数字
                    usage = meter.finish()
                    meter.stats.persist()
                    yield make_event("usage", usage=usage)
                    return


def _with_system_prompt(messages: list[dict], system_prompt: str) -> list[dict]:
    """把系统提示词固定为对话首条消息（覆盖外部传入的旧 system 消息）。"""
    conversation = list(messages)
    if system_prompt:
        if conversation and conversation[0].get("role") == "system":
            conversation[0] = {"role": "system", "content": system_prompt}
        else:
            conversation.insert(0, {"role": "system", "content": system_prompt})
    return conversation


async def _build_context(
    messages: list[dict],
    client: DeepSeekClient,
    config: AgentConfig,
    conversation_id: str,
    meter: UsageMeter,
) -> list[dict]:
    """组装带**稳定前缀**的上下文；只在超预算时才折叠一次历史。

    返回值**就是**后续每轮复用的那个数组：工具轮次往里 append，
    因此下一轮发出的前缀与这一轮逐字节相同 —— 这就是缓存能命中的全部前提。
    """
    convo = _with_system_prompt(messages, config.system_prompt)
    state = cache.load_state(conversation_id)
    plan = cache.plan_prefix(
        convo, state,
        window_tokens=config.context_window_tokens,
        threshold=config.context_threshold,
        keep_recent_turns=config.keep_recent_turns,
    )
    if not plan.needs_summary():
        return plan.messages

    summary = await _summarize(client, plan.truncated, meter)
    upto = state.summarized_upto + len(plan.truncated)
    new_state = cache.freeze_summary(conversation_id, summary, upto)
    logger.info(
        "折叠历史：会话 %s 的摘要边界推进到 %d 条（前缀变动一次，之后恢复稳定）",
        conversation_id[:8] or "-", new_state.summarized_upto,
    )
    replanned = cache.plan_prefix(
        convo, new_state,
        window_tokens=config.context_window_tokens,
        threshold=config.context_threshold,
        keep_recent_turns=config.keep_recent_turns,
    )
    return replanned.messages


async def _one_model_turn(
    context: list[dict],
    client: DeepSeekClient,
    registry: ToolHub,
    config: AgentConfig,
    safety: SafetyGate,
    *,
    conversation_id: str = "",
    plugins: list[str] | None = None,
    meter: UsageMeter | None = None,
) -> AsyncIterator[dict]:
    """一轮「模型流式输出 →（可能）工具循环」，工具回填后返回交由外层再开一轮。"""
    async for event in client.stream_chat(
        context,
        tools=registry.list_api_for(plugins),
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    ):
        if event["type"] == "delta":
            yield make_event("token", text=event["text"])
        elif event["type"] == "usage":
            # include_usage 的独立 usage 帧；与 end 事件里的 usage 是同一份数据。
            # **刻意不在这里 feed**：两处都收会把每次调用记成两次，命中率看着翻倍。
            continue
        elif event["type"] == "end":
            if meter is not None:
                meter.feed(event.get("usage"))
            assistant = {"role": "assistant", "content": event["content"]}
            if event["tool_calls"]:
                assistant["tool_calls"] = event["tool_calls"]

            if not assistant.get("tool_calls"):
                yield make_event("final", content=assistant["content"])
                context.append(assistant)
                return

            if assistant["content"]:
                yield make_event("thought", text=assistant["content"])
            context.append(assistant)

            for call in assistant["tool_calls"]:
                if safety.is_loop(call):
                    # 这条不写日志的话，用户只看到"模型说了一句话就停了"，
                    # 而真实原因是同一个调用连打了 3 次
                    logger.warning("检测到重复的工具调用，终止本轮：%s %s",
                                   call.get("name"), str(call.get("arguments"))[:200])
                    yield make_event("error", message="检测到重复的工具调用，已终止本轮")
                    return

                spec = registry.get(str(call.get("name") or ""))
                audit_id = uuid.uuid4().hex[:12]
                yield make_event(
                    "action",
                    call=call,
                    auditId=audit_id,
                    tool=_tool_meta(spec, call),
                )

                invocation = await registry.execute(
                    str(call.get("name") or ""),
                    call.get("arguments") or "{}",
                    ToolContext(
                        audit_id=audit_id,
                        source="agent",
                        conversation_id=conversation_id,
                    ),
                )
                safety.consume_step()

                if invocation.status is ToolStatus.NEEDS_APPROVAL:
                    # 确认门被触发是"用户接下来该做点什么"的信号，
                    # 也是排查"为什么危险工具没执行"的第一现场
                    logger.warning("工具被确认门拦下：%s（pendingId=%s）",
                                   invocation.tool, invocation.pending_id)
                    # 危险操作：**不执行**，把"要不要做"交回给人。
                    yield make_event(
                        "approval_required",
                        pendingId=invocation.pending_id,
                        tool=invocation.tool,
                        risk=invocation.risk.value,
                        summary=invocation.risk_reason,
                        auditId=invocation.audit_id,
                    )

                yield make_event(
                    "observation",
                    result={
                        "ok": not invocation.is_error,
                        "summary": _summarize_result(invocation),
                        "status": invocation.status.value,
                    },
                    invocation={
                        "toolCallId": invocation.request_id,
                        "tool": invocation.tool,
                        "auditId": invocation.audit_id,
                        "durationMs": invocation.duration_ms,
                        "risk": invocation.risk.value,
                        "plugin": invocation.plugin_id,
                        "status": invocation.status.value,
                        "isError": invocation.is_error,
                    },
                )
                context.append({
                    "role": "tool",
                    "tool_call_id": call.get("id") or invocation.request_id,
                    "content": invocation.content,
                })
            return  # 工具结果已回填，外层 while 发起下一轮


def _tool_meta(spec, call: dict) -> dict:
    """给前端的工具元信息（风险徽章靠它上色；未知工具也要能显示出来）。"""
    if spec is None:
        return {"name": str(call.get("name") or ""), "risk": Risk.READ.value,
                "plugin": "", "known": False}
    return {"name": spec.name, "risk": spec.risk.value, "plugin": spec.plugin_id, "known": True}


def _summarize_result(invocation) -> str:
    """观测摘要：给人看短的一行，给模型看完整 content（在 tool 消息里）。"""
    text = " ".join((invocation.content or invocation.error or "").split())
    if len(text) > 220:
        text = text[:220] + "…"
    return text


async def _summarize(client: DeepSeekClient, messages: list[dict], meter: UsageMeter) -> str:
    text = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in messages)
    summary = await client.chat(
        [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        temperature=0.3,
    )
    # 摘要调用的用量单独计：它的输入是历史原文，几乎必然**全 miss**——
    # 这正是"缓存优化到底有没有省到钱"最该看的那部分开销
    meter.feed(client.last_usage, summary=True)
    return summary
