"""壳协作面（`/api/shell/*`）：桌面 UI 请**桌面壳**干活，与扩展协议无关。

## 为什么从 `routes_extension.py` 搬出来（2026-09-17，随 P0-1 一起）

这三个端点原先挂在 `/extension/*` 上，但它们的调用方是**桌面 UI**（账号中心 `accounts.js`
与页内轮盘 `wheel-picker.html`），做的是"拉起 Chrome / 请壳打开 chrome://extensions /
读壳落盘的更新状态"——**没有一个碰账号凭据**。它们被放在扩展蓝图里，纯粹是历史上
"壳 ↔ 扩展 ↔ UI 三者运行态信息集中一处"的编排便利。

P0-1 给 `/extension/*` 加上访问令牌后，这个编排便利变成了**真实冲突**：
- 令牌只发给**扩展**（它经 `/extension/hello` 自举领取）；
- 桌面 UI 是**同源页面**（`http://127.0.0.1:18765`），它**拿不到也不该拿到**那个令牌——
  页面里放令牌等于把它交给任何能读页面/DOM 的东西；
- 于是"UI 要调"与"扩展专属"必须在**URL 命名空间**上分开，而不是靠"记得给某条路由加/不加依赖"。

分开之后有一条**结构性保证**（而非纪律）：`/extension/*` 下只剩"扩展与桌面之间的数据与指令通道"，
于是"这个前缀整段需要令牌"这句话才是真的、可机检的（`verify_packaging` 的 P10）。
原先那三条留在原地的话，就只能给它们开豁免——而豁免一开，
"`/extension/*` 全部需要令牌"就退化成一句需要人工维护的清单。

## 边界

本模块的端点**不读凭据、不下发凭据**：它们只与壳的控制服务（基准 `:18767`）或本机进程打交道。
**新增端点前先问一句**：它碰凭据吗？碰 → 它属于 `routes_extension.py`（需要令牌）；
不碰且是给桌面 UI 用的 → 放这里。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from .. import __version__, config

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


# 桌面壳的会话控制服务（Electron 主进程内）：扩展安装引导走它执行。
# 端口经 config 单点声明（ADR-0011 §D-2）——壳会把实际选中的端口下发到
# WORKBENCH_CONTROL_PORT，故此处在调用时读，**不做模块级常量快照**。
def control_base() -> str:
    return f"http://127.0.0.1:{config.CONTROL_PORT}"


@router.post("/update-state")
def update_state() -> dict[str, Any]:
    """读桌面壳落盘的更新状态（见 desktop/shell-state.js）。

    为什么回环读文件而不是 HTTP 探壳：账号中心每 3s 刷新一次，读本地文件更便宜；
    壳没运行时 `live=false`，UI 优雅降级为「只显示版本号」。
    """
    from .routes_update import read_shell_state

    return {"version": __version__, "shell": read_shell_state()}


@router.post("/setup-helper")
def setup_helper(body: dict[str, Any] | None = None) -> dict[str, Any]:
    """请桌面壳执行扩展安装引导：打开 chrome://extensions + 打开随包扩展目录 + 弹步骤说明。

    为什么不是「一键装好」：Chrome 在 Windows 上禁止非商店扩展直接安装，策略强制安装
    （ExtensionInstallForcelist）在 HKCU 下普遍不生效、自托管 update_url 亦常见失败，
    且本仓库无签名私钥 → 用户必须亲手点「加载已解压的扩展程序」。详见 docs/EXTENSION-INSTALL.md。

    `mode="reload"`：桌面端升版后，扩展文件被安装包覆盖但 Chrome 仍持有旧版内存镜像，
    此时引导用户点一次「重新加载」（同一套壳能力，仅文案不同）。
    壳不在时返回 ok=false，UI 退化为纯文字指引。
    """
    import urllib.request

    mode = str((body or {}).get("mode") or "install")
    try:
        req = urllib.request.Request(
            f"{control_base()}/extension-setup",
            data=json.dumps({"mode": mode}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            payload = json.loads(resp.read() or b"{}")
        return {"ok": True, "helper": payload}
    except Exception as exc:  # noqa: BLE001 —— 壳不可用时 UI 退化为文字指引
        return {"ok": False, "error": f"桌面壳未响应：{exc}"}
