"""Harness 类型定义：配置、消息、事件、工具调用结果。

设计原则：
- 所有跨模块流动的数据都有类型（消灭 dict[str, Any] 字符串约定）
- 事件字典的 type 字段使用 Literal，前端 SSE 协议与此一一对应
- Pydantic 模型负责校验与 JSON 输出（camelCase 兼容前端契约）
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

DEFAULT_MAX_STEPS = 40
"""一次用户消息里允许的**工具调用**次数上限（默认值）。

## 为什么是 40 而不是 12（2026-09-20 修订）

原来写死 12，且**任何地方都改不了**（路由组装 `AgentConfig` 时只传 temperature 与
system_prompt）。12 是通用问答 Agent 的默认量级，而配置工厂这类作业是**多步 + 有重试 +
有依赖**的：找账号 1 步、蓝图校验改 2~3 轮、创建 1 步、看结果 1 步——多对象蓝图几乎必然撞上限，
而撞上限的表现是**整轮被砍掉**（`loop.py` 的 `safety.exhausted` 分支），不是"再走一轮"。

40 的取法：实测量级在 6~20 步，留一倍余量。再往上，"模型跑飞"的代价开始显著——
**每一步都是一次完整的 LLM 往返**（还可能带工具副作用）。

> 它是**默认值**，不是常量：用户可在设置页改（`services/settings.py::load_agent_limits`）。
> 之所以仍然给一个默认值：绝大多数用户不该为了用一次配置工厂先去调参数。
"""

MIN_MAX_STEPS = 4
MAX_MAX_STEPS = 200
"""设置页允许的范围。下限 4 = 至少够"查一次 → 做一次 → 看一次"；
上限 200 是防手滑（填 100000 等于关掉刹车，而刹车是这层唯一的作用）。"""


class AgentConfig(BaseModel):
    """一次 Agent 运行的全部可调参数（全部有默认值，调用方只传差异项）。"""

    temperature: float = 0.7
    max_tokens: int | None = None
    max_steps: int = DEFAULT_MAX_STEPS
    context_window_tokens: int = 1_048_576
    """模型上下文窗口（token）。**默认取满 `deepseek-flash` 的上限**（用户 2026-09-24 下达）。

    ## 这个数从哪来

    `deepseek-flash` = **DeepSeek-V4.1-Flash**，官方[模型 & 价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)
    页写的是"**上下文长度 1M**／输出长度 最大 **384K**"。

    **为什么按 2²⁰ 而不是 10⁶ 读**：同一张表里输出写"384K"，而 384K = 393,216 =
    **3 × 131,072**（上一代 `deepseek-flash` 的 128K）⇒ 这家用**二进制**口径。
    所以 1M = **1,048,576**。（若哪天官方给出确切数字，改这一行即可。）

    ## 调大它意味着什么（不是免费的）

    折叠门槛是 `context_threshold × context_window_tokens`，所以调大窗口 =
    **折叠几乎不再发生**（0.8 × 1M ≈ 839K token 才折）。长会话因此会一路把原文
    发上去，而**缓存未命中的单价是命中价的 50 倍**（见 `cache.py` 的价目表）：
    一次全 miss 的 800K token 请求与一次命中的同样请求，账单差两个数量级。
    前缀 append-only（不变量 I1）在这里从"省钱技巧"变成"**主要成本控制手段**"。

    取满窗口是**产品选择**：优先"模型能记住更多"，代价由缓存命中率承担。
    """
    context_threshold: float = 0.8
    keep_recent_turns: int = 5
    system_prompt: str = ""
    context_hard_limit_tokens: int | None = None
    """上下文**硬上限**（2026-09-24 加，不变量 I17 的一半）。`None` = 用 `context_window_tokens`。

    ## 为什么要有它（与 `context_threshold` 不是一个东西）

    `context_threshold`（0.8 × 窗口）是**该折叠了**的门槛；硬上限是**不许再发**的门槛。
    两者分开是因为它们的处置完全不同：过门槛 → 折叠历史（还能继续做）；过硬上限 →
    **根本不发这次请求**，以可行动的文案收尾。

    为什么必须有后者：`_build_context` 在整轮开始前**只算一次**，而工具结果会不断往
    上下文里追加（一条 `read_web_page` 正文可到 12k 字符）。实测探针：预算 1000 token，
    第 5 次请求已 **12,059**，而折叠发生 **0** 次。超窗后是**平台**拒回来的，
    而它给的那句话用户与模型都无法据以行动。
    """
    approval_wait_s: float = 300.0
    """风险门（`risk >= write` 的工具被拦下）**就地等用户放行**的秒数（2026-09-22 加，决策卡 G2）。

    为什么要有这个等待：旧实现只发 `approval_required` 事件、**不等**，然后指望"模型重试同一次
    调用"（`claim_approved` 那条路）。实测的失败形态是——模型把话说完这一轮就结束了，
    用户随后点「放行」时**没有任何人在等**，那条批准被搁置，界面表现为"我点了，什么都没发生"。

    超时语义与实时确认**完全一致**：到点按**未同意**处理（`expired`），绝不默认同意。
    300 秒与 `plugin_api.DEFAULT_CONFIRM_TIMEOUT_S` 同值：一个真人看一眼再点，够用；
    真走开了，让这一轮结束比挂着一个不动的转圈更诚实。测试里把它调小即可（见
    `tests/test_agent_approval.py`）。
    """

    model_config = {"populate_by_name": True}

    def to_api_messages_field(self) -> dict[str, Any]:
        """转为前端/SSE 契约中的 camelCase 字段（如需要）。"""
        return {
            "temperature": self.temperature,
            "maxSteps": self.max_steps,
            "contextWindowTokens": self.context_window_tokens,
            "contextThreshold": self.context_threshold,
        }


class ToolCall(BaseModel):
    """模型发起的一次工具调用（OpenAI function calling 增量聚合后的最终形态）。"""

    id: str = ""
    name: str = ""
    arguments: str = "{}"


class ToolResult(BaseModel):
    """工具执行结果（is_error 时 content 为错误信息，作为观测回传给模型）。"""

    tool_call_id: str = ""
    content: str = ""
    is_error: bool = False


# ———— Agent 循环事件（与前端 SSE 协议一一对应） ————

EventType = Literal[
    "token",      # 流式正文增量 {text}
    "thought",    # 工具调用轮次里模型的叙述 {text}
    "action",     # 发起工具调用 {call: ToolCall, tool: {name,risk,plugin}, auditId}
    "observation",# 工具结果 {result: {ok,summary,status}, invocation: {…}}
    "approval_required",  # 风险工具被确认门拦下 {pendingId, tool, risk, summary}
    "confirm_required",   # 模型主动请求实时确认 {pendingId, question, details, target, timeoutS}
    "confirm_resolved",   # 实时确认有了答复 {pendingId, approved, expired, answer}
    "final",      # 最终回答 {content}
    "error",      # 终止性错误 {message}
]

TokenEvent = Annotated[dict, Field]  # 占位说明；实际事件用 make_event 构造


def make_event(event_type: EventType, **payload: Any) -> dict:
    """构造事件字典（统一出口，避免散落的字面量）。"""
    return {"type": event_type, **payload}
