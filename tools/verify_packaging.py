"""打包链路前置体检（秒级）：把「要跑完 PyInstaller + electron-builder 才暴露」的问题提前拦住。

为什么需要：2026-09-11 实测，`workbench/server_entry.py` 在一次重构中丢失，而 `server.spec`
仍指向它 → 整条 `tools/build.ps1` 在第 5 步（PyInstaller，数分钟）才失败，且失败输出被脚本
吞掉。开发态走 venv + uvicorn 完全不受影响，所以这类断裂能潜伏很久。

本脚本只做静态核对（不构建、不改文件），覆盖：
  P1 server.spec 的入口脚本存在
  P2 该入口只用绝对导入（PyInstaller 把它当顶层脚本执行，相对导入必 ImportError）
  P3 桌面壳 spawn 的 sidecar 路径 与 electron-builder 的 extraResources 映射一致
  P4 安装包要携带的扩展产物存在，且其 manifest 版本 == 项目版本（三写一致性）
  P5 tools/build.ps1 具备 UTF-8 BOM（PS5.1 约定；编辑后极易丢失）

用法：
    .venv\\Scripts\\python.exe tools\\verify_packaging.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "workbench" / "server.spec"
DESKTOP_PKG = ROOT / "desktop" / "package.json"
MAIN_JS = ROOT / "desktop" / "main.js"
BUILD_PS1 = ROOT / "tools" / "build.ps1"
PYPROJECT = ROOT / "pyproject.toml"
EXT_DIST = ROOT / "extensions" / "quick-login" / "dist"


def _project_version() -> str:
    m = re.search(r'^version\s*=\s*"([^"]+)"', PYPROJECT.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else "?"


def main() -> int:
    spec_text = SPEC.read_text(encoding="utf-8")
    checks: dict[str, bool] = {}
    details: list[str] = []

    # P1 入口脚本存在（spec 里路径写成 str(ROOT / "workbench" / "server_entry.py")——
    # 多段字符串用 / 拼接，故抓整个表达式再拼回相对路径）
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

    # P3 壳的 sidecar 路径 vs extraResources 映射
    pkg = json.loads(DESKTOP_PKG.read_text(encoding="utf-8"))
    resources = pkg.get("build", {}).get("extraResources", [])
    to_map = {r.get("to"): r.get("from") for r in resources if isinstance(r, dict)}
    main_js = MAIN_JS.read_text(encoding="utf-8")
    spawn_ok = "path.join(process.resourcesPath, 'server', 'AksoServer.exe')" in main_js
    checks["P3 壳 spawn 的 resources/server/AksoServer.exe 有对应 extraResources"] = (
        spawn_ok and to_map.get("server") == "../dist/AksoServer"
    )
    details.append(f"   extraResources → {to_map or '(空)'}")

    # P4 扩展产物 + 版本一致（安装包会携带 dist → resources/extension）
    ext_manifest = EXT_DIST / "manifest.json"
    bundled = to_map.get("extension") == "../extensions/quick-login/dist"
    checks["P4a 扩展产物已配置进 extraResources"] = bundled
    if ext_manifest.exists():
        ext_ver = json.loads(ext_manifest.read_text(encoding="utf-8")).get("version")
        ver = _project_version()
        checks["P4b 扩展 manifest 版本 == 项目版本"] = ext_ver == ver
        details.append(f"   项目 v{ver} / 扩展 v{ext_ver}")
        # 扩展关键产物（缺任一，装进去也是坏的）
        needed = ["background.js", "manifest.json", "content/shield-main.js", "ui/popup/popup.js"]
        missing = [n for n in needed if not (EXT_DIST / n).exists()]
        checks["P4c 扩展关键产物齐全"] = not missing
        if missing:
            details.append(f"   缺：{missing}")
    else:
        checks["P4b 扩展 manifest 版本 == 项目版本"] = False
        checks["P4c 扩展关键产物齐全"] = False
        details.append("   扩展产物不存在（先跑 cd extensions/quick-login && npm run build）")

    # P5 build.ps1 的 BOM
    head = BUILD_PS1.read_bytes()[:3]
    checks["P5 tools/build.ps1 具备 UTF-8 BOM（PS5.1）"] = head == b"\xef\xbb\xbf"
    if head != b"\xef\xbb\xbf":
        details.append("   缺 BOM → PS5.1 下中文会乱码；用 [System.IO.File]::WriteAllText(..., UTF8Encoding($true)) 补回")

    print("\n".join(details))
    print()
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    passed = sum(checks.values())
    print(f"\nPACKAGING_CHECKS: {passed}/{len(checks)}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
