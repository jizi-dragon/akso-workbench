r"""token 估算：**统计范围**（计划 §7.1）。

## 这一节修的是"估偏低"那一半 —— 而它是**不需要 tiktoken** 的那一半

计划 §7.1 有两个可分开的缺陷，此前被我一起判成"受阻"：

| | 缺陷 | 要不要 tiktoken |
|---|---|---|
| **a** | **统计范围不全**：只算 `content`，漏 `tool_calls` / `tool_call_id` / **工具 schema** | ❌ 不要 |
| b | 估算公式的精度（CJK 字数 vs BPE） | ✅ 要 |

★ 而 (a) 才是**危险**的那一半 —— 计划自己写着「**估偏低会导致该折叠没折叠**」，
`harness/loop.py` 也记着那个实测故障（"预算 1000 token 时第 5 次请求已 12,059，
而折叠发生 **0** 次"）。

## 实测的偏差（2026-09-24，18 个工具，一段两轮工具调用的对话）

```
消息正文（现状能算的）      190
+ tool_calls / tool_call_id   211   (+11%)
+ 工具 schema                2315   (+1118%)   ← schema 单独 2104
```

⇒ 一个真实请求里 **91%** 的 token 此前不在估算范围内。
★ 而计划 §7.1 的门槛是「估偏 > **15%** 就不许上线」—— 现状是 **1118%**。

## 本文件钉住的五件事

| 断言 | 它防的是 |
|---|---|
| `tool_calls` / `tool_call_id` **计入** | 工具轮次被系统性低估（本节的 (a)） |
| 工具 schema **单独可算**，且**不是** `estimate_messages_tokens` 的一部分 | 两个量被混成一个 —— 而它们的调用方需要不同（下一条） |
| 折叠判定用**全量**（含 schema） | 窗口设小就永远不折叠（那个实测故障） |
| **硬上限**仍只用消息那一份 | 取代一条**已决策**：宁可漏判也不要误判（见 `_over_hard_limit`） |
| 缺失/空输入不炸 | `content` 缺失、`tool_calls` 是空列表、`tools` 是 `None` —— 三种都真的会出现 |
"""

from __future__ import annotations

import json

import pytest

from workbench.harness import context as ctx
from workbench.harness.tools import default_registry


def _tool_call(name: str, args: str, call_id: str = "c1") -> dict:
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": args}}


# ---------------------------------------------------------------- 统计范围


def test_tool_calls_are_counted() -> None:
    """★★ `tool_calls` **计入**估算。

    此前它一个 token 都不算 —— 而 assistant 消息在工具轮次里
    `content` 往往是**空字符串**，于是整条消息只贡献那个 `+4`。
    """
    with_call = [{"role": "assistant", "content": "",
                  "tool_calls": [_tool_call("read_local_file",
                                            '{"path": "README.md", "maxBytes": 4000}')]}]
    bare = [{"role": "assistant", "content": ""}]
    assert ctx.estimate_messages_tokens(with_call) > ctx.estimate_messages_tokens(bare), (
        "带 tool_calls 的消息没有被多算 —— 统计范围又漏了"
    )


def test_tool_call_id_is_counted() -> None:
    """★ `tool_call_id` 计入（计划 §7.1 点名的第二项）。

    它不长，但**每条 tool 消息都有一条**，而且**一个回合里 tool 消息最多**。
    """
    with_id = [{"role": "tool", "tool_call_id": "call_00_AbC123XyZ", "content": ""}]
    without = [{"role": "tool", "content": ""}]
    assert ctx.estimate_messages_tokens(with_id) > ctx.estimate_messages_tokens(without)


def test_the_tool_schema_is_a_separate_quantity() -> None:
    """★★ 工具 schema **单独可算**，而且**不算在** `estimate_messages_tokens` 里。

    分开是**承重的**，不是洁癖：两个调用方需要不同的口径 ——
    折叠判定要全量（超窗的是**请求**），而 `_over_hard_limit` **刻意**只要消息那一份
    （宁可漏判也不要误判）。混成一个函数就没法同时满足两边。
    """
    schemas = [{"type": "function", "function": {"name": "f", "description": "读一个文件"}}]
    assert ctx.estimate_tools_tokens(schemas) > 0
    # 关键：`estimate_messages_tokens` **不因 schema 而变**
    messages = [{"role": "user", "content": "你好"}]
    assert ctx.estimate_messages_tokens(messages) == ctx.estimate_request_tokens(messages, None)


def test_the_real_schema_dominates_the_estimate() -> None:
    """★★ **工具 schema 是最大的一块** —— 这条判据钉住那个量级。

    没有它的话，"把 schema 算进去"看起来像一次可选的小优化，
    而实测它是消息正文的 **11 倍**（18 个工具 2,104 vs 190）。

    ⚠️ 断言的是**量级关系**（schema 比一段短对话大一个数量级），
    不是某个具体数字 —— 加一个工具就会让具体数字变，而"schema 很大"这件事不变。
    """
    schemas = [spec.to_api() for spec in default_registry().specs()]
    short_talk = [{"role": "user", "content": "你好，帮我看看"}]
    assert ctx.estimate_tools_tokens(schemas) > 10 * ctx.estimate_messages_tokens(short_talk), (
        "工具 schema 不再占主导？要么工具面被大幅裁剪了，要么估算又漏了它 —— "
        "两种都要先查清再改这条断言"
    )


def test_a_realistic_request_is_not_underestimated_by_an_order_of_magnitude() -> None:
    """★★ 一段**真实形状**的请求，估算不该比"只算正文"大不到一倍。

    这条是"估偏低"的**端到端**判据：它把 ① 只算 content 与
    ② 含 tool_calls + schema 放在一起比 —— 两者必须**差一个数量级**，
    否则说明有东西又漏了。
    """
    conversation = [
        {"role": "system", "content": "你是工作台助手。"},
        {"role": "user", "content": "看看有哪些账号，然后读一下 README。"},
        {"role": "assistant", "content": "",
         "tool_calls": [_tool_call("accounts_list", "{}", "call_1")]},
        {"role": "tool", "tool_call_id": "call_1",
         "content": json.dumps([{"id": "a1", "username": "ops@example.com"}],
                               ensure_ascii=False)},
    ]
    body_only = sum(ctx.estimate_content_tokens(m.get("content")) + 4 for m in conversation)
    full = ctx.estimate_request_tokens(
        conversation, [spec.to_api() for spec in default_registry().specs()])

    assert full > 5 * body_only, (
        f"全量估算 {full} 相比只算正文的 {body_only} 没有拉开量级 —— "
        "schema 或 tool_calls 又没算进去"
    )


# ---------------------------------------------------------------- 折叠判定


def test_the_folding_decision_includes_the_schema() -> None:
    """★★ **折叠判定用全量**（含 schema）。

    这是本节要修的那个后果：不算 schema 的话，窗口设小一点就**永远不折叠**
    （`harness/loop.py` 的实测：预算 1000 时第 5 次请求已 12,059，折叠 0 次）。
    """
    from workbench import cache

    messages = [{"role": "user", "content": "x" * 40}]
    window, threshold = 10_000, 0.5
    # 消息本身远低于门槛
    assert cache._over_budget(messages, window, threshold) is False
    # 加上一个巨大的 schema 之后越过门槛
    assert cache._over_budget(messages, window, threshold, tools_tokens=9_000) is True


def test_plan_prefix_passes_the_schema_through() -> None:
    """★ `plan_prefix` 真的把 `tools_tokens` 传下去了（不是收下就丢）。

    ★ 这条防的形态很具体：签名加了参数、内部忘了用 —— 而那**不报错**，
    只是"折叠又变回不算 schema"，也就是本节白改。
    """
    from workbench import cache

    state = cache.SummaryState(conversation_id="t", summary="", summarized_upto=0,
                               updated_at=0)
    # ★ 要**两轮**：只有一轮时"没有可折叠的"（`plan_prefix` 会走那条如实返回的分支），
    # 于是这条判据分不出"没收下 tools_tokens"与"收下了但没东西可折"。
    conversation = [{"role": "system", "content": "s"},
                    {"role": "user", "content": "x" * 20},
                    {"role": "assistant", "content": "y" * 20},
                    {"role": "user", "content": "z" * 20}]
    plan = cache.plan_prefix(conversation, state, window_tokens=10_000, threshold=0.5,
                             keep_recent_turns=1, tools_tokens=9_000)
    # 关键：**它认为超预算了** —— 消息本身远低于门槛，是 schema 把它顶过去的
    assert plan.needs_summary(), (
        f"tools_tokens 收下了但没用：plan={plan}\n"
        "  （签名加了参数、内部忘了用，不报错，只是折叠又变回不算 schema）"
    )


def test_the_hard_limit_deliberately_excludes_the_schema() -> None:
    """★★ **硬上限仍只用消息那一份** —— 这是一条**已决策**，不是漏改。

    `_over_hard_limit` 的文档串写着：「只用**估算值**，而它**偏低**……
    这个方向是**刻意**的：宁可漏判（少一次保护）也不要误判（把正常请求掐掉）」。

    ★ 本判据的作用是**把这条决策钉住**：谁"顺手"把它也改成含 schema，
    这条会红，而红的时候他会看到这段说明 —— 而不是以为自己在修一个 bug。
    ★ 真要改这个口径，按本仓纪律要**同步更新 `CORE-ARCHITECTURE.md` 里那条已决策**。
    """
    import inspect

    from workbench.harness import loop as loop_mod

    src = inspect.getsource(loop_mod._over_hard_limit)
    assert "estimate_messages_tokens" in src
    assert "estimate_request_tokens" not in src, (
        "硬上限改成含 schema 了 —— 那会取代一条已决策（见 _over_hard_limit 的文档串），"
        "请先更新 CORE-ARCHITECTURE.md 的已决策条目再改"
    )


# ---------------------------------------------------------------- 边界


@pytest.mark.parametrize("messages", [
    [],
    [{"role": "assistant", "content": ""}],
    [{"role": "assistant", "content": None}],          # 真的会出现（见 _with_system_prompt）
    [{"role": "assistant", "tool_calls": []}],          # 空列表 ≠ 有调用
    [{"role": "tool", "tool_call_id": "", "content": "x"}],
])
def test_degenerate_inputs_do_not_explode(messages) -> None:
    """★ 退化的输入不炸：`content` 缺失 / `None`、`tool_calls` 是空列表、id 是空串。

    `estimate_*` 跑在**每一次模型调用之前**（折叠判定与硬上限都用它）——
    它抛异常的话整轮对话就断了，而那是"一个统计函数把主流程搞挂"。
    """
    assert ctx.estimate_messages_tokens(messages) >= 0
    assert ctx.estimate_tools_tokens(None) == 0
    assert ctx.estimate_tools_tokens([]) == 0
    assert ctx.estimate_request_tokens(messages, None) >= 0


# ---------------------------------------------------------------- 偏差表


def _load_tool():
    """把 `tools/compare_token_estimators.py` 当模块载进来（它不是包的一部分）。"""
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "_compare_tokens", root / "tools" / "compare_token_estimators.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_deviation_table_excludes_synthetic_usage() -> None:
    """★★ **评测会话的 span 不进偏差表** —— 它们的 `usage` 是脚本编的。

    ## ★ 这个坑是实测撞到的

    第一版没排除，偏差表兴高采烈地报出「**24 个真实请求，比值中位数 27.9**」——
    而 24 个样本**全是 `eval-*`**：评测跑的是脚本化客户端，它回一个编好的
    `prompt_tokens: 100`。⇒ 那份偏差表看起来像一份结果，
    实际是在拿我们的估算去比一个**编出来的数**。

    ★ 同 PITFALLS #112 的族：**看起来有结论的测量，测的不是那件事**。
    """
    from workbench import db
    from workbench.harness import trace

    db.connect()
    tool = _load_tool()
    # 一个评测会话（合成 usage）+ 一个真实会话
    for conv, tokens in (("eval-probe_case", 100), ("real-conversation", 3000)):
        recorder = trace.RunRecorder(conv, "probe-model")
        recorder.span(trace.SPAN_MODEL, "probe-model", started=0.0, duration_ms=5,
                      attrs={"estimatedTokens": 2800, "messages": 4, "tools": 18,
                             "usage": {"prompt_tokens": tokens,
                                       "prompt_cache_hit_tokens": 0}})
        recorder.finish({})

    pairs = tool.collect(limit=200)
    convs = {p["conversation"] for p in pairs}
    assert "eval-probe_case" not in convs, (
        f"评测会话进了偏差表 —— 它的 usage 是脚本编的，会得出一个假结论：{pairs}"
    )
    assert "real-conversation" in convs, (
        f"真实会话被过滤掉了 —— 排除写错方向了（那会表现为'永远没有样本'）：{pairs}"
    )
    # 反向：调试开关必须能把它放回来（否则"排除"会退化成"看不见"）
    with_synth = tool.collect(limit=200, include_synthetic=True)
    assert "eval-probe_case" in {p["conversation"] for p in with_synth}


def test_the_deviation_table_separates_under_and_over_estimation() -> None:
    """★ **低估与高估分开报** —— 它们的后果完全不同。

    低估 ⇒ 该折叠没折叠（直到平台把请求拒回来）；高估 ⇒ 折叠偏早（多花一次摘要，不坏）。
    ★ 合成一个"一半低估、一半高估"的样本集，断言两边各自被数到 ——
    只报一个"平均偏差"会把这两件事抹成一个看起来中性的数。
    """
    tool = _load_tool()
    pairs = [{"estimated": 80, "actual": 100},     # 低估 20%
             {"estimated": 130, "actual": 100},    # 高估 30%
             {"estimated": 100, "actual": 100}]    # 正好
    d = tool.deviation(pairs)
    assert d["underestimated"] == 1, d
    assert d["overestimated"] == 1, d
    assert d["within_tolerance"] == 1, d
