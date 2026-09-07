"""Harness 类型定义：配置、消息、事件、工具调用结果。

设计原则：
- 所有跨模块流动的数据都有类型（消灭 dict[str, Any] 字符串约定）
- 事件字典的 type 字段使用 Literal，前端 SSE 协议与此一一对应
- Pydantic 模型负责校验与 JSON 输出（camelCase 兼容前端契约）
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    """一次 Agent 运行的全部可调参数（全部有默认值，调用方只传差异项）。"""

    temperature: float = 0.7
    max_tokens: int | None = None
    max_steps: int = 12
    context_window_tokens: int = 131072
    context_threshold: float = 0.8
    keep_recent_turns: int = 5
    system_prompt: str = ""

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
    "action",     # 发起工具调用 {call: ToolCall}
    "observation",# 工具结果 {result: ToolResult}
    "final",      # 最终回答 {content}
    "error",      # 终止性错误 {message}
]

TokenEvent = Annotated[dict, Field]  # 占位说明；实际事件用 make_event 构造


def make_event(event_type: EventType, **payload: Any) -> dict:
    """构造事件字典（统一出口，避免散落的字面量）。"""
    return {"type": event_type, **payload}
