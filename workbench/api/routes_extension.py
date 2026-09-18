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
import time
from typing import Any

from fastapi import APIRouter

from .. import __version__
from ..services import accounts as accounts_svc
from ..services.storage import get_setting, now_ms, set_setting

router = APIRouter(prefix="/extension", tags=["extension"])

_lock = threading.Lock()
# 长轮询的等待/唤醒条件变量：复用同一把锁，保证「队列内容」与「等待者」状态一致
_cond = threading.Condition(_lock)
_seq: int | None = None  # 惰性从 settings 表恢复（跨进程重启单调递增）
_seq_SEQ_KEY = "ext_cmd_seq"
_commands: list[dict[str, Any]] = []
# 已确认消费到的**序号水位线**（等价于 `max(acked seqs)`）。
#
# 为什么是水位线而不是"逐条 ack 的集合"（C4，2026-09-17 修）：
# 旧实现用 `_acked: set[int]`，回收条件是"**所有**队列内指令都在集合里"，而扩展端
# （`sync.ts:241-243`）**只上报最大 cursor**（`{"seqs": [cursor]}`）。当队列里同时存在
# seq 1 与 seq 2 时，客户端若先只消费到 1（下一条还没取到就 ack）→ 集合里只有 {1}，
# `all(...)` 恒假 → `_commands.clear()` **永不执行**：队列与集合只增不减，
# 每次 `/commands` 都要重扫全部历史指令。
#
# 语义依据：客户端的 cursor 是**单调推进**的（`cursor = Math.max(cursor, seq)`），
# 且逐条隔离——达到某个 seq 就意味着 seq 及之前都已处理过（失败的单条也会被跳过并推进，
# 见 sync.ts 的注释"单条失败不得阻塞后继指令，更不能阻止 cursor 持久化 + ack"）。
# 所以"ack 到 N"= "N 及之前全部可以回收"，这正是水位线语义。
_ack_watermark = 0
# 执行面状态回传（desktopId → 状态快照），供账号中心四态徽标；带 TTL 淘汰
_STATE: dict[str, dict[str, Any]] = {}
_STATE_TTL_MS = 60_000

# 执行面元信息（扩展自身版本）：与 _STATE 同寿命，供「扩展是否需要重新加载」判定
_ext_meta: dict[str, Any] = {"extVersion": "", "desktopVersionSeen": "", "at": 0.0}

# ⚠ 本模块**只有**"扩展 ↔ 桌面"的数据与指令通道，整段 `/extension/*` 需要访问令牌
# （`extension_auth.py`，P0-1）。因此这里**不得**再放"给桌面 UI 用、且不碰凭据"的端点——
# 那类端点会因为拿不到令牌而必须开豁免，而豁免一开，
# "这个前缀整段需要令牌"就退化成一份需要人工维护的清单。
# 2026-09-17 已把 `/launch-chrome`、`/update-state`、`/setup-helper` 搬到 `routes_shell.py`
# （`/api/shell/*`），见该模块文档串。新增端点前先问：**它碰凭据吗？**


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
    with _cond:
        _commands.append(cmd)
        # 唤醒正在长轮询等待的扩展：指令延迟 = 一次本机回环（旧实现要等 ≤2s 的轮询节拍）
        _cond.notify_all()
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
        "format": "workbench-snapshot",
        "version": 1,
        "generatedAt": now_ms(),
        # 桌面端版本：扩展据此自查「我是不是旧版」（用户定稿：两者版本号同步升版）
        "desktopVersion": __version__,
        "snapshotId": snapshot_id,
        "fernetKey": backup["fernetKey"],
        "sites": sites,
        "accounts": accounts,
        "boxes": {**backup["boxes"], "disabled": accounts_svc.list_disabled_boxes()},
    }


@router.get("/commands")
def commands(after: int = 0, wait: float = 0) -> dict[str, Any]:
    """拉取 after 序号之后的待执行指令。

    `wait>0` = **长轮询**：无待执行指令时挂起至多 wait 秒，指令一入队立即返回。

    这是「桌面点击 → 浏览器打开」延迟的主项：旧实现由扩展每 2s 轮询一次，指令平均要等
    ~1s、最坏 ~2s（用户实感「要等一两秒」）。长轮询把这段量化延迟压到一次本机回环（~1ms）。
    默认 `wait=0` 保持原非阻塞语义（既有调用方与 `tools/verify_extension_sync.mjs` 不受影响）。
    同步端点由 FastAPI 放进线程池执行，故阻塞安全；上限 25s 防止连接堆积。
    """
    timeout = min(max(wait, 0.0), 25.0)
    deadline = time.monotonic() + timeout
    with _cond:
        while True:
            pending = [c for c in _commands if c["seq"] > after]
            cursor = max((c["seq"] for c in _commands), default=after)
            if pending or timeout <= 0:
                # longPoll 显式告知客户端「本次是否真的挂起过」：旧版服务端不认 wait 参数，
                # 扩展侧据此退避，避免把立即返回当成有效长轮询而成热循环
                return {"commands": pending, "cursor": cursor, "longPoll": timeout > 0}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"commands": [], "cursor": cursor, "longPoll": True}
            _cond.wait(timeout=remaining)


@router.post("/commands")
def push_command(body: dict[str, Any]) -> dict[str, Any]:
    """桌面账号中心/轮盘写指令：type = par.open | wheel.toggle。"""
    cmd_type = str(body.get("type") or "")
    if cmd_type not in {"par.open", "wheel.toggle"}:
        from fastapi import HTTPException

        raise HTTPException(400, f"未知指令类型：{cmd_type}")
    cmd = dispatch_command(cmd_type, body.get("payload") or {})
    return {"seq": cmd["seq"], "accepted": True}


@router.post("/ack")
def ack(body: dict[str, Any]) -> dict[str, Any]:
    """确认指令已消费（扩展端每次消费后上报**本轮推进到的最大 cursor**）。

    按**水位线**回收：`seq <= max(acked)` 的指令一律出队（语义见 `_ack_watermark` 的说明）。
    客户端可能只 ack 一个比自己已取的更小的值（乱序/重试），故水位线**只增不减**。
    """
    global _ack_watermark
    seqs = [int(s) for s in body.get("seqs") or []]
    with _lock:
        if seqs:
            _ack_watermark = max(_ack_watermark, max(seqs))
        if _commands:
            _commands[:] = [c for c in _commands if c["seq"] > _ack_watermark]
        if not _commands:
            # 队列空了就把水位线归零：它是"队内进度"而不是"历史进度"，
            # 归零后 seq 仍由 settings 表单调递增，不会与游标比较产生歧义。
            _ack_watermark = 0
    return {"acked": seqs, "watermark": _ack_watermark, "pending": len(_commands)}


def _reset_command_queue_for_tests() -> None:
    """清空指令队列与水位线（**仅供测试**：模块级内存态在用例间会串）。

    `_seq` 一并清掉，逼 `_next_seq()` 重新从 settings 表恢复——
    这样"跨重启单调递增"这条契约在测试里是真的被验证，而不是被内存缓存掩盖。
    """
    global _seq, _ack_watermark
    with _lock:
        _commands.clear()
        _ack_watermark = 0
        _seq = None


@router.post("/state")
def report_state(body: dict[str, Any]) -> dict[str, Any]:
    """扩展执行面状态上报（每 ~6s）：desktopId → 页签数/token + 扩展自身版本。

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
        # 版本元信息独立于 items 更新：映射为空（还没同步过账号）时也要能上报版本，
        # 否则「扩展是旧版」永远传不到桌面端
        ext_version = str(body.get("extVersion") or "").strip()
        if ext_version:
            _ext_meta["extVersion"] = ext_version
            _ext_meta["at"] = now
    # 收到执行面上报本身即「连接成功过一次」的证据——立刻落库闩锁，
    # 这样即使账号中心页面从未打开过，安装引导也不会在以后重新出现。
    # （放在锁外：写库不该持状态锁；_mark_ever_connected 自身幂等）
    if items or ext_version:
        _mark_ever_connected()
    return {"accepted": len(items)}


@router.get("/state")
def get_state() -> dict[str, Any]:
    """桌面 UI 读取执行面状态（TTL 内的条目）。"""
    cutoff = now_ms() - _STATE_TTL_MS
    with _lock:
        items = [dict(v, desktopId=k) for k, v in _STATE.items() if v["at"] > cutoff]
    return {"items": items}


# 「曾成功连接过一次」的一次性闩锁（安装引导提示条据此永久静默）。
# 为什么必须落库：早期实现把它放在浏览器内存里，刷新页面/重启应用即复位 →
# 每次打开账号中心都重新弹「未检测到浏览器扩展」（用户实测反馈）。
# 语义：按**安装实例**记一次，之后无论扩展是否在线都不再提示（连接判据见 /health）。
_EVER_KEY = "ext_connected_once"
_ever_connected = False  # 进程内缓存（单进程应用，写入后置位）


def _read_ever_connected() -> bool:
    global _ever_connected
    if not _ever_connected:
        try:
            _ever_connected = bool(get_setting(_EVER_KEY))
        except Exception:  # noqa: BLE001 —— settings 读失败按未连接处理（顶多多提示一次）
            pass
    return _ever_connected


def _mark_ever_connected() -> None:
    """首次连接成功即落库；写成功才置内存位，失败留待下次上报重试。"""
    global _ever_connected
    if _ever_connected:
        return
    try:
        set_setting(_EVER_KEY, "1")
        _ever_connected = True
    except Exception:  # noqa: BLE001 —— 落库失败不影响本次判定
        pass


@router.get("/health")
def health() -> dict[str, Any]:
    """执行面健康 + 是否曾经连接成功过 + 版本一致性。

    `connected` = TTL 内是否收到状态上报（同时涵盖「扩展没装」与「Chrome 没开」两种情形，
    故文案保持中性）；`everConnected` = 本安装实例是否成功连接过至少一次——
    账号中心的安装引导提示条**只在从未连接过时**出现，且该判据持久在 DB、不随刷新复位。

    版本面（用户定稿：桌面安装包与扩展版本同号升版）：
    `extVersion` < `desktopVersion` ⇒ Chrome 里加载的仍是旧扩展目录内容，
    需要用户在 chrome://extensions 点一次「重新加载」（扩展随安装包更新，见 docs/EXTENSION-INSTALL.md）。

    认证面（P0-1）：`authBlocked` / `tokenlessRequests` 回答"有没有人在**不带访问令牌**敲门"。
    这是**防静默故障**的关键一环——grace 窗口过后，没重新加载扩展的用户会开始收到 401，
    而扩展侧对失败是静默吞掉的（离线回退是常态）→ 用户只会看到"快捷登录不灵了"。
    把这件事经本端点（免鉴权）告诉桌面 UI，账号中心就能复用既有的提示条引导用户重新加载。
    """
    from .extension_auth import auth_gap_stats
    from .routes_update import version_tuple

    cutoff = now_ms() - _STATE_TTL_MS
    with _lock:
        fresh = [v for v in _STATE.values() if v["at"] > cutoff]
        ext_version = str(_ext_meta["extVersion"]) if _ext_meta["at"] > cutoff else ""
    # 「已连接」= TTL 内收到过上报。**含只有版本、没有账号映射的上报**——否则用户还没同步过
    # 账号（或刚清空映射）时执行面明明活着，UI 却在说"未连接"，与"曾经连上过"的闩锁自相矛盾。
    connected = bool(fresh) or bool(ext_version)
    if connected:
        _mark_ever_connected()
    # 扩展未上报版本（旧版扩展 / 尚未上报）时不做旧版判定——避免误报打扰用户
    ext_stale = bool(ext_version) and version_tuple(ext_version) < version_tuple(__version__)
    gap = auth_gap_stats()
    return {
        "connected": connected,
        "reportedAccounts": len(fresh),
        "everConnected": _read_ever_connected(),
        "desktopVersion": __version__,
        "extVersion": ext_version,
        "extStale": ext_stale,
        # 有人在无令牌访问 = Chrome 里那份扩展还没重新加载（新扩展会自动自举令牌）
        "authBlocked": gap["active"],
        "tokenlessRequests": gap["tokenlessRequests"],
    }
