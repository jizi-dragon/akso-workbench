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
    cancelled        ★ 用户请求停止，在**两次动作之间**收尾（计划 §5.6）
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import replace
from typing import Any

from .. import cache, logging_setup
from ..plugin_api import Risk, ToolContext, ToolHub, ToolStatus
from ..services.deepseek import DeepSeekClient
from . import trace
from .context import content_to_text, estimate_messages_tokens
from .safety import SafetyGate
from .types import AgentConfig, make_event

logger = logging.getLogger("workbench.harness")

#: 等待确认时"多久问一次停止信号"。0.2s 是**手感与开销的折中**：
#: 用户点停止到真正放弃等待，最长多等这么久（人感觉不到），
#: 而一次空转只是读一个 bool —— 一个 300 秒的确认等待 ≈ 1500 次读，可忽略。
#: **不要调成 0**：那会变成忙等，把事件循环占满。
_STOP_POLL_INTERVAL_S = 0.2

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
        self._finished: dict | None = None
        self._persisted = False

    def feed(self, usage: dict | None, *, summary: bool = False) -> None:
        if not usage:
            return
        self.stats.add(usage)
        if summary:
            self.stats.summary_calls += 1

    def finish(self) -> dict:
        """收尾：冻结延迟并给出 API 形状。**幂等** —— 第二次返回同一个 dict。

        幂等是必需的，不是优化：`run_agent_loop` 现在保证"**任何出口都入账**"
        （含异常出口），而正常出口早就调过一次 —— 不幂等就会把延迟算两遍
        （第二次的时间戳更晚，账面看起来只是"慢了一点"，没人会怀疑）。
        """
        if self._finished is None:
            self.stats.latency_ms = int((time.perf_counter() - self._started) * 1000)
            self._finished = self.stats.to_api()
        return self._finished

    def persist(self) -> bool:
        """把用量落库。**幂等** —— 第二次调用不写第二行，返回 `False`。

        与 `finish` 同理，而且后果更重：不幂等就是**一条回答两行用量**，
        那比丢一行更难发现 —— 丢一行让数字偏小（有人会去查），
        多一行让账单偏大而且是"双份"，看起来只像"这次特别贵"。
        """
        if self._persisted:
            return False
        self._persisted = True
        return self.stats.persist()


@contextlib.contextmanager
def _accounted(meter: UsageMeter, recorder: trace.RunRecorder | None = None) -> Iterator[None]:
    """离开这个块时**必定**把用量入账（计划 §5.4a）并收掉运行记录（§6.1）。

    ## 为什么必须有它

    改造前全仓只有两处 `persist()`，都在**正常收尾**的路径上。于是任何异常出口
    （LLM 抛错、工具层意外抛出、`asyncio` 取消、消费方断开）**一条用量都不落** ——
    账面表现是"这次回答没花钱"，而钱已经花了。`deepseek_usage` 是唯一的用量事实源，
    命中率与"省钱口径"都从它算，所以这类丢失是**不可恢复**的偏差。

    ## 三个约定

    1. **幂等**：正常出口已落过库，这里靠 `UsageMeter.persist` 的守卫不写第二行。
    2. **绝不 `yield`**：`finally` 可能正因 `GeneratorExit`（消费方 `aclose()`）而执行，
       此时 `yield` 会抛 `RuntimeError: async generator ignored GeneratorExit`。
       **入账是账本的事，`usage` 事件是界面的事 —— 两者分开处理**：事件只在正常出口发。
    3. **不吞掉异常**：只做记账，让原异常继续往外抛（`routes_chat` 那侧已有兜底）。
    """
    try:
        yield
    except GeneratorExit:
        # 消费方断开（关页面 / 切会话）。**与"它坏了"分开记** —— 见 `trace.RUN_ABORTED`。
        if recorder is not None:
            recorder.abort()
        raise
    except BaseException as exc:
        # ★ 异常出口必须把 run 标成失败。不标的话 `finish()` 会把它当成功
        #   （`status` 仍是 `running` ⇒ 见 `RunRecorder.finish` 的兜底），
        #   于是**一次崩溃在数据里看起来是一次成功的回合** —— 而那是这个模块
        #   最不该犯的错（它的全部价值就是"哪一回合坏了"）。
        if recorder is not None:
            recorder.fail(f"{type(exc).__name__}: {exc}")
        raise
    finally:
        # 顺序是**承重**的：先入账（计费口径），再收运行记录（轨迹口径）。
        # 反过来会出现"run 里记着用量、用量表里却没有"这种不一致 ——
        # 而 `recorder.finish(usage)` 的入参正是这份用量，读的必须是**已冻结**的那份
        # （`meter.finish()` 幂等，这里再调一次只是取回同一个 dict）。
        usage = meter.finish()
        meter.persist()
        if recorder is not None:
            recorder.finish(usage)


class _SpanClient:
    """包住 LLM 客户端，为**每一次模型调用**记一条 `model` span。

    ## 为什么包客户端，而不是在 `_one_model_turn` 里埋点

    `_one_model_turn` 的 `async for` 有**多个提前 `return`**（截断终止、早退分支）。
    要覆盖全部出口就得把那一整段缩进一层 —— 而缩进是纯机械操作，**最容易出笔误、
    且出了笔误测试未必变红**。包一层客户端等价、不动调用方一个字符，
    还顺带覆盖了摘要那次 `chat()` 调用（它也是真实的模型开销）。

    ## ★ 记在 `yield` **之前**（PITFALLS #99 的教训）

    消费方收到 `end` 就 `return`，于是本生成器在 `yield` 处被 `aclose()` ——
    **循环之后再记就永远记不上**。这条坑本会话踩过两次（#98 / #99），
    所以这里刻意写在 `yield` 之前。
    """

    def __init__(self, inner, recorder: trace.RunRecorder) -> None:
        self._inner = inner
        self._recorder = recorder
        self.last_usage: dict = {}

    @property
    def model(self) -> str:
        return str(getattr(self._inner, "model", "") or "")

    async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
        started = time.perf_counter()
        status, error, finish = "ok", "", ""
        recorded = False
        try:
            async for event in self._inner.stream_chat(
                messages, tools=tools, temperature=temperature, max_tokens=max_tokens
            ):
                if event.get("type") == "end":
                    finish = str(event.get("finish_reason") or "")
                    if finish == "length":
                        # 被长度上限截断是**这一步**的失败，不是整轮的失败：
                        # 时间线要能指出"是这里截断的"（PITFALLS #58）
                        status, error = "truncated", "输出被长度上限截断"
                    self.last_usage = dict(event.get("usage") or {})
                    # ★ 记在 `yield` **之前**（见类文档串）
                    self._record(started, status, error, finish, messages, tools)
                    recorded = True
                yield event
        except Exception as exc:  # noqa: BLE001 —— 失败也要留下这一步
            self._record(started, "error", str(exc), finish, messages, tools)
            recorded = True
            raise
        finally:
            self.last_usage = dict(getattr(self._inner, "last_usage", {}) or {})
            if not recorded:
                # 没收到 `end`、也没抛异常 —— 消费方提前放弃，或流本身不完整。
                # **仍然留下这一步**：时间线上缺的那一块恰好是"出事的那一步"。
                self._record(started, "aborted", "", finish, messages, tools)

    def _record(self, started: float, status: str, error: str, finish: str,
                messages: list[dict], tools) -> None:
        self._recorder.span(
            trace.SPAN_MODEL, self.model, started=started, status=status, error=error,
            attrs={
                "messages": len(messages),
                "tools": len(tools or []),
                "finishReason": finish,
                # 用量放 attrs 而不新开列：它是**这一步**的成本，而 run 级的总量
                # 已经在 `agent_run` 上 —— 不建第二个"看起来也是总量"的列（铁律 28）
                "usage": self.last_usage,
            },
        )

    async def chat(self, messages, temperature=0.7, max_tokens=None, json_mode=False):
        started = time.perf_counter()
        try:
            text = await self._inner.chat(messages, temperature=temperature,
                                          max_tokens=max_tokens, json_mode=json_mode)
        except Exception as exc:  # noqa: BLE001
            self._recorder.span(trace.SPAN_MODEL, self.model, started=started,
                                status="error", error=str(exc),
                                attrs={"kind": "chat", "messages": len(messages)})
            raise
        self.last_usage = dict(getattr(self._inner, "last_usage", {}) or {})
        self._recorder.span(trace.SPAN_MODEL, self.model, started=started, status="ok",
                            attrs={"kind": "chat", "messages": len(messages),
                                   "usage": self.last_usage})
        return text


async def run_agent_loop(
    messages: list[dict],
    client: DeepSeekClient,
    registry: ToolHub,
    config: AgentConfig,
    *,
    conversation_id: str = "",
    plugins: list[str] | None = None,
    meter: UsageMeter | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> AsyncIterator[dict]:
    """运行 Agent 循环，逐事件 yield。

    事件序列：token* → (thought? → action → observation)* → usage? → final | error | cancelled

    `should_stop`：**协作式取消**（计划 §5.6）。在三个检查点被问一次 ——
    每次模型调用前、每次工具调用前、等待确认答复期间。它是"能不能停下来"的全部机制；
    不再是 `None` 时行为与从前**逐字相同**（默认关，所以不改变任何既有调用方）。
    见 `harness/stop.py` 讲为什么是协作式而不是 `Task.cancel()`。

    `plugins` 为插件白名单（None = 全部工具）。给未来"按页面裁剪工具面"留的口子——
    工具 schema 是真实的 token 成本，全量注入约 2k token/轮。
    """
    safety = SafetyGate(max_steps=config.max_steps)
    meter = meter or UsageMeter(conversation_id, getattr(client, "model", ""))
    # 运行记录：**一次用户回合是一个实体**（计划 §6.1）。它在 `_accounted` 的 `finally`
    # 里收尾 —— 与用量同一条理由：循环有多个出口（final / error / 预算 / 上下文 /
    # 异常 / 消费方断开），收尾只能挂在 `finally` 上，交给调用方记得就会重演 §5.4a。
    recorder = trace.RunRecorder(conversation_id, getattr(client, "model", ""))
    # 包一层客户端，为**每一次**模型调用记一条 `model` span（含摘要那次 `chat` ——
    # 它也是真实开销，而且几乎必然全 miss，见 `cache.UsageStats.summary_calls`）。
    client = _SpanClient(client, recorder)
    conversation = await _build_context(messages, client, config, conversation_id, meter)

    # 整个循环（含它内部发起的工具调用）都带 `conversationId`。
    # 注意：`contextvars` 在 `async for` 的每次迭代里**不会被自动传播**到
    # `registry.execute` 内部的同步代码——工具调用那一段由 `ToolHub._audit`
    # 自己再 bind 一次（那里同时补上 `auditId` / `tool`），两边合起来才是完整链路。
    #
    # `_accounted` 保证**任何出口**都入账（含异常出口），见它的文档串与计划 §5.4a。
    with logging_setup.bind(conversationId=conversation_id), _accounted(meter, recorder):
        logger.info("Agent 循环开始：%d 条历史消息，%d 个工具可用，步数预算 %d",
                    len(conversation), len(registry.specs()), config.max_steps)
        while True:
            # ★ 检查点①：每次模型调用前（计划 §5.6）。
            # 放在这里而不是循环开头之后：模型调用是**唯一一处会花钱**的动作，
            # 用户点了停止之后不该再发一次请求。
            if _stopped(should_stop):
                logger.info("收到停止请求（检查点：模型调用前），已用 %d 次工具调用",
                            safety.steps_used)
                yield make_event("cancelled", reason="user_stop",
                                 message=_cancelled_message(safety.steps_used))
                recorder.abort()
                yield make_event("usage", usage=_finish_usage(meter, config))
                return

            if safety.exhausted:
                # "为什么这次没答完"最需要这条：前端的错误提示很短，日志要留下预算与轮次
                logger.warning("步数预算耗尽，终止本轮（max_steps=%d，已用 %d 次工具调用）",
                               config.max_steps, safety.steps_used)
                # 事件里带 `reason` 让前端能分开渲染（不再是一律红色报错）；
                # 文案由 `_budget_exhausted_message` 统一给（另一处出口在工具批次内）。
                yield make_event("error", reason="budget_exhausted",
                                 message=_budget_exhausted_message(config.max_steps))
                recorder.fail(f"budget_exhausted: 步数预算耗尽（{config.max_steps}）")
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
                recorder.fail(f"context_exceeded: 上下文超过硬上限（{window} token）")
                yield make_event("usage", usage=_finish_usage(meter, config))
                return

            async for event in _one_model_turn(
                conversation, client, registry, config, safety,
                conversation_id=conversation_id, plugins=plugins, meter=meter,
                recorder=recorder, should_stop=should_stop,
            ):
                # ★ `error` 事件也要把 run 标成失败（2026-09-24）。
                #
                # 这些终止（预算耗尽 / 上下文超限 / 重复调用 / 模型报错）走的是
                # **正常 `return`**，不是抛异常 —— 所以 `_accounted` 的 `except`
                # 看不到它们。不在这里标的话，它们全都会被记成 **succeeded**，
                # 而这几类恰好是用户最常遇到的"没做完"。
                #
                # 放在这里而不是每个 `yield error` 旁边：**这是唯一那个扼流点**
                # （`_one_model_turn` 内部发出的 error 也都从这里过）。
                if event["type"] == "error" and recorder is not None:
                    recorder.fail(str(event.get("message") or event.get("reason") or "error"))
                yield event
                if event["type"] in ("final", "error", "cancelled"):
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


def _cancelled_message(executed_steps: int) -> str:
    """用户点了停止之后的文案（计划 §5.6）。

    ★ **必须说清"已执行的动作不会撤销"**。这是这一条最容易被写错的地方：
    写"已取消"会让人以为"什么都没发生"，而平台侧可能**已经建了东西**。
    工具调用是真调用，停止只能阻止下一步 —— 文案不许把这件事含糊过去。
    """
    if executed_steps <= 0:
        return "已停止。这次还没有执行任何工具调用，没有产生任何影响。"
    return (
        f"已停止（这次已经执行了 {executed_steps} 次工具调用）。"
        "**已经做过的事情不会因为停止而撤销** —— 如果刚才有写操作，"
        "请确认一下平台上的结果是不是你要的。这一轮的记录都留着，你可以直接说「继续」。"
    )


def _stopped(should_stop: Callable[[], bool] | None) -> bool:
    """问一次"用户要停吗"。

    `should_stop` 自己的异常**按"不停"处理**：一个坏掉的停止信号不该把正在干的活打断
    （同 `logging_setup` 的"日志失败不影响主流程"、`Trace._write` 的"轨迹失败不影响回答"）。
    """
    if should_stop is None:
        return False
    try:
        return bool(should_stop())
    except Exception:  # noqa: BLE001 —— 见 docstring
        logger.warning("停止信号求值失败，按「不停」处理", exc_info=True)
        return False


async def _await_confirmation_or_stop(
    registry: ToolHub,
    pending_id: str,
    timeout_s: float,
    should_stop: Callable[[], bool] | None,
):
    """等确认答复，但**可以被停止请求打断**。

    ★ 打断走的是 `approvals` **已有**的那条路：`CancelledError` ⇒
    `expire(pending_id, "对话已中断")`（计划 §5.6 点名"**不要另写一套**"）。
    所以这里 cancel 的是**等待任务**，而不是给 `await_confirmation` 加一个
    `should_stop` 形参 —— 那会把"等待 + 超时 + 中断"那套协议复制成两份，
    而两份迟早不一致（本仓已经因为"两处都写全量"栽过，见铁律 28）。

    返回 `None` 表示**因为停止请求而放弃等待**（调用方据此走取消分支）；
    其余情况原样返回 `ConfirmOutcome`。`should_stop is None` 时**一行都不多走**。
    """
    if should_stop is None:
        return await registry.await_confirmation(pending_id, timeout_s)

    task = asyncio.create_task(registry.await_confirmation(pending_id, timeout_s))
    try:
        while True:
            done, _pending = await asyncio.wait({task}, timeout=_STOP_POLL_INTERVAL_S)
            if task in done:
                return task.result()
            if _stopped(should_stop):
                logger.info("等待确认期间收到停止请求，放弃等待：%s", pending_id)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                return None
    finally:
        if not task.done():  # pragma: no cover —— 消费方放弃时的兜底
            task.cancel()


def _finish_usage(meter: UsageMeter, config: AgentConfig) -> dict:
    """收尾统一出口：算 latency → 补上下文口径 → 落库（三个终止分支共用）。

    落库走 `meter.persist()`（**幂等**）而不是 `meter.stats.persist()`：
    `run_agent_loop` 的 `_accounted` 会在异常出口再落一次，没有幂等守卫
    就会写出两行用量。
    """
    usage = meter.finish()
    usage.update(_context_usage(meter, config))
    meter.persist()
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


def _cancel_events(context: list[dict], calls: list[dict], index: int, reason: str,
                   steps_used: int, recorder: trace.RunRecorder | None) -> list[dict]:
    """取消一个工具批次的**统一收尾**（三个检查点共用）。返回**要发的事件列表**。

    抽出来是因为三处要做的事**逐字相同**：回填未执行调用 → 记 span 状态 →
    逐条发"跳过"观测 → 发 `cancelled`。抄三遍的话，将来改文案或改回填口径
    必然只改到一两处 —— 而**半截的取消收尾正是 §5.4c 那个缺陷的形态**。

    ⚠️ 返回列表而不是生成器：调用方是**异步**生成器，`yield from` 在那里是语法错误，
    而 `for e in ...: yield e` 也要求它是个可迭代对象 —— 列表最简单，
    且这三个事件本来就要一次性发完（没有"边算边发"的必要）。
    """
    remainder = calls[index:]
    _fill_unexecuted(context, remainder, reason)
    if recorder is not None:
        recorder.abort()
    events = [_skipped_observation(pending, reason) for pending in remainder]
    events.append(make_event("cancelled", reason="user_stop",
                             message=_cancelled_message(steps_used)))
    return events


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
    recorder: trace.RunRecorder | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> AsyncIterator[dict]:
    """一轮「模型流式输出 →（可能）工具循环」，工具回填后返回交由外层再开一轮。"""
    ended = False
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
            ended = True
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
                # ★ 检查点②：每次工具调用前（计划 §5.6）。
                #
                # 放在**预算检查之前**：用户说停就停，与预算还剩多少无关。
                # 也放在 `action` 事件之前 —— 否则界面会先显示"正在调用 X"，
                # 然后立刻显示"已停止"，而 X 根本没跑（那句"正在调用"是假的）。
                if _stopped(should_stop):
                    logger.info("收到停止请求（检查点：工具调用前），本批还有 %d 个调用未执行",
                                len(assistant["tool_calls"]) - index)
                    # 未执行的每一条都要回填（与预算耗尽 / 重复调用共用同一条纪律，I16）：
                    # 少一条，这段历史对平台就是**非法**的（assistant 带了 tool_calls
                    # 却没有配对响应）
                    for _ce in _cancel_events(context, assistant["tool_calls"], index,
                                              "用户请求停止", safety.steps_used, recorder):
                        yield _ce
                    return

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
                    outcome = await _await_confirmation_or_stop(
                        registry, invocation.pending_id,
                        float(invocation.confirm.get("timeoutS") or 0), should_stop)
                    if outcome is None:
                        # 等待期间用户点了停止：**这一步没执行**，按未执行回填
                        # （`execute` 只登记、没跑 handler），与检查点②同一条收尾
                        logger.info("实时确认等待被停止请求打断：%s", invocation.tool)
                        for _ce in _cancel_events(context, assistant["tool_calls"], index,
                                                  "用户请求停止", safety.steps_used, recorder):
                            yield _ce
                        return
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
                    outcome = await _await_confirmation_or_stop(
                        registry, invocation.pending_id, config.approval_wait_s, should_stop)
                    if outcome is None:
                        # 危险操作**本来就没执行**（确认门拦下了，handler 没跑）——
                        # 所以这一步与"没发生过"等价，按未执行回填即可
                        logger.info("风险确认等待被停止请求打断：%s", invocation.tool)
                        for _ce in _cancel_events(context, assistant["tool_calls"], index,
                                                  "用户请求停止", safety.steps_used, recorder):
                            yield _ce
                        return
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

                # 工具 span（计划 §6.1 的第二处写入点）。耗时用 `invocation.duration_ms`
                # —— 那是 `ToolHub` 自己测的，**不含**用户等待；若在这里另测墙钟，
                # "用户点了 3 分钟才同意"会被算成工具耗时，于是"哪个工具慢"完全失真。
                if recorder is not None:
                    recorder.span(
                        trace.SPAN_TOOL, str(invocation.tool or call.get("name") or ""),
                        started=0.0, duration_ms=invocation.duration_ms,
                        status="error" if invocation.is_error else "ok",
                        error=_summarize_result(invocation) if invocation.is_error else "",
                        attrs={
                            # 只放**指针**，不放参数与账号：审计才是那些东西的事实源（铁律 28）
                            "auditId": invocation.audit_id,
                            "plugin": invocation.plugin_id,
                            "risk": invocation.risk.value,
                            "status": invocation.status.value,
                            "step": safety.steps_used,
                        },
                    )

                yield make_event(
                    "observation",
                    result={
                        "ok": not invocation.is_error,
                        "summary": _summarize_result(invocation),
                        "status": invocation.status.value,
                    },
                    invocation={
                        # ★ 优先用**模型给的** tool_call id，回落到 `request_id`（2026-09-24 修）。
                        #
                        # 旧写法是 `invocation.request_id`，而那个值来自
                        # `plugin_api._call_id(arguments)` —— 它从**参数 JSON 里**找 id，
                        # 于是模型发起的调用（参数里没有那个键）拿到的是**空串**。
                        # 后果：`observation.invocation.toolCallId` 对每一条真实观测都是 `""`，
                        # 而"因预算/停止被跳过"的观测带的却是真实 id —— **同一个字段两种口径**，
                        # 谁想拿它配对工具卡都会配错（按空串配对 = 全部配到第一条）。
                        #
                        # 回填那侧的写法一直是对的（见 `_skipped_observation` 与
                        # 下面构造 tool 消息的那行：`call.get("id") or invocation.request_id`），
                        # 所以这里对齐到它，而不是反过来。今天没有消费方读这个字段
                        # （`grep toolCallId workbench/static/` 零命中），
                        # 正因为如此现在改**零风险**，而留着它迟早会有人按"它应该是 id"来用。
                        "toolCallId": call.get("id") or invocation.request_id,
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

    if not ended:
        # ★★ **流结束了，但没有 `end` 事件**（2026-09-24 加，修一个会「无限重试」的缺陷）。
        #
        # 不拦的话：本函数什么都不发就返回 ⇒ 外层 `while True` **立刻再开一轮** ⇒
        # 又是一次真实的、付费的模型调用 ⇒ 无限循环。而它**不报错、不会自己停**，
        # 步数预算也拦不住它（`safety` 只计**工具调用**，这条路上一个都没有）。
        #
        # 什么时候真的会发生：连接中途断掉、只收到 EOF 而没有 `[DONE]` ——
        # `deepseek.stream_chat` 在那种情况下循环自然结束，不会 yield `end`。
        # 也就是说，**网络抖一下就可能让用户的钱包持续失血**，而界面上什么都看不出来。
        #
        # ⚠️ 这个检查只能写在**这里**：本函数是外层循环的消费方、不会被提前放弃，
        # 所以 `async for` 之后的代码一定会跑到（与 PITFALLS #98/#99 那个
        # "跨生成器时循环之后的代码永远不执行"的情况不同 —— 差别在**谁是消费方**）。
        logger.error("模型流结束了但没有 end 事件——终止本轮，避免外层无限重试")
        yield make_event(
            "error",
            reason="stream_incomplete",
            message=(
                "这次连接中途断掉了，没收到模型的完整回答。**没有执行任何工具。**"
                "再发一次就行；如果反复出现，检查一下网络或代理。"
            ),
        )
        return


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
