"""写路径端点与枚举常量 —— **已提升为共用平台知识层，本模块只做转发**。

新代码请直接从 :mod:`workbench.services.egmp.kernel.endpoints`（端点）与
:mod:`workbench.services.egmp.kernel.semantics`（枚举与约定）导入。

保留本垫片是为了不打断既有调用点（如 `writers/idempotency.py` 从本模块导入常量），
避免一次纯粹的搬家变成跨模块改动。⚠ 本文件里的值**不再独立维护**——改值请改 kernel
（那些值仍是平台实证：改动前必须与原仓库 create-*.js 系列核对）。
"""

from __future__ import annotations

from ..kernel.endpoints import *  # noqa: F401,F403
from ..kernel.semantics import *  # noqa: F401,F403
