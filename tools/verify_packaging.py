"""打包链路前置体检（秒级）：把「要跑完 PyInstaller + Tauri/NSIS 才暴露」的问题提前拦住。

为什么需要：2026-09-11 实测，`workbench/server_entry.py` 在一次重构中丢失，而 `server.spec`
仍指向它 → 整条构建在第 5 步（PyInstaller，数分钟）才失败。
开发态走 venv + uvicorn 完全不受影响，所以这类断裂能潜伏很久。

本脚本只做静态核对（不构建、不改文件），覆盖：
  P1 server.spec 的入口脚本存在
  P2 该入口只用绝对导入（PyInstaller 把它当顶层脚本执行，相对导入必 ImportError）
  P5 tools/build_tauri.ps1 具备 UTF-8 BOM（PS5.1 约定；编辑后极易丢失）
  P7 工具插件清单（`plugins/__init__.py::_MODULES`）全部进了 server.spec 的 hiddenimports
  P9 版本真源同步（复用 tools/bump.py check，不另写一份正则）
  P10 Tauri 资源映射仍指向 `dist/WorkbenchServer`，且构建脚本会先生成它
  P11 Tauri 的 UI/图标存在，Cargo.toml 与 tauri.conf.json 都在

> **2026-10-19 Electron 退役**：P3（extraResources）/P6（electron-builder files 白名单）
> 随 `desktop/` 一起删除；Tauri 的资源映射由 P10 守。

用法：
    .venv\\Scripts\\python.exe tools\\verify_packaging.py
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "workbench" / "server.spec"
BUILD_PS1 = ROOT / "tools" / "build_tauri.ps1"
PLUGINS_INIT = ROOT / "workbench" / "plugins" / "__init__.py"
TAURI = ROOT / "src-tauri"
TAURI_CONF = TAURI / "tauri.conf.json"
CARGO = TAURI / "Cargo.toml"


def _plugin_modules() -> list[str]:
    """读 `workbench/plugins/__init__.py` 的 `_MODULES` 清单（AST，不 import）。"""
    try:
        tree = ast.parse(PLUGINS_INIT.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign) or getattr(node.target, "id", "") != "_MODULES":
            continue
        value = node.value
        if isinstance(value, ast.Tuple):
            return [e.value for e in value.elts if isinstance(e, ast.Constant)]
    return []


def main() -> int:
    spec_text = SPEC.read_text(encoding="utf-8")
    checks: dict[str, bool] = {}
    details: list[str] = []

    # P1 入口脚本存在
    entry_rel = ""
    expr_match = re.search(r"Analysis\(\s*\[\s*str\((.*?)\)\s*[,\]]", spec_text, re.S)
    if expr_match:
        entry_rel = "/".join(re.findall(r'"([^"]+)"', expr_match.group(1)))
    entry_path = ROOT / entry_rel if entry_rel else ROOT / "__missing__"
    checks["P1 server.spec 的入口脚本存在"] = bool(entry_rel) and entry_path.is_file()
    details.append(f"   入口 = {entry_rel or '(未解析到)'} → {'存在' if entry_path.is_file() else '缺失'}")

    # P2 入口不得用相对导入
    if entry_path.is_file():
        body = entry_path.read_text(encoding="utf-8")
        code = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))
        rel_import = re.search(r"^\s*from\s+\.", code, re.M)
        checks["P2 入口只用绝对导入（顶层脚本语境）"] = rel_import is None
        if rel_import:
            details.append(f"   发现相对导入：{rel_import.group(0).strip()}")
    else:
        checks["P2 入口只用绝对导入（顶层脚本语境）"] = False

    # P5 build_tauri.ps1 的 BOM
    head = BUILD_PS1.read_bytes()[:3] if BUILD_PS1.is_file() else b""
    checks["P5 tools/build_tauri.ps1 具备 UTF-8 BOM（PS5.1）"] = head == b"\xef\xbb\xbf"
    if head != b"\xef\xbb\xbf":
        details.append("   缺 BOM → PS5.1 下中文会乱码；用 [System.IO.File]::WriteAllText(..., UTF8Encoding($true)) 补回")

    # P7 工具插件清单 vs PyInstaller hiddenimports
    declared_plugins = _plugin_modules()
    undeclared = [m for m in declared_plugins if f'"workbench.plugins.{m}"' not in spec_text]
    checks["P7 工具插件清单全部进了 server.spec hiddenimports"] = (
        bool(declared_plugins) and not undeclared
    )
    details.append(f"   插件清单 = {declared_plugins or '读取失败'}")
    if undeclared:
        details.append(f"   缺：{undeclared}（打包后这些插件静默消失）")

    # P9 版本真源同步
    check_proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "bump.py"), "check"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    checks["P9 版本真源同步（bump.py check）"] = check_proc.returncode == 0
    if check_proc.returncode != 0:
        details.append("   " + (check_proc.stderr or check_proc.stdout or "").strip().replace("\n", "\n   "))

    # P10 Tauri 资源映射 + 构建脚本
    try:
        conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        conf = {}
    resources = conf.get("bundle", {}).get("resources", {})
    build_text = BUILD_PS1.read_text(encoding="utf-8") if BUILD_PS1.is_file() else ""
    resource_ok = (
        isinstance(resources, dict)
        and resources.get("../dist/WorkbenchServer") == "server"
        and "PyInstaller" in build_text
        and "WorkbenchServer" in build_text
    )
    checks["P10 Tauri 资源映射指向 dist/WorkbenchServer，构建脚本负责生成"] = resource_ok
    details.append(f"   bundle.resources = {resources}")

    # P11 Tauri 必要文件
    checks["P11 Tauri UI/图标/Cargo 配置齐全"] = all([
        (TAURI / "ui" / "index.html").is_file(),
        (TAURI / "icons" / "icon.ico").is_file(),
        CARGO.is_file(),
        TAURI_CONF.is_file(),
    ])

    print("\n".join(details))
    print()
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    passed = sum(checks.values())
    print(f"\nPACKAGING_CHECKS: {passed}/{len(checks)}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
