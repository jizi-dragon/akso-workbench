"""Agent 循环验收：工具调用 / 确认门 / 观测事件 / 审计入库 / 安全门。

用假 LLM 客户端驱动循环——循环本身不碰网络，事件序列就是它的全部契约，
因此这里的断言直接对事件流做（与前端 SSE 消费的是同一串事件）。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel

from workbench import approvals, db
from workbench import plugin_api as pa
from workbench.harness.loop import run_agent_loop
from workbench.harness.prompts import build_system_prompt
from workbench.harness.types import AgentConfig

# ---------------------------------------------------------------- 假客户端


class FakeClient:
    """按脚本逐轮返回的假 LLM：第 N 次调用消费第 N 条脚本。

    脚本项形如：
      {"content": "正文", "tool_calls": [{"id","name","arguments"}]}
    """

    def __init__(self, script: list[dict], usage: dict | None = None) -> None:
        self.script = list(script)
        self.calls: list[list[dict]] = []
        self.tools_seen: list[list[dict]] = []
        self.model = "fake-model"
        self.usage = usage or {}
        self.last_usage: dict = {}

    async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
        self.calls.append(list(messages))
        self.tools_seen.append(list(tools or []))
        step = self.script.pop(0) if self.script else {"content": "（脚本耗尽）"}
        for chunk in _chunks(step.get("content") or ""):
            yield {"type": "delta", "text": chunk}
        yield {
            "type": "end",
            "content": step.get("content") or "",
            "tool_calls": step.get("tool_calls") or [],
            "usage": step.get("usage", self.usage),
            # 真实客户端总会带它（`deepseek.py` 收 `finish_reason`）；
            # 脚本默认 "stop"，要模拟截断就写 `"finish_reason": "length"`
            "finish_reason": step.get("finish_reason", "stop"),
        }

    async def chat(self, messages, temperature=0.7, max_tokens=None, json_mode=False):
        # 摘要调用：用量经 last_usage 回传（与真实客户端同契约）
        self.last_usage = {"prompt_tokens": 900, "prompt_cache_hit_tokens": 0,
                           "prompt_cache_miss_tokens": 900, "completion_tokens": 60}
        return "（摘要）"


def _chunks(text: str, size: int = 8) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)]


async def collect(gen) -> list[dict]:
    return [event async for event in gen]


def drive(script: list[dict], hub: pa.ToolHub, config: AgentConfig | None = None,
          plugins: list[str] | None = None, message: str = "帮我看看"):
    client = FakeClient(script)
    events = asyncio.run(collect(run_agent_loop(
        [{"role": "user", "content": message}], client, hub,
        config or AgentConfig(system_prompt="你是测试助手"),
        conversation_id="conv-test", plugins=plugins,
    )))
    return client, events


def call(name: str, arguments: dict, call_id: str = "call_1") -> dict:
    return {"id": call_id, "name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}


def types_of(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


def skeleton(events: list[dict]) -> list[str]:
    """去掉 token 增量与收尾 usage 后的骨架。

    token 的**条数**取决于假客户端的分块大小，不该成为断言对象（正文由 final 断言）；
    `usage` 每次回答必发一条（缓存度量），其内容由 test_cache_* 单独断言。
    """
    return [e["type"] for e in events if e["type"] not in ("token", "usage")]


def final_of(events: list[dict]) -> dict:
    return next(e for e in events if e["type"] == "final")


def error_of(events: list[dict]) -> dict:
    return next(e for e in events if e["type"] == "error")


# ---------------------------------------------------------------- 夹具


class ProbeArgs(BaseModel):
    text: str = pa.Arg("内容")


async def probe(args: ProbeArgs, ctx: pa.ToolContext) -> str:
    return json.dumps({"ok": True, "echo": args.text, "source": ctx.source})


async def probe_write(args: ProbeArgs, ctx: pa.ToolContext) -> str:
    return json.dumps({"ok": True, "wrote": args.text})


@pytest.fixture()
def hub(monkeypatch):
    db.connect()
    approvals.reset_store()
    fresh = pa.ToolHub()
    approvals.install(fresh)
    fresh.add_plugin(pa.Plugin(id="probe", title="探针", tools=[
        pa.ToolSpec(name="probe_echo", description="回显", args_model=ProbeArgs,
                    handler=probe, risk=pa.Risk.READ, timeout_s=5),
        pa.ToolSpec(name="probe_write", description="写动作", args_model=ProbeArgs,
                    handler=probe_write, risk=pa.Risk.DANGEROUS, timeout_s=5),
    ]))
    monkeypatch.setattr(pa, "_hub", fresh)
    return fresh


# ---------------------------------------------------------------- 基本流


def test_plain_answer_has_no_tool_events(hub) -> None:
    _, events = drive([{"content": "你好"}], hub)
    assert skeleton(events) == ["final"]
    assert final_of(events)["content"] == "你好"


def test_system_prompt_is_injected(hub) -> None:
    client, _ = drive([{"content": "x"}], hub)
    assert client.calls[0][0]["role"] == "system"
    assert client.calls[0][0]["content"] == "你是测试助手"


def test_tools_are_advertised_to_the_model(hub) -> None:
    """工具必须真的进 function calling schema——这是"中枢能干活"的判据。"""
    client, _ = drive([{"content": "x"}], hub)
    names = {t["function"]["name"] for t in client.tools_seen[0]}
    assert {"probe_echo", "probe_write"} <= names


def test_plugin_whitelist_narrows_the_tool_surface(hub) -> None:
    client, _ = drive([{"content": "x"}], hub, plugins=["probe"])
    assert client.tools_seen[0]
    client2, _ = drive([{"content": "x"}], hub, plugins=["nonexistent"])
    assert client2.tools_seen[0] == []


# ---------------------------------------------------------------- 工具链


def test_tool_call_produces_action_and_observation(hub) -> None:
    _, events = drive([
        {"content": "我来查", "tool_calls": [call("probe_echo", {"text": "hi"})]},
        {"content": "查到了"},
    ], hub)

    assert skeleton(events) == ["thought", "action", "observation", "final"]
    assert types_of(events)[0] == "token"          # 工具调用前的正文是流式吐出来的
    assert final_of(events)["content"] == "查到了"
    action = next(e for e in events if e["type"] == "action")
    assert action["call"]["name"] == "probe_echo"
    assert action["tool"] == {"name": "probe_echo", "risk": "read", "plugin": "probe",
                              "known": True}
    assert action["auditId"]

    observation = next(e for e in events if e["type"] == "observation")
    assert observation["result"]["ok"] is True
    assert observation["invocation"]["tool"] == "probe_echo"
    assert observation["invocation"]["plugin"] == "probe"
    assert observation["invocation"]["risk"] == "read"
    assert observation["invocation"]["auditId"] == action["auditId"]


def test_tool_result_is_fed_back_to_the_model(hub) -> None:
    """工具结果必须作为 role=tool 消息回填，否则模型看不到观测。"""
    client, _ = drive([
        {"tool_calls": [call("probe_echo", {"text": "abc"})]},
        {"content": "done"},
    ], hub)
    second = client.calls[1]
    tool_msgs = [m for m in second if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert "abc" in tool_msgs[0]["content"]
    assistant = [m for m in second if m.get("role") == "assistant"][-1]
    assert assistant["tool_calls"][0]["name"] == "probe_echo"


def test_unknown_tool_is_recoverable(hub) -> None:
    _, events = drive([
        {"tool_calls": [call("no_such_tool", {})]},
        {"content": "抱歉，我换个方式"},
    ], hub)
    observation = next(e for e in events if e["type"] == "observation")
    assert observation["result"]["ok"] is False
    assert observation["invocation"]["isError"] is True
    assert skeleton(events)[-1] == "final"


def test_tool_audit_is_written_from_the_loop(hub) -> None:
    """对话链路的工具调用必须入审计（改造前 harness 完全不写这张表）。"""
    _, events = drive([{"tool_calls": [call("probe_echo", {"text": "audited"})]},
                       {"content": "ok"}], hub)
    audit_id = next(e for e in events if e["type"] == "action")["auditId"]
    row = db.query_one("SELECT * FROM agent_audit WHERE id = ?", (audit_id,))
    assert row is not None
    assert row["tool"] == "probe_echo"
    assert row["plugin"] == "probe"


# ---------------------------------------------------------------- 确认门


def test_dangerous_call_pauses_and_never_executes(hub) -> None:
    """核心安全属性：对话链路里危险工具被拦下，且**没有真的执行**。"""
    _, events = drive([
        {"content": "我来建", "tool_calls": [call("probe_write", {"text": "对象A"})]},
        {"content": "已暂停，等你确认"},
    ], hub)

    assert "approval_required" in types_of(events)
    gate = next(e for e in events if e["type"] == "approval_required")
    assert gate["tool"] == "probe_write"
    assert gate["risk"] == "dangerous"
    assert gate["pendingId"]
    assert "写动作" in gate["summary"]

    observation = next(e for e in events if e["type"] == "observation")
    assert observation["invocation"]["status"] == "needs_approval"
    assert observation["result"]["ok"] is False

    # 审计里应留下一行 needs_approval，且状态可查
    pending = approvals.pending_store().list("pending")
    assert [p["id"] for p in pending] == [gate["pendingId"]]
    assert pending[0]["tool"] == "probe_write"


def test_approved_token_lets_the_same_call_through(hub) -> None:
    """批准后模型重试**同一次调用**（原样带令牌）→ 真正执行。

    这条模拟界面的完整回路：拦下 → 人点放行 → 重投。
    """
    first = asyncio.run(hub.execute("probe_write", {"text": "对象A"}))
    assert first.status is pa.ToolStatus.NEEDS_APPROVAL
    token = approvals.pending_store().approve(first.pending_id).token

    second = asyncio.run(hub.execute(
        "probe_write", {"text": "对象A"}, pa.ToolContext(approval_token=token, source="agent"),
    ))
    assert second.status is pa.ToolStatus.OK
    assert json.loads(second.content)["wrote"] == "对象A"


def test_exhausted_budget_terminates(hub) -> None:
    """步数预算：模型无限调工具时必须被切断，而不是转圈烧 token。

    **并且它必须是"可继续的终态"而不是"一个错误"**（2026-09-20 修订）：
    改造前只有一句"已达到步数预算上限，已终止本轮"——用户看不懂预算是多少、
    为什么不够、接下来怎么办；前端也把它渲染成红色报错。现在它带 `reason`，
    文案里必须出现"继续"与"设置"这两条出路（见 docs/FACTORY-AGENT-GAP.md 问题①）。
    """
    script = [{"tool_calls": [call("probe_echo", {"text": str(i)}, f"c{i}")]} for i in range(20)]
    _, events = drive(script, hub, AgentConfig(max_steps=3, system_prompt="s"))
    event = error_of(events)
    assert event["reason"] == "budget_exhausted", f"前端靠 reason 区分「到头了」与「出错了」：{event}"
    assert "继续" in event["message"], "文案必须告诉用户怎么接着做"
    assert "设置" in event["message"], "反复撞上限的用户要知道去哪儿调大"
    assert types_of(events)[-1] == "usage", "预算耗尽也要把用量发出来（前端要显示）"


def test_truncated_output_is_not_reported_as_bad_args(hub) -> None:
    """输出被长度上限截断 → **显式说出来**，而不是让半截参数流到工具层。

    改造前：`finish_reason` 在 `DeepSeekClient.stream_chat` 里被丢弃，被截断的
    tool_call 带着半截 JSON 进 `_validate` → 观测是「参数解析失败」——
    模型据此完全推不出真正的原因，最可能的反应是原样重试，而重试大概率再截断一次
    （白烧步数，见 docs/FACTORY-AGENT-GAP.md 问题① 与 PITFALLS #58）。
    """
    _, events = drive(
        [{"content": "", "finish_reason": "length",
          "tool_calls": [{"id": "c1", "name": "probe_echo", "arguments": '{"text": "半截'}]}],
        hub, AgentConfig(max_steps=5, system_prompt="s"),
    )
    kinds = types_of(events)
    assert "observation" not in kinds, f"截断时不该执行任何工具：{kinds}"
    assert "action" not in kinds, "连 action 都不该发——那会让界面显示一个正在跑的工具卡"
    event = error_of(events)
    assert event["reason"] == "output_truncated"
    assert "截断" in event["message"]
    assert "没有" in event["message"], (
        "要说清「没有执行工具」，否则用户会以为已经动过平台了——"
        "而半截参数执行出去比不执行危险得多")


def test_truncated_final_answer_is_also_flagged(hub) -> None:
    """纯文字回答被截断同样要拦：半截回答读起来像"说完了"，那比报错更坏。"""
    _, events = drive([{"content": "结论如下：第一点……", "finish_reason": "length"}],
                      hub, AgentConfig(max_steps=5, system_prompt="s"))
    assert "final" not in types_of(events)
    assert error_of(events)["reason"] == "output_truncated"


def test_repeated_identical_call_is_cut_off(hub) -> None:
    """重复调用检测：同工具同参数第三次即终止（防死循环）。"""
    same = [{"tool_calls": [call("probe_echo", {"text": "same"}, f"c{i}")]} for i in range(6)]
    _, events = drive(same, hub, AgentConfig(max_steps=12, system_prompt="s"))
    assert "重复的工具调用" in error_of(events)["message"]


# ---------------------------------------------------------------- 系统提示词


def test_default_prompt_is_minimal(hub) -> None:
    """提示词只回答三件事：你是谁、工具从哪找、信息从哪来（2026-09-15 简化）。

    刻意**不再**罗列工具名与回答格式：
    - 工具清单随请求下发（每个工具自带 description），写两份必然漂移；
    - 回答格式是用户偏好，该由设置页决定（见 test_prompt_is_user_editable）。
    """
    prompt = build_system_prompt()
    assert len(prompt) < 1200, "提示词又膨胀了——它占每次请求最前面的字节，越短越省"
    for section in ("# 工具", "# 信息"):
        assert section in prompt
    # 不再点名具体工具：那是 plugins/ 里 description 的职责
    for leaked in ("insight_run", "factory_create", "accounts_login",
                   "request_user_confirmation"):
        assert leaked not in prompt, f"提示词里又硬编码工具名了：{leaked}"
    assert "ISPE" not in prompt and "阿克索" not in prompt, "身份设定已按用户要求移除"


def test_prompt_points_at_tools_and_sources() -> None:
    """"工具从哪找"与"信息从哪来"必须说清，否则简化就变成了信息缺失。"""
    prompt = build_system_prompt()
    assert "tools" in prompt          # 工具从请求里下发
    assert "description" in prompt    # 每个工具自带说明
    assert "工具返回的结果" in prompt  # 信息可信度排序
    assert "不要猜" in prompt


def test_prompt_is_user_editable(hub, monkeypatch) -> None:
    """设置页改的提示词立即生效；清空则回落到基线。"""
    from workbench.harness import prompts

    monkeypatch.setattr(prompts, "user_prompt", lambda: "")
    assert prompts.prompt_state()["custom"] is False
    assert build_system_prompt() == prompts.DEFAULT_SYSTEM_PROMPT

    monkeypatch.setattr(prompts, "user_prompt", lambda: "只回一个字")
    assert prompts.prompt_state()["custom"] is True
    assert build_system_prompt() == "只回一个字"


# ---------------------------------------------------------------- 缓存度量


def test_usage_event_reports_cache_hit_and_miss(hub) -> None:
    """每次回答收尾发一条 usage，含**缓存命中量**（改造前这些字段被整个丢掉）。"""
    client = FakeClient([{"content": "答"}], usage={
        "prompt_tokens": 1000, "prompt_cache_hit_tokens": 800,
        "prompt_cache_miss_tokens": 200, "completion_tokens": 50,
    })
    events = asyncio.run(collect(run_agent_loop(
        [{"role": "user", "content": "问"}], client, hub,
        AgentConfig(system_prompt="s"), conversation_id="conv-usage",
    )))
    usage = next(e for e in events if e["type"] == "usage")["usage"]
    assert usage["cacheHitTokens"] == 800
    assert usage["cacheMissTokens"] == 200
    assert usage["hitRate"] == 0.8
    assert usage["calls"] == 1


def test_usage_accumulates_across_tool_turns(hub) -> None:
    """工具轮次里会有多次 LLM 调用，用量必须累计——只记最后一次会高估命中率。"""
    step_usage = {"prompt_tokens": 100, "prompt_cache_hit_tokens": 100,
                  "prompt_cache_miss_tokens": 0, "completion_tokens": 10}
    client = FakeClient([
        {"tool_calls": [call("probe_echo", {"text": "x"})], "usage": step_usage},
        {"content": "答", "usage": step_usage},
    ])
    events = asyncio.run(collect(run_agent_loop(
        [{"role": "user", "content": "问"}], client, hub,
        AgentConfig(system_prompt="s"), conversation_id="conv-multi",
    )))
    usage = next(e for e in events if e["type"] == "usage")["usage"]
    assert usage["calls"] == 2
    assert usage["promptTokens"] == 200


def test_usage_is_persisted(hub) -> None:
    """用量要落库，否则设置页的命中率永远是 0。"""
    client = FakeClient([{"content": "答"}], usage={
        "prompt_tokens": 500, "prompt_cache_hit_tokens": 400,
        "prompt_cache_miss_tokens": 100, "completion_tokens": 20,
    })
    asyncio.run(collect(run_agent_loop(
        [{"role": "user", "content": "问"}], client, hub,
        AgentConfig(system_prompt="s"), conversation_id="conv-persist",
    )))
    row = db.query_one(
        "SELECT * FROM deepseek_usage WHERE conversation_id = ? ORDER BY created_at DESC LIMIT 1",
        ("conv-persist",),
    )
    assert row is not None
    assert row["cache_hit_tokens"] == 400
    assert row["cache_miss_tokens"] == 100
