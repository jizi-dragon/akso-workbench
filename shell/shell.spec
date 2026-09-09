# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包定义：Akso Workbench 桌面应用（onedir）。

构建：pyinstaller shell/shell.spec --noconfirm
要点：
- playwright：collect_all 收集驱动子目录（node 运行时）——浏览器本体仍需
  目标机执行 playwright install chromium（首次启动引导）；
- pywebview/pythonnet：collect 收集 .NET 桥；
- workbench/static 与 adapters/ 作为数据文件打入；
- 服务子进程 = 自身 exe --server 重入（见 shell/shell.py）。
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

# 打入 chromium 浏览器本体（目标机免 install；运行时 browser_pool 会把
# PLAYWRIGHT_BROWSERS_PATH 指到 _MEIPASS/ms-playwright）
ms_pw = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
if ms_pw.exists():
    for browser_dir in sorted(ms_pw.glob("chromium*")):
        if browser_dir.is_dir():
            datas.append((str(browser_dir), f"ms-playwright/{browser_dir.name}"))

for pkg in ("playwright", "webview", "clr_loader", "pythonnet"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    ["shell.py"],
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
    name="AksoWorkbench",
    debug=False,
    console=True,
    icon=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="AksoWorkbench")
