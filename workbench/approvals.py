"""工具确认门 + 审计写入器：把 `Risk` 分级变成真的安全属性。

## 为什么需要这一层

`plugin_api.Risk` 只是**声明**。没有这一层，"危险操作需人确认"就只是一行注释——
模型照样能一句话把 30 个对象建到真机上。本模块给出**执行侧的强制**：

    模型调用风险工具
      → 无令牌 → 建 pending_action 行 + 返回 needs_approval 观测（**不执行**）
      → 界面拿 pending_id + 人话摘要问用户
      → 用户点「放行」→ 兑换**单次令牌**（一次性、5 分钟、绑定工具+参数指纹）
      → 模型重试同一次调用（带令牌）→ 门开 → 真正执行
      → 用户点「拒绝」→ 记 rejected → 模型看到拒绝理由，不许重试

## 与"环境强制确认"的关系

配置工厂早有一条同类原则（`routes_factory.py:112`：`confirmed` 必须为 true，否则 409）。
那条是**页面级**的（前端勾选框）；这条是**工具级**的，作用于 Agent 对话链路——
两条闸门独立生效，不是替代关系。写平台的动作**两条都要过**。

## 进程内状态 vs 数据库

**权威状态在内存**（`PendingStore._items`），DB 只做**留痕**（`pending_action` 表）。
理由：这是单机单人应用，待确认操作的生命周期是"几十秒"，跨进程重启后**本来就应该
失效**（用户重启了应用，凭什么还替他记住一个未确认的写操作？）。因此启动时把上一
进程遗留的 `pending` 行标成 `expired`，而不是恢复它们——这条是刻意的，别"顺手"改成恢复。
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from . import db
from .plugin_api import ToolHub, ToolSpec, redact_args

logger = logging.getLogger("workbench.approvals")

TOKEN_TTL_MS = 5 * 60 * 1000
"""单次令牌有效期：5 分钟（人要来得及看一眼再点）。"""

PENDING_TTL_MS = 30 * 60 * 1000
"""待确认操作在界面上的可见期：30 分钟；过期后模型的重试会被要求重新申请。"""


@dataclass
class PendingAction:
    id: str
    tool: str
    plugin: str
    risk: str
    args: dict[str, Any]
    summary: str
    created_at: int
    status: str = "pending"       # pending | approved | rejected | expired
    token: str = ""
    token_expires_at: int = 0
    conversation_id: str = ""
    decided_at: int = 0
    decided_by: str = ""

    def public(self) -> dict[str, Any]:
        """给界面的形状（**不含令牌**——令牌只回给批准它的那一次调用）。"""
        return {
            "id": self.id,
            "tool": self.tool,
            "plugin": self.plugin,
            "risk": self.risk,
            "args": self.args,
            "summary": self.summary,
            "status": self.status,
            "createdAt": self.created_at,
            "decidedAt": self.decided_at,
            "conversationId": self.conversation_id,
        }


class PendingStore:
    """待确认操作的进程内权威存储（线程安全；FastAPI 的同步/异步线程都会碰它）。"""

    def __init__(self) -> None:
        self._items: dict[str, PendingAction] = {}
        self._by_token: dict[str, str] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 申请

    def request(self, spec: ToolSpec, args: BaseModel, conversation_id: str = "") -> PendingAction:
        """建一条待确认（同一工具+同一参数在有效期内**复用**，避免刷屏）。"""
        fingerprint = _fingerprint(spec.name, args)
        with self._lock:
            self._expire_locked()
            for item in self._items.values():
                if (item.status == "pending" and item.tool == spec.name
                        and _fingerprint(item.tool, item.args) == fingerprint):
                    return item

            args_dump = redact_args(_dump(args))
            item = PendingAction(
                id=uuid.uuid4().hex[:12],
                tool=spec.name,
                plugin=spec.plugin_id,
                risk=spec.risk.value,
                args=args_dump,
                summary=summarize(spec, args_dump),
                created_at=_now_ms(),
                conversation_id=conversation_id,
            )
            self._items[item.id] = item
            self._record(item)
            logger.info("待确认操作：%s（%s，%s）", item.id, item.tool, item.risk)
            return item

    # ------------------------------------------------------------ 决策

    def approve(self, pending_id: str, *, by: str = "user") -> PendingAction | None:
        """放行：兑换单次令牌（**一次性**）。"""
        with self._lock:
            item = self._items.get(pending_id)
            if item is None or item.status != "pending":
                return None
            item.status = "approved"
            item.token = secrets.token_urlsafe(24)
            item.token_expires_at = _now_ms() + TOKEN_TTL_MS
            item.decided_at = _now_ms()
            item.decided_by = by
            self._by_token[item.token] = item.id
            self._record(item)
            return item

    def reject(self, pending_id: str, reason: str = "", *, by: str = "user") -> PendingAction | None:
        with self._lock:
            item = self._items.get(pending_id)
            if item is None or item.status != "pending":
                return None
            item.status = "rejected"
            item.decided_at = _now_ms()
            item.decided_by = by
            if reason:
                item.summary = f"{item.summary}（用户拒绝：{reason}）"
            self._record(item)
            return item

    # ------------------------------------------------------------ 确认门

    def gate(
        self, spec: ToolSpec, args: BaseModel, token: str, conversation_id: str = ""
    ) -> tuple[bool, str, str]:
        """`ToolHub` 的确认门实现：返回 (放行?, pending_id, 需确认原因)。"""
        if token:
            with self._lock:
                self._expire_locked()
                pending_id = self._by_token.pop(token, "")
                item = self._items.get(pending_id) if pending_id else None
                if item is not None and item.status == "approved":
                    # 令牌一次性：pop 掉即作废，防止"批准一次、重放多次"
                    item.token = ""
                    return True, item.id, ""
            # 令牌无效/已用过/已过期 → 当作没批准，重新申请
            logger.info("确认令牌无效或已失效，重新申请确认：%s", spec.name)

        item = self.request(spec, args, conversation_id)
        return False, item.id, item.summary

    # ------------------------------------------------------------ 查询

    def list(self, status: str = "pending", limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            self._expire_locked()
            items = [i for i in self._items.values() if not status or i.status == status]
        items.sort(key=lambda i: i.created_at, reverse=True)
        return [i.public() for i in items[:limit]]

    def get(self, pending_id: str) -> PendingAction | None:
        with self._lock:
            self._expire_locked()
            return self._items.get(pending_id)

    # ------------------------------------------------------------ 内部

    def _expire_locked(self) -> None:
        now = _now_ms()
        for item in self._items.values():
            if item.status == "approved" and item.token_expires_at and now > item.token_expires_at:
                self._by_token.pop(item.token, None)
                item.token = ""
                item.status = "expired"
                self._record(item)
            elif (item.status == "pending" and now - item.created_at > PENDING_TTL_MS):
                item.status = "expired"
                self._record(item)

    def _record(self, item: PendingAction) -> None:
        try:
            db.execute(
                "INSERT INTO pending_action (id, tool, plugin, risk, args, summary, status, "
                "created_at, decided_at, decided_by, conversation_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
                "decided_at=excluded.decided_at, decided_by=excluded.decided_by",
                (item.id, item.tool, item.plugin, item.risk,
                 json.dumps(item.args, ensure_ascii=False)[:2000], item.summary[:1000],
                 item.status, item.created_at, item.decided_at, item.decided_by,
                 item.conversation_id),
            )
        except Exception:  # noqa: BLE001 —— 留痕失败不影响确认流程本身
            logger.warning("pending_action 留痕失败：%s", item.id, exc_info=True)


# ---------------------------------------------------------------- 人话摘要


_SUMMARY_LABEL = {
    "account_id": "账号",
    "blueprint_id": "蓝图",
    "objects": "对象",
    "object": "对象",
    "code": "对象编码",
    "requirement": "需求",
    "url": "地址",
    "limit": "条数",
}


def summarize(spec: ToolSpec, args: dict[str, Any]) -> str:
    """给用户看的一句话：做什么 + 影响谁。

    刻意不做成"工具名 + JSON"——用户要判断的是**后果**，不是参数名。
    """
    parts = [f"{spec.description}"]
    details = [f"{_SUMMARY_LABEL.get(k, k)}={v}" for k, v in args.items() if v not in (None, "", [])]
    if details:
        parts.append("（" + "，".join(details) + "）")
    parts.append(f"［{spec.risk.value} 级］")
    return "".join(parts)


def _fingerprint(tool: str, args: Any) -> str:
    payload = _dump(args) if isinstance(args, BaseModel) else (args or {})
    clean = {k: v for k, v in payload.items() if k != "__call_id__"}
    return json.dumps({"tool": tool, "args": clean}, ensure_ascii=False, sort_keys=True, default=str)


def _dump(args: BaseModel) -> dict[str, Any]:
    try:
        return args.model_dump(exclude_none=True)
    except Exception:  # noqa: BLE001
        return {}


def _now_ms() -> int:
    from .services.storage import now_ms

    return now_ms()


# ---------------------------------------------------------------- 审计 + 装配


def write_audit(record: dict[str, Any]) -> None:
    """把一次工具调用写进 `agent_audit`。

    **这是工具调用的唯一审计入口**——HTTP 面与对话循环共用它，因此"对话里的工具调用
    不入审计"这类缺口不会再出现（2026-09-15 之前 `harness` 完全不写审计表）。

    列兼容：`agent_audit` 建于迁移 9，只有 7 列；`plugin`/`risk`/`meta` 三列由迁移 16 补。
    写入时按实际存在的列裁剪，避免旧库（未迁移）因未知列整体失败。
    """
    columns = ["id", "tool", "args", "status", "result_summary", "duration_ms", "created_at"]
    params: list[Any] = [
        record.get("id") or uuid.uuid4().hex[:12],
        record.get("tool", ""),
        json.dumps(record.get("args") or {}, ensure_ascii=False)[:2000],
        record.get("status", "ok"),
        (record.get("summary") or "")[:1000],
        int(record.get("duration_ms") or 0),
        _now_ms(),
    ]
    if _has_columns("agent_audit", ("plugin", "risk", "meta")):
        columns += ["plugin", "risk", "meta"]
        params += [
            record.get("plugin", ""),
            record.get("risk", "read"),
            json.dumps(record.get("meta") or {}, ensure_ascii=False)[:1000],
        ]
    db.execute(
        f"INSERT INTO agent_audit ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})",
        tuple(params),
    )


def pending_store() -> PendingStore:
    """进程级单例（与 ToolHub 同寿命）。"""
    global _store
    if _store is None:
        _store = PendingStore()
    return _store


_store: PendingStore | None = None


def reset_store() -> None:
    """仅测试用。"""
    global _store
    _store = None


def install(hub: ToolHub) -> None:
    """把审计写入器 + 确认门装到工具中枢上（`get_hub()` 装配期调用一次）。

    门用 **lambda 而不是 `pending_store().gate`**：闭包里若是当初那个实例，
    `reset_store()`（测试隔离用）之后中枢还捏着旧店 —— 表现为"申请到 pendingId
    却查不到 / 批不了"，而且只在测试里出现、生产看不出，最难查的那种。
    """
    hub.set_audit_writer(write_audit)
    hub.set_approval_gate(
        lambda spec, args, token, conversation_id: pending_store().gate(
            spec, args, token, conversation_id
        )
    )
    _expire_orphans()


def _expire_orphans() -> None:
    """上一进程遗留的 pending 一律标 expired（见模块文档：刻意不恢复）。"""
    try:
        changed = db.execute(
            "UPDATE pending_action SET status='expired' WHERE status IN ('pending','approved')"
        )
        if changed:
            logger.info("启动清理：%d 条上一进程未决的待确认操作已标记 expired", changed)
    except Exception:  # noqa: BLE001 —— 表可能还不存在（未迁移），不该阻断装配
        logger.debug("pending_action 清理跳过", exc_info=True)


def _has_columns(table: str, names: tuple[str, ...]) -> bool:
    try:
        cols = {row["name"] for row in db.query(f"PRAGMA table_info({table})")}
    except Exception:  # noqa: BLE001
        return False
    return set(names) <= cols
