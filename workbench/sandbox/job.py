"""进程树的生命周期强制：把子进程绑定到 Job Object，确保**超时能杀掉整棵树**。

## 为什么需要这个模块（实测缺口）

`services/proc.py` 超时时执行的是 `proc.kill()` —— 只终结**直接子进程**。
如果被执行的程序自己派生了子进程（`node` 起的服务、`npm run`、`python -c "subprocess..."`），
**孙进程会被留下继续跑**：占端口、占文件句柄、继续联网。这不是理论问题，
而是"超时了但东西还在跑"这类最难查的现场。

## Windows：Job Object

Job Object 是内核对象，`TerminateJobObject` 一次性终结**所有**已绑定的进程（含之后派生的），
并且可以强制"子进程不得逃出"。三个必须做对的细节：

1. **绑定发生在进程跑起来之前**：Windows 上 `Popen` 带 `CREATE_SUSPENDED`，先
   `AssignProcessToJobObject`，再用 `ntdll.NtResumeProcess` 恢复。这样"绑定前已派生孙进程"
   的竞态在结构上不存在（根进程在绑定完成前根本没跑）。Win32 API 的导入/初始化放在模块
   导入期，避免把冷启动的十几毫秒塞进绑定窗口。
2. **绑定后仍要校验、并补绑已存在的后代**（纵深防御）：Windows **不会**在绑定父进程时
   追溯收编已经存在的子进程。所以绑定后枚举 Toolhelp32 快照，把已存在的后代
   逐个 `AssignProcessToJobObject`，再用 `IsProcessInJob` 校验；校验结果写进审计。
   ⚠️ 在宿主的嵌套 Job 里，补绑可能返回 `ACCESS_DENIED`——这时 `verified=False`，
   由 `taskkill /T /F` 兜底，并在审计里如实写明。
3. **失败不静默**：绑定失败时本模块返回原因，由执行器决定策略（当前是**继续但标记**，
   因为部分受限环境确实绑不上；而"因为绑不上就不让跑"会让功能在那些机器上完全不可用）。
   ⚠️ 这条是刻意的取舍，**不是遗忘**——它被写进审计的 `notes`。

## POSIX：进程组

没有 Job Object 的对等物；用 `os.setsid()` 让子进程成为新会话/进程组的组长，
超时时 `os.killpg(pgid, SIGKILL)` 杀掉整组。可达性略弱于 Job Object（进程可以自己 `setsid` 逃逸），
但已经覆盖"派生后忘了回收"这一真实场景。

## 诚实边界

**这不是安全沙箱**（不加权、不隔离文件系统、不拦系统调用）。它只保证一件明确的事：
**我说停的时候，整棵树都停**。Windows 上"整棵树"的保证依赖绑定 + 校验 + `taskkill /T`
兜底；三者都失败时会如实写进 `tree_kill`，不假装杀干净了。
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger("workbench.sandbox.job")

IS_WINDOWS = sys.platform == "win32"

# ── Win32 常量 ────────────────────────────────────────────────────────────────
CREATE_SUSPENDED = 0x00000004
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000

TH32CS_SNAPPROCESS = 0x00000002

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
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_SUSPEND_RESUME = 0x0800

TH32CS_SNAPTHREAD = 0x00000004
THREAD_SUSPEND_RESUME = 0x0002

_INVALID_HANDLE_VALUE = -1


@dataclass
class JobBinding:
    """一次绑定尝试的结果。`handle` 为空表示没绑上（原因在 `note`）。"""

    handle: int = 0
    note: str = ""
    verified: bool = False
    """是否用 `IsProcessInJob` 校验过**已绑定的每一个后代**。"""
    bound_pids: tuple[int, ...] = ()

    @property
    def ok(self) -> bool:
        return bool(self.handle)


#: 句柄按 pid 暂存：`terminate()` 与进程回收需要它，而 `Popen` 上没有挂自定义属性的干净位置。
_JOBS: dict[int, int] = {}
_JOBS_LOCK = threading.Lock()


class _Win32:
    """Win32 API 句柄与结构体（**模块导入期**初始化，避免绑定窗口被 import 拖长）。

    只在 Windows 上构造；构造失败时 `_WIN` 为 None，`assign()` 返回带原因的降级结果
    （服务仍可启动，只是没有 Job Object）。
    """

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wintypes = wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k = k

        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k.SetInformationJobObject.restype = wintypes.BOOL
        k.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        k.AssignProcessToJobObject.restype = wintypes.BOOL
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.TerminateJobObject.restype = wintypes.BOOL
        k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.CloseHandle.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.IsProcessInJob.restype = wintypes.BOOL
        k.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE,
                                     ctypes.POINTER(wintypes.BOOL)]
        k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]

        # 结构体只建一次：argtypes 与实际传给 byref() 的类必须是同一个，
        # 否则 ctypes 会报 "expected LP_PROCESSENTRY32W instance instead of pointer"。
        self.IO_COUNTERS = self._io_counters(ctypes, wintypes)
        self.JOBOBJECT_BASIC_LIMIT_INFORMATION = self._basic_limit(ctypes, wintypes)
        self.JOBOBJECT_EXTENDED_LIMIT_INFORMATION = self._extended_limit(ctypes, wintypes)
        self.PROCESSENTRY32W = self._process_entry_type(ctypes, wintypes)

        k.Process32FirstW.restype = wintypes.BOOL
        k.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(self.PROCESSENTRY32W)]
        k.Process32NextW.restype = wintypes.BOOL
        k.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(self.PROCESSENTRY32W)]

        # 恢复挂起的进程：NtResumeProcess 收的是**进程句柄**，绕开了
        # `OpenThread(pid)` 要线程 id 的问题（旧实现因此放弃 CREATE_SUSPENDED）。
        self.ntdll = None
        try:
            ntdll = ctypes.WinDLL("ntdll")
            ntdll.NtResumeProcess.restype = ctypes.c_long
            ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
            self.ntdll = ntdll
        except OSError:
            logger.warning("ntdll/NtResumeProcess 不可用，将退回 ResumeThread 枚举")

        self.THREADENTRY32 = self._thread_entry_type(ctypes, wintypes)
        k.OpenThread.restype = wintypes.HANDLE
        k.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.ResumeThread.restype = wintypes.DWORD
        k.ResumeThread.argtypes = [wintypes.HANDLE]
        k.Thread32First.restype = wintypes.BOOL
        k.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(self.THREADENTRY32)]
        k.Thread32Next.restype = wintypes.BOOL
        k.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(self.THREADENTRY32)]

    @staticmethod
    def _io_counters(ctypes, wintypes):
        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        return IO_COUNTERS

    @staticmethod
    def _basic_limit(ctypes, wintypes):
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
        return JOBOBJECT_BASIC_LIMIT_INFORMATION

    @staticmethod
    def _extended_limit(ctypes, wintypes):
        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _Win32._basic_limit(ctypes, wintypes)),
                ("IoInfo", _Win32._io_counters(ctypes, wintypes)),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]
        return JOBOBJECT_EXTENDED_LIMIT_INFORMATION

    @staticmethod
    def _process_entry_type(ctypes, wintypes):
        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]
        return PROCESSENTRY32W

    @staticmethod
    def _thread_entry_type(ctypes, wintypes):
        class THREADENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD),
                ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", wintypes.LONG),
                ("tpDeltaPri", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
            ]
        return THREADENTRY32


if IS_WINDOWS:
    try:
        _WIN: _Win32 | None = _Win32()
    except Exception as exc:  # noqa: BLE001 —— 没有 Win32 也要能启动（只是没有 Job）
        _WIN = None
        logger.warning("Win32 API 初始化失败，Job Object 不可用：%s", exc)
else:
    _WIN = None


def _process_table(api: _Win32) -> dict[int, int]:
    """`{pid: parent_pid}` 的一次性快照（Toolhelp32）。"""
    snap = api.k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == _INVALID_HANDLE_VALUE:
        return {}
    try:
        entry = api.PROCESSENTRY32W()
        entry.dwSize = api.ctypes.sizeof(entry)
        if not api.k.Process32FirstW(snap, api.ctypes.byref(entry)):
            return {}
        table: dict[int, int] = {}
        while True:
            table[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            if not api.k.Process32NextW(snap, api.ctypes.byref(entry)):
                break
        return table
    finally:
        api.k.CloseHandle(snap)


def _descendants(table: dict[int, int], root: int) -> list[int]:
    """`root` 的全部后代（含多层），按发现顺序。"""
    children: dict[int, list[int]] = {}
    for pid, ppid in table.items():
        children.setdefault(ppid, []).append(pid)
    out: list[int] = []
    stack = list(children.get(root, []))
    while stack:
        pid = stack.pop()
        if pid in out or pid == root:
            continue
        out.append(pid)
        stack.extend(children.get(pid, []))
    return out


def _assign_one(api: _Win32, pid: int, job: int) -> bool:
    handle = api.k.OpenProcess(
        PROCESS_TERMINATE | PROCESS_SET_QUOTA | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        return bool(api.k.AssignProcessToJobObject(job, handle))
    finally:
        api.k.CloseHandle(handle)


def _in_job(api: _Win32, pid: int, job: int) -> bool | None:
    """`True/False` = 已校验；`None` = 进程已退出或无权查询。"""
    handle = api.k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        flag = api.wintypes.BOOL()
        if not api.k.IsProcessInJob(handle, job, api.ctypes.byref(flag)):
            return None
        return bool(flag.value)
    finally:
        api.k.CloseHandle(handle)


def create_flags(*, allow_subprocesses: bool) -> int:
    """创建标志：**Windows 上创建即挂起**，绑定 Job 后再恢复。

    ## 为什么现在用 `CREATE_SUSPENDED`

    旧实现不用它，理由是"`OpenThread` 要线程 id，而我们只有进程 id"。
    但恢复挂起的进程还有一条更直接的路：`ntdll.NtResumeProcess(进程句柄)`
    （本机实测返回 0）。于是流程变成：

        CREATE_SUSPENDED 创建 → AssignProcessToJobObject → NtResumeProcess

    这样**孙进程不可能在绑定前被派生**——根进程在绑定完成前根本没跑起来。
    旧的"枚举已存在后代再补绑"保留为纵深防御（覆盖非挂起路径与 Resume 回退）。

    `CREATE_BREAKAWAY_FROM_JOB` 保留：我们自己很可能已在某个 Job 里（资源管理器/CI 启动），
    带上它在嵌套场景下才能绑得上。实测本机单独使用它**不影响进程正常运行**。
    """
    flags = 0
    if IS_WINDOWS:
        flags |= CREATE_BREAKAWAY_FROM_JOB | CREATE_SUSPENDED
    return flags


def assign(proc: subprocess.Popen, *, allow_subprocesses: bool) -> JobBinding:
    """把进程（及其已存在的后代）绑到 Job Object。

    非 Windows 走进程组，返回空 handle + 说明。
    """
    if not IS_WINDOWS:
        return JobBinding(handle=0, note="POSIX：使用进程组（killpg），不用 Job Object")
    if _WIN is None:
        return JobBinding(handle=0, note="Win32 API 不可用（ctypes/WinDLL 初始化失败）")

    api = _WIN
    job = api.k.CreateJobObjectW(None, None)
    if not job:
        return JobBinding(handle=0, note=f"CreateJobObject 失败（err={api.ctypes.get_last_error()}）")

    info = api.JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    # 关句柄即杀：宿主进程崩了也不会留下孤儿（比"记得清理"可靠）
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not allow_subprocesses:
        info.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        info.BasicLimitInformation.ActiveProcessLimit = MAX_ACTIVE_PROCESSES

    if not api.k.SetInformationJobObject(
        job, JobObjectExtendedLimitInformation, api.ctypes.byref(info),
        api.ctypes.sizeof(info)
    ):
        err = api.ctypes.get_last_error()
        api.k.CloseHandle(job)
        return JobBinding(handle=0, note=f"SetInformationJobObject 失败（err={err}）")

    if not _assign_one(api, proc.pid, job):
        err = api.ctypes.get_last_error()
        api.k.CloseHandle(job)
        return JobBinding(
            handle=0,
            note=(f"AssignProcessToJobObject 失败（err={err}）——"
                  "常见于已处于受限 Job 中的宿主；已降级为 direct-kill（进程树可能不完整）"),
        )

    bound: list[int] = [proc.pid]
    failed: list[int] = []
    # 两轮：第一轮收编已存在的后代；第二轮收编第一轮之后新派生的（窗口很小，但存在）。
    for _ in range(2):
        table = _process_table(api)
        pending = [pid for pid in _descendants(table, proc.pid) if pid not in bound]
        if not pending:
            break
        added = 0
        for pid in pending:
            # 已经因继承在 Job 里：不需要再 Assign（对已在本 Job 的进程再绑会失败，
            # 旧实现对这种"正常继承"会误记成补绑失败）。
            if _in_job(api, pid, job) is True:
                bound.append(pid)
                added += 1
                continue
            if _assign_one(api, pid, job):
                bound.append(pid)
                added += 1
            elif _in_job(api, pid, job) is True:
                # Assign 与检查之间它被父进程带进了 Job。
                bound.append(pid)
                added += 1
            elif pid in table:
                failed.append(pid)
        if not added:
            break
        time.sleep(0.01)

    # 校验：只对**还活着**的 pid 要求 IsProcessInJob（已退出的自然不在树里）。
    # 失败列表里的 pid 若仍活着，再查一次：可能已被继承进 Job（正常），否则记未验证。
    table = _process_table(api)
    unverified: list[int] = []
    for pid in dict.fromkeys(bound):
        if pid not in table:
            continue
        if _in_job(api, pid, job) is not True:
            unverified.append(pid)
    for pid in dict.fromkeys(failed):
        if pid not in table:
            continue
        if _in_job(api, pid, job) is True:
            bound.append(pid)
        else:
            unverified.append(pid)

    bound = list(dict.fromkeys(bound))
    with _JOBS_LOCK:
        _JOBS[proc.pid] = job

    note = ""
    if unverified:
        note = f"以下 pid 未通过 IsProcessInJob 校验：{sorted(set(unverified))}"
    return JobBinding(
        handle=job,
        note=note,
        verified=not unverified,
        bound_pids=tuple(bound),
    )


def _resume_threads(api: _Win32, pid: int) -> int:
    """回退：枚举线程并逐个 `ResumeThread`。返回成功恢复的线程数。"""
    snap = api.k.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if not snap or snap == _INVALID_HANDLE_VALUE:
        return 0
    try:
        entry = api.THREADENTRY32()
        entry.dwSize = api.ctypes.sizeof(entry)
        if not api.k.Thread32First(snap, api.ctypes.byref(entry)):
            return 0
        resumed = 0
        while True:
            if int(entry.th32OwnerProcessID) == pid:
                thread = api.k.OpenThread(THREAD_SUSPEND_RESUME, False, int(entry.th32ThreadID))
                if thread:
                    try:
                        if api.k.ResumeThread(thread) != 0xFFFFFFFF:
                            resumed += 1
                    finally:
                        api.k.CloseHandle(thread)
            if not api.k.Thread32Next(snap, api.ctypes.byref(entry)):
                break
        return resumed
    finally:
        api.k.CloseHandle(snap)


def resume(proc: subprocess.Popen) -> tuple[bool, str]:
    """恢复 `CREATE_SUSPENDED` 创建的子进程。返回 `(ok, 用了什么手段)`。

    优先 `ntdll.NtResumeProcess`（进程句柄，不需要线程 id）；不可用时退回
    Toolhelp32 枚举线程 + `ResumeThread`。两条都失败 ⇒ `ok=False`，调用方**必须**
    终止进程并返回错误——一个永远挂着的进程比一次失败更糟。
    """
    if not IS_WINDOWS:
        return True, ""
    if _WIN is None:
        return False, "Win32 API 不可用（ctypes/WinDLL 初始化失败）"
    api = _WIN
    handle = api.k.OpenProcess(PROCESS_SUSPEND_RESUME, False, proc.pid)
    if not handle:
        return False, f"OpenProcess(PROCESS_SUSPEND_RESUME) 失败（err={api.ctypes.get_last_error()}）"
    try:
        if api.ntdll is not None:
            status = api.ntdll.NtResumeProcess(handle)
            if status == 0:
                return True, "NtResumeProcess"
    finally:
        api.k.CloseHandle(handle)

    resumed = _resume_threads(api, proc.pid)
    if resumed:
        return True, f"ResumeThread×{resumed}"
    return False, "NtResumeProcess 与 ResumeThread 均失败"


def _taskkill(pid: int) -> tuple[int, str]:
    """`taskkill /T /F` 兜底（按父进程关系杀树，不依赖 Job）。"""
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, creationflags=CREATE_NO_WINDOW,
        )
        output = (result.stdout or "") + (result.stderr or "")
        return result.returncode, output.strip()[:300]
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, f"{type(exc).__name__}: {exc}"


def terminate(proc: subprocess.Popen, binding: JobBinding | None = None) -> str:
    """终结**整棵进程树**。返回实际用了哪种手段（写进审计）。

    Fail-Closed 语义：任何一步失败都记录下来，绝不假装杀干净了。
    Windows 的顺序是 **先 `taskkill /T` 再 `TerminateJobObject`**：
    前者按父进程关系抓 Job 外的后代，后者兜住已在 Job 里的；先 taskkill
    也避免进程退出后 pid 被复用、误杀无关进程。
    """
    if IS_WINDOWS:
        job = (binding.handle if binding else 0) or _JOBS.get(proc.pid, 0)
        notes: list[str] = []
        if proc.poll() is None:
            code, output = _taskkill(proc.pid)
            if code == 0:
                notes.append("taskkill /T /F")
            else:
                notes.append(f"taskkill 失败（rc={code}，{output or '无输出'}）")
        if job:
            try:
                api = _WIN
                if api is None:
                    notes.append("TerminateJobObject 跳过（Win32 API 不可用）")
                else:
                    ok = api.k.TerminateJobObject(job, 1)
                    notes.append("TerminateJobObject（整棵树）" if ok
                                 else "TerminateJobObject 失败")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"TerminateJobObject 异常：{exc}")
            finally:
                close(proc)
        elif proc.poll() is None:
            try:
                proc.kill()
                notes.append("direct-kill（未绑定 Job）")
            except OSError as exc:
                notes.append(f"direct-kill 失败：{exc}")
        return " + ".join(notes) if notes else "无进程可终止"

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
    with _JOBS_LOCK:
        job = _JOBS.pop(proc.pid, 0)
    if not job:
        return
    try:
        api = _WIN
        if api is not None:
            api.k.CloseHandle(job)
    except Exception:  # noqa: BLE001 —— 关句柄失败不影响结果（进程已终止）
        logger.warning("关闭 Job 句柄失败 pid=%s", proc.pid, exc_info=True)


def stats() -> dict:
    """运行期视图：当前持有多少个 Job（管理页用）。"""
    with _JOBS_LOCK:
        active = len(_JOBS)
    return {"platform": "windows" if IS_WINDOWS else sys.platform,
            "active_jobs": active, "job_api": IS_WINDOWS,
            "max_active_processes": MAX_ACTIVE_PROCESSES,
            "verified_binding": IS_WINDOWS and _WIN is not None}
