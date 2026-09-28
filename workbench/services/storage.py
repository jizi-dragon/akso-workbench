"""SQLite 数据访问（会话/消息/设置/问答度量）。

按表分组；只做数据存取，不含业务规则。所有函数返回 camelCase 键，
与前端 JSON 契约一致。（知识库相关函数已随功能下线移除；documents/doc_chunks
表由迁移历史保留，不再读写。）
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


#: `messages.kind` 的取值。**与 `role` 正交**：`role` 是发给模型的协议字段
#: （system/user/assistant/tool），`kind` 是**我们自己的**分类维度。
#:
#: - `message` —— 真实对话内容（含 `note`，那是界面事件，见 `approvals.NOTE_ROLE`）
#: - `error`   —— 系统记录（这一轮失败了）。**必须留在界面上、绝不能进模型上下文**：
#:   模型读到"自己说过"一句它从没说过的话，会据此继续推理（计划 §5.4b）
#: - `tool`    —— 工具回填（**预留**：当前工具观测不落库，见计划 §7.3 的 transcript 落库）
MESSAGE_KIND_DEFAULT = "message"
MESSAGE_KIND_ERROR = "error"


def insert_message(conv_id: str, role: str, content: str,
                   *, kind: str = MESSAGE_KIND_DEFAULT,
                   tool_calls: list[dict] | None = None,
                   tool_call_id: str = "") -> str:
    """落一条消息。`kind` 决定它**会不会进模型上下文**（由 `_model_history` 过滤）。

    ## `seq` 在这里自动分配（调用方不用管）

    `seq` 是**会话内单调递增**的序号，也是 `list_messages` 唯一的排序键。
    ★ 交给调用方传是错的：它要的是"上一条是多少"，而那个数字**只有写的时候
    才拿得到** —— 让每个调用方自己查一次 `MAX(seq)` 既慢又会在并发下重号。

    ## 为什么排序不能用 `created_at`

    它是**毫秒**。实测 8 条连续插入的消息落在 **7 个毫秒**里 —— 并列是常态。
    一条没有决胜列的 `ORDER BY` 顺序在 SQL 语义上是**未定义的**
    （今天看着对，是因为简单扫描恰好按 rowid 返回）。
    ★ 回合只有 2 条消息时无所谓；**加上工具轨迹后一个回合有 3–6 条背靠背写入**，
    那时"assistant 排在它的 tool 结果后面"会让整段历史**非法**（I16）。
    """
    msg_id = new_id()
    row = db.query_one(
        "SELECT COALESCE(MAX(seq), 0) AS n FROM messages WHERE conversation_id = ?",
        (conv_id,),
    )
    seq = int(row["n"] if row else 0) + 1
    db.execute(
        "INSERT INTO messages (id, conversation_id, role, content, kind, created_at, "
        "tool_calls, tool_call_id, seq) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (msg_id, conv_id, role, content, kind, now_ms(),
         json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
         tool_call_id or None, seq),
    )
    return msg_id


def list_messages(conv_id: str) -> list[dict]:
    """会话的全部消息（**含错误记录**），供界面渲染。

    ⚠️ 这里**刻意不过滤** `kind='error'`：界面需要看见"这一轮失败了"，
    而"哪些能发给模型"是**另一个问题**，它的答案在唯一那个扼流点
    （`routes_chat._model_history`）里。把过滤放在这里会让"界面的数据源"与
    "模型的数据源"变成同一条查询的两个变体 —— 那正是扼流点要防的分散。

    ★ 排序键是 `seq`（见 `insert_message`）。`created_at` 只作**兜底**：
    迁移 25 已把既有行的 `seq` 回填成 `rowid`，所以正常情况下它不会生效。
    """
    return db.query(
        "SELECT id, conversation_id AS conversationId, role, content, kind, "
        "tool_calls, tool_call_id AS toolCallId, seq, "
        "created_at AS createdAt FROM messages WHERE conversation_id = ? "
        "ORDER BY seq ASC, created_at ASC",
        (conv_id,),
    )


def model_transcript(conv_id: str) -> list[dict]:
    """会话里**曾经发给模型的那几条消息**，按原样翻回模型形状（计划 §7.3）。

    ## 它存在的理由：让"当时发出去了什么"变成**可复算**的

    `_model_history` 是"从界面数据源翻出模型要的东西"；这个函数是
    "把**当时发出去的那一条**原样取回来"。两者看起来像重复，其实是两个方向：
    - `_model_history`：**构造**下一轮要发的（含 `note` → `user` 的翻译、附件展开）
    - `model_transcript`：**取回**已经发过的（一个字段都不翻译）

    ★ 判据在 `tests/test_transcript_roundtrip.py`：录一次真实运行，
    从库里取回的这一段必须与**录下来的那次请求**逐字段相等。
    没有它，"逐字节"只是一句口号。

    ## 只翻 `assistant` / `tool`

    `user` 消息由 `_model_history` 走扼流点负责（它要处理附件展开与 `note` 翻译），
    这里**不重复那条路径** —— 两条路各翻一次就会出现"两个会漂的事实源"（铁律 28）。
    """
    out: list[dict] = []
    for row in list_messages(conv_id):
        role = str(row.get("role") or "")
        if role == "assistant":
            message: dict = {"role": "assistant", "content": row.get("content") or ""}
            raw = row.get("tool_calls")
            if raw:
                try:
                    calls = json.loads(raw)
                except (TypeError, ValueError):
                    calls = None
                if calls:
                    message["tool_calls"] = calls
            out.append(message)
        elif role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": row.get("toolCallId") or "",
                "content": row.get("content") or "",
            })
    return out


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
