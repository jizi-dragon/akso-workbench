"""更新链路端到端验证（离线自测）：检查 → 下载 → 就绪 → 退出时安装，几十秒跑完。

为什么需要：真机验证这条链要下 350MB 安装包（本机上行/下行都不快），成本高到没人会做；
而它恰恰是最容易"看着对、实际断"的地方（0.3.3 实测就是：能发现新版却下不动）。
做法：起一个本地静态服务冒充更新源（`latest.yml` + 一个极小的假安装包，体积与真实包无关，
因为这里验证的是**流程**不是内容），用 `AKSO_UPDATE_OVERRIDE` 把打包好的应用指过去，
再读它落盘的 `shell-state.json` 断言相位推进。

用**打包产物**跑（win-unpacked），因为 electron-updater 只在打包态工作（需要 app-update.yml）。

用法：
    .venv\\Scripts\\python.exe tools\\verify_update_flow.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
UNPACKED = ROOT / "desktop" / "dist" / "win-unpacked"
EXE = UNPACKED / "AksoWorkbench.exe"
BENCH = Path(os.environ.get("TEMP", "/tmp")) / "akso_update_bench"
SERVE = BENCH / "serve"
PORT = 18999
BASE = f"http://127.0.0.1:{PORT}"

# 壳状态文件：壳把 updater 状态写在 %APPDATA%\AksoWorkbench\shell-state.json
DATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "AksoWorkbench"
STATE = DATA_DIR / "shell-state.json"


def project_version() -> str:
    import re

    m = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else "0.0.0"


def bump_patch(v: str) -> str:
    a, b, c = (int(x) for x in v.split("."))
    return f"{a}.{b}.{c + 1}"


def make_fake_installer(path: Path) -> tuple[str, int]:
    """假安装包：内容随机（保证 sha512 稳定且不等于现有文件），体积刻意很小。"""
    payload = os.urandom(256 * 1024)
    path.write_bytes(payload)
    digest = base64.b64encode(hashlib.sha512(payload).digest()).decode()
    return digest, len(payload)


def serve() -> ThreadingHTTPServer:
    handler = lambda *a, **k: SimpleHTTPRequestHandler(*a, directory=str(SERVE), **k)  # noqa: E731
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def read_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def wait_phase(pred, timeout_s: float, label: str) -> dict:
    deadline = time.time() + timeout_s
    last: dict = {}
    while time.time() < deadline:
        last = read_state()
        if pred(last):
            return last
        time.sleep(1.0)
    print(f"  （等待 {label} 超时，最后状态：phase={last.get('phase')} err={last.get('lastError')}）")
    return last


def main() -> int:
    if not EXE.exists():
        print(f"未找到打包产物：{EXE}（先跑 tools\\build.ps1 -Bump none）")
        return 2

    version = project_version()
    new_version = bump_patch(version)
    installer_name = f"AksoWorkbench-{new_version}-setup.exe"

    SERVE.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(SERVE / "old", ignore_errors=True)
    digest, size = make_fake_installer(SERVE / installer_name)
    (SERVE / "latest.yml").write_text(
        "version: {v}\nfiles:\n  - url: {n}\n    sha512: {d}\n    size: {s}\n"
        "path: {n}\nsha512: {d}\nreleaseDate: '{t}'\n".format(
            v=new_version, n=installer_name, d=digest, s=size,
            t=time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        ),
        encoding="utf-8",
    )
    print(f"假更新源：{BASE}/latest.yml → v{new_version}（当前 v{version}，包 {size} 字节）")

    httpd = serve()
    env = dict(os.environ, AKSO_UPDATE_OVERRIDE=BASE)
    checks: dict[str, bool] = {}
    proc = None
    try:
        proc = subprocess.Popen(
            [str(EXE)],
            env=env,
            cwd=str(UNPACKED),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # 启动后 20s 才首查（FIRST_CHECK_DELAY_MS），这里直接等相位推进
        found = wait_phase(lambda s: s.get("phase") in {"downloading", "ready"}, 75, "update-available")
        checks["U1 检查发现新版（phase=downloading/ready）"] = found.get("phase") in {"downloading", "ready"}
        checks["U2 availableVersion == 假更新源版本"] = found.get("availableVersion") == new_version
        checks["U3 通道信息已落盘（proxy 字段存在）"] = isinstance(found.get("proxy"), dict)

        ready = wait_phase(lambda s: s.get("phase") == "ready", 90, "update-downloaded")
        checks["U4 下载完成进入 ready"] = ready.get("phase") == "ready"
        checks["U5 ready 时 percent=100"] = ready.get("percent") == 100
        checks["U6 updatePending=true（UI 会提示退出时安装）"] = ready.get("updatePending") is True
        checks["U7 lastError 为空"] = not ready.get("lastError")
    finally:
        if proc and proc.poll() is None:
            # 正常退出（不是 kill）：让壳走 before-quit → killServer → installOnExit 这条真实路径
            subprocess.run(["taskkill", "/PID", str(proc.pid)], capture_output=True)
            try:
                proc.wait(timeout=20)
            except Exception:  # noqa: BLE001
                proc.kill()
        httpd.shutdown()
        shutil.rmtree(SERVE, ignore_errors=True)

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print(f"\nUPDATE_FLOW_CHECKS: {sum(checks.values())}/{len(checks)}")
    return 0 if checks and all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
