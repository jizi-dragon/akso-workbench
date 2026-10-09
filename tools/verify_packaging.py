"""打包链路前置体检（秒级）：把「要跑完 PyInstaller + electron-builder 才暴露」的问题提前拦住。

为什么需要：2026-09-11 实测，`workbench/server_entry.py` 在一次重构中丢失，而 `server.spec`
仍指向它 → 整条 `tools/build.ps1` 在第 5 步（PyInstaller，数分钟）才失败，且失败输出被脚本
吞掉。开发态走 venv + uvicorn 完全不受影响，所以这类断裂能潜伏很久。

本脚本只做静态核对（不构建、不改文件），覆盖：
  P1 server.spec 的入口脚本存在
  P2 该入口只用绝对导入（PyInstaller 把它当顶层脚本执行，相对导入必 ImportError）
  P3 桌面壳 spawn 的 sidecar 路径 与 electron-builder 的 extraResources 映射一致
  P5 tools/build.ps1 具备 UTF-8 BOM（PS5.1 约定；编辑后极易丢失）
  P6 桌面壳的每个源文件都在 electron-builder 的 files 白名单里
  P7 工具插件清单（`plugins/__init__.py::_MODULES`）全部进了 server.spec 的 hiddenimports
  P8 server.spec 有「缺 chromium 就失败」的守卫（**静态契约，任何环境都查**）
  P8b 本机 chromium 已安装（**环境事实**：`WORKBENCH_REQUIRE_CHROMIUM=1` 时必查 = 构建机；
      CI 不要求——runner 上没有浏览器也不打包，那条判据在 CI 里永远修不好）
  P9 版本四写同步（复用 tools/bump.py check，不另写一份正则）

> **2026-10-09 变更**：原先还有 P4a/P4b/P4c（安装包携带的浏览器扩展产物与版本一致性）
> 与 P10（`/extension/*` 访问令牌边界）。浏览器扩展已整体移出本仓库
> （用户决策：桌面端与扩展不耦合，本仓只需账号管理页承接云端数据环境），
> 这四项随之删除——它们守的东西已经不存在了。

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
PLUGINS_INIT = ROOT / "workbench" / "plugins" / "__init__.py"


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

    # P8 chromium 守卫（静态契约）+ P8b 本机已装（环境事实）—— 拦在 PyInstaller（数分钟）之前
    #
    # ## 为什么必须查（2026-09-17）
    # 旧实现的 `server.spec` 是 `if ms_pw.exists():` —— 机器上没装浏览器时它
    # 既不收集**也不报错**，于是打出一个"成功"的、**没有浏览器**的安装包，
    # 问题只在客户运行期暴露（Monitor 录制 / 免密直达 / 自愈重登全废）。
    #
    # ## 为什么分两半查（2026-09-18 修：它把 CI 判红了）
    # 原实现**只看本机有没有装 chromium**，于是**在没有浏览器的 CI runner 上必然 FAIL** ——
    # 而 CI 什么都不打包，它根本没这个环境，也不可能"修好"。一个在 CI 里永远修不好的红项，
    # 等于把整条门禁变成噪声（首次红就是这个原因，见 run #17）。
    #
    # 这里把两件事分开，各自放在它能被判定的地方：
    #   ① **静态契约**（任何环境都能查）：`server.spec` 里**有**那道"缺 chromium 就炸"的守卫。
    #      这才是代码正确性——守卫在，构建机上一个浏览器都没有时，打包会**明确失败**而不是静默产出坏包。
    #   ② **环境事实**（只有构建机说得清）：本机 chromium 真的在。
    #      由 `WORKBENCH_REQUIRE_CHROMIUM=1` 打开，`build.ps1` 会设它。
    spec_has_guard = (
        "chromium_dirs" in spec_text
        and "raise SystemExit" in spec_text
        and 'ms_pw.glob("chromium*")' in spec_text
    )
    checks["P8 server.spec 有「缺 chromium 就失败」的守卫（静态契约）"] = spec_has_guard
    if not spec_has_guard:
        details.append("   server.spec 里找不到 chromium 守卫 → 缺浏览器时会静默打出坏包（回退到 2026-09-17 的形态）")

    ms_pw = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    chromium_dirs = sorted(d.name for d in ms_pw.glob("chromium*") if d.is_dir()) if ms_pw.exists() else []
    if chromium_dirs:
        details.append(f"   chromium = {chromium_dirs}（{ms_pw}）")
    else:
        details.append(
            f"   本机无 chromium（{ms_pw}）→ 跑 .venv\\Scripts\\python.exe -m playwright install chromium"
        )
    # `expect` 的语义：**这台机器所扮演的角色是否要求它有 chromium**。
    #   - 构建机（`WORKBENCH_REQUIRE_CHROMIUM=1`，由 build.ps1 设）：**必须真有**——
    #     缺了这次打包就会产出没有浏览器的安装包；
    #   - 其他环境（CI runner、开发机）：**不做要求** → 无论有没有都算 PASS。
    #
    # ⚠ 别把它写成 `present == expect`：那等于"不要求时必须**没有**"，
    # 于是有浏览器的开发机反而会被判红（本次实测踩到）。
    # 这一项**始终存在且始终参与计数**，所以两种环境下都是稳定的 13/13。
    expect_chromium = os.environ.get("WORKBENCH_REQUIRE_CHROMIUM") == "1"
    checks["P8b 本机 chromium 已安装（构建机必须；其他环境不要求）"] = (
        bool(chromium_dirs) if expect_chromium else True
    )

    # P9 版本四写同步（pyproject / __init__ / desktop pkg / .version.json）
    # 为什么必须查：手改单一文件是**已发生过**的事故形态——安装包命名（读 pyproject）、
    # 壳内显示版本（读 package.json）会各说各话；
    # 而 build.ps1 第 1 步的 bump 自查只在构建路径上生效，拉取后直接打包就绕过了。
    # 判据复用 bump.py check 的同一套逻辑（单一真源），此处不另写一份正则。
    check_proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "bump.py"), "check"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    checks["P9 版本四写同步（bump.py check）"] = check_proc.returncode == 0
    if check_proc.returncode != 0:
        details.append("   " + (check_proc.stderr or check_proc.stdout or "").strip().replace("\n", "\n   "))

    print("\n".join(details))
    print()
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    passed = sum(checks.values())
    print(f"\nPACKAGING_CHECKS: {passed}/{len(checks)}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
