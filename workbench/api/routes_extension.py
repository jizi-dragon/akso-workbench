"""扩展同步蓝图（quick-login 执行面的数据与指令通道）。

架构分工（用户定稿）：
- 桌面应用 = 数据面（账号/盒子/站点，SQLite+Fernet）+ 触发面（本地轮盘/热键写指令队列）；
- 扩展 = 执行面（用户 Chrome 里的会话切换/自动登录），每 2s 轮询本蓝图。

安全语义：凭据以「Fernet 密文 + 密钥」经 127.0.0.1 回环下发（与备份文件同语义），
扩展端内存解密后交由其本地 AES-GCM 存储落盘；指令队列仅存内存（进程生命周期）。
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from ..services import accounts as accounts_svc
from ..services.storage import get_setting, now_ms, set_setting

router = APIRouter(prefix="/extension", tags=["extension"])

_lock = threading.Lock()
_seq: int | None = None  # 惰性从 settings 表恢复（跨进程重启单调递增）
_seq_SEQ_KEY = "ext_cmd_seq"
_commands: list[dict[str, Any]] = []
_acked: set[int] = set()
# 执行面状态回传（desktopId → 状态快照），供账号中心四态徽标；带 TTL 淘汰
_STATE: dict[str, dict[str, Any]] = {}
_STATE_TTL_MS = 60_000


def _next_seq() -> int:
    """指令序号跨重启单调递增。

    扩展游标持久存于 chrome.storage.local；若服务端重启后 seq 从 1 重来，
    所有新指令 seq ≤ 历史游标，会被 `seq > after` 过滤器永久吞掉（0.2.4 实锤）。
    故序号落 settings 表，重启后接续。
    """
    global _seq
    with _lock:
        if _seq is None:
            try:
                _seq = int(get_setting(_seq_SEQ_KEY) or 0)
            except (TypeError, ValueError):
                _seq = 0
        _seq += 1
        try:
            set_setting(_seq_SEQ_KEY, str(_seq))
        except Exception:  # noqa: BLE001 —— settings 表异常不阻塞指令下发
            pass
        return _seq


def dispatch_command(command_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """桌面侧入口：写入一条扩展指令（轮盘呼出 / 切换账号）。"""
    cmd = {"seq": _next_seq(), "type": command_type, "payload": payload or {}, "at": now_ms()}
    with _lock:
        _commands.append(cmd)
    return cmd


def _host_of(base_url: str) -> str:
    """站点 host：保留端口（非标端口内网站点常见，hostname 会丢端口且 scheme 自学习无法挽回）。"""
    from urllib.parse import urlparse

    parsed = urlparse(base_url if "//" in base_url else f"//{base_url}", scheme="https")
    return parsed.netloc or base_url


def _scheme_of(base_url: str) -> str:
    from urllib.parse import urlparse

    return (urlparse(base_url if "//" in base_url else f"https://{base_url}").scheme or "https").lower()


@router.get("/snapshot")
def snapshot() -> dict[str, Any]:
    """扩展数据面快照：账号（密文+密钥）/ 盒子 / 站点清单。"""
    backup = accounts_svc.export_backup()  # 复用备份语义（fernetKey + 密文凭据）
    accounts = [
        {
            "desktopId": a["id"],
            # export_backup 输出驼峰键 envBaseUrl（备份文件语义），勿用下划线——
            # 错位会导致 host 恒空 → 扩展 sync 静默丢弃全部账号（0.2.3 实锤断点）
            "host": _host_of(a.get("envBaseUrl") or ""),
            "scheme": _scheme_of(a.get("envBaseUrl") or ""),
            "tabName": (a.get("tabName") or "").strip() or a["username"],
            "username": a["username"],
            "passwordEnc": a["passwordEnc"],
            "box": a.get("box") or "",
        }
        for a in backup["accounts"]
    ]
    sites = sorted({a["host"] for a in accounts})
    # 内容哈希：扩展端据此幂等跳过未变化的快照
    snapshot_id = hashlib.sha256(
        json.dumps({"accounts": accounts, "boxes": backup["boxes"]},
                   ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "format": "akso-workbench-snapshot",
        "version": 1,
        "generatedAt": now_ms(),
        "snapshotId": snapshot_id,
        "fernetKey": backup["fernetKey"],
        "sites": sites,
        "accounts": accounts,
        "boxes": {**backup["boxes"], "disabled": accounts_svc.list_disabled_boxes()},
    }


@router.get("/commands")
def commands(after: int = 0) -> dict[str, Any]:
    """拉取 after 序号之后的待执行指令。"""
    with _lock:
        pending = [c for c in _commands if c["seq"] > after and c["seq"] not in _acked]
        cursor = max((c["seq"] for c in _commands), default=after)
    return {"commands": pending, "cursor": cursor}


@router.post("/commands")
def push_command(body: dict[str, Any]) -> dict[str, Any]:
    """桌面账号中心/轮盘写指令：type = par.open | wheel.toggle。"""
    cmd_type = str(body.get("type") or "")
    if cmd_type not in {"par.open", "wheel.toggle"}:
        from fastapi import HTTPException

        raise HTTPException(400, f"未知指令类型：{cmd_type}")
    cmd = dispatch_command(cmd_type, body.get("payload") or {})
    return {"seq": cmd["seq"], "accepted": True}


@router.post("/launch-chrome")
def launch_chrome() -> dict[str, Any]:
    """确保 Chrome 正在运行（扩展在用户默认 profile 里；未运行则拉起）。

    关键约束：Chrome 未运行时，扩展 SW 不会轮询指令——此时点击轮盘选人，
    指令会滞留队列。故桌面在派发前先探测/拉起 Chrome。
    """
    import subprocess

    def chrome_running() -> bool:
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
                capture_output=True, text=True, timeout=10,
            )
            return "chrome.exe" in (out.stdout or "")
        except Exception:  # noqa: BLE001
            return False

    if chrome_running():
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
            subprocess.Popen([path])
            return {"launched": True, "running": True, "path": path}
    return {"launched": False, "running": False, "detail": "未找到 chrome.exe"}


@router.post("/ack")
def ack(body: dict[str, Any]) -> dict[str, Any]:
    seqs = [int(s) for s in body.get("seqs") or []]
    with _lock:
        _acked.update(seqs)
        # 回收：所有未回收指令均已被 ack 且 ack 序号 ≥ 全部 → 清空队列
        if _commands and all(c["seq"] in _acked for c in _commands):
            _commands.clear()
            _acked.clear()
    return {"acked": seqs}


@router.post("/state")
def report_state(body: dict[str, Any]) -> dict[str, Any]:
    """扩展执行面状态上报（每 ~6s）：desktopId → 页签数/token/授权暂停。

    仅内存态 + TTL 淘汰——扩展离线后徽标自动回落「离线」，无需清理任务。
    """
    now = now_ms()
    items = body.get("items") or []
    with _lock:
        for it in items:
            desktop_id = str(it.get("desktopId") or "")
            if not desktop_id:
                continue
            _STATE[desktop_id] = {
                "tabs": int(it.get("tabs") or 0),
                "hasToken": bool(it.get("hasToken")),
                "enforcementOff": bool(it.get("enforcementOff")),
                "at": now,
            }
        # 顺手淘汰过期项
        for key in [k for k, v in _STATE.items() if now - v["at"] > _STATE_TTL_MS]:
            _STATE.pop(key, None)
    return {"accepted": len(items)}


@router.get("/state")
def get_state() -> dict[str, Any]:
    """桌面 UI 读取执行面状态（TTL 内的条目）。"""
    cutoff = now_ms() - _STATE_TTL_MS
    with _lock:
        items = [dict(v, desktopId=k) for k, v in _STATE.items() if v["at"] > cutoff]
    return {"items": items}
