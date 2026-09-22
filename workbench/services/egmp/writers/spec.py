"""蓝图规格与审阅材料：**同一个契约的两种读法**（给 agent 的规格 / 给用户的清单）。

## 为什么需要这个模块（2026-09-20，见 docs/FACTORY-AGENT-GAP.md 问题②）

改造前，**蓝图长什么样**这件事只在两个地方存在过：

1. `services/egmp/generate.py` 里那 36 行 system prompt —— 只被 `command="orchestrate"`
   那条**已被删除**的路径使用；
2. `writers/blueprint.py` 的 pydantic 模型 —— 只有代码能读。

而真正要写蓝图的**对话 Agent 一处都看不到**。后果是它只能靠试错去猜字段名，
每次猜错 = 一次校验往返 = 一次工具调用（而步数预算是有限的）。

判据（本次改造前实测，现在已不成立）：
`Get-ChildItem workbench\\plugins\\*.py | Select-String 'referenceObjectCode|picklistCode'`
→ **0 命中**。

## 结构从运行期模型生成，语义靠注解，两者都有机检钉住

这里刻意**不手写第二份 schema 文档**——手写的必然与模型漂移，
而漂移的表现是"模型照着文档写、校验全红"（本仓已经因为"两份会漂的事实源"
栽过两次：两张敏感路径表、两套工具表）。

| 组成 | 来源 | 漂移防护 |
|---|---|---|
| 键名 / 类型 / 必填 / 取值范围 | `writers/blueprint.py` 的 pydantic 模型（`model_json_schema`） | 生成，不可能漂 |
| 每个键是什么意思 | 本模块的 `_NOTES` 注解表 | `tests/test_blueprint_spec.py` 断言**覆盖完整**：模型加了字段而没补注解 → 测试红 |
| `dataType` / `stepType` / `behaviorType` 等枚举 | `kernel/semantics.py`（上游实证词典） | 直接引用，不可能漂 |
| 编码约定（`__c` 后缀等） | `writers/blueprint.py` 的正则与常量 | 直接引用 |
| 执行顺序（8 步管道） | `orchestrate.PIPELINE_STEPS` | 函数内懒 import 同一个常量 |

## 与 `generate_review_spec` / `generate_checklist` 的分工

那两份是**给人看的**（Markdown，写进 `spec.md` / `checklist.md`，用户在页面上读）；
本模块的 `blueprint_spec()` 是**给模型看的**（结构化 JSON，模型据此写蓝图）。
两者都从同一份模型派生，不互为副本。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from ..kernel import semantics
from .blueprint import (
    CODE_OBJECT_RE,
    CODE_STATUS_RE,
    DATA_TYPES,
    REFERENCE_TYPES,
    Blueprint,
    DeclarationRuleDef,
    FieldDef,
    FormLayoutDef,
    FormSectionDef,
    LifecycleDef,
    ListColumnDef,
    ListLayoutDef,
    MenuDef,
    MenuParentDef,
    MenuSubDef,
    ObjectDef,
    OptionDef,
    PermissionDef,
    PicklistDef,
    RenameStatusDef,
    ReportTemplateDef,
    StatusDef,
    WorkflowDef,
    WorkflowParticipantDef,
    WorkflowRuleDef,
    WorkflowStepDef,
    to_object_blueprints,
)

# ---------------------------------------------------------------- 语义注解

#: `"<模型名>.<字段名>" → 一句话说明`。**每个声明字段都必须有一条**
#: （`test_blueprint_spec.py::test_every_model_field_is_annotated` 钉住）。
#:
#: 为什么要求"完整覆盖"而不是"重要字段就够"：漏一条的后果是模型对着一个
#: 没有解释的键名猜语义，而猜错的代价是一次校验往返。注解缺失是**静默**的
#: ——规格照样生成得出来，只是少一行字，所以必须靠机检而不是靠自觉。
_NOTES: dict[str, str] = {
    # 顶层
    "Blueprint.description": "这份蓝图是干什么的（会写进审阅材料，用户先看到它）",
    "Blueprint.objects": "要建的对象列表，至少 1 个",
    "Blueprint.picklists": "多个对象共享的选项集（引用它的字段写 picklistCode）",
    "Blueprint.rules": "声明区：校验规则（**引擎不执行**，进人工配置清单）",
    "Blueprint.permissions": "声明区：权限（**引擎不执行**，进人工配置清单）",
    "Blueprint.reportTemplates": "声明区：报表模板（**引擎不执行**，进人工配置清单）",
    "Blueprint.menu": "菜单挂载（母菜单 + 子菜单）",
    # 对象
    "ObjectDef.name": "对象显示名",
    "ObjectDef.code": "对象编码，必须匹配 ^[a-z][a-z0-9_]*__c$（小写 + 下划线 + __c 结尾）",
    "ObjectDef.createIfMissing": "**用户明确确认「确实要新建」之后**才写 true（默认 false）。"
                                 "不写它而平台上又存在相近对象时，创建会被预检**拒绝**并回候选清单"
                                 "——那是防「code 打错一个字就静默多出一个空对象」；"
                                 "用户确认新建后写 true，预检即放行",
    "ObjectDef.enableLifeCycle": "是否启用生命周期（启用后平台自动生成两个不可删状态，见 lifecycle）",
    "ObjectDef.enableSignatures": "是否启用电子签名",
    "ObjectDef.source": "固定 3（自定义对象），不要改",
    "ObjectDef.status": "固定 1，不要改",
    "ObjectDef.objectClass": "固定 1，不要改",
    "ObjectDef.picklists": "本对象专属的选项集（顶层 picklists 是共享的）",
    "ObjectDef.fields": "字段列表，至少 1 个",
    "ObjectDef.formLayout": "表单布局（引用字段编码，必须是本对象 fields 里存在的编码）",
    "ObjectDef.listLayout": "列表布局（列字段编码同上）",
    "ObjectDef.lifecycle": "生命周期状态定义（只在 enableLifeCycle=true 时有意义）",
    "ObjectDef.workflows": "工作流列表（需要 enableLifeCycle=true，步骤可绑定状态）",
    "ObjectDef.meta": "引擎回填的元数据（如 lifeCycleId），**不要在蓝图里写**",
    # 字段
    "FieldDef.name": "字段显示名",
    "FieldDef.code": "字段编码（本对象内唯一；建议小写 + 下划线）",
    "FieldDef.dataType": "字段类型码，见规格里的 dataTypes",
    "FieldDef.isRequired": "是否必填（⚠️ 平台写入侧无此键：真机 SaveField 从不发 isRequired，"
                           "必填落在布局控件层；工厂目前把它**上报为人工项**，不在字段上生效）",
    "FieldDef.invisible": "是否隐藏",
    "FieldDef.disabled": "是否只读",
    "FieldDef.maxLength": "文本长度上限：单行文本 ≤1500、长文本/富文本 ≤32000（手册 01B；"
                          "其它类型不适用，填了会被忽略）",
    "FieldDef.format": "文本格式约束",
    "FieldDef.max": "数字字段最大值（dataType=2；必须大于 min）",
    "FieldDef.min": "数字字段最小值（dataType=2）",
    "FieldDef.decimalPlaces": "小数位数（dataType=2）",
    "FieldDef.picklistCode": "选项集编码，**dataType=4 必填**；取值来自 picklists[].code",
    "FieldDef.referenceObjectCode": "引用目标的对象编码，**dataType 13/15/16/23 必填**",
    "FieldDef.attachmentCount": "附件数量上限（dataType=8）",
    "FieldDef.attachmentSize": "单个附件大小上限 MB（dataType=8）",
    "FieldDef.attachmentExtension": "允许的附件后缀，逗号分隔（dataType=8）",
    "FieldDef.returnType": "公式返回类型（dataType=14）",
    "FieldDef.formulaScript": "公式脚本（dataType=14）",
    "FieldDef.setDefaultValue": "默认值",
    # 选项集
    "PicklistDef.name": "选项集显示名",
    "PicklistDef.code": "选项集编码（字段用 picklistCode 引用它）",
    "PicklistDef.options": "选项列表，至少 1 个",
    "OptionDef.name": "选项显示名",
    "OptionDef.code": "选项编码（可省，平台会生成）",
    "OptionDef.status": "固定 1（启用）",
    "OptionDef.operations": "固定 1",
    "OptionDef.sort": "排序号，越小越靠前",
    # 布局
    "FormLayoutDef.sections": "表单部分，至少 1 个",
    "FormSectionDef.name": "部分显示名",
    "FormSectionDef.code": "部分编码",
    "FormSectionDef.type": "固定 1",
    "FormSectionDef.columnsNum": "该部分几列（常用 2）",
    "FormSectionDef.fields": "部分里放哪些字段（**字段编码**，不是显示名）",
    "ListLayoutDef.name": "列表布局名（**可省**；省了就是改平台的默认列表布局 `<对象code>_table__ak`）",
    "ListLayoutDef.code": "**要新建**列表布局时才填（`Listlayout/Save` 要求 code 非空，实测缺它 code=500）；"
                          "只想给默认布局设列就别填",
    "ListLayoutDef.columns": "列表列，至少 1 列",
    "ListColumnDef.fieldCode": "列对应的字段编码",
    "ListColumnDef.sort": "列顺序，越小越靠前",
    # 生命周期
    "LifecycleDef.statuses": "**新增**的状态（系统已自动生成 开启/关闭 两个状态，不要在这里重复它们）",
    "LifecycleDef.renameStatuses": "**重命名**系统状态（fromCode 只能是 "
                                   "init_status__c 或 complete_status__c）",
    "StatusDef.name": "状态显示名",
    "StatusDef.code": "状态编码，必须匹配 ^status_[a-z][a-z0-9_]*__c$",
    "RenameStatusDef.fromCode": "要改的系统状态编码（init_status__c / complete_status__c）",
    "RenameStatusDef.toName": "改成的显示名",
    # 工作流
    "WorkflowDef.name": "工作流显示名",
    "WorkflowDef.code": "工作流编码，必须匹配 __c 规范（^[a-z][a-z0-9_]*__c$）",
    "WorkflowDef.autoEnable": "是否创建后直接启用",
    "WorkflowDef.bindToStatusCode": "绑定到哪个状态（会在该状态生成提交按钮）；"
                                    "**必须出现在 lifecycle 的 statuses 或 renameStatuses 里**",
    "WorkflowDef.participants": "参与人定义（步骤用 participantRef 引用其 code）",
    "WorkflowDef.steps": "步骤列表，至少 1 步",
    "WorkflowDef.rules": "判断步骤的分支规则",
    "WorkflowStepDef.name": "步骤显示名",
    "WorkflowStepDef.code": "步骤编码（工作流内唯一）",
    "WorkflowStepDef.type": "步骤类型：task=审批任务 / decision=判断 / action=动作",
    "WorkflowStepDef.participantRef": "审批任务的参与人 code（type=task 时用）",
    "WorkflowStepDef.nextStepCode": "下一步的步骤 code（走完这一步往哪去）",
    "WorkflowStepDef.behaviorType": "动作类型码（type=action 时用），见规格里的 behaviorTypes",
    "WorkflowStepDef.behaviorValue": "动作参数（如 behaviorType=9 时填目标状态编码）",
    "WorkflowStepDef.dispatchMode": "审批方式：10=会签 / 20=或签（type=task 时用）",
    "WorkflowParticipantDef.name": "参与人显示名",
    "WorkflowParticipantDef.code": "参与人编码（步骤用 participantRef 引用它）",
    "WorkflowRuleDef.decisionStep": "判断步骤的 code",
    "WorkflowRuleDef.sourceStep": "从哪个步骤来的",
    "WorkflowRuleDef.jumpTo": "条件成立时跳到哪个步骤",
    "WorkflowRuleDef.conditionLabel": "条件的人话描述",
    "WorkflowRuleDef.matchValue": "匹配值",
    "WorkflowRuleDef.targetId": "目标 id（平台侧标识，一般不用填）",
    "WorkflowRuleDef.targetCode": "目标编码",
    "WorkflowRuleDef.logicType": "逻辑类型码（40=全部相等 / 41=任一相等 / 42=全不相等）",
    "WorkflowRuleDef.logicValue": "逻辑比较值",
    # 菜单
    "MenuDef.parent": "母菜单",
    "MenuDef.sub": "子菜单（挂到母菜单下）",
    "MenuParentDef.name": "母菜单显示名",
    "MenuParentDef.code": "母菜单编码",
    "MenuSubDef.name": "子菜单显示名",
    "MenuSubDef.code": "子菜单编码",
    "MenuSubDef.parentName": "子菜单指向的对象（对象编码或名称）",
    # 声明区
    "DeclarationRuleDef.name": "规则名",
    "DeclarationRuleDef.type": "规则类型（默认 validation）",
    "DeclarationRuleDef.code": "规则编码（可省）",
    "DeclarationRuleDef.description": "规则说明",
    "DeclarationRuleDef.source": "来源标记（人工配置项，一般不用填）",
    "DeclarationRuleDef.manual": "是否人工配置（默认 true，引擎不执行）",
    "DeclarationRuleDef.target": "规则作用的目标（对象/字段编码）",
    "DeclarationRuleDef.implement": "实现方式说明（人工配置项）",
    "PermissionDef.role": "角色名",
    "PermissionDef.roleCode": "角色编码",
    "PermissionDef.grants": "授予的能力列表",
    "PermissionDef.description": "权限说明",
    "ReportTemplateDef.name": "报表模板名",
    "ReportTemplateDef.code": "报表模板编码（可省）",
    "ReportTemplateDef.description": "模板说明",
    "ReportTemplateDef.source": "来源标记（人工配置项，一般不用填）",
    "ReportTemplateDef.manual": "是否人工配置（默认 true，引擎不执行）",
}

#: 模型 → 在规格里怎么分组。顺序即**推荐阅读顺序**。
_MODEL_GROUPS: tuple[tuple[str, type[BaseModel], str], ...] = (
    ("objects[]", ObjectDef, "一个对象 = 一次配置交付。多个对象按引用关系自动排序执行"),
    ("objects[].fields[]", FieldDef, "对象的字段"),
    ("objects[].picklists[]", PicklistDef, "本对象专属的选项集"),
    ("picklists[]（顶层）", PicklistDef, "多个对象共享的选项集"),
    ("objects[].picklists[].options[]", OptionDef, "选项集的取值"),
    ("objects[].formLayout", FormLayoutDef, "表单布局"),
    ("objects[].formLayout.sections[]", FormSectionDef, "表单部分"),
    ("objects[].listLayout", ListLayoutDef, "列表布局"),
    ("objects[].listLayout.columns[]", ListColumnDef, "列表列"),
    ("objects[].lifecycle", LifecycleDef, "生命周期"),
    ("objects[].lifecycle.statuses[]", StatusDef, "新增状态"),
    ("objects[].lifecycle.renameStatuses[]", RenameStatusDef, "重命名系统状态"),
    ("objects[].workflows[]", WorkflowDef, "工作流"),
    ("objects[].workflows[].steps[]", WorkflowStepDef, "工作流步骤"),
    ("objects[].workflows[].participants[]", WorkflowParticipantDef, "工作流参与人"),
    ("objects[].workflows[].rules[]", WorkflowRuleDef, "判断步骤的分支规则"),
    ("menu", MenuDef, "菜单挂载"),
    ("menu.parent", MenuParentDef, "母菜单"),
    ("menu.sub", MenuSubDef, "子菜单"),
    ("rules[]", DeclarationRuleDef, "声明区：校验规则（引擎不执行）"),
    ("permissions[]", PermissionDef, "声明区：权限（引擎不执行）"),
    ("reportTemplates[]", ReportTemplateDef, "声明区：报表模板（引擎不执行）"),
)

#: 最小可用蓝图。**必须真的能过校验**——`test_blueprint_spec.py` 会拿它跑一遍
#: `validate_blueprint`，所以它不可能腐烂成一段"看起来像"的示例。
MINIMAL_EXAMPLE: dict[str, Any] = {
    "description": "培训管理：一个对象 + 两个字段",
    "objects": [
        {
            "name": "培训记录",
            "code": "training_record__c",
            "fields": [
                {"name": "培训主题", "code": "topic__c", "dataType": 1, "isRequired": True},
                {"name": "参训人数", "code": "attendee_count__c", "dataType": 2, "min": 0, "max": 999},
            ],
            "formLayout": {"sections": [
                {"name": "基本信息", "code": "training_basic__l", "columnsNum": 2,
                 "fields": ["topic__c", "attendee_count__c"]},
            ]},
            "listLayout": {"columns": [
                {"fieldCode": "topic__c", "sort": 1},
                {"fieldCode": "attendee_count__c", "sort": 2},
            ]},
        },
    ],
}


# ---------------------------------------------------------------- 结构生成


def _type_label(entry: dict[str, Any]) -> str:
    """JSON Schema 片段 → 短类型标签（给人看，不追求完备）。"""
    if "const" in entry:
        return json.dumps(entry["const"], ensure_ascii=False)
    if "enum" in entry:
        return " | ".join(json.dumps(v, ensure_ascii=False) for v in entry["enum"])
    if "type" in entry:
        kind = entry["type"]
        if kind == "array":
            inner = _type_label(entry.get("items") or {})
            return f"{inner}[]"
        return str(kind)
    variants = entry.get("anyOf") or []
    if variants:
        labels = [_type_label(v) for v in variants]
        required = [x for x in labels if x != "null"]
        if len(required) == 1:
            return f"{required[0]}（可省）"
        return " | ".join(labels)
    if "$ref" in entry:
        return str(entry["$ref"]).rsplit("/", 1)[-1]
    return "?"


def _fields_of(model: type[BaseModel], prefix: str) -> list[dict[str, Any]]:
    schema = model.model_json_schema()
    required = set(schema.get("required") or [])
    out: list[dict[str, Any]] = []
    for name, entry in (schema.get("properties") or {}).items():
        key = f"{prefix}.{name}"
        out.append({
            "name": name,
            "type": _type_label(entry),
            "required": name in required,
            "note": _NOTES.get(key, ""),
        })
    return out


def blueprint_spec() -> dict[str, Any]:
    """给 agent 的蓝图规格（结构化）。**纯函数、无副作用**，可在任何上下文里调。"""
    from ..orchestrate import PIPELINE_STEPS

    return {
        "how_to_use": [
            "写一份蓝图 JSON（对象/字段/布局/生命周期/工作流/菜单）",
            "★ **先确认对象存在**：蓝图里的对象 code 若是「用户说的那个已有对象」，"
            "先用 `factory_resolve_object` 查一次——**查不到就停下来问用户**，"
            "把相近的候选念给他选；都不对再问是否要新建。"
            "（工厂的对象步骤是幂等的：code 存在就复用、不存在就**建一个新的** ⇒ "
            "打错一个字不会报错，只会静默多出一个空对象。管道层还有第二道闸："
            "对象不存在但存在相近对象时，`factory_create` 会**拒绝写入**并回候选清单）",
            "★ **用户确认「确实要新建」之后**，在该对象上写 `createIfMissing: true` 再重跑。"
            "这是唯一能让那道闸放行的凭据——**它看不见对话**，用户批准过也不算数；"
            "用户没明确确认过就不要写它（写它＝替他做了新建的决定）",
            "调用一次预览工具：校验通过就会暂存并返回 blueprint_id，"
            "同时给你一份给用户看的人话清单",
            "把那份清单讲给用户听（他要据此决定要不要真的创建）",
            "用户点头后调创建工具（会再弹一次确认门）",
        ],
        "top_level": _fields_of(Blueprint, "Blueprint"),
        "structures": [
            {"path": path, "note": note, "fields": _fields_of(model, model.__name__)}
            for path, model, note in _MODEL_GROUPS
        ],
        "dataTypes": {str(k): v for k, v in sorted(semantics.DATA_TYPE_NAMES.items())},
        "referenceDataTypes": sorted(REFERENCE_TYPES),
        "allDataTypes": sorted(DATA_TYPES),
        "behaviorTypes": {str(k): v for k, v in sorted(semantics.BEHAVIOR_TYPE_NAMES.items())},
        "stepTypes": {str(k): v for k, v in sorted(semantics.STEP_TYPE_NAMES.items())},
        "dispatchModes": {str(k): v for k, v in sorted(semantics.DISPATCH_MODE_NAMES.items())},
        "code_rules": {
            "object": f"{CODE_OBJECT_RE.pattern}（如 training_record__c）",
            "status": f"{CODE_STATUS_RE.pattern}（如 status_reviewing__c）",
            "workflow": f"{CODE_OBJECT_RE.pattern}",
            "layoutSection": "自定义编码，建议小写 + 下划线",
        },
        "system_statuses": {
            "init": semantics.INIT_STATUS_SUFFIX,
            "complete": semantics.COMPLETE_STATUS_SUFFIX,
            "note": "启用生命周期时平台自动建这两个状态且不可删；"
                    "蓝图只能用 renameStatuses 改它们的显示名，"
                    "**不要**在 statuses 里重复定义",
        },
        "engine_pipeline": list(PIPELINE_STEPS),
        "engine_notes": [
            "对象按引用关系拓扑排序（被引用的先建），不要自己排顺序",
            "每一步都幂等：已存在的对象/选项集会跳过，重跑安全",
            "最后一步是回读验证（对象 + 字段数 + 状态数）",
            "rules / permissions / reportTemplates 是**声明区**：引擎不执行，"
            "会进人工配置清单",
        ],
        "validation_rules": [
            f"对象编码必须匹配 {CODE_OBJECT_RE.pattern}",
            "字段 dataType 必须是 " + "/".join(str(x) for x in sorted(DATA_TYPES)) + " 之一",
            "dataType=4（选项）必须提供 picklistCode",
            "dataType 在 " + "/".join(str(x) for x in sorted(REFERENCE_TYPES))
            + " 时必须提供 referenceObjectCode",
            "dataType=2 时 max 必须大于 min",
            f"状态编码必须匹配 {CODE_STATUS_RE.pattern}",
            "表单/列表布局引用的字段编码必须在本对象 fields 里存在（系统字段白名单除外）",
            "workflow.bindToStatusCode 必须出现在 lifecycle 的 statuses 或 renameStatuses 里",
        ],
        "example": MINIMAL_EXAMPLE,
    }


def blueprint_spec_json() -> str:
    """`blueprint_spec()` 的 JSON 文本（工具返回值直接用它）。"""
    return json.dumps(blueprint_spec(), ensure_ascii=False, default=str)


# ---------------------------------------------------------------- 审阅材料


def blueprint_highlights(raw: dict[str, Any]) -> dict[str, Any]:
    """蓝图的要点（**给确认门与审阅材料用的几个数字**）。

    为什么单独抽一个函数：`approvals.summarize()` 的通用口径是"描述 + 参数 + 风险级"，
    对 `factory_create` 来说那句摘要等于没说——用户要判断的是**将要建出什么**。

    ## `needsManual` 为什么必须在这里算（2026-09-20）

    它列的这几项是**引擎当前不执行**的：菜单（`orchestrate.execute_menu` 至今
    **没有任何调用方**）、规则、权限、报表模板。界面必须把它们标成"人工配置"，
    否则确认框会替引擎做出它做不到的承诺——而那是用户据以点头的唯一依据
    （AGENTS.md 规则 32：界面文案是承诺）。

    ⚠️ **这是"如实描述现状"，不是"设计如此"**。清单与判据见
    [docs/FACTORY-VERIFICATION-PLAN.md](../../../../docs/FACTORY-VERIFICATION-PLAN.md) 的 T1 / T2。
    """
    objects = to_object_blueprints(raw)
    picklists: set[str] = set()
    fields = 0
    workflows = 0
    statuses = 0
    for obj in objects:
        picklists.update(str(p.get("code") or "") for p in obj.get("picklists") or [])
        fields += len(obj.get("fields") or [])
        workflows += len(obj.get("workflows") or [])
        lifecycle = obj.get("lifecycle") or {}
        statuses += len(lifecycle.get("statuses") or []) + len(lifecycle.get("renameStatuses") or [])

    needs_manual: list[str] = []
    if raw.get("menu"):
        needs_manual.append("菜单")
    for key, label in (("rules", "规则"), ("permissions", "权限"),
                       ("reportTemplates", "报表模板")):
        if raw.get(key):
            needs_manual.append(f"{label} {len(raw[key])} 项")
    return {
        "name": str(raw.get("name") or raw.get("description") or "")[:60],
        "objectCount": len(objects),
        "objectCodes": [str(o.get("code") or "") for o in objects][:20],
        "fieldCount": fields,
        "picklistCount": len(picklists),
        "workflowCount": workflows,
        "statusCount": statuses,
        "manualItemCount": len(needs_manual),
        "needsManual": needs_manual,
        "hasMenu": bool(raw.get("menu")),
    }


def blueprint_summary_line(raw: dict[str, Any]) -> str:
    """一句话说清"这份蓝图会建出什么"（确认门摘要与审阅材料共用）。

    ⚠️ 只承诺**引擎真的会做**的事：菜单与声明区一律归到"引擎不执行"里，
    不写成"将创建菜单"。见 `blueprint_highlights` 的说明。
    """
    h = blueprint_highlights(raw)
    parts = [f"{h['objectCount']} 个对象（{'、'.join(h['objectCodes']) or '—'}）",
             f"{h['fieldCount']} 个字段"]
    if h["picklistCount"]:
        parts.append(f"{h['picklistCount']} 个选项集")
    if h["statusCount"]:
        parts.append(f"{h['statusCount']} 个状态变更")
    if h["workflowCount"]:
        parts.append(f"{h['workflowCount']} 条工作流")
    line = "将创建 " + "、".join(parts)
    if h["needsManual"]:
        line += ("；另有 " + "、".join(h["needsManual"])
                 + " **引擎不执行**，需要你人工配置")
    return line


#: 字段名 → 注解的反查表（校验失败时用来"就地补一句规格"）。
_NOTE_BY_FIELD: dict[str, str] = {}
for _key, _note in _NOTES.items():
    _NOTE_BY_FIELD.setdefault(_key.rsplit(".", 1)[-1], _note)


def hints_for_issues(issues: list[Any], *, limit: int = 6) -> dict[str, str]:
    """从校验错误里认出"它其实是在说哪个字段没填对"，附上该字段的规格说明。

    **为什么值得做**：错误信息只说"错在哪"，从不说"对的是什么"——而模型要的是后者。
    以前它得再调一次规格工具（或直接猜），现在同一份返回值里就有了。

    实现刻意做得**笨**（在错误文本里找已知字段名），因为聪明版本（解析
    `objects[a].fields[b]` 路径再映射回模型）会随着路径格式一起腐烂，
    而这里错了的后果只是"少附一句说明"，不影响校验本身。
    """
    out: dict[str, str] = {}
    for issue in issues:
        message = getattr(issue, "message", None)
        if message is None and isinstance(issue, dict):
            message = issue.get("message", "")
        for field, note in _NOTE_BY_FIELD.items():
            if field in str(message or "") and field not in out:
                out[field] = note
                if len(out) >= limit:
                    return out
    return out
