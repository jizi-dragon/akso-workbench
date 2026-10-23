"""MCP 状态投影：配置里的 `env`/`headers` 永远不进对外响应。

MCP 配置可以含 token/webhook（`mcp.json` 因此进了敏感路径表）。状态面只该回答
"哪个 server、连没连、几个工具、什么错"——不要把整份配置的结构带出去。
allowlist 比"记得删某个字段"可靠：新增字段默认不出现，而不是默认泄露。
"""

from __future__ import annotations

import json

from workbench.api.routes_mcp import _project_status


def test_projection_drops_env_headers_and_unknown_fields() -> None:
    raw = {
        "configPath": "C:/Users/admin/AppData/Roaming/Workbench/mcp.json",
        "servers": [
            {
                "name": "probe",
                "enabled": True,
                "connected": True,
                "toolCount": 3,
                "env": {"TOKEN": "super-secret"},
                "headers": {"Authorization": "Bearer super-secret"},
                "args": ["--token", "super-secret"],
                "futureField": "whatever",
            }
        ],
        "errors": ["x" * 500],
    }
    out = _project_status(raw)
    server = out["servers"][0]
    assert set(server) == {"name", "enabled", "connected", "toolCount"}
    text = json.dumps(out, ensure_ascii=False)
    assert "super-secret" not in text
    assert "env" not in server and "headers" not in server
    assert len(out["errors"][0]) == 200, "错误文本没有限长"


def test_projection_keeps_the_fields_the_ui_needs() -> None:
    raw = {"servers": [{"name": "probe", "connected": False, "error": "启动失败",
                        "restarts": 2, "lastErrorAt": 123, "risk": "write"}],
           "errors": [], "healthy": 0, "failing": 1}
    out = _project_status(raw)
    assert out["healthy"] == 0 and out["failing"] == 1
    assert out["servers"][0]["error"] == "启动失败"
    assert out["servers"][0]["restarts"] == 2
