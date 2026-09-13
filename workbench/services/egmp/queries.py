"""OpenAPI 标准查询封装（akso-cc queries.ts 的 Python 化，带进程内缓存）。

端点（akso-cc 调研 §5.3 实证）：
- GET /api/openapi/v1.0/BasicObject/{code}          对象元数据（4.8）
- GET /api/openapi/v1.0/BasicObject/field/{code}    字段降级通道（4.6）
- GET /api/openapi/v1.0/BasicObject/lifecycleStatus/{code}  生命周期状态（4.5）
"""

from __future__ import annotations

from typing import Any

from .cache import cache_key, default_cache
from .kernel import endpoints


class GaiaQueries:
    """标准查询层（EgmpClient 的薄包装，键带**环境作用域**）。

    作用域取自客户端的 ``scope``（即归一化 base_url）；显式传入 ``scope`` 可覆盖（测试用）。
    键格式 ``{scope}|{kind}:{code}`` —— 见 cache.py 的模块说明。
    """

    def __init__(self, client: Any, *, scope: str | None = None) -> None:
        self._client = client
        self._scope = scope if scope is not None else str(getattr(client, "scope", "") or "")

    def _key(self, kind: str, code: str) -> str:
        return cache_key(self._scope, kind, code)

    def get_object_info(self, code: str) -> dict[str, Any]:
        """对象元数据（含 id/lifeCycleId/enableLifeCycle）。"""
        return default_cache().get_or_set(
            self._key("meta", code), lambda: self._client.get_object(code) or {}
        )

    def get_fields(self, code: str) -> list[dict[str, Any]]:
        return default_cache().get_or_set(
            self._key("fields", code), lambda: self._client.get_object_fields(code) or []
        )

    def get_statuses(self, code: str) -> list[dict[str, Any]]:
        return default_cache().get_or_set(
            self._key("statuses", code), lambda: self._client.get_object_statuses(code) or []
        )

    def get_picklist(self, code: str) -> dict[str, Any] | None:
        """选项集（验证/幂等用）。"""
        try:
            data = self._client.get(endpoints.render(endpoints.OBJECT_PICKLIST, code=code))
            return data or None
        except Exception:  # noqa: BLE001 —— 不存在/无权限按未命中处理
            return None

    def get_workflow(self, code: str) -> dict[str, Any] | None:
        try:
            data = self._client.get(endpoints.render(endpoints.WORKFLOW, code=code))
            return data or None
        except Exception:  # noqa: BLE001
            return None


def invalidate_object_cache(code: str | None = None) -> None:
    """创建/修改配置后失效缓存（全清，或按对象**跨全部环境**清）。"""
    cache = default_cache()
    if code is None:
        cache.clear()
    else:
        cache.invalidate(codes={code})
