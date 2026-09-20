"""端点覆盖分析：**哪些真实用到的平台端点，我们没有建模**。

这是整个目录最直接有用的一个分析。逻辑很简单：

    真实用到的端点  −  我们已经建模的端点（kernel/endpoints.py）  =  缺口

缺口就是蓝图 schema 与写路径的空白——用户手配时平台打的那些接口，
我们的 `writers/` 里没有对应实现，于是工厂"建不出来"或者"建出来跟手配的不一样"。

反向的那一半（`modeled_but_unseen`）也有用：**声明了却从没见真实流量用过**的端点，
要么是从上游抄来但没验证过的，要么用错了。这一半要小心解读——采样本就短。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Coverage:
    observed: list[str] = field(default_factory=list)
    modeled: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    modeled_but_unseen: list[str] = field(default_factory=list)
    by_namespace: dict[str, int] = field(default_factory=dict)

    @property
    def ratio(self) -> float:
        return (len(self.observed) - len(self.missing)) / len(self.observed) if self.observed else 1.0


def known_endpoints() -> list[str]:
    """产品里已建模的端点。

    ⚠️ 只读地 import 产品的**唯一事实源**（`kernel/endpoints.py`），不在这里另抄一份
    ——抄一份的下场是"分析说缺 A，其实我们早就有 A"，而那种错最难发现。
    带 `{code}` 的模板要做前缀匹配，见 `_matches`。
    """
    from workbench.services.egmp.kernel.endpoints import ALL_ENDPOINTS

    return list(ALL_ENDPOINTS)


def _matches(observed: str, template: str) -> bool:
    """端点是模板（`/api/openapi/v1.0/BasicObject/{code}`）还是固定串。"""
    if "{" not in template:
        return observed == template
    # 模板按段匹配：段数一致 + 非占位段逐字相等
    left, right = observed.split("/"), template.split("/")
    if len(left) != len(right):
        return False
    return all(r.startswith("{") or l == r for l, r in zip(left, right, strict=True))


def analyze(entries: list[Any], *, modeled: list[str] | None = None) -> Coverage:
    modeled = modeled if modeled is not None else known_endpoints()
    observed = sorted({e.endpoint for e in entries})

    missing = [path for path in observed
               if not any(_matches(path, t) for t in modeled)]
    unseen = [t for t in modeled
              if not any(_matches(path, t) for path in observed)]

    by_namespace: dict[str, int] = {}
    for path in missing:
        ns = path.split("/")[2] if path.startswith("/api/") and len(path.split("/")) > 2 else "other"
        by_namespace[ns] = by_namespace.get(ns, 0) + 1

    return Coverage(observed=observed, modeled=list(modeled), missing=missing,
                    modeled_but_unseen=unseen, by_namespace=by_namespace)
