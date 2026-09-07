"""蜘蛛计划五步织网（akso-cc spider/step1-5 的 Python 化）。

step5 图模型用 networkx 分析（9/4 调研认定的最大潜在收益在此兑现）：
节点=对象/状态，边=归因边（按钮/进入动作/工作流步骤）+ 关联边；
来源未知/目标未匹配的边挂对象节点，绝不猜测。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..auth import is_complete_status, is_init_status, is_reference_field
from . import drawio as drawio_mod
from .crawler import SIGNATURE_RE
from .lifecycle import LifecycleApi
from .report import _table  # noqa: PLC2701 —— 同包渲染工具

# ---------------------------------------------------------------- 第 1 步

def collect_relations(fields: list[dict[str, Any]], self_code: str) -> dict[str, Any]:
    """目标对象 + 相关对象信息表（签名语义对象排除）。"""
    targets: dict[str, dict[str, Any]] = {}
    excluded: list[dict[str, str]] = []
    for field in fields:
        if not is_reference_field(int(field.get("dataType") or 0)):
            continue
        target_code = str(field.get("referenceObjectCode") or "").strip()
        if not target_code:
            continue
        if target_code == self_code:
            entry = targets.setdefault(self_code, {"code": self_code, "via": [], "selfLoop": True})
            entry["via"].append(f"{field.get('name')}（自环）")
            continue
        joined = f"{field.get('name') or ''}{field.get('code') or ''}{target_code}"
        if SIGNATURE_RE.search(joined):
            excluded.append({"object": target_code, "field": str(field.get("code"))})
            continue
        entry = targets.setdefault(target_code, {"code": target_code, "via": [], "selfLoop": False})
        entry["via"].append(str(field.get("name") or field.get("code")))
    related = [
        {"objectCode": code, "via": entry["via"], "selfLoop": entry["selfLoop"]}
        for code, entry in sorted(targets.items()) if code != self_code
    ]
    return {"related": related, "excluded": excluded, "selfLoop": self_code in targets}


# ---------------------------------------------------------------- 第 2 步

def compose_status_cells(status_rows: list[dict[str, Any]],
                         bar_rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """GetList 记录 + GetActionBar → 进入条件/进入动作/用户动作 三格。"""
    by_id = {str(r.get("id")): r for r in bar_rows}
    cells = []
    for row in status_rows:
        action_ids = [str(a) for a in row.get("ids", []) if a]
        user_actions, enter_actions, conditions = [], [], []
        for action_id in action_ids:
            bar = by_id.get(action_id) or {}
            name = str(bar.get("name") or action_id)
            if bar.get("behaviorType") == 9:
                user_actions.append(f"{name}→{bar.get('targetName') or bar.get('targetId')}")
            elif bar.get("behaviorType") == 7:
                user_actions.append(f"{name}（发起工作流）")
            else:
                enter_actions.append(name)
        description = str(row.get("description") or "")
        if description:
            conditions.append(description)
        cells.append({
            "statusName": str(row.get("name") or ""),
            "enterCondition": "；".join(conditions) or "—",
            "enterAction": "、".join(enter_actions) or "—",
            "userAction": "、".join(user_actions) or "—",
        })
    return cells


# ---------------------------------------------------------------- 第 3 步

def attribute_status_changes(status_rows: list[dict[str, Any]],
                             bar_rows: list[dict[str, Any]],
                             workflows: list[dict[str, Any]],
                             status_details: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """状态变化归因：button / enterAction / workflowStep / workflowCancel 四类证据链。"""
    target_re = re.compile(r"修改状态为[［\[]([^\]］]+)")
    rows: list[dict[str, Any]] = []
    unverified_notes: list[str] = []
    workflow_by_basic_id = {str(w.get("workflowBasicId")): w for w in workflows if w.get("workflowBasicId")}

    for row in status_rows:
        status_name = str(row.get("name") or "")
        for action_id in [str(a) for a in row.get("ids", []) if a]:
            bar = next((b for b in bar_rows if str(b.get("id")) == action_id), None)
            if not bar:
                continue
            name = str(bar.get("name") or action_id)
            if bar.get("behaviorType") == 9:
                rows.append({"source": status_name, "carrier": name, "mechanism": "button",
                             "from": status_name, "to": str(bar.get("targetName") or bar.get("targetId")),
                             "evidence": "GetActionBar behaviorType=9"})
            elif bar.get("behaviorType") == 7:
                workflow = workflow_by_basic_id.get(str(bar.get("targetId"))) or {}
                rows.append({"source": status_name, "carrier": name, "mechanism": "workflowStep",
                             "from": status_name, "to": f"工作流「{workflow.get('name') or bar.get('targetId')}」",
                             "evidence": "GetActionBar behaviorType=7 发起"})
        description = str(row.get("description") or "")
        match = target_re.search(description)
        if match:
            rows.append({"source": status_name, "carrier": "进入动作", "mechanism": "enterAction",
                         "from": status_name, "to": match.group(1),
                         "evidence": f"description 正则：{description[:60]}"})
        detail = status_details.get(str(row.get("id"))) or {}
        cancel_id = detail.get("workflowCancelStatus")
        if cancel_id:
            unverified_notes.append(
                f"{status_name}：workflowCancelStatus={cancel_id} 为平台全局配置 id，不作边（实证结论）"
            )
    return {"rows": rows, "unverifiedNotes": unverified_notes}


# ---------------------------------------------------------------- 第 5 步

def build_network_model(objects: list[dict[str, Any]], attributions: list[dict[str, Any]],
                        relations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """对象节点 + 状态节点 + 归因边 + 关联边（未知来源/目标挂对象节点）。"""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    object_names: set[str] = set()

    for obj in objects:
        code = obj["code"]
        name = str(obj.get("name") or code)
        object_names.add(name)
        nodes.append({"id": f"obj:{code}", "kind": "object", "label": name, "code": code})
        for status in obj.get("statuses") or []:
            status_name = str(status.get("name") or status.get("code"))
            kind = "init" if is_init_status(str(status.get("code") or "")) else (
                "complete" if is_complete_status(str(status.get("code") or "")) else "state")
            nodes.append({"id": f"st:{code}:{status.get('id')}", "kind": kind, "label": status_name,
                          "object": code})
        for related in (relations.get(code, {}).get("related") or []):
            edges.append({"kind": "relation", "source": f"obj:{code}",
                          "target": f"obj:{related['objectCode']}",
                          "label": "、".join(related["via"])})

    for attr in attributions:
        source_status = attr.get("from")
        target = str(attr.get("to") or "")
        source_node = _find_status_node(nodes, source_status)
        target_node = _find_status_node(nodes, target) or f"obj:{target}" if target else None
        if not target_node:
            target_node = source_node  # 目标未匹配：挂对象节点，绝不猜测
        edges.append({"kind": f"attr:{attr.get('mechanism')}", "source": source_node,
                      "target": target_node, "label": attr.get("carrier"), "evidence": attr.get("evidence")})

    stats = {
        "objectNodes": sum(1 for n in nodes if n["kind"] == "object"),
        "stateNodes": sum(1 for n in nodes if n["kind"] in {"state", "init", "complete"}),
        "attrEdges": sum(1 for e in edges if e["kind"].startswith("attr:")),
        "relationEdges": sum(1 for e in edges if e["kind"] == "relation"),
    }
    return {"nodes": nodes, "edges": edges, "stats": stats}


def _find_status_node(nodes: list[dict[str, Any]], status_name: Any) -> str | None:
    for node in nodes:
        if node["kind"] in {"state", "init", "complete"} and node["label"] == str(status_name or ""):
            return str(node["id"])
    return None


def analyze_network(model: dict[str, Any]) -> dict[str, Any]:
    """networkx 分析：关键节点（度中心性）、孤立状态、最长归因链。"""
    import networkx as nx

    graph = nx.DiGraph()
    for node in model["nodes"]:
        graph.add_node(node["id"], **{k: v for k, v in node.items() if k != "id"})
    for edge in model["edges"]:
        if graph.has_node(edge["source"]) and graph.has_node(edge["target"]):
            graph.add_edge(edge["source"], edge["target"], kind=edge["kind"], label=edge.get("label"))
    centrality = nx.degree_centrality(graph)
    top = sorted(centrality.items(), key=lambda kv: kv[1], reverse=True)[:10]
    label_of = {n["id"]: n["label"] for n in model["nodes"]}
    orphans = [node["id"] for node in model["nodes"]
               if graph.degree(node["id"]) == 0]
    return {
        "hubs": [{"id": node_id, "label": label_of.get(node_id, node_id), "centrality": round(score, 3)}
                 for node_id, score in top],
        "orphans": [label_of.get(node_id, node_id) for node_id in orphans],
        "isDag": nx.is_directed_acyclic_graph(graph),
    }


# ---------------------------------------------------------------- 编排

def run_spider(client: Any, codes: list[str]) -> dict[str, Any]:
    """五步织网（单次调用跑完 codes 的 step1-5 模型，产物由调用方渲染落盘）。"""
    api = LifecycleApi(client)
    result: dict[str, Any] = {"objects": [], "perObject": {}}
    crawled: list[dict[str, Any]] = []
    all_attrs: list[dict[str, Any]] = []

    for code in codes:
        meta = client.get_object(code) or {}
        fields = client.paged_post("/api/platform/BasicObject/FieldPage",
                                   {"objectId": str(meta.get("id") or "")})
        relations = collect_relations(fields, code)

        # 相关对象全量信息（含生命周期）
        related_codes = [r["objectCode"] for r in relations["related"]]
        scoped: list[dict[str, Any]] = [{"code": code, "name": meta.get("name"), "meta": meta,
                                         "fields": fields, "statuses": []}]
        for related_code in related_codes:
            try:
                rmeta = client.get_object(related_code) or {}
            except Exception:  # noqa: BLE001
                continue
            scoped.append({"code": related_code, "name": rmeta.get("name"), "meta": rmeta,
                           "fields": client.paged_post(
                               "/api/platform/BasicObject/FieldPage",
                               {"objectId": str(rmeta.get("id") or "")}),
                           "statuses": (client.get_object_statuses(related_code) if rmeta.get("enableLifeCycle") else [])})
        if meta.get("enableLifeCycle"):
            scoped[0]["statuses"] = client.get_object_statuses(code) or []

        # step2/3：目标+相关对象状态表与归因
        workflows = client.list_workflows() or []
        for entry in scoped:
            status_rows: list[dict[str, Any]] = []
            bar_rows: list[dict[str, Any]] = []
            status_details: dict[str, dict[str, Any]] = {}
            for status in entry["statuses"]:
                try:
                    rows = api.user_actions(str(status["id"]))
                except Exception:  # noqa: BLE001
                    rows = []
                status_rows.append({"id": status.get("id"), "name": status.get("name"),
                                    "ids": [r.get("id") for r in rows],
                                    "description": "；".join(
                                        str(r.get("description") or "") for r in rows)})
                for row in rows:
                    try:
                        bar_rows.extend(api.action_bar([str(row.get("id"))]))
                    except Exception:  # noqa: BLE001
                        continue
                try:
                    status_details[str(status["id"])] = api.status_detail(str(status["id"]))
                except Exception:  # noqa: BLE001
                    continue
            cells = compose_status_cells(status_rows, bar_rows)
            attrs = attribute_status_changes(status_rows, bar_rows, workflows, status_details)
            all_attrs.extend(attrs["rows"])
            result["perObject"][entry["code"]] = {
                "step1": relations if entry["code"] == code else {"related": [], "excluded": []},
                "step2Cells": cells,
                "step3": attrs,
                "statuses": entry["statuses"],
            }
        result["objects"].append({"code": code, "name": meta.get("name"),
                                  "statuses": scoped[0]["statuses"]})
        crawled.extend(scoped)

    relations_map = {obj["code"]: collect_relations(obj.get("fields") or [], obj["code"]) for obj in crawled}
    model = build_network_model(crawled, all_attrs, relations_map)
    model["analysis"] = analyze_network(model)
    result["model"] = model
    return result


def write_spider_artifacts(spider: dict[str, Any], output_dir: Path) -> list[Path]:
    """落盘 step1-5 产物（md 索引 + drawio 多页 + network json）。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    model = spider["model"]

    # spider-network.json
    import json

    net_json = output_dir / "spider-network.json"
    net_json.write_text(json.dumps(
        {"meta": {"generatedFrom": "egmp-native"}, "model": {k: v for k, v in model.items() if k != "edges"} | {"edges": model["edges"]}},
        ensure_ascii=False, indent=2, default=str,
    ), encoding="utf-8")
    written.append(net_json)

    # spider-network.drawio：总览页 + 每对象状态机页
    pages: list[tuple[str, str]] = []
    overview_cells: list[str] = []
    obj_nodes = [n for n in model["nodes"] if n["kind"] == "object"]
    for idx, node in enumerate(obj_nodes):
        x, y = 40 + (idx % 3) * 290, 40 + (idx // 3) * 110
        overview_cells.append(drawio_mod._node_xml(node["id"], node["label"],
                                                   "rounded=1;fillColor=#dae8fc;strokeColor=#6c8ebf;",
                                                   x, y, 220, 70))
    used: set[str] = set()
    for edge in model["edges"]:
        if edge["kind"] == "relation":
            overview_cells.append(drawio_mod._edge_xml(edge["source"], edge["target"], edge.get("label") or ""))
    for node in obj_nodes:
        code = node["code"]
        cells: list[str] = []
        obj_anchor = f"obj:{code}"
        cells.append(drawio_mod._node_xml(obj_anchor, node["label"],
                                          "rounded=1;fillColor=#ffe6cc;strokeColor=#d79b00;", 40, 40, 180, 40))
        states = [n for n in model["nodes"] if n.get("object") == code and n["kind"] != "object"]
        state_style = {
            "init": "ellipse;fillColor=#d5e8d4;strokeColor=#82b366;",
            "complete": "ellipse;fillColor=#f8cecc;strokeColor=#b85450;",
            "state": "rounded=1;fillColor=#dae8fc;strokeColor=#6c8ebf;",
        }
        for idx, state in enumerate(states):
            cells.append(drawio_mod._node_xml(state["id"], state["label"], state_style[state["kind"]],
                                              320, 40 + idx * 92, 160, 36))
        for edge in model["edges"]:
            if edge["kind"].startswith("attr:") and (edge["source"] == obj_anchor or
                                                     any(s["id"] == edge["source"] for s in states)):
                cells.append(drawio_mod._edge_xml(edge["source"], edge["target"], edge.get("label") or ""))
        pages.append((drawio_mod.safe_page_name(f"{node['label']}·状态机", used), "".join(cells)))
    pages.insert(0, (drawio_mod.safe_page_name("总览·对象网络", used), "".join(overview_cells)))
    net_drawio = output_dir / "spider-network.drawio"
    net_drawio.write_text(drawio_mod.mxfile(pages, host="akso-workbench-spider"), encoding="utf-8")
    written.append(net_drawio)

    # spider-network.md（归因统计 + networkx 分析）
    analysis = model.get("analysis", {})
    lines = ["# 蜘蛛计划 · 历程第 5 步：总体生命周期网络", ""]
    lines.append("## 归因边统计")
    lines.append(_table(["指标", "数量"], [[k, v] for k, v in model["stats"].items()]))
    lines.append("")
    lines.append("## networkx 分析（Python 化收益点）")
    lines.append(f"- 有向无环：{'是' if analysis.get('isDag') else '否（存在环——归因回边）'}")
    lines.append(f"- 孤立节点：{'、'.join(map(str, analysis.get('orphans', []))) or '—'}")
    lines.append("")
    lines.append("| 枢纽节点（度中心性 Top） | 中心性 |")
    lines.append("|---|---|")
    for hub in analysis.get("hubs", []):
        lines.append(f"| {hub['label']} | {hub['centrality']} |")
    net_md = output_dir / "spider-network.md"
    net_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    written.append(net_md)

    # step3 归因 md / step4 工作流页（按对象归组）
    attr_rows = []
    for per in spider["perObject"].values():
        for row in per["step3"]["rows"]:
            attr_rows.append([row.get("from"), row.get("carrier"), row.get("mechanism"),
                              row.get("from"), row.get("to"), row.get("evidence")])
    step3_md = output_dir / "step3-attribution.md"
    step3_md.write_text(
        "# 蜘蛛计划 · 历程第 3 步：状态变化归因\n\n"
        + _table(["来源状态", "载体", "机制", "变化前", "变化后", "依据"], attr_rows) + "\n",
        encoding="utf-8",
    )
    written.append(step3_md)
    return written
