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

import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from .. import accounts as accounts_svc
from .kernel import endpoints, normalize_base_url

TOKEN_TTL_S = 55 * 60

#: `paged_post` 的硬上限（2026-10-22：恒 `hasNext` 的桩实测能打出 501 页）。
MAX_PAGES = 20


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
    """归一化平台根地址（唯一事实来源见 `kernel.urls`）。

    历史缺陷：这里原先是裸的 `url.rstrip("/")`，于是用户把**登录页**地址整段粘进来
    （`https://host/login`）时，AUTH 会被拼成 `.../login/api/openapi/v1.0/Auth` →
    网关 405，表现为「login 连通测试无法连通」。
    """
    return normalize_base_url(url)


def token_cache_path(base_url: str, username: str) -> Path:
    """token 缓存文件路径——按 **(base_url, username)** 分键。

    历史缺陷：只按 `env_id` 分键且缺省回落 `token-cli.json`，导致 A 环境的 token 被交给
    B 环境的 base_url 使用、同环境两个账号共享同一张票（第二个账号以第一个的身份运行）。
    `env_id` 只作为文件内的一列元数据保留，**不再参与分键**。
    """
    from ... import config

    path = config.RUNTIME_DIR / "egmp-tokens"
    path.mkdir(parents=True, exist_ok=True)
    return path / f"token-{_token_key(base_url, username)}.json"


def _token_key(base_url: str, username: str) -> str:
    scope = f"{_trim_base(base_url).lower()}\x1f{username}"
    return hashlib.sha1(scope.encode("utf-8")).hexdigest()[:16]


def _jwt_exp(token: str) -> float | None:
    """读 JWT 载荷里的 `exp`（**不验签**，仅用于比本地 TTL 更早地判定失效）。

    本地 TTL 只是墙钟约定；平台若签了更短的票，必须以平台的为准。
    非 JWT（无法解析）时返回 None，调用方退化为只看本地 TTL。
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        exp = data.get("exp")
        return float(exp) if exp is not None else None
    except Exception:  # noqa: BLE001 —— 任何解析失败都视为「读不出 exp」
        return None


def load_cached_token(base_url: str, username: str) -> str | None:
    """读取并**校验归属**的 token 缓存；不匹配/过期/过期票据一律返回 None。"""
    path = token_cache_path(base_url, username)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    token = data.get("token")
    if not token:
        return None
    # 归属校验（纵深防御）：文件被放错位置或换过内容都不能用
    if str(data.get("baseUrl") or "").rstrip("/") != _trim_base(base_url):
        return None
    if str(data.get("username") or "") != username:
        return None
    if float(data.get("expiresAt") or 0) <= time.time():
        return None
    exp = _jwt_exp(str(token))
    if exp is not None and exp <= time.time():
        return None
    return str(token)


def save_cached_token(base_url: str, username: str, token: str, env_id: str = "") -> None:
    """写 token 缓存（**原子写**：临时文件 + rename，避免并发写撕裂）。"""
    path = token_cache_path(base_url, username)
    payload = {
        "token": token,
        "expiresAt": time.time() + TOKEN_TTL_S,
        "baseUrl": _trim_base(base_url),
        "username": username,
        "envId": env_id or "",
        "savedAt": time.time(),
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def login(base_url: str, username: str, password: str, env_id: str = "") -> str:
    """登录（带 55 分钟磁盘缓存，按 `(base_url, username)` 分键）。返回 JWT。"""
    cached = load_cached_token(base_url, username)
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
        raise ApiError("POST", endpoints.AUTH,
                       payload.get("code"), str(payload.get("message")))
    token = payload["data"]
    save_cached_token(base_url, username, token, env_id)
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
        self._base_url = _trim_base(base_url)
        self._http = httpx.Client(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=timeout,
        )

    @property
    def scope(self) -> str:
        """环境作用域标识（= 归一化 base_url）——缓存分键用，防止跨环境串台。"""
        return self._base_url

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
        """平台统一分页循环（pageIndex 从 1；datas/items 兼容；hasNext 续页）。

        ★ 有硬上限：`hasNext` 恒为 true 的坏 server/坏代理会让这里**无限请求**
        （实测算过 501 页）。到上限抛 `ApiError(code="pagination")`，不静默返回半截。
        """
        rows: list[dict] = []
        page_index = 1
        for _page in range(1, MAX_PAGES + 1):
            data = self._envelope("POST", path, {**(body or {}), "pageIndex": page_index, "pageSize": page_size})
            if isinstance(data, dict):
                batch = data.get("datas") or data.get("items") or []
                rows.extend(batch if isinstance(batch, list) else [])
                if data.get("hasNext") is True:
                    if _page == MAX_PAGES:
                        raise ApiError(
                            "POST", path, "pagination",
                            f"分页超过 {MAX_PAGES} 页，已中止（hasNext 可能没有前进）")
                    page_index += 1
                    continue
            break
        return rows

    # ------------------------------------------------- 只读查询（阶段 3A 触发件）

    def get_object(self, code: str) -> dict:
        """OpenAPI 4.8：对象元数据。"""
        return self.get(endpoints.render(endpoints.OBJECT, code=code))

    def get_object_fields(self, code: str) -> list:
        """OpenAPI 4.6：字段（降级通道；主通道 FieldPage 见 page_fields）。"""
        return self.get(endpoints.render(endpoints.OBJECT_FIELD, code=code)) or []

    def get_object_statuses(self, code: str) -> list:
        """OpenAPI 4.5：生命周期状态。"""
        return self.get(endpoints.render(endpoints.OBJECT_LIFECYCLE, code=code)) or []

    def page_fields(self, object_id: str) -> list[dict]:
        """字段主通道（含内嵌选项集实例）。"""
        return self.paged_post(endpoints.FIELD_PAGE, {"objectId": object_id})

    def list_workflows(self) -> list[dict]:
        return self.paged_post(
            endpoints.WORKFLOW_LIST, {"filters": []}
        )

    def list_menus(self) -> list:
        return self.get(endpoints.MENU_LIST) or []


def egmp_client_for_account(account_id: str) -> EgmpClient:
    """便捷工厂：账号 id → 已登录客户端。"""
    base_url, token = token_for_account(account_id)
    return EgmpClient(base_url, token)
