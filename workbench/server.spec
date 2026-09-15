# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包定义：AksoServer（服务端 sidecar，onedir）。

构建：pyinstaller workbench/server.spec --noconfirm
产物：dist/AksoServer/ → build.ps1 v3 交给 electron-builder（extraResources）
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
]

ms_pw = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
if ms_pw.exists():
    for browser_dir in sorted(ms_pw.glob("chromium*")):
        if browser_dir.is_dir():
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
    name="AksoServer",
    debug=False,
    console=True,
    icon=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="AksoServer")
