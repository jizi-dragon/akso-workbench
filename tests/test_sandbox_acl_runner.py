"""DSH Windows ACL runner 接缝：配置存在才启用，非 Windows/缺配置不伪装。"""

from __future__ import annotations

import sys

from workbench.sandbox import win_acl_runner


def test_unconfigured_runner_is_unavailable(monkeypatch) -> None:
    monkeypatch.delenv("WORKBENCH_WINDOWS_ACL_RUNNER", raising=False)
    monkeypatch.delenv("WORKBENCH_NODE", raising=False)
    assert win_acl_runner.available() is False


def test_wrap_preserves_argv_and_adds_mode(tmp_path, monkeypatch) -> None:
    runner = tmp_path / "runner.js"
    runner.write_text("// fake", encoding="utf-8")
    monkeypatch.setenv("WORKBENCH_WINDOWS_ACL_RUNNER", str(runner))
    monkeypatch.setenv("WORKBENCH_NODE", sys.executable)
    assert win_acl_runner.available() is True

    temp = tmp_path / "temp"
    wrapped = win_acl_runner.wrap(
        [sys.executable, "-c", "print('x')"],
        workspace_root=tmp_path / "ws",
        temp_dir=temp,
    )
    assert wrapped[:2] == [sys.executable, str(runner)]
    assert "--mode" in wrapped and "workspace-write" in wrapped
    assert wrapped[-3:] == [sys.executable, "-c", "print('x')"]
    assert temp.is_dir()
