# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包定义（桌面壳）：pyinstaller shell/shell.spec

产物：dist/AksoWorkbench/ 目录（含壳 exe + workbench 包 + 静态资源）。
"""

from pathlib import Path

ROOT = Path(SPECPATH).parent  # akso-mogul/

a = Analysis(
    [str(ROOT / "shell" / "shell.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "workbench" / "static"), "workbench/static"),
    ],
    hiddenimports=["uvicorn.logging", "uvicorn.loops", "uvicorn.protocols", "workbench.api"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
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
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="AksoWorkbench")
