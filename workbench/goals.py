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


def execute_round(goal_id: str) -> dict:
    """手动推进一步（只读）：在目标绑定的会话里跑一个 Agent 回合。

    v1 **不自动循环**：由用户点「继续一轮」；达到 `max_rounds` 后拒绝继续。
    无人值守不成立，所以只给 READ 工具（与定时任务同一边界）。
    """
    import asyncio

    current = get(goal_id)
    if current is None:
        raise GoalError(f"目标不存在：{goal_id}")
    if current["status"] != "active":
        raise GoalError("只有 active 目标可以继续")
    if current["rounds"] >= current["maxRounds"]:
        raise GoalError(f"目标已达到轮次上限 {current['maxRounds']}，请调整预算或标记完成")

    from .harness.loop import run_agent_loop
    from .harness.prompts import build_system_prompt
    from .harness.tools import default_registry
    from .harness.types import AgentConfig
    from .plugin_api import Risk
    from .services import storage
    from .services.deepseek import DeepSeekClient
    from .services.settings import load_deepseek, load_max_steps

    cfg = load_deepseek()
    if not cfg.api_key:
        raise GoalError("未配置 DeepSeek API Key，无法推进目标")

    conv_id = current["conversationId"]
    if not conv_id:
        conv_id = storage.create_conversation(current["title"][:60])["id"]
        db.execute("UPDATE agent_goal SET conversation_id=?, updated_at=? WHERE id=?",
                   (conv_id, _now_ms(), goal_id))

    history = storage.model_transcript(conv_id)
    prompt = (current["objective"]
              + "\n\n（这是长期目标的一轮推进；只允许只读工具。"
                "如果目标已经完成，请明确说「目标完成」；否则给出下一步。）")
    history.append({"role": "user", "content": prompt})
    storage.insert_message(conv_id, "user", prompt)
    read_tools = tuple(spec.name for spec in default_registry().core_specs()
                       if spec.risk is Risk.READ)
    client = DeepSeekClient(cfg.api_key, model=cfg.model)
    config = AgentConfig(
        temperature=cfg.temperature,
        system_prompt=build_system_prompt() + (
            "\n\n## 长期目标\n当前目标：" + current["objective"]
            + "\n只使用只读工具；每轮给出可验证的进展与下一步。"
        ),
        max_steps=load_max_steps(),
        plan_mode=True,
        allowed_tools=read_tools,
        approval_wait_s=0.0,
    )
    content = ""
    error = ""

    async def _run() -> None:
        nonlocal content, error
        async for event in run_agent_loop(history, client, default_registry(), config,
                                          conversation_id=conv_id):
            if event["type"] == "token":
                content += event["text"]
            elif event["type"] == "final":
                content = event["content"]
            elif event["type"] == "error":
                error = str(event.get("message") or "Agent 回合失败")

    asyncio.run(_run())
    if content:
        storage.insert_message(conv_id, "assistant", content)
    updated = update(goal_id, rounds=current["rounds"] + 1)
    return {"goal": updated, "content": content, "error": error,
            "conversationId": conv_id}


def advance(goal_id: str, max_rounds: int | None = None) -> dict:
    """**手动触发**的有限自动推进：连续执行只读回合，直到上限/失败/模型说完成。

    - 不是后台自动循环：调用方点一次，最多推进 `max_rounds` 轮；
    - 只读边界由 `execute_round` 保证；
    - 完成判定只认模型正文里的「目标完成」标记，否则到上限停下，不擅自改 done。
    """
    current = get(goal_id)
    if current is None:
        raise GoalError(f"目标不存在：{goal_id}")
    if current["status"] != "active":
        raise GoalError("只有 active 目标可以推进")
    remaining = current["maxRounds"] - current["rounds"]
    if remaining <= 0:
        raise GoalError(f"目标已达到轮次上限 {current['maxRounds']}")
    limit = remaining if max_rounds is None else min(int(max_rounds), remaining)
    if limit < 1:
        raise GoalError("max_rounds 必须 ≥1")

    results: list[dict] = []
    for _ in range(limit):
        result = execute_round(goal_id)
        results.append({"content": result["content"], "error": result["error"]})
        current = result["goal"]
        if result["error"]:
            break
        if "目标完成" in (result["content"] or ""):
            current = update(goal_id, status="done")
            break
    return {"goal": current, "rounds": len(results), "results": results}
