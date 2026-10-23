"""`tools/repolint.py` 自身的判据：预算棘轮、错误文本、前端覆盖登记。

反证要点：
- 新文件 801 行且无基线 ⇒ 红；基线 900 ⇒ 绿；基线 700 ⇒ 红（只降不升）。
- `"401" in str(exc)` 种进临时树 ⇒ 红。
- 新端点没在 `frontend_parity.json` 登记 ⇒ 红（现有 known gaps 只告警）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import repolint  # noqa: E402


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "repo"
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_budget_ratchet_only_lowers(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"workbench/big.py": "\n".join("x = 1" for _ in range(801))})
    errors, updates = repolint.check_budget(root, {})
    assert errors and "不在基线" in errors[0]

    errors, updates = repolint.check_budget(root, {"workbench/big.py": 900})
    assert not errors
    assert updates == {"workbench/big.py": 801}, "低于基线时应给出可下调的值"

    errors, _ = repolint.check_budget(root, {"workbench/big.py": 700})
    assert errors and "只许降" in errors[0]


def test_error_text_rule_catches_a_planted_violation(tmp_path: Path) -> None:
    root = _tree(tmp_path, {
        "workbench/bad.py": 'def f(exc):\n    if "401" in str(exc):\n        return True\n',
    })
    errors = repolint.check_error_text(root)
    assert errors and "401" in errors[0]

    clean = _tree(tmp_path / "clean", {"workbench/ok.py": "def f(exc):\n    return exc.kind == 'auth'\n"})
    assert repolint.check_error_text(clean) == []


def test_new_endpoint_without_frontend_or_manifest_is_an_error(tmp_path: Path) -> None:
    root = _tree(tmp_path, {
        "workbench/api/routes_probe.py": (
            'from fastapi import APIRouter\n'
            'router = APIRouter(prefix="/api")\n'
            '@router.get("/probe-only")\n'
            "def probe() -> dict:\n"
            '    return {}\n'
        ),
        "workbench/static/app.js": "fetch('/api/other');\n",
    })
    errors, warnings = repolint.check_frontend(root)
    assert any("/api/probe-only" in item for item in errors), errors
    assert warnings == []


def test_repo_itself_has_no_unclassified_endpoint() -> None:
    errors, warnings = repolint.check_frontend(ROOT)
    assert errors == [], "有端点没登记：新端点请补前端或写进 tools/frontend_parity.json"
    assert warnings, "已知缺口列表为空 —— 是不是把 manifest 清空了？"
