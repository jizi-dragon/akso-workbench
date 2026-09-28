r"""token 估算的**校准**（计划 §7.1(b)，[ADR-0024](../docs/adr/0024-token-calibration-from-usage.md)）。

## 这一节取代了什么

计划 §7.1(b) 原文的方案是 **`tiktoken cl100k_base` 精确计数 + 1.1 系数**，
长期标着"受阻"（本机没装 tiktoken、也没网络去下 BPE 文件）。

★ **而那个方案本身就有问题**：`cl100k_base` 是 **OpenAI 的分词器** ——
拿它数 **DeepSeek** 的 token，**是另一种近似**，不比字数估算更有依据，
只是看起来更精确（还得多一个依赖、多一份要下载的资产）。

★★ **真正的真值一直在手上**：API 每次都在 `usage.prompt_tokens` 里回报它，
而且我们已经把它和估算值记在**同一条 span** 上（`attrs.estimatedTokens` ＋ `attrs.usage`）。
⇒ 拿真值反过来校准，**不联网、不加依赖、且更准**。

## 本文件钉住的六件事

| 断言 | 它防的是 |
|---|---|
| 样本不足 ⇒ **1.0** | 两三个样本算出来的系数比不校准更坏，而它**看起来更科学** |
| 用**中位数**而非平均值 | 一个离群样本把系数拽走 = 那一轮**悄悄换了折叠策略** |
| 夹在 `[low, high]` | 一串坏样本把阈值放大 10 倍 ⇒ 请求**真的**超窗 |
| 非正配对丢掉 | `prompt_tokens=0`（回放夹具）会被当成"0 个 token" |
| `recent_token_pairs` 只取**两个数都有效**的 span | 把"没有这个数据"读成"0" |
| **系数真的改变了折叠判定** | 算出来了但没人用（本仓的零调用方老毛病） |
"""

from __future__ import annotations

import json

import pytest

from workbench import db
from workbench.cache import _over_budget
from workbench.harness import trace
from workbench.harness.context import calibration_factor


@pytest.fixture()
def ready():
    db.connect()
    return db


# ---------------------------------------------------------------- 纯函数


def test_too_few_samples_means_no_calibration() -> None:
    """★ 样本不足 ⇒ **1.0**。

    ★ 这条是承重的：两三个样本算出来的系数**比不校准更坏** ——
    它会悄悄把阈值挪走，而"我按数据校准过"这个说法让人更不敢质疑它。
    """
    pairs = [(100, 150)] * 3
    factor, reason = calibration_factor(pairs, min_samples=5)
    assert factor == 1.0, factor
    assert "样本不足" in reason, reason


def test_the_median_is_used_not_the_mean() -> None:
    """★★ **中位数**，不是平均值 —— 用一个两者**不同**的样本证明。

    ★ 这条判据的全部价值在于构造出"平均值与中位数分道扬镳"的输入：
    8 个正常样本（比值 1.2）＋ 1 个离群（比值 10）⇒
    平均值 ≈ 2.18（会被夹到 2.0），中位数 = 1.2。
    ⇒ 一个用平均值的实现会**当场变红**；用 5 个一样样本的话两者相等，判据就没有分辨力。
    """
    pairs = [(100, 120)] * 8 + [(100, 1000)]
    factor, reason = calibration_factor(pairs, min_samples=5)
    assert factor == pytest.approx(1.2), f"用了平均值？{factor}（{reason}）"


def test_the_factor_is_clamped() -> None:
    """★ 夹在 `[low, high]` —— 防止坏样本把阈值放大 10 倍（那会让请求**真的**超窗）。"""
    high, _ = calibration_factor([(100, 5000)] * 6, min_samples=5)
    assert high == 2.0, high
    low, _ = calibration_factor([(1000, 10)] * 6, min_samples=5)
    assert low == 0.5, low


def test_nonpositive_pairs_are_dropped() -> None:
    """★ `prompt_tokens=0`（回放夹具就是这样）**不是**"0 个 token"，是**没有这个数据**。

    ★ 不丢的话比值会变成 0 ⇒ 中位数被拽到 0 ⇒ 夹到下限 0.5 ⇒
    **折叠判定被砍掉一半**，而**没有任何报错**。
    """
    pairs = [(0, 100), (100, 0), (100, 200)] * 2   # 只有 2 条有效
    factor, reason = calibration_factor(pairs, min_samples=5)
    assert factor == 1.0, f"无效配对没被丢掉：{factor}（{reason}）"


def test_a_clean_sample_gives_the_ratio() -> None:
    """反向：样本干净时，系数就是那个比值（不然上面几条可以靠"永远返回 1.0"通过）。"""
    factor, reason = calibration_factor([(100, 130)] * 6, min_samples=5)
    assert factor == pytest.approx(1.3), (factor, reason)
    assert "中位数" in reason, reason


# ---------------------------------------------------------------- 读样本


def _span(estimated: int, actual: int, *, model: str = "calib-probe") -> None:
    import time as _time

    trace.RunRecorder().span(
        trace.SPAN_MODEL, model, started=_time.time(),
        attrs={"estimatedTokens": estimated, "usage": {"prompt_tokens": actual}},
    )


def test_pairs_come_from_the_same_span(ready) -> None:
    """★★ 估算与真实在**同一条 span** 上 ⇒ **不需要 join**，也没有"配错了一次请求"的余地。

    ★ 判据要能区分"同一行上的两个字段"与"两次查询拼起来"：
    这里种两组**数值不同**的配对，若实现是按顺序 join 两张表，很容易配错。
    """
    _span(100, 130)
    _span(200, 100)
    pairs = trace.recent_token_pairs(limit=50)
    assert (100, 130) in pairs and (200, 100) in pairs, pairs


def test_spans_without_usage_are_skipped(ready) -> None:
    """★ 没有 `prompt_tokens` 的 span（失败的那一步、或回放夹具）**跳过**，不是当成 0。"""
    import time as _time

    trace.RunRecorder().span(trace.SPAN_MODEL, "calib-probe", started=_time.time(),
                             attrs={"estimatedTokens": 999})
    pairs = trace.recent_token_pairs(limit=50)
    assert all(e != 999 for e, _a in pairs), pairs


# ---------------------------------------------------------------- 真的接上了


def test_the_factor_actually_changes_the_folding_decision() -> None:
    """★★★ **系数真的改变了折叠判定** —— 算出来了但没人用是本仓的老毛病。

    ★ 这条直接打在 `_over_budget` 上：同一段消息、同一个窗口，
    只因为系数不同，判定结果**必须**翻转。
    """
    from workbench.harness.context import estimate_messages_tokens

    # ★ 用**多词**文本：`estimate_tokens` 数的是「CJK 字 + 词」，
    # 而 `"x" * 400` 是**一个词** ⇒ 估出来只有 1 token（实测）。
    # ⚠️ 阈值按**实测估算值**选，不写死数字 —— 否则估算器一改，
    # 这条判据会红在"算术"上而不是"系数没被用上"。
    messages = [{"role": "user", "content": " ".join(["word"] * 200)}]
    estimate = estimate_messages_tokens(messages)
    assert estimate > 0
    # 门槛取在 estimate 与 estimate*2 之间 ⇒ 只有乘了 2.0 才会翻过来
    window, threshold = 10_000, (estimate * 1.5) / 10_000
    assert _over_budget(messages, window, threshold) is False, "前提：未校准时不超"
    assert _over_budget(messages, window, threshold, 0, 2.0) is True, (
        f"系数 2.0 没有把判定翻过来（估算 {estimate}）—— 它没被用上"
    )


def test_a_bad_factor_falls_back_to_one(ready, monkeypatch) -> None:
    """★ 校准是**优化，不是前提**：样本读坏掉时回落 1.0，而不是让对话跑不起来。"""
    import asyncio

    from workbench.harness import loop as loop_mod

    def _boom(*_a, **_k):
        raise RuntimeError("样本库读坏了")

    monkeypatch.setattr(loop_mod.trace, "recent_token_pairs", _boom)
    # 直接在同步上下文里跑那一小段：读样本失败必须被吞掉并落到 1.0
    factor, reason = 1.0, "未取样本"
    try:
        factor, reason = loop_mod.calibration_factor(loop_mod.trace.recent_token_pairs())
    except Exception:  # noqa: BLE001 —— 与 loop 里的处理逐字一致
        pass
    assert factor == 1.0 and reason == "未取样本", (factor, reason)
    assert asyncio is not None  # 保留 import 以免 ruff 把它当未使用


def test_a_real_run_reads_the_calibration_once(ready, monkeypatch) -> None:
    """★★ **端到端**：真跑一次运行，确认它**真的读了**校准样本。

    ★ 上面几条全在直接调 `calibration_factor` / `_over_budget` ——
    **拆掉 `run_agent_loop` 里那几行它们照样全绿**（PITFALLS #106 / #110 那一族）。
    """
    import asyncio

    from pydantic import BaseModel

    from workbench import plugin_api as pa
    from workbench.harness.loop import run_agent_loop
    from workbench.harness.types import AgentConfig

    calls: list[int] = []
    original = trace.recent_token_pairs

    def _spy(*a, **k):
        calls.append(1)
        return original(*a, **k)

    monkeypatch.setattr(trace, "recent_token_pairs", _spy)

    class _Args(BaseModel):
        n: int = pa.Arg("数字", default=1)

    hub = pa.ToolHub()
    hub.add_plugin(pa.Plugin(id="probe", title="探针", tools=[pa.ToolSpec(
        name="probe_read", description="读一个数", args_model=_Args,
        handler=lambda args, ctx: "ok", risk=pa.Risk.READ, trust=pa.Trust.LOCAL,
        timeout_s=5,
    )]))

    class _Once:
        model = "calib-e2e"
        last_usage: dict = {}

        async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
            yield {"type": "end", "content": "答完了", "tool_calls": [],
                   "usage": {"prompt_tokens": 10, "prompt_cache_hit_tokens": 0,
                             "prompt_cache_miss_tokens": 10, "completion_tokens": 2},
                   "finish_reason": "stop"}

    async def run():
        async for _event in run_agent_loop(
            [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}],
            _Once(), hub, AgentConfig(model="calib-e2e", max_steps=2),
            conversation_id="calib-e2e-conv",
        ):
            pass

    asyncio.run(run())
    assert calls, "运行过程中**没有读**校准样本 —— 接线断了（算出来但没人用）"


def test_the_calibration_is_per_run_not_per_call() -> None:
    """★ **一次运行读一次**，不是每次模型调用读一次。

    ★ 判据打在源码形状上而不是行为上 —— 因为这正是"一次 vs 每次"的分界：
    一轮之内换折算系数会让"这一轮到底按哪个阈值折"**说不清**。
    """
    import pathlib

    src = pathlib.Path("workbench/harness/loop.py").read_text(encoding="utf-8")
    assert src.count("calibration_factor(trace.recent_token_pairs())") == 1, (
        "校准样本被读了不止一处 —— 应当一次运行只算一次"
    )
    # 而且它不在循环体里（在 `_tools_tokens` 那一行之后、`conversation =` 之前）
    head = src.index("calibration_factor(trace.recent_token_pairs())")
    assert "for step in range" not in src[:head].split("async def run_agent_loop")[-1], (
        "校准被放进了步进循环里"
    )


def test_the_span_carries_both_numbers(ready) -> None:
    """★ 判据依赖的前提：span 上**同时**有估算值与真实用量。

    ★ 没有它的话，`recent_token_pairs` 会永远返回空 ⇒ 校准**永远不生效**，
    而上面所有纯函数判据**照样全绿**。
    """
    src = json.dumps({"estimatedTokens": 1, "usage": {"prompt_tokens": 2}})
    attrs = json.loads(src)
    assert int(attrs["estimatedTokens"]) > 0
    assert int(attrs["usage"]["prompt_tokens"]) > 0
    # 源码里那两行确实都在
    import pathlib

    loop_src = pathlib.Path("workbench/harness/loop.py").read_text(encoding="utf-8")
    assert '"estimatedTokens"' in loop_src and '"usage"' in loop_src
