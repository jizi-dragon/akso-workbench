"""文档块混合检索：精确命中优先（函数名/参数名/引号短语/中文长词），
千问向量语义兜底（cos > 0.32 才启用），词汇重合补足。

FAQ 卡片与知识点图谱层已下线（2026-08）：检索聚焦钉钉镜像与上传文档的文本块。
"""

from __future__ import annotations

import math
import re
import time
from datetime import datetime
from typing import Awaitable, Callable

EmbedFn = Callable[[str], Awaitable[list[float]]]

# ———— 打分参数（调优入口，全部集中于此） ————
EXACT_BASE = 0.42        # 精确命中一次的基础分
EXACT_STEP = 0.16        # 每多命中一个精确词的增量
TITLE_BONUS = 0.18       # 精确词出现在块首行（标题）的加成
VECTOR_GATE = 0.32       # 余弦低于此值不启用向量融合（防向量噪音压过精确命中）
VECTOR_WEIGHT = 0.55     # 向量分融合权重
LEX_WEIGHT = 0.45        # 词汇分融合权重
CHUNK_MIN_SCORE = 0.16   # 文档块入选阈值
STALE_DAYS = 90          # 镜像知识陈旧阈值（天）：超过则降权并提示
STALE_PENALTY = 0.88     # 陈旧块惩罚系数

_QUOTED = re.compile(r"[“”\"'`]([^“”\"'`]{2,})[“”\"'`]")
_CODE_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{2,}")
_CJK_PHRASE = re.compile(r"[\u4e00-\u9fff]{4,}")


def tokenize(text: str) -> list[str]:
    """中文二元组 + 英文/数字词（≥3 位），去重后截断到 400。"""
    tokens: list[str] = []
    cjk_text = "".join(ch for ch in text if "\u4e00" <= ch <= "\u9fff")
    for i in range(len(cjk_text) - 1):
        tokens.append(cjk_text[i : i + 2])
    for word in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text.lower()):
        tokens.append(word)
    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            result.append(token)
    return result[:400]


def extract_exact_terms(query: str) -> list[str]:
    terms: list[str] = []
    terms.extend(_CODE_TOKEN.findall(query))
    terms.extend(m.group(1) for m in _QUOTED.finditer(query))
    terms.extend(_CJK_PHRASE.findall(query))
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        key = term.lower()
        if key not in seen:
            seen.add(key)
            result.append(term)
    return result[:24]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


def _lexical_score(text: str, tokens: list[str]) -> int:
    text_tokens = set(tokenize(text))
    return sum(1 for t in tokens if t in text_tokens)


async def search_knowledge(
    query: str,
    chunks: list[dict],
    embed_query: EmbedFn | None,
    limit: int = 6,
) -> dict:
    """返回 {"hits": [...], "contextBlock": str, "hitKind": str}"""
    query = query.strip()
    if not query:
        return {"hits": [], "contextBlock": "", "hitKind": "none"}

    terms = extract_exact_terms(query)
    tokens = tokenize(query)
    if not tokens:
        return {"hits": [], "contextBlock": "", "hitKind": "none"}

    query_vector: list[float] | None = None
    if embed_query is not None:
        try:
            query_vector = await embed_query(query)
        except Exception:
            query_vector = None

    hits: list[dict] = []

    # —— 文档块打分 ——
    max_lex = 0.0
    lex_by_id: dict[str, float] = {}
    for chunk in chunks:
        lex = _lexical_score(chunk["content"], tokens)
        norm = lex / max(1.0, math.sqrt(len(tokens)))
        lex_by_id[chunk["id"]] = norm
        max_lex = max(max_lex, norm)

    for chunk in chunks:
        lower = chunk["content"].lower()
        exact = sum(1 for term in terms if term.lower() in lower)
        first_line = chunk["content"].split("\n")[0].lower()
        title_hit = any(t.lower() in first_line for t in terms)
        lex_norm = lex_by_id.get(chunk["id"], 0.0) / max_lex if max_lex > 0 else 0.0
        vector = chunk.get("embedding")
        has_vector = bool(vector) and query_vector is not None
        cos = cosine_similarity(query_vector or [], vector or []) if has_vector else 0.0
        cos_norm = max(0.0, cos)

        if exact > 0:
            score = min(1.0, EXACT_BASE + (exact - 1) * EXACT_STEP + (TITLE_BONUS if title_hit else 0.0))
            method = "exact"
        elif has_vector and cos_norm > VECTOR_GATE:
            score = VECTOR_WEIGHT * cos_norm + LEX_WEIGHT * lex_norm
            method = "vector"
        else:
            score = lex_norm
            method = "keyword"

        updated_ms = chunk.get("sourceUpdatedAt")
        age_days = (time.time() * 1000 - updated_ms) / 86_400_000 if updated_ms and updated_ms > 0 else None
        if age_days is not None and age_days > STALE_DAYS:
            score *= STALE_PENALTY

        if score > CHUNK_MIN_SCORE:
            doc_name = chunk.get("documentName")
            hits.append({
                "sourceType": "chunk",
                "id": chunk["id"],
                "title": first_line[:44] or doc_name or f"块 {chunk['chunkIndex'] + 1}",
                "content": chunk["content"],
                "score": min(1.0, score),
                "ref": f"《{doc_name}》#{chunk['chunkIndex'] + 1}" if doc_name else f"文档块 #{chunk['chunkIndex'] + 1}",
                "method": method,
                "updatedAt": updated_ms if updated_ms and updated_ms > 0 else None,
                "originUrl": chunk.get("originUrl"),
            })

    hits.sort(key=lambda h: h["score"], reverse=True)
    hits = hits[: limit + 2]

    hit_kind = hits[0]["method"] if hits else "none"

    return {"hits": hits, "contextBlock": _context_block(hits), "hitKind": hit_kind}


def _context_block(hits: list[dict]) -> str:
    if not hits:
        return ""
    lines: list[str] = []
    for index, hit in enumerate(hits):
        content = hit["content"][:380] + ("…" if len(hit["content"]) > 380 else "")
        updated_note = ""
        if hit.get("updatedAt"):
            updated_note = f"（更新于 {datetime.fromtimestamp(hit['updatedAt'] / 1000):%Y-%m-%d}）"
        lines.append(f"{index + 1}. 【文档】{hit['ref']}{updated_note}：{content}")
    return "\n\n".join(lines)
