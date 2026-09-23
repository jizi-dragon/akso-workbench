"""Monitor 解读 + 复现计划（interpreter.js / reproduce.js 的 Python 化）。

骨架报告的「待 Agent 填充」段由调用方（Agent 层）经 append_error_record /
append_pitfall_record 回填后才能展示（SKILL 纪律继承）。

## 2026-09-22：复现计划的「执行」这一半随配置工厂一并移除

`reproduce-plan.json` 曾经把每个端点映射到**写路径的具体函数**
（`API_MAP`：`/api/platform/BasicObject/SaveBasicObject` → `writers.objects.create_object` …），
再由调用方逐步确认后调用它们回放。写路径（`egmp/writers/**` + `orchestrate.py`）已整体删除，
那张表如果留着，就会写出一份**指向不存在模块的计划**——一份看起来正常、点下去才知道是坏的产物。

现在 `reproduce-plan.json` 只承载**平台侧事实**：方法 / 路径 / 原始参数，
配合 DANGEROUS 类永不入计划的既有纪律。**本仓库不再提供任何回放执行器**；
需要回放时由使用方（人或另一个工具）自己按计划操作平台。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .query_layer import classify_api, read_log_entries

#: 可入复现计划的分类：**只收 WRITE**（READ 无需回放），DANGEROUS 永不入计划
#: ——安全边界继承上游（见 docs/adr/0010-monitor-no-dangerous-replay.md）。
PLANNABLE_CLASSES = ("WRITE",)


def interpret_segment(log_path: Path, session_dir: Path, *,
                      question: str = "", on_log: Any = None) -> dict[str, Any]:
    """关闭当前 segment → 解读该段 → interpretation.md 骨架 + reproduce-plan.json。"""
    entries = read_log_entries(log_path)
    checkpoints = _read_checkpoints(session_dir / "checkpoints.jsonl")
    start_ts, end_ts = _segment_bounds(checkpoints)
    segment_entries = [e for e in entries if start_ts <= e.get("timestamp", 0) <= end_ts]

    unique_apis = sorted({f"{e.get('method')} {e.get('path')}" for e in segment_entries})
    unknown = [e for e in segment_entries if not e.get("known")]
    reproducible_steps = []
    for entry in segment_entries:
        method = str(entry.get("method") or "")
        path = str(entry.get("path") or "")
        if classify_api(method, path) in PLANNABLE_CLASSES:
            reproducible_steps.append({
                "method": method, "path": path,
                "params": entry.get("postData"), "reproducible": True,
            })

    report_path = session_dir / "interpretation.md"
    lines = [
        f"# Segment 解读（{time.strftime('%Y-%m-%d %H:%M:%S')}）",
        "",
        f"- 用户问题：{question or '（未提供）'}",
        f"- 区间条目：{len(segment_entries)}；唯一 API：{len(unique_apis)}；陌生 API：{len(unknown)}",
        "",
        "## API 摘要",
        "| 方法 | 路径 | 已知 | 状态 |",
        "|---|---|---|---|",
        *[f"| {e.get('method')} | {e.get('path')} | {'是' if e.get('known') else '否'} | {e.get('status', '—')} |"
          for e in segment_entries[:80]],
        "",
        "## 摘要（待 Agent 填充）",
        "_（骨架——按 SKILL 纪律，未回填前不得对外展示结论）_",
        "",
        "## 是否一次正确解读: 否",
        "## 是否已迭代学习: 否",
        "",
        "> 复现计划只记**平台侧事实**（方法/路径/原始参数）。本仓库不提供回放执行器",
        "> ——写路径随配置工厂一并移除（2026-09-22）；DANGEROUS 类永不入计划。",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    plan = {"baseUrl": _guess_base_url(segment_entries), "steps": reproducible_steps,
            "executor": None,
            "note": "本仓库不提供回放执行器（写路径已随配置工厂移除，2026-09-22）"}
    plan_path = session_dir / "reproduce-plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    if on_log:
        on_log(f"解读完成：{len(segment_entries)} 条 / {len(unique_apis)} API / 可复现步骤 {len(reproducible_steps)}")
    return {"report": str(report_path), "plan": str(plan_path),
            "segmentEntries": len(segment_entries), "uniqueApis": unique_apis,
            "unknownApis": len(unknown)}


def _read_checkpoints(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _segment_bounds(checkpoints: list[dict[str, Any]]) -> tuple[int, int]:
    ends = [c["timestamp"] for c in checkpoints if c.get("checkpoint") in {"user-query", "recovery"}]
    starts = [c["timestamp"] for c in checkpoints if c.get("checkpoint") == "start"]
    end_ts = ends[-1] if ends else int(time.time() * 1000)
    earlier_starts = [s for s in starts if s < end_ts]
    start_ts = earlier_starts[-1] if earlier_starts else 0
    return start_ts, end_ts


def _guess_base_url(entries: list[dict[str, Any]]) -> str:
    for entry in entries:
        url = str(entry.get("url") or "")
        if url.startswith("http"):
            return "/".join(url.split("/", 3)[:3])
    return ""


def append_error_record(session_dir: Path, error: str, root_cause: str) -> None:
    _append_md(session_dir / "interpretation.md",
               f"\n## 勘误记录\n- 错误：{error}\n- 根因：{root_cause}\n")


def append_pitfall_record(session_dir: Path, pitfall: str) -> None:
    _append_md(session_dir / "interpretation.md", f"\n## 根源性问题\n- {pitfall}\n")


def mark_iterated(session_dir: Path) -> None:
    report = session_dir / "interpretation.md"
    if report.exists():
        text = report.read_text(encoding="utf-8").replace("## 是否已迭代学习: 否",
                                                          "## 是否已迭代学习: 是")
        report.write_text(text, encoding="utf-8")


def record_feedback(session_dir: Path, issue: str, suggestion: str) -> None:
    _append_md(session_dir / "feedback.md",
               f"\n## {time.strftime('%Y-%m-%d %H:%M:%S')}\n- 问题：{issue}\n- 建议：{suggestion}\n")


def _append_md(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text)


def reproduce_plan(session_dir: Path) -> list[dict[str, Any]]:
    """读回该段的复现计划：`[{method, path, params, reproducible}]`。

    ⚠️ 计划里**只有平台侧事实**，没有"调用哪个函数"这一项——回放执行器随配置工厂移除。
    调用方不要假设存在可调用的目标（见模块文档串）。
    """
    plan = session_dir / "reproduce-plan.json"
    if not plan.exists():
        return []
    data = json.loads(plan.read_text(encoding="utf-8"))
    return list(data.get("steps", []))
