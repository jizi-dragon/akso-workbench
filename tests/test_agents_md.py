"""AGENTS.md 的机器守卫。

## 为什么需要它

`AGENTS.md` 是**每一次 AI 协作的入口**，而它此前**没有任何机检**。代价已经付过：

- 它写着"真机环境坑（**89 条**）"，而 `PITFALLS.md` 当时已经是 **92 条**（本轮又到 94）；
- 它写着"**16 篇 ADR**"，而 `docs/adr/` 是 17 个文件（含 `0000-template.md`）——
  这个歧义让两处引用口径都对不上；
- 铁律编号在**阅读顺序上不单调**（1–12 → 33–37 → 13–32 → 38），因为 33 之后是后来补的、
  而编号是稳定 ID 不能重编。**"哪个节里应该有哪些号"没有任何东西守着**。

这三类漂移的**共同形态**是：文件照常被读、规则照常被执行，只有"读的人拿到了错的数字/
以为某条规则不存在"。同族判据见 `docs/PITFALLS.md` 的维护规则第 1 条
（它为自己的编号连续性写了机检判据）——**这里是把同一条纪律用在 AGENTS.md 上**。

## 这些断言各防什么

| 断言 | 防的漂移 |
|---|---|
| 编号 1..N 连续无缺号 | 删规则时整行删掉（PITFALLS #44 的形态） |
| 编号在**阅读顺序**上单调递增 | 新规则插在中间，读者以为后面没有了 |
| 头部声明的坑数 == `PITFALLS.md` 实际条目数 | "89 条"那类数字漂移 |
| 头部声明的 ADR 篇数 == `docs/adr/` 实际篇数 | 同上（且要排除 `0000-template.md`） |
| 全部 markdown 链接指向存在的文件 | 被引文档被移动/删除/归档后留下死链 |
| `## N.` 节号连续 | 用标题当锚点插入新节时把原标题吃掉（本仓实录犯过 3 次） |
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"
PITFALLS = ROOT / "docs" / "PITFALLS.md"


def _text() -> str:
    return AGENTS.read_text(encoding="utf-8")


def _rule_numbers() -> list[int]:
    """§1 铁律表里的编号，**按它们在文件里出现的顺序**。"""
    text = _text()
    start = text.index("## 1. 铁律")
    end = text.index("## 2. 命名与形状约定")
    return [int(m.group(1)) for m in
            re.finditer(r"^\| (\d+) \|", text[start:end], re.M)]


# ---------------------------------------------------------------- 铁律编号


def test_rule_numbers_have_no_gaps() -> None:
    """铁律编号必须 1..N 连续无缺号。

    编号是**稳定 ID 而非排序号**（全仓 38 处按它引用），所以缺号意味着
    "某条规则被整行删掉了"——而读的人会把缺失当成"本来就没有这条"。
    """
    numbers = _rule_numbers()
    assert numbers, "一条铁律都没解析到 —— 表格形状变了，本文件的所有断言都失去锚点"
    missing = [i for i in range(1, max(numbers) + 1) if i not in numbers]
    assert not missing, f"铁律编号有缺号：{missing}（是删了整行，还是编号写错了？）"
    assert len(numbers) == len(set(numbers)), "铁律编号有重复"


def test_rule_numbers_ascend_in_reading_order() -> None:
    """铁律在文件里的**出现顺序**必须单调递增。

    编号本身不重编（有 38 处外部引用），但**分节要按主题把顺序摆对**：
    读者是自上而下读的，遇到 33 之后再冒出 13，他会以为 13 那条已经读过了。
    """
    numbers = _rule_numbers()
    assert numbers == sorted(numbers), (
        "铁律的阅读顺序与编号顺序不一致：\n"
        f"  实际顺序 {numbers}\n"
        f"  期望顺序 {sorted(numbers)}\n"
        "处置：把编号大却排在前面那几条**移到与其编号相称的分节**里"
        "（不要重编号——外部有 38 处按编号引用）。"
    )


# ---------------------------------------------------------------- 头部数字


def test_declared_pitfall_count_matches_the_real_file() -> None:
    """头部写的"真机环境坑（**N 条**）"必须等于 `PITFALLS.md` 的实际条目数。"""
    match = re.search(r"真机环境坑（\*\*(\d+) 条\*\*", _text())
    assert match, "头部里找不到「真机环境坑（**N 条**）」这句话，断言失去锚点"
    declared = int(match.group(1))
    actual = len(re.findall(r"^\| (\d+) \|", PITFALLS.read_text(encoding="utf-8"), re.M))
    assert declared == actual, (
        f"AGENTS.md 写 {declared} 条，PITFALLS.md 实际 {actual} 条。"
        "加了坑就要同步这里（本轮之前已经漂过 89 → 92 → 94 三次）"
    )


def test_declared_adr_count_matches_the_real_directory() -> None:
    """头部写的 ADR 篇数必须等于 `docs/adr/` 里**除模板外**的篇数。"""
    match = re.search(r"不再变的决策（\*\*(\d+) 篇\*\*", _text())
    assert match, "头部里找不到「不再变的决策（**N 篇** ADR」这句话，断言失去锚点"
    declared = int(match.group(1))
    files = [p for p in (ROOT / "docs" / "adr").glob("*.md") if not p.name.startswith("0000")]
    assert declared == len(files), (
        f"AGENTS.md 写 {declared} 篇 ADR，docs/adr/ 里实际 {len(files)} 篇（不含模板）。"
        "新增 ADR 要同步这里"
    )


# ---------------------------------------------------------------- 链接与节号


def test_every_link_points_at_a_real_file() -> None:
    """所有 markdown 链接必须指向存在的文件（外部 URL 与页内锚点除外）。

    本仓已经有过"文档被归档/删除但引用没跟上"的实例（CHANGELOG 曾留下 6 处死链），
    所以入口文件的链接值得单独守一道。
    """
    broken: list[str] = []
    for match in re.finditer(r"\]\(([^)]+)\)", _text()):
        target = match.group(1)
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        path = target.split("#")[0]
        if path and not (AGENTS.parent / path).exists():
            broken.append(target)
    assert not broken, f"AGENTS.md 里有指向不存在文件的链接：{broken}"


def test_section_numbers_are_contiguous() -> None:
    """`## N.` 节号必须 0..N 连续。

    这是 `AGENTS.md` §5 自己那条纪律（"用标题当锚点插入新节时必须把原标题写回"，
    本仓实录在同一次会话里犯过 3 次）用在它自己身上。
    """
    numbers = [int(m.group(1)) for m in
               re.finditer(r"^## (\d+)\. ", _text(), re.M)]
    assert numbers, "一个 `## N.` 节都没解析到 —— 节标题形状变了"
    expected = list(range(len(numbers)))
    assert numbers == expected, f"节号不连续或不从 0 开始：实际 {numbers}，期望 {expected}"
