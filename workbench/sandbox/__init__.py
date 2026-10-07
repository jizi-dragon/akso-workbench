"""沙箱：不受信代码的**唯一执行底座**。

决策依据见 [ADR-0014](../../docs/adr/0007-credentials-sandbox-and-trust.md)。

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
它**不**保证：跑恶意代码也安全（无内核级隔离，见 ADR-0014 §D-3）。
"""

from __future__ import annotations

from .job import IS_WINDOWS
from .job import stats as job_stats
from .layout import SANDBOX_ROOT, ensure_dirs
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

__all__ = [
    "ALLOWED_EXECUTABLES",
    "ExecRequest",
    "ExecResult",
    "IS_WINDOWS",
    "POLICIES",
    "SANDBOX_ROOT",
    "SandboxError",
    "SandboxPolicy",
    "Severity",
    "build_env",
    "describe_policies",
    "ensure_dirs",
    "get_policy",
    "is_sensitive_path",
    "job_stats",
    "run",
    "within_root",
]
