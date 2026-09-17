"""Skill 库操作的持久化写入器（由 api 启动期注入，`skills.library` 不 import db）。

与 `sandbox.audit.install_db_writer` 同一手法：**唯一**知道 `db` 存在的地方在这里，
而它由宿主调用，不在 `library.py` 的 import 期。

## 为什么记这张表（而不是只留日志）

它回答的是"**这个工具是哪个 Skill 带进来的、什么时候装的、当时扫描结论如何**"——
本仓的工具审计（`agent_audit`）记"调用了什么"，但**记不到"工具从哪来"**。
装错了东西时，这是第一个要看的表。

文件才是权威（`<DATA_DIR>/skills/library/<id>/<version>/manifest.json`）；
本表是**动作流水**，清掉也能靠扫目录重建，反过来不行。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

logger = logging.getLogger("workbench.skills.audit")

_COLUMNS = ("id", "action", "capability_id", "version", "verdict", "detail",
            "high_findings", "created_at")

#: 写入 `detail` 的键白名单——**事件字典里可能有额外字段，一律丢弃**。
#: 与 `agent_audit` 只回摘要同一条纪律：留痕表不承接任意结构，否则它会慢慢变成垃圾场。
_DETAIL_KEYS = ("reason", "filename", "plugins", "was_installed", "high", "tools")


def _write(event: dict[str, Any]) -> None:
    from .. import db  # 本地导入：保持 skills 包可独立单测

    detail = {k: event[k] for k in _DETAIL_KEYS if k in event}
    row = {
        "id": uuid.uuid4().hex[:12],
        "action": str(event.get("action") or ""),
        "capability_id": str(event.get("capability_id") or ""),
        "version": str(event.get("version") or ""),
        "verdict": str(event.get("verdict") or ""),
        "detail": json.dumps(detail, ensure_ascii=False) if detail else "",
        "high_findings": int(event.get("high") or 0),
        "created_at": int(time.time() * 1000),
    }
    db.execute(
        f"INSERT INTO skill_audit ({', '.join(_COLUMNS)}) "
        f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
        tuple(row[c] for c in _COLUMNS),
    )


def install() -> None:
    """把写入器接到 `skills.library`（宿主启动期调用一次）。"""
    from . import library

    library.set_recorder(_write)
