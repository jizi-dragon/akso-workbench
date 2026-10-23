"""定时任务（v1）：只读 prompt 的周期执行。

边界（诚实写在这里，不是在 UI 文案里另说一套）：

- **只跑 READ 工具**（`plan_mode=True` + 显式 READ 白名单）：无人值守时没人能点确认门；
- 每个任务每次执行**新建一个会话**，结果可在会话记录里回看；
- 最小间隔 60 秒，最长 30 天；不支持 cron 表达式（v1 刻意只做固定间隔）；
- 不重试：失败写 `last_error`，下一次到点再试。
"""

from __future__ import annotations

import time
import uuid

from . import db

MIN_INTERVAL_S = 60
MAX_INTERVAL_S = 30 * 24 * 3600


class ScheduleError(ValueError):
    """定时任务的参数/状态错误（API 转 400/404）。"""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _clean(title: str, prompt: str, interval_s: int) -> tuple[str, str, int]:
    title = (title or "").strip()
    prompt = (prompt or "").strip()
    if not title or len(title) > 120:
        raise ScheduleError("title 必须为 1–120 字符")
    if not prompt or len(prompt) > 4000:
        raise ScheduleError("prompt 必须为 1–4000 字符")
    if not (MIN_INTERVAL_S <= int(interval_s) <= MAX_INTERVAL_S):
        raise ScheduleError(f"interval_s 必须在 {MIN_INTERVAL_S}–{MAX_INTERVAL_S} 秒之间")
    return title, prompt, int(interval_s)


def _to_dict(row: dict) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "prompt": row["prompt"],
        "intervalS": row["interval_s"],
        "enabled": bool(row["enabled"]),
        "lastRunAt": row["last_run_at"],
        "nextRunAt": row["next_run_at"],
        "lastStatus": row["last_status"],
        "lastError": row["last_error"],
        "lastConversationId": row["last_conversation_id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def list_all() -> list[dict]:
    return [_to_dict(row) for row in
            db.query("SELECT * FROM scheduled_task ORDER BY created_at DESC")]


def get(task_id: str) -> dict | None:
    row = db.query_one("SELECT * FROM scheduled_task WHERE id = ?", (task_id,))
    return _to_dict(row) if row else None


def create(title: str, prompt: str, interval_s: int) -> dict:
    title, prompt, interval_s = _clean(title, prompt, interval_s)
    now = _now_ms()
    task_id = f"sch-{uuid.uuid4().hex[:12]}"
    db.execute(
        """INSERT INTO scheduled_task
           (id,title,prompt,interval_s,enabled,last_run_at,next_run_at,last_status,
            last_error,last_conversation_id,created_at,updated_at)
           VALUES (?,?,?,?,1,NULL,?,'','','' ,?,?)""",
        (task_id, title, prompt, interval_s, now + interval_s * 1000, now, now),
    )
    return get(task_id) or {}


def update(task_id: str, *, title: str | None = None, prompt: str | None = None,
           interval_s: int | None = None, enabled: bool | None = None) -> dict:
    current = get(task_id)
    if current is None:
        raise ScheduleError(f"定时任务不存在：{task_id}")
    new_title, new_prompt, new_interval = _clean(
        title if title is not None else current["title"],
        prompt if prompt is not None else current["prompt"],
        interval_s if interval_s is not None else current["intervalS"],
    )
    enable = current["enabled"] if enabled is None else bool(enabled)
    next_at = current["nextRunAt"]
    if interval_s is not None or enabled is True:
        next_at = _now_ms() + new_interval * 1000
    db.execute(
        """UPDATE scheduled_task
           SET title=?,prompt=?,interval_s=?,enabled=?,next_run_at=?,updated_at=?
           WHERE id=?""",
        (new_title, new_prompt, new_interval, 1 if enable else 0, next_at,
         _now_ms(), task_id),
    )
    return get(task_id) or {}


def delete(task_id: str) -> None:
    if db.execute("DELETE FROM scheduled_task WHERE id = ?", (task_id,)) == 0:
        raise ScheduleError(f"定时任务不存在：{task_id}")


def due(now: int | None = None, limit: int = 5) -> list[dict]:
    moment = _now_ms() if now is None else int(now)
    rows = db.query(
        """SELECT * FROM scheduled_task
           WHERE enabled = 1 AND next_run_at <= ?
           ORDER BY next_run_at LIMIT ?""",
        (moment, max(1, min(limit, 20))),
    )
    return [_to_dict(row) for row in rows]


def mark_run(task_id: str, *, status: str, error: str = "",
             conversation_id: str = "") -> dict:
    row = db.query_one("SELECT interval_s FROM scheduled_task WHERE id = ?", (task_id,))
    if row is None:
        raise ScheduleError(f"定时任务不存在：{task_id}")
    now = _now_ms()
    db.execute(
        """UPDATE scheduled_task
           SET last_run_at=?,next_run_at=?,last_status=?,last_error=?,
               last_conversation_id=?,updated_at=?
           WHERE id=?""",
        (now, now + int(row["interval_s"]) * 1000, status[:40], error[:500],
         conversation_id, now, task_id),
    )
    return get(task_id) or {}
