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
