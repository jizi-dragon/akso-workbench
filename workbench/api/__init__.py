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
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, config, db, lifecycle, logging_setup
from . import routes_chat, routes_conversations, routes_settings

logger = logging.getLogger("workbench.app")


def _optional_routers(app: FastAPI) -> None:
    """注册阶段 1B/2/3 的新路由；单个路由装配失败不拖垮整个应用。"""
    from . import (  # noqa: F401 —— 显式列出便于检索
        routes_accounts,
        routes_agent,
        routes_attachments,
        routes_browser,
        routes_extension,
        routes_factory,
        routes_insight,
        routes_modules,
        routes_monitor,
        routes_sandbox,
        routes_shell,
        routes_skills,
        routes_update,
    )

    registrations = (
        ("modules", routes_modules),
        ("insight", routes_insight),
        ("factory", routes_factory),
        ("attachments", routes_attachments),
        ("accounts", routes_accounts),
        ("browser", routes_browser),
        ("agent", routes_agent),
        ("extension", routes_extension),
        ("shell", routes_shell),
        ("monitor", routes_monitor),
        ("sandbox", routes_sandbox),
        ("skills", routes_skills),
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
    # **不管从哪条路起，都保证有日志**：`main.py` / `server_entry.py` 已在更早处装配，
    # 这里是幂等的兜底（直接 `uvicorn workbench.api:app`、测试夹具等路径都能覆盖）。
    logging_setup.configure()
    logging_setup.install_excepthooks()
    db.connect()
    _sandbox_setup()
    _skills_setup()
    _tool_selfcheck()
    # **启动横幅放在这里而不是入口脚本里**：入口脚本只覆盖两条路
    # （`main.py` 与 `server_entry.py`），而**桌面壳的开发态是直接 spawn
    # `python -m uvicorn workbench.api:app`**（见 `desktop/main.js::spawnServer` 的
    # 开发态回退分支）——那条路**不经过任何入口脚本**，于是"版本/端口/数据目录/日志路径/
    # 插件装配失败"全都不会留痕。lifespan 是**所有启动路径的唯一交集**。
    logging_setup.banner(host=config.HOST, port=config.PORT)
    # 父进程监视：壳一旦非正常死亡（崩溃 / 被任务管理器结束），本 sidecar 随之退出，
    # 否则会成为孤儿并占着固定端口，导致下次启动直接失败（2026-09-20 实测复现，见 lifecycle.py）。
    # 放在 lifespan 而不是入口脚本里，理由与上面的横幅相同：**所有启动路径的唯一交集**。
    logger.info("父进程监视：%s", lifecycle.watch_parent())
    logger.info("应用就绪：lifespan 装配完成，监听 %s:%s", config.HOST, config.PORT)
    yield
    logger.info("应用退出：lifespan 收尾")


def _skills_setup() -> None:
    """Skill 装配：建目录 + 接留痕写入器 + **恢复已安装的能力**。

    最后一条是关键：`installed/<id>/current` 指针是"装过什么"的权威记录，
    进程重启后必须据此**重新注册**，否则用户会发现"重启一次我装的 Skill 就没了"
    （而库里还在，看起来像"装了但没生效"——这类静默失效最难查）。
    """
    try:
        from ..skills import audit as skill_audit
        from ..skills import ensure_dirs, installed_current
        from ..skills import library as skill_library
        from ..skills.layout import INSTALLED_DIR

        ensure_dirs()
        skill_audit.install()

        restored: list[str] = []
        failed: list[str] = []
        if INSTALLED_DIR.exists():
            for entry in sorted(INSTALLED_DIR.iterdir()):
                if not entry.is_dir() or installed_current(entry.name) is None:
                    continue
                try:
                    skill_library.install(entry.name)
                    restored.append(entry.name)
                except Exception as exc:  # noqa: BLE001 —— 单个 Skill 坏了不该拖垮启动
                    failed.append(f"{entry.name}: {type(exc).__name__}: {exc}")
        logger.info("Skill 已装配：恢复 %d 个已安装能力%s",
                    len(restored), f"，失败 {failed}" if failed else "")
        if failed:
            logger.warning("以下 Skill 恢复失败（库里的东西还在，可重新安装）：%s", failed)
    except Exception:  # noqa: BLE001 —— 与沙箱同一纪律：装配失败不阻断启动
        logger.warning("Skill 库装配失败（Skill 不可用，但应用继续启动）", exc_info=True)


def _sandbox_setup() -> None:
    """沙箱启动期装配：建目录 + 接审计写入器。

    与 `_tool_selfcheck` 同一纪律：**任何失败都不阻断启动**，但必须留下可见记录——
    "沙箱没装好"比"沙箱不存在"更危险，因为它会给人虚假的安全感。
    """
    try:
        from ..sandbox import audit, ensure_dirs

        ensure_dirs()
        audit.install_db_writer()
        logger.info("沙箱已装配：目录就绪，审计写入器已接入 sandbox_run")
    except Exception:  # noqa: BLE001 —— 沙箱装配失败不能拖垮应用启动
        logger.warning("沙箱装配失败（沙箱不可用，但应用继续启动）", exc_info=True)


def _tool_selfcheck() -> None:
    """工具插件自检（启动期一条日志，不阻断启动）。

    坏插件是被**静默跳过**的（`ToolHub.load` 的刻意设计），所以这里必须把结果喊出来：
    少了哪个插件、少了哪个必需工具，一眼可见。也可经 `GET /api/agent/selfcheck` 复查。

    这是"能力齐不齐"的**唯一**留痕点（启动横幅只管版本/端口/路径，不重复报这个——
    见 `logging_setup.banner()` 的文档串）。
    """
    try:
        from ..plugin_api import get_hub, verify_required_tools
        from .routes_agent import REQUIRED_TOOLS

        hub = get_hub()
        missing = verify_required_tools(hub, REQUIRED_TOOLS)
        logger.info(
            "工具中枢：%d 个插件 / %d 个工具%s",
            len(hub.plugins), len(hub.specs()),
            f"；装配失败 {len(hub.failures)} 个" if hub.failures else "",
        )
        # 逐条列出失败原因：光有个数量，现场没法知道"少了什么、为什么"
        for item in hub.failures:
            logger.error("插件装配失败（该插件的能力将静默缺失）：%s → %s",
                         item.get("plugin"), item.get("error"))
        if missing:
            logger.error("工具自检未通过：必需工具缺失 %s（对应插件未装配成功）", missing)
    except Exception:  # noqa: BLE001 —— 自检本身绝不能阻断应用启动
        logger.warning("工具自检执行失败", exc_info=True)


def create_app() -> FastAPI:
    app = FastAPI(title="Workbench", version=__version__, lifespan=lifespan)

    # `/extension/*` 的访问令牌（P0-1，见 api/extension_auth.py）。
    # ⚠ 必须**在 include_router 之前** add_middleware：Starlette 的中间件栈是"后加的在最外层"，
    # 但这不影响路由匹配——中间件在任何路由之前运行，与路由注册顺序无关。
    # 放在此处是为了让它离 create_app 最近，避免有人在下面插一条中间件时把它挤到内层
    # （内层也能工作，但"谁先看到请求"会变得难以推理）。
    from .extension_auth import ExtensionAuthMiddleware

    app.add_middleware(ExtensionAuthMiddleware)

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

    @app.middleware("http")
    async def _access_log(request: Request, call_next):
        """L1 请求日志 + `traceId` 贯通（**最外层**中间件）。

        ## 为什么刻意让它最外层

        Starlette 的中间件栈是"**后加的在最外层**"。这条在 `ExtensionAuthMiddleware`
        之后注册，因此它包在外面——**这是有意的**：令牌校验失败（401）也必须留下日志。
        PITFALLS #49 记的正是"扩展 401 了，但用户只看到快捷登录不灵"——
        那类故障的判据就是"有没有 401 被记下来"。若这条被挤到内层，401 就查不到了。

        这也**不影响** `ExtensionAuthMiddleware` 的"按前缀守备"性质：
        它守的是路由匹配前的请求，与它在栈里的位置无关。

        ## 只记 `/api/*`，且跳过 `/api/logs/*` 自己

        - 静态面（`/` 与 `/static/*`）不计入：它们由渲染进程高频拉取，记了只是噪音；
        - `/api/logs/*` 不计入：否则查一次日志就产生一条日志，**下一次查询里会看到自己**，
          排障时非常迷惑（自指噪声）。
        """
        path = request.url.path
        if not path.startswith("/api/") or path.startswith("/api/logs"):
            return await call_next(request)

        trace = logging_setup.new_trace_id()
        started = time.perf_counter()
        status = 500
        failed = False
        with logging_setup.bind(traceId=trace):
            try:
                response = await call_next(request)
                status = response.status_code
                response.headers["X-Trace-Id"] = trace
                return response
            except Exception:
                failed = True
                # 未捕获异常：先留全栈，再交回上层（不吞）
                logging.getLogger("workbench.http").exception(
                    "请求处理异常：%s %s", request.method, path)
                raise
            finally:
                logging_setup.log_event(
                    "http", f"{request.method} {path}",
                    method=request.method, path=path, status=status,
                    durationMs=int((time.perf_counter() - started) * 1000),
                    failed=failed,
                    traceId=trace,
                    client=request.client.host if request.client else "",
                )

    app.include_router(routes_conversations.router)
    app.include_router(routes_settings.router)
    app.include_router(routes_chat.router)
    _optional_routers(app)
    # 日志检索（放在 `_optional_routers` 之外：它是运维面，不是一个"业务域"，
    # 且**不允许被容错注册静默跳过**——查不到日志比少了某个业务路由更严重）
    from . import routes_logs

    app.include_router(routes_logs.router)

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
