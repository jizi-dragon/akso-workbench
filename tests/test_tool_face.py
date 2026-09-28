r"""工具面裁剪接线 ＋ 全局上限（计划 §7.2）。

## 这一节修的是什么

`run_agent_loop(plugins=...)` 这个白名单参数**早就在**，而全仓**没有任何调用方传过值** ——
是留白不是能力。同时工具 schema 是**每一轮请求**都要发的前缀里最贵的一块
（18 个工具约 2,192 token/轮），数目一旦失控，代价是线性的、静默的、每一次调用都付。

★ 它还是**接 MCP 的前置**（计划 §9：MCP 会让工具数暴涨）。

## ★ 一条与本计划原文不符的实测结论（写在这里，免得下一个人再推一遍）

计划 §7.2 的设计是「前端按**当前页面**传子集：账号页挂 `accounts`+`builtin`，
工厂页挂 `factory`+`builtin`，通用助手页 = 全量」。**实测（2026-09-24）这条在当前架构下不成立**：

| 页面 | 有对话输入区？ |
|---|---|
| `index.html`（主界面） | ✅ 唯一一个 |
| `pages/accounts.html` / `insight.html` / `skills.html` / `sandbox.html` | ❌ 全都没有 |

也就是说：**对话面只有"通用助手"一个**，而它本来就该拿到全量工具（用户在那里能问任何事）。
配置工厂页早已随功能移除。⇒ **"按页面裁剪"没有落点**。

所以这一节交付的是**机制**（`/api/chat/stream` 的 `plugins` 参数 + 全局上限），
而不是"某个页面传某个子集" —— 后者等**出现页面级对话**（或 MCP 落地后工具数真的暴涨到
需要按场景裁剪）时再用。`test_no_tool_page_embeds_a_chat_composer` 把这条实测结论**钉住**：
哪天有人给工具页加了对话，它会红，提醒把那一页的 `plugins` 子集一起加上。
"""

from __future__ import annotations

import asyncio
import pathlib

import pytest
from pydantic import BaseModel

from workbench import db
from workbench import hub as hub_mod
from workbench import plugin_api as pa
from workbench.api import routes_chat
from workbench.harness.tools import default_registry

STATIC = pathlib.Path(__file__).resolve().parents[1] / "workbench" / "static"


@pytest.fixture()
def ready():
    db.connect()
    return db


class Arg(BaseModel):
    n: int = pa.Arg("数字", default=1)


def _spec(name: str) -> pa.ToolSpec:
    async def handler(args: Arg, ctx: pa.ToolContext) -> str:  # pragma: no cover
        return "{}"

    return pa.ToolSpec(name=name, description=f"{name} 说明", args_model=Arg,
                       handler=handler, risk=pa.Risk.READ, trust=pa.Trust.LOCAL,
                       timeout_s=5)


# ---------------------------------------------------------------- 全局上限


def test_the_real_tool_face_is_under_the_cap() -> None:
    """★ 真实工具面必须在上限之内 —— 这条是**警报线**的存在性证明。

    上限不是容量线而是警报线（见 `MAX_TOTAL_TOOLS` 的文档串）：它在工具数开始失控时
    先在 `selfcheck` 上亮起来，而不是等到成本或选择质量已经变差。
    """
    hub = default_registry()
    assert len(hub.specs()) <= pa.MAX_TOTAL_TOOLS
    assert not [f for f in hub.failures if f.get("kind") == "tool_budget"]


def test_exceeding_the_cap_records_a_failure_without_blocking() -> None:
    """★ 超限 ⇒ **记一条 failure，但不拦装配**（计划 §7.2 的原文口径）。

    与本仓对坏插件的既有语义一致：**静默降级比崩溃更难查，但崩溃比降级更糟**。
    所以超限时工具**照常可用**（用户装的东西不会突然失效），只是现象变得可见。
    """
    hub = pa.ToolHub()
    hub.add_plugin(pa.Plugin(id="big", title="大插件",
                             tools=[_spec(f"probe_{i}") for i in range(pa.MAX_TOTAL_TOOLS + 1)]))

    assert len(hub.specs()) == pa.MAX_TOTAL_TOOLS + 1, "超限把工具拦掉了——应当照常可用"
    budget = [f for f in hub.failures if f.get("kind") == "tool_budget"]
    assert budget, f"超限没有留下 failure：{hub.failures}"
    assert str(pa.MAX_TOTAL_TOOLS) in budget[0]["error"]
    assert "每一轮请求" in budget[0]["error"], "错误信息要说清「为什么这个上限存在」"


def test_the_budget_failure_is_idempotent() -> None:
    """★ **幂等**：反复重建不重复记账。

    `_rebuild` 在装配、卸载、禁用、装 Skill 时都会被调用。每次都 append 的话
    `failures` 会随操作次数**线性增长**，而它的消费方（selfcheck / 前端）
    看的是"有没有问题"，不是"被提醒过几次" —— 一条刷屏的告警等于没有告警。
    """
    hub = pa.ToolHub()
    hub.add_plugin(pa.Plugin(id="big", title="大插件",
                             tools=[_spec(f"probe_{i}") for i in range(pa.MAX_TOTAL_TOOLS + 1)]))
    first = len([f for f in hub.failures if f.get("kind") == "tool_budget"])

    for _ in range(5):
        hub._rebuild()  # noqa: SLF001 —— 刻意直调：本用例要验的就是它反复跑

    again = len([f for f in hub.failures if f.get("kind") == "tool_budget"])
    assert first == again == 1, f"重建 5 次之后记了 {again} 条（应当恒为 1）"


def test_dropping_back_under_the_cap_clears_the_failure() -> None:
    """★ 反向断言：**降回上限之内时那条 failure 要消失**。

    只加不清的话，用户禁用掉插件之后 `selfcheck` 仍然红着 ——
    而一个**永远红着的告警**会训练人忽略它（那时它就不再是警报线了）。
    """
    hub = pa.ToolHub()
    hub.add_plugin(pa.Plugin(id="big", title="大插件",
                             tools=[_spec(f"probe_{i}") for i in range(pa.MAX_TOTAL_TOOLS + 1)]))
    assert [f for f in hub.failures if f.get("kind") == "tool_budget"]

    hub.set_plugin_enabled("big", False)
    assert not [f for f in hub.failures if f.get("kind") == "tool_budget"], (
        f"降回上限之内了，告警还留着：{hub.failures}"
    )


def test_selfcheck_exposes_the_budget_failure(monkeypatch) -> None:
    """上限的存在意义是**被看见** —— 所以它必须出现在 `selfcheck` 里。

    （`selfcheck` 已经暴露 `hub.failures`，这条断言钉住"那条 failure 走的是同一条路"，
    免得将来有人给它换个没人读的字段。）
    """
    from workbench.api.routes_agent import selfcheck

    hub = pa.ToolHub()
    hub.add_plugin(pa.Plugin(id="big", title="大插件",
                             tools=[_spec(f"probe_{i}") for i in range(pa.MAX_TOTAL_TOOLS + 1)]))
    monkeypatch.setattr(hub_mod, "_hub", hub)

    out = selfcheck()
    assert out["ok"] is False
    assert any(f.get("kind") == "tool_budget" for f in out["failures"])


# ---------------------------------------------------------------- API 参数


def test_plugins_defaults_to_none_and_none_differs_from_empty() -> None:
    """★ `None`（不裁剪）与 `[]`（一个工具都不给）**不同义** —— 刻意与 `attachmentIds` 相反。

    那边"漏传与传空同义"是对的（空附件条与没有附件条是一回事）；
    这里不同：漏传的语义是"我不关心"，传空的语义是"我明确要求它没有工具"。
    把两者合并，会让**一个写错的调用方静默地把模型的手脚全砍掉**。
    """
    assert routes_chat.ChatBody(message="x").plugins is None
    assert routes_chat.ChatBody(message="x", plugins=[]).plugins == []
    assert routes_chat.ChatBody(message="x").attachmentIds == []


def _record_plugins(monkeypatch) -> dict:
    """把 `run_agent_loop` 换成一个只记录 `plugins` 的假循环。

    ★ 这条断言的对象是"**参数有没有真的传下去**"：一个被接受、被写进文档、
    却在中间某一层悄悄丢掉的参数，是本仓最典型的一类静默失败
    （同族：PIIFALLS #35「前端字段是 Python→JS 的无类型接口」）。
    """
    seen: dict = {}

    class Cfg:
        ready = True
        api_key = "stub"
        model = "deepseek-flash"
        temperature = 0.7

    async def fake_loop(_history, _client, _registry, _config, **kwargs):
        seen.update(kwargs)
        yield {"type": "final", "content": "好"}
        yield {"type": "usage", "usage": {}}

    monkeypatch.setattr(routes_chat, "load_deepseek", lambda: Cfg())
    monkeypatch.setattr(routes_chat, "run_agent_loop", fake_loop)
    monkeypatch.setattr(routes_chat, "default_registry", lambda: pa.ToolHub())
    return seen


def _drain(body) -> list[str]:
    async def run() -> list[str]:
        return [sse async for sse in routes_chat._chat_events(body)]

    return asyncio.run(run())


def test_the_api_forwards_plugins_to_the_loop(ready, monkeypatch) -> None:
    """★ 端到端：`ChatBody.plugins` 真的被传到了循环里。"""
    seen = _record_plugins(monkeypatch)
    _drain(routes_chat.ChatBody(message="问", plugins=["builtin"]))
    assert seen.get("plugins") == ["builtin"], f"plugins 没传到循环：{seen}"


def test_omitting_plugins_means_no_narrowing(ready, monkeypatch) -> None:
    """不传 ⇒ 传下去的是 `None`（全量），而不是 `[]`（全砍）。

    ★ 这条是上一條的反面：只有正向断言的话，一个"永远传 `[]`"的实现也能过。
    """
    seen = _record_plugins(monkeypatch)
    _drain(routes_chat.ChatBody(message="问"))
    assert seen.get("plugins") is None, f"漏传被当成了裁剪：{seen}"


# ---------------------------------------------------------------- 前端：实测结论


def test_no_tool_page_embeds_a_chat_composer() -> None:
    """★★ 把"按页面裁剪在当前架构下不成立"这条实测结论**钉住**。

    计划 §7.2 假设每个工具页各有一个对话面。实测：**只有主界面有输入区**，
    四个工具页（accounts / insight / skills / sandbox）都没有。

    ⇒ 这条用例不是在测产品行为，它在**守一句结论**：哪天有人给工具页加了对话，
    它会红，提醒把那一页的 `plugins` 子集一起加上（否则那一页会拿到全量工具，
    而"按页面裁剪"的设计意图就静默失效了）。
    """
    pages = sorted((STATIC / "pages").glob("*.html"))
    assert pages, "找不到 pages 目录 —— 前端结构变了，这条判据要重写"

    with_composer = [p.name for p in pages
                     if "composer-input" in p.read_text(encoding="utf-8")]
    assert not with_composer, (
        f"这些工具页现在有对话输入区了：{with_composer}。"
        "⇒ 计划 §7.2 的「按页面传 plugins 子集」现在有了落点，"
        "请给它们各自的 chat 调用带上 plugins（见 docs/CORE-AGENT-PLAN.md §7.2）"
    )


def test_plugin_ids_used_anywhere_in_the_frontend_exist_in_the_registry() -> None:
    """★ 防"手抄漂移"：前端里出现的插件 id 必须真的在中枢里。

    计划 §7.2 原本的判据是"断言前端每个页面传的 `plugins` 与 `TOOLS.md` 的插件 id 一致"。
    当前前端**不传** `plugins`（见上一条），所以那条判据无从落地 ——
    但它的**目的**（防止 id 抄错）仍然有效，而且现在就有用：插件 id 也出现在别处
    （例如页面上的说明文案、将来的页面级裁剪）。抄错一个 id 的后果是
    **那一页静默地拿不到任何工具**（白名单不认识它 ⇒ 命中 0 个 ⇒ 模型没有手）。
    """
    real = {s.plugin_id for s in default_registry().specs()}
    assert real, "中枢里一个插件都没有 —— 前提不成立"

    import re

    # 只认"看起来是插件 id 的字面量"：小写字母/数字/._-，且出现在 plugins 语境里
    pattern = re.compile(r"plugins\s*[:=]\s*\[([^\]]*)\]")
    referenced: set[str] = set()
    for path in STATIC.rglob("*.js"):
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            referenced.update(re.findall(r"[\"']([A-Za-z0-9._-]+)[\"']", match.group(1)))

    unknown = sorted(referenced - real)
    assert not unknown, (
        f"前端引用了不存在的插件 id：{unknown}。"
        f"真实的是：{sorted(real)}。抄错的后果是那一页**静默地一个工具都拿不到**"
    )
