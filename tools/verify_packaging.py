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
  P6 桌面壳的每个源文件都在 electron-builder 的 files 白名单里
  P7 工具插件清单（`plugins/__init__.py::_MODULES`）全部进了 server.spec 的 hiddenimports
  P8 chromium 内核可打包（缺失会让安装包**没有浏览器**，且旧实现静默通过）
  P9 版本五写同步（复用 tools/bump.py check，不另写一份正则）
  P10 `/extension/*` 访问令牌边界（P0-1：中间件已装 + 豁免面最小 + UI 端点已迁出该前缀）

用法：
    .venv\\Scripts\\python.exe tools\\verify_packaging.py
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
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
PLUGINS_INIT = ROOT / "workbench" / "plugins" / "__init__.py"


def _project_version() -> str:
    m = re.search(r'^version\s*=\s*"([^"]+)"', PYPROJECT.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else "?"


def _plugin_modules() -> list[str]:
    """读 `workbench/plugins/__init__.py` 的 `_MODULES` 清单（AST，不 import）。

    不 import 的原因：这个脚本要在**没装依赖的机器上**也能跑（它本来就是构建前的体检）。
    找不到就返回空列表 —— P7 会因此判 FAIL，那是刻意的：读不到清单等于体检没做。
    """
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
    spawn_ok = "path.join(process.resourcesPath, 'server', 'WorkbenchServer.exe')" in main_js
    checks["P3 壳 spawn 的 resources/server/WorkbenchServer.exe 有对应 extraResources"] = (
        spawn_ok and to_map.get("server") == "../dist/WorkbenchServer"
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

    # P6 壳源码文件 vs electron-builder files 白名单
    # 为什么必须查：files 是**白名单**，新增 .js 模块（如 0.3.3 的 updater.js / shell-state.js）
    # 若忘了加，开发态一切正常、装出来的应用却 require 失败——只有打包后才会暴露。
    listed = pkg.get("build", {}).get("files", [])
    shell_sources = sorted(p.name for p in MAIN_JS.parent.glob("*.js"))
    missing_src = [n for n in shell_sources if n not in listed]
    checks["P6 桌面壳全部 .js 都在 files 白名单"] = not missing_src
    details.append(f"   files = {listed}；壳源文件 = {shell_sources}")
    if missing_src:
        details.append(f"   缺：{missing_src}（打包后会 require 失败）")

    # P7 工具插件清单 vs PyInstaller hiddenimports
    # 为什么必须查：插件是 `importlib.import_module(f"{__name__}.{name}")` **按名字动态导入**的，
    # PyInstaller 的静态分析看不到；而插件加载器把坏插件隔离成一条 warning（刻意的，
    # "坏插件不拖垮宿主"），于是「漏打进去」与「加载失败」表现完全一样：安装后少几个工具、
    # 不报任何错。P7 拿源码里的 `_MODULES` 清单去比对 server.spec，把这类静默失败挡在构建前。
    declared_plugins = _plugin_modules()
    undeclared = [m for m in declared_plugins if f'"workbench.plugins.{m}"' not in spec_text]
    checks["P7 工具插件清单全部进了 server.spec hiddenimports"] = (
        bool(declared_plugins) and not undeclared
    )
    details.append(f"   插件清单（plugins/__init__.py::_MODULES）= {declared_plugins or '读取失败'}")
    if undeclared:
        details.append(f"   缺：{undeclared}（打包后这些插件静默消失）")

    # P8 chromium 必须可打包 —— 拦在 PyInstaller（数分钟）之前
    # 为什么必须查（2026-09-17）：旧实现的 `server.spec` 是 `if ms_pw.exists():` —— 机器上没装
    # 浏览器时它既不收集**也不报错**，于是打出一个"成功"的、**没有浏览器**的安装包，
    # 问题只在客户运行期暴露（Monitor 录制 / 免密直达 / 自愈重登全废）。
    # 本项与 `server.spec` 内的 SystemExit 构成双保险：这里省掉一次几分钟的 PyInstaller。
    ms_pw = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    chromium_dirs = sorted(d.name for d in ms_pw.glob("chromium*") if d.is_dir()) if ms_pw.exists() else []
    checks["P8 chromium 内核可打包（缺失会让安装包没有浏览器）"] = bool(chromium_dirs)
    if chromium_dirs:
        details.append(f"   chromium = {chromium_dirs}（{ms_pw}）")
    else:
        details.append(
            f"   缺：{ms_pw} 下无 chromium* 目录"
            " → 跑 .venv\\Scripts\\python.exe -m playwright install chromium"
        )

    # P9 版本五写同步（pyproject / __init__ / desktop pkg / 扩展 manifest+package / .version.json）
    # 为什么必须查：手改单一文件是**已发生过**的事故形态——安装包命名（读 pyproject）、
    # 壳内显示版本（读 package.json）、扩展弹窗版本（读 manifest）会各说各话；
    # 而 build.ps1 第 1 步的 bump 自查只在构建路径上生效，拉取后直接打包就绕过了。
    # 判据复用 bump.py check 的同一套逻辑（单一真源），此处不另写一份正则。
    check_proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "bump.py"), "check"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    checks["P9 版本五写同步（bump.py check）"] = check_proc.returncode == 0
    if check_proc.returncode != 0:
        details.append("   " + (check_proc.stderr or check_proc.stdout or "").strip().replace("\n", "\n   "))

    # P10 `/extension/*` 的访问令牌边界（P0-1）
    # 为什么必须查：这不是"某条路由忘了加依赖"，而是**整段前缀**的安全姿态。
    # 它靠三件事同时成立：① 中间件真的被 create_app 装上；② 桌面 UI 用的那几条
    # "不碰凭据"的端点**不在**受保护前缀里（否则它们只能靠开豁免，而豁免一开，
    # "整个前缀需要令牌"就退化成人工维护的清单）；③ 自举端点仍然存在（否则扩展死锁）。
    api_init = (ROOT / "workbench" / "api" / "__init__.py").read_text(encoding="utf-8")
    auth_src = (ROOT / "workbench" / "api" / "extension_auth.py").read_text(encoding="utf-8")
    shell_src = (ROOT / "workbench" / "api" / "routes_shell.py").read_text(encoding="utf-8")
    middleware_installed = "ExtensionAuthMiddleware" in api_init and "add_middleware" in api_init
    shell_registered = "routes_shell" in api_init
    hello_defined = 'HELLO_PATH = "/extension/hello"' in auth_src
    exempt_ok = 'EXEMPT_PATHS = frozenset({HELLO_PATH, "/extension/health"})' in auth_src
    ui_routes_relocated = all(
        f'@router.post("/{name}")' in shell_src for name in ("launch-chrome", "update-state", "setup-helper")
    )
    checks["P10 /extension/* 访问令牌边界（中间件+豁免面+UI 端点已迁出）"] = (
        middleware_installed and shell_registered and hello_defined and exempt_ok and ui_routes_relocated
    )
    if not all((middleware_installed, shell_registered, hello_defined, exempt_ok, ui_routes_relocated)):
        details.append(
            f"   中间件={middleware_installed} 壳路由注册={shell_registered} 自举端点={hello_defined} "
            f"豁免面={exempt_ok} UI 端点已迁出={ui_routes_relocated}"
            " → /extension/* 可能又变回无鉴权（CODEBASE_OVERVIEW R1）"
        )

    print("\n".join(details))
    print()
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    passed = sum(checks.values())
    print(f"\nPACKAGING_CHECKS: {passed}/{len(checks)}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
