"""文档切分：标题行与表格感知。

钉钉手册以纯文本 + Markdown 表格为主：
1. 标题行（#/数字编号/中文序号）作为块边界
2. 连续表格行聚为独立块（函数/参数说明常以表格承载）
3. 普通段落按长度窗口切分，保持句子完整
"""

from __future__ import annotations

import re

_HEADING = re.compile(
    r"^\s*(#{1,6}\s+|\d+(\.\d+)*[、.．\s]|[一二三四五六七八九十]+[、.．])"
)
_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def split_into_chunks(text: str, max_len: int = 600) -> list[str]:
    text = text.replace("\r", "")
    if not text.strip():
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    current_is_table = False

    def flush() -> None:
        nonlocal current, current_len, current_is_table
        if current:
            blocks = _split_long("".join(current), max_len)
            chunks.extend(b for b in blocks if b.strip())
        current = []
        current_len = 0
        current_is_table = False

    for raw_line in text.split("\n"):
        line = raw_line.rstrip()
        is_table = bool(_TABLE_LINE.match(line)) and not _TABLE_SEP.match(line)

        if is_table != current_is_table and current:
            flush()
        current_is_table = is_table

        if not is_table and _HEADING.match(line):
            flush()
            current.append(line)
            current_len += len(line)
            continue

        if not is_table and current_len > 0 and current_len + len(line) > max_len:
            flush()

        if line.strip():
            current.append(line + "\n")
            current_len += len(line)
        elif current and not is_table:
            current.append("\n")

    flush()
    return [c for c in chunks if len(c.strip()) >= 8]


def _split_long(block: str, max_len: int) -> list[str]:
    """表格块不切（保持行级完整）；超长普通块按句子边界粗切。"""
    if len(block) <= max_len * 1.6 or block.lstrip().startswith("|"):
        return [block]
    parts: list[str] = []
    buf = ""
    for sentence in re.split(r"(?<=[。！？；\n])", block):
        buf += sentence
        if len(buf) >= max_len:
            parts.append(buf)
            buf = ""
    if buf.strip():
        parts.append(buf)
    return parts
