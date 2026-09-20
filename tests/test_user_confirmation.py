"""`request_user_confirmation`：模型主动"先问再做"，且**对话就地暂停**等答复。

## 它买的是什么（与风险门的区别，别读混）

| | 谁发起 | 拦的是什么 | 权威在 |
|---|---|---|---|
| 风险门（`Risk`） | **宿主**，按声明自动拦 | 这一次**调用** | 宿主，模型绕不过 |
| 实时确认（本文件） | **模型**，主动调工具 | 模型**打算做的事** | 模型的自律 + 用户当场判断 |

⚠️ 实时确认**不能**当高风险调用的放行依据——模型不调用它就绕过去了。
它买的是"模型可以先问再做"，而风险门买的是"不做就不能做"。两者互补。

## 本文件钉住的四件事

1. **暂停是真的**：循环在确认期间不再往下走（observation 在答复之后才发）。
2. **预先批准绑定参数指纹**：同意 `X(A)` 之后 `X(A)` 直接执行，而 `X(B)` 仍要重新过门。
3. **拒绝 / 超时不会变成默许**：超时的确认**不能**再授权任何调用。
4. **宿主校验无法被插件绕过**：目标工具不存在 / 是只读的 / 目标自己也要确认 —— 一律拒。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from workbench import approvals, db
from workbench import plugin_api as pa
from workbench.harness.loop import run_agent_loop
from workbench.harness.types import AgentConfig
from workbench.plugins import builtins

EXECUTED: list[str] = []
CONFIRM_TOOL = "request_user_confirmation"


class Arg(BaseModel):
    text: str = pa.Arg("内容", default="")


async def danger_handler(args: BaseModel, ctx: pa.ToolContext) -> str:
    EXECUTED.append(str(getattr(args, "text", "")))
    return json.dumps({"ok": True})


class ScriptedClient:
    """第 N 轮返回脚本第 N 项（与 `test_agent_approval.py` 同一套假客户端）。"""

    def __init__(self, calls: list[dict]) -> None:
        self._script = list(calls)
        self.calls: list[list[dict]] = []
        self.model = "fake"
        self.last_usage: dict = {}

    async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
        self.calls.append([dict(m) for m in messages])
        if self._script:
            step = {"content": "", "tool_calls": [self._script.pop(0)]}
        else:
            step = {"content": "结束", "tool_calls": []}
        yield {"type": "end", "content": step["content"], "tool_calls": step["tool_calls"],
               "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    async def chat(self, messages, temperature=0.7, max_tokens=None, json_mode=False):
        self.last_usage = {}
        return "摘要"


@pytest.fixture()
def hub(monkeypatch):
    db.connect()
    approvals.reset_store()
    EXECUTED.clear()
    fresh = pa.ToolHub()
    approvals.install(fresh)
    fresh.add_plugin(pa.Plugin(id="probe", title="探针", tools=[
        pa.ToolSpec(name="probe_dangerous", description="写动作", args_model=Arg,
                    handler=danger_handler, risk=pa.Risk.DANGEROUS, timeout_s=5),
        pa.ToolSpec(name="probe_confirm2", description="第二个确认工具（用来验「确认不能嵌套」）",
                    args_model=Arg, handler=danger_handler, risk=pa.Risk.READ,
                    timeout_s=5, needs_confirmation=True),
    ]))
    # 用**真实**的内置插件（而不是测试里另写一份声明）：
    # 本文件要验的正是它那条 needs_confirmation + confirm_payload 声明，另写一份就验不到了
    fresh.add_plugin(builtins.PLUGIN)
    monkeypatch.setattr(pa, "_hub", fresh)
    return fresh


def make_call(name: str, args: dict, call_id: str = "c1") -> dict:
    return {"id": call_id, "name": name, "arguments": json.dumps(args)}


async def _drive(hub, script: list[dict], on_confirm=None, conv: str = "conv-confirm"):
    """跑一轮循环；遇到 `confirm_required` 时执行 `await on_confirm(event)`。

    `on_confirm` 在循环已经挂起（停在 `confirm_required` 的 yield 处）时执行
    ——这正是真实世界的样子：**用户点按钮的时刻，循环正卡在等待里**。
    """
    client = ScriptedClient(script)
    events: list[dict] = []
    gen = run_agent_loop(
        [{"role": "user", "content": "开始"}], client, hub,
        AgentConfig(system_prompt="s", max_steps=6), conversation_id=conv,
    )
    async for event in gen:
        events.append(event)
        if event["type"] == "confirm_required" and on_confirm is not None:
            await on_confirm(event)
    return events


def _confirm_call(text: str, *, tool: str = "probe_dangerous", timeout_s: float = 5, **extra) -> dict:
    """构造一次确认调用。

    `timeout_s` 刻意**显式给一个小值**（默认 300s）：本文件里有几条反证（`verify_regression_tests.py`）
    会把实现改回缺陷版，那时的表现是"答复丢了、循环一直等"——
    给 300s 的话那不是"断言变红"而是"挂住五分钟"，工具与人都会以为脚本坏了。
    给 5s，缺陷版会在 5 秒内以一个明确的 `status=timeout` 失败掉。
    """
    args = {
        "question": f"将要写入 {text}，是否继续？",
        "details": "影响生产环境蓝图 B12，可回滚",
        "target_tool": tool,
        "target_args": {"text": text},
        "timeout_s": timeout_s,
    }
    args.update(extra)
    return make_call(CONFIRM_TOOL, args)


def _decision(events: list[dict], kind: str) -> dict:
    return next(e for e in events if e["type"] == kind)


def _last_observation(events: list[dict]) -> dict:
    return [e for e in events if e["type"] == "observation"][-1]


def run(hub, script, on_confirm=None):
    return asyncio.run(_drive(hub, script, on_confirm))


# ---------------------------------------------------------------- 1. 暂停 + 同意后放行

def test_loop_pauses_then_resumes_and_preapproval_works(hub) -> None:
    """核心：确认期间循环**停住**；用户同意后模型原样调用目标工具，直接执行。"""

    async def approve_later(event):
        # 先让循环真的走到 `await wait_for(...)`（而不是抢在它前面 resolve）——
        # 这条路径才是生产路径：用户是在循环**已经等了一会儿**之后才点的
        await asyncio.sleep(0)
        assert approvals.pending_store().approve(event["pendingId"]) is not None

    events = run(hub, [_confirm_call("对象A"), make_call("probe_dangerous", {"text": "对象A"})],
                 on_confirm=approve_later)

    # 事件顺序：action → confirm_required → confirm_resolved → observation
    types = [e["type"] for e in events]
    assert "confirm_required" in types and "confirm_resolved" in types
    assert types.index("confirm_required") < types.index("confirm_resolved")
    assert types.index("confirm_resolved") < types.index("observation"), (
        "观测必须先等答复——确认期间循环若继续往下走，界面会在用户点之前就看到结果")

    required = _decision(events, "confirm_required")
    assert required["question"].startswith("将要写入 对象A")
    assert required["details"]
    assert required["target"] == {"tool": "probe_dangerous", "risk": "dangerous",
                                 "args": {"text": "对象A"}}
    assert required["timeoutS"] > 0

    resolved = _decision(events, "confirm_resolved")
    assert resolved["approved"] is True
    assert resolved["expired"] is False

    # 确认工具自己「没有 handler 执行」这回事——真正跑起来的是目标工具
    assert EXECUTED == ["对象A"], f"目标工具没有执行：{EXECUTED}（或确认工具被误执行）"
    assert _last_observation(events)["invocation"]["status"] == "ok"


def test_rejection_is_fed_back_and_target_never_runs(hub) -> None:
    """拒绝：模型看到拒绝（含理由），目标工具**一次都没跑**。"""

    async def reject_later(event):
        assert approvals.pending_store().reject(
            event["pendingId"], "这次只做演练") is not None

    events = run(hub, [_confirm_call("对象B")], on_confirm=reject_later)

    assert EXECUTED == [], "拒绝后目标工具仍被执行了"
    statuses = [e["invocation"]["status"] for e in events if e["type"] == "observation"]
    assert statuses == ["rejected"]
    answer = _decision(events, "confirm_resolved")["answer"]
    assert "拒绝" in answer and "这次只做演练" in answer


def test_timeout_is_not_consent(hub) -> None:
    """超时 = 未同意。**并且**那条确认必须失效，不能事后再被点一下就成了通行证。"""

    async def do_nothing(_event):
        return None

    events = run(hub, [_confirm_call("对象C", timeout_s=0.05)], on_confirm=do_nothing)
    assert EXECUTED == []
    resolved = _decision(events, "confirm_resolved")
    assert resolved["approved"] is False and resolved["expired"] is True
    statuses = [e["invocation"]["status"] for e in events if e["type"] == "observation"]
    assert statuses == ["timeout"]

    pending_id = _decision(events, "confirm_required")["pendingId"]
    store = approvals.pending_store()
    assert store.get(pending_id).status == "expired"
    # 事后补点「同意」必须失败——`approve()` 只认 pending
    assert store.approve(pending_id) is None

    # 事后再调目标工具：仍要重新过风险门（说明超时没有留下任何批准）
    later = run(hub, [make_call("probe_dangerous", {"text": "对象C"})])
    assert _last_observation(later)["invocation"]["status"] == "needs_approval"
    assert EXECUTED == []


# ---------------------------------------------------------------- 2. 指纹绑定

def test_confirming_one_call_does_not_authorize_another(hub) -> None:
    """**本工具最要紧的一条**：同意 `X(A)` 不等于同意 `X(B)`。"""

    async def approve_later(event):
        assert approvals.pending_store().approve(event["pendingId"]) is not None

    events = run(hub, [
        _confirm_call("对象D"),
        make_call("probe_dangerous", {"text": "对象E"}),  # 换了参数
    ], on_confirm=approve_later)

    assert EXECUTED == [], f"换了参数仍被放行：{EXECUTED}"
    statuses = [e["invocation"]["status"] for e in events if e["type"] == "observation"]
    assert statuses[-1] == "needs_approval"


def test_intent_only_confirmation_authorizes_nothing(hub, monkeypatch) -> None:
    """不带 `target_tool` 的纯意图确认：用户同意后**不产生任何可认领的批准**。"""
    monkeypatch.setattr(approvals, "PENDING_TTL_MS", 30 * 60 * 1000)

    async def approve_later(event):
        assert approvals.pending_store().approve(event["pendingId"]) is not None

    call = make_call(CONFIRM_TOOL, {"question": "我想先确认一下方向", "details": "只是问一句"})
    events = run(hub, [call, make_call("probe_dangerous", {"text": "对象F"})],
                 on_confirm=approve_later)

    assert _decision(events, "confirm_resolved")["approved"] is True
    assert EXECUTED == []
    statuses = [e["invocation"]["status"] for e in events if e["type"] == "observation"]
    assert statuses[-1] == "needs_approval", "纯意图确认不该放行任何写操作"


# ---------------------------------------------------------------- 3. 宿主校验（插件绕不过）

def _execute(hub, name: str, args: dict):
    return asyncio.run(hub.execute(name, args))


def test_unknown_target_is_rejected(hub) -> None:
    inv = _execute(hub, CONFIRM_TOOL, {"question": "q", "target_tool": "probe_nope"})
    assert inv.status is pa.ToolStatus.BAD_ARGS
    assert "probe_nope" in inv.content and "不存在" in inv.content


def test_read_only_target_is_rejected(hub) -> None:
    """只读目标没意义：只读调用本来就不过风险门，预先批准它只会留下一条永远没人认领的行。"""
    inv = _execute(hub, CONFIRM_TOOL, {"question": "q", "target_tool": "read_local_file",
                                       "target_args": {"path": "C:/tmp/a.txt"}})
    assert inv.status is pa.ToolStatus.BAD_ARGS
    assert "只读" in inv.content


def test_nested_confirmation_is_rejected(hub) -> None:
    """确认不能嵌套：一次确认只批准一次具体调用。

    两种形态都要拒——自己批自己，以及"用一次确认去批准另一次确认"。
    """
    self_ref = _execute(hub, CONFIRM_TOOL, {"question": "q", "target_tool": CONFIRM_TOOL})
    assert self_ref.status is pa.ToolStatus.BAD_ARGS
    assert "自己" in self_ref.content

    nested = _execute(hub, CONFIRM_TOOL, {"question": "q", "target_tool": "probe_confirm2"})
    assert nested.status is pa.ToolStatus.BAD_ARGS
    assert "嵌套" in nested.content


def test_bad_target_args_are_reported_as_bad_args(hub) -> None:
    """目标参数按**目标自己的** args_model 校验，错的当场告诉模型。"""
    inv = _execute(hub, CONFIRM_TOOL, {"question": "q", "target_tool": "probe_dangerous",
                                       "target_args": {"nope": 1}})
    # `Arg.text` 有默认值，所以多出来的 `nope` 被 pydantic 忽略 → 不是 BAD_ARGS。
    # 真正会失败的是"目标参数不是对象"这种形状错误：
    assert inv.status is pa.ToolStatus.NEEDS_CONFIRMATION

    bad = _execute(hub, CONFIRM_TOOL, {"question": "q", "target_tool": "probe_dangerous",
                                       "target_args": "不是对象"})
    assert bad.status is pa.ToolStatus.BAD_ARGS


def test_confirmation_tool_must_be_read(hub) -> None:
    """装配期自检：`needs_confirmation` + 非只读 = 无限递归，必须在装配时就拒掉。"""
    spec = pa.ToolSpec(
        name="probe_confirm_write", description="d", args_model=Arg,
        handler=danger_handler, risk=pa.Risk.WRITE, needs_confirmation=True,
    )
    problems = spec.valid()
    assert any("无限递归" in p for p in problems), problems

    # 反向：声明了 confirm_payload 却忘了 needs_confirmation → 内容会被静默忽略
    loose = pa.ToolSpec(
        name="probe_loose", description="d", args_model=Arg, handler=danger_handler,
        risk=pa.Risk.READ, confirm_payload=lambda args: {"question": "q"},
    )
    assert any("静默忽略" in p for p in loose.valid())


def test_real_builtin_tool_declaration_is_valid(hub) -> None:
    """真实声明必须自检通过（否则装配期会被静默摘掉）。"""
    spec = hub.get(CONFIRM_TOOL)
    assert spec is not None
    assert spec.needs_confirmation is True
    assert spec.risk is pa.Risk.READ
    assert spec.valid() == []
    assert spec.confirm_payload is not None


def test_handler_is_never_called_by_design(hub) -> None:
    """handler 只是占位；真被调用到必须**抛**，绝不能返回"已确认"。"""
    spec = hub.get(CONFIRM_TOOL)
    with pytest.raises(RuntimeError):
        asyncio.run(spec.handler(spec.args_model(question="q"), pa.ToolContext()))


# ---------------------------------------------------------------- 4. 通道缺失 = 不问 = 不许

def test_missing_gate_fails_closed(hub) -> None:
    """宿主没接确认通道时：**不能**假装问过了，也不能执行。"""
    bare = pa.ToolHub()
    bare.add_plugin(builtins.PLUGIN)
    inv = asyncio.run(bare.execute(CONFIRM_TOOL, {"question": "q"}))
    assert inv.status is pa.ToolStatus.ERROR
    assert "没有" in inv.content and "问" in inv.content
    assert inv.confirm == {}


def test_missing_waiter_is_treated_as_no_answer(hub) -> None:
    """登记了但没人能等 → `await_confirmation` 必须按未同意返回（不是默认同意）。"""
    bare = pa.ToolHub()
    outcome = asyncio.run(bare.await_confirmation("whatever", 0.01))
    assert outcome.approved is False
    assert outcome.expired is True


# ---------------------------------------------------------------- 5. 前后端契约（无类型接口）

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_confirm_payload_keys_all_have_a_frontend_consumer() -> None:
    """`_confirm_view()` 产出的每个键，都必须真的走完前端那两跳。

    `Python → JS` 是**无类型接口**（AGENTS.md §2）：改一个键名不会有任何报错，
    只会让弹窗上少一行字——而"少一行字"在一次真实写入的确认窗上等于
    **用户看不到他要批准什么**。链路是两跳，两跳都要查：

        SSE 事件 → `chat.js` 的映射（`event.<key>`）→ 弹窗读取（`request.<key>`）

    只查第一跳是不够的：改造过程中就出现过"映射搬了、消费端没搬"（反之亦然）。
    """
    plan = pa.ConfirmPlan(
        question="q", details="d", timeout_s=12.0,
        target=pa.ToolSpec(name="t", description="d", args_model=Arg, handler=danger_handler),
        target_args=Arg(text="x"), target_args_raw={"text": "x"},
    )
    view = pa._confirm_view(plan, "pid")
    assert set(view) == {"pendingId", "question", "details", "timeoutS", "target"}
    assert set(view["target"]) == {"tool", "risk", "args"}

    mapper = _read("workbench/static/js/chat.js")
    modal = _read("workbench/static/js/approval-modal.js")
    for key in ("pendingId", "question", "details", "timeoutS"):
        assert f"event.{key}" in mapper, f"chat.js 没有把 event.{key} 交给弹窗"
        assert f"request.{key}" in modal, f"弹窗没有消费 request.{key}"
    # `target` 在映射那一跳被**摊平**（弹窗要的是"目标工具 + 它的风险 + 它的参数"，
    # 不需要知道它原来嵌在 target 里），所以两跳分开查：
    for key in ("tool", "risk"):
        assert f"event.target.{key}" in mapper, f"chat.js 没有读 event.target.{key}"
        assert f"request.{key}" in modal, f"弹窗没有消费摊平后的 request.{key}"
    assert "target.args" in modal, "弹窗没有消费 target.args（用户看不到将要传什么参数）"
    # 回执那一跳：轨迹里要能查到"当时问的是什么"
    assert "event.question" in _read("workbench/static/js/chat-tools.js")


def test_event_contract_names_are_in_sync() -> None:
    """事件名三处必须一致：`EventType` 字面量、循环发出的、前端接收的。"""
    from typing import get_args

    from workbench.harness.types import EventType

    loop_src = _read("workbench/harness/loop.py")
    chat_src = _read("workbench/static/js/chat.js")
    for name in ("confirm_required", "confirm_resolved"):
        assert name in get_args(EventType), f"{name} 不在 EventType 里"
        assert f'"{name}"' in loop_src, f"循环没有发 {name}"
        assert f'event.type === "{name}"' in chat_src, f"前端没有接 {name}"


def test_confirmation_card_wording_is_not_copied_from_the_risk_gate() -> None:
    """两类决定的**文案不能互抄**——它们要人做的事是相反的。

    风险门：本轮对话**已经结束**，用户放行后还得再发一条消息让模型重试。
    实时确认：循环**正卡在等待里**，用户点完模型自己就继续。

    抄错方向的后果是让人对着一个已经复活的循环干等，或者反过来以为还要再发一句。
    改造后这些文案都在**弹窗**里（`approval-modal.js`）——测试跟着搬到那里。
    """
    modal = _read("workbench/static/js/approval-modal.js")
    assert "本轮对话已经结束" in modal, "风险门那条没告诉用户「放行后还得让模型重试」"
    assert "模型继续" in modal, "确认那条没告诉用户「点完模型自己会继续」"
    assert "未同意" in modal, "超时的语义没写清（超时 = 没有产生任何批准）"


def test_approval_is_a_modal_not_an_inline_card() -> None:
    """**交互走弹窗，轨迹只留回执**（2026-09-20 用户反馈）。

    > 「提供给用户审批的，应该设计为弹出窗，而不是放在对话框中随着输出消失」

    内联卡片会随输出滚走、不要求答复、且长得像一条消息——而它是一次真实写入的闸门。
    这条断言钉住三件事：① 弹窗模块存在且用了原生 `showModal()`；
    ② `chat-tools.js` **不再**自己渲染带按钮的卡片（它只留不可交互的回执）；
    ③ 两类事件都接到了弹窗上。
    """
    modal = _read("workbench/static/js/approval-modal.js")
    assert "showModal()" in modal, "没用原生模态——焦点陷阱与 Esc 就得手搓"
    assert "requestDecision" in modal and "settleDecision" in modal

    tools_js = _read("workbench/static/js/chat-tools.js")
    # 旧实现的两个渲染器必须消失：留着它们等于留了一条"卡片也能点"的旁路
    assert "function approvalCard" not in tools_js, "带按钮的风险门卡片还在"
    assert "function confirmationCard" not in tools_js, "带按钮的确认卡片还在"
    assert "approvePending" not in tools_js, "轨迹文件里还留着「点一下就放行」的调用"
    assert "decisionReceipt" in tools_js, "轨迹里没有回执——那就查不到「当时问了什么」"

    chat_js = _read("workbench/static/js/chat.js")
    for event_type, factory in (("approval_required", "decisionFromApproval"),
                                ("confirm_required", "decisionFromConfirm")):
        assert factory in chat_js, f"{event_type} 没接到弹窗上"


def test_esc_means_later_not_reject() -> None:
    """Esc / 「稍后」= **没有答复**，不是拒绝。

    只给"同意/拒绝"两态时，用户想先看一眼对话就只剩"关掉弹窗"这一条路，
    而关掉等于**悄悄丢掉一次确认**（循环还在等，用户却以为已经处理完了）。
    第三态是承重的：它把决定收成底部待办条，循环继续等，到点仍算未同意。
    """
    modal = _read("workbench/static/js/approval-modal.js")
    assert 'addEventListener("cancel"' in modal and "preventDefault()" in modal, (
        "Esc 没有被拦下——原生 dialog 会直接关掉它，而用户以为那等于拒绝")
    assert "pending-strip" in modal, "「稍后」没有落点（那就等于把确认弄丢了）"


def test_pending_is_recovered_on_page_load() -> None:
    """刷新页面后，**遗留在服务端的待确认必须还能被看到**。

    没有这一步时，一条待确认只活在当前这条 SSE 流里：刷新一下、切走再看，
    它就永远没人处理了——而它是一个真实写入的闸门。
    """
    chat_js = _read("workbench/static/js/chat.js")
    assert "recoverPending" in chat_js, "页面加载时没有认领遗留的待确认"
    api_js = _read("workbench/static/js/api.js")
    assert "listPending" in api_js

