"""文档层健康检查：断链、指向不存在的 ADR / 坑号。

## 它为什么存在

这一整类缺陷**全是静默的**：文档照常渲染、链接照样是蓝色，读的人点进去才发现
404 —— 或者更糟，点进去是**另一篇**文档。`PLATFORM-OPERATIONS-LEARNED.md` 里
F73/F78 两条的**证据格**引用一个**已删除的 ADR-0018**，而那个文件的表头
逐字写着"本文件已把每条事实的正文内联，**不依赖任何已删除的文件**" ——
文件自己违反了自己的纪律，而形态看起来完全正常（一个证据格的引用）。

★ 这条缺陷与代码里的那些是同一族：**没有任何报错，只有事后读的人被误导。**

## ★★ 设计上最要紧的一件事：作用域

第一版跑出 **789 处**问题，其中 700+ 是假的（vendor 手册的 URL 编码链接没解码）。
**一个整天误报的检查会被人无视** —— 那与"永远红着的告警"是同一个问题：
假阳性不是"小噪音"，它会把真缺陷埋掉（真正的 3 处幻影引用当时被埋在 700 条里）。

所以本工具把文档分成**两类**，只有第一类参与判定：

| 类别 | 例子 | 断链算不算缺陷 |
|---|---|---|
| **活文档** | `AGENTS.md` / `docs/CORE-*.md` / `docs/adr/**` / `README` / 各 `docs/*.md` | ✅ 算。它们描述的是**今天**，链接必须今天就能点开 |
| **存档** | vendor 手册、上游 README 副本、**带日期的真机取证报告** | ❌ 不算。链接反映的是**当时**的状态；改它 = **篡改记录** |

★ 第二类不是"懒得修"：那些是**带日期的历史证据**（`factory-tuning/reports/**`
记录的是 2026-09-21/22 真机上发生了什么），而报告里指向 `docs/FACTORY-VERIFICATION-PLAN.md`
的链接**在当时是有效的** —— 那个文件随配置工厂一起被删了。今天把它改成"该文件已删除"
是**改写历史**，与"ADR 只增不改""CHANGELOG 是历史"是同一条纪律。

⇒ 存档的处置是：**在存档自己的 README 里写明"里面的链接可能指向已移除的文件"**，
而不是去改那些报告。

## 用法

    .\\.venv\\Scripts\\python.exe tools\\check_doc_links.py          # 人读的报告
    .\\.venv\\Scripts\\python.exe tools\\check_doc_links.py --quiet  # 只输出计数（门禁用）
"""

from __future__ import annotations

import pathlib
import re
import sys
from urllib.parse import unquote

ROOT = pathlib.Path(__file__).resolve().parents[1]

# 不是我们的内容，也没有人会去点它们的链接
SKIP_DIRS = {".git", ".venv", "node_modules", "dist", "build", "__pycache__",
             "tmp", ".pytest_cache", ".ruff_cache"}

ARCHIVES = (
    "docs/gaia平台技术手册",                 # vendor 的 Gaia V3.8 产品手册（逐字存档）
    "extensions/quick-login/docs/archive",  # 上游 README 的副本
    "factory-tuning",                       # 带日期的真机取证报告（历史证据）
)
"""**存档**：链接反映当时状态，不参与判定。每一处都要说得出理由（见模块文档串）。"""

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
ADR_REF = re.compile(r"ADR[- ]?(\d{4})")
ADR_LINK = re.compile(r"\[[^\]]*ADR[- ]?(\d{4})[^\]]*\]\(([^)\s]+)\)")
PITFALL_REF = re.compile(r"PITFALLS?\s*#(\d+)")

FENCE = re.compile(r"^\s*(?:>\s*)*(?:[-*+]\s+|\d+\.\s+)?(```|~~~)")
# ★ 行内代码段的定界符是**N 个反引号配 N 个**（Markdown 规范），不是固定一个。
# 只认单个的后果实测过：PITFALLS 里引用一段正则时用了双反引号定界，
# 于是 `` 被匹配成"空代码段"、里面的 `[［\[]([^\]］]+)` **露在正文里**，
# 检查器把它当成一条断链报出来 —— 又是"喊狼来了"。
INLINE_CODE = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.S)


class UnbalancedFence(RuntimeError):
    """围栏代码块数不成对 —— 检查器自身的缺陷，必须大声失败。

    ★★ 这个异常是本工具**最重要的一处设计**，因为它防的是一种"检查器自己静默失效"：

    第一版的围栏正则要求**行首**就是 ```，而引用块里的围栏写作 `> ``` `（不匹配），
    于是"开"没被认出、"关"被认成"开" —— **状态从此翻转**，文件剩下的部分全被当成
    代码块剥成空格，**里面的链接一个都不再检查，而工具报 PASS**。

    实测（2026-09-24）：`AGENTS.md` 行首围栏 **3 个（奇数）**、
    `CORE-ARCHITECTURE.md` 35 个、`CORE-AGENT-PLAN.md` 23 个 ——
    **仓库里最重要的三份文档，各自从某一处起就不再被检查了**，而门禁是绿的。

    ⇒ 判据是"剥完之后围栏必须成对"。不成对 = 检查器的 bug，**抛异常，不许静默通过**。
    """

RETIRED_MARKERS = ("删除", "移除", "不存在", "下架", "退役", "不复存在")
"""一行里出现这些词，就认为它是在**讨论一个已退役的编号**，而不是拿它当依据。

★ 这些词是**从真实合法提及里收出来的**，不是凭空想的：
`已随配置工厂一并移除` / `随本链路下架` / `已不存在的 ADR-0018` / `已被删除的工厂 ADR 占用过`。

★★ 第一版写的是 `"已删除"` —— **它匹配不上 `"已被删除的工厂"`**。
因为"已被删除"是 已／被／删／除，`已` 与 `删除` 之间**隔着一个"被"字**，
所以 `"已删除" in "已被删除的"` 是 **`False`**。

这个坑很难看：**串看着完全对**，检查器照常运行、照常报出 9 处"待处置"，
而其中 4 处是它自己的匹配 bug。中文的词形变化（已被／已／被／随…）比英文更容易出这种事，
而英文那边一个 `\bremoved\b` 就够了。⇒ **判据要落在词根上（`删除` 而不是 `已删除`）。**
"""


def strip_code(text: str) -> tuple[str, list[tuple[int, str]]]:
    r"""剥掉**围栏代码块**与**行内代码**，再去找链接。返回 (剥后文本, 代码块里的链接)。

    ★ 不加这一步会误报，而且是很难看的那种：`docs/INSIGHT-SPEC.md` 里有一句
    「我们当前用正则 ``修改状态为[［\[]([^\]］]+)`` 从中提取目标状态名」——
    链接正则把字符类里的 `[` 当成了链接开头，报出一条「断链 -> `[^\]］]+`」。
    **把散文里的正则当成断链**是典型的喊狼来了。

    做法是"用等长空格替换"而不是删掉：**行号不变**，报错位置才对得上。

    ## ★★ 第二个返回值：代码块**里面**的 markdown 链接

    剥掉代码块之后，块内的链接就不再被检查了 —— 这通常是对的（代码块里的
    `[x](y)` 多半是示例）。但有一种情况它是**缺陷**：

    > **散文被误吞进了代码块。** 实测（2026-09-24）：`docs/CODEBASE_OVERVIEW.md`
    第 70 行开的目录树直到第 **116** 行才闭合，于是 100–113 行那几段
    「2026-09-20 删除 / 2026-09-22 追加」的**备注渲染成了等宽代码**，而其中
    `[FACTORY-PLATFORM-COGNITION.md](FACTORY-PLATFORM-COGNITION.md)` **根本不显示为链接**。
    这种形态很难自己发现：**文档照常渲染、没有报错**，只有盯着看才发现那一段"字号不对"。

    ⇒ 代码块里出现 markdown 链接，就值得报出来让人看一眼。
    这是启发式（示例代码里也可能有链接），但**误报的代价只是看一眼**，
    而漏报的代价是一整段正文静默失效。
    """
    out: list[str] = []
    inside: list[tuple[int, str]] = []
    in_fence = False
    for index, line in enumerate(text.split("\n"), 1):
        if FENCE.match(line):
            in_fence = not in_fence
            out.append(" " * len(line))
            continue
        if in_fence:
            for match in LINK.finditer(line):
                inside.append((index, match.group(1)))
            out.append(" " * len(line))
            continue
        out.append(INLINE_CODE.sub(lambda m: " " * len(m.group(0)), line))
    if in_fence:
        raise UnbalancedFence(
            "剥完代码块之后围栏仍然开着 —— 说明围栏识别漏了某种写法，"
            "而它会让本文件**剩下的部分全不被检查**。修 FENCE 正则，不要忽略这个异常。"
        )
    return "\n".join(out), inside


def _is_archive(path: pathlib.Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    return any(rel == a or rel.startswith(a + "/") for a in ARCHIVES)


def all_docs() -> list[pathlib.Path]:
    return sorted(p for p in ROOT.rglob("*.md")
                  if not any(part in SKIP_DIRS for part in p.parts))


def living_docs() -> list[pathlib.Path]:
    return [p for p in all_docs() if not _is_archive(p)]


def real_adr_numbers() -> list[int]:
    return sorted({int(m.group(1)) for p in (ROOT / "docs" / "adr").glob("[0-9]*.md")
                   for m in [re.match(r"(\d{4})", p.name)] if m})


def real_pitfall_numbers() -> list[int]:
    text = (ROOT / "docs" / "PITFALLS.md").read_text(encoding="utf-8")
    return sorted({int(m.group(1)) for m in re.finditer(r"^\| (\d+) \|", text, re.M)})


def scan() -> dict[str, list[str]]:
    adr_nums = real_adr_numbers()
    pit_nums = set(real_pitfall_numbers())
    problems: dict[str, list[str]] = {}

    def note(path: pathlib.Path, kind: str, detail: str) -> None:
        problems.setdefault(kind, []).append(f"{path.relative_to(ROOT).as_posix()}  {detail}")

    for path in living_docs():
        text, inside_links = strip_code(path.read_text(encoding="utf-8"))

        for line_no, target in inside_links:
            if target.startswith(("http://", "https://", "mailto:", "#", "/")):
                continue
            note(path, "代码块里的链接（疑似正文被吞）", f":{line_no} -> {target}")

        for match in LINK.finditer(text):
            target = match.group(1)
            if target.startswith(("http://", "https://", "mailto:", "#", "/")):
                continue
            clean = unquote(target).split("#", 1)[0]
            if clean and not (path.parent / clean).exists() and not (ROOT / clean).exists():
                note(path, "断链", f"-> {target}")

        # ★ 幻影 ADR **只认链接形态**：本仓有多处**故意**讨论 0017/0018 这两个已退役的
        # 编号（解释"新 ADR 为什么从 0019 起"）。那些是在**讲编号这件事**，
        # 不是"引一条 ADR 支撑某个说法"。混在一起报就就又是假阳性。
        for match in ADR_LINK.finditer(text):
            num = int(match.group(1))
            if num not in adr_nums:
                line = text[:match.start()].count("\n") + 1
                note(path, "幻影 ADR 链接", f":{line} ADR-{num:04d} -> {match.group(2)}")

        # ★★ 裸提引用：**引用**与**讨论**的区别靠"退役标记"判定。
        #
        # 区分的必要：`（ADR-0017 §D-2）` 是**拿一条 ADR 当依据** —— 那条 ADR 没了，
        # 读者却会以为有东西支撑这个说法；而「0017/0018 已被删除的工厂 ADR 占用过」
        # 是在**讲编号这件事**，它本来就该出现。
        #
        # 判据是实测出来的：本仓 12 处合法提及**每一处**都带退役标记，
        # 而 5 处真缺陷**一处都没有**。规则不完美（它是词面匹配），
        # 但它把"要不要人工看"从 17 处降到 0 处，而**误报的代价远高于漏报这里**。
        for match in ADR_REF.finditer(text):
            num = int(match.group(1))
            if num in adr_nums:
                continue
            line_no = text[:match.start()].count("\n") + 1
            # ★ 窗口取"该行 ＋ 紧随两行"，不是只取该行：退役说明常常是**跨行的括注**
            # （实测：`（ADR-0017 §D-1 ——\n> 该 ADR 已随配置工厂一并移除，…）`）。
            # 只读一行会把这种写法误判成"没说明" —— 而**误报会把真缺陷埋掉**，
            # 这是本工具最该避免的失败模式（见模块文档串里的 789→5 那段）。
            window = "\n".join(
                path.read_text(encoding="utf-8").split("\n")[line_no - 1:line_no + 2]
            )
            if not any(marker in window for marker in RETIRED_MARKERS):
                note(path, "幻影 ADR 引用",
                     f":{line_no} ADR-{num:04d}（该编号不存在，且附近没有说明它已退役）")

        for match in PITFALL_REF.finditer(text):
            num = int(match.group(1))
            if num not in pit_nums:
                line = text[:match.start()].count("\n") + 1
                note(path, "幻影坑号", f":{line} PITFALLS #{num}")

    return problems


def main(argv: list[str]) -> int:
    quiet = "--quiet" in argv
    living, archives = living_docs(), [p for p in all_docs() if _is_archive(p)]
    adr_nums, pit_nums = real_adr_numbers(), real_pitfall_numbers()

    if not quiet:
        print(f"  活文档 {len(living)} 份；存档 {len(archives)} 份不参与判定")
        for a in ARCHIVES:
            print(f"    · {a}")
        print(f"  真实 ADR 编号: {adr_nums}")
        print(f"  真实坑号: {pit_nums[0]}..{pit_nums[-1]}（{len(pit_nums)} 条）")

    problems = scan()
    total = sum(len(v) for v in problems.values())

    if not quiet:
        for kind, items in problems.items():
            print(f"\n  === {kind}（{len(items)}） ===")
            for item in items[:40]:
                print(f"    {item}")
            if len(items) > 40:
                print(f"    …还有 {len(items) - 40} 条")
        print(f"\n  DOC_LINK_CHECKS: {'PASS' if total == 0 else f'{total} 处待处置'}")
    else:
        print(f"DOC_LINK_CHECKS: {total}")

    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
