"""幂等层（akso-auto api/idempotency.js 的 Python 化）。

全部基于 OpenAPI 查询；存在即 skipped —— 这也是断点续跑的真正兜底
（akso-auto 引擎不落盘 checkpoint，幂等查询使重复执行天然安全）。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from ..queries import GaiaQueries
from .endpoints import OPENAPI_PICKLIST, OPENAPI_WORKFLOW


class Idempotency:
    def __init__(self, queries: GaiaQueries) -> None:
        self._queries = queries

    @staticmethod
    def _normalize(code: str) -> str:
        return re.sub(r"^(?:status_)?(.+?)(?:__c|__ak|__sys)?$", r"\1", code or "")

    def object_exists(self, code: str) -> dict[str, Any] | None:
        try:
            data = self._queries.get_object_info(code)
            return data or None
        except Exception:  # noqa: BLE001
            return None

    def picklist_exists(self, code: str) -> dict[str, Any] | None:
        try:
            data = self._queries._client.get(OPENAPI_PICKLIST.format(code=code))
            return data or None
        except Exception:  # noqa: BLE001
            return None

    def field_exists(self, object_code: str, field_code: str) -> dict[str, Any] | None:
        try:
            fields = self._queries.get_fields(object_code)
            for field in fields:
                if str(field.get("code")) == field_code:
                    return field
        except Exception:  # noqa: BLE001
            pass
        return None

    def status_exists(self, object_code: str, status_code: str) -> dict[str, Any] | None:
        try:
            statuses = self._queries.get_statuses(object_code)
            for status in statuses:
                if str(status.get("code")) == status_code:
                    return status
        except Exception:  # noqa: BLE001
            pass
        return None

    def workflow_exists(self, code: str) -> dict[str, Any] | None:
        try:
            data = self._queries._client.get(OPENAPI_WORKFLOW.format(code=code))
            return data or None
        except Exception:  # noqa: BLE001
            return None


def with_idempotency(check_fn: Callable[[], Any], create_fn: Callable[[], dict[str, Any]],
                     label: str) -> dict[str, Any]:
    """存在即跳过；否则执行创建。返回 {status: created|skipped|failed, ...}。"""
    try:
        existing = check_fn()
    except Exception as exc:  # noqa: BLE001 —— 查询失败不阻断创建
        existing = None
        label = f"{label}（存在性检查失败：{exc}）"
    if existing:
        return {"status": "skipped", "message": f"{label} 已存在，跳过", "existing": existing}
    result = create_fn()
    result.setdefault("status", "created" if result.get("success") else "failed")
    return result
