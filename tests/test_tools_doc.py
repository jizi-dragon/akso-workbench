"""工具清单治理回归（docs/TOOLS.md 必须与运行期工具中枢一致）。

钉住的是**两个事实源不许漂**：清单块由 `tools/gen_tools_doc.py` 从 `ToolHub` 生成，
而文档里的"N 个工具 / N 个插件"必须等于实测值。

为什么值得一条测试：2026-09-15 插件从 4 变 5、工具从 19 变 20，四处文档全写旧数字，
用户是**自己发现的**（「我不知道有 19 个 tools」）。数字漂移靠人盯不住，靠断言才盯得住。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_generator():
    """按路径加载 tools/gen_tools_doc.py（tools/ 不是包）。"""
    spec = importlib.util.spec_from_file_location("gen_tools_doc", ROOT / "tools" / "gen_tools_doc.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tools_doc_block_matches_runtime_hub() -> None:
    gen = _load_generator()
    from workbench.plugin_api import get_hub

    hub = get_hub()
    doc = gen.DOC.read_text(encoding="utf-8")
    current = gen.extract_block(doc)
    assert current is not None, "docs/TOOLS.md 缺少清单标记（BEGIN/END）"
    assert current == gen.render(hub, hub.catalog()), (
        "docs/TOOLS.md 的清单块与运行期中枢不一致：跑 `python tools/gen_tools_doc.py --write`"
    )


def test_count_claims_in_active_docs_match_runtime() -> None:
    gen = _load_generator()
    from workbench.plugin_api import get_hub

    catalog = get_hub().catalog()
    # **只比 core**：文档承诺的是产品自带能力，与用户装了什么无关（见 gen._core 的说明）
    problems = gen._claim_problems(len(gen._core(catalog, "plugins")),
                                   len(gen._core(catalog, "tools")))
    assert not problems, "文档里的工具/插件数字与实际不符：" + "；".join(problems)


def test_installed_skill_never_turns_the_doc_gate_red() -> None:
    """**目录里多出一个自装 Skill，不该让文档门禁红。**

    这条是那个口径的回归：`catalog()` 返回"当前注册的一切"，而 `render()` 与数字核对
    只许看 `origin=core`。不这么分的话，**任何装过 Skill 的机器跑门禁都会红**，
    而红的原因与文档对不对毫无关系——那种门禁会很快被人无视，比没有更糟。
    """
    gen = _load_generator()
    from pydantic import BaseModel

    from workbench.plugin_api import (
        Plugin,
        PluginOrigin,
        Reversibility,
        Risk,
        ToolSpec,
        Trust,
        get_hub,
        reset_hub,
    )

    async def _noop(args, ctx):  # pragma: no cover —— 只为凑一个可注册的工具
        return "{}"

    class _Args(BaseModel):
        x: str = ""

    try:
        hub = get_hub()
        before_block = gen.render(hub, hub.catalog())
        hub.register_installed(Plugin(
            id="skill:doc-gate-probe", title="探针", version="1.0.0",
            tools=[ToolSpec(name="doc_gate_probe", description="探针工具",
                            args_model=_Args, handler=_noop, risk=Risk.WRITE, reversibility=Reversibility.IRREVERSIBLE,
                            trust=Trust.LOCAL)],
            origin=PluginOrigin.INSTALLED, capability_id="doc-gate-probe", removable=True,
        ))
        after_catalog = hub.catalog()
        assert gen.render(hub, after_catalog) == before_block, (
            "装了 Skill 之后 TOOLS.md 的清单块变了——文档只该反映 core"
        )
        problems = gen._claim_problems(len(gen._core(after_catalog, "plugins")),
                                       len(gen._core(after_catalog, "tools")))
        assert not problems, "自装 Skill 让文档数字核对失败了：" + "；".join(problems)
    finally:
        reset_hub()



def test_tools_doc_is_idempotent() -> None:
    """整段替换必须幂等——曾因 `str.replace("", x)` 把文档炸成 11 万行。"""
    gen = _load_generator()
    from workbench.plugin_api import get_hub

    hub = get_hub()
    doc = gen.DOC.read_text(encoding="utf-8")
    once = gen.splice_block(doc, gen.render(hub, hub.catalog()))
    twice = gen.splice_block(once, gen.render(hub, hub.catalog()))
    assert once == twice
    assert len(once.splitlines()) < 1000, "清单块替换后文档行数异常（疑似空串替换退化）"
