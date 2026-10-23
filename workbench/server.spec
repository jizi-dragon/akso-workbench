# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包定义：WorkbenchServer（服务端 sidecar，onedir）。

构建：pyinstaller workbench/server.spec --noconfirm
产物：dist/WorkbenchServer/ → build.ps1 交给 electron-builder（extraResources）
要点：
- 2026-10-19 内置浏览器下线：不再收集 playwright / chromium / ms-playwright；
- workbench/static 与 adapters/ 作为数据文件；
- 入口 = workbench/server_entry.py（uvicorn 阻塞运行）。
"""

from pathlib import Path

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
    # 出站地址守卫（2026-09-24）。它是被**静态**导入的（plugins/builtins.py 与
    # plugins/websearch.py），照理 PyInstaller 跟得下去；但这两个插件本身是
    # hiddenimport、登记它们是本仓的既有惯例（cache 同样被静态导入却也在列）。
    # 漏收的后果是**静默的**：plugins/__init__.py 的 load_all 会把 ImportError
    # 吞成一条 failure，于是 builtins 整插件消失（6 个工具没了）而应用照常启动。
    "workbench.net_guard",
    # 可选路由是**按名字动态导入**的（api/__init__.py::_OPTIONAL_ROUTERS 里是字符串），
    # PyInstaller 的静态分析看不到。2026-10-19 把那段 `from . import (...)` 改成
    # 逐个 try/except 之后，这些模块**必须**在这里显式登记，否则打包后它们会整段
    # 消失，而应用照常启动（静默少功能，正是 P7 要防的那一类）。
    "workbench.api.routes_modules",
    "workbench.api.routes_insight",
    "workbench.api.routes_attachments",
    "workbench.api.routes_accounts",
    "workbench.api.routes_agent",
    "workbench.api.routes_mcp",
    "workbench.api.routes_sandbox",
    "workbench.api.routes_skills",
    "workbench.api.routes_checkpoints",
    "workbench.api.routes_schedules",
    "workbench.api.routes_goals",
    "workbench.api.routes_vault",
    # MCP：连接器与注册器都在函数体内延迟导入（不阻塞启动 / 不循环依赖）。
    "workbench.hub",
    "workbench.mcp_registry",
    "workbench.services.mcp",
    "workbench.plugins",
    "workbench.plugins.builtins",
    "workbench.plugins.websearch",
    "workbench.plugins.insight",
    "workbench.plugins.accounts",
    "workbench.plugins.memory",
    "workbench.memory",
    "workbench.schedules",
    "workbench.goals",
    "workbench.scheduler",
    "workbench.skills.folders",
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
