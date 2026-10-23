"""长期目标 v1：CRUD/状态/轮次边界；明确不自动续跑。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from workbench import goals
from workbench.api import app


def test_goal_crud_and_status() -> None:
    goal = goals.create("季度目标", "把偏差处理对象梳理完", max_rounds=5)
    try:
        assert goal["status"] == "active"
        assert goal["rounds"] == 0
        paused = goals.update(goal["id"], status="paused", rounds=2)
        assert paused["status"] == "paused"
        assert paused["rounds"] == 2
        done = goals.update(goal["id"], status="done")
        assert done["status"] == "done"
    finally:
        goals.delete(goal["id"])


def test_goal_validation() -> None:
    with pytest.raises(goals.GoalError):
        goals.create("", "x")
    with pytest.raises(goals.GoalError):
        goals.create("t", "x", max_rounds=0)


def test_goal_api_round_trip() -> None:
    with TestClient(app) as client:
        created = client.post("/api/goals", json={
            "title": "年度目标", "objective": "完成 GxP 报告模板库", "maxRounds": 8,
        })
        assert created.status_code == 200, created.text
        goal_id = created.json()["id"]
        try:
            listed = client.get("/api/goals")
            assert any(item["id"] == goal_id for item in listed.json()["goals"])
            patched = client.patch(f"/api/goals/{goal_id}", json={"status": "done"})
            assert patched.status_code == 200
            assert patched.json()["status"] == "done"
        finally:
            assert client.delete(f"/api/goals/{goal_id}").status_code == 200


def test_goal_ui_is_wired() -> None:
    root = Path(__file__).resolve().parents[1] / "workbench" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    js = (root / "js" / "settings.js").read_text(encoding="utf-8")
    api_js = (root / "js" / "api.js").read_text(encoding="utf-8")
    assert 'id="goal-title"' in html and 'id="goal-list"' in html
    assert "initGoals" in js and "api.createGoal" in js
    assert "listGoals" in api_js and "createGoal" in api_js


def test_goal_round_rejects_inactive_and_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    goal = goals.create("推进", "完成一项只读检查", max_rounds=1)
    try:
        goals.update(goal["id"], rounds=1)
        with pytest.raises(goals.GoalError, match="轮次上限"):
            goals.execute_round(goal["id"])
        goals.update(goal["id"], status="done")
        with pytest.raises(goals.GoalError, match="active"):
            goals.execute_round(goal["id"])
    finally:
        goals.delete(goal["id"])


def test_goal_continue_endpoint_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    goal = goals.create("推进", "完成一项只读检查")
    try:
        monkeypatch.setattr(goals, "execute_round",
                            lambda _id: {"goal": goal, "content": "进展", "error": "",
                                         "conversationId": "conv-1"})
        with TestClient(app) as client:
            response = client.post(f"/api/goals/{goal['id']}/continue")
            assert response.status_code == 200
            assert response.json()["content"] == "进展"
    finally:
        goals.delete(goal["id"])


def test_goal_ui_has_continue_button() -> None:
    root = Path(__file__).resolve().parents[1] / "workbench" / "static"
    js = (root / "js" / "settings.js").read_text(encoding="utf-8")
    api_js = (root / "js" / "api.js").read_text(encoding="utf-8")
    assert "continueGoal" in js and "data-goal-continue" in js
    assert "continueGoal" in api_js
