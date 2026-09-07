"""字段创建路由器（akso-auto create-field.js + field-router.js 的 Python 化）。"""

from __future__ import annotations

import concurrent.futures as futures
from typing import Any

from ..queries import GaiaQueries
from .endpoints import FIELD_PAGE, SAVE_FIELD
from .picklists import create_picklist  # noqa: F401 —— 转出口


def create_field(client: Any, *, object_id: str, name: str, code: str, data_type: int,
                 extras: dict[str, Any] | None = None) -> dict[str, Any]:
    """默认注入 statisticsArrange:{id:1}、invisible/disabled=false（上游口径）。"""
    body: dict[str, Any] = {
        "name": name, "code": code, "dataType": data_type, "objectId": object_id,
        "invisible": False, "disabled": False,
        "isRequired": (extras or {}).get("isRequired", False),
        "statisticsArrange": {"id": 1},
    }
    body.update(extras or {})
    body.pop("extras", None)
    resp = client.post(SAVE_FIELD, body)
    field_id = resp.get("id") if isinstance(resp, dict) else resp
    return {"success": True, "name": name, "code": code, "fieldId": field_id,
            "message": f"字段[{name}]创建成功"}


def query_fields(client: Any, object_id: str) -> list[dict[str, Any]]:
    return client.paged_post(FIELD_PAGE, {"objectId": object_id})


def collect_extras(field: dict[str, Any]) -> dict[str, Any]:
    """按 dataType 抽取类型专属参数（collectExtras 口径）。"""
    data_type = field.get("dataType")
    extras: dict[str, Any] = {"isRequired": bool(field.get("isRequired"))}
    if field.get("maxLength") is not None:
        extras["maxLength"] = field["maxLength"]
    if field.get("format"):
        extras["format"] = field["format"]
    if data_type == 2:
        for key in ("max", "min", "decimalPlaces"):
            if field.get(key) is not None:
                extras[key] = field[key]
    if data_type == 8:
        for key in ("attachmentCount", "attachmentSize", "attachmentExtension"):
            if field.get(key) is not None:
                extras[key] = field[key]
    if data_type == 14:
        extras["returnType"] = field.get("returnType")
        extras["formulaScript"] = field.get("formulaScript")
    if field.get("setDefaultValue") is not None:
        extras["setDefaultValue"] = field["setDefaultValue"]
    return extras


def resolve_picklist_id(client: Any, picklist_code: str, local_map: dict[str, str]) -> str | None:
    """picklistMap 优先 → OpenAPI 查询回退。"""
    if picklist_code in local_map:
        return local_map[picklist_code]
    meta = GaiaQueries(client).get_picklist(picklist_code)
    return str(meta.get("id")) if meta else None


def resolve_reference_object_id(client: Any, reference_code: str) -> str | None:
    meta = GaiaQueries(client).get_object_info(reference_code)
    return str(meta.get("id")) if meta else None


def batch_create_fields(client: Any, *, object_id: str, fields: list[dict[str, Any]],
                        picklist_map: dict[str, str],
                        on_log: Any = None) -> dict[str, Any]:
    """并发 5 批次创建并维护 fieldMap（code → id）；引用/选项字段先解析 ID。"""
    field_map: dict[str, str] = {}
    results: list[dict[str, Any]] = []

    def _create(field: dict[str, Any]) -> dict[str, Any]:
        extras = collect_extras(field)
        if field.get("dataType") == 4 and field.get("picklistCode"):
            picklist_id = resolve_picklist_id(client, str(field["picklistCode"]), picklist_map)
            if picklist_id:
                extras["picklistId"] = picklist_id
        if field.get("dataType") in {13, 15, 16, 23} and field.get("referenceObjectCode"):
            ref_id = resolve_reference_object_id(client, str(field["referenceObjectCode"]))
            if ref_id:
                extras["referenceObjectId"] = ref_id
        return create_field(client, object_id=object_id, name=str(field.get("name")),
                            code=str(field.get("code")),
                            data_type=int(field.get("dataType")), extras=extras)

    with futures.ThreadPoolExecutor(max_workers=5) as pool:
        for field, result in zip(fields, pool.map(_create, fields), strict=True):
            if result.get("success"):
                field_map[str(field.get("code"))] = str(result.get("fieldId") or "")
                field_map[f"_index:{field.get('code')}"] = str(field.get("dataType"))
            results.append(result)
            if on_log:
                on_log(f"   字段[{field.get('name')}] {result.get('message')}")
    return {"fieldMap": field_map, "results": results}
