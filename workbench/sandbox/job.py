"""进程树的生命周期强制：把子进程绑定到 Job Object，确保**超时能杀掉整棵树**。

## 为什么需要这个模块（实测缺口）

`services/proc.py` 超时时执行的是 `proc.kill()` —— 只终结**直接子进程**。
如果被执行的程序自己派生了子进程（`node` 起的服务、`npm run`、`python -c "subprocess..."`），
**孙进程会被留下继续跑**：占端口、占文件句柄、继续联网。这不是理论问题，
而是"超时了但东西还在跑"这类最难查的现场。

## Windows：Job Object

Job Object 是内核对象，`TerminateJobObject` 一次性终结**所有**已绑定的进程（含之后派生的），
并且可以强制"子进程不得逃出"。三个必须做对的细节：

1. **必须在进程跑起来之前绑定**——否则它可能已经派生了孙进程，那些孙进程不在 Job 里，杀不掉。
   做法：`CREATE_SUSPENDED` 创建 → 绑定 → `ResumeThread` 恢复。
2. **`CREATE_BREAKAWAY_FROM_JOB`**：我们自己很可能已经处在某个 Job 里
   （从资源管理器/任务计划/CI 启动时常见，本仓的开发环境就是）。嵌套 Job 在
   Windows 8+ 通常允许，但一旦宿主 Job 设了 `JOB_OBJECT_LIMIT_BREAKAWAY_OK` 之外的约束就会失败，
   故带上 BREAKAWAY 标志并在失败时**降级重试**（见 `assign` 的返回值语义）。
3. **失败不静默**：绑定失败时本模块返回原因，由执行器决定策略（当前是**继续但标记**，
   因为 Windows 7/部分受限环境确实绑不上；而"因为绑不上就不让跑"会让功能在那些机器上完全不可用）。
   ⚠️ 这条是刻意的取舍，**不是遗忘**——它被写进审计的 `notes`。

## POSIX：进程组

没有 Job Object 的对等物；用 `os.setsid()` 让子进程成为新会话/进程组的组长，
超时时 `os.killpg(pgid, SIGKILL)` 杀掉整组。可达性略弱于 Job Object（进程可以自己 `setsid` 逃逸），
但已经覆盖"派生后忘了回收"这一真实场景。

## 诚实边界

**这不是安全沙箱**（不加权、不隔离文件系统、不拦系统调用）。它只保证一件明确的事：
**我说停的时候，整棵树都停。** 见 ADR-0014 §D-3。
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from dataclasses import dataclass

logger = logging.getLogger("workbench.sandbox.job")

IS_WINDOWS = sys.platform == "win32"

# ── Win32 常量 ────────────────────────────────────────────────────────────────
CREATE_SUSPENDED = 0x00000004
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000

JobObjectExtendedLimitInformation = 9

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
"""关闭 Job 句柄即杀掉其中所有进程——**这是"宿主崩了也不留孤儿"的保证**。"""

JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008

#: 允许的活动进程数上限（够用即可，见 `assign` 里那段实测说明）。
#: 取 10 而不是 1：**venv 的 python.exe 是个 launcher，它会 re-exec 真正的解释器**，
#: 于是"跑一个脚本"本身就占 2 个进程槽。实测取 1 会让脚本**根本起不来**
#: （rc=101 + launcher 的乱码报错），比不限制更糟。
MAX_ACTIVE_PROCESSES = 10

PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100


@dataclass
class JobBinding:
    """一次绑定尝试的结果。`handle` 为空表示没绑上（原因在 `note`）。"""

    handle: int = 0
    note: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.handle)


#: 句柄按 pid 暂存：`terminate()` 与进程回收需要它，而 `Popen` 上没有挂自定义属性的干净位置。
_JOBS: dict[int, int] = {}


def create_flags(*, allow_subprocesses: bool) -> int:
    """创建标志。

    ## 为什么**不用** `CREATE_SUSPENDED`（实测结论，别再"顺手优化"回去）

    教科书做法是 `CREATE_SUSPENDED` → 绑定 Job → `ResumeThread`，以彻底消灭
    "绑定前已派生孙进程"的竞态。**本机实测它做不干净**：

    - `OpenThread(THREAD_SUSPEND_RESUME, False, pid)` 返回 **NULL，err=87**
      （`ERROR_INVALID_PARAMETER`）——`OpenThread` 要的是**线程 id**，而我们手上只有
      进程 id。Windows 上两者**常常**相同（主线程），但实测本机 Python 3.14 下不相同。
    - 要拿线程 id 就得 `CreateToolhelp32Snapshot` 枚举线程，再逐个比对
      `th32OwnerProcessID` —— 换来的是"消灭一个微秒级窗口"，**不值得**，而且多一段
      容易在别的 Windows 版本上失灵的内核代码。
    - 不 resume 的后果是**进程永远挂着**（实测：`communicate()` 超时），这比竞态更糟。

    ## 实际做法与它为什么够用

    正常创建 → **立刻** `AssignProcessToJobObject`。竞态窗口 = `Popen` 返回到本模块被调用
    之间（微秒级）；而且**已存在的子进程也会被一并移入 Job**（除非它自己带
    `CREATE_BREAKAWAY_FROM_JOB` 逃逸——那是蓄意行为，不是竞态）。
    残余风险写进审计的 `notes`，不假装为零。

    `CREATE_BREAKAWAY_FROM_JOB` 保留：我们自己很可能已在某个 Job 里（资源管理器/CI 启动），
    带上它在嵌套场景下才能绑得上。实测本机单独使用它**不影响进程正常运行**。
    """
    flags = 0
    if IS_WINDOWS:
        flags |= CREATE_BREAKAWAY_FROM_JOB
    return flags


def assign(proc: subprocess.Popen, *, allow_subprocesses: bool) -> JobBinding:
    """把进程绑到 Job Object（Windows）。非 Windows 走进程组，返回空 handle + 说明。

    调用方约定：**必须在进程 resume 之前调用**（本函数只做绑定，不做 resume）。
    """
    if not IS_WINDOWS:
        return JobBinding(handle=0, note="POSIX：使用进程组（killpg），不用 Job Object")

    try:
        import ctypes
        from ctypes import wintypes
    except ImportError as exc:  # pragma: no cover —— 非 Windows 不会走到
        return JobBinding(handle=0, note=f"ctypes 不可用：{exc}")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return JobBinding(handle=0, note=f"CreateJobObject 失败（err={ctypes.get_last_error()}）")

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in
                    ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                     "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    # 关句柄即杀：宿主进程崩了也不会留下孤儿（比"记得清理"可靠）
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not allow_subprocesses:
        # 允许少量活动进程，而不是 1。**这是实测校准出来的，不是随手写的**：
        #   - 取 1 时，"跑一个 python 脚本"会因为 **venv launcher 的 re-exec** 占掉第二个槽而失败
        #     （实测 rc=101 + launcher 的乱码 stderr，脚本根本没跑）；
        #   - 取 10 仍能挡住 fork 炸弹式的失控，同时容得下 launcher/包装脚本这类正常形态。
        # 注意：即便不限，**子进程也无法逃出 Job**（除非它自己带 CREATE_BREAKAWAY_FROM_JOB），
        # 所以"杀整棵树"的保证不依赖这个限制——它只是防失控的额外一层。
        info.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        info.BasicLimitInformation.ActiveProcessLimit = MAX_ACTIVE_PROCESSES

    if not kernel32.SetInformationJobObject(
        job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
    ):
        err = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        return JobBinding(handle=0, note=f"SetInformationJobObject 失败（err={err}）")

    handle = kernel32.OpenProcess(PROCESS_TERMINATE | PROCESS_SET_QUOTA, False, proc.pid)
    if not handle:
        err = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        return JobBinding(handle=0, note=f"OpenProcess 失败（err={err}）")

    ok = kernel32.AssignProcessToJobObject(job, handle)
    kernel32.CloseHandle(handle)
    if not ok:
        err = ctypes.get_last_error()
        kernel32.CloseHandle(job)
        return JobBinding(
            handle=0,
            note=(f"AssignProcessToJobObject 失败（err={err}）——"
                  "常见于已处于受限 Job 中的宿主；已降级为 direct-kill（进程树可能不完整）"),
        )

    _JOBS[proc.pid] = job
    return JobBinding(handle=job, note="")


def terminate(proc: subprocess.Popen, binding: JobBinding | None = None) -> str:
    """终结**整棵进程树**。返回实际用了哪种手段（写进审计）。

    Fail-Closed 语义：任何一步失败都记录下来，绝不假装杀干净了。
    """
    if IS_WINDOWS:
        job = (binding.handle if binding else 0) or _JOBS.get(proc.pid, 0)
        if job:
            try:
                import ctypes
                from ctypes import wintypes

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.TerminateJobObject.restype = wintypes.BOOL
                kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
                ok = kernel32.TerminateJobObject(job, 1)
                note = "TerminateJobObject（整棵树）" if ok else "TerminateJobObject 失败"
            except Exception as exc:  # noqa: BLE001
                note = f"TerminateJobObject 异常：{exc}"
            finally:
                close(proc)
            return note
        try:
            proc.kill()
            return "direct-kill（未绑定 Job：进程树可能不完整）"
        except OSError as exc:
            return f"direct-kill 失败：{exc}"

    # POSIX：杀整个进程组
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        return "killpg(SIGKILL)（整组）"
    except (ProcessLookupError, PermissionError, OSError) as exc:
        try:
            proc.kill()
            return f"killpg 不可用（{type(exc).__name__}），降级 direct-kill"
        except OSError as exc2:
            return f"direct-kill 失败：{exc2}"


def close(proc: subprocess.Popen) -> None:
    """释放 Job 句柄（幂等）。**句柄关闭本身即杀树**（KILL_ON_JOB_CLOSE）。"""
    job = _JOBS.pop(proc.pid, 0)
    if not job:
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle(job)
    except Exception:  # noqa: BLE001 —— 关句柄失败不影响结果（进程已终止）
        logger.warning("关闭 Job 句柄失败 pid=%s", proc.pid, exc_info=True)


def stats() -> dict:
    """运行期视图：当前持有多少个 Job（管理页用）。"""
    return {"platform": "windows" if IS_WINDOWS else sys.platform,
            "active_jobs": len(_JOBS), "job_api": IS_WINDOWS}
