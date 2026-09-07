"""对象创建（akso-auto create-object.js 的 Python 化）。"""

from __future__ import annotations

from typing import Any

from .endpoints import SAVE_BASIC_OBJECT


def create_object(client: Any, *, name: str, code: str, enable_life_cycle: bool = True,
                  enable_signatures: bool = False) -> dict[str, Any]:
    resp = client.post(SAVE_BASIC_OBJECT, {
        "name": name, "code": code, "source": 3, "status": 1, "objectClass": 1,
        "enableLifeCycle": enable_life_cycle, "enableSignatures": enable_signatures,
        "summaryFields": [],
    })
    object_id = resp.get("id") if isinstance(resp, dict) else resp
    return {"success": True, "objectId": object_id, "code": code, "message": "对象创建成功"}
