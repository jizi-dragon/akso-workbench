"""本地账号库 → 云端 merge 的请求形状与 revision 处理。"""

from __future__ import annotations

from workbench.services import accounts, storage, vault


def _seed_local_account() -> None:
    env = accounts.create_env(name="云端测试站", base_url="https://cloud.example.com")
    account = accounts.create_account(
        env_id=env["id"],
        username="cloud-user",
        password="secret-pw",
        box="运行组",
        tab_name="云端页签",
    )
    assert account["id"]


def test_push_local_builds_contract_payload(monkeypatch) -> None:
    _seed_local_account()
    monkeypatch.setattr(vault, "session_token", lambda: "session-token")
    captured: dict = {}

    def fake_request(method: str, path: str, *, body=None, token=None):
        captured.update(method=method, path=path, body=body, token=token)
        return {"ok": True, "created": 1, "updated": 0, "skipped": 0, "revision": 8}

    monkeypatch.setattr(vault, "_request", fake_request)
    result = vault.push_local()

    assert captured["method"] == "POST"
    assert captured["path"] == "/api/vault/merge"
    assert captured["token"] == "session-token"
    payload = captured["body"]
    assert payload["clientAccountCount"] == len(payload["accounts"])
    assert payload["clientAccountCount"] >= 1
    site = next(s for s in payload["sites"] if s["host"] == "cloud.example.com")
    assert site["scheme"] == "https"
    row = next(a for a in payload["accounts"] if a["username"] == "cloud-user")
    assert row == {
        "siteHost": "cloud.example.com",
        "username": "cloud-user",
        "password": "secret-pw",
        "tabName": "云端页签",
        "box": "运行组",
        "scheme": "",
        "updatedAt": row["updatedAt"],
    }
    assert result["sent"] == payload["clientAccountCount"]
    assert storage.get_setting("vault_revision") == "8"


def test_push_local_requires_login(monkeypatch) -> None:
    _seed_local_account()
    monkeypatch.setattr(vault, "session_token", lambda: "")
    try:
        vault.push_local()
    except vault.VaultError as exc:
        assert "登录" in str(exc)
    else:
        raise AssertionError("未登录必须拒绝推送")
