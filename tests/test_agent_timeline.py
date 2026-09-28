r"""「本次运行」时间线（计划 §6.2）。

## 它守的是什么

§6.1 已经把 `agent_run` / `agent_span` 落了库（迁移 24），而 **§6.2 之前没有任何 API 读它们**
—— 数据在库里、只有 `sqlite3` 命令行看得见。★ 这正是本仓 PITFALLS #106 的形态：
**写了一半的可观测性不是可观测性**（"落了库"与"能被看见"是两件事，
而前者不会因为后者缺失而报错）。

`GET /api/agent/runs` 是那一半。本文件的断言全部打在**接口契约**上：

| 断言 | 它防的是 |
|---|---|
| 空结果返回 **200 + 空数组**，不是 404 | "这次还没跑过"与"查询坏了"被前端当成同一件事 |
| run 与 span **一次返回** | 前端出现"run 到了、span 没到"的半截渲染（看起来像数据缺失） |
| `attrs.summary` 由**产生它的地方**标 | 前端/路由自己按 `kind == "chat"` 判断 ⇒ 两边各写一份语义 |
| span 按 `step` 升序 | 时间线顺序不确定（同毫秒的时间戳分不出先后） |
| 会话过滤真的生效 | 面板显示的是"这台机器上最近的运行"，而不是"本次" |
"""

from __future__ import annotations

import pytest

from workbench import db
from workbench.api import routes_agent
from workbench.harness import trace


@pytest.fixture()
def ready():
    db.connect()
    return db


def _seed(conversation_id: str, *, steps: int = 2) -> str:
    """造一次真实的 run（走 `RunRecorder`，不手写 SQL —— 手写会漏掉列）。"""
    recorder = trace.RunRecorder(conversation_id, "deepseek-flash")
    for i in range(steps):
        recorder.span(trace.SPAN_MODEL, "deepseek-flash", started=0.0, duration_ms=10 + i,
                      attrs={"messages": 2 + i, "usage": {"prompt_tokens": 100}})
    recorder.finish({"prompt_tokens": 300, "prompt_cache_hit_tokens": 240})
    return recorder.run_id


def test_an_empty_result_is_a_200_with_an_empty_list(ready) -> None:
    """★ **空结果不是错误。**

    "这次对话还没跑过"与"查询坏了"必须分得开。让前者返回 404 会训练前端
    把两者当同一件事处理 —— 而那意味着**一次真正的后端故障会显示成"还没有运行记录"**，
    用户会一直等一个永远不会出现的面板。
    """
    out = routes_agent.runs(conversationId="从来没跑过的会话")
    assert out["runs"] == []
    assert "traceWriteFailures" in out, "自检信息要一起返回（写失败时轨迹是缺的）"


def test_runs_come_back_with_their_spans(ready) -> None:
    """★ run 与 span **一次返回** —— 否则前端会出现"这次运行没有任何步骤"的中间态。"""
    conv = "timeline-1"
    _seed(conv, steps=3)

    out = routes_agent.runs(conversationId=conv)
    assert len(out["runs"]) >= 1
    run = out["runs"][0]
    assert run["conversationId"] == conv
    assert run["steps"] == 3, f"步数不对：{run['steps']}"
    assert len(run["spans"]) == 3, f"span 没有随 run 一起回来：{run['spans']}"


def test_spans_are_ordered_by_step(ready) -> None:
    """★ 按 `step` 升序，不按时间戳。

    `started_at` 是毫秒 —— 同一毫秒内的两步在时间戳上**分不出先后**，
    于是时间线的顺序会变成"看运气"。`step` 是 run 内单调递增的序号。
    """
    conv = "timeline-2"
    _seed(conv, steps=4)
    run = routes_agent.runs(conversationId=conv)["runs"][0]
    assert [s["step"] for s in run["spans"]] == [1, 2, 3, 4]


def test_the_conversation_filter_actually_filters(ready) -> None:
    """★ 会话过滤真的生效 —— 面板是「**本次**运行」。

    不生效的形态很难看：它会显示**别的会话**的轨迹，而两条时间线在界面上长得一样 ——
    用户会以为"我这次跑了这些步骤"，实际那是另一个对话的。
    """
    _seed("timeline-a", steps=2)
    _seed("timeline-b", steps=5)

    a = routes_agent.runs(conversationId="timeline-a")["runs"]
    b = routes_agent.runs(conversationId="timeline-b")["runs"]
    assert {r["conversationId"] for r in a} == {"timeline-a"}
    assert {r["conversationId"] for r in b} == {"timeline-b"}
    assert a[0]["steps"] == 2 and b[0]["steps"] == 5


def test_the_summary_span_declares_itself(ready) -> None:
    """★★ 摘要那次调用**自己声明语义**（`attrs.summary = True`），路由只透传。

    计划 §6.2 要求「**摘要那次调用单独标红** —— 它是几乎必然全 miss 的开销，
    `cache.py` 的 `summary_calls` 就是为它存在的」。

    ★ 标记必须由**产生它的地方**写：如果前端或路由按 `kind == "chat"` 自己判断，
    就成了"两边各写一份语义"，而**改了一边不会让另一边变红**。
    这条断言直接调 `_SpanClient.chat`，验的是那个产生点。
    """
    import asyncio

    class FakeInner:
        model = "deepseek-flash"
        last_usage = {"prompt_tokens": 900, "prompt_cache_hit_tokens": 0}

        async def chat(self, messages, temperature=0.7, max_tokens=None, json_mode=False):
            return "摘要"

    conv = "timeline-summary"
    recorder = trace.RunRecorder(conv, "deepseek-flash")

    from workbench.harness.loop import _SpanClient

    asyncio.run(_SpanClient(FakeInner(), recorder).chat([{"role": "user", "content": "x"}]))
    recorder.finish({})

    run = routes_agent.runs(conversationId=conv)["runs"][0]
    summary = [s for s in run["spans"] if s["attrs"].get("summary")]
    assert len(summary) == 1, f"摘要那次调用没有自己声明：{run['spans']}"
    assert summary[0]["attrs"]["usage"]["prompt_cache_hit_tokens"] == 0, (
        "摘要调用的缓存用量要能被看见 —— 「几乎必然全 miss」正是要标红它的理由"
    )
