r"""Agent 运行记录：`agent_run` + `agent_span`（计划 §6.1）。

## 这个模块修的是什么

此前"一次用户回合"在数据里**不是一个实体**：`agent_audit` 记单次工具调用、
`deepseek_usage` 记一次回答的用量，两者只能靠**时间戳猜**关联。于是
"这一回合失败了"没有载体、"这次为什么慢"只能翻日志、跨步因果表达不了。

## 本文件要钉住的三件事

1. **span 数与 kind 齐全**（计划 §6.1 的判据原话）：一次带工具的对话里，
   每一次模型调用与每一次工具调用都各有一条；
2. **run 的状态/步数/用量正确**，且**失败回合也留下记录**
   （这是 §5.4a 那条"任何出口都入账"在轨迹口径上的孪生）；
3. ★ **`attrs` 里不许出现工具参数** —— 轨迹是给人看"哪一步慢"的，
   而参数与账号的事实源是 `agent_audit`（铁律 28）。复制一份 = 第二个会漂的事实源。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel

from workbench import approvals, cache, db
from workbench import hub as hub_mod
from workbench import plugin_api as pa
from workbench.harness import trace
from workbench.harness.loop import run_agent_loop
from workbench.harness.types import AgentConfig


@pytest.fixture()
def ready():
    db.connect()
    return db


class ScriptedClient:
    def __init__(self, script: list[dict], *, model: str = "deepseek-flash") -> None:
        self._script = list(script)
        self.model = model
        self.last_usage: dict = {}

    async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
        step = self._script.pop(0) if self._script else {"content": "（耗尽）",
                                                        "tool_calls": []}
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
        return "摘要"


class BoomClient(ScriptedClient):
    async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
        if False:  # pragma: no cover —— 让它成为异步生成器（见 test_harness_record 的说明）
            yield {}
        raise RuntimeError("上游炸了")


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
    monkeypatch.setattr(hub_mod, "_hub", tool_hub)
    return tool_hub, executed


def _calls(n: int) -> list[dict]:
    return [{"id": f"c{i}", "name": "echo_text",
             "arguments": json.dumps({"text": f"n{i}"})} for i in range(n)]


def _drive(hub_obj, conv_id: str, script: list[dict], config: AgentConfig,
           client=None, *, tolerate_error: bool = True) -> list[dict]:
    """跑一次循环并收事件。

    `tolerate_error`：有些用例**刻意**让模型炸（`BoomClient`），而 `run_agent_loop`
    此时会让异常**穿透**（把异常转成 `error` 事件是 `routes_chat` 那一层的职责）。
    这里吞掉它只是为了拿到"异常之前已经发出的事件" —— 而 run 的终态由
    `_accounted` 的 `except` 落库，与事件无关。
    """
    cache.clear_state(conv_id)
    client = client or ScriptedClient(script)

    async def run() -> list[dict]:
        out: list[dict] = []
        try:
            async for event in run_agent_loop(
                [{"role": "user", "content": "开始"}], client, hub_obj, config,
                conversation_id=conv_id,
            ):
                out.append(event)
        except Exception:  # noqa: BLE001 —— 见 docstring
            if not tolerate_error:
                raise
        return out

    return asyncio.run(run())


def _runs(conv_id: str) -> list[dict]:
    return [r for r in trace.recent_runs(50) if r["conversationId"] == conv_id]


# ---------------------------------------------------------------- span 数与 kind


def test_a_tool_turn_produces_model_and_tool_spans(hub) -> None:
    """★ 计划 §6.1 的判据原话：一次带工具的对话 → **span 数与 kind 齐全**。

    两轮模型调用（第一轮发工具调用、第二轮收尾）+ 一个工具调用 ⇒
    2 条 `model` + 1 条 `tool`。
    """
    tool_hub, _executed = hub
    conv_id = "span-basic"
    _drive(tool_hub, conv_id, [
        {"content": "", "tool_calls": _calls(1)},
        {"content": "做完了"},
    ], AgentConfig(system_prompt="sys", max_steps=4, approval_wait_s=0.05))

    runs = _runs(conv_id)
    assert len(runs) == 1, f"应当恰好一次运行：{runs}"
    spans = trace.run_spans(runs[0]["id"])
    kinds = [s["spanKind"] for s in spans]

    assert kinds == [trace.SPAN_MODEL, trace.SPAN_TOOL, trace.SPAN_MODEL], (
        f"span 序列不对：{[(s['step'], s['spanKind'], s['name']) for s in spans]}"
    )


def test_spans_are_numbered_from_one_and_ascending(hub) -> None:
    """`step` 从 1 开始、在 run 内单调递增 —— 时间线按它排序。

    它比时间戳可靠：同一毫秒内的两步在时间戳上分不出先后。
    """
    tool_hub, _ = hub
    conv_id = "span-steps"
    _drive(tool_hub, conv_id, [
        {"content": "", "tool_calls": _calls(2)},
        {"content": "好"},
    ], AgentConfig(system_prompt="sys", max_steps=6, approval_wait_s=0.05))

    spans = trace.run_spans(_runs(conv_id)[0]["id"])
    assert [s["step"] for s in spans] == [1, 2, 3, 4], f"step 不是 1..N：{spans}"
    assert [s["spanKind"] for s in spans] == [
        trace.SPAN_MODEL, trace.SPAN_TOOL, trace.SPAN_TOOL, trace.SPAN_MODEL]


def test_the_tool_span_names_the_tool_and_points_at_the_audit(hub) -> None:
    """工具 span 的 `name` 是工具名，`attrs` 里带 `auditId` **指针**。"""
    tool_hub, _ = hub
    conv_id = "span-tool-name"
    _drive(tool_hub, conv_id, [
        {"content": "", "tool_calls": _calls(1)},
        {"content": "好"},
    ], AgentConfig(system_prompt="sys", max_steps=4, approval_wait_s=0.05))

    tool_span = next(s for s in trace.run_spans(_runs(conv_id)[0]["id"])
                     if s["spanKind"] == trace.SPAN_TOOL)
    assert tool_span["name"] == "echo_text"
    assert tool_span["status"] == "ok"
    assert tool_span["attrs"]["auditId"], "没带 auditId —— 轨迹与审计就断开了"
    assert tool_span["attrs"]["plugin"] == "echo"
    assert tool_span["attrs"]["risk"] == "read"


def test_tool_span_does_not_copy_the_arguments(hub) -> None:
    """★ **`attrs` 里不许出现工具参数**（铁律 28：两处都写全量 = 第二个会漂的事实源）。

    参数与账号的事实源是 `agent_audit`；轨迹只该带**指针**。
    这条断言的价值在于它是一个**会随实现漂移**的约定：谁"顺手"把 `arguments`
    塞进 attrs 图个方便，这里就会红。
    """
    tool_hub, _ = hub
    conv_id = "span-no-args"
    _drive(tool_hub, conv_id, [
        {"content": "", "tool_calls": [{"id": "c1", "name": "echo_text",
                                        "arguments": json.dumps({"text": "机密内容"})}]},
        {"content": "好"},
    ], AgentConfig(system_prompt="sys", max_steps=4, approval_wait_s=0.05))

    blob = json.dumps(trace.run_spans(_runs(conv_id)[0]["id"]), ensure_ascii=False)
    assert "机密内容" not in blob, "工具参数被复制进了轨迹 —— 那是 audit 的事实源"


# ---------------------------------------------------------------- run 的状态与用量


def test_run_records_status_steps_and_usage(hub) -> None:
    """run 落终态：`succeeded`、步数 = span 数、用量与 `deepseek_usage` **同源**。"""
    tool_hub, _ = hub
    conv_id = "span-run-ok"
    _drive(tool_hub, conv_id, [
        {"content": "", "tool_calls": _calls(1)},
        {"content": "做完了"},
    ], AgentConfig(system_prompt="sys", max_steps=4, approval_wait_s=0.05))

    run = _runs(conv_id)[0]
    assert run["status"] == trace.RUN_SUCCEEDED
    assert run["endedAt"], "没落 ended_at"
    assert run["steps"] == 3, f"步数应等于 span 数：{run['steps']}"
    assert run["model"] == "deepseek-flash"
    assert run["promptTokens"] == 200, f"两次模型调用各 100：{run}"
    assert run["cacheHitTokens"] == 160, f"两次各 80 命中：{run}"
    assert run["latencyMs"] >= 0


def test_a_failed_turn_is_recorded_as_failed(hub) -> None:
    """★ **失败回合也必须留下记录** —— 这是 §5.4a 在轨迹口径上的孪生。

    不修的话，"哪一回合失败了、失败在哪一步"在数据里**根本没有载体**，
    而唯一的现象是"用户说它刚才报错了"。
    """
    tool_hub, _ = hub
    conv_id = "span-run-fail"
    _drive(tool_hub, conv_id, [{"content": "x"}],
           AgentConfig(system_prompt="sys", max_steps=4, approval_wait_s=0.05),
           client=BoomClient([]))

    run = _runs(conv_id)[0]
    assert run["status"] == trace.RUN_FAILED, f"失败回合被记成了 {run['status']}"
    assert "上游炸了" in run["error"]


def test_a_failed_model_call_still_leaves_a_span(hub) -> None:
    """★ **出事的那一步必须在时间线上看得见**。

    `_SpanClient` 在异常路径上也记一条（`status="error"`）——
    只记成功步的时间线，缺的那一块恰好是最该看的那一块。
    """
    tool_hub, _ = hub
    conv_id = "span-model-fail"
    _drive(tool_hub, conv_id, [{"content": "x"}],
           AgentConfig(system_prompt="sys", max_steps=4, approval_wait_s=0.05),
           client=BoomClient([]))

    spans = trace.run_spans(_runs(conv_id)[0]["id"])
    assert len(spans) == 1, f"失败的那一步没留下：{spans}"
    assert spans[0]["spanKind"] == trace.SPAN_MODEL
    assert spans[0]["status"] == "error"
    assert "上游炸了" in spans[0]["error"]


def test_a_truncated_output_is_visible_on_the_model_span(hub) -> None:
    """被长度上限截断 ⇒ `status="truncated"`（PITFALLS #58 的可观测形态）。

    截断此前只有一个 `error` 事件；现在时间线上能直接指出"是这一步被截断的"。
    """
    tool_hub, _ = hub
    conv_id = "span-truncated"
    _drive(tool_hub, conv_id, [{"content": "半截", "finish_reason": "length"}],
           AgentConfig(system_prompt="sys", max_steps=4, approval_wait_s=0.05))

    span = trace.run_spans(_runs(conv_id)[0]["id"])[0]
    assert span["status"] == "truncated", f"截断没被标出来：{span}"
    assert span["attrs"]["finishReason"] == "length"


def test_each_run_gets_its_own_id(hub) -> None:
    """两次回合是两条 run —— 否则"上一次为什么慢"会被这一次的数字盖掉。"""
    tool_hub, _ = hub
    conv_id = "span-two-runs"
    cfg = AgentConfig(system_prompt="sys", max_steps=4, approval_wait_s=0.05)
    _drive(tool_hub, conv_id, [{"content": "第一次"}], cfg)
    _drive(tool_hub, conv_id, [{"content": "第二次"}], cfg)

    runs = _runs(conv_id)
    assert len(runs) == 2, f"应当两条 run：{runs}"
    assert runs[0]["id"] != runs[1]["id"]
    assert trace.run_spans(runs[0]["id"]), "第一条 run 没有 span"


# ---------------------------------------------------------------- 失败不影响回答


def test_trace_write_failure_is_counted_not_swallowed_silently(hub, monkeypatch) -> None:
    """轨迹写库失败**继续吞异常**（轨迹不该影响回答），但**必须留下计数**。

    没有计数时，运行时间线会**安静地缺步** —— 那比用量变低更难发现，
    因为"少了三步"看起来就像"这次它只走了三步"。
    """
    tool_hub, _ = hub
    conv_id = "span-write-fail"
    before = trace.trace_write_failures()

    real_execute = db.execute

    def broken_execute(sql, params=()):  # noqa: ANN001, ANN202
        if "agent_span" in sql or "agent_run" in sql:
            raise RuntimeError("模拟：磁盘满了")
        return real_execute(sql, params)

    monkeypatch.setattr(db, "execute", broken_execute)
    events = _drive(tool_hub, conv_id, [{"content": "好"}],
                    AgentConfig(system_prompt="sys", max_steps=4, approval_wait_s=0.05))
    monkeypatch.undo()

    # 事件流**不许**因为轨迹写失败而改变：正常收尾仍然是 final → usage。
    assert [e["type"] for e in events[-2:]] == ["final", "usage"], (
        f"轨迹写失败改变了事件流：{[e['type'] for e in events]}"
    )
    assert trace.trace_write_failures() > before, "写失败没有被计数"

    from workbench.api.routes_agent import selfcheck

    assert selfcheck()["traceWriteFailures"] >= 1, "selfcheck 没有暴露这个计数"


def test_the_recorder_is_closed_on_an_exception_exit(hub) -> None:
    """异常出口也要收 run —— `_accounted` 的 `finally` 里收（与用量同一条）。

    这条是**回归**：把 `recorder.finish(...)` 写在正常路径上，
    run 会永远停在 `running`，而"卡在 running 的那条"看起来像"还在跑"。
    """
    tool_hub, _ = hub
    conv_id = "span-close-on-error"
    _drive(tool_hub, conv_id, [{"content": "x"}],
           AgentConfig(system_prompt="sys", max_steps=4, approval_wait_s=0.05),
           client=BoomClient([]))

    run = _runs(conv_id)[0]
    assert run["status"] != trace.RUN_RUNNING, "异常出口没有收 run（永远停在 running）"
    assert run["endedAt"], "没落 ended_at"


# ---------------------------------------------------------------- OTLP 导出层


def _otlp_spans(conv_id: str) -> list[dict]:
    from tools.export_otel import to_otlp

    payload = to_otlp(50)
    spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    run_ids = {r["id"] for r in _runs(conv_id)}
    return [s for s in spans if s["traceId"] in run_ids]


def test_otel_export_produces_a_structurally_valid_payload(hub) -> None:
    """★ 计划 §6.1 的判据原话：导出**结构合法**。

    这里逐条钉的是 OTLP/JSON 的 **wire format 要求**（不是我们的偏好）：
    `traceId` 16 字节、`spanId` 8 字节、纳秒时间戳是**字符串**、
    每个属性是 `{key, value:{<类型>Value}}`。
    形状错了的后果很具体：通用工具**读不进去**，而导出这个功能的意义全在于"能被别人读"。
    """
    tool_hub, _ = hub
    conv_id = "otel-shape"
    _drive(tool_hub, conv_id, [
        {"content": "", "tool_calls": _calls(1)},
        {"content": "做完了"},
    ], AgentConfig(system_prompt="sys", max_steps=4, approval_wait_s=0.05))

    spans = _otlp_spans(conv_id)
    assert len(spans) == 4, f"1 根 + 3 步：{len(spans)}"

    roots = [s for s in spans if not s["parentSpanId"]]
    assert len(roots) == 1, "应当恰好一个根 span"
    root = roots[0]
    for span in spans:
        assert len(span["traceId"]) == 32, "traceId 必须是 16 字节（32 个十六进制字符）"
        assert len(span["spanId"]) == 16, "spanId 必须是 8 字节（16 个十六进制字符）"
        assert isinstance(span["startTimeUnixNano"], str), "纳秒时间戳在 OTLP/JSON 里是字符串"
        assert int(span["endTimeUnixNano"]) >= int(span["startTimeUnixNano"])
        assert span["attributes"], "没有属性"
        for attr in span["attributes"]:
            assert set(attr) == {"key", "value"}
            assert len(attr["value"]) == 1, f"值必须显式标注类型：{attr}"
        if span is not root:
            assert span["parentSpanId"] == root["spanId"], "子 span 没挂在根上"


def test_otel_attributes_are_namespaced(hub) -> None:
    """属性名只有两类：**标准**的 `gen_ai.*` 与**本仓特有**的 `workbench.*`。

    分开是必要的：混在一起之后，**没人分得清哪些能被通用工具读懂**。
    而"哪些是标准"这件事恰好是本仓此刻**没核实过**的那一件
    （属性名清单见 `tools/export_otel.py --list-attributes`）。
    """
    tool_hub, _ = hub
    conv_id = "otel-namespace"
    _drive(tool_hub, conv_id, [{"content": "好"}],
           AgentConfig(system_prompt="sys", max_steps=4, approval_wait_s=0.05))

    keys = {a["key"] for s in _otlp_spans(conv_id) for a in s["attributes"]}
    bad = [k for k in keys if not k.startswith(("gen_ai.", "workbench."))]
    assert not bad, f"出现了没有命名空间的属性：{bad}"
    assert any(k.startswith("gen_ai.") for k in keys), "一个标准属性都没有"


def test_otel_export_is_deterministic(hub) -> None:
    """同一份数据导两次必须**逐字节相同** —— 否则"这次和上次比多了什么"没法做。

    所以根 span 的 id 是从 run id **确定性派生**的，不是随机的。
    """
    from tools.export_otel import to_otlp

    tool_hub, _ = hub
    conv_id = "otel-deterministic"
    _drive(tool_hub, conv_id, [{"content": "好"}],
           AgentConfig(system_prompt="sys", max_steps=4, approval_wait_s=0.05))

    first = json.dumps(to_otlp(50), sort_keys=True)
    second = json.dumps(to_otlp(50), sort_keys=True)
    assert first == second, "两次导出不一致（根 span id 可能是随机的）"


def test_otel_export_does_not_fabricate_attribute_confidence() -> None:
    """★ 未核实的属性名**必须标着 `verify`**。

    这条断言防的是"把 `verify` 悄悄改成 `ok` 而不真的去核"——
    那会让"这个属性名对不对"这个问题从**已知的未知**变成**未知的未知**，
    而后者正是本仓最防的一类（`CORE-ARCHITECTURE.md` §7.5 已明写"未逐条核"）。

    核对之后当然可以改：**改了这条就会红，提醒你把核对结论写进 ADR**。
    """
    from tools.export_otel import OTEL_ATTRS

    unverified = [slot for slot, (_n, st) in OTEL_ATTRS.items() if st != "ok"]
    if unverified:
        return  # 还有待核实的 —— 这正是当前状态，符合预期
    raise AssertionError(
        "所有属性都标成了 ok —— 请确认你真的逐条对着 "
        "https://opentelemetry.io/docs/specs/semconv/gen-ai/ 核过，"
        "并把核对日期与结论写进 ADR-0021 的「外部参照」栏。"
    )
