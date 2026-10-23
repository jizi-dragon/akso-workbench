"""云端账号库客户端的契约测试（假云端，不联网、不起服务）。

## 为什么补这一份

`services/vault.py` + `routes_vault.py` 是 2026-10-10 新增的用户可见功能，
而 2026-10-19 全仓 grep `vault` 在 `tests/` 里是 **0 命中** —— 设备流、会话保存、
退出顺序、错误映射一条判据都没有。这里用 monkeypatch 把云端换成内存函数，
把**本仓这一侧**的契约钉住：

- 会话令牌**密文落库**（规则 19：凭据不落明文）；
- `status()` **不打网络**（首屏不能因为断网卡住）；
- `logout()` 即使云端失败也必须清本地（用户点了退出就必须退出）；
- 路由把 `VaultError` 映射成 502/401（不是 500 —— 那是上游不可用，不该让人先查本服务）；
- 默认地址是 **443**（:8443 只是服务端的临时垫片）。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from workbench import db
from workbench.api import routes_vault
from workbench.services import storage, vault


@pytest.fixture()
def ready():
    db.connect()
    vault.clear_session()
    yield
    vault.clear_session()


def test_default_url_uses_standard_https_port() -> None:
    """★ 默认地址不能继续指 :8443（服务端已迁 443，旧端口只是垫片）。"""
    assert vault.DEFAULT_URL.startswith("https://")
    assert not vault.DEFAULT_URL.endswith(":8443")


def test_session_token_is_encrypted_at_rest(ready) -> None:
    """规则 19：令牌落库必须是密文，且能原样取回。"""
    vault.save_session("tok-secret-123", {"email": "alice@example.com"})

    blob = storage.get_setting("vault_session_token")
    assert blob and "tok-secret-123" not in blob, "会话令牌不能明文落库"
    assert vault.session_token() == "tok-secret-123"
    assert vault.status()["loggedIn"] is True
    assert vault.status()["email"] == "alice@example.com"


def test_status_does_not_hit_the_network(ready, monkeypatch: pytest.MonkeyPatch) -> None:
    """首屏状态必须纯本地：一旦联网，断网时它会把页面卡住。"""
    def boom(*_a, **_k):
        raise AssertionError("status() 不该发请求")

    monkeypatch.setattr(vault, "_request", boom)
    assert vault.status()["loggedIn"] is False


def test_logout_clears_local_session_even_if_cloud_fails(
    ready, monkeypatch: pytest.MonkeyPatch
) -> None:
    """退出顺序：先吊销、再清本地；云端失败也必须清干净。"""
    vault.save_session("tok", {"email": "e"})

    def boom(*_a, **_k):
        raise vault.VaultError("云端不可达")

    monkeypatch.setattr(vault, "_request", boom)
    vault.logout()
    assert vault.session_token() == ""
    assert vault.status()["loggedIn"] is False


def test_login_poll_saves_the_session_when_approved(
    ready, monkeypatch: pytest.MonkeyPatch
) -> None:
    """批准的那一刻由**服务端**落会话（避免"拿到了令牌但没存上"的窗口）。"""
    monkeypatch.setattr(
        vault, "device_poll",
        lambda code: {"status": "approved", "token": "tok-approved", "user": {"email": "a@b.c"}},
    )
    body = routes_vault.PollBody(deviceCode="device-code-123")
    resp = asyncio.run(routes_vault.vault_login_poll(body))

    assert resp["status"] == "approved"
    assert "token" not in resp, "令牌只留在本机，不能回给浏览器"
    assert vault.session_token() == "tok-approved"


def test_upstream_failure_maps_to_502(ready, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a, **_k):
        raise vault.VaultError("连不上云端")

    monkeypatch.setattr(vault, "device_start", boom)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(routes_vault.vault_login_start(None))
    assert ei.value.status_code == 502, "上游不可用必须是 502，不是 500"


def test_refresh_maps_to_401_when_session_is_gone(
    ready, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a, **_k):
        raise vault.VaultError("会话已过期，请重新登录", kind="auth")

    monkeypatch.setattr(vault, "fetch_profile", boom)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(routes_vault.vault_refresh())
    assert ei.value.status_code == 401


# ---------------------------------------------------------------- 只读展示视图


def _snapshot() -> dict:
    return {
        "format": "akso-cloud-snapshot",
        "version": 1,
        "generatedAt": 1_730_000_000_000,
        "snapshotId": "snap-123",
        "revision": 7,
        "fernetKey": "FERNEL-SECRET",
        "sites": [
            {"id": "s1", "name": "生产环境", "baseUrl": "https://a.example.com",
             "note": "", "createdAt": 1, "updatedAt": 2, "accountCount": 2},
        ],
        "accounts": [
            {"id": "a1", "siteId": "s1", "siteName": "生产环境",
             "baseUrl": "https://a.example.com", "username": "admin",
             "passwordEnc": "gAAAA-secret", "tabName": "生产管理员", "role": "",
             "tags": ["核心"], "note": "", "status": "idle", "scheme": "",
             "box": "运行组", "createdAt": 1, "updatedAt": 2},
        ],
        "boxes": [
            {"name": "默认盒子", "isDefault": True, "disabled": False, "autoDisabled": False,
             "count": 0, "deletable": False, "renameable": False, "bindable": False,
             "siteId": "", "siteName": "", "sortOrder": 0},
            {"name": "运行组", "isDefault": False, "disabled": False, "autoDisabled": False,
             "count": 1, "deletable": True, "renameable": True, "bindable": True,
             "siteId": "s1", "siteName": "生产环境", "sortOrder": 1},
        ],
        "defaultBox": "默认盒子",
        "counts": {"sites": 1, "accounts": 1, "boxes": 2},
    }


def _patch_snapshot(monkeypatch: pytest.MonkeyPatch, data: dict | None = None):
    vault.save_session("tok-test", {"email": "tester@example.com"})
    calls: list[tuple] = []

    def fake_request(method: str, path: str, *, body=None, token=None):
        calls.append((method, path, token))
        return data if data is not None else _snapshot()

    monkeypatch.setattr(vault, "_request", fake_request)
    return calls


def test_library_strips_credentials_and_maps_fields(ready, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_snapshot(monkeypatch)
    view = vault.library(refresh=True)

    assert view["snapshotId"] == "snap-123"
    assert view["revision"] == 7
    assert view["defaultBox"] == "默认盒子"
    assert view["counts"] == {"sites": 1, "accounts": 1, "boxes": 2}
    assert view["sites"][0]["accountCount"] == 2
    assert view["accounts"][0]["hasPassword"] is True
    assert view["accounts"][0]["tabName"] == "生产管理员"
    # ★ 最关键的一条：页面视图里绝不能出现密钥或密文
    text = str(view)
    assert "FERNEL-SECRET" not in text
    assert "gAAAA-secret" not in text
    assert "fernetKey" not in view and "passwordEnc" not in view["accounts"][0]
    # 缓存里也不能留密钥/密文（缓存会落 settings 表，和视图同样敏感）
    cached = storage.get_setting(vault._SETTING_LIBRARY) or ""
    assert "FERNEL-SECRET" not in cached and "gAAAA-secret" not in cached


def test_library_uses_cache_within_ttl(ready, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_snapshot(monkeypatch)
    vault.library(refresh=True)
    assert len(calls) == 1

    def boom(*_a, **_k):
        raise vault.VaultError("这次不该再发请求")

    monkeypatch.setattr(vault, "_request", boom)
    view = vault.library(refresh=False)
    assert view["fromCache"] is True
    assert view["stale"] is False
    assert view["accounts"][0]["username"] == "admin"


def test_library_returns_stale_view_when_network_fails(ready, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_snapshot(monkeypatch)
    vault.library(refresh=True)
    storage.set_setting(vault._SETTING_LIBRARY_AT, "0")

    def boom(*_a, **_k):
        raise vault.VaultError("连不上云端")

    monkeypatch.setattr(vault, "_request", boom)
    view = vault.library(refresh=True)
    assert view["stale"] is True and view["fromCache"] is True
    assert "连不上云端" in view["error"]
    assert view["accounts"][0]["username"] == "admin"


def test_library_auth_error_clears_session_and_cache(
    ready, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault.save_session("tok", {"email": "a@b.c"})
    _patch_snapshot(monkeypatch)
    vault.library(refresh=True)

    def boom(*_a, **_k):
        raise vault.VaultError("会话已过期（401），请重新登录", kind="auth")

    monkeypatch.setattr(vault, "_request", boom)
    with pytest.raises(vault.VaultError):
        vault.library(refresh=True)
    assert vault.session_token() == ""
    assert storage.get_setting(vault._SETTING_LIBRARY) == ""


def test_library_rejects_unknown_snapshot_format(ready, monkeypatch: pytest.MonkeyPatch) -> None:
    data = _snapshot()
    data["format"] = "something-else"
    _patch_snapshot(monkeypatch, data)
    with pytest.raises(vault.VaultError, match="格式"):
        vault.library(refresh=True)


def test_library_route_maps_errors(ready, monkeypatch: pytest.MonkeyPatch) -> None:
    def auth(*_a, **_k):
        raise vault.VaultError("尚未登录云端账号库", kind="auth")

    monkeypatch.setattr(vault, "library", auth)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(routes_vault.vault_library())
    assert ei.value.status_code == 401

    def upstream(*_a, **_k):
        raise vault.VaultError("连不上云端")

    monkeypatch.setattr(vault, "library", upstream)
    with pytest.raises(HTTPException) as ei2:
        asyncio.run(routes_vault.vault_library())
    assert ei2.value.status_code == 502


def test_cloud_requests_identify_workbench(ready, monkeypatch: pytest.MonkeyPatch) -> None:
    """云端个人页靠 X-Akso-Client 显示客户端状态；每次云端请求都要带上。"""
    captured: dict[str, object] = {}

    class Resp:
        def read(self) -> bytes:
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def fake_urlopen(req, timeout):  # noqa: ANN001
        captured["req"] = req
        return Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    vault._request("GET", "/api/auth/profile", token="t")
    headers = {key.lower(): value for key, value in captured["req"].headers.items()}
    assert headers["x-akso-client"] == "akso-workbench"
    assert headers["x-akso-client-version"] == __import__("workbench").__version__


def test_cloud_credentials_decrypt_only_for_tool_use(ready, monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    enc = Fernet(key).encrypt(b"pw-cloud").decode()
    monkeypatch.setattr(vault, "_snapshot", lambda: {
        "fernetKey": key.decode(),
        "accounts": [{"id": "a1", "username": "alice", "passwordEnc": enc,
                      "baseUrl": "https://a.test", "siteName": "生产", "siteId": "s1"}],
    })
    creds = vault.cloud_credentials("a1")
    assert creds["password"] == "pw-cloud"
    assert creds["username"] == "alice"
    assert creds["base_url"] == "https://a.test"
