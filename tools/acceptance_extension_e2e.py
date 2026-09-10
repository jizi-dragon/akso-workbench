"""quick-login 扩展真机验收（隔离 Chrome 全链路）：

  服务端 → 隔离 Chrome 装载扩展 dist → SW 同步快照 → par.open 指令
  → 打开平台登录页 → 自动填表提交 → 登录成功证据采集

通过标准：
  A1 扩展 SW 启动且 akso:acctMap 覆盖快照全部账号（数据面通）
  A2 par.open 消费后新页签打开目标平台（指令面通）
  A3 登录页出现自动填表证据，且 URL 离开 /login 或捕获到身份 token（执行面通）

用法：.venv\\Scripts\\python.exe tools\\acceptance_extension_e2e.py [账号用户名]
不传用户名默认取快照第一个账号。隔离 profile 位于 %TEMP%，不触碰真实浏览器数据。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT_DIST = ROOT / "extensions" / "quick-login" / "dist"
BASE = "http://127.0.0.1:18765"


def wait_server(proc: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE + "/", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.4)
    proc.kill()
    raise SystemExit("服务端未就绪")


def http_json(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def main() -> int:
    # GBK 控制台兜底：✔/✘/中文需要 UTF-8 输出
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    username_filter = sys.argv[1] if len(sys.argv) > 1 else None

    # ── 服务端 ─────────────────────────────────────────────
    server = subprocess.Popen(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
         "workbench.api:app", "--host", "127.0.0.1", "--port", "18765"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    wait_server(server)
    snap = http_json("GET", "/extension/snapshot")
    accounts = snap["accounts"]
    if username_filter:
        accounts = [a for a in accounts if a["username"] == username_filter]
    if not accounts:
        server.kill()
        raise SystemExit(f"快照无可用账号（filter={username_filter}）")
    target = accounts[0]
    print(f"[setup] 目标账号: {target['username']} @ {target['host']} ({target['scheme']})")

    # ── 隔离 Chrome + 扩展 ────────────────────────────────
    from playwright.sync_api import sync_playwright

    passed: list[str] = []
    failed: list[str] = []
    with sync_playwright() as p:
        import tempfile
        profile = Path(tempfile.mkdtemp(prefix="ql-e2e-"))
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,
            args=[
                f"--disable-extensions-except={EXT_DIST}",
                f"--load-extension={EXT_DIST}",
                "--no-first-run",
            ],
        )
        try:
            # A1: SW 启动 + 快照同步
            # Playwright 1.62 实测：service_workers 列表需页面活动后才暴露目标——
            # 先开一个页面，必要时经 chrome://extensions 触发内部目标枚举
            probe = ctx.new_page()
            sw = None
            for _ in range(40):
                sws = ctx.service_workers
                if sws:
                    sw = sws[0]
                    break
                time.sleep(0.3)
            if not sw:
                try:
                    probe.goto("chrome://extensions", timeout=15000)
                    time.sleep(2)
                    sws = ctx.service_workers
                    if sws:
                        sw = sws[0]
                except Exception:
                    pass
            if not sw:
                failed.append("A1 扩展 Service Worker 未启动")
            else:
                time.sleep(6)  # 等 2-3 个同步 tick
                state = sw.evaluate(
                    "() => chrome.storage.local.get(['akso:acctMap','akso:snapshotId'])"
                )
                acct_map = state.get("akso:acctMap") or {}
                total = len(http_json("GET", "/extension/snapshot")["accounts"])
                if len(acct_map) >= max(total, 1):
                    passed.append(f"A1 数据面通：acctMap {len(acct_map)}/{total} 账号已同步")
                else:
                    failed.append(f"A1 acctMap 仅 {len(acct_map)}/{total}（快照同步不完整）")

                # A2: par.open 指令消费（逐秒追踪 cursor 与页签）
                before = {pg.url for pg in ctx.pages}
                resp = http_json("POST", "/extension/commands",
                                 {"type": "par.open", "payload": {"accountId": target["desktopId"]}})
                print(f"[a2] 已投递 seq={resp.get('seq')}，before={before}")
                opened = None
                deadline = time.time() + 20
                while time.time() < deadline and not opened:
                    time.sleep(1)
                    cur_sw = ctx.service_workers[0] if ctx.service_workers else None
                    cur = cur_sw.evaluate(
                        "() => chrome.storage.local.get(['akso:cmdCursor'])"
                    ) if cur_sw else {}
                    urls = [pg.url[:60] for pg in ctx.pages]
                    print(f"[a2] t+{int(20 - (deadline - time.time()))}s cursor={cur.get('akso:cmdCursor')} pages={urls}")
                    for pg in ctx.pages:
                        if pg.url not in before and target["host"] in pg.url:
                            opened = pg
                            break
                if opened:
                    passed.append(f"A2 指令面通：新页签打开 {opened.url[:70]}")
                else:
                    failed.append("A2 par.open 后 20s 内未出现目标平台页签")

                # A3: 自动登录执行证据
                if opened:
                    deadline = time.time() + 50
                    token_seen = False
                    left_login = False
                    while time.time() < deadline:
                        try:
                            if opened.is_closed():
                                break
                            cur = opened.url
                            if "/login" not in cur:
                                left_login = True
                                break
                            token_seen = opened.evaluate(
                                """() => {
                                  for (let i = 0; i < localStorage.length; i++) {
                                    const k = localStorage.key(i);
                                    if (k && (k.includes('auth_token') || k.startsWith('__ql_ns_'))) {
                                      const v = k.startsWith('__ql_ns_')
                                        ? localStorage.getItem(k) : localStorage.getItem(k);
                                      if (v && v.includes('auth_token')) return true;
                                    }
                                  }
                                  return false;
                                }"""
                            )
                        except Exception:
                            pass
                        time.sleep(1.0)
                    if left_login:
                        passed.append(f"A3 执行面通：URL 已离开 /login（当前 {cur[:60]}）")
                    elif token_seen:
                        passed.append("A3 执行面通：捕获到身份 token 痕迹")
                    else:
                        failed.append(f"A3 50s 内未观察到登录成功证据（URL 仍 {opened.url[:60]}）")
                        # 取证：v3.12.2 黑匣子（逐事件填表记录）——失败现场可分析
                        try:
                            dump = sw.evaluate(
                                "() => { const o = chrome.storage.local.get('ql:diag'); return (o['ql:diag'] || []).slice(-40); }"
                            )
                            for line in (dump or [])[-15:]:
                                print(f"    [diag] {str(line)[:150]}")
                        except Exception as de:
                            print(f"    [diag] 黑匣子读取失败: {de}")
        finally:
            ctx.close()

    server.kill()

    print("\n===== 验收结果 =====")
    for item in passed:
        print(f"✔ {item}")
    for item in failed:
        print(f"✘ {item}")
    if failed:
        return 1
    print("E2E_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
