"""选项集创建（akso-auto create-picklist.js 的 Python 化）。"""

from __future__ import annotations

import re
from typing import Any

from .endpoints import PICKLIST_SAVE


def option_name_to_code(name: str, index: int) -> str:
    """选项缺 code 的自动生成（上游口径）。"""
    base = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "_", name or "").lower()
    return (base[:20] + "__c") if base else f"opt_{index}__c"


def create_picklist(client: Any, *, name: str, code: str,
                    options: list[dict[str, Any]]) -> dict[str, Any]:
    """创建选项集；创建接口不回 ID，成功后经 OpenAPI 回查 objectPicklistId（实证）。"""
    processed = []
    for idx, opt in enumerate(options):
        processed.append({
            "name": opt.get("name"),
            "code": opt.get("code") or option_name_to_code(str(opt.get("name") or ""), idx),
            "status": opt.get("status", 1),
            "operations": opt.get("operations", 1),
            "sort": opt.get("sort", idx),
        })
    client.post(PICKLIST_SAVE, {"name": name, "code": code, "options": processed})
    picklist_id: str | None = None
    try:
        from ..queries import GaiaQueries

        meta = GaiaQueries(client).get_picklist(code)
        if meta:
            picklist_id = str(meta.get("id") or meta.get("objectPicklistId") or "") or None
    except Exception:  # noqa: BLE001 —— 回查失败不影响创建结果
        picklist_id = None
    return {"success": True, "name": name, "code": code, "picklistId": picklist_id,
            "message": f"选项集[{name}]创建成功"}
