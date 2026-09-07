"""drawio mxfile 生成（akso-cc understand/drawio.ts + spider step4/step5 的 Python 化，纯函数）。

规则（实证口径，勿改）：
- 每条边必带 <mxGeometry relative="1"/>；根节点 id=0/1；属性一律 XML 转义；
- html=1 标签内换行以 &lt;br&gt; 实体嵌入；
- 形状按 stepType：70 判断=黄色菱形、10/20 开始结束=绿色胶囊、30 审批=紫色圆角、
  40 通知=橙色圆角、其余=蓝色圆角；
- Kahn 最长路径分层：understand 层=行自上而下；spider step4 层=列自左向右。
"""

from __future__ import annotations

import hashlib
from typing import Any

from ..auth import STEP_TYPE_NAMES

_STYLE = {
    10: "ellipse;fillColor=#d5e8d4;strokeColor=#82b366;",
    20: "ellipse;fillColor=#f8cecc;strokeColor=#b85450;",
    30: "rounded=1;fillColor=#e1d5e7;strokeColor=#9673a6;",
    40: "rounded=1;fillColor=#ffe6cc;strokeColor=#d79b00;",
    70: "rhombus;fillColor=#fff2cc;strokeColor=#d6b656;",
}
_DEFAULT_STYLE = "rounded=1;fillColor=#dae8fc;strokeColor=#6c8ebf;"


def esc(text: Any) -> str:
    return (
        str(text if text is not None else "")
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _label(step_name: Any, meta: dict[str, Any]) -> str:
    lines = [str(step_name or "")]
    for behavior in meta.get("behaviors", []):
        if behavior.get("summary"):
            lines.append(str(behavior["summary"]))
    if meta.get("dispatchMode") or meta.get("approver"):
        lines.append(f"{meta.get('dispatchMode') or ''} {meta.get('approver') or ''}".strip())
    return "&lt;br&gt;".join(esc(line) for line in lines if line)


def _node_xml(node_id: str, label: str, style: str, x: float, y: float, w: float, h: float) -> str:
    value = esc(label).replace("&#10;", "&lt;br&gt;")
    return (
        f'<mxCell id="{esc(node_id)}" value="{value}" style="{style}html=1;" vertex="1" parent="1">'
        f'<mxGeometry x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" as="geometry"/></mxCell>'
    )


def _edge_xml(source: str, target: str, label: str = "") -> str:
    value = esc(label) if label else ""
    return (
        f'<mxCell id="e{esc(source)}-{esc(target)}-{esc(label)}" value="{value}" '
        f'style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="1" '
        f'source="{esc(source)}" target="{esc(target)}">'
        f'<mxGeometry relative="1" as="geometry"/></mxCell>'
    )


def kahn_layers(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, int]:
    """Kahn 最长路径分层：返回 node_id → 层号。成环节点归入剩余层。"""
    ids = [str(n["id"]) for n in nodes]
    indegree = {i: 0 for i in ids}
    adjacency: dict[str, list[str]] = {i: [] for i in ids}
    for edge in edges:
        s, t = str(edge["source"]), str(edge["target"])
        if s in indegree and t in indegree:
            adjacency[s].append(t)
            indegree[t] += 1
    layer = {i: 0 for i in ids}
    queue = [i for i in ids if indegree[i] == 0]
    visited = 0
    while queue:
        current = queue.pop(0)
        visited += 1
        for nxt in adjacency[current]:
            layer[nxt] = max(layer[nxt], layer[current] + 1)
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    if visited < len(ids):  # 成环：未定层节点排到最后
        base = max(layer.values(), default=0) + 1
        for i in ids:
            if indegree[i] > 0:
                layer[i] = base
    return layer


def workflow_graph_to_cells(graph: dict[str, Any], *, axis: str = "row",
                            node_w: float = 180, node_h: float = 40,
                            gap: float = 70) -> list[str]:
    """工作流图 → mxCell 片段。axis=row 层=行（自上而下），axis=col 层=列。"""
    layer = kahn_layers(graph["nodes"], graph["edges"])
    by_layer: dict[int, list[dict[str, Any]]] = {}
    for node in graph["nodes"]:
        by_layer.setdefault(layer[str(node["id"])], []).append(node)
    cells: list[str] = []
    for lvl in sorted(by_layer):
        for idx, node in enumerate(by_layer[lvl]):
            step_type = node.get("stepType")
            style = _STYLE.get(step_type, _DEFAULT_STYLE)
            label = _label(node.get("name"), node)
            if axis == "row":
                x, y = 40 + idx * (node_w + gap), 40 + lvl * (node_h + gap)
            else:
                x, y = 40 + lvl * (node_w + gap), 40 + idx * (node_h + gap)
            cells.append(_node_xml(str(node["id"]), label, style, x, y, node_w, node_h))
    edge_keys: set[tuple[str, str, str]] = set()
    for edge in graph["edges"]:
        key = (str(edge["source"]), str(edge["target"]), str(edge.get("label") or ""))
        if key in edge_keys:
            continue
        edge_keys.add(key)
        cells.append(_edge_xml(key[0], key[1], key[2]))
    return cells


def mxfile(diagrams: list[tuple[str, str]], *, host: str = "akso-workbench") -> str:
    """多页 mxfile。diagrams: [(page_name, cells...)]。"""
    pages = []
    for name, body in diagrams:
        digest = hashlib.md5(name.encode("utf-8")).hexdigest()
        pages.append(
            f'<diagram id="d{digest[:12]}" name="{esc(name)}">'
            f'<mxGraphModel dx="800" dy="600" grid="0" gridSize="10" guides="1" tooltips="1" '
            f'connect="1" arrows="1" fit="1" page="1" pageScale="1" math="0" shadow="0">'
            f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>{body}</root>'
            f"</mxGraphModel></diagram>"
        )
    return (
        f'<mxfile host="{esc(host)}" type="device">' + "".join(pages) + "</mxfile>"
    )


def workflow_to_drawio(graph: dict[str, Any]) -> str:
    """单页 mxfile（understand：动作涉及的工作流）。"""
    cells = "".join(workflow_graph_to_cells(graph, axis="row"))
    return mxfile([( "workflow", cells )])


def safe_page_name(base: str, used: set[str]) -> str:
    name = (base or "page")[:40]
    candidate, n = name, 2
    while candidate in used:
        candidate = f"{name}({n})"
        n += 1
    used.add(candidate)
    return candidate


STEP_TYPE_NAME = STEP_TYPE_NAMES  # 复用词典
