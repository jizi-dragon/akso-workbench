"""Monitor 解读 + 复现计划 + 复现执行（interpreter.js / reproduce.js 的 Python 化）。

骨架报告的「待 Agent 填充」段由调用方（Agent 层）经 append_error_record /
append_pitfall_record 回填后才能展示（SKILL 纪律继承）。
reproduce：逐步显式授权（confirmed_per_step），DANGEROUS 类永不回放。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .query_layer import classify_api, read_log_entries

# API_MAP：端点 → Python writers 模块/函数（gmp/interpret.js 的原生对位）
API_MAP: dict[str, dict[str, str]] = {
    "/api/platform/BasicObject/SaveBasicObject": {"module": "writers.objects", "fn": "create_object"},
    "/api/platform/BasicObject/SaveField": {"module": "writers.fields", "fn": "create_field"},
    "/api/platform/ObjectPicklist/save": {"module": "writers.picklists", "fn": "create_picklist"},
    "/api/config/lifecycle/Status/Create": {"module": "writers.lifecycle", "fn": "create_status"},
    "/api/platform/Workflow/AddWorkflowBasic": {"module": "writers.workflows", "fn": "create_workflow_basic"},
    "/api/platform/Workflow/AddWorkflowTaskStep": {"module": "writers.workflows", "fn": "add_task_step"},
    "/api/platform/Workflow/AddWorkflowDecisionStep": {"module": "writers.workflows", "fn": "add_decision_step"},
    "/api/platform/Workflow/AddWorkflowActionStep": {"module": "writers.workflows", "fn": "add_action_step"},
    "/api/platform/Workflow/UpdateWorkflowStartStep": {"module": "writers.workflows", "fn": "config_start_step"},
    "/api/config/lifecycle/UserAction/Create": {"module": "writers.lifecycle", "fn": "bind_workflow_to_status"},
    "/api/platform/Layout/SaveLayoutDetail": {"module": "writers.layouts", "fn": "save_form_layout"},
    "/api/platform/Listlayout/AddColumns": {"module": "writers.layouts", "fn": "set_list_columns"},
    "/api/platform/Menu/Submit": {"module": "writers.menus", "fn": "create_parent_menu"},
}


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
        mapping = API_MAP.get(str(entry.get("path")))
        if mapping and classify_api(str(entry.get("method")), str(entry.get("path"))) != "DANGEROUS":
            reproducible_steps.append({
                "path": entry.get("path"), "module": mapping["module"], "fn": mapping["fn"],
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
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    plan = {"baseUrl": _guess_base_url(segment_entries), "steps": reproducible_steps}
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
    plan = session_dir / "reproduce-plan.json"
    if not plan.exists():
        return []
    data = json.loads(plan.read_text(encoding="utf-8"))
    return list(data.get("steps", []))
