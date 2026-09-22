"""gws CLI 适配层（Akso/Gaia 平台官方命令行）。

背景与研判见 `docs/CLI-CONFIG-PLAN.md`。一句话：平台研发发布了官方 CLI `gws v1.0.0`，
它把配置写入收敛成**一个公开、版本化**的端点（`POST /openapi/v3.8.1/component/instances/save`），
而配置工厂目前在用的是 58 个**私有、无版本、逆向自浏览器流量**的端点
（`/api/platform/*` 37 + `/api/config/*` 15）——后者每次平台升级都要重新取证。

本包**只做通道**，不做决策：它把 CLI 当子进程调用、解析 JSON、把失败翻译成我们自己的错误类型。
"写什么内容"是蓝图层的事，不在这里。
"""

from __future__ import annotations

from .client import (
    MIGRATION_WRITE_VERBS,
    OP_CREATE,
    OP_OVERWRITE,
    OP_UPDATE,
    OPERATION_TYPES,
    READ_ONLY_VERBS,
    GwsBinaryNotFound,
    GwsClient,
    GwsConfig,
    GwsError,
    GwsNotConfigured,
    GwsPlatformError,
    GwsProcessError,
    GwsTimeout,
    MigrationPackage,
    resolve_binary,
)
from .shape import ShapeDiff, ShapeReport, compare_shapes, diff_key_sets, key_profile

__all__ = [
    "MIGRATION_WRITE_VERBS",
    "OPERATION_TYPES",
    "OP_CREATE",
    "OP_OVERWRITE",
    "OP_UPDATE",
    "READ_ONLY_VERBS",
    "GwsBinaryNotFound",
    "GwsClient",
    "GwsConfig",
    "GwsError",
    "GwsNotConfigured",
    "GwsPlatformError",
    "GwsProcessError",
    "GwsTimeout",
    "MigrationPackage",
    "ShapeDiff",
    "ShapeReport",
    "compare_shapes",
    "diff_key_sets",
    "key_profile",
    "resolve_binary",
]
