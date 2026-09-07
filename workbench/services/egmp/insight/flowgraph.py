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

    for status in statuses:
        status_id = str(status.get("id"))
        state: dict[str, Any] = {"id": status_id, "name": status.get("name"),
                                 "code": status.get("code"), "actions": []}
        actions = user_actions_by_status.get(status_id, [])

        for action in actions:
            action_id = str(action.get("id"))
            state["actions"].append({
                "id": action_id, "name": action.get("actions") or action.get("name"),
                "executeType": action.get("executeType"),
                "description": action.get("description"),
            })
            execute_type = action.get("executeType")
            target_from_desc = extract_target_status_from_description(action.get("description") or "")

            # GetActionBar 匹配（消耗式：同状态内按名称匹配一次）
            bar_row = action_bar_rows.get(action_id)
            if bar_row is None:
                bar_row = _consume_match(action_bar_rows, action, status_id)

            if bar_row:
                decoded = decode_action_bar_row(bar_row)
                if decoded and decoded["kind"] == "update_status":
                    user_action_edges.append({
                        "source": status_id, "target": decoded.get("targetStatusId"),
                        "button": decoded.get("button"),
                        "targetName": decoded.get("targetStatusName"),
                    })
                elif decoded and decoded["kind"] == "launch_workflow":
                    workflow = workflows_by_row_id.get(str(decoded.get("workflowBasicId"))) or {}
                    launch_edges.append({
                        "source": status_id, "workflowBasicId": decoded.get("workflowBasicId"),
                        "workflowName": workflow.get("name") or decoded.get("button"),
                        "button": decoded.get("button"),
                    })
                    if decoded.get("workflowBasicId"):
                        involved_workflows[str(decoded["workflowBasicId"])] = {
                            "workflowBasicId": decoded["workflowBasicId"],
                            "name": workflow.get("name"),
                            "launchedByActions": decoded.get("button"),
                            "graph": workflow_graphs.get(str(decoded["workflowBasicId"])),
                        }
            else:
                # 无 bar 行：executeType=2 → 进入动作；target 取 description 正则
                if execute_type == 2:
                    if target_from_desc:
                        target_id = _find_status_id_by_name(status_names, target_from_desc)
                        enter_action_edges.append({
                            "source": status_id, "target": target_id,
                            "targetName": target_from_desc,
                            "action": action.get("actions") or action.get("name"),
                            "evidence": "description",
                        })
                    else:
                        trigger_edges.append({
                            "source": status_id, "action": action.get("actions") or action.get("name"),
                            "description": action.get("description"),
                        })
                elif action.get("actionList"):
                    # GetList 自带 actionList 的兜底通道
                    for item in action["actionList"]:
                        if isinstance(item, dict) and item.get("behaviorType") == BEHAVIOR_UPDATE_STATUS:
                            user_action_edges.append({
                                "source": status_id, "target": item.get("targetId"),
                                "button": item.get("name"), "targetName": item.get("targetName"),
                            })
                else:
                    unmatched_buttons.append({"status": status_id, "action": action.get("name") or action.get("actions"),
                                              "reason": "no-bar-row"})
        states.append(state)

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
        "flowEdges": flow_edges,
        "unmatchedButtons": unmatched_buttons,
        "involvedWorkflows": list(involved_workflows.values()),
    }


def _consume_match(action_bar_rows: dict[str, list[dict[str, Any]]],
                   action: dict[str, Any], status_id: str) -> dict[str, Any] | None:
    """同状态内消耗式名称匹配（GetList 与 GetActionBar id 不一致时的上游口径）。"""
    name = str(action.get("actions") or action.get("name") or "").strip()
    if not name:
        return None
    for row in action_bar_rows.values():
        if str(row.get("name") or "").strip() == name and not row.get("_consumed"):
            row["_consumed"] = True
            return row
    return None


def _find_status_id_by_name(status_names: dict[str, Any], name: str) -> str | None:
    for status_id, status_name in status_names.items():
        if status_name == name:
            return status_id
    return None
