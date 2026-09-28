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
import re
from pathlib import Path

import pytest
from pydantic import BaseModel

from workbench import approvals, db
from workbench import plugin_api as pa
from workbench.harness.loop import run_agent_loop
from workbench.harness.types import AgentConfig
from workbench.plugins import builtins
from workbench.services import storage

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
                    handler=danger_handler, risk=pa.Risk.DANGEROUS, trust=pa.Trust.LOCAL, timeout_s=5),
        pa.ToolSpec(name="probe_confirm2", description="第二个确认工具（用来验「确认不能嵌套」）",
                    args_model=Arg, handler=danger_handler, risk=pa.Risk.READ, trust=pa.Trust.LOCAL,
                    timeout_s=5, needs_confirmation=True),
    ]))
    # 用**真实**的内置插件（而不是测试里另写一份声明）：
    # 本文件要验的正是它那条 needs_confirmation + confirm_payload 声明，另写一份就验不到了
    fresh.add_plugin(builtins.PLUGIN)
    monkeypatch.setattr(pa, "_hub", fresh)
    return fresh


def make_call(name: str, args: dict, call_id: str = "c1") -> dict:
    return {"id": call_id, "name": name, "arguments": json.dumps(args)}


async def _drive(hub, script: list[dict], on_confirm=None, conv: str = "conv-confirm",
                 approval_wait_s: float = 0.05):
    """跑一轮循环；遇到 `confirm_required` 时执行 `await on_confirm(event)`。

    `on_confirm` 在循环已经挂起（停在 `confirm_required` 的 yield 处）时执行
    ——这正是真实世界的样子：**用户点按钮的时刻，循环正卡在等待里**。

    `approval_wait_s`（**风险门**的等待秒数）在这个文件里**默认给极小值**：
    本文件验的是"模型主动确认"，而 2026-09-22（决策卡 G2）之后风险门也会就地等答复
    ——真值 300 秒，任何一次"危险工具没被预先批准"的调用都会把用例挂在那里。
    """
    client = ScriptedClient(script)
    events: list[dict] = []
    gen = run_agent_loop(
        [{"role": "user", "content": "开始"}], client, hub,
        AgentConfig(system_prompt="s", max_steps=6, approval_wait_s=approval_wait_s),
        conversation_id=conv,
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


def run(hub, script, on_confirm=None, approval_wait_s: float = 0.05):
    return asyncio.run(_drive(hub, script, on_confirm, approval_wait_s=approval_wait_s))


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

    # 事后再调目标工具：仍要重新过风险门（说明超时没有留下任何批准）；
    # 而这次同样没人放行 ⇒ 风险门等到超时后按**未同意**处理（2026-09-22 G2 之后，风险门也等了）
    later = run(hub, [make_call("probe_dangerous", {"text": "对象C"})])
    assert _last_observation(later)["invocation"]["status"] == "timeout"
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
    assert statuses[-1] == "timeout", statuses


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
    assert statuses[-1] == "timeout", f"纯意图确认不该放行任何写操作：{statuses}"


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
        handler=danger_handler, risk=pa.Risk.WRITE, trust=pa.Trust.LOCAL, needs_confirmation=True,
    )
    problems = spec.valid()
    assert any("无限递归" in p for p in problems), problems

    # 反向：声明了 confirm_payload 却忘了 needs_confirmation → 内容会被静默忽略
    loose = pa.ToolSpec(
        name="probe_loose", description="d", args_model=Arg, handler=danger_handler,
        risk=pa.Risk.READ, confirm_payload=lambda args: {"question": "q"},
        trust=pa.Trust.LOCAL,
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


# ---------------------------------------------------------------- 选择型确认（2026-09-21）
#
# 用户原始要求：「希望将选择变为选择卡片式的，成功选择卡片后，卡片 UI 则经过移动动画，
# 变成简化版，显示在右下角……后续就默认使用选择的账号。」
#
# "后续默认使用"分两半，**两半都要在这条链路上成立**：
#   · 机器那一半 = 下面这些测试（选择结果作为观测回到模型手里）；
#   · 人那一半 = 右下角那张卡片（前端契约由文件末尾的静态断言守）。

CHOICE_OPTIONS = [
    {"value": "acc-1", "label": "liyulong", "description": "测试环境",
     "meta": {"地址": "https://standard-val.example"}},
    {"value": "acc-2", "label": "T0901", "description": "通桥"},
]


def _with_options(hub, options=None):
    hub.set_option_provider(
        lambda _source: list(CHOICE_OPTIONS if options is None else options))
    return hub


def _choice_call(*, source: str = "accounts", timeout_s: float = 5, **extra) -> dict:
    args = {
        "question": "要用哪个账号执行？",
        "details": "选定的账号会被后续步骤默认使用",
        "options_source": source,
        "timeout_s": timeout_s,
    }
    args.update(extra)
    return make_call(CONFIRM_TOOL, args)


def test_choice_confirmation_carries_options_and_returns_the_pick(hub) -> None:
    """选一个：候选项进事件，用户点中的那个 value **必须回到模型手里**。"""
    _with_options(hub)

    async def pick(event):
        await asyncio.sleep(0)  # 走生产路径：循环已经等了一会儿，用户才点
        assert [o["value"] for o in event["options"]] == ["acc-1", "acc-2"]
        assert event["options"][0]["label"] == "liyulong"
        assert event["options"][0]["meta"] == {"地址": "https://standard-val.example"}
        assert approvals.pending_store().approve(event["pendingId"], value="acc-2") is not None

    events = run(hub, [_choice_call()], on_confirm=pick)

    confirmation = _decision(events, "confirm_required")
    assert [o["label"] for o in confirmation["options"]] == ["liyulong", "T0901"]

    resolved = _decision(events, "confirm_resolved")
    assert resolved["approved"] is True
    # 观测里要**同时**有 id（机器要用的）与人话标签（模型转述给用户时用），
    # 并且明说"别再问一遍"——否则模型很容易把同一件事再确认一次，
    # 而用户会觉得自己刚才那一下白点了
    assert "acc-2" in resolved["answer"] and "T0901" in resolved["answer"]
    assert "不要再为同一件事问用户" in resolved["answer"]
    # 选择题**不执行任何工具**：它只是"问一句"，答复本身就是结果
    assert EXECUTED == []


def test_a_choice_cannot_be_approved_without_a_choice(hub) -> None:
    """选择题的"同意"没有意义——没有 value（或 value 不在候选项里）一律拒。"""
    _with_options(hub)
    inv = _execute(hub, CONFIRM_TOOL, {"question": "选哪个？", "options_source": "accounts"})
    assert inv.status is pa.ToolStatus.NEEDS_CONFIRMATION

    store = approvals.pending_store()
    assert store.approve(inv.pending_id) is None, "没有 value 也放行了——等于同意了一个空答案"
    assert store.approve(inv.pending_id, value="acc-9") is None, "不在候选项里的 value 被放行了"
    assert store.approve(inv.pending_id, value="acc-1") is not None


def test_option_source_without_provider_fails_closed(hub) -> None:
    """没接候选项提供方时必须**拒**，不能退化成"让模型自己编候选项"。

    编出来的 value 会被当成真实账号 id 一路用下去，而且**不会报错**
    （同族事故见 PITFALLS #62 / #67）。
    """
    hub.set_option_provider(None)
    inv = _execute(hub, CONFIRM_TOOL, {"question": "选哪个？", "options_source": "accounts"})
    assert inv.status is pa.ToolStatus.BAD_ARGS
    assert "候选项提供方" in inv.content


def test_unknown_option_source_is_rejected(hub) -> None:
    """未知来源要**如实说"这个来源不存在"**，而不是返回空列表。

    空列表看起来像"用户没有可选的东西"，而事实是"这个来源根本不存在"
    ——两者对模型意味着完全不同的下一步。
    """
    inv = _execute(hub, CONFIRM_TOOL, {"question": "选哪个？", "options_source": "nope"})
    assert inv.status is pa.ToolStatus.BAD_ARGS
    assert "未知的候选项来源" in inv.content


def test_empty_option_source_is_rejected_with_a_next_step(hub) -> None:
    """来源是空的：拒绝，并且给一句**能照做**的话（不是让模型原样重试）。"""
    _with_options(hub, options=[])
    inv = _execute(hub, CONFIRM_TOOL, {"question": "选哪个？", "options_source": "accounts"})
    assert inv.status is pa.ToolStatus.BAD_ARGS
    assert "空的" in inv.content


def test_choice_and_target_tool_are_mutually_exclusive(hub) -> None:
    """「挑一个」与「预先批准一次具体调用」不能混：选哪个是用户点完才知道的。"""
    _with_options(hub)
    inv = _execute(hub, CONFIRM_TOOL, {
        "question": "选哪个？", "options_source": "accounts",
        "target_tool": "probe_dangerous", "target_args": {"text": "x"},
    })
    assert inv.status is pa.ToolStatus.BAD_ARGS
    assert "不能同时填" in inv.content


def test_selection_is_remembered_per_conversation(hub) -> None:
    """选中的账号按**会话**记（界面刷新后靠它把右下角那张卡画回来）。

    跨会话必须互不串台：A 会话显示出 B 会话选的账号，比不显示更糟。
    """
    _with_options(hub)
    store = approvals.pending_store()

    first = asyncio.run(hub.execute(
        CONFIRM_TOOL, {"question": "选哪个？", "options_source": "accounts"},
        pa.ToolContext(conversation_id="conv-A"),
    ))
    assert first.status is pa.ToolStatus.NEEDS_CONFIRMATION
    assert store.approve(first.pending_id, value="acc-1") is not None

    remembered = approvals.current_selection("conv-A")
    assert remembered["value"] == "acc-1"
    assert remembered["label"] == "liyulong"
    assert remembered["description"] == "测试环境"
    # 别的会话（以及没有会话的 HTTP 直调链路）都不该看到它
    assert approvals.current_selection("conv-B") is None
    assert approvals.current_selection("") is None


def test_option_provider_is_wired_to_the_real_accounts_source(hub) -> None:
    """装配期必须真的把 `accounts` 接上。

    没接上的表现很隐蔽：模型一填 `options_source` 就被拒，它看到的是一句
    "宿主没有接入候选项提供方"，于是退回自然语言问用户——功能**在**，但没人用得到。
    """
    assert isinstance(approvals.option_provider("accounts"), list)
    with pytest.raises(ValueError):
        approvals.option_provider("definitely-not-a-source")


def test_ui_initiated_pick_remembers_the_account_and_tells_the_model(hub) -> None:
    """界面发起的选择（右下角「选择账号」默认入口）：**记住 + 告诉模型**，缺一不可。

    · 只记住 → 模型下一轮照样问"用哪个账号"，用户觉得白选了；
    · 只告诉模型 → 刷新页面右下角那张卡就没了，用户觉得选丢了。
    """
    _with_options(hub)
    spec = next(t for t in hub.specs() if t.name == CONFIRM_TOOL)
    store = approvals.pending_store()
    conversation_id = storage.create_conversation()["id"]

    pending_id = store.request_selection(
        spec, CHOICE_OPTIONS, conversation_id, "要用哪个账号执行？")

    item = store.get(pending_id)
    assert item is not None and item.kind == "selection"
    assert len(item.options) == 2
    # **没有等待者**：用户是在开口之前选的，没有任何对话循环在等它
    assert pending_id not in store._waiters, "界面发起的选择不该建等待者"

    # 校验一条都不放松：换个入口不等于换一套规则
    assert store.approve(pending_id, value="nope") is None
    assert store.approve(pending_id, value="") is None
    assert store.approve(pending_id, value="acc-1") is not None

    remembered = approvals.current_selection(conversation_id)
    assert remembered["value"] == "acc-1" and remembered["label"] == "liyulong"

    notes = [r for r in storage.list_messages(conversation_id)
             if r.get("role") == approvals.NOTE_ROLE]
    assert len(notes) == 1, f"界面事件没有进会话（模型下一轮就不知道了）：{notes}"
    assert "acc-1" in notes[0]["content"] and "liyulong" in notes[0]["content"]


def test_model_initiated_pick_does_not_leave_a_note(hub) -> None:
    """模型自己问的那种**不留** note：它马上会收到工具观测。

    再留一条就是同一件事说两遍——而"两个都写全量"迟早会漂（AGENTS.md 规则 28）。
    """
    _with_options(hub)
    conversation_id = storage.create_conversation()["id"]
    inv = asyncio.run(hub.execute(
        CONFIRM_TOOL, {"question": "选哪个？", "options_source": "accounts"},
        pa.ToolContext(conversation_id=conversation_id),
    ))
    assert inv.status is pa.ToolStatus.NEEDS_CONFIRMATION
    assert approvals.pending_store().approve(inv.pending_id, value="acc-2") is not None

    assert approvals.current_selection(conversation_id)["value"] == "acc-2"
    assert [r for r in storage.list_messages(conversation_id)
            if r.get("role") == approvals.NOTE_ROLE] == []


def test_note_role_never_reaches_the_model_as_is() -> None:
    """`note` **不能原样发给模型**——那不是少一条上下文，是整轮请求被 400。

    DeepSeek / OpenAI 只认 `system` / `user` / `assistant` / `tool`。
    这条钉住"翻译发生在唯一那个扼流点"，以及"未知角色宁可丢掉也不发出去"。
    """
    from workbench.api.routes_chat import _model_history

    out = _model_history([
        {"role": "user", "content": "帮我建对象"},
        {"role": "assistant", "content": "好的"},
        {"role": approvals.NOTE_ROLE, "content": "用户在界面上选定了执行账号：lyulong"},
        {"role": "未来才有的角色", "content": "x"},
    ])
    assert [m["role"] for m in out] == ["user", "assistant", "user"], out
    assert "lyulong" in out[2]["content"]
    assert "界面事件" in out[2]["content"], "翻成 user 之后必须留下「这不是用户说的话」的痕迹"
    assert all(m["role"] in {"system", "user", "assistant", "tool"} for m in out)


def test_the_ui_pick_entry_is_default_open() -> None:
    """右下角**默认开放**（用户 2026-09-21 要求）：

    > 「在右下角选择卡片的位置，默认开放。意思是用户进入新对话时，就可以立即选择账号。」

    所以"没选账号"时的表现必须是**摆出一个可点的入口**，而不是把整个浮层藏起来
    ——藏起来等于新对话里根本没有地方可以选账号。
    """
    dock = _read("workbench/static/js/account-dock.js")
    chat = _read("workbench/static/js/chat.js")
    assert "account-invite" in dock, "没有默认入口"
    assert "PICK_ACCOUNT_EVENT" in dock and "PICK_ACCOUNT_EVENT" in chat, (
        "默认入口没有接到对话流程上")
    # 点它是**请服务端登记**一次选择（候选由服务端展开），不是前端自己列账号
    assert "pickSelection" in chat and "pickSelection" in _read("workbench/static/js/api.js")
    # 没选账号时展示入口，不是 hidden
    assert "invite.hidden = false" in dock, "默认态把入口藏起来了"


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
    )
    view = pa._confirm_view(plan, "pid")
    assert set(view) == {"pendingId", "question", "details", "timeoutS", "target", "options"}
    assert set(view["target"]) == {"tool", "risk", "args"}
    assert view["options"] == [], "没有候选项时必须是空数组，不能让界面进'选择题'模式"

    mapper = _read("workbench/static/js/chat.js")
    modal = _read("workbench/static/js/approval-modal.js")
    for key in ("pendingId", "question", "details", "timeoutS", "options"):
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
    """两类决定的**文案不能互抄**——它们要人做的事不一样。

    2026-09-22（决策卡 G2）**之前**：风险门那条得说"本轮对话已经结束，放行后还得让模型重试"。
    G2 之后两条**都在循环里等**，区别变成"**谁接手**"：

    | 决定 | 放行之后 |
    |---|---|
    | 风险门（宿主拦下） | **服务端**用完全相同的参数执行，结果回到这一轮 |
    | 实时确认（模型问） | **模型**自己接着往下走 |

    抄错方向的后果是让人对着一个已经复活的循环干等，或者反过来以为还要再发一句。
    """
    modal = _read("workbench/static/js/approval-modal.js")
    assert "服务端" in modal and "相同参数执行" in modal, "风险门那条没说清「放行后由谁执行」"
    assert "模型继续" in modal, "确认那条没告诉用户「点完模型自己会继续」"
    assert "未同意" in modal, "超时的语义没写清（超时 = 没有产生任何批准）"
    assert "本轮对话已经结束" not in modal, (
        "还在写旧口径：G2 之后风险门也在循环里等，本轮**没有**结束")


def test_decision_modal_is_scannable_not_a_wall_of_text() -> None:
    """★ 弹窗必须**可扫读**，不许把说明文字堆成一大坨（2026-09-22 用户下达）。

    > 「弹出的蓝图/审核界面，请你不要放一堆文字上去，请设计排版，言简意赅，
    >  用户在执行时没有时间去仔细看 UI 上比蚂蚁还小的字」

    确认框是一次**真实写入的闸门**，用户只有几秒钟做判断，所以这条把它变成四条判据
    （都是源码级——本仓没有 DOM 测试环境，前端断言一律这样钉）：

    ① 说明文字被切成**标题 + 要点**（`headlineOf` / `briefingPoints`，长段落按句读切开）；
    ② 要点**超过 4 条折起来**（渐进披露：`MAX_POINTS` + `tk-more` + `aria-expanded`）；
    ③ 技术参数（UUID 那堆）**默认收起**（`tk-disclosure`）——旧实现是把 JSON 直接摊在脸上；
    ④ 字号有**下限**：标题 ≥15px、要点 ≥13px、次要文字 ≥12px。
       这一条是"蚂蚁字"的正面判据，改小了就红。
    """
    modal = _read("workbench/static/js/approval-modal.js")
    assert "headlineOf" in modal and "briefingPoints" in modal, "说明文字没有被切成标题 + 要点"
    assert "MAX_POINTS = 4" in modal, "要点没有条数上限（一屏读不完就等于没写）"
    assert "tk-more" in modal and 'aria-expanded' in modal, "长要点没有渐进披露的落点"
    assert "tk-disclosure" in modal, "技术参数没有收起的入口"

    css = _read("workbench/static/css/chat-tools.css")

    def font_px(selector: str) -> float:
        match = re.search(
            re.escape(selector) + r"\s*\{[^}]*font-size:\s*([\d.]+)px", css, re.S)
        assert match, f"{selector} 没有可判定的 font-size"
        return float(match.group(1))

    assert font_px(".tk-decision-question") >= 15, "标题字号回到 14px 了"
    assert font_px(".tk-point") >= 13, "要点字号低于 13px（用户抱怨的就是这个）"
    assert font_px(".tk-decision-details") >= 12
    assert font_px(".tk-decision-meta") >= 12


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


def test_the_pick_is_docked_and_the_dock_survives_a_reload() -> None:
    """「选中之后变成简化版、停在右下角」这件事的**两半都要在**。

    人那一半（右下角卡片）如果只活在前端内存里，刷新一下就没了
    ——而模型那边还记得那个账号。**两边认知不一致比不显示更糟**：
    用户会以为"我选的丢了"，于是重选一遍，而模型已经在按旧的选择干活。
    所以卡片必须能从服务端拉回来（`getSelection`），会话一换就跟着换。
    """
    dock_js = _read("workbench/static/js/account-dock.js")
    modal_js = _read("workbench/static/js/approval-modal.js")
    chat_js = _read("workbench/static/js/chat.js")
    css = _read("workbench/static/css/chat-tools.css")

    # ① 弹窗里是"卡片"，不是又一组按钮；点中=选中，并把矩形快照交给停靠卡
    assert "tk-choice-grid" in modal_js, "弹窗里没有候选卡片栅格"
    assert "tk-choice-card" in modal_js
    assert "dockSelection" in modal_js, "选中之后没有落到右下角"
    assert "getBoundingClientRect" in modal_js, (
        "没有取来源卡片的矩形——飞行动画会从屏幕左上角飞出来")
    # 「同意」在选择题里是答非所问，必须藏起来
    assert "ui.allow.hidden = isChoice" in modal_js, "选择题里还摆着「同意」"

    # ② 停靠卡：会飞、会收成简化版、刷新能拉回来、尊重"减少动态效果"
    assert "fromRect" in dock_js, "动画起点不是矩形快照"
    assert "prefers-reduced-motion" in dock_js, "没有尊重系统的减少动态效果设置"
    assert "getSelection" in _read("workbench/static/js/api.js"), "没有取回选择的接口调用"
    assert "restoreSelection" in chat_js, "页面加载/切会话时没有把卡片画回来"

    # ③ CSS 里那一格必须真的存在（只写 JS 不写样式 = 卡片堆在文档流末尾）
    for cls in (".tk-choice-grid", ".tk-choice-card", ".account-dock", ".account-chip"):
        assert cls in css, f"{cls} 没有样式"
    assert "position: fixed" in css, "右下角停靠卡没有脱离文档流"
    assert "prefers-reduced-motion" in css, "CSS 侧没有关掉动画的兜底"


def test_the_account_picker_does_not_open_its_own_dropdown() -> None:
    """「换一个账号」走**服务端那套选择**，而不是前端自己列账号。

    候选必须由服务端按账号表展开（`/api/agent/selection/pick`），前端自己弹一个
    下拉就等于造出**第二个会漂的事实源**——而卡片上的 `value` 是要当参数用下去的。
    """
    chat_js = _read("workbench/static/js/chat.js")
    dock_js = _read("workbench/static/js/account-dock.js")
    # 停靠卡自己不请求账号清单（`/api/accounts` 只属于账号中心页面）
    assert "/api/accounts" not in dock_js, "停靠卡自己拉了账号清单——那会造出第二个事实源"
    assert "pickSelection" in chat_js, "界面发起的选择没有走服务端那条路"


def test_switching_account_reopens_the_picker_instead_of_typing_a_message() -> None:
    """用户 2026-09-21 反馈第 3 条：「选好账号后点切换没有反应」。

    **根因是上一版留下的不一致**：「换一个账号」复用的是"把一句话放进输入框、
    让模型重新问一遍"——那是 `/api/agent/selection/pick` 还不存在时的权宜之计。
    等界面自己就能发起选择之后，那条老路只剩副作用：用户点一下，弹窗关了、
    输入框里多了一句话（还被右下角那张卡挡着），看起来就是"按钮坏了"。

    现在它和默认入口**同一个事件、同一条链路**：直接弹卡片。
    """
    dock_js = _read("workbench/static/js/account-dock.js")
    chat_js = _read("workbench/static/js/chat.js")
    assert "SWITCH_ACCOUNT_EVENT" not in dock_js, "还留着那条「放进输入框」的老路"
    assert "SWITCH_ACCOUNT_EVENT" not in chat_js
    # 切换按钮派发的是**选择**事件（和默认入口同一个），不是"塞一句话"
    assert dock_js.count("PICK_ACCOUNT_EVENT") >= 2, (
        "「换一个账号」没有复用「选择账号」那条路——它应该派发同一个事件")
    assert "input.value = prompt" not in chat_js, "还在往输入框里塞话"


def test_a_user_initiated_pick_never_lands_in_the_pending_strip() -> None:
    """用户 2026-09-21 反馈第 4 条：「我主动选的，凭什么给我挂一条待办横幅」。

    > 「当用户主动选择账号时，弹出来 UI 选择框，测试用户选择稍后，随后在用户的
    >  输入框上方会出现一条横幅提醒继续操作。我认为这不合理，因为这是用户主动
    >  选择，不是 agent 发起。」

    待办条的语义是**"有人在等你答复"**（它是「稍后」的落点、是 agent 卡在等待里的
    可视化）。而一次自己点开的选择背后**没有任何东西在等**——它进待办条只会让用户
    以为哪里卡住了。所以 `local: true` 的那类请求**不进 `pending`**。
    """
    chat_js = _read("workbench/static/js/chat.js")
    modal = _read("workbench/static/js/approval-modal.js")
    assert "local: true" in chat_js, "界面发起的选择没有标 local —— 它会掉进待办条"
    assert "if (!request.local) pending.set(" in modal, (
        "待办条仍然收 local 请求：用户点「关闭」之后会多出一条'等你拍板'的横幅")
    # 自己点开的那种选择后面没有接收方：拒绝按钮与「稍后」都要说人话
    assert "ui.deny.hidden = isLocalChoice" in modal, "自己点开的选择还摆着「都不合适」"
    assert '"关闭"' in modal, "自己点开的选择没有把「稍后」改成「关闭」"


def test_the_dock_never_covers_the_composer() -> None:
    """用户 2026-09-21 反馈第 1 条：「没有选择账号时，遮挡输入框」。

    根因是写死了 `bottom: 22px`——那是**视口**底部，而输入区也在视口底部，两者必然
    重叠。修法不是换一个写死的数字（输入区高度会变：textarea 随内容长高、
    提示条出现/消失），而是**按实际高度让位**。
    """
    dock_js = _read("workbench/static/js/account-dock.js")
    css = _read("workbench/static/css/chat-tools.css")
    assert "keepClearOfComposer" in dock_js, "没有让位逻辑"
    assert "ResizeObserver" in dock_js, (
        "没有跟着输入区高度走——写死一个 bottom 只能挡住「测试时的那个高度」")
    assert "--dock-bottom" in dock_js and "var(--dock-bottom" in css, (
        "让位变量没有接上样式")
    # 面板要**向上**展开：否则它会顶在最底下，把锚点连同输入区一起压住
    assert "order: -1" in css, "账号面板没有改成向上展开"

