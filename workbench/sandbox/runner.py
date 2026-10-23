"""受控执行器：**不受信代码唯一被允许的入口**。

对照 的四层强制：

| 层 | 本模块怎么强制 |
|---|---|
| **E1 生命周期** | `job.assign` 绑定 Job Object；超时/取消走 `job.terminate` → **杀整棵进程树** |
| **E2 环境** | `policy.build_env` **白名单**筛环境变量（不再是 `dict(os.environ)` 全量继承） |
| **E3 文件系统** | `cwd` 必须在**沙箱根**内（`Path.resolve()` 后比较，防 `..`/符号链接）；路径命中敏感表直接拒 |
| **E4 可观测** | 返回结构里带 `policy` / `policy_fingerprint` / `tree_kill` / `notes` / `output_truncated`，由调用方落审计 |

## Fail-Closed 的具体含义（本模块不做"尽力而为"）

- 未知策略 → **拒**（不回落到默认策略）；
- `cwd` 越出沙箱根 → **拒**（不是"警告后照跑"）；
- `cwd` 命中敏感路径 → **拒**；
- 可执行文件不在**允许的解释器白名单**里 → **拒**；
- 输出超限 → 停止读取并**显式标记** `stdout_truncated=True`（不是静默截断，
  否则调用方会把"被截断的日志"当成"完整日志"——本仓在工具结果上已有同类纪律：
  `plugin_api.MAX_RESULT_CHARS` 的显式告知）。

## 诚实边界

它**不是**安全边界：跑起来的代码仍能读它有权读的一切（除我们主动拒掉的路径）。
本模块保证的是"整棵树能停 / 凭据不自动喂进去 / 目录不越界 / 每次都留痕"。
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import job
from .policy import (
    SandboxError,
    SandboxPolicy,
    build_env,
    get_policy,
    is_sensitive_path,
    within_root,
)

logger = logging.getLogger("workbench.sandbox.runner")

#: 允许作为被沙箱执行的**解释器/可执行文件**的程序名（小写，含 .exe）。
#:
#: 为什么需要这个白名单：沙箱的执行对象来自"用户上传的东西"，如果允许任意
#: `argv[0]`，那么沙箱就退化成"一个带超时的通用命令执行器"——它能跑 `cmd.exe`、
#: 能跑任何系统工具，把"跑一个数据处理脚本"变成"跑任何东西"。
#: 这是**收窄入口**，不是完备防护（脚本内部当然还能调别的），但让默认形态是安全的。
ALLOWED_EXECUTABLES: frozenset[str] = frozenset({
    "python", "python.exe", "python3", "python3.exe",
    "py", "py.exe",
    "node", "node.exe",
    "deno", "deno.exe", "bun", "bun.exe",
    # PowerShell / cmd 刻意**不在**名单内：它们让"执行脚本"等价于"执行任意命令"
})


@dataclass
class ExecRequest:
    """一次沙箱执行的完整描述。"""

    argv: list[str]
    policy_name: str
    cwd: Path
    label: str = ""
    stdin_text: str | None = None
    timeout_s: float | None = None
    """覆盖策略超时。**只允许调小**（调大被拒）——策略是上限，不是建议。"""

    extra_env: dict[str, str] | None = None
    """调用方要额外注入的环境变量（**经白名单叠加**，不是替换）。

    为什么需要它：脚本式 Skill 要知道"我是哪个 Skill、哪个工具、包在哪"
    （`WORKBENCH_SKILL_ID` 等），否则一个包里的多个脚本没法共享逻辑。
    它**不破坏 E2**：仍然先按白名单筛宿主环境，再叠这几个调用方提供的值；
    而且 `policy.validate()` 会拒掉任何名字像凭据的变量（走 `extra_env` 声明时）。
    这里不做那层校验是因为调用方是**我们自己的代码**（`script_runner`），
    给的是固定的 `WORKBENCH_*` 常量，不含凭据。
    """


@dataclass
class ExecResult:
    """执行结果。字段刻意做成"审计能直接用"的形状。"""

    ok: bool
    code: int = -1
    duration_s: float = 0.0
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    timed_out: bool = False
    policy_name: str = ""
    policy_fingerprint: str = ""
    tree_kill: str = ""
    """超时/取消时实际用的终止手段（写进审计，回答"树到底杀干净没有"）。"""
    cwd: str = ""
    notes: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "code": self.code,
            "duration_s": round(self.duration_s, 3),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "timed_out": self.timed_out,
            "policy": self.policy_name,
            "policy_fingerprint": self.policy_fingerprint,
            "tree_kill": self.tree_kill,
            "cwd": self.cwd,
            "notes": list(self.notes),
            "error": self.error,
        }


def _reject(reason: str, **kw) -> ExecResult:
    """构造一个"被拒"的结果。**统一从这条路径返回**，保证拒绝也带得出审计信息。"""
    result = ExecResult(ok=False, error=reason, **kw)
    result.notes.append("拒绝执行（Fail-Closed）")
    return result


def resolve_executable(argv: list[str]) -> tuple[str | None, str]:
    """校验 `argv[0]` 是否在允许的解释器名单内。

    返回 `(规范化后的路径, 空串)` 或 `(None, 拒绝原因)`。
    **只按 basename 判定**：无论如何都不接受 `cmd.exe /c ...` 这类形态。
    """
    if not argv:
        return None, "空命令行"
    exe = argv[0]
    name = Path(exe).name.lower()
    if name not in ALLOWED_EXECUTABLES:
        return None, (
            f"可执行文件不在沙箱允许名单内：{name!r}。"
            f"允许：{', '.join(sorted(ALLOWED_EXECUTABLES))}（shell 类刻意排除）"
        )
    if os.path.isabs(exe) or os.sep in exe or "/" in exe:
        if not Path(exe).exists():
            return None, f"可执行文件不存在：{exe}"
        return str(Path(exe).resolve()), ""
    import shutil
    found = shutil.which(exe)
    if not found:
        return None, f"在 PATH 上找不到可执行文件：{exe}"
    return found, ""


class _CappedReader:
    """按字节上限读取一个流，超出即停止累积并标记截断。

    为什么不用 `stream.read()`：那会等到进程结束（长任务期间看不到任何输出）。
    为什么超限后仍继续读：**不读满管道会让子进程阻塞在写 stdout 上**，
    表现为"脚本卡死"，比截断更难查。
    """

    def __init__(self, stream, cap: int) -> None:
        self.lines: list[str] = []
        self.truncated = False
        self._cap = cap
        self._bytes = 0
        self._stream = stream

    def pump(self) -> None:
        try:
            for raw in iter(self._stream.readline, ""):
                if self.truncated:
                    continue  # 继续读、不累积（防子进程阻塞）
                self._bytes += len(raw.encode("utf-8", "replace"))
                if self._bytes > self._cap:
                    self.truncated = True
                    self.lines.append(f"[输出超过 {self._cap} 字节上限，后续内容已丢弃]")
                    continue
                self.lines.append(raw.rstrip("\r\n"))
        except (OSError, ValueError):
            pass
        finally:
            try:
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def run(req: ExecRequest) -> ExecResult:
    """同步执行一次沙箱任务。**任何失败都转成 ExecResult，不向调用方抛。**

    ## 审计（E4）在这里落，**不在调用方**

    这是本模块唯一的对外执行入口，所以把 `audit.record` 放在这一层意味着
    **每一条返回路径都会被记录**——包括各种"被拒"（策略不存在、可执行文件不在名单、
    cwd 越界、启动失败）。`_run` 里有 7 处 `return`，靠调用方逐个记得写审计，
    迟早漏一个：**实测就漏了全部**——`audit.record` 一度没有任何调用方，
    于是 `/api/sandbox/runs` 永远是空表、管理页的"执行审计"永远没内容，
    而 D-2 的 E4 层在文档上写着"已实现"。放在扼流点上，这类"漏"在结构上不可能发生。

    审计失败**绝不影响执行结果**（`audit.record` 自己吞异常），
    这与 `plugin_api` / `skills.library` 的留痕纪律一致：
    **留痕是义务，不是执行的前置条件**。
    """
    result = _run(req)
    from . import audit  # 本地导入：让 policy/layout 仍可独立单测，且避免循环

    audit.record(result, label=req.label, argv=req.argv)
    return result


def _run(req: ExecRequest) -> ExecResult:
    """真正的执行体。**只被 `run` 调用**，以免绕过审计。"""
    # ── 1) 策略
    try:
        policy: SandboxPolicy = get_policy(req.policy_name)
    except SandboxError as exc:
        return _reject(str(exc))

    timeout = policy.timeout_s
    if req.timeout_s is not None:
        if req.timeout_s > policy.timeout_s:
            return _reject(
                f"请求超时 {req.timeout_s}s 超过策略 {policy.name} 的上限 {policy.timeout_s}s"
                "（策略是上限，不是建议）",
                policy_name=policy.name, policy_fingerprint=policy.fingerprint())
        timeout = req.timeout_s

    # ── 1.5) 工作区权限：只读 / 联网 / 派生进程（2026-10-19）
    from .layout import permission_error, workspace_root

    try:
        permission_why = permission_error(policy)
    except Exception as exc:  # noqa: BLE001 —— 策略层读不出配置也必须 Fail-Closed
        return _reject(f"工作区权限不可判定：{type(exc).__name__}: {exc}",
                       policy_name=policy.name, policy_fingerprint=policy.fingerprint())
    if permission_why:
        return _reject(permission_why, policy_name=policy.name,
                       policy_fingerprint=policy.fingerprint())

    # ── 2) 可执行文件
    exe, why = resolve_executable(req.argv)
    if exe is None:
        return _reject(why, policy_name=policy.name, policy_fingerprint=policy.fingerprint())
    argv = [exe] + list(req.argv[1:])

    # ── 3) 文件系统：cwd 必须在**工作区根**内，且不得命中敏感路径
    cwd = Path(req.cwd)
    try:
        root = workspace_root()
    except Exception as exc:  # noqa: BLE001 —— 同上
        return _reject(f"工作区根不可用：{type(exc).__name__}: {exc}",
                       policy_name=policy.name, policy_fingerprint=policy.fingerprint())
    if is_sensitive_path(cwd):
        return _reject(f"工作目录命中敏感路径拒绝表：{cwd}",
                       policy_name=policy.name, policy_fingerprint=policy.fingerprint())
    if not within_root(cwd, root):
        return _reject(
            f"工作目录越出工作区根：{cwd}（工作区根 = {root}）",
            policy_name=policy.name, policy_fingerprint=policy.fingerprint())
    if not cwd.is_dir():
        return _reject(f"工作目录不存在：{cwd}",
                       policy_name=policy.name, policy_fingerprint=policy.fingerprint())

    # ── 4) 环境：白名单
    env = build_env(policy, extra=req.extra_env)
    notes: list[str] = [
        f"环境变量白名单：注入 {len(env)} 个，宿主其余变量未传入",
        f"工作区：{root}（cwd={cwd}）",
    ]

    # ── 5) 起进程 + 绑 Job
    started = time.perf_counter()
    # 可选的工作区快照：默认关（每次执行都扫工作区太贵），显式开关才做。
    # 它只覆盖"调用时存在的文件"，脚本的 shell 副作用撤不了——与 Reasonix 同一口径。
    if os.environ.get("WORKBENCH_SANDBOX_CHECKPOINTS") == "1" and policy.writes:
        from .. import checkpoints

        try:
            cap = checkpoints.capture(workspace_root())
        except Exception as exc:  # noqa: BLE001 —— 快照失败不阻断执行，但必须留痕
            notes.append(f"⚠ 工作区快照失败：{type(exc).__name__}: {exc}")
        else:
            notes.append(f"已创建工作区快照：{cap.id}（{len(cap.files)} 文件）")

    flags = job.create_flags(allow_subprocesses=policy.allow_subprocesses)
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.PIPE if req.stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=flags,
        )
    except OSError as exc:
        return _reject(f"启动失败：{argv[0]} → {exc}",
                       policy_name=policy.name, policy_fingerprint=policy.fingerprint())

    try:
        binding = job.assign(proc, allow_subprocesses=policy.allow_subprocesses)
    except Exception as exc:  # noqa: BLE001 —— 执行器不向调用方抛
        proc.kill()
        return _reject(f"绑定 Job 失败：{type(exc).__name__}: {exc}",
                       policy_name=policy.name, policy_fingerprint=policy.fingerprint())
    if binding.ok:
        if binding.verified:
            notes.append(
                f"已绑定 Job Object 并校验 {len(binding.bound_pids)} 个进程（超时可杀整棵树）")
        else:
            # 绑定成功但校验没全过：**必须让它在审计里可见**，不能假装"整棵树都绑上了"。
            notes.append(f"⚠ 已绑定 Job Object，但部分进程未校验：{binding.note}")
    else:
        # 刻意不因此拒绝执行：部分受限环境确实绑不上，而"绑不上就不让跑"会让功能在这些机器上
        # 完全不可用。**但必须让它在审计里可见**，不能静默降级。
        notes.append(f"⚠ 未绑定 Job Object：{binding.note}")

    # ★ Windows 上进程是 CREATE_SUSPENDED 创建的：绑定完成后必须恢复。
    #   恢复失败 ⇒ 终止并 Fail-Closed，绝不留一个永远挂着的进程。
    resumed, resume_note = job.resume(proc)
    if not resumed:
        tree_kill = job.terminate(proc, binding)
        return _reject(
            f"无法恢复被挂起的进程：{resume_note}（已终止：{tree_kill}）",
            policy_name=policy.name, policy_fingerprint=policy.fingerprint())
    if resume_note:
        notes.append(f"已恢复挂起进程（{resume_note}）")

    readers: dict[str, _CappedReader] = {}
    threads: list[threading.Thread] = []

    # ★ 从构造 reader 到 wait 结束，整段包在 try/finally 里：
    #   这段里的任何异常（reader 构造失败、线程起不来、stdin 写失败、join 被打断）
    #   都必须先终止进程树再返回，否则会留下一个"没人管、还在跑"的子进程。
    try:
        readers = {
            "out": _CappedReader(proc.stdout, policy.max_output_bytes),
            "err": _CappedReader(proc.stderr, policy.max_output_bytes),
        }
        threads = [threading.Thread(target=r.pump, daemon=True) for r in readers.values()]
        for t in threads:
            t.start()

        if req.stdin_text is not None and proc.stdin is not None:
            def _feed() -> None:
                try:
                    proc.stdin.write(req.stdin_text)  # type: ignore[arg-type]
                    proc.stdin.flush()
                except (OSError, ValueError):
                    pass
                finally:
                    try:
                        proc.stdin.close()  # type: ignore[union-attr]
                    except (OSError, ValueError):
                        pass

            threading.Thread(target=_feed, daemon=True).start()

        # ── 6) 等 / 超时
        timed_out = False
        try:
            code = proc.wait(timeout=timeout)
            tree_kill = ""
        except subprocess.TimeoutExpired:
            timed_out = True
            tree_kill = job.terminate(proc, binding)
            try:
                code = proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                code = -1
            readers["err"].lines.append(f"[timeout] 超过 {timeout:g}s，已终止：{tree_kill}")

        for t in threads:
            t.join(timeout=5)

        duration = time.perf_counter() - started
        out_r, err_r = readers["out"], readers["err"]
        if out_r.truncated or err_r.truncated:
            notes.append("输出被上限截断（已显式标记，不是静默丢弃）")

        return ExecResult(
            ok=(code == 0 and not timed_out),
            code=code,
            duration_s=duration,
            stdout=out_r.text,
            stderr=err_r.text,
            stdout_truncated=out_r.truncated,
            stderr_truncated=err_r.truncated,
            timed_out=timed_out,
            policy_name=policy.name,
            policy_fingerprint=policy.fingerprint(),
            tree_kill=tree_kill,
            cwd=str(cwd),
            notes=notes,
        )
    except Exception as exc:  # noqa: BLE001 —— 兑现"不向调用方抛"
        tree_kill = job.terminate(proc, binding)
        duration = time.perf_counter() - started
        out_r, err_r = readers.get("out"), readers.get("err")
        return ExecResult(
            ok=False,
            code=-1,
            duration_s=duration,
            stdout=out_r.text if out_r else "",
            stderr=err_r.text if err_r else "",
            stdout_truncated=out_r.truncated if out_r else False,
            stderr_truncated=err_r.truncated if err_r else False,
            policy_name=policy.name,
            policy_fingerprint=policy.fingerprint(),
            tree_kill=tree_kill,
            cwd=str(cwd),
            notes=notes + [f"执行器异常（进程树已终止）：{type(exc).__name__}: {exc}"],
            error=f"执行器异常：{type(exc).__name__}: {exc}",
        )
    finally:
        for t in threads:
            try:
                t.join(timeout=1)
            except RuntimeError:
                pass
        job.close(proc)
