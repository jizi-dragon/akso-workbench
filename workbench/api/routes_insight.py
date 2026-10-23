"""平台洞察路由 —— **只剩 HTTP 形状**（路由、状态码、响应类型）。

业务逻辑（跑任务、列产物、读产物、**路径守卫**）已下沉到
`workbench/services/egmp/insight/jobs.py`。这么切的理由写在那个模块的文档串里，
一句话版本：**页面链路与工具链路必须调同一份实现**，否则守卫可能只对一边生效 ——
而 `plugins/insight.py` 原先只能 `from ..api import routes_insight` 去调 HTTP handler，
那是本仓明令禁止的依赖方向（AGENTS.md 规则 7）。

凭证链：统一账号库 → Fernet 解密（仅内存）→ egmp client 登录（55min token 缓存）。

产物落点（规格 §4）：spider 写进**用户可配的工作区根** `<root>/<对象code>/<时间戳>/`
（`insight_runs.workspace_dir` 回填该目录，产物面板据此列出日志与产物）；工作区不可写时
退回兜底目录 `数据目录/runtime/insight/<job_id>/`。原仓库 output/ 与 node 均不再涉及。
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from .. import db
from ..services.egmp.insight import jobs

logger = logging.getLogger("workbench.routes_insight")

router = APIRouter(prefix="/api/insight", tags=["insight"])

#: SSE 空闲时的心跳间隔。抽成常量是为了**可测**：断开连接的终态判据
#: 不必真的等半秒（`tests/test_insight_sse.py` 会把它调小）。
KEEPALIVE_S = 0.5

#: 请求体模型定义在领域层（`jobs.InsightRequest`）——它同时是"工具的入参"，
#: 不是 HTTP 专有形状。这里再导出一次，保持既有 `routes_insight.InsightRequest` 可用。
InsightRequest = jobs.InsightRequest


class WorkspaceIn(BaseModel):
    path: str = Field("", description="工作区根目录绝对路径；空串 = 恢复默认")


def _as_http(exc: jobs.InsightError) -> HTTPException:
    """领域错误 → HTTP 状态码（这一层唯一该做的事）。"""
    return HTTPException(exc.status, str(exc))


def _outcome_of(fut, workdir) -> dict:  # noqa: ANN001 —— asyncio.Future
    """把 worker 的 Future 收成 outcome；异常转成 failed（不把后台崩溃带进 loop）。

    ★ 模块级而不是嵌在 `gen()` 里：嵌套同步函数被 async 路径直接调用会命中
    I11 的规则 S（那条规则没有豁免名单，是有意的）。
    """
    try:
        return fut.result()
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": str(exc), "workdir": str(workdir)}


@router.post("/run")
def run(req: InsightRequest) -> dict:
    """同步执行一次洞察命令（原生 Python 实现）。"""
    try:
        return jobs.run_sync(req)
    except jobs.InsightError as exc:
        # 可预期的拒绝（参数/账号问题）→ 用领域层建议的状态码，**不要**被下面兜成 502
        raise _as_http(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"对象分析执行失败：{exc}") from exc


@router.post("/run-sse")
async def run_sse(req: InsightRequest):
    """SSE 流式执行：逐行进度回传（原生实现，日志经队列推送）。

    ## ★ 终态必须在**所有**出口落库（2026-10-19 修）

    原实现的 `jobs.finish_job(...)` 只写在生成器**末尾**。SSE 客户端中断
    （刷新 / 关页 / 网络断）会让生成器在 `yield` 处收到 `GeneratorExit`，
    末尾那几行**永远不执行** ⇒ `insight_runs` 里那一行永远是 `running`。
    对照 `jobs.run_sync()`：它是有 `try/except` 的，两条链路语义必须一致。

    现在用 `finally` 收口，并分两种情形：

    - 生成器关闭时 worker **已结束** ⇒ 立即落真实终态；
    - worker **还在跑** ⇒ 注册 `add_done_callback`，等它结束后落真实终态
      （**不取消** worker：一次织网可能已经写了半天产物，取消只会留下半截状态；
      也**不**先写一个假的 failed —— 那会让"还在跑"与"失败了"混在一起）。

    ★ 终态写库走 `asyncio.to_thread`（I11：`jobs.finish_job` 是同步 DB 写入）。

    ★ `start_job` 放在生成器**内部**：如果客户端在第一次迭代前就断开，
    Starlette 可能根本不启动 `gen()` —— 那样连 `insight_runs` 行都不该建，
    更不该留下一个永远 `running` 的幽灵任务。
    """
    async def gen():
        # I11：`start_job` 是同步 DB 写入（建 run 行 + 准备目录）。
        # 它必须在生成器内部（见上），但不能再直接跑在事件循环上。
        job_id, workdir = await asyncio.to_thread(jobs.start_job, req)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def on_log(line: str) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, line)

        def worker() -> dict:
            try:
                return jobs.run_job(req, workdir)
            except Exception as exc:  # noqa: BLE001
                return {"status": "failed", "exit_code": 1, "error": str(exc), "workdir": str(workdir)}

        task = loop.run_in_executor(None, worker)
        # worker 一结束就把等待中的 `queue.get()` 叫醒：否则循环要等满一个
        # KEEPALIVE_S 才看见 `task.done()`，`done` 事件就白晚半秒。
        task.add_done_callback(lambda _f: loop.call_soon_threadsafe(queue.put_nowait, None))
        finalized = False

        def _write_terminal(outcome: dict) -> None:
            """**唯一的终态写入点**（调用方负责幂等与线程）。"""
            try:
                jobs.finish_job(
                    job_id, outcome.get("status", "failed"),
                    workspaces=outcome.get("workspaces"),
                    coverage_status=outcome.get("coverage_status", ""),
                )
            except Exception:  # noqa: BLE001 —— 终态写入失败不能盖掉已经发出去的事件
                logger.warning("对象分析终态落库失败：job=%s", job_id, exc_info=True)

        def _on_done(fut) -> None:  # noqa: ANN001 —— asyncio.Future
            """客户端已断开、worker 后来才结束：在 loop 里补写终态。"""
            nonlocal finalized
            if finalized:
                return
            finalized = True
            loop.run_in_executor(None, _write_terminal, _outcome_of(fut, workdir))

        try:
            while not task.done() or not queue.empty():
                try:
                    line = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_S)
                    if line is None:  # worker 结束的唤醒信号（不是一条日志）
                        break
                    yield f"data: {json.dumps({'type': 'log', 'line': line}, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
            outcome = task.result()
            if not finalized:
                finalized = True
                await asyncio.to_thread(_write_terminal, outcome)
            yield f"data: {json.dumps({'type': 'done', 'job_id': job_id, **outcome}, ensure_ascii=False, default=str)}\n\n"
        finally:
            # 客户端断开时这里就是**唯一**能落终态的地方。
            if task.done():
                if not finalized:
                    finalized = True
                    await asyncio.to_thread(_write_terminal, _outcome_of(task, workdir))
            else:
                logger.info("对象分析 SSE 客户端断开：job=%s，worker 结束后落终态", job_id)
                task.add_done_callback(_on_done)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/runs")
def list_runs(limit: int = 10, offset: int = 0) -> dict:
    """任务列表（分页）。

    页大小默认 10（用户定稿：一页 10 条 + 翻页），上限 200；
    ``total`` 一并回传，前端才能算出总页数——不用另开一条 count 接口。
    """
    size = max(1, min(limit, 200))
    start = max(0, offset)
    total = db.query_one("SELECT COUNT(*) AS n FROM insight_runs") or {}
    rows = db.query(
        "SELECT * FROM insight_runs ORDER BY created_at DESC LIMIT ? OFFSET ?", (size, start)
    )
    return {"runs": rows, "total": int(total.get("n") or 0), "limit": size, "offset": start}


@router.get("/artifacts/{job_id}")
def list_artifacts(job_id: str) -> dict:
    try:
        return jobs.job_artifacts(job_id)
    except jobs.InsightError as exc:
        raise _as_http(exc) from exc


@router.get("/artifacts/{job_id}/file")
def read_artifact(job_id: str, name: str, download: bool = False):
    try:
        target = jobs.resolve_artifact(job_id, name)
    except jobs.InsightError as exc:
        raise _as_http(exc) from exc
    suffix = target.suffix.lower()
    if download:
        return FileResponse(target, filename=target.name)
    if suffix in jobs.INLINE_IMAGE_SUFFIXES:
        # 图片是二进制：必须走 FileResponse（按文本读会把 PNG 读坏），
        # 但 `Content-Disposition` 要 inline，否则 `<img>` 在某些壳里会触发下载。
        return FileResponse(
            target,
            media_type=jobs.INLINE_IMAGE_SUFFIXES[suffix],
            headers={"Content-Disposition": "inline"},
        )
    if suffix not in jobs.INLINE_SUFFIXES:
        return FileResponse(target, filename=target.name)
    # 给准 media type：前端按后缀决定怎么渲染，浏览器直接打开这条 URL 时也别把它当二进制
    return PlainTextResponse(jobs.read_artifact_text(target),
                             media_type=jobs.INLINE_SUFFIXES[suffix])


@router.get("/workspace")
def get_workspace() -> dict:
    """当前产物工作区根（用户可配）。"""
    from ..services.egmp.insight import workspace as workspace_mod

    root = workspace_mod.workspace_root()
    default = workspace_mod.default_workspace_root()
    return {"root": str(root), "default": str(default),
            "custom": root != default, "exists": root.is_dir()}


@router.put("/workspace")
def set_workspace(body: WorkspaceIn) -> dict:
    """设置产物工作区根；传空串恢复默认。"""
    from ..services.egmp.insight import workspace as workspace_mod

    if not body.path.strip():
        workspace_mod.clear_workspace_root()
    else:
        try:
            workspace_mod.set_workspace_root(body.path)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return get_workspace()
