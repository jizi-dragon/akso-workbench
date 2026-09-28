r"""可中止（stop）—— 计划 §5.6。

## 这个模块补的是什么

长作业（一次 300 秒的洞察任务、一次 40 步的工具链）跑起来**只能关页面**，
而关页面会留半截会话（§5.4c）。用户在真机上说过的话是「停不下来」。

## 计划 §5.6 的四条判据（本文件的骨架）

> 取消后断言：① 未执行调用被回填 ② assistant 消息落库 ③ pending 变 `expired`
> ④ 事件流以 `cancelled` 结尾

## ★ 两条容易被写错的边界（本文件专门钉住）

- **停止不撤销已经发生的副作用**：工具调用是真调用，平台上可能已经建了东西。
  所以 `_cancelled_message` 必须说清这件事 —— 写成"已取消"会让人以为"什么都没发生"。
- **协作者身份要分清**：`（已停止）`（用户自己按的）≠ `（已中止）`（连接断了）。
  两者对用户的含义完全不同，而 `cancelled` 事件**刷新后就没了**，
  所以标注必须落进库，否则刷新之后那一轮看起来就是"模型答到一半不说了"。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel

from workbench import approvals, cache, db
from workbench import plugin_api as pa
from workbench.api import routes_chat
from workbench.harness import stop as stop_mod
from workbench.harness import trace
from workbench.harness.loop import _cancelled_message, run_agent_loop
from workbench.harness.types import AgentConfig

CONFIRM_TOOL = "request_user_confirmation"


@pytest.fixture()
def ready():
    db.connect()
    return db


class ScriptedClient:
    def __init__(self, script: list[dict], *, model: str = "deepseek-flash") -> None:
        self._script = list(script)
        self.model = model
        self.last_usage: dict = {}
        self.calls: list[list[dict]] = []

    async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
        self.calls.append([dict(m) for m in messages])
        step = self._script.pop(0) if self._script else {"content": "（脚本耗尽）",
                                                        "tool_calls": []}
        yield {
            "type": "end",
            "content": step.get("content", ""),
            "tool_calls": step.get("tool_calls", []),
            "usage": {"prompt_tokens": 10, "prompt_cache_hit_tokens": 5,
                      "prompt_cache_miss_tokens": 5, "completion_tokens": 2},
            "finish_reason": step.get("finish_reason", "stop"),
        }

    async def chat(self, messages, temperature=0.7, max_tokens=None, json_mode=False):
        return "摘要"


class StopAfter:
    """前 `n` 次问"要停吗"都答 False，之后答 True。

    用它把停止**精确地落在某一个检查点**上 —— 比"跑一半然后从外面置位"确定得多，
    因为后者取决于事件循环的调度顺序，而用例不该依赖那个。
    """

    def __init__(self, n: int) -> None:
        self.n = n
        self.reads = 0

    def __call__(self) -> bool:
        self.reads += 1
        return self.reads > self.n


class ExplodingStop:
    """求值就抛 —— 用来验"坏掉的停止信号不该把正在干的活打断"。"""

    def __call__(self) -> bool:
        raise RuntimeError("停止信号自己坏了")


class EchoArgs(BaseModel):
    text: str = pa.Arg("内容", default="")


def _short_confirm(args: EchoArgs) -> dict:
    """确认计划的载荷，`timeoutS` **刻意给 2 秒**。

    ⚠️ 不给的话会走 `DEFAULT_CONFIRM_TIMEOUT_S = 300.0`，而本文件里
    "等待被打断"那条路径一旦失效，表现是**挂住五分钟**而不是断言变红
    —— 工具与人都会以为脚本坏了。本文件第一版就撞了一次：反证跑卡满 300 秒，
    而**还原步骤排在它后面**，差点把缺陷版留在工作区
    （`test_user_confirmation.py` 里记着同一个坑，我读过它，仍然撞了）。
    """
    return {"question": f"将要处理 {args.text}，是否继续？", "details": "用例构造",
            "timeoutS": 2.0}


@pytest.fixture()
def hub(ready, monkeypatch):
    approvals.reset_store()
    tool_hub = pa.ToolHub()
    approvals.install(tool_hub)
    executed: list[str] = []

    async def handler(args: EchoArgs, ctx: pa.ToolContext) -> str:
        executed.append(args.text)
        return json.dumps({"ok": True, "echo": args.text})

    async def confirm_handler(args: EchoArgs, ctx: pa.ToolContext) -> str:
        executed.append(f"confirm:{args.text}")
        return json.dumps({"ok": True})

    tool_hub.add_plugin(pa.Plugin(id="probe", title="探针", tools=[
        pa.ToolSpec(name="probe_echo", description="回显", args_model=EchoArgs,
                    handler=handler, risk=pa.Risk.READ, timeout_s=5),
        pa.ToolSpec(name="probe_confirm", description="需要确认的回显",
                    args_model=EchoArgs, handler=confirm_handler,
                    risk=pa.Risk.READ, timeout_s=5, needs_confirmation=True,
                    confirm_payload=_short_confirm),
    ]))
    monkeypatch.setattr(pa, "_hub", tool_hub)
    return tool_hub, executed


def _calls(n: int) -> list[dict]:
    return [{"id": f"c{i}", "name": "probe_echo",
             "arguments": json.dumps({"text": f"n{i}"})} for i in range(n)]


def _confirm_call(text: str = "x") -> dict:
    """一次**模型直接发起的**实时确认。

    ★ 刻意**不走** `request_user_confirmation` 那个工具：那条路是给
    "我打算调用某个 write/dangerous 工具"用的（且 read 级会被拒）。
    而 `needs_confirmation=True` 的工具是**模型直接调用它自己** ——
    `ToolHub.execute` 看到那个标记就返回 `NEEDS_CONFIRMATION`、不跑 handler。
    用前者的形状去构造后者的场景，会以 `bad_args` 静默失败（本用例第一版就是这样）。
    """
    return {"id": "cc", "name": "probe_confirm",
            "arguments": json.dumps({"text": text})}


def _drive(hub_obj, conv_id: str, script: list[dict], *, should_stop=None,
           max_steps: int = 8, on_confirm=None) -> list[dict]:
    cache.clear_state(conv_id)
    client = ScriptedClient(script)
    cfg = AgentConfig(system_prompt="sys", max_steps=max_steps, approval_wait_s=0.05)

    async def run() -> list[dict]:
        out: list[dict] = []
        try:
            async for event in run_agent_loop(
                [{"role": "user", "content": "开始"}], client, hub_obj, cfg,
                conversation_id=conv_id, should_stop=should_stop,
            ):
                out.append(event)
                if event["type"] == "confirm_required" and on_confirm is not None:
                    await on_confirm(event)
        except Exception:  # noqa: BLE001 —— 有的用例刻意让模型炸
            pass
        return out

    events = asyncio.run(run())
    return events, client


def _types(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


# ---------------------------------------------------------------- 检查点① 模型调用前


def test_stop_before_the_model_call_never_sends_a_request(hub) -> None:
    """★ 检查点①最要紧的性质：**一次请求都没发出去**。

    模型调用是本轮唯一"会花钱"的动作。停止请求已经在了，还发一次请求，
    就是"用户说别做了，我们还是花了他的钱" —— 而它不会报任何错。
    """
    tool_hub, executed = hub
    token = stop_mod.StopToken("s1")
    token.request()

    events, client = _drive(tool_hub, "stop-before-model", [{"content": "不该跑到这里"}],
                            should_stop=token)

    assert client.calls == [], "停止请求已经在了，还是发了模型请求"
    assert executed == [], "工具被执行了"
    assert _types(events) == ["cancelled", "usage"], f"事件流不对：{_types(events)}"


def test_a_turn_with_nothing_executed_says_so(hub) -> None:
    """一次工具都没跑时说"没有产生任何影响" —— 这是**能给出的最强保证**，不该含糊。"""
    tool_hub, _ = hub
    token = stop_mod.StopToken("s1b")
    token.request()
    events, _ = _drive(tool_hub, "stop-nothing", [{"content": "x"}], should_stop=token)

    message = next(e for e in events if e["type"] == "cancelled")["message"]
    assert "没有产生任何影响" in message


# ---------------------------------------------------------------- 检查点② 工具调用前


def test_stop_before_a_tool_call_backfills_every_unexecuted_call(hub) -> None:
    """★ 判据①：未执行调用被回填，且**剩下的工具真的没跑**。

    读数走向（`StopAfter`）：①模型调用前=1(False) → ②第 0 个工具前=2(False)
    → ③第 1 个工具前=3(True) ⇒ 只有第 0 个执行。
    """
    tool_hub, executed = hub
    events, _ = _drive(tool_hub, "stop-mid-batch",
                       [{"content": "", "tool_calls": _calls(3)}],
                       should_stop=StopAfter(2))

    assert executed == ["n0"], f"该只执行第 0 个，实际 {executed}"
    skipped = [e for e in events if e["type"] == "observation"
               and "停止" in str(e.get("result", {}).get("summary", ""))]
    assert len(skipped) == 2, f"未执行的两条没都被回填：{_types(events)}"


def test_stop_keeps_the_history_legal(hub) -> None:
    """★ 回填的用途：`assistant` 带了 3 个 `tool_calls` 就必须有 3 个配对响应。

    少一条这段历史对平台就是**非法的**（下一轮请求会被拒），
    而它在本地的表现只是"模型下一轮开始胡说"。
    """
    tool_hub, _ = hub
    events, _ = _drive(tool_hub, "stop-legal",
                       [{"content": "", "tool_calls": _calls(3)}],
                       should_stop=StopAfter(2))

    observations = [e for e in events if e["type"] == "observation"]
    assert len(observations) == 3, f"3 个调用应当有 3 条观测（含跳过）：{len(observations)}"
    paired = {o["invocation"]["toolCallId"] for o in observations}
    assert paired == {"c0", "c1", "c2"}, f"配对不齐：{paired}"


def test_the_action_event_is_not_emitted_for_a_call_we_will_not_make(hub) -> None:
    """停止检查在 `action` 事件**之前**：否则界面先显示"正在调用 X"、紧接着"已停止"，
    而 X 根本没跑 —— 那句"正在调用"是假的。"""
    tool_hub, _ = hub
    events, _ = _drive(tool_hub, "stop-no-fake-action",
                       [{"content": "", "tool_calls": _calls(3)}],
                       should_stop=StopAfter(2))

    actions = [e for e in events if e["type"] == "action"]
    assert len(actions) == 1, f"只该有 1 个 action 事件：{[a['call']['id'] for a in actions]}"
    assert actions[0]["call"]["id"] == "c0"


def test_the_stream_ends_with_cancelled(hub) -> None:
    """★ 判据④：事件流以 `cancelled` 结尾。

    精确口径：`cancelled` 是**最后一个内容事件**，它之后只有 `usage`
    —— 与 `final` / `error` 的收尾形状一致（用量汇总永远在最后一条，
    因为它要等所有 LLM 调用结束才有完整答案）。
    """
    tool_hub, _ = hub
    events, _ = _drive(tool_hub, "stop-ends",
                       [{"content": "", "tool_calls": _calls(3)}],
                       should_stop=StopAfter(2))

    kinds = _types(events)
    assert kinds[-1] == "usage", f"最后一条应当是 usage：{kinds}"
    assert kinds[-2] == "cancelled", f"倒数第二条应当是 cancelled：{kinds}"
    assert "final" not in kinds and "error" not in kinds, f"还发了别的终止事件：{kinds}"


# ---------------------------------------------------------------- 检查点③ 等待确认期间


def test_stop_during_a_confirmation_wait_expires_the_pending(hub) -> None:
    """★ 判据③：pending 变 `expired`。

    ★ 走的是 `approvals` **已有**的那条路（`CancelledError` ⇒
    `expire(pending_id, "对话已中断")`），**没有另写一套** —— 计划点名。
    另写一套的后果是两份"中断语义"迟早不一致，而它们都自称是权威。

    这里在读到 `confirm_required` 之后就置位停止（循环此刻正挂在等待上），
    下一次轮询（≤0.2s）就会取消等待。
    """
    tool_hub, executed = hub
    token = stop_mod.StopToken("s3")

    async def on_confirm(_event) -> None:
        token.request("用例置位")

    events, _ = _drive(tool_hub, "stop-during-confirm",
                       [{"content": "", "tool_calls": [_confirm_call("probe_confirm")]}],
                       should_stop=token, on_confirm=on_confirm)

    kinds = _types(events)
    assert "confirm_required" in kinds, f"前提不成立，没走到确认：{kinds}"
    assert kinds[-2] == "cancelled", f"没以 cancelled 收尾：{kinds}"
    assert "confirm:" not in executed, f"确认目标工具被执行了：{executed}"

    pending_id = next(e for e in events if e["type"] == "confirm_required")["pendingId"]
    rows = db.query("SELECT status FROM pending_action WHERE id = ?", (pending_id,))
    assert rows, f"pending 没落库：{pending_id}"
    assert rows[0]["status"] == "expired", (
        f"pending 没被标 expired（实际 {rows[0]['status']}）—— 一条晾着的确认"
        "会让它五分钟内看起来还能点"
    )


# ---------------------------------------------------------------- 默认关 / 失败安全


def test_nothing_changes_when_should_stop_is_none(hub) -> None:
    """★ **默认关**：`should_stop=None` 时行为与从前逐字相同。

    这条是回归的护栏 —— 一个新增的可选参数若在默认路径上改变了行为，
    影响面是**所有**既有调用方，而它不会报错。
    """
    tool_hub, executed = hub
    events, _ = _drive(tool_hub, "stop-none",
                       [{"content": "", "tool_calls": _calls(2)}, {"content": "做完了"}])

    kinds = _types(events)
    assert "cancelled" not in kinds, f"没传 should_stop 却取消了：{kinds}"
    assert kinds[-2] == "final"
    assert executed == ["n0", "n1"]


def test_a_broken_stop_signal_does_not_break_the_turn(hub) -> None:
    """★ 停止信号自己抛异常时**按"不停"处理**。

    它是从 HTTP 线程那一侧读来的一个外部输入；让一个坏掉的停止信号打断正在干的活，
    等于把"控制面"的故障升级成"数据面"的故障 —— 本仓在日志（规则 27）、
    用量入账（§5.4a）、运行轨迹（§6.1）上都是同一条纪律。
    """
    tool_hub, executed = hub
    events, _ = _drive(tool_hub, "stop-broken",
                       [{"content": "", "tool_calls": _calls(1)}, {"content": "做完了"}],
                       should_stop=ExplodingStop())

    kinds = _types(events)
    assert "cancelled" not in kinds, f"坏信号把活打断了：{kinds}"
    assert kinds[-2] == "final"
    assert executed == ["n0"]


# ---------------------------------------------------------------- ★ 副作用不撤销


def test_the_message_says_side_effects_are_not_undone(hub) -> None:
    """★★ **停止不撤销已经发生的副作用** —— 这条必须写在文案里。

    工具调用是真调用，平台上可能已经建了东西。写成"已取消"会让人以为
    "什么都没发生"，而他可能已经建了一个模块/发了一条消息。
    文案里必须同时给出**做了多少**（步数）与**要做什么**（去确认结果）。
    """
    message = _cancelled_message(3)
    assert "3 次" in message, f"没说清执行了多少步：{message}"
    assert "不会因为停止而撤销" in message, f"没提副作用不撤销：{message}"
    assert "确认" in message, f"没告诉用户该做什么：{message}"


def test_a_zero_step_stop_gives_the_stronger_guarantee() -> None:
    """一次都没跑时说"没有产生任何影响" —— 这是**能给出的最强保证**，不该含糊。"""
    assert "没有产生任何影响" in _cancelled_message(0)


# ---------------------------------------------------------------- 登记表与端点


def test_the_stop_token_is_released_after_the_turn(monkeypatch) -> None:
    """★ **登记表必须被摘干净**（计划 §5.6 的"不引入全局单例"落到实处）。

    不摘的两个后果都不报错：① 这张表随会话数无限增长；
    ② 更坏 —— 某个会话停止后令牌**永远停着**，它下一轮会**一上来就被取消**，
    而现象是"这个会话再也问不出东西了"。
    """
    routes_chat._STOP_TOKENS.clear()
    token = routes_chat._register_stop("conv-x")
    assert routes_chat.stop_conversation("conv-x") is True
    assert token.requested

    routes_chat._release_stop("conv-x", token)
    assert "conv-x" not in routes_chat._STOP_TOKENS, "令牌没被摘掉"
    assert routes_chat.stop_conversation("conv-x") is False, "摘掉后还报'在跑'"


def test_release_does_not_remove_someone_elses_token() -> None:
    """★ 摘令牌时**只摘自己那一枚**。

    同一个会话可能有两条连接（用户开了两个标签页）：先来的那条结束时，
    不能把后来的那条的令牌摘掉 —— 否则**停止功能对那个标签页静默失效**。
    """
    routes_chat._STOP_TOKENS.clear()
    first = routes_chat._register_stop("conv-y")
    second = routes_chat._register_stop("conv-y")   # 后来的那条覆盖了它
    routes_chat._release_stop("conv-y", first)      # 先来的那条结束
    assert routes_chat._STOP_TOKENS.get("conv-y") is second, "把别人的令牌摘掉了"
    routes_chat._STOP_TOKENS.clear()


def test_stopping_an_idle_conversation_reports_not_running() -> None:
    """没在跑时点停止**不是错误**：如实回"这一轮已经结束了"。

    报 404 会让用户以为"停止功能坏了" —— 而他其实只是手快，
    或者网络延迟让回合在他点之前就结束了。
    """
    routes_chat._STOP_TOKENS.clear()
    out = routes_chat.stop(routes_chat.StopBody(conversationId="conv-idle"))
    assert out["ok"] is True
    assert out["running"] is False
    assert "已经结束" in out["message"]


def test_the_stop_endpoint_reaches_the_running_turn() -> None:
    """端点是登记表的一层薄封装 —— 在跑的时候必须能置位到那一枚令牌上。"""
    routes_chat._STOP_TOKENS.clear()
    token = routes_chat._register_stop("conv-live")
    out = routes_chat.stop(routes_chat.StopBody(conversationId="conv-live"))
    assert out["running"] is True
    assert token.requested, "端点没有置位到令牌上"
    assert "做完了手上这一步" in out["message"], "文案没说清'不是立刻停'"
    routes_chat._STOP_TOKENS.clear()


# ---------------------------------------------------------------- ★ 落库标注


@pytest.mark.parametrize(("result", "completed", "expect"), [
    ({"content": "半截话", "cancelled": True}, True, "半截话（已停止）"),
    ({"content": "", "cancelled": True}, True, "（模型未返回内容）（已停止）"),
    ({"content": "半截话"}, False, "半截话（已中止）"),
    ({"content": "半截话", "cancelled": True, "error": "网络失败"}, True,
     "错误：网络失败"),
])
def test_a_cancelled_turn_is_stored_with_the_stopped_marker(result, completed, expect) -> None:
    """★ **停止与中止必须分开标注**，而错误优先于两者。

    为什么"已停止"要落进库：`cancelled` **事件刷新后就没了**，
    所以刷新之后那一轮若只有半截正文，看起来就是"模型答到一半不说话了"
    —— 用户会去怀疑模型或网络，而真实原因是**他自己按了停止**。
    """
    content, kind = routes_chat._turn_final_text(result, completed=completed)
    assert content == expect, f"落库正文不对：{content!r}"
    if result.get("error"):
        assert kind == routes_chat.MESSAGE_KIND_ERROR
    else:
        assert kind == routes_chat.MESSAGE_KIND_DEFAULT


def test_the_run_is_recorded_as_aborted_not_failed(hub) -> None:
    """★ run 的状态是 `aborted` 而**不是** `failed`。

    用户按停止不是"它坏了"。记成 failed 会让"最近失败率"这个指标从此不可信
    —— 而那是这套运行记录最主要的用途之一。
    """
    tool_hub, _ = hub
    _drive(tool_hub, "stop-run-status",
           [{"content": "", "tool_calls": _calls(3)}], should_stop=StopAfter(2))

    run = next(r for r in trace.recent_runs(50)
               if r["conversationId"] == "stop-run-status")
    assert run["status"] == trace.RUN_ABORTED, f"记成了 {run['status']}"
    assert run["status"] != trace.RUN_FAILED
