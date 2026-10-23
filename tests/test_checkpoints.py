"""工作区快照 / 恢复：只覆盖文件、有上限、路径不许逃出 root。

## 边界（与 Reasonix checkpoint 同口径）

- 只跟踪**文件**；脚本的 `rm`/网络/DB 副作用撤不了。
- 只覆盖 capture 时存在的文件；之后新建的文件不会出现在快照里。
- 超上限显式 `truncated=True`，不假装完整。
"""

from __future__ import annotations

import sys

import pytest

from workbench import checkpoints, config
from workbench.sandbox import ExecRequest, layout, run


@pytest.fixture()
def work(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "a.txt").write_text("A", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "b.txt").write_text("B", encoding="utf-8")
    return root


def test_capture_then_restore_round_trips(work) -> None:
    cap = checkpoints.capture(work)
    assert sorted(cap.files) == ["a.txt", "sub/b.txt"]
    assert cap.bytes == 2 and not cap.truncated

    (work / "a.txt").write_text("changed", encoding="utf-8")
    (work / "sub" / "b.txt").unlink()
    (work / "new.txt").write_text("new", encoding="utf-8")

    result = checkpoints.restore(cap.id)
    assert result["restored"] == 2
    assert (work / "a.txt").read_text(encoding="utf-8") == "A"
    assert (work / "sub" / "b.txt").read_text(encoding="utf-8") == "B"
    # 快照之外的新文件不会被删——这是刻意的边界，测试钉住它
    assert (work / "new.txt").exists()


def test_capture_respects_limits_and_marks_truncated(work) -> None:
    cap = checkpoints.capture(work, max_files=1)
    assert len(cap.files) == 1
    assert cap.truncated is True

    cap2 = checkpoints.capture(work, max_bytes=1)
    assert cap2.bytes <= 1
    assert cap2.truncated is True


def test_restore_rejects_path_escape(work) -> None:
    with pytest.raises(checkpoints.CheckpointError, match="逃出工作区"):
        checkpoints._safe_target(work, "../evil.txt")


def test_list_captures_returns_manifests(work) -> None:
    cap = checkpoints.capture(work)
    listed = checkpoints.list_captures()
    assert any(item["id"] == cap.id for item in listed)


@pytest.mark.slow
def test_sandbox_captures_workspace_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    layout.ensure_dirs()
    work = layout.run_dir(layout.new_run_id())
    monkeypatch.setenv("WORKBENCH_SANDBOX_CHECKPOINTS", "1")
    result = run(ExecRequest(argv=[sys.executable, "-c", "print('ok')"],
                             policy_name="skill-script", cwd=work))
    assert result.ok, result.error
    assert any("已创建工作区快照" in note for note in result.notes), result.notes
