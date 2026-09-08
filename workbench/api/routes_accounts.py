"""统一账号库路由（阶段 2A）：平台环境 + 账号 CRUD + 卡片墙数据。

安全纪律：任何响应都不回传明文密码；reveal 只在托管浏览器/内联注入内部使用。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import accounts as svc

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


# ------------------------------------------------------------------ 平台环境


class EnvIn(BaseModel):
    name: str = Field(..., min_length=1)
    base_url: str = ""
    note: str = ""


class EnvPatch(BaseModel):
    name: str | None = None
    base_url: str | None = None
    note: str | None = None


@router.get("/envs")
def list_envs() -> dict:
    return {"envs": svc.list_envs()}


@router.post("/envs")
def create_env(body: EnvIn) -> dict:
    try:
        return svc.create_env(**body.model_dump())
    except svc.AccountError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/envs/{env_id}")
def update_env(env_id: str, body: EnvPatch) -> dict:
    env = svc.update_env(env_id, **body.model_dump(exclude_none=True))
    if not env:
        raise HTTPException(404, f"环境不存在：{env_id}")
    return env


@router.delete("/envs/{env_id}")
def delete_env(env_id: str) -> dict:
    if not svc.delete_env(env_id):
        raise HTTPException(404, f"环境不存在：{env_id}")
    return {"deleted": env_id}


# ---------------------------------------------------------------------- 账号


class AccountIn(BaseModel):
    env_id: str
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    role: str = ""
    tags: list[str] = []
    note: str = ""


class AccountPatch(BaseModel):
    username: str | None = None
    password: str | None = None
    role: str | None = None
    tags: list[str] | None = None
    note: str | None = None
    status: str | None = None
    pool: str | list[str] | None = None
    box: str | None = None


class PoolBody(BaseModel):
    pool: str | list[str] = Field(default="", description="池角色：config/monitor 或组合；空=移出")


@router.get("")
def list_accounts(env_id: str | None = None, pool: str | None = None) -> dict:
    if pool:
        return {"accounts": svc.pool_members(pool)}
    return {"accounts": svc.list_accounts(env_id)}


@router.post("/{account_id}/pool")
def set_pool(account_id: str, body: PoolBody) -> dict:
    """加入/移出分配池（pool=[] 即移出）。"""
    try:
        account = svc.update_account(account_id, pool=body.pool)
    except svc.AccountError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not account:
        raise HTTPException(404, f"账号不存在：{account_id}")
    return {"account_id": account_id, "pool": account["pool"]}


# ------------------------------------------------- 盒子管理 + 备份（quick-login 语义）


@router.get("/boxes")
def list_boxes() -> dict:
    return {"boxes": svc.list_boxes()}


class BoxRenameBody(BaseModel):
    from_name: str = Field(..., min_length=1, alias="from")
    to_name: str = Field("", alias="to")
    model_config = {"populate_by_name": True}


@router.post("/boxes/rename")
def rename_box(body: BoxRenameBody) -> dict:
    """盒子重命名 / 移动账号（to 为空 = 并入默认盒子）。"""
    try:
        moved = svc.rename_box(body.from_name, body.to_name)
    except svc.AccountError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"moved": moved, "boxes": svc.list_boxes()}


class BoxCreateBody(BaseModel):
    name: str = Field(..., min_length=1)


@router.post("/boxes/create")
def create_box(body: BoxCreateBody) -> dict:
    """新建一个记忆盒子（可为空盒）。"""
    try:
        svc.create_box(body.name)
    except svc.AccountError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"boxes": svc.list_boxes()}


@router.post("/boxes/delete")
def delete_box(body: BoxRenameBody) -> dict:
    """删除盒子 = 并入默认盒子。"""
    moved = svc.delete_box(body.from_name)
    return {"moved": moved, "boxes": svc.list_boxes()}


@router.post("/boxes/default-name")
def set_default_box_name(body: BoxRenameBody) -> dict:
    """自定义默认盒子的显示名。"""
    svc.set_default_box_name(body.to_name)
    return {"ok": True, "boxes": svc.list_boxes()}


@router.get("/export")
def export_backup() -> dict:
    """导出备份（⚠ 文件含密钥与加密凭据，请自行妥善保管）。"""
    return svc.export_backup()


@router.post("/import-backup")
def import_backup(payload: dict) -> dict:
    try:
        return svc.import_backup(payload)
    except svc.AccountError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{account_id}")
def get_account(account_id: str) -> dict:
    account = svc.get_account(account_id)
    if not account:
        raise HTTPException(404, f"账号不存在：{account_id}")
    return account


@router.post("")
def create_account(body: AccountIn) -> dict:
    try:
        return svc.create_account(**body.model_dump())
    except svc.AccountError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/{account_id}")
def update_account(account_id: str, body: AccountPatch) -> dict:
    try:
        account = svc.update_account(account_id, **body.model_dump(exclude_none=True))
    except svc.AccountError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not account:
        raise HTTPException(404, f"账号不存在：{account_id}")
    return account


@router.delete("/{account_id}")
def delete_account(account_id: str) -> dict:
    if not svc.delete_account(account_id):
        raise HTTPException(404, f"账号不存在：{account_id}")
    return {"deleted": account_id}


# ------------------------------------------------- 原项目 env 一键导入（只读）


@router.get("/import/preview")
def import_preview() -> dict:
    """扫描三个原项目的 env 文件（只读），预览将导入的账号。"""
    return {
        "preview": [
            {"text": f"[{s['module']}] {s['path']}：{'找到' if s['exists'] else '未找到'}"
                     + (f"，{len(s['envs'])} 个环境" if s["exists"] else ""), "cls": "sys"}
            for s in svc.scan_original_env_files()
        ]
        + [{"text": "（点击「开始导入」执行；同 baseUrl+username 去重）", "cls": ""}]
    }


@router.post("/import")
def import_run() -> dict:
    """执行导入（原文件只读不动）。"""
    return {"result": svc.import_from_original_projects(dry_run=False)}
