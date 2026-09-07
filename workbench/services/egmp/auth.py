"""eGMP 认证模块（akso-cc auth.ts 的 Python 化，凭证走统一账号库）。

实际登录/缓存逻辑在 client.py（login / token_for_account）；本模块提供
语义化封装与语义词典（semantics.ts 实证资产）。
"""

from __future__ import annotations

from .client import ApiError, EgmpClient, login, token_for_account  # re-export

# 语义词典（semantics.ts 实证资产，Python 重写必备）
DATA_TYPE_NAMES: dict[int, str] = {
    1: "文本", 2: "数字", 3: "是/否", 4: "选项", 5: "日期", 7: "日期时间", 8: "附件",
    13: "查找", 15: "对象", 16: "父对象", 23: "对象多选", 14: "公式", 17: "长文本", 18: "富文本",
}
# 关联边只认这四种 dataType（relations.ts 口径）
REFERENCE_DATA_TYPES = {13, 15, 16, 23}

BEHAVIOR_TYPE_NAMES: dict[int, str] = {5: "更新字段值", 7: "发起工作流", 9: "修改状态"}
EXECUTE_TYPE_NAMES: dict[int, str] = {1: "用户触发", 2: "进入状态自动执行"}
STEP_TYPE_NAMES: dict[int, str] = {
    10: "开始", 20: "结束", 30: "审批任务", 40: "通知", 70: "判断", 90: "动作",
}
DISPATCH_MODE_NAMES: dict[int, str] = {10: "会签", 20: "或签"}

OBJECT_SUFFIXES = ("__c", "__ak", "__sys")  # 自定义/标准/系统
CODE_PREFIXES = {
    "layout_": "布局", "part_": "表单分组", "option_": "选项集", "lifecycle_": "生命周期",
    "workflow_": "工作流", "status_": "状态", "action_": "用户动作",
}
INIT_STATUS_SUFFIX = "init_status__c"  # 初始状态（系统默认不可删）
COMPLETE_STATUS_SUFFIX = "complete_status__c"  # 终止状态

# 已知边界（重写时必须继承）：进入动作的结构化条件/行为配置无公开读接口，
# 6 轮 48+ 路径探测全部 404/405/500（akso-cc semantics.ts UNRESOLVED_ACTION_READ_PROBES）。
UNRESOLVED_ACTION_READ_NOTE = (
    "进入动作的结构化配置无公开读接口；进入条件语义只能取 UserAction/GetList 的 "
    "description，或用正则 修改状态为[［\\[]([^\\]］]+) 从 description 提取目标状态名"
)


def is_reference_field(data_type: int) -> bool:
    return data_type in REFERENCE_DATA_TYPES


def is_init_status(code: str) -> bool:
    return str(code).endswith(INIT_STATUS_SUFFIX)


def is_complete_status(code: str) -> bool:
    return str(code).endswith(COMPLETE_STATUS_SUFFIX)


__all__ = [
    "ApiError", "EgmpClient", "login", "token_for_account",
    "DATA_TYPE_NAMES", "REFERENCE_DATA_TYPES", "BEHAVIOR_TYPE_NAMES",
    "EXECUTE_TYPE_NAMES", "STEP_TYPE_NAMES", "DISPATCH_MODE_NAMES",
    "OBJECT_SUFFIXES", "CODE_PREFIXES", "INIT_STATUS_SUFFIX", "COMPLETE_STATUS_SUFFIX",
    "UNRESOLVED_ACTION_READ_NOTE", "is_reference_field", "is_init_status", "is_complete_status",
]
