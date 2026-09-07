"""断点续跑（akso-auto api/checkpoint.js 思路的 Python 化）。

akso-auto 的语义：任务按步骤推进，每步完成后把状态写入 checkpoint；
中断后重新执行时从最近 checkpoint 恢复，已完成步骤跳过。
本模块提供文件型 checkpoint 存储；具体任务的步骤协议由 writers/orchestrate 定义。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class Checkpoint:
    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "checkpoint.json"

    def load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"steps": {}}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"steps": {}}
        except (OSError, ValueError):
            return {"steps": {}}

    def save(self, state: dict[str, Any]) -> None:
        self._path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def mark_done(self, step: str, detail: dict[str, Any] | None = None) -> None:
        state = self.load()
        steps: dict[str, Any] = state.setdefault("steps", {})
        steps[step] = {"done": True, "detail": detail or {}, "at": int(0)}
        self.save(state)

    def is_done(self, step: str) -> bool:
        step_info = self.load().get("steps", {}).get(step)
        return bool(step_info and step_info.get("done"))

    def reset(self) -> None:
        if self._path.exists():
            self._path.unlink()
