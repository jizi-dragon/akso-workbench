"""proc.py 单测：Node 检测 / stdin 喂入 / 超时 / 错误诊断。"""

from __future__ import annotations

import sys
import tempfile
import uuid
from pathlib import Path

import pytest

from workbench.services import proc


def _absent_absolute_path(suffix: str) -> str:
    """一个**保证不存在**的绝对路径，且跨平台都是绝对路径。

    为什么不用 `Z:/...`：`check_spec` 只在 `os.path.isabs(exe)` 成立时才查存在性。
    POSIX 下 `Z:/…` **不是**绝对路径（不以 `/` 开头），于是它会被当成"走 PATH 的命令名"跳过检查、
    测试报出的是"没抛 ProcError"——**看着像 proc.py 的 bug，其实是用例不可移植**
    （同一个坑还有 `test_missing_cwd_diagnosis`：cwd 那条不走 isabs 判断，反而能过）。
    2026-09-17 为让本套测试可在 Linux 上跑而改。
    """
    return str(Path(tempfile.gettempdir()) / f"akso-absent-{uuid.uuid4().hex}" / suffix)


def test_node_detection() -> None:
    ok, detail = proc.node_available()
    if ok:
        assert detail
    else:
        assert "未找到" in detail or "失败" in detail  # 诊断信息可读


def test_run_basic_python() -> None:
    result = proc.run_proc(proc.ProcSpec(
        argv=[sys.executable, "-c", "print('hello-proc')"],
        label="test",
    ))
    assert result.ok
    assert any("hello-proc" in line for line in result.stdout_tail)


def test_run_streaming_log() -> None:
    lines: list[str] = []
    result = proc.run_proc(proc.ProcSpec(
        argv=[sys.executable, "-c", "print('a'); print('b')"],
        on_log=lines.append,
        label="stream",
    ))
    assert result.ok
    assert any("a" in x for x in lines) and any("b" in x for x in lines)


def test_stdin_feed() -> None:
    """stdin_text 写入并被子进程读取（confirmEnv 管道喂入的机制基础）。"""
    result = proc.run_proc(proc.ProcSpec(
        argv=[sys.executable, "-c", "import sys; d=sys.stdin.read(); print('GOT:'+d.strip())"],
        stdin_text="\n\n",
    ))
    assert result.ok
    assert any("GOT:" in line for line in result.stdout_tail)


def test_timeout_kills() -> None:
    result = proc.run_proc(proc.ProcSpec(
        argv=[sys.executable, "-c", "import time; time.sleep(30)"],
        timeout_s=1.5,
    ))
    assert result.timed_out
    assert not result.ok


def test_missing_executable_diagnosis() -> None:
    with pytest.raises(proc.ProcError, match="可执行文件不存在"):
        proc.run_proc(proc.ProcSpec(argv=[_absent_absolute_path("missing.exe")]))


def test_missing_cwd_diagnosis() -> None:
    with pytest.raises(proc.ProcError, match="工作目录不存在"):
        proc.run_proc(proc.ProcSpec(
            argv=[sys.executable, "-c", "pass"],
            cwd=_absent_absolute_path("no-such-dir"),
        ))
