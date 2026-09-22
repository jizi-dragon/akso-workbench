"""布局创建（akso-auto save-form-layout.js + form-layout-builder.js +
save-list-layout.js + list-layout-builder.js 的 Python 化）。

⚠ SaveLayoutDetail / AddColumns 均为全量替换语义：必须先读旧结构、
合并新内容后再整体提交（上游实证，漏读合并 = 清掉平台已有布局）。
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

from .endpoints import (
    FIELD_PAGE,
    FIELDS_AND_OUT_FIELD,
    LAYOUT_DETAIL,
    LAYOUT_LIST,
    LAYOUT_SAVE_DETAIL,
    LAYOUT_STABLE_IDS,
    LISTLAYOUT_ADD_COLUMNS,
    LISTLAYOUT_COLUMNS,
    LISTLAYOUT_LIST,
    LISTLAYOUT_SAVE,
)

#: **真机实测的** `dataType → AK 组件名` 表（控件写体的 `type` 值）。
#:
#: 2026-09-22 逐条取证：**9 种 dataType → 7 个 `AK*` 值**（多对一，**不是双射**）——
#: `1→AKInput`、`2→AKInputNumber`、`3→AKSelectorYesNo`、`4→AKSelectorSingle`、
#: `5/7→AKDateTime`、`8→AKAttachment`、`15/16→AKSelectorObject`。
#:
#: ⚠️ **13/14/17/18/23 一个样本都没有** ⇒ 取不到就**显式失败**，**不许外推、不许退化成 `AKInput`**。
#:
#: 📌 这张表**取代**了原来那张自造词表（`CONTROL_TYPE_BY_DATA_TYPE`：`input`/`object`/`date`… 13 个值，
#: 真机**从未出现过**任何一个是那个拼法）——旧表已删除，`keygate.py` 的词表层改盯这一张
#: （`VOCAB_PAIRS`），这样"又编一个没实测过的类型名"会被当场抓住。
AK_BY_DATA_TYPE: dict[int, str] = {
    1: "AKInput", 2: "AKInputNumber", 3: "AKSelectorYesNo", 4: "AKSelectorSingle",
    5: "AKDateTime", 7: "AKDateTime", 8: "AKAttachment",
    15: "AKSelectorObject", 16: "AKSelectorObject",
}

#: 保存布局体顶层要带的 **17 个键**（真机 25 条样本逐条一致）。
#:
#: ⚠️ 旧实现只发 4 键（`id / basicObjectId / sections / controls`）—— 而 `code` / `name` /
#: `source` / `isMain` / `objectTypeId` / `labelCol` / `status` 这些是**这份布局自己的身份**；
#: 该端点是**整份提交**语义（空保存也整份重写）⇒ 漏发身份键可能把布局改名/改来源/清掉主布局标记。
#: ⇒ 正确姿势：**读旧详情 → 原样带回身份键**，只替换 `sections`/`controls`（见 D-C②/D-F）。
_SAVE_TOP_KEYS = (
    "activePageCode", "activeSectionCode", "code", "controls", "formEditStatus", "id",
    "isMain", "labelCol", "name", "objectId", "objectTypeId", "pages", "rules",
    "sections", "skipRequiredValidation", "source", "status",
)

#: 新建「部分」的最小形状（真机实测 5 键；**已有的部分原样带回，不重建**）。
_NEW_SECTION_KEYS = ("id", "name", "code", "type", "columnsNum")


def get_form_layouts(client: Any, object_id: str) -> list[dict[str, Any]]:
    """读该对象的表单布局清单。

    ⚠️ **`LAYOUT_LIST` 回的是分页信封，不是数组**（2026-09-22 真机实测，用户授权后修）：
    实测返回 `{count, hasUp, hasNext, datas:[…], pageIndex, pageSize, rows, pageCount, skip}`。
    旧写法 `list(client.get(LAYOUT_LIST, …) or [])` 对 dict 迭代，拿到的是**键名**，
    于是 `find_default_form_layout` 里的 `layout.get("source")` 立刻
    `AttributeError: 'str' object has no attribute 'get'` —— **表单布局这一步在"读默认布局"
    就断了**（走不到保存，更走不到加控件），而它前面"对象""字段"两步**已经真的写进平台**
    ⇒ 每次跑到这里都留下半成品。

    ⚠️ **同一份代码里两个端点的信封形状不同**，这是本条最容易复发的地方：
    `LISTLAYOUT_LIST` 回的是**真数组**（同一次实测，`get_list_layouts` 一直正常），
    所以"列表布局能跑通、表单布局跑不通"看起来像功能差异，其实是信封差异。
    判据与复现见 `docs/PITFALLS.md` **#81**（"把 dict 当 list 迭代不报错"）。
    """
    rows = client.get(LAYOUT_LIST, params={"basicObjectId": object_id,
                                           "pageIndex": 1, "pageSize": 100}) or []
    if isinstance(rows, dict):  # 分页信封：datas / items 两种口味都认，别只认一种
        rows = rows.get("datas") or rows.get("items") or []
    return [row for row in rows if isinstance(row, dict)]


def find_default_form_layout(client: Any, object_id: str) -> dict[str, Any] | None:
    for layout in get_form_layouts(client, object_id):
        if layout.get("source") == 2:
            return layout
    return None


def get_form_layout_detail(client: Any, layout_id: str, object_id: str) -> dict[str, Any]:
    """⚠ 双参数缺一返回 500。"""
    return dict(client.get(LAYOUT_DETAIL, params={"layoutId": layout_id,
                                                  "basicObjectId": object_id}) or {})


def save_form_layout(client: Any, *, identity: dict[str, Any], object_id: str,
                     sections: list[dict[str, Any]], controls: list[dict[str, Any]]) -> dict[str, Any]:
    """按真机 **17 键**提交（身份键从读回的详情**原样带回**）。

    ⚠️ 两处 2026-09-22 修正（都有真机判据）：
    1. **写体顶层用 `objectId`**（旧实现发 `basicObjectId`）—— 真机 25 条样本一致；
       实测后果：发错时平台回 **`code=500 message=数据不存在`**。
       ⚠️ 别改错地方：**读**布局的参数仍叫 `basicObjectId`（`get_form_layouts` / `get_form_layout_detail`），
       真机读侧也是它。
    2. **不再只发 4 键**：`code`/`name`/`source`/`isMain`/`objectTypeId`/`labelCol`/`status`… 是这份布局
       自己的身份，而该端点是整份提交语义 ⇒ 漏发有改坏布局的风险。
    """
    body: dict[str, Any] = {key: identity.get(key) for key in _SAVE_TOP_KEYS}
    body["objectId"] = object_id
    body["sections"] = sections
    body["controls"] = controls
    client.post(LAYOUT_SAVE_DETAIL, body)
    return {"success": True, "message": "表单布局保存成功（全量替换；17 键照真机）"}


def _field_index(client: Any, object_id: str,
                 field_map: dict[str, str]) -> dict[str, dict[str, Any]]:
    """`code → {id, name, dataType}`，**以平台现有字段为准**。

    ★ 这条修的是 **D-G**：旧实现只认 `field_map`（唯一生产者＝本次创建成功的字段），
    于是"**字段已经存在、但还没进这个布局**"的会被 `continue` **静默丢掉**。
    控件既然是**字段的投影**（手册 `01D §4.1`：控件即对象字段），就该**从平台读字段**。
    """
    index: dict[str, dict[str, Any]] = {}
    try:
        rows = client.paged_post(FIELD_PAGE, {"objectId": object_id}) or []
    except Exception:  # noqa: BLE001 —— 读不到就退化成"只有本次新建的字段"，但会在 skipped 里现形
        rows = []
    for row in rows:
        code = str(row.get("code") or "")
        if code:
            index[code] = {"id": str(row.get("id") or ""), "name": row.get("name"),
                           "dataType": row.get("dataType")}
    for code, field_id in (field_map or {}).items():
        if code.startswith("_index:"):
            continue
        entry = index.setdefault(code, {"id": "", "name": None, "dataType": None})
        entry["id"] = str(field_id or entry["id"])
        dt = field_map.get(f"_index:{code}")
        if dt:
            entry["dataType"] = int(dt)
    return index


def stabilize_component_ids(client: Any, *, object_id: str, layout_code: str,
                            components: list[tuple[str, str, str]]) -> dict[str, str]:
    """把**新建**组件的客户端 UUID 换成服务端 id（真机同款的一步，**缺它则自造 id 进请求体**）。

    `components` = `[(客户端UUID, componentType, componentCode), …]`，
    其中 `componentType` ∈ `layout_section` / `layout_control`（真机实测值）。
    返回 `{客户端UUID: 服务端id}`；失败/为空时返回 `{}`（调用方保留原 UUID，不阻断）。
    """
    payload = {
        cid: {"objectId": object_id, "layoutCode": layout_code,
              "componentType": ctype, "componentCode": code}
        for cid, ctype, code in components if cid
    }
    if not payload:
        return {}
    try:
        data = client.post(LAYOUT_STABLE_IDS, {"componentIds": payload})
    except Exception:  # noqa: BLE001 —— 换 id 失败不阻断保存（但会在 message 里说明）
        return {}
    stable = (data or {}).get("stableComponentIds") if isinstance(data, dict) else None
    return {str(k): str(v) for k, v in (stable or {}).items()}


def build_form_layout(client: Any, *, object_id: str, object_code: str,
                      blueprint_layout: dict[str, Any],
                      field_map: dict[str, str]) -> dict[str, Any]:
    """读默认布局 → 按 name 复用部分 → 生成**新**元素（最小形状）→ 合并后整份提交。

    三条 2026-09-22 真机实证的规矩（段 5/段 6）：
    1. **已有的部分/控件原样带回**（它们是"完整物化形状"：部分 62 键、控件 23 键）；
    2. **本次新增的部分/控件发最小形状**：部分 **5 键**、控件 **8 键**（`type` = `AK*`）；
    3. **新增组件先换稳定 id**（`GetStableComponentIds`）再进保存体。

    返回里带 `skipped`（请求了但放不进去的字段）—— **不许静默少放**（旧实现只 `continue`）。
    """
    default_layout = find_default_form_layout(client, object_id)
    if not default_layout:
        return {"success": False, "message": "默认表单布局未找到"}
    layout_id = str(default_layout.get("id"))
    detail = get_form_layout_detail(client, layout_id, object_id)
    layout_code = str(detail.get("code") or default_layout.get("code") or "")
    sections: list[dict[str, Any]] = [dict(s) for s in (detail.get("sections") or [])]
    controls: list[dict[str, Any]] = [dict(c) for c in (detail.get("controls") or [])]
    index = _field_index(client, object_id, field_map)

    new_sections: list[dict[str, Any]] = []
    new_controls: list[dict[str, Any]] = []
    skipped: list[str] = []
    unknown: list[str] = []
    section_by_name = {str(s.get("name")): s for s in sections}

    for bp_section in blueprint_layout.get("sections", []):
        section_name = str(bp_section.get("name"))
        existing = section_by_name.get(section_name)
        if existing is not None:
            section_id = str(existing.get("id"))
        else:
            section_id = str(_uuid.uuid4())
            section = {
                "id": section_id, "name": section_name, "code": bp_section.get("code"),
                "type": bp_section.get("type", 1), "columnsNum": bp_section.get("columnsNum", 2),
            }
            sections.append(section)
            new_sections.append(section)
        placed = {str(c.get("fieldId")) for c in controls if str(c.get("sectionId")) == section_id}
        for field_code in bp_section.get("fields", []):
            code = str(field_code)
            meta = index.get(code) or {}
            field_id = str(meta.get("id") or "")
            if not field_id:
                skipped.append(code)
                continue
            if field_id in placed:
                continue
            ak = AK_BY_DATA_TYPE.get(int(meta.get("dataType") or 0))
            if not ak:
                # ⚠️ Fail-Closed：真机从没见过的类型**不许外推**，也不许退化成 AKInput
                unknown.append(f"{code}(dataType={meta.get('dataType')})")
                continue
            control = {
                "id": str(_uuid.uuid4()), "sectionId": section_id, "code": code,
                "name": meta.get("name") or code, "fieldId": field_id,
                "type": ak, "gridSpan": 1, "attrs": {},
            }
            controls.append(control)
            new_controls.append(control)

    if unknown:
        return {"success": False, "saved": False, "skipped": skipped, "unknownTypes": unknown,
                "message": f"未知字段类型，**未提交**（Fail-Closed，不许外推 AK 组件名）：{unknown}"}

    stable = stabilize_component_ids(
        client, object_id=object_id, layout_code=layout_code,
        components=[(str(s["id"]), "layout_section", str(s.get("code") or "")) for s in new_sections]
        + [(str(c["id"]), "layout_control", str(c.get("code") or "")) for c in new_controls])
    for element in new_sections + new_controls:
        element["id"] = stable.get(str(element["id"]), str(element["id"]))
    for control in new_controls:
        control["sectionId"] = stable.get(str(control["sectionId"]), str(control["sectionId"]))

    result = save_form_layout(client, identity=detail, object_id=object_id,
                              sections=sections, controls=controls)
    message = str(result.get("message") or "")
    if skipped:
        message += f"；⚠️ 这些字段没放进布局（平台上也查不到 id）：{skipped}"
    return {**result, "success": bool(result.get("success")) and not skipped,
            "sections": len(sections), "controls": len(controls),
            "newSections": len(new_sections), "newControls": len(new_controls),
            "stableIds": len(stable), "skipped": skipped, "message": message}


# ------------------------------------------------------------------ 列表布局

def get_list_layouts(client: Any, object_id: str) -> list[dict[str, Any]]:
    return list(client.get(LISTLAYOUT_LIST, params={"objectId": object_id}) or [])


def set_list_columns(client: Any, *, listlayout_id: str,
                     columns: list[dict[str, Any]]) -> dict[str, Any]:
    """AddColumns = 全量替换（非追加）。

    ⚠️ 数组键名是 **`listlayoutColumns`**（2026-09-22 真机实测）——旧实现发 `columns`（D-A）。
    真机请求体逐字：`{"listlayoutId": …, "listlayoutColumns": [{"fieldId", "fieldPath", "sort"}, …]}`，
    `sort` **从 1 起**。
    """
    client.post(LISTLAYOUT_ADD_COLUMNS, {"listlayoutId": listlayout_id,
                                         "listlayoutColumns": columns})
    return {"success": True, "columns": len(columns), "message": "列表列设置成功（全量替换）"}


def read_list_columns(client: Any, listlayout_id: str) -> list[dict[str, Any]]:
    """读该列表布局**现有的列**（`Listlayout/Columns`，实测可用、回真数组）。"""
    rows = client.get(LISTLAYOUT_COLUMNS, params={"listlayoutId": listlayout_id}) or []
    if isinstance(rows, dict):  # 保险：若哪天改成信封，两种口味都认
        rows = rows.get("datas") or rows.get("items") or []
    return [row for row in rows if isinstance(row, dict)]


def build_list_layout(client: Any, *, object_id: str, object_code: str,
                      blueprint_layout: dict[str, Any],
                      field_map: dict[str, str]) -> dict[str, Any]:
    """挑目标列表布局 → 读旧列 → 合并 → 全量提交（D-A/D-B 之后的样子）。

    ⚠️ `AddColumns` 是**全量替换**：不读旧列就提交 = 把平台已有的列（含平台强制列，
    例如末列的 `status_id__sys`）**清掉**，而接口照样回成功。所以这里：
    ① 读旧列 → ② 旧列原样保留（顺序不变）→ ③ 蓝图里的列按需追加 → ④ 重新编号 `sort`（从 1 起）。
    **读旧列失败时不提交**（Fail-Closed）：宁可这一步失败，也不冒清空列的风险。

    ## 目标怎么挑（2026-09-22 真机实测后定的，修的是"新建列表布局必失败"）

    真机事实（n=2，`test_0903__c` / `test_0904__c`）：**建对象时平台自动生成两个默认布局** ——
    表单 `<对象code>_base_layout__ak`（默认布局）与列表 `<对象code>_table__ak`（所有的<对象名>），
    两者 `source=2`。所以"对象上有没有列表布局"几乎总是"有"。

    于是：
    1. 蓝图给了 `name` ⇒ 先按 name 找；**找不到就退到默认（source=2）**，
       并在 message 里**明说退到了哪一个**（不许静默改目标）；
    2. 蓝图给了 `code` ⇒ 按 code 找；**找不到就新建**（`Listlayout/Save`）。
       ⚠️ **平台要求 `code` 非空**（实测：`{name, objectId, source:3, status:1}` 被拒
       —— `code=500 组件[listlayout]的Code字段不能为空`）。所以**没有 code 就不新建**
       （Fail-Closed）：不自己编一个从没在真机见过的 code 形状（D-D 的教训）。
    """
    name = blueprint_layout.get("name")
    code = blueprint_layout.get("code")
    layouts = get_list_layouts(client, object_id)
    target: dict[str, Any] | None = None
    created = False
    note = ""

    if code:
        target = next((l for l in layouts if str(l.get("code")) == str(code)), None)
    if target is None and name:
        target = next((l for l in layouts if str(l.get("name")) == str(name)), None)
    if target is None and code:
        client.post(LISTLAYOUT_SAVE, {
            "name": name or f"所有的{object_code}", "code": str(code),
            "objectId": object_id, "source": 3, "status": 1,
        })
        created = True
        layouts = get_list_layouts(client, object_id)
        target = next((l for l in layouts if str(l.get("code")) == str(code)), None)
    if target is None:
        target = next((l for l in layouts if l.get("source") == 2), None)
        if target is not None and (name or code):
            note = (f"（蓝图里的 name={name!r} / code={code!r} 平台上都没有 ⇒ "
                    f"改用默认列表布局 {target.get('name')!r} 设列；"
                    "要在平台上**新建**一个列表布局，得在蓝图里给出 `code`——平台要求它非空）")
    if target is None:
        return {"success": False, "saved": False,
                "message": ("该对象在平台上没有列表布局，且蓝图未给 `code` ⇒ 不新建"
                            "（`Listlayout/Save` 要求 `code` 非空，实测 code=500）")}

    listlayout_id = str(target.get("id"))
    try:
        old_columns = read_list_columns(client, listlayout_id)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "saved": False,
                "message": f"读旧列失败 ⇒ **不提交**（避免把已有列清空，Fail-Closed）：{exc}"}

    merged: list[tuple[str, str]] = []          # (fieldId, fieldPath)
    seen: set[str] = set()
    for column in old_columns:
        path = str(column.get("fieldPath") or "")
        field_id = str(column.get("fieldId") or "")
        if path and field_id and path not in seen:
            merged.append((field_id, path))
            seen.add(path)

    index = _field_index(client, object_id, field_map)
    skipped: list[str] = []
    for column in blueprint_layout.get("columns", []):
        raw_code = str(column.get("fieldCode", ""))
        if not raw_code or raw_code in seen:
            continue
        if "." in raw_code:
            field_id = _resolve_cross_object_field(client, object_id, raw_code)
        else:
            field_id = str((index.get(raw_code) or {}).get("id") or "")
        if not field_id:
            skipped.append(raw_code)
            continue
        merged.append((field_id, raw_code))
        seen.add(raw_code)

    columns = [{"fieldId": field_id, "fieldPath": path, "sort": position}
               for position, (field_id, path) in enumerate(merged, start=1)]
    result = set_list_columns(client, listlayout_id=listlayout_id, columns=columns)
    message = str(result.get("message") or "")
    if created:
        message = f"已新建列表布局 {name!r}（code={code}）；" + message
    if note:
        message += f"；{note}"
    if skipped:
        message += f"；⚠️ 这些字段没进列（平台上也查不到 id）：{skipped}"
    return {**result, "success": bool(result.get("success")) and not skipped,
            "keptColumns": len(old_columns), "totalColumns": len(columns),
            "layoutId": listlayout_id, "created": created,
            "skipped": skipped, "message": message}


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
