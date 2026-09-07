"""上下文管理：token 估算 + 阈值裁剪（按轮次）。"""

from __future__ import annotations

import re

_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[a-zA-Z0-9_]+")


def estimate_tokens(text: str) -> int:
    return len(_CJK.findall(text)) + len(_WORD.findall(text))


def estimate_messages_tokens(messages: list[dict]) -> int:
    return sum(estimate_tokens(m.get("content") or "") + 4 for m in messages)


def split_turns(messages: list[dict]) -> list[list[dict]]:
    turns: list[list[dict]] = []
    current: list[dict] = []
    for message in messages:
        if message.get("role") == "user" and current:
            turns.append(current)
            current = []
        current.append(message)
    if current:
        turns.append(current)
    return turns


def build_context(messages: list[dict], window_tokens: int, threshold: float, keep_recent_turns: int):
    """返回 (kept_turns, truncated_messages)。未超阈值时 truncated 为空。"""
    limit = int(window_tokens * threshold)
    if estimate_messages_tokens(messages) <= limit:
        return messages, []
    turns = split_turns(messages)
    if len(turns) <= keep_recent_turns:
        return messages, []
    kept = [m for turn in turns[-keep_recent_turns:] for m in turn]
    truncated = [m for turn in turns[:-keep_recent_turns] for m in turn]
    return kept, truncated
