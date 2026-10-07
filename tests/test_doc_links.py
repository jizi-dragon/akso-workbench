r"""文档层健康检查进 CI（目标第 3 件：清理过期、无引用、无意义的文档）。

## 这些用例守的是什么

`tools/check_doc_links.py` 是一个**脚本**，而脚本不会自己跑。这条门禁把它变成
"改坏文档会红"的判据 —— 与 `gen_tools_doc.py` / `verify_packaging.py` 同一形态。

★ 为什么值得一条门禁：文档层的缺陷**全是静默的**。断链照常渲染成蓝色、
指向已删除 ADR 的引用照常像"有依据"。而本仓的入口文件（`AGENTS.md`）
逐字引用这些文档 —— **它写错的数字会被逐字继承**。

## 本文件同时钉住"检查器自己没坏"

★ 这一点比链接本身重要。第一版的检查器报出 **789 处**问题，其中 700+ 是假的
（vendor 手册的 URL 编码链接没解码）—— 而**假阳性会把真缺陷埋掉**：
真正的 3 处幻影引用当时就埋在 700 条噪音里，没人会一条条看完。

所以我在这里对检查器**自己的性质**加了断言：
- 它认识 vendored / 存档目录，且**每一条排除都写得出理由**；
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
    """★ 判据本体：活文档里断链 / 幻影 ADR 引用 / 幻影坑号**必须为零**。

    红了怎么处置 —— 按缺陷的种类分：

    | 现象 | 处置 |
    |---|---|
    | 断链指向一个**还在**的文件（改了名 / 挪了目录） | 改链接。这是最常见的一种 |
    | 引用的 ADR **已退役** | 在那一行（或紧随两行）**说明它已移除**并给取回命令。**不要**把编号删掉了事 —— 读的人需要知道"当时有一条 ADR 支撑这个判断" |
    | 引用的 ADR **从未存在**（例如 0020） | 改成真实的编号，并在原处记一句"这里原先写的是 NNNN，实际落地成了 MMMM" |
    | 那是一个**存档**（vendor / 带日期的取证报告） | 加到 `ARCHIVES` 里，**并写明理由**。★ 不要改存档里的链接 —— 那是改写历史 |
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


def test_retired_markers_match_the_real_phrasings() -> None:
    """★ 退役标记词必须匹配**实际写法** —— 这里踩过一次很难看的坑。

    第一版写的是 `"已删除"`，而正文里是 **"已被删除的工厂 ADR"**：
    `已` 与 `删除` 之间**隔着一个"被"字**，所以 `"已删除" in "已被删除的"` 是 **`False`**。
    结果 4 处合法提及被误报成"待处置"，而**串看着完全对**。

    ⇒ 判据落在**词根**（`删除` / `移除`），并在这里用真实句式钉住它。
    """
    checker = _checker()
    for phrasing in ("该 ADR 已随配置工厂一并移除",
                     "0017 / 0018 已被删除的工厂 ADR 占用过",
                     "原文随本链路下架",
                     "复用会让引用指向一个已不存在的编号"):
        assert any(m in phrasing for m in checker.RETIRED_MARKERS), (
            f"这句合法提及里没有任何退役标记，会被误报：{phrasing!r}"
        )


# ------------------------------------------------- 2026-10-07 主题合订留下的两张表


def _adr_files() -> set[str]:
    return {p.name for p in (ROOT / "docs" / "adr").glob("[0-9]*.md")}


def test_the_retired_adr_map_cannot_silently_widen() -> None:
    """★★ `RETIRED_ADR_TO_MERGED` 是一张**白名单**，它的两条性质都必须成立。

    它存在的理由：14 个编号在 2026-10-07 被并进 8 篇合订本，于是
    「引用 ADR-0014」这件事从"引一篇不存在的 ADR"变成了"引合订本里的一节"。
    判据必须认识这个新事实，否则 193 处合法提及会全变成噪音 ——
    而**噪音会把真缺陷埋掉**（见模块文档串里的 789→5 那段）。

    ★★ **但它绝不能变成"凡是退役编号一律放过"**，否则一个**打错的**编号
    （把 0019 写成 0091）会被静默放过，而那恰恰是这类检查最该抓的东西。
    所以这里钉两条性质：

    1. **每个目标编号必须真的存在** —— 否则映射把读者指向一个不存在的合订本；
    2. **每个源编号必须真的不再存在** —— 否则"退役"这个词就是假的，
       而它会让检查器对一篇**还活着**的 ADR 停止做编号校验（静默地少检查）。

    第 2 条尤其要紧：它让这张表**不能**被用来给任意编号开后门 ——
    想把一个还在的编号塞进来，先得把它删掉。
    """
    checker = _checker()
    files = _adr_files()
    live = {int(n[:4]) for n in files}

    assert checker.RETIRED_ADR_TO_MERGED, "退役映射空了 —— 要么漏了，要么常量被改名"

    for old, new in checker.RETIRED_ADR_TO_MERGED.items():
        assert new in live, (
            f"RETIRED_ADR_TO_MERGED 把 ADR-{old:04d} 指向 ADR-{new:04d}，"
            f"但后者在 docs/adr/ 里不存在（现有：{sorted(live)}）"
        )
        assert old not in live, (
            f"RETIRED_ADR_TO_MERGED 里的 ADR-{old:04d} **仍然存在** —— "
            f"那它不是退役编号。这张表只放『已经不在 docs/adr/ 里』的编号；"
            f"还在的编号不需要它，塞进来等于对该编号停止校验"
        )
        assert old != new, f"ADR-{old:04d} 不能映射到自己"


def test_the_renamed_adr_map_points_at_files_that_exist() -> None:
    """`RENAMED_ADR_FILES` 是"编号没变、文件名改了"，两条性质同上一节但结论相反。

    ★ 与退役映射的区别是**承重的**：`0011` **没有退役**（它今天就叫
    `0011-port-governance.md`）。把改名当退役处理，会让"引用 0011"看起来可疑；
    把退役当改名处理，则会让编号校验对着一个不存在的编号放行。
    """
    checker = _checker()
    files = _adr_files()

    assert checker.RENAMED_ADR_FILES, "改名映射空了 —— 要么漏了，要么常量被改名"
    for old_name, new_name in checker.RENAMED_ADR_FILES.items():
        assert new_name in files, (
            f"RENAMED_ADR_FILES 把 {old_name} 指向 {new_name}，但后者不存在"
        )
        assert old_name not in files, (
            f"RENAMED_ADR_FILES 里的 {old_name} **仍然存在** —— 那它没改名"
        )
        # 编号必须一致：改名不改号
        assert old_name[:4] == new_name[:4], (
            f"改名映射不允许改编号：{old_name} -> {new_name}（改号属于退役/合订，"
            f"该进 RETIRED_ADR_TO_MERGED）"
        )


def test_the_history_exemption_is_narrow() -> None:
    """★ `HISTORICAL_RECORDS` 只放过**一类**断链，而且名单必须写得出理由。

    这一条防的是"豁免慢慢变成整份文件不检查"：
    `ARCHIVES` 是整份不参与判定，`HISTORICAL_RECORDS` **不是** ——
    它只放过"目标是一份已被合订取代的 ADR 旧文件名"的断链，
    别种断链（打错的路径、被挪走的文档）在那两份文件里**仍然要报**。
    """
    checker = _checker()
    assert checker.HISTORICAL_RECORDS, "历史记录名单空了 —— 那两份文件的旧链接会全变成待处置"
    for rel in checker.HISTORICAL_RECORDS:
        assert (ROOT / rel).exists(), f"HISTORICAL_RECORDS 里的路径不存在：{rel}"
    doc = checker.__doc__ or ""
    assert "改写历史" in doc or "历史证据" in doc, "豁免的理由没写在模块文档串里"

    # 作用域判据：豁免**只**认"编号已退役 / 已改名"的旧文件名，别的一律不认
    assert checker._is_retired_adr_file("docs/adr/0014-sandbox-for-untrusted-code.md")
    assert checker._is_retired_adr_file("adr/0011-port-and-session-negotiation.md")
    assert not checker._is_retired_adr_file("docs/adr/0007-credentials-sandbox-and-trust.md"), (
        "存活的 ADR 文件名不该被当成'已退役'"
    )
    assert not checker._is_retired_adr_file("docs/adr/0091-typo.md"), (
        "★ 打错的编号（从未存在）必须仍然被报 —— 否则这张表就成了'什么都放过'"
    )
    assert not checker._is_retired_adr_file("docs/SCHEMA.md"), "不是 ADR 文件名的不该被放过"


def test_a_typo_number_is_still_reported() -> None:
    """★★ 反向断言：退役映射**没有**把检查变成"任何编号都放过"。

    这是上一条的端到端版本：构造一份带**打错编号**的临时文档，断言它**仍会被报**。
    没有这一条，`RETIRED_ADR_TO_MERGED` 完全可以被一段"return True"的实现骗过 ——
    而那种实现的「全绿」与「没有缺陷」在输出上长得一模一样。
    """
    checker = _checker()
    live = checker.real_adr_numbers()
    wrong = 9091
    assert wrong not in live and wrong not in checker.RETIRED_ADR_TO_MERGED

    # 用真实存在的文件走一遍 scan 的逻辑不方便，这里直接断言判定式本身
    assert wrong not in checker.RETIRED_ADR_TO_MERGED, (
        f"ADR-{wrong} 不该在退役表里 —— 本用例选的正是'从未存在过'的编号"
    )
    assert not checker._is_retired_adr_file(f"docs/adr/{wrong:04d}-typo.md")


def test_the_history_dangling_link_exemption_covers_both_shapes() -> None:
    """★★ 历史记录的断链豁免要认**两种**形态，而第三种必须仍然被报。

    这不是"想宽松一点"，而是**实测逼出来的**：2026-10-07 第一次实现只认"目标文件名是旧名"，
    而 CHANGELOG 里存在 `[ADR-0011](adr/0011-port-governance.md)` 这种
    **标签是退役编号、目标已被改指**的形态 —— 它会被报成断链，
    而唯一的修法是**改写那句历史文本**，正是本仓不许做的事。

    三条判据（第 3 条是承重的反向断言）：
      ① `[ADR-0011](adr/0011-port-and-session-negotiation.md)` —— 目标就是旧名 ⇒ 豁免；
      ② `[ADR-0011](adr/0011-port-governance.md)` —— 标签是退役编号 ⇒ 豁免；
      ③ `[ADR-0091](adr/0091-typo.md)` —— 编号从未存在 ⇒ **仍然要报**。
    """
    checker = _checker()

    assert checker._is_retired_adr_file("adr/0011-port-and-session-negotiation.md"), "形态①"
    assert checker._label_names_a_superseded_adr(
        "[ADR-0011](adr/0011-port-governance.md)"), "形态②"

    # ③ 承重的反向断言：打错的编号不能因为"它在历史记录里"就被放过
    assert not checker._label_names_a_superseded_adr(
        "[ADR-0091](adr/0091-typo.md)"), "打错的编号不该被豁免"
    assert not checker._is_retired_adr_file("adr/0091-typo.md"), "打错的文件名不该被豁免"
    # 存活的编号同样不豁免：`[ADR-0019](...)` 是正常引用，不是退役讨论
    assert not checker._label_names_a_superseded_adr(
        "[ADR-0019](adr/0019-in-turn-context-and-budget-governance.md)")

