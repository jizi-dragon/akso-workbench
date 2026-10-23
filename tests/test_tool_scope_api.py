"""工具范围设置：白名单保存/校验 + 对话侧全局生效。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from workbench.api import app, routes_chat
from workbench.services import storage


def test_tool_scope_round_trip(tmp_path) -> None:
    storage.set_setting(storage.TOOL_ALLOWLIST_KEY, "")
    try:
        with TestClient(app) as client:
            got = client.get("/api/settings/tools")
            assert got.status_code == 200
            body = got.json()
            assert body["allowed"] is None
            assert len(body["tools"]) == 20

            saved = client.put("/api/settings/tools",
                               json={"allowed": ["get_current_time"]})
            assert saved.status_code == 200
            assert saved.json()["allowed"] == ["get_current_time"]
            assert routes_chat._tool_allowlist(None) == ("get_current_time",)

            unknown = client.put("/api/settings/tools",
                                 json={"allowed": ["no_such_tool"]})
            assert unknown.status_code == 400

            reset = client.put("/api/settings/tools", json={"allowed": None})
            assert reset.json()["allowed"] is None
            assert routes_chat._tool_allowlist(None) is None
    finally:
        storage.set_setting(storage.TOOL_ALLOWLIST_KEY, "")


def test_tool_scope_ui_is_wired() -> None:
    root = Path(__file__).resolve().parents[1] / "workbench" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    js = (root / "js" / "settings.js").read_text(encoding="utf-8")
    api = (root / "js" / "api.js").read_text(encoding="utf-8")
    css = (root / "css" / "settings.css").read_text(encoding="utf-8")
    assert 'id="tool-scope-list"' in html and 'id="btn-save-tools"' in html
    assert "getToolScope" in js and "saveToolScope" in js
    assert "getToolScope" in api and "saveToolScope" in api
    assert ".tool-scope-row" in css
