"""Windows 受限令牌（WRITE_RESTRICTED）：给**只读策略**加一层真实的运行时写限制。

## 这是什么

`strict-readonly` 的旧语义只是"启动前拒绝声明会写的策略"；脚本仍以当前用户权限跑，
写工作区完全拦不住。本模块在进程 **CREATE_SUSPENDED** 阶段把它的主令牌换成
`CreateRestrictedToken(WRITE_RESTRICTED)` 令牌，限制 SID 列表只有
**logon SID + Everyone**（与 DSH `sandbox-windows-acl` 的 read-only 列表一致）。

## 诚实边界

- 这是 **read-only 的部分强制**：`Everyone` 仍有环境授予的写 ACE（如 `C:\\Users\\Public`），
  DSH 文档也把这类称为 partial；不能宣称"文件系统完全只读"。
- **workspace-write 没做**：capability SID 的写 ACE 在 POC 中未被访问检查接受
  （见 `tmp/audit/UPGRADE-PROGRESS.md`），所以不在这里假装支持。
- 失败一律 fail-closed：拿不到令牌/换不上令牌 ⇒ 调用方必须终止并拒绝执行。

## 为什么用 `NtSetInformationProcess` 而不是 `CreateProcessAsUser`

`subprocess` 已经负责管道、job、恢复；在挂起阶段换 token 可以复用同一套 runner，
不用重写 stdio/超时/进程树。POC 已验证 `NtSetInformationProcess(ProcessAccessToken)`
返回 0（成功）。这比再实现一套 `CreateProcessAsUser` 的 STARTUPINFO 管道更小。
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

logger = logging.getLogger("workbench.sandbox.win_token")

IS_WINDOWS = sys.platform == "win32"

TOKEN_QUERY = 0x0008
TOKEN_DUPLICATE = 0x0002
TOKEN_ASSIGN_PRIMARY = 0x0001
WRITE_RESTRICTED = 0x0008
LUA_TOKEN = 0x0004
DISABLE_MAX_PRIVILEGE = 0x0001
SE_GROUP_LOGON_ID = 0xC0000000
TokenGroups = 2
WinWorldSid = 1
PROCESS_SET_INFORMATION = 0x0200
ProcessAccessToken = 9


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class TOKEN_GROUPS(ctypes.Structure):
    _fields_ = [("GroupCount", wintypes.DWORD), ("Groups", SID_AND_ATTRIBUTES * 1)]


class PROCESS_ACCESS_TOKEN(ctypes.Structure):
    _fields_ = [("Token", wintypes.HANDLE), ("Thread", wintypes.HANDLE)]


def _api():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.OpenProcessToken.restype = wintypes.BOOL
    kernel32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.HANDLE)]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                             ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD)]
    advapi32.CreateWellKnownSid.restype = wintypes.BOOL
    advapi32.CreateWellKnownSid.argtypes = [ctypes.c_int, ctypes.c_void_p,
                                            ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetLengthSid.restype = wintypes.DWORD
    advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
    advapi32.CopySid.restype = wintypes.BOOL
    advapi32.CopySid.argtypes = [wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
    advapi32.CreateRestrictedToken.restype = wintypes.BOOL
    advapi32.CreateRestrictedToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(SID_AND_ATTRIBUTES), ctypes.POINTER(wintypes.HANDLE),
    ]
    ntdll.NtSetInformationProcess.restype = ctypes.c_long
    ntdll.NtSetInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                              ctypes.c_void_p, wintypes.ULONG]
    return kernel32, advapi32, ntdll


def _logon_sid(advapi32, token) -> ctypes.Array:
    need = wintypes.DWORD()
    advapi32.GetTokenInformation(token, TokenGroups, None, 0, ctypes.byref(need))
    if need.value == 0:
        raise OSError("GetTokenInformation(TokenGroups) 取不到大小")
    buf = ctypes.create_string_buffer(need.value)
    if not advapi32.GetTokenInformation(token, TokenGroups, buf, need, ctypes.byref(need)):
        raise OSError(ctypes.get_last_error(), "GetTokenInformation(TokenGroups)")
    groups = ctypes.cast(buf, ctypes.POINTER(TOKEN_GROUPS)).contents
    base = ctypes.addressof(buf) + 8  # DWORD GroupCount + 4 字节对齐
    stride = ctypes.sizeof(SID_AND_ATTRIBUTES)
    for index in range(groups.GroupCount):
        item = ctypes.cast(base + index * stride, ctypes.POINTER(SID_AND_ATTRIBUTES)).contents
        if item.Attributes & SE_GROUP_LOGON_ID:
            length = advapi32.GetLengthSid(item.Sid)
            copy = ctypes.create_string_buffer(length)
            if not advapi32.CopySid(length, copy, item.Sid):
                raise OSError(ctypes.get_last_error(), "CopySid(logon)")
            return copy
    raise OSError("令牌里没有 logon SID（无法构造 read-only 限制列表）")


def restrict_readonly(pid: int) -> tuple[bool, str]:
    """把 **已挂起** 的子进程换成 read-only 受限令牌。返回 `(ok, note)`。"""
    if not IS_WINDOWS:
        return False, "非 Windows"
    kernel32 = advapi32 = ntdll = None
    token = new_token = None
    process = None
    try:
        kernel32, advapi32, ntdll = _api()
        token = wintypes.HANDLE()
        if not kernel32.OpenProcessToken(
            kernel32.GetCurrentProcess(),
            TOKEN_QUERY | TOKEN_DUPLICATE | TOKEN_ASSIGN_PRIMARY,
            ctypes.byref(token),
        ):
            raise OSError(ctypes.get_last_error(), "OpenProcessToken")
        logon = _logon_sid(advapi32, token)
        world = ctypes.create_string_buffer(68)
        size = wintypes.DWORD(68)
        if not advapi32.CreateWellKnownSid(WinWorldSid, None, world, ctypes.byref(size)):
            raise OSError(ctypes.get_last_error(), "CreateWellKnownSid")
        restrict = (SID_AND_ATTRIBUTES * 2)()
        restrict[0] = SID_AND_ATTRIBUTES(ctypes.cast(logon, ctypes.c_void_p), 0)
        restrict[1] = SID_AND_ATTRIBUTES(ctypes.cast(world, ctypes.c_void_p), 0)
        new_token = wintypes.HANDLE()
        flags = DISABLE_MAX_PRIVILEGE | LUA_TOKEN | WRITE_RESTRICTED
        if not advapi32.CreateRestrictedToken(
            token, flags, 0, None, 0, None, 2, restrict, ctypes.byref(new_token)
        ):
            raise OSError(ctypes.get_last_error(), "CreateRestrictedToken")
        process = kernel32.OpenProcess(PROCESS_SET_INFORMATION, False, int(pid))
        if not process:
            raise OSError(ctypes.get_last_error(), "OpenProcess")
        info = PROCESS_ACCESS_TOKEN(new_token, None)
        status = ntdll.NtSetInformationProcess(
            process, ProcessAccessToken, ctypes.byref(info), ctypes.sizeof(info)
        )
        if status != 0:
            raise OSError(f"NtSetInformationProcess 返回 NTSTATUS 0x{status & 0xFFFFFFFF:08X}")
        return True, "read-only 受限令牌已附加（logon + Everyone；partial）"
    except OSError as exc:
        logger.warning("附加 read-only 受限令牌失败：%s", exc)
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        if process:
            kernel32.CloseHandle(process)
        if new_token:
            kernel32.CloseHandle(new_token)
        if token:
            kernel32.CloseHandle(token)
