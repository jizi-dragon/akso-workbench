"""错误身份契约：调用方按**类型/码**分类，不按消息文本猜。

## 为什么单列一份

2026-10-22 审计实测了两处同族缺陷：

- `services/vault.py`：`if "会话" in str(exc) or "401" in str(exc)` —— 云端换一句
  措辞，401 就退化成 502；界面把"请重新登录"显示成"上游挂了"。
- `services/mcp.py`：`if "-32601" in text or "Method not found" in text` —— 正常
  server 不实现 ping 时，健康检查会每 30 秒重启它一次。

两处都**不报错**，只让行为在某个措辞下悄悄变差。这份测试钉三件事：

1. `VaultError.kind` / `McpError.code` 是唯一判据；
2. **反证**：消息里带 "401" 但 kind 是 upstream 时，**不得**被当成鉴权失败；
3. 静态扫描：`workbench/**` 里不得再出现「字符串字面量 `in` 错误文本」的写法。
"""

from __future__ import annotations

import ast
import io
import urllib.error
from pathlib import Path

import httpx
import pytest

from workbench import db
from workbench.services import vault
from workbench.services.egmp.client import ApiError
from workbench.services.egmp.queries import GaiaQueries
from workbench.services.mcp import McpClient, McpError, ServerConfig

ROOT = Path(__file__).resolve().parents[1]
WORKBENCH = ROOT / "workbench"


@pytest.fixture()
def ready():
    db.connect()
    vault.clear_session()
    yield
    vault.clear_session()


# ---------------------------------------------------------------- VaultError


def test_vault_auth_kind_is_the_only_judgement() -> None:
    auth = vault.VaultError("随便什么措辞", status=401, kind="auth")
    upstream = vault.VaultError("401 网关错误", status=502, kind="upstream")
    assert vault.is_auth_error(auth) is True
    assert vault.is_auth_error(upstream) is False, (
        "消息里有 401 就被当成鉴权失败 —— 这正是旧实现的做法，已经删除。"
    )


def test_vault_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="kind"):
        vault.VaultError("x", kind="made-up")


def test_request_maps_http_and_network_errors_to_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_401(*_a, **_k):
        raise urllib.error.HTTPError(
            "https://cloud.example/api", 401, "Unauthorized", {},
            io.BytesIO(b'{"detail":"expired"}'))

    monkeypatch.setattr(vault.urllib.request, "urlopen", raise_401)
    with pytest.raises(vault.VaultError) as ei:
        vault._request("GET", "/api/auth/profile")
    assert ei.value.kind == "auth" and ei.value.status == 401

    def raise_502(*_a, **_k):
        raise urllib.error.HTTPError(
            "https://cloud.example/api", 502, "Bad Gateway", {}, io.BytesIO(b""))

    monkeypatch.setattr(vault.urllib.request, "urlopen", raise_502)
    with pytest.raises(vault.VaultError) as ei2:
        vault._request("GET", "/api/auth/profile")
    assert ei2.value.kind == "upstream" and ei2.value.status == 502

    def raise_network(*_a, **_k):
        raise urllib.error.URLError("dns down")

    monkeypatch.setattr(vault.urllib.request, "urlopen", raise_network)
    with pytest.raises(vault.VaultError) as ei3:
        vault._request("GET", "/api/auth/profile")
    assert ei3.value.kind == "network"


def test_fetch_profile_clears_only_on_auth_kind(
    ready, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault.save_session("tok", {"email": "a@b.c"})

    def auth(*_a, **_k):
        raise vault.VaultError("会话已过期", kind="auth")

    monkeypatch.setattr(vault, "_request", auth)
    with pytest.raises(vault.VaultError):
        vault.fetch_profile()
    assert vault.session_token() == "", "鉴权失败必须清本地会话"

    # 反证：消息里带 401，但 kind 是 upstream —— 不得清会话。
    vault.save_session("tok2", {"email": "a@b.c"})

    def upstream(*_a, **_k):
        raise vault.VaultError("401 网关错误", status=502, kind="upstream")

    monkeypatch.setattr(vault, "_request", upstream)
    with pytest.raises(vault.VaultError):
        vault.fetch_profile()
    assert vault.session_token() == "tok2", "上游 502 不该让用户重新登录"


# ---------------------------------------------------------------- EGMP queries


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://platform.example/api")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


class _RaisingClient:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def get(self, _path: str):
        raise self._exc


def test_queries_swallow_only_identified_misses() -> None:
    assert GaiaQueries(_RaisingClient(ApiError("GET", "/x", 500, "not found"))).get_picklist("x") is None
    assert GaiaQueries(_RaisingClient(_http_error(404))).get_picklist("x") is None

    with pytest.raises(httpx.HTTPStatusError):
        GaiaQueries(_RaisingClient(_http_error(502))).get_picklist("x")
    with pytest.raises(httpx.ReadTimeout):
        GaiaQueries(_RaisingClient(httpx.ReadTimeout("timed out"))).get_picklist("x")


def test_workflow_uses_the_same_miss_boundary() -> None:
    assert GaiaQueries(_RaisingClient(ApiError("GET", "/w", 500, "nope"))).get_workflow("w") is None
    with pytest.raises(httpx.ReadTimeout):
        GaiaQueries(_RaisingClient(httpx.ReadTimeout("timed out"))).get_workflow("w")


# ---------------------------------------------------------------- MCP


def test_mcp_ping_uses_the_json_rpc_code_not_the_message() -> None:
    client = McpClient(ServerConfig(name="probe", command="probe"))

    def method_not_found(_method, _params, *, timeout):
        raise McpError("Method not found", code=-32601, kind="rpc")

    client._request = method_not_found  # type: ignore[method-assign]
    client.ping()  # 不抛 = 健康

    def lookalike(_method, _params, *, timeout):
        # 反证：措辞一模一样，但没有码 —— 必须抛。
        raise McpError("Method not found")

    client._request = lookalike  # type: ignore[method-assign]
    with pytest.raises(McpError):
        client.ping()


# ---------------------------------------------------------------- 静态规则

_ERROR_TEXT_NAMES = {"text", "msg", "message", "str_exc", "detail", "body"}
_ERROR_STRINGS = {"401", "403", "404", "500", "502", "会话", "未登录", "重新登录",
                  "Method not found", "-32601"}


def _is_error_text(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id in _ERROR_TEXT_NAMES
    if isinstance(node, ast.Call):
        return isinstance(node.func, ast.Name) and node.func.id == "str"
    return False


def _violations() -> list[str]:
    out: list[str] = []
    for path in WORKBENCH.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            left = node.left
            if not (isinstance(left, ast.Constant) and isinstance(left.value, str)):
                continue
            if left.value not in _ERROR_STRINGS:
                continue
            for op, right in zip(node.ops, node.comparators, strict=True):
                if isinstance(op, (ast.In, ast.NotIn)) and _is_error_text(right):
                    rel = path.relative_to(ROOT).as_posix()
                    out.append(f"{rel}:{node.lineno}: {left.value!r} in 错误文本")
    return out


def test_no_code_matches_error_messages_by_string_literal() -> None:
    bad = _violations()
    assert not bad, (
        "用字符串字面量判断错误文本 —— 云端/上游换一句措辞，分支就静默失效。\n"
        "改用 typed code / kind。命中：\n" + "\n".join(bad)
    )
