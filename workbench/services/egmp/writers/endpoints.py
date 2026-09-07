"""写路径端点与枚举常量（自 akso-auto scripts/api 只读提取，Python 化对齐）。

⚠ 这些值是平台实证：改动前必须与原仓库 create-*.js 系列核对。
"""

from __future__ import annotations

# ---------------- 对象/字段/选项集 ----------------
SAVE_BASIC_OBJECT = "/api/platform/BasicObject/SaveBasicObject"
SAVE_FIELD = "/api/platform/BasicObject/SaveField"
FIELD_PAGE = "/api/platform/BasicObject/FieldPage"
PICKLIST_SAVE = "/api/platform/ObjectPicklist/save"

# ---------------- 生命周期 ----------------
STATUS_CREATE = "/api/config/lifecycle/Status/Create"
STATUS_UPDATE = "/api/config/lifecycle/Status/Update"
USER_ACTION_CREATE = "/api/config/lifecycle/UserAction/Create"
LIFESTATUS_USER_ACTION_GET = "/api/config/power/LifestatusUserAction/Get"

# 平台全局固定值（实证：create-lifecycle-status.js）
WORKFLOW_CANCEL_STATUS_FIXED = "11515107-dce6-4ef1-8d51-b1ffed22040f"

# ---------------- 工作流 ----------------
WORKFLOW_ADD = "/api/platform/Workflow/AddWorkflowBasic"
WORKFLOW_UPDATE = "/api/platform/Workflow/UpdateWorkflowBasic"
WORKFLOW_CHANGE_STATUS = "/api/platform/Workflow/ChangeWorkflowBasicStatus"
WORKFLOW_DELETE = "/api/platform/Workflow/DeleteWorkflowBasic"
WORKFLOW_LIST = "/api/platform/Workflow/GetWorkflowBasicPageViewList"
WORKFLOW_ADD_TASK_STEP = "/api/platform/Workflow/AddWorkflowTaskStep"
WORKFLOW_ADD_DECISION_STEP = "/api/platform/Workflow/AddWorkflowDecisionStep"
WORKFLOW_ADD_ACTION_STEP = "/api/platform/Workflow/AddWorkflowActionStep"
WORKFLOW_UPDATE_TASK_STEP = "/api/platform/Workflow/UpdateWorkflowTaskStep"
WORKFLOW_UPDATE_DECISION_STEP = "/api/platform/Workflow/UpdateWorkflowDecisionStep"
WORKFLOW_UPDATE_ACTION_STEP = "/api/platform/Workflow/UpdateWorkflowActionStep"
WORKFLOW_UPDATE_START_STEP = "/api/platform/Workflow/UpdateWorkflowStartStep"
WORKFLOW_COPY_STEP = "/api/platform/Workflow/CopyWorkflowCurrentStep"
WORKFLOW_DELETE_STEP = "/api/platform/Workflow/DeleteWorkflowStep"

WORKFLOW_STATUS = {"DRAFT": 10, "ENABLED": 20, "EDITING": 40}
WORKFLOW_TYPE = {"RECORD": 10}
STEP_TYPE = {"START": 10, "END": 20, "TASK": 30, "DECISION": 70, "ACTION": 90}
DISPATCH_MODE = {"COUNTERSIGN": 10, "OR_SIGN": 20}
TASK_REQUIRE_TYPE = {"REQUIRED": 20}
LOGIC_TYPE = {"ALL_EQUAL": 40, "ANY_EQUAL": 41, "NONE_EQUAL": 42}
LOGICAL_OPERATOR = {"AND": 1}
CTRL_TYPE = {"PARTICIPANT": 20}
SELECT_USER_TYPE = {"SPECIFIED": 10}
BELONG_TYPE = {"STATUS": 2}
BEHAVIOR_TYPE = {"START_WORKFLOW": 7, "UPDATE_STATUS": 9}

# ---------------- 布局 ----------------
LAYOUT_LIST = "/api/platform/Layout/LayoutList"
LAYOUT_DETAIL = "/api/platform/Layout/LayoutDetail"  # ⚠ 双参数缺一 500
LAYOUT_SAVE_DETAIL = "/api/platform/Layout/SaveLayoutDetail"  # 全量替换
LISTLAYOUT_LIST = "/api/platform/Listlayout/List"
LISTLAYOUT_COLUMNS = "/api/platform/Listlayout/Columns"
LISTLAYOUT_SAVE = "/api/platform/Listlayout/Save"  # 新建布局
LISTLAYOUT_ADD_COLUMNS = "/api/platform/Listlayout/AddColumns"  # 全量替换
LISTLAYOUT_SAVE_FILTER = "/api/platform/Listlayout/SaveFilter"
FIELDS_AND_OUT_FIELD = "/api/platform/BasicObject/FieldsAndOutField"  # 跨对象字段

# ---------------- 菜单 ----------------
MENU_SUBMIT = "/api/platform/Menu/Submit"
MENU_SORT = "/api/platform/Menu/Sort"  # 全量树
MENUGROUP_QUERY = "/api/platform/MenuGroup/QueryList"
MENU_ACTION_TYPE = {"OBJECT_LINK": 1, "CONTAINER": 3}
MENU_TYPE = {"STANDARD": 1}

# ---------------- OpenAPI（幂等/验证） ----------------
OPENAPI_OBJECT = "/api/openapi/v1.0/BasicObject/{code}"
OPENAPI_FIELD = "/api/openapi/v1.0/BasicObject/field/{code}"
OPENAPI_PICKLIST = "/api/openapi/v1.0/ObjectPicklist/{code}"
OPENAPI_STATUS = "/api/openapi/v1.0/BasicObject/lifecycleStatus/{code}"
OPENAPI_WORKFLOW = "/api/openapi/v1.0/Workflow/{code}"

# 布局系统字段白名单（validate-blueprint.js Layer2）
SYSTEM_FIELD_WHITELIST = {
    "status_id__sys", "created_by__sys", "created_time__sys", "name__ak", "is_deleted__sys",
}

# 系统默认状态（lifecycle-builder.js DEFAULT_STATUSES 口径）
DEFAULT_STATUS_NAMES = ("草稿", "待审批", "已完成", "已关闭")
INIT_STATUS_CODE = "init_status__c"
COMPLETE_STATUS_CODE = "complete_status__c"
