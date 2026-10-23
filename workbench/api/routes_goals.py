"""长期目标管理面（v1 tracker：持久化状态，不自动续跑）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import goals

router = APIRouter(prefix="/api/goals", tags=["goals"])


class GoalBody(BaseModel):
    title: str
    objective: str
    maxRounds: int = 10
    conversationId: str = ""


class GoalPatch(BaseModel):
    title: str | None = None
    objective: str | None = None
    status: str | None = None
    rounds: int | None = None
    maxRounds: int | None = None


@router.get("")
def list_goals() -> dict:
    return {"goals": goals.list_all()}


@router.post("")
def create_goal(body: GoalBody) -> dict:
    try:
        return goals.create(body.title, body.objective, body.maxRounds,
                            body.conversationId)
    except goals.GoalError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/{goal_id}")
def patch_goal(goal_id: str, body: GoalPatch) -> dict:
    try:
        return goals.update(goal_id, title=body.title, objective=body.objective,
                            status=body.status, rounds=body.rounds,
                            max_rounds=body.maxRounds)
    except goals.GoalError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/{goal_id}")
def delete_goal(goal_id: str) -> dict:
    try:
        goals.delete(goal_id)
    except goals.GoalError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True}
