"""壳协作面（`/api/shell/*`）：桌面 UI 请**桌面壳**干活。

## 命名空间为什么独立（2026-09-17 随 P0-1 建立）

这些端点的调用方是**桌面 UI**（账号中心 `accounts.js`），做的是"拉起 Chrome /
读壳落盘的更新状态"——**没有一个碰账号凭据**。它们一度与浏览器扩展的数据通道混在同一段
前缀下，纯粹是历史上"壳 ↔ 扩展 ↔ UI 三者运行态信息集中一处"的编排便利。

分开之后有一条**结构性保证**（而非纪律）：碰凭据的通道与"给 UI 用的"通道在 URL 上不同前缀，
于是各自的守备要求可以按前缀整段声明，而不必靠"记得给某条路由加/不加依赖"。

> **2026-10-09 变更**：浏览器扩展整体从本仓库移除（用户决策：桌面端与扩展不耦合，
> 本仓只需账号管理页来承接云端数据环境）。随之删除的是 `/extension/*` 整段蓝图、
> 访问令牌中间件，以及本模块里唯一为扩展服务的端点 `POST /setup-helper`
> （它代理壳的 `/extension-setup`，用于引导用户装载/重载扩展）。
> `POST /launch-chrome` 与 `POST /update-state` **保留**——它们与扩展无关。

## 边界

本模块的端点**不读凭据、不下发凭据**：它们只与壳的控制服务（基准 `:18767`）或本机进程打交道。
**新增端点前先问一句**：它碰凭据吗？碰 → 它不该放在这里。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from .. import __version__

router = APIRouter(prefix="/api/shell", tags=["shell"])

# Chrome 运行状态探测缓存：tasklist 是一次子进程（实测 ~200ms），点击路径不该每次都付
# 这个代价（它是「桌面点击 → 打开浏览器」链路上除轮询外的第二笔固定开销）。
_PROBE_TTL_RUNNING_MS = 10_000  # 正结果 10s（Chrome 不会无声消失）
_PROBE_TTL_STOPPED_MS = 2_000  # 负结果只信 2s（刚退出时仍要能拉起）
_chrome_probe: dict[str, Any] = {"running": False, "at": 0.0}


def _probe_chrome_running() -> bool:
    """tasklist 探测 Chrome 是否在运行（子进程，实测 ~200ms）。"""
    import subprocess

    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
            capture_output=True, text=True, timeout=10,
        )
        return "chrome.exe" in (out.stdout or "")
    except Exception:  # noqa: BLE001
        return False


def _chrome_running_cached() -> bool:
    """带缓存的探测：它位于「点击 → 打开浏览器」的关键路径上，不该每次都付 200ms。"""
    now = time.monotonic() * 1000
    age = now - float(_chrome_probe["at"])
    ttl = _PROBE_TTL_RUNNING_MS if _chrome_probe["running"] else _PROBE_TTL_STOPPED_MS
    if age < ttl:
        return bool(_chrome_probe["running"])
    running = _probe_chrome_running()
    _chrome_probe.update({"running": running, "at": now})
    return running


@router.post("/launch-chrome")
def launch_chrome() -> dict[str, Any]:
    """确保 Chrome 正在运行（扩展在用户默认 profile 里；未运行则拉起）。

    关键约束：Chrome 未运行时，扩展 SW 不会轮询指令——此时点击轮盘选人，
    指令会滞留队列。故桌面在派发前先探测/拉起 Chrome。
    """
    if _chrome_running_cached():
        return {"launched": False, "running": True}

    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
        ) as key:
            candidates.insert(0, winreg.QueryValueEx(key, "")[0])
    except OSError:
        pass

    for path in candidates:
        if Path(path).exists():
            import subprocess

            subprocess.Popen([path])
            # 立即标记「运行中」：Chrome 启动期间连点不应再拉起第二个实例/多开窗口
            _chrome_probe.update({"running": True, "at": time.monotonic() * 1000})
            return {"launched": True, "running": True, "path": path}
    return {"launched": False, "running": False, "detail": "未找到 chrome.exe"}


@router.post("/update-state")
def update_state() -> dict[str, Any]:
    """读桌面壳落盘的更新状态（见 desktop/shell-state.js）。

    为什么回环读文件而不是 HTTP 探壳：账号中心每 3s 刷新一次，读本地文件更便宜；
    壳没运行时 `live=false`，UI 优雅降级为「只显示版本号」。
    """
    from .routes_update import read_shell_state

    return {"version": __version__, "shell": read_shell_state()}
