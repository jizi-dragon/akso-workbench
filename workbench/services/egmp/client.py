"""eGMP HTTP 客户端内核（akso-cc client.ts / akso-auto api/client.js 的 httpx 化）。

实证资产（重写时不得丢失，来源 akso-cc 调研 §5）：
- 统一响应信封 {code, message?, data?}，code===0 才算成功；
- 登录 POST /api/openapi/v1.0/Auth，body {account, password}，data 即 JWT；
- token 缓存 55 分钟（文件 output/token-{envId}.json 语义 → 本地 workbench 运行时目录）；
- 分页 POST：pageIndex 从 1 起、pageSize 默认 1000、data.hasNext 续页、
  data.datas / data.items 兼容；
- 已知边界：进入动作结构化条件无公开读接口（UNRESOLVED_ACTION_READ_PROBES）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from .. import accounts as accounts_svc

TOKEN_TTL_S = 55 * 60


class ApiError(RuntimeError):
    """平台返回非 0 信封或网络错误（对应 TS 版 ApiError）。"""

    def __init__(self, method: str, path: str, code: Any, message: str) -> None:
        super().__init__(f"{method} {path} 失败：code={code} message={message}")
        self.method = method
        self.path = path
        self.code = code
        self.message = message


class EgmpSettings(BaseModel):
    base_url: str = Field(..., description="平台根地址，如 https://demo-val.example.com")
    username: str = ""
    password: str = ""


def _trim_base(url: str) -> str:
    return url.rstrip("/")


def token_cache_path(env_id: str) -> Path:
    from ... import config

    path = config.RUNTIME_DIR / "egmp-tokens"
    path.mkdir(parents=True, exist_ok=True)
    return path / f"token-{env_id or 'cli'}.json"


def load_cached_token(env_id: str) -> str | None:
    path = token_cache_path(env_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("expiresAt", 0) > time.time() and data.get("token"):
            return str(data["token"])
    except (OSError, ValueError):
        pass
    return None


def save_cached_token(env_id: str, token: str) -> None:
    path = token_cache_path(env_id)
    path.write_text(
        json.dumps({"token": token, "expiresAt": time.time() + TOKEN_TTL_S}, ensure_ascii=False),
        encoding="utf-8",
    )


def login(base_url: str, username: str, password: str, env_id: str = "") -> str:
    """登录（带 55 分钟磁盘缓存）。返回 JWT。"""
    cached = load_cached_token(env_id)
    if cached:
        return cached
    body = {"account": username, "password": password}
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{_trim_base(base_url)}/api/openapi/v1.0/Auth", json=body,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        payload = resp.json()
    if payload.get("code") != 0 or not isinstance(payload.get("data"), str):
        raise ApiError("POST", "/api/openapi/v1.0/Auth",
                       payload.get("code"), str(payload.get("message")))
    token = payload["data"]
    save_cached_token(env_id, token)
    return token


def token_for_account(account_id: str) -> tuple[str, str]:
    """从统一账号库取凭证并登录，返回 (base_url, token)。"""
    account = accounts_svc.get_account(account_id)
    if not account:
        raise ValueError(f"账号不存在：{account_id}")
    base_url = account.get("env_base_url") or ""
    if not base_url:
        raise ValueError(f"账号 {account['username']} 所属平台环境未配置 baseUrl")
    password = accounts_svc.reveal_password(account_id)
    token = login(base_url, account["username"], password, env_id=str(account.get("env_id") or ""))
    return base_url, token


class EgmpClient:
    """同步 httpx 客户端（Bearer + 信封校验 + 分页循环）。"""

    def __init__(self, base_url: str, token: str, timeout: float = 60.0) -> None:
        self._http = httpx.Client(
            base_url=_trim_base(base_url),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "EgmpClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _envelope(self, method: str, path: str, payload: dict) -> dict:
        resp = self._http.request(method, path, json=payload if method == "POST" else None)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise ApiError(method, path, data.get("code"), str(data.get("message")))
        return data.get("data") or {}

    def get(self, path: str, params: dict | None = None) -> Any:
        if params:
            resp = self._http.get(path, params=params)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise ApiError("GET", path, data.get("code"), str(data.get("message")))
            return data.get("data")
        return self._envelope("GET", path, {})

    def post(self, path: str, body: dict | None = None) -> Any:
        return self._envelope("POST", path, body or {})

    def paged_post(self, path: str, body: dict | None = None, page_size: int = 1000) -> list[dict]:
        """平台统一分页循环（pageIndex 从 1；datas/items 兼容；hasNext 续页）。"""
        rows: list[dict] = []
        page_index = 1
        while True:
            data = self._envelope("POST", path, {**(body or {}), "pageIndex": page_index, "pageSize": page_size})
            if isinstance(data, dict):
                batch = data.get("datas") or data.get("items") or []
                rows.extend(batch if isinstance(batch, list) else [])
                if data.get("hasNext") is True:
                    page_index += 1
                    continue
            break
        return rows

    # ------------------------------------------------- 只读查询（阶段 3A 触发件）

    def get_object(self, code: str) -> dict:
        """OpenAPI 4.8：对象元数据。"""
        return self.get(f"/api/openapi/v1.0/BasicObject/{code}")

    def get_object_fields(self, code: str) -> list:
        """OpenAPI 4.6：字段（降级通道；主通道 FieldPage 见 page_fields）。"""
        return self.get(f"/api/openapi/v1.0/BasicObject/field/{code}") or []

    def get_object_statuses(self, code: str) -> list:
        """OpenAPI 4.5：生命周期状态。"""
        return self.get(f"/api/openapi/v1.0/BasicObject/lifecycleStatus/{code}") or []

    def page_fields(self, object_id: str) -> list[dict]:
        """字段主通道（含内嵌选项集实例）。"""
        return self.paged_post("/api/platform/BasicObject/FieldPage", {"objectId": object_id})

    def list_workflows(self) -> list[dict]:
        return self.paged_post(
            "/api/platform/Workflow/GetWorkflowBasicPageViewList", {"filters": []}
        )

    def list_menus(self) -> list:
        return self.get("/api/platform/Menu/GetMenuList") or []


def egmp_client_for_account(account_id: str) -> EgmpClient:
    """便捷工厂：账号 id → 已登录客户端。"""
    base_url, token = token_for_account(account_id)
    return EgmpClient(base_url, token)
