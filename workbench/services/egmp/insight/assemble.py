"""全量盘点装配（akso-cc assemble.ts 的 Python 化）。

口径：逐对象深度遍历（meta+statuses+fields+双布局）；字段行内嵌选项集聚合为
picklists（含 usedBy 反查）；仅含 picklistId 的记入 unresolved_picklist_ids。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def build_inventory(
    meta: dict[str, Any],
    crawled_objects: list[dict[str, Any]],
    workflows: list[dict[str, Any]],
    menu_groups: list[dict[str, Any]],
    menus: list[dict[str, Any]],
    failures: list[dict[str, str]],
    unresolved_object_ids: list[str] | None = None,
) -> dict[str, Any]:
    picklists: dict[str, dict[str, Any]] = {}
    unresolved_picklist_ids: list[dict[str, str]] = []
    counts = {
        "objectCount": len(crawled_objects),
        "fieldCount": 0,
        "statusCount": 0,
        "formLayoutCount": 0,
        "listLayoutCount": 0,
        "picklistCount": 0,
        "unresolvedPicklistCount": 0,
        "workflowCount": len(workflows),
        "menuGroupCount": len(menu_groups),
        "menuCount": len(menus),
        "unresolvedObjectIdCount": len(unresolved_object_ids or []),
        "failureCount": len(failures),
    }

    for obj in crawled_objects:
        object_code = obj["code"]
        for field in obj.get("fields") or []:
            counts["fieldCount"] += 1
            options = field.get("options")
            picklist_id = field.get("picklistId")
            if options:
                key = str(field.get("picklistCode") or options[0].get("picklistCode") or f"id:{picklist_id}")
                entry = picklists.setdefault(
                    key,
                    {"code": field.get("picklistCode"), "id": picklist_id, "name": None,
                     "options": [], "usedBy": []},
                )
                for option in options:
                    if option not in entry["options"]:
                        entry["options"].append(option)
                used_by = f"{object_code}.{field.get('code')}"
                if used_by not in entry["usedBy"]:
                    entry["usedBy"].append(used_by)
                if entry["name"] is None:
                    entry["name"] = field.get("picklistName") or field.get("name")
            elif picklist_id:
                counts["unresolvedPicklistCount"] += 1
                unresolved_picklist_ids.append(
                    {"id": str(picklist_id), "field": f"{object_code}.{field.get('code')}"}
                )
        counts["statusCount"] += len(obj.get("statuses") or [])
        counts["formLayoutCount"] += len(obj.get("formLayouts") or [])
        counts["listLayoutCount"] += len(obj.get("listLayouts") or [])

    return {
        "meta": {
            "baseUrl": meta.get("base_url", ""),
            "envId": meta.get("env_id", ""),
            "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "discoveryVia": meta.get("discovery_via", ""),
            "scope": "platform-config-metadata",
        },
        "summary": counts,
        "objects": crawled_objects,
        "picklists": list(picklists.values()),
        "unresolvedPicklistIds": unresolved_picklist_ids,
        "workflows": workflows,
        "menuGroups": menu_groups,
        "menus": menus,
        "unresolvedObjectIds": unresolved_object_ids or [],
        "failures": failures,
    }
