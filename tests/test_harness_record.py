r"""模型侧录制/回放台架（计划 §0.2）。

## 这个台架服务的是什么

`CORE-AGENT-PLAN.md` 后续每一步（评测、上下文治理、工具面裁剪、可信度标签）都要
**反复跑同一条对话**看行为有没有变。没有它，每次验证都要真调模型 —— 于是
"不花钱就不能验证""改了坏没坏分不开""夹具覆盖度不可证"三件事同时成立。

## 本文件的三条主线

| 主线 | 断言什么 |
|---|---|
| 录制 → 回放**逐字段相等** | 回放出来的东西确实等于当时录到的东西 |
| **指纹变化必须 miss** | 请求形状变了就不能命中（否则夹具在撒谎，而谎报的正是评测要抓的那类变化） |
| **回放能驱动整条循环** | 这是台架存在的目的；只测 `ReplayLLM` 单件等于没测到它的用途 |

★ 第三条是本文件的重点：`RecordingClient` / `ReplayLLM` 各自"能用"不代表
**同一条对话录完能原样跑第二遍**（工具 schema、system 提示词、消息装配都可能对不上）。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel

from workbench import approvals, cache, db
from workbench import plugin_api as pa
from workbench.harness import record
from workbench.harness.loop import run_agent_loop
from workbench.harness.types import AgentConfig


@pytest.fixture()
def ready():
    db.connect()
    return db


class ScriptedClient:
    """一个最小的"真客户端"替身：按脚本返回，接口与 `DeepSeekClient` 一致。"""

    def __init__(self, script: list[dict], *, model: str = "deepseek-flash",
                 summary: str = "摘要") -> None:
        self._script = list(script)
        self._summary = summary
        self.model = model
        self.last_usage: dict = {}
        self.calls: list[list[dict]] = []

    async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
        self.calls.append([dict(m) for m in messages])
        step = self._script.pop(0) if self._script else {"content": "（脚本耗尽）",
                                                        "tool_calls": []}
        for char in step.get("content", ""):
            yield {"type": "delta", "text": char}
        yield {
            "type": "end",
            "content": step.get("content", ""),
            "tool_calls": step.get("tool_calls", []),
            "usage": {"prompt_tokens": 100, "prompt_cache_hit_tokens": 80,
                      "prompt_cache_miss_tokens": 20, "completion_tokens": 5},
            "finish_reason": step.get("finish_reason", "stop"),
        }

    async def chat(self, messages, temperature=0.7, max_tokens=None, json_mode=False):
        self.last_usage = {"prompt_tokens": 7}
        return self._summary


class BoomClient(ScriptedClient):
    """第一次调用就抛 —— 用来验"录制时失败，回放时也要按失败复现"。

    ⚠️ 它**必须是异步生成器**（靠那个永远不会执行的 `yield` 做到），不能只写 `raise`：
    只写 `raise` 的 `async def` 是**协程函数**，`async for` 拿到的是协程对象 ——
    报 `RuntimeWarning: coroutine … was never awaited`。那是**假客户端没遵守契约**
    （真客户端 `stream_chat` 里有 `yield`），不是被测代码的问题。
    """

    async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
        if False:  # pragma: no cover —— 只为让本函数成为异步生成器
            yield {}
        raise RuntimeError("上游炸了")


def _drain(gen) -> list[dict]:
    async def run() -> list[dict]:
        return [event async for event in gen]
    return asyncio.run(run())


def _requests(emitted: list[dict]) -> list[dict]:
    return [e for e in emitted if e["type"] != "delta"]


# ---------------------------------------------------------------- 逐字段相等


def test_record_then_replay_matches_field_by_field() -> None:
    """★ 录一次、回放一次、**逐字段**相等（计划 §0.2 的判据原话）。"""
    inner = ScriptedClient([{"content": "你好", "tool_calls": []}])
    recorder = record.RecordingClient(inner, name="t")
    messages = [{"role": "user", "content": "在吗"}]

    recorded = _drain(recorder.stream_chat(messages))
    recorder.fixture["model"] = recorder.model
    replay = record.ReplayLLM(recorder.fixture)
    replayed = _drain(replay.stream_chat(messages))

    assert _requests(recorded) == _requests(replayed), "回放出来的事件与录到的不相等"
    assert "".join(e["text"] for e in recorded if e["type"] == "delta") == "你好"
    assert replay.interaction_count == 1


def test_replay_is_deep_copied() -> None:
    """回放返回的 `tool_calls` 必须是**拷贝**：调用方可能就地改它。

    共用同一份对象会让"第一次回放"与"第二次回放"结果不同 —— 而回放的全部意义
    就是两次跑出同一个结果。
    """
    inner = ScriptedClient([{"content": "", "tool_calls": [
        {"id": "c1", "name": "echo_text", "arguments": "{}"}]}])
    recorder = record.RecordingClient(inner, name="t2")
    messages = [{"role": "user", "content": "跑"}]
    _drain(recorder.stream_chat(messages))

    replay = record.ReplayLLM(recorder.fixture)
    first = _drain(replay.stream_chat(messages))
    first_end = next(e for e in first if e["type"] == "end")
    first_end["tool_calls"][0]["arguments"] = "被就地改过了"

    second = _drain(replay.stream_chat(messages))
    second_end = next(e for e in second if e["type"] == "end")
    assert second_end["tool_calls"][0]["arguments"] == "{}", "回放返回了共享对象"


# ---------------------------------------------------------------- 指纹必须会变


def test_changing_the_tool_schema_invalidates_the_fixture() -> None:
    """★ **工具 schema 必须进指纹**（计划点名的那条）。

    不进的后果不是"回放失败"，是**回放成功**：改了工具描述、模型看到的东西变了，
    而夹具照旧给出上一次的答案，评测于是把"行为已经变了"读成"没变"。
    """
    inner = ScriptedClient([{"content": "好"}])
    recorder = record.RecordingClient(inner, name="t3")
    messages = [{"role": "user", "content": "在吗"}]
    tools = [{"type": "function", "function": {"name": "echo_text", "description": "回显"}}]
    _drain(recorder.stream_chat(messages, tools=tools))

    replay = record.ReplayLLM(recorder.fixture)
    changed = [{"type": "function", "function": {"name": "echo_text", "description": "回显（改过）"}}]
    with pytest.raises(record.ReplayMiss):
        _drain(replay.stream_chat(messages, tools=changed))


@pytest.mark.parametrize("changed", [
    {"temperature": 0.1},
    {"max_tokens": 512},
])
def test_changing_generation_params_invalidates_the_fixture(changed: dict) -> None:
    """`temperature` / `max_tokens` 都改了请求体（后者还决定会不会被截断）⇒ 必须 miss。"""
    inner = ScriptedClient([{"content": "好"}])
    recorder = record.RecordingClient(inner, name="t4")
    messages = [{"role": "user", "content": "在吗"}]
    _drain(recorder.stream_chat(messages, temperature=0.7))

    replay = record.ReplayLLM(recorder.fixture)
    with pytest.raises(record.ReplayMiss):
        _drain(replay.stream_chat(messages, **changed))


def test_changing_the_messages_invalidates_the_fixture() -> None:
    inner = ScriptedClient([{"content": "好"}])
    recorder = record.RecordingClient(inner, name="t5")
    _drain(recorder.stream_chat([{"role": "user", "content": "在吗"}]))

    replay = record.ReplayLLM(recorder.fixture)
    with pytest.raises(record.ReplayMiss):
        _drain(replay.stream_chat([{"role": "user", "content": "在吗？"}]))


def test_model_aliases_share_one_fingerprint() -> None:
    """别名与正名打的是**同一个模型、同一个请求体** ⇒ 指纹就该相同。

    否则换个写法就要重录一遍夹具，纯属自找麻烦 —— 而 `normalize_model` 存在的
    全部理由就是把这类写法归一。
    """
    base = record.fingerprint([{"role": "user", "content": "x"}], model="deepseek-flash")
    alias = record.fingerprint([{"role": "user", "content": "x"}], model="deepseek-v4-flash")
    assert base == alias


def test_a_replay_miss_lists_the_available_keys_with_labels() -> None:
    """miss 必须**报出可用键**（计划 §0.2 的原话）—— 夹具覆盖度由此可证。"""
    inner = ScriptedClient([{"content": "好"}])
    recorder = record.RecordingClient(inner, name="t6")
    _drain(recorder.stream_chat([{"role": "user", "content": "第一条"}]))

    replay = record.ReplayLLM(recorder.fixture)
    with pytest.raises(record.ReplayMiss) as exc:
        _drain(replay.stream_chat([{"role": "user", "content": "另一条"}]))

    text = str(exc.value)
    known = list(recorder.fixture["interactions"][0]["key"])
    assert recorder.fixture["interactions"][0]["key"][:12] in text, "没列出可用键"
    assert "第一条" in text, "没列标签 —— 只有哈希的话人没法判断该补哪一条"
    assert len(known) == 64, "指纹应当是 sha256 十六进制"


# ---------------------------------------------------------------- chat 与失败


def test_chat_is_recorded_and_replayed() -> None:
    """摘要那次调用走的是 `chat`（非流式），它也得能录/回放 —— 否则长会话录不全。"""
    inner = ScriptedClient([], summary="这是一段摘要")
    recorder = record.RecordingClient(inner, name="t7")
    messages = [{"role": "user", "content": "总结一下"}]

    assert asyncio.run(recorder.chat(messages)) == "这是一段摘要"
    replay = record.ReplayLLM(recorder.fixture)
    assert asyncio.run(replay.chat(messages)) == "这是一段摘要"
    assert replay.last_usage == {"prompt_tokens": 7}, "chat 的用量没回放出来"


def test_a_recorded_failure_replays_as_a_recorded_error() -> None:
    """★ 录制时失败，回放时**按失败复现**，且与 `ReplayMiss` **分开**。

    两者的处置完全不同：`ReplayMiss` 是"夹具里没有这条"（去补录），
    `ReplayRecordedError` 是"夹具里有，而当时它就是失败的"（接受这个事实）。
    混成一个类型会让人去补一条**本来就在**的夹具。
    """
    recorder = record.RecordingClient(BoomClient([]), name="t8")
    messages = [{"role": "user", "content": "在吗"}]

    with pytest.raises(RuntimeError, match="上游炸了"):
        _drain(recorder.stream_chat(messages))
    assert recorder.fixture["interactions"][0]["event"]["ok"] is False

    replay = record.ReplayLLM(recorder.fixture)
    with pytest.raises(record.ReplayRecordedError):
        _drain(replay.stream_chat(messages))


def test_non_strict_replay_returns_nothing_instead_of_raising() -> None:
    """非严格模式：miss 记在 `misses` 里并**安静返回**，供"先跑一遍看看缺哪些"用。"""
    replay = record.ReplayLLM(record.make_fixture("empty"), strict=False)
    assert _drain(replay.stream_chat([{"role": "user", "content": "x"}])) == []
    assert len(replay.misses) == 1, "miss 没有被记下来"


# ---------------------------------------------------------------- 落盘往返


def test_a_fixture_survives_a_round_trip_through_disk(tmp_path) -> None:
    """夹具是**要提交的资产**：写盘再读回来必须还能用（否则它只是一次性的内存对象）。"""
    recorder = record.RecordingClient(ScriptedClient([{"content": "好"}]), name="t9")
    messages = [{"role": "user", "content": "在吗"}]
    _drain(recorder.stream_chat(messages))

    path = tmp_path / "llm.json"
    record.save_fixture(recorder.fixture, path)
    loaded = record.load_fixture(path)

    replay = record.ReplayLLM(loaded)
    events = _drain(replay.stream_chat(messages))
    end = next(e for e in events if e["type"] == "end")
    assert end["content"] == "好"


def test_a_fixture_with_the_wrong_format_version_is_refused(tmp_path) -> None:
    """格式版本不匹配要**明确拒绝**，不要"尽力而为"。

    尽力而为的后果是：夹具的字段语义变了，旧文件被读成"缺字段 ⇒ 空值"，
    而空值在回放里表现为"模型什么都没说"—— 那看起来像一次**正常但很短的回答**。
    """
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"formatVersion": 999, "interactions": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="格式版本不匹配"):
        record.load_fixture(path)


# ---------------------------------------------------------------- 真正的用途：驱动整条循环


class EchoArgs(BaseModel):
    text: str = pa.Arg("内容", default="")


@pytest.fixture()
def hub(ready, monkeypatch):
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


def test_a_whole_agent_turn_can_be_replayed_without_the_model(hub, tmp_path) -> None:
    """★★ **这条才是台架存在的理由**：录一遍真实的 Agent 轮次，然后**不联网**跑第二遍。

    只测 `ReplayLLM` 单件是不够的 —— 各自"能用"不代表**同一条对话录完能原样跑第二遍**：
    工具 schema、system 提示词、消息装配任何一处对不上，指纹就不一样、回放就 miss。
    这条用例把这三件事一起钉住。
    """
    tool_hub, _executed = hub
    conv_id = "record-e2e"
    cache.clear_state(conv_id)
    config = AgentConfig(system_prompt="系统提示词", max_steps=4, approval_wait_s=0.05)
    messages = [{"role": "user", "content": "开始"}]
    script = [
        {"content": "", "tool_calls": [{"id": "c1", "name": "echo_text",
                                        "arguments": json.dumps({"text": "hi"})}]},
        {"content": "做完了"},
    ]

    # ---- 第一遍：录制 ----
    recorder = record.RecordingClient(ScriptedClient(list(script)), name="e2e")
    first = _drain(run_agent_loop(messages, recorder, tool_hub, config,
                                  conversation_id=conv_id))
    assert [e["type"] for e in first].count("final") == 1, "第一遍没跑完"
    assert recorder.fixture["interactions"], "什么都没录到"
    assert len(recorder.fixture["interactions"]) == 2, (
        f"两轮模型调用应当录两条，实际 {len(recorder.fixture['interactions'])}"
    )

    # ---- 第二遍：回放（没有真客户端，没有网络）----
    record.save_fixture(recorder.fixture, tmp_path / "e2e.json")
    replay = record.ReplayLLM(record.load_fixture(tmp_path / "e2e.json"))
    cache.clear_state(conv_id)
    second = _drain(run_agent_loop(messages, replay, tool_hub, config,
                                   conversation_id=conv_id))

    assert replay.misses == [], f"回放漏了请求：{replay.misses}"
    assert [e["type"] for e in second] == [e["type"] for e in first], (
        "回放的事件序列与录制时不同"
    )
    first_final = next(e for e in first if e["type"] == "final")
    second_final = next(e for e in second if e["type"] == "final")
    assert first_final["content"] == second_final["content"] == "做完了"
    usage = [e for e in second if e["type"] == "usage"]
    assert usage and usage[0]["usage"]["cacheHitTokens"] == 160, (
        f"回放没有还原用量（两次模型调用各 80 命中）：{usage}"
    )
