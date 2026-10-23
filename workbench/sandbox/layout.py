"""沙箱的目录布局与**工作区权限**：唯一决定"什么算工作区根 / 谁能写"的地方。

## 为什么单独一个模块

`runner.py` 需要回答"这个 `cwd` 是否越界"、"当前权限允不允许这份策略"。
如果这些判断散落在执行器里，将来加第二个消费方（Skill / MCP）就会出现
**两套根目录口径**——那正是本仓"两个工具表并存"那类事故的形态。
所以：**根目录与权限只有一个定义处**，任何人都从这里取。

## 布局

    <工作区根>/                       ← 默认 %APPDATA%/Workbench/sandbox，可在「沙箱」页改
      runs/<run_id>/                  ← 每次执行一个独立工作目录
      staged/<capability_id>/         ← 已安装能力的解包目录

`runs/` 下的目录**默认保留**：出问题时要能进去看现场。清理是显式的
（`tools/db_maintenance.py` 一类的维护动作），不做"跑完就删"——那会把唯一的现场证据删掉。

## 权限模型（2026-10-19）

这是"工作区边界"的落点，不是内核级隔离：

| 项 | 含义 | 强制点 |
|---|---|---|
| `mode` | `read-only` / `read-write` | `read-only` 时**拒绝一切声明会写工作区的策略**（`skill-script` / `mcp-stdio`）；只能跑 `strict-readonly` |
| `allow_network` | 是否允许声明联网的策略运行 | 策略 `allow_network=True` 且开关关闭 ⇒ 拒 |
| `allow_subprocess` | 是否允许策略派生进程 | 策略 `allow_subprocesses=True` 且开关关闭 ⇒ 拒 |
| `root` | 工作区根 | `cwd` 必须在其内（`Path.resolve()` 后比较） |

★ **诚实边界**：`read-only` 禁的是"我们的执行器声明会写的策略"，不是"脚本内核级写不了"。
不受信代码仍以当前用户权限运行；`read-only` 是一道**意图 + 预检**的闸门，
不是 ACL。要真正的只读需要单独用户 / 容器 / 文件系统 ACL，本仓不做。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .. import config

logger = logging.getLogger("workbench.sandbox.layout")

#: 默认工作区根。**保留这个常量**：既有测试与文档引用它，而且它现在是"默认值"。
SANDBOX_ROOT: Path = config.DATA_DIR / "sandbox"

#: 工作区配置（改根 / 改权限）的落点。**放在 DATA_DIR 而不是工作区里**：
#: 换工作区时配置不能跟着搬走，否则"新根还没建好之前就找不到旧配置"。
CONFIG_PATH: Path = config.DATA_DIR / "sandbox-workspace.json"

#: run_id 的形状：`YYYYmmdd-HHMMSS-<6位随机>`。**必须是安全文件名**——
#: 它会被拼进路径，而路径拼接处是历史上出过注入问题的地方
#: （见 `docs/CODEBASE_OVERVIEW` C5：`env_id` 直接进文件名）。
RUN_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")

MODES = ("read-only", "read-write")

#: 系统目录：不拒绝的话，"把工作区设在 C:\\Windows 下"等于给脚本一个系统目录写口。
_FORBIDDEN_ROOTS: tuple[Path, ...] = tuple(
    path for path in (
        Path(os.environ.get("SystemRoot", "C:/Windows")),
        Path(os.environ.get("ProgramFiles", "C:/Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")),
        Path(os.environ.get("ProgramData", "C:/ProgramData")),
        Path("/etc"), Path("/usr"), Path("/bin"), Path("/sbin"),
        Path("/boot"), Path("/dev"), Path("/proc"), Path("/sys"),
    )
    if path.exists()
)


@dataclass(frozen=True)
class WorkspaceSettings:
    root: Path
    mode: str = "read-write"
    allow_network: bool = False
    allow_subprocess: bool = False
    updated_at: int = 0


def _now_ms() -> int:
    return int(time.time() * 1000)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)


def _forbidden(path: Path) -> Path | None:
    for forbidden in _FORBIDDEN_ROOTS:
        try:
            if path == forbidden or forbidden in path.parents:
                return forbidden
        except (OSError, ValueError):
            continue
    return None


def validate_root(value: str | os.PathLike[str]) -> Path:
    """校验并规范化工作区根。不合格**直接抛**（不回落）。

    拒绝的六类：空 / 非绝对路径 / 盘符或文件系统根 / 敏感路径 / 系统目录 / 不可创建或不可写。
    """
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("工作区根不能为空")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("工作区根必须是绝对路径")
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise ValueError(f"工作区根无法解析：{exc}") from exc
    if resolved == Path(resolved.anchor):
        raise ValueError("工作区根不能是盘符 / 文件系统根")
    if config.is_sensitive_path(resolved):
        raise ValueError("工作区根命中敏感路径表（凭据 / 浏览器数据区），换一个目录")
    forbidden = _forbidden(resolved)
    if forbidden is not None:
        raise ValueError(f"工作区根不能放在系统目录下：{forbidden}")
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"工作区根无法创建：{exc}") from exc
    if not resolved.is_dir():
        raise ValueError(f"工作区根不是目录：{resolved}")
    if not os.access(resolved, os.W_OK):
        raise ValueError(f"工作区根不可写：{resolved}")
    return resolved


def _load_raw() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("工作区配置无法读取，回落默认：%s", exc)
        return {}
    return data if isinstance(data, dict) else {}


def _as_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def workspace_settings() -> WorkspaceSettings:
    """当前工作区设置。配置坏了**回落默认**（而不是让整个沙箱不可用）。"""
    raw = _load_raw()
    root_raw = raw.get("root") or str(SANDBOX_ROOT)
    try:
        root = validate_root(root_raw)
    except ValueError as exc:
        logger.warning("工作区配置里的 root 不合格，回落默认：%s", exc)
        root = SANDBOX_ROOT
        root.mkdir(parents=True, exist_ok=True)
    mode = str(raw.get("mode") or "read-write")
    if mode not in MODES:
        logger.warning("工作区配置里的 mode 不认识（%r），回落 read-write", mode)
        mode = "read-write"
    try:
        updated_at = int(raw.get("updatedAt") or 0)
    except (TypeError, ValueError):
        updated_at = 0
    return WorkspaceSettings(
        root=root,
        mode=mode,
        allow_network=_as_bool(raw.get("allowNetwork", False)),
        allow_subprocess=_as_bool(raw.get("allowSubprocess", False)),
        updated_at=updated_at,
    )


def workspace_root() -> Path:
    return workspace_settings().root


def runs_dir() -> Path:
    return workspace_root() / "runs"


def staged_dir_root() -> Path:
    return workspace_root() / "staged"


def save_workspace(
    *,
    root: str | os.PathLike[str] | None = None,
    mode: str | None = None,
    allow_network: bool | None = None,
    allow_subprocess: bool | None = None,
) -> WorkspaceSettings:
    """保存工作区设置。**只校验传入的字段**，其余沿用当前值。"""
    current = workspace_settings()
    new_root = validate_root(root) if root is not None else current.root
    new_mode = current.mode if mode is None else str(mode)
    if new_mode not in MODES:
        raise ValueError(f"权限模式只能是 {' / '.join(MODES)}")
    payload = {
        "root": str(new_root),
        "mode": new_mode,
        "allowNetwork": current.allow_network if allow_network is None else bool(allow_network),
        "allowSubprocess": current.allow_subprocess if allow_subprocess is None else bool(allow_subprocess),
        "updatedAt": _now_ms(),
    }
    _atomic_write(CONFIG_PATH, json.dumps(payload, ensure_ascii=False, indent=2))
    return workspace_settings()


def reset_workspace() -> WorkspaceSettings:
    """恢复默认工作区与保守权限。"""
    try:
        CONFIG_PATH.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ValueError(f"无法重置工作区配置：{exc}") from exc
    return workspace_settings()


def permission_error(policy) -> str:
    """当前工作区权限是否允许这份策略。返回空串 = 允许；否则是拒绝理由。"""
    settings = workspace_settings()
    if getattr(policy, "writes", False) and settings.mode == "read-only":
        return (
            f"策略 {policy.name} 会写工作区，而当前工作区是「只读」模式；"
            "先在「沙箱 → 工作区与权限」里改成「读写」"
        )
    if policy.allow_network and not settings.allow_network:
        return (
            f"策略 {policy.name} 声明需要联网，而工作区权限里「允许联网」是关闭的；"
            "先在「沙箱 → 工作区与权限」里打开"
        )
    if policy.allow_subprocesses and not settings.allow_subprocess:
        return (
            f"策略 {policy.name} 声明需要派生进程，而工作区权限里「允许派生进程」是关闭的；"
            "先在「沙箱 → 工作区与权限」里打开"
        )
    return ""


def ensure_dirs() -> None:
    """建好工作区目录树（幂等）。启动期调用一次即可。"""
    for directory in (workspace_root(), runs_dir(), staged_dir_root()):
        directory.mkdir(parents=True, exist_ok=True)


def new_run_id() -> str:
    """生成一个新的 run id。"""
    from datetime import datetime

    return f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


def run_dir(run_id: str, *, create: bool = True) -> Path:
    """取某次执行的工作目录。**先校验 run_id 形状**，不合法直接拒。"""
    if not RUN_ID_RE.match(run_id or ""):
        raise ValueError(f"run_id 形状不合法（拒绝拼进路径）：{run_id!r}")
    path = runs_dir() / run_id
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def staged_dir(capability_id: str, *, create: bool = True) -> Path:
    """取某个已安装能力的解包目录。能力 id 只允许 `[a-z0-9._-]`。"""
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", capability_id or ""):
        raise ValueError(f"capability_id 不合法（拒绝拼进路径）：{capability_id!r}")
    path = staged_dir_root() / capability_id
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def describe_workspace() -> dict:
    """给「沙箱 → 工作区与权限」页/API 的形状。"""
    settings = workspace_settings()
    return {
        "root": str(settings.root),
        "defaultRoot": str(SANDBOX_ROOT),
        "mode": settings.mode,
        "allowNetwork": settings.allow_network,
        "allowSubprocess": settings.allow_subprocess,
        "exists": settings.root.is_dir(),
        "writable": settings.root.is_dir() and os.access(settings.root, os.W_OK),
        "configPath": str(CONFIG_PATH),
        "updatedAt": settings.updated_at,
        "modes": list(MODES),
        "enforced": [
            "cwd 必须在工作区根内",
            "只读模式拒绝写策略",
            "联网 / 派生进程按开关预检",
            "敏感路径与系统目录拒绝",
        ],
        "honest_limit": (
            "只读模式禁的是「声明会写工作区的策略」，不是内核级只读。"
            "脚本仍以当前用户权限运行；真正的只读需要单独用户 / 容器 / ACL。"
        ),
    }


def describe() -> dict:
    """给管理页/API 的形状。"""
    settings = workspace_settings()
    return {
        "root": str(settings.root),
        "default_root": str(SANDBOX_ROOT),
        "runs_dir": str(runs_dir()),
        "staged_dir": str(staged_dir_root()),
        "mode": settings.mode,
        "allow_network": settings.allow_network,
        "allow_subprocess": settings.allow_subprocess,
        "run_count": sum(1 for p in runs_dir().iterdir() if p.is_dir()) if runs_dir().exists() else 0,
        "staged_count": (
            sum(1 for p in staged_dir_root().iterdir() if p.is_dir())
            if staged_dir_root().exists() else 0
        ),
    }

def cleanup_runs(*, keep_days: int = 30, keep_count: int = 200,
                 max_bytes: int = 512 * 1024 * 1024, dry_run: bool = True) -> dict:
    """按保留策略清理 `runs/`（**默认只预览**）。

    三个维度任一超限都从最旧的开始删：
    - `keep_days`：mtime 早于 now-keep_days 的目录；
    - `keep_count`：最多保留多少个 run；
    - `max_bytes`：总字节上限。

    `staged/` 与工作区根不在范围内；`RUN_ID_RE` 之外的目录也不碰（宁可留，不可误删）。
    """
    if keep_days < 0 or keep_count < 0 or max_bytes < 0:
        raise ValueError("keep_days / keep_count / max_bytes 必须 ≥ 0")
    base = runs_dir()
    if not base.exists():
        return {"deleted": [], "freedBytes": 0, "kept": 0, "dryRun": dry_run}
    entries: list[tuple[float, Path, int]] = []
    for path in base.iterdir():
        if not path.is_dir() or not RUN_ID_RE.match(path.name):
            continue
        size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        entries.append((path.stat().st_mtime, path, size))
    entries.sort(key=lambda item: item[0])
    now = time.time()
    cutoff = now - keep_days * 86400
    doomed: set[Path] = {path for mtime, path, _ in entries if keep_days > 0 and mtime < cutoff}

    remaining = [item for item in entries if item[1] not in doomed]
    if len(remaining) > keep_count:
        doomed.update(path for _, path, _ in remaining[: len(remaining) - keep_count])
        remaining = remaining[len(remaining) - keep_count:]

    total = sum(size for _, path, size in remaining if path not in doomed)
    for _mtime, path, size in remaining:
        if total <= max_bytes:
            break
        if path in doomed:
            continue
        doomed.add(path)
        total -= size

    freed = sum(size for _, path, size in entries if path in doomed)
    deleted: list[str] = []
    errors: list[dict[str, str]] = []
    for _, path, _size in entries:
        if path not in doomed:
            continue
        deleted.append(path.name)
        if dry_run:
            continue
        try:
            shutil.rmtree(path)
        except OSError as exc:
            errors.append({"run": path.name, "error": f"{type(exc).__name__}: {exc}"})
    return {
        "deleted": deleted,
        "freedBytes": freed,
        "kept": len(entries) - len(doomed),
        "dryRun": dry_run,
        "errors": errors,
    }
