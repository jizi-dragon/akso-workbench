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
from pydantic import BaseModel

from workbench import approvals, cache, db
from workbench import plugin_api as pa
from workbench.harness.context import estimate_messages_tokens
from workbench.harness.loop import run_agent_loop
from workbench.harness.types import AgentConfig
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
    """摘要冻结后不再重算——改造前**每轮**都调一次 LLM 摘要，前缀每轮都变。

    ⚠️ 本用例原先断言 `plan.messages[0]` **就是那条摘要**，并据此检查摘要正文。
    那是把缺陷当契约写了：当时 `summarized_upto` 含首条 system，"冻结摘要"就意味着
    system 被折进摘要、摘要顶到第 0 位。I15 修正后第 0 位恒为**原始 system**。
    """
    conv = conversation(2)
    conv_id = "conv-freeze"
    cache.freeze_summary(conv_id, "早期对话的摘要", upto=2)

    plan = cache.plan_prefix(conv, cache.load_state(conv_id), window_tokens=50_000,
                             threshold=0.8, keep_recent_turns=5)
    assert not plan.needs_summary(), "已有摘要且未超预算时，不该再触发重算"
    # I15：第 0 位恒为原始 system，摘要只能排在它之后
    assert plan.messages[0] == conv[0], "首条必须仍是原始 system 提示词（I15）"
    assert plan.messages[1]["role"] == "system"
    assert "早期对话的摘要" in plan.messages[1]["content"]


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


# ---------------------------------------------------------------- I15：折叠永不吞 system


def test_folding_never_swallows_the_system_prompt(ready) -> None:
    """★ I15：**任何**折叠状态下，请求的第 0 条都必须是原始 system 提示词。

    这条是"看起来完全正常但其实是坏的"的典型：折叠前后的请求都**合法**、接口都回 200，
    只是模型从此看不到自己的行为约束 —— 长会话表现为「越用越不听话」，且不报错。

    缺陷机理：`harness.context.split_turns` 只按 `role == "user"` 切轮，而 system 后
    紧跟 user ⇒ system **自成第一个 turn** ⇒ 被 `_split_keep` 算进 `older` ⇒ 并进摘要
    ⇒ 原始提示词永久消失（`loop.py` 的契约第 17 行要求它永远在最前）。
    """
    conv_id = "conv-i15"
    conv = conversation(6, body="很长的一段内容" * 20)

    plan = cache.plan_prefix(conv, cache.load_state(conv_id), window_tokens=200,
                             threshold=0.8, keep_recent_turns=2)
    assert plan.needs_summary(), "前置：这份对话必须触发折叠，否则本用例什么都没验到"
    assert all(m.get("role") != "system" for m in plan.truncated), (
        "被折叠的那一段里不许出现 system（I15）"
    )

    # 走真实链路的两步：先冻结摘要，再按新边界重新组装
    cache.freeze_summary(conv_id, "摘要正文",
                         cache.load_state(conv_id).summarized_upto + len(plan.truncated))
    after = cache.plan_prefix(conv, cache.load_state(conv_id), window_tokens=200,
                              threshold=0.8, keep_recent_turns=2)
    assert after.messages[0] == conv[0], "折叠之后首条仍是原始 system（I15）"
    assert after.messages[0]["content"] == "系统提示", "而且必须是**原文**，不是摘要"


def test_summary_sits_between_system_and_history(ready) -> None:
    """契约顺序：`[system] + [冻结摘要?] + [正文历史] + [新消息]`（I3）。"""
    conv_id = "conv-order"
    conv = conversation(3)
    cache.freeze_summary(conv_id, "摘要", upto=2)

    plan = cache.plan_prefix(conv, cache.load_state(conv_id), window_tokens=50_000,
                             threshold=0.8, keep_recent_turns=5)
    assert plan.messages[0]["content"] == "系统提示"
    assert "摘要" in plan.messages[1]["content"] and plan.messages[1]["role"] == "system"
    # 边界（2 条正文）之后的正文应当原样接在摘要后面
    assert plan.messages[2:] == conv[3:], "摘要之后应当正好是边界之后的正文，不多不少"


def test_conversation_without_system_still_folds(ready) -> None:
    """没有 system 首条时（例如摘要器自己那次调用）必须照常折叠，不许因 I15 而退化。"""
    conv_id = "conv-nosys"
    body = [m for m in conversation(6, body="很长的一段内容" * 20) if m["role"] != "system"]

    plan = cache.plan_prefix(body, cache.load_state(conv_id), window_tokens=200,
                             threshold=0.8, keep_recent_turns=2)
    assert plan.needs_summary() and plan.truncated, "无 system 的对话仍必须能折叠"
    assert plan.messages == body[len(plan.truncated):]


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


def test_two_persists_in_one_conversation_both_land(ready, monkeypatch) -> None:
    """同一会话连续两次入账必须**都**落库 —— 这是"静默丢账"的回归。

    旧主键是 `f"{now_ms()}-{conversation_id[:8]}"`：同一毫秒内的两次入账
    （"重算摘要"与"回答收尾"经常挨在一起）PK 撞车 → `INSERT` 抛 IntegrityError
    → 被 `except Exception` 吞成一条 warning ⇒ **那条用量永远消失**。
    而 `deepseek_usage` 是**唯一**的用量事实源，命中率与"省钱口径"都从它算 ——
    丢一条就永久偏小，界面上却看不出来。

    ⚠️ **必须把时钟钉死**才验得到：不钉的话两次入账常常落在不同毫秒，旧实现也能过
    —— 那种"偶尔会绿"的回归测试等于没有。钉死 `now_ms` 之后，旧实现**必然**撞 PK。
    """
    monkeypatch.setattr(cache, "now_ms", lambda: 1_700_000_000_000)

    for _ in range(2):
        stats = cache.UsageStats(conversation_id="conv-usage-pk", model="deepseek-flash")
        stats.add({"prompt_tokens": 100, "prompt_cache_hit_tokens": 60,
                   "prompt_cache_miss_tokens": 40, "completion_tokens": 5})
        stats.persist()

    row = db.query_one(
        "SELECT COUNT(*) AS n FROM deepseek_usage WHERE conversation_id = ?",
        ("conv-usage-pk",),
    )
    assert int(row["n"]) == 2, "同一毫秒的两次入账只落了一条 —— 主键又撞车了（静默丢账）"


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


# ---------------------------------------------------------------- 前缀稳定（工具轮次与回填）
#
# 上面那批测的是"普通问答轮次"。但 `run_agent_loop` **内部**还有一处会改动 context：
# `_one_model_turn` 拿到 tool_calls 后会 append 两条（assistant 带 tool_calls + role=tool 结果），
# 然后才发起本轮的第二次 LLM 调用。**那是 append-only 最容易被悄悄破坏的地方**，
# 而它此前没有任何断言覆盖——只验了"工具结果确实回填了"，没验"前缀没被改写"。


class ToolTurnClient:
    """第一轮要求调工具、第二轮收尾的假 LLM 客户端。

    与 `test_agent_loop.FakeClient` 的分工：那个面向"事件序列"断言，
    这个只关心"每次调用时发出去的 messages 长什么样"。
    """

    def __init__(self, tool_calls: list[dict], final: str = "查到了") -> None:
        self._script = [
            {"content": "", "tool_calls": tool_calls},
            {"content": final, "tool_calls": []},
        ]
        self.calls: list[list[dict]] = []
        self.model = "fake"
        self.last_usage: dict = {}

    async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
        self.calls.append([dict(m) for m in messages])
        step = self._script.pop(0) if self._script else {"content": "（脚本耗尽）", "tool_calls": []}
        yield {
            "type": "end",
            "content": step["content"],
            "tool_calls": step["tool_calls"],
            "usage": {"prompt_tokens": 10, "prompt_cache_hit_tokens": 10,
                      "prompt_cache_miss_tokens": 0, "completion_tokens": 1},
        }

    async def chat(self, messages, temperature=0.7, max_tokens=None, json_mode=False):
        self.last_usage = {"prompt_tokens": 5, "prompt_cache_hit_tokens": 0,
                           "prompt_cache_miss_tokens": 5}
        return "摘要"


class EchoArgs(BaseModel):
    text: str = pa.Arg("内容", default="")


async def _echo(args: EchoArgs, ctx: pa.ToolContext) -> str:
    return json.dumps({"ok": True, "echo": args.text})


async def _echo_huge(args: EchoArgs, ctx: pa.ToolContext) -> str:
    """返回远超单条结果上限的内容（用于验"注入上下文的东西有硬上限"）。"""
    return "x" * (pa.MAX_RESULT_CHARS * 3)


@pytest.fixture()
def echo_hub(ready, monkeypatch):
    """带两个只读回显工具的极简中枢。"""
    approvals.reset_store()
    hub = pa.ToolHub()
    approvals.install(hub)
    hub.add_plugin(pa.Plugin(id="echo", title="回显", tools=[
        pa.ToolSpec(name="echo_text", description="回显", args_model=EchoArgs,
                    handler=_echo, risk=pa.Risk.READ, trust=pa.Trust.LOCAL, timeout_s=5),
        pa.ToolSpec(name="echo_huge", description="回显超长内容", args_model=EchoArgs,
                    handler=_echo_huge, risk=pa.Risk.READ, trust=pa.Trust.LOCAL, timeout_s=5),
    ]))
    monkeypatch.setattr(pa, "_hub", hub)
    return hub


async def _drain(gen) -> list[dict]:
    return [event async for event in gen]


def _drive_tool_turn(hub, conv_id: str, tool_calls: list[dict]) -> ToolTurnClient:
    client = ToolTurnClient(tool_calls)
    asyncio.run(_drain(run_agent_loop(
        [msg("user", "查一下")], client, hub,
        AgentConfig(system_prompt="系统", context_window_tokens=1_000_000),
        conversation_id=conv_id,
    )))
    return client


def test_tool_turn_keeps_prefix_within_one_run(ready, echo_hub) -> None:
    """工具轮次 append 了两条消息，**已发出的前缀必须原样保留**。

    `run_agent_loop` 内部唯一改动 context 的地方就是这里（`loop.py::_one_model_turn`）：
    拿到 tool_calls → `context.append(assistant)` → 每个调用再 append 一条 role=tool。
    如果这里被改成"重建 messages"或"回填时顺手规整一下"，缓存就从那一刻起全 miss，
    而**测试、类型、ruff 都不会报错**——这是本仓库最贵的一类静默回归（价差 50×）。
    """
    cache.clear_state("conv-tool-prefix")
    client = _drive_tool_turn(echo_hub, "conv-tool-prefix", [
        {"id": "c1", "name": "echo_text", "arguments": json.dumps({"text": "hi"})},
    ])

    assert len(client.calls) == 2, "工具轮次应当产生两次 LLM 调用"
    first, second = client.calls[0], client.calls[1]

    assert second[: len(first)] == first, "工具轮次改写了已发出的前缀，缓存必 miss"
    assert fingerprint(second[: len(first)]) == fingerprint(first)

    # 并且确实是"只追加"：assistant(带 tool_calls) + tool 结果 这两条
    assert len(second) == len(first) + 2
    assert second[-2]["role"] == "assistant"
    assert second[-2]["tool_calls"], "assistant 那条必须带 tool_calls，否则模型看不到自己调过什么"
    assert second[-1]["role"] == "tool"
    assert second[-1]["tool_call_id"] == "c1"


def test_prefix_stable_over_history_containing_tool_messages(ready) -> None:
    """带「assistant(tool_calls) + tool」的**持久化历史**开新一轮，前缀仍逐字节一致。

    与 `test_prefix_is_byte_identical_across_turns` 的区别：那条用的是 `conversation()` 夹具，
    它**永远产生不出**工具轮次特有的两种消息形状（`content: None` + `tool_call_id`）。
    而这两种形状在真实链路里每次带工具的对话都会出现，且要经过
    「落库 → 读回 → 组装」三道手。

    `fingerprint` 用 `sort_keys=False`，所以**键的插入顺序变了也会被抓到**——
    这一点是刻意的：DeepSeek 的前缀匹配发生在**序列化之后的字节**上，
    而 dict 的 `==` 比较忽略键序，单靠 `==` 会漏掉这类漂移。
    """
    cache.clear_state("conv-tool-history")
    history = [
        msg("system", "系统提示"),
        msg("user", "查一下"),
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "name": "echo_text", "arguments": "{}"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "结果"},
        msg("assistant", "查到了"),
    ]
    first = cache.plan_prefix(history, cache.load_state("conv-tool-history"),
                              window_tokens=50_000, threshold=0.8, keep_recent_turns=5)
    extended = [*history, msg("user", "再问"), msg("assistant", "再答")]
    second = cache.plan_prefix(extended, cache.load_state("conv-tool-history"),
                               window_tokens=50_000, threshold=0.8, keep_recent_turns=5)

    assert second.messages[: len(first.messages)] == first.messages
    assert fingerprint(second.messages[: len(first.messages)]) == fingerprint(first.messages)


def test_injected_tool_result_is_bounded(ready, echo_hub) -> None:
    """回填进**上下文**的工具结果必须受硬上限约束——这是**链路级**断言，不是函数级。

    为什么要单列一条：`tests/test_plugin_api.py::test_long_result_is_truncated` 验的是
    `_truncate()` **这个函数**；而"loop 回填进 context 的那条 tool 消息真的被截断了"
    是**另一件事**。本仓 PITFALLS #42 的教训正是「函数级测过了 ≠ 链路用上了」
    （`audit.record` 当时三条测试全绿，而它一个调用方都没有），
    所以这里按同一纪律补上链路级断言。

    上限来自 Codex `AGENTS.md` 的同一条评审规则："注入模型上下文的一切必须有界且带硬上限"。
    """
    cache.clear_state("conv-bounded")
    client = _drive_tool_turn(echo_hub, "conv-bounded", [
        {"id": "c1", "name": "echo_huge", "arguments": "{}"},
    ])

    tool_msg = client.calls[1][-1]
    assert tool_msg["role"] == "tool"
    assert len(tool_msg["content"]) <= pa.MAX_RESULT_CHARS + 200, (
        f"注入上下文的结果没有被硬上限约束：{len(tool_msg['content'])} 字符"
    )
    assert "已截断" in tool_msg["content"], "截断必须**显式告知模型**，不能静默丢弃"
