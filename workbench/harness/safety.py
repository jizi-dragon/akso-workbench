"""安全门控：步数预算 + 重复调用循环检测。"""

from __future__ import annotations

from collections import deque

from .types import DEFAULT_MAX_STEPS


class SafetyGate:
    """两道闸门：**步数预算**（这一轮最多走多远）与**重复调用检测**（原地打转）。

    ⚠️ 计费口径：**每一次工具调用扣 1 步**，只读与危险一样贵
    （`loop.exhausted` 只在**两次模型轮次之间**检查，所以实际数字是
    "`max_steps` 次工具调用之后不再发起新一轮"）。
    想知道"为什么这次没答完"时，`steps_used` 与 `max_steps` 要一起看。
    """

    def __init__(self, max_steps: int = DEFAULT_MAX_STEPS, loop_threshold: int = 3,
                 loop_window: int = 4):
        self.max_steps = max_steps
        self.loop_threshold = loop_threshold
        self._steps = 0
        self._history: deque[str] = deque(maxlen=loop_window)

    @property
    def steps_used(self) -> int:
        """已经花掉的工具调用次数（日志与"预算耗尽"文案要引用它）。"""
        return self._steps

    @property
    def exhausted(self) -> bool:
        return self._steps >= self.max_steps

    def consume_step(self) -> None:
        self._steps += 1

    def is_loop(self, call: dict) -> bool:
        key = f"{call.get('name')}:{call.get('arguments')}"
        self._history.append(key)
        return sum(1 for k in self._history if k == key) >= self.loop_threshold

    def would_loop(self, call: dict) -> bool:
        """`is_loop` 的**只读**版本：只回答"假如现在记下这一条，会不会触发"。

        ★ 为什么必须有它（2026-09-24 加，为计划 §7.4 的并发快路径）：

        并发路径要在**动手之前**做资格预检，而预检失败时必须**原封不动地**退回串行路径。
        用 `is_loop` 做预检会在 `_history` 里留下记录 ——
        退回串行后同一次调用**会被记第二遍**，于是重复检测**提前一轮误报**
        （本该第 3 次才触发，变成第 2 次就触发）。

        这个缺陷的形态很难看：它**只在"预检失败"时出现**，也就是只在
        "有风险批次 / 有重复 / 有确认"的那些回合里出现，而那时人正在盯别的问题。
        判据：`tests/test_agent_parallel.py::test_a_rejected_batch_leaves_the_loop_history_untouched`。
        """
        key = f"{call.get('name')}:{call.get('arguments')}"
        probe = deque(self._history, maxlen=self._history.maxlen)
        probe.append(key)  # 与 `is_loop` 里的 append 语义**逐字一致**（含 maxlen 截断）
        return sum(1 for k in probe if k == key) >= self.loop_threshold
