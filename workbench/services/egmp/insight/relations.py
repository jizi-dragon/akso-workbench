"""L2 关系网（akso-cc understand/relations.ts 的 Python 化，纯函数）。

口径：字段 referenceObjectCode（dataType 13/15/16/23）+ enableTree 自环。
"""

from __future__ import annotations

from typing import Any

from ..auth import is_reference_field


def build_relation_graph(fields: list[dict[str, Any]], object_name: str,
                         enable_tree: bool, *, object_code: str = "") -> dict[str, Any]:
    """由字段构建 nodes/edges。自环 = 树形引用自身。

    ``object_name`` 只用于自身节点的**显示**；判定自环必须比 **code**
    （字段里的 `referenceObjectCode` 是编码）。历史缺陷：拿编码去比显示名，
    比较恒假 → `enableTree=false` 时自引用与 `selfLoop` 标记双双失效。
    """
    self_code = object_code or object_name
    nodes: list[dict[str, Any]] = [{"id": object_name, "kind": "self"}]
    edges: list[dict[str, Any]] = []
    seen: set[str] = {object_name}

    for field in fields:
        data_type = field.get("dataType")
        if not is_reference_field(int(data_type) if data_type is not None else 0):
            continue
        target = str(field.get("referenceObjectCode") or "").strip()
        if not target:
            continue
        if target == self_code and not enable_tree:
            continue
        if target not in seen:
            nodes.append({"id": target, "kind": "reference"})
            seen.add(target)
        edges.append({
            "source": object_name,
            "target": target,
            "field": field.get("code"),
            "fieldName": field.get("name"),
            "dataType": data_type,
            "selfLoop": target == self_code,
        })
    return {"nodes": nodes, "edges": edges}
