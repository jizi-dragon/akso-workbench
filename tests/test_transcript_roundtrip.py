r"""transcript 落库的保真（计划 §7.3）。

## 这一节守的是**一句话**：落库的必须是"当时逐字节发出去的那一条"

不是"重新构造的近似"。理由很实际：只有落的是原件，
「**重放组装 == 当时请求**」才是一个**能断言**的等式。
落一个近似值的话，断言只能验"我构造的近似还在不在" —— 而那恰恰是它要防的东西。

## 判据怎么做到"逐字段"

`tests/eval/runner` 那套录制层（`record.RecordingClient`）会把**每一次真实的模型
请求原样存下来**。所以这里的做法是：

1. 用一个脚本化客户端**真跑一次**两轮工具调用的回合（请求被录下来）；
2. 从**库里**把 transcript 取回来（`storage.model_transcript`）；
3. 断言它 == 录下来的那次请求里的**那一段**，`==` 而不是"包含"。

★ 这是本仓最容易被写坏的地方之一（I1 的接口），所以判据必须是**相等**：
任何一处字段名、顺序、`tool_call_id` 取法的偏差都会让它红。

## 本文件钉住的五件事

| 断言 | 它防的是 |
|---|---|
| 落库的 assistant+tool 段与录制请求**逐字段相等**（串行） | 落了一个"长得像"的近似 |
| 同上（**并发快路径**） | 两条路径各写一份构造逻辑，改一条漏一条 |
| `tool_call_id` 与 `assistant.tool_calls[].id` **配对** | I16 的非法历史（assistant 带 tool_calls 却没有配对响应） |
| 最终那条 assistant **不被写重** | 模型看到自己说了两遍同样的话 |
| `seq` 会话内单调，且**同一毫秒内也不乱序** | 排序键是毫秒时间戳（并列是常态，见迁移 25） |
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from workbench import approvals, db
from workbench import plugin_api as pa
from workbench.harness import record, transcript
from workbench.harness.loop import run_agent_loop
from workbench.harness.types import AgentConfig
from workbench.services import storage


@pytest.fixture()
def ready():
    db.connect()
    return db


class Args(BaseModel):
    n: int = pa.Arg("数字", default=1)


class Scripted:
    """按脚本回答的假模型。"""

    def __init__(self, script: list[dict], *, model: str = "probe-model") -> None:
        self._script = list(script)
        self.model = model
        self.last_usage: dict = {}

    async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
        step = self._script.pop(0) if self._script else {"content": "（脚本耗尽）",
                                                        "tool_calls": []}
        yield {
            "type": "end",
            "content": step.get("content", ""),
            "tool_calls": step.get("tool_calls", []),
            "usage": {"prompt_tokens": 10, "prompt_cache_hit_tokens": 0,
                      "prompt_cache_miss_tokens": 10, "completion_tokens": 5},
            "finish_reason": "stop",
        }


class Probe:
    """把**每一次真实请求的 messages 原样抄下来**。

    ★ 为什么不从 `RecordingClient` 的夹具里取：夹具存的是**模型响应**
    （`event` = content/tool_calls/usage/finish_reason）加一个请求**指纹**，
    而请求正文本身不入库 —— 那是对的（夹具要能被 diff，不该塞进整个上下文）。
    所以"当时发出去的那一条"只能**在现场**抄一份，这正是本文件要断言的东西。
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.model = getattr(inner, "model", "")
        self.last_usage: dict = {}
        self.requests: list[list[dict]] = []

    async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
        # ★ 深拷贝：循环随后会往同一个 list 上 append，浅拷贝会让"当时发的"跟着变。
        import copy

        self.requests.append(copy.deepcopy(list(messages)))
        async for event in self._inner.stream_chat(
            messages, tools=tools, temperature=temperature, max_tokens=max_tokens
        ):
            if event.get("type") == "end":
                self.last_usage = dict(event.get("usage") or {})
            yield event


def _call(call_id: str, name: str, n: int = 1) -> dict:
    """★ 形状是**扁平**的（`{"id","name","arguments"}`），不是 OpenAI 的嵌套形态。

    这不是随手定的：`DeepSeekClient` 在收流时已经把线格式归一成这个形状，
    循环读的就是它（`call.get("name")` / `call.get("arguments")`）。
    用嵌套形态写脚本的话，工具名会读成空字符串 —— 而**表现只是"未知工具调用"**。
    """
    return {"id": call_id, "name": name, "arguments": f'{{"n": {n}}}'}


def _hub(monkeypatch, *, parallel_safe: bool) -> pa.ToolHub:
    approvals.reset_store()
    hub = pa.ToolHub()
    approvals.install(hub)
    monkeypatch.setattr(pa, "_hub", hub)

    async def handler(args: Args, ctx: pa.ToolContext) -> str:
        return f"结果-{args.n}"

    hub.add_plugin(pa.Plugin(id="probe", title="探针", tools=[pa.ToolSpec(
        name="probe_read", description="读一个数", args_model=Args, handler=handler,
        risk=pa.Risk.READ, trust=pa.Trust.LOCAL, timeout_s=5,
        parallel_safe=parallel_safe,
    )]))
    return hub


def _drive(conv_id: str, script: list[dict], hub: pa.ToolHub) -> "Probe":
    """跑一个回合，返回那个抄下了**每一次真实请求**的探针。"""
    probe = Probe(Scripted(script))
    recorder = record.RecordingClient(probe, name="roundtrip")
    messages = [
        {"role": "system", "content": "你是探针。"},
        {"role": "user", "content": "读两个数"},
    ]

    async def run():
        events = []
        async for event in run_agent_loop(
            messages, recorder, hub,
            AgentConfig(model="probe-model", max_steps=5),
            conversation_id=conv_id,
        ):
            events.append(event)
        return events

    asyncio.run(run())
    return probe


def _tail_of(messages: list[dict]) -> list[dict]:
    """取录制请求里**这一段工具轨迹**：从第一条带 `tool_calls` 的 assistant 开始。"""
    for index, message in enumerate(messages):
        if message.get("role") == "assistant" and message.get("tool_calls"):
            return messages[index:]
    return []


# ---------------------------------------------------------------- 保真


def test_the_stored_transcript_is_field_for_field_equal_to_what_was_sent(ready,
                                                                        monkeypatch) -> None:
    """★★ **落库的那一段 == 当时发出去的那一段**（逐字段相等）。

    这是整节的判据。`==` 而不是"包含"，因为要防的正是
    "字段名对了、取法不同、顺序不同"这一类**看起来完全正常**的偏差。
    """
    conv = storage.create_conversation("roundtrip-serial")
    conv_id = conv["id"] if isinstance(conv, dict) else conv
    hub = _hub(monkeypatch, parallel_safe=False)

    probe = _drive(conv_id, [
        {"content": "先读第一个", "tool_calls": [_call("c1", "probe_read", 1)]},
        {"content": "再读第二个", "tool_calls": [_call("c2", "probe_read", 2)]},
        {"content": "都读完了", "tool_calls": []},
    ], hub)

    sent = probe.requests[-1]
    tail = _tail_of(sent)
    assert len(tail) == 4, f"录制请求里应该有 2 组 assistant+tool：{tail}"

    stored = storage.model_transcript(conv_id)
    assert stored == tail, (
        "落库的 transcript 与当时发出去的那一段不相等。\n"
        f"  库里：{stored}\n  发出去：{tail}"
    )


def test_the_parallel_fast_path_persists_the_same_way(ready, monkeypatch) -> None:
    """★★ **并发快路径落库的口径与串行路径一致。**

    两条路径各有一个 `context.append(_tool_message(...))`（那正是 `_tool_message`
    被收敛成单一构造点的理由）。★ 只验串行的话，并发那条**漏接落库**
    不会有任何东西变红 —— 而它恰恰是最近才加的那条路。
    """
    conv = storage.create_conversation("roundtrip-parallel")
    conv_id = conv["id"] if isinstance(conv, dict) else conv
    hub = _hub(monkeypatch, parallel_safe=True)

    probe = _drive(conv_id, [
        {"content": "一起读", "tool_calls": [_call("p1", "probe_read", 1),
                                          _call("p2", "probe_read", 2)]},
        {"content": "读完了", "tool_calls": []},
    ], hub)

    sent = probe.requests[-1]
    tail = _tail_of(sent)
    assert len(tail) == 3, f"并发批应该是 1 条 assistant + 2 条 tool：{tail}"

    stored = storage.model_transcript(conv_id)
    assert stored == tail, (
        "并发快路径落库的口径与串行不一致（它是不是漏接了？）\n"
        f"  库里：{stored}\n  发出去：{tail}"
    )


def test_every_tool_message_pairs_with_a_declared_call(ready, monkeypatch) -> None:
    """★ `tool_call_id` 必须与 `assistant.tool_calls[].id` **配对**（不变量 I16）。

    ★ 这条不重复上面的相等断言 —— 它验的是**等式两边都错得一样**的那种情况：
    如果 `_tool_message` 取 id 的取法与 `assistant` 里的不一致，两者会
    **一起**漂到同一个错值上，而"相等"仍然成立。配对检查是独立的一票。
    """
    conv = storage.create_conversation("roundtrip-pairing")
    conv_id = conv["id"] if isinstance(conv, dict) else conv
    hub = _hub(monkeypatch, parallel_safe=False)

    _drive(conv_id, [
        {"content": "读", "tool_calls": [_call("k1", "probe_read", 1),
                                       _call("k2", "probe_read", 2)]},
        {"content": "好了", "tool_calls": []},
    ], hub)

    declared: list[str] = []
    answered: list[str] = []
    for message in storage.model_transcript(conv_id):
        if message["role"] == "assistant":
            declared += [str(c.get("id") or "") for c in message.get("tool_calls") or []]
        else:
            answered.append(str(message.get("tool_call_id") or ""))
    assert declared == ["k1", "k2"], declared
    assert sorted(answered) == sorted(declared), (declared, answered)


def test_record_refuses_a_final_assistant(ready) -> None:
    """★★ **`transcript.record` 自己拒绝不带 `tool_calls` 的 assistant。**

    ## ★ 这条判据我第一版写成了端到端，而它**不可证伪**

    第一版是"跑一个回合，断言最终那句没进 transcript"。它**永远绿**：
    真正的第一道防线是循环在 `final` 分支里的提前 `return`（不带 tool_calls 的
    assistant 根本走不到那行 `record` 调用）。⇒ 拆掉 `record` 里的护栏**也照样绿**
    —— 实测过：把循环和 `record` 里的两道护栏**一起**拆掉，它仍然全绿。

    **一条不会被证伪的断言比没有断言更糟**：它占着"这里验过了"的位置。
    （同族：本轮还删掉了一条 flaky 的"毫秒并列确实会发生"。）

    ⇒ 判据改成**直接打 `record`**：它才是"最终那条不会被写重"的最后一道防线，
    而这条断言拆掉那道护栏就当场变红（实测）。
    """
    conv = storage.create_conversation("record-guard")
    conv_id = conv["id"] if isinstance(conv, dict) else conv

    # ① 不带 tool_calls 的 assistant：**不落**
    transcript.record(conv_id, {"role": "assistant", "content": "答案是 1"})
    assert storage.model_transcript(conv_id) == [], (
        "最终那条 assistant 被 transcript 落库了 —— 模型下一轮会看到自己说了两遍"
    )

    # ② 带 tool_calls 的 assistant：**必须落**（否则这个"拒绝"会退化成"什么都不落"）
    transcript.record(conv_id, {
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "z1", "name": "probe_read", "arguments": "{}"}],
    })
    stored = storage.model_transcript(conv_id)
    assert len(stored) == 1 and stored[0]["tool_calls"][0]["id"] == "z1", stored


def test_an_end_to_end_run_leaves_no_final_assistant(ready, monkeypatch) -> None:
    """★ 端到端复核：**跑完一个真实回合之后**，库里也没有最终那条 assistant。

    ⚠️ 这条**不是**主要的判据（它观察的是净效果，拆掉任一道护栏它都可能仍然绿，
    见上一条的说明）。它留在这里是因为它验的是**用户真正会遇到的形状**：
    整条链路上，最终那句只有一个落点（`routes_chat`）。
    """
    conv = storage.create_conversation("roundtrip-final")
    conv_id = conv["id"] if isinstance(conv, dict) else conv
    hub = _hub(monkeypatch, parallel_safe=False)

    _drive(conv_id, [
        {"content": "读一下", "tool_calls": [_call("f1", "probe_read", 1)]},
        {"content": "答案是 1", "tool_calls": []},
    ], hub)

    finals = [m for m in storage.model_transcript(conv_id)
              if m["role"] == "assistant" and m["content"] == "答案是 1"]
    assert finals == [], f"最终那条被 transcript 写进去了：{finals}"
    with_calls = [m for m in storage.model_transcript(conv_id) if m.get("tool_calls")]
    assert len(with_calls) == 1, with_calls


# ---------------------------------------------------------------- 定序


def test_seq_is_monotonic_within_a_conversation(ready) -> None:
    """★ `seq` **会话内单调递增** —— 它是 `list_messages` 唯一的排序键。

    与 `cache.summarized_upto` 用"消息条数"是同一个理由：单调递增**能做到**，
    而"按 id 排"做不到（id 是随机 hex）。
    """
    conv = storage.create_conversation("seq-mono")
    conv_id = conv["id"] if isinstance(conv, dict) else conv
    for i in range(6):
        storage.insert_message(conv_id, "assistant", f"M{i}")

    seqs = [row["seq"] for row in storage.list_messages(conv_id)]
    assert seqs == sorted(seqs) and len(set(seqs)) == 6, seqs
    assert [row["content"] for row in storage.list_messages(conv_id)] == \
        [f"M{i}" for i in range(6)]

def test_ordering_follows_seq_not_the_timestamp(ready) -> None:
    """★★ **排序键是 `seq`，不是 `created_at`** —— 让两者给出**不同**答案再断言。

    ## ★ 这一条我第一版写错了，记下来

    第一版是"把 4 条消息的 `created_at` 全设成同一个值，再把 `seq` 倒过来"。
    **它没有鉴别力**：实测 `ORDER BY created_at ASC` 在那个局面下**也**返回
    `T3,T2,T1,T0` —— 因为迁移 25 建的索引 `(conversation_id, seq)` 成了扫描路径，
    并列项的顺序于是**跟着索引走**。于是"退回 created_at"那个反证是绿的，
    而**判据本身没毛病，是它根本没在问那个问题**。

    ## 正确的形状：让两个候选键**互相矛盾**

    把 `created_at` 设成与 `seq` **相反**的顺序 ⇒ 两个键给出完全不同的答案，
    这时断言才有鉴别力（"退回 created_at"会当场变红，实测过）。

    ★ 这条例子的意义不只是排序：一个回合里的 `assistant` 与它的 `tool` 结果
    是**背靠背**写进去的（毫秒常常相同），顺序错了会让整段历史**非法**（I16）。
    """
    conv = storage.create_conversation("seq-vs-time")
    conv_id = conv["id"] if isinstance(conv, dict) else conv
    for i in range(4):
        storage.insert_message(conv_id, "assistant", f"T{i}")

    # 时间戳与 seq **相反**：按时间排会得到 T3,T2,T1,T0；按 seq 排得到 T0,T1,T2,T3
    db.execute(
        "UPDATE messages SET created_at = CASE content "
        "WHEN 'T0' THEN 5000 WHEN 'T1' THEN 4000 "
        "WHEN 'T2' THEN 3000 WHEN 'T3' THEN 2000 END WHERE conversation_id = ?",
        (conv_id,),
    )

    by_time = [r["content"] for r in db.query(
        "SELECT content FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
        (conv_id,))]
    assert by_time == ["T3", "T2", "T1", "T0"], (
        f"前提不成立：按时间排应当是反序，实际 {by_time} —— 这条判据失去鉴别力"
    )

    got = [row["content"] for row in storage.list_messages(conv_id)]
    assert got == ["T0", "T1", "T2", "T3"], (
        "读回顺序不是按 seq —— 排序键仍在用 created_at（毫秒）\n"
        f"  实际：{got}"
    )


# ★ 曾经还有一条 `test_timestamp_ties_really_happen`：断言"8 条连续插入的消息
# 会落在少于 8 个毫秒里"。**它是 flaky 的** —— 换个机器/负载就可能 8 条落进 8 个毫秒
# （实测两种结果都出现过）。⇒ 删掉。
#
# 它想证的前提（"毫秒并列是常态"）本身仍然成立，但那是**一次测量**，不是一条判据：
# 测量结果写在迁移 25 的注释里（并标明它随机器而变），而判据只留上面那条
# **有鉴别力**的（让两个候选排序键互相矛盾）。
#
# ★ 教训：一条会随机器速度变红变绿的断言，比没有断言更糟 —— 它会训练人忽略它。
