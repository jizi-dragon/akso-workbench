"""能力诊断（`GET /api/diagnostics`）：把"为什么这个能力没出现"变成稳定 code。

## 为什么需要它

本仓的失败经常**不是报错**，而是"某个能力静默消失"：MCP 配置写错一个字段、
Skill 的 `SKILL.md` 没被扫到、权限配置少一个括号。用户看到的是"模型不会用那个工具"，
而日志里什么都没有。Reasonix 的 `doctor capabilities` 用**稳定 issue code**
（`mcp.start_failed` / `skill.shadowed`）解决这个问题；这里做同一件事的最小版。

## 三条纪律

1. **默认静态**：不启动 MCP、不联网、不 probe。`live=true` 也只读**已在内存里**的
   注册表状态，不主动起进程。
2. **稳定 code**：调用方按 code 分支，不按 message（同 PITFALLS #133）。
3. **脱敏**：只输出 `<data>/…` / `<repo>/…` / `<external>/<basename>`，不输出绝对路径；
   异常只输出类型/计数，不输出依赖方原文（可能含 token）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from . import config, permissions
from .services import mcp, vault
from .skills import library as skills_library

SCHEMA_VERSION = 1
SEVERITIES = ("error", "warning", "info")


def _display_path(path: Path | str) -> str:
    """绝对路径 → `<data>/…` / `<repo>/…` / `<external>/<basename>`。"""
    p = Path(path).resolve(strict=False)
    roots = (
        (Path(config.DATA_DIR).resolve(strict=False), "<data>"),
        (Path(__file__).resolve().parents[1], "<repo>"),
    )
    for root, label in roots:
        try:
            return f"{label}/{p.relative_to(root).as_posix()}"
        except ValueError:
            continue
    return f"<external>/{p.name}"


def _issue(issues: list[dict[str, Any]], code: str, severity: str,
           module: str, message: str, **facts: Any) -> None:
    if severity not in SEVERITIES:
        raise ValueError(f"未知 severity：{severity}")
    issues.append({
        "code": code,
        "severity": severity,
        "module": module,
        "message": message,
        "facts": facts,
    })


def _config(issues: list[dict[str, Any]]) -> dict[str, Any]:
    data_dir = Path(config.DATA_DIR)
    out: dict[str, Any] = {"dataDir": _display_path(data_dir), "exists": data_dir.exists()}
    if not data_dir.exists():
        _issue(issues, "config.data_dir_missing", "error", "config",
               "数据目录不存在（首次启动会创建；创建失败时所有能力都不可用）")
        out["writable"] = False
    else:
        writable = os.access(data_dir, os.W_OK)
        out["writable"] = writable
        if not writable:
            _issue(issues, "config.data_dir_unwritable", "error", "config",
                   "数据目录不可写（设置、会话、Skill 都无法落盘）")
    return out


def _permissions(issues: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        policy = permissions.load()
    except Exception as exc:  # noqa: BLE001 —— 配置坏了要能被看见
        _issue(issues, "permissions.invalid_config", "error", "permissions",
               "权限配置无法解析，所有工具调用会按 Fail-Closed 拒绝",
               errorType=type(exc).__name__)
        return {"mode": "unknown", "rules": 0}
    rules = len(policy.allow) + len(policy.ask) + len(policy.deny)
    return {"mode": policy.mode, "rules": rules,
            "deny": len(policy.deny), "ask": len(policy.ask), "allow": len(policy.allow)}


def _tools(issues: list[dict[str, Any]]) -> dict[str, Any]:
    from .hub import get_hub

    hub = get_hub()
    specs = hub.specs()
    out = {"plugins": len(hub.plugins), "tools": len(specs),
            "failures": len(hub.failures)}
    if hub.failures:
        _issue(issues, "tools.assembly_failures", "error", "tools",
               "有插件在装配期失败，它贡献的工具不会出现", count=len(hub.failures))
    if not specs:
        _issue(issues, "tools.empty", "error", "tools", "工具中枢为空，模型没有任何能力")
    return out


def _skills(issues: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        described = skills_library.describe()
    except Exception as exc:  # noqa: BLE001
        _issue(issues, "skills.library_unreadable", "error", "skills",
               "Skill 共享库无法读取", errorType=type(exc).__name__)
        return {"library": 0, "installed": 0}
    library = described.get("library") or []
    installed = described.get("installed") or []
    if not library:
        _issue(issues, "skills.library_empty", "info", "skills", "共享库为空（还没上传过 Skill）")
    return {"library": len(library), "installed": len(installed)}


def _mcp(issues: list[dict[str, Any]], *, live: bool) -> dict[str, Any]:
    path = mcp.config_path()
    servers, errors = mcp.load_config()
    out: dict[str, Any] = {
        "configPath": _display_path(path),
        "exists": path.exists(),
        "servers": len(servers),
        "errors": len(errors),
    }
    if not path.exists():
        _issue(issues, "mcp.config_missing", "info", "mcp", "还没有 mcp.json（没有配置 MCP server）")
    if errors:
        _issue(issues, "mcp.config_invalid", "warning", "mcp",
               "mcp.json 有条目无法解析，被跳过的 server 不会出现",
               count=len(errors))
    if live:
        try:
            from . import mcp_registry

            state = mcp_registry.get_registry().status()
        except Exception as exc:  # noqa: BLE001
            _issue(issues, "mcp.registry_unavailable", "warning", "mcp",
                   "MCP 注册表状态读不出来", errorType=type(exc).__name__)
        else:
            out["registry"] = {
                "loaded": bool(state.get("loaded")),
                "healthy": int(state.get("healthy") or 0),
                "failing": int(state.get("failing") or 0),
                "tools": int(state.get("tools") or 0),
            }
            if int(state.get("failing") or 0):
                _issue(issues, "mcp.servers_failing", "warning", "mcp",
                       "有 MCP server 当前不健康（工具可能缺失）",
                       failing=int(state.get("failing") or 0))
    return out


def _vault(issues: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        state = vault.status()
    except Exception as exc:  # noqa: BLE001
        _issue(issues, "vault.status_unavailable", "warning", "vault",
               "云端账号库状态读不出来", errorType=type(exc).__name__)
        return {"loggedIn": False}
    logged_in = bool(state.get("loggedIn"))
    if not logged_in:
        _issue(issues, "vault.not_logged_in", "info", "vault",
               "未登录云端账号库（本地账号功能不受影响）")
    return {"loggedIn": logged_in, "baseUrl": state.get("baseUrl") or ""}


def _sandbox(issues: list[dict[str, Any]]) -> dict[str, Any]:
    from .sandbox import capability_matrix, job_stats, layout

    matrix = capability_matrix()
    out = {"platform": matrix.get("platform"), "job": job_stats(),
            "enforced": len(matrix.get("enforced") or ()),
            "notEnforced": len(matrix.get("not_enforced") or ())}
    try:
        described = layout.describe()
        out["root"] = _display_path(described.get("root") or config.DATA_DIR)
    except Exception as exc:  # noqa: BLE001
        _issue(issues, "sandbox.layout_unavailable", "warning", "sandbox",
               "沙箱目录布局读不出来", errorType=type(exc).__name__)
    if matrix.get("platform") == "windows" and not out["job"].get("job_api"):
        _issue(issues, "sandbox.job_unavailable", "warning", "sandbox",
               "Job Object 不可用：超时只能杀直接子进程（进程树可能残留）")
    return out


def collect(*, live: bool = False) -> dict[str, Any]:
    """采集一次诊断。`live=True` 只读内存注册表，不启动任何进程。"""
    issues: list[dict[str, Any]] = []
    modules = {
        "config": _config(issues),
        "permissions": _permissions(issues),
        "tools": _tools(issues),
        "skills": _skills(issues),
        "mcp": _mcp(issues, live=live),
        "vault": _vault(issues),
        "sandbox": _sandbox(issues),
    }
    counts = {level: sum(1 for item in issues if item["severity"] == level)
              for level in SEVERITIES}
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": int(time.time() * 1000),
        "ok": counts["error"] == 0,
        "summary": counts,
        "modules": modules,
        "issues": issues,
        "notes": [
            "默认静态：不启动 MCP、不联网。",
            "live=true 只读已在内存里的注册表，不主动起进程。",
        ],
    }
