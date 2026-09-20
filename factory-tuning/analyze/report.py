"""出报告：把覆盖分析与形状分析写成一份可读的 markdown。

    .\\.venv\\Scripts\\python.exe factory-tuning\\analyze\\report.py --latest
    .\\.venv\\Scripts\\python.exe factory-tuning\\analyze\\report.py --fixtures   # 用合成样例跑通链路

产物：`factory-tuning/reports/<日期>-<会话>.md`

## 报告里刻意有的两段

**① 「怎么读这份报告」**——它列的缺口**不是待办清单**，是待核实清单。
一次采集只覆盖你那天做的那点事；把 `missing` 直接当成"要补的功能"，
会把"那天刚好没点到"误判成"我们没有"。

**② 「本次没覆盖到的」**——`modeled_but_unseen` 单独列出，并注明**这不代表它没用**。
反过来解读（没见过 = 可以删）会删掉真实存在的功能。
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from analyze import coverage, shapes  # noqa: E402
from analyze.model import FACTORY_TUNING, load_session, pick  # noqa: E402

REPORTS = FACTORY_TUNING / "reports"


def _table(rows: list[list[str]], header: list[str]) -> list[str]:
    if not rows:
        return ["（无）"]
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return out


def build(session) -> str:  # noqa: ANN001 —— Session 见 model.py
    cov = coverage.analyze(session.entries)
    shape_rows = [s for s in shapes.analyze(session.write_entries) if s.samples]
    carried = sum(s.samples for s in shape_rows)

    lines: list[str] = []
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines += [
        f"# 配置工厂调优报告 · {session.name}",
        "",
        f"_生成于 {stamp}；本次采集 {len(session.entries)} 条请求，"
        f"其中带请求体的写请求 {carried} 条。_",
        "",
        "> **怎么读这份报告**：下面列出的「缺口」是**待核实清单，不是待办清单**。",
        "> 一次采集只覆盖你那天做过的那点事——某个端点没出现，可能只是那天没点到，",
        "> 不代表它不存在或没建模。核实顺序应当是：**先在平台上复现一次，再看它是否真缺**。",
        "",
        "## 1. 端点覆盖",
        "",
        f"- 真实用到：**{len(cov.observed)}** 个端点",
        f"- 其中**我们没有建模**：**{len(cov.missing)}** 个（覆盖率 {cov.ratio:.0%}）",
        f"- 我们建模了、这次没见到：{len(cov.modeled_but_unseen)} 个（**不代表它没用**）",
        "",
    ]

    lines += ["### 1.1 真实用到、但我们没建模的端点", ""]
    if cov.missing:
        rows = []
        for path in cov.missing:
            hits = [e for e in session.write_entries if e.endpoint == path]
            first = hits[0]
            rows.append([f"`{path}`", str(len(hits)),
                         first.action_summary or "（无页面动作记录）"])
        lines += _table(rows, ["端点", "次数", "触发它的页面操作"])
        lines += ["", "命名空间分布：" +
                  "、".join(f"`{k}` {v}" for k, v in sorted(cov.by_namespace.items())), ""]
    else:
        lines += ["（这次没有发现缺口）", ""]

    if cov.modeled_but_unseen:
        lines += ["### 1.2 我们建模了、这次没见到的端点", ""]
        lines += _table([[f"`{p}`"] for p in cov.modeled_but_unseen], ["端点"])
        lines += ["", "> ⚠️ **不要**把这一节读成「可以删」。它只说明这次采集没覆盖到。", ""]

    lines += ["## 2. 请求体形状（真实发的键 vs 蓝图模型声明的键）", ""]
    if not shape_rows:
        lines += ["（这次没有抓到带请求体的写请求）", ""]
    for index, shape in enumerate(shape_rows, 1):
        title = f"### 2.{index} `{shape.endpoint}`"
        if shape.model:
            title += f" — {shape.note}（`{shape.model}`）"
        lines += [title, "",
                  f"样本 {shape.samples} 条，真实出现 {len(shape.seen_keys)} 个业务键。", ""]
        if shape.unmodeled:
            lines += ["**平台接受、而我们的模型里没有的键**（蓝图 schema 的候选缺口）：", ""]
            lines += _table([[f"`{k}`"] for k in shape.unmodeled], ["键"])
            lines += [""]
        if shape.unseen:
            lines += ["模型里有、真实流量没出现的键（可能是我们编的，也可能只是这次没用）：", ""]
            lines += _table([[f"`{k}`"] for k in shape.unseen], ["键"])
            lines += [""]
        if not shape.unmodeled and not shape.unseen:
            lines += ["（与模型完全一致）", ""]

    lines += ["## 3. 操作序列（用户是怎么一步步配的）", ""]
    rows = [[str(e.seq), f"`{e.endpoint}`", str(e.status if e.status is not None else "?"),
             e.action_summary or "—"] for e in session.write_entries]
    lines += _table(rows, ["#", "端点", "状态", "这一步之前的页面动作"])
    lines += [
        "",
        "> 这一节回答的是「人手套的顺序，与我们的 8 步管道差在哪」——",
        "> 我们的顺序见 `orchestrate.PIPELINE_STEPS`。",
        "",
        "## 4. 下一步（人要做的）",
        "",
        "1. 挑 `1.1` 里最意外的那个端点，在平台上复现一次，确认它确实没被建模；",
        "2. 挑 `2.x` 里 `unmodeled` 最多的那个模型，决定要不要补进蓝图 schema；",
        "3. 结论写进 [docs/FACTORY-VERIFICATION-PLAN.md](../../docs/FACTORY-VERIFICATION-PLAN.md)"
        " 的台账，别只留在这份报告里。",
        "",
        "---",
        "",
        f"数据：`data/sessions/{session.name}`（已脱敏：无令牌/口令/会话 id、无主机名、"
        "账号为稳定假名。见 `tools/redact.py`）",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从采集数据出一份调优报告")
    parser.add_argument("--session", default="", help="会话目录名")
    parser.add_argument("--latest", action="store_true", help="用最近一次采集")
    parser.add_argument("--fixtures", action="store_true", help="用 tests/fixtures 下的合成样例")
    parser.add_argument("--stdout", action="store_true", help="只打到标准输出，不写文件")
    args = parser.parse_args(argv)

    directory = pick(args.session, latest=args.latest, fixtures=args.fixtures)
    session = load_session(directory)
    text = build(session)

    if args.stdout:
        print(text)
        return 0

    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / f"{dt.datetime.now():%Y-%m-%d}-{session.name}.md"
    out.write_text(text, encoding="utf-8", newline="\n")
    cov = coverage.analyze(session.entries)
    print(f"✓ 报告已写入 {out.relative_to(FACTORY_TUNING.parent)}")
    print(f"  端点覆盖：真实用到 {len(cov.observed)} 个，未建模 {len(cov.missing)} 个")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
