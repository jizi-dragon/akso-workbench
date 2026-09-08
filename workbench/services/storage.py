"""SQLite 数据访问（会话/消息/设置/问答度量）。

按表分组；只做数据存取，不含业务规则。所有函数返回 camelCase 键，
与前端 JSON 契约一致。（知识库相关函数已随功能下线移除；documents/doc_chunks
表由迁移历史保留，不再读写。）
"""

from __future__ import annotations

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
