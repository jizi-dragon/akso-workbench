"""Markdown 表格渲染工具（供 spider 产物使用）。

> **2026-09-14 清理**：本模块原名"清单 Markdown 渲染"，整段 `to_markdown()`（把
> `inventory.json` 渲染成「Gaia 系统配置清单」）**随 `inventory` 命令下线一并删除**——
> 它已无任何调用方，留着只会让人以为还有一条全量盘点的读路径。
> 现存唯一使用者是 `spider.py`，它只用 `_table`（下划线开头是**同包**约定，非公开 API）。

来源：akso-cc `report.ts` 的 Python 化（表格口径与上游一致：`|` 转义、换行转空格、空值用 `—`）。
"""

from __future__ import annotations

from typing import Any


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for row in rows:
        cells = [str(c).replace("|", "\\|").replace("\n", " ") if c is not None else "—" for c in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)
