r"""文档层健康检查进 CI（断链 / 幻影坑号 / 代码块误吞正文）。

## 这些用例守的是什么

`tools/check_doc_links.py` 是一个**脚本**，而脚本不会自己跑。这条门禁把它变成
"改坏文档会红"的判据 —— 与 `gen_tools_doc.py` / `verify_packaging.py` 同一形态。

★ 为什么值得一条门禁：文档层的缺陷**全是静默的**。断链照常渲染成蓝色、
指向已删除文档的引用照常像"有依据"。而本仓的入口文件（`AGENTS.md`）
逐字引用这些文档 —— **它写错的数字会被逐字继承**。

## 本文件同时钉住"检查器自己没坏"

★ 这一点比链接本身重要。第一版的检查器报出 **789 处**问题，其中 700+ 是假的
（vendor 手册的 URL 编码链接没解码）—— 而**假阳性会把真缺陷埋掉**：
真正的 3 处幻影引用当时就埋在 700 条噪音里，没人会一条条看完。

所以我在这里对检查器**自己的性质**加了断言：
- 它认识存档目录，且**每一条排除都写得出理由**；
- 它剥代码块之后**围栏必须成对**（不成对 = 它从此不再检查该文件剩余部分）；
- 它真的能抓到"散文被吞进代码块"（那类缺陷让块内的链接彻底不被检查）。
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _checker():
    """按文件路径加载那个脚本（`tools/` 不是包，不能 import）。"""
    spec = importlib.util.spec_from_file_location(
        "check_doc_links", ROOT / "tools" / "check_doc_links.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_living_documents_have_no_broken_links_or_phantom_refs() -> None:
    """★ 判据本体：活文档里断链 / 幻影坑号**必须为零**。

    红了怎么处置 —— 按缺陷的种类分：

    | 现象 | 处置 |
    |---|---|
    | 断链指向一个**还在**的文件（改了名 / 挪了目录） | 改链接。这是最常见的一种 |
    | 那是一个**存档**（vendor / 带日期的取证报告） | 加到 `ARCHIVES` 里，**并写明理由**。★ 不要改存档里的链接 —— 那是改写历史 |
    | 幻影坑号（`PITFALLS #NN` 不存在） | 改成真实编号，或删掉那个引用 |
    """
    checker = _checker()
    problems = checker.scan()
    detail = "\n".join(f"  [{kind}] {item}" for kind, items in problems.items() for item in items)
    assert not problems, f"文档层有 {sum(len(v) for v in problems.values())} 处待处置：\n{detail}"


def test_every_excluded_archive_actually_exists_and_has_a_reason() -> None:
    """★ 排除项必须**真实存在**且**写得出理由** —— 排除是这份检查最危险的一步。

    `ARCHIVES` 里的每一条都让对应目录**永久不被检查**。所以：
    - 路径必须真的存在（写错一个字符 = 静默地什么都没排除，或者排除错目录）；
    - 每一条都必须在常量下面有文档串说明理由（这是本仓对"清单"的一贯要求：
      `MAX_TOTAL_TOOLS` / `PARALLEL_READ_MAX` 都写了"为什么是这个数"）。

    ★ 反过来说：**往 `ARCHIVES` 里加一条之前，先问"改了它是改写历史吗"**。
    只有"带日期的证据"和"逐字存档"两类够格；"这个文件我懒得修"不够。
    """
    checker = _checker()
    assert checker.ARCHIVES, "排除清单空了 —— 那说明要么漏了存档，要么常量被改名"
    for entry in checker.ARCHIVES:
        assert (ROOT / entry).exists(), f"ARCHIVES 里的路径不存在：{entry}"
    doc = checker.__doc__ or ""
    assert "存档" in doc and "历史证据" in doc, "排除清单的理由没写在模块文档串里"


def test_the_checker_refuses_to_silently_stop_checking() -> None:
    """★★ 检查器**不许静默地少检查**：围栏不成对时必须抛异常。

    这一条防的是一个真实发生过的形态：围栏正则漏认了某种写法（例如引用块里的
    `> ``` `），于是"开"没被认出、"关"被认成"开"，**状态从此翻转** ——
    文件剩下的部分全被当成代码块剥成空格，**里面的链接一个都不再检查，而工具报 PASS**。

    ⇒ 判据是"剥完之后围栏必须成对"。不成对 = 检查器的 bug，**必须大声失败**。
    """
    checker = _checker()
    with pytest.raises(checker.UnbalancedFence):
        checker.strip_code("```\n没有闭合的围栏\n")


def test_swallowed_prose_is_reported() -> None:
    """★ 代码块**里面**的 Markdown 链接要报出来（疑似正文被吞）。

    实测（2026-09-24）：`docs/CODEBASE_OVERVIEW.md` 第 70 行开的目录树直到第 **116** 行
    才闭合，于是 100–113 行那几段备注**渲染成了等宽代码**，其中的
    `[FACTORY-PLATFORM-COGNITION.md](…)` **根本不显示为链接**。

    ★ 这种形态很难自己发现：**文档照常渲染、没有报错**，只有盯着看才发现"那一段字号不对"。
    而它同时有一个更坏的后果：**块内的链接彻底不被检查**（剥离代码块是对的，
    但它把这类缺陷一起藏了）。所以这里要**反过来**报它。
    """
    checker = _checker()
    stripped, inside = checker.strip_code("```\n[x](docs/there.md)\n```\n")
    assert inside == [(2, "docs/there.md")], f"代码块里的链接没被收集：{inside}"
    assert "there.md" not in stripped, "代码块没有被剥掉"


def test_inline_code_spans_with_n_backticks_are_stripped() -> None:
    """★ 行内代码段的定界符是 **N 个反引号配 N 个**，不是固定一个。

    实测（2026-09-24，就在本轮）：我在 PITFALLS 里引用一段正则时用了**双反引号**定界
    （因为原文里有反引号），而检查器只认单个 —— 于是 ` `` ` 被匹配成"**空**代码段"、
    里面的 `[［\\[]([^\\]］]+)` **露在正文里**，检查器把它当成**一条断链**报出来。

    ★ 这已经是"喊狼来了"的**第四次**（前三次：URL 编码、正则被当链接、`已删除`
    匹配不上 `已被删除的`）。所以这里把它钉成一条判据 —— **误报是这份检查最主要的失败模式**。
    """
    checker = _checker()
    stripped, _ = checker.strip_code("前面 ``修改状态为[［\\[]([^\\]］]+)`` 后面\n")
    assert "修改状态为" not in stripped, "双反引号里的内容没有被剥掉"
    assert "前面" in stripped and "后面" in stripped, "剥的时候把正文一起吃掉了"


def test_no_decision_record_directory_comes_back() -> None:
    """★★ 决策记录不单独建目录（2026-10-22 用户定稿）。

    这条是**反向守卫**：它保证"决策文档目录"不会在某次提交里悄悄长回来。
    判据只看目录是否存在 —— 不存在就通过，出现就让提交红。
    """
    assert not (ROOT / "docs" / "adr").exists(), (
        "docs/adr 不应存在：决策写进 CORE-ARCHITECTURE.md 与 CHANGELOG.md，"
        "不再单独建决策文档目录。"
    )