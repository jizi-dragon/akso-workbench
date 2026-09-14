"""生命周期/工作流深挖端点（akso-cc understand/lifecycle.ts 的 Python 化）。

关键返回语义（实证，勿改）：
- UserAction/GetList：executeType 1=用户按钮 / 2=进入状态自动执行；
  description 为平台生成的条件/行为摘要
- UserAction/GetActionBar：behaviorType 7=发起工作流（targetId=workflowBasicId）/
  9=修改状态（targetId=目标状态 id）
- Status/Detail：workflowCancelStatus 是平台全局配置 id，不是"取消后流转到"的边
- GetWorkflowSteps 用工作流行 id（非 workflowBasicId）

> **2026-09-15 真机重校（通达 change_plan__c，19 状态）**：上面第 2 条**在部分环境不成立**——
> `GetActionBar`（POST ids）在该环境**恒返回空**，于是「动作类型 / 目标状态 / 发起的工作流」
> 全部读不到（这正是「进入条件/进入动作/用户动作全错」的根因）。经平台前端 bundle 反查，
> 设计器用的是另外几条：
>
> | 要读的东西 | 真接口 |
> |---|---|
> | 某状态的**用户动作**（带 code） | `UserAction/GetListByStatusId?statusId=` |
> | 动作的**行为类型**（7/9/46…） | `UserAction/GetUserAction?objectId=` 的 `behaviorTypes` |
> | 某状态的**进入动作** | `EnterAction/GetListByStatusId?statusId=` |
> | 进入动作的**目标对象/状态** | `EnterAction/GetById?id=` 的 `configs[].behaviorValue` |
> | 行为可选的**关系对象** | `UserAction/GetFieldsAndRelationsList?objectId=`（`targetType=2`） |
>
> `behaviorType`（平台枚举，实测）：5=更新字段值、7=发起工作流、9=修改状态、
> **46=修改相关对象生命周期状态**（`behaviorValue` 带 `relationId/relatedObjectId/targetStatusId`）。
"""

from __future__ import annotations

from typing import Any

from ..kernel import endpoints


class LifecycleApi:
    def __init__(self, client: Any) -> None:
        self._client = client
        self._status_names: dict[str, str] = {}

    def user_actions(self, status_id: str) -> list[dict[str, Any]]:
        """状态动作记录（统一视图，executeType 1/2）。"""
        data = self._client.get(
            endpoints.USER_ACTION_LIST, params={"statusId": status_id}
        )
        return list(data or [])

    # ------------------------------------------------ 真机口径（2026-09-15 重校）

    def user_actions_by_status(self, status_id: str) -> list[dict[str, Any]]:
        """某状态的**用户动作**清单（设计器口径，带 `code`/`name`/`description`）。

        与 :meth:`user_actions` 的区别：那条只回运行时形态（`{actions, executeType}`，**没有 code**），
        这条才带动作编码与描述——「用户动作」列必须用这条。
        """
        data = self._client.get(
            endpoints.USER_ACTION_BY_STATUS, params={"statusId": status_id}
        )
        return list(data or [])

    def enter_actions(self, status_id: str) -> list[dict[str, Any]]:
        """某状态的**进入动作**（`description` 形如「修改风险评估任务状态为[评估中]」）。"""
        data = self._client.get(
            endpoints.ENTER_ACTION_BY_STATUS, params={"statusId": status_id}
        )
        return list(data or [])

    def enter_action_detail(self, action_id: str) -> dict[str, Any]:
        """进入动作详情：`configs[].behaviorType` + `behaviorValue`（目标状态 / 关系对象）。"""
        data = self._client.get(endpoints.ENTER_ACTION_BY_ID, params={"id": action_id})
        return dict(data or {})

    def action_behavior_types(self, object_id: str) -> dict[str, list[int]]:
        """动作 code → `behaviorTypes`（**只有这条接口给类型**：7=发起工作流 / 9=修改状态 / 46=跨对象）。"""
        data = self._client.get(endpoints.USER_ACTION_PERMS, params={"objectId": object_id}) or {}
        perms = (data or {}).get("userActionPerms") or []
        out: dict[str, list[int]] = {}
        for item in perms:
            code = str(item.get("actionCode") or "")
            if code:
                out[code] = [int(x) for x in (item.get("behaviorTypes") or []) if x is not None]
        return out

    def relation_targets(self, object_id: str) -> dict[str, str]:
        """关系对象 targetId → 名称（`targetType=2`；例如「变更关联的评估矩阵/变更/变更」）。"""
        rows = self._client.get(endpoints.USER_ACTION_TARGETS, params={"objectId": object_id}) or []
        return {
            str(row.get("targetId")): str(row.get("targetName") or "")
            for row in rows
            if row.get("targetType") == 2 and row.get("targetId")
        }

    def status_name(self, status_id: str) -> str:
        """状态 id → 名称（进程内缓存；读不到就如实回显 id，不猜）。"""
        key = str(status_id or "")
        if not key:
            return ""
        if key in self._status_names:
            return self._status_names[key]
        try:
            detail = self._client.get(endpoints.STATUS_DETAIL, params={"id": key}) or {}
            name = str(detail.get("name") or "") or key
        except Exception:  # noqa: BLE001 —— 名字解析失败不该炸整次织网
            name = key
        self._status_names[key] = name
        return name

    def action_bar(self, action_ids: list[str]) -> list[dict[str, Any]]:
        """动作行为（按钮目标：behaviorType 7/9）。

        ⚠ 真机（通达）实测**恒返回空**——动作类型改走
        :meth:`action_behavior_types`（对象级）与 :meth:`enter_action_detail`（进入动作）。
        """
        if not action_ids:
            return []
        data = self._client.post(endpoints.USER_ACTION_BAR, {"ids": action_ids})
        return list(data or [])

    def status_detail(self, status_id: str) -> dict[str, Any]:
        data = self._client.get(endpoints.STATUS_DETAIL, params={"id": status_id})
        return dict(data or {})

    def workflow_steps(self, workflow_row_id: str) -> list[dict[str, Any]]:
        """工作流步骤（注意：用工作流行 id）。"""
        data = self._client.get(
            endpoints.WORKFLOW_STEPS,
            params={"workflowConfigId": workflow_row_id},
        )
        return list(data or [])

    def workflow_step_detail(self, step_id: str) -> dict[str, Any]:
        data = self._client.get(
            endpoints.WORKFLOW_STEP_DETAIL, params={"id": step_id}
        )
        data = data or {}
        if isinstance(data, dict) and isinstance(data.get("step"), dict):
            return data["step"]
        return data

    def form_layout_detail(self, layout_id: str, object_id: str) -> dict[str, Any]:
        """⚠ 双参数缺一返回 500（实证）。"""
        data = self._client.get(
            endpoints.LAYOUT_DETAIL,
            params={"layoutId": layout_id, "basicObjectId": object_id},
        )
        return dict(data or {})

    def list_columns(self, listlayout_id: str) -> list[dict[str, Any]]:
        data = self._client.get(
            endpoints.LISTLAYOUT_COLUMNS, params={"listlayoutId": listlayout_id}
        )
        return list(data or [])
