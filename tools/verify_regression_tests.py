"""回归断言反证台：证明「声称反证过」的那些断言**真的会红**。

## 为什么需要它

本仓纪律（见 [docs/CODEBASE_OVERVIEW.md](../docs/CODEBASE_OVERVIEW.md) 的 C1 / C4 条目）：

> 回归断言必须"**已实测对旧实现 FAIL**"，否则它只是装饰。

**一条从没红过的测试，你不知道它测的是什么。**

## 为什么是**一个**脚本而不是三个

本文件原来有三份近乎相同的副本（`verify_prefix_tests.py` / `verify_logging_tests.py` /
以及 P0-A/P0-B 的那份）。三份复制品就是三个会各自漂的事实源——正是本仓
2026-09-15「两套工具表并存」与 2026-09-20「两张敏感表并存」的**同一种错误**。
⇒ 收成这张 `CASES` 表：加一条反证 = 加一行。

## 用法与安全

    .\\.venv\\Scripts\\python.exe tools\\verify_regression_tests.py

**手动诊断用，不进 CI**（它会临时改写生产源码）。安全措施：

- 任何异常都走 `finally` 还原；
- 结束时对每个被改过的文件**逐字节**核验，不一致 `exit 2`；
- 锚点找不到时**立即报错**（不静默跳过一项反证而假装通过）；
- 读写一律 `read_bytes()` / `write_bytes()`，锚点匹配走**归一化视图**（CRLF 工作副本下
  直接匹配会永远找不到）—— 见 PITFALLS #51。

退出码：`0` = 全部反证有效；`1` = 有反证没抓到（那断言是装饰）；`2` = 锚点失效或还原失败。
"""

from __future__ import annotations

import pathlib
import subprocess

PY = r".venv\Scripts\python.exe"

TIMEOUT_S = 120
"""单条反证的墙钟上限。

**这个超时是承重的**，不是保险丝：缺陷版的失败形态可能是"挂住"而不是"断言红"
（实时确认真答不到就会干等满一个 `timeoutS`）。没有它，一次失误会**占住 5 分钟以上**，
而且上层把它杀掉时本脚本的 `finally` 还原**不会执行** —— 2026-09-20 实测发生过一次，
`workbench/approvals.py` 里留下了缺陷版代码（PITFALLS #51 的同族）。
"""

#: (标签, 目标测试, 待改文件, 锚点, 缺陷版替换)
#:
#: 每一项都描述「把实现改回缺陷形态 → 哪条断言必须变红」。
CASES: list[tuple[str, str, str, str, str]] = [
    # ── 上下文前缀（tests/test_cache.py）
    (
        "前缀：工具轮次追加",
        "tests/test_cache.py::test_tool_turn_keeps_prefix_within_one_run",
        "workbench/harness/loop.py",
        "            context.append(assistant)\n"
        "\n"
        '            for call in assistant["tool_calls"]:',
        "            context.append(assistant)\n"
        '            context[0] = {"role": "system", "content": config.system_prompt + " "}\n'
        "\n"
        '            for call in assistant["tool_calls"]:',
    ),
    (
        "前缀：注入有界",
        "tests/test_cache.py::test_injected_tool_result_is_bounded",
        "workbench/plugin_api.py",
        "def _truncate(text: str) -> str:\n"
        "    if len(text) <= MAX_RESULT_CHARS:\n"
        "        return text",
        "def _truncate(text: str) -> str:\n    if True:\n        return text",
    ),
    # ── 日志系统（tests/test_logging.py）
    (
        "日志：归档名唯一化",
        "tests/test_logging.py::test_two_size_rolls_same_day_do_not_overwrite",
        "workbench/logging_setup.py",
        "        name = super().rotation_filename(default_name)\n"
        "        if not os.path.exists(name):\n"
        "            return name\n",
        "        return super().rotation_filename(default_name)\n"
        "        name = super().rotation_filename(default_name)\n"
        "        if not os.path.exists(name):\n"
        "            return name\n",
    ),
    (
        "日志：JSON 形态打码",
        "tests/test_logging.py::test_redaction_covers_json_shape",
        "workbench/logging_setup.py",
        "    text = _KEY_VALUE.sub(\n"
        '        lambda m: f"{m.group(\'key\')}{m.group(\'sep\')}{m.group(\'quote\')}***", text)\n'
        "    return text\n",
        "    return text\n",
    ),
    # ── P0-A：对话链路的确认门（tests/test_agent_approval.py）
    (
        "P0-A：确认门认领已批准条目",
        "tests/test_agent_approval.py::test_approved_retry_actually_executes_in_the_chat_flow",
        "workbench/approvals.py",
        "        fingerprint = _fingerprint(spec.name, args)\n"
        "        with self._lock:\n"
        "            self._expire_locked()\n"
        "            for item in self._items.values():\n"
        '                if (item.status == "approved" and item.tool == spec.name\n',
        "        return None  # 缺陷版：无令牌时不认领\n"
        "        fingerprint = _fingerprint(spec.name, args)\n"
        "        with self._lock:\n"
        "            self._expire_locked()\n"
        "            for item in self._items.values():\n"
        '                if (item.status == "approved" and item.tool == spec.name\n',
    ),
    # ── P0-B：敏感路径（tests/test_sensitive_paths.py）
    (
        "P0-B：敏感表与位置无关",
        "tests/test_sensitive_paths.py::test_read_local_file_rejects_credential_artifacts",
        "workbench/plugins/builtins.py",
        "    if config.is_sensitive_path(resolved):\n",
        "    if False and config.is_sensitive_path(resolved):  # 缺陷版：应用目录内不查\n",
    ),
    # ── 父进程监视（tests/test_lifecycle.py，**slow 层**：真起父子进程）
    (
        "生命周期：父死子退（防孤儿占端口）",
        "tests/test_lifecycle.py::test_sidecar_exits_when_parent_is_force_killed",
        "workbench/lifecycle.py",
        "        pid = parent_pid()\n"
        "        if pid is None:\n",
        '        return "缺陷版：不监视"\n'
        "        pid = parent_pid()\n"
        "        if pid is None:\n",
    ),
    # ── 实时确认（tests/test_user_confirmation.py）
    #
    # 这一条抓的是"答复先到、等待者后到"的 lost wakeup（PITFALLS #57）：缺陷版把
    # `_decisions` 那条着陆点去掉，于是用户在 `confirm_required` 回调里点的同意无处落地，
    # 循环干等满 timeoutS。锚点选 `wait_decision` 入口那三行——它正是"先查答复再取等待者"。
    (
        "实时确认：答复先到时不能丢（lost wakeup）",
        "tests/test_user_confirmation.py::test_loop_pauses_then_resumes_and_preapproval_works",
        "workbench/approvals.py",
        "            early = self._decisions.pop(pending_id, None)\n",
        "            early = None  # 缺陷版：不查已到的答复\n",
    ),
    # 这一条抓的是"预先批准绑定参数指纹"（ADR-0016 §D-3）：缺陷版让确认去批准**任意**参数，
    # 于是"同意 X(A)"变成了"同意 X 的一切"。
    (
        "实时确认：同意 A 不等于同意 B",
        "tests/test_user_confirmation.py::test_confirming_one_call_does_not_authorize_another",
        "workbench/approvals.py",
        '                if (item.status == "approved" and item.tool == spec.name\n'
        "                        and item.fingerprint == fingerprint):\n",
        '                if (item.status == "approved" and item.tool == spec.name):\n',
    ),
    # 这一条抓的是"装配期自检挡住无限递归"：缺陷版把 `needs_confirmation` + 非只读这条检查去掉，
    # 于是那个"确认工具自己也要先过确认门"的配置能装进去。
    (
        "实时确认：确认工具自身必须是只读",
        "tests/test_user_confirmation.py::test_confirmation_tool_must_be_read",
        "workbench/plugin_api.py",
        "        if self.needs_confirmation and self.risk is not Risk.READ:\n",
        "        if False and self.needs_confirmation and self.risk is not Risk.READ:\n",
    ),
    # 这一条抓的是 PITFALLS #56：`_validate_tools` 一旦退回"逐字段手写重建"，
    # 新字段就在装配时被静默丢掉（工具还在、行为没了）。
    (
        "装配：新增声明字段不被静默丢掉",
        "tests/test_user_confirmation.py::test_real_builtin_tool_declaration_is_valid",
        "workbench/plugin_api.py",
        "            bound = replace(spec, plugin_id=plugin.id)\n",
        "            bound = ToolSpec(\n"
        "                name=spec.name, description=spec.description,\n"
        "                args_model=spec.args_model, handler=spec.handler, risk=spec.risk,\n"
        "                plugin_id=plugin.id, timeout_s=spec.timeout_s,\n"
        "                audit_extra=spec.audit_extra,\n"
        "            )\n",
    ),
]


def read_source(path: pathlib.Path) -> tuple[bytes, str]:
    data = path.read_bytes()
    return data, data.decode("utf-8")


def write_source(path: pathlib.Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def apply_patch(text: str, anchor: str, replacement: str) -> str | None:
    """按 LF 归一化匹配、按原风格落盘（CRLF 工作副本下直接匹配会永远找不到）。"""
    crlf = "\r\n" in text
    normalized = text.replace("\r\n", "\n")
    if anchor not in normalized:
        return None
    patched = normalized.replace(anchor, replacement, 1)
    return patched.replace("\n", "\r\n") if crlf else patched


def run_one(test_target: str) -> tuple[int, str]:
    """跑一条反证。**必须有超时**——见下面 `TIMEOUT_S` 的说明。"""
    try:
        proc = subprocess.run(
            [PY, "-m", "pytest", "-o", "addopts=", "-q", test_target],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        # 超时本身**就是抓到了**：缺陷版的典型表现不是断言失败，而是**挂住**
        # （实时确认那条反证修复前会干等满一个 timeoutS）。
        # ⚠️ 但超时也会让本进程被上层杀掉，于是 `finally` 里的还原**跑不到**——
        # 2026-09-20 实测：一次 300s 超时之后 `workbench/approvals.py` 里留着
        # `early = None  # 缺陷版：不查已到的答复`。所以这里必须自己兜住超时，
        # 绝不能靠"外层会杀我"。
        return 1, f"超时 {TIMEOUT_S}s 未结束（缺陷版挂住 = 抓到了）"
    lines = [ln for ln in proc.stdout.splitlines()
             if "passed" in ln or "failed" in ln or "error" in ln]
    return proc.returncode, (lines[-1] if lines else proc.stdout.strip()[-160:])


def main() -> int:
    originals: dict[pathlib.Path, bytes] = {}
    caught = 0
    try:
        for label, test_target, file_name, anchor, replacement in CASES:
            path = pathlib.Path(file_name)
            if path not in originals:
                originals[path] = read_source(path)[0]
            _, text = read_source(path)

            patched = apply_patch(text, anchor, replacement)
            if patched is None:
                print(f"✗ {label}：锚点未找到——{file_name} 结构变了，请更新本脚本")
                return 2
            write_source(path, patched)
            try:
                code, msg = run_one(test_target)
            finally:
                # 每条反证跑完立刻按**原始字节**还原，下一条从干净状态开始
                path.write_bytes(originals[path])
            ok = code != 0
            caught += ok
            print(f"[{label}] 期望 FAIL → "
                  f"{'FAIL ✅ 抓到了' if ok else 'PASS ❌ 没抓到！'}  | {msg}")
    finally:
        for path, raw in originals.items():
            path.write_bytes(raw)

    bad = [str(p) for p, raw in originals.items() if p.read_bytes() != raw]
    print(f"\n还原核验（逐字节）：{len(originals)} 个文件，"
          f"{'全部一致 ✅' if not bad else f'以下不一致 ❌ {bad}'}")
    if bad:
        print("✗ 还原失败——请 `git checkout --` 上面这些文件")
        return 2

    print(f"反证结论：{len(CASES)} 项里 {caught} 项有效")
    return 0 if caught == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
