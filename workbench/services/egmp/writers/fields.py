"""字段创建路由器（akso-auto create-field.js + field-router.js 的 Python 化）。"""

from __future__ import annotations

import concurrent.futures as futures
from typing import Any

from ..queries import GaiaQueries
from .endpoints import FIELD_PAGE, SAVE_FIELD
from .picklists import create_picklist  # noqa: F401 —— 转出口
from .responses import created_id


def _field_by_code(client: Any, object_id: str, code: str) -> dict[str, Any] | None:
    """按编码回读刚建的字段。**用 objectId 走 FieldPage**，不用对象编码走 OpenAPI
    —— 建字段这一层手上只有 `object_id`，为一条回读去改签名不划算。"""
    for field in query_fields(client, object_id):
        if str(field.get("code")) == code:
            return field
    return None


def create_field(client: Any, *, object_id: str, name: str, code: str, data_type: int,
                 extras: dict[str, Any] | None = None) -> dict[str, Any]:
    """默认注入 statisticsArrange:{id:1}、invisible/disabled=false（上游口径）。

    ⚠️ **真机实证（2026-09-21）**：`SaveField` 与 `SaveBasicObject` 一样只回
    `{code:0, data:true}`，**不回 id** —— 旧写法把布尔当成 id，`fieldMap` 里于是
    存进字符串 `"True"`，被布局步骤当作 fieldId 发出去。见 `writers/responses.py`。
    """
    body: dict[str, Any] = {
        "name": name, "code": code, "dataType": data_type, "objectId": object_id,
        "invisible": False, "disabled": False,
        "statisticsArrange": {"id": 1},
    }
    body.update(extras or {})
    body.pop("extras", None)
    field_id = created_id(client, path=SAVE_FIELD, body=body,
                          refetch=lambda: _field_by_code(client, object_id, code))
    if not field_id:
        return {"success": False, "name": name, "code": code, "fieldId": None,
                "message": f"字段[{name}]已创建但回读不到 id"}
    return {"success": True, "name": name, "code": code, "fieldId": field_id,
            "message": f"字段[{name}]创建成功"}


def query_fields(client: Any, object_id: str) -> list[dict[str, Any]]:
    return client.paged_post(FIELD_PAGE, {"objectId": object_id})


def collect_extras(field: dict[str, Any]) -> dict[str, Any]:
    """按 dataType 抽取类型专属参数（collectExtras 口径）。

    ⚠️ **`isRequired` 不在这里了**（2026-09-22 真机实证，D-H）：平台自己的客户端在
    **11 条 `SaveField`** 里**一次都没发过**它；而字段**读侧** 93 键里**有** `isRequired`
    ⇒ 写入侧与读取侧词表不一致。手册口径是「**必填**落在**控件层**（布局里那个控件的 `required`）」，
    不是字段写入体。
    ⇒ 所以这里不再拼它；蓝图里的 `isRequired` 由 `batch_create_fields` **如实上报**成人工项
    （**不许静默失效**——见 AGENTS.md 规则 4 的同族要求）。
    """
    data_type = field.get("dataType")
    extras: dict[str, Any] = {}
    if field.get("maxLength") is not None:
        extras["maxLength"] = field["maxLength"]
    if field.get("format"):
        extras["format"] = field["format"]
    if data_type == 2:
        # ⚠️ **键名必须用平台的**：`minValue` / `maxValue` / `scale`，
        # **不是** `min` / `max` / `decimalPlaces`。
        #
        # 真机实证（2026-09-21）：蓝图侧叫 `min`/`max`/`decimalPlaces`，直接把这个名字
        # 发出去，平台**静默丢弃** —— 接口回 `data:true`、字段也建出来了，
        # 只有回读 `minValue`/`maxValue` 才会发现是空的。对照实验：
        #   · 工厂发 `min`/`max`        → 平台 `minValue="" maxValue=""`
        #   · 界面发 `minValue`/`maxValue` → 平台 `minValue="1" maxValue="888888"`
        # 见 docs/PITFALLS.md #69。
        # `scale`（小数位数，手册 §3.4）取自平台字段 schema —— 同一份 schema 也是
        # `minValue`/`maxValue` 的来源，那两个已被界面请求体确证，故一并采用并**真机复验**。
        for src, dst in (("min", "minValue"), ("max", "maxValue"),
                         ("decimalPlaces", "scale")):
            if field.get(src) is not None:
                extras[dst] = field[src]
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
    """并发 5 批次创建并维护 fieldMap（code → id）；引用/选项字段先解析 ID。

    ## 幂等：先读一次字段全表，已存在的**跳过并复用它的 id**（2026-09-22 真机实测）

    旧实现**无条件** POST `SaveField`，于是"幂等可重跑"这句话在字段这一层是假的：
    重跑一份已经建过字段的蓝图，平台回 **`code=500 字段名称重复`**，
    而那个异常会**一路穿出整条管道**（连 `result.json` 都写不出来）——
    2026-09-22 建「测试09-04」时就撞了：对象建好了、字段建好了，
    重跑补布局时死在字段步骤，现场只剩半成品。

    平台自己的字段读侧（`FieldPage`）本来就能按 code 认出来，所以这里读**一次**全表
    （不是每个字段一次），建过的直接用它的 id 进 `fieldMap`（布局步骤要用）。
    """
    field_map: dict[str, str] = {}
    results: list[dict[str, Any]] = []
    existing = {str(f.get("code")): f for f in query_fields(client, object_id)}

    def _create(field: dict[str, Any]) -> dict[str, Any]:
        code = str(field.get("code"))
        found = existing.get(code)
        if found:
            return {"success": True, "name": field.get("name"), "code": code,
                    "fieldId": str(found.get("id") or ""), "existing": True,
                    "dataType": found.get("dataType"),
                    "message": "字段已存在，复用（不重复创建）"}
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
                            code=code,
                            data_type=int(field.get("dataType")), extras=extras)

    with futures.ThreadPoolExecutor(max_workers=5) as pool:
        for field, result in zip(fields, pool.map(_create, fields), strict=True):
            if result.get("success"):
                code = str(field.get("code"))
                field_map[code] = str(result.get("fieldId") or "")
                # 已存在的字段以**平台读回的 dataType** 为准（蓝图里可能没写对）
                field_map[f"_index:{code}"] = str(
                    result.get("dataType") if result.get("existing") else field.get("dataType"))
            results.append(result)
            if on_log:
                on_log(f"   字段[{field.get('name')}] {result.get('message')}")
    # ⚠️ 蓝图里声明了必填的字段：**如实上报**，别让它静默失效（D-H）。
    # 为什么现在表达不了：真机 `SaveField` 从不发 `isRequired`，而**写侧控件**的样本里也没有
    # 承载"必填"的键（读侧控件 33 键有 `required`）⇒ 在拿到写侧证据之前**不猜键名**，
    # 改为把它变成一条**人工项**（会出现在编排结果与检查表里）。
    required_but_unexpressed = [str(f.get("code")) for f in fields if f.get("isRequired")]
    notes: list[str] = []
    if required_but_unexpressed:
        notes.append("必填（isRequired）在写入侧暂无表达：真机 SaveField 从不发该键，"
                     "而平台把必填放在控件层（读侧有 required、写侧尚无样本）⇒ "
                     f"这些字段的必填**需要人工在布局里设置**：{required_but_unexpressed}")
    return {"fieldMap": field_map, "results": results, "notes": notes}
