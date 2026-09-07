"""路径与运行配置（fork 自 mogul.config，改名 + 兼容接管）。

数据目录：优先环境变量 WORKBENCH_DATA（兼容 MOGUL_DATA）；
默认 %APPDATA%/AksoWorkbench（Windows）或 ~/.akso-workbench。
首次运行时若本机存在旧版 mogul（Tauri 或 Python 版）的 mogul.db，
自动接管（复制）为初始数据库，历史会话 / 知识 / 密钥全部保留。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

APP_NAME = "AksoWorkbench"
LEGACY_IDENTIFIER = "com.example.mogulsimulator"  # 旧 Tauri 版
LEGACY_PY_NAME = "MogulWorkbench"  # 旧 Python 版（mogul_simulator）


def app_data_dir() -> Path:
    env = os.environ.get("WORKBENCH_DATA") or os.environ.get("MOGUL_DATA")
    if env:
        return Path(env)
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_NAME
    return Path.home() / ".akso-workbench"


DATA_DIR = app_data_dir()
DB_PATH = Path(
    os.environ.get("WORKBENCH_DB")
    or os.environ.get("MOGUL_DB")
    or (DATA_DIR / "workbench.db")
)


def legacy_db_candidates() -> list[Path]:
    """旧版数据库的可能位置（旧 Tauri 版 + 旧 Python 版 mogul）。"""
    candidates: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / LEGACY_PY_NAME / "mogul.db")
        candidates.append(Path(appdata) / LEGACY_IDENTIFIER / "mogul.db")
    candidates.append(Path.home() / LEGACY_IDENTIFIER / "mogul.db")
    return candidates


def bootstrap_database() -> Path:
    """确保数据库就绪：优先复用旧库数据。返回最终 DB 路径。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        for legacy in legacy_db_candidates():
            if legacy.exists():
                shutil.copy2(legacy, DB_PATH)
                for suffix in ("-wal", "-shm"):
                    side = legacy.with_name(legacy.name + suffix)
                    if side.exists():
                        shutil.copy2(side, DB_PATH.with_name(DB_PATH.name + suffix))
                break
    return DB_PATH


HOST = os.environ.get("WORKBENCH_HOST") or os.environ.get("MOGUL_HOST", "127.0.0.1")
PORT = int(os.environ.get("WORKBENCH_PORT") or os.environ.get("MOGUL_PORT", "18765"))

# 项目根（workbench/ 的上一级）：adapters/、tools/、临时产物目录的锚点
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ADAPTERS_DIR = Path(os.environ.get("WORKBENCH_ADAPTERS") or (PROJECT_ROOT / "adapters"))
RUNTIME_DIR = DATA_DIR / "runtime"  # 子进程日志 / 蓝图暂存 / env 导出

STATIC_DIR = Path(__file__).parent / "static"
