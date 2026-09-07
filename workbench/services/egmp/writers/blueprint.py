"""蓝图模型与两层校验（akso-auto validate-blueprint.js + resources/blueprint-schema.json 的 Python 化）。

Layer1：pydantic 结构校验（替代 ajv + JSON Schema draft-07）；
Layer2：业务规则（__c 后缀 / 字段去重 / dataType 条件必填 / 选项集引用 /
布局字段白名单 / 跨对象引用 / 生命周期与工作流一致性）。
错误分级：error（阻断）/ warning（提示）。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .endpoints import SYSTEM_FIELD_WHITELIST

CODE_OBJECT_RE = re.compile(r"^[a-z][a-z0-9_]*__c$")
CODE_STATUS_RE = re.compile(r"^status_[a-z][a-z0-9_]*__c$")
DATA_TYPES = {1, 2, 3, 4, 5, 7, 8, 13, 14, 15, 16, 17, 18, 23}
REFERENCE_TYPES = {13, 15, 16, 23}


class OptionDef(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    code: str | None = None
    status: int = 1
    operations: int = 1
    sort: int = 0


class PicklistDef(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    code: str
    options: list[OptionDef] = Field(min_length=1)


class FieldDef(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    code: str
    dataType: int
    isRequired: bool = False
    invisible: bool = False
    disabled: bool = False
    maxLength: int | None = Field(None, ge=1, le=17)
    format: str | None = None
    max: float | None = None
    min: float | None = None
    decimalPlaces: int | None = None
    picklistCode: str | None = None
    referenceObjectCode: str | None = None
    attachmentCount: int | None = None
    attachmentSize: int | None = None
    attachmentExtension: str | None = None
    returnType: int | None = None
    formulaScript: str | None = None
    setDefaultValue: Any = None


class FormSectionDef(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    code: str
    type: int = 1
    columnsNum: int = 2
    fields: list[str] = Field(default_factory=list)


class FormLayoutDef(BaseModel):
    model_config = ConfigDict(extra="allow")
    sections: list[FormSectionDef] = Field(min_length=1)


class ListColumnDef(BaseModel):
    model_config = ConfigDict(extra="allow")
    fieldCode: str
    sort: int


class ListLayoutDef(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str | None = None
    columns: list[ListColumnDef] = Field(min_length=1)


class StatusDef(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    code: str


class RenameStatusDef(BaseModel):
    model_config = ConfigDict(extra="allow")
    fromCode: str
    toName: str


class LifecycleDef(BaseModel):
    model_config = ConfigDict(extra="allow")
    statuses: list[StatusDef] = Field(default_factory=list)
    renameStatuses: list[RenameStatusDef] = Field(default_factory=list)


class WorkflowRuleDef(BaseModel):
    model_config = ConfigDict(extra="allow")
    decisionStep: str
    sourceStep: str
    jumpTo: str
    conditionLabel: str | None = None
    matchValue: Any = None
    targetId: str | None = None
    targetCode: str | None = None
    logicType: int | None = None
    logicValue: Any = None


class WorkflowStepDef(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    code: str
    type: Literal["task", "decision", "action"]
    participantRef: str | None = None
    nextStepCode: str | None = None
    behaviorType: int | None = None
    behaviorValue: Any = None
    dispatchMode: int | None = None


class WorkflowParticipantDef(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    code: str


class WorkflowDef(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    code: str
    autoEnable: bool = False
    bindToStatusCode: str | None = None
    participants: list[WorkflowParticipantDef] = Field(default_factory=list)
    steps: list[WorkflowStepDef] = Field(min_length=1)
    rules: list[WorkflowRuleDef] = Field(default_factory=list)


class ObjectDef(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    code: str
    enableLifeCycle: bool = False
    enableSignatures: bool = False
    source: Literal[3] = 3
    status: Literal[1] = 1
    objectClass: Literal[1] = 1
    picklists: list[PicklistDef] = Field(default_factory=list)
    fields: list[FieldDef] = Field(min_length=1)
    formLayout: FormLayoutDef | None = None
    listLayout: ListLayoutDef | None = None
    lifecycle: LifecycleDef | None = None
    workflows: list[WorkflowDef] = Field(default_factory=list)


class MenuParentDef(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    code: str


class MenuSubDef(BaseModel):
    model_config = ConfigDict(extra="allow")
    parentName: str
    name: str
    code: str


class MenuDef(BaseModel):
    model_config = ConfigDict(extra="allow")
    parent: MenuParentDef | None = None
    sub: MenuSubDef | None = None


class DeclarationRuleDef(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    type: str | None = None
    target: str | None = None
    implement: str | None = None
    manual: bool = True


class PermissionDef(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    roleCode: str | None = None
    description: str | None = None
    grants: list[str] = Field(default_factory=list)


class ReportTemplateDef(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    code: str | None = None
    description: str | None = None
    source: int | None = None
    manual: bool = True


class Blueprint(BaseModel):
    """多对象形态（单对象由 to_object_blueprints 归一化）。

    注：$schema 等元数据键由 extra=allow 容忍透传，不参与校验。
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)
    description: str | None = None
    objects: list[ObjectDef] = Field(min_length=1)
    picklists: list[PicklistDef] = Field(default_factory=list)
    rules: list[DeclarationRuleDef] = Field(default_factory=list)
    permissions: list[PermissionDef] = Field(default_factory=list)
    reportTemplates: list[ReportTemplateDef] = Field(default_factory=list)
    menu: MenuDef | None = None


class Issue(BaseModel):
    level: Literal["error", "warning"]
    path: str
    message: str


def to_object_blueprints(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """单/多对象归一化：都返回 objects 列表；顶层共享 picklists 附到引用对象。"""
    if "objects" in raw:
        blueprint = Blueprint.model_validate(raw)
        shared = blueprint.picklists
        result = []
        for obj in blueprint.objects:
            merged = obj.model_dump(by_alias=False)
            merged["picklists"] = list(shared) + merged.get("picklists", [])
            result.append(merged)
        return result
    # 单对象形态：平铺字段转 objects[0]
    single = {k: v for k, v in raw.items()
              if k not in {"$schema", "description", "rules", "permissions", "reportTemplates", "menu"}}
    obj = ObjectDef.model_validate(single)
    blueprint = Blueprint.model_validate({**raw, "objects": [obj.model_dump()]})
    return [blueprint.objects[0].model_dump()]


def validate_blueprint(raw: dict[str, Any]) -> list[Issue]:
    """两层校验。返回 issues；error 非空则阻断执行。"""
    issues: list[Issue] = []
    try:
        objects = to_object_blueprints(raw)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(x) for x in err["loc"])
            issues.append(Issue(level="error", path=loc, message=err["msg"]))
        return issues

    all_codes: set[str] = set()
    all_picklist_codes: set[str] = set()
    all_object_codes = {obj["code"] for obj in objects}

    for obj in objects:
        code = obj.get("code", "")
        path = f"objects[{code}]"
        if not CODE_OBJECT_RE.match(code):
            issues.append(Issue(level="error", path=path, message=f"对象编码 {code} 不匹配 ^[a-z][a-z0-9_]*__c$"))
        if code in all_codes:
            issues.append(Issue(level="error", path=path, message=f"对象编码重复：{code}"))
        all_codes.add(code)

        # 字段：去重 + dataType 条件必填 + 数字 max>min
        field_codes: set[str] = set()
        for field in obj.get("fields", []):
            fcode = field.get("code", "")
            fpath = f"{path}.fields[{fcode}]"
            dt = field.get("dataType")
            if dt not in DATA_TYPES:
                issues.append(Issue(level="error", path=fpath, message=f"非法 dataType：{dt}"))
            if fcode in field_codes:
                issues.append(Issue(level="error", path=fpath, message=f"字段编码重复：{fcode}"))
            field_codes.add(fcode)
            if dt == 4 and not field.get("picklistCode"):
                issues.append(Issue(level="error", path=fpath, message="选项字段（dataType=4）必须提供 picklistCode"))
                all_picklist_codes.add(field.get("picklistCode") or "")
            if dt in REFERENCE_TYPES and not field.get("referenceObjectCode"):
                issues.append(Issue(level="error", path=fpath,
                                    message="引用字段（13/15/16/23）必须提供 referenceObjectCode"))
            if dt in REFERENCE_TYPES:
                ref = field.get("referenceObjectCode")
                if ref and ref != code and ref not in all_object_codes:
                    issues.append(Issue(level="warning", path=fpath,
                                        message=f"跨对象引用 {ref} 不在本蓝图中（可能已存在于平台）"))
                all_picklist_codes.discard("")
            if dt == 2 and field.get("max") is not None and field.get("min") is not None \
                    and field["max"] <= field["min"]:
                issues.append(Issue(level="error", path=fpath, message="数字字段 max 必须大于 min"))

        # 布局引用字段必须存在（系统字段白名单）
        valid = field_codes | SYSTEM_FIELD_WHITELIST
        for section in (obj.get("formLayout") or {}).get("sections", []):
            for fcode in section.get("fields", []):
                if fcode not in valid:
                    issues.append(Issue(level="error", path=f"{path}.formLayout",
                                        message=f"表单布局引用了不存在的字段：{fcode}"))
        for column in (obj.get("listLayout") or {}).get("columns", []):
            base_code = str(column.get("fieldCode", "")).split(".")[0]
            if base_code not in valid and "." not in str(column.get("fieldCode", "")):
                issues.append(Issue(level="error", path=f"{path}.listLayout",
                                    message=f"列表布局引用了不存在的字段：{column.get('fieldCode')}"))

        # 生命周期/工作流一致性
        lifecycle = obj.get("lifecycle")
        status_codes = {s.get("code") for s in (lifecycle or {}).get("statuses", [])} | \
                       {r.get("fromCode") for r in (lifecycle or {}).get("renameStatuses", [])}
        if obj.get("enableLifeCycle") and not lifecycle:
            issues.append(Issue(level="warning", path=path,
                                message="启用生命周期但未配置 lifecycle（将只有系统保留状态）"))
        if obj.get("workflows") and not obj.get("enableLifeCycle"):
            issues.append(Issue(level="warning", path=path, message="配置了工作流但未启用生命周期"))
        for workflow in obj.get("workflows", []):
            wpath = f"{path}.workflows[{workflow.get('code')}]"
            if not CODE_OBJECT_RE.match(str(workflow.get("code", ""))):
                issues.append(Issue(level="error", path=wpath,
                                    message=f"工作流编码 {workflow.get('code')} 不匹配 __c 规范"))
            bind = workflow.get("bindToStatusCode")
            if bind and bind not in status_codes:
                issues.append(Issue(level="error", path=wpath,
                                    message=f"绑定状态 {bind} 不在 lifecycle（statuses/renameStatuses）中"))
            step_codes = {s.get("code") for s in workflow.get("steps", [])}
            for rule in workflow.get("rules", []):
                for key in ("decisionStep", "sourceStep", "jumpTo"):
                    if rule.get(key) and rule[key] not in step_codes:
                        issues.append(Issue(level="error", path=f"{wpath}.rules",
                                            message=f"规则 {key}={rule[key]} 不在步骤清单中"))

        # 选项集引用检查（warning：可能已存在于平台）
        for field in obj.get("fields", []):
            pick_code = field.get("picklistCode")
            if pick_code:
                in_scope = any(p.get("code") == pick_code for p in obj.get("picklists", [])) or \
                    any(p.get("code") == pick_code for p in raw.get("picklists", []) if isinstance(raw, dict))
                if not in_scope:
                    issues.append(Issue(level="warning", path=f"{path}.fields[{field.get('code')}]",
                                        message=f"选项集 {pick_code} 不在蓝图作用域（假定已存在于平台）"))
    return issues


def has_blocking(issues: list[Issue]) -> bool:
    return any(issue.level == "error" for issue in issues)


# ------------------------------------------------------------ 规范化

def _snake_case(name: str) -> str:
    import re as _re

    text = _re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)  # camelCase → snake_case
    text = _re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_").lower()
    return _re.sub(r"_+", "_", text)


def normalize_blueprint(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """用户蓝图规范化（akso-auto SKILL §2 规范化模式）：补 __c / snake_case / 系统字段。"""
    notes: list[str] = []
    data = dict(raw)

    def fix_code(value: str, kind: str) -> str:
        text = _snake_case(value) if not re.match(r"^[a-z][a-z0-9_]*$", value) else value
        if kind in {"object", "picklist", "workflow", "option"} and not text.endswith("__c"):
            text += "__c"
            notes.append(f"补编码后缀：{value} → {text}")
        if kind == "status":
            if not text.startswith("status_"):
                text = f"status_{text}"
            if not text.endswith("__c"):
                text += "__c"
            notes.append(f"规范化状态编码：{value} → {text}")
        return text

    objects = to_object_blueprints(data) if "objects" in data else None
    if objects is None:
        single = {k: v for k, v in data.items()
                  if k not in {"$schema", "description", "rules", "permissions", "reportTemplates", "menu"}}
        objects = [single]

    for obj in objects:
        obj["code"] = fix_code(str(obj.get("code", "")), "object")
        for field in obj.get("fields", []):
            field.setdefault("statisticsArrange", {"id": 1})
            if field.get("source") is None:
                field["source"] = 3
            if field.get("status") is None:
                field["status"] = 1
            field["code"] = _snake_case(str(field.get("code", "")))
        for picklist in obj.get("picklists", []):
            picklist["code"] = fix_code(str(picklist.get("code", "")), "picklist")
            for idx, option in enumerate(picklist.get("options", [])):
                if not option.get("code"):
                    option["code"] = f"opt_{_snake_case(str(option.get('name', '')))}_{idx}__c"
                option.setdefault("status", 1)
                option.setdefault("operations", 1)
                option.setdefault("sort", idx)
        for status in (obj.get("lifecycle") or {}).get("statuses", []):
            status["code"] = fix_code(str(status.get("code", "")), "status")
        for workflow in obj.get("workflows", []):
            workflow["code"] = fix_code(str(workflow.get("code", "")), "workflow")

    if "objects" in data:
        data["objects"] = objects
    else:
        data.update(objects[0])
    return data, notes


# ---------------------------------------------------- 审阅三件套（spec/checklist）

def generate_review_spec(raw: dict[str, Any]) -> str:
    """审阅 Spec（spec-generator.py 口径的精简忠实版）。"""
    objects = to_object_blueprints(raw)
    lines = ["# 配置蓝图审阅", ""]
    description = raw.get("description")
    if description:
        lines += [f"> {description}", ""]
    for obj in objects:
        lines.append(f"## 对象：{obj.get('name')}（{obj.get('code')}）")
        lines.append(f"- 生命周期：{'启用' if obj.get('enableLifeCycle') else '不启用'}；"
                     f"签名：{'启用' if obj.get('enableSignatures') else '不启用'}")
        lines.append("")
        if obj.get("picklists"):
            lines.append("### 选项集")
            lines.append("| 编码 | 名称 | 选项 |")
            lines.append("|---|---|---|")
            for p in obj["picklists"]:
                opts = "、".join(o.get("name") or "" for o in p.get("options", []))
                lines.append(f"| {p.get('code')} | {p.get('name')} | {opts} |")
            lines.append("")
        lines.append("### 字段")
        lines.append("| 名称 | 编码 | 类型 | 必填 | 关联/选项 |")
        lines.append("|---|---|---|---|---|")
        for f in obj.get("fields", []):
            extra = f.get("referenceObjectCode") or f.get("picklistCode") or ""
            lines.append(f"| {f.get('name')} | {f.get('code')} | {f.get('dataType')} | "
                         f"{'✓' if f.get('isRequired') else ''} | {extra} |")
        lines.append("")
        lifecycle = obj.get("lifecycle")
        if obj.get("enableLifeCycle") and lifecycle:
            lines.append("### 生命周期")
            renames = "；".join(f"{r.get('fromCode')}→{r.get('toName')}" for r in lifecycle.get("renameStatuses", []))
            statuses = "、".join(s.get("name") or "" for s in lifecycle.get("statuses", []))
            lines.append(f"- 复用重命名：{renames or '—'}")
            lines.append(f"- 新增状态：{statuses or '—'}")
            lines.append("")
        for workflow in obj.get("workflows", []):
            lines.append(f"### 工作流：{workflow.get('name')}（{workflow.get('code')}）")
            steps = " → ".join(s.get("name") or "" for s in workflow.get("steps", []))
            lines.append(f"- 步骤：{steps}")
            if workflow.get("bindToStatusCode"):
                lines.append(f"- 绑定状态：{workflow['bindToStatusCode']}（生成「提交」按钮）")
            lines.append("")
    if raw.get("menu"):
        menu = raw["menu"]
        if menu.get("parent"):
            lines.append(f"## 菜单：母菜单 {menu['parent'].get('name')}（{menu['parent'].get('code')}）")
        if menu.get("sub"):
            lines.append(f"- 子菜单 {menu['sub'].get('name')}（{menu['sub'].get('code')}）→ {menu['sub'].get('parentName')}")
        lines.append("")
    manual_items = []
    for rule in raw.get("rules", []):
        manual_items.append(f"规则：{rule.get('name')}（{rule.get('type') or 'validation'}）")
    for perm in raw.get("permissions", []):
        manual_items.append(f"权限：{perm.get('role')} → {'、'.join(perm.get('grants', []))}")
    for tpl in raw.get("reportTemplates", []):
        manual_items.append(f"报表模板：{tpl.get('name')}")
    if manual_items:
        lines.append("## 人工配置项（引擎不执行）")
        for item in manual_items:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines) + "\n"


def generate_checklist(raw: dict[str, Any]) -> str:
    """审阅清单（orchestrate._generateChecklist 口径精简版）。"""
    objects = to_object_blueprints(raw)
    lines = ["# 执行清单", "", "逐项确认后勾选：", ""]
    for obj in objects:
        code = obj.get("code")
        lines.append(f"## {obj.get('name')}（{code}）")
        for pick in obj.get("picklists", []):
            lines.append(f"- [ ] 选项集 {pick.get('name')}（{pick.get('code')}）")
        lines.append(f"- [ ] 对象创建（enableLifeCycle={obj.get('enableLifeCycle')}）")
        lines.append(f"- [ ] 字段 {len(obj.get('fields', []))} 个")
        if obj.get("formLayout"):
            lines.append("- [ ] 表单布局")
        if obj.get("listLayout"):
            lines.append("- [ ] 列表布局")
        lifecycle = obj.get("lifecycle")
        if obj.get("enableLifeCycle") and lifecycle:
            if lifecycle.get("renameStatuses"):
                lines.append(f"- [ ] 系统状态重命名 {len(lifecycle['renameStatuses'])} 个")
            if lifecycle.get("statuses"):
                lines.append(f"- [ ] 新增状态 {len(lifecycle['statuses'])} 个")
        for workflow in obj.get("workflows", []):
            lines.append(f"- [ ] 工作流 {workflow.get('name')}（{len(workflow.get('steps', []))} 步骤 + 绑定）")
        lines.append("- [ ] 验证（OpenAPI 回读）")
        lines.append("")
    return "\n".join(lines) + "\n"
