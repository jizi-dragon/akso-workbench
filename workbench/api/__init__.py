"""FastAPI 应用装配：路由注册 + 生命周期 + 静态托管。

路由按领域拆分：
- routes_conversations  会话与消息（fork mogul）
- routes_settings       引导数据 + 设置 + Embedding 测试（fork mogul）
- routes_knowledge      知识工作台（文档/重建索引/检索）（fork mogul）
- routes_sync           钉钉知识库增量同步（fork mogul）
- routes_chat           SSE 流式对话 + 回答反馈（fork mogul）
- routes_modules        模块注册表 + 依赖体检（新，阶段1B）
- routes_insight        平台洞察（封装 akso-cc）（新，阶段1B）
- routes_factory        配置工厂（封装 akso-auto）（新，阶段1B）
- routes_accounts       统一账号库（新，阶段2A）
- routes_browser        托管浏览器/自动登录（新，阶段2B）
- routes_agent          Agent 工具层（新，阶段3）
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, config, db
from . import routes_chat, routes_conversations, routes_knowledge, routes_settings, routes_sync

logger = logging.getLogger("workbench.app")

SCHEDULE_CHECK_INTERVAL = 600  # 定时同步检查周期（秒）


def _optional_routers(app: FastAPI) -> None:
    """注册阶段 1B/2/3 的新路由；单个路由装配失败不拖垮整个应用。"""
    from . import (  # noqa: F401 —— 显式列出便于检索
        routes_accounts,
        routes_agent,
        routes_browser,
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
    sync_task = asyncio.create_task(_scheduled_sync_loop())
    yield
    sync_task.cancel()


async def _scheduled_sync_loop() -> None:
    """周期检查钉钉同步开关与间隔（run_scheduled 内部自行判断是否到期）。"""
    from ..services.dingtalk_sync import run_scheduled

    await asyncio.sleep(15)  # 等服务就绪
    while True:
        try:
            await run_scheduled()
        except Exception:  # noqa: BLE001 —— 定时任务永不拖垮应用
            logger.exception("定时同步循环异常")
        await asyncio.sleep(SCHEDULE_CHECK_INTERVAL)


def create_app() -> FastAPI:
    app = FastAPI(title="Akso Workbench", version=__version__, lifespan=lifespan)
    app.include_router(routes_conversations.router)
    app.include_router(routes_settings.router)
    app.include_router(routes_knowledge.router)
    app.include_router(routes_knowledge.search_router)
    app.include_router(routes_sync.router)
    app.include_router(routes_chat.router)
    _optional_routers(app)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(config.STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
    return app


app = create_app()
