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


def test_goal_advance_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    goal = goals.create("推进", "完成只读检查", max_rounds=3)
    calls: list[str] = []

    def fake_round(goal_id: str) -> dict:
        calls.append(goal_id)
        current = goals.get(goal_id)
        updated = goals.update(goal_id, rounds=current["rounds"] + 1)
        return {"goal": updated, "content": f"第 {updated['rounds']} 轮", "error": "",
                "conversationId": "conv-1"}

    monkeypatch.setattr(goals, "execute_round", fake_round)
    result = goals.advance(goal["id"], max_rounds=2)
    assert result["rounds"] == 2
    assert result["goal"]["rounds"] == 2
    assert len(calls) == 2
    goals.delete(goal["id"])


def test_goal_advance_stops_on_completion_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    goal = goals.create("推进", "完成只读检查", max_rounds=5)

    def fake_round(goal_id: str) -> dict:
        current = goals.get(goal_id)
        updated = goals.update(goal_id, rounds=current["rounds"] + 1)
        return {"goal": updated, "content": "目标完成，已交付。", "error": "",
                "conversationId": "conv-1"}

    monkeypatch.setattr(goals, "execute_round", fake_round)
    result = goals.advance(goal["id"])
    assert result["goal"]["status"] == "done"
    assert result["rounds"] == 1
    goals.delete(goal["id"])


def test_goal_advance_ui_is_wired() -> None:
    root = Path(__file__).resolve().parents[1] / "workbench" / "static"
    js = (root / "js" / "settings.js").read_text(encoding="utf-8")
    api_js = (root / "js" / "api.js").read_text(encoding="utf-8")
    assert "advanceGoal" in js and "data-goal-advance" in js
    assert "advanceGoal" in api_js


def test_goal_round_executes_and_persists(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from workbench.services import storage

    async def fake_run(_messages, _client, _hub, _config, **_kwargs):
        yield {"type": "final", "content": "本轮进展：已检查一半。"}

    monkeypatch.setattr("workbench.harness.loop.run_agent_loop", fake_run)
    monkeypatch.setattr("workbench.services.settings.load_deepseek",
                        lambda: SimpleNamespace(api_key="sk-test", model="fake",
                                                temperature=0.2))
    monkeypatch.setattr("workbench.services.settings.load_max_steps", lambda: 5)
    monkeypatch.setattr("workbench.services.deepseek.DeepSeekClient",
                        lambda *args, **kwargs: object())
    goal = goals.create("推进", "完成只读检查", max_rounds=3)
    try:
        result = goals.execute_round(goal["id"])
        assert result["goal"]["rounds"] == 1
        assert result["error"] == ""
        messages = storage.list_messages(result["conversationId"])
        assert any("本轮进展" in m["content"] for m in messages)
    finally:
        goals.delete(goal["id"])
