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
    proc = subprocess.run(
        [PY, "-m", "pytest", "-o", "addopts=", "-q", test_target],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
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
