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

import json
import logging

logger = logging.getLogger("workbench.transcript")

#: 轨迹写失败的次数（进程内累计）。★ 它是"轨迹是不是完整的"的唯一信号 ——
#: 没有它，写失败会表现为"这次运行只有两步"，而那看起来完全正常。
_write_failures = 0


def dangling_calls(conversation_id: str) -> list[dict]:
    """transcript 里**声明了却没有配对结果**的 `tool_calls`（不变量 I16）。

    ## 什么情况下会留下它们

    `loop._fill_unexecuted` 已经覆盖了**进程内**的三个出口（步数预算 / 用户取消 /
    超时）—— 那三条路都会给未执行的调用补一条 `[未执行]` 观测。
    ★ 而**进程死亡**（崩溃、强杀、断电）那三条路**一条都跑不到**：
    已经写进库的 `assistant(tool_calls)` 就永远没有配对结果。

    ## 为什么必须补，而不是"读的时候跳过就算了"

    `_fill_unexecuted` 的文档串把这个后果写得很清楚：
    「`assistant.tool_calls` 里的**每一个 id** 都必须有配对的 `tool` 消息。少一条，
    这段历史就是**非法**的……而"对话级断点续跑"（重放历史）永远做不成
    ——**重放一段非法历史没有意义**」。

    ★ 所以这个函数是**续跑的前置件**：先能判断"哪里是干净的"，才谈得上从那里继续。

    ## 口径

    按 `seq` 走一遍：assistant 声明一批 id，`tool` 行销掉各自的 id；
    走完之后**还剩在集合里的**就是悬空的。⇒ 顺序是承重的，而 `seq` 保证了它
    （见迁移 25 讲为什么不能用毫秒时间戳）。
    """
    if not conversation_id:
        return []
    from ..services import storage

    pending: dict[str, dict] = {}
    order: list[str] = []
    for row in storage.list_messages(conversation_id):
        role = str(row.get("role") or "")
        if role == "assistant" and row.get("tool_calls"):
            try:
                calls = json.loads(row["tool_calls"])
            except (TypeError, ValueError):
                continue
            for call in calls or []:
                call_id = str((call or {}).get("id") or "")
                if call_id:
                    pending[call_id] = call
                    order.append(call_id)
        elif role == "tool":
            pending.pop(str(row.get("toolCallId") or ""), None)
    return [pending[cid] for cid in order if cid in pending]


def repair_dangling(conversation_id: str, *, reason: str = "") -> int:
    """给悬空的 `tool_calls` 补上 `[未执行]` 观测，返回补了几条。

    ## 它是**幂等**的

    补完之后那些 id 就有配对了，再跑一次什么也不做 ⇒ 可以在每次运行开始时无脑调。

    ## 措辞：**不能让它看起来像执行过了**

    正文写"未执行"，并且**说清原因**（同 `_fill_unexecuted` 的理由：
    模型看得见它才可能自我纠正）。★ 尤其**不能**留空或写"已完成" ——
    那会让模型以为这一步有结果，然后据此下结论（比失败更坏）。

    ## 它**不执行任何东西**

    这条是**刻意的**，也是与"续跑"的分界线：本函数只补历史，
    **绝不重新发起**那些调用。理由很硬 —— 一个写操作重放一次的后果
    与"少一条观测"完全不是一个量级，而**我们无法知道**上一次究竟执行到哪了
    （进程死在"已经发出去、还没写回结果"的那个窗口里是可能的）。
    ⇒ 宁可标成"未执行"（模型可以重试），也不赌它没执行。
    """
    if not conversation_id:
        return 0
    calls = dangling_calls(conversation_id)
    if not calls:
        return 0
    from ..services import storage

    note = reason or "上一次运行被中断（应用退出或崩溃），这一步**没有执行**，也没有执行结果。"
    filled = 0
    for call in calls:
        call_id = str((call or {}).get("id") or "")
        if not call_id:
            continue
        name = str((call or {}).get("name") or "")
        storage.insert_message(
            conversation_id, "tool",
            f"[未执行] {note}" + (f"（工具：{name}）" if name else ""),
            tool_call_id=call_id,
        )
        filled += 1
    if filled:
        logger.warning("补写 %d 条未执行的工具调用（会话 %s）—— 上次运行留下了悬空的 tool_calls",
                       filled, conversation_id[:8] or "-")
    return filled



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
