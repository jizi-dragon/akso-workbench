"""工作流创建引擎（akso-auto create-workflow.js + create-workflow-step.js +
workflow-builder.js 的 Python 化）。

管道（上游 workflow-builder.js 口径）：
  a 创建基础 → b 取步骤找开始/结束 → c 配置开始步骤参与者 → d 建
  participantControlMap → e 创建全部步骤（先不连线）→ f backfillConnections
  回填连线（task/decision 规则树/action 的 nextStepId）→ g 开始步骤连第一步 →
  h autoEnable 启用 → i bindWorkflowToStatus 绑状态。
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

from . import lifecycle as lifecycle_ops
from .endpoints import (
    CTRL_TYPE,
    DISPATCH_MODE,
    LOGIC_TYPE,
    LOGICAL_OPERATOR,
    SELECT_USER_TYPE,
    STEP_TYPE,
    TASK_REQUIRE_TYPE,
    WORKFLOW_ADD,
    WORKFLOW_ADD_ACTION_STEP,
    WORKFLOW_ADD_DECISION_STEP,
    WORKFLOW_ADD_TASK_STEP,
    WORKFLOW_TYPE,
    WORKFLOW_UPDATE_START_STEP,
)


def create_workflow_basic(client: Any, *, name: str, code: str, object_id: str,
                          lifecycle_id: str) -> dict[str, Any]:
    resp = client.post(WORKFLOW_ADD, {
        "basicConfig": {
            "name": name, "code": code, "status": 10,  # DRAFT
            "objectId": object_id, "lifecycleId": lifecycle_id,
            "workflowType": WORKFLOW_TYPE["RECORD"], "isSingleRecord": True,
        },
        "variables": [],
    })
    detail = resp if isinstance(resp, dict) else {}
    return {
        "success": True,
        "workflowConfigId": str(detail.get("workflowConfigId") or detail.get("id") or detail or ""),
        "workflowBasicId": str(detail.get("workflowBasicId") or detail.get("basicId") or ""),
        "message": f"工作流[{name}]创建成功",
    }


def get_workflow_steps(client: Any, workflow_config_id: str) -> list[dict[str, Any]]:
    from ..queries import invalidate_object_cache

    invalidate_object_cache()
    rows = client.get(
        "/api/platform/Workflow/GetWorkflowSteps",
        params={"workflowConfigId": workflow_config_id},
    )
    return list(rows or [])


def add_task_step(client: Any, *, workflow_config_id: str, name: str, code: str,
                  next_step_id: str = "", dispatch_mode: int = DISPATCH_MODE["COUNTERSIGN"],
                  participant_ref: str | None = None) -> dict[str, Any]:
    step_task_config = {
        "taskNodeType": 0, "preTaskIds": "", "reciverType": 20, "notifyType": 0,
        "selectStatusTypeIds": "", "externalSignatureInfoFrom": 1,
        "name": name, "taskAssign": "taskOwner", "isAssignToPromoter": True,
        "dispatchMode": dispatch_mode, "taskRequireType": TASK_REQUIRE_TYPE["REQUIRED"],
        "pageOpenType": 0, "breakApproveIds": "",
    }
    body = {
        "step": {
            "nextStepId": next_step_id, "name": name, "code": code,
            "stepType": STEP_TYPE["TASK"],
            "stepTaskConfig": step_task_config,
            "workflowConfigId": workflow_config_id,
        }
    }
    resp = client.post(WORKFLOW_ADD_TASK_STEP, body)
    step_id = (resp or {}).get("step", {}).get("id") if isinstance(resp, dict) else None
    return {"success": True, "stepId": step_id, "code": code}


def add_decision_step(client: Any, *, workflow_config_id: str, name: str, code: str,
                      rules: list[dict[str, Any]], else_next_step_id: str = "",
                      next_step_id: str = "") -> dict[str, Any]:
    decision_rules = []
    for idx, rule in enumerate(rules):
        relation_id = str(_uuid.uuid4())
        decision_rules.append({
            "id": str(_uuid.uuid4()), "sort": idx + 1,
            "name": rule.get("conditionLabel") or f"规则{idx + 1}",
            "condition": {
                "details": [{
                    "id": str(_uuid.uuid4()), "index": idx * 1000,
                    "relationId": relation_id, "targetType": 7,
                    "targetId": rule.get("targetId"), "targetCode": rule.get("targetCode"),
                    "logicType": rule.get("logicType") or LOGIC_TYPE["ALL_EQUAL"],
                    "logicValue": rule.get("logicValue"),
                }],
                "relations": [{"id": relation_id, "logicalOperator": LOGICAL_OPERATOR["AND"],
                               "index": idx}],
            },
            "nextStepId": rule.get("jumpTo") or rule.get("nextStepId"),
        })
    body = {
        "step": {
            "nextStepId": next_step_id, "name": name, "code": code,
            "stepType": STEP_TYPE["DECISION"],
            "stepDecision": {"rules": decision_rules, "nextStepId": else_next_step_id},
            "workflowConfigId": workflow_config_id,
        }
    }
    resp = client.post(WORKFLOW_ADD_DECISION_STEP, body)
    step_id = (resp or {}).get("step", {}).get("id") if isinstance(resp, dict) else None
    return {"success": True, "stepId": step_id, "code": code}


def add_action_step(client: Any, *, workflow_config_id: str, name: str, code: str,
                    behavior_type: int, behavior_value: Any,
                    next_step_id: str = "") -> dict[str, Any]:
    body = {
        "step": {
            "nextStepId": next_step_id, "name": name, "code": code,
            "stepType": STEP_TYPE["ACTION"],
            "stepAction": {
                "belongType": 3,
                "actions": [{
                    "code": "", "name": "", "isEnabled": True, "belongType": 3,
                    "configs": [{"behaviorType": behavior_type, "behaviorValue": behavior_value,
                                 "sort": 0}],
                    "sort": 0, "executeType": 1, "description": "",
                    "lifecycleId": "", "lifecycleStatusId": "",
                }],
            },
            "workflowConfigId": workflow_config_id,
        }
    }
    resp = client.post(WORKFLOW_ADD_ACTION_STEP, body)
    step_id = (resp or {}).get("step", {}).get("id") if isinstance(resp, dict) else None
    return {"success": True, "stepId": step_id, "code": code}


def config_start_step(client: Any, *, workflow_config_id: str, participants: list[dict[str, Any]],
                      next_step_id: str = "") -> dict[str, Any]:
    """开始步骤：参与者 startUpControls + nextStepId 连线（上游 configStartStep）。"""
    step: dict[str, Any] = {"stepType": STEP_TYPE["START"], "workflowConfigId": workflow_config_id}
    controls = []
    participant_map: dict[str, str] = {}
    for idx, participant in enumerate(participants):
        control_id = str(_uuid.uuid4())
        participant_map[str(participant.get("code"))] = control_id
        controls.append({
            "id": control_id, "ctrlType": CTRL_TYPE["PARTICIPANT"],
            "name": participant.get("name"), "sort": idx,
            "config": {"selectUserType": SELECT_USER_TYPE["SPECIFIED"],
                       "userContent": participant.get("code")},
        })
    step["startUpControls"] = controls
    if next_step_id:
        step["nextStepId"] = next_step_id
    client.post(WORKFLOW_UPDATE_START_STEP, {"step": step})
    return {"success": True, "participantControlMap": participant_map}


def backfill_connections(client: Any, *, workflow_config_id: str,
                         created: dict[str, dict[str, Any]],
                         blueprint_steps: list[dict[str, Any]],
                         blueprint_rules: list[dict[str, Any]]) -> dict[str, Any]:
    """回填连线（两次提交语义：先建后连）。

    created: blueprint step code → {stepId, type}
    """
    from .endpoints import (
        WORKFLOW_UPDATE_ACTION_STEP,
        WORKFLOW_UPDATE_DECISION_STEP,
        WORKFLOW_UPDATE_TASK_STEP,
    )

    done = 0
    # action/task 的 nextStepCode → nextStepId
    for bp in blueprint_steps:
        target = created.get(str(bp.get("code")))
        if not target or not bp.get("nextStepCode"):
            continue
        next_target = created.get(str(bp["nextStepCode"]))
        if not next_target:
            continue
        step_type = target.get("type")
        if step_type == "task":
            client.post(WORKFLOW_UPDATE_TASK_STEP, {
                "step": {"id": target["stepId"], "nextStepId": next_target["stepId"],
                         "workflowConfigId": workflow_config_id},
            })
            done += 1
        elif step_type == "decision":
            client.post(WORKFLOW_UPDATE_DECISION_STEP, {
                "step": {"id": target["stepId"], "nextStepId": next_target["stepId"],
                         "workflowConfigId": workflow_config_id},
            })
            done += 1
        elif step_type == "action":
            client.post(WORKFLOW_UPDATE_ACTION_STEP, {
                "step": {"id": target["stepId"], "nextStepId": next_target["stepId"],
                         "workflowConfigId": workflow_config_id},
            })
            done += 1
    # 判断规则树：sourceStep 的规则跳到 jumpTo
    for rule in blueprint_rules:
        source = created.get(str(rule.get("sourceStep")))
        jump = created.get(str(rule.get("jumpTo")))
        decision = created.get(str(rule.get("decisionStep")))
        if not (source and jump and decision and decision.get("type") == "decision"):
            continue
        client.post(WORKFLOW_UPDATE_DECISION_STEP, {
            "step": {
                "id": decision["stepId"], "workflowConfigId": workflow_config_id,
                "stepDecision": {"rules": [{
                    "name": rule.get("conditionLabel") or "规则",
                    "condition": {"details": [{
                        "targetType": 7, "targetId": rule.get("targetId"),
                        "targetCode": rule.get("targetCode"),
                        "logicType": rule.get("logicType") or LOGIC_TYPE["ALL_EQUAL"],
                        "logicValue": rule.get("matchValue"),
                    }], "relations": [{"logicalOperator": LOGICAL_OPERATOR["AND"]}]},
                    "nextStepId": jump["stepId"],
                }], "nextStepId": None},
            },
        })
        done += 1
    return {"success": True, "connections": done}


def build_workflow(client: Any, *, object_id: str, lifecycle_id: str, object_code: str,
                   blueprint: dict[str, Any], status_map: dict[str, str]) -> dict[str, Any]:
    """全流程编排（workflow-builder.js a-i 步骤）。"""
    name = str(blueprint.get("name"))
    code = str(blueprint.get("code"))
    created_base = create_workflow_basic(client, name=name, code=code,
                                         object_id=object_id, lifecycle_id=lifecycle_id)
    workflow_config_id = created_base["workflowConfigId"]

    steps = get_workflow_steps(client, workflow_config_id)
    start_step = next((s for s in steps if s.get("stepType") == STEP_TYPE["START"]), None)
    end_step = next((s for s in steps if s.get("stepType") == STEP_TYPE["END"]), None)

    blueprint_steps = list(blueprint.get("steps", []))
    first_bp = blueprint_steps[0] if blueprint_steps else None

    # c/d 开始步骤参与者
    config_start_step(
        client, workflow_config_id=workflow_config_id,
        participants=list(blueprint.get("participants", [])),
        next_step_id="",
    ) if blueprint.get("participants") else {"participantControlMap": {}}

    # e 创建全部步骤（先不连线）
    created: dict[str, dict[str, Any]] = {}
    for bp in blueprint_steps:
        bp_type = str(bp.get("type"))
        common = {"workflow_config_id": workflow_config_id, "name": str(bp.get("name")),
                  "code": str(bp.get("code"))}
        if bp_type == "task":
            result = add_task_step(client, **common,
                                   dispatch_mode=bp.get("dispatchMode") or DISPATCH_MODE["COUNTERSIGN"])
        elif bp_type == "decision":
            result = add_decision_step(client, **common, rules=[])
        else:
            result = add_action_step(client, **common,
                                     behavior_type=int(bp.get("behaviorType") or 0),
                                     behavior_value=bp.get("behaviorValue"))
        created[str(bp.get("code"))] = {**result, "type": bp_type}

    # f 回填连线 + g 开始步骤连第一步
    backfill = backfill_connections(
        client, workflow_config_id=workflow_config_id, created=created,
        blueprint_steps=blueprint_steps, blueprint_rules=list(blueprint.get("rules", [])),
    )
    if start_step and first_bp and created.get(str(first_bp.get("code"))):
        client.post(WORKFLOW_UPDATE_START_STEP, {
            "step": {"id": start_step.get("id"), "nextStepId": created[str(first_bp["code"])]["stepId"],
                     "workflowConfigId": workflow_config_id},
        })
    # 末步骤连结束
    if end_step and blueprint_steps:
        last_bp = blueprint_steps[-1]
        last = created.get(str(last_bp.get("code")))
        if last and not last_bp.get("nextStepCode"):
            updater = {
                "task": "/api/platform/Workflow/UpdateWorkflowTaskStep",
                "decision": "/api/platform/Workflow/UpdateWorkflowDecisionStep",
                "action": "/api/platform/Workflow/UpdateWorkflowActionStep",
            }[str(last["type"])]
            client.post(updater, {"step": {"id": last["stepId"], "nextStepId": end_step.get("id"),
                                           "workflowConfigId": workflow_config_id}})

    # h 启用 + i 绑状态
    enabled = None
    binding = None
    if blueprint.get("autoEnable") or blueprint.get("bindToStatusCode"):
        workflow_basic_id = created_base.get("workflowBasicId")
        if workflow_basic_id:
            enabled = lifecycle_ops.ensure_workflow_enabled(
                client, workflow_config_id=workflow_config_id, workflow_basic_id=workflow_basic_id)
        bind_code = blueprint.get("bindToStatusCode")
        if bind_code and workflow_basic_id and status_map.get(str(bind_code)):
            binding = lifecycle_ops.bind_workflow_to_status(
                client, status_id=status_map[str(bind_code)], lifecycle_id=lifecycle_id,
                object_id=object_id, object_code=object_code, workflow_basic_id=workflow_basic_id,
            )

    return {
        "success": True, "name": name, "code": code,
        "workflowConfigId": workflow_config_id,
        "workflowBasicId": created_base.get("workflowBasicId"),
        "steps": len(created), "connections": backfill.get("connections", 0),
        "enabled": bool(enabled), "bound": bool(binding),
    }
