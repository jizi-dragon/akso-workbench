"""生命周期创建（akso-auto create-lifecycle-status.js + lifecycle-builder.js 的 Python 化）。

两阶段（上游口径）：先 renameStatuses 复用重命名系统默认状态（幂等跳过，
重命名结果也入 statusMap），再批量新建 statuses。
"""

from __future__ import annotations

from typing import Any

from .endpoints import (
    BEHAVIOR_TYPE,
    BELONG_TYPE,
    COMPLETE_STATUS_CODE,
    INIT_STATUS_CODE,
    STATUS_CREATE,
    STATUS_UPDATE,
    USER_ACTION_CREATE,
    WORKFLOW_CANCEL_STATUS_FIXED,
    WORKFLOW_STATUS,
)


def create_status(client: Any, *, lifecycle_id: str, name: str, code: str,
                  is_enabled: bool = True) -> dict[str, Any]:
    resp = client.post(STATUS_CREATE, {
        "code": code, "name": name, "lifecycleId": lifecycle_id, "isEnabled": is_enabled,
        "description": "", "source": 3, "isRecordDeactivate": False,
        "workflowCancelStatus": WORKFLOW_CANCEL_STATUS_FIXED,
    })
    return {"success": True, "statusId": resp if isinstance(resp, str) else (resp or {}).get("id"),
            "name": name, "code": code, "message": f"状态[{name}]创建成功"}


def update_status(client: Any, *, status_id: str, name: str | None = None,
                  is_enabled: bool | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"id": status_id}
    if name is not None:
        body["name"] = name
    if is_enabled is not None:
        body["isEnabled"] = is_enabled
    client.request("PATCH", STATUS_UPDATE, body)
    return {"success": True, "statusId": status_id, "message": f"状态[{name or status_id}]更新成功"}


def build_statuses(client: Any, *, lifecycle_id: str, blueprint_lifecycle: dict[str, Any],
                   object_code: str) -> dict[str, Any]:
    """两阶段构建，产出 statusMap（code → statusId）。"""
    status_map: dict[str, str] = {}
    steps: list[dict[str, Any]] = []

    for rename in blueprint_lifecycle.get("renameStatuses", []):
        from_code = str(rename.get("fromCode"))
        to_name = str(rename.get("toName"))
        if from_code in (INIT_STATUS_CODE, COMPLETE_STATUS_CODE):
            # 系统保留状态：经 OpenAPI 找到 id 后重命名（幂等：同名跳过）
            existing = _find_system_status(client, object_code, from_code)
            if existing and existing.get("name") != to_name:
                update_status(client, status_id=str(existing["id"]), name=to_name)
                status_map[from_code] = str(existing["id"])
                steps.append({"step": f"rename:{from_code}", "status": "success"})
            elif existing:
                status_map[from_code] = str(existing["id"])
                steps.append({"step": f"rename:{from_code}", "status": "skipped"})
            else:
                steps.append({"step": f"rename:{from_code}", "status": "failed",
                              "message": "系统保留状态未找到"})
        else:
            steps.append({"step": f"rename:{from_code}", "status": "skipped",
                          "message": "非系统保留状态，跳过重命名"})

    for status in blueprint_lifecycle.get("statuses", []):
        result = create_status(client, lifecycle_id=lifecycle_id,
                               name=str(status.get("name")), code=str(status.get("code")))
        if result.get("statusId"):
            status_map[str(status.get("code"))] = str(result["statusId"])
        steps.append({"step": f"create:{status.get('code')}", "status": "success" if result.get("statusId") else "failed"})

    return {"statusMap": status_map, "steps": steps}


def _find_system_status(client: Any, object_code: str, system_code: str) -> dict[str, Any] | None:
    from ..queries import GaiaQueries

    statuses = GaiaQueries(client).get_statuses(object_code) or []
    for status in statuses:
        if str(status.get("code")) == system_code:
            return status
    return None


def bind_workflow_to_status(
    client: Any, *, status_id: str, lifecycle_id: str, object_id: str, object_code: str,
    workflow_basic_id: str, action_name: str = "提交", icon: str = "CheckOutlined",
    description: str = "提交动作",
) -> dict[str, Any]:
    """UserAction/Create：把工作流绑到状态（behaviorType=7 发起工作流）。

    前置：工作流必须已启用（上游 bindWorkflowToStatus 步骤 0）。
    """
    import uuid as _uuid

    code = f"action_{_uuid.uuid4().hex[:12]}__c"
    observed = str(_uuid.uuid4())
    body = {
        "isEnabled": True,
        "belongId": status_id,
        "belongType": BELONG_TYPE["STATUS"],
        "sort": 1,
        "executeType": 1,
        "description": description,
        "lifecycleId": lifecycle_id,
        "lifecycleStatusId": status_id,
        "executionConditionGroups": [],
        "__observed": observed,
        "basicObjectId": object_id,
        "objectCode": object_code,
        "enableSignatures": True,
        "actions": [{
            "__observed": str(_uuid.uuid4()),
            "name": action_name,
            "code": code,
            "icon": icon,
            "group": 1,
            "isSubmitAndExecute": False,
            "isCommonly": True,
            "sort": 0,
            "configs": [{
                "behaviorType": BEHAVIOR_TYPE["START_WORKFLOW"],
                "behaviorValue": {
                    "isBeforeWorkflowCompleteReminderTask": True,
                    "workflowId": workflow_basic_id,
                },
            }],
        }],
    }
    client.post(USER_ACTION_CREATE, body)
    return {"success": True, "message": f"工作流绑定到状态成功（{action_name}）"}


def ensure_workflow_enabled(client: Any, *, workflow_config_id: str, workflow_basic_id: str) -> dict[str, Any]:
    """启用工作流（已启用则跳过）。"""
    from .endpoints import WORKFLOW_CHANGE_STATUS

    try:
        detail = client.get(f"/api/platform/Workflow/GetWorkflowBasicDetail?workflowConfigId={workflow_config_id}")
    except Exception:  # noqa: BLE001
        detail = {}
    current = None
    if isinstance(detail, dict):
        current = (detail.get("basicConfig") or {}).get("status") or detail.get("status")
    if current == WORKFLOW_STATUS["ENABLED"]:
        return {"success": True, "skipped": True, "message": "工作流已启用"}
    client.post(WORKFLOW_CHANGE_STATUS, {
        "workflowConfigId": workflow_config_id, "workflowBasicId": workflow_basic_id,
        "status": WORKFLOW_STATUS["ENABLED"],
    })
    return {"success": True, "message": "工作流已启用"}
