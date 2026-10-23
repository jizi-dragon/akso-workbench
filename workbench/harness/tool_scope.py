"""按回合裁剪模型可见工具（计划模式只保留 READ）。

放在独立模块，避免 `loop.py` 再长（repolint 文件预算只许降）。
"""

from __future__ import annotations

from ..plugin_api import Risk, ToolHub
from .types import make_event


def visible_tool_specs(registry: ToolHub, plugins: list[str] | None,
                       plan_mode: bool,
                       allowed_tools: tuple[str, ...] | None = None) -> list[dict]:
    specs = registry.list_api_for(plugins)
    allow = None if allowed_tools is None else set(allowed_tools)
    visible: list[dict] = []
    for item in specs:
        name = str((item.get("function") or {}).get("name") or "")
        spec = registry.get(name) if name else None
        if spec is None:
            continue
        if allow is not None and name not in allow:
            continue
        if plan_mode and spec.risk is not Risk.READ:
            continue
        visible.append(item)
    return visible


def skipped_observation(call: dict, reason: str) -> dict:
    """未执行调用的 `observation` 事件（前端据此把那张卡画成"已跳过"而不是转圈）。"""
    return make_event(
        "observation",
        result={"ok": False, "summary": f"未执行：{reason}", "status": "skipped"},
        invocation={
            "toolCallId": call.get("id") or "",
            "tool": str(call.get("name") or ""),
            "auditId": "",
            "durationMs": 0,
            "risk": Risk.READ.value,
            "plugin": "",
            "status": "skipped",
            "isError": True,
        },
    )


def tool_allowed(allowed_tools: tuple[str, ...] | None, name: str) -> bool:
    return allowed_tools is None or name in set(allowed_tools)
