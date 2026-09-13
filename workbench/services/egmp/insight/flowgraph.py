"""L3 流转图（akso-cc understand/flowgraph.ts 的 Python 化，纯函数核心）。

三类边：
- triggerEdges  状态触发（进入动作 executeType=2）
- userActionEdges 用户按钮（executeType=1，经 GetActionBar 解析 behaviorType 7/9）
- flowEdges     工作流步骤连线（decodeStep 解析步骤详情）

GetActionBar 与 GetList 的 id 不一致时按「同状态内消耗式名称匹配」，
余量记 unmatched_buttons（上游口径）。
"""

from __future__ import annotations

import re
from typing import Any

from ..auth import DISPATCH_MODE_NAMES, STEP_TYPE_NAMES

TARGET_STATUS_RE = re.compile(r"修改状态为[［\[]([^\]］]+)")

# stepAction.configs 的 behaviorValue 语义（akso-auto/akso-cc 双源一致）
BEHAVIOR_UPDATE_STATUS = 9
BEHAVIOR_LAUNCH_WORKFLOW = 7


def extract_target_status_from_description(description: str) -> str | None:
    """从进入动作 description 提取目标状态名（已知边界的唯一通道）。"""
    match = TARGET_STATUS_RE.search(description or "")
    return match.group(1) if match else None


def decode_step(step: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    """工作流步骤详情 → 节点 meta（分发模式/审批人/行为/判断规则）。"""
    node: dict[str, Any] = {
        "id": step.get("id"),
        "name": step.get("name"),
        "code": step.get("code"),
        "stepType": step.get("stepType"),
        "stepTypeName": STEP_TYPE_NAMES.get(step.get("stepType"), str(step.get("stepType"))),
        "nextStepIds": [],
    }
    next_step_id = step.get("nextStepId")
    if next_step_id:
        node["nextStepIds"].append(str(next_step_id))
    for next_id in step.get("nextStepList") or []:
        if next_id and str(next_id) not in node["nextStepIds"]:
            node["nextStepIds"].append(str(next_id))

    task_config = detail.get("stepTaskConfig") or {}
    if task_config:
        dispatch = task_config.get("dispatchMode")
        node["dispatchMode"] = DISPATCH_MODE_NAMES.get(dispatch)
        approvers = task_config.get("approverContent") or task_config.get("approvers")
        node["approver"] = approvers

    behaviors: list[dict[str, Any]] = []
    for action in detail.get("stepAction", {}).get("actions") or []:
        for config in action.get("configs") or []:
            behavior_type = config.get("behaviorType")
            behaviors.append({
                "behaviorType": behavior_type,
                "behaviorValue": config.get("behaviorValue"),
                "summary": _behavior_summary(behavior_type, config.get("behaviorValue")),
            })
    node["behaviors"] = behaviors

    decisions = detail.get("stepDecision") or {}
    rules = []
    for rule in decisions.get("rules") or []:
        rules.append({
            "condition": _cond_text(rule.get("condition")),
            "jumpTo": rule.get("nextStepId"),
        })
    if decisions.get("defaultNextStepId"):
        rules.append({"condition": "默认", "jumpTo": decisions["defaultNextStepId"]})
    node["decisionRules"] = rules
    return node


def _behavior_summary(behavior_type: Any, value: Any) -> str:
    if behavior_type == BEHAVIOR_UPDATE_STATUS:
        return f"修改状态 → {value}"
    if behavior_type == BEHAVIOR_LAUNCH_WORKFLOW:
        return f"发起工作流 {value}"
    if behavior_type == 5:
        return f"更新字段 {value}"
    return f"behaviorType={behavior_type}"


def _cond_text(condition: Any) -> str:
    if not condition:
        return ""
    if isinstance(condition, str):
        return condition
    details = condition.get("details") if isinstance(condition, dict) else None
    if isinstance(details, list):
        parts = []
        for item in details:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("label") or item))
            else:
                parts.append(str(item))
        return " 且 ".join(parts)
    return str(condition)


def build_workflow_graph(steps: list[dict[str, Any]], details: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """步骤 + 连线 → 图。details: step_id → 步骤详情。"""
    nodes = [decode_step(step, details.get(str(step.get("id")), {})) for step in steps]
    edges: list[dict[str, Any]] = []
    node_ids = {str(n["id"]) for n in nodes}
    for node in nodes:
        for next_id in node["nextStepIds"]:
            if str(next_id) in node_ids:
                edges.append({"source": node["id"], "target": next_id, "kind": "flow"})
        for rule in node.get("decisionRules", []):
            jump = rule.get("jumpTo")
            if jump and str(jump) in node_ids and str(jump) not in [e["target"] for e in edges if e["source"] == node["id"]]:
                edges.append({"source": node["id"], "target": str(jump), "kind": "decision",
                              "label": rule.get("condition")})
    return {"nodes": nodes, "edges": edges}


def decode_action_bar_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """按钮行为语义：9=改状态→目标、7=发起工作流→workflowBasicId。"""
    behavior_type = row.get("behaviorType")
    if behavior_type == BEHAVIOR_UPDATE_STATUS:
        return {"kind": "update_status", "targetStatusId": row.get("targetId"),
                "targetStatusName": row.get("targetName"), "button": row.get("name")}
    if behavior_type == BEHAVIOR_LAUNCH_WORKFLOW:
        return {"kind": "launch_workflow", "workflowBasicId": row.get("targetId"),
                "button": row.get("name")}
    return None


def build_lifecycle_graph(
    statuses: list[dict[str, Any]],
    user_actions_by_status: dict[str, list[dict[str, Any]]],
    action_bar_rows: dict[str, list[dict[str, Any]]],
    workflows_by_row_id: dict[str, dict[str, Any]],
    workflow_graphs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """状态机 × 工作流 × 状态动作融合。

    参数：
      statuses: OpenAPI 状态列表（id/code/name）
      user_actions_by_status: status_id → UserAction/GetList 行
      action_bar_rows: action_id → GetActionBar 行
      workflows_by_row_id: 工作流行 id → 工作流行（含 workflowBasicId/name）
      workflow_graphs: 工作流行 id → build_workflow_graph 结果
    """
    states: list[dict[str, Any]] = []
    trigger_edges: list[dict[str, Any]] = []
    user_action_edges: list[dict[str, Any]] = []
    enter_action_edges: list[dict[str, Any]] = []
    launch_edges: list[dict[str, Any]] = []
    unmatched_buttons: list[dict[str, Any]] = []
    involved_workflows: dict[str, dict[str, Any]] = {}

    status_names = {str(s.get("id")): s.get("name") for s in statuses}

    # `GetActionBar` 的 targetId 是 **workflowBasicId**，而 workflows_by_row_id / workflow_graphs
    # 都按**工作流行 id** 建键。不建这张反查表就会既取不到工作流名、也取不到流程图（→ 不产出 drawio）。
    row_id_by_basic: dict[str, str] = {}
    for row_id, row in workflows_by_row_id.items():
        basic = str(row.get("workflowBasicId") or "")
        if basic:
            row_id_by_basic.setdefault(basic, row_id)

    # 全部状态下已知的动作 id —— 用于判定某行动作栏是否**明确属于别的状态**
    all_known_action_ids = {
        str(a.get("id")) for rows in user_actions_by_status.values()
        for a in rows if a.get("id")
    }

    for status in statuses:
        status_id = str(status.get("id"))
        state: dict[str, Any] = {"id": status_id, "name": status.get("name"),
                                 "code": status.get("code"), "actions": []}
        actions = user_actions_by_status.get(status_id, [])
        # 本状态**自身**的动作 id 集合：消耗式名称匹配只允许在这里面找（见 _consume_match）
        own_action_ids = [str(a.get("id")) for a in actions if a.get("id")]

        for action in actions:
            action_id = str(action.get("id"))
            action_label = action.get("actions") or action.get("name")
            state["actions"].append({
                "id": action_id, "name": action_label,
                "executeType": action.get("executeType"),
                "description": action.get("description"),
            })
            execute_type = action.get("executeType")
            is_enter = execute_type == 2  # 2 = 进入状态时自动执行
            target_from_desc = extract_target_status_from_description(action.get("description") or "")

            # GetActionBar 匹配（先按 id 精确命中；否则同状态内消耗式名称匹配）
            bar_row = action_bar_rows.get(action_id)
            if bar_row is None:
                bar_row = _consume_match(
                    action_bar_rows, action, set(own_action_ids),
                    all_known_action_ids - set(own_action_ids),
                )

            if bar_row:
                decoded = decode_action_bar_row(bar_row)
                if decoded and decoded["kind"] == "update_status":
                    # 归类按 executeType 走：进入动作不能混进「用户动作」列（否则四列表两列都错）
                    edge: dict[str, Any] = {
                        "source": status_id, "target": decoded.get("targetStatusId"),
                        "targetName": decoded.get("targetStatusName"),
                        "mechanism": "enterAction" if is_enter else "button",
                    }
                    if is_enter:
                        edge["action"] = action_label
                        enter_action_edges.append(edge)
                    else:
                        edge["button"] = decoded.get("button")
                        user_action_edges.append(edge)
                elif decoded and decoded["kind"] == "launch_workflow":
                    basic_id = str(decoded.get("workflowBasicId") or "")
                    row_id = row_id_by_basic.get(basic_id, basic_id)
                    workflow = workflows_by_row_id.get(row_id) or {}
                    launch_edges.append({
                        "source": status_id, "workflowBasicId": decoded.get("workflowBasicId"),
                        "workflowName": workflow.get("name") or decoded.get("button"),
                        "button": decoded.get("button"),
                        "mechanism": "enterAction" if is_enter else "button",
                    })
                    if basic_id:
                        involved_workflows[basic_id] = {
                            "workflowBasicId": decoded["workflowBasicId"],
                            "name": workflow.get("name"),
                            "launchedByActions": decoded.get("button"),
                            "graph": workflow_graphs.get(row_id),
                        }
            else:
                # 无 bar 行：executeType=2 → 进入动作；target 取 description 正则
                if execute_type == 2:
                    if target_from_desc:
                        target_id = _find_status_id_by_name(status_names, target_from_desc)
                        enter_action_edges.append({
                            "source": status_id, "target": target_id,
                            "targetName": target_from_desc,
                            "action": action_label,
                            "evidence": "description",
                            "mechanism": "enterAction",
                        })
                    else:
                        trigger_edges.append({
                            "source": status_id, "action": action_label,
                            "description": action.get("description"),
                        })
                elif action.get("actionList"):
                    # GetList 自带 actionList 的兜底通道
                    for item in action["actionList"]:
                        if isinstance(item, dict) and item.get("behaviorType") == BEHAVIOR_UPDATE_STATUS:
                            user_action_edges.append({
                                "source": status_id, "target": item.get("targetId"),
                                "button": item.get("name"), "targetName": item.get("targetName"),
                                "mechanism": "button",
                            })
                else:
                    unmatched_buttons.append({"status": status_id, "action": action_label,
                                              "reason": "no-bar-row"})
        states.append(state)

    # 工作流触发状态：来自工作流行的 lifecycleStatusIds（上游口径，与按钮通道**取并集**）。
    # 只认已知状态 id——已删除/不可见的状态不得产出悬空触发。
    workflow_trigger_edges: list[dict[str, Any]] = []
    for row_id, row in workflows_by_row_id.items():
        for raw_status_id in row.get("lifecycleStatusIds") or []:
            sid = str(raw_status_id)
            if sid not in status_names:
                continue
            workflow_trigger_edges.append({
                "statusId": sid, "statusName": status_names.get(sid),
                "workflowRowId": row_id,
                "workflowBasicId": row.get("workflowBasicId"),
                "workflowName": row.get("name"),
                "mechanism": "lifecycleStatusIds",
            })

    flow_edges: list[dict[str, Any]] = []
    for row_id, graph in workflow_graphs.items():
        workflow = workflows_by_row_id.get(row_id, {})
        for edge in graph.get("edges", []):
            flow_edges.append({"workflow": workflow.get("name") or row_id, **edge})

    return {
        "states": states,
        "triggerEdges": trigger_edges,
        "userActionEdges": user_action_edges,
        "enterActionEdges": enter_action_edges,
        "launchEdges": launch_edges,
        "workflowTriggerEdges": workflow_trigger_edges,
        "flowEdges": flow_edges,
        "unmatchedButtons": unmatched_buttons,
        "involvedWorkflows": list(involved_workflows.values()),
    }


def _consume_match(action_bar_rows: dict[str, dict[str, Any]],
                   action: dict[str, Any], own_action_ids: set[str],
                   foreign_action_ids: set[str]) -> dict[str, Any] | None:
    """同状态内消耗式名称匹配（GetList 与 GetActionBar id 不一致时的上游口径）。

    历史缺陷：函数接受 status_id 却从未使用，实际遍历 `action_bar_rows.values()`（**全部状态**），
    导致不同状态间的同名按钮互相错配（实测：`sa` 的「提交」被配到 `sb` 的动作栏行）。

    现行规则——只在「本状态自身的动作」与「无法归属任何状态的动作」里按名称找：
    - 本状态自己的 id：允许（正常路径）；
    - 已知属于**别的状态**的 id：**排除**，不得借用；
    - 谁的 id 都不是（GetActionBar 返回的 id 不解析）：允许按名称匹配——这正是该兜底存在的理由。
    """
    name = str(action.get("actions") or action.get("name") or "").strip()
    if not name:
        return None
    for row_id, row in action_bar_rows.items():
        if row_id not in own_action_ids and row_id in foreign_action_ids:
            continue
        if str(row.get("name") or "").strip() == name and not row.get("_consumed"):
            row["_consumed"] = True
            return row
    return None


def _find_status_id_by_name(status_names: dict[str, Any], name: str) -> str | None:
    for status_id, status_name in status_names.items():
        if status_name == name:
            return status_id
    return None
