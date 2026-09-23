"""egmp.monitor —— Monitor 闭环 Python 化（阶段 3D，已实现核心）。

- monitor.py     录制引擎（Playwright 三级降噪 + DOM 动作缓冲）
- query_layer.py 查询层（三格式兼容/过滤/参数 JSON-Path 提取/多样本推断）
- interpreter.py 分段解读 + 复现计划（**只记平台侧事实**：方法/路径/原始参数）

> **2026-09-22：复现执行随配置工厂移除。** `interpreter.py` 曾用一张 `API_MAP`
> 把端点映射到写路径的具体函数，再由调用方逐步确认后回放；写路径
> （`egmp/writers/**` + `orchestrate.py`）已整体删除，那张表被移除，
> 免得出产一份**指向不存在模块**的复现计划。安全边界不变：DANGEROUS 类永不入计划。
> 本仓库现在**不提供任何回放执行器**。
"""

from __future__ import annotations

from .interpreter import (  # noqa: F401
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
