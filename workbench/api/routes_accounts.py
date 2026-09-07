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


@router.get("")
def list_accounts(env_id: str | None = None) -> dict:
    return {"accounts": svc.list_accounts(env_id)}


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
