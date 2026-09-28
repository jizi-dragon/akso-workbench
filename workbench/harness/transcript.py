"""把工具轨迹**按当时发出去的样子**落库（计划 §7.3）。

## 它解决什么

一个回合里的 `assistant(tool_calls)` 与配对的 `tool(...)` 此前**完全不落库** ——
它们只活在 `run_agent_loop` 的局部 `context` 里，回合结束就没了。后果有两个：

1. **刷新页面后模型看不到自己上一步调了什么工具**；
2. ★ 更根本的：**没有任何地方记着"当时究竟发出去了什么"** ——
   事后想复现一次请求只能靠猜。

## 口径：**当时逐字节发出去的那一条**

不是"重新构造一个等价的"。理由很实际：只有落的是原件，
「重放组装 == 当时请求」才是一个**能断言**的等式
（`tests/test_transcript_roundtrip.py`）。落一个近似值的话，
那条断言只能验"我构造的近似还在不在"，而那正是它要防的东西。

★ 实现上这一点是**免费**的：`_tool_message()` 与 `context.append(assistant)` 是
**单一构造点**（2026-09-24 收敛出来的），所以落库拿到的就是同一个对象 ——
不存在"落库那份和发出去那份长得像但不是同一个"的余地。

## 为什么不落最终那条 assistant

回合结束时的 `final` assistant（**不带 `tool_calls`**）由 `routes_chat` 落库，
而且带正确的 `kind`（`message` / `error`）。在这里再落一次会**写重**，
而重的那条会进 `_model_history` ⇒ 模型看到自己说了两遍同样的话。
⇒ 本模块**只落带 `tool_calls` 的 assistant 与它的 tool 结果**。

## 写失败绝不影响主流程

同 `trace` 的纪律：落库是**留痕**，不该让一次本来能成的对话失败。
失败计数留着，由 `selfcheck` / 时间线面板暴露（静默失败 = 时间线悄悄缺步骤）。
"""

from __future__ import annotations

import logging

logger = logging.getLogger("workbench.transcript")

#: 轨迹写失败的次数（进程内累计）。★ 它是"轨迹是不是完整的"的唯一信号 ——
#: 没有它，写失败会表现为"这次运行只有两步"，而那看起来完全正常。
_write_failures = 0


def write_failures() -> int:
    """轨迹落库失败的累计次数（供 `selfcheck` 与时间线面板显示）。"""
    return _write_failures


def reset_write_failures() -> None:
    """清计数（仅测试用）。"""
    global _write_failures
    _write_failures = 0


def record(conversation_id: str, message: dict) -> None:
    """落一条**当时发出去**的消息。`conversation_id` 为空时什么都不做。

    ★ 空 id 静默跳过是**刻意**的：`run_agent_loop` 有一个"不带会话"的调用形状
    （评测、单测直接驱动循环），而那种调用**没有会话可归属**。
    在那里报错会把"评测跑不起来"变成一个与业务无关的故障。
    """
    global _write_failures
    if not conversation_id:
        return
    role = str(message.get("role") or "")
    try:
        from ..services import storage  # 本地导入：harness 不把 services 拖进 import 期

        if role == "assistant":
            calls = message.get("tool_calls")
            if not calls:
                # 最终那条由 routes_chat 落（带 kind），见模块文档串
                return
            storage.insert_message(conversation_id, "assistant",
                                   str(message.get("content") or ""),
                                   tool_calls=list(calls))
        elif role == "tool":
            storage.insert_message(conversation_id, "tool",
                                   str(message.get("content") or ""),
                                   tool_call_id=str(message.get("tool_call_id") or ""))
        # 其它 role（user / system / note）不经这里 —— 它们各有自己的落点
    except Exception:  # noqa: BLE001 —— 留痕失败绝不影响主流程
        _write_failures += 1
        logger.warning("工具轨迹落库失败（会话 %s，role=%s）", conversation_id, role,
                       exc_info=True)
