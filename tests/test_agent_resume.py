r"""对话级断点续跑的前置件：**跨重启的悬空 `tool_calls` 收尾**（计划 §7.3）。

## 这一节在整条链上的位置

计划 §7.3 的"续跑"有一句前提：`agent_run` 记录最后一条**完整** turn 边界，
**恢复时从该边界继续**。要从"某个位置"继续，先得能判断**哪里是干净的** ——
而"干净"的全部含义就是 **I16**：`assistant.tool_calls` 里的每一个 id
都必须有配对的 `tool` 消息。

★ `_fill_unexecuted` 已经覆盖了**进程内**的三个出口（步数预算 / 用户取消 / 超时）。
**进程死亡**（崩溃 / 强杀 / 断电）时那三条路**一条都跑不到** ——
已经写进库的 `assistant(tool_calls)` 就永远没有配对结果，
而它的文档串自己写着：「**重放一段非法历史没有意义**」。

⇒ 本节补的就是**那第四条路**。

## ★ 两条边界（本节刻意**不**越过的）

| 不做 | 为什么 |
|---|---|
| **不重新执行**那些调用 | 一个写操作重放一次的后果与"少一条观测"不是一个量级；而**我们无法知道**上次执行到哪了（进程可能死在"已发出、未写回"那个窗口里）。宁可标成"未执行"，让模型自己重试 |
| **不恢复审批令牌** | `approvals` 的"跨重启失效"是**刻意的**（铁律 12）：用户重启了应用，凭什么还替他记住一个未确认的写操作。本节只补**历史文本**，一个令牌都不碰 |

## 本文件钉住的五件事

| 断言 | 它防的是 |
|---|---|
| 悬空调用**能被认出来**（声明了没有配对结果） | 判断不出"哪里不干净"，续跑无从谈起 |
| 补写之后 **I16 成立**（每个 id 都有配对） | 补了但补错（漏一条、id 取法不同） |
| 补写**幂等** | 每回合都调一次 ⇒ 不幂等的话历史会越补越长 |
| 正文**明说"未执行"**，且**不含**"已完成"字样 | 模型以为它执行过了，据此下结论（比失败更坏） |
| **一个调用都没有真的被执行** | "补历史"悄悄变成"重放" |

★ 还有一条端到端的：**真的跑一次运行**，确认它在开头就把上次的悬空补掉了
（否则这个函数没有任何调用方 —— 同 PITFALLS #106 / #110 那一族）。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel

from workbench import approvals, db
from workbench import plugin_api as pa
from workbench.harness import transcript
from workbench.harness.loop import run_agent_loop
from workbench.harness.types import AgentConfig
from workbench.services import storage


@pytest.fixture()
def ready():
    db.connect()
    return db


def _conv(name: str) -> str:
    conv = storage.create_conversation(name)
    return conv["id"] if isinstance(conv, dict) else conv


def _declare(conv_id: str, ids: list[str], *, content: str = "我要调工具") -> None:
    """写一条**声明了 tool_calls 的 assistant**（模拟崩溃前已经落库的那一半）。"""
    storage.insert_message(conv_id, "assistant", content, tool_calls=[
        {"id": cid, "name": "probe_read", "arguments": "{}"} for cid in ids
    ])


def _answer(conv_id: str, call_id: str) -> None:
    """写一条配对的 `tool` 结果。"""
    storage.insert_message(conv_id, "tool", "结果", tool_call_id=call_id)


def _paired(conv_id: str) -> bool:
    """I16：transcript 里每一个声明的 id 都有配对结果。"""
    declared: list[str] = []
    answered: list[str] = []
    for message in storage.model_transcript(conv_id):
        if message["role"] == "assistant":
            declared += [str(c.get("id") or "") for c in message.get("tool_calls") or []]
        else:
            answered.append(str(message.get("tool_call_id") or ""))
    return sorted(declared) == sorted(answered)


# ---------------------------------------------------------------- 认得出来


def test_a_crashed_batch_is_recognised(ready) -> None:
    """★ 崩溃留下的形态**能被认出来**：assistant 声明了 3 个，只有 1 个有结果。"""
    conv_id = _conv("resume-detect")
    _declare(conv_id, ["a", "b", "c"])
    _answer(conv_id, "a")

    dangling = transcript.dangling_calls(conv_id)
    assert [c["id"] for c in dangling] == ["b", "c"], dangling
    assert not _paired(conv_id), "前提不成立：这段历史应该是非法的"


def test_a_clean_transcript_reports_nothing(ready) -> None:
    """★ 反向：**干净的历史不该被误报**。

    只验"能认出来"的话，一个"永远报告全悬空"的实现也能过 ——
    而那会让每次运行都往历史里塞一遍 `[未执行]`。
    """
    conv_id = _conv("resume-clean")
    _declare(conv_id, ["a", "b"])
    _answer(conv_id, "a")
    _answer(conv_id, "b")

    assert transcript.dangling_calls(conv_id) == []
    assert transcript.repair_dangling(conv_id) == 0, "干净的历史不该被补"
    assert _paired(conv_id)


# ---------------------------------------------------------------- 补得对


def test_repair_makes_the_history_legal(ready) -> None:
    """★★ 补写之后 **I16 成立** —— 这是本节要买的东西。"""
    conv_id = _conv("resume-repair")
    _declare(conv_id, ["x1", "x2"])
    _answer(conv_id, "x1")

    assert transcript.repair_dangling(conv_id) == 1
    assert _paired(conv_id), "补完之后历史仍然非法"
    assert transcript.dangling_calls(conv_id) == [], "补完之后还报悬空"


def test_repair_is_idempotent(ready) -> None:
    """★★ 幂等 —— 它在**每一次运行开始时**都会被调一次。

    不幂等的话，历史会随运行次数越补越长，而那是**静默**的：
    界面上只是多几条"未执行"，看起来像"上次确实没跑"。
    """
    conv_id = _conv("resume-idempotent")
    _declare(conv_id, ["y1"])
    before = len(storage.list_messages(conv_id))

    assert transcript.repair_dangling(conv_id) == 1
    middle = len(storage.list_messages(conv_id))
    assert transcript.repair_dangling(conv_id) == 0, "第二次又补了 —— 不幂等"
    assert len(storage.list_messages(conv_id)) == middle, "第二次调用改了历史"
    assert middle == before + 1


def test_repair_says_not_executed(ready) -> None:
    """★★ 正文**明说"未执行"**，且**不许**出现"已完成"这类字样。

    模型看得见这条观测。把它写成"已完成"（或留空）会让模型以为这一步**有结果** ——
    然后据此下结论，而那**比失败更坏**（失败它会重试，误以为成功它会继续往下走）。
    """
    conv_id = _conv("resume-wording")
    _declare(conv_id, ["z1"])
    transcript.repair_dangling(conv_id)

    row = [m for m in storage.list_messages(conv_id) if m["role"] == "tool"][0]
    text = str(row["content"])
    assert "未执行" in text, text
    assert "中断" in text, f"没说清为什么没执行：{text}"
    for bad in ("已完成", "执行成功", "结果："):
        assert bad not in text, f"补写的正文里有 {bad!r}，会让模型以为它执行过了：{text}"


def test_repair_executes_nothing(ready, monkeypatch) -> None:
    """★★★ **补写不执行任何东西** —— 一个调用都不许真的跑。

    ★ 这是本节与"续跑"的分界线：补的是**历史文本**，不是重放。
    一个写操作重放一次的后果与"少一条观测"不是一个量级，
    而**我们无法知道**上次执行到哪了（进程可能死在"已发出、未写回"那个窗口里）。

    ★ 判据打在**中枢的执行入口**上（`ToolHub.execute`），
    而不是"看 handler 有没有被调"—— 后者会漏掉"调了但被风险门挡下"那一半，
    而那一半同样是"我们试图执行了它"。
    """
    conv_id = _conv("resume-no-exec")
    _declare(conv_id, ["w1", "w2"])

    called: list[str] = []
    original = pa.ToolHub.execute

    async def _spy(self, name, arguments, ctx):  # pragma: no cover —— 不该被调到
        called.append(str(name))
        return await original(self, name, arguments, ctx)

    monkeypatch.setattr(pa.ToolHub, "execute", _spy)
    transcript.repair_dangling(conv_id)
    assert called == [], f"补写过程中真的执行了工具：{called}"


# ---------------------------------------------------------------- 端到端


class _Args(BaseModel):
    n: int = pa.Arg("数字", default=1)


def test_a_real_run_repairs_the_previous_crash(ready, monkeypatch) -> None:
    """★★ **端到端**：真的跑一次运行，它在开头就把上次的悬空补掉了。

    ★ 这条防的是"函数写好了但没有任何调用方"（PITFALLS #106 / #110 那一族）：
    上面几条全在直接调 `repair_dangling`，**拆掉 `run_agent_loop` 里那一行它们照样全绿**。
    """
    conv_id = _conv("resume-e2e")
    # 模拟上一次崩溃：声明了 2 个，只落了 1 个结果
    _declare(conv_id, ["e1", "e2"], content="上次没跑完")
    _answer(conv_id, "e1")

    approvals.reset_store()
    hub = pa.ToolHub()
    approvals.install(hub)
    monkeypatch.setattr(pa, "_hub", hub)

    async def handler(args, ctx):
        return "这次的结果"

    hub.add_plugin(pa.Plugin(id="probe", title="探针", tools=[pa.ToolSpec(
        name="probe_read", description="读一个数", args_model=_Args, handler=handler,
        risk=pa.Risk.READ, trust=pa.Trust.LOCAL, timeout_s=5,
    )]))

    class _Once:
        model = "probe-model"
        last_usage: dict = {}

        async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
            yield {"type": "end", "content": "这次答完了", "tool_calls": [],
                   "usage": {"prompt_tokens": 10, "prompt_cache_hit_tokens": 0,
                             "prompt_cache_miss_tokens": 10, "completion_tokens": 2},
                   "finish_reason": "stop"}

    async def run():
        events = []
        async for event in run_agent_loop(
            [{"role": "system", "content": "s"}, {"role": "user", "content": "再试一次"}],
            _Once(), hub, AgentConfig(model="probe-model", max_steps=3),
            conversation_id=conv_id,
        ):
            events.append(event)
        return events

    asyncio.run(run())

    assert _paired(conv_id), "运行跑完了，上次崩溃留下的悬空仍在 —— 补写没接上"
    repaired = [m for m in storage.list_messages(conv_id)
                if m["role"] == "tool" and "未执行" in str(m["content"])]
    assert len(repaired) == 1, f"应该正好补 1 条（e2）：{[m['content'] for m in repaired]}"
    assert json.loads(repaired[0]["tool_calls"] or "null") is None  # 补的是结果，不是声明


def test_a_pending_approval_from_before_the_restart_is_not_honoured(ready) -> None:
    """★★★ **跨重启的待确认操作绝不恢复**（铁律 12，`approvals` 的刻意行为）。

    ★ 这条**不是**在测本节新加的东西，而是在钉住一条**相邻的**决策：
    "补写历史"与"恢复一个未确认的写操作"看起来都是"把上次的状态接上"，
    但前者应该做、后者**明确不做**。两条放在一起，是为了让下一个改这里的人
    看到那条界线在哪 —— 而不是顺手把 pending 也"续"上。
    """
    approvals.reset_store()
    store = approvals.pending_store()
    # 造一个"上次留下的待确认"
    pending = store.request(
        pa.ToolSpec(name="probe_write", description="写", args_model=_Args,
                    handler=None, risk=pa.Risk.WRITE, trust=pa.Trust.LOCAL,
                    reversibility=pa.Reversibility.IRREVERSIBLE),
        _Args(), conversation_id="resume-pending")
    assert store.get(pending.id) is not None, "前提不成立：待确认没建起来"

    # 重启的等价动作：`approvals` 装配时会跑 `_expire_orphans()`。
    # ⚠️ 查**库**而不是查 `store.get()` —— 真实重启之后内存是空的，
    # 而"重启后还剩什么"的唯一事实源是那张表（`store.get` 只找内存，实测返回 None）。
    approvals._expire_orphans()
    approvals.reset_store()

    row = db.query_one("SELECT status FROM pending_action WHERE id = ?", (pending.id,))
    assert row is not None, "待确认没有落库 —— 那它跨重启本来就不存在"
    assert row["status"] == "expired", (
        f"重启之后残留的 pending 没有被标成 expired：{row}"
    )

    # ★ 而且它**不能被批准** —— "标了 expired"与"批准不了"是两件事
    assert approvals.pending_store().approve(pending.id) is None, (
        "一条 expired 的待确认竟然还能被批准"
    )
