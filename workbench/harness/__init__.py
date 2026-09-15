"""Harness 包入口：对话 Agent 循环 + 提示词 + 类型。

`COMPLIANCE_SYSTEM_PROMPT` 的旧名已随提示词简化下线（2026-09-15）——
提示词现在分基线/用户两版，见 `prompts.py`：`DEFAULT_SYSTEM_PROMPT`（基线）、
`build_system_prompt()`（实际下发的那一份）。旧名保留会让人以为还能引用那个常量。
"""

from .loop import UsageMeter, run_agent_loop  # noqa: F401
from .prompts import DEFAULT_SYSTEM_PROMPT, build_system_prompt  # noqa: F401

__all__ = ["UsageMeter", "run_agent_loop", "DEFAULT_SYSTEM_PROMPT", "build_system_prompt"]
