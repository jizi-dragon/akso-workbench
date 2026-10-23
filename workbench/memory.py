"""Agent 长期记忆（v1）：存**事实**，不存指令。

## 为什么与"指令"分开

指令（`AGENTS.md` / `CORE-ARCHITECTURE.md`）是每轮都在的规则；事实是"检索到了才用、
可能过期"的信息。混在一起会让"某条发布分支叫 X"看起来像一条必须遵守的规则。
所以这里只做事实表，检索由 `memory_search` 工具按需触发，**不自动改 system 前缀**
（I1/I5：system 每轮变一次 = 缓存全 miss）。

## 检索为什么不用 FTS

SQLite 的 FTS5 在部分打包环境里不可用，而且中文分词要另配。v1 用"行数上限 + 词元重叠"
（英文词 + 中文单字/双字），够在几百条记忆里排序；数据量上去再换 FTS。

## 过期是硬边界

`expires_at` 到了就**不参与自动检索**（显式 list 仍能看到，便于清理）。
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

from . import db

SCOPES = frozenset({"global", "project"})
TYPES = frozenset({"reference", "project", "user", "feedback"})
MAX_TITLE = 120
MAX_BODY = 4000
MAX_KEYWORDS = 500
MAX_ROWS = 500

_TOKEN_RE = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]")


class MemoryError(RuntimeError):
    """记忆参数/存储错误（面向调用方，消息可直接展示）。"""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _tokens(text: str) -> set[str]:
    raw = _TOKEN_RE.findall((text or "").lower())
    out = set(raw)
    # 中文双字：单字召回噪音大，双字明显更准；不引入分词依赖。
    for left, right in zip(raw, raw[1:], strict=False):
        if len(left) == 1 and len(right) == 1 and "\u4e00" <= left <= "\u9fff" \
                and "\u4e00" <= right <= "\u9fff":
            out.add(left + right)
    return out


def _row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "scope": row["scope"],
        "type": row["type"],
        "title": row["title"],
        "body": row["body"],
        "keywords": row.get("keywords") or "",
        "revision": int(row.get("revision") or 1),
        "createdAt": int(row["created_at"]),
        "updatedAt": int(row["updated_at"]),
        "expiresAt": row.get("expires_at"),
    }


def _validate(*, title: str, body: str, scope: str, type: str, keywords: str,
              expires_at: int | None) -> None:
    if scope not in SCOPES:
        raise MemoryError(f"scope 只能是 {' / '.join(sorted(SCOPES))}")
    if type not in TYPES:
        raise MemoryError(f"type 只能是 {' / '.join(sorted(TYPES))}")
    if not title.strip() or len(title) > MAX_TITLE:
        raise MemoryError(f"title 必须非空且 ≤{MAX_TITLE} 字符")
    if not body.strip() or len(body) > MAX_BODY:
        raise MemoryError(f"body 必须非空且 ≤{MAX_BODY} 字符")
    if len(keywords) > MAX_KEYWORDS:
        raise MemoryError(f"keywords ≤{MAX_KEYWORDS} 字符")
    if expires_at is not None and not isinstance(expires_at, int):
        raise MemoryError("expiresAt 必须是毫秒时间戳或 null")


def add(*, title: str, body: str, scope: str = "project", type: str = "project",
        keywords: str = "", expires_at: int | None = None) -> dict[str, Any]:
    _validate(title=title, body=body, scope=scope, type=type,
              keywords=keywords, expires_at=expires_at)
    now = _now_ms()
    memory_id = uuid.uuid4().hex[:16]
    db.execute(
        """INSERT INTO agent_memory
           (id, scope, type, title, body, keywords, revision, created_at, updated_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
        (memory_id, scope, type, title.strip(), body.strip(), keywords.strip(),
         now, now, expires_at),
    )
    return get(memory_id) or {}


def get(memory_id: str) -> dict[str, Any] | None:
    row = db.query_one("SELECT * FROM agent_memory WHERE id = ?", (memory_id,))
    return _row(row) if row else None


def list_memories(*, scope: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    if scope is not None and scope not in SCOPES:
        raise MemoryError(f"scope 只能是 {' / '.join(sorted(SCOPES))}")
    limit = max(1, min(int(limit), MAX_ROWS))
    if scope:
        rows = db.query(
            "SELECT * FROM agent_memory WHERE scope = ? ORDER BY updated_at DESC LIMIT ?",
            (scope, limit))
    else:
        rows = db.query(
            "SELECT * FROM agent_memory ORDER BY updated_at DESC LIMIT ?", (limit,))
    return [_row(row) for row in rows]


def search(query: str, *, scope: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
    """按词元重叠排序；只返回**未过期**的记忆。"""
    limit = max(1, min(int(limit), 20))
    wanted = _tokens(query or "")
    now = _now_ms()
    if scope:
        rows = db.query(
            """SELECT * FROM agent_memory
               WHERE scope = ? AND (expires_at IS NULL OR expires_at > ?)
               ORDER BY updated_at DESC LIMIT ?""",
            (scope, now, MAX_ROWS))
    else:
        rows = db.query(
            """SELECT * FROM agent_memory
               WHERE (expires_at IS NULL OR expires_at > ?)
               ORDER BY updated_at DESC LIMIT ?""",
            (now, MAX_ROWS))
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        if not wanted:
            scored.append((0, row))
            continue
        score = 0
        score += 3 * len(wanted & _tokens(row["title"]))
        score += 2 * len(wanted & _tokens(row.get("keywords") or ""))
        score += len(wanted & _tokens(row["body"]))
        if score:
            scored.append((score, row))
    scored.sort(key=lambda item: (item[0], int(item[1]["updated_at"])), reverse=True)
    return [{**_row(row), "score": score} for score, row in scored[:limit]]


def delete(memory_id: str) -> bool:
    return db.execute("DELETE FROM agent_memory WHERE id = ?", (memory_id,)) > 0


def count() -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM agent_memory")
    return int(row["n"] if row else 0)
