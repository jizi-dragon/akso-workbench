"""Agent 行为契约（`docs/CORE-AGENT-PLAN.md` §0.1）。

## 为什么需要这一层

计划写明："后面每个阶段都会动 `loop.py` / `cache.py`，而这两个文件的错误
**不会让现有测试变红**：它们用假客户端注入**预写脚本**，从不检查'发出去的 messages
长什么样'、也不检查'预算有没有被遵守'。" 所以先把契约写成断言。

## 契约（每条都是断言，不是描述）

| # | 断言 | 不变量 |
|---|---|---|
| C1 | 一次请求的 messages 第 0 条是**原始** system 提示词（任何折叠状态下） | I15 |
| C2 | `summarized_upto` 单调不减 | I2 |
| C3 | 发出的 `assistant.tool_calls` 与 `tool` 消息**成对** | I16 |
| C4 | 工具调用次数 ≤ `max_steps`（**含一轮多调用**） | —— |
| C5 | 每次模型调用前的估算 ≤ 硬上限，否则必须有终止事件 | I17 |

## 一处诚实的观测限制

C3 在**正常轮次**里能直接从"下一次请求的 messages"上验到（本文件 `test_c3_*_normal_turn`）。
但**预算截断那一轮**验不到——因为那条路径会 `return`，被截断的 `context` 随后就被丢掉了，
外部拿不到引用。所以截断路径只用**事件级**断言（每个 tool_call 都有一条 `observation`，
执行数 + skipped 数 == 调用数）+ 对 `_fill_unexecuted` 的函数级断言来钉。
不写"这条也验到了"——那是假话。
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


@pytest.fixture()
def ready():
    db.connect()
    return db


class ScriptedClient:
    """按脚本逐轮返回的假 LLM；**记录每次调用时发出去的 messages**（这才是契约的观测面）。"""

    def __init__(self, script: list[dict], summary: str = "摘要") -> None:
        self._script = list(script)
        self._summary = summary
        self.calls: list[list[dict]] = []
        self.model = "fake"
        self.last_usage: dict = {}

    async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
        self.calls.append([dict(m) for m in messages])
        step = self._script.pop(0) if self._script else {"content": "（脚本耗尽）",
                                                        "tool_calls": []}
        yield {
            "type": "end",
            "content": step.get("content", ""),
            "tool_calls": step.get("tool_calls", []),
            "usage": {"prompt_tokens": 10, "prompt_cache_hit_tokens": 10,
                      "prompt_cache_miss_tokens": 0, "completion_tokens": 1},
        }

    async def chat(self, messages, temperature=0.7, max_tokens=None, json_mode=False):
        self.last_usage = {"prompt_tokens": 5, "prompt_cache_hit_tokens": 0,
                           "prompt_cache_miss_tokens": 5}
        return self._summary


class EchoArgs(BaseModel):
    text: str = pa.Arg("内容", default="")


@pytest.fixture()
def hub(ready, monkeypatch):
    """带一个计数回显工具的极简中枢；返回 `(hub, executed)`。"""
    approvals.reset_store()
    tool_hub = pa.ToolHub()
    approvals.install(tool_hub)
    executed: list[str] = []

    async def handler(args: EchoArgs, ctx: pa.ToolContext) -> str:
        executed.append(args.text)
        return json.dumps({"ok": True, "echo": args.text})

    tool_hub.add_plugin(pa.Plugin(id="echo", title="回显", tools=[
        pa.ToolSpec(name="echo_text", description="回显", args_model=EchoArgs,
                    handler=handler, risk=pa.Risk.READ, trust=pa.Trust.LOCAL, timeout_s=5),
    ]))
    monkeypatch.setattr(pa, "_hub", tool_hub)
    return tool_hub, executed


def _calls(n: int) -> list[dict]:
    return [{"id": f"c{i}", "name": "echo_text",
             "arguments": json.dumps({"text": f"n{i}"})} for i in range(n)]


async def _drain(gen) -> list[dict]:
    return [event async for event in gen]


def _drive(hub, conv_id: str, script: list[dict], config: AgentConfig,
           history: list[dict] | None = None):
    cache.clear_state(conv_id)
    client = ScriptedClient(script)
    events = asyncio.run(_drain(run_agent_loop(
        history or [{"role": "user", "content": "开始"}],
        client, hub, config, conversation_id=conv_id,
    )))
    return client, events


# ---------------------------------------------------------------- C1 / I15


def test_c1_every_request_starts_with_the_original_system_prompt(ready, hub) -> None:
    """★ C1：**任何**折叠状态下，第 0 条都是原始 system 提示词（不变量 I15）。

    长历史 + 极小窗口 ⇒ 必然折叠。折叠是这条契约最容易破的时刻：修复前
    `summarized_upto` 含首条 system，一折就把提示词并进摘要、原文永久消失。
    """
    tool_hub, _ = hub
    history = [{"role": "user", "content": "很长的一段内容" * 200} for _ in range(8)]
    # 窗口要选得**能装下折叠后的结果**：`keep_recent_turns=1` 会留下一条 ~600 token 的消息，
    # 所以窗口不能小于它 —— 否则硬上限会（正确地）在发请求前就终止，
    # 于是本用例变成"什么都没验到"。这个坑第一次就是踩在这里。
    config = AgentConfig(system_prompt="唯一的系统提示词", max_steps=4,
                         context_window_tokens=2_000, context_threshold=0.8,
                         keep_recent_turns=1)
    client, _events = _drive(tool_hub, "contract-c1", [{"content": "好"}], config, history)

    assert client.calls, "模型一次都没被调用，用例没验到东西"
    for index, sent in enumerate(client.calls):
        assert sent[0]["role"] == "system", f"第 {index} 次请求的第 0 条不是 system"
        assert sent[0]["content"] == "唯一的系统提示词", (
            f"第 {index} 次请求的 system 被换掉了：{sent[0]['content'][:80]!r}"
        )


# ---------------------------------------------------------------- C2 / I2


def test_c2_summary_boundary_only_moves_forward(ready, hub) -> None:
    """C2：`summarized_upto` 单调不减（不变量 I2）——回退等于前缀回退，缓存恒 miss。"""
    tool_hub, _ = hub
    conv_id = "contract-c2"
    history = [{"role": "user", "content": "很长的一段内容" * 200} for _ in range(8)]
    config = AgentConfig(system_prompt="sys", max_steps=4, context_window_tokens=2_000,
                         context_threshold=0.8, keep_recent_turns=1)

    seen: list[int] = []
    for _ in range(3):
        client, events = _drive(tool_hub, conv_id, [{"content": "好"}], config, history)
        # 必须先确认这一轮**真的发了请求**：否则"边界单调"可能只是因为
        # `_build_context` 折了一次就撞硬上限终止，本用例什么都没验到。
        assert client.calls, "这一轮没发请求，用例没验到东西"
        assert not [e for e in events if e.get("reason") == "context_exceeded"], (
            "这一轮撞了硬上限，说明窗口选小了"
        )
        seen.append(cache.load_state(conv_id).summarized_upto)
        history = [*history, {"role": "user", "content": "更长" * 200}]

    assert seen == sorted(seen), f"摘要边界回退了：{seen}"
    assert seen[-1] > seen[0], f"三轮下来边界没有前进，折叠可能压根没发生：{seen}"


# ---------------------------------------------------------------- C3 / I16


def test_c3_tool_calls_are_paired_on_a_normal_turn(ready, hub) -> None:
    """★ C3：一轮里发 N 个 `tool_call`，下一条请求里必须有 N 条配对 `tool` 消息（I16）。

    这条从"下一次请求的 messages"上直接验——那是唯一的真观测面。
    """
    tool_hub, executed = hub
    config = AgentConfig(system_prompt="sys", max_steps=10, approval_wait_s=0.05)
    client, _events = _drive(tool_hub, "contract-c3", [
        {"content": "", "tool_calls": _calls(5)},
        {"content": "收尾"},
    ], config)

    assert len(executed) == 5, f"5 个调用应当都执行，实际 {len(executed)}"
    assert len(client.calls) >= 2, "应当有第二次请求（工具结果回填后）"
    sent = client.calls[1]
    assistant = next(m for m in sent if m.get("tool_calls"))
    ids = [c["id"] for c in assistant["tool_calls"]]
    paired = [m.get("tool_call_id") for m in sent if m.get("role") == "tool"]
    assert sorted(ids) == sorted(paired), (
        f"tool_calls 与 tool 消息不成对：发起 {ids}，配对 {paired}"
    )


def test_c3_budget_truncated_batch_backfills_every_unexecuted_call(ready, hub) -> None:
    """★ C3（截断路径）：预算在**批次内**用尽时，未执行的调用必须留下痕迹。

    旧实现只在两次模型轮次之间检查预算，且**直接 `return`**——`assistant` 那条消息
    带着 N 个 `tool_call` 却只有前几个有配对响应，是一段**非法历史**。

    ⚠️ 这条路径会终止本轮，被截断的 `context` 随之丢弃、外部拿不到引用，
    所以只能在**事件层**断言（见文件头「一处诚实的观测限制」）。
    """
    tool_hub, executed = hub
    config = AgentConfig(system_prompt="sys", max_steps=2, approval_wait_s=0.05)
    _client, events = _drive(tool_hub, "contract-c3-trunc",
                             [{"content": "", "tool_calls": _calls(5)}], config)

    assert len(executed) == 2, f"max_steps=2 应当只执行 2 次，实际 {len(executed)}"

    observations = [e for e in events if e["type"] == "observation"]
    skipped = [e for e in observations if e["result"]["status"] == "skipped"]
    assert len(observations) == 5, f"5 个调用应当各有一条观测，实际 {len(observations)}"
    assert len(skipped) == 3, f"应当有 3 条 skipped，实际 {len(skipped)}"
    assert [e.get("reason") for e in events if e["type"] == "error"] == ["budget_exhausted"]


def test_fill_unexecuted_pairs_every_call() -> None:
    """`_fill_unexecuted` 的函数级契约：**每个** id 都补上，且一条不多一条不少。

    ⚠️ 这个私有 helper **在函数内惰性导入**：本文件其余部分是"行为契约"，
    要能在**修复前的 `loop.py`** 上被收集并跑出有信息量的红（反证用）。
    放在模块级的话，旧代码下是**收集错误**（ImportError）而不是断言红 ——
    那证明不了"这些断言抓得住缺陷"。
    """
    from workbench.harness.loop import _fill_unexecuted

    context: list[dict] = [{"role": "assistant", "tool_calls": _calls(3)}]
    _fill_unexecuted(context, _calls(3)[1:], "测试原因")
    tools = [m for m in context if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tools] == ["c1", "c2"]
    assert all("测试原因" in m["content"] for m in tools), "原因必须写进正文（模型要看得见）"


def test_loop_detection_also_backfills(ready, hub) -> None:
    """重复调用检测那条早退路径**同样**要回填 —— 它此前也是直接 `return`。"""
    tool_hub, _executed = hub
    config = AgentConfig(system_prompt="sys", max_steps=20, approval_wait_s=0.05)
    same = _calls(1)[0]
    _client, events = _drive(tool_hub, "contract-loop", [
        {"content": "", "tool_calls": [same]},
        {"content": "", "tool_calls": [same]},
        {"content": "", "tool_calls": [same]},
    ], config)
    assert any(e["type"] == "error" and "重复" in e.get("message", "") for e in events)


# ---------------------------------------------------------------- C4


def test_c4_step_budget_holds_for_a_multi_call_turn(ready, hub) -> None:
    """C4：`max_steps` 是**工具调用次数**的上限，一轮里多发也不许超。"""
    tool_hub, executed = hub
    config = AgentConfig(system_prompt="sys", max_steps=3, approval_wait_s=0.05)
    _client, events = _drive(tool_hub, "contract-c4", [
        {"content": "", "tool_calls": _calls(5)},
        {"content": "收尾"},
    ], config)
    assert len(executed) == 3, f"预算 3，实际执行 {len(executed)}"
    assert any(e.get("reason") == "budget_exhausted" for e in events if e["type"] == "error")


# ---------------------------------------------------------------- C5 / I17


def test_c5_context_hard_limit_terminates_before_an_over_window_request(ready, hub) -> None:
    """★ C5：上下文超硬上限时**不发**请求，改用可行动的文案终止（不变量 I17）。

    旧实现整轮只算一次预算，工具结果不断追加却从不复检 —— 超窗后由**平台**拒回来，
    而那句话用户与模型都无法据以行动。
    """
    tool_hub, _executed = hub
    config = AgentConfig(system_prompt="sys", max_steps=10,
                         context_window_tokens=120, context_hard_limit_tokens=120,
                         approval_wait_s=0.05)
    history = [{"role": "user", "content": "很长的一段内容" * 60}]
    client, events = _drive(tool_hub, "contract-c5", [{"content": "好", "tool_calls": _calls(2)}],
                            config, history)

    reasons = [e.get("reason") for e in events if e["type"] == "error"]
    assert "context_exceeded" in reasons, f"没有 context_exceeded 终止事件：{reasons}"
    # 并且**真的没有**发出任何超上限的请求
    for index, sent in enumerate(client.calls):
        assert estimate_messages_tokens(sent) <= config.context_hard_limit_tokens, (
            f"第 {index} 次请求超了硬上限：{estimate_messages_tokens(sent)}"
        )


# ---------------------------------------------------------------- C6 / 账本（§5.4a）


class ExplodingClient(ScriptedClient):
    """**先花钱再炸**：第一轮正常跑完（还执行了一次工具），第二轮直接抛。

    ## 为什么是跨轮，而不是"同一轮里先吐 usage 再抛"

    第一版就是那么写的，结果 `DID NOT RAISE` —— 因为 **`_one_model_turn` 收到 `end`
    事件就 `return` 了，它不会再向 `stream_chat` 要下一个元素**：写在同一轮 `end`
    之后的 `raise` 永远不会被执行（`stream_chat` 被 `aclose()`，异常路径根本没走到）。

    这不是测试的怪癖，而是**真实的失败时序**：钱在**已经收完的那一轮**里花掉了
    （`prompt_tokens` 是真的），炸发生在**下一轮**。改造前这一笔一条都不落。
    """

    def __init__(self) -> None:
        super().__init__([
            {"content": "", "tool_calls": _calls(1)},
            {"content": "（第二轮不会返回）"},
        ])

    async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
        self.calls.append([dict(m) for m in messages])
        if len(self.calls) == 1:
            yield {
                "type": "end",
                "content": "",
                "tool_calls": _calls(1),
                "usage": {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 800,
                          "prompt_cache_miss_tokens": 200, "completion_tokens": 7},
            }
            return
        raise RuntimeError("模拟：第二轮请求时网络断了")


def _usage_rows(conv_id: str) -> list[dict]:
    return db.query("SELECT * FROM deepseek_usage WHERE conversation_id = ?", (conv_id,))


def test_c6_a_failed_turn_still_records_usage(ready, hub) -> None:
    """★ C6：**异常出口也要入账**（计划 §5.4a）。

    改造前全仓只有两处 `persist()`，都在正常收尾路径上 —— 于是任何异常出口
    （LLM 抛错、工具层意外抛出、`asyncio` 取消、消费方断开）**一条用量都不落**，
    账面表现是"这次回答没花钱"，而钱已经花了。`deepseek_usage` 是**唯一**的用量
    事实源，命中率与"省钱口径"都从它算 ⇒ 这类丢失不可恢复。
    """
    tool_hub, _ = hub
    conv_id = "contract-c6-fail"
    cache.clear_state(conv_id)
    client = ExplodingClient()

    with pytest.raises(RuntimeError, match="网络断了"):
        asyncio.run(_drain(run_agent_loop(
            [{"role": "user", "content": "开始"}], client, tool_hub,
            AgentConfig(system_prompt="sys", max_steps=4), conversation_id=conv_id,
        )))

    rows = _usage_rows(conv_id)
    assert len(rows) == 1, f"异常终止的回合没有落用量（落了 {len(rows)} 行）"
    assert rows[0]["prompt_tokens"] == 1000, "落下来的数字不对"
    assert rows[0]["cache_hit_tokens"] == 800, "命中量丢了——那是省钱口径的依据"


def test_c6_usage_is_persisted_exactly_once_on_a_normal_turn(ready, hub) -> None:
    """★ C6 的另一半：正常出口**只落一行**。

    这条是给上面那条兜底的。让"任何出口都入账"能成立的做法是"在 `finally` 里
    再落一次"，而**不幂等**的 `persist` 会写出**两行** —— 那比丢一行更难发现：
    丢一行让数字偏小（有人会去查），多一行让账单变成双份，看起来只像"这次特别贵"。
    """
    tool_hub, _ = hub
    conv_id = "contract-c6-once"
    _client, events = _drive(tool_hub, conv_id, [{"content": "好"}],
                             AgentConfig(system_prompt="sys", max_steps=4))

    assert [e["type"] for e in events][-2:] == ["final", "usage"], (
        f"正常出口的事件序列变了：{[e['type'] for e in events]}"
    )
    assert len(_usage_rows(conv_id)) == 1, "正常回合落了不止一行用量"


def test_c6_usage_event_is_not_attempted_on_the_exception_path(ready, hub) -> None:
    """异常出口**不发** `usage` 事件，只入账。

    `finally` 可能正因 `GeneratorExit`（消费方 `aclose()`）而执行，此时 `yield` 会抛
    `RuntimeError: async generator ignored GeneratorExit`。所以约定是：
    **入账是账本的事，`usage` 事件是界面的事**。这条断言钉住这个分工 ——
    谁"顺手"在 `_accounted` 里补一个 `yield`，这里就会红。
    """
    tool_hub, _ = hub
    conv_id = "contract-c6-silent"
    cache.clear_state(conv_id)
    seen: list[str] = []

    async def drive() -> None:
        with pytest.raises(RuntimeError):
            async for event in run_agent_loop(
                [{"role": "user", "content": "开始"}], ExplodingClient(), tool_hub,
                AgentConfig(system_prompt="sys", max_steps=4), conversation_id=conv_id,
            ):
                seen.append(event["type"])

    asyncio.run(drive())
    assert "usage" not in seen, f"异常出口发了 usage 事件：{seen}"
    assert len(_usage_rows(conv_id)) == 1, "不发事件 ≠ 不入账"


def test_c6_persist_failure_is_counted_not_swallowed_silently(ready, hub, monkeypatch) -> None:
    """★ C6：入账失败**继续吞异常**（度量不该影响回答），但**必须留下计数**（§5.4d）。

    从前只有一条 `logger.warning` —— 而持续失败的表现是"用量曲线莫名其妙变低"，
    没有任何迹象。计数由 `/api/agent/selfcheck` 的 `usagePersistFailures` 暴露。
    """
    tool_hub, _ = hub
    conv_id = "contract-c6-counted"
    cache.clear_state(conv_id)
    before = cache.usage_persist_failures()

    real_execute = db.execute

    def broken_execute(sql, params=()):  # noqa: ANN001, ANN202
        if "deepseek_usage" in sql:
            raise RuntimeError("模拟：磁盘满了")
        return real_execute(sql, params)

    monkeypatch.setattr(db, "execute", broken_execute)
    _client, events = _drive(tool_hub, conv_id, [{"content": "好"}],
                             AgentConfig(system_prompt="sys", max_steps=4))
    monkeypatch.undo()

    assert events[-1]["type"] == "usage", "入账失败不该改变事件流（度量不该影响回答）"
    assert cache.usage_persist_failures() == before + 1, "入账失败没有被计数"
    assert _usage_rows(conv_id) == [], "这条用例的前提是它真的没落上库"

    from workbench.api.routes_agent import selfcheck

    assert selfcheck()["usagePersistFailures"] >= 1, "selfcheck 没有暴露这个计数"
