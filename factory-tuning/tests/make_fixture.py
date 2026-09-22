"""生成合成样例会话 —— **用来证明分析链路能跑，不是真实数据**。

## 为什么需要它

真实的采集数据要等用户在平台上配一遍才存在。在那之前，"分析脚本能不能跑"
这件事**无法验证**——而一个从没跑过的脚本，按本仓的纪律等于不存在。

所以这里造一份**明显是合成**的会话：端点选真实存在的（`kernel/endpoints.py` 里的），
但混入两个**故意没建模**的端点，好让覆盖分析的"缺口"那一段真的出现在报告里。
报告文件名带 `SAMPLE`，`meta.json` 里也写明——**不能被人误当成真实采集**。

## 用法

    .\\.venv\\Scripts\\python.exe factory-tuning\\tests\\make_fixture.py
"""

from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "SAMPLE-20260101-000000"

#: (端点, 意图, 页面动作) —— 模拟"人在平台上手配一个对象"的一条流水
SCRIPT: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("/api/platform/BasicObject/SaveBasicObject", "save_object",
     [("click", "新建对象"), ("change", "培训记录")]),
    ("/api/platform/BasicObject/SaveField", "field_text",
     [("click", "添加字段"), ("change", "培训主题")]),
    ("/api/platform/BasicObject/SaveField", "field_number",
     [("click", "添加字段"), ("change", "参训人数")]),
    # ⚠ 下面这条**故意不在 kernel/endpoints.py 里**：覆盖分析的"缺口"要有东西可报
    ("/api/platform/BasicObject/SaveFieldDefaultValue", "default_value",
     [("click", "设置默认值")]),
    ("/api/platform/ObjectPicklist/save", "picklist",
     [("click", "新建选项集"), ("change", "培训类型")]),
    ("/api/config/lifecycle/Status/Create", "status",
     [("click", "新增状态"), ("change", "待确认")]),
    ("/api/platform/Workflow/AddWorkflowBasic", "workflow",
     [("click", "新建工作流"), ("change", "培训审批")]),
    ("/api/platform/Workflow/AddWorkflowTaskStep", "task_step",
     [("click", "添加审批步骤")]),
    # ⚠ 这条也不在端点表里
    ("/api/platform/BasicObject/SaveOutField", "out_field",
     [("click", "配置跨对象字段")]),
    ("/api/platform/Layout/SaveLayoutDetail", "layout",
     [("click", "保存布局")]),
]

#: 真实的请求体形状（键名取自产品的模型，另加两个**模型里没有**的键，
#: 好让形状分析真的报出缺口）
BODIES: dict[str, dict] = {
    "save_object": {"name": "培训记录", "code": "training_record__c",
                    "enableLifeCycle": True, "enableSignatures": False,
                    "source": 3, "status": 1, "objectClass": 1,
                    "tenantId": "11111111-2222-3333-4444-555555555555"},
    "field_text": {"name": "培训主题", "code": "topic__c", "dataType": 1,
                   "isRequired": True, "maxLength": 17,
                   # ↓ 模型里没有的键：形状分析应当把它报成 schema 候选缺口
                   "outFieldConfig": {"target": None}},
    "field_number": {"name": "参训人数", "code": "attendee_count__c", "dataType": 2,
                     "min": 0, "max": 999, "decimalPlaces": 0},
    "default_value": {"fieldId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                      "defaultValue": "未填", "applyToNew": True},
    "picklist": {"name": "培训类型", "code": "training_type",
                 "options": [{"name": "内部", "status": 1, "operations": 1, "sort": 1}]},
    "status": {"name": "待确认", "code": "status_pending__c"},
    "workflow": {"name": "培训审批", "code": "training_approval__c",
                 "autoEnable": True, "bindToStatusCode": "status_pending__c"},
    "task_step": {"name": "部门审批", "code": "step_dept", "type": "task",
                  "participantRef": "part_dept", "dispatchMode": 20},
    "out_field": {"objectCode": "training_record__c", "outFieldCode": "dept_owner__c"},
    # ⚠️ 布局这一条**照段 2 真机抄两套形状**（F36）：新建的组件发**精简形状**、
    # 服务端已有的组件发**完整形状**。这样 §2.5「形状谱」那条链路才**真的被跑到**，
    # 而不是每次都在"这次没有嵌套数组"的分支上空转。
    # ⚠️ 键名**只用实测过的**（`type` 只出现在 6 个实测 `AK*` 值里）——
    # 样例里编一个没见过的控件类型，等于让分析器对着假的输入被验证（PITFALLS #62 同族）。
    "layout": {
        "objectId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "sections": [
            # 新建的部分：真机是 **5 键**
            {"id": "id-section-new", "name": "基本信息2", "code": "basic2__l",
             "type": 1, "columnsNum": 2},
            # 服务端已有的部分：真机是 **62 键**（这里只列代表键；键名全部来自真机那一份）
            {"id": "id-section-old", "name": "工作流时间线", "code": "workflow_time__cs",
             "type": 4, "sort": 1, "hidden": False, "expansion": False,
             "relationId": "00000000-0000-0000-0000-000000000000", "referenceType": 0,
             "isSimpleRelation": False, "isAssociatedMultipleRecords": False,
             "listlayoutId": "00000000-0000-0000-0000-000000000000",
             "selectListlayoutId": "00000000-0000-0000-0000-000000000000",
             "selectObjectId": "00000000-0000-0000-0000-000000000000", "relationFilter": [],
             "fieldId": None, "height": None, "gridSpan": None, "columnsNum": None,
             "referenceFieldCode": None, "required": None, "visible": None, "readOnly": None},
        ],
        "controls": [
            # 新建的控件：真机是 **8 键**
            {"id": "id-control-new", "sectionId": "id-section-new", "code": "test_time__c",
             "name": "测试时间", "fieldId": "id-field-time", "type": "AKDateTime",
             "gridSpan": 2, "attrs": {"readPrettyShowType": "1"}},
            # 服务端已有的控件：真机是 **23 键**（含 13 个 `null` 键）
            {"id": "id-control-old", "sectionId": "id-section-old", "code": "test_file__c",
             "name": "测试附件", "sort": 1, "fieldId": "id-field-file", "dataType": 8,
             "type": "AKAttachment", "gridSpan": 1,
             "attrs": {"readPrettyShowType": "1"},
             "isRecalculateDefaultOnDependencyChange": False,
             "visible": None, "readOnly": None, "readPretty": None, "hidden": None,
             "hideWhenNotData": None, "required": None, "defaultValue": None,
             "defaultValueDepFields": None, "forceAssignment": None,
             "displayOnlyInLifecycleStatusIds": None, "externalDataConfig": None,
             "combinationDisplayFieldId": None},
        ],
    },
}

#: 每个动作相对该条请求的时间（ms，页面时钟）。**样例也要有**，否则 §3.1
#: 「动作缓冲逐格」在自检里永远走"没有带时间戳的动作"那个分支（= 没被验证）。
ACTION_OFFSETS_MS = (-2100, -1650, -300)


def main() -> int:
    sys.path.insert(0, str(HERE.parent / "tools"))
    from redact import redact_body  # noqa: PLC0415

    FIXTURE.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, (endpoint, key, actions) in enumerate(SCRIPT, 1):
        rows.append({
            "seq": index,
            "at": 1767225600000 + index * 1000,
            "method": "POST",
            "path": endpoint,
            "status": 200,
            "known": False,
            # ⚠️ 刻意**过一遍真正的脱敏函数**：这样样例与 `ingest.py` 的产物
            # 形状完全一致（GUID 已假名化、敏感键已替换）。手写一份"看起来像"的
            # 样例，等于让分析器对着一个假的输入形状被验证。
            "postData": redact_body(BODIES[key]),
            # ⚠️ 信封必须照**真机**抄。这里原本写的是 `{"code": 0, "message": "ok",
            # "success": True}` —— 那是**猜的**，真机没有任何一个响应长这样。
            # 真机信封（2026-09-21 首次完整录到，见 docs/PITFALLS.md #63）：
            #   {"code":0,"errorCode":0,"message":null,"data":true,"requestID":"…","traceId":"…"}
            # 内容全在 `data` 里，而 `success` 这个键**根本不存在**。
            # 样例照猜的信封写，等于让分析器对着一个假的输入形状被验证（PITFALLS #62 同族）。
            "body": {"code": 0, "errorCode": 0, "message": None, "data": True,
                     "requestID": "fixture", "traceId": "fixture"},
            # 动作的 `at` 是**页面时钟**（相对该条请求），与真机产物的形状一致——
            # 少了它，§3.1 那条链路在自检里就是空转的。
            "precedingActions": [
                {"type": t, "tag": "BUTTON", "text": s,
                 "at": 1767225600000 + index * 1000 + offset}
                for (t, s), offset in zip(actions, ACTION_OFFSETS_MS, strict=False)
            ],
        })
    (FIXTURE / "traffic.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8", newline="\n")
    (FIXTURE / "meta.json").write_text(json.dumps({
        "session": FIXTURE.name,
        "synthetic": True,
        "note": "**合成样例，不是真实采集**。由 tests/make_fixture.py 生成，"
                "只为证明分析链路能跑通；端点选真实存在的，另混入两个故意没建模的端点。",
        "entry_count": len(rows),
        "filter_stats": {"total": len(rows) + 12, "dropped": 12, "trimmed": 0, "full": len(rows)},
    }, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(f"✓ 合成样例已写入 {FIXTURE}")
    print(f"  {len(rows)} 条请求（其中 2 条用了**没建模**的端点，供覆盖分析报缺口）")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
