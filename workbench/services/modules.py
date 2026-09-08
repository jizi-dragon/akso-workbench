"""模块注册表：读取 adapters/*.json，提供模块清单与依赖体检。

四个模块：
- workbench   自身（FastAPI/Python 依赖体检）
- akso-cc     平台洞察（只读引用，Node 子进程封装）
- akso-auto   配置自动化（只读引用，Node 子进程封装）
- accounts    统一账号库 + 托管浏览器（能力已内化，检查 Playwright）

原仓库只读：本模块绝不写 adapters 指向的目录。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("workbench.modules")

# 内置模块（不依赖 adapters 文件）
BUILTIN_MODULES: list[dict[str, Any]] = [
    {
        "id": "workbench",
        "title": "AI 工作台（fork mogul）",
        "runtime": "python",
        "mode": "native",
        "repoPath": None,
        "entry": None,
        "commands": {
            "chat": {"description": "AI 对话（SSE 流式）"},
        },
    },
    {
        "id": "accounts",
        "title": "账号中心（凭据托管 + 托管会话 + 分配池，迁自 quick-login）",
        "runtime": "python",
        "mode": "native",
        "repoPath": None,
        "entry": None,
        "commands": {
            "accounts": {"description": "账号/环境 CRUD + 卡片墙 + 分配池"},
            "browser": {"description": "一键启动托管会话（轮盘快速入口）"},
        },
    },
]


def adapters_dir() -> Path:
    from .. import config

    return config.ADAPTERS_DIR


def load_adapters() -> list[dict[str, Any]]:
    """读取 adapters/*.json（容错：单个坏文件不影响其余）。"""
    result: list[dict[str, Any]] = []
    directory = adapters_dir()
    if not directory.exists():
        logger.warning("adapters 目录不存在：%s", directory)
        return result
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("id"):
                data["_file"] = path.name
                result.append(data)
        except (OSError, ValueError) as exc:
            logger.warning("适配器文件损坏 %s：%s", path.name, exc)
    return result


def resolve_repo_path(adapter: dict[str, Any]) -> Path | None:
    """repoPath 解析：环境变量 > JSON 值。"""
    env_name = adapter.get("repoPathEnv")
    raw = (os.environ.get(env_name) if env_name else None) or adapter.get("repoPath")
    return Path(raw) if raw else None


def _check_python_deps() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for modname, need in (
        ("fastapi", True), ("uvicorn", True), ("httpx", True),
        ("pydantic", True), ("cryptography", True), ("playwright", False),
    ):
        try:
            __import__(modname)
            checks.append({"name": f"python:{modname}", "ok": True, "detail": "ok"})
        except ImportError:
            checks.append({
                "name": f"python:{modname}",
                "ok": not need,
                "detail": "缺失" if need else "未安装（托管浏览器功能不可用）",
            })
    return checks


def _check_adapter(adapter: dict[str, Any]) -> list[dict[str, Any]]:
    """阶段 3 原生化：功能由 egmp Python 包承接，node/原仓库降级为只读参考。

    体检项：Python 依赖（门禁）+ 原仓库存在性（信息项，缺失不影响功能）。
    """
    checks = _check_python_deps()
    repo = resolve_repo_path(adapter)
    checks.append({
        "name": "source-reference",
        "ok": True,
        "detail": (str(repo) if repo and repo.exists()
                   else f"参考存档不可达：{repo}（功能已原生化，不影响使用）"),
    })
    return checks


def _check_playwright_browser() -> dict[str, Any]:
    """检测 Playwright chromium 是否可执行。"""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            path = p.chromium.executable_path
            ok = Path(path).exists()
        return {
            "name": "playwright:chromium",
            "ok": ok,
            "detail": str(path) if ok else "chromium 未安装（.venv/Scripts/playwright install chromium）",
        }
    except Exception as exc:  # noqa: BLE001
        return {"name": "playwright:chromium", "ok": False, "detail": f"检测失败：{exc}"}


def module_status(mod_id: str, adapter: dict[str, Any] | None = None) -> dict[str, Any]:
    """单个模块的完整状态（清单 + 体检）。"""
    if mod_id == "workbench":
        checks = _check_python_deps()
        mod = BUILTIN_MODULES[0]
    elif mod_id == "accounts":
        checks = _check_python_deps() + [_check_playwright_browser()]
        mod = BUILTIN_MODULES[1]
    else:
        mod = adapter or {}
        checks = _check_adapter(mod) if mod else []

    ok_count = sum(1 for c in checks if c["ok"])
    status = "ok" if ok_count == len(checks) and checks else "degraded" if ok_count else "missing"
    repo = resolve_repo_path(mod) if mod else None
    return {
        "id": mod_id,
        "title": mod.get("title", mod_id),
        "runtime": mod.get("runtime", ""),
        "mode": mod.get("mode", ""),
        "repoPath": str(repo) if repo else None,
        "commands": mod.get("commands", {}),
        "status": status,
        "checks": checks,
    }


def list_modules() -> list[dict[str, Any]]:
    """全部模块状态（内置 + adapters）。"""
    result = [module_status(m["id"]) for m in BUILTIN_MODULES]
    for adapter in load_adapters():
        result.append(module_status(adapter["id"], adapter))
    return result


def get_adapter(mod_id: str) -> dict[str, Any] | None:
    for adapter in load_adapters():
        if adapter["id"] == mod_id:
            return adapter
    return None
