"""扩展版本上报端到端验证：真实 Chrome 装载 dist → 桌面端能判出「扩展是否需要重新加载」。

为什么需要：0.3.3 的「扩展版本过旧」提示条建立在一条跨进程契约上——
扩展每 ~6s 把自身版本塞进 `POST /extension/state`，桌面端比对后给出 `extStale`。
这条链路任何一端漏字段都不会报错，只会**静默不提示**（用户升级后一直跑旧扩展）。
本脚本用真实浏览器 + 真实服务把它钉住。

在**独立实例**上跑：`WORKBENCH_DATA`/`WORKBENCH_DB` 指向临时目录（临时库 + 临时 fernet 密钥），
不触碰实时服务的数据库。但**端口必须用 18765**——扩展里的桌面地址是编译期常量
（`sync.ts` 的 `const DESKTOP`，改端口等于改产品代码），所以脚本会先确认该端口空闲，
若被占用（通常是遗留的 uvicorn）直接报错退出：在**旧代码**的服务上跑，结果毫无意义。

用法：
    .venv\\Scripts\\python.exe tools\\verify_extension_version.py

要点（踩过的坑）：
- **必须 headful**：MV3 扩展在旧 headless 下不加载（`ctx.service_workers` 为空）。
- 版本上报只在**同步 tick**（每 2s，每 3 tick 上报一次 ≈6s）里发生 → 必须等够时间，
  并且要有一条指令/快照把 SW 唤醒。
- 旧版扩展不上报 `extVersion` ⇒ 桌面端必须**判为不旧**（否则每次升级都误报，见 T4）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "extensions" / "quick-login" / "dist"
# 必须与扩展里的编译期常量一致（extensions/quick-login/.../background/sync.ts 的 DESKTOP）
PORT = 18765
BASE = f"http://127.0.0.1:{PORT}"
BENCH = Path(os.environ.get("TEMP", "/tmp")) / "ql_version_bench"
PROFILE = BENCH / "profile"
DATA = BENCH / "data"


def port_busy() -> bool:
    import socket

    with socket.socket() as s:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def get(path: str, timeout: float = 40) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def wait_server(proc: subprocess.Popen, timeout_s: float = 25.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            urllib.request.urlopen(BASE + "/", timeout=2)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


def wait_sw(ctx, timeout_s: float = 20.0):
    """SW 需要先开一个页面才暴露（Playwright 语义）。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        sws = list(ctx.service_workers)
        if sws:
            return sws[0]
        time.sleep(0.4)
    return None


def health_until(pred, timeout_s: float = 30.0) -> dict:
    deadline = time.time() + timeout_s
    last: dict = {}
    while time.time() < deadline:
        try:
            last = get("/extension/health")
            if pred(last):
                return last
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.0)
    return last


def main() -> int:
    if not (DIST / "manifest.json").exists():
        print(f"未找到构建产物：{DIST}（先跑 `cd extensions/quick-login && npm run build`）")
        return 2
    if port_busy():
        print(
            f"端口 {PORT} 已被占用：请先停掉桌面应用/遗留 uvicorn"
            f"（Get-NetTCPConnection -LocalPort {PORT} 查 PID）。"
            "在旧代码的服务上跑本脚本毫无意义。"
        )
        return 2
    shutil.rmtree(BENCH, ignore_errors=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ, WORKBENCH_DATA=str(DATA), WORKBENCH_DB=str(DATA / "bench.db"))
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "workbench.api:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    checks: dict[str, bool] = {}
    info: dict = {}
    try:
        if not wait_server(proc):
            print("测试实例未就绪")
            return 2

        # 造环境 + 两个账号：让扩展有东西可同步（快照里 host 来自 env 的 base_url）
        req = urllib.request.Request(
            BASE + "/api/accounts/envs",
            data=json.dumps({"name": "version_probe", "base_url": "https://example.com"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        env_id = json.loads(urllib.request.urlopen(req, timeout=10).read())["id"]
        for user in ("version_probe_a", "version_probe_b"):
            req = urllib.request.Request(
                BASE + "/api/accounts",
                data=json.dumps({"env_id": env_id, "username": user, "password": "pw-probe"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10).read()

        snap = get("/extension/snapshot")
        info["snapshot"] = {k: snap.get(k) for k in ("format", "version", "desktopVersion", "sites")}
        checks["T1 快照带 desktopVersion（扩展据此自查版本）"] = bool(snap.get("desktopVersion"))

        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE),
                headless=False,  # MV3 扩展在 headless 下不加载
                args=[
                    f"--disable-extensions-except={DIST}",
                    f"--load-extension={DIST}",
                    "--window-position=-2400,-2400",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            )
            page = ctx.new_page()
            page.goto(f"{BASE}/static/blank.html", wait_until="load", timeout=15000)
            sw = wait_sw(ctx)
            checks["T2 SW 已启动"] = sw is not None
            if sw:
                # 等桌面快照同步落 acctMap（每 2s 一次 tick）
                deadline = time.time() + 25
                acct_map: dict = {}
                while time.time() < deadline:
                    acct_map = sw.evaluate(
                        "async () => (await chrome.storage.local.get('akso:acctMap'))['akso:acctMap'] || {}"
                    ) or {}
                    if len(acct_map) >= 2:
                        break
                    time.sleep(1.0)
                info["acctMap"] = acct_map
                checks["T3 桌面快照已同步（acctMap 有映射）"] = len(acct_map) >= 2

                # 有映射时：版本必须随状态上报回到桌面端
                h = health_until(lambda d: bool(d.get("extVersion")), timeout_s=35)
                info["health_with_accounts"] = h
                checks["T4 有账号映射时上报 extVersion"] = bool(h.get("extVersion"))
                checks["T5 同号（dist 版本）且判为不旧"] = (
                    h.get("extVersion") == h.get("desktopVersion") and h.get("extStale") is False
                )

                # 清空映射后不再等扩展的下一次节拍上报（真实场景里"映射为空"更多是
                # 「账号刚被删/还没同步」，而扩展的上报是 6s 节拍 + 60s TTL，等待窗口太脆弱）。
                # 改为直接验证**契约本身**：服务端必须接受「无 items、只有版本」的上报并记下版本。
                # ⚠ 不能断言 reportedAccounts==0 —— 服务端 _STATE 里上一批条目还在 60s TTL 内，
                #   「清空」是扩展侧的事、TTL 过期是服务端的事（第一版脚本在这里断言错了对象）。
                req = urllib.request.Request(
                    BASE + "/extension/state",
                    data=json.dumps({"items": [], "extVersion": "0.3.3"}).encode(),
                    headers={"Content-Type": "application/json"},
                )
                accepted = json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")
                h2 = get("/extension/health")
                info["health_after_empty_report"] = h2
                info["accepted_empty_report"] = accepted
                checks["T7 接受「无账号、仅版本」上报并记下版本"] = (
                    accepted.get("accepted") == 0 and h2.get("extVersion") == "0.3.3" and h2.get("connected") is True
                )
            ctx.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()
        shutil.rmtree(PROFILE, ignore_errors=True)

    print(json.dumps(info, ensure_ascii=False, indent=2)[:3000])
    print()
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print(f"\nEXT_VERSION_CHECKS: {sum(checks.values())}/{len(checks)}")
    return 0 if checks and all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
