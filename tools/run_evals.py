r"""跑评测集（计划 §6.3）。

    python tools\run_evals.py --offline            # 回放夹具（不联网、不花 token）
    python tools\run_evals.py --offline --json out.json
    python tools\run_evals.py --record             # ★ 重录夹具（合成；要换成真机见下）
    python tools\run_evals.py --list               # 列出用例与它们的期望

## 三种用法各自的场合

| 什么时候跑 | 用哪条 |
|---|---|
| 改了 `harness/` / `prompts.py` / `plugin_api.py` / 工具描述 | `--offline`，并把 before/after 数附在提交里（计划 §6.3 的门禁） |
| `--offline` 报"回放 miss" | 那是**请求形状变了**，不是行为变了 ⇒ 先看清 diff 再 `--record` |
| 想量"模型选得对不对" | 把 `runner.ScriptedClient` 换成真 `DeepSeekClient` 再 `--record` —— ★ 那要花 token，且**不进门禁** |

## ⚠️ 关于 `--record` 的诚实说明

当前录出来的夹具是**合成的**（`runner.ScriptedClient` 编的回答），
所以 `工具选择准确率` 量的是"**给定这样的模型输出，链路走对了吗**"，
**不是**"模型选得对吗"。每个夹具的 `note` 字段都写着这件事。

★ 为什么不干脆不提供合成夹具：因为**它们仍然锁住了请求形状**（夹具按指纹匹配），
于是"改了提示词/工具描述"会变成一次**可见的 miss** —— 而那正是这条门禁要的东西。
用脚本化客户端跑评测做不到这件事（改提示词它不会有任何反应）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.eval import cases as cases_mod  # noqa: E402
from tests.eval import runner  # noqa: E402


def _list_cases() -> None:
    print(f"评测用例（{len(cases_mod.CASES)} 条）\n")
    for case in cases_mod.CASES:
        print(f"  {case.id}")
        print(f"     {case.title}")
        print(f"     期望工具：{case.expect_tools or '（不调工具）'}")
        if case.expect_events:
            print(f"     期望事件：{case.expect_events}")
        if case.expect_handler_not_run:
            print(f"     ★ 绝不执行：{case.expect_handler_not_run}")
        if case.note:
            print(f"     注：{case.note}")


def main() -> int:
    parser = argparse.ArgumentParser(description="离线评测集（夹具回放）")
    parser.add_argument("--offline", action="store_true", help="回放全部用例并汇总")
    parser.add_argument("--record", action="store_true", help="重录夹具")
    parser.add_argument("--list", action="store_true", help="列出用例与期望")
    parser.add_argument("--only", default="", help="只跑这些用例（逗号分隔）")
    parser.add_argument("--json", default="", help="把汇总报告写成 JSON")
    args = parser.parse_args()

    if args.list:
        _list_cases()
        return 0

    if args.record:
        written = runner.record_all()
        print(f"已重录 {written} 份夹具 → {runner.FIXTURE_DIR}")
        print(f"  ⚠️ 当前的夹具是**合成**的（{runner.SYNTHETIC_NOTE}）")
        print("  ⚠️ `git diff tests/eval/fixtures/` 就是「请求形状变了多少」的答案")
        return 0

    if not args.offline:
        parser.print_help()
        return 2

    only = [x.strip() for x in args.only.split(",") if x.strip()]
    results = runner.run_offline(only or None)
    summary = runner.summarize(results)
    print(runner.format_report(summary, results))

    if args.json:
        Path(args.json).write_text(
            json.dumps({"summary": summary,
                        "cases": [vars(r) for r in results]},
                       ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"\n报告已写入 {args.json}")

    return 0 if not summary["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
