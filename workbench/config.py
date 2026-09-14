"""路径与运行配置（fork 自 mogul.config，改名 + 兼容接管）。

数据目录：优先环境变量 WORKBENCH_DATA（兼容 MOGUL_DATA）；
默认 %APPDATA%/AksoWorkbench（Windows）或 ~/.akso-workbench。
首次运行时若本机存在旧版 mogul（Tauri 或 Python 版）的 mogul.db，
自动接管（复制）为初始数据库，历史会话 / 知识 / 密钥全部保留。
"""

from __future__ import annotations

import os
import shutil
import sys
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
# 壳的另一半端口（见 docs/adr/0011）：browser_pool 也要用，故在此单点声明，
# 免得再出现「同一个数字在两个模块各写一遍」——这正是 ADR-0011 要消灭的东西。
CDP_PORT = int(os.environ.get("WORKBENCH_CDP_PORT", "18766"))
CONTROL_PORT = int(os.environ.get("WORKBENCH_CONTROL_PORT", "18767"))

# 壳公布的端口文件（ADR-0011 §D-3）。壳是仲裁者，它把**实际选中**的端口写在这里；
# 服务端只读它用于自证身份（/api/health），不拿它当监听依据（监听端口由壳下发）。
# 路径规则必须与 desktop/shell-state.js 的 DATA_DIR 完全一致，否则会读到另一个目录。
# 注意：具体的 DESKTOP_PORTS_PATH 在下方 RUNTIME_DIR 定义之后才算出来。
DESKTOP_PORTS_NAME = "desktop-ports.json"

# 打包态（PyInstaller）：资源解包到 sys._MEIPASS
FROZEN = getattr(sys, "frozen", False)
_BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))

# 项目根（workbench/ 的上一级）：adapters/、tools/、临时产物目录的锚点
# 打包态下 adapters 作为数据文件打进 _MEIPASS/adapters
PROJECT_ROOT = _BUNDLE_DIR if FROZEN else Path(__file__).resolve().parent.parent
ADAPTERS_DIR = Path(os.environ.get("WORKBENCH_ADAPTERS") or (PROJECT_ROOT / "adapters"))
RUNTIME_DIR = DATA_DIR / "runtime"  # 子进程日志 / 蓝图暂存 / env 导出
DESKTOP_PORTS_PATH = RUNTIME_DIR / DESKTOP_PORTS_NAME  # 壳公布的实际端口（ADR-0011 §D-3）

STATIC_DIR = Path(__file__).parent / "static"


# ---------------------------------------------------------------- 端口公布文件


def read_desktop_ports() -> dict | None:
    """读壳公布的端口文件；不存在或不可解析返回 None。

    为什么服务端也要读它：`/api/health` 要回答「我是谁、我在哪个口、同一批的另两个口在哪」，
    这样壳的就绪探测才能从「有人应答」升级为「是我们的服务在应答」（ADR-0011 §D-4）。
    读不到不算错误——开发者手起 `python -m workbench.main` 时本来就没有壳。
    """
    import json

    try:
        data = json.loads(DESKTOP_PORTS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def describe_service() -> dict:
    """本服务端的身份描述（`/api/health` 与 `/whoami` 共用的同一份口径）。

    `ports` 以壳公布的**实际值**为准、缺失时回落到本进程的环境变量/默认值——
    两者不一致本身就是个 bug（壳必须下发端口，见 ADR-0011 §D-2），故把出处一并报出来。
    """
    ports = {"server": PORT, "cdp": CDP_PORT, "control": CONTROL_PORT}
    published = read_desktop_ports() or {}
    published_ports = published.get("ports") or {}
    source = "default"
    if isinstance(published_ports, dict) and published_ports:
        source = "desktop-ports.json"
        for key in ports:
            value = published_ports.get(key)
            if isinstance(value, int) and value > 0:
                ports[key] = value
    if os.environ.get("WORKBENCH_PORT"):
        source = "env"
        ports["server"] = PORT
    return {
        "ports": ports,
        "portsSource": source,
        "shell": {
            "pid": published.get("pid"),
            "version": published.get("version"),
            "startedAt": published.get("startedAt"),
        } if published else None,
    }
