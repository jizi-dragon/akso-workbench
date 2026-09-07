"""Node 子进程统一封装：spawn / 超时 / 流式日志 / 退出码 / 环境检测。

契约见 docs/模块契约.md §2。所有对 akso-cc / akso-auto 的调用都经由此模块，
保证：统一的日志行回调（SSE 用）、统一超时杀进程、统一的错误诊断
（可执行文件缺失 / 入口缺失 / cwd 缺失都给出人话）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


class ProcError(RuntimeError):
    """子进程启动失败 / 超时 / 非零退出。"""


def node_executable() -> str:
    """Node 可执行文件（可用 NODE_COMMAND 覆盖）。"""
    return os.environ.get("NODE_COMMAND") or "node"


def node_available() -> tuple[bool, str]:
    """Node 环境检测：返回 (是否可用, 版本或错误信息)。"""
    exe = node_executable()
    path = shutil.which(exe)
    if not path:
        return False, f"未找到 Node 可执行文件：{exe}（可设 NODE_COMMAND 指定）"
    try:
        out = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=15
        )
        version = (out.stdout or out.stderr).strip()
        return True, version or "node(版本未知)"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Node 版本检测失败：{exc}"


@dataclass
class ProcSpec:
    """一次子进程调用的完整描述。"""

    argv: list[str]  # 完整命令行（argv[0] 为可执行文件）
    cwd: str | None = None  # 工作目录（默认适配器 repoPath）
    env: dict[str, str] | None = None  # 追加环境变量（叠加在 os.environ 上）
    timeout_s: float = 1800  # 超时（秒），超时杀进程
    on_log: Callable[[str], None] | None = None  # 行级日志回调（SSE 用）
    on_done: Callable[["ProcResult"], None] | None = None  # 结束回调（后台任务用）
    label: str = ""  # 展示用标签（日志前缀）
    stdin_text: str | None = None  # 启动后写入 stdin 的内容（如 confirmEnv 的 "\n\n"）


@dataclass
class ProcResult:
    code: int = -1  # 退出码（-1 表示未正常退出）
    duration_s: float = 0.0
    stdout_tail: list[str] = field(default_factory=list)
    stderr_tail: list[str] = field(default_factory=list)
    timed_out: bool = False
    label: str = ""

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.timed_out

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "duration_s": round(self.duration_s, 2),
            "stdout_tail": self.stdout_tail[-50:],
            "stderr_tail": self.stderr_tail[-50:],
            "timed_out": self.timed_out,
            "label": self.label,
            "ok": self.ok,
        }


_TAIL = 400  # 每个流保留的尾部行数


def _pump(stream, sink: list[str], on_log: Callable[[str], None] | None, label: str) -> None:
    """逐行读取子进程输出流；utf-8 解码容错。"""
    try:
        for raw in iter(stream.readline, ""):
            line = raw.rstrip("\r\n")
            if not line:
                continue
            sink.append(line)
            if len(sink) > _TAIL:
                del sink[: len(sink) - _TAIL]
            if on_log is not None:
                try:
                    on_log(f"[{label}] {line}" if label else line)
                except Exception:  # noqa: BLE001 —— 日志回调永不拖垮子进程泵
                    pass
    except (ValueError, OSError):
        pass
    finally:
        try:
            stream.close()
        except Exception:  # noqa: BLE001
            pass


def check_spec(spec: ProcSpec) -> None:
    """启动前体检：可执行文件 / cwd 是否存在。失败抛 ProcError。"""
    exe = spec.argv[0] if spec.argv else ""
    if not exe:
        raise ProcError("空命令行")
    if os.path.isabs(exe):
        if not Path(exe).exists():
            raise ProcError(f"可执行文件不存在：{exe}")
    # 相对路径（如 node）交给 PATH；cwd 检查
    if spec.cwd and not Path(spec.cwd).exists():
        raise ProcError(f"工作目录不存在：{spec.cwd}")


def run_proc(spec: ProcSpec) -> ProcResult:
    """同步执行子进程（阻塞直到退出/超时）。行级输出经 on_log 回调。"""
    check_spec(spec)
    env = dict(os.environ)
    if spec.env:
        env.update(spec.env)

    started = time.perf_counter()
    log_label = spec.label or Path(spec.argv[0]).stem
    try:
        proc = subprocess.Popen(
            spec.argv,
            cwd=spec.cwd,
            env=env,
            stdin=subprocess.PIPE if spec.stdin_text is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        raise ProcError(f"启动失败：{spec.argv[0]} → {exc}") from exc

    if spec.stdin_text is not None:
        # 在独立线程写 stdin 再关闭，避免管道缓冲死锁
        def _feed() -> None:
            try:
                assert proc.stdin is not None
                proc.stdin.write(spec.stdin_text)
                proc.stdin.flush()
            except (OSError, ValueError):
                pass
            finally:
                try:
                    assert proc.stdin is not None
                    proc.stdin.close()
                except (OSError, ValueError):
                    pass

        threading.Thread(target=_feed, daemon=True).start()

    out: list[str] = []
    err: list[str] = []
    threads = [
        threading.Thread(target=_pump, args=(proc.stdout, out, spec.on_log, log_label), daemon=True),
        threading.Thread(target=_pump, args=(proc.stderr, err, spec.on_log, log_label), daemon=True),
    ]
    for t in threads:
        t.start()

    timed_out = False
    try:
        code = proc.wait(timeout=spec.timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        code = proc.wait(timeout=30)
        err.append(f"[timeout] 超过 {spec.timeout_s:.0f}s，已强制终止")

    for t in threads:
        t.join(timeout=5)

    result = ProcResult(
        code=code,
        duration_s=time.perf_counter() - started,
        stdout_tail=out,
        stderr_tail=err,
        timed_out=timed_out,
        label=spec.label,
    )
    if spec.on_done is not None:
        try:
            spec.on_done(result)
        except Exception:  # noqa: BLE001
            pass
    return result


def spawn_proc(spec: ProcSpec) -> subprocess.Popen:
    """后台启动子进程（不等待），返回 Popen 供调用方管理（如 Monitor 长任务）。

    输出泵在守护线程中持续工作；调用方负责最终 kill/等待。
    """
    check_spec(spec)
    env = dict(os.environ)
    if spec.env:
        env.update(spec.env)
    try:
        proc = subprocess.Popen(
            spec.argv,
            cwd=spec.cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        raise ProcError(f"启动失败：{spec.argv[0]} → {exc}") from exc

    log_label = spec.label or Path(spec.argv[0]).stem
    threading.Thread(
        target=_pump, args=(proc.stdout, [], spec.on_log, log_label), daemon=True
    ).start()
    threading.Thread(
        target=_pump, args=(proc.stderr, [], spec.on_log, log_label), daemon=True
    ).start()
    return proc
