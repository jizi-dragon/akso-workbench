"""版本号演进工具（规则 A，用户定稿）：

- push（代码修改推送）  → PATCH +1          0.0.1 → 0.0.2
- build（构建安装包）   → MINOR +1 且 PATCH 重置 1   0.0.2 → 0.1.1 → 0.2.1

版本唯一真源 = pyproject.toml [project].version；
workbench/__init__.py 的 __version__ 由本工具同步写入（双文件机制）。

用法：
    python tools/bump.py push    # 推送前手动执行（或由 pre-push 钩子调用）
    python tools/bump.py build   # 由 tools/build.ps1 在构建开头自动执行
    python tools/bump.py show    # 显示当前版本
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
INIT_PY = ROOT / "workbench" / "__init__.py"
VERSION_CACHE = ROOT / ".version.json"


def _read() -> tuple[int, int, int]:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"(\d+)\.(\d+)\.(\d+)"', text, re.M)
    if not match:
        raise SystemExit("pyproject.toml 中未找到 version 字段")
    return tuple(int(x) for x in match.groups())  # type: ignore[return-value]


def _write(major: int, minor: int, patch: int) -> None:
    version = f"{major}.{minor}.{patch}"
    text = PYPROJECT.read_text(encoding="utf-8")
    text = re.sub(r'^(version\s*=\s*")\d+\.\d+\.\d+(")', rf"\g<1>{version}\g<2>", text, flags=re.M)
    PYPROJECT.write_text(text, encoding="utf-8", newline="\n")

    init_text = INIT_PY.read_text(encoding="utf-8")
    init_text = re.sub(r'^__version__\s*=\s*"[^"]*"', f'__version__ = "{version}"', init_text, flags=re.M)
    INIT_PY.write_text(init_text, encoding="utf-8", newline="\n")

    VERSION_CACHE.write_text(json.dumps({"version": version}), encoding="utf-8")


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "show"
    major, minor, patch = _read()
    if action == "show":
        print(f"{major}.{minor}.{patch}")
        return 0
    if action == "push":
        patch += 1
    elif action == "build":
        minor += 1
        patch = 1
    else:
        print(f"未知动作：{action}（push / build / show）")
        return 1
    _write(major, minor, patch)
    print(f"{major}.{minor}.{patch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
