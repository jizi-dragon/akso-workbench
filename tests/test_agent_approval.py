"""确认门在**对话链路**里可用（P0-A 的回归）。

## 缺陷原状（2026-09-20 实测复现，见 docs/AGENT-TOOL-AUDIT.md P0-A）

前端刻意**不自动重发**被拦下的那次调用（"放行是人做的决定"），所以用户放行后会让模型
**再试一次**。但模型的重试走 `harness/loop.py`，**那里拿不到令牌**——令牌只回给了批准它的
那一次 HTTP 响应。改造前的结果：

    轮1 被拦（pendingId=A）→ 人放行 → 轮2 重试 → **仍被拦，且新建了 pendingId=B** → 工具从未执行

⇒ `accounts_browser_open` / `accounts_browser_close` / `factory_create`
（**全部 3 个写 / 危险工具**）在对话链路里永远无法执行。

## 修法与安全性

`PendingStore.claim_approved()`：无令牌时按「工具 + **参数指纹**」认领一条**已批准**的条目，
**一次性**（认领后置 `consumed`）。门槛与令牌完全一致，只是凭据从令牌换成了服务端内存里的事实。

## 本文件是**反证过**的

`tools/verify_approval_tests.py` 会把 `claim_approved()` 改回缺陷版（直接返回 None），
期望本文件的两条核心断言立刻变红。
"""

from __future__ import annotations

import asyncio
import json

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


def run_turn(hub, call: dict, conv: str = "conv-approval") -> dict:
    """跑一轮 Agent 循环，返回最后那个 observation 的 invocation。"""
    client = ScriptedClient([call])
    events = asyncio.run(_collect(run_agent_loop(
        [{"role": "user", "content": "继续"}], client, hub,
        AgentConfig(system_prompt="s", max_steps=4), conversation_id=conv,
    )))
    return next(e["invocation"] for e in events if e["type"] == "observation")


# ---------------------------------------------------------------- 核心：放行后重试必须真的执行

def test_approved_retry_actually_executes_in_the_chat_flow(hub) -> None:
    """**P0-A 的核心断言**：放行 → 让模型重试同一次调用 → **工具真的执行了**。

    这条在修复前必然 FAIL（重试仍返回 `needs_approval`，`EXECUTED` 为空）。
    """
    call = make_call("probe_dangerous", {"text": "对象A"})

    first = run_turn(hub, call)
    assert first["status"] == "needs_approval"
    assert not EXECUTED, "未确认前不该执行"

    # 观测里不带 pendingId（那是 `approval_required` 事件的字段），
    # 所以这里从待确认清单取——与界面拿到的是同一条
    pending_id = approvals.pending_store().list("pending")[0]["id"]
    assert approvals.pending_store().approve(pending_id) is not None

    second = run_turn(hub, call)
    assert second["status"] == "ok", (
        f"放行后重试仍被拦（status={second['status']}）——"
        "对话链路拿不到令牌，必须靠 claim_approved 认领")
    assert EXECUTED == ["对象A"], f"工具没有真的执行：{EXECUTED}"


def test_approval_is_one_shot(hub) -> None:
    """认领是**一次性**的：批准一次只能放行一次，第二次重试必须重新确认。

    否则"批准一次、重放多次"就成立了——那正是令牌语义要防的东西。
    """
    call = make_call("probe_dangerous", {"text": "对象B"})
    run_turn(hub, call)
    pending_id = approvals.pending_store().list("pending")[0]["id"]
    approvals.pending_store().approve(pending_id)

    assert run_turn(hub, call)["status"] == "ok"
    assert EXECUTED == ["对象B"]

    third = run_turn(hub, call)
    assert third["status"] == "needs_approval", "第二次重试不该复用那次批准"
    assert EXECUTED == ["对象B"], "不该第二次执行"


def test_claim_is_bound_to_argument_fingerprint(hub) -> None:
    """指纹绑定：**换了参数**必须重新确认（这是确认门的全部意义）。"""
    run_turn(hub, make_call("probe_dangerous", {"text": "对象C"}))
    pending_id = approvals.pending_store().list("pending")[0]["id"]
    approvals.pending_store().approve(pending_id)

    other = run_turn(hub, make_call("probe_dangerous", {"text": "对象D"}))
    assert other["status"] == "needs_approval", "换参数不该被放行"
    assert not EXECUTED


def test_claim_is_bound_to_tool_name(hub) -> None:
    """认领按**工具名**匹配：批准 A 工具不能放行 B 工具。"""
    run_turn(hub, make_call("probe_dangerous", {"text": "x"}))
    pending_id = approvals.pending_store().list("pending")[0]["id"]
    approvals.pending_store().approve(pending_id)

    other = run_turn(hub, make_call("probe_secret", {"token": "x"}))
    assert other["status"] == "needs_approval"
    assert not EXECUTED


def test_expired_approval_is_not_claimable(hub, monkeypatch) -> None:
    """过期的批准不可认领（5 分钟窗口是承重的）。"""
    call = make_call("probe_dangerous", {"text": "对象E"})
    run_turn(hub, call)
    pending_id = approvals.pending_store().list("pending")[0]["id"]
    item = approvals.pending_store().approve(pending_id)
    assert item is not None

    # 把令牌有效期推到过去，再触发一次过期清理。
    # ⚠️ 必须用一个**真值**时间戳：`_expire_locked` 的条件是
    # `item.status == "approved" and item.token_expires_at and now > ...`，
    # 写 `0` 会因为 falsy 而被守卫跳过（本断言第一版就是这么写错的——测试错，不是实现错）。
    item.token_expires_at = 1
    assert run_turn(hub, call)["status"] == "needs_approval"
    assert not EXECUTED


def test_rejected_approval_is_not_claimable(hub) -> None:
    """拒绝之后不可认领——模型必须看到"被拒"而不是"再试就过"。"""
    call = make_call("probe_dangerous", {"text": "对象F"})
    run_turn(hub, call)
    pending_id = approvals.pending_store().list("pending")[0]["id"]
    approvals.pending_store().reject(pending_id, "不要动生产")

    assert run_turn(hub, call)["status"] == "needs_approval"
    assert not EXECUTED


def test_consumed_status_is_recorded(hub) -> None:
    """认领后状态是 `consumed`（不是 `approved`）——否则"用过了"看不出来。"""
    call = make_call("probe_dangerous", {"text": "对象G"})
    run_turn(hub, call)
    pending_id = approvals.pending_store().list("pending")[0]["id"]
    approvals.pending_store().approve(pending_id)
    run_turn(hub, call)

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
