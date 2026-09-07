"""生命周期/工作流深挖端点（akso-cc understand/lifecycle.ts 的 Python 化）。

关键返回语义（实证，勿改）：
- UserAction/GetList：executeType 1=用户按钮 / 2=进入状态自动执行；
  description 为平台生成的条件/行为摘要
- UserAction/GetActionBar：behaviorType 7=发起工作流（targetId=workflowBasicId）/
  9=修改状态（targetId=目标状态 id）
- Status/Detail：workflowCancelStatus 是平台全局配置 id，不是"取消后流转到"的边
- GetWorkflowSteps 用工作流行 id（非 workflowBasicId）
"""

from __future__ import annotations

from typing import Any


class LifecycleApi:
    def __init__(self, client: Any) -> None:
        self._client = client

    def user_actions(self, status_id: str) -> list[dict[str, Any]]:
        """状态动作记录（统一视图，executeType 1/2）。"""
        data = self._client.get(
            "/api/config/lifecycle/UserAction/GetList", params={"statusId": status_id}
        )
        return list(data or [])

    def action_bar(self, action_ids: list[str]) -> list[dict[str, Any]]:
        """动作行为（按钮目标：behaviorType 7/9）。"""
        if not action_ids:
            return []
        data = self._client.post("/api/config/lifecycle/UserAction/GetActionBar", {"ids": action_ids})
        return list(data or [])

    def status_detail(self, status_id: str) -> dict[str, Any]:
        data = self._client.get("/api/config/lifecycle/Status/Detail", params={"id": status_id})
        return dict(data or {})

    def workflow_steps(self, workflow_row_id: str) -> list[dict[str, Any]]:
        """工作流步骤（注意：用工作流行 id）。"""
        data = self._client.get(
            "/api/platform/Workflow/GetWorkflowSteps",
            params={"workflowConfigId": workflow_row_id},
        )
        return list(data or [])

    def workflow_step_detail(self, step_id: str) -> dict[str, Any]:
        data = self._client.get(
            "/api/platform/Workflow/GetWorkflowStepWithDetails", params={"id": step_id}
        )
        data = data or {}
        if isinstance(data, dict) and isinstance(data.get("step"), dict):
            return data["step"]
        return data

    def form_layout_detail(self, layout_id: str, object_id: str) -> dict[str, Any]:
        """⚠ 双参数缺一返回 500（实证）。"""
        data = self._client.get(
            "/api/platform/Layout/LayoutDetail",
            params={"layoutId": layout_id, "basicObjectId": object_id},
        )
        return dict(data or {})

    def list_columns(self, listlayout_id: str) -> list[dict[str, Any]]:
        data = self._client.get(
            "/api/platform/Listlayout/Columns", params={"listlayoutId": listlayout_id}
        )
        return list(data or [])
