"""Mermaid 三图 + 三层理解报告渲染（akso-cc understand/render.ts 的 Python 化）。"""

from __future__ import annotations

from typing import Any

from .drawio import esc


def _mid(text: Any, fallback: str = "?") -> str:
    value = str(text or fallback).replace('"', "'").strip()
    return value or fallback


def mermaid_relations(graph: dict[str, Any]) -> str:
    lines = ["graph LR"]
    for edge in graph.get("edges", []):
        arrow = "-.->" if edge.get("selfLoop") else "-->"
        label = f"|{_mid(edge.get('fieldName'), edge.get('field'))}|" if edge.get("field") else ""
        lines.append(f"  {_mid(edge['source'])} {arrow}{label} {_mid(edge['target'])}")
    return "\n".join(lines)


def mermaid_state_machine(lifecycle: dict[str, Any]) -> str:
    lines = ["stateDiagram-v2"]
    states = lifecycle.get("states", [])
    if states:
        lines.append("  [*] --> " + _mid(states[0].get("name")))
    for state in states:
        name = _mid(state.get("name"))
        lines.append(f"  {name} : {_mid(state.get('code'))}")
    for edge in lifecycle.get("userActionEdges", []):
        src = _mid(_state_name(lifecycle, edge.get("source")))
        tgt = _mid(_state_name(lifecycle, edge.get("target"), edge.get("targetName")))
        lines.append(f"  {src} --> {tgt} : {_mid(edge.get('button'))}")
    for edge in lifecycle.get("enterActionEdges", []):
        src = _mid(_state_name(lifecycle, edge.get("source")))
        tgt = _mid(_state_name(lifecycle, edge.get("target"), edge.get("targetName")))
        lines.append(f"  {src} --> {tgt} : 进入动作·{_mid(edge.get('action'))}")
    for edge in lifecycle.get("launchEdges", []):
        src = _mid(_state_name(lifecycle, edge.get("source")))
        lines.append(f"  {src} --> {_mid(edge.get('workflowName'))} : 发起工作流")
    return "\n".join(lines)


def _state_name(lifecycle: dict[str, Any], state_id: Any, fallback: Any = None) -> str:
    for state in lifecycle.get("states", []):
        if str(state.get("id")) == str(state_id):
            return str(state.get("name"))
    return str(fallback or state_id or "?")


def mermaid_workflow(graph: dict[str, Any]) -> str:
    lines = ["flowchart TD"]
    name_by_id = {str(n.get("id")): _mid(n.get("name")) for n in graph.get("nodes", [])}
    for edge in graph.get("edges", []):
        label = f"|{_mid(edge.get('label'))}|" if edge.get("label") else ""
        lines.append(
            f"  {name_by_id.get(str(edge['source']), edge['source'])} -->{label} "
            f"{name_by_id.get(str(edge['target']), edge['target'])}"
        )
    return "\n".join(lines)


def to_markdown(object_meta: dict[str, Any], understanding: dict[str, Any]) -> str:
    """三层理解报告（render.ts 章节口径）。"""
    lifecycle = understanding.get("lifecycle", {})
    parts: list[str] = []
    name = _mid(object_meta.get("name"))
    code = _mid(object_meta.get("code"))
    parts.append(f"# 对象理解报告：{name}（{code}）")
    parts.append("")

    # L1
    fields = understanding.get("fields", [])
    parts.append("## 一、基础理解（L1：有什么）")
    parts.append(f"- 对象类型：{_mid(object_meta.get('objectClass'))}；生命周期："
                 f"{'启用（' + _mid(object_meta.get('lifeCycleName')) + '）' if object_meta.get('enableLifeCycle') else '未启用'}")
    parts.append(f"- 字段 {len(fields)} 个；生命周期状态 {len(lifecycle.get('states', []))} 个；"
                 f"涉及工作流 {len(lifecycle.get('involvedWorkflows', []))} 条")
    parts.append("")

    parts.append("### 职责理解")
    purpose = understanding.get("purpose", {})
    parts.append(purpose.get("summary") or "_（确定性标注为空）_")
    if purpose.get("traits"):
        parts.append("")
        parts.append("| 判定 | 依据 |")
        parts.append("|---|---|")
        for trait in purpose["traits"]:
            parts.append(f"| {_mid(trait.get('trait'))} | {_mid(trait.get('evidence'))} |")
    if understanding.get("llmSummary"):
        parts.append("")
        parts.append(f"> LLM 解读：{understanding['llmSummary']}")
    parts.append("")

    parts.append("### 字段构成")
    field_rows = []
    for field in fields[:60]:
        ref = field.get("referenceObjectCode")
        related = f"→ {ref}" if ref else ""
        field_rows.append([field.get("name"), field.get("code"), field.get("dataType"), related,
                           "必填" if field.get("isRequired") else ""])
    parts.append("| 名称 | 编码 | 类型 | 关联 | 必填 |" if field_rows else "_（无字段）_")
    if field_rows:
        parts.append("|---|---|---|---|---|")
        for row in field_rows:
            parts.append("| " + " | ".join(str(c or "—") for c in row) + " |")
    parts.append("")

    parts.append("### 生命周期状态与动作明细")
    parts.append("> 平台实证口径：进入条件语义取自 UserAction/GetList 的 description；"
                 "进入动作的结构化配置无公开读接口。")
    parts.append("")
    if lifecycle.get("states"):
        parts.append("| 生命周期状态名称 | 进入动作 | 用户动作 | 进入条件 |")
        parts.append("|---|---|---|---|")
        for state in lifecycle["states"]:
            enter = "、".join(
                _mid(a.get("action")) for a in lifecycle.get("enterActionEdges", [])
                if str(a.get("source")) == str(state.get("id"))
            ) or "—"
            user = "、".join(
                _mid(e.get("button")) for e in lifecycle.get("userActionEdges", [])
                if str(e.get("source")) == str(state.get("id"))
            ) or "—"
            conditions = "；".join(
                filter(None, (_mid(a.get("description")) for a in state.get("actions", [])
                              if a.get("executeType") == 2 and a.get("description")))
            ) or "—"
            parts.append(f"| {_mid(state.get('name'))} | {enter} | {user} | {conditions} |")
    else:
        parts.append("_（未启用生命周期）_")
    parts.append("")

    if lifecycle.get("involvedWorkflows"):
        parts.append("### 动作涉及的工作流")
        parts.append("| 触发来源 | 工作流 | 步骤数 | drawio |")
        parts.append("|---|---|---|---|")
        for wf in lifecycle["involvedWorkflows"]:
            graph = wf.get("graph") or {}
            parts.append(f"| {_mid(wf.get('launchedByActions'))} | {_mid(wf.get('name'))} | "
                         f"{len(graph.get('nodes', []))} | {esc(wf.get('drawioFile') or '—')} |")
        parts.append("")

    # L2
    parts.append("## 二、关系网（L2：与谁关联）")
    parts.append("```mermaid")
    parts.append(mermaid_relations(understanding.get("relations", {})))
    parts.append("```")
    parts.append("")

    # L3
    parts.append("## 三、生命周期流转（L3：如何流转）")
    parts.append("```mermaid")
    parts.append(mermaid_state_machine(lifecycle))
    parts.append("```")
    for wf in lifecycle.get("involvedWorkflows", []):
        graph = wf.get("graph")
        if graph and graph.get("nodes"):
            parts.append("")
            parts.append(f"**{_mid(wf.get('name'))}**")
            parts.append("")
            parts.append("```mermaid")
            parts.append(mermaid_workflow(graph))
            parts.append("```")
    parts.append("")

    parts.append("## 已知边界")
    parts.append("- 进入动作的结构化条件/行为配置无公开读接口；条件语义仅取 description")
    parts.append("- workflowCancelStatus 为平台全局配置 id，不作为「取消后流转到」的边")
    if lifecycle.get("unmatchedButtons"):
        parts.append(f"- 未匹配按钮 {len(lifecycle['unmatchedButtons'])} 个（GetList 与 GetActionBar id 不一致且名称无消耗匹配）")
    return "\n".join(parts) + "\n"
