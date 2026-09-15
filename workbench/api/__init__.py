"""FastAPI 应用装配：路由注册 + 生命周期 + 静态托管。

路由按领域拆分：
- routes_conversations  会话与消息（fork mogul）
- routes_settings       引导数据 + 设置（fork mogul，知识库相关已随功能下线裁剪）
- routes_chat           SSE 流式对话 + 回答反馈（fork mogul）
- routes_modules        模块注册表 + 依赖体检（阶段1B）
- routes_insight        平台洞察（egmp 原生）
- routes_factory        配置工厂（egmp 原生）
- routes_accounts       统一账号库
- routes_browser        托管浏览器/自动登录
- routes_agent          Agent 工具层

（fork 自 mogul 的知识库/钉钉同步功能已按产品方向裁剪，源码删除；
 DB 迁移 1-6 保留历史不可变性，相关表仅留存不再读写。）
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, config, db
from . import routes_chat, routes_conversations, routes_settings

logger = logging.getLogger("workbench.app")


def _optional_routers(app: FastAPI) -> None:
    """注册阶段 1B/2/3 的新路由；单个路由装配失败不拖垮整个应用。"""
    from . import (  # noqa: F401 —— 显式列出便于检索
        routes_accounts,
        routes_agent,
        routes_browser,
        routes_extension,
        routes_factory,
        routes_insight,
        routes_modules,
        routes_monitor,
        routes_update,
    )

    registrations = (
        ("modules", routes_modules),
        ("insight", routes_insight),
        ("factory", routes_factory),
        ("accounts", routes_accounts),
        ("browser", routes_browser),
        ("agent", routes_agent),
        ("extension", routes_extension),
        ("monitor", routes_monitor),
        ("update", routes_update),
    )
    for name, mod in registrations:
        router = getattr(mod, "router", None)
        if router is None:
            logger.warning("路由模块 %s 缺少 router，跳过", name)
            continue
        app.include_router(router)
        logger.info("路由已注册：%s", name)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    db.connect()
    _tool_selfcheck()
    yield


def _tool_selfcheck() -> None:
    """工具插件自检（启动期一条日志，不阻断启动）。

    坏插件是被**静默跳过**的（`ToolHub.load` 的刻意设计），所以这里必须把结果喊出来：
    少了哪个插件、少了哪个必需工具，一眼可见。也可经 `GET /api/agent/selfcheck` 复查。
    """
    try:
        from ..plugin_api import get_hub, verify_required_tools
        from .routes_agent import REQUIRED_TOOLS

        hub = get_hub()
        missing = verify_required_tools(hub, REQUIRED_TOOLS)
        logger.info(
            "工具中枢：%d 个插件 / %d 个工具%s",
            len(hub.plugins), len(hub.specs()),
            f"；装配失败 {hub.failures}" if hub.failures else "",
        )
        if missing:
            logger.error("工具自检未通过：必需工具缺失 %s（对应插件未装配成功）", missing)
    except Exception:  # noqa: BLE001 —— 自检本身绝不能阻断应用启动
        logger.warning("工具自检执行失败", exc_info=True)


def create_app() -> FastAPI:
    app = FastAPI(title="Workbench", version=__version__, lifespan=lifespan)

    @app.middleware("http")
    async def _no_store_static(request, call_next):
        """静态页与首页**禁止缓存**。

        为什么必须这么做（血泪）：本项目是"改完就重建、重建就要立刻看到"的本机工具，
        客户端就是自己的 Electron 渲染进程。`StaticFiles` 默认发 `ETag` + `Last-Modified`，
        于是重建产物后浏览器带 `If-Modified-Since` 复验、服务端回 **304 未修改** →
        **渲染进程继续用自己那份旧副本**，而服务端实吐的已经是新版。
        表现就是「我明明重建了、服务端也对，界面上却还是旧功能」——排查成本极高，
        因为两边各自都"正确"（实测：一轮对话里绕了很久才发现是 304）。
        本机回环读几十 KB 文件的成本可以忽略，**不值得为省这点 IO 换来"改了看不见"**。

        只对静态面生效；`/api/*` 本来就是动态的，不动它（避免影响流式响应）。
        """
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response
    app.include_router(routes_conversations.router)
    app.include_router(routes_settings.router)
    app.include_router(routes_chat.router)
    _optional_routers(app)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(config.STATIC_DIR / "index.html")

    @app.get("/api/health")
    def service_health() -> dict:
        """服务端**身份**（进程级、无副作用）——供壳的就绪探测校验。

        与 `GET /extension/health` 的分工（ADR-0011 §D-4）：
        本端点只回答"你是哪个进程、在哪个端口"，**不写任何状态**，因此可以被反复探测；
        `/extension/health` 回答"执行面（浏览器扩展）活着吗"，它会写 `everConnected` 闩锁，
        是给 UI 的。**不要把探测打到有副作用的端点上。**

        存在理由：改造前壳的 `waitReady()` 只看"18765 上有人回了 200"，于是
        "旧实例占着端口 + 新壳起不来" 会表现成"代码改动没生效"（ADR-0011 §1）。
        """
        import os

        return {
            "app": "Workbench",
            "service": "workbench-server",
            "version": __version__,
            "pid": os.getpid(),
            "frozen": config.FROZEN,
            **config.describe_service(),
        }

    app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
    return app


app = create_app()
