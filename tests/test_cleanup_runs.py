"""沙箱 runs/ 保留策略：按时间/数量/体积删除，默认 dry-run，绝不碰非 run 目录。"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from workbench.sandbox import layout


@pytest.fixture()
def runs(tmp_path, monkeypatch: pytest.MonkeyPatch):
    base = tmp_path / "runs"
    base.mkdir()
    monkeypatch.setattr(layout, "runs_dir", lambda: base)
    return base


def _make(base: Path, name: str, size: int, age_days: float) -> Path:
    path = base / name
    path.mkdir()
    (path / "blob.bin").write_bytes(b"x" * size)
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))
    return path


def test_dry_run_deletes_nothing(runs) -> None:
    _make(runs, "20260101-000000-aaaaaa", 10, 100)
    result = layout.cleanup_runs(keep_days=30, dry_run=True)
    assert result["deleted"] == ["20260101-000000-aaaaaa"]
    assert result["dryRun"] is True
    assert (runs / "20260101-000000-aaaaaa").exists()


def test_keeps_newest_by_count(runs) -> None:
    _make(runs, "20260101-000000-aaaaaa", 10, 10)
    _make(runs, "20260102-000000-bbbbbb", 10, 5)
    _make(runs, "20260103-000000-cccccc", 10, 1)
    result = layout.cleanup_runs(keep_days=0, keep_count=1, dry_run=False)
    assert result["kept"] == 1
    assert (runs / "20260103-000000-cccccc").exists()
    assert not (runs / "20260101-000000-aaaaaa").exists()
    assert not (runs / "20260102-000000-bbbbbb").exists()


def test_deletes_old_by_days(runs) -> None:
    _make(runs, "20260101-000000-aaaaaa", 10, 40)
    _make(runs, "20260102-000000-bbbbbb", 10, 1)
    result = layout.cleanup_runs(keep_days=30, keep_count=10, dry_run=False)
    assert result["deleted"] == ["20260101-000000-aaaaaa"]
    assert (runs / "20260102-000000-bbbbbb").exists()


def test_size_cap_deletes_oldest_first(runs) -> None:
    _make(runs, "20260101-000000-aaaaaa", 100, 10)
    _make(runs, "20260102-000000-bbbbbb", 100, 5)
    result = layout.cleanup_runs(keep_days=0, keep_count=10, max_bytes=100, dry_run=False)
    assert result["freedBytes"] == 100
    assert (runs / "20260102-000000-bbbbbb").exists()
    assert not (runs / "20260101-000000-aaaaaa").exists()


def test_ignores_non_run_directories(runs) -> None:
    (runs / "staged").mkdir()
    (runs / "not-a-run").mkdir()
    result = layout.cleanup_runs(keep_days=0, keep_count=0, dry_run=False)
    assert result["deleted"] == []
    assert (runs / "staged").exists() and (runs / "not-a-run").exists()
