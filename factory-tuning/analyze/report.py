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


def _hhmmss(ms: int | None) -> str:
    """毫秒时间戳 → 本地 `HH:MM:SS`（缺了就写 `—`，不要凭空造时间）。"""
    if ms is None:
        return "—"
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S")


def _table(rows: list[list[str]], header: list[str]) -> list[str]:
    if not rows:
        return ["（无）"]
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return out


def nested_shape_rows(session) -> tuple[list[list[str]], bool]:  # noqa: ANN001
    """（端点, 数组字段, 形状）→ 元素数 + 代表 code。

    **为什么要单列一节**（2026-09-22 段 2 实测 F36）：真机对**新建**的组件发**精简形状**
    （部分 5 键 / 控件 8 键），对**服务端已经认识的**组件发**完整形状**（62 / 23 键）。
    也就是说，**"这个元素有几个键"本身回答了"服务端认不认识它"**——
    而这件事以前只能靠人肉把两次采集并排看。

    返回 `(表格行, 是否出现过两种以上形状)`。
    """
    buckets: dict[tuple[str, str, tuple[str, ...]], list[dict]] = {}
    for entry in session.write_entries:
        body = entry.post_data
        if not isinstance(body, dict):
            continue
        for name, value in body.items():
            if not isinstance(value, list):
                continue
            for element in value:
                if not isinstance(element, dict):
                    continue
                shape = tuple(sorted(str(k) for k in element))
                buckets.setdefault((entry.endpoint, str(name), shape), []).append(element)
    families: dict[tuple[str, str], set[tuple[str, ...]]] = {}
    for endpoint, name, shape in buckets:
        families.setdefault((endpoint, name), set()).add(shape)
    multi = any(len(v) > 1 for v in families.values())
    rows: list[list[str]] = []
    for (endpoint, name, shape), members in sorted(
            buckets.items(), key=lambda kv: (kv[0][0], kv[0][1], len(kv[0][2]))):
        labels = [str(m.get("code") or m.get("name") or "?") for m in members[:3]]
        if len(members) > 3:
            labels.append(f"…共 {len(members)} 个")
        rows.append([f"`{endpoint}`", f"`{name}`", f"{len(shape)} 键",
                     str(len(members)), "、".join(labels)])
    return rows, multi


#: 值域表里**跳过**的键名：它们是标识符或自由文本，不是词汇表。
#: 显式列出来（而不是靠"值多不多"去猜）——猜出来的过滤会随着数据变，
#: 而"哪一列算词汇表"应该是一个**可以审的声明**。
_VALUE_SKIP_KEYS = {
    "id", "name", "code", "attrs", "content", "helpContent", "fieldId", "sectionId",
    "objectId", "layoutId", "listlayoutId", "relationId", "selectObjectId",
    "selectListlayoutId", "objectTypeId", "requestID", "traceId", "layoutCode",
}
#: 以上述后缀结尾的键同样跳过（`xxxId` / `xxxCode` 一律是标识符）。
_VALUE_SKIP_SUFFIX = ("Id", "Code")
#: 一个键的**不同取值**超过这个数就不当词汇表看（多半是自由文本或标识符）。
_VALUE_MAX_DISTINCT = 12
#: 单个值超过这个长度就不展示（同 `audit_keys._VALUE_MAX_CHARS` 的口径）。
_VALUE_MAX_CHARS = 40


def _is_vocab_key(key: str) -> bool:
    return key not in _VALUE_SKIP_KEYS and not key.endswith(_VALUE_SKIP_SUFFIX)


def value_rows(session) -> list[list[str]]:  # noqa: ANN001
    """（端点, 字段位置）→ 真机见过的**取值**。

    **为什么要有这一节**（2026-09-22）：D-A/D-C/D-D 那一族缺陷全是"**照着猜**写的"
    ——猜键名，也猜**值**（`input`/`object`/`date` 对上平台是 `AKAttachment`…）。
    键名那一半已经有闸门（`keygate.py` 的顶层/嵌套/词表三层）；这一节是让**每一段采集
    自己把值域摊开**，于是"平台到底用哪几个词"这件事不必再靠人回忆。

    ⚠️ 过滤器是**声明式**的（`_is_vocab_key` + 取值数上限），不是"看着像不像"：
    标识符与自由文本被显式排除，剩下的才是词表候选。
    """
    seen: dict[tuple[str, str], dict[str, int]] = {}
    for entry in session.write_entries:
        body = entry.post_data
        if not isinstance(body, dict):
            continue
        pairs: list[tuple[str, object]] = [(f"`{k}`", v) for k, v in body.items()]
        for name, value in body.items():
            if isinstance(value, list):
                for element in value:
                    if isinstance(element, dict):
                        pairs += [(f"`{name}[]`.`{k}`", v) for k, v in element.items()]
        for label, value in pairs:
            key = label.split("`")[-2]
            if not _is_vocab_key(key):
                continue
            if value is None:
                text = "null"  # 与 `audit_keys.describe` 同一口径（JSON 里它就是 null）
            elif isinstance(value, bool):
                text = "true" if value else "false"
            elif isinstance(value, (int, float)):
                text = str(value)
            elif isinstance(value, str) and value and len(value) <= _VALUE_MAX_CHARS:
                text = value
            else:
                continue
            bucket = seen.setdefault((entry.endpoint, label), {})
            bucket[text] = bucket.get(text, 0) + 1
    rows: list[list[str]] = []
    for (endpoint, label), counts in sorted(seen.items()):
        if len(counts) > _VALUE_MAX_DISTINCT:
            continue
        shown = "、".join(f"`{v}`" for v, _n in sorted(counts.items())[:12])
        rows.append([f"`{endpoint}`", label, str(len(counts)), shown])
    return rows


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
        "## 0. 采集覆盖的窗口",
        "",
    ]
    # 这一节必须放最前面：它决定**整份报告怎么读**。2026-09-22 用户当面指出
    # 「现在都分不清我在哪个页面窗口操作的」——那时录制只挂一个 page，平台用
    # `window.open` 开出的设计器窗口一条都录不到，而接口只回 `page.title()`
    # （平台所有页面标题都是 `Akso eGMP (配置)`）⇒ 事后无法回答"这条请求来自哪个窗口"。
    if not session.pages:
        lines += [
            "（这次采集**没有窗口记录**。⚠️ **没有记录 ≠ 只有一个窗口**——在那个能力补上之前，"
            "「这条请求是哪个窗口发的」是**不可回答**的，别用默认值冒充证据。）",
            "",
        ]
    else:
        per_page: dict[str, int] = {}
        for entry in session.write_entries:
            key = entry.page_label or "（未标）"
            per_page[key] = per_page.get(key, 0) + 1
        page_rows = []
        for label, info in sorted(session.pages.items()):
            page_rows.append([
                f"`{label}`" + ("（主页面）" if info.get("primary") else ""),
                "已关闭" if info.get("closed") else "录制期间在场",
                str(info.get("title") or "—"),
                f"`{info.get('url') or '—'}`",
                str(per_page.get(label, 0)),
            ])
        lines += _table(page_rows, ["窗口", "状态", "标题", "页面 URL", "写请求数"])
        lines += [
            "",
            "> 读法：`p1` 是池子挂的主页面；`p2`… 是**平台自己开出来的同源新窗口**"
            "（`window.open`）。**非同源页面不进录制**（工作台 UI 自己的窗口就是被排除的那类）——",
            "> 否则产品界面的轮询流量会混进采集里，「这条来自哪个窗口」会重新变得不可信。",
            "",
        ]

    lines += ["## 1. 端点覆盖", "",
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

    lines += ["## 2.5 嵌套数组的形状谱（它回答「客户端把模型物化到什么程度」）", ""]
    array_rows, multi_shape = nested_shape_rows(session)
    if array_rows:
        lines += _table(array_rows, ["端点", "数组", "形状", "元素数", "代表 code"])
        lines += [
            "",
            "> **怎么读**：请求体里的键集 = **客户端此刻把该组件的模型物化到了什么程度**。",
            "> 段 2 曾读成「新建 = 精简 / 已有 = 完整（F36）」，**段 3 用自己的数据更正了它**：",
            "> 同一个控件在**同一次点击的三条保存**里发过 **14 / 15 / 16 / 23** 键，平台**全部接受**",
            "> ——「新建 ↔ 精简」只是**相关**，不是因果（见 F48）。缺的键全是**可空可选键**。",
            "> ⇒ **形状本身就是信息**：同一次保存里出现两种形状 ⇒ 那一次的模型是**逐步物化**的；",
            "> 只有一种形状时**别急着下结论**。",
            "> ⇒ 这条同时是 **D-D / D-E 修好之后的验收面**：工厂发出去的新组件",
            "> **不必**凑齐 23 键（真机自己就发过 14 键），但**键名必须对**。",
            "",
        ]
        if multi_shape:
            lines += ["> ★ **这次同一个数组出现了两种以上形状** ⇒ 那一次的模型是**逐步物化**的"
                      "（**不是**「两类组件」——段 3 已用同一控件的 14/15/16/23 键更正了这条读法）。", ""]
    else:
        lines += ["（这次没有抓到带嵌套数组的请求体——数组型字段只在布局/工作流这类请求里出现）", ""]

    lines += ["## 2.6 值域：真机到底用哪几个词（**猜键名的另一半是猜值**）", ""]
    value_table = value_rows(session)
    if value_table:
        lines += _table(value_table, ["端点", "字段", "取值数", "见过的取值"])
        lines += [
            "",
            "> 这一节是**给 D-A / D-C / D-D 那一族准备的**：它们的成因都一样——**照着猜**。",
            "> 既猜键名（`keygate.py` 的顶层/嵌套/词表三层已经管住），也猜**值**",
            "> （工厂写 `input`/`object`/`date`，真机是 `AKAttachment`/`AKSelectorObject`/`AKDateTime`…）。",
            "> ⇒ **改任何一个写入端点之前，先把它的词从这一节抄下来**，不要凭印象写。",
            "> 过滤器是**声明式**的：标识符（`id`/`name`/`code`/`*Id`/`*Code`…）与取值数 > 12 的键**一律不列**",
            "> ——它们是标识符或自由文本，不是词汇表。",
            "",
        ]
    else:
        lines += ["（这次没有可用于比对的短取值：请求体是动态拼的，或字段都被过滤规则排除了）", ""]

    lines += ["## 3. 操作序列（用户是怎么一步步配的）", ""]
    rows = []
    prev_at: int | None = None
    # 窗口列**只在真有窗口记录时才出现**（老会话一列"未记录"除了占地方，还会让人
    # 以为"查过了、只有一个窗口"——那正好是最该避免的误读，见 §0）
    show_page = any(e.page_label for e in session.write_entries)
    for e in session.write_entries:
        if e.at is None:
            delta = "—"
        elif prev_at is None:
            delta = "首条"
        else:
            delta = str(e.at - prev_at)
        intervals = e.buffer_intervals
        lag = e.buffer_lag
        row = [str(e.seq), _hhmmss(e.at), delta, f"`{e.endpoint}`",
               str(e.status if e.status is not None else "?"),
               e.action_summary or "—",
               "/".join(str(i) for i in intervals) if intervals else "—",
               "—" if lag is None else f"{lag}{' ⚠' if lag > 5000 else ''}"]
        if show_page:
            row.append(e.page_label or "（未标）")
        rows.append(row)
        if e.at is not None:
            prev_at = e.at
    header = ["#", "时刻", "距上条(ms)", "端点", "状态",
              "这一步之前的页面动作", "缓冲内部(ms)", "缓冲↔请求(ms)⚠"]
    lines += _table(rows, header + (["窗口"] if show_page else []))
    lines += [
        "",
        "> ⚠️ **「距上条」是最容易被忽略的一列，而它决定这一节怎么读。**",
        "> 毫秒级的**同一突发**有两种可能，读之前必须分清：",
        "> ① **一次页面事件**（渲染 / 切视图 / 拉数据）——那它**不是**用户的操作序列；",
        "> ② **一次人工操作被平台展开成的一组请求**——那它**就是**操作序列。",
        "> ⇒ **Δt 是辅助判据，不是独裁判决：必须与操作者的观察交叉**"
        "（他点了吗？界面上弹出提示了吗？）。",
        "> 两种读法**各有一次实测**：`acct-891a4f9d-20260921-210820` 的 8 条全在 **165ms** 内，",
        "> 那是**一次页面事件**（曾被误读成「用户依次配了 10 列」）；",
        "> 而 `acct-fb8dc317-20260922-102423` 的 7 条落在 **63ms** 内，**却是用户真的点了保存**",
        "> （有成功提示 + 动作缓冲里的「保存」为证）。**只看 Δt 会把后者误判成前者。**",
        "> （`AddColumns` 的键名结论**不受影响**：那条比的是**平台自己发的键名**，",
        "> 与触发方式是页面渲染还是人工点击无关。）",
        "",
        "> 这一节回答的是「人手套的顺序，与我们的 8 步管道差在哪」——",
        "> 我们的顺序见 `orchestrate.PIPELINE_STEPS`。",
        "",
        "> ⚠️ **最后一列「缓冲↔请求(ms)」不是操作节奏，是异常探测器。**",
        "> 它拿**页面**时钟减**进程**时钟：动作的 `at` 是页面上 `Date.now()`，",
        "> 而请求的时间戳是 Monitor 在**事件回调**里取的 `time.time()`——",
        "> 而回调本身可能晚到几十秒才被分发（worker 只在作业间隙泵事件）。",
        "> 实测：09-21 的会话 **0.1–3.1 秒**；09-22 的三个布局会话 **44.5 / 38.4 / 61.4 秒**。",
        "> ⇒ **不许**用它推「用户犹豫了多久」，也不许当因果（PITFALLS #74）。",
        "> 要看操作节奏，看下面 3.1 的 **缓冲内部**——那是页面时钟自己减自己。",
        "",
        "### 3.1 动作缓冲逐格（**页面时钟**：这半条时间线才是可信的）",
        "",
    ]
    timeline = [e for e in session.write_entries if len(e.buffer_intervals) >= 1]
    if not timeline:
        lines += [
            "（这次的采集里没有**带时间戳**的动作缓冲。三种可能，别混为一谈：",
            "① 那一刻页面上没有 DOM 动作；② 触发者不是点击（键盘 / iframe / shadow DOM 不进缓冲）；",
            "③ 采集端把动作的时间戳丢了——**这一条已修**，判据见 `selfcheck` 的 **H3**。）",
            "",
        ]
    else:
        for e in timeline:
            stamped = [a for a in e.actions if a.at is not None]
            steps = []
            for index, action in enumerate(stamped):
                label = (action.text.strip() or action.tag or action.type)[:20]
                gap = "" if index == 0 else f" **+{stamped[index].at - stamped[index - 1].at}ms**"
                steps.append(f"`{label}`{gap}")
            lines += [f"- **#{e.seq}** `{e.endpoint}`：{' → '.join(steps)}", ""]
    lines += [
        "> 读法：**同一个缓冲内部**的间隔回答「人在这一步是怎么点的」——",
        "> 例如「选宽度 → 停 0.76 秒 → 确认」与「点保存 → 停 2 秒 → 再点」是两种不同的操作。",
        "> 它**不**回答「这个动作是不是发出这条请求的人」：缓冲是 5 槽快照，跨操作会渗漏。",
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
