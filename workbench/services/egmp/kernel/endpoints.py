"""平台端点唯一事实来源（**读路径与写路径共用**）。

规格见 docs/INSIGHT-SPEC.md §3.2。此前的问题：`writers/endpoints.py` 自称权威表，但 insight
**完全没用它**——同样的字面量被硬编码了约 14 处，另有 6 个 insight 在用的端点表里根本没有。
现在读写两侧都从这里取，不再各自写字符串。

**命名空间必须分清**，因为两套契约不同，平台升级时最先断的是后者：

- :data:`NS_OPENAPI` = ``/api/openapi/v1.0/*`` —— **版本化**标准 API；
- :data:`NS_PLATFORM` = ``/api/platform/*`` —— 私有 web 控制台接口，**无版本**；
- :data:`NS_CONFIG` = ``/api/config/*`` —— 私有配置接口，**无版本**。

带 ``{code}`` 的模板一律用 :func:`render` 展开（会对 code 做 URL 编码，
避免非 ASCII/特殊字符编码的对象编码被原样拼进路径）。
"""

from __future__ import annotations

from urllib.parse import quote

NS_OPENAPI = "openapi"
NS_PLATFORM = "platform"
NS_CONFIG = "config"

# ---------------------------------------------------------------- OpenAPI（版本化）

AUTH = "/api/openapi/v1.0/Auth"
OBJECT = "/api/openapi/v1.0/BasicObject/{code}"
OBJECT_FIELD = "/api/openapi/v1.0/BasicObject/field/{code}"
OBJECT_LIFECYCLE = "/api/openapi/v1.0/BasicObject/lifecycleStatus/{code}"
OBJECT_PICKLIST = "/api/openapi/v1.0/ObjectPicklist/{code}"
WORKFLOW = "/api/openapi/v1.0/Workflow/{code}"

# ---------------------------------------------------------------- platform（私有、无版本）

FIELD_PAGE = "/api/platform/BasicObject/FieldPage"
MENU_LIST = "/api/platform/Menu/GetMenuList"
MENU_GROUP_QUERY = "/api/platform/MenuGroup/QueryList"
MENU_SUBMIT = "/api/platform/Menu/Submit"
MENU_SORT = "/api/platform/Menu/Sort"

LAYOUT_LIST = "/api/platform/Layout/LayoutList"
LAYOUT_DETAIL = "/api/platform/Layout/LayoutDetail"  # ⚠ 双参数缺一 500
LAYOUT_SAVE_DETAIL = "/api/platform/Layout/SaveLayoutDetail"  # 全量替换
LISTLAYOUT_LIST = "/api/platform/Listlayout/List"
LISTLAYOUT_COLUMNS = "/api/platform/Listlayout/Columns"
LISTLAYOUT_SAVE = "/api/platform/Listlayout/Save"
LISTLAYOUT_ADD_COLUMNS = "/api/platform/Listlayout/AddColumns"  # 全量替换
LISTLAYOUT_SAVE_FILTER = "/api/platform/Listlayout/SaveFilter"
FIELDS_AND_OUT_FIELD = "/api/platform/BasicObject/FieldsAndOutField"  # 跨对象字段

WORKFLOW_LIST = "/api/platform/Workflow/GetWorkflowBasicPageViewList"
WORKFLOW_STEPS = "/api/platform/Workflow/GetWorkflowSteps"
WORKFLOW_STEP_DETAIL = "/api/platform/Workflow/GetWorkflowStepWithDetails"
WORKFLOW_ADD = "/api/platform/Workflow/AddWorkflowBasic"
WORKFLOW_UPDATE = "/api/platform/Workflow/UpdateWorkflowBasic"
WORKFLOW_CHANGE_STATUS = "/api/platform/Workflow/ChangeWorkflowBasicStatus"
WORKFLOW_DELETE = "/api/platform/Workflow/DeleteWorkflowBasic"
WORKFLOW_ADD_TASK_STEP = "/api/platform/Workflow/AddWorkflowTaskStep"
WORKFLOW_ADD_DECISION_STEP = "/api/platform/Workflow/AddWorkflowDecisionStep"
WORKFLOW_ADD_ACTION_STEP = "/api/platform/Workflow/AddWorkflowActionStep"
WORKFLOW_UPDATE_TASK_STEP = "/api/platform/Workflow/UpdateWorkflowTaskStep"
WORKFLOW_UPDATE_DECISION_STEP = "/api/platform/Workflow/UpdateWorkflowDecisionStep"
WORKFLOW_UPDATE_ACTION_STEP = "/api/platform/Workflow/UpdateWorkflowActionStep"
WORKFLOW_UPDATE_START_STEP = "/api/platform/Workflow/UpdateWorkflowStartStep"
WORKFLOW_COPY_STEP = "/api/platform/Workflow/CopyWorkflowCurrentStep"
WORKFLOW_DELETE_STEP = "/api/platform/Workflow/DeleteWorkflowStep"

SAVE_BASIC_OBJECT = "/api/platform/BasicObject/SaveBasicObject"
SAVE_FIELD = "/api/platform/BasicObject/SaveField"

# 对象发现链第 3 步「候选接口探针」——上游 README 给出的端点，此前移植版**只在注释里写了**。
# 三条都是 POST；可用性需按环境实测（可能全 404，那就是该环境的既有边界）。
OBJECT_PROBE_QUERY_LIST = "/api/platform/BasicObject/QueryList"
OBJECT_PROBE_PAGE_LIST = "/api/platform/BasicObject/GetPageList"
OBJECT_PROBE_PAGE = "/api/platform/BasicObject/Page"
OBJECT_PROBES = (OBJECT_PROBE_QUERY_LIST, OBJECT_PROBE_PAGE_LIST, OBJECT_PROBE_PAGE)

# ---------------------------------------------------------------- config（私有、无版本）

USER_ACTION_LIST = "/api/config/lifecycle/UserAction/GetList"
USER_ACTION_BAR = "/api/config/lifecycle/UserAction/GetActionBar"
STATUS_DETAIL = "/api/config/lifecycle/Status/Detail"
STATUS_CREATE = "/api/config/lifecycle/Status/Create"
STATUS_UPDATE = "/api/config/lifecycle/Status/Update"
USER_ACTION_CREATE = "/api/config/lifecycle/UserAction/Create"
PICKLIST_SAVE = "/api/platform/ObjectPicklist/save"
LIFESTATUS_USER_ACTION_GET = "/api/config/power/LifestatusUserAction/Get"

# 平台全局固定值（实证：create-lifecycle-status.js）——不是"取消后流转到"的边，是全局配置 id
WORKFLOW_CANCEL_STATUS_FIXED = "11515107-dce6-4ef1-8d51-b1ffed22040f"

# 布局系统字段白名单（validate-blueprint.js Layer2）
SYSTEM_FIELD_WHITELIST = {
    "status_id__sys", "created_by__sys", "created_time__sys", "name__ak", "is_deleted__sys",
}

# 系统默认状态（lifecycle-builder.js DEFAULT_STATUSES 口径）
DEFAULT_STATUS_NAMES = ("草稿", "待审批", "已完成", "已关闭")
INIT_STATUS_CODE = "init_status__c"
COMPLETE_STATUS_CODE = "complete_status__c"


# ---------------------------------------------------------------- 展开与归属


def render(template: str, **params: object) -> str:
    """展开带 ``{name}`` 的端点模板；路径参数做 URL 编码。

    编码是必要的：对象编码来自用户输入，可能含空格/斜杠/非 ASCII，
    原样拼接会改变路径结构。
    """
    encoded = {key: quote(str(value), safe="") for key, value in params.items()}
    return template.format(**encoded)


def namespace_of(path: str) -> str:
    """判断端点属于哪个命名空间（用于把「版本化」与「私有」分开诊断）。"""
    if path.startswith("/api/openapi/"):
        return NS_OPENAPI
    if path.startswith("/api/platform/"):
        return NS_PLATFORM
    if path.startswith("/api/config/"):
        return NS_CONFIG
    return "unknown"


def is_versioned(path: str) -> bool:
    """是否版本化端点。私有端点在平台升级时最先断，排障时应优先怀疑。"""
    return namespace_of(path) == NS_OPENAPI


ALL_ENDPOINTS: tuple[str, ...] = tuple(sorted({
    value for name, value in list(globals().items())
    if name.isupper() and isinstance(value, str) and value.startswith("/api/")
}))


# ---------------------------------------------------------------- 兼容别名
# 旧名（`writers/endpoints.py` 时代的拼写），保留以免打断既有调用点。
# 新代码请用上面规范名；这些名字随垫片一起在将来清理。
# `LEGACY_ALIASES` 供「端点表无重复值」的校验排除——别名按定义就是重复值。

MENUGROUP_QUERY = MENU_GROUP_QUERY
OPENAPI_OBJECT = OBJECT
OPENAPI_FIELD = OBJECT_FIELD
OPENAPI_STATUS = OBJECT_LIFECYCLE
OPENAPI_PICKLIST = OBJECT_PICKLIST
OPENAPI_WORKFLOW = WORKFLOW

LEGACY_ALIASES = frozenset({
    "MENUGROUP_QUERY", "OPENAPI_OBJECT", "OPENAPI_FIELD",
    "OPENAPI_STATUS", "OPENAPI_PICKLIST", "OPENAPI_WORKFLOW",
})
