"""长期目标（v1 tracker）：持久化目标、状态与轮次。

## 本期做什么、不做什么

- **做**：记录目标（title/objective）、状态（active/paused/done）、已用轮次/上限；
  设置页可创建、暂停/恢复、标记完成、删除；数据落 `agent_goal`。
- **不做**：不自动续跑、不自动判定完成。自动 goal loop 需要"完成判定 + 轮次预算 +
  上下文重置"三件事，先把可追踪的记录做出来，避免用一个假的自动循环冒充完成。

`conversation_id` 预留给后续把目标绑定到会话；v1 允许为空。
"""

from __future__ import annotations

import time
import uuid

from . import db

STATUSES = {"active", "paused", "done"}
MAX_ROUNDS_LIMIT = 100


class GoalError(ValueError):
    """目标的参数/状态错误（API 转 400/404）。"""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _clean(title: str, objective: str, max_rounds: int) -> tuple[str, str, int]:
    title = (title or "").strip()
    objective = (objective or "").strip()
    if not title or len(title) > 120:
        raise GoalError("title 必须为 1–120 字符")
    if not objective or len(objective) > 4000:
        raise GoalError("objective 必须为 1–4000 字符")
    if not (1 <= int(max_rounds) <= MAX_ROUNDS_LIMIT):
        raise GoalError(f"max_rounds 必须在 1–{MAX_ROUNDS_LIMIT} 之间")
    return title, objective, int(max_rounds)


def _to_dict(row: dict) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "objective": row["objective"],
        "status": row["status"],
        "rounds": row["rounds"],
        "maxRounds": row["max_rounds"],
        "conversationId": row["conversation_id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def list_all() -> list[dict]:
    return [_to_dict(row) for row in
            db.query("SELECT * FROM agent_goal ORDER BY created_at DESC")]


def get(goal_id: str) -> dict | None:
    row = db.query_one("SELECT * FROM agent_goal WHERE id = ?", (goal_id,))
    return _to_dict(row) if row else None


def create(title: str, objective: str, max_rounds: int = 10,
           conversation_id: str = "") -> dict:
    title, objective, max_rounds = _clean(title, objective, max_rounds)
    now = _now_ms()
    goal_id = f"goal-{uuid.uuid4().hex[:12]}"
    db.execute(
        """INSERT INTO agent_goal
           (id,title,objective,status,rounds,max_rounds,conversation_id,created_at,updated_at)
           VALUES (?,?,?,'active',0,?,?,?,?)""",
        (goal_id, title, objective, max_rounds, conversation_id, now, now),
    )
    return get(goal_id) or {}


def update(goal_id: str, *, status: str | None = None, rounds: int | None = None,
           title: str | None = None, objective: str | None = None,
           max_rounds: int | None = None) -> dict:
    current = get(goal_id)
    if current is None:
        raise GoalError(f"目标不存在：{goal_id}")
    new_status = current["status"] if status is None else str(status)
    if new_status not in STATUSES:
        raise GoalError(f"status 只能是 {' / '.join(sorted(STATUSES))}")
    new_rounds = current["rounds"] if rounds is None else int(rounds)
    if new_rounds < 0 or new_rounds > MAX_ROUNDS_LIMIT:
        raise GoalError(f"rounds 必须在 0–{MAX_ROUNDS_LIMIT} 之间")
    new_title, new_objective, new_max = _clean(
        title if title is not None else current["title"],
        objective if objective is not None else current["objective"],
        max_rounds if max_rounds is not None else current["maxRounds"],
    )
    db.execute(
        """UPDATE agent_goal
           SET title=?,objective=?,status=?,rounds=?,max_rounds=?,updated_at=?
           WHERE id=?""",
        (new_title, new_objective, new_status, new_rounds, new_max,
         _now_ms(), goal_id),
    )
    return get(goal_id) or {}


def delete(goal_id: str) -> None:
    if db.execute("DELETE FROM agent_goal WHERE id = ?", (goal_id,)) == 0:
        raise GoalError(f"目标不存在：{goal_id}")
