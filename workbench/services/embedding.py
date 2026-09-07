"""千问（DashScope）文本向量客户端 —— OpenAI 兼容 /embeddings。

设置（Key/模型/端点）由 services.settings 统一管理；
向量仍以 JSON 文本存 SQLite TEXT 列，个人量级下内存余弦足够。
"""

from __future__ import annotations

import httpx

from .settings import EmbeddingSettings, load_embedding

MAX_BATCH = 16
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class EmbeddingError(Exception):
    pass


async def embed_texts(texts: list[str], settings: EmbeddingSettings | None = None) -> list[list[float]]:
    """批量向量化；空输入返回 []，顺序与输入一一对应（空串位置返回占位 []）。

    注意：返回列表长度等于去空后的文本数，调用方按 zip 顺序消费。
    """
    cfg = settings or load_embedding()
    if not cfg.ready:
        raise EmbeddingError("未配置 Embedding API Key")

    pairs = [(text, index) for index, text in enumerate(texts) if text.strip()]
    if not pairs:
        return []

    base_url = cfg.base_url.rstrip("/")
    results: list[list[float] | None] = [None] * len(texts)

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for start in range(0, len(pairs), MAX_BATCH):
            batch = pairs[start : start + MAX_BATCH]
            try:
                res = await client.post(
                    f"{base_url}/embeddings",
                    headers={"Authorization": f"Bearer {cfg.api_key}"},
                    json={"model": cfg.model, "input": [t for t, _ in batch]},
                )
            except httpx.HTTPError as exc:
                raise EmbeddingError(f"无法连接 Embedding 服务：{exc}") from exc
            if res.status_code != 200:
                raise EmbeddingError(_parse_error(res.text, res.status_code))
            data = res.json().get("data") or []
            ordered = sorted(data, key=lambda item: item.get("index") or 0)
            for slot, item in enumerate(ordered):
                vector = item.get("embedding") or []
                if not vector:
                    raise EmbeddingError("向量接口返回了空向量")
                results[batch[slot][1]] = vector

    return [v for v in results if v is not None]


async def embed_text(text: str, settings: EmbeddingSettings | None = None) -> list[float]:
    vectors = await embed_texts([text], settings)
    if not vectors:
        raise EmbeddingError("向量接口未返回结果")
    return vectors[0]


def _parse_error(body: str, status: int) -> str:
    import json

    try:
        parsed = json.loads(body)
        message = parsed.get("error", {}).get("message") or parsed.get("message")
        if isinstance(message, str) and message:
            return message
    except ValueError:
        pass
    return body[:200] if body else f"Embedding 请求失败（{status}）"
