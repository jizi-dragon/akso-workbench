"""定时任务：CRUD、到期判定、调度器只读执行边界。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from workbench import db, scheduler, schedules
from workbench.api import app


def test_schedule_crud_and_due() -> None:
    task = schedules.create("周报", "汇总本周行业动态", 3600)
    try:
        assert task["enabled"] is True
        assert task["nextRunAt"] > int(time.time() * 1000)
        assert schedules.due(now=task["nextRunAt"] + 1)[0]["id"] == task["id"]

        paused = schedules.update(task["id"], enabled=False)
        assert paused["enabled"] is False
        assert schedules.due(now=task["nextRunAt"] + 1) == []

        marked = schedules.mark_run(task["id"], status="ok", conversation_id="conv-1")
        assert marked["lastStatus"] == "ok"
        assert marked["lastConversationId"] == "conv-1"
    finally:
        schedules.delete(task["id"])


def test_schedule_validation() -> None:
    with pytest.raises(schedules.ScheduleError, match="title"):
        schedules.create("", "p", 60)
    with pytest.raises(schedules.ScheduleError, match="interval_s"):
        schedules.create("t", "p", 10)


def test_scheduler_tick_marks_status(monkeypatch: pytest.MonkeyPatch) -> None:
    task = schedules.create("手动", "只读检查", 60)
    db.execute("UPDATE scheduled_task SET next_run_at = 0 WHERE id = ?", (task["id"],))
    monkeypatch.setattr(scheduler, "execute", lambda _task: ("ok", "", "conv-x"))
    sched = scheduler.Scheduler(interval_s=3600)
    assert sched.tick() == 1
    deadline = time.time() + 3
    while time.time() < deadline:
        current = schedules.get(task["id"])
        if current and current["lastStatus"] == "ok":
            break
        time.sleep(0.02)
    assert schedules.get(task["id"])["lastStatus"] == "ok"
    schedules.delete(task["id"])


def test_schedule_api_round_trip() -> None:
    with TestClient(app) as client:
        created = client.post("/api/schedules", json={
            "title": "周报", "prompt": "汇总本周行业动态", "intervalS": 3600,
        })
        assert created.status_code == 200, created.text
        task_id = created.json()["id"]
        try:
            listed = client.get("/api/schedules")
            assert any(item["id"] == task_id for item in listed.json()["schedules"])

            patched = client.patch(f"/api/schedules/{task_id}", json={"enabled": False})
            assert patched.status_code == 200
            assert patched.json()["enabled"] is False
        finally:
            assert client.delete(f"/api/schedules/{task_id}").status_code == 200


def test_schedule_ui_is_wired() -> None:
    root = Path(__file__).resolve().parents[1] / "workbench" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    js = (root / "js" / "settings.js").read_text(encoding="utf-8")
    api = (root / "js" / "api.js").read_text(encoding="utf-8")
    assert 'id="sch-title"' in html and 'id="sch-list"' in html
    assert "initSchedules" in js and "api.createSchedule" in js
    assert "listSchedules" in api and "createSchedule" in api


def test_scheduled_tools_are_read_only() -> None:
    tools = set(scheduler._read_only_tools())
    assert "get_current_time" in tools
    assert "memory_add" not in tools
    assert "memory_delete" not in tools
