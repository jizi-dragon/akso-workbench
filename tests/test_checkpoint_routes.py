"""工作区快照 API：列表 / 创建 / 恢复，只认沙箱工作区根。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from workbench.api import app
from workbench.sandbox import layout


def test_checkpoint_routes_round_trip(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "note.md"
    target.write_text("v1", encoding="utf-8")
    original = layout.workspace_settings()
    layout.save_workspace(root=str(workspace))
    try:
        with TestClient(app) as client:
            listed = client.get("/api/checkpoints")
            assert listed.status_code == 200
            assert listed.json()["checkpoints"] == []

            created = client.post("/api/checkpoints")
            assert created.status_code == 200, created.text
            checkpoint = created.json()["checkpoint"]
            assert checkpoint["files"] == ["note.md"]

            target.write_text("v2", encoding="utf-8")
            restored = client.post(f"/api/checkpoints/{checkpoint['id']}/restore")
            assert restored.status_code == 200, restored.text
            assert restored.json()["restored"] == 1
            assert target.read_text(encoding="utf-8") == "v1"

            missing = client.post("/api/checkpoints/does-not-exist/restore")
            assert missing.status_code == 404
    finally:
        layout.save_workspace(root=original.root, mode=original.mode,
                              allow_network=original.allow_network,
                              allow_subprocess=original.allow_subprocess)
