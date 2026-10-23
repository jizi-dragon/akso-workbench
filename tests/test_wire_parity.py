"""Wire parity：前端调用的路径必须存在；关键端点的字段必须与前端消费一致。

## 为什么不是"类型生成"

本仓前端是原生 JS、后端是 FastAPI 返回 `dict`，没有共享类型。真正会发生的漂移是：

1. 前端调了一个**后端不存在**的路径（`api.js` 里 `/api/agent/selection/pick`
   曾长期只存在于注释里）；
2. 后端把一个字段改名，前端还在读旧名，于是界面**静默显示占位**（PITFALLS #35）。

类型生成解决不了第 1 类（路径），也覆盖不了没有 `response_model` 的 `dict`。
这里用两张**手写但可执行**的契约：`api.js` 路径全集、关键端点的 required/consumed 字段。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import repolint  # noqa: E402

from workbench import db  # noqa: E402
from workbench.api import (  # noqa: E402
    routes_agent,
    routes_diagnostics,
    routes_mcp,
    routes_sandbox,
    routes_settings,
)

CONTRACT = json.loads((ROOT / "tools" / "wire_contract.json").read_text(encoding="utf-8"))
API_JS = ROOT / "workbench" / "static" / "js" / "api.js"


@pytest.fixture(autouse=True)
def _ready():
    db.connect()


def _route_matches(js_path: str, route: str) -> bool:
    if js_path == route:
        return True
    # 前端模板串：`/api/attachments/${id}/raw` → `/api/attachments/{}/raw`
    if "{}" in js_path:
        pattern = "^" + re.escape(js_path).replace(r"\{\}", "[^/]+") + "$"
        return re.match(pattern, route) is not None
    # 查询串/尾部斜杠：`/api/vault/library?refresh=1`
    return route.startswith(js_path.rstrip("/") + "/") or js_path.startswith(route.rstrip("/") + "/")


def _api_paths() -> list[str]:
    """从 api.js 抽 `/api/...` 字面量（含模板串），归一成路径。"""
    text = API_JS.read_text(encoding="utf-8")
    candidates = [a or b for a, b in re.findall(r"`([^`]*)`|\"([^\"]*)\"", text)]
    paths: list[str] = []
    for raw in candidates:
        if "/api/" not in raw:
            continue
        normalized = re.sub(r"\$\{[^}]*\}", "{}", raw).split("?", 1)[0].rstrip("/")
        if normalized.endswith("{}") and not normalized.endswith("/{}"):
            normalized = normalized[:-2]  # 模板串末尾的可选查询，不是路径参数
        paths.append(normalized)
    return sorted(set(paths))


def test_every_api_js_path_exists_in_the_backend() -> None:
    routes = repolint.route_paths(ROOT)
    missing: list[str] = []
    for path in _api_paths():
        if not any(_route_matches(path, route) for route in routes):
            missing.append(path)
    assert not missing, f"api.js 调用了不存在的后端路径：{missing}"


def test_critical_endpoints_expose_the_contract_fields() -> None:
    responses = {
        "/api/bootstrap": routes_settings.bootstrap(),
        "/api/agent/tools": routes_agent.list_tools(),
        "/api/agent/selfcheck": routes_agent.selfcheck(),
        "/api/mcp/servers": routes_mcp.list_servers(),
        "/api/settings/permissions": routes_settings.get_permissions(),
        "/api/diagnostics": routes_diagnostics.diagnostics(live=False),
        "/api/sandbox/status": routes_sandbox.status(),
    }
    for path, contract in CONTRACT.items():
        body = responses[path]
        missing = [key for key in contract["required"] if key not in body]
        assert not missing, f"{path} 缺少契约字段：{missing}（实际：{sorted(body)}）"


def test_consumed_fields_still_appear_in_frontend_source() -> None:
    js = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted((ROOT / "workbench" / "static").rglob("*.js"))
    )
    missing: list[str] = []
    for path, contract in CONTRACT.items():
        for field in contract["consumed"]:
            if field not in js:
                missing.append(f"{path} → {field}")
    assert not missing, (
        "后端字段在前端源码里完全找不到 —— 要么后端改名了没跟前端，"
        f"要么字段已废弃：{missing}"
    )
