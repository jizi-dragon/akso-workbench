"""扩展同步蓝图（quick-login 执行面的数据与指令通道）。

架构分工（用户定稿）：
- 桌面应用 = 数据面（账号/盒子/站点，SQLite+Fernet）+ 触发面（本地轮盘/热键写指令队列）；
- 扩展 = 执行面（用户 Chrome 里的会话切换/自动登录），每 2s 轮询本蓝图。

安全语义：凭据以「Fernet 密文 + 密钥」经 127.0.0.1 回环下发（与备份文件同语义），
扩展端内存解密后交由其本地 AES-GCM 存储落盘；指令队列仅存内存（进程生命周期）。
"""

from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter

from ..services import accounts as accounts_svc
from ..services.storage import now_ms

router = APIRouter(prefix="/extension", tags=["extension"])

_lock = threading.Lock()
_seq = 0
_commands: list[dict[str, Any]] = []
_acked: set[int] = set()


def dispatch_command(command_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """桌面侧入口：写入一条扩展指令（轮盘呼出 / 切换账号）。"""
    global _seq
    with _lock:
        _seq += 1
        cmd = {"seq": _seq, "type": command_type, "payload": payload or {}, "at": now_ms()}
        _commands.append(cmd)
        return cmd


def _host_of(base_url: str) -> str:
    from urllib.parse import urlparse

    host = urlparse(base_url).hostname or base_url
    return host


@router.get("/snapshot")
def snapshot() -> dict[str, Any]:
    """扩展数据面快照：账号（密文+密钥）/ 盒子 / 站点清单。"""
    backup = accounts_svc.export_backup()  # 复用备份语义（fernetKey + 密文凭据）
    accounts = [
        {
            "desktopId": a["id"],
            "host": _host_of(a.get("env_base_url") or ""),
            "tabName": a["username"],
            "username": a["username"],
            "passwordEnc": a["passwordEnc"],
            "box": a.get("box") or "",
        }
        for a in backup["accounts"]
    ]
    sites = sorted({a["host"] for a in accounts})
    return {
        "format": "akso-workbench-snapshot",
        "version": 1,
        "generatedAt": now_ms(),
        "fernetKey": backup["fernetKey"],
        "sites": sites,
        "accounts": accounts,
        "boxes": backup["boxes"],
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
    """桌面轮盘/热键写指令：type = par.open | wheel.toggle。"""
    cmd_type = str(body.get("type") or "")
    if cmd_type not in {"par.open", "wheel.toggle"}:
        from fastapi import HTTPException

        raise HTTPException(400, f"未知指令类型：{cmd_type}")
    cmd = dispatch_command(cmd_type, body.get("payload") or {})
    return {"seq": cmd["seq"], "accepted": True}


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
