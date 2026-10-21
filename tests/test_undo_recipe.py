r"""危险操作的可撤销性声明（计划 §8.3）。

## 这一节修的是什么

计划 §8.3 要求「写工具在 `audit_extra` 里带**反向操作配方**」，并把它变成一条规矩：
「接第一个"不可逆写"工具时，它**必须**声明 `Risk.DANGEROUS` 并**同时带上反向配方**」。

★ 问题在于：**今天全仓 0 个 `DANGEROUS` 工具**。只按那句话实现的话，这个字段在第一个
DANGEROUS 工具出现之前**没有任何调用方** —— 而本仓对这件事有明确态度
（`summary_fn` 的文档串）：「零调用方的扩展点是**下一次重构里最容易被无声改坏的东西**」。

⇒ 口径落到**所有会改状态的工具**（`risk is not READ`）。2026-10-19 内置浏览器下线后
产品里暂时没有真实 WRITE 工具，本文件的装配期契约由合成 `ToolSpec` 覆盖。

## 本文件钉住的四件事

| 断言 | 它防的是 |
|---|---|
| 会改状态的工具**没声明** ⇒ 装配期报错 | "做错了怎么办"这个问题**从来没有被问过** |
| `UNDOABLE` 必须有配方、配方必须有 `UNDOABLE` | 「能撤」变成一句没有指令的空话，或者反过来 |
| 配方**真的落进 `agent_audit.meta`** | 声明了、也算出来了，但**没人能查到** |
| 配方抽取失败**不影响调用**，但留一条说明 | 事后"没有配方"与"有配方但没抽出来"看起来一样 |

★ **幂等不是回滚**（计划原文）：幂等层防的是"重复执行同一次调用"，
防不了"这一次本来就做错了"。所以"再跑一遍就好了"**不是** `REVERSIBLE` 的理由 ——
这条写在 `Reversibility` 的文档串里，也写在下面那条用例里。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel

from workbench import approvals, db
from workbench import plugin_api as pa


@pytest.fixture()
def ready():
    db.connect()
    return db


class Args(BaseModel):
    account_id: str = pa.Arg("账号", default="acct-1")


def _spec(**kw) -> pa.ToolSpec:
    async def handler(args, ctx):  # pragma: no cover —— 本文件验的是声明，不是执行
        return "{}"

    base = dict(name="probe_w", description="写动作", args_model=Args, handler=handler,
                risk=pa.Risk.WRITE, trust=pa.Trust.LOCAL, timeout_s=5)
    base.update(kw)
    return pa.ToolSpec(**base)


# ---------------------------------------------------------------- 装配期强制


def test_a_mutating_tool_without_a_reversibility_declaration_is_refused() -> None:
    """★ **会改状态的工具必须回答"做错了能不能撤"。**

    这一条与 `trust` 是**同一种纪律**：它**没有安全的缺省** ——
    缺省成 `reversible` 等于静默地断言"做错了也没关系"，
    而那句话在出事之前**没有任何地方会反驳它**。

    报错文案要说清**三个选项**（给配方 / 明说撤不了 / 确实无残留），
    否则人会随手填一个最省事的把它糊过去。
    """
    problems = _spec().valid()
    assert problems, "会改状态的工具没声明 reversibility 竟然通过了"
    assert "能不能撤" in problems[0]
    assert "undo_recipe" in problems[0] and "irreversible" in problems[0], (
        "报错要给出三条出路，而不只是说「缺一个字段」"
    )


def test_undoable_without_a_recipe_is_refused() -> None:
    """「能撤」必须落成一条**具体指令** —— 否则事后没有人知道该怎么撤。"""
    problems = _spec(reversibility=pa.Reversibility.UNDOABLE).valid()
    assert problems and "undo_recipe" in problems[0]
    assert "具体指令" in problems[0]


def test_a_recipe_without_the_undoable_declaration_is_refused() -> None:
    """反向矛盾也要拦：声明成 `REVERSIBLE` 却给了配方 ⇒ 读的人以为有一条配方可依。"""
    problems = _spec(reversibility=pa.Reversibility.REVERSIBLE,
                     undo_recipe=lambda args: {"action": "close"}).valid()
    assert problems and "自相矛盾" in problems[0]


def test_a_read_only_tool_does_not_need_it() -> None:
    """★ 反向断言：**只读工具不该被这条规则波及**。

    只有正向断言的话，一个"所有工具都必须声明"的实现也能过 ——
    而那个实现会让**每一个只读工具**都要回答一个与它无关的问题，
    于是这条声明的信号被稀释成"每行都要填的东西"（那时它会和没有一样）。
    """
    assert _spec(risk=pa.Risk.READ).valid() == []


def test_every_real_mutating_tool_declares_reversibility() -> None:
    """★ **真实工具面上，每一个会改状态的工具都声明了。**

    这条是**对着运行期中枢**核的：谁加了一个写工具却忘了回答那个问题，
    装配期就会报错（`valid()`），而这条断言让"装配期报错被跳过"也没法蒙混过去
    （坏插件会被 `load_all` 吞成一条 failure，启动照常）。
    """
    from workbench.harness.tools import default_registry

    hub = default_registry()
    assert hub.failures == [], f"装配有失败：{hub.failures}"
    mutating = [s for s in hub.specs() if s.risk is not pa.Risk.READ]
    # 2026-10-19：内置浏览器下线后产品里暂时没有 WRITE 工具，因此这里允许为空。
    # 本文件其余用例用合成 spec 覆盖 UNDOABLE / IRREVERSIBLE 的装配期契约。
    for spec in mutating:
        assert spec.reversibility is not None, f"{spec.name} 没声明 reversibility"
        if spec.reversibility is pa.Reversibility.UNDOABLE:
            assert spec.undo_recipe is not None, f"{spec.name} 说能撤却没给配方"
        if spec.reversibility is pa.Reversibility.IRREVERSIBLE:
            # 撤不了是可以的，但**必须是显式声明的**（界面要据此提醒用户）
            assert spec.undo_recipe is None


# ---------------------------------------------------------------- 落进审计


def test_the_recipe_lands_in_the_audit_meta() -> None:
    """★ 配方**真的落进 `agent_audit.meta`**（不是只算出来又丢掉）。

    声明了、也算出来了，但没人能查到 —— 那是"写了等于没写"。
    `_extra` 是审计 `meta` 的**唯一产出点**，所以这条断言直接打在那里。
    """
    spec = _spec(reversibility=pa.Reversibility.UNDOABLE,
                 undo_recipe=lambda args: {"action": "close", "id": args.account_id},
                 audit_extra=lambda args: {"环境": "prod"})
    meta = pa._extra(spec, Args())
    assert meta["reversibility"] == "undoable"
    assert meta["undo"] == {"action": "close", "id": "acct-1"}, meta
    assert meta["环境"] == "prod", "audit_extra 与配方必须**共存**（不是二选一）"


def test_a_failing_recipe_does_not_break_the_call_but_leaves_a_trace() -> None:
    """★ 配方抽取失败**不影响调用**，但要**留一条说明**。

    失败时静默丢掉的话，审计里"没有配方"与"有配方但没抽出来"**看起来完全一样** ——
    而两者的处置完全不同（前者是这个操作本来就撤不了，后者是我们没记下来）。
    """
    def boom(args):
        raise RuntimeError("抽不出来")

    spec = _spec(reversibility=pa.Reversibility.UNDOABLE, undo_recipe=boom)
    meta = pa._extra(spec, Args())
    assert meta["reversibility"] == "undoable"
    assert "undoError" in meta and "抽不出来" in meta["undoError"]
    assert "undo" not in meta


def test_the_recipe_ends_up_in_the_audit_row(ready) -> None:
    """★★ 端到端：**一次真实的写调用之后，审计那一行里能查到配方。**

    前面几条验的都是"函数算对了"。这一条验的是**它有没有真的落库** ——
    两者是两件事，而本仓在这上面栽过（PITFALLS #42：`audit.record` 一度
    **没有任何调用方**，E4 层在文档上写着"已实现"而实际永远空转）。
    """
    approvals.reset_store()
    hub = pa.ToolHub()
    approvals.install(hub)

    async def handler(args, ctx):
        return json.dumps({"ok": True})

    hub.add_plugin(pa.Plugin(id="probe", title="探针", tools=[pa.ToolSpec(
        name="probe_create", description="造一个东西", args_model=Args, handler=handler,
        risk=pa.Risk.WRITE, trust=pa.Trust.LOCAL, timeout_s=5,
        reversibility=pa.Reversibility.UNDOABLE,
        undo_recipe=lambda args: {"action": "probe_delete", "id": args.account_id},
        audit_extra=lambda args: {"对象": args.account_id},
    )]))

    async def run():
        # 风险门要令牌 —— 先走一次拿到 pending，批准，再执行
        first = await hub.execute("probe_create", {"account_id": "acct-9"},
                                  pa.ToolContext(source="agent"))
        assert first.status is pa.ToolStatus.NEEDS_APPROVAL, first.status
        store = approvals.pending_store()
        assert store.approve(first.pending_id or "") is not None, "批准失败"
        token = store.get(first.pending_id or "")
        return await hub.execute(
            "probe_create", {"account_id": "acct-9"},
            pa.ToolContext(source="agent", approval_token=getattr(token, "token", "") or ""),
        )

    invocation = asyncio.run(run())
    assert invocation.status is pa.ToolStatus.OK, (invocation.status, invocation.content)

    row = db.query_one("SELECT meta FROM agent_audit WHERE tool = ? ORDER BY created_at DESC",
                       ("probe_create",))
    assert row, "这次调用没有进审计表"
    meta = json.loads(row["meta"] or "{}")
    assert meta.get("reversibility") == "undoable", meta
    assert meta.get("undo") == {"action": "probe_delete", "id": "acct-9"}, (
        f"配方没有落进审计：{meta}"
    )
