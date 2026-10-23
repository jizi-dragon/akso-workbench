"""OpenAPI 标准查询封装（akso-cc queries.ts 的 Python 化，带进程内缓存）。

端点（akso-cc 调研 §5.3 实证）：
- GET /api/openapi/v1.0/BasicObject/{code}          对象元数据（4.8）
- GET /api/openapi/v1.0/BasicObject/field/{code}    字段降级通道（4.6）
- GET /api/openapi/v1.0/BasicObject/lifecycleStatus/{code}  生命周期状态（4.5）
"""

from __future__ import annotations

from typing import Any

import httpx

from .cache import cache_key, default_cache
from .client import ApiError
from .kernel import endpoints


def _is_missing(exc: Exception) -> bool:
    """这次失败是否等于"对象不存在/无权看"——只有这一种才允许回落 `None`。

    ★ 旧实现 `except Exception: return None` 把**超时、DNS、平台 500** 也变成了
    "这个对象没有选项集"，调用方随后会拿 `None` 当"确认不存在"继续跑。
    现在只吞两类**有身份**的未命中；网络类与 5xx 必须抛给调用方。
    """
    if isinstance(exc, ApiError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (401, 403, 404)
    return False


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
        """选项集（验证 / 幂等 / **字段的 picklistId 解析**三处共用）。

        ⚠️ **真机响应形状（2026-09-21 实测）**：`data` 是**选项数组**，不是选项集对象：

            {"code":0,"message":null,
             "data":[{"id":"<选项id>","objectPicklistId":"<选项集id>",
                      "name":"选项A","code":"A__c","sort":0}, ...]}

        所以「选项集 id」在 `objectPicklistId` 里，**不在** `data["id"]`。

        这里**统一归一化成一个 dict 再返回**。为什么不在三个调用点各修一次：
        它们都写的是 `meta.get("id")`，而 `data` 是 list 时 `list.get` 会
        `AttributeError`，那个异常又会被调用方的 `try/except` 吞掉 ——
        表现为「拿不到 id」而不是「报错」。把形状差异收在这一个函数里，
        比在三个地方各写一遍更不容易再错一次（见 docs/PITFALLS.md #67）。

        平台对**不存在**的编码不是回 404 或空数组，而是 HTTP 200 + `code:500`
        + `message:"Object reference not set to an instance of an object."`；
        `client.get` 会据此抛 `ApiError`，被下面吞成 `None`（= 不存在）。
        """
        try:
            data = self._client.get(endpoints.render(endpoints.OBJECT_PICKLIST, code=code))
        except (ApiError, httpx.HTTPStatusError) as exc:
            if _is_missing(exc):
                return None
            raise
        if not data:
            return None
        if isinstance(data, list):
            options = [opt for opt in data if isinstance(opt, dict)]
            first = options[0] if options else {}
            return {"id": first.get("objectPicklistId"), "code": code, "options": options}
        return data if isinstance(data, dict) else None

    def get_workflow(self, code: str) -> dict[str, Any] | None:
        try:
            data = self._client.get(endpoints.render(endpoints.WORKFLOW, code=code))
            return data or None
        except (ApiError, httpx.HTTPStatusError) as exc:
            if _is_missing(exc):
                return None
            raise


def invalidate_object_cache(code: str | None = None) -> None:
    """创建/修改配置后失效缓存（全清，或按对象**跨全部环境**清）。"""
    cache = default_cache()
    if code is None:
        cache.clear()
    else:
        cache.invalidate(codes={code})
