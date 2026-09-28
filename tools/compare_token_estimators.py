"""token 估算的**偏差表**（计划 §7.1 点名的那一件）。

## 它回答什么

计划 §7.1 的纪律是「**先取证再换**」：

> `tools/compare_token_estimators.py` 把新旧估算与真实 `usage.prompt_tokens`
> 对同一批真实请求做偏差表 —— **估偏 > 15% 就不许上线**（估偏低会导致该折叠没折叠）。

★ 所以这个脚本是那次替换的**前置条件**，不是附属品。没有它，
"换成 tiktoken"就只是一次凭感觉的改动。

## 数据从哪来（**不需要联网、不需要 tiktoken**）

`agent_span` 的 `model` span 上同时有两个数：

| 键 | 是什么 |
|---|---|
| `attrs.estimatedTokens` | **我们算的**（`context.estimate_request_tokens`，含工具 schema） |
| `attrs.usage.prompt_tokens` | **平台算的**（真实值） |

★ 两个数记在**同一条 span** 上（不是两张表各记一半）——
比较因此不需要 join，也没有"配错了一次请求"的余地。

## 为什么它现在可能没有样本

`estimatedTokens` 是 2026-09-24 才加进 span 的 ⇒ **在那之前跑过的运行里没有这个键**。
所以样本要靠**之后的真实对话**积累。★ 没有样本时它**如实说没有**，
不编一个"平均偏差 0%"出来 —— 那正是这个脚本要防的东西。

## 用法

    .\\.venv\\Scripts\\python.exe tools\\compare_token_estimators.py
    .\\.venv\\Scripts\\python.exe tools\\compare_token_estimators.py --limit 500
    .\\.venv\\Scripts\\python.exe tools\\compare_token_estimators.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: 计划 §7.1 的门槛：估偏超过这个比例就**不许上线**。
#: ★ 方向不对称：**偏低**是危险的那一侧（该折叠没折叠 ⇒ 直到平台拒回来），
#: 所以报告里两条分别给。
TOLERANCE = 0.15

#: **评测会话的 id 前缀**（`tests/eval/runner._conv` 就是 `f"eval-{case.id}"`）。
#:
#: ★★ 为什么必须排除它们：评测跑的是**脚本化客户端**，它回一个**编好的**
#: `usage`（`prompt_tokens: 100`）—— 那个数**不是平台算的**。
#: 把它放进偏差表，会得到一个"我们高估了 28 倍"的结论，而**那个结论完全是假的**。
#:
#: ★ 这个坑我是**实测撞到的**：第一版没排除，偏差表兴高采烈地报出
#: 「24 个真实请求，比值中位数 27.9」—— 而 24 个样本**全是 `eval-*`**。
#: 它看起来像一份结果，实际是在拿我们的估算去比一个**编出来的数**。
SYNTHETIC_PREFIX = "eval-"


def collect(limit: int = 2000, *, include_synthetic: bool = False) -> list[dict]:
    """把库里「估算 vs 真实」配好对的样本取出来。

    `include_synthetic=True` 时**不排除**评测会话（调试用；
    ★ 那时候得到的偏差表**没有意义**，见 `SYNTHETIC_PREFIX` 的说明）。
    """
    from workbench import db

    db.connect()
    # ★ 排除**写在 SQL 里**，不是取回来再丢：`LIMIT` 在过滤之前的话，
    # 几千条评测 span 会把真实样本**挤没** —— 而那表现为"还没有样本"，
    # 也就是"看起来什么都没发生"（实测：第一版排除了 2000 条、一条真的都没看到）。
    where = "s.span_kind = 'model'"
    params: tuple = ()
    if not include_synthetic:
        where += (" AND (r.conversation_id IS NULL"
                  " OR r.conversation_id NOT LIKE ?)")
        params = (f"{SYNTHETIC_PREFIX}%",)
    rows = db.query(
        "SELECT s.attrs_json, r.conversation_id AS conv "
        "FROM agent_span s LEFT JOIN agent_run r ON r.id = s.run_id "
        f"WHERE {where} ORDER BY s.started_at DESC LIMIT ?",
        (*params, max(1, int(limit))),
    )
    pairs: list[dict] = []
    skipped = 0
    for row in rows:
        conv = str(row["conv"] or "")
        if not include_synthetic and conv.startswith(SYNTHETIC_PREFIX):
            skipped += 1  # 理论上不会有（SQL 已排除），留着当断言
            continue
        try:
            attrs = json.loads(row["attrs_json"] or "{}")
        except (TypeError, ValueError):
            continue
        estimated = attrs.get("estimatedTokens")
        usage = attrs.get("usage") or {}
        actual = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        # ★ 两个数**缺一不可**：只有一边的样本不能进偏差表 ——
        # 拿估算去比"另一件事的真实值"会得出一个看起来很像结论的数字。
        if isinstance(estimated, int) and isinstance(actual, int) and actual > 0:
            pairs.append({
                "estimated": estimated,
                "actual": actual,
                "conversation": conv,
                "summary": bool(attrs.get("summary")),
                "messages": attrs.get("messages"),
                "tools": attrs.get("tools"),
            })
    if skipped:
        # ★ 说清排除了多少，而不是**悄悄**少算几个 ——
        # 一个默默过滤的样本集比一个不过滤的更难查（"怎么只有这么几个样本"）
        print(f"（已排除 {skipped} 条评测会话的 span：它们的 usage 是脚本编的，不是平台算的）")
    return pairs


def deviation(pairs: list[dict]) -> dict:
    """偏差表本体。`ratio = estimated / actual`，**1.0 是完美**。"""
    ratios = [p["estimated"] / p["actual"] for p in pairs]
    return {
        "samples": len(pairs),
        "ratio_mean": statistics.fmean(ratios) if ratios else None,
        "ratio_median": statistics.median(ratios) if ratios else None,
        "ratio_min": min(ratios) if ratios else None,
        "ratio_max": max(ratios) if ratios else None,
        # ★ **低估**与**高估**分开报：它们的后果完全不同。
        # 低估 ⇒ 该折叠没折叠（直到平台把请求拒回来）；
        # 高估 ⇒ 折叠得太早（多花一次摘要调用，但不会坏）。
        "underestimated": sum(1 for r in ratios if r < 1 - TOLERANCE),
        "overestimated": sum(1 for r in ratios if r > 1 + TOLERANCE),
        "within_tolerance": sum(1 for r in ratios if abs(r - 1) <= TOLERANCE),
    }


def report(pairs: list[dict], *, include_synthetic: bool = False) -> str:
    if not pairs:
        return (
            "偏差表：**还没有样本**。\n"
            "  `attrs.estimatedTokens` 是 2026-09-24 才加进 model span 的 ——\n"
            "  在那之前跑过的运行里没有这个键。跑几轮真实对话（要有工具调用）再来看。\n"
            "  ★ 这里**不编**一个平均偏差出来：一个没有样本的偏差表和没有偏差表一样，\n"
            "    而且它更坏 —— 它看起来像验过了。"
        )
    d = deviation(pairs)
    lines = [
        f"token 估算偏差表（{d['samples']} 个请求）"
        + ("★ 含评测会话 —— **这份偏差表没有意义**（它们的 usage 是脚本编的）"
           if include_synthetic else ""),
        f"  比值 估计/真实：中位数 {d['ratio_median']:.3f}  "
        f"均值 {d['ratio_mean']:.3f}  区间 [{d['ratio_min']:.3f}, {d['ratio_max']:.3f}]",
        f"  在 ±{TOLERANCE:.0%} 内：{d['within_tolerance']}/{d['samples']}",
        f"  ★ 低估（危险侧，该折叠没折叠）：{d['underestimated']}",
        f"    高估（安全侧，折叠偏早）：  {d['overestimated']}",
        "",
    ]
    if d["underestimated"]:
        lines.append(
            f"  ⇒ 有 {d['underestimated']} 个请求被**低估**超过 {TOLERANCE:.0%}，"
            "按计划 §7.1 的口径**不许上线**新估算器（先把偏差压下来）"
        )
    else:
        lines.append(f"  ⇒ 没有请求低估超过 {TOLERANCE:.0%}（按计划 §7.1 的口径可上线）")
    # 摘要那次调用单独看：它读的是整段历史，形状与普通轮次不同
    summary = [p for p in pairs if p["summary"]]
    if summary:
        sd = deviation(summary)
        lines.append(
            f"  （其中摘要调用 {sd['samples']} 次，比值中位数 {sd['ratio_median']:.3f}）"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="token 估算偏差表（计划 §7.1）")
    parser.add_argument("--limit", type=int, default=2000, help="最多看多少条 span")
    parser.add_argument("--json", default="", help="把偏差表写成 JSON")
    parser.add_argument("--include-synthetic", action="store_true",
                        help="调试用：连评测会话一起算（得到的偏差表没有意义）")
    args = parser.parse_args()

    pairs = collect(args.limit, include_synthetic=args.include_synthetic)
    print(report(pairs, include_synthetic=args.include_synthetic))
    if args.json:
        Path(args.json).write_text(
            json.dumps({"deviation": deviation(pairs), "pairs": pairs},
                       ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"\n已写入 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
