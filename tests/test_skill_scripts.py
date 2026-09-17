"""脚本式 Skill 的执行契约回归。

**这组测试为什么必须存在**：它验证的是"用户上传的代码被跑起来了"这件事的边界——
凭据拿不到、目录出不去、超时会被杀、失败可见。这些**都不报错**（脚本照跑），
只有断言能钉住。

分两层：
- **快层**：入口约定的翻译逻辑（`_interpret`）+ 包校验，不起进程。
- **慢层**（`-m slow`）：真的起子进程跑脚本，验证沙箱真的拦住了东西。
"""

from __future__ import annotations

import io
import json
import sys
import zipfile

import pytest

from workbench.plugin_api import Risk, get_hub, reset_hub
from workbench.sandbox import layout as sandbox_layout
from workbench.sandbox.runner import ExecResult
from workbench.skills import layout
from workbench.skills import library as L
from workbench.skills import security as S
from workbench.skills.manifest import SkillPackageError, parse_skill_md
from workbench.skills.script_runner import _interpret, find_python, resolve_script

SCRIPT_SKILL = """---
id: script-demo
name: 脚本技能
version: 1.0.0
description: 把输入数字加倍。
capabilities: [filesystem]
tools:
  - name: script_double
    description: 把 n 加倍后返回
    script: scripts/double.py
    args:
      n: {type: integer, description: 要加倍的整数}
---
# 说明
"""

DOUBLE_PY = """
import json, sys
payload = json.loads(sys.stdin.read() or "{}")
n = payload.get("n")
if not isinstance(n, int):
    print(json.dumps({"ok": False, "error": "n 必须是整数"}))
    sys.exit(0)
print(json.dumps({"ok": True, "result": {"doubled": n * 2}}))
"""


def make_zip(skill_md: str = SCRIPT_SKILL, files: dict[str, str] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("SKILL.md", skill_md)
        for name, text in (files or {"scripts/double.py": DOUBLE_PY}).items():
            archive.writestr(name, text)
    return buf.getvalue()


@pytest.fixture
def clean_hub():
    reset_hub()
    hub = get_hub()
    layout.ensure_dirs()
    yield hub
    for plugin in list(hub.installed_plugins()):
        hub.deregister_capability(plugin.capability_id)
    reset_hub()


def _cleanup(capability_id: str = "script-demo") -> None:
    try:
        L.delete_from_library(capability_id)
    except Exception:  # noqa: BLE001
        pass


# ─────────────────────────────────────────────────────────────────────────────
# 入口约定：manifest 契约
# ─────────────────────────────────────────────────────────────────────────────


def test_manifest_requires_wraps_or_script() -> None:
    text = SCRIPT_SKILL.replace("    script: scripts/double.py\n", "")
    with pytest.raises(SkillPackageError, match="wraps.*或.*script|script.*或.*wraps"):
        parse_skill_md(text)


def test_manifest_rejects_both_wraps_and_script() -> None:
    """二选一。回退语义（先转发、失败再跑脚本）会让失败路径不可预测。"""
    text = SCRIPT_SKILL.replace("    script: scripts/double.py",
                                "    script: scripts/double.py\n    wraps: insight_read_config")
    with pytest.raises(SkillPackageError, match="二选一"):
        parse_skill_md(text)


def test_script_requires_args() -> None:
    """脚本式没有可继承的 schema；缺了 args 工具就成了只能瞎试的黑盒。"""
    text = SCRIPT_SKILL.replace(
        "    args:\n      n: {type: integer, description: 要加倍的整数}\n", "")
    with pytest.raises(SkillPackageError, match="必须.*声明 args"):
        parse_skill_md(text)


@pytest.mark.parametrize("bad", ["../evil.py", "/abs/evil.py", "a/../../b.py", "scripts/x.txt"])
def test_manifest_rejects_bad_script_paths(bad: str) -> None:
    text = SCRIPT_SKILL.replace("scripts/double.py", bad)
    with pytest.raises(SkillPackageError):
        parse_skill_md(text)


def test_script_must_end_with_py() -> None:
    text = SCRIPT_SKILL.replace("scripts/double.py", "scripts/run.sh")
    with pytest.raises(SkillPackageError, match="只支持 Python"):
        parse_skill_md(text)


def test_script_path_shape_is_not_a_traversal() -> None:
    """**两层校验**：形状正则管字符，段语义单独管。

    这里刻意断言"正则会接受 `a/../../b.py`"——因为 `.` 是合法字符，正则**表达不了**
    段结构。初版只有正则，于是注释里"不得含 `..`"是假的。把这条断言写下来，
    后人就不会再把两层误合成一层。
    """
    from workbench.skills.manifest import _SCRIPT_PATH_RE, validate_script_path

    assert not _SCRIPT_PATH_RE.fullmatch("../evil.py"), "首段不得是 .."
    assert _SCRIPT_PATH_RE.fullmatch("a/../../b.py"), (
        "形状合法 → 正说明段语义必须由 validate_script_path 单独拒"
    )
    with pytest.raises(SkillPackageError, match="不得含"):
        validate_script_path("a/../../b.py", "tools[0]")
    for bad in ("", "  ", "/abs/x.py", "a\\b.py", "a//b.py", "a/./b.py", "a/b.txt"):
        with pytest.raises(SkillPackageError):
            validate_script_path(bad, "tools[0]")
    assert validate_script_path("scripts/double.py", "tools[0]") == "scripts/double.py"


# ─────────────────────────────────────────────────────────────────────────────
# 上传期：脚本必须真的在包里
# ─────────────────────────────────────────────────────────────────────────────


def test_upload_rejects_script_missing_from_package(clean_hub) -> None:
    """声明了 script 但包里没有 → 上传期就拒（否则装上了、调用时才报文件不存在）。"""
    result = L.upload(make_zip(files={"scripts/other.py": "print(1)"}))
    assert result.ok is False
    assert "不在包内" in result.reason


def test_upload_accepts_script_skill(clean_hub) -> None:
    result = L.upload(make_zip())
    assert result.ok is True, result.reason
    assert result.tools == ["script_double"]
    _cleanup()


# ─────────────────────────────────────────────────────────────────────────────
# 风险：脚本**不得**被降级成 read
# ─────────────────────────────────────────────────────────────────────────────


def test_script_risk_floor_is_write(clean_hub) -> None:
    """脚本是上传代码，即便声明 `risk: read` 也只升不降 → 默认必须过确认门。"""
    text = SCRIPT_SKILL.replace("    script: scripts/double.py",
                                "    script: scripts/double.py\n    risk: read")
    assert L.upload(make_zip(text)).ok is True
    L.install("script-demo")
    spec = get_hub().get("script_double")
    assert spec is not None
    assert spec.risk is Risk.WRITE, f"脚本工具的 risk 不得低于 write，得到 {spec.risk}"
    _cleanup()


def test_declarative_tool_may_stay_read(clean_hub) -> None:
    """对照：声明式可以 read（被包装工具的语义是我们自己定的）。"""
    decl = SCRIPT_SKILL.replace("    script: scripts/double.py", "    wraps: insight_read_config")
    decl = decl.replace("capabilities: [filesystem]", "capabilities: [hosttools]")
    assert L.upload(make_zip(decl)).ok is True
    L.install("script-demo")
    assert get_hub().get("script_double").risk is Risk.READ
    _cleanup()


# ─────────────────────────────────────────────────────────────────────────────
# 结果翻译：Fail-Closed
# ─────────────────────────────────────────────────────────────────────────────


def _res(code: int = 0, stdout: str = "", stderr: str = "", timed_out: bool = False) -> ExecResult:
    return ExecResult(ok=(code == 0 and not timed_out), code=code, duration_s=0.1,
                      stdout=stdout, stderr=stderr, timed_out=timed_out,
                      policy_name="skill-script", policy_fingerprint="fp",
                      cwd=str(sandbox_layout.SANDBOX_ROOT),
                      tree_kill="TerminateJobObject" if timed_out else "")


def test_interpret_success() -> None:
    out = _interpret(_res(stdout=json.dumps({"ok": True, "result": {"v": 1}})), tool_name="t")
    assert out.ok and json.loads(out.text) == {"v": 1}


def test_interpret_script_reported_failure() -> None:
    out = _interpret(_res(stdout=json.dumps({"ok": False, "error": "参数不对"})), tool_name="t")
    assert not out.ok and "参数不对" in out.error


@pytest.mark.parametrize("stdout,fragment", [
    ("", "没有向 stdout"),
    ("not json at all", "不是合法 JSON"),
    ('{"result": 1}', "缺少 `ok`"),
])
def test_interpret_rejects_malformed_stdout(stdout: str, fragment: str) -> None:
    out = _interpret(_res(stdout=stdout), tool_name="t")
    assert not out.ok, f"应判失败：{stdout!r}"
    assert fragment in out.error


def test_nonzero_exit_wins_over_valid_stdout() -> None:
    """**退出码优先于 stdout**：进程级事实优先于脚本自己声明的意图。"""
    out = _interpret(_res(code=3, stdout=json.dumps({"ok": True, "result": "看起来成功"})),
                     tool_name="t")
    assert not out.ok and "退出码 3" in out.error


def test_timeout_is_reported_with_tree_kill() -> None:
    out = _interpret(_res(timed_out=True, stderr=""), tool_name="t")
    assert not out.ok and "超时" in out.error and "TerminateJobObject" in out.error


def test_result_is_truncated_explicitly() -> None:
    """结果过长必须**显式告知**截断（与 `plugin_api.MAX_RESULT_CHARS` 同一纪律）。"""
    out = _interpret(_res(stdout=json.dumps({"ok": True, "result": "x" * 30000})), tool_name="t")
    assert out.ok and "已截断" in out.text


# ─────────────────────────────────────────────────────────────────────────────
# 路径解析：解析后判断，而不是字符串形状
# ─────────────────────────────────────────────────────────────────────────────


def test_resolve_script_blocks_escape(tmp_path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (tmp_path / "outside.py").write_text("print(1)", encoding="utf-8")
    with pytest.raises(SkillPackageError):
        resolve_script(pkg, "../outside.py")


def test_resolve_script_requires_existence(tmp_path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    with pytest.raises(SkillPackageError, match="不存在"):
        resolve_script(pkg, "nope.py")


def test_find_python_honours_override_and_rejects_missing(monkeypatch) -> None:
    monkeypatch.setenv("WORKBENCH_SKILL_PYTHON", r"C:\definitely\not\here\python.exe")
    with pytest.raises(SkillPackageError, match="不存在"):
        find_python()
    monkeypatch.delenv("WORKBENCH_SKILL_PYTHON", raising=False)
    assert find_python() == sys.executable


# ─────────────────────────────────────────────────────────────────────────────
# 慢层：真的跑子进程，验沙箱真的拦住了东西
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.slow
def test_script_runs_end_to_end(clean_hub) -> None:
    """完整链路：上传 → 安装 → 真的跑脚本 → 拿到结构化结果。

    注意 `ToolHub.execute` 对 write 风险工具会**先过确认门**——所以这里直接调
    `script_runner.run_script`（绕过确认门），确认门本身在 `test_plugin_api` 有覆盖。
    """
    from workbench.skills import script_runner

    assert L.upload(make_zip()).ok is True
    package = layout.library_version_dir("script-demo", "1.0.0", create=False)
    outcome = script_runner.run_script(
        capability_id="script-demo", version="1.0.0", package_dir=package,
        script="scripts/double.py", tool_name="script_double", args={"n": 21},
    )
    assert outcome.ok, outcome.error
    assert json.loads(outcome.text) == {"doubled": 42}
    _cleanup()


@pytest.mark.slow
def test_script_gets_identity_env_and_no_credentials(clean_hub, monkeypatch) -> None:
    """脚本能知道"我是谁"，但**拿不到宿主凭据**——沙箱 E2 在脚本路径上也要生效。"""
    from workbench.skills import script_runner

    probe = (
        "import json, os, sys\n"
        "print(json.dumps({'ok': True, 'result': {\n"
        "  'skill': os.environ.get('WORKBENCH_SKILL_ID'),\n"
        "  'tool': os.environ.get('WORKBENCH_TOOL_NAME'),\n"
        "  'leak': os.environ.get('WORKBENCH_SANDBOX_LEAK_TOKEN'),\n"
        "  'pkg': bool(os.environ.get('WORKBENCH_PACKAGE_DIR')),\n"
        "}}))\n"
    )
    monkeypatch.setenv("WORKBENCH_SANDBOX_LEAK_TOKEN", "must-not-leak")
    assert L.upload(make_zip(files={"scripts/double.py": probe})).ok is True
    package = layout.library_version_dir("script-demo", "1.0.0", create=False)
    outcome = script_runner.run_script(
        capability_id="script-demo", version="1.0.0", package_dir=package,
        script="scripts/double.py", tool_name="script_double", args={},
    )
    assert outcome.ok, outcome.error
    got = json.loads(outcome.text)
    assert got["skill"] == "script-demo"
    assert got["tool"] == "script_double"
    assert got["pkg"] is True
    assert got["leak"] is None, "宿主凭据泄漏进了 Skill 脚本"
    _cleanup()


@pytest.mark.slow
def test_script_cannot_read_outside_sandbox_root(clean_hub, tmp_path) -> None:
    """脚本的 cwd 在沙箱运行目录里——它**读不到宿主数据目录**（E3）。

    这里验的是"位置"而非"权限"：沙箱不给内核级隔离，所以断言的是
    **运行目录确实在沙箱根内**、且与库/数据目录不是同一处。
    """
    from workbench.skills import script_runner

    probe = (
        "import json, os\n"
        "print(json.dumps({'ok': True, 'result': {'cwd': os.getcwd()}}))\n"
    )
    assert L.upload(make_zip(files={"scripts/double.py": probe})).ok is True
    package = layout.library_version_dir("script-demo", "1.0.0", create=False)
    outcome = script_runner.run_script(
        capability_id="script-demo", version="1.0.0", package_dir=package,
        script="scripts/double.py", tool_name="script_double", args={},
    )
    assert outcome.ok, outcome.error
    cwd = json.loads(outcome.text)["cwd"]
    from workbench.sandbox import within_root

    assert within_root(cwd, sandbox_layout.SANDBOX_ROOT), f"运行目录不在沙箱根内：{cwd}"
    _cleanup()


@pytest.mark.slow
def test_script_timeout_kills_it(clean_hub) -> None:
    """死循环脚本必须被超时杀掉（否则一个上传的 Skill 能永久占住工作线程）。"""
    from workbench.skills import script_runner

    loop = "import time\nwhile True:\n    time.sleep(0.5)\n"
    assert L.upload(make_zip(files={"scripts/double.py": loop})).ok is True
    package = layout.library_version_dir("script-demo", "1.0.0", create=False)
    outcome = script_runner.run_script(
        capability_id="script-demo", version="1.0.0", package_dir=package,
        script="scripts/double.py", tool_name="script_double", args={}, timeout_s=3,
    )
    assert not outcome.ok
    assert "超时" in outcome.error
    _cleanup()


@pytest.mark.slow
def test_uploaded_binary_is_still_rejected_for_script_skills(clean_hub) -> None:
    """脚本式 Skill 也不能夹带二进制——准入规则不因形态而放宽。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("SKILL.md", SCRIPT_SKILL)
        archive.writestr("scripts/double.py", DOUBLE_PY)
        archive.writestr("payload.exe", "MZ")
    report = S.inspect_zip(buf.getvalue())
    assert report.verdict == "reject"
    assert L.upload(buf.getvalue()).ok is False
