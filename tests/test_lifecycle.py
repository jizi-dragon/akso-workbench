"""父进程监视验收：sidecar 不该在壳非正常死亡后变成孤儿。

## 缺陷原状（2026-09-20 实测复现）

桌面壳用 Node 的 `child_process.spawn` 拉起 sidecar，**Windows 下不使用 Job Object**，
所以父进程**非正常死亡**（任务管理器结束 / 壳崩溃）时 `before-quit` 不触发
→ `killServer()` 不执行 → **sidecar 存活并占着 18765**。实测：

    对父进程执行 taskkill /F（不带 /T）→ 3 秒后 sidecar 仍在、端口仍在 LISTEN

在**固定端口**设计（ADR-0012）下后果被放大：下次启动 `portPreflight` 直接失败，
而 `portBroker.clearStale()` 只清公布文件、清不了活着的进程 → 用户必须手工清理。

## 本文件的两层

- **快层**（默认跑）：只验"什么时候**不**启用"——因为这些分支不启线程、不会误杀本进程。
  真正启用了监视的分支**不能在测试进程内测**（它会 `os._exit`，把 pytest 一起带走）。
- **慢层**（`-m slow`）：真起一对父子进程，强杀父进程，断言 sidecar 自行退出。
  这是本文件的核心断言，也是 `tools/verify_regression_tests.py` 的反证对象。
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import subprocess
import sys
import time

import pytest

from workbench import lifecycle

# ---------------------------------------------------------------- 快层：停用分支

def test_disabled_without_env(monkeypatch) -> None:
    """未设 `WORKBENCH_PARENT_PID` 时**不启用**——Web 模式与独立运行都靠这条。"""
    monkeypatch.delenv(lifecycle.PARENT_PID_ENV, raising=False)
    lifecycle.reset_for_tests()
    status = lifecycle.watch_parent()
    assert "停用" in status and "未设" in status, status


@pytest.mark.parametrize("value", ["abc", "12.5", "", "  ", "-3", "0"])
def test_disabled_with_invalid_value(monkeypatch, value: str) -> None:
    """非法值一律**停用而不是报错**——启动路径上不能因为一个环境变量而挂掉。"""
    monkeypatch.setenv(lifecycle.PARENT_PID_ENV, value)
    lifecycle.reset_for_tests()
    assert "停用" in lifecycle.watch_parent()


def test_disabled_when_pid_is_ourselves(monkeypatch) -> None:
    """把自己的 pid 当父进程 → 停用。否则会**等自己退出**（永远等不到，白占一个线程）。"""
    monkeypatch.setenv(lifecycle.PARENT_PID_ENV, str(os.getpid()))
    lifecycle.reset_for_tests()
    assert "停用" in lifecycle.watch_parent()


def test_parent_pid_parsing(monkeypatch) -> None:
    monkeypatch.setenv(lifecycle.PARENT_PID_ENV, "4321")
    assert lifecycle.parent_pid() == 4321
    monkeypatch.setenv(lifecycle.PARENT_PID_ENV, " 4321 ")
    assert lifecycle.parent_pid() == 4321, "两侧空白应被容忍"
    monkeypatch.delenv(lifecycle.PARENT_PID_ENV, raising=False)
    assert lifecycle.parent_pid() is None


def test_watch_is_idempotent(monkeypatch) -> None:
    """重复调用只启动一次（`api.lifespan` 与入口脚本都可能调它）。

    这里把平台实现替换掉——**真正启用的分支不能在测试进程内跑**（它会 `os._exit`）。
    """
    calls: list[int] = []
    monkeypatch.setattr(lifecycle, "_watch_windows", lambda pid: calls.append(pid) or "fake")
    monkeypatch.setattr(lifecycle, "_watch_posix", lambda pid: calls.append(pid) or "fake")
    monkeypatch.setenv(lifecycle.PARENT_PID_ENV, "4321")
    lifecycle.reset_for_tests()

    assert lifecycle.watch_parent() == "fake"
    assert lifecycle.watch_parent() == "已启用（此前已启动）"
    assert calls == [4321], f"监视线程被启动了多次：{calls}"


def test_failed_watch_does_not_raise(monkeypatch) -> None:
    """监视启动失败也必须**返回状态而不是抛**（启动路径上的硬要求）。"""
    def boom(_pid: int) -> str:
        raise OSError("没有这个 API")

    monkeypatch.setattr(lifecycle, "_watch_windows", boom)
    monkeypatch.setattr(lifecycle, "_watch_posix", boom)
    monkeypatch.setenv(lifecycle.PARENT_PID_ENV, "4321")
    lifecycle.reset_for_tests()
    status = lifecycle.watch_parent()
    assert "停用" in status and "OSError" in status, status


# ---------------------------------------------------------------- 慢层：真实端到端

#: 中间进程（扮演桌面壳）。写成常量而不是单独提交一个文件——
#: 它只服务于这一条断言，独立成文件反而多一个需要维护的入口。
_MIDDLE_SOURCE = '''
import json, os, pathlib, subprocess, sys, time
port, report = sys.argv[1], pathlib.Path(sys.argv[2])
repo = pathlib.Path(sys.argv[3])
env = {
    **os.environ,
    "WORKBENCH_DATA": str(report.parent / "data"),
    "WORKBENCH_LOG_DIR": str(report.parent / "logs"),
    "WORKBENCH_PORT": port,
    "WORKBENCH_PARENT_PID": str(os.getpid()),   # ← 与 Tauri 壳下发的是同一个东西
    "PYTHONIOENCODING": "utf-8",
}
stdio = (report.parent / "stdio.log").open("w", encoding="utf-8")
child = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "workbench.api:app",
     "--host", "127.0.0.1", "--port", port],
    cwd=str(repo), env=env, stdout=stdio, stderr=subprocess.STDOUT,
)
report.write_text(json.dumps({"parent": os.getpid(), "child": child.pid}),
                  encoding="utf-8")
time.sleep(600)
'''

PORT = 18797
REPO = pathlib.Path(__file__).resolve().parent.parent


def _port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _pid_alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                         capture_output=True, text=True, errors="replace").stdout
    return str(pid) in out


@pytest.mark.slow
@pytest.mark.skipif(os.name != "nt", reason="taskkill / tasklist 是 Windows 语义")
def test_sidecar_exits_when_parent_is_force_killed(tmp_path) -> None:
    """**核心断言**：父进程被强杀（不带 `/T`）后，sidecar 必须自行退出并释放端口。

    这条断言在"没有父进程监视"的实现上必然 FAIL——它正是 PITFALLS #20 的根治判据。
    """
    assert not _port_open(PORT), f"端口 {PORT} 已被占用，换个端口再跑"

    middle_src = tmp_path / "middle.py"
    middle_src.write_text(_MIDDLE_SOURCE, encoding="utf-8")
    report = tmp_path / "pids.json"

    middle = subprocess.Popen([sys.executable, str(middle_src), str(PORT), str(report), str(REPO)],
                              cwd=str(REPO), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pids: dict = {}
    try:
        deadline = time.time() + 20
        while time.time() < deadline and not report.exists():
            time.sleep(0.2)
        assert report.exists(), "中间进程没写出 pid 文件，实验前提不成立"
        pids = json.loads(report.read_text(encoding="utf-8"))

        deadline = time.time() + 60
        while time.time() < deadline and not _port_open(PORT):
            assert middle.poll() is None, "中间进程提前退出"
            time.sleep(0.5)
        assert _port_open(PORT), "sidecar 没能起服，实验无法继续"

        # 只杀父进程，**不带 /T** —— 精确模拟"任务管理器结束壳"或壳崩溃
        subprocess.run(["taskkill", "/F", "/PID", str(pids["parent"])],
                       capture_output=True, text=True)

        # sidecar 应当**自行**发现父进程已死并退出（给足 25s 余量）
        deadline = time.time() + 25
        while time.time() < deadline and (_pid_alive(pids["child"]) or _port_open(PORT)):
            time.sleep(0.5)

        assert not _pid_alive(pids["child"]), (
            f"父进程已被强杀，但 sidecar（pid={pids['child']}）仍在运行——"
            "它就是占着固定端口、让下次启动失败的孤儿")
        assert not _port_open(PORT), f"sidecar 退了但端口 {PORT} 仍被占着"
    finally:
        if pids.get("child") and _pid_alive(pids["child"]):
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pids["child"])],
                           capture_output=True, text=True)
        if middle.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(middle.pid)],
                           capture_output=True, text=True)
        time.sleep(0.5)
