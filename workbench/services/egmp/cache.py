"""进程内查询缓存（akso-cc queries.ts Map 缓存 + akso-auto api/cache.js 思路）。

**键必须带环境作用域**（`{scope}|{kind}:{code}`）。历史缺陷：键只有 `meta:{code}`，
而 `QueryCache` 是进程级全局单例——多个 eGMP 部署在同一进程内查询同名对象（真实场景：
四个环境都有 `init_status__c` 这类标准编码）时会**互相污染**。
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

_SEP = "|"
_MISS = object()


def cache_key(scope: str, kind: str, code: str) -> str:
    """统一键格式：``{scope}|{kind}:{code}``。"""
    return f"{scope}{_SEP}{kind}:{code}"


class QueryCache:
    """简单 TTL 进程内缓存（键形如 `{scope}|meta:{code}`）。"""

    def __init__(self, ttl_s: float = 300.0) -> None:
        self._ttl = ttl_s
        self._store: dict[str, tuple[float, Any]] = {}

    def _lookup(self, key: str) -> Any:
        """返回命中值，或 :data:`_MISS`。用哨兵而非 ``None``，以便缓存**假值**。"""
        hit = self._store.get(key)
        if hit is None:
            return _MISS
        at, value = hit
        if time.monotonic() - at > self._ttl:
            self._store.pop(key, None)
            return _MISS
        return value

    def get(self, key: str) -> Any | None:
        value = self._lookup(key)
        return None if value is _MISS else value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.monotonic(), value)

    def clear(self) -> None:
        self._store.clear()

    def get_or_set(self, key: str, factory) -> Any:
        value = self._lookup(key)
        if value is not _MISS:
            return value
        value = factory()
        self.set(key, value)
        return value

    def invalidate(self, *, scope: str | None = None, codes: Iterable[str] | None = None) -> int:
        """按作用域与/或对象编码失效；返回清除条数。

        - 两者都不给 → 全清；
        - 只给 ``scope`` → 清该环境；
        - 只给 ``codes`` → **跨全部环境**清这些对象（写路径用，避免另一个环境留旧值）。
        """
        wanted = set(codes) if codes else None
        if scope is None and wanted is None:
            removed = len(self._store)
            self._store.clear()
            return removed
        removed = 0
        for key in list(self._store):
            key_scope, _, rest = key.partition(_SEP)
            if scope is not None and key_scope != scope:
                continue
            if wanted is not None and rest.partition(":")[2] not in wanted:
                continue
            self._store.pop(key, None)
            removed += 1
        return removed


_default = QueryCache()


def default_cache() -> QueryCache:
    return _default
