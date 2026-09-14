"""平台洞察路由（阶段 3 原生化）：egmp.insight 直接实现 login / spider，不再调 node。

凭证链：统一账号库 → Fernet 解密（仅内存）→ egmp client 登录（55min token 缓存）。

产物落点（规格 §4）：spider 写进**用户可配的工作区根** `<root>/<对象code>/<时间戳>/`
（`insight_runs.workspace_dir` 回填该目录，产物面板据此列出日志与产物）；工作区不可写时
退回兜底目录 `数据目录/runtime/insight/<job_id>/`。原仓库 output/ 与 node 均不再涉及。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from .. import config, db
from ..services import accounts as accounts_svc
from ..services.egmp import insight as native_insight
from ..services.egmp.client import EgmpClient
from ..services.storage import now_ms

router = APIRouter(prefix="/api/insight", tags=["insight"])


class InsightRequest(BaseModel):
    command: str = Field(..., description="login | spider")
    account_id: str = Field(..., description="统一账号库账号 id（凭证来源）")
    objects: str = Field("", description="spider：逗号分隔对象编码")
    env_id: str = Field("", description="token 缓存文件名后缀（可选）")


def _split_codes(raw: str) -> list[str]:
    return [c.strip() for c in (raw or "").split(",") if c.strip()]


def _credentials(account_id: str) -> tuple[dict, str, str, str]:
    account = accounts_svc.get_account(account_id)
    if not account:
        raise HTTPException(400, f"账号不存在：{account_id}")
    base_url = account.get("env_base_url") or ""
    if not base_url:
        raise HTTPException(400, f"账号 {account['username']} 所属平台环境未配置 baseUrl")
    return account, base_url, account["username"], accounts_svc.reveal_password(account_id)


def _environment_of(account: dict, base_url: str) -> dict:
    """写进 manifest 的环境信息（不含任何凭据）。"""
    return {
        "envId": account.get("env_id"),
        "envName": account.get("env_name"),
        "baseUrl": base_url,
    }


def _status_from_coverage(result: dict) -> str:
    """覆盖度三态 → 任务状态：complete→succeeded、partial→partial、degraded→failed。

    取代原先「取 result['ok'] 布尔」的写法——那样部分成功也会被记成 succeeded。
    """
    coverage = (result or {}).get("coverage") or {}
    status = coverage.get("status")
    if status in {"complete", "partial", "degraded"}:
        return {"complete": "succeeded", "partial": "partial", "degraded": "failed"}[status]
    return "succeeded" if result.get("ok") else "failed"


def _run_job(req: InsightRequest, workdir: Path) -> dict:
    """同步执行（原生 Python，无子进程）。"""
    from ..services.egmp.client import login

    account, base_url, username, password = _credentials(req.account_id)
    environment = _environment_of(account, base_url)
    token = login(base_url, username, password, req.env_id)
    log_path = workdir / "proc.log"

    def on_log(line: str) -> None:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def run_spider_into(codes: list[str]) -> dict:
        """spider 落点：用户可配工作区根优先，不可用则退回任务目录。

        先定落点再爬——不做事后重试，否则平台调用会被重放一遍。
        """
        from ..services.egmp.insight import workspace as workspace_mod

        try:
            target = workspace_mod.run_dir(",".join(codes))
            on_log(f"产物落点：{target}")
        except OSError as exc:
            on_log(f"工作区不可写（{exc}），产物改落任务目录")
            target = workdir
        outcome = native_insight.run_spider(client, codes, output_dir=target,
                                            on_log=on_log, environment=environment)
        # 任务日志留在任务目录（同一 job 的既有位置不变），另在工作区留一份副本，
        # 好让产物面板能在同一处列出「日志 + 产物」。
        if target != workdir and log_path.is_file():
            try:
                (target / "proc.log").write_bytes(log_path.read_bytes())
            except OSError:
                pass
        return outcome

    started = now_ms()
    with EgmpClient(base_url, token) as client:
        if req.command == "login":
            result = native_insight.run_login(client, base_url, username, password, req.env_id)
            on_log(f"登录成功：{base_url}")
        elif req.command == "spider":
            codes = _split_codes(req.objects)
            if not codes:
                raise HTTPException(400, "spider 需要 objects（对象编码，逗号分隔）")
            result = run_spider_into(codes)
        else:
            raise HTTPException(400, f"未知命令：{req.command}（login/spider）")

    status = _status_from_coverage(result)
    coverage = result.get("coverage") or {}
    return {
        "status": status,
        "exit_code": 0 if status == "succeeded" else 1,
        "duration_ms": now_ms() - started,
        "result": {k: v for k, v in result.items() if k != "ok"},
        "workdir": str(workdir),
        "workspaces": result.get("workspaces") or [],
        "coverage_status": coverage.get("status", ""),
    }


def _start_job(req: InsightRequest) -> tuple[str, Path]:
    job_id = uuid.uuid4().hex[:12]
    workdir = config.RUNTIME_DIR / "insight" / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    db.execute(
        "INSERT INTO insight_runs (id, module, command, objects, env_id, status, log_path, "
        "artifact_dir, created_at, updated_at) VALUES (?, 'egmp-native', ?, ?, ?, 'running', ?, ?, ?, ?)",
        (job_id, req.command, req.objects, str(req.env_id or ""), str(workdir / "proc.log"),
         str(workdir), now_ms(), now_ms()),
    )
    return job_id, workdir


def _finish_job(job_id: str, status: str, *, workspaces: list[str] | None = None,
                coverage_status: str = "") -> None:
    """落终态。工作区目录可能多个（每对象一个），用换行分隔存一列。"""
    db.execute(
        "UPDATE insight_runs SET status = ?, updated_at = ?, workspace_dir = ?, "
        "coverage_status = ? WHERE id = ?",
        (status, now_ms(), "\n".join(workspaces or []), coverage_status or "", job_id),
    )


def _workspace_dirs(row: Any) -> list[Path]:
    """从行里取出工作区目录列表（兼容未跑过迁移 14 语义的历史行）。"""
    try:
        raw = row["workspace_dir"] or ""
    except (KeyError, IndexError, TypeError):
        return []
    return [Path(p) for p in str(raw).split("\n") if p.strip()]


@router.post("/run")
def run(req: InsightRequest) -> dict:
    """同步执行一次洞察命令（原生 Python 实现）。"""
    job_id, workdir = _start_job(req)
    try:
        outcome = _run_job(req, workdir)
    except HTTPException:
        _finish_job(job_id, "failed")
        raise
    except Exception as exc:  # noqa: BLE001
        _finish_job(job_id, "failed")
        raise HTTPException(502, f"洞察执行失败：{exc}") from exc
    _finish_job(job_id, outcome["status"], workspaces=outcome.get("workspaces"),
                coverage_status=outcome.get("coverage_status", ""))
    return {"job_id": job_id, **outcome}


@router.post("/run-sse")
async def run_sse(req: InsightRequest):
    """SSE 流式执行：逐行进度回传（原生实现，日志经队列推送）。"""
    job_id, workdir = _start_job(req)

    async def gen():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str] = asyncio.Queue()

        def on_log(line: str) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, line)

        def worker() -> dict:
            try:
                return _run_job(req, workdir)
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
        _finish_job(job_id, outcome.get("status", "failed"),
                    workspaces=outcome.get("workspaces"),
                    coverage_status=outcome.get("coverage_status", ""))
        yield f"data: {json.dumps({'type': 'done', 'job_id': job_id, **outcome}, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/runs")
def list_runs(limit: int = 30) -> dict:
    rows = db.query("SELECT * FROM insight_runs ORDER BY created_at DESC LIMIT ?", (min(limit, 200),))
    return {"runs": rows}


@router.get("/artifacts/{job_id}")
def list_artifacts(job_id: str) -> dict:
    row = db.query_one("SELECT * FROM insight_runs WHERE id = ?", (job_id,))
    if not row:
        raise HTTPException(404, f"任务不存在：{job_id}")
    # 产物目录：优先取工作区列（历史任务可能有，understand 时代写入），
    # 否则回落到 runtime 的 artifact_dir（现存命令 login/spider 都走这条）
    dirs = _workspace_dirs(row)
    if not dirs and row["artifact_dir"]:
        dirs = [Path(row["artifact_dir"])]
    files: list[dict] = []
    for base in dirs:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file():
                files.append({"name": str(path.relative_to(base)),
                              "size": path.stat().st_size, "suffix": path.suffix.lower(),
                              "workspace": base.name})
    return {"job_id": job_id, "status": row["status"], "files": files,
            "coverageStatus": (row["coverage_status"] if "coverage_status" in row.keys() else ""),
            "workspaces": [str(d) for d in dirs]}


@router.get("/artifacts/{job_id}/file")
def read_artifact(job_id: str, name: str, download: bool = False):
    row = db.query_one("SELECT * FROM insight_runs WHERE id = ?", (job_id,))
    if not row:
        raise HTTPException(404, f"任务不存在：{job_id}")
    dirs = _workspace_dirs(row)
    if not dirs and row["artifact_dir"]:
        dirs = [Path(row["artifact_dir"])]
    if not dirs:
        # 空目录会让 "." 解析成 CWD，守位退化为「当前目录下任意文件」——直接拒绝
        raise HTTPException(404, f"任务无可读产物目录：{job_id}")

    target: Path | None = None
    for base in dirs:
        root = base.resolve()
        candidate = (root / name).resolve()
        # 必须用「路径包含」而非字符串前缀比较：`.../insight/abc` 在字符串上「包含」`.../insight/abc_evil`
        if candidate.is_relative_to(root) and candidate.is_file():
            target = candidate
            break
    if target is None:
        raise HTTPException(404, f"产物不存在：{name}")
    if download or target.suffix.lower() not in {".md", ".json", ".txt", ".log", ".csv"}:
        return FileResponse(target, filename=target.name)
    text = target.read_text(encoding="utf-8", errors="replace")
    if len(text) > 2_000_000:
        text = text[:2_000_000] + "\n…（超长截断，请下载查看）"
    return PlainTextResponse(text)


class WorkspaceIn(BaseModel):
    path: str = Field("", description="工作区根目录绝对路径；空串 = 恢复默认")


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
