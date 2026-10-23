"""能力诊断端点：`GET /api/diagnostics?live=false`。

返回稳定 issue code（见 `workbench/diagnostics.py`），供设置页/支持脚本消费。
默认静态：不启动 MCP、不联网；`live=true` 只读内存注册表。
"""

from __future__ import annotations

from fastapi import APIRouter

from ..diagnostics import collect

router = APIRouter(prefix="/api", tags=["diagnostics"])


@router.get("/diagnostics")
def diagnostics(live: bool = False) -> dict:
    """一次能力诊断。`live=true` 也不启动任何进程（只读注册表状态）。"""
    return collect(live=live)
