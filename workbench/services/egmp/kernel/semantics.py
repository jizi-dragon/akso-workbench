"""平台语义词典（**读路径与写路径共用**）：dataType / behaviorType / stepType / 后缀约定。

规格见 docs/INSIGHT-SPEC.md §3.1。此前这些常量散在 `auth.py`，且 insight 里还有几处**自己重写**
同一份约定（`crawler.py` 的后缀候选、`annotate.py` 的 trait 字面量），三处容易漂移。现统一在此。

词典值全部来自上游实证（akso-cc `understand/semantics.ts`），**不是猜的**——改动前请先核对。
"""

from __future__ import annotations

# ---------------------------------------------------------------- dataType

DATA_TYPE_NAMES: dict[int, str] = {
    1: "文本", 2: "数字", 3: "是/否", 4: "选项", 5: "日期", 7: "日期时间", 8: "附件",
    13: "查找", 15: "对象", 16: "父对象", 23: "对象多选", 14: "公式", 17: "长文本", 18: "富文本",
}
# 关联边只认这四种（relations.ts 口径）
REFERENCE_DATA_TYPES = {13, 15, 16, 23}

# ---------------------------------------------------------------- 行为 / 步骤

BEHAVIOR_TYPE_NAMES: dict[int, str] = {
    5: "更新字段值", 7: "发起工作流", 9: "修改状态",
    # 46 = 修改相关对象生命周期状态（2026-09-15 真机实证：behaviorValue 带
    # relationId / relatedObjectId / targetStatusId；名字取自平台自身的 i18n 词典）
    46: "修改相关对象生命周期状态",
}
EXECUTE_TYPE_NAMES: dict[int, str] = {1: "用户触发", 2: "进入状态自动执行"}
STEP_TYPE_NAMES: dict[int, str] = {
    10: "开始", 20: "结束", 30: "审批任务", 40: "通知", 70: "判断", 90: "动作",
}
DISPATCH_MODE_NAMES: dict[int, str] = {10: "会签", 20: "或签"}

# 行为码（代码里直接比较用的那几个，避免散落魔法数）
BEHAVIOR_UPDATE_FIELD = 5
BEHAVIOR_LAUNCH_WORKFLOW = 7
BEHAVIOR_UPDATE_STATUS = 9
BEHAVIOR_UPDATE_RELATED_STATUS = 46   # 跨对象：改**相关对象**的生命周期状态

#: 动作行为的**归属**（平台枚举 `belongType`，取自前端 bundle）：
#: 1=进入反应(进入条件) / 2=进入用户动作 / 3=工作流步骤动作 / 4=工作流取消 / 5=事件动作 / 6=工作流起始步骤
BELONG_TYPE_NAMES: dict[int, str] = {
    1: "进入反应", 2: "进入动作", 3: "工作流步骤动作", 4: "工作流取消", 5: "事件动作", 6: "工作流起始步骤",
}

# 执行类型码
EXECUTE_USER_ACTION = 1
EXECUTE_ENTER_ACTION = 2

# 写路径枚举（自 akso-auto 只读提取）
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
MENU_ACTION_TYPE = {"OBJECT_LINK": 1, "CONTAINER": 3}
MENU_TYPE = {"STANDARD": 1}

# ---------------------------------------------------------------- 命名约定

OBJECT_SUFFIXES = ("__c", "__ak", "__sys")  # 自定义 / 标准 / 系统
# 布局编码 → 剥掉这些后缀后得到对象编码的候选前缀
LAYOUT_CODE_SUFFIXES = ("_table__ak", "_base_layout__ak")
# 剥掉布局后缀后，依次补这些后缀去验证对象编码是否存在（首个命中即确认）
OBJECT_SUFFIX_CANDIDATES = ("__c", "__ak", "__sys", "")
CODE_PREFIXES = {
    "layout_": "布局", "part_": "表单分组", "option_": "选项集", "lifecycle_": "生命周期",
    "workflow_": "工作流", "status_": "状态", "action_": "用户动作",
}
INIT_STATUS_SUFFIX = "init_status__c"  # 初始状态（系统默认不可删）
COMPLETE_STATUS_SUFFIX = "complete_status__c"  # 终止状态

# 已知边界（重写时必须继承）：进入动作的结构化条件/行为配置无公开读接口，
# 6 轮 48+ 路径探测全部 404/405/500（上游 semantics.ts UNRESOLVED_ACTION_READ_PROBES）。
UNRESOLVED_ACTION_READ_NOTE = (
    "进入动作的结构化配置无公开读接口；进入条件语义只能取 UserAction/GetList 的 "
    "description，或用正则 修改状态为[［\\[]([^\\]］]+) 从 description 提取目标状态名"
)


# ---------------------------------------------------------------- 判定函数


def is_reference_field(data_type: int) -> bool:
    """该 dataType 是否构成对象间的关联边。"""
    return data_type in REFERENCE_DATA_TYPES


def is_init_status(code: str) -> bool:
    return str(code).endswith(INIT_STATUS_SUFFIX)


def is_complete_status(code: str) -> bool:
    return str(code).endswith(COMPLETE_STATUS_SUFFIX)


def object_suffix_of(code: str) -> str:
    """对象编码的后缀类型：``__c`` / ``__ak`` / ``__sys``，都不是则空串。

    注意历史缺陷：`annotate.py` 用 `elif` 链自己重写了一份同样的判断，
    无后缀的编码会**拿不到任何 trait**。统一到这里之后，调用方不用再各自实现。
    """
    text = str(code or "")
    for suffix in OBJECT_SUFFIXES:
        if text.endswith(suffix):
            return suffix
    return ""


def data_type_name(data_type: object) -> str:
    """dataType → 中文名；未登记的取值回 ``dataType=<n>``（**不静默丢弃**）。"""
    try:
        value = int(data_type)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return f"dataType={data_type}"
    return DATA_TYPE_NAMES.get(value) or f"dataType={value}"


def behavior_type_name(behavior_type: object) -> str:
    """behaviorType → 中文名；未登记回 ``behaviorType=<n>``（未识别值必须可见）。"""
    try:
        value = int(behavior_type)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return f"behaviorType={behavior_type}"
    return BEHAVIOR_TYPE_NAMES.get(value) or f"behaviorType={value}"


def step_type_name(step_type: object) -> str:
    """stepType → 中文名；未登记回 ``stepType=<n>``。"""
    try:
        value = int(step_type)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return f"stepType={step_type}"
    return STEP_TYPE_NAMES.get(value) or f"stepType={value}"
