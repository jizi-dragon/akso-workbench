"""Agent 账号工具：云端优先 + 云端 id 登录。"""

from __future__ import annotations

import asyncio
import json

from workbench.plugins import accounts as plugin
from workbench.services import vault


def test_accounts_list_uses_cloud_when_logged_in(monkeypatch) -> None:
    monkeypatch.setattr(vault, "status", lambda: {"loggedIn": True})
    monkeypatch.setattr(vault, "library", lambda: {
        "accounts": [
            {"id": "a1", "username": "alice", "siteName": "生产", "baseUrl": "https://a.test",
             "box": "默认", "role": "管理员", "tags": ["gmp"], "hasPassword": True},
        ],
    })
    raw = asyncio.run(plugin.list_accounts(None, object()))
    data = json.loads(raw)
    assert data["source"] == "cloud"
    assert data["accounts"][0]["account_id"] == "a1"
    assert data["accounts"][0]["has_password"] is True
    assert "passwordEnc" not in raw and "secret" not in raw


def test_accounts_login_can_use_cloud_account(monkeypatch) -> None:
    seen: dict[str, str] = {}

    monkeypatch.setattr(vault, "status", lambda: {"loggedIn": True})
    monkeypatch.setattr(vault, "cloud_credentials", lambda _id: {
        "username": "alice", "password": "secret", "base_url": "https://a.test",
        "env_name": "生产", "env_id": "s1",
    })
    from workbench.services import accounts as accounts_svc

    monkeypatch.setattr(accounts_svc, "get_account", lambda _id: None)

    def fake_login(base_url, username, password, env_id):
        seen.update(base_url=base_url, username=username, password=password, env_id=env_id)
        return "tok-123456789"

    monkeypatch.setattr("workbench.services.egmp.client.login", fake_login)
    args = plugin.LoginArgs(account_id="a1")
    raw = asyncio.run(plugin.login(args, object()))
    data = json.loads(raw)
    assert seen["password"] == "secret"
    assert seen["env_id"] == "s1"
    assert data["account_id"] == "a1"
    assert data["username"] == "alice"
    assert "secret" not in raw
