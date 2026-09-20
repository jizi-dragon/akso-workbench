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

import asyncio
import json
import logging
import secrets
import threading
import uuid
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from . import db
from .plugin_api import (
    DEFAULT_CONFIRM_TIMEOUT_S,
    ConfirmOutcome,
    ConfirmPlan,
    ToolHub,
    ToolSpec,
    redact_args,
)

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
    status: str = "pending"       # pending | approved | rejected | expired | consumed
    token: str = ""
    token_expires_at: int = 0
    conversation_id: str = ""
    decided_at: int = 0
    decided_by: str = ""

    fingerprint: str = ""
    """**未打码**的参数指纹（`_fingerprint(tool, 原始参数)`）。

    为什么单独存一份而不是每次从 `args` 现算：`args` 是 `redact_args()` 之后的副本，
    若参数里出现 `_REDACT_KEYS` 命中的键名（`token` / `secret` …），现算出来的指纹
    **与申请时的对不上** → 复用一个 pending 的逻辑会静默失效、每次都新建一条。
    这是收 P0-A 时顺带发现的既有缺陷（当时所有工具的参数名恰好都不敏感，所以没暴露）。
    """

    target_tool: str = ""
    """`request_user_confirmation` 预先批准的那次调用的工具名（空 = 纯意图确认，不绑定调用）。

    **刻意不落库**：`_record()` 的列里没有它。它是"这次确认绑定到哪一次调用"的运行时事实，
    跨进程重启本来就该失效（见模块文档：不恢复遗留 pending）。
    """

    decided_reason: str = ""
    """用户拒绝时填写的理由（原样，未打码——它由用户自己写下，且会回给模型）。"""

    kind: str = "approval"
    """这条待确认是**谁发起的**：`approval`（宿主按风险门自动拦）/ `confirmation`（模型主动问）。

    界面靠它决定弹窗标题与文案——两者对用户要做的动作**不一样**：
    风险门那张要说"放行后本轮已结束，需要让模型重试"，
    实时确认这张要说"你点完我就继续"。混用会让用户对着一个已经复活的循环干等。

    **不落库**（`_record()` 的列里没有它）：它是运行期事实，而历史行不需要这个区分
    ——`pending_action.tool` 已经能说明是哪一次调用。DB 迁移只增不改，不值得为它开一列。
    """

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
            "kind": self.kind,
        }


class PendingStore:
    """待确认操作的进程内权威存储（线程安全；FastAPI 的同步/异步线程都会碰它）。

    它同时服务**两种**待确认，两者共用一张表、一套 approve/reject 端点、一套指纹语义：

    | | 谁发起 | 谁在等 | 用户同意后 |
    |---|---|---|---|
    | 风险门（`gate`） | 宿主，按 `Risk` 自动拦 | **没人等**，本轮对话已结束 | 模型下次重试同一次调用时被 `claim_approved` 认领 |
    | 实时确认（`request_confirmation`） | 模型主动调 `request_user_confirmation` | **对话循环**（`wait_decision`） | 循环立刻醒来，把答复回填成工具观测 |

    差别只在"谁在等"。**指纹与一次性语义完全一致**，所以实时确认不放松任何安全属性。
    """

    def __init__(self) -> None:
        self._items: dict[str, PendingAction] = {}
        self._by_token: dict[str, str] = {}
        self._lock = threading.Lock()
        self._waiters: dict[str, Future[ConfirmOutcome]] = {}
        """pending_id → 正在等答复的那个对话循环。

        ⚠️ 它必须在**登记确认的那一刻**就建好（`request_confirmation` 里），
        不能等循环走到 `wait_decision` 时再懒建：用户手快先点了「同意」，
        而当时还没人登记 → 这个答复**没有地方落地**，循环白等满一个超时。
        """

        self._decisions: dict[str, ConfirmOutcome] = {}
        """已经产生、但等待者还没来取的答复（"答复先到"的着陆点，见 `_settle_locked`）。"""

    # ------------------------------------------------------------ 申请

    def request(self, spec: ToolSpec, args: BaseModel, conversation_id: str = "") -> PendingAction:
        """建一条待确认（同一工具+同一参数在有效期内**复用**，避免刷屏）。"""
        fingerprint = _fingerprint(spec.name, args)
        with self._lock:
            self._expire_locked()
            for item in self._items.values():
                if (item.status == "pending" and item.tool == spec.name
                        and item.fingerprint == fingerprint
                        and item.id not in self._waiters):
                    # `id not in self._waiters` 是必须的：**有一方正在等答复**的条目
                    # 不能被复用。复用会让第二个等待者挂到一个已经 resolve 过的 future 上，
                    # 于是它立刻拿到上一个人的答案——"用户同意过 A" 于是变成了 "同意 B"。
                    return item
            return self._mint(spec, args, conversation_id, summary="")

    def request_confirmation(
        self, spec: ToolSpec, args: BaseModel, plan: ConfirmPlan, conversation_id: str = ""
    ) -> str:
        """登记一次**实时确认**，返回 pending_id。**只登记，不等待**（等待在 `wait_decision`）。

        ## 这条待确认挂在谁头上（本模块最容易读错的一处）

        | `plan.target` | 行挂在 | 为什么 |
        |---|---|---|
        | 有（模型给了 `targetTool`） | **目标工具 + 目标参数** | 用户同意的是"那一次具体调用"，所以指纹必须和那次调用对得上，`claim_approved` 才认它 |
        | 无 | 确认工具自己 | 纯意图确认，不绑定任何调用（那条行永远不会被认领——确认工具是只读的，不过风险门） |

        第一行是「预先批准一次具体调用」的全部要害：**用户批准的是参数指纹，不是模型的意图**。
        模型换个参数再调一次目标工具，指纹对不上，照样重新走一遍风险门。
        """
        target, target_args = plan.target, plan.target_args
        with self._lock:
            self._expire_locked()
            if target is not None and target_args is not None:
                item = self._mint(target, target_args, conversation_id,
                                  summary=f"{plan.question}［{target.risk.value} 级］")
                item.target_tool = target.name
            else:
                item = self._mint(spec, args, conversation_id, summary=plan.question)
            item.kind = "confirmation"
            # 等待者与条目**同时**建好（都在锁内），见 `_waiters` 的说明
            self._waiters[item.id] = Future()
        logger.info("实时确认已登记：%s（%s → %s）",
                    item.id, spec.name, item.target_tool or "（未绑定调用）")
        return item.id

    def _mint(
        self, spec: ToolSpec, args: BaseModel, conversation_id: str, summary: str
    ) -> PendingAction:
        """建一条待确认行（**不查重**）。调用方必须已持有 `self._lock`。"""
        args_dump = redact_args(_dump(args))
        item = PendingAction(
            id=uuid.uuid4().hex[:12],
            tool=spec.name,
            plugin=spec.plugin_id,
            risk=spec.risk.value,
            args=args_dump,
            summary=summary or summarize(spec, args_dump, model=args),
            created_at=_now_ms(),
            conversation_id=conversation_id,
            fingerprint=_fingerprint(spec.name, args),
        )
        self._items[item.id] = item
        self._record(item)
        logger.info("待确认操作：%s（%s，%s）", item.id, item.tool, item.risk)
        return item

    # ------------------------------------------------------------ 决策

    def approve(self, pending_id: str, *, by: str = "user") -> PendingAction | None:
        """放行：兑换单次令牌（**一次性**），并叫醒正在等答复的对话循环（如果有）。"""
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
            self._settle_locked(item.id, approved=True)
            return item

    def reject(self, pending_id: str, reason: str = "", *, by: str = "user") -> PendingAction | None:
        with self._lock:
            item = self._items.get(pending_id)
            if item is None or item.status != "pending":
                return None
            item.status = "rejected"
            item.decided_at = _now_ms()
            item.decided_by = by
            item.decided_reason = reason
            if reason:
                item.summary = f"{item.summary}（用户拒绝：{reason}）"
            self._record(item)
            self._settle_locked(item.id, approved=False)
            return item

    def expire(self, pending_id: str, reason: str = "已过期") -> None:
        """把一条**未决**的待确认标成到期，并叫醒等待者（未同意）。

        超时/中断都必须走这一步：否则一条"晾过的"确认还停在 `pending`，
        用户回来随手一点就成了事后通行证——这是"确认"与"批准"共用一张表
        带来的唯一真实风险，靠这里堵住。
        """
        with self._lock:
            item = self._items.get(pending_id)
            if item is not None and item.status == "pending":
                item.status = "expired"
                item.decided_at = _now_ms()
                self._record(item)
                logger.info("确认到期：%s（%s）", pending_id, reason)
            self._settle_locked(pending_id, approved=False, expired=True)

    # ------------------------------------------------------------ 等待答复

    async def wait_decision(self, pending_id: str, timeout_s: float) -> ConfirmOutcome:
        """等用户答复（`ToolHub.await_confirmation` 的实现）。

        这是**唯一**会阻塞对话循环的地方，因此四件事都要做对：

        - **答复可能先到**。界面在 `confirm_required` 事件里就能点「同意」，
          而那个事件**早于**本方法被调用（循环要先 yield 出去，界面才看得见）。
          所以答复必须能存住、后到的等待者能取到——否则用户明明答复了，
          循环还要干等到自己的超时。
        - **超时 → 标 expired 并按未同意返回**。绝不"等不到就算默认同意"。
        - **被取消 → 同样标 expired**。用户关掉页面 / SSE 断开时 `wait_for` 会把
          这个协程取消掉；此时若什么都不做，那条 pending 就留在了 `pending`。
        - **答复文本在这里拼**（`_answer`），而不是在 `approve()` 里：
          只有这里同时看得见"用户点了什么"和"这条确认绑定了哪次调用"。
        """
        timeout_s = float(timeout_s) if timeout_s and timeout_s > 0 else DEFAULT_CONFIRM_TIMEOUT_S
        with self._lock:
            early = self._decisions.pop(pending_id, None)
            fut: Future[ConfirmOutcome] | None = None
            if early is None:
                fut = self._waiters.get(pending_id)
                if fut is None:
                    # 正常路径上不该发生（登记时已建好）；真发生了也不能当"已同意"
                    fut = Future()
                    self._waiters[pending_id] = fut

        try:
            if early is not None:
                return self._finish(pending_id, early)
            outcome = await asyncio.wait_for(asyncio.wrap_future(fut), timeout=timeout_s)
            return self._finish(pending_id, outcome)
        except asyncio.TimeoutError:
            self.expire(pending_id, "用户未在限时内答复")
            return self._finish(pending_id, ConfirmOutcome(
                approved=False, pending_id=pending_id, expired=True))
        except BaseException:
            # CancelledError 也要落到这里：对话被中断，等待者没了
            self.expire(pending_id, "对话已中断")
            raise
        finally:
            # 无论走哪条路都把存下来的答复清掉，`_decisions` 不会随对话增长
            with self._lock:
                self._decisions.pop(pending_id, None)

    def _finish(self, pending_id: str, outcome: ConfirmOutcome) -> ConfirmOutcome:
        """补上给模型看的答复文本。**必须在锁外调用**（`get()` 自己会抢锁）。"""
        return ConfirmOutcome(
            approved=outcome.approved, expired=outcome.expired, pending_id=pending_id,
            answer=self._answer(self.get(pending_id), approved=outcome.approved,
                                expired=outcome.expired),
        )

    def _answer(self, item: PendingAction | None, *, approved: bool, expired: bool) -> str:
        """把答复拼成给模型看的一句话（工具观测的正文）。"""
        target = item.target_tool if item is not None else ""
        if approved:
            if target:
                return (f"用户已同意这次操作。现在可以调用 `{target}`——**参数必须与申请时"
                        "完全一致**：服务端按「工具 + 参数指纹」认领这次批准，"
                        "换了参数会重新走一遍风险确认门。")
            return "用户已同意继续。"
        if expired:
            return ("用户没有在限时内答复，已按**未同意**处理。不要重复发起同一次确认；"
                    "如果还需要，先用自然语言问用户是否方便。")
        reason = (item.decided_reason if item is not None else "").strip()
        return ("用户**拒绝**了这次操作。" + (f"理由：{reason} " if reason else "")
                + "不要重试，也不要换一个工具绕过去；按用户的理由换个做法，或直接问他要怎么办。")

    # ------------------------------------------------------------ 确认门

    def gate(
        self, spec: ToolSpec, args: BaseModel, token: str, conversation_id: str = ""
    ) -> tuple[bool, str, str]:
        """`ToolHub` 的确认门实现：返回 (放行?, pending_id, 需确认原因)。

        放行有**两条**通道，都是"一次性"的：

        | 通道 | 谁用 | 凭据 |
        |---|---|---|
        | 令牌 | HTTP 链路（`POST /api/agent/invoke`） | `approval_token`，由 `/pending/{id}/approve` 换得 |
        | **认领** | **对话链路**（`harness/loop.py`） | 无需令牌：按"工具 + 参数指纹"认领一条**已批准**的条目 |

        第二条是 2026-09-20 补的，修的是 P0-A：前端刻意**不自动重发**那次调用
        （"放行是人做的决定"），所以用户放行后会让模型**再试一次**；
        而模型的重试走 `harness/loop.py`，那里**拿不到令牌**（令牌只回给了批准它的
        那一次 HTTP 响应，不回 DOM、不落日志）。改造前的结果是：
        **危险工具在对话链路里永远无法执行**，每次重试都新建一条 pending（已实测复现）。
        """
        if token:
            with self._lock:
                self._expire_locked()
                pending_id = self._by_token.pop(token, "")
                item = self._items.get(pending_id) if pending_id else None
                if item is not None and item.status == "approved":
                    # 令牌一次性：pop 掉即作废，防止"批准一次、重放多次"
                    item.token = ""
                    item.status = "consumed"
                    self._record(item)
                    return True, item.id, ""
            # 令牌无效/已用过/已过期 → 当作没批准，重新申请
            logger.info("确认令牌无效或已失效，重新申请确认：%s", spec.name)

        claimed = self.claim_approved(spec, args)
        if claimed is not None:
            return True, claimed.id, ""

        item = self.request(spec, args, conversation_id)
        return False, item.id, item.summary

    def claim_approved(self, spec: ToolSpec, args: BaseModel) -> PendingAction | None:
        """无令牌时，认领一条**已批准且参数指纹相同**的条目（P0-A 的修法）。

        ## 安全性没有放松

        认领的门槛与令牌**完全一致**，只是"凭据"从令牌换成了服务端内存里的事实：

        - 同一**工具**；
        - 同一**参数指纹**（`_fingerprint`，未打码的那份）；
        - 仍在 **5 分钟**令牌有效期内（`_expire_locked` 会把过期的 approved 变 `expired`）；
        - **一次性**——认领后置 `consumed`，第二次重试必须重新申请。

        ## 为什么权威放在内存里是对的

        与 `approvals` 既有的"权威状态在内存、DB 只留痕"完全一致：单机单人应用，
        待确认操作的生命周期是几十秒，跨进程重启后**本来就应该失效**
        （`install()` 会把遗留的 pending/approved 标 expired）。
        """
        fingerprint = _fingerprint(spec.name, args)
        with self._lock:
            self._expire_locked()
            for item in self._items.values():
                if (item.status == "approved" and item.tool == spec.name
                        and item.fingerprint == fingerprint):
                    item.token = ""
                    self._by_token.pop(item.token, None)
                    item.status = "consumed"
                    self._record(item)
                    logger.info("确认门：按已批准条目放行（认领）%s（%s）",
                                spec.name, item.id)
                    return item
        return None

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

    def _settle_locked(
        self, pending_id: str, *, approved: bool, expired: bool = False
    ) -> None:
        """叫醒等这条确认的对话循环。**调用方必须已持有 `self._lock`**。

        刻意做成"要求在锁内调用"而不是自己抢锁：`_expire_locked` 已经在锁内，
        自己抢锁就是**自死锁**（`threading.Lock` 不可重入）——而这类死锁只在
        "TTL 恰好到期且恰好有人在等"时出现，最难复现。

        `Future.set_result` 在锁内调用是安全的：它只是把回调排进事件循环
        （`call_soon_threadsafe`），不阻塞、也不会回头抢这把锁。
        """
        fut = self._waiters.pop(pending_id, None)
        if fut is None or fut.done():
            # 没人在等（风险门的普通待确认），或等待者已经放弃（超时后 future 被取消）
            return
        outcome = ConfirmOutcome(approved=approved, expired=expired, pending_id=pending_id)
        fut.set_result(outcome)
        # ⚠️ 还要**存一份**：答复可能先于等待者到达（界面在 `confirm_required` 事件里
        # 就点了同意，而循环那时还没走到 `wait_decision`）。不存的话，`wait_decision`
        # 到 `_waiters` 里已经找不到东西，只能新建一个永远不会被 resolve 的 future
        # ——用户明明答复了，循环却干等满一个超时（实测复现过）。
        # 清理由 `wait_decision` 的 `finally` 负责。
        self._decisions[pending_id] = outcome

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
                # 有人正在等的话必须一起叫醒：否则那个循环会一直等到自己的超时，
                # 而条目早已不是 pending —— 用户这时候点「同意」只会拿到 409
                self._settle_locked(item.id, approved=False, expired=True)

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


def summarize(spec: ToolSpec, args: dict[str, Any], *, model: BaseModel | None = None) -> str:
    """给用户看的一句话：做什么 + 影响谁。

    刻意不做成"工具名 + JSON"——用户要判断的是**后果**，不是参数名。

    `ToolSpec.summary_fn` 优先（只有它知道这个工具的"后果"该怎么讲，
    见 `factory_create`）；它没给、返回空串、或抛异常时一律退回通用口径。
    """
    custom = _custom_summary(spec, model)
    if custom:
        return custom
    parts = [f"{spec.description}"]
    details = [f"{_SUMMARY_LABEL.get(k, k)}={v}" for k, v in args.items() if v not in (None, "", [])]
    if details:
        parts.append("（" + "，".join(details) + "）")
    parts.append(f"［{spec.risk.value} 级］")
    return "".join(parts)


def _custom_summary(spec: ToolSpec, model: BaseModel | None) -> str:
    """调 `summary_fn`。**任何失败都退回空串**——摘要坏掉不能让确认框弹不出来。

    传的是**已校验的参数模型**而不是打码后的 dict：摘要实现要读的往往是
    "这份蓝图里有什么"，而打码后的 dict 可能缺键。
    """
    if spec.summary_fn is None or model is None:
        return ""
    try:
        return str(spec.summary_fn(model) or "").strip()
    except Exception:  # noqa: BLE001 —— 见上
        logger.warning("summary_fn 失败，退回通用摘要：%s", spec.name, exc_info=True)
        return ""


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
    hub.set_confirmation_gate(
        lambda spec, args, plan, ctx: pending_store().request_confirmation(
            spec, args, plan, ctx.conversation_id
        )
    )
    hub.set_confirmation_waiter(
        lambda pending_id, timeout_s: pending_store().wait_decision(pending_id, timeout_s)
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
