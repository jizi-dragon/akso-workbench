"""eGMP 认证模块 —— 语义词典**已提升为共用平台知识层**，本模块只做转发。

实际登录/缓存逻辑在 `client.py`（`login` / `token_for_account`）；
词典与判定函数现在住在 :mod:`workbench.services.egmp.kernel.semantics`。

新代码请直接从 kernel 导入；保留本垫片是为了不打断既有调用点。
"""

from __future__ import annotations

from .client import ApiError, EgmpClient, login, token_for_account  # noqa: F401 —— re-export
from .kernel.semantics import *  # noqa: F401,F403
