"""蜘蛛计划五步织网（akso-cc spider/step1-5 的 Python 化）。

step5 图模型用 networkx 分析（9/4 调研认定的最大潜在收益在此兑现）：
节点=对象/状态，边=归因边（按钮/进入动作/工作流步骤）+ 关联边；
来源未知/目标未匹配的边挂对象节点，绝不猜测。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from ..client import ApiError
from ..kernel.semantics import (
    BEHAVIOR_LAUNCH_WORKFLOW,
    BEHAVIOR_TYPE_NAMES,
    BEHAVIOR_UPDATE_FIELD,
    BEHAVIOR_UPDATE_RELATED_STATUS,
    BEHAVIOR_UPDATE_STATUS,
    STEP_TYPE_NAMES,
    is_complete_status,
    is_init_status,
)
from . import drawio as drawio_mod
from .lifecycle import LifecycleApi
from .report import _table  # noqa: PLC2701 —— 同包渲染工具

# 签名语义对象（电子签名类对象不参与织网的关系推导）。
# 原定义在 crawler.py，随 inventory/understand 下线一并迁到这里——它现在的唯一使用者是本模块。
SIGNATURE_RE = re.compile(r"签名|sign|esign", re.IGNORECASE)

#: **容忍**的读失败：网络/HTTP 状态/平台信封 code≠0/本地 IO。这类失败记进 `failures`
#: 并让覆盖度降级，但整次织网继续（读不到一个端点不该让产物全废）。
#:
#: ⚠ 刻意**不**容忍回放台架的缺口异常（`ReplayMiss` / `ReplayRecordedError`，都是 RuntimeError）：
#: 那是夹具覆盖度问题、或是我们自己的 bug，必须炸穿——否则「夹具少一条交互」会伪装成
#: 「平台没配」。真机上同一处的失败是 httpx/ApiError，走容忍分支。
READ_FAILURES = (httpx.HTTPError, ApiError, OSError)


class SpiderReadErrors(RuntimeError):
    """有**目标对象**读不到。

    不能吞掉：`get_object` 拿到空账（对象不存在、无权限、令牌失效）时，流程会一路走完并产出
    一个「对象节点 + 0 状态」的空网，落到 manifest 里还是 `complete` —— 那是谎报。
    用异常把「这次没读到」顶到调用方，任务状态与产物才不会互相矛盾。

    只对**目标对象**（用户点名要看的那些）生效：相关对象读不到是正常的（部分对象本就在
    织网范围外），那种失败记进 ``failures`` 即可，不该炸掉整次运行。
    """

    def __init__(self, codes: list[str]) -> None:
        self.codes = codes
        super().__init__(f"目标对象读不到实体：{', '.join(codes)}")


# ---------------------------------------------------------------- 第 1 步

def behaviour_of(config: dict[str, Any], *, relation_names: dict[str, str],
                 status_name: Callable[[str], str]) -> dict[str, Any] | None:
    """一条行为配置（`configs[]` 元素）→ 可读事实（**跨对象关联的唯一来源**）。

    实测口径（通达 2026-09-15；`EnterAction/GetById` 与 `GetWorkflowStepWithDetails` 同构）：

    - ``behaviorType=9``  → ``behaviorValue.statusId``：**本对象**改到哪个状态；
    - ``behaviorType=46`` → ``behaviorValue{relationId, relatedObjectId, targetStatusId}``：
      **修改相关对象生命周期状态**——真正有分析价值的跨对象关联
      （真机例：状态「等待风险影响评估完成」的进入动作 → 关系「变更关联的评估矩阵」→ 状态[评估中]）；
    - ``behaviorType=7``  → 发起工作流；``5`` → 更新字段值；
    - 其余如实回显 `行为类型 N`，不猜。

    > 用户定稿（2026-09-15）：**字段引用式的"关联对象"不要了**——很多根本不在生命周期里起作用。
    > 只认「生命周期/工作流里真的被改动的相关对象」，也就是这里 behaviorType=46 的那批。
    """
    try:
        behavior_type = int(config.get("behaviorType"))
    except (TypeError, ValueError):
        return None
    value = config.get("behaviorValue") or {}
    fact: dict[str, Any] = {"behaviorType": behavior_type}

    if behavior_type == BEHAVIOR_UPDATE_RELATED_STATUS:
        relation_id = str(value.get("relationId") or "")
        # 关系名形如「变更关联的评估矩阵/变更/变更」——首段即被修改的相关对象
        raw_name = relation_names.get(relation_id, "")
        related = raw_name.split("/")[0].strip() if raw_name else ""
        target_status_id = str(value.get("targetStatusId") or "")
        fact.update({
            "kind": "relatedStatus",
            "relationId": relation_id,
            "relationName": raw_name,
            "relatedObjectId": str(value.get("relatedObjectId") or ""),
            "relatedObjectName": related,
            "targetStatusId": target_status_id,
            "targetStatusName": status_name(target_status_id),
            "isSetCondition": bool(value.get("isSetCondition")),
            "text": f"修改相关对象「{related or relation_id}」状态为[{status_name(target_status_id) or '?'}]",
        })
        return fact
    if behavior_type == BEHAVIOR_UPDATE_STATUS:
        target_id = str(value.get("statusId") or "")
        fact.update({"kind": "localStatus", "targetStatusId": target_id,
                     "targetStatusName": status_name(target_id),
                     "text": f"修改状态为[{status_name(target_id) or '?'}]"})
        return fact
    if behavior_type == BEHAVIOR_LAUNCH_WORKFLOW:
        fact.update({"kind": "workflow", "text": "发起工作流"})
        return fact
    if behavior_type == BEHAVIOR_UPDATE_FIELD:
        fact.update({"kind": "field", "text": "更新字段值"})
        return fact
    # 词典外的类型：**不编名字**（描述文本里平台自己会说），只留类型号供排障
    fact.update({"kind": "other", "behaviorTypeName": "", "text": ""})
    return fact


def behaviours_from(configs: Any, *, relation_names: dict[str, str],
                    status_name: Callable[[str], str]) -> list[dict[str, Any]]:
    """一组 `configs` → 行为事实列表（保序，便于与平台描述对齐）。"""
    facts: list[dict[str, Any]] = []
    for config in configs or []:
        if not isinstance(config, dict):
            continue
        fact = behaviour_of(config, relation_names=relation_names, status_name=status_name)
        if fact:
            facts.append(fact)
    return facts


def action_kinds(behavior_types: list[int]) -> str:
    """动作类型注解（`behaviorTypes` → 中文名；空则空串，不编）。"""
    names = [BEHAVIOR_TYPE_NAMES.get(int(bt), f"类型{bt}") for bt in behavior_types or []]
    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return "、".join(seen)


# ---------------------------------------------------------------- 第 2 步

CONDITION_RE = re.compile(r"^(?P<cond>[^，,]*[=＝][^，,]*)(?:[，,](?P<rest>.*))?$")
TARGET_STATUS_RE = re.compile(r"(?:修改状态为|提交到状态)\s*[［\[“‘\"']([^］\]”\"']+)[］\]”\"']")


def condition_part(description: str) -> str:
    """从平台生成的动作描述里取**条件片段**（形如「行动计划制定时间=变更申请时&…」）。

    取不到条件就返回空串——宁可空着，也不把动作名当成条件（这正是上一轮列错的原因之一）。
    """
    match = CONDITION_RE.match(str(description or "").strip())
    if not match:
        return ""
    cond = (match.group("cond") or "").strip()
    return cond if ("=" in cond or "＝" in cond) else ""


def target_status_of(description: str) -> str:
    """从动作描述里取目标状态名（平台生成文本：「提交到状态"风险评估"」「修改状态为[X]」）。"""
    match = TARGET_STATUS_RE.search(str(description or ""))
    return match.group(1).strip() if match else ""


#: 条件明细的运算符（平台 `logicType`；只收录实测见过的，其余如实回显类型号）
LOGIC_TYPE_TEXT = {1: "等于", 2: "不等于", 10: "不为空", 11: "为空", 20: "属于", 21: "不属于", 60: "满足"}


def _logic_value_text(raw: Any, status_name: Callable[[str], str]) -> str:
    """条件右值 → 可读文本（GUID 尽量解析成状态名；解析不了就原样截断）。"""
    if raw is None or raw == "" or raw == []:
        return ""
    values: list[Any]
    if isinstance(raw, str):
        text = raw.strip()
        try:
            parsed = json.loads(text)
            values = parsed if isinstance(parsed, list) else [parsed]
        except ValueError:
            values = [text]
    elif isinstance(raw, (list, tuple)):
        values = list(raw)
    else:
        values = [raw]
    out: list[str] = []
    for value in values:
        text = str(value)
        resolved = status_name(text) if len(text) == 36 and text.count("-") == 4 else ""
        out.append(resolved or (text if len(text) <= 24 else text[:24] + "…"))
    return "、".join(out)


def render_condition(condition: Any, status_name: Callable[[str], str], *, depth: int = 0) -> str:
    """条件对象 → 一行可读文本（递归展开 logicType=60 的嵌套条件）。

    平台把条件存成 `{details:[{targetCode, logicType, logicValue, condition}]}`；
    直接 json.dumps 进表格是没法看的（第一版就是这么出的洋相）。
    """
    if not isinstance(condition, dict) or depth > 3:
        return ""
    parts: list[str] = []
    for detail in condition.get("details") or []:
        if not isinstance(detail, dict):
            continue
        field = str(detail.get("targetCode") or "").strip() or "字段"
        logic_type = detail.get("logicType")
        if int(logic_type or 0) == 60 and isinstance(detail.get("condition"), dict):
            nested = render_condition(detail["condition"], status_name, depth=depth + 1)
            if nested:
                parts.append(f"({nested})")
            continue
        operator = LOGIC_TYPE_TEXT.get(int(logic_type or 0), f"运算符{logic_type}")
        value = _logic_value_text(detail.get("logicValue"), status_name)
        if value:
            parts.append(f"{field} {operator} {value}")
        else:
            parts.append(f"{field} {operator}")
    relations = condition.get("relations") or []
    operator_code = int((relations[0] or {}).get("logicalOperator") or 1) if relations else 1
    joiner = {1: " 且 ", 2: " 或 "}.get(operator_code, " 且 ")
    text = joiner.join(parts)
    return text[:300] + ("…" if len(text) > 300 else "")


def _cell(text: str, limit: int = 600) -> str:
    """单元格文本截断：宁可截断并标明，也不让一张表被单个长条件撑爆。"""
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…（已截断）"


def compose_status_cells(statuses: list[dict[str, Any]],
                         per_status: dict[str, dict[str, Any]],
                         status_name: Callable[[str], str] | None = None) -> list[dict[str, str]]:
    """状态 + 采集到的事实 → 「进入动作 / 用户动作 / 进入条件」三格（列口径写死为契约）。

    逐列来源（2026-09-15 真机重校，都可追溯）：

    - **用户动作** ← ``UserAction/GetListByStatusId`` 的 `name`（附 `code`）+ 类型注解
      （类型来自 ``GetUserAction.behaviorTypes``）+ 平台生成的描述原文；
    - **进入动作** ← ``EnterAction/GetListByStatusId`` 的 `description`，再用
      ``EnterAction/GetById`` 的 `configs[]` 补出**目标对象/目标状态**；
    - **进入条件** ← 平台**没有**独立的条件端点（`EnterCondition`/`EnterRule`/`Condition`/
      `EntryCondition` 四条候选实测全 404），故取：①进入动作详情的 `conditionStr`/`condition`；
      ②指向本状态的用户动作描述里「，」前的条件片段。都没有就写「平台未配置」——**不编**。
    """
    cells: list[dict[str, str]] = []
    facts_status_name = status_name or (lambda value: "")
    for status in statuses:
        facts = per_status.get(str(status.get("id"))) or {}
        user_actions = facts.get("userActions") or []
        enter_actions = facts.get("enterActions") or []

        user_cells: list[str] = []
        for item in user_actions:
            name = str(item.get("name") or item.get("code") or "")
            code = str(item.get("code") or "")
            kinds = action_kinds(item.get("behaviorTypes") or [])
            description = str(item.get("description") or "").strip()
            bits = [f"{name}（{code}）" if code else name]
            if kinds:
                bits.append(f"· {kinds}")
            if description and description != name:
                bits.append(f"· {description}")
            user_cells.append(" ".join(bits))

        enter_cells: list[str] = []
        for item in enter_actions:
            merged = str(item.get("description") or "").strip() or "（平台未给描述）"
            extra = [f.get("text") for f in (item.get("behaviours") or [])
                     if f.get("text") and f.get("text") not in merged]
            if extra:
                merged = f"{merged} ← {'；'.join(extra)}"
            if item.get("isEnabled") is False:
                merged += "（已停用）"
            enter_cells.append(merged)

        conditions: list[str] = []
        for item in enter_actions:
            for key in ("conditionStr", "condition"):
                raw = item.get(key)
                if isinstance(raw, dict):
                    text = render_condition(raw, facts_status_name)
                else:
                    text = str(raw or "").strip()
                if text:
                    conditions.append(text)
        for item in user_actions:
            condition = condition_part(str(item.get("description") or ""))
            if condition:
                conditions.append(condition)

        cells.append({
            "statusName": str(status.get("name") or ""),
            "enterCondition": _cell("；".join(dict.fromkeys(conditions))) or "平台未配置",
            "enterAction": _cell("；".join(enter_cells)) or "平台未配置",
            "userAction": _cell("；".join(user_cells)) or "平台未配置",
        })
    return cells


# ---------------------------------------------------------------- 第 3 步

def attribute_status_changes(statuses: list[dict[str, Any]],
                             per_status: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """状态变化归因：进入动作 / 用户动作 / 工作流步骤 三类证据链（+ 不可入边的平台事实）。

    每条边都带**证据**，且分「精确」与「描述推断」两档：

    - **精确**：进入动作详情、工作流步骤详情里的 `configs[]` → `behaviorType` +
      `behaviorValue`（本对象目标状态 / 相关对象 + 目标状态直接给 id）；
    - **描述推断**：用户动作只有 `description`（平台生成文本，如「提交到状态"风险评估"」），
      目标状态靠 :func:`target_status_of` 解析——证据字段会写明这是"描述解析"。

    `behaviorType=46`（改相关对象生命周期状态）**同时**是归因边与关系边：它进本表，
    也进第 1 步的关联对象表与第 5 步的对象网络。
    """
    rows: list[dict[str, Any]] = []
    unverified_notes: list[str] = []

    for status in statuses:
        sid = str(status.get("id"))
        status_name = str(status.get("name") or "")
        facts = per_status.get(sid) or {}

        for item in facts.get("enterActions") or []:
            carrier = str(item.get("description") or "进入动作")
            for fact in item.get("behaviours") or []:
                kind = fact.get("kind")
                if kind == "localStatus":
                    rows.append({"source": status_name, "carrier": carrier, "trigger": "enter",
                                 "mechanism": "enterAction", "from": status_name,
                                 "to": fact.get("targetStatusName") or "?",
                                 "targetId": fact.get("targetStatusId") or "",
                                 "evidence": f"进入动作详情 behaviorType=9（{carrier}）"})
                elif kind == "relatedStatus":
                    rows.append({"source": status_name, "carrier": carrier, "trigger": "enter",
                                 "mechanism": "enterRelatedStatus", "from": status_name,
                                 "to": f"{fact.get('relatedObjectName') or '?'}"
                                       f"→{fact.get('targetStatusName') or '?'}",
                                 "targetId": fact.get("targetStatusId") or "",
                                 "relationId": fact.get("relationId") or "",
                                 "relatedObjectName": fact.get("relatedObjectName") or "",
                                 "evidence": f"进入动作详情 behaviorType=46（{carrier}）"})
                elif kind == "field":
                    unverified_notes.append(
                        f"{status_name}：进入动作「{carrier}」是更新字段值（behaviorType=5），不作状态边")

        for item in facts.get("userActions") or []:
            name = str(item.get("name") or item.get("code") or "")
            kinds = [int(x) for x in (item.get("behaviorTypes") or [])]
            description = str(item.get("description") or "")
            target = target_status_of(description)
            if target:
                rows.append({"source": status_name, "carrier": name, "trigger": "user",
                             "mechanism": "workflowStep" if BEHAVIOR_LAUNCH_WORKFLOW in kinds else "button",
                             "from": status_name, "to": target, "targetId": "",
                             "evidence": f"用户动作描述解析：{description[:60]}"})
            elif kinds:
                unverified_notes.append(
                    f"{status_name}：用户动作「{name}」类型为"
                    f"{action_kinds(kinds) or '未知'}，但描述里没有可解析的目标状态 → 不入边"
                    f"（{description[:40] or '无描述'}）")

        for entry in facts.get("workflowBehaviours") or []:
            wf_name = str(entry.get("workflowName") or "")
            step_name = str(entry.get("stepName") or "")
            carrier = f"工作流「{wf_name}」步骤「{step_name}」"
            for fact in entry.get("behaviours") or []:
                if fact.get("kind") == "localStatus":
                    rows.append({"source": status_name, "carrier": carrier, "trigger": "workflow",
                                 "mechanism": "workflowStep", "from": status_name,
                                 "to": fact.get("targetStatusName") or "?",
                                 "targetId": fact.get("targetStatusId") or "",
                                 "evidence": "工作流步骤详情 behaviorType=9"})
                elif fact.get("kind") == "relatedStatus":
                    rows.append({"source": status_name, "carrier": carrier, "trigger": "workflow",
                                 "mechanism": "workflowRelatedStatus", "from": status_name,
                                 "to": f"{fact.get('relatedObjectName') or '?'}"
                                       f"→{fact.get('targetStatusName') or '?'}",
                                 "targetId": fact.get("targetStatusId") or "",
                                 "relationId": fact.get("relationId") or "",
                                 "relatedObjectName": fact.get("relatedObjectName") or "",
                                 "evidence": "工作流步骤详情 behaviorType=46"})

        detail = (facts.get("statusDetail") or {})
        cancel_id = detail.get("workflowCancelStatus")
        if cancel_id:
            unverified_notes.append(
                f"{status_name}：workflowCancelStatus={cancel_id} 为平台全局配置 id，不作边（实证结论）"
            )
    return {"rows": rows, "unverifiedNotes": unverified_notes}


# ---------------------------------------------------------------- 第 4 步

def step_type_name(step_type: Any) -> str:
    """stepType → 中文名（词典缺失时如实回显原始值，不静默留空）。"""
    try:
        return STEP_TYPE_NAMES.get(int(step_type), f"stepType={step_type}")
    except (TypeError, ValueError):
        return f"stepType={step_type}"


def build_workflow_graph(steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
    """工作流步骤行 → drawio 图模型（节点=步骤，边=nextStepList）。

    平台契约（2026-09-14 真机 `the_change_lh__c` 实测）：``GetWorkflowSteps`` 每行带
    ``id/name/stepType/sort/nextStepId/nextStepList[{id,name}]`` —— **分支就在 nextStepList**
    （判断节点会有多条），所以流程图只靠这一次调用就能画完整。
    逐步骤的 ``GetWorkflowStepWithDetails``（审批人/字段/会签或签）属后续增强，
    本版**不调用**：保持请求面最小，也让离线夹具的覆盖度可控。
    """
    nodes = [
        {
            "id": str(step.get("id")),
            "name": str(step.get("name") or ""),
            "stepType": step.get("stepType"),
            "behaviors": [{"summary": step_type_name(step.get("stepType"))}],
        }
        for step in steps if step.get("id")
    ]
    known = {node["id"] for node in nodes}
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for step in steps:
        source = str(step.get("id") or "")
        if source not in known:
            continue
        targets = [str(item.get("id")) for item in (step.get("nextStepList") or []) if item.get("id")]
        if not targets and step.get("nextStepId"):
            targets = [str(step["nextStepId"])]
        for target in targets:
            if target in known and (source, target) not in seen:
                seen.add((source, target))
                edges.append({"source": source, "target": target, "label": ""})
    return {"name": name or "未命名工作流", "nodes": nodes, "edges": edges,
            "stepCount": len(nodes)}


# ---------------------------------------------------------------- 第 5 步

def build_network_model(objects: list[dict[str, Any]], attributions: list[dict[str, Any]],
                        relations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """对象节点 + 状态节点 + 归因边 + 关联边（未知来源/目标挂对象节点）。

    关联边的目标节点按 **编码 → 名称 → 标识** 依次命中已有对象节点；都命中不到时
    建一个 `stub` 占位节点（平台对这种目标常常只给 id + 名称），并记进
    `unexpandedRelations` —— 边界外显，不静默丢边。
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    by_code: dict[str, str] = {}
    by_label: dict[str, str] = {}
    unexpanded: list[dict[str, Any]] = []

    for obj in objects:
        code = obj["code"]
        name = str(obj.get("name") or code)
        node_id = f"obj:{code}"
        nodes.append({"id": node_id, "kind": "object", "label": name, "code": code})
        by_code.setdefault(code, node_id)
        by_label.setdefault(name, node_id)
        for status in obj.get("statuses") or []:
            status_name = str(status.get("name") or status.get("code"))
            kind = "init" if is_init_status(str(status.get("code") or "")) else (
                "complete" if is_complete_status(str(status.get("code") or "")) else "state")
            nodes.append({"id": f"st:{code}:{status.get('id')}", "kind": kind, "label": status_name,
                          "object": code})

    for obj in objects:
        code = obj["code"]
        source_id = f"obj:{code}"
        for related in (relations.get(code, {}).get("related") or []):
            key = str(related.get("key") or related.get("code") or related.get("name") or "")
            label = str(related.get("name") or related.get("code") or key)
            target_id = (
                by_code.get(str(related.get("code") or ""))
                or by_label.get(str(related.get("name") or ""))
                or by_code.get(key)
                or by_label.get(key)
            )
            if target_id is None:
                target_id = f"obj:{key}"
                if not any(node["id"] == target_id for node in nodes):
                    nodes.append({"id": target_id, "kind": "object", "label": label,
                                  "code": str(related.get("code") or ""), "stub": True,
                                  "related": True})
                    by_label.setdefault(label, target_id)
                unexpanded.append({"id": target_id, "label": label,
                                   "via": "、".join(related.get("via") or [])})
            # 边标签带上**目标状态**：这才是「两者相关联且有作用」的证据
            target_states = "、".join(related.get("targetStatusNames") or [])
            edge_label = str(related.get("relationName") or related.get("name") or "")
            if target_states:
                edge_label = f"{edge_label} → [{target_states}]" if edge_label else f"→ [{target_states}]"
            edges.append({"kind": "relation", "source": source_id, "target": target_id,
                          "label": edge_label or "、".join(related.get("via") or [])})

    for attr in attributions:
        if attr.get("mechanism") in {"enterRelatedStatus", "workflowRelatedStatus"}:
            # 跨对象边已由上面的 relation 边表达（step1）；在这里再画一次会变成
            # 「本对象 → 本对象」的假自环，反而误导。
            continue
        source_status = attr.get("from")
        target = str(attr.get("to") or "")
        source_node = _find_status_node(nodes, source_status)
        # 目标解析三级：状态节点 → 已爬对象节点 → 兜底挂到「来源状态所属对象」。
        # 修复前的写法 `_find(...) or f"obj:{target}" if target else None` 因运算符优先级
        # 解析为 `(_find(...) or f"obj:{target}") if target else None`，会为工作流类目标
        # （`to` = 工作流名）造出 `obj:工作流「X」` 这种**永不存在的节点 id** → drawio 悬空引用。
        target_node = _find_status_node(nodes, target) if target else None
        if target_node is None and target:
            candidate = f"obj:{target}"
            if any(n["id"] == candidate for n in nodes):
                target_node = candidate
        if target_node is None:
            target_node = _object_anchor_of(nodes, source_node)
        edge: dict[str, Any] = {
            "kind": f"attr:{attr.get('mechanism')}", "source": source_node,
            "target": target_node, "label": attr.get("carrier"), "evidence": attr.get("evidence"),
        }
        if source_node is None or target_node is None:
            # 两端都定不了位：显式标记 unresolved（产物里可见），drawio 侧不画悬空边
            edge["unresolved"] = True
            edge["rawTarget"] = target
        edges.append(edge)

    stats = {
        "objectNodes": sum(1 for n in nodes if n["kind"] == "object"),
        "stateNodes": sum(1 for n in nodes if n["kind"] in {"state", "init", "complete"}),
        "attrEdges": sum(1 for e in edges if e["kind"].startswith("attr:")),
        "relationEdges": sum(1 for e in edges if e["kind"] == "relation"),
    }
    model: dict[str, Any] = {"nodes": nodes, "edges": edges, "stats": stats}
    if unexpanded:
        model["unexpandedRelations"] = unexpanded
    return model


def _object_anchor_of(nodes: list[dict[str, Any]], state_node_id: Any) -> str | None:
    """状态节点 id（``st:<code>:<statusId>``）→ 其所属对象节点 id（若该对象在图中）。"""
    text = str(state_node_id or "")
    if not text.startswith("st:"):
        return None
    code = text[len("st:"):].rsplit(":", 1)[0]
    candidate = f"obj:{code}"
    return candidate if any(n["id"] == candidate for n in nodes) else None


def _find_status_node(nodes: list[dict[str, Any]], status_name: Any) -> str | None:
    for node in nodes:
        if node["kind"] in {"state", "init", "complete"} and node["label"] == str(status_name or ""):
            return str(node["id"])
    return None


def analyze_network(model: dict[str, Any]) -> dict[str, Any]:
    """networkx 分析：关键节点（度中心性）、孤立状态、最长归因链。"""
    import networkx as nx

    graph = nx.DiGraph()
    for node in model["nodes"]:
        graph.add_node(node["id"], **{k: v for k, v in node.items() if k != "id"})
    for edge in model["edges"]:
        if graph.has_node(edge["source"]) and graph.has_node(edge["target"]):
            graph.add_edge(edge["source"], edge["target"], kind=edge["kind"], label=edge.get("label"))
    centrality = nx.degree_centrality(graph)
    top = sorted(centrality.items(), key=lambda kv: kv[1], reverse=True)[:10]
    label_of = {n["id"]: n["label"] for n in model["nodes"]}
    orphans = [node["id"] for node in model["nodes"]
               if graph.degree(node["id"]) == 0]
    return {
        "hubs": [{"id": node_id, "label": label_of.get(node_id, node_id), "centrality": round(score, 3)}
                 for node_id, score in top],
        "orphans": [label_of.get(node_id, node_id) for node_id in orphans],
        "isDag": nx.is_directed_acyclic_graph(graph),
    }


# ---------------------------------------------------------------- 编排

def run_spider(client: Any, codes: list[str]) -> dict[str, Any]:
    """五步织网（单次调用跑完 codes 的 step1-5 模型，产物由调用方渲染落盘）。

    目标对象读不到实体即抛 :class:`SpiderReadErrors`——宁可这次没有产物，也不要一张假网
    （详见该类文档）。

    **读失败一律进 `failures`（带 stage）**，不再静默换成空列表：否则「平台没配」与
    「我们没读到」在产物里长得一模一样（都是空表），而 coverage 还写着 complete ——
    2026-09-14 实测就是这种形态：`except Exception: rows = []` 把私有端点的失败全吞了。
    `notes` 记的是**能力边界**（未启用生命周期、平台没给编码所以没展开、对象本来就没配动作），
    与 `failures`（我们没读到）分开，两者都进产物。
    """
    api = LifecycleApi(client)
    result: dict[str, Any] = {"objects": [], "perObject": {}, "failures": [], "notes": []}
    crawled: list[dict[str, Any]] = []
    all_attrs: list[dict[str, Any]] = []
    unreadable: list[str] = []

    def fail(code: str, stage: str, exc: Any) -> None:
        result["failures"].append({"code": code, "stage": stage, "error": str(exc)[:300]})

    def note(text: str) -> None:
        if text not in result["notes"]:
            result["notes"].append(text)

    for code in codes:
        meta = client.get_object(code) or {}
        if not meta.get("id"):
            unreadable.append(code)
            result["failures"].append({"code": code, "stage": "step1", "error": "对象实体未读到"})
            continue
        object_id = str(meta.get("id"))

        try:
            statuses = client.get_object_statuses(code) or [] if meta.get("enableLifeCycle") else []
        except READ_FAILURES as exc:
            fail(code, "step1-statuses", exc)
            statuses = []
        lifecycle_ids = {str(s.get("lifecycleId") or "") for s in statuses if s.get("lifecycleId")}
        status_by_id = {str(s.get("id")): s for s in statuses}

        # —— 对象级素材（各一次）：关系对象名、动作类型、归属本对象的工作流 ——
        try:
            relation_names = api.relation_targets(object_id)
        except READ_FAILURES as exc:
            fail(code, "step1-relationTargets", exc)
            relation_names = {}
        try:
            kind_by_action = api.action_behavior_types(object_id)
        except READ_FAILURES as exc:
            fail(code, "step2-actionTypes", exc)
            kind_by_action = {}
        try:
            workflows = client.list_workflows() or []
        except READ_FAILURES as exc:
            fail(code, "step4-workflowList", exc)
            workflows = []
        # ⚠ 归属判定**不能看 objectCode**：真机该字段恒为 null（这就是上一版"0 条工作流"的根因）
        my_workflows = [w for w in workflows
                        if str(w.get("objectId") or "") == object_id
                        or str(w.get("lifecycleId") or "") in lifecycle_ids]

        if meta.get("enableLifeCycle") and not statuses:
            note(f"{code}：生命周期状态读不到或为空 → 第 2/3 步无内容可织")
        if not meta.get("enableLifeCycle"):
            note(f"{code}：未启用生命周期（enableLifeCycle=false）→ 无状态与流转可织")
        if meta.get("enableLifeCycle") and not my_workflows:
            note(f"{code}：本对象/本生命周期下没有工作流 → 第 4 步无流程图可画")

        # —— 工作流步骤里的行为（精准的跨对象证据）→ 归到触发状态名下 ——
        wf_behaviours = collect_workflow_behaviours(api, my_workflows, relation_names,
                                                   api.status_name, fail, note)

        per_status: dict[str, dict[str, Any]] = {}
        for status in statuses:
            status_id = str(status.get("id"))
            entry: dict[str, Any] = {"userActions": [], "enterActions": [],
                                     "workflowBehaviours": [], "statusDetail": {}}
            try:
                for item in api.user_actions_by_status(status_id):
                    item = dict(item)
                    item["behaviorTypes"] = kind_by_action.get(str(item.get("code") or ""), [])
                    entry["userActions"].append(item)
            except READ_FAILURES as exc:
                fail(code, f"step2-userActions:{status.get('name')}", exc)
            try:
                for item in api.enter_actions(status_id):
                    item = dict(item)
                    detail = {}
                    if item.get("id"):
                        try:
                            detail = api.enter_action_detail(str(item["id"]))
                        except READ_FAILURES as exc:
                            fail(code, f"step2-enterActionDetail:{status.get('name')}", exc)
                    item["conditionStr"] = detail.get("conditionStr")
                    item["condition"] = detail.get("condition")
                    item["behaviours"] = behaviours_from(detail.get("configs"), relation_names=relation_names,
                                                        status_name=api.status_name)
                    entry["enterActions"].append(item)
            except READ_FAILURES as exc:
                fail(code, f"step2-enterActions:{status.get('name')}", exc)
            try:
                entry["statusDetail"] = api.status_detail(status_id)
            except READ_FAILURES as exc:
                fail(code, f"step3-statusDetail:{status.get('name')}", exc)
            triggered = [b for wf in my_workflows for b in wf_behaviours.get(str(wf.get("id")), [])
                         if status_id in b["triggerStatusIds"]]
            entry["workflowBehaviours"] = triggered
            if not entry["userActions"] and not entry["enterActions"]:
                note(f"{code}/{status.get('name')}：平台未配置用户动作与进入动作")
            per_status[status_id] = entry

        cells = compose_status_cells(statuses, per_status, api.status_name)
        attrs = attribute_status_changes(statuses, per_status)
        all_attrs.extend(attrs["rows"])

        relations = relations_from_facts(per_status)
        result["perObject"][code] = {
            "step1": relations,
            "step2Cells": cells,
            "step3": attrs,
            "statuses": statuses,
            "workflows": my_workflows,
        }
        result["objects"].append({"code": code, "name": meta.get("name"), "statuses": statuses})
        crawled.append({"code": code, "name": meta.get("name"), "meta": meta,
                        "statuses": statuses, "fields": []})
        result.setdefault("workflowGraphs", []).extend(
            build_graphs(api, my_workflows, status_by_id, fail, note))

    relations_map = {obj["code"]: (result["perObject"].get(obj["code"]) or {}).get("step1")
                     or {"related": [], "excluded": []} for obj in crawled}
    model = build_network_model(crawled, all_attrs, relations_map)
    model["analysis"] = analyze_network(model)
    result["model"] = model
    if unreadable:
        raise SpiderReadErrors(unreadable)
    return result


def workflow_trigger_status_ids(row: dict[str, Any]) -> list[str]:
    """工作流触发状态 id 列表。

    ⚠ 真机 `lifecycleStatusIds` 是**逗号分隔的字符串**（不是数组，`lifeStatusName` 另给单名）；
    离线夹具里两种形态都出现过，所以两种都得吃。
    """
    raw = row.get("lifecycleStatusIds")
    if isinstance(raw, str):
        return [x.strip() for x in raw.split(",") if x.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def collect_workflow_behaviours(api: LifecycleApi, workflows: list[dict[str, Any]],
                                relation_names: dict[str, str],
                                status_name: Callable[[str], str],
                                fail: Any, note: Any) -> dict[str, list[dict[str, Any]]]:
    """逐工作流读步骤详情，取出 `stepAction.configs[]` 里的行为（跨对象修改就在这里）。

    只对 **stepType=90（动作）** 的步骤取详情：真机实测跨对象行为只挂在这类步骤上，
    全量取详情会把请求数翻好几倍（15 个工作流 × 10 步）。
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for row in workflows:
        row_id = str(row.get("id") or "")
        if not row_id:
            continue
        try:
            steps = api.workflow_steps(row_id) or []
        except READ_FAILURES as exc:
            fail(str(row.get("name") or row_id), "step4-workflowSteps", exc)
            continue
        triggers = workflow_trigger_status_ids(row)
        collected: list[dict[str, Any]] = []
        for step in steps:
            if int(step.get("stepType") or 0) != 90:
                continue
            try:
                detail = api.workflow_step_detail(str(step.get("id")))
            except READ_FAILURES as exc:
                fail(str(row.get("name") or row_id), "step4-stepDetail", exc)
                continue
            configs = [cfg for action in ((detail.get("stepAction") or {}).get("actions") or [])
                       for cfg in (action.get("configs") or [])]
            facts = behaviours_from(configs, relation_names=relation_names, status_name=status_name)
            if facts:
                collected.append({
                    "workflowName": str(row.get("name") or row_id),
                    "workflowRowId": row_id,
                    "stepName": str(step.get("name") or ""),
                    "triggerStatusIds": triggers,
                    "behaviours": facts,
                })
        out[row_id] = collected
    return out


def relations_from_facts(per_status: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """从行为事实里抽出**跨对象关联**（用户定稿：只认生命周期/工作流里真的被改动的相关对象）。

    标识用**对象名称**（平台对这类目标只给 relationId + relatedObjectId + 目标状态 id，
    名字来自 `GetFieldsAndRelationsList` 的关系名首段，例如「变更关联的评估矩阵/变更/变更」）。
    """
    targets: dict[str, dict[str, Any]] = {}
    for _status_id, entry in per_status.items():
        status_label = str((entry.get("statusDetail") or {}).get("name") or "")
        sources: list[tuple[str, dict[str, Any]]] = [
            (f"进入动作「{item.get('description') or '未命名'}」", fact)
            for item in entry.get("enterActions") or []
            for fact in item.get("behaviours") or []
        ]
        sources += [
            (f"工作流「{item.get('workflowName')}」步骤「{item.get('stepName')}」", fact)
            for item in entry.get("workflowBehaviours") or []
            for fact in item.get("behaviours") or []
        ]
        for where, fact in sources:
            if fact.get("kind") != "relatedStatus":
                continue
            key = str(fact.get("relatedObjectName") or fact.get("relationId") or "")
            if not key:
                continue
            record = targets.setdefault(key, {
                "key": key,
                "name": fact.get("relatedObjectName") or key,
                "code": "",
                "id": str(fact.get("relatedObjectId") or ""),
                "relationName": fact.get("relationName") or "",
                "targetStatusNames": [],
                "statuses": [],
                "via": [],
                "selfLoop": False,
                "systemField": False,
            })
            record["via"].append(f"{where} → [{fact.get('targetStatusName') or '?'}]"
                                 + (f"（状态「{status_label}」）" if status_label else ""))
            name = str(fact.get("targetStatusName") or "")
            if name and name not in record["targetStatusNames"]:
                record["targetStatusNames"].append(name)
            if status_label and status_label not in record["statuses"]:
                record["statuses"].append(status_label)
    return {"related": list(targets.values()), "excluded": [], "selfLoop": False, "unexpandable": []}


def build_graphs(api: LifecycleApi, workflows: list[dict[str, Any]],
                 status_by_id: dict[str, dict[str, Any]], fail: Any, note: Any) -> list[dict[str, Any]]:
    """第 4 步的取数：本对象/本生命周期的工作流 → 步骤编排图（含分支与触发状态）。"""
    graphs: list[dict[str, Any]] = []
    for row in workflows:
        row_id = str(row.get("id") or "")
        name = str(row.get("name") or row_id)
        try:
            steps = api.workflow_steps(row_id) or []
        except READ_FAILURES as exc:  # 单个工作流读不到不该炸整次织网
            fail(name, "step4-workflowSteps", exc)
            continue
        if not steps:
            note(f"工作流「{name}」读不到步骤 → 流程图缺这一页")
            continue
        triggers = [str(status_by_id.get(sid, {}).get("name") or sid)
                    for sid in workflow_trigger_status_ids(row)]
        if not triggers and row.get("lifeStatusName"):
            triggers = [str(row["lifeStatusName"])]
        graph = build_workflow_graph(steps, name)
        graph["origin"] = ("状态触发：" + "、".join(triggers)) if triggers else "未绑定触发状态"
        graph["workflowRowId"] = row_id
        graph["workflowCode"] = str(row.get("code") or "")
        graph["isEnable"] = bool(row.get("isEnable"))
        graphs.append(graph)
    return graphs


def _fenced(text: str) -> str:
    return text if text.strip() else "—"


def _write_step1_md(spider: dict[str, Any], output_dir: Path) -> Path:
    """第 1 步产物：关联对象表。

    > 口径（用户定稿 2026-09-15）：**不再用字段引用推关联**——很多字段级引用根本不在生命周期里
    > 起作用。只认「生命周期/工作流里真的被改动的相关对象」，即行为
    > `behaviorType=46`（修改相关对象生命周期状态）里的 `relatedObjectId` + `targetStatusId`；
    > 关系名取自 `GetFieldsAndRelationsList`（如「变更关联的评估矩阵/变更/变更」），
    > **以对象名称作标识**。
    """
    lines = ["# 蜘蛛计划 · 第 1 步：关联对象（生命周期/工作流里真正起作用的相关对象）", "",
             "> 来源：进入动作详情 + 工作流步骤详情里的 `behaviorType=46` 行为。",
             "> 每条都给出来源（哪个进入动作 / 哪个工作流的哪一步）与**被改成的目标状态**。", ""]
    any_related = False
    for code, per in (spider.get("perObject") or {}).items():
        step1 = per.get("step1") or {}
        related = step1.get("related") or []
        if not related:
            continue
        any_related = True
        lines.append(f"## {code}")
        lines.append("")
        rows = [
            [code, entry.get("name") or entry.get("key"),
             entry.get("relationName") or "",
             "、".join(entry.get("targetStatusNames") or []) or "—",
             "；".join(entry.get("via") or [])]
            for entry in related
        ]
        lines.append(_table(["来源对象", "相关对象", "经由关系", "被改成的状态", "来源（动作/工作流步骤）"], rows))
        lines.append("")
    if not any_related:
        lines.append("本次没有取到跨对象行为（`behaviorType=46`）——即该对象在生命周期与工作流里"
                     "**没有改动任何相关对象的状态**。这是平台配置事实，不是读失败"
                     "（读失败会出现在 manifest 的 `failures` 里）。")
        lines.append("")
    path = output_dir / "step1-relations.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_step2_md(spider: dict[str, Any], output_dir: Path) -> Path:
    """第 2 步产物：状态明细表（规格 C2 的**写死列口径**）。"""
    lines = ["# 蜘蛛计划 · 第 2 步：生命周期状态明细", "",
             "> 列口径（规格 §2.1 C2，写死为契约）：状态名称 / 进入动作 / 用户动作 / 进入条件。",
             "> 来源（2026-09-15 真机重校）：用户动作 ← `UserAction/GetListByStatusId`（带动作 code）"
             "+ `GetUserAction.behaviorTypes` 的类型注解；进入动作 ← `EnterAction/GetListByStatusId`"
             "+ `EnterAction/GetById` 的目标对象/状态；进入条件 ← 平台把条件写在动作描述与行为值里"
             "（**没有独立的条件端点**，四条候选实测全 404），取不到就写「平台未配置」。", ""]
    any_cells = False
    for code, per in (spider.get("perObject") or {}).items():
        cells = per.get("step2Cells") or []
        if not cells:
            continue
        any_cells = True
        lines.append(f"## {code}")
        lines.append("")
        lines.append(_table(
            ["生命周期状态名称", "进入动作", "用户动作", "进入条件"],
            [[c.get("statusName"), c.get("enterAction"), c.get("userAction"), c.get("enterCondition")]
             for c in cells],
        ))
        lines.append("")
    if not any_cells:
        lines.append("本次没有读到任何状态行（对象未启用生命周期，或状态/动作读失败——见 manifest 的"
                     "`coverage` 与 `failures`）。")
        lines.append("")
    path = output_dir / "step2-status-table.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_step3_md(spider: dict[str, Any], output_dir: Path) -> Path:
    """第 3 步产物：状态变化归因明细（button / enterAction / workflowStep）。"""
    attr_rows = []
    notes: list[str] = []
    for per in (spider.get("perObject") or {}).values():
        for row in per["step3"]["rows"]:
            attr_rows.append([row.get("from"), row.get("carrier"),
                              "进入" if row.get("trigger") == "enter" else "用户",
                              row.get("mechanism"), row.get("to"), row.get("evidence")])
        notes.extend(per["step3"].get("unverifiedNotes") or [])
    lines = ["# 蜘蛛计划 · 第 3 步：状态变化归因", "",
             "> 每条边都带证据（按钮/进入动作/工作流发起），不猜。", ""]
    if attr_rows:
        lines.append(_table(["来源状态", "载体", "触发", "机制", "变化后", "依据"], attr_rows))
    else:
        lines.append("本次没有归因边：平台在这两个状态上都没有配用户动作/进入动作"
                     "（`UserAction/GetList` 返回空）。空的原因见 manifest 的 `coverage`/`failures`/`notes`。")
    lines.append("")
    if notes:
        lines.append("## 不入边的平台事实（如实记录）")
        lines.append("")
        lines.extend(f"- {text}" for text in notes)
        lines.append("")
    path = output_dir / "step3-attribution.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_step4_drawio(spider: dict[str, Any], output_dir: Path) -> Path | None:
    """第 4 步产物：工作流流程图（每个工作流一页，左→右分层）。"""
    graphs = spider.get("workflowGraphs") or []
    usable = [g for g in graphs if g.get("nodes")]
    if not usable:
        return None
    used: set[str] = set()
    pages: list[tuple[str, str]] = []
    for graph in usable:
        origin = str(graph.get("origin") or "")
        name = f"{graph.get('name')}·流程" + (f"（{origin}）" if origin else "")
        body = "".join(drawio_mod.workflow_cells(graph, axis="col"))
        pages.append((drawio_mod.safe_page_name(name, used), body))
    path = output_dir / "step4-workflows.drawio"
    path.write_text(drawio_mod.mxfile(pages, host="akso-workbench-spider"), encoding="utf-8")
    return path


def write_spider_artifacts(spider: dict[str, Any], output_dir: Path) -> list[Path]:
    """落盘五步产物（每步一件：step1 关联表 / step2 状态表 / step3 归因 / step4 流程图 / step5 网络）。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    model = spider["model"]

    # spider-network.json
    import json

    net_json = output_dir / "spider-network.json"
    net_json.write_text(json.dumps(
        {"meta": {"generatedFrom": "egmp-native"}, "model": {k: v for k, v in model.items() if k != "edges"} | {"edges": model["edges"]}},
        ensure_ascii=False, indent=2, default=str,
    ), encoding="utf-8")
    written.append(net_json)

    # spider-network.drawio：总览页 + 每对象状态机页
    # id 一律经 drawio_mod.cell_id() 归一（纯函数，故节点与边的引用天然对得上），
    # 且边只在**两端都在本页节点集内**时才画——否则会产生悬空引用（如 source=None、
    # 或 target 指向不存在的 obj:工作流「X」）。
    pages: list[tuple[str, str]] = []
    overview_cells: list[str] = []
    obj_nodes = [n for n in model["nodes"] if n["kind"] == "object"]
    overview_ids = {drawio_mod.cell_id(n["id"]) for n in obj_nodes}
    for idx, node in enumerate(obj_nodes):
        x, y = 40 + (idx % 3) * 290, 40 + (idx // 3) * 110
        overview_cells.append(drawio_mod.node_cell(
            drawio_mod.cell_id(node["id"]), [node["label"]],
            "rounded=1;fillColor=#dae8fc;strokeColor=#6c8ebf;" if not node.get("stub")
            else "rounded=1;dashed=1;fillColor=#f5f5f5;strokeColor=#999999;",
            x, y, 220, 70))
    used: set[str] = set()
    for edge in model["edges"]:
        if edge["kind"] != "relation":
            continue
        src, tgt = drawio_mod.cell_id(edge.get("source")), drawio_mod.cell_id(edge.get("target"))
        if src in overview_ids and tgt in overview_ids:
            overview_cells.append(drawio_mod.edge_cell(src, tgt, edge.get("label") or ""))
    for node in obj_nodes:
        code = node["code"]
        cells: list[str] = []
        obj_anchor = f"obj:{code}"
        anchors = {drawio_mod.cell_id(obj_anchor)}
        cells.append(drawio_mod.node_cell(
            drawio_mod.cell_id(obj_anchor), [node["label"]],
            "rounded=1;fillColor=#ffe6cc;strokeColor=#d79b00;", 40, 40, 180, 40))
        states = [n for n in model["nodes"] if n.get("object") == code and n["kind"] != "object"]
        state_style = {
            "init": "ellipse;fillColor=#d5e8d4;strokeColor=#82b366;",
            "complete": "ellipse;fillColor=#f8cecc;strokeColor=#b85450;",
            "state": "rounded=1;fillColor=#dae8fc;strokeColor=#6c8ebf;",
        }
        for idx, state in enumerate(states):
            cell = drawio_mod.cell_id(state["id"])
            anchors.add(cell)
            cells.append(drawio_mod.node_cell(cell, [state["label"]],
                                              state_style[state["kind"]], 320, 40 + idx * 92, 160, 36))
        for edge in model["edges"]:
            if not edge["kind"].startswith("attr:"):
                continue
            src, tgt = drawio_mod.cell_id(edge.get("source")), drawio_mod.cell_id(edge.get("target"))
            if src in anchors and tgt in anchors:
                cells.append(drawio_mod.edge_cell(src, tgt, edge.get("label") or ""))
        pages.append((drawio_mod.safe_page_name(f"{node['label']}·状态机", used), "".join(cells)))
    pages.insert(0, (drawio_mod.safe_page_name("总览·对象网络", used), "".join(overview_cells)))
    net_drawio = output_dir / "spider-network.drawio"
    net_drawio.write_text(drawio_mod.mxfile(pages, host="akso-workbench-spider"), encoding="utf-8")
    written.append(net_drawio)

    # 第 1/2/3/4 步各自的产物
    written.append(_write_step1_md(spider, output_dir))
    written.append(_write_step2_md(spider, output_dir))
    written.append(_write_step3_md(spider, output_dir))
    step4 = _write_step4_drawio(spider, output_dir)
    if step4 is not None:
        written.append(step4)

    # spider-network.md（步骤索引 + 归因统计 + networkx 分析 + 边界）
    analysis = model.get("analysis", {})
    graphs = spider.get("workflowGraphs") or []
    lines = ["# 蜘蛛计划 · 第 5 步：总体生命周期网络", ""]
    lines.append("## 步骤与产物")
    lines.append("")
    lines.append(_table(["步骤", "产物", "本次结果"], [
        ["1 关联对象", "step1-relations.md",
         f"{model['stats']['relationEdges']} 条关系边"],
        ["2 状态明细", "step2-status-table.md",
         f"{model['stats']['stateNodes']} 个状态"],
        ["3 状态变化归因", "step3-attribution.md",
         f"{model['stats']['attrEdges']} 条归因边"],
        ["4 工作流流程图", "step4-workflows.drawio" if step4 is not None else "（本次没有可画的工作流）",
         f"{len(graphs)} 个工作流"],
        ["5 网络与分析", "spider-network.json / .drawio",
         f"{model['stats']['objectNodes']} 个对象节点"],
    ]))
    lines.append("")
    lines.append("## 归因边统计")
    lines.append(_table(["指标", "数量"], [[k, v] for k, v in model["stats"].items()]))
    lines.append("")
    lines.append("## networkx 分析（Python 化收益点）")
    lines.append(f"- 有向无环：{'是' if analysis.get('isDag') else '否（存在环——归因回边）'}")
    lines.append(f"- 孤立节点：{'、'.join(map(str, analysis.get('orphans', []))) or '—'}")
    lines.append("")
    lines.append("| 枢纽节点（度中心性 Top） | 中心性 |")
    lines.append("|---|---|")
    for hub in analysis.get("hubs", []):
        lines.append(f"| {hub['label']} | {hub['centrality']} |")
    lines.append("")
    lines.append("## 边界与缺口（不允许静默留白）")
    lines.append("")
    notes = spider.get("notes") or []
    failures = spider.get("failures") or []
    unexpanded = model.get("unexpandedRelations") or []
    if notes:
        lines.extend(f"- {text}" for text in notes)
    if unexpanded:
        lines.append("- 未展开的关联对象（平台未给编码）："
                     + "、".join(str(item.get("label")) for item in unexpanded))
    if failures:
        lines.append(f"- 读失败 {len(failures)} 项（详见 manifest.json 的 failures）：")
        lines.extend(f"  - [{item.get('stage')}] {item.get('code')}：{item.get('error')}"
                     for item in failures[:20])
    if not (notes or unexpanded or failures):
        lines.append("- 本次没有记录到边界或读失败。")
    net_md = output_dir / "spider-network.md"
    net_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    written.append(net_md)
    return written
