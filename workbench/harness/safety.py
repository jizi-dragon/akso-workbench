"""安全门控：步数预算 + 重复调用循环检测。"""

from __future__ import annotations

from collections import deque


class SafetyGate:
    def __init__(self, max_steps: int = 12, loop_threshold: int = 3, loop_window: int = 4):
        self.max_steps = max_steps
        self.loop_threshold = loop_threshold
        self._steps = 0
        self._history: deque[str] = deque(maxlen=loop_window)

    @property
    def exhausted(self) -> bool:
        return self._steps >= self.max_steps

    def consume_step(self) -> None:
        self._steps += 1

    def is_loop(self, call: dict) -> bool:
        key = f"{call.get('name')}:{call.get('arguments')}"
        self._history.append(key)
        return sum(1 for k in self._history if k == key) >= self.loop_threshold
