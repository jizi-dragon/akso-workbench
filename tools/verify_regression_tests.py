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
import sys

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
        # ★ 2026-09-24 换锚点：原锚点是 `context.append(assistant)` + 一个空行，
        # 而 §7.3 在它们之间插了 `transcript.record(...)` —— 于是这条**静默失效**，
        # 而本脚本遇到第一个坏锚点就停 ⇒ 其余 13 条根本没跑。
        # 现在锚在**唯一**的 `_parallel_eligible(...)` 行上，缺陷码插在它之前
        # （位置等价：仍然在第一次模型调用**之后**改写 `context[0]`，破坏前缀）。
        "前缀：工具轮次追加",
        "tests/test_cache.py::test_tool_turn_keeps_prefix_within_one_run",
        "workbench/harness/loop.py",
        '            if _parallel_eligible(assistant["tool_calls"], registry, safety, config):',
        '            context[0] = {"role": "system", "content": config.system_prompt + " "}\n'
        '            if _parallel_eligible(assistant["tool_calls"], registry, safety, config):',
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
    # 问题①：截断一旦不可见，模型看到的是"参数解析失败"，于是原样重试、又截断一次。
    (
        "问题①：输出截断必须显式说出来",
        "tests/test_agent_loop.py::test_truncated_output_is_not_reported_as_bad_args",
        "workbench/harness/loop.py",
        '            if event.get("finish_reason") == "length":\n',
        '            if False and event.get("finish_reason") == "length":\n',
    ),
    # 确认摘要（`ToolSpec.summary_fn`）：退回通用口径后，用户看到的是参数名与取值——
    #   而他要判断的是"这一按下去会发生什么"。⚠️ 本条的靶子 2026-09-22 由
    #   `tests/test_factory_agent.py`（已随配置工厂删除）换成了**合成 spec** 的用例：
    #   机制没变，只是产品里暂时没有工具在用它。
    (
        "确认摘要：summary_fn 必须真的接管文本",
        "tests/test_plugin_api.py::test_summary_fn_takes_over_the_confirmation_text",
        "workbench/approvals.py",
        "    custom = _custom_summary(spec, model)\n",
        '    custom = ""  # 缺陷版：永远用通用摘要\n',
    ),
    # 审批弹窗：Esc 一旦不被拦，原生 dialog 会直接关掉它——而用户以为那等于拒绝，
    # 循环却还在等（2026-09-20 用户反馈"审批要弹窗，不要随输出消失"）。
    (
        "审批弹窗：Esc 不等于拒绝",
        "tests/test_user_confirmation.py::test_esc_means_later_not_reject",
        "workbench/static/js/approval-modal.js",
        '  dialog.box.addEventListener("cancel", (event) => {\n'
        "    event.preventDefault();\n"
        "    deferCurrent();\n"
        "  });\n",
        "  // 缺陷版：不拦 Esc，让它直接关掉\n",
    ),

    # ── 以下 7 条是 2026-09-24 那一轮架构重构里新增的（此前只有前 14 条）
    # ── 不变量 I18：外部内容的信任标签（tests/test_trust_labels.py）
    (
        "I18：外部网页必须标 UNTRUSTED",
        "tests/test_trust_labels.py::test_the_real_web_tools_are_declared_untrusted",
        "workbench/plugins/websearch.py",
        "            trust=pa.Trust.UNTRUSTED,",
        "            trust=pa.Trust.LOCAL,",
    ),
    # ── 可撤销性声明（tests/test_undo_recipe.py，计划 §8.3）
    (
        "可撤销性：会改状态的工具必须声明",
        "tests/test_undo_recipe.py::test_a_mutating_tool_without_a_reversibility_declaration_is_refused",
        "workbench/plugin_api.py",
        "        if self.risk is not Risk.READ:\n"
        "            if self.reversibility is None:",
        "        if False:\n"
        "            if self.reversibility is None:",
    ),
    # ── transcript 保真（tests/test_transcript_roundtrip.py，计划 §7.3）
    (
        "transcript：落库的必须是当时发出去的那一条",
        "tests/test_transcript_roundtrip.py::test_the_stored_transcript_is_field_for_field_equal_to_what_was_sent",
        "workbench/harness/loop.py",
        "            transcript.record(conversation_id, assistant)\n",
        "            pass\n",
    ),
    # ── 跨重启收尾（tests/test_agent_resume.py，计划 §7.3）
    (
        "跨重启：运行开头必须补悬空调用",
        "tests/test_agent_resume.py::test_a_real_run_repairs_the_previous_crash",
        "workbench/harness/loop.py",
        "        transcript.repair_dangling(conversation_id)\n",
        "        pass\n",
    ),
    # ── token 估算的统计范围（tests/test_context_tokens.py，计划 §7.1a）
    (
        "估算：tool_calls 必须计入",
        "tests/test_context_tokens.py::test_tool_calls_are_counted",
        "workbench/harness/context.py",
        "        + estimate_tokens(tool_calls_text(m))\n",
        "",
    ),
    (
        "估算：折叠判定必须含工具 schema",
        "tests/test_context_tokens.py::test_the_folding_decision_includes_the_schema",
        "workbench/cache.py",
        # 2026-09-24 换锚点：ADR-0024 给这一行加了校准系数，原来的整行写法不再存在。
        # 而上一轮写的 tests/test_regression_harness.py 当场抓到了它（PITFALLS #115）。
        # ⚠️ 这段刻意不用反引号：用 PowerShell here-string 写会把反引号及其后字母吃掉。
        # 缺陷形态不变：折叠判定丢掉工具 schema。
        "    scale = float(factor) if float(factor) > 0 else 1.0\n"
        "    estimate = (estimate_messages_tokens(messages) + max(0, int(tools_tokens))) * scale",
        "    scale = float(factor) if float(factor) > 0 else 1.0\n"
        "    estimate = estimate_messages_tokens(messages) * scale"
    ),
    # ── 在线层闸门（tests/test_eval_live_mode.py，计划 §6.3）
    (
        "在线层：不带 --yes 时必须一次请求都不发",
        "tests/test_eval_live_mode.py::test_live_without_yes_sends_nothing",
        "tools/run_evals.py",
        "    if not args.yes:",
        "    if False:",
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
    # GBK 控制台兜底：✅/❌/中文需要 UTF-8 输出。
    # ⚠️ 少了这三行，本脚本会在**打印第一行结果**时抛 UnicodeEncodeError ——
    # 而那时它已经临时改写了生产源码，虽然 `finally` 仍会还原，但"跑到一半崩掉"
    # 会让人以为是被改写坏了（2026-09-20 实测踩到，与 PITFALLS #5 同族）。
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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
