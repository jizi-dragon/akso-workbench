"""沙箱页「工作区与权限」的浏览器验收（真 Chromium，临时数据目录）。

## 为什么需要它

`tests/test_sandbox_page.py` 只验 HTML/JS 的 id 契约；它看不见：
- 页面加载后 `#sb-ws-root` 有没有被填上、`#sb-ws-mode` 选中的是不是当前值；
- 点「保存」会不会真的把 PUT 发出去并刷新 meta；
- 以及**有没有 JS 报错**（`getElementById` 返回 null 后 `addEventListener` 抛错，
  表现只是"按钮点了没反应"）。

用法：`.venv\\Scripts\\python.exe tools\\verify_sandbox_ui.py`
"""

from __future__ import annotations

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
PORT = 18789
BASE = f"http://127.0.0.1:{PORT}"

WORKBENCH_DATA = pathlib.Path(tempfile.mkdtemp(prefix="wb-sandbox-verify-"))
_ENV = {
    **os.environ,
    "WORKBENCH_DATA": str(WORKBENCH_DATA),
    "WORKBENCH_LOG_DIR": str(WORKBENCH_DATA / "logs"),
    "PYTHONIOENCODING": "utf-8",
}


def _port_free(port: int) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


if not _port_free(PORT):
    raise SystemExit(f"✗ 端口 {PORT} 被占用——先退出占用它的进程（不要连上去跑验收）")

server = subprocess.Popen(
    [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
     "workbench.api:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(ROOT), env=_ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
try:
    for _ in range(50):
        try:
            urllib.request.urlopen(BASE + "/static/pages/sandbox.html", timeout=2)
            break
        except Exception:
            time.sleep(0.3)

    from playwright.sync_api import sync_playwright

    checks: list[tuple[str, bool]] = []

    def check(label: str, ok: bool) -> None:
        checks.append((label, bool(ok)))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(f"{BASE}/static/pages/sandbox.html", wait_until="domcontentloaded")
        page.wait_for_selector("#sb-workspace-card", timeout=8000)
        page.wait_for_timeout(500)

        root = page.input_value("#sb-ws-root")
        mode = page.input_value("#sb-ws-mode")
        check(f"S1 工作区根已回填（{root[-24:]}）", root and ("sandbox" in root or root.strip()))
        check(f"S2 权限模式已回填（{mode}）", mode in {"read-only", "read-write"})
        check("S3 策略表 3 行", page.locator("#sb-policies tbody tr").count() == 3)
        check("S4 策略表有「写工作区」列", "写工作区" in page.inner_text("#sb-policies"))
        status_text = page.inner_text("#sb-status")
        check("S5 运行状态含工作区根", "工作区根" in status_text)
        check("S6 状态含权限", "权限" in status_text)
        check("S7 自检通过", "通过" in status_text)

        page.click("#sb-ws-save")
        page.wait_for_timeout(600)
        meta = page.inner_text("#sb-ws-meta")
        check(f"S8 保存生效（{meta[:18]}…）", "已保存" in meta)

        page.click("#sb-ws-reset")
        page.wait_for_timeout(1200)
        reset_meta = page.inner_text("#sb-ws-meta")
        check(f"S9 恢复默认生效（{reset_meta[:18]}…）", "恢复默认" in reset_meta)

        check(f"S10 零 JS 报错（{errors[:1]}）", not errors)
        shot = WORKBENCH_DATA / "sandbox-ui.png"
        page.screenshot(path=str(shot), full_page=True)
        print(f"  截图：{shot}")
        browser.close()

    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    failed = [label for label, ok in checks if not ok]
    print(f"\nSANDBOX_UI_CHECKS: {len(checks) - len(failed)}/{len(checks)}")
    if failed:
        raise SystemExit(1)
finally:
    server.terminate()
    try:
        server.wait(timeout=5)
    except Exception:
        server.kill()
