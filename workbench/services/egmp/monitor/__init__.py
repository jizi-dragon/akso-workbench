"""egmp.monitor —— Monitor 闭环 Python 化（阶段 3D，已实现核心）。

- monitor.py     录制引擎（Playwright 三级降噪 + DOM 动作缓冲）
- query_layer.py 查询层（三格式兼容/过滤/参数 JSON-Path 提取/多样本推断）
- interpreter.py 分段解读 + 复现计划（API_MAP 对位 Python writers）+ 复现授权协议
- 复现执行：由调用方经 reproduce_plan 的 steps 逐步确认后调用对应 writers 函数
  （DANGEROUS 类永不回放——安全边界继承上游）。
"""

from __future__ import annotations

from .interpreter import (  # noqa: F401
    API_MAP,
    append_error_record,
    append_pitfall_record,
    interpret_segment,
    mark_iterated,
    record_feedback,
    reproduce_plan,
)
from .monitor import MonitorSession  # noqa: F401
from .query_layer import (  # noqa: F401
    classify_api,
    compare_recordings,
    extract_from_log,
    extract_params,
    query_monitor_log,
    read_log_entries,
)
