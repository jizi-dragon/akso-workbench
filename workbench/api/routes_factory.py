"""配置工厂路由（阶段 3 原生化）：egmp.writers + orchestrate 直接执行，不再调 node。

- **create** → `orchestrate.run_full_workflow`（校验/幂等/拓扑排序/8 步管道/checkpoint）
- 「环境强制确认」原则由 UI 承担（confirmed=true 才发起；引擎侧 approved/confirmed_env 双闸）；
- 蓝图暂存与产物全部在 workbench 运行时目录；凭证走统一账号库 + egmp client。

## 2026-09-20：`orchestrate` 命令已删除（见 docs/FACTORY-AGENT-GAP.md 问题④）

原来这里还有一条 `command="orchestrate"`：**需求描述 → 复杂度评估 → 另一个 LLM 调用生成蓝图
→ 校验 → 审阅三件套**。它和对话 Agent 干的是同一件事，但只有一个 `requirement` 字符串的输入
——拿不到对话上下文、拿不到用户前面说的约束、也拿不到工具查到的真实对象结构。
**是对话链路的降级复制品**，而且本仓对"两套会漂的事实源"已经栽过两次（两张敏感路径表、
两套工具表），所以直接删掉而不是标废弃。

要"把一句话变成蓝图"，现在走对话：模型先用 `factory_blueprint_spec` 看规格，
再用 `factory_preview_blueprint` 提交。**手工上传 blueprint.json 的那条路保留**
（`POST /blueprint`）——用户手里已经有文件时不该被迫让模型把内容抄一遍。
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
    blueprint_id: str = Field("", description="已暂存蓝图 id（必填）")
    command: str = Field("create", description="create（本路由只有这一个命令）")
    account_id: str = Field(..., min_length=1, description="凭证来源账号（确认后的环境）")
    confirmed: bool = Field(False, description="UI 环境强制确认（必须勾选）")


@router.post("/run")
def run(body: FactoryRunIn) -> dict:
    """执行工厂任务（原生 Python；日志落盘，任务行入 blueprint_jobs）。

    ⚠️ `command` 现在**只有 `create`**。旧的 `orchestrate`（需求→LLM 生成蓝图）已删除，
    见模块文档串。保留这个字段而不是删掉：它是**已存的 `blueprint_jobs.command` 列**的
    写入源，删了会让历史行与现在写入的口径对不上（而"任务表里的 command 是什么"
    是排障时要看的）。
    """
    if body.command != "create":
        raise HTTPException(
            400,
            f"未知命令：{body.command}。本路由只接受 create；"
            "「一句话生成蓝图」请改用对话（模型会调 factory_blueprint_spec + "
            "factory_preview_blueprint）",
        )
    if not body.confirmed:
        raise HTTPException(409, "违反配置「环境强制确认」原则：需要 UI 明确确认（confirmed=true）")

    workdir = config.RUNTIME_DIR / "factory" / (body.blueprint_id or uuid.uuid4().hex[:12])
    workdir.mkdir(parents=True, exist_ok=True)
    blueprint_path = workdir / "blueprint.json"

    job_id = uuid.uuid4().hex[:12]
    db.execute(
        "INSERT INTO blueprint_jobs (id, module, command, blueprint_path, env_id, status, "
        "log_path, artifact_dir, created_at, updated_at) "
        "VALUES (?, 'factory', ?, ?, 'workbench', 'running', ?, ?, ?, ?)",
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
            if not blueprint_path.exists():
                raise HTTPException(404, f"蓝图不存在：{body.blueprint_id}")
            blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
            result = native_orchestrate.run_full_workflow(
                client, blueprint, approved=True, confirmed_env=True,
                artifact_dir=workdir, on_log=on_log)
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
