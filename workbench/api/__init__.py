"""FastAPI 应用装配：路由注册 + 生命周期 + 静态托管。

路由按领域拆分：
- routes_conversations  会话与消息（fork mogul）
- routes_settings       引导数据 + 设置（fork mogul，知识库相关已随功能下线裁剪）
- routes_chat           SSE 流式对话 + 回答反馈（fork mogul）
- routes_modules        模块注册表 + 依赖体检（阶段1B）
- routes_insight        平台洞察（egmp 原生）
- routes_accounts       统一账号库
- routes_agent          Agent 工具层

（fork 自 mogul 的知识库/钉钉同步功能已按产品方向裁剪，源码删除；
 DB 迁移 1-6 保留历史不可变性，相关表仅留存不再读写。）
"""

from __future__ import annotations

import importlib
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

#: 可选路由清单：`(日志名, 模块名)`。顺序即注册顺序（与历史一致）。
#: ★ 新增路由加在这里即可；装配失败**不再拖垮整个应用**（见 `_optional_routers`）。
_OPTIONAL_ROUTERS: tuple[tuple[str, str], ...] = (
    ("modules", "routes_modules"),
    ("insight", "routes_insight"),
    ("attachments", "routes_attachments"),
    ("accounts", "routes_accounts"),
    ("agent", "routes_agent"),
    ("mcp", "routes_mcp"),
    ("sandbox", "routes_sandbox"),
    ("skills", "routes_skills"),
    ("checkpoints", "routes_checkpoints"),
    ("schedules", "routes_schedules"),
    ("goals", "routes_goals"),
    ("vault", "routes_vault"),
)


def _optional_routers(app: FastAPI, *, importer=None) -> None:
    """注册业务路由；**单个路由装配失败不拖垮整个应用**。

    ## 2026-10-19 修：注释承诺了容错，实现里没有

    原实现是一整块 `from . import (...)` ＋ 一个 `for` 循环 —— 任何**一个**模块
    import 失败都会让整个应用起不来，与文档串逐字相反。现在逐个 import、逐个注册，
    失败只记一条 WARNING（含堆栈）并继续下一个。

    `importer` 只为可测性存在：测试注入一个"第 3 个模块 import 失败"的假 importer，
    断言其余模块仍然注册成功（不需要真的把某个模块改坏）。

    ★ 这里吞异常与 `plugins.load_all` 同一语义：**装配期少一个域**比**整个应用起不来**
    更难查，所以必须留一条可见的 WARNING；但它不该阻断其余能力。
    """
    if importer is None:
        importer = importlib.import_module
    for name, module_name in _OPTIONAL_ROUTERS:
        try:
            module = importer(f"{__package__}.{module_name}")
            router = getattr(module, "router", None)
            if router is None:
                logger.warning("路由模块 %s 缺少 router，跳过", name)
                continue
            app.include_router(router)
            logger.info("路由已注册：%s", name)
        except Exception as exc:  # noqa: BLE001 —— 单个域装配失败不能拖垮应用
            logger.warning("路由 %s 装配失败（跳过，其余域继续）：%s", name, exc, exc_info=True)


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
    # 定时任务：daemon 线程，30s 轮询；WORKBENCH_DISABLE_SCHEDULER=1 可关。
    from ..scheduler import Scheduler

    _scheduler = Scheduler()
    _scheduler.start()
    _mcp_setup()
    # **启动横幅放在这里而不是入口脚本里**：入口脚本只覆盖两条路
    # （`main.py` 与 `server_entry.py`），而**桌面壳的开发态是直接 spawn
    # `python -m uvicorn workbench.api:app`**（见 Tauri 的 sidecar 启动路径）
    # 开发态回退分支）——那条路**不经过任何入口脚本**，于是"版本/端口/数据目录/日志路径/
    # 插件装配失败"全都不会留痕。lifespan 是**所有启动路径的唯一交集**。
    logging_setup.banner(host=config.HOST, port=config.PORT)
    # 父进程监视：壳一旦非正常死亡（崩溃 / 被任务管理器结束），本 sidecar 随之退出，
    # 否则会成为孤儿并占着固定端口，导致下次启动直接失败（2026-09-20 实测复现，见 lifecycle.py）。
    # 放在 lifespan 而不是入口脚本里，理由与上面的横幅相同：**所有启动路径的唯一交集**。
    logger.info("父进程监视：%s", lifecycle.watch_parent())
    logger.info("应用就绪：lifespan 装配完成，监听 %s:%s", config.HOST, config.PORT)
    yield
    # MCP 连接是子进程：退出时主动收，不留给 OS 去猜（尤其 Windows 上子进程不会
    # 因为父进程退出就自动消失）。失败不阻断退出。
    try:
        _scheduler.stop()
    except Exception:  # noqa: BLE001
        logger.warning("定时任务调度器收尾失败（不影响应用退出）", exc_info=True)
    try:
        from .. import mcp_registry

        mcp_registry.reset_registry()
    except Exception:  # noqa: BLE001
        logger.warning("MCP 收尾失败（不影响应用退出）", exc_info=True)
    logger.info("应用退出：lifespan 收尾")


def _mcp_setup() -> None:
    """MCP 后台装载：**不阻塞启动**。

    一个 MCP server 的 `initialize` 可能要等几秒（npx/uvx 首次拉包更久），
    把它放在 lifespan 的同步路径上会让窗口迟迟打不开。这里只起一个后台线程；
    设置页的「重新连接」按钮是用户能看到的第二条路。
    """
    try:
        from .. import mcp_registry

        mcp_registry.start_background()
    except Exception:  # noqa: BLE001 —— MCP 是增强能力，坏了不影响主流程
        logger.warning("MCP 后台装载启动失败（MCP 不可用，应用继续启动）", exc_info=True)


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
        from ..hub import get_hub
        from ..plugin_api import verify_required_tools
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

    @app.middleware("http")
    async def _no_store_static(request, call_next):
        """静态页与首页**禁止缓存**。

        为什么必须这么做（血泪）：本项目是"改完就重建、重建就要立刻看到"的本机工具，
        客户端就是自己的 Tauri WebView2。`StaticFiles` 默认发 `ETag` + `Last-Modified`，
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

        Starlette 的中间件栈是"**后加的在最外层**"。这条**最后注册**，因此它包在所有中间件外面
        ——**这是有意的**：任何被前置中间件拒掉的请求（401/403）也必须留下日志。
        PITFALLS #49 记的正是"客户端 401 了，但用户只看到功能不灵"——
        那类故障的判据就是"有没有 401 被记下来"。若这条被挤到内层，401 就查不到了。

        > 2026-10-09：当时这里举的例子是 `ExtensionAuthMiddleware`（守 `/extension/*`），
        > 它已随浏览器扩展一起删除。**结论不变**：**注册顺序决定谁包谁**，
        > 将来再加任何会拒绝请求的中间件，都要重新确认这条日志中间件仍在最外层。

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
    # 能力诊断：运维面，不允许被 `_optional_routers` 的容错注册静默跳过。
    from . import routes_diagnostics, routes_memory

    app.include_router(routes_diagnostics.router)
    app.include_router(routes_memory.router)
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

        本端点只回答"你是哪个进程、在哪个端口"，**不写任何状态**，因此可以被反复探测。
        （历史上还有一个 `/extension/health` 回答"浏览器扩展活着吗"并写 `everConnected` 闩锁；
        2026-10-09 扩展整体移除后它已不存在， 的分工只剩本端点半边。）
        **不要把探测打到有副作用的端点上。**

        存在理由：改造前壳的 `waitReady()` 只看"18765 上有人回了 200"，于是
        "旧实例占着端口 + 新壳起不来" 会表现成"代码改动没生效"。
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
