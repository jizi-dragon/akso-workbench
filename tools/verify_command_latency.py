"""指令下发延迟基准（隔离实例）：锁住「桌面点击 → 浏览器打开」这条链路的延迟契约。

背景（实测）：旧实现由扩展每 2s 轮询 `/extension/commands`，指令平均要等 ~1s、最坏 ~2s，
是用户实感「要等一两秒」的主项。改为**长轮询**（服务端无指令时挂起请求，入队即唤醒）后
平均降到 ~20ms。本脚本用「旧节拍 vs 长轮询」A/B 把该契约钉住——若有人误删 `notify_all`
或 `wait` 参数，这里会立刻红。

在**独立实例**上跑（独立端口 + 临时库 WORKBENCH_DB），不触碰实时服务与真实扩展。

用法：
    .venv\\Scripts\\python.exe tools\\verify_command_latency.py
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("QL_BENCH_PORT", "18801"))
BASE = f"http://127.0.0.1:{PORT}"
BENCH = Path(os.environ.get("TEMP", "/tmp")) / "ql_lat_bench"
DB = BENCH / "bench.db"


def post(path: str, body: dict | None = None) -> tuple[dict, float]:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body or {}).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    return data, (time.perf_counter() - t0) * 1000


def get(path: str) -> tuple[dict, float]:
    t0 = time.perf_counter()
    with urllib.request.urlopen(BASE + path, timeout=40) as r:
        data = json.loads(r.read())
    return data, (time.perf_counter() - t0) * 1000


def run_phase(name: str, wait: float, n: int = 6) -> list[float]:
    """模拟扩展侧消费者：wait=0 每 2s 轮询一次（旧实现），wait>0 长轮询（新实现）。

    计时用「发送时刻 FIFO」而非 seq→时刻 字典：长轮询会在 POST 尚未返回时就把指令交给
    消费者，字典写法会漏计（实测漏掉 2/6）。
    """
    lat: list[float] = []
    sends: deque[float] = deque()
    lock = threading.Lock()
    stop = threading.Event()

    def poller() -> None:
        cursor = 0
        while not stop.is_set():
            try:
                data, _ = get(f"/extension/commands?after={cursor}" + (f"&wait={wait}" if wait else ""))
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
                continue
            picked = time.perf_counter()
            for c in data.get("commands") or []:
                with lock:
                    t_enq = sends.popleft() if sends else None
                if t_enq is not None:
                    lat.append((picked - t_enq) * 1000)
                cursor = max(cursor, int(c["seq"]))
            if cursor:
                try:
                    post("/extension/ack", {"seqs": [cursor]})
                except Exception:  # noqa: BLE001
                    pass
            if not wait:
                time.sleep(2.0)  # 旧实现节拍：每 2s 轮询一次（消费后同样等下一拍）

    th = threading.Thread(target=poller, daemon=True)
    th.start()
    for _ in range(n):
        time.sleep(0.4 if wait else 2.6)  # 错开，贴近真实点击的到达时刻
        with lock:
            sends.append(time.perf_counter())
        post("/extension/commands", {"type": "wheel.toggle", "payload": {}})
    deadline = time.time() + 15
    while len(lat) < n and time.time() < deadline:
        time.sleep(0.02)
    stop.set()
    th.join(timeout=3)
    print(f"{name}: n={len(lat)}  平均={statistics.mean(lat):.1f}ms  最大={max(lat):.1f}ms  最小={min(lat):.1f}ms")
    return lat


def main() -> int:
    BENCH.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()
    env = dict(os.environ, WORKBENCH_DB=str(DB))
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "workbench.api:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(BASE + "/", timeout=2)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.3)
        else:
            print("测试实例未就绪")
            return 2

        _, t_default = get("/extension/commands?after=0")
        data_to, t_timeout = get("/extension/commands?after=0&wait=1")
        print(f"兼容 wait=0 立即返回: {t_default:.1f}ms   超时路径 wait=1: {t_timeout:.0f}ms（空={not data_to['commands']}）")
        print()
        old = run_phase("【旧】每 2s 轮询", wait=0)
        new = run_phase("【新】长轮询 wait=15", wait=15)

        checks = {
            "C1 默认 wait=0 非阻塞（<100ms，向后兼容）": t_default < 100,
            "C2 wait=1 超时路径按约 1s 返回空": 900 <= t_timeout <= 1600 and not data_to["commands"],
            "C3 旧节拍复现 0~2000ms 量化延迟": statistics.mean(old) > 700,
            "C4 长轮询平均延迟 < 50ms": statistics.mean(new) < 50,
            "C5 长轮询最大延迟 < 150ms": max(new) < 150,
        }
        print()
        for name, ok in checks.items():
            print(f"{'PASS' if ok else 'FAIL'}  {name}")
        speedup = statistics.mean(old) / max(statistics.mean(new), 0.001)
        print(f"\nLATENCY_CHECKS: {sum(checks.values())}/{len(checks)}   加速比 ≈ {speedup:.0f}×")
        return 0 if all(checks.values()) else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
