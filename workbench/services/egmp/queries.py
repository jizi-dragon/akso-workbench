"""OpenAPI 标准查询封装（akso-cc queries.ts 的 Python 化，带进程内缓存）。

端点（akso-cc 调研 §5.3 实证）：
- GET /api/openapi/v1.0/BasicObject/{code}          对象元数据（4.8）
- GET /api/openapi/v1.0/BasicObject/field/{code}    字段降级通道（4.6）
- GET /api/openapi/v1.0/BasicObject/lifecycleStatus/{code}  生命周期状态（4.5）
"""

from __future__ import annotations

from typing import Any

from .cache import default_cache


class GaiaQueries:
    """标准查询层（EgmpClient 的薄包装 + meta:{code} 缓存风格）。"""

    def __init__(self, client: Any) -> None:
        self._client = client

    def get_object_info(self, code: str) -> dict[str, Any]:
        """对象元数据（含 id/lifeCycleId/enableLifeCycle）。"""
        return default_cache().get_or_set(f"meta:{code}", lambda: self._client.get_object(code) or {})

    def get_fields(self, code: str) -> list[dict[str, Any]]:
        return default_cache().get_or_set(
            f"fields:{code}", lambda: self._client.get_object_fields(code) or []
        )

    def get_statuses(self, code: str) -> list[dict[str, Any]]:
        return default_cache().get_or_set(
            f"statuses:{code}", lambda: self._client.get_object_statuses(code) or []
        )

    def get_picklist(self, code: str) -> dict[str, Any] | None:
        """选项集（验证/幂等用）。"""
        try:
            data = self._client.get(f"/api/openapi/v1.0/ObjectPicklist/{code}")
            return data or None
        except Exception:  # noqa: BLE001 —— 不存在/无权限按未命中处理
            return None

    def get_workflow(self, code: str) -> dict[str, Any] | None:
        try:
            data = self._client.get(f"/api/openapi/v1.0/Workflow/{code}")
            return data or None
        except Exception:  # noqa: BLE001
            return None


def invalidate_object_cache(code: str | None = None) -> None:
    """创建/修改配置后失效缓存（全清或按对象）。"""
    cache = default_cache()
    if code is None:
        cache.clear()
    else:
        for key in (f"meta:{code}", f"fields:{code}", f"statuses:{code}"):
            cache._store.pop(key, None)  # noqa: SLF001 —— 同包内受控访问
