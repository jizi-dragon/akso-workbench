r"""Skill 的目录布局：**共享库**与**已安装**两处，都不在仓库里。

## 为什么都不进 git（用户 2026-09-17 明确要求）

Skill 是**运行期资产**：同事上传的、来自客户现场的、可能含内部数据的。
它们和账号库、洞察产物同类，属于"本机数据"，不属于"产品源码"。
所以两处都落在 `DATA_DIR`（`%APPDATA%\Workbench`）之下——`.gitignore` 已覆盖整个数据目录，
**结构上不可能被 `git add -A` 带进仓库**。

    <DATA_DIR>/skills/
      library/                       ← 共享库（上传到这里，安装从这里取）
        <capability_id>/
          <version>/                 ← 保留历史版本：出问题能回滚，也能对比
            SKILL.md                 ← 原文（人可读）
            source.zip               ← 上传原件（可复核、可重新解包）
            manifest.json            ← 解析后的规范化清单（解析结果只算一次）
      installed/                     ← 已安装（真正生效的那些）
        <capability_id>/
          current                    ← 指向某个版本的指针文件（写纯文本，不用 symlink）

## 与"云服务器上的共享库"的关系

用户说明有一台云服务器，将来要把共享库放上去。所以**"库"被抽成一层接口**
（`library.py` 里的读写函数只依赖本模块的路径约定），换成远端实现时
`library/` 这套目录结构可以原样搬到服务器上（同形），上层代码不动。

**但本期只实现本地**——用户选的是"先做本地共享库"。
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import config

#: Skill 数据根（在 `DATA_DIR` 下，**不进仓库**）。
SKILLS_ROOT: Path = config.DATA_DIR / "skills"

#: 共享库：上传落在这里。
LIBRARY_DIR: Path = SKILLS_ROOT / "library"

#: 已安装：真正生效的能力。
INSTALLED_DIR: Path = SKILLS_ROOT / "installed"

#: 能力 id 与版本号的形状（都会被拼进路径，必须严格）。
#: 与 `sandbox/layout.py` 同一条纪律：形状不合法一律拒，不做"清理后再用"。
CAPABILITY_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def ensure_dirs() -> None:
    for d in (SKILLS_ROOT, LIBRARY_DIR, INSTALLED_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _safe(part: str, pattern: re.Pattern[str], what: str) -> str:
    if not pattern.fullmatch(part or ""):
        raise ValueError(f"{what} 形状不合法（拒绝拼进路径）：{part!r}")
    return part


def library_version_dir(capability_id: str, version: str, *, create: bool = True) -> Path:
    """共享库里某个能力的某个版本目录。"""
    _safe(capability_id, CAPABILITY_ID_RE, "capability_id")
    _safe(version, VERSION_RE, "version")
    path = LIBRARY_DIR / capability_id / version
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def installed_dir(capability_id: str, *, create: bool = True) -> Path:
    """已安装目录的根（版本由 `current` 指针决定，见 `installed_current`）。"""
    _safe(capability_id, CAPABILITY_ID_RE, "capability_id")
    path = INSTALLED_DIR / capability_id
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def installed_current(capability_id: str) -> Path | None:
    """读已安装能力当前生效的**版本目录**。

    刻意用**纯文本指针文件**而不是符号链接：Windows 上建 symlink 需要
    开发者模式或管理员权限（本仓的安装包是免管理员的，见 `README`），
    用 symlink 会让"安装"在某些机器上直接失败。
    """
    ptr = INSTALLED_DIR / capability_id / "current"
    if not ptr.is_file():
        return None
    version = ptr.read_text(encoding="utf-8").strip()
    try:
        _safe(version, VERSION_RE, "version")
    except ValueError:
        return None  # 指针被改坏 → 当作没安装，不抛（调用方会走"未安装"分支）
    path = LIBRARY_DIR / capability_id / version
    return path if path.is_dir() else None


def write_current(capability_id: str, version: str) -> None:
    """把已安装指针指向某个版本（安装/回滚都走它）。"""
    _safe(capability_id, CAPABILITY_ID_RE, "capability_id")
    _safe(version, VERSION_RE, "version")
    installed_dir(capability_id)
    ptr = INSTALLED_DIR / capability_id / "current"
    ptr.write_text(version, encoding="utf-8", newline="\n")


def clear_installed(capability_id: str) -> None:
    """卸载：删掉 `current` 指针。

    **刻意不删共享库里的版本目录**——卸载是"不再生效"，不是"把库里的东西删了"。
    库里的东西要删得走"从共享库删除"这个独立动作（用户可能只是暂时不用）。
    """
    ptr = INSTALLED_DIR / capability_id / "current"
    if ptr.exists():
        ptr.unlink()


def describe() -> dict:
    ensure_dirs()
    abilities = sorted(p.name for p in LIBRARY_DIR.iterdir() if p.is_dir()) if LIBRARY_DIR.exists() else []
    installed = sorted(p.name for p in INSTALLED_DIR.iterdir()
                       if p.is_dir() and (p / "current").is_file()) if INSTALLED_DIR.exists() else []
    return {
        "root": str(SKILLS_ROOT),
        "library_dir": str(LIBRARY_DIR),
        "installed_dir": str(INSTALLED_DIR),
        "library_count": len(abilities),
        "installed_count": len(installed),
        "in_repo": False,
        "note": "Skill 是运行期资产，落在 DATA_DIR 下，不入 git（用户 2026-09-17 明确要求）",
    }
