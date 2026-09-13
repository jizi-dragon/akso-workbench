"""drawio mxfile 生成（akso-cc understand/drawio.ts + spider step4/step5 的 Python 化，纯函数）。

规则（实证口径，勿改）：
- 每条边必带 <mxGeometry relative="1"/>；根节点 id=0/1；属性一律 XML 转义（**只转义一次**）；
- html=1 标签内换行以**真实 <br> 标签**嵌入（不是转义后的实体，否则页面上显示字面 `&lt;br&gt;`）；
- 形状按 stepType：70 判断=黄色菱形、10/20 开始结束=绿色胶囊、30 审批=紫色圆角、
  40 通知=橙色圆角、其余=蓝色圆角；
- Kahn 最长路径分层：understand 层=行自上而下；spider step4 层=列自左向右。

安全约束（见 docs/INSIGHT-SPEC.md §6.2；tests/test_drawio.py 逐条钉死）：

1. **转义只在 ``esc()`` 一处发生**。任何已转义的字符串不得再次经过 ``esc()``——
   历史缺陷：``_label`` 先转义、``_node_xml`` 又转义一次，标签变成字面 ``&amp;lt;br&amp;gt;``。
   现在标签一律以**原始文本行列表**传入，由 ``label_value()`` 单点转义。
2. **非法 XML 字符必须清除**。C0 控制字符在 XML 1.0 中即使写成字符引用（``&#11;``）也非法，
   名字里出现一个就让整个 ``.drawio`` 无法解析（实测 expat 报 not well-formed）。
3. **cell id 不得与根单元冲突**。平台步骤 id 可能是 ``"0"``/``"1"``——``cell_id()`` 是纯函数，
   统一加 ``n_`` 前缀 + 短哈希，保证同输入恒同输出（跨模块可安全互相引用）且不与 ``0``/``1`` 撞。
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
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

# XML 1.0 非法字符：C0 中除 \t(09) \n(0A) \r(0D) 之外的全部、孤立代理对、FFFE/FFFF。
_ILLEGAL_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


class DrawioXmlError(ValueError):
    """产出的 drawio 文本不是良构 XML。"""


# ---------------------------------------------------------------- 文本安全


def sanitize(text: Any) -> str:
    """清除 XML 1.0 非法字符（不转义）。合法空白 \\t \\n \\r 保留。"""
    return _ILLEGAL_XML.sub("", str(text if text is not None else ""))


def esc(text: Any) -> str:
    """把任意文本转成可安全放入 XML 属性值的字符串——**单层转义**。

    实证口径：把 ``A & B`` 写进标签后 draw.io 自身产出 ``value="A &amp; B"``（单层，
    解析回来是裸 ``&``），因此这里**只做一层**。历史缺陷是做了两层，
    导致 ``&`` 显示成 ``&amp;``。即便标签带 ``html=1``，也**不要**额外做 HTML 层转义。
    """
    return (
        sanitize(text)
        .replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;").replace('"', "&quot;")
    )


def label_value(lines: Iterable[Any]) -> str:
    """标签行 → XML 属性内容：逐行单层转义，用 ``&#xa;`` 连接。

    换行用 ``&#xa;`` 而非 ``<br>``：**字面 ``<`` 在 XML 属性值里非法**（必须转义），
    而 ``&#xa;`` 是字符引用，XML 1.0 与 HTML 都认；这也是 draw.io 自身导出的写法，
    且在 ``html=0`` 下同样能换行。行内换行也拆成多行（平台名称可能自带 \\n）。
    """
    parts: list[str] = []
    for line in lines:
        text = sanitize(line).replace("\r\n", "\n").replace("\r", "\n")
        for piece in text.split("\n"):
            if piece.strip():
                parts.append(esc(piece))
    return "&#xa;".join(parts)


# ---------------------------------------------------------------- id 安全


def cell_id(raw: Any) -> str:
    """平台 id → 安全且唯一的 mxCell id（**纯函数**：同输入恒同输出）。

    前缀 ``n_`` 保证永不等于根单元的 ``0``/``1``；短哈希保证不同原始 id 不会撞车
    （``"a b"`` 与 ``"a_b"`` 清洗后同为 ``a_b``，但哈希不同）。
    """
    text = str(raw if raw is not None else "")
    safe = re.sub(r"[^0-9A-Za-z_.-]", "_", text)[:32] or "x"
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return f"n_{safe}_{digest}"


def _unique_ids(nodes: list[dict[str, Any]]) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """为节点分配互不重复的 cell id。

    返回 ``(ordered, by_raw)``：``ordered`` 是 ``[(raw_id, cell_id)]`` 保序列表；
    ``by_raw`` 供边解析引用（平台若给出重复 id，边指向**首个**同名节点）。
    """
    used = {"0", "1"}
    ordered: list[tuple[str, str]] = []
    by_raw: dict[str, str] = {}
    for node in nodes:
        raw = str(node.get("id"))
        base = cell_id(raw)
        candidate, n = base, 2
        while candidate in used:
            candidate = f"{base}_{n}"
            n += 1
        used.add(candidate)
        ordered.append((raw, candidate))
        by_raw.setdefault(raw, candidate)
    return ordered, by_raw


def edge_id(source: str, target: str, label: str) -> str:
    """边 id：内容哈希 + 短摘要，同源同目标不同标签也能区分。"""
    digest = hashlib.sha1(f"{source}\x1f{target}\x1f{label}".encode("utf-8")).hexdigest()[:12]
    return f"e_{digest}"


# ---------------------------------------------------------------- 单元构造


def node_cell(cell: str, lines: Iterable[Any], style: str,
              x: float, y: float, w: float, h: float) -> str:
    """顶点单元。``lines`` 是**原始文本行**（不得预先转义）。

    ``whiteSpace=wrap`` 是 draw.io 多行标签的惯例（换行在包裹模式下才可靠折行）。
    """
    value = label_value(lines)
    return (
        f'<mxCell id="{esc(cell)}" value="{value}" '
        f'style="{style}whiteSpace=wrap;html=1;" vertex="1" parent="1">'
        f'<mxGeometry x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" as="geometry"/></mxCell>'
    )


def edge_cell(source: str, target: str, label: Any = "") -> str:
    """边单元。``source``/``target`` 必须是已分配的 cell id；``label`` 是**原始文本**。"""
    return (
        f'<mxCell id="{edge_id(source, target, str(label or ""))}" value="{esc(label)}" '
        f'style="edgeStyle=orthogonalEdgeStyle;html=1;" edge="1" parent="1" '
        f'source="{esc(source)}" target="{esc(target)}">'
        f'<mxGeometry relative="1" as="geometry"/></mxCell>'
    )


def _style_for(step_type: Any) -> str:
    """stepType → 样式。平台可能把数字给成字符串，这里统一强制转型。"""
    try:
        return _STYLE.get(int(step_type), _DEFAULT_STYLE)
    except (TypeError, ValueError):
        return _DEFAULT_STYLE


def label_lines(step_name: Any, meta: dict[str, Any]) -> list[str]:
    """节点标签行：步骤名 + 行为摘要 + 分发/审批人。"""
    lines = [str(step_name or "")]
    for behavior in meta.get("behaviors", []) or []:
        if isinstance(behavior, dict) and behavior.get("summary"):
            lines.append(str(behavior["summary"]))
    dispatch = meta.get("dispatchMode")
    approver = meta.get("approver")
    if dispatch or approver:
        lines.append(f"{dispatch or ''} {approver or ''}".strip())
    return [line for line in lines if line.strip()]


# ---------------------------------------------------------------- 分层


def kahn_layers(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, int]:
    """Kahn 最长路径分层：返回 node_id → 层号。成环节点归入同一剩余层。"""
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


def workflow_cells(graph: dict[str, Any], *, axis: str = "row",
                   node_w: float = 180, node_h: float = 40,
                   gap: float = 70) -> list[str]:
    """工作流图 → mxCell 片段。``axis="row"`` 层=行（自上而下），``"col"`` 层=列（自左向右）。"""
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    layer = kahn_layers(nodes, edges)
    ordered, by_raw = _unique_ids(nodes)
    node_by_raw: dict[str, dict[str, Any]] = {}
    for node in nodes:
        node_by_raw.setdefault(str(node.get("id")), node)

    by_layer: dict[int, list[tuple[str, str, dict[str, Any]]]] = {}
    for raw, cell in ordered:
        by_layer.setdefault(layer.get(raw, 0), []).append((raw, cell, node_by_raw.get(raw, {})))

    cells: list[str] = []
    for lvl in sorted(by_layer):
        for idx, (_raw, cell, node) in enumerate(by_layer[lvl]):
            if axis == "row":
                x, y = 40 + idx * (node_w + gap), 40 + lvl * (node_h + gap)
            else:
                x, y = 40 + lvl * (node_w + gap), 40 + idx * (node_h + gap)
            cells.append(node_cell(cell, label_lines(node.get("name"), node),
                                   _style_for(node.get("stepType")), x, y, node_w, node_h))

    seen_edges: set[tuple[str, str, str]] = set()
    for edge in edges:
        src_raw, tgt_raw = str(edge.get("source")), str(edge.get("target"))
        src, tgt = by_raw.get(src_raw), by_raw.get(tgt_raw)
        if src is None or tgt is None:  # 悬空边：端点不在本图内，丢弃（不得产出悬空引用）
            continue
        label = str(edge.get("label") or "")
        key = (src, tgt, label)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        cells.append(edge_cell(src, tgt, label))
    return cells


# ---------------------------------------------------------------- 组装


def safe_page_name(base: str, used: set[str]) -> str:
    """页名去重 + 截断。转义由 ``mxfile`` 统一做，这里只保证唯一性。"""
    name = sanitize(base or "page").strip()[:40] or "page"
    candidate, n = name, 2
    while candidate in used:
        candidate = f"{name}({n})"
        n += 1
    used.add(candidate)
    return candidate


def mxfile(diagrams: list[tuple[str, str]], *, host: str = "akso-workbench") -> str:
    """多页 mxfile。``diagrams``: ``[(page_name, cells...)]``。"""
    pages: list[str] = []
    used_ids: set[str] = set()
    for name, body in diagrams:
        page_id, n = f"d{hashlib.md5(name.encode('utf-8')).hexdigest()[:12]}", 2
        while page_id in used_ids:  # 同名页的 md5 相同 → 必须再去重，否则 diagram id 重复
            page_id = f"d{hashlib.md5(name.encode('utf-8')).hexdigest()[:12]}_{n}"
            n += 1
        used_ids.add(page_id)
        pages.append(
            f'<diagram id="{page_id}" name="{esc(name)}">'
            f'<mxGraphModel dx="800" dy="600" grid="0" gridSize="10" guides="1" tooltips="1" '
            f'connect="1" arrows="1" fit="1" page="1" pageScale="1" math="0" shadow="0">'
            f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>{body}</root>'
            f"</mxGraphModel></diagram>"
        )
    return f'<mxfile host="{esc(host)}" type="device">' + "".join(pages) + "</mxfile>"


def workflow_to_drawio(graph: dict[str, Any], *, page: str = "workflow") -> str:
    """单页 mxfile（understand：动作涉及的工作流）。"""
    return mxfile([(page, "".join(workflow_cells(graph, axis="row")))])


# ---------------------------------------------------------------- 良构校验


def is_well_formed(text: str) -> bool:
    """产出的 drawio 文本是否为良构 XML。"""
    try:
        ET.fromstring(text)
    except ET.ParseError:
        return False
    return True


def validate(text: str) -> None:
    """良构校验；失败抛 :class:`DrawioXmlError`（带解析器原始信息，便于定位）。"""
    try:
        ET.fromstring(text)
    except ET.ParseError as exc:
        raise DrawioXmlError(f"drawio 产物不是良构 XML：{exc}") from exc


STEP_TYPE_NAME = STEP_TYPE_NAMES  # 复用词典
