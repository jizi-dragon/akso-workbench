"""沙箱：不受信代码的**唯一执行底座**。

安全姿态与「挡不住什么」写在 `docs/CORE-ARCHITECTURE.md` §3.4.1。

## 模块分工（依赖方向单向，不允许反向）

    layout  ← 只管目录口径（唯一决定"什么算沙箱根"）
    policy  ← 只管策略声明与校验（纯数据，不 import db / services / 插件）
    job     ← 只管进程树的生命周期强制（Job Object / 进程组）
    runner  ← 执行器：把上面三者组装起来，四层强制都在这里落地

依赖方向：`runner → {policy, job, layout}`，`layout → config`。
**`policy` 不依赖任何本包其他模块**——它必须能在没装依赖的机器上被单测
（与本仓 `tools/verify_packaging.py` 的"不 import 也能跑"同一考虑）。

## 这个包**不是**安全边界

它保证：整棵进程树能停 · 凭据不自动喂进去 · 工作目录不越界 · 每次都留痕 · 失败即拒。
它**不**保证：跑恶意代码也安全（无内核级隔离）。
"""

from __future__ import annotations

from .job import IS_WINDOWS, MAX_ACTIVE_PROCESSES
from .job import stats as job_stats
from .layout import (
    SANDBOX_ROOT,
    describe_workspace,
    ensure_dirs,
    permission_error,
    reset_workspace,
    save_workspace,
    workspace_root,
    workspace_settings,
)
from .policy import (
    POLICIES,
    SandboxError,
    SandboxPolicy,
    Severity,
    build_env,
    describe_policies,
    get_policy,
    is_sensitive_path,
    within_root,
)
from .runner import ALLOWED_EXECUTABLES, ExecRequest, ExecResult, run


def capability_matrix() -> dict:
    """平台能力矩阵：**只列被强制的项**，并显式列出不强制项。

    这是给界面与审计读的单一事实源——"沙箱能拦什么"不要再由各处文案各写一遍。
    每一项要么指向 enforcement primitive，要么进 `not_enforced`。
    """
    enforced = [
        "cwd 必须在工作区根内（启动前校验）",
        "cwd 不得命中敏感路径（启动前校验）",
        "可执行文件必须在解释器白名单内",
        "环境变量白名单",
        "输出字节上限（显式截断标记）",
        "超时终止整棵进程树（Job Object / 进程组）",
        "每次执行落审计",
    ]
    not_enforced = [
        "运行时文件系统读写（脚本仍以当前用户权限运行）",
        "网络访问（allow_network 是声明，不是强制）",
        "文件删除 / 重命名的隔离",
        "workspace-write 的内核级写隔离（capability SID 尚未落地）",
        "网络访问的内核级强制（WFP/防火墙）",
    ]
    if IS_WINDOWS:
        enforced.append(f"Job Object 后代补绑 + IsProcessInJob 校验；活动进程上限 {MAX_ACTIVE_PROCESSES}")
        enforced.append("read-only 策略附加 WRITE_RESTRICTED 受限令牌（logon + Everyone；partial）")
    return {
        "platform": "windows" if IS_WINDOWS else "posix",
        "enforced": enforced,
        "not_enforced": not_enforced,
        "job_object": IS_WINDOWS,
        "read_only_token": IS_WINDOWS,
        "workspace_write_token": False,
        "max_active_processes": MAX_ACTIVE_PROCESSES,
        "honest_limit": "这是限损层，不是安全边界；除 enforced 外的字段都只是声明。",
    }


__all__ = [
    "ALLOWED_EXECUTABLES",
    "ExecRequest",
    "ExecResult",
    "IS_WINDOWS",
    "MAX_ACTIVE_PROCESSES",
    "POLICIES",
    "SANDBOX_ROOT",
    "SandboxError",
    "SandboxPolicy",
    "Severity",
    "build_env",
    "capability_matrix",
    "describe_policies",
    "describe_workspace",
    "ensure_dirs",
    "get_policy",
    "is_sensitive_path",
    "job_stats",
    "permission_error",
    "reset_workspace",
    "run",
    "save_workspace",
    "within_root",
    "workspace_root",
    "workspace_settings",
]
