"""proc.py 单测：Node 检测 / stdin 喂入 / 超时 / 错误诊断。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from workbench.services import proc


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
        proc.run_proc(proc.ProcSpec(argv=[str(Path("Z:/definitely/missing.exe"))]))


def test_missing_cwd_diagnosis() -> None:
    with pytest.raises(proc.ProcError, match="工作目录不存在"):
        proc.run_proc(proc.ProcSpec(argv=[sys.executable, "-c", "pass"], cwd="Z:/no/such/dir"))
