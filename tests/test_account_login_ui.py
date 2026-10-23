"""账号登录入口与自动同步的界面接线。"""

from __future__ import annotations

from pathlib import Path


def test_global_login_chip_is_wired() -> None:
    root = Path(__file__).resolve().parents[1] / "workbench" / "static"
    js = (root / "js" / "sidebar.js").read_text(encoding="utf-8")
    assert 'id="vault-chip"' in js
    assert "/api/vault/status" in js
    assert "登录云端账号" in js
    app_js = (root / "js" / "app.js").read_text(encoding="utf-8")
    html = (root / "index.html").read_text(encoding="utf-8")
    assert 'id="vault-chip-main"' in html
    assert "initVaultChip" in app_js


def test_accounts_page_auto_syncs() -> None:
    root = Path(__file__).resolve().parents[1] / "workbench" / "static"
    js = (root / "js" / "accounts.js").read_text(encoding="utf-8")
    assert "setInterval(autoSync, 60_000)" in js
    assert "visibilitychange" in js
    assert "state.status?.loggedIn" in js or "state.status?.loggedIn" in js
    html = (root / "pages" / "accounts.html").read_text(encoding="utf-8")
    assert 'id="cloud-login"' in html
