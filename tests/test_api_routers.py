"""可选路由装配的**容错**判据（2026-10-19 补）。

## 背景

`workbench/api/__init__.py::_optional_routers` 的文档串从 2026-09 起就写着
「单个路由装配失败不拖垮整个应用」，而实现是一整块 `from . import (...)`：
任何**一个**模块 import 失败 ⇒ 整个应用起不来。注释与实现相反，且没有任何判据。

现在逐个 import / 逐个注册，失败留 WARNING。本文件钉两件事：

1. 一个模块 import 失败时，**其余模块仍然注册**（这是注释承诺的那一条）；
2. 缺 `router` 的模块只跳过它自己（不是静默当成成功）。

## 为什么用注入的 importer

不需要真的把某个模块改坏（那会污染工作区，也会让"恢复"成为一次额外操作）。
注入 importer 让"第 3 个模块失败"成为一次纯内存事件，0.01 秒跑完。
"""

from __future__ import annotations

import importlib
import logging
import types
from pathlib import Path

from fastapi import FastAPI

from workbench import api as api_mod


class _Recorder:
    """最小 app：只记录 `include_router` 收到了什么。"""

    def __init__(self) -> None:
        self.routers: list[object] = []

    def include_router(self, router: object) -> None:
        self.routers.append(router)


def test_one_broken_router_does_not_block_the_rest(caplog) -> None:
    good = types.SimpleNamespace(router=object())
    seen: list[str] = []

    def importer(name: str):
        seen.append(name)
        if name.endswith("routes_attachments"):
            raise ImportError("模拟：某个可选路由的依赖坏了")
        return good

    app = _Recorder()
    with caplog.at_level(logging.WARNING, logger="workbench.app"):
        api_mod._optional_routers(app, importer=importer)

    assert len(seen) == len(api_mod._OPTIONAL_ROUTERS), "每个模块都要尝试一次"
    assert len(app.routers) == len(api_mod._OPTIONAL_ROUTERS) - 1, "坏的那个之外的模块必须注册"
    assert "attachments" in caplog.text and "装配失败" in caplog.text, "失败必须留可见的 WARNING"


def test_module_without_router_is_skipped_not_crashing(caplog) -> None:
    def importer(name: str):
        if name.endswith("routes_vault"):
            return types.SimpleNamespace()  # 忘了定义 router
        return types.SimpleNamespace(router=object())

    app = _Recorder()
    with caplog.at_level(logging.WARNING, logger="workbench.app"):
        api_mod._optional_routers(app, importer=importer)

    assert len(app.routers) == len(api_mod._OPTIONAL_ROUTERS) - 1
    assert "vault" in caplog.text and "缺少 router" in caplog.text


def test_the_real_module_list_still_registers() -> None:
    """前提断言：清单里的模块名全部真实存在，且能挂到一个空 app 上。

    ★ 没有它，"注入 importer"那两条可以在**清单本身写错**时照样绿。
    """
    for _name, module_name in api_mod._OPTIONAL_ROUTERS:
        module = importlib.import_module(f"workbench.api.{module_name}")
        assert hasattr(module, "router"), f"{module_name} 没有 router"

    app = FastAPI()
    api_mod._optional_routers(app)
    # ★ FastAPI 0.141 起 `include_router` 记的是 `_IncludedRouter`（懒展开），
    # 所以 `len(app.routes)` 只有 "4 个内置 + 13 个包装器" = 17；
    # 真正能数的路由要看 OpenAPI paths（当前 78）。断言用后者。
    paths = app.openapi().get("paths", {})
    assert len(paths) >= 60, f"只注册出 {len(paths)} 条路径 —— 路由清单是不是少了一半？"


def test_optional_routes_are_declared_for_pyinstaller() -> None:
    """★ 动态导入的模块必须进 `server.spec` 的 hiddenimports。

    2026-10-19 把 `_optional_routers` 从静态 `from . import (...)` 改成逐个
    `importlib.import_module()`（为了单域失败不拖垮应用）。代价是 PyInstaller
    的静态分析再也看不到它们 —— 打包后这些域会整段消失，而应用照常启动。
    这条判据把"漏登记"变成一次红灯。
    """
    spec = Path(api_mod.__file__).parents[2] / "workbench" / "server.spec"
    text = spec.read_text(encoding="utf-8")
    missing = [
        module_name for _name, module_name in api_mod._OPTIONAL_ROUTERS
        if f'"workbench.api.{module_name}"' not in text
    ]
    assert not missing, f"server.spec 漏登记了可选路由：{missing}"
    for module in ("workbench.mcp_registry", "workbench.services.mcp"):
        assert f'"{module}"' in text, f"server.spec 漏登记了延迟导入模块：{module}"
