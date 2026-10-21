"""侧栏结构契约：唯一入口、可折叠、模块页共享。

这些是静态可查的“界面承诺”：
- 账号中心与云端账号库不再各占一个左栏入口；
- 主窗有隐藏/显示按钮；
- 模块页（账号/洞察/Skill/沙箱）都加载共享 sidebar.js，因此点击导航不会丢侧栏。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "workbench" / "static"


def test_main_sidebar_has_single_account_entry() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="nav-accounts"' in html
    assert 'id="nav-cloud"' not in html
    assert 'href="/static/pages/cloud.html"' not in html
    # 2026-10-20：Agent 能力暂不面向用户，两个入口隐藏
    assert 'id="nav-skills"' not in html
    assert 'id="nav-sandbox"' not in html


def test_main_sidebar_has_hide_and_reopen_buttons() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="btn-sidebar-toggle"' in html
    assert 'id="btn-sidebar-open"' in html
    css = (STATIC / "css" / "app.css").read_text(encoding="utf-8")
    assert "width: 180px" in css
    js = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
    assert "workbench.sidebar.hidden" in js
    assert "sidebar-hidden" in js


def test_module_pages_load_shared_sidebar() -> None:
    for name in ("accounts.html", "insight.html", "skills.html", "sandbox.html"):
        html = (STATIC / "pages" / name).read_text(encoding="utf-8")
        assert "/static/css/sidebar.css" in html, name
        assert "/static/js/sidebar.js" in html, name
    shared = (STATIC / "js" / "sidebar.js").read_text(encoding="utf-8")
    assert "workbench.sidebar.hidden" in shared
    assert 'id="nav-${key}"' in shared
    assert "账号中心" in shared
    assert "Skill 管理" not in shared
    assert "cloud.html" not in shared


def test_tauri_treats_tauri_localhost_as_local() -> None:
    main = (ROOT / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
    assert 'Some("tauri.localhost")' in main
    # 首次导航带 shell=tauri，让 Web 端跳过第二段 splash
    assert "?shell=tauri" in main


def test_web_splash_is_skipped_in_tauri_shell() -> None:
    app = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
    assert 'get("shell") === "tauri"' in app
    assert "shellMode" in app


def test_tauri_default_window_is_80_percent_and_centered() -> None:
    main = (ROOT / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
    assert ".work_area()" in main
    assert "area_w * 0.8" in main
    assert ".position(x, y)" in main
    assert "set_size(tauri::LogicalSize::new(1440.0, 920.0))" not in main
