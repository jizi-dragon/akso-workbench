"""平台洞察路由（阶段 3 原生化）：egmp.insight 直接实现四命令，不再调 node。

凭证链：统一账号库 → Fernet 解密（仅内存）→ egmp client 登录（55min token 缓存）。
产物统一落 `数据目录/runtime/insight/<job_id>/`，原仓库 output/ 与 node 均不再涉及。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

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
    command: str = Field(..., description="login | inventory | understand | spider")
    account_id: str = Field(..., description="统一账号库账号 id（凭证来源）")
    objects: str = Field("", description="understand/spider：逗号分隔对象编码")
    known_objects: str = Field("", description="inventory 兜底白名单（逗号分隔）")
    llm: bool = Field(False, description="understand 是否附加 LLM 职责摘要")
    env_id: str = Field("", description="token 缓存文件名后缀（可选）")


def _split_codes(raw: str) -> list[str]:
    return [c.strip() for c in (raw or "").split(",") if c.strip()]


def _llm_summary_fn():
    """LLM 职责摘要（mogul DeepSeek 客户端；未配置 Key 时返回 None → 确定性标注）。"""
    from ..services.settings import load_deepseek

    settings = load_deepseek()
    if not settings.api_key:
        return None

    def _summarize(meta: dict, fields: list, lifecycle: dict) -> str:
        import asyncio

        from ..services.deepseek import DeepSeekClient

        prompt = (
            f"用 2-3 句中文概括该 eGMP 业务对象的职责与流转特征。\n"
            f"对象：{meta.get('name')}（{meta.get('code')}）\n"
            f"字段示例：{[f.get('name') for f in fields[:15]]}\n"
            f"状态数：{len(lifecycle.get('states', []))}；"
            f"发起工作流数：{len(lifecycle.get('launchEdges', []))}"
        )
        client = DeepSeekClient(api_key=settings.api_key, model=settings.model)
        return asyncio.run(client.chat([{"role": "user", "content": prompt}], temperature=0.3))

    return _summarize


def _credentials(account_id: str) -> tuple[str, str, str]:
    account = accounts_svc.get_account(account_id)
    if not account:
        raise HTTPException(400, f"账号不存在：{account_id}")
    base_url = account.get("env_base_url") or ""
    if not base_url:
        raise HTTPException(400, f"账号 {account['username']} 所属平台环境未配置 baseUrl")
    return base_url, account["username"], accounts_svc.reveal_password(account_id)


def _run_job(req: InsightRequest, workdir: Path) -> dict:
    """同步执行（原生 Python，无子进程）。"""
    from ..services.egmp.client import login

    base_url, username, password = _credentials(req.account_id)
    token = login(base_url, username, password, req.env_id)
    log_path = workdir / "proc.log"

    def on_log(line: str) -> None:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    started = now_ms()
    with EgmpClient(base_url, token) as client:
        if req.command == "login":
            result = native_insight.run_login(client, base_url, username, password, req.env_id)
            on_log(f"登录成功：{base_url}")
        elif req.command == "inventory":
            result = native_insight.run_inventory(
                client, base_url=base_url, env_id=req.env_id,
                known_objects=_split_codes(req.known_objects),
                output_dir=workdir, on_log=on_log)
        elif req.command == "understand":
            codes = _split_codes(req.objects)
            if not codes:
                raise HTTPException(400, "understand 需要 objects（对象编码，逗号分隔）")
            result = native_insight.run_understand(
                client, codes, output_dir=workdir / "understand",
                llm_summary_fn=_llm_summary_fn() if req.llm else None, on_log=on_log)
        elif req.command == "spider":
            codes = _split_codes(req.objects)
            if not codes:
                raise HTTPException(400, "spider 需要 objects（对象编码，逗号分隔）")
            result = native_insight.run_spider(client, codes, output_dir=workdir, on_log=on_log)
        else:
            raise HTTPException(400, f"未知命令：{req.command}（login/inventory/understand/spider）")
    return {
        "status": "succeeded" if result.get("ok") else "failed",
        "exit_code": 0 if result.get("ok") else 1,
        "duration_ms": now_ms() - started,
        "result": {k: v for k, v in result.items() if k != "ok"},
        "workdir": str(workdir),
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


def _finish_job(job_id: str, status: str) -> None:
    db.execute("UPDATE insight_runs SET status = ?, updated_at = ? WHERE id = ?",
               (status, now_ms(), job_id))


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
    _finish_job(job_id, outcome["status"])
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
        _finish_job(job_id, outcome.get("status", "failed"))
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
    artifact_dir = Path(row["artifact_dir"] or ".")
    files: list[dict] = []
    if artifact_dir.exists():
        for path in sorted(artifact_dir.rglob("*")):
            if path.is_file():
                files.append({"name": str(path.relative_to(artifact_dir)),
                              "size": path.stat().st_size, "suffix": path.suffix.lower()})
    return {"job_id": job_id, "status": row["status"], "files": files}


@router.get("/artifacts/{job_id}/file")
def read_artifact(job_id: str, name: str, download: bool = False):
    row = db.query_one("SELECT * FROM insight_runs WHERE id = ?", (job_id,))
    if not row:
        raise HTTPException(404, f"任务不存在：{job_id}")
    artifact_dir = Path(row["artifact_dir"] or ".").resolve()
    target = (artifact_dir / name).resolve()
    if not str(target).startswith(str(artifact_dir)) or not target.exists() or not target.is_file():
        raise HTTPException(404, f"产物不存在：{name}")
    if download or target.suffix.lower() not in {".md", ".json", ".txt", ".log", ".csv"}:
        return FileResponse(target, filename=target.name)
    text = target.read_text(encoding="utf-8", errors="replace")
    if len(text) > 2_000_000:
        text = text[:2_000_000] + "\n…（超长截断，请下载查看）"
    return PlainTextResponse(text)
