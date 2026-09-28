"""兼容垫片：工具层已迁到 `workbench/plugin_api.py` + `workbench/plugins/`。

**这个文件不再定义任何工具。** 2026-09-15 之前它自己维护一套工具注册表
（`Tool` / `ToolRegistry` / `default_registry`），与 `api/routes_agent.py` 的域工具表
**两套并存且互不可见**——对话里的模型只能看到三个基础工具，看不到平台能力；
`routes_agent` 的四个域工具又从不进对话循环。现在只有一个事实源：`plugin_api.ToolHub`。

保留本模块是因为既有调用点还在用旧名字（尤其是测试）。**新代码请直接用 `plugin_api`。**
"""

from __future__ import annotations

from typing import Any

from ..hub import get_hub  # noqa: F401 —— 旧名字的再导出
from ..plugin_api import (  # noqa: F401 —— 旧名字的再导出
    MAX_RESULT_CHARS,
    Arg,
    Invocation,
    Plugin,
    PluginOrigin,
    Risk,
    ToolContext,
    ToolHub,
    ToolSpec,
    ToolStatus,
    ok,
    split_codes,
)

#: 旧名 → 新类型（`Tool`/`ToolRegistry` 是被 `ToolSpec`/`ToolHub` 取代的两个概念）。
Tool = ToolSpec
ToolRegistry = ToolHub


def default_registry() -> ToolHub:
    """旧入口名（测试与 routes_chat 仍在用）→ 进程级工具中枢。"""
    return get_hub()


def tool(description: str, args_model: type, *, name: str | None = None) -> Any:
    """旧装饰器形态**已删除**——工具必须带风险分级，装饰器会诱导写出无分级的工具。

    迁移方式：改写成 `ToolSpec(...)`（见 `plugins/builtins.py` 的三个示例）。
    """
    raise NotImplementedError(
        "@tool 装饰器已下线：请改用 plugin_api.ToolSpec（必须显式声明 risk）。"
        "示例见 workbench/plugins/builtins.py"
    )


__all__ = [
    "MAX_RESULT_CHARS", "Arg", "Invocation", "Plugin", "PluginOrigin", "Risk", "ToolContext",
    "ToolHub", "ToolSpec", "ToolStatus", "Tool", "ToolRegistry", "default_registry", "get_hub",
    "ok", "split_codes", "tool",
]
