"""Agent 循环引擎：call → tool → observe → repeat（事件流驱动）。

与传输层解耦：本模块只 yield 事件（见 types.EventType），
SSE / WebSocket / 测试断言都直接消费同一事件序列。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from ..services.deepseek import DeepSeekClient
from .context import build_context
from .safety import SafetyGate
from .tools import ToolRegistry
from .types import AgentConfig, make_event

SUMMARY_SYSTEM_PROMPT = "你是对话摘要助手。请将以下对话压缩为简洁摘要，保留关键事实、用户意图与结论。"


async def run_agent_loop(
    messages: list[dict],
    client: DeepSeekClient,
    registry: ToolRegistry,
    config: AgentConfig,
) -> AsyncIterator[dict]:
    """运行 Agent 循环，逐事件 yield。

    事件序列：token* → (thought? → action → observation)* → final | error
    """
    safety = SafetyGate(max_steps=config.max_steps)
    conversation = _with_system_prompt(messages, config.system_prompt)

    while True:
        if safety.exhausted:
            yield make_event("error", message="已达到步数预算上限，已终止本轮")
            return

        context = await _manage_context(conversation, client, config)

        async for event in _one_model_turn(context, client, registry, config, safety, conversation):
            yield event
            if event["type"] in ("final", "error"):
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


async def _manage_context(
    conversation: list[dict], client: DeepSeekClient, config: AgentConfig
) -> list[dict]:
    """超阈值时把旧轮次折叠为摘要，保留最近 N 轮原文。"""
    kept, truncated = build_context(
        conversation,
        window_tokens=config.context_window_tokens,
        threshold=config.context_threshold,
        keep_recent_turns=config.keep_recent_turns,
    )
    if not truncated:
        return conversation
    summary = await _summarize(client, truncated)
    return [{"role": "system", "content": f"历史对话摘要：\n{summary}"}, *kept]


async def _one_model_turn(
    context: list[dict],
    client: DeepSeekClient,
    registry: ToolRegistry,
    config: AgentConfig,
    safety: SafetyGate,
    conversation: list[dict],
) -> AsyncIterator[dict]:
    """一轮「模型流式输出 →（可能）工具循环」，工具回填后返回交由外层再开一轮。"""
    async for event in client.stream_chat(
        context,
        tools=registry.list_api(),
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    ):
        if event["type"] == "delta":
            yield make_event("token", text=event["text"])
        elif event["type"] == "end":
            assistant = {"role": "assistant", "content": event["content"]}
            if event["tool_calls"]:
                assistant["tool_calls"] = event["tool_calls"]

            if not assistant.get("tool_calls"):
                yield make_event("final", content=assistant["content"])
                conversation.append(assistant)
                return

            if assistant["content"]:
                yield make_event("thought", text=assistant["content"])
            conversation.append(assistant)

            for call in assistant["tool_calls"]:
                if safety.is_loop(call):
                    yield make_event("error", message="检测到重复的工具调用，已终止本轮")
                    return
                yield make_event("action", call=call)
                result = await registry.execute(call)
                safety.consume_step()
                yield make_event("observation", result=result.model_dump())
                conversation.append({
                    "role": "tool",
                    "tool_call_id": result.tool_call_id,
                    "content": result.content,
                })
            return  # 工具结果已回填，外层 while 发起下一轮


async def _summarize(client: DeepSeekClient, messages: list[dict]) -> str:
    text = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in messages)
    return await client.chat(
        [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        temperature=0.3,
    )
