"""配置工厂路由（阶段 3 原生化）：egmp.writers + orchestrate 直接执行，不再调 node。

- create      → orchestrate.run_full_workflow（校验/幂等/拓扑排序/8 步管道/checkpoint）
- orchestrate → 复杂度评估 → DeepSeek 蓝图生成（规范化+校验）→ 审阅三件套（spec/checklist）
- 「环境强制确认」原则由 UI 承担（confirmed=true 才发起；引擎侧 approved/confirmed_env 双闸）；
- 蓝图暂存与产物全部在 workbench 运行时目录；凭证走统一账号库 + egmp client。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import config, db
from ..services.egmp import orchestrate as native_orchestrate
from ..services.egmp.client import egmp_client_for_account
from ..services.egmp.generate import format_review_prompt, generate_blueprint
from ..services.storage import now_ms

router = APIRouter(prefix="/api/factory", tags=["factory"])

BLUEPRINT_MAX_BYTES = 10 * 1024 * 1024


# ---------------------------------------------------------------- 蓝图管理


@router.post("/blueprint")
async def upload_blueprint(file: UploadFile) -> dict:
    """上传 blueprint.json → 暂存运行时目录，返回 blueprint_id。"""
    raw = await file.read()
    if len(raw) > BLUEPRINT_MAX_BYTES:
        raise HTTPException(413, "蓝图文件超过 10MB 上限")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(400, f"不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(400, "蓝图根节点必须是 JSON 对象")
    issues = [
        {"level": i.level, "path": i.path, "message": i.message}
        for i in native_orchestrate_imported_validate(data)
    ]
    blueprint_id = uuid.uuid4().hex[:12]
    workdir = config.RUNTIME_DIR / "factory" / blueprint_id
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "blueprint.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "blueprint_id": blueprint_id,
        "path": str(workdir / "blueprint.json"),
        "name": file.filename or "blueprint.json",
        "top_keys": sorted(data.keys())[:20],
        "validation": issues,
    }


def native_orchestrate_imported_validate(data: dict):
    from ..services.egmp.writers.blueprint import validate_blueprint

    return validate_blueprint(data)


@router.post("/blueprint/validate")
def validate_only(payload: dict) -> dict:
    """蓝图两层校验（不上传暂存）。"""
    issues = native_orchestrate_imported_validate(payload)
    return {"issues": [i.model_dump() for i in issues],
            "valid": not any(i.level == "error" for i in issues)}


@router.get("/blueprints")
def list_blueprints() -> dict:
    root = config.RUNTIME_DIR / "factory"
    items: list[dict] = []
    if root.exists():
        for path in sorted(root.glob("*/blueprint.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                items.append({
                    "blueprint_id": path.parent.name,
                    "name": str(data.get("name") or path.parent.name),
                    "mtime": int(path.stat().st_mtime * 1000),
                })
            except (OSError, ValueError):
                continue
    return {"blueprints": items}


# ---------------------------------------------------------------- 任务执行


class FactoryRunIn(BaseModel):
    blueprint_id: str = Field("", description="已有暂存蓝图 id（create 模式必填）")
    command: str = Field("create", description="create | orchestrate")
    account_id: str = Field(..., min_length=1, description="凭证来源账号（确认后的环境）")
    confirmed: bool = Field(False, description="UI 环境强制确认（必须勾选）")
    requirement: str = Field("", description="orchestrate 模式：需求描述（不填则取蓝图 description）")


@router.post("/run")
def run(body: FactoryRunIn) -> dict:
    """执行工厂任务（原生 Python；日志落盘，任务行入 blueprint_jobs）。"""
    if body.command not in {"create", "orchestrate"}:
        raise HTTPException(400, f"未知命令：{body.command}（monitor 长驻录制见 /api/monitor）")
    if not body.confirmed:
        raise HTTPException(409, "违反 akso-auto「环境强制确认」原则：需要 UI 明确确认（confirmed=true）")

    workdir = config.RUNTIME_DIR / "factory" / (body.blueprint_id or uuid.uuid4().hex[:12])
    workdir.mkdir(parents=True, exist_ok=True)
    blueprint_path = workdir / "blueprint.json"

    job_id = uuid.uuid4().hex[:12]
    db.execute(
        "INSERT INTO blueprint_jobs (id, module, command, blueprint_path, env_id, status, "
        "log_path, artifact_dir, created_at, updated_at) "
        "VALUES (?, 'egmp-native', ?, ?, 'workbench', 'running', ?, ?, ?, ?)",
        (job_id, body.command, str(blueprint_path), str(workdir / "proc.log"), str(workdir),
         now_ms(), now_ms()),
    )
    log_path = workdir / "proc.log"
    lines: list[str] = []

    def on_log(line: str) -> None:
        lines.append(line)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    try:
        with egmp_client_for_account(body.account_id) as client:
            if body.command == "create":
                if not blueprint_path.exists():
                    raise HTTPException(404, f"蓝图不存在：{body.blueprint_id}")
                blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
                result = native_orchestrate.run_full_workflow(
                    client, blueprint, approved=True, confirmed_env=True,
                    artifact_dir=workdir, on_log=on_log)
            else:  # orchestrate
                requirement = body.requirement
                if not requirement and blueprint_path.exists():
                    existing = json.loads(blueprint_path.read_text(encoding="utf-8"))
                    requirement = str(existing.get("description") or existing.get("name") or "")
                if not requirement:
                    raise HTTPException(400, "orchestrate 需要 requirement（需求描述）或带 description 的蓝图")
                on_log("① 复杂度评估 → ② LLM 蓝图生成（DeepSeek）→ ③ 规范化+校验")
                generation = generate_blueprint(requirement, llm_chat_fn=_llm_chat_fn())
                (workdir / "blueprint.generated.json").write_text(
                    json.dumps(generation["blueprint"], ensure_ascii=False, indent=2),
                    encoding="utf-8")
                (workdir / "blueprint.json").write_text(
                    json.dumps(generation["blueprint"], ensure_ascii=False, indent=2),
                    encoding="utf-8")
                (workdir / "spec.md").write_text(
                    native_orchestrate_imported_generate_spec(generation["blueprint"]),
                    encoding="utf-8")
                (workdir / "checklist.md").write_text(
                    native_orchestrate_imported_generate_checklist(generation["blueprint"]),
                    encoding="utf-8")
                on_log(format_review_prompt(generation))
                result = {
                    "success": generation["valid"],
                    "message": "蓝图已生成并通过校验（审阅后用 create 执行）"
                    if generation["valid"] else "蓝图校验未通过（见 issues）",
                    "complexity": generation["complexity"],
                    "issues": generation["issues"],
                    "artifact_dir": str(workdir),
                }
        status = "succeeded" if result.get("success") else "failed"
    except HTTPException:
        db.execute("UPDATE blueprint_jobs SET status = 'failed', updated_at = ? WHERE id = ?",
                   (now_ms(), job_id))
        raise
    except Exception as exc:  # noqa: BLE001
        db.execute("UPDATE blueprint_jobs SET status = 'failed', updated_at = ? WHERE id = ?",
                   (now_ms(), job_id))
        raise HTTPException(502, f"工厂执行失败：{exc}") from exc

    db.execute("UPDATE blueprint_jobs SET status = ?, updated_at = ? WHERE id = ?",
               (status, now_ms(), job_id))
    return {"job_id": job_id, "status": status, "result": result, "log_tail": lines[-50:]}


def _llm_chat_fn():
    from ..services.settings import load_deepseek

    settings = load_deepseek()
    if not settings.api_key:
        raise HTTPException(400, "orchestrate 需要 DeepSeek API Key（设置页配置后重试）")

    def _chat(prompt: str) -> str:
        import asyncio

        from ..services.deepseek import DeepSeekClient

        client = DeepSeekClient(api_key=settings.api_key, model=settings.model)
        return asyncio.run(client.chat(
            [{"role": "user", "content": prompt}], temperature=0.3, json_mode=True))

    return _chat


def native_orchestrate_imported_generate_spec(blueprint: dict) -> str:
    from ..services.egmp.writers.blueprint import generate_review_spec

    return generate_review_spec(blueprint)


def native_orchestrate_imported_generate_checklist(blueprint: dict) -> str:
    from ..services.egmp.writers.blueprint import generate_checklist

    return generate_checklist(blueprint)


@router.get("/jobs")
def list_jobs(limit: int = 30) -> dict:
    rows = db.query("SELECT * FROM blueprint_jobs ORDER BY created_at DESC LIMIT ?", (min(limit, 200),))
    return {"jobs": rows}


@router.get("/jobs/{job_id}")
def job_detail(job_id: str) -> dict:
    row = db.query_one("SELECT * FROM blueprint_jobs WHERE id = ?", (job_id,))
    if not row:
        raise HTTPException(404, f"任务不存在：{job_id}")
    log_path = Path(row["log_path"] or ".")
    lines: list[str] = []
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
    checkpoint: dict = {}
    cp = Path(row["artifact_dir"] or ".") / "checkpoint.json"
    if cp.exists():
        try:
            checkpoint = json.loads(cp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {**row, "log_tail": lines, "checkpoint": checkpoint}


@router.get("/jobs/{job_id}/artifacts")
def job_artifacts(job_id: str) -> dict:
    row = db.query_one("SELECT * FROM blueprint_jobs WHERE id = ?", (job_id,))
    if not row:
        raise HTTPException(404, f"任务不存在：{job_id}")
    artifact_dir = Path(row["artifact_dir"] or ".")
    files = []
    if artifact_dir.exists():
        for path in sorted(artifact_dir.rglob("*")):
            if path.is_file() and path.name not in {"blueprint.json", "proc.log"}:
                files.append({"name": str(path.relative_to(artifact_dir)),
                              "size": path.stat().st_size})
    return {"job_id": job_id, "files": files}


@router.get("/jobs/{job_id}/log")
def job_log_download(job_id: str) -> FileResponse:
    row = db.query_one("SELECT * FROM blueprint_jobs WHERE id = ?", (job_id,))
    if not row:
        raise HTTPException(404, f"任务不存在：{job_id}")
    log_path = Path(row["log_path"] or ".")
    if not log_path.exists():
        raise HTTPException(404, "日志文件不存在")
    return FileResponse(log_path, filename=f"{job_id}.log")
