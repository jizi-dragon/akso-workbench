# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包定义：WorkbenchServer（服务端 sidecar，onedir）。

构建：pyinstaller workbench/server.spec --noconfirm
产物：dist/WorkbenchServer/ → build.ps1 v3 交给 electron-builder（extraResources）
要点：
- playwright collect_all（node 驱动）+ chromium 浏览器本体打入（运行时
  browser_pool 设 PLAYWRIGHT_BROWSERS_PATH=_MEIPASS/ms-playwright）；
- workbench/static 与 adapters/ 作为数据文件；
- 入口 = workbench/server_entry.py（uvicorn 阻塞运行）。
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "workbench" / "static"), "workbench/static"),
    (str(ROOT / "adapters"), "adapters"),
]
binaries = []
hiddenimports = [
    "workbench",
    "workbench.api",
    # 工具插件是**按名字动态导入**的（plugins/__init__.py 里 importlib.import_module
    # 拼字符串），PyInstaller 的静态分析看不到它们 —— 漏收的后果是"装完没功能"
    # 且**不报错**（2026-09-15 工具插件层上线时补）。
    # ⚠️ 新增插件必须同步这里；`tools/verify_packaging.py` 的 P7 会拿
    #    plugins/__init__.py 的 `_MODULES` 清单来对账，漏了直接 FAIL（实测拦下过 websearch）。
    "workbench.plugin_api",
    "workbench.approvals",
    "workbench.cache",
    "workbench.plugins",
    "workbench.plugins.builtins",
    "workbench.plugins.websearch",
    "workbench.plugins.insight",
    "workbench.plugins.factory",
    "workbench.plugins.accounts",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "anyio._backends._asyncio",
    # 附件解析（2026-09-22，用户下达「用户会传 word/pdf/excel/markdown」）：
    # 这三个是在**函数体里 import** 的（`services/parsing.py` 故意延迟导入——没装依赖时
    # 不该让整个模块 import 失败），PyInstaller 的静态分析对函数体内的导入**能**看到，
    # 但这里显式列出来是"体检"而不是"补救"：漏收的后果是**打包后传文件才失败**，
    # 而开发机上永远复现不了（`.venv` 里装着）。lxml 是 python-docx 的二进制依赖，
    # 由 PyInstaller 自带的 hook 收。
    "docx",
    "openpyxl",
    "pypdf",
]

ms_pw = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
# ⚠ **chromium 缺失必须炸，不能静默跳过**（2026-09-17 加固）。
# 旧实现是 `if ms_pw.exists():` —— 机器上没装浏览器时它既不收集**也不报错**，
# 于是打出一个"成功"的、**没有浏览器**的安装包，问题只在客户运行期暴露
# （Monitor 录制 / 免密直达 / 自愈重登全废）。这里与 tools/verify_packaging.py 的 P8
# 构成**双保险**：P8 在打包前拦（省掉一次几分钟的 PyInstaller），这里在 PyInstaller
# 内部兜底（有人绕过 build.ps1 直接跑 spec 时仍然拦得住）。
chromium_dirs = [d for d in sorted(ms_pw.glob("chromium*")) if d.is_dir()] if ms_pw.exists() else []
if not chromium_dirs:
    raise SystemExit(
        f"打包中止：{ms_pw} 下没有任何 chromium* 目录，安装包会缺少浏览器内核。\n"
        "  处置： .\\.venv\\Scripts\\python.exe -m playwright install chromium\n"
        "  （原因与后果见 docs/adr/0009-*.md 与 tools/verify_packaging.py 的 P8）"
    )
for browser_dir in chromium_dirs:
    datas.append((str(browser_dir), f"ms-playwright/{browser_dir.name}"))

for pkg in ("playwright",):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    [str(ROOT / "workbench" / "server_entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "tests"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WorkbenchServer",
    debug=False,
    console=True,
    icon=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="WorkbenchServer")
