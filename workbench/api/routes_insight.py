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

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from .. import db
from ..services.egmp.insight import jobs

router = APIRouter(prefix="/api/insight", tags=["insight"])

#: 请求体模型定义在领域层（`jobs.InsightRequest`）——它同时是"工具的入参"，
#: 不是 HTTP 专有形状。这里再导出一次，保持既有 `routes_insight.InsightRequest` 可用。
InsightRequest = jobs.InsightRequest


class WorkspaceIn(BaseModel):
    path: str = Field("", description="工作区根目录绝对路径；空串 = 恢复默认")


def _as_http(exc: jobs.InsightError) -> HTTPException:
    """领域错误 → HTTP 状态码（这一层唯一该做的事）。"""
    return HTTPException(exc.status, str(exc))


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
    """SSE 流式执行：逐行进度回传（原生实现，日志经队列推送）。"""
    job_id, workdir = jobs.start_job(req)

    async def gen():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str] = asyncio.Queue()

        def on_log(line: str) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, line)

        def worker() -> dict:
            try:
                return jobs.run_job(req, workdir)
            except Exception as exc:  # noqa: BLE001
                return {"status": "failed", "exit_code": 1, "error": str(exc), "workdir": str(workdir)}

        task = loop.run_in_executor(None, worker)
        while not task.done() or not queue.empty():
            try:
                line = await asyncio.wait_for(queue.get(), timeout=0.5)
                yield f"data: {json.dumps({'type': 'log', 'line': line}, ensure_ascii=False)}\n\n"
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
        outcome = task.result()
        jobs.finish_job(job_id, outcome.get("status", "failed"),
                        workspaces=outcome.get("workspaces"),
                        coverage_status=outcome.get("coverage_status", ""))
        yield f"data: {json.dumps({'type': 'done', 'job_id': job_id, **outcome}, ensure_ascii=False, default=str)}\n\n"

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
    if download or target.suffix.lower() not in jobs.INLINE_SUFFIXES:
        return FileResponse(target, filename=target.name)
    # 给准 media type：前端按后缀决定怎么渲染，浏览器直接打开这条 URL 时也别把它当二进制
    return PlainTextResponse(jobs.read_artifact_text(target),
                             media_type=jobs.INLINE_SUFFIXES[target.suffix.lower()])


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
