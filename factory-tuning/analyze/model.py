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


@dataclass
class Session:
    name: str
    meta: dict[str, Any]
    entries: list[Entry]

    @property
    def write_entries(self) -> list[Entry]:
        """只保留写请求（GET 被 Monitor 丢掉了，这里再挡一道，防数据来自别处）。"""
        return [e for e in self.entries if e.method.upper() != "GET"]


def _entry_from_row(row: dict[str, Any]) -> Entry:
    return Entry(
        seq=int(row.get("seq") or 0),
        method=str(row.get("method") or ""),
        path=str(row.get("path") or ""),
        status=row.get("status"),
        known=bool(row.get("known")),
        post_data=row.get("postData"),
        body=row.get("body"),
        actions=[Action(**{k: str(v or "") for k, v in a.items()})
                 for a in (row.get("precedingActions") or []) if isinstance(a, dict)],
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


def list_sessions() -> list[pathlib.Path]:
    if not SESSION_ROOT.exists():
        return []
    return sorted((d for d in SESSION_ROOT.iterdir() if (d / "traffic.jsonl").is_file()),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def pick(directory: str = "", *, latest: bool = False,
         fixtures: bool = False) -> pathlib.Path:
    if directory:
        return pathlib.Path(directory)
    root = FIXTURE_ROOT if fixtures else SESSION_ROOT
    candidates = sorted((d for d in root.iterdir() if (d / "traffic.jsonl").is_file()),
                        key=lambda p: p.stat().st_mtime, reverse=True) if root.exists() else []
    if not candidates:
        hint = "（--fixtures 下也没有）" if fixtures else \
            "——还没有采集数据。先跑 capture.py start/stop 再 ingest.py；" \
            "或加 --fixtures 用合成样例跑通链路。"
        raise SystemExit(f"✗ {root} 下没有会话{hint}")
    if not latest and len(candidates) > 1:
        raise SystemExit("✗ 有多个会话，请给 --latest 或 --session <名字>。可选："
                         + ", ".join(d.name for d in candidates))
    return candidates[0]
