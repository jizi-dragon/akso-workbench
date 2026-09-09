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
    )

    registrations = (
        ("modules", routes_modules),
        ("insight", routes_insight),
        ("factory", routes_factory),
        ("accounts", routes_accounts),
        ("browser", routes_browser),
        ("agent", routes_agent),
        ("extension", routes_extension),
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
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Akso Workbench", version=__version__, lifespan=lifespan)
    app.include_router(routes_conversations.router)
    app.include_router(routes_settings.router)
    app.include_router(routes_chat.router)
    _optional_routers(app)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(config.STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
    return app


app = create_app()
