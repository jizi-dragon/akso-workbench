"""洞察产物工作区：用户可配的根目录 + 每次运行一格的落盘布局 + 自证覆盖度的 manifest。

> 2026-09-14：`inventory`/`understand` 下线后，`coverage_for_understand()` 一并删除；
> 本模块的 `run_dir()` / `coverage_status()` 等保留为**通用产物契约**（spider 已在用
> `build_coverage`/`build_manifest`/`write_manifest`）。

对应 docs/INSIGHT-SPEC.md §4：

    <工作区根目录>/            ← 用户可配（设置键 insight_workspace_root）
    └── <对象code>/            ← 按对象归集，同一对象的历次产物可比对
        └── 20260913-153000/   ← 每次运行一格（Windows 合法字符，可排序）
            ├── manifest.json
            ├── spider-network.json / spider-network.md / spider-network.drawio
            └── step3-attribution.md

两条硬要求（规格 §4.2/§4.3）：

1. 对象 code 来自用户输入，进入路径前必须过 :func:`safe_path_component`，落点再用
   :func:`resolve_within` 断言——实测未做保护时 `../../x` 会写到工作区之外。
2. 产物必须**自证覆盖度**：`coverage.status` 取 complete / partial / degraded 三态，
   取代原先「无条件 ok:True」的布尔谎报。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .... import config
from ...paths import resolve_within, safe_path_component

SETTING_KEY = "insight_workspace_root"
MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA_VERSION = 1
TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"

WORKSPACE_DIR_NAME = "洞察"
APP_SUBDIR = "Workbench"
LEGACY_APP_SUBDIR = "AksoWorkbench"  # 2026-09-16 更名前的目录名（已存在则继续沿用）


# ---------------------------------------------------------------- 根目录


def default_workspace_root() -> Path:
    """默认工作区根：``<我的文档>/Workbench/洞察``。

    取不到「我的文档」时回落到数据目录——绝不因为取不到桌面路径而让洞察无法落盘。

    **更名兼容（2026-09-16）**：若本机已存在 ``<我的文档>/AksoWorkbench/洞察`` 而新目录还没有，
    就**继续用旧目录**——已落盘的产物、用户已经习惯的路径，不该因为改个品牌名而"消失"
    （表现为"我的产物去哪了"）。旧目录被用户自行清掉后，下一次自动落到新名。
    """
    candidates = [Path.home() / "Documents", Path.home() / "文档"]
    for candidate in candidates:
        try:
            if not candidate.is_dir():
                continue
            legacy = candidate / LEGACY_APP_SUBDIR / WORKSPACE_DIR_NAME
            fresh = candidate / APP_SUBDIR / WORKSPACE_DIR_NAME
            if legacy.is_dir() and not fresh.is_dir():
                return legacy
            return fresh
        except OSError:
            continue
    return config.DATA_DIR / WORKSPACE_DIR_NAME


def workspace_root() -> Path:
    """当前工作区根：设置表优先，未配置或读表失败时回落默认值。"""
    raw = ""
    try:
        from ...storage import get_setting

        raw = (get_setting(SETTING_KEY) or "").strip()
    except Exception:  # noqa: BLE001 —— 无 DB（纯离线调用）时不该炸
        raw = ""
    return Path(raw) if raw else default_workspace_root()


def set_workspace_root(path: str | Path) -> str:
    """设置工作区根（会做校验并尝试创建）。返回归一化后的绝对路径字符串。

    拒绝：空值、相对路径、已存在但是**文件**的路径、无法创建的路径。
    """
    text = str(path or "").strip()
    if not text:
        raise ValueError("工作区根目录不能为空")
    target = Path(text).expanduser()
    if not target.is_absolute():
        raise ValueError(f"工作区根目录必须是绝对路径：{text}")
    if target.exists() and not target.is_dir():
        raise ValueError(f"工作区根目录是一个文件，不是目录：{target}")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"工作区根目录无法创建：{target}（{exc}）") from exc

    from ...storage import set_setting

    resolved = str(target.resolve())
    set_setting(SETTING_KEY, resolved)
    return resolved


def clear_workspace_root() -> None:
    """清掉自定义根，回到默认值。"""
    from ...storage import set_setting

    set_setting(SETTING_KEY, "")


# ---------------------------------------------------------------- 运行目录


def run_dir(object_code: Any, *, root: Path | str | None = None,
            at: datetime | None = None) -> Path:
    """本次运行的工作目录：``<root>/<对象code>/<时间戳>/``（已创建）。"""
    base = Path(root) if root is not None else workspace_root()
    stamp = (at or datetime.now()).strftime(TIMESTAMP_FORMAT)
    target = resolve_within(base, safe_path_component(object_code), stamp)
    target.mkdir(parents=True, exist_ok=True)
    return target


# ---------------------------------------------------------------- 覆盖度三态


def coverage_status(*, total: int, succeeded: int, failures: int = 0) -> str:
    """三态覆盖度：``complete`` / ``partial`` / ``degraded``。

    - 一个都没成功 → ``degraded``（不可用）；
    - 成功但少于目标数，或存在失败项 → ``partial``（可用但残缺，必须让用户看见）；
    - 全部成功且无失败 → ``complete``。
    """
    if succeeded <= 0:
        return "degraded"
    if failures or succeeded < total:
        return "partial"
    return "complete"


def build_coverage(*, total: int, succeeded: int, failures: list | None = None,
                   **counters: Any) -> dict[str, Any]:
    """构造 manifest 里的 ``coverage`` 段。``counters`` 为额外计数（如 statesRead）。"""
    fails = failures or []
    coverage: dict[str, Any] = {
        "status": coverage_status(total=total, succeeded=succeeded, failures=len(fails)),
        "total": total,
        "succeeded": succeeded,
        "failureCount": len(fails),
    }
    coverage.update({k: v for k, v in counters.items() if v is not None})
    return coverage


# ---------------------------------------------------------------- manifest


def build_manifest(*, command: str, object_code: Any, coverage: dict[str, Any],
                   artifacts: list[Path] | None = None, environment: dict[str, Any] | None = None,
                   failures: list | None = None, platform_version: str | None = None,
                   notes: list | None = None,
                   generated_at: datetime | None = None) -> dict[str, Any]:
    """构造 manifest：让产物自证「这是什么、覆盖到哪、有什么失败的、边界在哪」。

    ``failures`` = **我们没读到**（读失败）；``notes`` = **平台本身的边界**
    （未启用生命周期、没给对象编码所以没展开、这个状态本来就没配动作）。两者分开记，
    否则「没配」与「没读到」在产物里长得一模一样。
    """
    import hashlib

    files = []
    for path in artifacts or []:
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        files.append({
            "name": path.name,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "command": command,
        "objectCode": str(object_code),
        "generatedAt": (generated_at or datetime.now()).isoformat(timespec="seconds"),
        "environment": environment or {},
        "platformVersion": platform_version,
        "coverage": coverage,
        "artifacts": files,
        "failures": list(failures or []),
        "notes": list(notes or []),
    }


def write_manifest(directory: Path, manifest: dict[str, Any]) -> Path:
    """原子写 manifest（临时文件 + rename）。"""
    target = resolve_within(directory, MANIFEST_NAME)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
                   encoding="utf-8")
    tmp.replace(target)
    return target


def read_manifest(directory: Path) -> dict[str, Any] | None:
    """读 manifest；不存在或不可解析返回 None（历史目录允许没有 manifest）。"""
    path = Path(directory) / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
