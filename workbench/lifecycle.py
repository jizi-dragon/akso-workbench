"""父进程存活监视：**不让 sidecar 变成孤儿**。

## 它解决什么（2026-09-20 实测复现）

桌面壳用 Node 的 `child_process.spawn` 拉起 sidecar，**Windows 下不使用 Job Object**——
所以父进程一旦**非正常死亡**（任务管理器结束、壳自身崩溃），

- `before-quit` 不会触发 → `killServer()` 不会执行 → **sidecar 存活**；
- 实测（`subprocess.Popen` 精确模拟 Electron 的 spawn，语义一致）：

      对父进程执行 taskkill /F（不带 /T）→ 3 秒后 sidecar 仍在、端口仍在 LISTEN

- 后果在**固定端口**设计（[ADR-0012](../../docs/adr/0012-fixed-ports.md)）下被放大：
  孤儿占着 18765 → 下次启动 `portPreflight` 立刻失败 → 用户必须**手工**清理才能再用。
  更糟的是 `portBroker.clearStale()` 只清 `desktop-ports.json` 这个**文件**，清不了**活着的
  进程**；文件一清 `prev` 变 null，弹窗只能说"占用者不是 Workbench 实例"——
  **明明是我们自己的孤儿，却认不出来**（PITFALLS #20 的完整形态）。

## 做法：等父进程的**句柄**，而不是轮询

    壳 spawn 时下发 WORKBENCH_PARENT_PID = 自己的 pid
      → sidecar 起一个守护线程：OpenProcess(SYNCHRONIZE) + WaitForSingleObject(INFINITE)
      → 父进程一结束，等待立刻返回 → 本进程 os._exit(0)

**为什么用句柄等待而不是轮询 pid**：`OpenProcess` 拿到的句柄会**钉住那个进程对象**，
即使它已经退出，句柄依然有效且 `WaitForSingleObject` 会返回 signaled ——
因此**不存在"pid 被复用、误等成别人"的风险**（这是轮询方案最难处理的问题）。
代价是一行 ctypes，零新依赖、零轮询。

## 三条安全约束（防"自杀"）

1. **只在设了 `WORKBENCH_PARENT_PID` 时才启用**——Web 模式（`python -m workbench.main`）
   没有父壳，不能因此被杀；
2. **拿不到句柄时一律"不杀自己"**：若失败原因是 `ERROR_ACCESS_DENIED`（进程存在但打不开），
   记一条 warning 并**停用**监视；只有 `ERROR_INVALID_PARAMETER`（无此进程）才立即退出；
3. **只启动一次**（幂等）——`api.lifespan` 与入口脚本都可能调它。
"""

from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger("workbench.lifecycle")

PARENT_PID_ENV = "WORKBENCH_PARENT_PID"
"""桌面壳 spawn 时下发的父进程 pid。**未设 = 不启用监视**（Web 模式就是这样）。"""

_SYNCHRONIZE = 0x00100000
_INFINITE = 0xFFFFFFFF
_WAIT_OBJECT_0 = 0x00000000
_ERROR_INVALID_PARAMETER = 87

_started = False
_lock = threading.Lock()


def parent_pid() -> int | None:
    """读环境变量里的父进程 pid；未设或非法返回 `None`。"""
    raw = (os.environ.get(PARENT_PID_ENV) or "").strip()
    if not raw:
        return None
    try:
        pid = int(raw)
    except ValueError:
        logger.warning("%s 不是整数（%r），父进程监视已停用", PARENT_PID_ENV, raw)
        return None
    if pid <= 0 or pid == os.getpid():
        logger.warning("%s 的值不合法（%d），父进程监视已停用", PARENT_PID_ENV, pid)
        return None
    return pid


def _die(reason: str) -> None:
    """父进程没了 → 本进程随之退出。**先 flush 日志再硬退出**。"""
    logger.warning("父进程已结束（%s），本 sidecar 随之退出——"
                   "否则会成为孤儿并占着固定端口，导致下次启动失败", reason)
    try:
        logging.shutdown()  # flush + close，保证最后一条日志真的落盘
    except Exception:  # noqa: BLE001 —— 退出路径上绝不抛
        pass
    os._exit(0)


def _watch_windows(pid: int) -> str:
    """Windows：等父进程句柄。返回状态描述（用于日志）。"""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
    if not handle:
        err = ctypes.get_last_error()
        if err == _ERROR_INVALID_PARAMETER:
            # 父进程在我们打开句柄之前就没了（例如壳刚 spawn 完就崩）→ 直接退出
            _die(f"打开句柄失败：无此进程（pid={pid}）")
        # 其余情况（如 ACCESS_DENIED）**不杀自己**——宁可漏一次，也不要误杀健康进程
        return f"停用（OpenProcess 失败，err={err}）"

    def wait() -> None:
        code = kernel32.WaitForSingleObject(handle, _INFINITE)
        kernel32.CloseHandle(handle)
        if code == _WAIT_OBJECT_0:
            _die("句柄已 signaled")
        else:
            logger.warning("等待父进程句柄返回意外码 %s，监视线程退出（不杀本进程）", code)

    threading.Thread(target=wait, name="parent-watch", daemon=True).start()
    return f"已启用（Windows 句柄等待，父 pid={pid}）"


def _watch_posix(pid: int) -> str:
    """POSIX 回退：轮询进程是否存在。产品是 Windows 桌面应用，这条只为可移植性。"""
    import time

    def poll() -> None:
        while True:
            try:
                os.kill(pid, 0)  # 只探存在性，不真发信号
            except ProcessLookupError:
                _die("父进程已不存在（posix 轮询）")
                return
            except PermissionError:
                pass  # 存在但无权限 → 继续
            time.sleep(2.0)

    threading.Thread(target=poll, name="parent-watch", daemon=True).start()
    return f"已启用（POSIX 轮询，父 pid={pid}）"


def watch_parent() -> str:
    """按需启动父进程监视（幂等）。返回状态描述，供启动日志留痕。

    **调用点必须在所有启动路径的交集上**（本仓用 `api.lifespan`）——
    入口脚本只覆盖两条路，而桌面壳的**开发态**是直接 spawn
    `python -m uvicorn workbench.api:app`、不经过任何入口脚本
    （这个教训刚在"启动横幅"上踩过一次）。
    """
    global _started
    with _lock:
        if _started:
            return "已启用（此前已启动）"
        pid = parent_pid()
        if pid is None:
            return "停用（未设 WORKBENCH_PARENT_PID——Web 模式/独立运行属正常情况）"
        _started = True
        try:
            return _watch_windows(pid) if os.name == "nt" else _watch_posix(pid)
        except Exception as exc:  # noqa: BLE001 —— 监视失败绝不阻断启动
            return f"停用（启动监视失败：{type(exc).__name__}: {exc}）"


def reset_for_tests() -> None:
    """仅测试用：复位"已启动"标志（监视线程是 daemon，无法回收，故测试里只测停用分支）。"""
    global _started
    with _lock:
        _started = False
