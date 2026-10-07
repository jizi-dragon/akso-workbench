"""长文档的**编码与换行**守卫（它们属于语义，不属于格式）。

## 为什么需要它

`docs/PITFALLS.md` 是 CRLF + UTF-8 无 BOM；`AGENTS.md` / `README.md` 是 LF + 无 BOM。
而**改这些文件最常见的方式是一次脚本替换**，而 Python 的两个默认值会**静默**改写它们：

| 写法 | 后果 |
|---|---|
| `Path.read_text()` | **通用换行**：内存里 CRLF 已变成 LF（读的时候就没了一样东西） |
| `Path.write_text(text, newline="")` | 不翻译 ⇒ 把内存里的 LF **原样写回** ⇒ **整份文件被改成 LF** |
| `Path.write_text(text)`（Windows 默认） | `\\n` → `os.linesep` ⇒ 反过来把 LF 文件改成 CRLF |

⇒ 一次"看起来无害"的批量替换就能把整份文件的换行改掉，而**所有门禁照绿**
（它们读的是文本，不是字节）。实测：一次补丁脚本把 138 条的 PITFALLS 从 CRLF 改成 LF，
**没有任何判据会红** —— 唯一能救回来的是字节级备份。

★ 纪律：**改这类文件用 `read_bytes` / `write_bytes`**，或者改完立刻跑本文件。

判据：`pytest tests/test_doc_encoding.py -o addopts=""`
反证：把 `docs/PITFALLS.md` 的 CRLF 全换成 LF ⇒ 当场红。
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: 长文档 → (换行, 是否允许 BOM)。**这张表就是"唯一事实源"**：
#: 新增一份长文档时在这里登记，别在别处再写一遍它的编码约定。
#:
#: ★★ 表里的值是**实测出来的**（2026-10-07），不是"按习惯以为的" ——
#:   第一版按印象写成「PITFALLS 是 CRLF、其余是 LF」，结果**健康树上 5 条红**。
#:   一个在健康树上就红的检查会把真缺陷埋进噪音里（本仓踩过：那份断链检查一度报 789 处、
#:   700+ 是假的）。⇒ 加新条目时**先量**：`python -c "import pathlib;raw=pathlib.Path('X').read_bytes();print(raw.count(b'\r\n'), raw.count(b'\n'))"`
DOCS: dict[str, tuple[str, bool]] = {
    "docs/PITFALLS.md": ("crlf", False),
    "AGENTS.md": ("crlf", False),
    "README.md": ("crlf", False),
    "CHANGELOG.md": ("crlf", False),
    "CONTRIBUTING.md": ("crlf", False),
}


#: ★ 既存损坏的**上限**（不是"允许损坏"）：`docs/PITFALLS.md` 第 38 条那一行中间有 8 个
#: U+FFFD（约 2026-09 之前引入，基线与备份分支里都已经是这样）。
#: **修它需要在原始文本**，而它已经不在仓库里 —— 编一段出来比留着更坏（那是**伪造记录**）。
#: ⇒ 登记上限，**新增一律红**。真要修：`git log -p --all -- docs/PITFALLS.md` 找回那一行的历史版本。
KNOWN_DAMAGE: dict[str, int] = {"docs/PITFALLS.md": 8}


def _read(path: Path) -> bytes:
    assert path.is_file(), f"{path.relative_to(ROOT)} 不存在"
    return path.read_bytes()


def test_the_table_is_not_empty() -> None:
    """★ 空洞断言比没有断言更坏：一张空表的 `all(...)` 在缺陷版上照样绿。"""
    assert DOCS, "DOCS 表是空的 —— 这个文件就什么都没在守"
    for rel in DOCS:
        assert (ROOT / rel).is_file(), f"表里登记了不存在的文件：{rel}"


@pytest.mark.parametrize(("rel", "spec"), sorted(DOCS.items()))
def test_encoding_and_line_endings(rel: str, spec: tuple[str, bool]) -> None:
    ending, bom_ok = spec
    raw = _read(ROOT / rel)

    # ① BOM：本仓的 markdown 一律无 BOM（`tools/build.ps1` 是刻意的例外，它不在这张表里）
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    assert has_bom == bom_ok, (
        f"{rel}：BOM 状态不对（实际 {'有' if has_bom else '无'}，期望 {'有' if bom_ok else '无'}）—— "
        "PowerShell 的 `Set-Content -Encoding UTF8` 会加 BOM"
    )

    # ② 能按 UTF-8 解出来，且没有替换字符（解码坏掉的证据）
    text = raw.decode("utf-8")
    found = text.count("\ufffd")
    allowed = KNOWN_DAMAGE.get(rel, 0)
    assert found <= allowed, (
        f"{rel}：U+FFFD = {found}，超过登记的既存损坏 {allowed} —— "
        "文件在某个环节被按错误编码读过再写回（PowerShell 的 `Get-Content -Raw` 按 GBK 读 UTF-8 "
        "就是这一类）。**新损坏一律修掉，不要加进 KNOWN_DAMAGE。**"
    )

    # ③ 换行：**混用也要拦**（混用说明改它的人不知道自己写了什么）
    crlf = raw.count(b"\r\n")
    bare_lf = raw.count(b"\n") - crlf
    if ending == "crlf":
        assert crlf > 0 and bare_lf == 0, (
            f"{rel}：期望全 CRLF，实际 CRLF={crlf} / 裸 LF={bare_lf} —— "
            "多半是一次 `read_text()` + `write_text(newline='')` 的替换（见本文件顶部）"
        )
    else:
        assert bare_lf > 0 and crlf == 0, (
            f"{rel}：期望全 LF，实际 CRLF={crlf} / 裸 LF={bare_lf} —— "
            "多半是用 Windows 默认的 `write_text()` 写回的（它会把 \\n 翻成 \\r\\n）"
        )


def test_the_reverse_is_real() -> None:
    """反证说明（`python tools\\verify_regression_tests.py` 之外的口径）：

    本文件**能**被反证：把 `docs/PITFALLS.md` 的 `\\r\\n` 全替换成 `\\n`，
    `test_encoding_and_line_endings[docs/PITFALLS.md]` 当场红。
    这里不做文件改写（判据不该自带副作用），只在文档里写明手法。
    """
    assert (ROOT / "docs" / "PITFALLS.md").read_bytes().count(b"\r\n") > 100, (
        "PITFALLS 的 CRLF 行数异常少 —— 要么文件被改坏了，要么它已不是 CRLF 文档（那就该改 DOCS 表）"
    )
