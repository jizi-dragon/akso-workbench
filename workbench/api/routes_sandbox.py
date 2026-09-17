"""沙箱管理面：策略清单 / 运行期状态 / 执行审计。

决策见 [ADR-0014](../../docs/adr/0014-sandbox-for-untrusted-code.md) §D-5：
"沙箱策略是**声明**，运行期必须能看能停"——本模块就是那个"能看"。

## 本模块**不提供**"执行一段任意代码"的端点

刻意的。执行入口只应该出现在**有明确业务语义**的地方（将来的 Skill 调用、MCP stdio），
而不是一个通用的 `/api/sandbox/exec`——那等于给本机任意进程开一个"跑任意代码"的接口，
而本仓服务端**只监听回环、无认证**（见 `docs/CODEBASE_OVERVIEW.md` R1）。
执行能力由消费方按需接入，不在这里开通用口子。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from .. import db
from ..sandbox import IS_WINDOWS, describe_policies, job_stats, layout
from ..sandbox.runner import ALLOWED_EXECUTABLES

router = APIRouter(prefix="/api/sandbox", tags=["sandbox"])


@router.get("/policies")
def policies() -> dict:
    """全部策略 + 每条**真正被强制**的项。

    `enforced` 与声明字段是两回事：本沙箱不提供内核级隔离，
    所以"声明允许联网"≠"技术上拦得住"。界面必须把这两者分开显示，否则会给人虚假安全感。
    """
    return {
        "policies": describe_policies(),
        "enforced_note": (
            "本沙箱**不是安全边界**：只保证进程树可终止、凭据不注入、目录不越界、"
            "输出可截断、执行可审计；不提供内核级隔离（见 ADR-0014 §D-3）。"
            "除 /policies 里 enforced 列出的项外，其余字段只是**声明**。"
        ),
    }


@router.get("/status")
def status() -> dict:
    """运行期状态：平台能力、目录布局、当前持有的 Job 数。"""
    return {
        "platform": "windows" if IS_WINDOWS else "posix",
        "job": job_stats(),
        "layout": layout.describe(),
        "executables_allowed": sorted(ALLOWED_EXECUTABLES),
    }


@router.get("/runs")
def runs(limit: int = 50) -> dict:
    """最近的沙箱执行审计。

    与 `/api/agent/audit` 的分工：那边是**工具调用**（谁调了什么工具），
    这边是**执行**（沙箱里跑了什么、用的哪份策略、树杀干净没有）。
    两层用 `audit_id` 串联，但独立成表——见迁移 19 的说明。
    """
    rows = db.query(
        "SELECT id, audit_id, label, policy, policy_fingerprint, code, ok, timed_out, "
        "duration_ms, output_truncated, tree_kill, argv_summary, created_at "
        "FROM sandbox_run ORDER BY created_at DESC LIMIT ?",
        (min(limit, 500),),
    )
    return {"runs": rows}


@router.get("/runs/{run_id}")
def run_detail(run_id: str) -> dict:
    row = db.query_one("SELECT * FROM sandbox_run WHERE id = ?", (run_id,))
    if not row:
        raise HTTPException(404, f"沙箱执行记录不存在：{run_id}")
    if row.get("notes"):
        try:
            row["notes"] = json.loads(row["notes"])
        except (ValueError, TypeError):
            pass
    return row


@router.get("/selfcheck")
def selfcheck() -> dict:
    """沙箱自检：策略可加载、目录可建、Job Object 可用性。

    `job_api=False` **不是**错误（非 Windows 平台正常走进程组），但要在界面上说明，
    因为它意味着"杀整棵树"的强度不同。
    """
    problems: list[str] = []
    try:
        policies_list = describe_policies()
        if not policies_list:
            problems.append("没有加载到任何沙箱策略")
    except Exception as exc:  # noqa: BLE001
        policies_list = []
        problems.append(f"策略加载失败：{type(exc).__name__}: {exc}")
    try:
        layout.ensure_dirs()
        desc = layout.describe()
    except Exception as exc:  # noqa: BLE001
        desc = {}
        problems.append(f"沙箱目录不可建：{type(exc).__name__}: {exc}")

    jobs = job_stats()
    if IS_WINDOWS and not jobs.get("job_api"):
        problems.append("Windows 上 Job Object 不可用——进程树可能杀不干净")

    return {
        "ok": not problems,
        "policies": len(policies_list),
        "platform": "windows" if IS_WINDOWS else "posix",
        "job_api": jobs.get("job_api"),
        "layout": desc,
        "problems": problems,
    }
