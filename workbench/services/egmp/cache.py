"""进程内查询缓存（akso-cc queries.ts Map 缓存 + akso-auto api/cache.js 思路）。"""

from __future__ import annotations

import time
from typing import Any


class QueryCache:
    """简单 TTL 进程内缓存（key 形如 meta:{code}，与原实现一致）。"""

    def __init__(self, ttl_s: float = 300.0) -> None:
        self._ttl = ttl_s
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        hit = self._store.get(key)
        if hit is None:
            return None
        at, value = hit
        if time.monotonic() - at > self._ttl:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.monotonic(), value)

    def clear(self) -> None:
        self._store.clear()

    def get_or_set(self, key: str, factory) -> Any:
        value = self.get(key)
        if value is None:
            value = factory()
            self.set(key, value)
        return value


_default = QueryCache()


def default_cache() -> QueryCache:
    return _default
