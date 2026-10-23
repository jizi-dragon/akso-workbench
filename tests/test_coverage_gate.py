"""覆盖率棘轮自身的判据：低于基线红、缺文件红、升高只建议不失败。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import coverage_gate  # noqa: E402


def _report(pct: float, name: str = "workbench/sandbox/job.py") -> dict:
    return {"files": {name: {"summary": {"percent_covered": pct}}}}


def test_below_baseline_is_an_error() -> None:
    errors, _ = coverage_gate.check(_report(60.0), {"workbench/sandbox/job.py": 70.0})
    assert errors and "60.0% < 基线 70.0%" in errors[0]


def test_equal_or_higher_passes_and_suggests_update() -> None:
    errors, updates = coverage_gate.check(_report(70.0), {"workbench/sandbox/job.py": 70.0})
    assert errors == [] and updates == {}

    errors, updates = coverage_gate.check(_report(75.0), {"workbench/sandbox/job.py": 70.0})
    assert errors == []
    assert updates == {"workbench/sandbox/job.py": 75.0}


def test_missing_file_is_an_error() -> None:
    errors, _ = coverage_gate.check({"files": {}}, {"workbench/sandbox/job.py": 70.0})
    assert errors and "没有这个文件" in errors[0]


def test_windows_separators_are_normalised() -> None:
    errors, _ = coverage_gate.check(
        _report(70.0, r"workbench\sandbox\job.py"), {"workbench/sandbox/job.py": 70.0})
    assert errors == []


def test_repo_baseline_covers_the_sensitive_modules() -> None:
    baseline = json.loads((ROOT / "tools" / "coverage_baseline.json").read_text(encoding="utf-8"))
    assert "workbench/sandbox/runner.py" in baseline
    assert "workbench/approvals.py" in baseline
    assert "workbench/services/vault.py" in baseline
