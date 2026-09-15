"""Agent 缓存验收：稳定前缀 / 摘要冻结 / 命中量入账 / 模型名归一。

为什么这些断言值得写：DeepSeek 的上下文缓存命中价是未命中价的 **1/50**
（[官方价格表](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)），
而命中条件是"**完整匹配**一个缓存前缀单元"——前缀里改一个字节就全 miss。
所以"前缀稳定"不是风格问题，是钱的问题，必须被测试钉住。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from workbench import cache, db
from workbench.harness.context import estimate_messages_tokens
from workbench.services.deepseek import DEFAULT_MODEL, normalize_model


@pytest.fixture()
def ready():
    db.connect()
    return db


def msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def conversation(turns: int, body: str = "内容") -> list[dict]:
    out = [msg("system", "系统提示")]
    for i in range(turns):
        out.append(msg("user", f"{body} 问 {i}"))
        out.append(msg("assistant", f"{body} 答 {i}"))
    return out


def fingerprint(messages: list[dict]) -> str:
    """前缀指纹：把 messages 序列化后取哈希，用于断言"逐字节相同"。"""
    import hashlib

    return hashlib.sha256(
        json.dumps(messages, ensure_ascii=False, sort_keys=False).encode()
    ).hexdigest()


# ---------------------------------------------------------------- 前缀稳定


def test_prefix_is_byte_identical_across_turns(ready) -> None:
    """同一会话连续两轮：第二轮的**前缀**必须与第一轮完全相同（只多出末尾）。"""
    conv = conversation(3)
    conv_id = "conv-stable"

    first = cache.plan_prefix(conv, cache.load_state(conv_id), window_tokens=10_000,
                              threshold=0.8, keep_recent_turns=5)
    # 第二轮：把第一轮的问答追加进去（这正是真实调用的形态）
    conv2 = [*conv, msg("user", "追问"), msg("assistant", "再答")]
    second = cache.plan_prefix(conv2, cache.load_state(conv_id), window_tokens=10_000,
                               threshold=0.8, keep_recent_turns=5)

    assert first.messages == conv
    # 第二轮的前 len(first) 条与第一轮逐字节一致 —— 这就是缓存命中的前提
    assert second.messages[: len(first.messages)] == first.messages
    assert fingerprint(second.messages[: len(first.messages)]) == fingerprint(first.messages)


def test_stable_prefix_survives_many_turns(ready) -> None:
    """连续 5 轮都不触发折叠时，前缀一路保持可命中。"""
    conv = conversation(2)
    conv_id = "conv-many"
    previous: list[dict] = []

    for turn in range(5):
        if turn:
            conv = [*conv, msg("user", f"问 {turn}"), msg("assistant", f"答 {turn}")]
        plan = cache.plan_prefix(conv, cache.load_state(conv_id), window_tokens=50_000,
                                 threshold=0.8, keep_recent_turns=5)
        if previous:
            assert plan.messages[: len(previous)] == previous, f"第 {turn} 轮前缀被改写了"
        previous = plan.messages


# ---------------------------------------------------------------- 摘要冻结


def test_summary_is_not_recomputed_every_turn(ready) -> None:
    """摘要冻结后不再重算——改造前**每轮**都调一次 LLM 摘要，前缀每轮都变。"""
    conv = conversation(2)
    conv_id = "conv-freeze"
    cache.freeze_summary(conv_id, "早期对话的摘要", upto=2)

    plan = cache.plan_prefix(conv, cache.load_state(conv_id), window_tokens=50_000,
                             threshold=0.8, keep_recent_turns=5)
    assert not plan.needs_summary(), "已有摘要且未超预算时，不该再触发重算"
    assert plan.messages[0]["role"] == "system"
    assert "早期对话的摘要" in plan.messages[0]["content"]


def test_frozen_summary_prefix_stays_identical(ready) -> None:
    """冻结摘要进前缀后，前缀里那条摘要消息逐字节不变。"""
    conv_id = "conv-frozen-prefix"
    cache.freeze_summary(conv_id, "摘要A", upto=2)
    first = cache.plan_prefix(conversation(2), cache.load_state(conv_id), window_tokens=50_000,
                              threshold=0.8, keep_recent_turns=5)
    conv = [*conversation(2), msg("user", "新问"), msg("assistant", "新答")]
    second = cache.plan_prefix(conv, cache.load_state(conv_id), window_tokens=50_000,
                               threshold=0.8, keep_recent_turns=5)
    assert second.messages[: len(first.messages)] == first.messages


def test_fold_only_when_over_budget(ready) -> None:
    """超预算才折叠，且折叠边界只前进、不回退。"""
    conv_id = "conv-fold"
    conv = conversation(6, body="很长的一段内容" * 20)
    state = cache.load_state(conv_id)
    plan = cache.plan_prefix(conv, state, window_tokens=200, threshold=0.8,
                             keep_recent_turns=2)
    assert plan.needs_summary(), "超预算时必须给出要折叠的那一段"
    assert plan.truncated, "折叠段不能为空"

    upto = state.summarized_upto + len(plan.truncated)
    cache.freeze_summary(conv_id, "折叠后的摘要", upto)
    assert cache.load_state(conv_id).summarized_upto == upto

    # 边界只前进：再冻一个更小的边界应被忽略
    cache.freeze_summary(conv_id, "更短的摘要", 1)
    assert cache.load_state(conv_id).summarized_upto == upto
    assert cache.load_state(conv_id).summary == "折叠后的摘要"


def test_fold_reduces_context(ready) -> None:
    """折叠确实让上下文变短（否则这个机制就是摆设）。"""
    conv_id = "conv-shrink"
    conv = conversation(8, body="很长的一段内容" * 30)
    before = estimate_messages_tokens(conv)
    state = cache.load_state(conv_id)
    plan = cache.plan_prefix(conv, state, window_tokens=400, threshold=0.8,
                             keep_recent_turns=2)
    cache.freeze_summary(conv_id, "摘要", state.summarized_upto + len(plan.truncated))
    after_plan = cache.plan_prefix(conv, cache.load_state(conv_id), window_tokens=400,
                                   threshold=0.8, keep_recent_turns=2)
    assert estimate_messages_tokens(after_plan.messages) < before


def test_empty_conversation_id_is_safe(ready) -> None:
    """没有会话 id（例如临时调用）时不该炸，也不该写库。"""
    state = cache.load_state("")
    assert state.summarized_upto == 0
    cache.freeze_summary("", "x", 5)
    plan = cache.plan_prefix(conversation(1), state, window_tokens=10_000,
                             threshold=0.8, keep_recent_turns=5)
    assert plan.messages


# ---------------------------------------------------------------- 用量与命中率


def test_usage_stats_accept_both_api_shapes() -> None:
    """Chat Completions 与 Responses API 两种 usage 口径都要吃下。"""
    cc = cache.UsageStats()
    cc.add({"prompt_tokens": 1000, "prompt_cache_hit_tokens": 600,
            "prompt_cache_miss_tokens": 400, "completion_tokens": 50})
    assert (cc.cache_hit_tokens, cc.cache_miss_tokens) == (600, 400)
    assert cc.hit_rate == 0.6
    assert cc.completion_tokens == 50

    resp = cache.UsageStats()
    resp.add({"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 250},
              "output_tokens": 30, "output_tokens_details": {"reasoning_tokens": 10}})
    assert (resp.cache_hit_tokens, resp.cache_miss_tokens) == (250, 750)
    assert resp.reasoning_tokens == 10


def test_usage_without_cache_fields_is_all_miss() -> None:
    """没给缓存字段时按全 miss 记——宁可低估命中率，也不要虚报省钱。"""
    stats = cache.UsageStats()
    stats.add({"prompt_tokens": 300, "completion_tokens": 10})
    assert stats.cache_hit_tokens == 0
    assert stats.cache_miss_tokens == 300
    assert stats.hit_rate == 0.0


def test_usage_overview_aggregates(ready) -> None:
    stats = cache.UsageStats(conversation_id="conv-agg", model="deepseek-flash")
    stats.add({"prompt_tokens": 1000, "prompt_cache_hit_tokens": 900,
               "prompt_cache_miss_tokens": 100, "completion_tokens": 40})
    stats.persist()

    overview = cache.usage_overview(days=1)
    assert overview["answers"] >= 1
    assert overview["cacheHitTokens"] >= 900
    assert 0 < overview["hitRate"] <= 1
    assert overview["savedRatio"] > 0


# ---------------------------------------------------------------- 模型名


def test_model_name_normalization() -> None:
    """旧模型名会被官方兼容路由，但"temporarily"——本工作台提前归一。"""
    assert DEFAULT_MODEL == "deepseek-flash"
    assert normalize_model("deepseek-v4-flash") == "deepseek-flash"
    assert normalize_model("deepseek-v4-flash-vision-exp") == "deepseek-flash"
    assert normalize_model("deepseek-v4.1-flash") == "deepseek-flash"
    assert normalize_model("deepseek-v4-pro") == "deepseek-v4-pro"   # 不误伤 pro
    assert normalize_model(None) == DEFAULT_MODEL
    assert normalize_model("") == DEFAULT_MODEL


def test_client_stores_last_usage() -> None:
    """`chat()` 的用量经属性回传（摘要折叠要读它）。"""
    from workbench.services.deepseek import DeepSeekClient

    client = DeepSeekClient("k", model="deepseek-v4-flash")
    assert client.last_usage == {}
    assert normalize_model(client.model) == "deepseek-flash"


def test_summary_calls_are_counted_separately() -> None:
    """摘要调用单独计数：它几乎必然全 miss，是"缓存优化有没有用"最该看的开销。"""
    stats = cache.UsageStats()
    stats.add({"prompt_tokens": 2000, "prompt_cache_hit_tokens": 0,
               "prompt_cache_miss_tokens": 2000}, )
    stats.summary_calls += 1
    assert stats.to_api()["summaryCalls"] == 1
    assert stats.summary_calls == 1


# ---------------------------------------------------------------- 与循环的集成


def test_loop_freezes_summary_and_keeps_prefix(ready, monkeypatch) -> None:
    """跑两轮真实循环：第二轮的前缀必须能命中第一轮（不被摘要重算打断）。"""
    from workbench import approvals
    from workbench import plugin_api as pa
    from workbench.harness.loop import run_agent_loop
    from workbench.harness.types import AgentConfig

    approvals.reset_store()
    hub = pa.ToolHub()
    approvals.install(hub)
    monkeypatch.setattr(pa, "_hub", hub)

    class Client:
        model = "fake"

        def __init__(self) -> None:
            self.seen: list[list[dict]] = []
            self.last_usage: dict = {}

        async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
            self.seen.append([dict(m) for m in messages])
            yield {"type": "end", "content": "答", "tool_calls": [],
                   "usage": {"prompt_tokens": 10, "prompt_cache_hit_tokens": 10,
                             "prompt_cache_miss_tokens": 0, "completion_tokens": 1}}

        async def chat(self, messages, temperature=0.7, max_tokens=None, json_mode=False):
            self.last_usage = {"prompt_tokens": 5, "prompt_cache_hit_tokens": 0,
                               "prompt_cache_miss_tokens": 5}
            return "摘要"

    conv_id = "conv-loop-stable"
    cache.clear_state(conv_id)
    client = Client()
    cfg = AgentConfig(system_prompt="系统", context_window_tokens=1_000_000)

    async def run(history):
        return [e async for e in run_agent_loop(
            history, client, hub, cfg, conversation_id=conv_id)]

    asyncio.run(run([msg("user", "第一问")]))
    first_request = client.seen[-1]
    asyncio.run(run([msg("user", "第一问"), msg("assistant", "答"), msg("user", "第二问")]))
    second_request = client.seen[-1]

    assert second_request[: len(first_request)] == first_request, "第二轮前缀被改写了，缓存必 miss"
    assert not cache.load_state(conv_id).summary, "未超预算就不该产生摘要（省一次 LLM 调用）"
