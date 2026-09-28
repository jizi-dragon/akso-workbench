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

## 与"环境强制确认"（页面级闸门）的关系

本仓曾有第二条同类原则（页面级：`confirmed` 必须为 true，否则 409），它随配置工厂整体移除
（2026-09-22）——现在的产品里**没有页面级的写入确认**了。那条是**页面级**的（前端勾选框）；
这条是**工具级**的，作用于 Agent 对话链路。给任何新的页面级写入入口做闸门时，
照这条的口径来：**页面勾选不算数，执行侧才算数**。

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
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from . import db
from .plugin_api import (
    DEFAULT_CONFIRM_TIMEOUT_S,
    ZERO_ARGS,
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

_MAX_STASHED_DECISIONS = 64
"""`_decisions`（"答复先到一步"的暂存区）的硬上限，见 `_trim_decisions_locked`。

64 是"远大于任何真实并发确认数、又小到不可能成为内存问题"的取值：
对话链路每答一条就 `pop` 一条，只有**HTTP 面**（没有循环在等）会留下来。"""


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

    tainted: bool = False
    """**建这条待确认时，本回合是否已经读过外部不可信内容**（不变量 I18）。

    ★ 它**不参与任何放行判定** —— 一次"读过外部内容"不会让批准变得更难或更容易。
    它只做一件事：让**用户**在按下同意之前知道"这次操作可能是被别人诱导的"。
    用户需要的是**后果**，不是机制名（`summarize()` 的既有取向）。

    为什么不做成硬闸门（"有 untrusted 就不许调写工具"）：那会与"用户明确要求写入"
    的日常场景直接冲突 —— 一次正常的"看看这个网页，然后照它建个对象"会被拦死。
    判断与理由见 ADR-0022。
    """

    target_tool: str = ""
    """`request_user_confirmation` 预先批准的那次调用的工具名（空 = 纯意图确认，不绑定调用）。

    **刻意不落库**：`_record()` 的列里没有它。它是"这次确认绑定到哪一次调用"的运行时事实，
    跨进程重启本来就该失效（见模块文档：不恢复遗留 pending）。
    """

    decided_reason: str = ""
    """用户拒绝时填写的理由（原样，未打码——它由用户自己写下，且会回给模型）。"""

    options: list[dict[str, Any]] = field(default_factory=list)
    """这次确认的候选项。**非空 = 让用户在卡片上挑一个**（见 `plugin_api.ConfirmPlan.options`）。

    **不落库**（`_record()` 的列里没有它）：与 `target_tool` / `kind` 同一条理由——
    它是运行期事实，而 DB 迁移只增不改，不值得为它开一列。重启后遗留的 pending
    本来就一律标 `expired`，那批卡片也就没有意义了。
    """

    decided_value: str = ""
    """用户在卡片上选中那一项的 `value`（空 = 不是选择型确认，或还没选）。

    它和 `decided_reason` 不是一回事，别混：那是用户随手写的理由（给人看的），
    这是**要当参数用下去的 id**（给机器用的）。所以它必须原样进 `_answer()`——
    模型靠它知道"余下的对话该用哪个账号"，而不必再问一遍。
    """

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
            # 非空 = 这条是「挑一个」。界面刷新后靠 `recoverPending()` 重新弹出来，
            # 所以卡片数据必须跟着 `public()` 走，不能只活在当初那个 SSE 事件里。
            "options": [dict(option) for option in self.options],
            "decidedValue": self.decided_value,
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
        self._selections: dict[str, dict[str, Any]] = {}
        """会话 id → 用户在卡片上最近一次选中的那一项。

        只服务界面（刷新后把右下角那张"当前账号"卡片画回来），**不落库**：
        它记的是"这一轮对话用哪个账号"，跨应用重启本就该重新问
        ——与模块文档里"不恢复遗留 pending"同一条理由。
        """
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

    def request(self, spec: ToolSpec, args: BaseModel, conversation_id: str = "",
                tainted: bool = False) -> PendingAction:
        """建一条待确认（同一工具+同一参数在有效期内**复用**，避免刷屏）。

        `tainted`：本回合是否已经读过**外部不可信内容**（不变量 I18）。
        它只影响**给用户看的那句话**与留痕，不影响任何放行判定 —— 见 `gate()`。
        """
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
                    #
                    # ★ 复用也要补 taint：这一条可能是**读外部内容之前**建的，
                    # 而用户此刻看到的那句话必须反映**现在**的风险。
                    if tainted and not item.tainted:
                        item.tainted = True
                        item.summary += _TAINT_NOTE
                    return item
            return self._mint(spec, args, conversation_id, summary="",
                              tainted=tainted)

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
        options = [dict(option) for option in plan.options]
        with self._lock:
            self._expire_locked()
            if target is not None and target_args is not None:
                item = self._mint(target, target_args, conversation_id,
                                  summary=f"{plan.question}［{target.risk.value} 级］")
                item.target_tool = target.name
            else:
                item = self._mint(spec, args, conversation_id, summary=plan.question)
            item.kind = "confirmation"
            item.options = options
            # 等待者与条目**同时**建好（都在锁内），见 `_waiters` 的说明
            self._waiters[item.id] = Future()
        logger.info("实时确认已登记：%s（%s → %s）",
                    item.id, spec.name, item.target_tool or "（未绑定调用）")
        return item.id

    def _mint(
        self, spec: ToolSpec, args: BaseModel, conversation_id: str, summary: str,
        tainted: bool = False,
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
            tainted=tainted,
        )
        if tainted:
            # ★ 提示写在**摘要末尾**而不是别处：摘要就是用户在卡片上读到的那句话，
            # 而这条信息必须与那句"将要做什么"**在同一段文字里** —— 分成两处，
            # 用户会读到其中一处就当自己看完了。
            item.summary += _TAINT_NOTE
        self._items[item.id] = item
        self._record(item)
        logger.info("待确认操作：%s（%s，%s%s）", item.id, item.tool, item.risk,
                    "，本回合读过外部内容" if tainted else "")
        return item

    # ------------------------------------------------------------ 决策

    def approve(self, pending_id: str, *, value: str = "", by: str = "user") -> PendingAction | None:
        """放行：兑换单次令牌（**一次性**），并叫醒正在等答复的对话循环（如果有）。

        `value` 只在**选择型确认**（`item.options` 非空）时有意义：它是用户在卡片上
        点中的那一项。这里**必须校验它确实在候选项里**——一个不在候选里的 value
        会一路变成下游的参数，而下游没有任何地方能发现它是编的。

        选择型确认**必须有 value**：空 value 的"同意"没有任何含义（同意了什么？），
        所以它和"非法 value"一样被拒。拒掉是安全的默认——宁可让用户再点一次。
        """
        note: str = ""
        with self._lock:
            item = self._items.get(pending_id)
            if item is None or item.status != "pending":
                return None
            if item.options:
                allowed = {str(option.get("value") or "") for option in item.options}
                if value not in allowed:
                    logger.warning("选择型确认收到非法/缺失的 value=%r：%s（候选项 %d 个）",
                                   value, pending_id, len(item.options))
                    return None
                item.decided_value = value
                self._remember_selection_locked(item)
                if item.kind == "selection":
                    # **界面发起**的选择：模型没有任何工具观测可看（它没问过），
                    # 所以必须往会话里留一条 note，否则下一轮它照样不知道用哪个账号。
                    # 模型自己发起的那种**不留**：它马上会收到工具观测，
                    # 再留一条就是同一件事说两遍（而且是两个会漂的事实源）。
                    note = _selection_note(item)
            elif value:
                # 非选择型确认却带了 value：不是攻击面（`_answer` 根本不看它），
                # 但它是调用方的 bug，留痕而不是静默吞掉。
                logger.info("非选择型确认收到 value=%r，已忽略：%s", value, pending_id)
            item.status = "approved"
            item.token = secrets.token_urlsafe(24)
            item.token_expires_at = _now_ms() + TOKEN_TTL_MS
            item.decided_at = _now_ms()
            item.decided_by = by
            self._by_token[item.token] = item.id
            self._record(item)
            self._settle_locked(item.id, approved=True)
        # 留痕放在**锁外**：它是 DB 写入，没有理由占着确认门的锁
        if note:
            _append_conversation_note(item.conversation_id, note)
        return item

    def request_selection(
        self, spec: ToolSpec, options: list[dict[str, Any]],
        conversation_id: str, question: str,
    ) -> str:
        """登记一次**由界面发起**的"挑一个"（右下角那张卡的默认入口）。

        为什么它不是"一次确认"：没有任何对话循环在等它——用户是在**开口之前**
        先挑账号的。所以这里**不建等待者**（`_settle_locked` 在没等待者时会提前返回，
        见它的注释），批准它只做两件事：记住选择 + 往会话里留一条 note 让模型知道。

        它借用确认门那张表是有意的：批准路径、`value` 合法性校验、"必须有 value"
        这些安全属性**一条都不用重写**——换个入口就松开校验，才是真正危险的做法。
        """
        with self._lock:
            self._expire_locked()
            item = self._mint(spec, ZERO_ARGS(), conversation_id, summary=question)
            item.kind = "selection"
            item.options = [dict(option) for option in options]
        logger.info("界面发起的选择已登记：%s（%d 个候选）", item.id, len(item.options))
        return item.id

    def _remember_selection_locked(self, item: PendingAction) -> None:
        """记住"这个会话当前用哪个账号"（调用方必须已持有 `self._lock`）。

        界面靠它在**刷新页面之后**把右下角那张卡片画回来。刻意只活在进程内：
        用户选的是"这一轮对话用哪个账号"，跨应用重启本就该重新问一遍
        ——与模块文档里"不恢复遗留 pending"是同一条理由。
        """
        conversation_id = str(item.conversation_id or "")
        if not conversation_id:
            return
        option = next(
            (dict(o) for o in item.options
             if str(o.get("value") or "") == item.decided_value),
            {},
        )
        self._selections[conversation_id] = {
            "conversationId": conversation_id,
            "value": item.decided_value,
            "label": str(option.get("label") or item.decided_value),
            "description": str(option.get("description") or ""),
            "meta": dict(option.get("meta") or {}),
            "pendingId": item.id,
            "tool": item.tool,
            "at": item.decided_at,
        }

    def selection(self, conversation_id: str) -> dict[str, Any] | None:
        """这个会话当前选中的账号/环境（没有就返回 None）。"""
        with self._lock:
            found = self._selections.get(str(conversation_id or ""))
            return dict(found) if found else None

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
            chosen = (item.decided_value if item is not None else "").strip()
            if chosen:
                # 选择型确认的答复**不是**"同意"，而是"用这一个"。
                # 所以观测里必须同时给出 value（机器要用的 id）和人话标签（模型转述给用户时用），
                # 并明确"别再问一遍"——否则模型很容易把同一件事再确认一次，
                # 而用户会觉得自己刚才那一下白点了。
                label = next(
                    (str(o.get("label") or "") for o in (item.options if item else [])
                     if str(o.get("value") or "") == chosen),
                    "",
                )
                return (f"用户已选定：**{label or chosen}**（value=`{chosen}`）。"
                        "**接下来就用它**——把它当作本次任务的目标账号 / 环境，"
                        "不要再为同一件事问用户一遍；直到用户改口为止。")
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
        self, spec: ToolSpec, args: BaseModel, token: str, conversation_id: str = "",
        tainted: bool = False,
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

        item = self.request(spec, args, conversation_id, tainted=tainted)
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

        ## 没人等的时候也要**存一份**（2026-09-22 改，决策卡 G2）

        旧实现是"没有等待者就直接 return"——那条早退在**风险门**上是致命的：
        风险门的待确认**从来不提前建等待者**（旧设计假设"放行之后模型会重试"），
        于是用户放行时若那一刻没有等待者（本轮早已结束），这条答复**哪儿都不去**、
        界面表现为"我点了放行，什么都没发生"（2026-09-22 15:08 真机实测）。

        现在放行/拒绝一律存进 `_decisions`：后到的等待者（`wait_decision`）在锁内
        先取 `_decisions`、取不到才挂 future ⇒ **答复早到一步也不会丢**。
        `_decisions` 由 `wait_decision` 的 `finally` 清理，另有容量上限兜底（没人来取的条目）。
        """
        fut = self._waiters.pop(pending_id, None)
        if fut is not None and not fut.done():
            fut.set_result(ConfirmOutcome(approved=approved, expired=expired,
                                          pending_id=pending_id))
        # ⚠️ 无论有没有等待者都存一份：答复可能先于等待者到达（界面在 `confirm_required`
        # / `approval_required` 事件里就点了，而循环那时还没走到等待）。
        # 不存的话，等待者到 `_waiters` 里已经找不到东西，只能新建一个永远不会被 resolve 的
        # future ——用户明明答复了，循环却干等满一个超时（PITFALLS #57 实测复现过）。
        self._decisions[pending_id] = ConfirmOutcome(
            approved=approved, expired=expired, pending_id=pending_id)
        self._trim_decisions_locked()

    def _trim_decisions_locked(self) -> None:
        """给 `_decisions` 一个**硬上限**（调用方必须已持有 `self._lock`）。

        谁会留在里面：**HTTP 面**批准的那些条目——那条路没有对话循环在等，
        所以永远没人来 `pop`。数量级很小（一次批准一条），但"只增不减的字典"
        在本仓是明确的红线（见 `AGENT-TOOL-AUDIT` 里同族的内存问题），
        所以按插入序丢最老的。丢弃是安全的：`wait_decision` 取不到就走超时/未同意，
        **绝不会**把"没取到答复"当成同意。
        """
        overflow = len(self._decisions) - _MAX_STASHED_DECISIONS
        for _ in range(max(0, overflow)):
            self._decisions.pop(next(iter(self._decisions)), None)

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


_TAINT_NOTE = (
    "\n\n⚠️ 这一回合里模型**读过外部内容**（网页 / 搜索结果），"
    "有可能是在那些内容的引导下提出的这次操作。"
    "请核对一下：**这真的是你要它做的吗？**"
)
"""外部内容污染过的回合，给用户的额外提示（不变量 I18）。

★ 措辞是刻意的：**说后果，不说机制**。写成"本回合 taint=true"对用户毫无意义，
而他需要判断的是一件很具体的事 ——「这次操作是不是我自己要的」。
同 `summarize()` 的既有取向（用户要判断的是后果，不是参数名）。

★ 它**不改变放行判定**：一次"读过外部内容"不会让批准变得更难。
硬闸门（"有 untrusted 就不许调写工具"）会与"用户明确要求写入"直接冲突 ——
判断与理由见 ADR-0022。
"""


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

    `ToolSpec.summary_fn` 优先（只有它知道这个工具的"后果"该怎么讲）；
    它没给、返回空串、或抛异常时一律退回通用口径。
    > ⚠️ 当前**产品里没有任何工具声明 `summary_fn`**（唯一那个随配置工厂移除了），
    > 因此这里恒走通用口径；机制由 `tests/test_plugin_api.py` 的合成 spec 守着。
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


NOTE_ROLE = "note"
"""会话里表示"这不是谁说的话，是一件发生过的事"的消息角色。

为什么不用 `user`：界面会把它画成用户自己说的话（他没说过），而模型的上下文里
它又会被当成一条**用户指令**。它是**界面事件**，两者都不是。

⚠️ 这个角色**不能直接进模型请求**——DeepSeek / OpenAI 只认 `system` / `user` /
`assistant` / `tool`。映射发生在唯一那个扼流点（`api/routes_chat.py` 的
`_chat_events`），别在别处再拼一遍。
"""


def _selection_note(item: PendingAction) -> str:
    """界面选完账号之后留给模型的那句话。"""
    chosen = next(
        (o for o in item.options if str(o.get("value") or "") == item.decided_value), {})
    label = str(chosen.get("label") or item.decided_value)
    env = str(chosen.get("description") or "")
    where = f"（{env}）" if env else ""
    return (f"用户在界面上选定了执行账号：**{label}**{where}，id=`{item.decided_value}`。"
            "后续步骤默认用它，不要再为同一件事问一遍。")


def _append_conversation_note(conversation_id: str, note: str) -> None:
    """把一条界面事件写进会话历史。

    **失败不影响选择本身**（与 `_record` 同一条口径）：用户已经选好了，那是事实；
    留痕失败只意味着模型下一轮可能再问一次，不该让用户看到一次报错。
    """
    if not conversation_id:
        return
    try:
        from .services import storage

        storage.insert_message(conversation_id, NOTE_ROLE, note)
    except Exception:  # noqa: BLE001
        logger.warning("会话留痕失败（选择本身仍然生效）：%s", conversation_id, exc_info=True)


def install(hub: ToolHub) -> None:
    """把审计写入器 + 确认门装到工具中枢上（`get_hub()` 装配期调用一次）。

    门用 **lambda 而不是 `pending_store().gate`**：闭包里若是当初那个实例，
    `reset_store()`（测试隔离用）之后中枢还捏着旧店 —— 表现为"申请到 pendingId
    却查不到 / 批不了"，而且只在测试里出现、生产看不出，最难查的那种。
    """
    hub.set_audit_writer(write_audit)
    hub.set_approval_gate(
        lambda spec, args, token, conversation_id, tainted=False: pending_store().gate(
            spec, args, token, conversation_id, tainted
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
    hub.set_option_provider(option_provider)
    _expire_orphans()


def option_provider(source: str) -> list[dict[str, Any]]:
    """候选项来源 → 候选项（注入给 `ToolHub`，见 `plugin_api.set_option_provider`）。

    **未知来源一律抛**：`plugin_api` 会把它变成一句模型能照做的观测。
    退回空列表是不行的——那看起来像"用户没有可选的东西"，而事实是"这个来源不存在"，
    两者对模型意味着完全不同的下一步。

    这里刻意只有 `accounts` 一个来源。要加新来源，先回答一个问题：
    **它的真值在哪张表里？** 如果答案是"在模型嘴里"，那它就不该做成来源
    ——那正是"让模型自己编候选项"的老路，而卡片上的 `value` 是要当参数用下去的。
    """
    key = str(source or "").strip().lower()
    if key in {"accounts", "account"}:
        from .services.accounts import account_options

        return account_options()
    raise ValueError(f"未知的候选项来源 {source!r}（目前只支持 'accounts'）")


def current_selection(conversation_id: str) -> dict[str, Any] | None:
    """这个会话当前选中的账号 / 环境（没有就 None）。

    界面刷新后靠它把右下角那张卡片画回来——否则用户会觉得"我选过的东西丢了"，
    而模型那边其实还记得（它看得见会话历史里那条观测）。两边的认知不能不一致。
    """
    return pending_store().selection(conversation_id)


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
