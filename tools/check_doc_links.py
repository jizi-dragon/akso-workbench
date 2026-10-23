"""文档层健康检查：断链、幻影坑号、代码块误吞正文。

## 它为什么存在

这一整类缺陷**全是静默的**：文档照常渲染、链接照样是蓝色，读的人点进去才发现
404 —— 或者更糟，点进去是**另一篇**文档。文件自己声明"不依赖任何已删除的文件"，
正文里却引用着一个已经被删掉的东西：看起来完全正常，只有读的人会被误导。

★ 这条缺陷与代码里的那些是同一族：**没有任何报错，只有事后读的人被误导。**

## ★★ 设计上最要紧的一件事：作用域

第一版跑出 **789 处**问题，其中 700+ 是假的（vendor 手册的 URL 编码链接没解码）。
**一个整天误报的检查会被人无视** —— 那与"永远红着的告警"是同一个问题：
假阳性不是"小噪音"，它会把真缺陷埋掉（真正的 3 处幻影引用当时被埋在 700 条里）。

所以本工具把文档分成**两类**，只有第一类参与判定：

| 类别 | 例子 | 断链算不算缺陷 |
|---|---|---|
| **活文档** | `AGENTS.md` / `docs/CORE-*.md` / `README` / 各 `docs/*.md` | ✅ 算。它们描述的是**今天**，链接必须今天就能点开 |
| **存档** | 上游 README 副本、**带日期的真机取证报告** | ❌ 不算。链接反映的是**当时**的状态；改它 = **篡改记录** |

★ 第二类不是"懒得修"：那些是**带日期的历史证据**，报告里指向当时某份文档的链接
**在当时是有效的** —— 那份文件后来被删了。今天把它改成"该文件已删除"是**改写历史**，
与"CHANGELOG 是历史"是同一条纪律。
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
# `target` 是 Tauri/Cargo 构建产物（含旧 sidecar 资源），不是活文档。
SKIP_DIRS = {".git", ".venv", "node_modules", "dist", "build", "__pycache__",
             "tmp", ".pytest_cache", ".ruff_cache", "target"}

ARCHIVES = (
    "factory-tuning",                       # 带日期的真机取证报告（历史证据）
)
"""**存档**：链接反映当时状态，不参与判定。每一处都要说得出理由（见模块文档串）。

> 删目录**必须同时删这里的条目**：有一条判据断言「`ARCHIVES` 里的路径必须真实存在」，
> 留着它会让门禁红，而红的原因（"存档路径不存在"）与真正的问题（"手册已删"）对不上号。
"""

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
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
    `CORE-ARCHITECTURE.md` 35 个 —— **仓库里最重要的文档从某一处起就不再被检查了**，
    而门禁是绿的。

    ⇒ 判据是"剥完之后围栏必须成对"。不成对 = 检查器的 bug，**抛异常，不许静默通过**。
    """


def strip_code(text: str) -> tuple[str, list[tuple[int, str]]]:
    r"""剥掉**围栏代码块**与**行内代码**，再去找链接。返回 (剥后文本, 代码块里的链接)。

    ★ 不加这一步会误报，而且是很难看的那种：散文里写了一段正则
    「修改状态为[［\[]([^\]］]+)」—— 链接正则把字符类里的 `[` 当成了链接开头，
    报出一条「断链 -> `[^\]］]+`」。**把散文里的正则当成断链**是典型的喊狼来了。

    做法是"用等长空格替换"而不是删掉：**行号不变**，报错位置才对得上。

    ## ★★ 第二个返回值：代码块**里面**的 markdown 链接

    剥掉代码块之后，块内的链接就不再被检查了 —— 这通常是对的（代码块里的
    `[x](y)` 多半是示例）。但有一种情况它是**缺陷**：

    > **散文被误吞进了代码块。** 实测：一份文档的目录树围栏开在正文中间、
    几十行后才闭合，于是中间那几段备注**渲染成了等宽代码**，而其中
    `[某文档](某文档.md)` **根本不显示为链接**。文档照常渲染、没有报错，
    只有盯着看才发现那一段"字号不对"。

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


def real_pitfall_numbers() -> list[int]:
    text = (ROOT / "docs" / "PITFALLS.md").read_text(encoding="utf-8")
    return sorted({int(m.group(1)) for m in re.finditer(r"^\| (\d+) \|", text, re.M)})


def scan() -> dict[str, list[str]]:
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

        for match in PITFALL_REF.finditer(text):
            num = int(match.group(1))
            if num not in pit_nums:
                line = text[:match.start()].count("\n") + 1
                note(path, "幻影坑号", f":{line} PITFALLS #{num}")

    return problems


def main(argv: list[str]) -> int:
    quiet = "--quiet" in argv
    living, archives = living_docs(), [p for p in all_docs() if _is_archive(p)]
    pit_nums = real_pitfall_numbers()

    if not quiet:
        print(f"  活文档 {len(living)} 份；存档 {len(archives)} 份不参与判定")
        for a in ARCHIVES:
            print(f"    · {a}")
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
