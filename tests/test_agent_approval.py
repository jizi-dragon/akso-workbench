"""确认门在**对话链路**里可用（P0-A 的回归）+ 风险门**就地等答复**（决策卡 G2，2026-09-22）。

## 缺陷原状 A（2026-09-20 实测复现，见 docs/AGENT-TOOL-AUDIT.md P0-A）

前端刻意**不自动重发**被拦下的那次调用（"放行是人做的决定"），所以用户放行后会让模型
**再试一次**。但模型的重试走 `harness/loop.py`，**那里拿不到令牌**——令牌只回给了批准它的
那一次 HTTP 响应。改造前的结果：

    轮1 被拦（pendingId=A）→ 人放行 → 轮2 重试 → **仍被拦，且新建了 pendingId=B** → 工具从未执行

⇒ `accounts_browser_open` / `accounts_browser_close`
（当时全部 3 个写 / 危险工具；第 3 个 `factory_create` 已随配置工厂整体移除）在对话链路里
永远无法执行。

修法 A：`PendingStore.claim_approved()`——无令牌时按「工具 + **参数指纹**」认领一条**已批准**的
条目，**一次性**（认领后置 `consumed`）。门槛与令牌完全一致，只是凭据从令牌换成了服务端内存里的事实。

## 缺陷原状 B（2026-09-22 真机实测，决策卡 G2）

修法 A **假设"模型会重试"**。真机上的失败形态是：模型把话说完，**这一轮就结束了**，
用户随后点「放行」时**没有任何人在等**——那条批准被搁置，界面表现为"我点了放行，什么都没发生"
（15:08 实测：`POST /pending/{id}/approve` 之后进程日志**零行**）。

修法 B：风险门与实时确认**对称**——`harness/loop.py` 发完 `approval_required` **就地等答复**，
同意则**由服务端用完全相同的参数执行**（走同一条 `claim_approved`），拒绝/超时按未同意处理。
⇒ 本文件里"先超时 → 放行 → 再重试"那几条用例仍然有意义：它们钉的是**放行晚于本轮**
（以及 HTTP 面）时的认领路径，而那正是修法 A 买的东西。

## 本文件是**反证过**的

`tools/verify_regression_tests.py` 会把 `claim_approved()` 改回缺陷版（直接返回 None），
期望 `test_approved_retry_actually_executes_in_the_chat_flow` 立刻变红。
⚠️ **那条用例名是机器接口**（反证脚本按名字点它），改名必须同步改 `tools/verify_regression_tests.py`。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest
from pydantic import BaseModel

from workbench import approvals, db
from workbench import plugin_api as pa
from workbench.harness.loop import run_agent_loop
from workbench.harness.types import AgentConfig

EXECUTED: list[str] = []


class Arg(BaseModel):
    text: str = pa.Arg("内容", default="")


class SecretArg(BaseModel):
    """参数名命中 `_REDACT_KEYS`（`token`）——用来钉住"指纹必须用未打码的那份"。"""

    token: str = pa.Arg("凭据", default="")


async def dangerous_handler(args: BaseModel, ctx: pa.ToolContext) -> str:
    EXECUTED.append(getattr(args, "text", getattr(args, "token", "")))
    return json.dumps({"ok": True})


class ScriptedClient:
    """第 N 轮返回脚本第 N 项；每次都调同一个工具（模拟"重试同一次调用"）。"""

    def __init__(self, calls: list[dict]) -> None:
        self._script = list(calls)
        self.calls = []
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


async def _collect(gen):
    return [e async for e in gen]


@pytest.fixture()
def hub(monkeypatch):
    db.connect()
    approvals.reset_store()
    EXECUTED.clear()
    fresh = pa.ToolHub()
    approvals.install(fresh)
    fresh.add_plugin(pa.Plugin(id="probe", title="探针", tools=[
        pa.ToolSpec(name="probe_dangerous", description="写动作", args_model=Arg,
                    handler=dangerous_handler, risk=pa.Risk.DANGEROUS, timeout_s=5),
        pa.ToolSpec(name="probe_secret", description="参数名叫 token 的写动作",
                    args_model=SecretArg, handler=dangerous_handler,
                    risk=pa.Risk.WRITE, timeout_s=5),
    ]))
    monkeypatch.setattr(pa, "_hub", fresh)
    return fresh


def make_call(name: str, args: dict, call_id: str = "c1") -> dict:
    return {"id": call_id, "name": name, "arguments": json.dumps(args)}


def run_turn(hub, call: dict, conv: str = "conv-approval", wait: float = 0.05) -> dict:
    """跑一轮 Agent 循环（**没人答复**），返回最后那个 observation 的 invocation。

    `wait` = `AgentConfig.approval_wait_s`：风险门的等待秒数。**测试里必须调小**——
    默认真值是 300 秒（真人看一眼再点的量级），照默认跑会把用例挂到超时。
    ⚠️ 等不到答复 = 按**未同意**处理，那条 pending 会被标 `expired`（`approvals.expire`），
    所以"先超时、再批准"是**做不到**的（那正是"绝不默认同意"的另一面）；
    要测"用户放行"，就必须在**等待窗口之内**答复 ⇒ 用 `run_turn_answered`。
    """
    client = ScriptedClient([call])
    events = asyncio.run(_collect(run_agent_loop(
        [{"role": "user", "content": "继续"}], client, hub,
        AgentConfig(system_prompt="s", max_steps=4, approval_wait_s=wait),
        conversation_id=conv,
    )))
    return next(e["invocation"] for e in events if e["type"] == "observation")


def _answer_as_soon_as_asked(*, approve: bool, reason: str = "",
                             deadline: float = 5.0) -> threading.Thread:
    """模拟"用户看到弹窗后点一下"：**在另一个线程里**等 pending 出现就答复。

    为什么要另起线程：这一下必须发生在循环**正卡在等待里**的时候（真机上的形状也一样——
    答复走 HTTP，落在 worker 线程）。在同一个事件循环里"先答复再等待"是另一个场景
    （答复早到一步），由 `_settle_locked` 的暂存区兜住。
    """
    def _worker() -> None:
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            rows = approvals.pending_store().list("pending")
            if rows:
                if approve:
                    approvals.pending_store().approve(rows[0]["id"])
                else:
                    approvals.pending_store().reject(rows[0]["id"], reason)
                return
            time.sleep(0.01)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return thread


def run_turn_answered(hub, call: dict, *, approve: bool = True, reason: str = "",
                      conv: str = "conv-approval", wait: float = 5.0):
    """跑一轮，并**在它等答复的时候**放行/拒绝（模拟用户在弹窗上点那一下）。"""
    client = ScriptedClient([call])
    thread = _answer_as_soon_as_asked(approve=approve, reason=reason)
    events = asyncio.run(_collect(run_agent_loop(
        [{"role": "user", "content": "继续"}], client, hub,
        AgentConfig(system_prompt="s", max_steps=4, approval_wait_s=wait),
        conversation_id=conv,
    )))
    thread.join(timeout=1.0)
    return next(e["invocation"] for e in events if e["type"] == "observation"), events, client


# ---------------------------------------------------------------- 核心：放行必须真的执行

def test_approved_retry_actually_executes_in_the_chat_flow(hub) -> None:
    """**P0-A 的核心断言**：用户放行 → **工具真的执行了**（且是服务端按指纹认领的）。

    ⚠️ **用例名是机器接口**：`tools/verify_regression_tests.py` 把 `claim_approved()` 改回
    缺陷版（直接返回 None）后期望**这一条**变红——改名字必须同步改那个脚本。

    2026-09-22（决策卡 G2）之后它的形态变了：放行由**服务端就地执行**（对话链路拿不到令牌，
    只能靠 `claim_approved` 认领那条批准），不再指望模型重试。所以这条用例同时钉三件事：
    ① 没答复时不执行；② 放行后**真的执行**；③ 模型看到的是**真实结果**，而不是"请你重试"。
    """
    call = make_call("probe_dangerous", {"text": "对象A"})

    # ① 没人答复 ⇒ 按未同意处理，一个字节都没执行
    first = run_turn(hub, call)
    assert first["status"] == "timeout", (
        f"没等到答复时必须按**未同意**处理（status={first['status']}）")
    assert not EXECUTED, "未确认前不该执行"

    # ② 用户在等待窗口内放行 ⇒ 服务端用**完全相同的参数**执行（认领那条批准）
    observation, events, client = run_turn_answered(hub, call, conv="conv-approval-2")
    assert observation["status"] == "ok", (
        f"放行后仍被拦（status={observation['status']}）——"
        "对话链路拿不到令牌，必须靠 claim_approved 认领")
    assert EXECUTED == ["对象A"], f"工具没有真的执行：{EXECUTED}"
    assert [e for e in events if e["type"] == "approval_required"], "没有发出放行请求事件"

    # ③ 模型看到的是真实结果，**不是**"请你重试同一次调用"那套话术
    retry_hint = [m for m in client.calls[-1]
                  if m.get("role") == "tool" and "重试" in str(m.get("content") or "")]
    assert not retry_hint, f"模型被要求自己重试（旧行为）：{retry_hint}"
    tool_msgs = [m for m in client.calls[-1] if m.get("role") == "tool"]
    assert tool_msgs and '"ok": true' in str(tool_msgs[-1].get("content")), tool_msgs


def test_risk_gate_reports_a_rejection_and_never_executes(hub) -> None:
    """拒绝要**如实写进观测**（`rejected`），且绝不执行。

    与放行那条对称：观测只在这一条消息里，模型据此知道"这条路被用户否了"。
    """
    observation, events, _ = run_turn_answered(
        hub, make_call("probe_dangerous", {"text": "对象I"}, call_id="c-g2b"),
        approve=False, reason="别动生产", conv="conv-g2b")

    assert observation["status"] == "rejected", observation
    assert "别动生产" in str(observation.get("summary") or "") or not EXECUTED
    assert not EXECUTED, "拒绝之后不该执行"
    settled = [e for e in events if e["type"] == "confirm_resolved"]
    assert settled and settled[0]["approved"] is False, settled


def test_risk_gate_timeout_ends_the_turn_without_executing(hub) -> None:
    """**超时 = 未同意**：绝不等不到答复就默认放行（这是整条确认门的底线）。"""
    observation = run_turn(hub, make_call("probe_dangerous", {"text": "对象J"}), wait=0.05)
    assert observation["status"] == "timeout", observation
    assert not EXECUTED, "超时之后执行了——这条是安全底线"
    statuses = [i["status"] for i in approvals.pending_store().list("")]
    assert statuses == ["expired"], statuses


def test_approval_is_one_shot(hub) -> None:
    """认领是**一次性**的：批准一次只能执行一次，第二次必须重新确认。

    否则"批准一次、重放多次"就成立了——那正是令牌语义要防的东西。
    """
    call = make_call("probe_dangerous", {"text": "对象B"})
    observation, _, _ = run_turn_answered(hub, call, conv="conv-oneshot")
    assert observation["status"] == "ok"
    assert EXECUTED == ["对象B"]

    # 第二次：**没人再放行** ⇒ 不许复用上一次那条批准
    second = run_turn(hub, call, conv="conv-oneshot-2")
    assert second["status"] == "timeout", f"不该复用那次批准（status={second['status']}）"
    assert EXECUTED == ["对象B"], "不该第二次执行"


def test_claim_is_bound_to_argument_fingerprint(hub) -> None:
    """指纹绑定：**换了参数**必须重新确认（这是确认门的全部意义）。

    直接打在 `claim_approved` 上（不再借道对话循环）：那条 5 秒的等待窗口
    让"先超时、再批准"变得不可能，而这条要验的本来就是认领的**匹配规则**。
    """
    store = approvals.pending_store()
    spec = hub.get("probe_dangerous")
    assert spec is not None
    same, other = Arg(text="对象C"), Arg(text="对象D")

    item = store.request(spec, same)
    assert store.approve(item.id) is not None
    assert store.claim_approved(spec, other) is None, "换参数不该被放行"
    assert store.claim_approved(spec, same) is not None, "同参数应当被认领"


def test_claim_is_bound_to_tool_name(hub) -> None:
    """认领按**工具名**匹配：批准 A 工具不能放行 B 工具。"""
    store = approvals.pending_store()
    dangerous = hub.get("probe_dangerous")
    secret = hub.get("probe_secret")
    assert dangerous is not None and secret is not None

    item = store.request(dangerous, Arg(text="x"))
    assert store.approve(item.id) is not None
    assert store.claim_approved(secret, SecretArg(token="x")) is None
    assert store.claim_approved(dangerous, Arg(text="x")) is not None


def test_expired_approval_is_not_claimable(hub) -> None:
    """过期的批准不可认领（5 分钟窗口是承重的）。"""
    store = approvals.pending_store()
    spec = hub.get("probe_dangerous")
    assert spec is not None
    args = Arg(text="对象E")

    item = store.request(spec, args)
    approved = store.approve(item.id)
    assert approved is not None

    # 把令牌有效期推到过去，再触发一次过期清理。
    # ⚠️ 必须用一个**真值**时间戳：`_expire_locked` 的条件是
    # `item.status == "approved" and item.token_expires_at and now > ...`，
    # 写 `0` 会因为 falsy 而被守卫跳过（本断言第一版就是这么写错的——测试错，不是实现错）。
    approved.token_expires_at = 1
    assert store.claim_approved(spec, args) is None, "过期的批准不该被认领"


def test_rejected_approval_is_not_claimable(hub) -> None:
    """拒绝之后不可认领——模型必须看到"被拒"而不是"再试就过"。"""
    store = approvals.pending_store()
    spec = hub.get("probe_dangerous")
    assert spec is not None
    args = Arg(text="对象F")

    item = store.request(spec, args)
    assert store.reject(item.id, "不要动生产") is not None
    assert store.claim_approved(spec, args) is None


def test_consumed_status_is_recorded(hub) -> None:
    """认领后状态是 `consumed`（不是 `approved`）——否则"用过了"看不出来。"""
    call = make_call("probe_dangerous", {"text": "对象G"})
    observation, events, _ = run_turn_answered(hub, call, conv="conv-consumed")
    assert observation["status"] == "ok"

    pending_id = next(e["pendingId"] for e in events if e["type"] == "approval_required")
    statuses = {i["id"]: i["status"] for i in approvals.pending_store().list("")}
    assert statuses[pending_id] == "consumed", statuses


# ---------------------------------------------------------------- 顺带修掉的既有缺陷

def test_fingerprint_uses_unredacted_args(hub) -> None:
    """参数名命中 `_REDACT_KEYS` 时，pending **仍应被复用**（不再每次新建）。

    改造前 `request()` 用 `_fingerprint(item.tool, item.args)` 现算，而 `item.args`
    是 `redact_args()` 之后的副本 —— 参数名叫 `token` 时两边指纹不同，
    于是"同一工具同一参数复用"的逻辑**静默失效**，每次调用都新建一条 pending。
    """
    hub2 = hub
    spec = hub2.get("probe_secret")
    assert spec is not None
    args = SecretArg(token="abc")

    first = approvals.pending_store().request(spec, args)
    second = approvals.pending_store().request(spec, args)
    assert first.id == second.id, "同一参数应复用同一条 pending（指纹被打了码就不相等了）"
    assert len(approvals.pending_store().list("pending")) == 1
