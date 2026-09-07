"""知识工作台路由：概览 / 文档入库 / 删除 / 重建索引 / 检索测试。"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..harness.tools import default_registry
from ..services import chunking, embedding, retrieval, storage
from ..services.settings import load_embedding

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

registry = default_registry()
EMBED_BATCH = 12


class DocumentBody(BaseModel):
    name: str
    content: str


class SearchBody(BaseModel):
    query: str


@router.get("")
def get_knowledge() -> dict:
    chunks = storage.list_chunks()
    documents = [
        {**doc, "chunkCount": sum(1 for c in chunks if c["documentId"] == doc["id"])}
        for doc in storage.list_documents()
    ]
    return {
        "documents": documents,
        "chunks": [
            {"id": c["id"], "documentId": c["documentId"], "chunkIndex": c["chunkIndex"],
             "documentName": c.get("documentName"), "hasEmbedding": bool(c.get("embedding"))}
            for c in chunks
        ],
        "metrics": storage.list_metrics(200),
    }


@router.post("/documents")
async def upload_document(body: DocumentBody) -> dict:
    if not body.content.strip():
        raise HTTPException(400, "文档内容为空")

    doc = storage.insert_document(body.name, body.content)
    chunks = storage.insert_chunks(doc["id"], chunking.split_into_chunks(body.content))
    await _embed_chunks(chunks)
    return {
        "document": {**doc, "chunkCount": len(chunks)},
        "chunkCount": len(chunks),
    }


async def _embed_chunks(chunks: list[dict]) -> None:
    """块级向量化；失败即停（精确/关键词检索仍可用，可稍后重建索引）。"""
    emb = load_embedding()
    if not emb.ready or not chunks:
        return
    for start in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[start : start + EMBED_BATCH]
        try:
            vectors = await embedding.embed_texts([c["content"] for c in batch], emb)
        except Exception:  # noqa: BLE001
            return
        for chunk, vector in zip(batch, vectors, strict=False):
            storage.update_chunk_embedding(chunk["id"], vector)


@router.delete("/documents/{doc_id}")
def remove_document(doc_id: str) -> dict:
    storage.delete_document(doc_id)
    return {"ok": True}


@router.get("/documents/{doc_id}")
def document_detail(doc_id: str) -> dict:
    doc = storage.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "文档不存在")
    return doc


@router.post("/reindex")
async def reindex() -> dict:
    """为所有缺向量的节点与文本块重建索引（同步执行，量级大时应改任务队列）。"""
    emb = load_embedding()
    if not emb.ready:
        raise HTTPException(400, "请先配置 Embedding API Key")

    jobs: list[tuple[str, str, str]] = []  # (kind, id, text)
    for chunk in storage.list_chunks():
        if not chunk.get("embedding"):
            jobs.append(("chunk", chunk["id"], chunk["content"]))

    indexed = failed = 0
    first_error: str | None = None
    for start in range(0, len(jobs), EMBED_BATCH):
        batch = jobs[start : start + EMBED_BATCH]
        try:
            vectors = await embedding.embed_texts([j[2] for j in batch], emb)
        except Exception as exc:  # noqa: BLE001
            if indexed == 0 and failed == 0:
                first_error = str(exc)
            failed += len(batch)
            continue
        for (_kind, item_id, _), vector in zip(batch, vectors, strict=False):
            storage.update_chunk_embedding(item_id, vector)
            indexed += 1

    if first_error and indexed == 0:
        raise HTTPException(502, first_error)
    return {"indexed": indexed, "failed": failed}


# ———— 检索测试台（也挂载在 /api/search，供对话前复用） ————

search_router = APIRouter(prefix="/api", tags=["search"])


@search_router.post("/search")
async def search(body: SearchBody) -> dict:
    started = time.perf_counter()
    result = await _search(body.query)
    return {**result, "elapsedMs": int((time.perf_counter() - started) * 1000)}


async def _search(query: str) -> dict:
    emb = load_embedding()

    async def embed_query(text: str) -> list[float]:
        return await embedding.embed_text(text, emb)

    return await retrieval.search_knowledge(
        query,
        storage.list_chunks(),
        embed_query if emb.ready else None,
    )
