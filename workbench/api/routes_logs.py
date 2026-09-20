"""运行日志查询面（`/api/logs/*`）。

## 定位

这是**运维面**，不是业务域：它的三个端点回答"刚才发生了什么"。
装配点写在 `api/__init__.py::create_app()` 里**显式注册**（不进 `_optional_routers`）
——查不到日志比少一个业务路由更严重，不该被容错注册静默跳过。

## 安全边界（务必先读）

**这些端点会返回日志原文，而日志里可能有用户数据**（对象编码、文件路径、
用户问题的长度、工具名）。因此：

1. **只在 `127.0.0.1` 暴露**——与 `/api/*` 同级，沿用本机回环这一层（既有取舍 R1b）；
   **新增本端点不得扩大它**（日志可能比业务数据更长、更杂）。
2. **导出时再脱敏一次**（`_redact_lines`）。落盘时已经过 `RedactionFilter`，
   这里是纵深防御：万一某天有人绕过 `configure()` 装了自己的 handler，导出这条路上还有一道。
3. **不提供删除/清空**。删日志的能力应当由人来文件系统操作——那是一个不可逆动作，
   不该藏在 HTTP 面里（与 `approvals.py` 对不可逆动作的态度一致）。
"""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from .. import logging_setup

router = APIRouter(prefix="/api/logs", tags=["logs"])

MAX_EXPORT_BYTES = 32 * 1024 * 1024
"""导出上限：日志文件不该被当成大文件传输通道（也避免一次请求吃满内存）。"""


@router.get("/days")
def list_days() -> dict:
    """有哪些天的日志可读（倒序）。"""
    return {
        "days": logging_setup.available_days(),
        "dir": str(logging_setup.log_dir()),
        "current": logging_setup.current_log_file().name,
    }


@router.get("/tail")
def tail(
    n: int = Query(200, ge=1, le=2000, description="最多返回多少条"),
    level: str = Query("", description="最低级别：DEBUG/INFO/WARNING/ERROR/CRITICAL"),
    contains: str = Query("", description="关键字（在整条记录里搜）"),
    trace: str = Query("", description="只看某个 traceId（响应头 X-Trace-Id 里能拿到）"),
    kind: str = Query("", description="事件类别：http / …"),
    day: str = Query("", description="YYYYMMDD，默认当天"),
) -> dict:
    """读最近 N 条日志（**从新到旧**）。

    刻意只读**一天**：日志是排障用的，不是搜索引擎；跨天分析请用 `/download` 导出。
    """
    rows = logging_setup.tail(limit=n, level=level, contains=contains,
                              trace=trace, kind=kind, day=day)
    return {
        "count": len(rows),
        "day": day or datetime.now().strftime("%Y%m%d"),
        "rows": rows,
    }


@router.get("/trace/{trace_id}")
def trace_timeline(trace_id: str, n: int = Query(500, ge=1, le=2000)) -> dict:
    """把**一次请求**的全部日志按时间正序排出来。

    这是本接口的核心用途：拿到响应头里的 `X-Trace-Id`，就能看这条请求从头到尾发生了什么。
    """
    rows = logging_setup.tail(limit=2000, trace=trace_id.strip())
    rows.reverse()  # tail 给的是"新→旧"，时间线要"旧→新"
    if not rows:
        raise HTTPException(
            404,
            f"没有 traceId={trace_id} 的记录（只查当天；跨天请用 /api/logs/download）",
        )
    return {"traceId": trace_id, "count": len(rows), "rows": rows[:n]}


def _redact_lines(text: str) -> str:
    """逐行再脱敏一次（纵深防御）。

    JSONL 行：只重打码 `msg` 字段，**不碰结构**（打码如果把引号吃掉，导出的就不是 JSON 了）；
    非 JSON 行（开发态 text 格式）：整行 `redact_text`。
    """
    out: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            out.append(logging_setup.redact_text(line))
            continue
        if isinstance(row, dict) and isinstance(row.get("msg"), str):
            row["msg"] = logging_setup.redact_text(row["msg"])
        out.append(json.dumps(row, ensure_ascii=False, default=str))
    return "\n".join(out) + "\n"


@router.get("/download", response_class=PlainTextResponse)
def download(day: str = Query("", description="YYYYMMDD，默认当天")) -> PlainTextResponse:
    """导出某一天的日志（**再脱敏一次**，`text/plain`，JSONL 行）。"""
    target_day = (day or datetime.now().strftime("%Y%m%d")).strip()
    if not target_day.isdigit() or len(target_day) != 8:
        raise HTTPException(400, "day 必须是 8 位数字，如 20260920")

    directory = logging_setup.log_dir()
    parts = sorted(
        [p for p in directory.glob(f"{logging_setup.LOG_FILE_STEM}-{target_day}.jsonl*")
         if p.is_file()],
        key=lambda p: p.stat().st_mtime,
    )
    if not parts:
        raise HTTPException(404, f"{target_day} 没有日志文件（{directory}）")

    total = sum(p.stat().st_size for p in parts)
    if total > MAX_EXPORT_BYTES:
        raise HTTPException(
            413,
            f"当天日志合计 {total / 1048576:.1f} MB，超过导出上限 "
            f"{MAX_EXPORT_BYTES // 1048576} MB；请直接在文件系统里取 "
            f"{directory}",
        )

    body = "\n".join(_redact_lines(p.read_text(encoding="utf-8", errors="replace")).rstrip("\n")
                     for p in parts) + "\n"
    return PlainTextResponse(
        body,
        headers={"Content-Disposition": f'attachment; filename="workbench-{target_day}.jsonl"'},
    )
