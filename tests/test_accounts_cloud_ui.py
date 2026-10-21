"""账号中心云端化后的静态契约。

页面必须：
- 数据源是 `/api/vault/library`，不再调用本地 `/api/accounts` 管理接口；
- 未登录遮罩与登录按钮存在；
- 不再有本地站点/账号/备份管理控件。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "workbench" / "static" / "pages" / "accounts.html"
JS = ROOT / "workbench" / "static" / "js" / "accounts.js"
API = ROOT / "workbench" / "static" / "js" / "api.js"


def test_account_page_has_cloud_login_overlay_and_no_local_controls() -> None:
    html = PAGE.read_text(encoding="utf-8")
    assert 'id="login-overlay"' in html
    assert 'id="cloud-login"' in html
    assert "cloud-account.css" in html
    for stale in ("btn-acc-add", "site-form", "btn-export", "btn-import-backup", "acc-bulk"):
        assert f'id="{stale}"' not in html


def test_account_js_reads_cloud_library_and_pushes_local() -> None:
    js = JS.read_text(encoding="utf-8")
    assert "getVaultLibrary" in js
    assert "vaultPush" in js
    assert "/api/accounts" not in js
    assert "setOverlay(true" in js


def test_api_exposes_vault_push() -> None:
    assert "vaultPush" in API.read_text(encoding="utf-8")
