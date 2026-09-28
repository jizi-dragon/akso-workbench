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
    confirm_required  模型主动请求实时确认 {pendingId, question, details, target, timeoutS}
    confirm_resolved  实时确认拿到答复 {pendingId, approved, expired, answer}
    usage             本轮 token 用量与**缓存命中量**（一次回答只发一条，收尾时）
    final            最终回答
    error            终止性错误
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

from .. import cache, logging_setup
from ..plugin_api import Risk, ToolContext, ToolHub, ToolStatus
from ..services.deepseek import DeepSeekClient
from .context import content_to_text, estimate_messages_tokens
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
                logger.warning("步数预算耗尽，终止本轮（max_steps=%d，已用 %d 次工具调用）",
                               config.max_steps, safety.steps_used)
                # 事件里带 `reason` 让前端能分开渲染（不再是一律红色报错）；
                # 文案由 `_budget_exhausted_message` 统一给（另一处出口在工具批次内）。
                yield make_event("error", reason="budget_exhausted",
                                 message=_budget_exhausted_message(config.max_steps))
                yield make_event("usage", usage=_finish_usage(meter, config))
                return

            # ★ 每次模型调用前**复检上下文预算**（不变量 I17，2026-09-24 加，修复缺陷②）。
            # 旧实现只在整轮开始前算一次（`_build_context`），而工具结果会不断往
            # `conversation` 里追加（一条 read_web_page 正文可到 12k 字符）——实测探针：
            # 预算 1000 token 时第 5 次请求已 12,059，而折叠发生 **0** 次。超窗后是
            # **平台**拒回来的，那句话用户与模型都无法据以行动。
            if _over_hard_limit(conversation, config):
                window = int(config.context_hard_limit_tokens
                             or config.context_window_tokens or 0)
                logger.warning("上下文超过硬上限，终止本轮（估算 %d > 上限 %d）",
                               estimate_messages_tokens(conversation), window)
                yield make_event(
                    "error",
                    reason="context_exceeded",
                    message=(
                        f"这次要发上去的内容超过了模型的上下文窗口（约 {window} token），"
                        "再发就会被平台拒绝。**已完成的部分都保留着**——"
                        "请把这件事分成两次做，比如「先读其中两份，我确认后你再读剩下的」。"
                    ),
                )
                yield make_event("usage", usage=_finish_usage(meter, config))
                return

            async for event in _one_model_turn(
                conversation, client, registry, config, safety,
                conversation_id=conversation_id, plugins=plugins, meter=meter,
            ):
                yield event
                if event["type"] in ("final", "error"):
                    # 用量在**收尾时**发一条：命中量要等所有 LLM 调用（含摘要重算）都结束
                    # 才有完整答案；提前发会让前端显示一个偏低的数字
                    yield make_event("usage", usage=_finish_usage(meter, config))
                    return


def _context_usage(meter: UsageMeter, config: AgentConfig) -> dict:
    """上下文用量（输入框下方那行常驻指标用，2026-09-22）。

    `contextTokens` 取**平台回的 `prompt_tokens`**（最近一次调用），不是估算：
    它同时包含系统提示词 + 冻结摘要 + 原文历史 + 工具 schema ——
    也就是"这一次到底发了多少上去"。估算值会偏低，而用户是拿这个数判断"还能聊多久"。
    """
    tokens = int(getattr(meter.stats, "last_prompt_tokens", 0) or 0)
    window = int(config.context_window_tokens or 0)
    return {
        "contextTokens": tokens,
        "contextWindow": window,
        "contextThreshold": float(config.context_threshold or 0),
        "contextRatio": round(tokens / window, 4) if window else 0.0,
    }


def _budget_exhausted_message(max_steps: int) -> str:
    """步数用尽时的**可行动**文案（两处出口共用，避免两句话漂开）。

    ⚠️ 这不是"错误"，是"这一轮到头了"——两者对用户要做的动作完全不同：
    错误要他去查，预算耗尽只需他回一句「继续」。所以文案说清三件事：
    发生了什么、怎么接着做、去哪儿调大。
    """
    return (
        f"这一轮的工具调用次数用完了（{max_steps} 次）。"
        "**已完成的部分都保留着**——直接回一句「继续」我就接着做。"
        "如果是配置这类多步任务反复撞上限，可以在「设置 → 推理参数」里调大这个数字。"
    )


def _finish_usage(meter: UsageMeter, config: AgentConfig) -> dict:
    """收尾统一出口：算 latency → 补上下文口径 → 落库（三个终止分支共用）。"""
    usage = meter.finish()
    usage.update(_context_usage(meter, config))
    meter.stats.persist()
    return usage


def _over_hard_limit(conversation: list[dict], config: AgentConfig) -> bool:
    """这次请求是不是已经超过**硬上限**（不变量 I17 的一半，见 `AgentConfig`）。

    只用**估算值**，而它**偏低**（`estimate_messages_tokens` 只算 `content`，
    不算 `tool_calls` / 工具 schema）—— 这个方向是**刻意**的：宁可漏判
    （少一次保护）也不要误判（把正常请求掐掉）。
    """
    window = int(config.context_hard_limit_tokens or config.context_window_tokens or 0)
    if window <= 0:
        return False
    return estimate_messages_tokens(conversation) > window


def _fill_unexecuted(context: list[dict], calls: list[dict], reason: str) -> None:
    """为**未执行**的 `tool_call` 补一条 `tool` 观测（不变量 I16）。

    ## 为什么必须补，而不是"跳过就算了"

    `assistant.tool_calls` 里的**每一个 id** 都必须有配对的 `tool` 消息。少一条，
    这段历史就是**非法**的：下一次请求平台会回 `tool_call_id not found`，
    而"对话级断点续跑"（重放历史）永远做不成——重放一段非法历史没有意义。

    ## 为什么原因要写进正文

    模型**看得见**它才可能自我纠正（"预算用完了"→ 下次把任务拆小）。静默丢掉这些调用，
    模型会以为它们**执行过**了——那是比失败更坏的结局：它会据此下结论。

    ## 一处实现，三个调用方

    步数预算（本文件）／用户取消／超时（后续 `_fill_unexecuted` 的另两个调用方）。
    三个都要"未执行的调用必须留下痕迹"，所以只写一份。
    """
    for call in calls:
        context.append({
            "role": "tool",
            "tool_call_id": call.get("id") or "",
            "content": f"[未执行] {reason}",
        })


def _skipped_observation(call: dict, reason: str) -> dict:
    """未执行调用的 `observation` 事件（前端据此把那张卡画成"已跳过"而不是转圈）。"""
    return make_event(
        "observation",
        result={"ok": False, "summary": f"未执行：{reason}", "status": "skipped"},
        invocation={
            "toolCallId": call.get("id") or "",
            "tool": str(call.get("name") or ""),
            "auditId": "",
            "durationMs": 0,
            "risk": Risk.READ.value,
            "plugin": "",
            "status": "skipped",
            "isError": True,
        },
    )


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
            if event.get("finish_reason") == "length":
                # ⚠️ 输出被长度上限截断。**必须在这里拦下**，不能让它流到工具层：
                # 一个正在生成的 tool_call 被截断后是**半截 JSON**，上层只会报
                # 「参数解析失败」——模型据此完全推不出真正的原因，最可能的反应是
                # 原样重试，而重试大概率再截断一次（白烧步数，见 PITFALLS #58）。
                logger.warning(
                    "模型输出被长度上限截断（finish_reason=length，%d 个工具调用，"
                    "正文 %d 字）——本轮终止，避免把半截参数喂给工具",
                    len(event.get("tool_calls") or []), len(event.get("content") or ""))
                yield make_event(
                    "error",
                    reason="output_truncated",
                    message=(
                        "模型这次的输出被长度上限截断了，内容不完整，**没有**执行任何工具"
                        "（半截参数执行出去比不执行危险得多）。"
                        "请把这件事拆小一点再说一次——"
                        "比如「先建对象和字段，建好我再让你加工作流」，"
                        "或者分几次把蓝图补全。"
                    ),
                )
                return

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

            for index, call in enumerate(assistant["tool_calls"]):
                # ★ 步数预算**逐调用**检查（不变量 I16，2026-09-24 加，修复缺陷③）。
                # 旧实现只在**两次模型轮次之间**检查（外层 while 的 `safety.exhausted`），
                # 于是模型一轮里发 N 个 tool_call 就 N 个全执行——实测 max_steps=2 +
                # 首轮 5 个调用 ⇒ **5 个全跑**。`safety.SafetyGate` 的文档串当时自己
                # 就写着"只在两次模型轮次之间检查"，而没有任何断言去读那句话。
                if safety.exhausted:
                    remaining = assistant["tool_calls"][index:]
                    reason = "步数预算已用完"
                    # 未执行的每一条都要留下痕迹（I16）：少了它们，这段历史对平台是非法的
                    _fill_unexecuted(context, remaining, reason)
                    for pending in remaining:
                        yield _skipped_observation(pending, reason)
                    logger.warning("步数预算在工具批次内用尽（max_steps=%d，已用 %d），"
                                   "本批 %d 个调用只有前 %d 个执行了",
                                   config.max_steps, safety.steps_used,
                                   len(assistant["tool_calls"]), index)
                    yield make_event("error", reason="budget_exhausted",
                                     message=_budget_exhausted_message(config.max_steps))
                    return

                if safety.is_loop(call):
                    # 这条不写日志的话，用户只看到"模型说了一句话就停了"，
                    # 而真实原因是同一个调用连打了 3 次
                    logger.warning("检测到重复的工具调用，终止本轮：%s %s",
                                   call.get("name"), str(call.get("arguments"))[:200])
                    # 同样要回填（I16）：旧实现在这里直接 `return`，于是 assistant 那条
                    # 消息带着 N 个 tool_calls 却只有前面几个有配对响应 —— 非法历史。
                    remainder = assistant["tool_calls"][index:]
                    _fill_unexecuted(context, remainder, "检测到重复调用，本轮已终止")
                    for pending in remainder:
                        yield _skipped_observation(pending, "检测到重复调用，本轮已终止")
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

                if invocation.status is ToolStatus.NEEDS_CONFIRMATION:
                    # 实时确认：**本轮对话就地暂停**，等用户点「同意 / 拒绝」。
                    #
                    # 为什么要在这里等、而不是在 `ToolHub.execute` 里等：
                    # `execute` 一旦 `await`，这个异步生成器就挂起了，下面的
                    # `confirm_required` 事件根本发不出去——界面不知道有东西要确认，
                    # 用户也就永远没法点。所以 execute 只登记（返回 NEEDS_CONFIRMATION），
                    # 事件发出去之后再等。这是本模块唯一一处"工具调用横跨两次 yield"。
                    yield make_event(
                        "confirm_required",
                        auditId=invocation.audit_id,
                        tool=invocation.tool,
                        **invocation.confirm,
                    )
                    outcome = await registry.await_confirmation(
                        invocation.pending_id,
                        float(invocation.confirm.get("timeoutS") or 0),
                    )
                    logger.info("实时确认有答复：%s → %s（approved=%s expired=%s）",
                                invocation.pending_id, invocation.tool,
                                outcome.approved, outcome.expired)
                    # 答复立刻回给界面：确认卡要就地变成「已同意 / 已拒绝」，
                    # 不能等到下一条 observation（那时用户已经在看别的东西了）
                    yield make_event(
                        "confirm_resolved",
                        pendingId=invocation.pending_id,
                        approved=outcome.approved,
                        expired=outcome.expired,
                        answer=outcome.answer,
                    )
                    invocation = _apply_confirmation(invocation, outcome)

                elif invocation.status is ToolStatus.NEEDS_APPROVAL:
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
                    # ★ 就地等答复（2026-09-22 加，决策卡 G2）。旧实现在这里**只发事件不等**
                    # ——它假设"模型会重试同一次调用"（`claim_approved` 那条路）。实测的失败形态：
                    # 模型把话说完这一轮就结束了，用户随后点「放行」时**没有任何人在等**，
                    # 那条批准被搁置、界面表现为"我点了，什么都没发生"（15:08 真机实测）。
                    # 现在与实时确认对称：等答复 → 同意则**由服务端用完全相同的参数执行**，
                    # 用户只点一次；拒绝/超时则把答复如实写进观测，本轮结束。
                    outcome = await registry.await_confirmation(
                        invocation.pending_id, config.approval_wait_s)
                    logger.info("风险确认有答复：%s → %s（approved=%s expired=%s）",
                                invocation.pending_id, invocation.tool,
                                outcome.approved, outcome.expired)
                    # 答复立刻回给界面：确认卡要就地变成「已放行 / 已拒绝」，
                    # 不能等到下一条 observation（那时用户已经在看别的东西了）
                    yield make_event(
                        "confirm_resolved",
                        pendingId=invocation.pending_id,
                        approved=outcome.approved,
                        expired=outcome.expired,
                        answer=outcome.answer,
                        tool=invocation.tool,
                        risk=invocation.risk.value,
                    )
                    if outcome.approved:
                        # **执行**：重投一次同样的调用，服务端按「工具 + 参数指纹」认领那条批准
                        # （`claim_approved`，一次性）。审计里因此有**两行**——一行 needs_approval
                        # （谁在什么时候被拦下）、一行真实结果。这与旧实现里"模型自己重试"留下的
                        # 两行完全同形，所以排查口径不变。
                        retry_audit = uuid.uuid4().hex[:12]
                        yield make_event("action", call=call, auditId=retry_audit,
                                         tool=_tool_meta(spec, call))
                        invocation = await registry.execute(
                            str(call.get("name") or ""),
                            call.get("arguments") or "{}",
                            ToolContext(
                                audit_id=retry_audit,
                                source="agent",
                                conversation_id=conversation_id,
                            ),
                        )
                    else:
                        invocation = _apply_confirmation(invocation, outcome)

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


def _apply_confirmation(invocation, outcome) -> Any:
    """把用户的答复变成这次工具调用的**终态**。

    `request_user_confirmation` 的 handler 从头到尾**不会被调用**——它没有"执行"这回事，
    用户的答复本身就是结果。所以这里直接替换掉 invocation：

    | 答复 | status | 模型的观测 |
    |---|---|---|
    | 同意 | `OK` | "现在可以调用 `X`，参数必须一致" |
    | 拒绝 | `REJECTED` | "用户拒绝了，不要重试，也别绕过去" |
    | 超时/中断 | `TIMEOUT` | "没等到答复，按未同意处理" |

    三种都**不是** `is_error` 之外的含糊状态：模型必须能从观测里分辨
    "再问一次也没用"（拒绝）和"用户可能只是没看见"（超时）。
    """
    if outcome.approved:
        status = ToolStatus.OK
    elif outcome.expired:
        status = ToolStatus.TIMEOUT
    else:
        status = ToolStatus.REJECTED
    return replace(
        invocation,
        status=status,
        content=outcome.answer or invocation.content,
        error="" if outcome.approved else (outcome.answer or "未获同意"),
        pending_id=invocation.pending_id,
    )


async def _summarize(client: DeepSeekClient, messages: list[dict], meter: UsageMeter) -> str:
    # content 可能是多模态 parts（用户传了图片）⇒ 统一走 `content_to_text` 压成文本，
    # 否则摘要里会出现 `[{'type': 'image_url', ...}]` 这种把 base64 塞进摘要的形态。
    text = "\n".join(f"{m.get('role')}: {content_to_text(m.get('content'))}"
                     for m in messages)
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
