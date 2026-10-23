"""敏感路径覆盖率棘轮（读取 `coverage json`）。

## 为什么不用一个固定的总覆盖率

总覆盖率会被无关的大文件稀释：把 `plugin_api.py` 从 92% 降到 80%，只要别处涨一点，
总数可能不变。这里只盯**改错了后果最重**的文件：沙箱、确认门、凭据、权限、循环。

## 用法

    .venv\\Scripts\\python.exe -m coverage run --source=workbench -m pytest -o addopts=""
    .venv\\Scripts\\python.exe -m coverage json -o tmp\\coverage.json
    .venv\\Scripts\\python.exe tools\\coverage_gate.py tmp\\coverage.json

`--update` 把基线改成**当前值**（可升可降，需人工确认）；默认只检查。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tools" / "coverage_baseline.json"
TOLERANCE = 0.0


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def check(report: dict, baseline: dict[str, float]) -> tuple[list[str], dict[str, float]]:
    files = {_norm(name): info for name, info in (report.get("files") or {}).items()}
    errors: list[str] = []
    updates: dict[str, float] = {}
    for name, floor in sorted(baseline.items()):
        info = files.get(name)
        if info is None:
            errors.append(f"{name}: 覆盖率报告里没有这个文件（没被导入？）")
            continue
        pct = round(float(info["summary"]["percent_covered"]), 1)
        if pct + TOLERANCE < floor:
            errors.append(f"{name}: {pct:.1f}% < 基线 {floor:.1f}%")
        elif pct > floor + 2.0:
            updates[name] = round(pct, 1)
    return errors, updates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="敏感路径覆盖率棘轮")
    parser.add_argument("report", help="coverage json 路径")
    parser.add_argument("--update", action="store_true", help="把基线改成当前值")
    args = parser.parse_args(argv)

    if not BASELINE.exists():
        print(f"COVERAGE_GATE: FAIL（缺基线 {BASELINE}）")
        return 1
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    errors, updates = check(report, baseline)

    if args.update:
        baseline.update(updates)
        BASELINE.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        print(f"coverage baseline updated: {len(updates)} file(s)")

    if errors:
        print("COVERAGE_GATE: FAIL")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"COVERAGE_GATE: PASS（{len(baseline)} sensitive files）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
