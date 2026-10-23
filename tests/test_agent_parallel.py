r"""整批只读调用的并发执行（计划 §7.4）。

## ★ 这一节的实现**偏离了计划原文**，而且偏离是有证据的

计划 §7.4 写的是「只并发**整批都是 `Risk.READ` 且都不需确认**的调用」。
按那个口径实现完之后，**计划自己 §5.6 的两条停止判据 + 一条评测用例当场变红**
（`test_stop_before_a_tool_call_backfills_every_unexecuted_call`、
`test_the_action_event_is_not_emitted_for_a_call_we_will_not_make`、评测用例 `stop_mid_batch`）。

原因不是实现写错了，而是那个口径**必然**带来一个后果：并发会把"停止"的检查点
从**每次调用之间**退到**每批之间**，而那是 §5.6 明确买下的性质。

★ **新功能让已批准的判据变红时，默认假设是新功能错了，不是判据该改。**
所以口径收窄成**逐工具举证**：`ToolSpec.parallel_safe`（缺省 `False`），
标它之前必须回答「这个工具并发跑两个，会不会碰同一份可变状态？」。

## 本文件钉住的四件事

| 断言 | 它防的是 |
|---|---|
| 标了并发的批次**真的重叠** | 一个"看起来并发了、其实是串行"的实现 |
| 事件顺序按 **index**、不按完成顺序 | 事件流不可复现（所有基于顺序的断言变成掷骰子） |
| 没标的批次**保持串行** | 口径被悄悄放宽（那正是本次变红的那条路） |
| 被拒的批次**不碰**重复检测历史 | `would_loop` 的必要性 —— 预检不能改状态 |
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel

from workbench import approvals, cache, db
from workbench import hub as hub_mod
from workbench import plugin_api as pa
from workbench.harness import loop as loop_mod
from workbench.harness.loop import run_agent_loop
from workbench.harness.safety import SafetyGate
from workbench.harness.types import AgentConfig


@pytest.fixture()
def ready():
    db.connect()
    return db


class Args(BaseModel):
    n: int = pa.Arg("数字", default=1)


class Hub:
    """一个探针中枢。`at_once_max` 用来**证明两次调用真的重叠**（而不是看耗时猜）。"""

    def __init__(self, monkeypatch) -> None:
        approvals.reset_store()
        hub = pa.ToolHub()
        approvals.install(hub)
        monkeypatch.setattr(hub_mod, "_hub", hub)
        self.hub = hub
        self.ran: list[str] = []
        self.inside = 0
        self.at_once_max = 0

    def add(self, name: str, *, parallel_safe: bool, risk: pa.Risk = pa.Risk.READ,
            needs_confirmation: bool = False, delay: float = 0.0) -> None:
        async def handler(args: Args, ctx: pa.ToolContext) -> str:
            # ★ 重叠的判据是"**同一时刻有几个在跑**"，不是"谁看见过谁的事件"。
            #
            # 我第一版用的是"两个 Event 互相等"，那在**串行**下会误报：
            # A 先跑并置了自己的事件，B 之后跑时看见它**早就置好了** ——
            # 于是 B 报告"重叠了"，而它其实是接着 A 跑的。
            # 现在的形状在两种情况下都是确定的：
            #   并发：A 进来（inside=1）→ `sleep(0)` 让出 → B 进来（inside=2）
            #         → 两个都读到 2 ⇒ 判为重叠；
            #   串行：A 从头跑到尾（inside 始终没到 2）→ 然后才轮到 B ⇒ 判为不重叠。
            self.ran.append(name)
            self.inside += 1
            await asyncio.sleep(0)
            if self.inside >= 2:
                self.at_once_max = max(self.at_once_max, 2)
            if delay:
                await asyncio.sleep(delay)
            else:
                await asyncio.sleep(0.02)
            self.inside -= 1
            return json.dumps({"tool": name, "n": args.n})

        def payload(args: Args) -> dict:
            # ★ `timeoutS` 必须显式给小值：不写就走 `DEFAULT_CONFIRM_TIMEOUT_S = 300.0`，
            # 于是"用例挂住"而不是"断言变红"（AGENTS.md §3 专门警告过这条，
            # 我第一版就踩了 —— 整轮跑挂 5 分钟）。
            return {"question": f"要不要跑 {name}？", "timeoutS": 0.1}

        self.hub.add_plugin(pa.Plugin(id=f"p-{name}", title=name, tools=[
            pa.ToolSpec(name=name, description=f"{name} 说明", args_model=Args,
                        handler=handler, risk=risk, reversibility=pa.Reversibility.IRREVERSIBLE, trust=pa.Trust.LOCAL,
                        parallel_safe=parallel_safe, needs_confirmation=needs_confirmation,
                        confirm_payload=payload if needs_confirmation else None,
                        timeout_s=5),
        ]))


def _call(name: str, args: dict | None = None, call_id: str = "c1") -> dict:
    return {"id": call_id, "name": name, "arguments": json.dumps(args or {})}


def _drive(hub: Hub, script: list[dict], *, conv: str, max_steps: int = 10,
           on_event=None) -> list[dict]:
    cache.clear_state(conv)

    class Client:
        model = "fake"

        def __init__(self) -> None:
            self._script = list(script)

        async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
            step = self._script.pop(0) if self._script else {"content": "完"}
            yield {"type": "end", "content": step.get("content", ""),
                   "tool_calls": step.get("tool_calls", []),
                   "usage": {}, "finish_reason": "stop"}

        async def chat(self, messages, temperature=0.7, max_tokens=None, json_mode=False):
            return "摘要"

    async def run():
        out: list[dict] = []
        async for event in run_agent_loop(
            [{"role": "user", "content": "开始"}], Client(), hub.hub,
            AgentConfig(system_prompt="s", max_steps=max_steps, approval_wait_s=0.05),
            conversation_id=conv,
        ):
            out.append(event)
            if on_event is not None:
                result = on_event(event)
                if hasattr(result, "__await__"):
                    await result
        return out

    return asyncio.run(run())


def _two_calls(a="probe_a", b="probe_b") -> list[dict]:
    return {"tool_calls": [_call(a, {"n": 1}, "c1"), _call(b, {"n": 2}, "c2")]}


# ---------------------------------------------------------------- 真的重叠


def test_two_opted_in_calls_really_overlap(monkeypatch) -> None:
    """★ 判据一：标了 `parallel_safe` 的两个调用**真的同时在跑**。

    用 `asyncio.Event` 互相等对方 —— 串行执行时**必然超时**，
    所以这条断言不依赖"总耗时小于某个数"那种看运气的写法。
    """
    hub = Hub(monkeypatch)
    hub.add("probe_a", parallel_safe=True)
    hub.add("probe_b", parallel_safe=True)

    _drive(hub, [_two_calls(), {"content": "好"}], conv="par-1")

    assert hub.at_once_max == 2, "两个调用没有重叠（串行跑了）—— 见 Hub.add 里的重叠判据"


def test_a_tool_that_did_not_opt_in_stays_serial(monkeypatch) -> None:
    """★★ 判据二：**没标的批次保持串行** —— 这条是本节口径的守门人。

    计划原文只要求 `Risk.READ`。★ 如果哪天有人把 `parallel_safe` 从这个判断里去掉
    （"反正都是只读"），这条会红 —— 而它红了之后**不要改它**：
    回去读 `ToolSpec.parallel_safe` 的文档串，那里记着按原口径实现时
    §5.6 的两条停止判据是怎么变红的。
    """
    hub = Hub(monkeypatch)
    hub.add("probe_a", parallel_safe=False)
    hub.add("probe_b", parallel_safe=False)

    _drive(hub, [_two_calls(), {"content": "好"}], conv="par-serial")

    assert hub.at_once_max < 2, "没标 parallel_safe 的调用竟然并发了 —— 见 ToolSpec.parallel_safe"
    assert hub.ran == ["probe_a", "probe_b"], f"串行顺序也变了：{hub.ran}"


def test_a_mixed_batch_stays_serial(monkeypatch) -> None:
    """★ **只要有一个没标，整批退回串行。**

    "半数并发"不是省一半时间，而是造出两种事件形状（有的批分组、有的批交替），
    而所有消费方都要同时处理两种 —— 那笔复杂度比省下的那点延迟贵得多。
    """
    hub = Hub(monkeypatch)
    hub.add("probe_a", parallel_safe=True)
    hub.add("probe_b", parallel_safe=False)

    _drive(hub, [_two_calls(), {"content": "好"}], conv="par-mixed")

    assert hub.at_once_max < 2, "混合批次并发了"


def test_a_risk_batch_stays_serial(monkeypatch) -> None:
    """★ 有副作用的批次**绝不并发** —— "每个动作要么完整发生、要么完全没发生"
    的保证只在串行路径上给（那正是 §5.6 买下的性质）。"""
    hub = Hub(monkeypatch)
    hub.add("probe_a", parallel_safe=True, risk=pa.Risk.WRITE)
    hub.add("probe_b", parallel_safe=True, risk=pa.Risk.WRITE)

    _drive(hub, [_two_calls(), {"content": "好"}], conv="par-risk",
           on_event=lambda e: approvals.pending_store().approve(e["pendingId"])
           if e["type"] == "approval_required" else None)

    assert hub.at_once_max < 2, "写操作批次并发了"


def test_a_confirmation_batch_stays_serial(monkeypatch) -> None:
    """★ 要确认的调用**绝不并发**：它会去等人，并发它等于把多个等用户的交互搅在一起。"""
    hub = Hub(monkeypatch)
    hub.add("probe_a", parallel_safe=True, needs_confirmation=True)
    hub.add("probe_b", parallel_safe=True)

    _drive(hub, [_two_calls(), {"content": "好"}], conv="par-confirm", max_steps=3)

    assert hub.at_once_max < 2, "要确认的批次并发了"


def test_a_batch_bigger_than_the_cap_stays_serial(monkeypatch) -> None:
    """★ 超过 `PARALLEL_READ_MAX` 的批次**整批退回串行**（不切波）。

    切波要引入"波与波之间要不要重新查停止/预算"的额外语义，而那正是这条快路径
    最该保持简单的地方。见 `PARALLEL_READ_MAX` 的文档串。
    """
    hub = Hub(monkeypatch)
    hub.add("probe_a", parallel_safe=True)
    hub.add("probe_b", parallel_safe=True)
    n = loop_mod.PARALLEL_READ_MAX + 1
    calls = [_call("probe_a" if i % 2 == 0 else "probe_b", {"n": i}, f"c{i}")
             for i in range(n)]

    _drive(hub, [{"tool_calls": calls}, {"content": "好"}], conv="par-big")

    assert hub.at_once_max < 2, f"{n} 个调用竟然并发了"


def test_an_insufficient_budget_stays_serial(monkeypatch) -> None:
    """★ 预算不够整批 ⇒ 退回串行，由它逐调用扣减并回填未执行的（不变量 I16）。

    快路径**不承担**预算耗尽的语义：那条路上有"每一条未执行的调用都要回填"的完整逻辑
    与它的回归断言。快路径重写它 = 把那些断言一起弄失效。
    """
    hub = Hub(monkeypatch)
    hub.add("probe_a", parallel_safe=True)
    hub.add("probe_b", parallel_safe=True)

    events = _drive(hub, [_two_calls(), {"content": "好"}], conv="par-budget",
                    max_steps=1)

    assert hub.at_once_max < 2, "预算只够 1 个却并发了"
    obs = [e for e in events if e["type"] == "observation"]
    assert len(obs) == 2, f"应当有一条执行 + 一条回填，实际 {len(obs)} 条"


def test_a_duplicate_in_the_batch_stays_serial(monkeypatch) -> None:
    """★ 批内有重复 ⇒ 退回串行：重复检测是有状态的，快路径不碰它。"""
    hub = Hub(monkeypatch)
    hub.add("probe_a", parallel_safe=True)

    same = _call("probe_a", {"n": 1}, "c1")
    _drive(hub, [{"tool_calls": [same, dict(same, id="c2")]}, {"content": "好"}],
           conv="par-dup")

    assert hub.at_once_max < 2


# ---------------------------------------------------------------- 事件顺序与 I16


def test_events_come_out_in_call_order_not_completion_order(monkeypatch) -> None:
    """★ 判据三：事件按 `index` 而不是**完成顺序**回填。

    让第一个调用慢、第二个快。完成顺序会是 B→A，而事件流必须是 A→B ——
    否则"这次先回来的是谁"取决于网络，所有基于顺序的断言都变成掷骰子。
    """
    hub = Hub(monkeypatch)
    hub.add("probe_a", parallel_safe=True, delay=0.12)
    hub.add("probe_b", parallel_safe=True, delay=0.0)

    events = _drive(hub, [_two_calls(), {"content": "好"}], conv="par-order")
    names = [e["invocation"]["tool"] for e in events if e["type"] == "observation"]
    assert names == ["probe_a", "probe_b"], f"观测顺序不是 index 顺序：{names}"

    actions = [e["tool"]["name"] for e in events if e["type"] == "action"]
    assert actions == ["probe_a", "probe_b"], f"action 顺序不是 index 顺序：{actions}"


def test_every_call_is_paired_and_counted(monkeypatch) -> None:
    """★ I16 + 步数计费：并发的 N 个调用要**逐个**记步，且每个都有一条 tool 消息。

    并发的诱惑是"一次性结算" —— 那会让 `steps_used` 少记，而步数预算是**成本闸门**，
    少记的后果是 agent 比配置的多跑几步，**不报错**。
    """
    hub = Hub(monkeypatch)
    hub.add("probe_a", parallel_safe=True)
    hub.add("probe_b", parallel_safe=True)

    events = _drive(hub, [_two_calls(), {"content": "好"}], conv="par-count")

    obs = [e for e in events if e["type"] == "observation"]
    assert len(obs) == 2
    assert {e["invocation"]["status"] for e in obs} == {"ok"}
    # 两条观测各带一个**互不相同**的 auditId —— 前端按它配对卡片
    ids = {e["invocation"]["auditId"] for e in obs}
    assert len(ids) == 2, f"两条观测的 auditId 撞了：{ids}"
    # toolCallId 必须是**模型给的那个**（不是空串），否则审计轨迹对不上账
    assert {e["invocation"]["toolCallId"] for e in obs} == {"c1", "c2"}


# ---------------------------------------------------------------- would_loop


def test_would_loop_does_not_mutate_the_history() -> None:
    """★★ `would_loop` 必须是 `is_loop` 的**只读**版本。

    这是并发快路径的**前提**：资格预检失败时必须原封不动地退回串行路径，
    而预检一旦改了 `_history`，同一次调用在串行路径上会被**记第二遍**，
    重复检测就**提前一轮误报**（本该第 3 次触发，变成第 2 次）。

    ★ 这个缺陷的形态很难看：它**只在预检失败时出现**（有风险批次 / 有重复 /
    有确认的那些回合），而那时人正在盯别的问题。
    """
    gate = SafetyGate(max_steps=10, loop_threshold=3, loop_window=4)
    call = _call("probe_a", {"n": 1})

    for _ in range(3):
        gate.would_loop(call)
    # 探了 3 次之后，真正的 is_loop 才应该开始报重复
    assert gate.is_loop(call) is False, "would_loop 改了历史 —— 预检把重复检测喂饱了"
    assert gate.is_loop(call) is False
    assert gate.is_loop(call) is True, "第 3 次真调用应当触发重复检测"


def test_a_rejected_batch_leaves_the_loop_history_untouched(monkeypatch) -> None:
    """★★ 端到端版：**被拒的批次不许在重复检测历史里留下任何东西**。

    构造一个只差一条就能触发重复检测的历史，然后跑一个**会被拒**的批次
    （混合 parallel_safe），再断言"第 3 次相同调用仍然触发"。
    预检若用了 `is_loop`，这里会提前触发。
    """
    hub = Hub(monkeypatch)
    hub.add("probe_a", parallel_safe=True)
    hub.add("probe_b", parallel_safe=False)

    same = _call("probe_a", {"n": 7}, "c1")
    # 先让 probe_a 的历史里已有 1 条，且批内重复 ⇒ 必定被拒
    events = _drive(hub, [
        {"tool_calls": [dict(same, id="p0")]},
        {"tool_calls": [same, dict(same, id="c2")]},
        {"content": "好"},
    ], conv="par-history")

    # 第 2 批被拒（批内重复）⇒ 走串行 ⇒ 它自己会触发重复检测并回填
    kinds = [e["type"] for e in events]
    assert "observation" in kinds
    # 关键断言：**第 2 批的两次调用都真的走到了重复检测那一步**
    skipped = [e for e in events if e["type"] == "observation"
               and "重复" in json.dumps(e, ensure_ascii=False)]
    assert skipped, (
        "被拒的批次没有触发串行路径的重复检测 —— "
        "预检很可能用了 is_loop（改了历史）或干脆没退回串行"
    )


def test_the_parallel_path_records_spans_marked_parallel(monkeypatch) -> None:
    """★ 并发跑的调用在轨迹里**标出来**（`attrs["parallel"]=True`）。

    不标的话，事后看时间线只会觉得"这几个 span 怎么时间重叠了" ——
    而"为什么重叠"（我们主动并发的）与"是不是时钟错了"是两件事，
    轨迹自己应该答得出来。

    ★ 这里刻意**只验 span 的形状**（用假 recorder），不验"写库成功"：
    两件事混在一起测，写库失败会被误读成"标记没写对"。
    """
    from workbench.harness import trace

    hub = Hub(monkeypatch)
    hub.add("probe_a", parallel_safe=True)
    hub.add("probe_b", parallel_safe=True)

    recorded: list[dict] = []

    class FakeRecorder:
        def span(self, kind, name, **kw):
            recorded.append({"kind": kind, "name": name, **kw})

    context = asyncio.run(_collect_spans(hub, FakeRecorder()))

    parallel = [s for s in recorded if s.get("attrs", {}).get("parallel")]
    assert len(parallel) == 2, f"并发批次的 span 没有逐个标 parallel：{recorded}"
    assert {s["kind"] for s in recorded} == {trace.SPAN_TOOL}
    assert len(context) == 2, f"并发路径没把两条 tool 消息写进上下文：{context}"


async def _collect_spans(hub: "Hub", recorder) -> list[dict]:
    """只为了拿到 `_run_parallel_batch` 写出的 span 形状与上下文回填。"""
    from workbench.harness.loop import _run_parallel_batch

    safety = SafetyGate(max_steps=4)
    calls = [_call("probe_a", {"n": 1}, "c1"), _call("probe_b", {"n": 2}, "c2")]
    context: list[dict] = []
    async for _ in _run_parallel_batch(calls, hub.hub, AgentConfig(system_prompt="s"),
                                       safety, context=context, conversation_id="par-span",
                                       recorder=recorder):
        pass
    assert safety.steps_used == 2, f"并发路径没有逐个记步（I16）：{safety.steps_used}"
    return context


def test_real_tools_are_not_opted_in_by_default() -> None:
    """★★ **真实工具面上，除一个例外全都没标并发。**

    这条是**对着手写清单**核的（同 `test_the_real_web_tools_are_declared_untrusted`
    的手法）：谁把某个工具批量标上 `parallel_safe=True`（"反正它只是读"），
    这条会红 —— 而那个直觉正是本节的起因。

    今天只放行 `get_current_time`：纯计算，读一次系统时钟，没有 IO、没有共享状态、
    没有 `to_thread`。要再加一个，**先回答**「它并发跑两个，会不会碰同一份可变状态？」
    （HTTP 会话 / 浏览器那条单工作线程队列 / sqlite 连接都是"只读但会碰"的例子），
    然后把答案写进那条工具的注释里。
    """
    from workbench.harness.tools import default_registry

    opted = sorted(s.name for s in default_registry().specs() if s.parallel_safe)
    assert opted == ["get_current_time"], (
        f"标了并发的工具变了：{opted}。"
        "新增一个之前请先回答「它并发跑两个会不会碰同一份可变状态」"
        "（见 ToolSpec.parallel_safe 的文档串与）"
    )
