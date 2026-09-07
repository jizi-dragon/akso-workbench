"""对象三层理解报告编排（akso-cc understand/index.ts 的 Python 化）。

understand_object(client, code)：
  素材抓取（字段/状态/布局）→ L2 关系网 → L3 流转图（状态动作/工作流图）→
  职责标注 → Mermaid/Markdown 渲染 + drawio。
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from . import drawio as drawio_mod
from .annotate import annotate_purpose
from .crawler import Crawler
from .flowgraph import build_lifecycle_graph, build_workflow_graph
from .lifecycle import LifecycleApi
from .relations import build_relation_graph
from .render import to_markdown


def understand_object(client: Any, code: str, *, llm_summary_fn=None) -> dict[str, Any]:
    """生成单对象三层理解模型（返回 dict；调用方负责落盘）。"""
    crawler = Crawler(client)
    api = LifecycleApi(client)
    queries_meta = client.get_object(code) or {}
    object_id = str(queries_meta.get("id") or "")

    fields = crawler.page_fields(object_id)
    statuses = client.get_object_statuses(code) or []
    relations = build_relation_graph(fields, str(queries_meta.get("name") or code),
                                     bool(queries_meta.get("enableTree")))

    # 状态动作 + 按钮行为
    user_actions_by_status: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_action_ids: list[str] = []
    for status in statuses:
        status_id = str(status.get("id"))
        try:
            rows = api.user_actions(status_id)
        except Exception:  # noqa: BLE001 —— 单状态失败不阻断
            rows = []
        user_actions_by_status[status_id] = rows
        for row in rows:
            action_id = str(row.get("id"))
            if action_id and action_id not in all_action_ids:
                all_action_ids.append(action_id)

    action_bar_rows: dict[str, list[dict[str, Any]]] = {}
    try:
        for row in api.action_bar(all_action_ids):
            action_bar_rows[str(row.get("id"))] = row
    except Exception:  # noqa: BLE001
        pass

    # 工作流行 + 图
    workflows_by_row_id: dict[str, dict[str, Any]] = {}
    workflow_graphs: dict[str, dict[str, Any]] = {}
    try:
        for row in client.list_workflows() or []:
            workflows_by_row_id[str(row.get("id"))] = row
    except Exception:  # noqa: BLE001
        pass
    for row_id in workflows_by_row_id:
        try:
            steps = api.workflow_steps(row_id)
            details: dict[str, dict[str, Any]] = {}
            for step in steps:
                try:
                    details[str(step.get("id"))] = api.workflow_step_detail(str(step.get("id")))
                except Exception:  # noqa: BLE001 —— 单步骤失败不阻断
                    continue
            workflow_graphs[row_id] = build_workflow_graph(steps, details)
        except Exception:  # noqa: BLE001
            continue

    lifecycle = build_lifecycle_graph(
        statuses, user_actions_by_status, action_bar_rows, workflows_by_row_id, workflow_graphs,
    )

    purpose = annotate_purpose(queries_meta, fields, len(statuses), len(lifecycle["launchEdges"]))
    llm_summary = None
    if llm_summary_fn is not None:
        try:
            llm_summary = llm_summary_fn(queries_meta, fields, lifecycle)
        except Exception:  # noqa: BLE001 —— LLM 失败回退确定性
            llm_summary = None

    # drawio：只输出被发起（launchedByActions 非空）的工作流
    involved = []
    for wf in lifecycle["involvedWorkflows"]:
        if not wf.get("launchedByActions"):
            continue
        graph = wf.get("graph") or {"nodes": [], "edges": []}
        file_name = f"{code}-wf{len(involved) + 1}-{_safe_name(wf.get('name'))}.drawio"
        involved.append({**wf, "drawioFile": file_name,
                         "drawioXml": drawio_mod.workflow_to_drawio(graph)})

    understanding = {
        "meta": {"objectCode": code, "generatedFrom": "egmp-native"},
        "object": queries_meta,
        "fields": fields,
        "purpose": purpose,
        "relations": relations,
        "lifecycle": {**lifecycle, "involvedWorkflows": involved},
        "llmSummary": llm_summary,
        "failures": crawler.failures,
    }
    understanding["markdown"] = to_markdown(queries_meta, understanding)
    return understanding


def _safe_name(name: Any) -> str:
    text = str(name or "workflow")
    return "".join(ch for ch in text if ch not in '\\/:*?"<>|')[:40]


def write_understanding_artifacts(understanding: dict[str, Any], output_dir: Path) -> list[Path]:
    """落盘：{code}.json / {code}.md / {code}-wfN-*.drawio。返回写入文件。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    code = understanding["meta"]["objectCode"]
    written = []
    json_path = output_dir / f"{code}.json"
    json_path.write_text(_json_dumps(understanding), encoding="utf-8")
    written.append(json_path)
    md_path = output_dir / f"{code}.md"
    md_path.write_text(understanding["markdown"], encoding="utf-8")
    written.append(md_path)
    for wf in understanding["lifecycle"]["involvedWorkflows"]:
        wf_path = output_dir / wf["drawioFile"]
        wf_path.write_text(wf["drawioXml"], encoding="utf-8")
        written.append(wf_path)
    return written


def _json_dumps(data: dict[str, Any]) -> str:
    import json

    def default(obj: Any) -> Any:
        if isinstance(obj, set):
            return sorted(obj)
        return str(obj)

    return json.dumps(data, ensure_ascii=False, indent=2, default=default)
