"""沙箱的目录布局：**唯一**决定"什么算沙箱根"的地方。

## 为什么单独一个模块

`runner.py` 需要回答"这个 `cwd` 是否越界"。如果这个判断散落在执行器里，
将来加第二个消费方（Skill / MCP）就会出现**两套根目录口径**——那正是本仓
"两个工具表并存"那类事故的形态（见 `docs/PLUGIN-SDK.md` §3 的收口说明）。
所以：**根目录只有一个定义处**，任何人都从这里取。

## 布局

    <DATA_DIR>/sandbox/                  ← 沙箱根（SANDBOX_ROOT）
      runs/<run_id>/                     ← 每次执行一个独立工作目录
      staged/<capability_id>/            ← 已安装能力的解包目录（下一期 Skill 用）

`runs/` 下的目录**默认保留**：出问题时要能进去看现场。清理是显式的
（`tools/db_maintenance.py` 一类的维护动作），不做"跑完就删"——那会把唯一的现场证据删掉。
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from .. import config

#: 沙箱根。**所有沙箱执行的工作目录必须在这里面**（`runner.py` 会强制校验）。
SANDBOX_ROOT: Path = config.DATA_DIR / "sandbox"

#: 每次执行的工作区父目录。
RUNS_DIR: Path = SANDBOX_ROOT / "runs"

#: 已安装能力的解包目录（下一期 Skill 用；本期只建目录，不消费）。
STAGED_DIR: Path = SANDBOX_ROOT / "staged"

#: run_id 的形状：`YYYYmmdd-HHMMSS-<6位随机>`。**必须是安全文件名**——
#: 它会被拼进路径，而路径拼接处是历史上出过注入问题的地方
#: （见 `docs/CODEBASE_OVERVIEW.md` C5：`env_id` 直接进文件名）。
RUN_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")


def ensure_dirs() -> None:
    """建好沙箱目录树（幂等）。启动期调用一次即可。"""
    for d in (SANDBOX_ROOT, RUNS_DIR, STAGED_DIR):
        d.mkdir(parents=True, exist_ok=True)


def new_run_id() -> str:
    """生成一个新的 run id。"""
    from datetime import datetime

    return f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


def run_dir(run_id: str, *, create: bool = True) -> Path:
    """取某次执行的工作目录。**先校验 run_id 形状**，不合法直接拒。"""
    if not RUN_ID_RE.match(run_id or ""):
        raise ValueError(f"run_id 形状不合法（拒绝拼进路径）：{run_id!r}")
    path = RUNS_DIR / run_id
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def staged_dir(capability_id: str, *, create: bool = True) -> Path:
    """取某个已安装能力的解包目录。能力 id 只允许 `[a-z0-9._-]`。"""
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", capability_id or ""):
        raise ValueError(f"capability_id 不合法（拒绝拼进路径）：{capability_id!r}")
    path = STAGED_DIR / capability_id
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def describe() -> dict:
    """给管理页/API 的形状。"""
    return {
        "root": str(SANDBOX_ROOT),
        "runs_dir": str(RUNS_DIR),
        "staged_dir": str(STAGED_DIR),
        "run_count": sum(1 for p in RUNS_DIR.iterdir() if p.is_dir()) if RUNS_DIR.exists() else 0,
        "staged_count": sum(1 for p in STAGED_DIR.iterdir() if p.is_dir()) if STAGED_DIR.exists() else 0,
    }
