"""更新面路由（版本号 / 自动更新状态 / 检查更新）。

架构分工：**更新动作只属于桌面壳**（Electron + electron-updater），
Python 服务不自己下载安装包——它做两件事：
1. 把「当前版本 + 壳的更新状态」整理成一份 UI 可直接渲染的数据；
2. 把「检查更新 / 立即安装」的请求代理给壳的控制服务（127.0.0.1:18767）。

数据来源是壳写的 `shell-state.json`（见 desktop/shell-state.js），而不是每次请求都去
HTTP 探壳：账号中心每 3s 刷新一次轮询，读一个本地文件比跨端口请求更便宜，
且壳没运行时也能优雅降级（`live=false` + 陈旧判定）。
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Any

from fastapi import APIRouter, Request

from .. import __version__, config

logger = logging.getLogger("workbench.update")

router = APIRouter(prefix="/api/update", tags=["update"])

# 壳的控制服务（Electron 主进程内）：更新动作的实际执行者。
# 端口经 config 单点声明（ADR-0011 §D-2）；壳会下发实际选中的值，故调用时读、不快照。
def control_base() -> str:
    return f"http://127.0.0.1:{config.CONTROL_PORT}"

# 壳状态新鲜度：壳每 15s 心跳一次（见 desktop/main.js 的 updater 心跳），
# 超过 60s 视为陈旧（壳已退出或崩溃）→ UI 不应该再沿用旧结论。
_SHELL_STATE_TTL_MS = 60_000

_PHASE_LABEL = {
    "idle": "未检查更新",
    "checking": "正在检查更新…",
    "latest": "已是最新版本",
    "downloading": "正在后台下载更新…",
    "ready": "更新已就绪，退出时安装",
    "error": "检查更新失败",
    # `checkAndReport()` 在自动更新关闭时返回这个 phase。它目前**不会**进状态文件
    # （关闭时 phase 停在 idle），所以这一档是**防御性**的：缺了它，万一哪天壳把它写进去，
    # 界面就会原样显示出英文内部枚举值 `disabled`（`_PHASE_LABEL.get(phase, phase)` 的兜底）。
    "disabled": "自动更新已关闭",
    "inactive": "桌面壳未运行",
    "unsupported": "本进程不支持自动更新",
}


def version_tuple(v: str | None) -> tuple[int, int, int]:
    """语义化版本 → 可比较元组（非数字段按 0 处理，便于稳健比较）。"""
    parts: list[int] = []
    for seg in str(v or "").split(".")[:3]:
        digits = "".join(c for c in seg if c.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def read_shell_state() -> dict[str, Any]:
    """读壳落盘的状态；缺失 / 损坏 / 陈旧均按「壳不活跃」返回。

    返回结构恒定（不缺键）：UI 直接渲染，不必到处判空。
    """
    inactive = {
        "live": False,
        "ageMs": 0,
        "phase": "inactive",
        "label": "桌面壳未运行",
        "version": "",
        "availableVersion": "",
        "percent": 0,
        "updatePending": False,
        "supported": False,
        "autoUpdate": True,
        "message": "",
        "lastCheckAt": 0,
        "lastError": "",
    }
    path = config.DATA_DIR / "shell-state.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return inactive
    if not isinstance(data, dict):
        return inactive
    age_ms = int(time.time() * 1000) - int(data.get("at") or 0)
    live = 0 <= age_ms <= _SHELL_STATE_TTL_MS
    if not live:
        return {**inactive, "ageMs": age_ms}
    phase = str(data.get("phase") or "idle")
    return {
        "live": True,
        "ageMs": age_ms,
        "phase": phase,
        # **壳给的口径优先**。这里是"同一个事实两个来源"最容易出事的地方：
        # 壳写下来的 label 是它当下的判断（例如「自动更新已关闭」），
        # 本地 `_PHASE_LABEL` 只是**按 phase 重算的近似**——重算会**丢掉壳知道的理由**。
        # 2026-09-21 实测：自动更新关闭时 phase 仍是 idle，于是角标显示「未检查更新」，
        # 用户点一下才吃到一个「已关闭」弹窗，当场问"我不是关了吗"。
        "label": str(data.get("label") or _PHASE_LABEL.get(phase, phase)),
        "version": str(data.get("version") or ""),
        "availableVersion": str(data.get("availableVersion") or ""),
        "percent": int(data.get("percent") or 0),
        "updatePending": bool(data.get("updatePending")),
        "supported": bool(data.get("supported")),
        # 缺字段时按 True 回落：旧壳没这个键，那时角标维持原行为（"未检查更新"），
        # 不能因为读不到就断言"自动更新被关了"。
        "autoUpdate": bool(data.get("autoUpdate", True)),
        "message": str(data.get("message") or ""),
        "lastCheckAt": int(data.get("lastCheckAt") or 0),
        "lastError": str(data.get("lastError") or ""),
    }


@router.get("")
def update_info() -> dict[str, Any]:
    """当前版本 + 更新状态（账号中心版本角标的数据源）。"""
    shell = read_shell_state()
    return {
        "version": __version__,
        "shell": shell,
        "updateAvailable": bool(shell.get("availableVersion"))
        and version_tuple(shell.get("availableVersion")) > version_tuple(__version__),
    }


@router.post("/{action}")
def update_action(action: str, request: Request) -> dict[str, Any]:
    """代理「检查更新 / 立即安装」到桌面壳。

    action: `check` | `install`。壳不在时返回 ok=false —— UI 退化为文字提示
    （与 /api/shell/setup-helper 同一套降级哲学）。

    ⚠️ **这里要留痕，而且要记下来源页面**。2026-09-21 排查实录：用户报告
    「自动更新已关闭」弹窗自己冒出来、还冒了两次，而全仓只有账号中心的版本角标
    会调这个路由——于是"到底是谁调的"没法回答，只能靠猜。`Referer` 一句话解决：
    它直接指出是哪个页面发起的（角标在 `pages/accounts.html` 上）。
    """
    if action not in {"check", "install"}:
        return {"ok": False, "error": f"未知动作：{action}"}
    if action == "check":
        logger.info(
            "收到检查更新请求：referer=%r ua=%r",
            request.headers.get("referer") or "（无）",
            (request.headers.get("user-agent") or "")[:80],
        )
    timeout = 60 if action == "check" else 8  # 检查要等下载元数据往返，安装是即时的
    try:
        req = urllib.request.Request(
            f"{control_base()}/update-{'check' if action == 'check' else 'install'}",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 —— 固定回环地址
            payload = json.loads(resp.read() or b"{}")
        return {"ok": bool(payload.get("ok", True)), "result": payload, "shell": read_shell_state()}
    except Exception as exc:  # noqa: BLE001 —— 壳不可用不该让 UI 报错崩掉
        return {"ok": False, "error": f"桌面壳未响应：{exc}"}
