"""工具插件包：工作台的能力以「进程内工具插件」形式接入 Agent 核心。

装配入口是 `load_all(hub)`（由 `workbench/hub.py::get_hub()` 唯一调用）。四个插件：

| 插件 | 业务 | 工具 |
|---|---|---|
| `builtin` | 通用能力（原 `harness/tools.py` 三件套） | `get_current_time` / `read_local_file` / `read_web_page` |
| `websearch` | 自建联网检索（DeepSeek API **没有**内置 web_search） | `web_search` |
| `insight` | 平台洞察（akso-cc 沉淀） | `insight_read_config` / `insight_run` / `insight_artifacts` / `insight_read_artifact` / `insight_runs` / `insight_accounts` |
| `accounts` | 账号库 + 平台登录验证（quick-login 沉淀） | `accounts_list` / `accounts_login` |

> **2026-09-22：`factory` 插件整体移除。** 配置工厂（蓝图校验 / 幂等创建 / 拓扑编排 /
> 断点续跑）连同 `services/egmp/writers/**`、`services/egmp/orchestrate.py`、
> `api/routes_factory.py` 与其前端页一并删除，只保留平台知识文档
> （[docs/PLATFORM-OPERATIONS-LEARNED.md](../docs/PLATFORM-OPERATIONS-LEARNED.md)、
> [docs/FACTORY-PLATFORM-COGNITION.md](../docs/FACTORY-PLATFORM-COGNITION.md)）。
> 这条移除顺带**拆掉了一处依赖倒灌**：`plugins/factory.py` 曾经 import
> `api/routes_factory.py`（违反下面第 2 条纪律），是当时唯一一处。

## 装配纪律（写新插件前先读）

1. **单个插件失败不影响其余，也不阻断应用启动**——每个插件在自己的 `try/except` 里
   注册；失败进 `hub.failures`，在 `GET /api/agent/tools` 的 `failures` 字段可见。
   这是刻意的：装配期静默少一个插件，比启动崩溃更难查。
2. **插件只依赖 `plugin_api` 与既有的 services**，不 import `api/` 里的任何东西。
   依赖方向永远是 `plugins → services/plugin_api`，**永不反向**。
3. **新增插件 = 加一个模块 + 在 `_MODULES` 加一行**。没有自动发现——
   研究结论是"发现机制的收益接近零，依赖方向与版本边界才是要买的东西"
   （`docs/research/core-plugin-separation-research.md` §0）。
"""

from __future__ import annotations

import importlib
import logging

from ..plugin_api import ToolHub

logger = logging.getLogger("workbench.plugins")

_MODULES: tuple[str, ...] = (
    "builtins",
    "websearch",
    "insight",
    "accounts",
    "memory",
)


def load_all(hub: ToolHub) -> list[str]:
    """按清单装配全部插件；返回成功装配的插件 id 列表。

    **坏插件隔离在这里**：`register()` 抛异常只记一条 failure，其余插件照常装。
    """
    loaded: list[str] = []
    for name in _MODULES:
        try:
            module = importlib.import_module(f"{__name__}.{name}")
            module.register(hub)
            loaded.append(name)
        except Exception as exc:  # noqa: BLE001 —— 坏插件不能拖垮宿主
            hub.mark_load_failure(name, f"{type(exc).__name__}: {exc}")
    if loaded:
        logger.info("工具插件已装配：%s（共 %d 个工具）", "、".join(loaded), len(hub.specs()))
    return loaded
