"""沙箱契约回归。

分两层，**刻意区分快慢**：

- **纯逻辑层**（策略、环境白名单、路径判定）：毫秒级，**每次必跑**。
- **真实执行层**（真的起进程、真的杀进程树）：秒级，打 `slow` 标记，
  默认由 pytest 配置排除，用 `-m slow` 显式跑。

为什么这样分：执行层测的是**内核行为**（Job Object 到底绑没绑上、树到底杀没杀干净），
它慢且依赖平台，但它恰恰是**这个模块存在的理由**——不能因为慢就不写。
排除它只是为了不让每次 `pytest` 都被拖十几秒，而不是让它"平时不跑"：
CI 里应显式跑一次 `-m slow`（见 `.github/workflows/ci.yml`）。
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from workbench.sandbox import (
    ALLOWED_EXECUTABLES,
    ExecRequest,
    SandboxError,
    SandboxPolicy,
    Severity,
    build_env,
    describe_policies,
    get_policy,
    is_sensitive_path,
    layout,
    run,
    within_root,
)

# ─────────────────────────────────────────────────────────────────────────────
# 纯逻辑层
# ─────────────────────────────────────────────────────────────────────────────


def test_policies_are_enumerable_and_self_valid() -> None:
    """策略是有限具名集合，每条都自检通过（装配期就暴露问题）。"""
    described = describe_policies()
    assert described, "至少应有一条策略"
    for policy in described:
        assert set(policy) == {"name", "severity", "timeout_s", "max_output_bytes",
                               "allow_subprocesses", "allow_network", "description",
                               "fingerprint", "enforced"}
        assert policy["enforced"], f"{policy['name']} 没有声明任何强制项"
        assert policy["fingerprint"]


def test_unknown_policy_fails_closed() -> None:
    """未知策略名**必须抛**，不许回落到默认策略——否则策略名打错等于悄悄放宽。"""
    with pytest.raises(SandboxError, match="未知沙箱策略"):
        get_policy("does-not-exist")


def test_env_whitelist_excludes_credentials() -> None:
    """E2：宿主环境里的凭据**不得**进入子进程环境。

    这条是沙箱存在的首要理由（`proc.py` 当年是 `dict(os.environ)` 全量继承）。
    """
    fake_host = {
        "PATH": "/usr/bin", "SystemRoot": r"C:\Windows", "LANG": "zh_CN.UTF-8",
        "TEMP": "/tmp", "LC_ALL": "zh_CN.UTF-8",
        # 以下都**不该**出现在结果里
        "GH_TOKEN": "ghp_x", "AWS_SECRET_ACCESS_KEY": "k", "OPENAI_API_KEY": "sk-x",
        "WORKBENCH_DB": "/secret", "MY_PASSWORD": "p", "SESSION_COOKIE": "c",
    }
    got = build_env(get_policy("skill-script"), base=fake_host)
    assert set(got) == {"PATH", "SystemRoot", "LANG", "TEMP", "LC_ALL"}
    for leaked in ("GH_TOKEN", "AWS_SECRET_ACCESS_KEY", "OPENAI_API_KEY", "WORKBENCH_DB",
                   "MY_PASSWORD", "SESSION_COOKIE"):
        assert leaked not in got, f"凭据类变量泄漏进沙箱：{leaked}"


def test_extra_env_may_not_inject_credentials() -> None:
    """策略想显式注入一个"看起来是凭据"的变量名 → 装配期就拒。"""
    with pytest.raises(SandboxError, match="疑似凭据"):
        SandboxPolicy(name="bad", severity=Severity.NORMAL, timeout_s=5,
                      extra_env=("MY_API_KEY",)).validate()


@pytest.mark.parametrize("bad_name", ["has space", "has/slash", "", "中文名"])
def test_policy_name_must_be_safe(bad_name: str) -> None:
    """策略名会被写进审计与 API 路径，形状必须受限。"""
    with pytest.raises(SandboxError):
        SandboxPolicy(name=bad_name, severity=Severity.NORMAL, timeout_s=5).validate()


def test_policy_timeout_bounds() -> None:
    with pytest.raises(SandboxError):
        SandboxPolicy(name="zero", severity=Severity.NORMAL, timeout_s=0).validate()
    with pytest.raises(SandboxError, match="上限"):
        SandboxPolicy(name="huge", severity=Severity.NORMAL, timeout_s=99999).validate()


@pytest.mark.parametrize("path,expected", [
    ("/data/runtime/browser-states/acc.json", True),
    (r"C:\Users\x\AppData\Roaming\Workbench\workbench.db", True),
    ("/d/egmp-tokens/token-a.json", True),
    ("/home/u/.ssh/id_rsa", True),
    ("/data/cookies", True),
    ("/sandbox/runs/20260101-000000-abcdef/out.txt", False),
    ("/tmp/plain.txt", False),
])
def test_sensitive_path_table(path: str, expected: bool) -> None:
    assert is_sensitive_path(path) is expected


def test_within_root_blocks_prefix_trap() -> None:
    """字符串前缀比较会把 `/sandbox/abc_evil` 判成在 `/sandbox/abc` 内。

    本仓在别处踩过同类问题（`docs/CODEBASE_OVERVIEW.md` C2 的产物路径守卫），
    所以这里必须是**解析后**比较。
    """
    assert within_root("/sandbox/abc/sub/f.txt", "/sandbox/abc") is True
    assert within_root("/sandbox/abc", "/sandbox/abc") is True
    assert within_root("/sandbox/abc_evil/f.txt", "/sandbox/abc") is False
    assert within_root("/sandbox/ab", "/sandbox/abc") is False


def test_run_id_and_capability_id_reject_path_traversal() -> None:
    """目录名会被拼进路径 → 形状不合法必须拒（本仓 C5 的 `env_id` 教训）。"""
    for bad in ("../../etc", "..", "a/b", "", "x" * 80):
        with pytest.raises(ValueError):
            layout.run_dir(bad, create=False)
    for bad in ("..", "a/b", "UPPER", "", "-lead"):
        with pytest.raises(ValueError):
            layout.staged_dir(bad, create=False)


# ── runner 的**拒绝路径**（不起进程，快）────────────────────────────────────────


def test_run_rejects_shell_executable() -> None:
    """shell 类可执行文件刻意不在名单内：否则"跑脚本"退化成"跑任意命令"。"""
    result = run(ExecRequest(argv=["cmd.exe", "/c", "echo hi"], policy_name="skill-script",
                             cwd=layout.SANDBOX_ROOT))
    assert result.ok is False
    assert "允许名单" in result.error
    assert "cmd.exe" in result.error


def test_run_rejects_cwd_outside_root(tmp_path) -> None:
    """E3：工作目录越出沙箱根 → 拒（不是警告后照跑）。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    result = run(ExecRequest(argv=[sys.executable, "-c", "print(1)"],
                             policy_name="skill-script", cwd=outside))
    assert result.ok is False
    assert "越出沙箱根" in result.error


def test_run_rejects_sensitive_cwd() -> None:
    """即便路径在沙箱根内，命中敏感表也拒（防将来根目录被挪到数据目录上层）。"""
    result = run(ExecRequest(argv=[sys.executable, "-c", "print(1)"],
                             policy_name="skill-script",
                             cwd=layout.SANDBOX_ROOT / "browser-states"))
    assert result.ok is False
    assert "敏感路径" in result.error


def test_run_rejects_timeout_above_policy() -> None:
    """策略超时是**上限**，不是建议——请求调大必须被拒。"""
    layout.ensure_dirs()
    work = layout.run_dir(layout.new_run_id())
    result = run(ExecRequest(argv=[sys.executable, "-c", "print(1)"],
                             policy_name="strict-readonly", cwd=work, timeout_s=9999))
    assert result.ok is False
    assert "上限" in result.error


def test_run_rejects_unknown_policy() -> None:
    layout.ensure_dirs()
    work = layout.run_dir(layout.new_run_id())
    result = run(ExecRequest(argv=[sys.executable, "-c", "print(1)"],
                             policy_name="nope", cwd=work))
    assert result.ok is False
    assert "未知沙箱策略" in result.error


def test_allowed_executables_excludes_shells() -> None:
    for shell in ("cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "bash", "sh"):
        assert shell not in ALLOWED_EXECUTABLES, f"{shell} 不该在沙箱可执行名单里"


# ─────────────────────────────────────────────────────────────────────────────
# 真实执行层（内核行为）—— -m slow 才跑
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.slow
def test_executes_and_captures_output() -> None:
    """正常路径：能跑、能拿到 stdout、退出码对、绑上了 Job。"""
    layout.ensure_dirs()
    work = layout.run_dir(layout.new_run_id())
    result = run(ExecRequest(argv=[sys.executable, "-c", "print('sandbox-ok')"],
                             policy_name="skill-script", cwd=work, label="probe"))
    assert result.ok, f"未成功：{result.error} {result.stderr}"
    assert "sandbox-ok" in result.stdout
    assert result.policy_fingerprint
    assert result.tree_kill == ""


@pytest.mark.slow
def test_timeout_kills_the_whole_process_tree() -> None:
    """**本模块存在的理由**：超时必须杀掉整棵树（含孙进程）。

    `services/proc.py` 只 `proc.kill()` 直接子进程，孙进程会残留——这是实测缺口。
    这里真的派生一个孙进程，超时后确认它**不在了**。
    """
    layout.ensure_dirs()
    work = layout.run_dir(layout.new_run_id())
    code = (
        "import subprocess,sys,time;"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(300)']);"
        "print(p.pid, flush=True);"
        "time.sleep(300)"
    )
    result = run(ExecRequest(argv=[sys.executable, "-c", code],
                             policy_name="skill-script", cwd=work, timeout_s=3))
    assert result.timed_out, "应当超时"
    assert not result.ok
    assert result.tree_kill, "超时后必须记录用了哪种终止手段"
    grandchild = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    if grandchild.isdigit() and sys.platform == "win32":
        listing = subprocess.run(["tasklist", "/FI", f"PID eq {grandchild}"],
                                 capture_output=True, text=True, timeout=30)
        assert grandchild not in listing.stdout, f"孙进程 {grandchild} 仍然活着——进程树没杀干净"


@pytest.mark.slow
def test_output_cap_marks_truncation() -> None:
    """输出超限必须**显式标记**，不许静默截断（否则调用方会把残日志当完整日志）。

    ⚠️ 数量要对齐上限：`strict-readonly` 的 `max_output_bytes` 是 **256 KiB**
    （初版这里印了 200_000 字符 = 约 195 KiB，**根本没触发截断**，于是"测试红了"
    看起来像功能坏了，其实是用例的算术错了）。这里印 400_000 字符以确保超过。
    """
    layout.ensure_dirs()
    work = layout.run_dir(layout.new_run_id())
    cap = get_policy("strict-readonly").max_output_bytes
    assert cap < 400_000, "上限已变大，请同步调大本用例的输出量"
    result = run(ExecRequest(argv=[sys.executable, "-c", "print('x' * 400000)"],
                             policy_name="strict-readonly", cwd=work))
    assert result.stdout_truncated is True, f"未触发截断（cap={cap}）"
    assert "上限" in result.stdout


@pytest.mark.slow
def test_credentials_not_visible_inside_sandbox() -> None:
    """端到端确认：宿主设一个凭据变量，沙箱里的进程**读不到**。"""
    layout.ensure_dirs()
    work = layout.run_dir(layout.new_run_id())
    import os

    os.environ["WORKBENCH_SANDBOX_PROBE_TOKEN"] = "must-not-leak"
    try:
        result = run(ExecRequest(
            argv=[sys.executable, "-c",
                  "import os;print('LEAK' if os.environ.get('WORKBENCH_SANDBOX_PROBE_TOKEN') else 'CLEAN')"],
            policy_name="skill-script", cwd=work))
    finally:
        os.environ.pop("WORKBENCH_SANDBOX_PROBE_TOKEN", None)
    assert result.ok, result.stderr
    assert result.stdout.strip() == "CLEAN", "宿主凭据泄漏进了沙箱"


# ─────────────────────────────────────────────────────────────────────────────
# 审计（注入式写入器：验证"记录了什么"，不需要数据库）
# ─────────────────────────────────────────────────────────────────────────────


def test_audit_records_and_strips_credentials_from_argv() -> None:
    """E4：审计要记下"谁跑的、哪份策略、树杀没杀"，且**命令行摘要里不许留凭据**。

    这条不起进程（直接构造 `ExecResult`），所以是快测——审计逻辑与执行无关。
    """
    from workbench.sandbox import audit
    from workbench.sandbox.runner import ExecResult

    captured: list[dict] = []
    audit.install(captured.append)
    try:
        result = ExecResult(
            ok=True, code=0, duration_s=0.5, stdout="hi", stderr="",
            policy_name="skill-script", policy_fingerprint="abc123",
            cwd=str(layout.SANDBOX_ROOT), tree_kill="TerminateJobObject（整棵树）",
            notes=["已绑定 Job Object"],
        )
        run_id = audit.record(
            result, label="probe", audit_id="audit-xyz",
            argv=["python", "script.py", "--api-key=SUPERSECRET", "TOKEN=abc", "normal.txt"],
        )
    finally:
        audit.reset()

    assert run_id, "装了写入器却返回空 id"
    assert len(captured) == 1
    row = captured[0]
    assert row["audit_id"] == "audit-xyz"
    assert row["policy"] == "skill-script"
    assert row["policy_fingerprint"] == "abc123"
    assert row["tree_kill"].startswith("TerminateJobObject")
    assert row["ok"] == 1
    # 凭据必须被打码；普通参数保留
    assert "SUPERSECRET" not in row["argv_summary"]
    assert "--api-key=***" in row["argv_summary"]
    assert "TOKEN=***" in row["argv_summary"]
    assert "normal.txt" in row["argv_summary"]


def test_audit_is_silent_when_no_writer_installed() -> None:
    """没接审计时**跳过但不抛**——沙箱必须能在无数据库场景（单测/脚本）下工作。"""
    from workbench.sandbox import audit
    from workbench.sandbox.runner import ExecResult

    audit.reset()
    assert audit.record(ExecResult(ok=True, code=0), label="x", argv=["python"]) == ""


def test_audit_writer_failure_does_not_raise() -> None:
    """审计写失败**绝不影响执行结果**（与 `plugin_api._audit` 同一条纪律）。"""
    from workbench.sandbox import audit
    from workbench.sandbox.runner import ExecResult

    def boom(_row):
        raise RuntimeError("db is gone")

    audit.install(boom)
    try:
        assert audit.record(ExecResult(ok=True, code=0), label="x", argv=["python"]) == ""
    finally:
        audit.reset()
