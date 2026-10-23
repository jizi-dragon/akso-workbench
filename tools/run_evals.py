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
| 想量"模型选得对不对" | `python tools\run_evals.py --live`（**真花钱、要配 `--yes`**）—— ★ 那**不进门禁** |

## ⚠️ 关于 `--record` 的诚实说明

当前录出来的夹具是**合成的**（`runner.ScriptedClient` 编的回答），
所以 `工具选择准确率` 量的是"**给定这样的模型输出，链路走对了吗**"，
**不是**"模型选得对吗"。每个夹具的 `note` 字段都写着这件事。

★ 为什么不干脆不提供合成夹具：因为**它们仍然锁住了请求形状**（夹具按指纹匹配），
于是"改了提示词/工具描述"会变成一次**可见的 miss** —— 而那正是这条门禁要的东西。
用脚本化客户端跑评测做不到这件事（改提示词它不会有任何反应）。

## ★ 在线层（计划 §6.3）：`--live`

```
python tools\run_evals.py --live          # 只预览：算出要发几次请求，一次都不发
python tools\run_evals.py --live --yes    # 真跑：用真实模型重录夹具
```

★ **两条命令的区别是承重的**（2026-09-24 加）：
`--live` 会**覆盖受版本控制的 `tests/eval/fixtures/*.json`**，而它发的每一次请求都花钱。
本仓在固定端口那件事上留过一条教训：
**"预览"与"真跑"如果都不报错，人就会分不清自己在哪一个里**。
所以不带 `--yes` 时**一次请求都不发**，并在最后明说"这只是预览"。

★ **它不进 CI、不进 §3 门禁矩阵**：要联网、要花钱、还依赖模型当天的状态。
门禁跑的始终是 `--offline`（回放）；`--live` 是**手动**换夹具的那一步。
★ 真机重录之后 **期望值一行都不该改** —— 夹具锁的是请求形状，不是模型选得对不对。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.eval import cases as cases_mod
from tests.eval import runner


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


def _live(args) -> int:
    """★ 在线层（计划 §6.3）：用**真实模型**重录夹具。

    ## 为什么要 `--yes`

    `--live` 一次会发 `estimate_live_calls()` 次模型请求，**真花钱**，
    而且**会覆盖受版本控制的 `tests/eval/fixtures/*.json`**。两件事都不该在
    一次"手滑多打一个词"里发生 ⇒ 不带 `--yes` 时**只打印估算与确认命令**，
    一次请求都不发（并在最后明确说"什么都没做"）。

    ★ 这不是"多一道手续"：本仓的固定端口让"跑测试"与"用户在用"
    两件事都不报错，同类风险在这里是"预览"与"真跑"都不报错 —— 所以必须显式区分。

    ## 为什么它不是门禁

    它要花 token、要联网、依赖模型当天的状态 ⇒ **不进 CI、不进 §3 门禁矩阵**。
    门禁跑的是 `--offline`（回放）。这条只是**手动**用来把夹具换成真机的那一步。
    """
    try:
        client = runner.live_client()
    except runner.NoApiKey as exc:
        print(f"✗ {exc}")
        return 1

    calls = runner.estimate_live_calls()
    cases = len(cases_mod.CASES)
    print("真机重录（在线层）")
    print(f"  用例 {cases} 条 · 估计 {calls} 次模型请求（按现有夹具里的调用数估，不是拍的）")
    print(f"  模型 {getattr(client, 'model', '?')}")
    print(f"  ⚠️ 会覆盖 {runner.FIXTURE_DIR}/*.json（受版本控制；不满意用 git checkout 还原）")

    if not args.yes:
        print("\n★ 这只是**预览**：一次请求都没发。")
        print("  确认要花这笔钱就跑：  python tools\\run_evals.py --live --yes")
        return 0

    print()
    written = runner.record_all(client=client)
    print(f"已用**真实模型**重录 {written} 份夹具 → {runner.FIXTURE_DIR}")
    print("  ⚠️ 期望值一行都不该改 —— 夹具锁的是请求形状，不是模型选得对不对")
    print("  ⚠️ `git diff tests/eval/fixtures/` 就是「请求形状变了多少」的答案")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="离线评测集（夹具回放）")
    parser.add_argument("--offline", action="store_true", help="回放全部用例并汇总")
    parser.add_argument("--record", action="store_true", help="用脚本化客户端重录夹具（合成、不花钱）")
    parser.add_argument("--live", action="store_true",
                        help="★ 用真实模型重录夹具（要花 token，需配 --yes 才真的跑）")
    parser.add_argument("--yes", action="store_true", help="配合 --live：确认真的发请求")
    parser.add_argument("--list", action="store_true", help="列出用例与期望")
    parser.add_argument("--only", default="", help="只跑这些用例（逗号分隔）")
    parser.add_argument("--json", default="", help="把汇总报告写成 JSON")
    args = parser.parse_args()

    if args.list:
        _list_cases()
        return 0

    if args.live:
        return _live(args)

    if args.record:
        written = runner.record_all()
        print(f"已重录 {written} 份夹具 → {runner.FIXTURE_DIR}")
        print(f"  ⚠️ 当前的夹具是**合成**的（{runner.SYNTHETIC_NOTE}）")
        print("  ⚠️ `git diff tests/eval/fixtures/` 就是「请求形状变了多少」的答案")
        print("  ★ 想换成真实模型的回答（要花 token）：python tools\\run_evals.py --live")
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
