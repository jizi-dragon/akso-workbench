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
    problems = gen._claim_problems(len(catalog["plugins"]), len(catalog["tools"]))
    assert not problems, "文档里的工具/插件数字与实际不符：" + "；".join(problems)


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
