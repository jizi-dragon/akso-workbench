"""Agent 缓存：把 DeepSeek 的「上下文硬盘缓存」从"碰运气"变成可测可优化。

## 为什么需要这一层（官方规则，2026-09-15 核对）

DeepSeek 的 [上下文硬盘缓存](https://api-docs.deepseek.com/zh-cn/guides/kv_cache) 是**默认开启、
自动生效**的，但命中条件很硬：

> 后续请求必须**完整匹配**一个已持久化的**缓存前缀单元**（cache prefix unit）才算命中。

前缀单元在三个地方产生：① 每次请求的**用户输入末尾**与**模型输出末尾**；② 系统检测到多次请求
的公共前缀时；③ 长输入按固定 token 间隔切分。**"完整匹配"是关键**——前缀里改一个字节，
从那一字节起全部算 cache miss。

价格差是本层存在的全部理由（`deepseek-flash`，1M input tokens）：

| | 缓存命中 | 缓存未命中 | 倍率 |
|---|---|---|---|
| 非高峰 | $0.003 | $0.15 | **50×** |
| 高峰 | $0.006 | $0.30 | **50×** |

## 本层做的三件事

1. **稳定前缀（stable prefix）**：本模块保证每次请求的 messages 满足
   `本轮 = [system] + [冻结的摘要（如有）] + [原文历史] + [新消息]`，
   且**已发出的前缀永不改写**（append-only）。
   ★ 首条 system **永不进折叠域**（I15）——它由 `_pin_system` 摘出，折叠只能动它之后的正文。
   旧实现把 system 当成一个可折叠的轮次，导致折叠一次之后原始提示词就消失（见 `_pin_system`）。
2. **摘要暂存**：改造前 `_manage_context` 每轮都重新调 LLM 生成摘要——既慢、又烧 token、
   还让前缀每轮都变（缓存必 miss）。现在摘要**按冻结边界持久化**，只在需要时重算一次。
3. **命中量入账**：官方在 `usage` 里回 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`
   （Responses API 里叫 `input_tokens_details.cached_tokens`）。把它落库并算命中率——
   **没有度量就没有优化**，之前这些字段是被整个丢掉的。

## 一条容易被忽略的坑

`prompt_cache_hit_tokens` 是**省钱的量**，但**缓存不会降低输出随机性**，也不保证命中
（官方原文：best-effort，缓存几小时到几天不用就清）。因此本层的目标是**提高命中率**，
而不是"保证命中"——统计表里同时记 hit 与 miss，才能看出真实比例。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from . import db
from .services.storage import now_ms

logger = logging.getLogger("workbench.cache")

#: 用量入账失败的**进程内**累计次数。见 `UsageStats.persist` 与 `usage_persist_failures`。
_USAGE_PERSIST_FAILURES = 0


def usage_persist_failures() -> int:
    """入账失败累计次数（`/api/agent/selfcheck` 暴露它）。

    它存在只为一件事：**把"刻意吞掉的失败"变成"看得见的失败"**。
    与 `sandbox` 的审计口径同族 —— 一条永远不会被读到的 warning 等于没有。
    """
    return _USAGE_PERSIST_FAILURES


# ---------------------------------------------------------------- 摘要暂存


@dataclass
class SummaryState:
    """一个会话的冻结摘要状态（落库，跨轮次跨进程稳定）。"""

    conversation_id: str
    summary: str = ""
    summarized_upto: int = 0
    """摘要覆盖到的**正文**消息条数（= 摘掉首条 system 之后、按 created_at 正序的前 N 条）。

    用条数而不是消息 id：历史消息不会删除也不会重排，条数是单调递增的，
    天然满足"冻结边界只前进、不回退"这一缓存前提。

    ⚠️ **口径说明（2026-09 修正，I15）**：它**不含**首条 system。旧实现按"整份对话
    （含 system）"计数，于是折叠时边界会迈过 system 把它一起并进摘要 —— 原始提示词
    就此消失。迁移 22 把历史数据整体减 1，使**被折叠的消息集合逐条不变**。
    """
    updated_at: int = 0

    @property
    def frozen(self) -> dict:
        """已经冻进前缀的那条 system 消息（没有摘要时为空）。"""
        if not self.summary:
            return {}
        return {"role": "system", "content": f"历史对话摘要：\n{self.summary}"}


def load_state(conversation_id: str) -> SummaryState:
    if not conversation_id:
        return SummaryState(conversation_id="")
    row = db.query_one(
        "SELECT summary, summarized_upto, updated_at FROM conversation_context WHERE conversation_id = ?",
        (conversation_id,),
    )
    if not row:
        return SummaryState(conversation_id=conversation_id)
    return SummaryState(
        conversation_id=conversation_id,
        summary=row["summary"] or "",
        summarized_upto=int(row["summarized_upto"] or 0),
        updated_at=int(row["updated_at"] or 0),
    )


def freeze_summary(conversation_id: str, summary: str, upto: int) -> SummaryState:
    """把摘要冻到指定边界（只前进）。"""
    if not conversation_id:
        return SummaryState(conversation_id="")
    state = load_state(conversation_id)
    if upto <= state.summarized_upto:
        return state
    db.execute(
        "INSERT INTO conversation_context (conversation_id, summary, summarized_upto, updated_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(conversation_id) DO UPDATE SET "
        "summary = excluded.summary, summarized_upto = excluded.summarized_upto, "
        "updated_at = excluded.updated_at",
        (conversation_id, summary, upto, now_ms()),
    )
    return SummaryState(conversation_id=conversation_id, summary=summary,
                        summarized_upto=upto, updated_at=now_ms())


def clear_state(conversation_id: str) -> None:
    db.execute("DELETE FROM conversation_context WHERE conversation_id = ?", (conversation_id,))


# ---------------------------------------------------------------- 前缀组装


@dataclass
class PrefixPlan:
    """一次请求的上下文计划：**稳定前缀 + 待追加**，同时说明是否需要重算摘要。"""

    messages: list[dict] = field(default_factory=list)
    truncated: list[dict] = field(default_factory=list)
    """尚未进摘要、需要本次折叠的历史（空 = 无需重算摘要，前缀保持不变）。"""

    def needs_summary(self) -> bool:
        return bool(self.truncated)


def _pin_system(conversation: list[dict]) -> tuple[dict | None, list[dict]]:
    """把首条 system 摘出折叠域（不变量 I15）。

    返回 `(pinned, body)`。`pinned is None` 表示这份对话没有 system 首条
    （例如摘要器自己的那次调用），此时 `body` 就是原样输入。

    **为什么必须单独摘**：`harness.context.split_turns` 只按 `role == "user"` 切轮，
    而 system 后面紧跟 user ⇒ system **自成第一个 turn** ⇒ `_split_keep` 把它算进
    `older` ⇒ 它被并进摘要 ⇒ 请求里只剩 `{"role":"system","content":"历史对话摘要：…"}`，
    **原始提示词消失**。失败形态是"长会话越用越不听话"，且不报错。
    """
    if conversation and conversation[0].get("role") == "system":
        return conversation[0], conversation[1:]
    return None, list(conversation)


def _compose(pinned: dict | None, body: list[dict], summarized: dict) -> list[dict]:
    """按契约拼装：`[system] + [冻结摘要?] + [正文历史]`（不变量 I3 / I5）。"""
    head = [pinned] if pinned is not None else []
    if summarized:
        head.append(summarized)
    return [*head, *body]


def plan_prefix(
    conversation: list[dict],
    state: SummaryState,
    *,
    window_tokens: int,
    threshold: float,
    keep_recent_turns: int,
    tools_tokens: int = 0,
    factor: float = 1.0,
) -> PrefixPlan:
    """把完整对话切成「稳定前缀 + 待追加」。

    返回值保证：`plan.messages[0]` **恒为原始 system 提示词**（I15），
    其后是 `[冻结摘要?] + 正文历史`；边界（`summarized_upto`）**只在折叠时前进**
    —— 因此已发出的前缀逐字节不变，DeepSeek 侧能命中上一轮请求末尾产生的缓存前缀单元。

    两条**不要改**（各自对应一条已经发生过的静默故障）：

    1. **不要**改成"每轮按最近 N 轮裁剪"：那样前缀每轮都在滑动，缓存命中率恒为 0。
    2. **不要**把 system 放回折叠域：见 `_pin_system`。`summarized_upto` 索引的是
       **摘掉 system 之后的正文**（body），所以它与"会话消息总条数"不再相差 0。
    """
    pinned, body = _pin_system(conversation)
    tail = body[state.summarized_upto:]
    messages = _compose(pinned, tail, state.frozen)

    if not _over_budget(messages, window_tokens, threshold, tools_tokens, factor):
        return PrefixPlan(messages=messages, truncated=[])

    # 超预算才折叠：「摘要边界之后、最近 keep_recent_turns 轮之前」那一段并进摘要。
    # 这一步让前缀变一次（摘要变长），之后连续多轮重新稳定。
    kept_recent, _older = _split_keep(body, keep_recent_turns)
    recent_ids = {id(m) for m in kept_recent}
    fold = [m for m in tail if id(m) not in recent_ids]
    if not fold:
        # 已经只剩最近几轮还超预算 —— 没有可折叠的了，如实返回（不装作已处理）
        return PrefixPlan(messages=messages, truncated=[])

    fold_upto = state.summarized_upto + len(fold)
    plan = PrefixPlan(
        messages=_compose(pinned, body[fold_upto:], state.frozen),
        truncated=fold,
    )
    # 装配期断言（I15）：折叠**永远**不许动到原始 system。
    # 放在这里而不是测试里 —— 它是每次请求的必经之路，坏掉时会立刻炸而不是静默降级。
    assert pinned is None or plan.messages[0] is pinned, "折叠吞掉了 system 提示词（I15）"
    return plan


def _split_keep(body: list[dict], keep_recent_turns: int) -> tuple[list[dict], list[dict]]:
    """按轮切分正文（**入参必须是摘掉 system 之后的 body**，见 `_pin_system`）。"""
    from .harness.context import split_turns

    turns = split_turns(body)
    if len(turns) <= keep_recent_turns:
        return body, []
    kept = [m for turn in turns[-keep_recent_turns:] for m in turn]
    older = [m for turn in turns[:-keep_recent_turns] for m in turn]
    return kept, older


def _over_budget(messages: list[dict], window_tokens: int, threshold: float,
                 tools_tokens: int = 0, factor: float = 1.0) -> bool:
    """折叠判定：**这个请求**是不是超了软门槛（计划 §7.1）。

    ★ 用 `estimate_messages_tokens + tools_tokens` 而不是只用前者：
    超窗的是**请求**，而工具 schema 是每一轮都发的常量（实测 18 个工具约 2,104 token）。
    不算它的话，窗口设小一点就**永远不会折叠** —— 而那正是
    `harness/loop.py` 记下的那个实测故障（"预算 1000 token 时第 5 次请求已 12,059，
    而折叠发生 0 次"）。

    ⚠️ `tools_tokens` **缺省 0**：没有工具面的调用方行为与从前**逐字相同**，
    不需要知道这个参数存在。

    ★ `factor` 是**校准系数**（计划 §7.1(b)，见 [ADR-0024](../docs/adr/0024-token-calibration-from-usage.md)）：
    字数估算与真实分词**永远有偏差**，而 API 每次都回报真值。
    用它把估算放大/缩小到接近真值，折叠判定才不会"该折的时候不折"。
    ⚠️ **缺省 1.0** —— 没有样本时行为与从前逐字相同；调用方不该被迫知道这个参数存在。
    """
    from .harness.context import estimate_messages_tokens

    scale = float(factor) if float(factor) > 0 else 1.0
    estimate = (estimate_messages_tokens(messages) + max(0, int(tools_tokens))) * scale
    return estimate > int(window_tokens * threshold)


# ---------------------------------------------------------------- 用量入账


@dataclass
class UsageStats:
    """一次回答的 token 用量与缓存命中情况。"""

    conversation_id: str = ""
    model: str = ""
    calls: int = 0
    prompt_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    summary_calls: int = 0
    """其中用于「重算历史摘要」的调用数（这些是纯开销，应当趋近于 0）。"""
    latency_ms: int = 0
    last_prompt_tokens: int = 0
    """**最近一次**调用的 `prompt_tokens` —— 也就是"这一轮实际发上去的上下文有多大"。

    为什么用它而不是自己估：这是**平台回的真值**，而 `estimate_tokens` 只是估算
    （CJK 按字、英文按词）。界面上"上下文用量"那个数字必须是真的，否则用户会照着
    一个偏低的数去判断"还能聊多久"。
    """

    def add(self, usage: dict) -> None:
        """累计一次 API 响应的 usage（Chat Completions 与 Responses 两种口径都吃）。"""
        self.calls += 1
        hit = usage.get("prompt_cache_hit_tokens")
        miss = usage.get("prompt_cache_miss_tokens")
        prompt = usage.get("prompt_tokens")
        if hit is None and miss is None:
            # Responses API 口径：input_tokens + input_tokens_details.cached_tokens
            details = usage.get("input_tokens_details") or {}
            hit = details.get("cached_tokens") or 0
            prompt = usage.get("input_tokens") or prompt
            miss = max(0, int(prompt or 0) - int(hit or 0))
        hit = int(hit or 0)
        miss = int(miss or 0)
        self.last_prompt_tokens = int(prompt or (hit + miss))
        self.cache_hit_tokens += hit
        self.cache_miss_tokens += miss
        self.prompt_tokens += int(prompt or (hit + miss))
        self.completion_tokens += int(usage.get("completion_tokens")
                                      or usage.get("output_tokens") or 0)
        output_details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
        self.reasoning_tokens += int(output_details.get("reasoning_tokens") or 0)

    @property
    def hit_rate(self) -> float:
        total = self.cache_hit_tokens + self.cache_miss_tokens
        return round(self.cache_hit_tokens / total, 4) if total else 0.0

    def to_api(self) -> dict:
        """给前端的事件字段（camelCase）。"""
        return {
            "calls": self.calls,
            "promptTokens": self.prompt_tokens,
            "cacheHitTokens": self.cache_hit_tokens,
            "cacheMissTokens": self.cache_miss_tokens,
            "completionTokens": self.completion_tokens,
            "reasoningTokens": self.reasoning_tokens,
            "hitRate": self.hit_rate,
            "summaryCalls": self.summary_calls,
        }

    def persist(self) -> bool:
        """把这一轮的用量落库。**返回是否成功；失败不抛**（见下）。

        ⚠️ 主键必须是**不可预测地唯一**的。旧实现用 `f"{now_ms()}-{conversation_id[:8]}"`，
        同一毫秒内的两次入账（"摘要调用"与"回答收尾"经常挨在一起）会 PK 撞车 →
        `INSERT` 抛 IntegrityError → 被下面的 `except` 吞成一条 warning ⇒ **静默丢账**。
        而 `deepseek_usage` 是**唯一**的用量事实源，命中率与"省钱口径"都从它算 ——
        丢一条就永久偏小，且没有任何迹象。

        ## 为什么 `except` 保留、但要返回 `False` 并计数

        "度量失败绝不影响回答"是对的，所以这里**继续吞异常**。但"刻意吞掉"与
        "静默失败"的区别在于**有没有一个能被看见的地方**：从前只有一条 `logger.warning`，
        而持续失败的表现是"用量曲线莫名其妙变低"——没有任何迹象。
        现在失败会累进 `usage_persist_failures()`，由 `/api/agent/selfcheck` 暴露。
        """
        global _USAGE_PERSIST_FAILURES
        try:
            db.execute(
                "INSERT INTO deepseek_usage (id, conversation_id, model, calls, prompt_tokens, "
                "cache_hit_tokens, cache_miss_tokens, completion_tokens, reasoning_tokens, "
                "summary_calls, latency_ms, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, self.conversation_id, self.model,
                 self.calls, self.prompt_tokens, self.cache_hit_tokens, self.cache_miss_tokens,
                 self.completion_tokens, self.reasoning_tokens, self.summary_calls,
                 self.latency_ms, now_ms()),
            )
            return True
        except Exception:  # noqa: BLE001 —— 度量失败绝不影响回答
            _USAGE_PERSIST_FAILURES += 1
            logger.warning("用量入账失败（进程内累计 %d 次）",
                           _USAGE_PERSIST_FAILURES, exc_info=True)
            return False


def usage_overview(days: int = 7, conversation_id: str = "") -> dict:
    """近 N 天的缓存命中概览（设置页与 `/api/agent/cache` 用）。

    `conversation_id` 非空时**只统计这一段对话** —— 输入框下面那行常驻的
    「缓存命中 / 命中率 / 上下文用量」（2026-09-22 用户下达）要的是"这段对话花了多少"，
    而不是"最近 7 天所有对话加起来"。它同时是**刷新页面后还能看到**那份数字的来源
    （`usage` 事件只在回答收尾时发一次，刷新就没了）。
    """
    since = now_ms() - days * 86_400_000
    where = "created_at >= ?"
    params: list[Any] = [since]
    if conversation_id:
        where += " AND conversation_id = ?"
        params.append(str(conversation_id))
    row = db.query_one(
        "SELECT COUNT(*) AS n, SUM(prompt_tokens) AS prompt, SUM(cache_hit_tokens) AS hit, "
        "SUM(cache_miss_tokens) AS miss, SUM(completion_tokens) AS completion, "
        "SUM(summary_calls) AS summary_calls FROM deepseek_usage WHERE " + where,
        tuple(params),
    ) or {}
    hit = int(row.get("hit") or 0)
    miss = int(row.get("miss") or 0)
    total = hit + miss
    return {
        "days": days,
        "conversationId": conversation_id,
        "answers": int(row.get("n") or 0),
        "promptTokens": int(row.get("prompt") or 0),
        "cacheHitTokens": hit,
        "cacheMissTokens": miss,
        "completionTokens": int(row.get("completion") or 0),
        "summaryCalls": int(row.get("summary_calls") or 0),
        "hitRate": round(hit / total, 4) if total else 0.0,
        # 省钱口径：命中价按未命中价的 1/50 计（官方 2026-09-15 价格表）
        "savedRatio": round(hit / total * 0.98, 4) if total else 0.0,
    }
