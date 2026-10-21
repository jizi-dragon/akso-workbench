"""服务端身份契约：Tauri 动态端口经 `/api/health` 如实回报。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from workbench import config
from workbench.api import app


def test_health_reports_workbench_identity_and_one_port() -> None:
    data = TestClient(app).get("/api/health").json()
    assert data["app"] == "Workbench"
    assert data["service"] == "workbench-server"
    assert data["version"]
    assert data["pid"] > 0
    assert data["ports"] == {"server": config.PORT}
    assert "control" not in data["ports"]


def test_health_port_source_follows_env(monkeypatch) -> None:
    monkeypatch.setattr(config, "PORT", 50196)
    monkeypatch.setenv("WORKBENCH_PORT", "50196")
    data = TestClient(app).get("/api/health").json()
    assert data["ports"] == {"server": 50196}
    assert data["portsSource"] == "env"
