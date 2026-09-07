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
            return {"id": object_id}
        if path == "/api/platform/BasicObject/SaveField":
            object_id = body["objectId"]
            for oc, meta in self.objects.items():
                if meta["id"] == object_id:
                    self.fields_by_object.setdefault(oc, []).append(
                        {"id": str(uuid.uuid4()), "code": body["code"], "name": body["name"],
                         "dataType": body["dataType"], **{k: v for k, v in body.items()
                                                          if k not in {"objectId"}}})
                    return {"id": str(uuid.uuid4())}
            raise RuntimeError("objectId 不存在")
        if path == "/api/platform/ObjectPicklist/save":
            self.picklists[body["code"]] = {"id": str(uuid.uuid4()), "code": body["code"],
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
        if path.startswith("/api/openapi/v1.0/BasicObject/"):
            code = path.rsplit("/", 1)[-1]
            meta = self.objects.get(code)
            if meta is None:
                raise RuntimeError(f"对象不存在：{code}")
            return dict(meta)
        if path.startswith("/api/openapi/v1.0/BasicObject/field/"):
            code = path.rsplit("/", 1)[-1]
            return self.fields_by_object.get(code, [])
        if path.startswith("/api/openapi/v1.0/BasicObject/lifecycleStatus/"):
            code = path.rsplit("/", 1)[-1]
            return self.statuses_by_object.get(code, [])
        if path.startswith("/api/openapi/v1.0/ObjectPicklist/"):
            code = path.rsplit("/", 1)[-1]
            meta = self.picklists.get(code)
            if meta is None:
                raise RuntimeError(f"选项集不存在：{code}")
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
            return {"isEnabled": True}
        if path.startswith("/api/platform/Menu/GetMenuList"):
            return self.menus
        if path.startswith("/api/platform/Workflow/GetWorkflowSteps"):
            return [
                {"id": "step-start", "stepType": 10, "name": "开始"},
                {"id": "step-end", "stepType": 20, "name": "结束"},
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
