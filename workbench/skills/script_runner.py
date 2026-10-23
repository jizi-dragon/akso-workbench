"""脚本式 Skill 的执行：把包内脚本作为一个工具跑起来，**全程在沙箱里**。

## 入口约定（写在这里就是契约，Skill 作者照此实现）

    输入：**stdin = 一个 JSON 对象**——就是模型的工具入参，原样传，不加工
    环境：WORKBENCH_SKILL_ID / WORKBENCH_SKILL_VERSION / WORKBENCH_TOOL_NAME / WORKBENCH_PACKAGE_DIR
    输出：**stdout = 一个 JSON 对象**，形状二选一：
            {"ok": true,  "result": <任意可 JSON 序列化的东西>}
            {"ok": false, "error": "人话的失败原因"}
    日志：stderr（只进审计与日志，**不回灌给模型**）
    退出码：非 0 即失败（即便 stdout 里有合法 JSON，也以退出码为准）

## 为什么用 stdin/stdout 传 JSON 而不是命令行参数

1. **不经过 shell**：参数里带引号/换行/中文时，命令行拼接是最经典的注入面。
   stdin 是字节流，没有"转义"这件事。
2. **与 MCP 的 stdio 传输同构**：将来接 MCP 时这套约定可以直接复用。

## 为什么退出码优先于 stdout

脚本可能"部分成功"却写了合法 JSON。**退出码是进程级的事实**，
而 stdout 是脚本自己声明的意图——两者冲突时以事实为准（Fail-Closed）。

## 沙箱与策略

按 manifest 的 `capabilities` 选策略：**默认取最紧的、能跑脚本的那条**
（`skill-script`：不联网、不派生进程、目录限在沙箱根内）。
`capabilities` 里声明了 `network`/`subprocess` 的包**在上传期就被拒**了
（见 `manifest.ALLOWED_CAPABILITIES`），所以这里不会出现"声明了却给不了"的错配。

## 诚实边界

沙箱不是安全边界。本模块额外做的是**把脚本关进沙箱**、
**不让它拿到凭据**、**把结果按契约解析**——而不是"跑恶意脚本也安全"。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from ..sandbox import ExecRequest, ExecResult
from ..sandbox import layout as sandbox_layout
from ..sandbox import run as sandbox_run
from .manifest import SkillPackageError

logger = logging.getLogger("workbench.skills.runner")

#: 脚本式工具使用的沙箱策略。**单一具名策略**，不由 Skill 自己挑——
#: 让上传件选策略等于让它给自己发权限。
SCRIPT_POLICY = "skill-script"

#: 回灌给模型的结果长度上限。与 `plugin_api.MAX_RESULT_CHARS` 同一个量级，
#: 理由是"工具结果最终要进模型上下文"，不设上限的脚本能一句话把上下文打满。
MAX_RESULT_CHARS = 16_000


class ScriptOutcome:
    """脚本执行结果。"""

    def __init__(self, ok: bool, text: str, error: str = "", exit_code: int = -1,
                 duration_ms: int = 0, tree_kill: str = "", timed_out: bool = False,
                 notes: list[str] | None = None) -> None:
        self.ok = ok
        self.text = text
        self.error = error
        self.exit_code = exit_code
        self.duration_ms = duration_ms
        self.tree_kill = tree_kill
        self.timed_out = timed_out
        self.notes = notes or []


#: 解释器覆盖项。**必须指向真实存在的文件**——给运维一个明确旋钮。
PYTHON_ENV_KEY = "WORKBENCH_SKILL_PYTHON"


def find_python() -> str:
    """沙箱里用的解释器。优先级：`WORKBENCH_SKILL_PYTHON` → `sys.executable`。

    为什么默认用 `sys.executable` 而**不是** `"python"` 走 PATH：
    venv / conda 下 `sys.executable` 就是正确的解释器，靠 PATH 可能解析到**另一个** Python。

    ⚠️ **打包态必须显式配置**：PyInstaller 下 `sys.executable` 是 `WorkbenchServer.exe`，
    它**不是** Python 解释器——拿它跑脚本会失败。所以冻结态下本函数**直接报错并说清怎么配**，
    而不是"试一下再说"（失败信息会出现在用户现场，必须自解释）。
    """
    override = (os.environ.get(PYTHON_ENV_KEY) or "").strip()
    if override:
        if not Path(override).is_file():
            raise SkillPackageError(f"{PYTHON_ENV_KEY} 指向的解释器不存在：{override}")
        return override

    if getattr(sys, "frozen", False):
        raise SkillPackageError(
            "打包态未配置脚本解释器：请设 WORKBENCH_SKILL_PYTHON 指向一个真实存在的 "
            "Python 可执行文件（打包态下 sys.executable 是 WorkbenchServer.exe，不能当解释器用）"
        )
    return sys.executable


def resolve_script(package_dir: Path, script: str) -> Path:
    """把 manifest 里的相对脚本路径解析成绝对路径，**并确认它没有越出包目录**。

    双重校验（manifest 已校验过一次形状）：这里做的是**解析后**的包含判断，
    防的是符号链接/`..` 之类的实际绕过，而不是字符串形状。
    """
    target = (package_dir / script).resolve()
    root = package_dir.resolve()
    if root not in target.parents and target != root:
        raise SkillPackageError(f"脚本路径越出包目录：{script!r}")
    if not target.is_file():
        raise SkillPackageError(f"脚本不存在：{script!r}（包内应有该文件）")
    return target


def prepare_run_dir(capability_id: str, version: str, package_dir: Path) -> Path:
    """把包内容复制到一次性的沙箱运行目录。

    **为什么复制而不是直接在库里跑**：脚本会写文件（临时产物、缓存），
    库必须保持"只读的权威副本"——否则一次执行就能改掉共享库里的东西，
    而库里那份是"下次安装会得到什么"的答案。
    """
    sandbox_layout.ensure_dirs()
    run_dir = sandbox_layout.run_dir(sandbox_layout.new_run_id())
    target = run_dir / "package"
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(package_dir, target, dirs_exist_ok=True)
    # 运行目录里放一份元信息，脚本可据此自我说明（也便于事后翻现场）
    (run_dir / "run.json").write_text(
        json.dumps({"capability_id": capability_id, "version": version,
                    "packages": str(target)}, ensure_ascii=False, indent=2),
        encoding="utf-8", newline="\n",
    )
    return run_dir


def run_script(
    *,
    capability_id: str,
    version: str,
    package_dir: Path,
    script: str,
    tool_name: str,
    args: dict[str, Any],
    timeout_s: float | None = None,
) -> ScriptOutcome:
    """跑一次脚本式工具。**任何失败都转成 ScriptOutcome，不向调用方抛。**"""
    try:
        resolve_script(package_dir, script)
    except SkillPackageError as exc:
        return ScriptOutcome(ok=False, text="", error=str(exc))

    run_dir = prepare_run_dir(capability_id, version, package_dir)
    sandbox_package = run_dir / "package"

    payload = json.dumps(args or {}, ensure_ascii=False)
    request = ExecRequest(
        argv=[find_python(), script],
        policy_name=SCRIPT_POLICY,
        cwd=sandbox_package,
        label=f"skill:{capability_id}/{tool_name}",
        stdin_text=payload,
        timeout_s=timeout_s,
        # 脚本要能知道"我是谁、在哪"，但**拿不到任何宿主凭据**——
        # 这几个值是常量，宿主环境仍由沙箱白名单筛过（E2 不受影响）
        extra_env={
            "WORKBENCH_SKILL_ID": capability_id,
            "WORKBENCH_SKILL_VERSION": version,
            "WORKBENCH_TOOL_NAME": tool_name,
            "WORKBENCH_PACKAGE_DIR": str(sandbox_package),
        },
    )
    result: ExecResult = sandbox_run(request)

    outcome = _interpret(result, tool_name=tool_name)
    logger.info("Skill 脚本执行：%s/%s ok=%s exit=%s %dms",
                capability_id, tool_name, outcome.ok, outcome.exit_code, outcome.duration_ms)
    return outcome


def _interpret(result: ExecResult, *, tool_name: str) -> ScriptOutcome:
    """把沙箱结果按入口约定翻译成工具结果。**Fail-Closed**。"""
    base = {
        "exit_code": result.code,
        "duration_ms": int(result.duration_s * 1000),
        "tree_kill": result.tree_kill,
        "timed_out": result.timed_out,
        "notes": list(result.notes),
    }

    if result.error:
        return ScriptOutcome(ok=False, text="", error=result.error, **base)

    if result.timed_out:
        return ScriptOutcome(
            ok=False, text="",
            error=(f"脚本超时（{SCRIPT_POLICY} 策略上限）——已终止整棵进程树："
                   f"{result.tree_kill or '（未记录手段）'}"),
            **base,
        )

    if result.code != 0:
        detail = (result.stderr or "").strip().splitlines()
        tail = " / ".join(detail[-3:]) if detail else "（无 stderr 输出）"
        return ScriptOutcome(
            ok=False, text="",
            error=f"脚本以退出码 {result.code} 结束（**退出码优先于 stdout**）：{tail}",
            **base,
        )

    raw = (result.stdout or "").strip()
    if not raw:
        return ScriptOutcome(
            ok=False, text="",
            error=("脚本没有向 stdout 写任何内容。入口约定要求输出一个 JSON 对象："
                   '{"ok": true, "result": ...} 或 {"ok": false, "error": "..."}'),
            **base,
        )

    try:
        payload = json.loads(raw)
    except ValueError as exc:
        head = raw[:200].replace("\n", " ")
        return ScriptOutcome(
            ok=False, text="",
            error=(f"脚本 stdout 不是合法 JSON（{exc}）。开头是：{head!r}。"
                   "**注意**：调试用的 print 会污染 stdout——日志请写 stderr"),
            **base,
        )

    if not isinstance(payload, dict) or "ok" not in payload:
        return ScriptOutcome(
            ok=False, text="",
            error=('脚本输出的 JSON 缺少 `ok` 字段。约定形状：'
                   '{"ok": true, "result": ...} 或 {"ok": false, "error": "..."}'),
            **base,
        )

    if not payload.get("ok"):
        reason = payload.get("error") or payload.get("message") or "（脚本未给出原因）"
        return ScriptOutcome(ok=False, text="", error=f"脚本报告失败：{reason}", **base)

    body = payload.get("result")
    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"\n…（结果过长已截断，原文 {len(text)} 字符）"
    return ScriptOutcome(ok=True, text=text, **base)
