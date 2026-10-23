"""仓库结构门禁（`python tools/repolint.py`）：文件预算 + 错误文本 + 前端覆盖。

## 为什么不是"再加一条 pytest"

本仓的漂移不是"功能错了"，而是"文件悄悄长成 1,700 行 / 错误分类又开始猜字符串"。
这类问题用一次性测试只能守住**写测试那一刻**的形态；要防漂移，需要**棘轮**：
基线只降不升，新增违规当场红。

## 规则

1. **file-budget**：新文件 ≤ 800 行；存量超限文件进 `tools/repolint_baseline.json`，
   只允许变小。`--update` 只把数字**调低**，不会放宽。
2. **error-text**：禁止用字符串字面量判断错误文本（与 `tests/test_error_identity.py` 同一规则，
   两份实现互为反证）。
3. **frontend**（`--frontend`）：报告"有路由但静态页没调用"的端点；默认只报告不失败，
   因为动态拼接的路径会产生假阳性。要把它变成门禁，先把例外写进
   `tools/frontend_parity.json`。

用法：
    python tools/repolint.py
    python tools/repolint.py --update
    python tools/repolint.py --frontend
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKBENCH = ROOT / "workbench"
BASELINE = ROOT / "tools" / "repolint_baseline.json"
FRONTEND_PARITY = ROOT / "tools" / "frontend_parity.json"
FILE_BUDGET = 800


def line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8", errors="replace").splitlines())


def check_budget(root: Path, baseline: dict[str, int]) -> tuple[list[str], dict[str, int]]:
    errors: list[str] = []
    updates: dict[str, int] = {}
    base = root / "workbench"
    for path in sorted(base.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        lines = line_count(path)
        if lines <= FILE_BUDGET:
            continue
        old = baseline.get(rel)
        if old is None:
            errors.append(f"{rel}: {lines} 行 > {FILE_BUDGET}，且不在基线里（新文件不许超预算）")
        elif lines > old:
            errors.append(f"{rel}: {lines} 行 > 基线 {old}（只许降，不许升）")
        elif lines < old:
            updates[rel] = lines
    return errors, updates


_ERROR_TEXT_NAMES = {"text", "msg", "message", "str_exc", "detail", "body"}
_ERROR_STRINGS = {"401", "403", "404", "500", "502", "会话", "未登录", "重新登录",
                  "Method not found", "-32601"}


def _is_error_text(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id in _ERROR_TEXT_NAMES
    if isinstance(node, ast.Call):
        return isinstance(node.func, ast.Name) and node.func.id == "str"
    return False


def check_error_text(root: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted((root / "workbench").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            left = node.left
            if not (isinstance(left, ast.Constant) and isinstance(left.value, str)):
                continue
            if left.value not in _ERROR_STRINGS:
                continue
            for op, right in zip(node.ops, node.comparators, strict=True):
                if isinstance(op, (ast.In, ast.NotIn)) and _is_error_text(right):
                    rel = path.relative_to(root).as_posix()
                    errors.append(f"{rel}:{node.lineno}: {left.value!r} in 错误文本")
    return errors


_ROUTER_PREFIX = re.compile(r"APIRouter\([^)]*prefix\s*=\s*[\"']([^\"']+)")
_ROUTE_DECORATOR = re.compile(
    r"@router\.(get|post|put|delete|patch)\(\s*[\"']([^\"']+)[\"']")


def route_paths(root: Path) -> list[str]:
    """从 `api/routes_*.py` 抽 `(prefix + decorator path)`。"""
    paths: list[str] = []
    for path in sorted((root / "workbench" / "api").glob("routes_*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        prefix_match = _ROUTER_PREFIX.search(text)
        prefix = prefix_match.group(1) if prefix_match else ""
        for _method, route in _ROUTE_DECORATOR.findall(text):
            full = f"{prefix.rstrip('/')}/{route.lstrip('/')}" if route != "/" else prefix or "/"
            paths.append(full)
    return sorted(set(paths))


def frontend_coverage(root: Path) -> list[tuple[str, bool]]:
    """端点是否在 `workbench/static/**/*.js` 里出现过（子串匹配，保守）。"""
    js = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted((root / "workbench" / "static").rglob("*.js"))
    )
    report: list[tuple[str, bool]] = []
    for route in route_paths(root):
        # 带 `{param}` 的路由在 JS 里通常是模板串/拼接：用参数前的静态前缀判断，
        # 否则 `"/api/attachments/" + id + "/raw"` 会被误报成"没人用"。
        probe = route.split("{", 1)[0].rstrip("/") if "{" in route else route.rstrip("/")
        report.append((route, probe in js))
    return report


def check_frontend(root: Path, *, strict: bool = False) -> tuple[list[str], list[str]]:
    """未引用端点必须**显式登记**：apiOnly（设计如此）或 missing（已知缺口）。

    新端点默认红；existing missing 只告警（`--strict` 才失败），这样缺口不会
    被"又加了一个没 UI 的端点"稀释，而历史债也不会让门禁永远红。
    """
    manifest = json.loads(FRONTEND_PARITY.read_text(encoding="utf-8")) \
        if FRONTEND_PARITY.exists() else {}
    api_only = set(manifest.get("apiOnly") or {})
    missing = set(manifest.get("missing") or {})
    errors: list[str] = []
    warnings: list[str] = []
    for route, seen in frontend_coverage(root):
        if seen:
            continue
        if route in api_only:
            continue
        if route in missing:
            warnings.append(f"{route}：{manifest['missing'][route]}")
            continue
        errors.append(f"{route} 没有前端调用，也没有在 tools/frontend_parity.json 登记")
    if strict:
        errors.extend(f"[strict] {item}" for item in warnings)
        warnings = []
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="akso-workbench 结构门禁")
    parser.add_argument("--update", action="store_true", help="把基线数字调低（不会调高）")
    parser.add_argument("--frontend", action="store_true", help="检查前端未接的端点")
    parser.add_argument("--strict", action="store_true", help="把已知前端缺口也当成失败")
    args = parser.parse_args(argv)

    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    budget_errors, updates = check_budget(ROOT, baseline)
    errors = [*budget_errors, *check_error_text(ROOT)]

    if args.update:
        merged = {**baseline, **updates}
        BASELINE.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        print(f"repolint baseline updated: {len(updates)} file(s) lowered")

    if args.frontend:
        frontend_errors, frontend_warnings = check_frontend(ROOT, strict=args.strict)
        errors.extend(frontend_errors)
        for warning in frontend_warnings:
            print(f"  FRONTEND_GAP {warning}")

    if errors:
        print("REPOLINT: FAIL")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"REPOLINT: PASS（{len(list((ROOT / 'workbench').rglob('*.py')))} files, "
          f"budget ≤ {FILE_BUDGET}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
