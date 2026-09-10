"""SQLite 访问层：连接管理、迁移（兼容旧 Tauri/sqlx 的 _sqlx_migrations 表）。

设计要点：
- 单连接 + 线程锁（本地单用户场景，足够且最简单）
- 迁移机制沿用旧版的 _sqlx_migrations 表，保证同一个 mogul.db 在
  旧 Tauri 版与本 Python 版之间无缝互认（版本 1-4 已含知识工作台全部表）
- 迁移 1-6 为 fork 历史（含已下线的知识库表结构），按不可变纪律保留；
  迁移 7-9 为 Workbench 新增（账号库/任务台账/Agent 审计）
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from typing import Any, Iterable

from . import config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "create_conversations_messages_settings", """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""),
    (2, "create_knowledge_base", """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_nodes (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    document_id TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS knowledge_edges (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (source_id) REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES knowledge_nodes(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_knowledge_nodes_title ON knowledge_nodes(title);
"""),
    (3, "add_embedding_column", """
ALTER TABLE knowledge_nodes ADD COLUMN embedding TEXT;
"""),
    (4, "create_workbench_tables", """
CREATE TABLE IF NOT EXISTS doc_chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding TEXT,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_document ON doc_chunks(document_id);
CREATE TABLE IF NOT EXISTS faq_cards (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '',
    answer TEXT NOT NULL,
    source_ref TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    category TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS qa_metrics (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    hit TEXT NOT NULL DEFAULT 'none',
    feedback INTEGER,
    latency_ms INTEGER,
    created_at INTEGER NOT NULL
);
"""),
    (5, "add_dingtalk_sync_fields", """
ALTER TABLE documents ADD COLUMN source_id TEXT;
ALTER TABLE documents ADD COLUMN origin_url TEXT;
ALTER TABLE documents ADD COLUMN source_updated_at INTEGER;
ALTER TABLE documents ADD COLUMN synced_at INTEGER;
ALTER TABLE doc_chunks ADD COLUMN chunk_hash TEXT;
ALTER TABLE doc_chunks ADD COLUMN valid_to INTEGER;
CREATE INDEX IF NOT EXISTS idx_documents_source_id ON documents(source_id);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_valid ON doc_chunks(valid_to);
"""),
    (6, "drop_knowledge_graph_and_faq", """
DROP TABLE IF EXISTS knowledge_edges;
DROP TABLE IF EXISTS knowledge_nodes;
DROP TABLE IF EXISTS faq_cards;
"""),
    (7, "create_unified_accounts", """
CREATE TABLE IF NOT EXISTS platform_env (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS account (
    id TEXT PRIMARY KEY,
    env_id TEXT NOT NULL,
    username TEXT NOT NULL,
    password_enc TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'idle',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (env_id) REFERENCES platform_env(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_account_env ON account(env_id);
"""),
    (8, "create_task_runs_and_logs", """
CREATE TABLE IF NOT EXISTS blueprint_jobs (
    id TEXT PRIMARY KEY,
    module TEXT NOT NULL DEFAULT 'akso-auto',
    command TEXT NOT NULL,
    blueprint_path TEXT NOT NULL DEFAULT '',
    env_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    exit_code INTEGER,
    log_path TEXT NOT NULL DEFAULT '',
    artifact_dir TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS insight_runs (
    id TEXT PRIMARY KEY,
    module TEXT NOT NULL DEFAULT 'akso-cc',
    command TEXT NOT NULL,
    objects TEXT NOT NULL DEFAULT '',
    env_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    exit_code INTEGER,
    log_path TEXT NOT NULL DEFAULT '',
    artifact_dir TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS job_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    line TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_logs_job ON job_logs(job_id);
CREATE INDEX IF NOT EXISTS idx_blueprint_jobs_status ON blueprint_jobs(status);
CREATE INDEX IF NOT EXISTS idx_insight_runs_status ON insight_runs(status);
"""),
    (9, "create_agent_audit", """
CREATE TABLE IF NOT EXISTS agent_audit (
    id TEXT PRIMARY KEY,
    tool TEXT NOT NULL,
    args TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'ok',
    result_summary TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_audit_tool ON agent_audit(tool);
"""),
    (10, "add_account_pool", """
ALTER TABLE account ADD COLUMN pool TEXT NOT NULL DEFAULT '';
"""),
    (11, "add_account_box", """
ALTER TABLE account ADD COLUMN box TEXT NOT NULL DEFAULT '';
"""),
    (12, "add_account_tab_name", """
ALTER TABLE account ADD COLUMN tab_name TEXT NOT NULL DEFAULT '';
"""),
]


def connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            path = config.bootstrap_database()
            _conn = sqlite3.connect(str(path), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode = WAL;")
            _conn.execute("PRAGMA foreign_keys = ON;")
            _migrate(_conn)
        return _conn


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS _sqlx_migrations (
            version BIGINT PRIMARY KEY,
            description TEXT NOT NULL,
            installed_on TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            success BIGINT NOT NULL DEFAULT 1,
            checksum BLOB,
            execution_time_in_millis BIGINT
        )"""
    )
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS v FROM _sqlx_migrations"
    ).fetchone()
    current = int(row["v"])
    for version, description, sql in MIGRATIONS:
        if version <= current:
            continue
        started = time.perf_counter()
        conn.executescript(sql)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        _record_migration(conn, version, description, sql, elapsed_ms)
        conn.commit()


def _record_migration(
    conn: sqlite3.Connection,
    version: int,
    description: str,
    sql: str,
    elapsed_ms: int,
) -> None:
    """写入迁移记录，按实际表结构适配列差异。

    新库由上方 CREATE TABLE 建表（execution_time_in_millis 可空）；
    旧 Tauri 库的表由 sqlx 变体创建（checksum BLOB NOT NULL、execution_time BIGINT NOT NULL）。
    动态读取 PRAGMA table_info，对存在的已知列赋值，对未知 NOT NULL 无默认值列填充中性值，
    避免接管旧库时逐列触发 IntegrityError。
    """
    candidates: dict[str, Any] = {
        "version": version,
        "description": description,
        "success": 1,
        # checksum 按 sqlx 语义取迁移 SQL 的 SHA-384 摘要
        "checksum": hashlib.sha384(sql.encode("utf-8")).digest(),
        "execution_time": elapsed_ms,
        "execution_time_in_millis": elapsed_ms,
    }
    names: list[str] = []
    params: list[Any] = []
    for row in conn.execute("PRAGMA table_info(_sqlx_migrations)"):
        _cid, name, ctype, notnull, dflt, _pk = tuple(row)
        if name in candidates:
            names.append(name)
            params.append(candidates[name])
        elif notnull and dflt is None:
            names.append(name)
            params.append(0 if (ctype or "").upper() in {"BIGINT", "INTEGER", "INT", "BOOLEAN"} else "")
    conn.execute(
        f"INSERT INTO _sqlx_migrations ({', '.join(names)}) "
        f"VALUES ({', '.join('?' for _ in names)})",
        params,
    )


def query(sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    with _lock:
        conn = connect()
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]


def query_one(sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    """执行写语句并提交，返回受影响行数。"""
    with _lock:
        conn = connect()
        cursor = conn.execute(sql, tuple(params))
        conn.commit()
        return cursor.rowcount
