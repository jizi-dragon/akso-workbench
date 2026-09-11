"""模块注册与健康体检路由（阶段 1B，契约见 docs/API.md §5）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..services import modules as modules_svc

router = APIRouter(prefix="/api/modules", tags=["modules"])


@router.get("")
def list_all() -> dict:
    """全部模块清单 + 依赖体检（Node/入口/repoPath/Python 依赖/Playwright）。"""
    items = modules_svc.list_modules()
    return {
        "modules": items,
        "summary": {
            "total": len(items),
            "ok": sum(1 for m in items if m["status"] == "ok"),
            "degraded": sum(1 for m in items if m["status"] == "degraded"),
            "missing": sum(1 for m in items if m["status"] == "missing"),
        },
    }


@router.get("/{module_id}")
def get_one(module_id: str) -> dict:
    adapter = modules_svc.get_adapter(module_id)
    if module_id not in {"workbench", "accounts"} and adapter is None:
        raise HTTPException(404, f"未知模块：{module_id}")
    return modules_svc.module_status(module_id, adapter)


@router.post("/{module_id}/check")
def recheck(module_id: str) -> dict:
    """即时复检单个模块（体检项全部重跑）。"""
    adapter = modules_svc.get_adapter(module_id)
    if module_id not in {"workbench", "accounts"} and adapter is None:
        raise HTTPException(404, f"未知模块：{module_id}")
    return modules_svc.module_status(module_id, adapter)
