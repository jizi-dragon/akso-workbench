"""协作式取消令牌（计划 §5.6）：让用户能**停下来**，而不是只能关页面。

## 为什么是"协作式"，不是 `Task.cancel()`，也不是强杀

三种做法，选最弱的那种，理由各自具体：

| 做法 | 为什么不选 |
|---|---|
| `asyncio.Task.cancel()` | 它把 `CancelledError` 抛在**任意一行**上。工具 handler 正写到一半、审计正 insert 到一半，都会在那里断掉 —— 留下"做了一半且没人知道做到哪"的状态。而本仓的整个错误哲学是**任何失败都要变成可读的观测**，强杀恰好绕过它 |
| 进程/线程强杀 | 同上，且会丢审计与用量（§5.4a 修的就是这个方向） |
| ★ **协作式：在检查点问一句** | 停止发生在**两次动作之间**，于是每个动作要么完整发生、要么完全没发生 —— `_fill_unexecuted` 能如实回填"哪些没跑"，用量与审计也都收得干净 |

代价是**响应不是瞬间的**：最长要等当前那一步做完（一次模型调用或一次工具调用）。
这是刻意的取舍 —— 宁可晚半秒停在一个干净的位置，也不要立刻停在一个说不清的位置。

## ★ 它不撤销已经发生的副作用

用户点"停止"时，**已经执行过的工具调用是真执行过的** —— 平台上可能已经建了东西。
令牌只能阻止"下一步"，不能回滚"上一步"。所以 `cancelled` 事件的文案必须把这件事说清楚
（见 `loop._cancelled_message`），**不许**把它写成"已取消，什么都没发生"。

## 线程与并发

只有"置位"与"读取"两个操作，都是单次属性赋值/读取 —— 在 CPython 下原子。
停止请求从 HTTP 线程进来、循环在事件循环里读，所以这里**不加锁**；
若将来出现"请求 → 撤销请求"这种需要 CAS 的语义，再改成带锁的实现。
"""

from __future__ import annotations


class StopToken:
    """一个会话一枚。`request()` 置位，`should_stop()` 读取。

    **可以直接当 `should_stop` 传给 `run_agent_loop`**（实现了 `__call__`）——
    这样调用方不需要写 `lambda: token.requested`，少一层间接就少一处写错的机会。
    """

    def __init__(self, conversation_id: str = "") -> None:
        self.conversation_id = conversation_id
        self._reason = ""

    def request(self, reason: str = "用户点了停止") -> bool:
        """请求停止。返回**这次是不是第一次请求**。

        返回值是给调用方区分"用户点了一次"与"用户连点了五次"用的 ——
        后者不该被当成错误，但值得记一笔（连点通常意味着界面没给出反馈，
        而那是一个**界面缺陷**，不是用户手快）。
        """
        first = not self._reason
        if first:
            self._reason = reason
        return first

    @property
    def requested(self) -> bool:
        return bool(self._reason)

    @property
    def reason(self) -> str:
        return self._reason

    def __call__(self) -> bool:
        return self.requested

    def __repr__(self) -> str:  # pragma: no cover —— 只为日志好看
        state = f"已请求（{self._reason}）" if self.requested else "未请求"
        return f"<StopToken {self.conversation_id or '-'} {state}>"
