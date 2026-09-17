"""沙箱执行的留痕：把一次 `ExecResult` 写进 `sandbox_run` 表。

## 为什么是"注入写入器"而不是直接 import db

与 `plugin_api.py` 对审计的处理**完全同构**（见该文件 §「宿主服务注入」）：
沙箱层不该知道数据库的存在，否则"沙箱能在无依赖的机器上单测"这条就没了，
而且将来换存储（本仓文档里讨论过 SQLite → PostgreSQL）会连带改动执行器。

所以：`install(writer)` 由宿主（`api/` 层）在启动期调用一次；
沙箱自己只调用 `record(...)`，没有装写入器时**静默跳过**（但**不静默失败**——
跳过是"没接审计"，与"审计写失败"是两件事，后者会告警）。

## 记录什么（决策见 ADR-0014 §D-2 E4）

三个事后必答的问题，直接对应三个字段：

| 问题 | 字段 |
|---|---|
| 谁跑的、跑的是什么 | `label` / `argv_summary` / `cwd` / `audit_id` |
| 当时用的是哪份策略 | `policy` / `policy_fingerprint` |
| 树杀干净没有 | `tree_kill` / `timed_out` / `ok` |
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from .runner import ExecResult

logger = logging.getLogger("workbench.sandbox.audit")

#: 命令行摘要的长度上限。**只存摘要**：完整 argv 可能含用户数据，
#: 而审计表的读者是"排查者"不是"复现者"（与本仓 `agent_audit` 只回摘要同一条纪律）。
ARGV_SUMMARY_CHARS = 300

_writer: Callable[[dict[str, Any]], None] | None = None


def install(writer: Callable[[dict[str, Any]], None]) -> None:
    """宿主注入审计写入器（启动期调用一次）。"""
    global _writer
    _writer = writer


def reset() -> None:
    """清空写入器（仅测试用）。"""
    global _writer
    _writer = None


def summarize_argv(argv: list[str]) -> str:
    """命令行摘要：**剥掉可能的凭据片段**后再截断。"""
    from .policy import looks_like_secret

    parts: list[str] = []
    for token in argv[:8]:
        # `--api-key=xxx` / `TOKEN=xxx` 这类形态：只留键名
        if "=" in token and looks_like_secret(token.split("=", 1)[0]):
            parts.append(token.split("=", 1)[0] + "=***")
        else:
            parts.append(token)
    text = " ".join(parts)
    if len(text) > ARGV_SUMMARY_CHARS:
        text = text[:ARGV_SUMMARY_CHARS] + "…"
    return text


def record(result: ExecResult, *, label: str = "", argv: list[str] | None = None,
           audit_id: str = "") -> str:
    """写一条沙箱执行审计。返回本次记录 id（也用作 `sandbox_run.id`）。"""
    if _writer is None:
        # 未接审计：**跳过但不报错**——沙箱要能在没数据库的场景（单测/脚本）下工作。
        return ""
    run_id = uuid.uuid4().hex[:12]
    row = {
        "id": run_id,
        "audit_id": audit_id,
        "label": label,
        "policy": result.policy_name,
        "policy_fingerprint": result.policy_fingerprint,
        "cwd": result.cwd,
        "argv_summary": summarize_argv(argv or []),
        "code": result.code,
        "ok": 1 if result.ok else 0,
        "timed_out": 1 if result.timed_out else 0,
        "duration_ms": int(result.duration_s * 1000),
        "stdout_chars": len(result.stdout),
        "stderr_chars": len(result.stderr),
        "output_truncated": 1 if (result.stdout_truncated or result.stderr_truncated) else 0,
        "tree_kill": result.tree_kill,
        "notes": json.dumps(result.notes, ensure_ascii=False),
        "created_at": int(time.time() * 1000),
    }
    try:
        _writer(row)
    except Exception:  # noqa: BLE001 —— 审计失败绝不影响执行结果（与 plugin_api 同纪律）
        logger.warning("沙箱审计写入失败 id=%s", run_id, exc_info=True)
        return ""
    return run_id


def install_db_writer() -> None:
    """把写入器接到 `db`（由 `api/` 层在启动期调用）。

    这是**唯一**知道 `db` 存在的地方，且它在 `api/` 的调用路径上被触发，
    而不是在 `runner.py` 的 import 期。
    """
    from .. import db  # noqa: PLC0415 —— 刻意本地导入：保持模块可独立单测

    columns = ("id", "audit_id", "label", "policy", "policy_fingerprint", "cwd", "argv_summary",
               "code", "ok", "timed_out", "duration_ms", "stdout_chars", "stderr_chars",
               "output_truncated", "tree_kill", "notes", "created_at")

    def _write(row: dict[str, Any]) -> None:
        db.execute(
            f"INSERT INTO sandbox_run ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            tuple(row.get(c, "") for c in columns),
        )

    install(_write)
