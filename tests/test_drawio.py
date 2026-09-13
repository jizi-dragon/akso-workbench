"""drawio 生成的质量门禁（离线、快、对抗性输入）。

存在理由（见 docs/INSIGHT-SPEC.md §6.2）：drawio 产物必须是**任何输入都能被 XML 解析**的良构文件。
历史上有三类真实缺陷，本文件逐条钉死：

1. **双重转义** —— `_label` 已转义，`_node_xml` 又转义一次 → 标签变成字面 `&amp;lt;br&amp;gt;`；
2. **非法 XML 字符未清理** —— C0 控制字符（如 U+000B）在 XML 1.0 中**即使写成字符引用也非法**，
   名里出现一个就让整个 `.drawio` 无法解析；
3. **节点 id 与根单元冲突** —— 平台步骤 id 若为 `0`/`1`，会与 `<mxCell id="0"/>` / `<mxCell id="1"/>` 重复。

断言口径：**先良构，再语义**。良构靠 `xml.etree` 解析；语义靠解析回来的属性值比对。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from workbench.services.egmp.insight import drawio

# XML 1.0 允许的空白：\t \n \r；其余 C0 一律非法（即使写成 &#11; 也是非法的）
NASTY_TEXT = "计划\u000b审批\u0000通过\u001f！"

ADVERSARIAL_NAMES = [
    "A & B",
    'He said "hi"',
    "<script>alert(1)</script>",
    "多行\n标签",
    "CRLF\r\n换行",
    NASTY_TEXT,
    "中文标签（含全角括号）",
    "emoji 🎯 也要能过",
    "",  # 空名
    "   ",  # 纯空白
    "a<br>b",  # 伪装成标签的输入
    "&amp;",  # 已转义的输入（不得二次转义）
]


def _graph(names: list[str], *, ids: list[str] | None = None) -> dict:
    """构造一个线性工作流图：n0 → n1 → ... """
    n = len(names)
    ids = ids or [f"s{i}" for i in range(n)]
    nodes = [
        {"id": ids[i], "name": names[i], "stepType": 30,
         "behaviors": [{"behaviorType": 9, "summary": "修改状态 → 已完成"}],
         "dispatchMode": "或签", "approver": "张三 & 李四"}
        for i in range(n)
    ]
    edges = [
        {"source": ids[i], "target": ids[i + 1], "kind": "flow", "label": ""}
        for i in range(n - 1)
    ]
    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------- 文本安全


def test_esc_is_single_pass() -> None:
    """esc 只转义一次：& 变 &amp;，已转义的 &amp; 变 &amp;amp;。"""
    assert drawio.esc("A & B") == "A &amp; B"
    assert drawio.esc('"q"') == "&quot;q&quot;"
    assert drawio.esc("<t>") == "&lt;t&gt;"
    assert drawio.esc("&amp;") == "&amp;amp;"


def test_esc_strips_illegal_xml_chars() -> None:
    """C0 控制字符必须被清掉——它们在 XML 1.0 里根本无法表示。"""
    out = drawio.esc(NASTY_TEXT)
    assert not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", out)
    # 合法空白必须保留
    assert drawio.esc("a\tb\nc\rd").count("b") == 1


# ---------------------------------------------------------------- 良构性（核心门禁）


@pytest.mark.parametrize("name", ADVERSARIAL_NAMES, ids=lambda s: repr(s)[:28])
def test_single_node_label_is_well_formed(name: str) -> None:
    xml = drawio.workflow_to_drawio(_graph([name]))
    parsed = ET.fromstring(xml)  # 不抛异常即良构
    values = [c.get("value") for c in parsed.iter("mxCell") if c.get("vertex") == "1"]
    assert len(values) == 1


@pytest.mark.parametrize("name", ADVERSARIAL_NAMES, ids=lambda s: repr(s)[:28])
def test_two_node_workflow_is_well_formed(name: str) -> None:
    """带边的图：边与节点都要良构。"""
    xml = drawio.workflow_to_drawio(_graph([name, f"次{name}"]))
    ET.fromstring(xml)


def test_full_adversarial_graph_is_well_formed() -> None:
    xml = drawio.workflow_to_drawio(_graph(ADVERSARIAL_NAMES))
    ET.fromstring(xml)


def test_empty_graph_is_well_formed() -> None:
    xml = drawio.workflow_to_drawio({"nodes": [], "edges": []})
    ET.fromstring(xml)


def test_missing_fields_do_not_crash() -> None:
    """平台行可能缺键：name/id/stepType 全缺也要能产出良构文件。"""
    graph = {"nodes": [{"id": "x"}], "edges": []}
    ET.fromstring(drawio.workflow_to_drawio(graph))


# ---------------------------------------------------------------- 语义正确性


def _vertex_values(xml: str) -> list[str]:
    root = ET.fromstring(xml)
    return [c.get("value") for c in root.iter("mxCell") if c.get("vertex") == "1"]


def test_line_break_uses_character_reference_not_literal_tag() -> None:
    """换行必须用 &#xa; 字符引用——字面 `<` 在 XML 属性值里非法。

    实证：draw.io 自身导出多行标签就是 `&#xa;`（见 SKILL 参考手册：
    `&#xa;` 在 html=0/1 下都能换行，`&lt;br&gt;` 需要 html=1）。
    """
    xml = drawio.workflow_to_drawio(_graph(["第一行\n第二行"]))
    assert "&#xa;" in xml, "文件里应以 &#xa; 表示换行"
    value = _vertex_values(xml)[0]
    assert value.startswith("第一行\n第二行"), f"解析回来应是真实换行，实际：{value!r}"
    assert "&lt;br&gt;" not in value
    assert "&amp;#xa;" not in xml, "出现 &amp;#xa; 即双重转义"


def test_ampersand_roundtrips_to_single_ampersand() -> None:
    """A & B 在文件里是 A &amp; B，解析回来必须正好是 A & B（单层转义）。"""
    value = _vertex_values(drawio.workflow_to_drawio(_graph(["A & B"])))[0]
    assert value == "A & B\n修改状态 → 已完成\n或签 张三 & 李四"
    assert "&amp;" not in value, f"解析结果里不应残留 &amp;：{value!r}"


def test_less_than_roundtrips_to_single_less_than() -> None:
    """< 必须单层转义：解析回来是裸 <，不得是 &lt;。"""
    value = _vertex_values(drawio.workflow_to_drawio(_graph(["<script>"])))[0]
    assert value.startswith("<script>")
    assert "&lt;" not in value


def test_behavior_summary_appears_in_label() -> None:
    xml = drawio.workflow_to_drawio(_graph(["审批节点"]))
    root = ET.fromstring(xml)
    value = next(c.get("value") for c in root.iter("mxCell") if c.get("vertex") == "1")
    assert "审批节点" in value
    assert "修改状态" in value


# ---------------------------------------------------------------- id 安全


@pytest.mark.parametrize("bad_id", ["0", "1", "", "a b", 'x"y', "中文id", "<id>"])
def test_node_id_never_collides_with_root_cells(bad_id: str) -> None:
    """平台 id 可以是 0/1，绝不能与根单元 id="0"/id="1" 重复。"""
    xml = drawio.workflow_to_drawio(_graph(["节点"], ids=[bad_id]))
    root = ET.fromstring(xml)
    ids = [c.get("id") for c in root.iter("mxCell")]
    assert len(ids) == len(set(ids)), f"存在重复 mxCell id：{ids}"
    assert "0" in ids and "1" in ids, "根单元必须保留"
    # 除根单元外，不得再出现裸 0/1
    assert ids.count("0") == 1 and ids.count("1") == 1


def test_all_cell_ids_unique_with_duplicate_step_ids() -> None:
    """平台若给出重复步骤 id，产物仍不得有重复 mxCell id。"""
    graph = {
        "nodes": [{"id": "dup", "name": "A", "stepType": 30},
                  {"id": "dup", "name": "B", "stepType": 30}],
        "edges": [],
    }
    root = ET.fromstring(drawio.workflow_to_drawio(graph))
    ids = [c.get("id") for c in root.iter("mxCell")]
    assert len(ids) == len(set(ids)), f"重复步骤 id 泄漏到产物：{ids}"


def test_edge_ids_unique_for_parallel_edges() -> None:
    """同源同目标但标签不同的两条边，id 必须不同。"""
    graph = {
        "nodes": [{"id": "a", "name": "A", "stepType": 30},
                  {"id": "b", "name": "B", "stepType": 30}],
        "edges": [{"source": "a", "target": "b", "kind": "flow", "label": "是"},
                  {"source": "a", "target": "b", "kind": "decision", "label": "否"}],
    }
    root = ET.fromstring(drawio.workflow_to_drawio(graph))
    ids = [c.get("id") for c in root.iter("mxCell")]
    assert len(ids) == len(set(ids))


def test_edge_references_resolve_to_existing_nodes() -> None:
    """边的 source/target 必须能在同一文件里找到对应节点。"""
    root = ET.fromstring(drawio.workflow_to_drawio(_graph(["A", "B", "C"])))
    vertex_ids = {c.get("id") for c in root.iter("mxCell") if c.get("vertex") == "1"}
    for edge in (c for c in root.iter("mxCell") if c.get("edge") == "1"):
        assert edge.get("source") in vertex_ids, f"悬空 source：{edge.get('source')}"
        assert edge.get("target") in vertex_ids, f"悬空 target：{edge.get('target')}"


# ---------------------------------------------------------------- 结构契约


def test_every_edge_has_geometry_child() -> None:
    """draw.io 要求每条边都带 <mxGeometry relative="1"/> 子元素，否则不渲染。"""
    root = ET.fromstring(drawio.workflow_to_drawio(_graph(["A", "B", "C"])))
    edges = [c for c in root.iter("mxCell") if c.get("edge") == "1"]
    assert edges, "夹具应当产出边"
    for edge in edges:
        geo = edge.find("mxGeometry")
        assert geo is not None, "边缺少 mxGeometry 子元素"
        assert geo.get("relative") == "1"


def test_every_vertex_has_geometry() -> None:
    root = ET.fromstring(drawio.workflow_to_drawio(_graph(["A", "B"])))
    for cell in (c for c in root.iter("mxCell") if c.get("vertex") == "1"):
        assert cell.find("mxGeometry") is not None


def test_no_xml_comments() -> None:
    """drawio 技能明确禁止输出 XML 注释。"""
    xml = drawio.workflow_to_drawio(_graph(ADVERSARIAL_NAMES))
    assert "<!--" not in xml


def test_root_structure_present() -> None:
    root = ET.fromstring(drawio.workflow_to_drawio(_graph(["A"])))
    ids = [c.get("id") for c in root.iter("mxCell")]
    assert ids[:2] == ["0", "1"], "前两个单元必须是根层与默认父层"


def test_html_labels_enabled() -> None:
    """多行标签依赖 html=1 + whiteSpace=wrap 才会可靠折行（draw.io 惯例）。"""
    root = ET.fromstring(drawio.workflow_to_drawio(_graph(["A"])))
    vertex = next(c for c in root.iter("mxCell") if c.get("vertex") == "1")
    style = vertex.get("style") or ""
    assert "html=1" in style
    assert "whiteSpace=wrap" in style


# ---------------------------------------------------------------- 多页 mxfile


def test_multipage_mxfile_is_well_formed() -> None:
    pages = [("总览·对象网络", "".join(drawio.workflow_cells(_graph(["A", "B"])))),
             ("A·状态机", "".join(drawio.workflow_cells(_graph(["A"]), axis="col")))]
    root = ET.fromstring(drawio.mxfile(pages))
    diagrams = list(root.iter("diagram"))
    assert len(diagrams) == 2
    names = [d.get("name") for d in diagrams]
    assert len(names) == len(set(names)), "页名必须唯一"


def test_page_names_deduplicated_and_sanitized() -> None:
    """页名只负责唯一性与非法字符清除；转义由 mxfile 统一做（见下一个用例）。"""
    used: set[str] = set()
    first = drawio.safe_page_name("同名页", used)
    second = drawio.safe_page_name("同名页", used)
    assert first != second
    assert not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", drawio.safe_page_name(NASTY_TEXT, used))
    assert drawio.safe_page_name("", used)  # 空名要有兜底
    assert len(drawio.safe_page_name("长" * 200, used)) <= 48


def test_multipage_with_adversarial_page_name_is_well_formed() -> None:
    pages = [(name, "".join(drawio.workflow_cells(_graph(["A"])))) for name in ADVERSARIAL_NAMES]
    ET.fromstring(drawio.mxfile(pages))


# ---------------------------------------------------------------- 校验器自身


def test_validate_accepts_generated_file() -> None:
    drawio.validate(drawio.workflow_to_drawio(_graph(ADVERSARIAL_NAMES)))


def test_validate_rejects_malformed() -> None:
    with pytest.raises(drawio.DrawioXmlError):
        drawio.validate("<mxfile><root></mxfile>")


def test_is_well_formed_predicate() -> None:
    assert drawio.is_well_formed(drawio.workflow_to_drawio(_graph(["A"])))
    assert not drawio.is_well_formed("not xml at all")


# ---------------------------------------------------------------- 分层


def test_kahn_layers_linear() -> None:
    graph = _graph(["A", "B", "C"])
    layers = drawio.kahn_layers(graph["nodes"], graph["edges"])
    assert layers["s0"] == 0 and layers["s1"] == 1 and layers["s2"] == 2


def test_kahn_layers_cycle_does_not_hang() -> None:
    """成环必须终止，并把未定层节点归入同一剩余层。"""
    graph = {
        "nodes": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}],
        "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}],
    }
    layers = drawio.kahn_layers(graph["nodes"], graph["edges"])
    assert set(layers) == {"a", "b"}
    assert layers["a"] == layers["b"]


def test_no_overlapping_node_positions() -> None:
    """同层多节点不得坐标重叠（否则图上叠在一起）。"""
    graph = _graph(["A", "B", "C"])
    graph["edges"] = []  # 全在同层
    root = ET.fromstring(drawio.workflow_to_drawio(graph))
    boxes = []
    for cell in (c for c in root.iter("mxCell") if c.get("vertex") == "1"):
        geo = cell.find("mxGeometry")
        boxes.append((float(geo.get("x")), float(geo.get("y")),
                      float(geo.get("width")), float(geo.get("height"))))
    for i, (x1, y1, w1, h1) in enumerate(boxes):
        for x2, y2, w2, h2 in boxes[i + 1:]:
            overlap = not (x1 + w1 <= x2 or x2 + w2 <= x1 or y1 + h1 <= y2 or y2 + h2 <= y1)
            assert not overlap, f"节点重叠：{(x1, y1)} 与 {(x2, y2)}"
