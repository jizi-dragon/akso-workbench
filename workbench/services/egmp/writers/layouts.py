"""布局创建（akso-auto save-form-layout.js + form-layout-builder.js +
save-list-layout.js + list-layout-builder.js 的 Python 化）。

⚠ SaveLayoutDetail / AddColumns 均为全量替换语义：必须先读旧结构、
合并新内容后再整体提交（上游实证，漏读合并 = 清掉平台已有布局）。
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

from .endpoints import (
    FIELDS_AND_OUT_FIELD,
    LAYOUT_DETAIL,
    LAYOUT_LIST,
    LAYOUT_SAVE_DETAIL,
    LISTLAYOUT_ADD_COLUMNS,
    LISTLAYOUT_LIST,
    LISTLAYOUT_SAVE,
)

CONTROL_TYPE_BY_DATA_TYPE = {
    1: "input", 2: "number", 3: "switch", 4: "select", 5: "date", 7: "datetime",
    8: "attachment", 13: "lookup", 14: "formula", 15: "object", 16: "object",
    17: "textarea", 18: "rich", 23: "muiltObject",
}


def get_form_layouts(client: Any, object_id: str) -> list[dict[str, Any]]:
    return list(client.get(LAYOUT_LIST, params={"basicObjectId": object_id,
                                                "pageIndex": 1, "pageSize": 100}) or [])


def find_default_form_layout(client: Any, object_id: str) -> dict[str, Any] | None:
    for layout in get_form_layouts(client, object_id):
        if layout.get("source") == 2:
            return layout
    return None


def get_form_layout_detail(client: Any, layout_id: str, object_id: str) -> dict[str, Any]:
    """⚠ 双参数缺一返回 500。"""
    return dict(client.get(LAYOUT_DETAIL, params={"layoutId": layout_id,
                                                  "basicObjectId": object_id}) or {})


def save_form_layout(client: Any, *, layout_id: str, object_id: str,
                     sections: list[dict[str, Any]], controls: list[dict[str, Any]]) -> dict[str, Any]:
    client.post(LAYOUT_SAVE_DETAIL, {
        "id": layout_id, "basicObjectId": object_id,
        "sections": sections, "controls": controls,
    })
    return {"success": True, "message": "表单布局保存成功（全量替换）"}


def build_form_layout(client: Any, *, object_id: str, object_code: str,
                      blueprint_layout: dict[str, Any],
                      field_map: dict[str, str]) -> dict[str, Any]:
    """读默认布局 → 按 name 复用分区 → 生成新 sections/controls → 合并旧结构后全量提交。"""
    default_layout = find_default_form_layout(client, object_id)
    if not default_layout:
        return {"success": False, "message": "默认表单布局未找到"}
    detail = get_form_layout_detail(client, str(default_layout.get("id")), object_id)
    old_sections = list(detail.get("sections") or [])
    old_controls = list(detail.get("controls") or [])

    section_by_name = {str(s.get("name")): s for s in old_sections}
    sections: list[dict[str, Any]] = [dict(s) for s in old_sections]
    controls: list[dict[str, Any]] = [dict(c) for c in old_controls]

    for bp_section in blueprint_layout.get("sections", []):
        section_name = str(bp_section.get("name"))
        if section_name in section_by_name:
            section_id = str(section_by_name[section_name].get("id"))
        else:
            section_id = str(_uuid.uuid4())
            sections.append({
                "id": section_id, "name": section_name, "code": bp_section.get("code"),
                "type": bp_section.get("type", 1),
                "columnsNum": bp_section.get("columnsNum", 2),
                "sort": len(sections),
            })
        existing_in_section = {str(c.get("fieldId")) for c in controls
                               if str(c.get("sectionId")) == section_id}
        for sort, field_code in enumerate(bp_section.get("fields", [])):
            field_id = field_map.get(str(field_code))
            if not field_id or field_id in existing_in_section:
                continue
            fields_by_id = {str(f.get("id")): f for f in []}  # dataType 由 field_map 附带（见下）
            controls.append({
                "id": str(_uuid.uuid4()), "sectionId": section_id,
                "fieldId": field_id, "fieldCode": field_code,
                "ctrlType": CONTROL_TYPE_BY_DATA_TYPE.get(
                    _data_type_of(field_map, field_code, fields_by_id), "input"),
                "sort": sort, "invisible": False, "disabled": False,
            })
    result = save_form_layout(client, layout_id=str(default_layout.get("id")),
                              object_id=object_id, sections=sections, controls=controls)
    return {**result, "sections": len(sections), "controls": len(controls)}


def _data_type_of(field_map: dict[str, str], field_code: str,
                  _unused: dict[str, Any]) -> int:
    # field_map 值为 id；dataType 通过 fields 索引附带（orchestrate 传 field_index）
    index = field_map.get(f"_index:{field_code}")
    return int(index) if index else 1


# ------------------------------------------------------------------ 列表布局

def get_list_layouts(client: Any, object_id: str) -> list[dict[str, Any]]:
    return list(client.get(LISTLAYOUT_LIST, params={"objectId": object_id}) or [])


def set_list_columns(client: Any, *, listlayout_id: str,
                     columns: list[dict[str, Any]]) -> dict[str, Any]:
    """AddColumns = 全量替换（非追加）。"""
    client.post(LISTLAYOUT_ADD_COLUMNS, {"listlayoutId": listlayout_id, "columns": columns})
    return {"success": True, "columns": len(columns), "message": "列表列设置成功（全量替换）"}


def build_list_layout(client: Any, *, object_id: str, object_code: str,
                      blueprint_layout: dict[str, Any],
                      field_map: dict[str, str]) -> dict[str, Any]:
    """无 name 改默认布局（source=2）；有 name 新建。可选 filters 失败不阻断。"""
    name = blueprint_layout.get("name")
    layouts = get_list_layouts(client, object_id)
    if name:
        target = next((l for l in layouts if str(l.get("name")) == str(name)), None)
        if not target:
            client.post(LISTLAYOUT_SAVE, {
                "name": name, "objectId": object_id, "source": 3, "status": 1,
            })
            layouts = get_list_layouts(client, object_id)
            target = next((l for l in layouts if str(l.get("name")) == str(name)), None)
    else:
        target = next((l for l in layouts if l.get("source") == 2), None)
    if not target:
        return {"success": False, "message": "列表布局目标未找到"}

    columns = []
    for column in blueprint_layout.get("columns", []):
        raw_code = str(column.get("fieldCode", ""))
        if "." in raw_code:
            field_id = _resolve_cross_object_field(client, object_id, raw_code)
        else:
            field_id = field_map.get(raw_code)
        if not field_id:
            continue
        columns.append({"fieldId": field_id, "fieldPath": raw_code,
                        "sort": column.get("sort", len(columns))})
    result = set_list_columns(client, listlayout_id=str(target.get("id")), columns=columns)
    return result


def _resolve_cross_object_field(client: Any, object_id: str, path: str) -> str | None:
    """点号跨对象路径 obj__c.field → 目标字段 UUID（上游 FieldsAndOutField 通道）。"""
    try:
        rows = client.get(FIELDS_AND_OUT_FIELD, params={"objectId": object_id}) or []
        for row in rows:
            if str(row.get("fieldPath") or row.get("code")) == path:
                return str(row.get("id"))
    except Exception:  # noqa: BLE001
        pass
    return None
