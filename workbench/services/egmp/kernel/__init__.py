"""平台知识层：端点表与语义词典（读路径 / 写路径共用）。

规格见 docs/INSIGHT-SPEC.md §3.1。三个模块：

- :mod:`~workbench.services.egmp.kernel.endpoints` —— **唯一**端点来源，带命名空间归属；
- :mod:`~workbench.services.egmp.kernel.semantics` —— dataType / behaviorType / stepType /
  dispatchMode / 后缀约定等实证词典与判定函数；
- :mod:`~workbench.services.egmp.kernel.urls` —— 平台地址归一化（**唯一**事实来源：
  用户填的 baseUrl 在各处消费前先过它，避免「登录页地址被拼进 API 路径」那类事故）。

历史问题（本层要消灭的）：`writers/endpoints.py` 自称权威表，但 insight 绕过它自行硬编码约 14 处；
同一份后缀约定在 `auth.py`、`crawler.py`、`annotate.py` **各写了一遍**，漂移风险高。
原有的 `writers/endpoints.py` 与 `auth.py` 保留为**转发垫片**，不动既有调用点。
"""

from __future__ import annotations

from . import endpoints, semantics, urls
from .urls import normalize_base_url

__all__ = ["endpoints", "semantics", "urls", "normalize_base_url"]
