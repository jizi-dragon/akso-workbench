"""egmp 测试台架：内存版 eGMP 平台（假客户端）+ 断言工具。

FakeEgmpClient 忠实模拟信封语义（code==0）与分页（hasNext），
不做真实 HTTP —— 管道/幂等/拓扑逻辑可离线全量验证。
"""

from __future__ import annotations

import uuid
from typing import Any


class FakeEgmpClient:
    """内存平台：objects / picklists / statuses / workflows / layouts。"""

    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}
        self.picklists: dict[str, dict[str, Any]] = {}
        self.fields_by_object: dict[str, list[dict[str, Any]]] = {}
        self.statuses_by_object: dict[str, list[dict[str, Any]]] = {}
        self.workflows: list[dict[str, Any]] = []
        self.form_layouts: dict[str, list[dict[str, Any]]] = {}
        self.list_layouts: dict[str, list[dict[str, Any]]] = {}
        self.menus: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str]] = []  # (method, path)
        self.fail_paths: set[str] = set()  # 模拟这些路径失败

    # ---------------- 信封语义 ----------------

    def post(self, path: str, body: dict | None = None) -> Any:
        self.calls.append(("POST", path))
        if path in self.fail_paths:
            raise RuntimeError(f"模拟失败：{path}")
        if path == "/api/platform/BasicObject/SaveBasicObject":
            code = body["code"]
            object_id = str(uuid.uuid4())
            self.objects[code] = {"id": object_id, "name": body["name"], "code": code,
                                  "enableLifeCycle": body.get("enableLifeCycle", False),
                                  "lifeCycleId": str(uuid.uuid4()) if body.get("enableLifeCycle") else None}
            if body.get("enableLifeCycle"):
                self.statuses_by_object[code] = [
                    {"id": str(uuid.uuid4()), "code": "init_status__c", "name": "开启"},
                    {"id": str(uuid.uuid4()), "code": "complete_status__c", "name": "关闭"},
                ]
            # ⚠️ 真机只回 `{code:0, data:true}`（经 _envelope 后就是布尔 True），**不回 id**。
            # 台架原先在这里返回 `{"id": ...}` —— 那是**真实平台不做的事**，于是
            # `create_object` 里 `isinstance(resp, dict)` 那条正确分支被反复执行、
            # `else resp` 那条**从未被测过**，缺陷因此长期藏着（见 PITFALLS #62）。
            return True
        if path == "/api/platform/BasicObject/SaveField":
            object_id = body["objectId"]
            for oc, meta in self.objects.items():
                if meta["id"] == object_id:
                    self.fields_by_object.setdefault(oc, []).append(
                        {"id": str(uuid.uuid4()), "code": body["code"], "name": body["name"],
                         "dataType": body["dataType"], **{k: v for k, v in body.items()
                                                          if k not in {"objectId"}}})
                    # 同上：真机 SaveField 也只回成功标志，不回 id
                    return True
            raise RuntimeError("objectId 不存在")
        if path == "/api/platform/ObjectPicklist/save":
            # 真机 `save` 只回 `data: true`，**不回 id**；选项集 id 由平台生成，
            # 之后只能从 `/BasicObject/picklist/{code}` 的 `objectPicklistId` 里读到。
            self.picklists[body["code"]] = {"objectPicklistId": str(uuid.uuid4()),
                                            "code": body["code"],
                                            "name": body["name"], "options": body["options"]}
            return True
        if path == "/api/config/lifecycle/Status/Create":
            for oc, meta in self.objects.items():
                if meta.get("lifeCycleId") == body.get("lifecycleId"):
                    status_id = str(uuid.uuid4())
                    self.statuses_by_object.setdefault(oc, []).append(
                        {"id": status_id, "code": body["code"], "name": body["name"]})
                    return status_id
            raise RuntimeError("lifecycleId 不存在")
        if path == "/api/config/lifecycle/UserAction/GetActionBar":
            ids = (body or {}).get("ids", [])
            rows = []
            for action_id in ids:
                if action_id.startswith("ua-") and self.workflows:
                    rows.append({"id": action_id, "name": "提交", "behaviorType": 7,
                                 "targetId": self.workflows[0]["workflowBasicId"]})
            return rows
        if path.startswith("/api/platform/Workflow/"):
            if path == "/api/platform/Workflow/AddWorkflowBasic":
                workflow_id = str(uuid.uuid4())
                self.workflows.append({"id": workflow_id, "workflowBasicId": str(uuid.uuid4()),
                                       **body.get("basicConfig", {})})
                return {"workflowConfigId": workflow_id, "workflowBasicId": self.workflows[-1]["workflowBasicId"]}
            return {"step": {"id": str(uuid.uuid4())}}
        if path == "/api/platform/MenuGroup/QueryList":
            return []
        if path == "/api/platform/Menu/Submit":
            menu_id = str(uuid.uuid4())
            self.menus.append({"id": menu_id, **(body or {})})
            return {"Id": menu_id}
        return {"ok": True}

    def request(self, method: str, path: str, body: dict | None = None) -> Any:
        self.calls.append((method, path))
        return {"ok": True}

    def get(self, path: str, params: dict | None = None) -> Any:
        self.calls.append(("GET", path))
        if path in self.fail_paths:
            raise RuntimeError(f"模拟失败：{path}")
        if path.startswith("/api/openapi/v1.0/BasicObject/field/"):
            code = path.rsplit("/", 1)[-1]
            return self.fields_by_object.get(code, [])
        if path.startswith("/api/openapi/v1.0/BasicObject/lifecycleStatus/"):
            code = path.rsplit("/", 1)[-1]
            return self.statuses_by_object.get(code, [])
        if path.startswith("/api/openapi/v1.0/BasicObject/picklist/"):
            # ⚠ 必须放在下面那条 `BasicObject/` **之前**：`BasicObject/picklist/...` 同样以
            # 该前缀开头，顺序写反会把选项集当成对象元数据（与 field / lifecycleStatus 同一个坑）。
            #
            # ⚠⚠ **返回形状是「选项数组」，不是选项集对象**（2026-09-21 真机实测）：
            #     [{"id": "<选项id>", "objectPicklistId": "<选项集id>", "name": ..., "code": ...}]
            # 选项集 id 在 `objectPicklistId` 里，**不在** `data["id"]`。
            # 本夹具原来返回的是 `{{"id": ...}}` 这样一个 dict —— 那是**照着错常量猜的**，
            # 于是测试一直"通过"，而真机上 `data` 是 list，调用方的 `meta.get("id")`
            # 会 AttributeError 并被吞掉（PITFALLS #67）。夹具照真机抄，才不会反过来守错误行为。
            code = path.rsplit("/", 1)[-1]
            meta = self.picklists.get(code)
            if meta is None:
                raise RuntimeError(f"选项集不存在：{code}")
            return [{**opt, "id": str(uuid.uuid4()),
                     "objectPicklistId": meta["objectPicklistId"]}
                    for opt in meta.get("options", [])]
        if path.startswith("/api/openapi/v1.0/BasicObject/"):
            # ⚠ 必须放在上面两条**之后**：`BasicObject/field/...` 与 `BasicObject/lifecycleStatus/...`
            # 同样以本前缀开头，顺序写反会把它们误判成对象元数据（真实平台由路由区分，不靠前缀）。
            code = path.rsplit("/", 1)[-1]
            meta = self.objects.get(code)
            if meta is None:
                raise RuntimeError(f"对象不存在：{code}")
            return dict(meta)
        if path.startswith("/api/platform/Layout/LayoutList"):
            oid = (params or {}).get("basicObjectId")
            code = self._code_of(oid)
            return [{"id": f"formlayout-{oid}", "source": 2, "code": f"{code}_base_layout__ak"}]
        if path.startswith("/api/platform/Layout/LayoutDetail"):
            return {"sections": [], "controls": []}
        if path.startswith("/api/platform/Listlayout/List"):
            oid = (params or {}).get("objectId")
            code = self._code_of(oid)
            return [{"id": f"listlayout-{oid}", "source": 2, "code": f"{code}_table__ak"}]
        if path.startswith("/api/platform/Listlayout/Columns"):
            return []
        if path == "/api/platform/MenuGroup/QueryList":
            return []
        if path.startswith("/api/config/lifecycle/UserAction/GetListByStatusId"):
            # 设计器口径：某状态的用户动作（带 code）。初始状态给一个「发起工作流」动作。
            status_id = (params or {}).get("statusId", "")
            for statuses in self.statuses_by_object.values():
                status = next((s for s in statuses if s["id"] == status_id), None)
                if status and status["code"] == "init_status__c":
                    return [{"id": "ua-submit", "code": "Initiate_the_review__c", "name": "提交",
                             # 真机形态：平台生成的描述里写明目标状态 → 归因边靠它解析
                             "description": "提交到状态“关闭”", "group": 1, "isEnabled": True,
                             "lifecycleStatusId": status_id}]
            return []
        if path.startswith("/api/config/lifecycle/UserAction/GetUserAction"):
            # 动作类型只在这条接口里给（7=发起工作流 / 9=修改状态 / 46=跨对象）
            return {"userActionPerms": [
                {"actionId": "ua-submit", "actionCode": "Initiate_the_review__c",
                 "actionName": "提交审核", "behaviorTypes": [7]},
                {"actionId": "ua-close", "actionCode": "close__c",
                 "actionName": "关闭", "behaviorTypes": [9]},
            ]}
        if path.startswith("/api/config/lifecycle/UserAction/GetFieldsAndRelationsList"):
            return [{"targetId": "rel-fake-dept", "targetName": "部门/编号/主对象", "targetType": 2}]
        if path.startswith("/api/config/lifecycle/EnterAction/GetListByStatusId"):
            status_id = (params or {}).get("statusId", "")
            for statuses in self.statuses_by_object.values():
                status = next((s for s in statuses if s["id"] == status_id), None)
                if status and status["code"] == "init_status__c":
                    return [{"id": "ea-init", "sort": 0, "description": "将负责人添加为负责人",
                             "belongId": status_id, "belongType": 1, "executeType": 1, "isEnabled": True}]
            return []
        if path.startswith("/api/config/lifecycle/EnterAction/GetById"):
            return {"id": "ea-init", "description": "将负责人添加为负责人", "belongType": 1,
                    "conditionStr": None, "condition": None,
                    "configs": [{"id": "cfg-1", "behaviorType": 34, "behaviorValue": {}}]}
        if path.startswith("/api/platform/Workflow/GetWorkflowStepWithDetails"):
            # 步骤详情里才是跨对象行为的落点（behaviorType=46）
            return {"id": "step-done", "stepType": 90, "stepAction": {"actions": [
                {"id": "sa-1", "configs": [
                    {"id": "c1", "behaviorType": 9, "behaviorValue": {"statusId": "st-complete"}},
                    {"id": "c2", "behaviorType": 46,
                     "behaviorValue": {"relationId": "rel-fake-dept", "relatedObjectId": "obj-dept",
                                       "targetStatusId": "st-dept-done"}},
                ]},
            ]}}
        if path.startswith("/api/config/lifecycle/UserAction/GetList"):
            status_id = (params or {}).get("statusId", "")
            for statuses in self.statuses_by_object.values():
                if any(s["id"] == status_id for s in statuses):
                    status = next(s for s in statuses if s["id"] == status_id)
                    if status["code"] == "init_status__c" and self.workflows:
                        return [{"id": f"ua-{status_id}", "actions": "提交", "executeType": 1,
                                 "description": "提交"}]
                    return []
            return []
        if path.startswith("/api/config/lifecycle/Status/Detail"):
            # 名字解析用：targetStatusId → 名字（真机同形）
            return {"id": (params or {}).get("id"), "name": f"状态-{(params or {}).get('id', '')[:6]}",
                    "isEnabled": True}
        if path.startswith("/api/platform/Menu/GetMenuList"):
            return self.menus
        if path.startswith("/api/platform/Workflow/GetWorkflowSteps"):
            # 真机形态：步骤之间用 nextStepList/nextStepId 连边（没有边就画不出流程图）
            return [
                {"id": "step-start", "stepType": 10, "name": "开始", "sort": 1,
                 "nextStepId": "step-end", "nextStepList": [{"id": "step-end", "name": "结束"}]},
                {"id": "step-end", "stepType": 20, "name": "结束", "sort": 2,
                 "nextStepId": None, "nextStepList": []},
            ]
        if path.startswith("/api/platform/Workflow/GetWorkflowBasicPageViewList"):
            return self.workflows
        if path.startswith("/api/platform/BasicObject/FieldsAndOutField"):
            return []
        raise RuntimeError(f"未知 GET：{path}")

    # ---------------- 分页语义 ----------------

    def _code_of(self, object_id: str) -> str:
        for code, meta in self.objects.items():
            if meta["id"] == object_id:
                return code
        return str(object_id)

    def paged_post(self, path: str, body: dict | None = None, page_size: int = 1000) -> list[dict]:
        self.calls.append(("POST", path))
        if path == "/api/platform/BasicObject/FieldPage":
            object_id = (body or {}).get("objectId")
            for oc, meta in self.objects.items():
                if meta["id"] == object_id:
                    return self.fields_by_object.get(oc, [])
            return []
        if path == "/api/platform/Workflow/GetWorkflowBasicPageViewList":
            return self.workflows
        return []

    def list_workflows(self) -> list[dict[str, Any]]:
        return self.workflows

    def list_menus(self) -> list[dict]:
        return self.menus

    def get_object(self, code: str) -> dict[str, Any]:
        meta = self.objects.get(code)
        if meta is None:
            raise RuntimeError(f"对象不存在：{code}")
        return dict(meta)

    def get_object_fields(self, code: str) -> list[dict[str, Any]]:
        return self.fields_by_object.get(code, [])

    def get_object_statuses(self, code: str) -> list[dict[str, Any]]:
        return self.statuses_by_object.get(code, [])

    def __enter__(self) -> "FakeEgmpClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        pass
