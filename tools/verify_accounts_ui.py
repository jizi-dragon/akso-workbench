"""账号中心（云端版）DOM 验证。

覆盖用户 2026-10-20 定的口径：
- 数据源只有云端，未登录时账号中心有遮罩，中间是登录；
- 不再有本地账号/站点/盒子/备份管理控件；
- 云端登录后账号卡片、盒子 chips、站点/盒子台账可渲染；
- 侧栏 180px，Agent 能力菜单已隐藏；
- 零 JS 错误。

脚本自起服务、临时数据目录、独立端口；用 Playwright 路由伪造云端状态与库，
不需要真实登录，也不写用户真实数据库。
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
PORT = 18798
BASE = f"http://127.0.0.1:{PORT}"

WORKBENCH_DATA = pathlib.Path(tempfile.mkdtemp(prefix="wb-verify-accounts-"))
ENV = {
    **os.environ,
    "WORKBENCH_DATA": str(WORKBENCH_DATA),
    "WORKBENCH_LOG_DIR": str(WORKBENCH_DATA / "logs"),
    "PYTHONIOENCODING": "utf-8",
}

with socket.socket() as sock:
    if sock.connect_ex(("127.0.0.1", PORT)) == 0:
        raise SystemExit(f"✗ 端口 {PORT} 被占用——先退出占用它的进程")

server = subprocess.Popen(
    [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
     "workbench.api:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(ROOT), env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
try:
    for _ in range(50):
        try:
            urllib.request.urlopen(BASE + "/api/health", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    else:
        raise SystemExit("✗ 服务没有就绪")

    library = {
        "format": "akso-cloud-snapshot",
        "version": 1,
        "revision": 7,
        "snapshotId": "verify",
        "defaultBox": "默认盒子",
        "counts": {"sites": 2, "accounts": 3, "boxes": 2},
        "sites": [
            {"id": "s1", "name": "生产环境", "baseUrl": "https://prod.example.com",
             "note": "", "accountCount": 2},
            {"id": "s2", "name": "测试环境", "baseUrl": "https://test.example.com",
             "note": "", "accountCount": 1},
        ],
        "boxes": [
            {"name": "默认盒子", "isDefault": True, "disabled": False, "count": 1},
            {"name": "运行组", "isDefault": False, "disabled": False, "count": 2},
        ],
        "accounts": [
            {"id": "a1", "siteId": "s1", "siteName": "生产环境", "baseUrl": "https://prod.example.com",
             "username": "admin", "tabName": "生产管理员", "box": "运行组", "tags": ["核心"],
             "hasPassword": True, "status": "idle"},
            {"id": "a2", "siteId": "s2", "siteName": "测试环境", "baseUrl": "https://test.example.com",
             "username": "tester", "tabName": "", "box": "", "tags": [],
             "hasPassword": False, "status": "idle"},
            {"id": "a3", "siteId": "s1", "siteName": "生产环境", "baseUrl": "https://prod.example.com",
             "username": "ops", "tabName": "运维", "box": "运行组", "tags": [],
             "hasPassword": True, "status": "idle"},
        ],
    }

    from playwright.sync_api import sync_playwright

    checks: list[tuple[str, bool]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1094, "height": 656})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def status(handler):
            handler.fulfill(status=200, content_type="application/json",
                            body=json.dumps({"loggedIn": False}))

        page.route("**/api/vault/status", status)
        page.route("**/api/vault/library*", lambda handler: handler.fulfill(
            status=200, content_type="application/json", body=json.dumps(library)))
        page.goto(f"{BASE}/static/pages/accounts.html", wait_until="networkidle")
        page.wait_for_timeout(400)

        checks.append(("C1 侧栏宽 180px", abs(page.evaluate(
            "() => document.querySelector('.app-sidebar').getBoundingClientRect().width") - 180) < 1))
        checks.append(("C2 未登录遮罩可见", page.locator("#login-overlay").is_visible()))
        checks.append(("C3 遮罩中间有登录按钮", page.locator("#cloud-login").is_visible()))
        checks.append(("C4 内容被 aria-hidden", page.evaluate(
            "() => document.getElementById('cloud-content').getAttribute('aria-hidden') === 'true'")))
        checks.append(("C5 无本地账号/站点/备份控件", page.evaluate(
            "() => !document.getElementById('btn-acc-add') && !document.getElementById('site-form')"
            " && !document.getElementById('btn-export')")))
        checks.append(("C6 Agent 能力菜单已隐藏", page.locator("#nav-skills").count() == 0
                       and page.locator("#nav-sandbox").count() == 0))

        page.unroute("**/api/vault/status")
        page.route("**/api/vault/status", lambda handler: handler.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"loggedIn": True, "displayName": "李渝龙", "email": "a@b.c"})))
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(500)

        checks.append(("C7 登录后遮罩消失", not page.locator("#login-overlay").is_visible()))
        checks.append(("C8 统计来自云端 3/2/2", page.evaluate(
            "() => [document.getElementById('stat-accounts').textContent,"
            " document.getElementById('stat-sites').textContent,"
            " document.getElementById('stat-boxes').textContent].join('/')") == "3/2/2"))
        checks.append(("C9 云端账号卡片 ×3", page.locator("#acc-wall .account-card").count() == 3))
        checks.append(("C10 卡片无本地操作按钮", page.locator("#acc-wall .ac-actions").count() == 0))
        checks.append(("C11 卡片显示只读标记", page.locator("#acc-wall .cloud-readonly").count() == 3))
        checks.append(("C12 盒子 chips = 全部 + 2", page.locator("#box-chips .chip").count() == 3))
        checks.append(("C13 站点台账 2 行", page.locator("#site-list li").count() == 2))
        checks.append(("C14 盒子台账 2 行", page.locator("#box-list li").count() == 2))
        checks.append(("C15 云端身份显示", page.locator("#cloud-user").inner_text().strip() == "李渝龙"))
        checks.append(("C16 本地推云端按钮在位", page.locator("#btn-push").is_visible()))

        page.fill("#acc-search", "ops")
        page.wait_for_timeout(120)
        checks.append(("C17 搜索过滤到 1 张卡", page.locator("#acc-wall .account-card").count() == 1))
        checks.append(("C18 零 JS 错误", not errors))

        if errors:
            print("\n".join(f"  JS: {error}" for error in errors))
        browser.close()
finally:
    server.terminate()
    try:
        server.wait(timeout=5)
    except Exception:
        server.kill()

print()
for name, ok in checks:
    print(f"{'✔' if ok else '✗'} {name}")
print(f"\nUI_CHECKS: {sum(ok for _, ok in checks)}/{len(checks)}")
sys.exit(0 if all(ok for _, ok in checks) else 1)
