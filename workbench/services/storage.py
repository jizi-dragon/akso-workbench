"""SQLite 数据访问（会话/消息/设置/文档块/同步/度量）。

按表分组；只做数据存取，不含业务规则。所有函数返回 camelCase 键，
与前端 JSON 契约一致。
"""

from __future__ import annotations

import json
import time
import uuid

from .. import db


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id() -> str:
    return str(uuid.uuid4())


# ———— 设置 ————

def get_setting(key: str) -> str | None:
    row = db.query_one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# ———— 会话与消息 ————

def list_conversations() -> list[dict]:
    return db.query(
        "SELECT id, title, created_at AS createdAt, updated_at AS updatedAt "
        "FROM conversations ORDER BY updated_at DESC"
    )


def create_conversation(title: str = "新会话") -> dict:
    conv = {
        "id": new_id(),
        "title": title,
        "createdAt": now_ms(),
        "updatedAt": now_ms(),
    }
    db.execute(
        "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (conv["id"], conv["title"], conv["createdAt"], conv["updatedAt"]),
    )
    return conv


def update_conversation_title(conv_id: str, title: str) -> None:
    db.execute(
        "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
        (title, now_ms(), conv_id),
    )


def delete_conversation(conv_id: str) -> None:
    db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))


def insert_message(conv_id: str, role: str, content: str) -> str:
    msg_id = new_id()
    db.execute(
        "INSERT INTO messages (id, conversation_id, role, content, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (msg_id, conv_id, role, content, now_ms()),
    )
    return msg_id


def list_messages(conv_id: str) -> list[dict]:
    return db.query(
        "SELECT id, conversation_id AS conversationId, role, content, "
        "created_at AS createdAt FROM messages WHERE conversation_id = ? "
        "ORDER BY created_at ASC",
        (conv_id,),
    )


def count_user_messages(conv_id: str) -> int:
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ? AND role = 'user'",
        (conv_id,),
    )
    return int(row["n"]) if row else 0


# ———— 知识文档 / 文本块 ————

def insert_document(name: str, content: str) -> dict:
    doc = {"id": new_id(), "name": name, "source": "upload", "content": content, "createdAt": now_ms()}
    db.execute(
        "INSERT INTO documents (id, name, source, content, created_at) VALUES (?, ?, ?, ?, ?)",
        (doc["id"], doc["name"], doc["source"], doc["content"], doc["createdAt"]),
    )
    return doc


def list_documents() -> list[dict]:
    """文档列表（不含正文——列表量大时避免臃肿，详情走 get_document）。"""
    return db.query(
        "SELECT id, name, source, created_at AS createdAt, source_id AS sourceId, "
        "origin_url AS originUrl, source_updated_at AS sourceUpdatedAt, synced_at AS syncedAt "
        "FROM documents ORDER BY created_at ASC"
    )


def get_document(doc_id: str) -> dict | None:
    return db.query_one(
        "SELECT id, name, source, content, created_at AS createdAt, source_id AS sourceId, "
        "origin_url AS originUrl, source_updated_at AS sourceUpdatedAt, synced_at AS syncedAt "
        "FROM documents WHERE id = ?",
        (doc_id,),
    )


def delete_document(doc_id: str) -> None:
    db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))


def insert_chunks(doc_id: str, chunks: list[str]) -> list[dict]:
    result = []
    for index, content in enumerate(chunks):
        chunk = {"id": new_id(), "documentId": doc_id, "chunkIndex": index, "content": content}
        db.execute(
            "INSERT INTO doc_chunks (id, document_id, chunk_index, content) VALUES (?, ?, ?, ?)",
            (chunk["id"], doc_id, index, content),
        )
        result.append(chunk)
    return result


def list_chunks(valid_only: bool = True) -> list[dict]:
    """列出文本块。默认只取有效块（valid_to IS NULL）——墓碑块不参与检索/重建索引。"""
    where = "WHERE c.valid_to IS NULL" if valid_only else ""
    rows = db.query(
        "SELECT c.id, c.document_id AS documentId, c.chunk_index AS chunkIndex, "
        "c.content, c.embedding, c.chunk_hash AS chunkHash, c.valid_to AS validTo, "
        "d.name AS documentName, d.origin_url AS originUrl, "
        "d.source_updated_at AS sourceUpdatedAt, d.synced_at AS syncedAt, d.source AS documentSource "
        "FROM doc_chunks c LEFT JOIN documents d ON d.id = c.document_id "
        f"{where} "
        "ORDER BY c.document_id ASC, c.chunk_index ASC"
    )
    for row in rows:
        row["embedding"] = db.parse_embedding(row.pop("embedding"))
    return rows


def update_chunk_embedding(chunk_id: str, vector: list[float]) -> None:
    db.execute("UPDATE doc_chunks SET embedding = ? WHERE id = ?", (json.dumps(vector), chunk_id))


# ———— 钉钉知识库镜像（documents.source = 'dingtalk'） ————

def get_document_by_source_id(source_id: str) -> dict | None:
    return db.query_one(
        "SELECT id, name, source, content, source_id AS sourceId, origin_url AS originUrl, "
        "source_updated_at AS sourceUpdatedAt, synced_at AS syncedAt "
        "FROM documents WHERE source_id = ?",
        (source_id,),
    )


def upsert_dingtalk_document(
    source_id: str, name: str, content: str, origin_url: str, source_updated_at: int, synced_at: int
) -> dict:
    """按钉钉 nodeId 幂等写入/更新镜像文档，返回 {id, isNew}。"""
    existing = get_document_by_source_id(source_id)
    if existing:
        db.execute(
            "UPDATE documents SET name = ?, content = ?, origin_url = ?, "
            "source_updated_at = ?, synced_at = ? WHERE id = ?",
            (name, content, origin_url, source_updated_at, synced_at, existing["id"]),
        )
        return {"id": existing["id"], "isNew": False}
    doc_id = new_id()
    db.execute(
        "INSERT INTO documents (id, name, source, content, created_at, "
        "source_id, origin_url, source_updated_at, synced_at) VALUES (?, ?, 'dingtalk', ?, ?, ?, ?, ?, ?)",
        (doc_id, name, content, now_ms(), source_id, origin_url, source_updated_at, synced_at),
    )
    return {"id": doc_id, "isNew": True}


def mark_document_removed(doc_id: str, synced_at: int) -> None:
    """文档在钉钉侧消失（删除/撤回/移动出可见范围）：置 -1 标记，保留审计痕迹。"""
    db.execute(
        "UPDATE documents SET source_updated_at = -1, synced_at = ? WHERE id = ?",
        (synced_at, doc_id),
    )


def list_valid_chunk_hashes(doc_id: str) -> list[dict]:
    return db.query(
        "SELECT id, chunk_hash AS chunkHash FROM doc_chunks "
        "WHERE document_id = ? AND valid_to IS NULL AND chunk_hash IS NOT NULL",
        (doc_id,),
    )


def invalidate_doc_chunks(doc_id: str, chunk_hashes: list[str] | None = None) -> int:
    """墓碑：把指定文档的有效块置失效。指定 hashes 时只失效这些哈希，否则全部失效。"""
    if chunk_hashes is None:
        return db.execute(
            "UPDATE doc_chunks SET valid_to = ? WHERE document_id = ? AND valid_to IS NULL",
            (now_ms(), doc_id),
        )
    if not chunk_hashes:
        return 0
    placeholders = ", ".join("?" for _ in chunk_hashes)
    return db.execute(
        f"UPDATE doc_chunks SET valid_to = ? WHERE document_id = ? AND valid_to IS NULL "
        f"AND chunk_hash IN ({placeholders})",
        (now_ms(), doc_id, *chunk_hashes),
    )


def insert_dingtalk_chunks(doc_id: str, items: list[tuple[int, str, str]]) -> list[dict]:
    """写入带哈希的新块（items = [(chunk_index, content, chunk_hash), ...]）。"""
    result = []
    for index, content, chunk_hash in items:
        chunk = {
            "id": new_id(), "documentId": doc_id, "chunkIndex": index,
            "content": content, "chunkHash": chunk_hash,
        }
        db.execute(
            "INSERT INTO doc_chunks (id, document_id, chunk_index, content, chunk_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            (chunk["id"], doc_id, index, content, chunk_hash),
        )
        result.append(chunk)
    return result


def list_dingtalk_documents() -> list[dict]:
    return db.query(
        "SELECT id, name, source_id AS sourceId, origin_url AS originUrl, "
        "source_updated_at AS sourceUpdatedAt, synced_at AS syncedAt "
        "FROM documents WHERE source = 'dingtalk'"
    )


def sync_stats() -> dict:
    """同步度量：镜像文档/有效块规模与新鲜度分布（D4 验收指标）。"""
    docs_total = db.query_one(
        "SELECT COUNT(*) AS n FROM documents WHERE source = 'dingtalk'"
    )["n"]
    docs_removed = db.query_one(
        "SELECT COUNT(*) AS n FROM documents WHERE source = 'dingtalk' AND source_updated_at = -1"
    )["n"]
    chunks_valid = db.query_one(
        "SELECT COUNT(*) AS n FROM doc_chunks c JOIN documents d ON d.id = c.document_id "
        "WHERE d.source = 'dingtalk' AND c.valid_to IS NULL"
    )["n"]
    chunks_invalidated = db.query_one(
        "SELECT COUNT(*) AS n FROM doc_chunks c JOIN documents d ON d.id = c.document_id "
        "WHERE d.source = 'dingtalk' AND c.valid_to IS NOT NULL"
    )["n"]
    freshness = db.query_one(
        "SELECT MIN(source_updated_at) AS oldestMs, MAX(source_updated_at) AS newestMs "
        "FROM documents WHERE source = 'dingtalk' AND source_updated_at > 0"
    )
    return {
        "docsTotal": int(docs_total or 0),
        "docsRemoved": int(docs_removed or 0),
        "chunksValid": int(chunks_valid or 0),
        "chunksInvalidated": int(chunks_invalidated or 0),
        "oldestSourceUpdatedAt": freshness["oldestMs"],
        "newestSourceUpdatedAt": freshness["newestMs"],
    }


# ———— 问答度量 ————

def insert_qa_metric(question: str, hit: str, latency_ms: int) -> str:
    metric_id = new_id()
    db.execute(
        "INSERT INTO qa_metrics (id, question, hit, feedback, latency_ms, created_at) "
        "VALUES (?, ?, ?, NULL, ?, ?)",
        (metric_id, question[:500], hit, latency_ms, now_ms()),
    )
    return metric_id


def update_qa_feedback(metric_id: str, feedback: int) -> None:
    db.execute("UPDATE qa_metrics SET feedback = ? WHERE id = ?", (feedback, metric_id))


def list_metrics(limit: int = 500) -> list[dict]:
    return db.query(
        "SELECT id, question, hit, feedback, latency_ms AS latencyMs, created_at AS createdAt "
        "FROM qa_metrics ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
