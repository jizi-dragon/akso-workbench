"""上下文管理：token 估算 + 阈值裁剪（按轮次）。"""

from __future__ import annotations

import re

_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[a-zA-Z0-9_]+")

#: 一张图进模型大概折合多少 token（**估个量级，不追求精确**）。
#:
#: 为什么要一个常数：多模态请求里图片**不占文本 token**，但**确实占上下文与账单**
#: （实测：一张 8×8 的 PNG 也让 `prompt_tokens` 从 20 量级涨到 223）。
#: 不折算的话，`build_context` 会以为"这条消息几乎不占地方"，于是图片一直不被折叠、
#: 越堆越多——而它们是**唯一**会随轮次悄悄推高成本的东西。
#: 600 是保守取法：真实值随分辨率走（远小于一张大图、远大于一个短句）。
IMAGE_TOKEN_ESTIMATE = 600


def content_to_text(content: object) -> str:
    """把 content（字符串 **或** 多模态 parts）压成可估/可摘要的文本。

    唯一真源：`estimate_*` 与摘要折叠都从这里走，别在别处再写一遍 isinstance 分支
    （那正是"多模态一上线，某条老路径就静默算错"的成因）。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                chunks.append(str(part.get("text") or ""))
            elif part.get("type") == "image_url":
                chunks.append("[图片]")
            else:
                chunks.append("[附件]")
        return "\n".join(chunks)
    return "" if content is None else str(content)


def estimate_tokens(text: str) -> int:
    return len(_CJK.findall(text)) + len(_WORD.findall(text))


def estimate_content_tokens(content: object) -> int:
    """content（字符串或 parts）折合多少 token：文本按字算 + 每张图一个常数。"""
    if isinstance(content, list):
        images = sum(1 for part in content
                     if isinstance(part, dict) and part.get("type") == "image_url")
        return estimate_tokens(content_to_text(content)) + images * IMAGE_TOKEN_ESTIMATE
    return estimate_tokens(content_to_text(content))


def estimate_messages_tokens(messages: list[dict]) -> int:
    return sum(estimate_content_tokens(m.get("content")) + 4 for m in messages)


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
