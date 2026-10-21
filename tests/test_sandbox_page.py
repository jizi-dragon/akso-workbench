"""沙箱页「工作区与权限」的装配判据（不启浏览器）。

视觉/交互由手工验收；这里守的是**入口没被改名、接口没被拆掉**：
`sb-ws-root` 等 id 是 JS 的隐式契约，改名不会报错，只会让按钮点了没反应。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from workbench.api import app


def test_sandbox_page_and_workspace_api() -> None:
    with TestClient(app) as client:
        page = client.get("/static/pages/sandbox.html")
        assert page.status_code == 200
        assert "工作区与权限" in page.text
        for asset in ("/static/js/sandbox.js", "/static/css/modules.css"):
            assert client.get(asset).status_code == 200

        workspace = client.get("/api/sandbox/workspace")
        assert workspace.status_code == 200
        body = workspace.json()
        assert body["mode"] in {"read-only", "read-write"}
        assert body["root"]
        assert "honest_limit" in body

        status = client.get("/api/sandbox/status").json()
        assert status["workspace"]["root"] == body["root"]

        check = client.get("/api/sandbox/selfcheck").json()
        assert "workspace" in check


def test_sandbox_page_ids_match_script() -> None:
    with TestClient(app) as client:
        html = client.get("/static/pages/sandbox.html").text
        js = client.get("/static/js/sandbox.js").text
    for element_id in (
        "sb-ws-root", "sb-ws-mode", "sb-ws-network", "sb-ws-subprocess",
        "sb-ws-meta", "sb-ws-honest", "sb-ws-save", "sb-ws-reset", "sb-ws-copy",
    ):
        assert f'id="{element_id}"' in html, f"sandbox.html 缺少 id={element_id}"
        assert (f'"{element_id}"' in js or f"'{element_id}'" in js), (
            f"sandbox.js 没有引用 id={element_id}"
        )
