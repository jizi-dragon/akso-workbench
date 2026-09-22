"""采集数据的统一模型与加载器。

**为什么要有这一层**：`coverage.py` / `shapes.py` / `report.py` 三个分析器不该
各自去解析 JSONL——那样一旦数据形状变了，就要改三处，而漏改的那一处**不会报错**，
只会安静地少算一部分（本仓反复记过这类"两份会漂的事实源"）。
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from typing import Any

FACTORY_TUNING = pathlib.Path(__file__).resolve().parents[1]
SESSION_ROOT = FACTORY_TUNING / "data" / "sessions"
FIXTURE_ROOT = FACTORY_TUNING / "tests" / "fixtures"


@dataclass
class Action:
    """页面上的一次点击/输入（Monitor 抓的 `precedingActions`）。"""

    type: str = ""
    tag: str = ""
    text: str = ""
    #: 这次动作发生的**页面时钟**（ms）。
    #:
    #: ⚠️ 两条时间线**不同源**，用之前必须分清（PITFALLS #74）：
    #: **同一个缓冲内部**动作之间的间隔是页面时钟自己减自己 ⇒ **可信**
    #: （"整 行 → 确 定" = 760ms）；而"缓冲 ↔ 请求"的差要拿页面时钟减**进程**时钟
    #: ⇒ **不可信**（实测差 38–61 秒，见 `Entry.buffer_lag`）。
    at: int | None = None
    #: 采集端将来新增的动作字段。**保留而不是丢掉**——静默丢弃正是本仓最忌讳的形态
    #: （`ingest.clean_entry` 一度把 `at` 丢了，判据命令当场 `KeyError`）。
    extra: dict[str, str] = field(default_factory=dict)


def _action_from_row(row: dict[str, Any]) -> Action:
    """一行动作 → `Action`。

    ⚠️ 这里**不能**写成 `Action(**row)`：那样采集端每加一个字段，
    分析层就在**运行期**炸 `TypeError`（2026-09-22 实录：`ingest` 补了 `at` 之后，
    `report.py` 立刻崩在 `Action.__init__() got an unexpected keyword argument 'at'`）。
    新字段的正确处置是**收进 `extra`**，让"采集端多了东西"变成可见的数据，而不是崩溃。
    """
    known = {"type", "tag", "text"}
    at = row.get("at")
    return Action(
        type=str(row.get("type") or ""),
        tag=str(row.get("tag") or ""),
        text=str(row.get("text") or ""),
        at=int(at) if isinstance(at, (int, float)) else None,
        extra={str(k): str(v) for k, v in row.items() if k not in known and k != "at"},
    )


@dataclass
class Entry:
    """一条平台请求（已脱敏）。"""

    seq: int
    method: str
    path: str
    status: int | None = None
    known: bool = False
    post_data: Any = None
    body: Any = None
    actions: list[Action] = field(default_factory=list)
    #: 请求发出的毫秒时间戳（`ingest` 从 Monitor 的 `timestamp` 写进 `at`）。
    #:
    #: ⚠️ **这个字段一度在加载时被静默丢掉**（`_entry_from_row` 不读 `at`，`Entry` 也没有它），
    #: 后果不是报错，而是**分析器从此分不出"一次页面事件"与"一串人工操作"**：
    #: `acct-891a4f9d-20260921-210820` 的 8 条请求实测**全在 165ms 内**，
    #: 却一度被读成"用户在界面上依次配了 10 列"（见 `reports/2026-09-21-L3…复盘.md` §1）。
    #: 判据：selfcheck 的 H 组。
    at: int | None = None

    #: 这条请求来自**哪一个窗口**。`page_label`：`p1` = 池子挂的主页面，`p2`… = 同源新窗口
    #: （平台 `window.open` 开出来的设计器窗口）；`page_url` 是录制侧在作业间隙回填的页面 URL。
    #:
    #: ⚠️ **2026-09-22 之前根本没有字段可读**——录制只挂一个 page，新窗口一个字都录不到，
    #: 而产品接口只回 `page.title()`（平台所有页面标题都是 `Akso eGMP (配置)`）
    #: ⇒ "用户那次保存究竟在哪个窗口做的"事后无法回答。用户当面指出后，录制侧补了
    #: `context.on("page")` 跟随 + 每条记录带身份（见 `services/egmp/monitor/monitor.py`）。
    #: 老会话两个键都缺 ⇒ `None`，读法是**"那时没有记录窗口"**，不是"主页面"。
    page_label: str | None = None
    page_url: str | None = None

    @property
    def endpoint(self) -> str:
        """去掉查询串的路径——端点覆盖分析按它归类。"""
        return self.path.split("?", 1)[0]

    @property
    def action_summary(self) -> str:
        """把这一条之前的页面动作压成一行（报告里用）。"""
        bits = []
        for action in self.actions:
            label = action.text.strip().replace("\n", " ")
            if not label:
                label = action.tag or action.type
            bits.append(label[:24])
        return " → ".join(bits[-3:])

    @property
    def buffer_intervals(self) -> list[int]:
        """**同一个动作缓冲内部**相邻动作的间隔（ms）——这半条时间线**可信**。

        两边都取自页面时钟（`Action.at`），是自己减自己，不跨时钟域。
        实测样本：`整 行 →(1ms) INPUT →(3ms) change →(756ms) 确 定 →(1968ms) 保 存`
        ——"人先选宽度、停 0.76 秒、再确认"这种节奏只有这一列看得出来。
        """
        stamps = [a.at for a in self.actions if a.at is not None]
        return [later - earlier for earlier, later in zip(stamps, stamps[1:], strict=False)]

    @property
    def buffer_lag(self) -> int | None:
        """缓冲里**最后一次动作** → 这条请求的差（ms）。

        ⚠️ **这不是"人犹豫了多久"，也不能当因果用**（PITFALLS #74）：
        动作打的是**页面**时钟，请求打的是**进程**时钟（`monitor.py` 的事件回调里取
        `time.time()`），而且回调本身可能晚到几十秒才被分发。
        实测：09-21 的会话是 **0.1–3.1 秒**，09-22 的三个布局会话是 **44.5 / 38.4 / 61.4 秒**。
        ⇒ 留在报告里是**为了看见这个异常**，不是为了解释操作节奏。
        """
        stamps = [a.at for a in self.actions if a.at is not None]
        if self.at is None or not stamps:
            return None
        return self.at - max(stamps)


@dataclass
class Session:
    name: str
    meta: dict[str, Any]
    entries: list[Entry]

    @property
    def write_entries(self) -> list[Entry]:
        """只保留写请求（GET 被 Monitor 丢掉了，这里再挡一道，防数据来自别处）。"""
        return [e for e in self.entries if e.method.upper() != "GET"]

    @property
    def pages(self) -> dict[str, Any]:
        """这次录制**覆盖了哪些窗口**（`_pages`：label → {url,title,primary,closed}）。

        空 dict 的正确读法是**"这次录制没有窗口记录"**（老会话，或录制时还没这个能力），
        报告里一律写「未记录」——**不许**把它渲染成"只有主页面"（那是把缺省值当证据）。
        """
        pages = self.meta.get("pages")
        return pages if isinstance(pages, dict) else {}


def _entry_from_row(row: dict[str, Any]) -> Entry:
    at = row.get("at")
    return Entry(
        seq=int(row.get("seq") or 0),
        method=str(row.get("method") or ""),
        path=str(row.get("path") or ""),
        status=row.get("status"),
        known=bool(row.get("known")),
        post_data=row.get("postData"),
        body=row.get("body"),
        actions=[_action_from_row(a)
                 for a in (row.get("precedingActions") or []) if isinstance(a, dict)],
        at=int(at) if isinstance(at, (int, float)) else None,
        page_label=(str(row["pageLabel"]) if row.get("pageLabel") else None),
        page_url=(str(row["pageUrl"]) if row.get("pageUrl") else None),
    )


def load_session(directory: pathlib.Path) -> Session:
    traffic = directory / "traffic.jsonl"
    if not traffic.is_file():
        raise FileNotFoundError(f"缺少 traffic.jsonl：{directory}")
    entries: list[Entry] = []
    for line in traffic.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(_entry_from_row(json.loads(line)))
        except ValueError:
            continue
    meta_path = directory / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    return Session(name=directory.name, meta=meta, entries=entries)


def _sessions_under(root: pathlib.Path) -> list[pathlib.Path]:
    """某个根下的会话目录，按 mtime 倒序（**只认带 traffic.jsonl 的**）。"""
    if not root.exists():
        return []
    return sorted((d for d in root.iterdir() if (d / "traffic.jsonl").is_file()),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def list_sessions() -> list[pathlib.Path]:
    return _sessions_under(SESSION_ROOT)


def pick(directory: str = "", *, latest: bool = False,
         fixtures: bool = False) -> pathlib.Path:
    root = FIXTURE_ROOT if fixtures else SESSION_ROOT
    if directory:
        # `--session` 要认**两种口径**（2026-09-22 修，见 selfcheck G 组）：
        #   ① **会话目录名**：`acct-…-20260921-210820` —— README、`--help`
        #      与 `ingest.py --session <名字>` 的口径（那边一直是对的）；
        #   2 **显式路径**：相对/绝对路径，旧口径，保留兼容。
        #
        # ⚠️ 修之前这里是 `return pathlib.Path(directory)`——把①**直接当路径**，
        # 于是照着 `--help` 和自己的报错提示（下面那条 SystemExit）给会话名的人，
        # 会拿到「缺少 traffic.jsonl：<会话名>」；而**同一个参数名在 ingest.py 里
        # 一直是按名字解析的**。两套语义共用一个 flag，才是这条真正的形态。
        candidate = pathlib.Path(directory)
        if candidate.is_dir():
            return candidate
        named = root / directory
        if named.is_dir():
            return named
        available = ", ".join(d.name for d in _sessions_under(root)) or "（无）"
        raise SystemExit(f"✗ 找不到会话：{directory}\n"
                         f"  既不是目录，{root} 下也没有同名会话。可选：{available}")
    candidates = _sessions_under(root)
    if not candidates:
        hint = "（--fixtures 下也没有）" if fixtures else \
            "——还没有采集数据。先跑 capture.py start/stop 再 ingest.py；" \
            "或加 --fixtures 用合成样例跑通链路。"
        raise SystemExit(f"✗ {root} 下没有会话{hint}")
    if not latest and len(candidates) > 1:
        raise SystemExit("✗ 有多个会话，请给 --latest 或 --session <名字>。可选："
                         + ", ".join(d.name for d in candidates))
    return candidates[0]
