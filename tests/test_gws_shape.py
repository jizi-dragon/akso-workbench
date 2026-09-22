"""形状对照引擎的测试 —— 含**用真机录制数据钉住的基线**。

## 为什么这个模块值得有测试

`shape.compare_shapes` 的产出是 A/B/C 三档判定，而那个判定会**决定整个 CLI 改造路线的形状**
（可迁移 ⇒ 换传输层；不可迁移 ⇒ 重新取证）。一个会撒谎的判定比没有判定更糟。

所以这里钉三件事：

1. **纯逻辑**（集合运算 / 分诊边界 / 空侧不能读成 C）；
2. **真机基线**（`factory-tuning` 里 25 条 `SaveLayoutDetail` 的实际形状，逐字钉住）；
3. **"逐元素"这条口径本身**——下面 `test_real_data_has_mixed_shapes_inside_one_array`
   是它的反证：谁要是把比较简化成"整份请求一个形状"，那条会红。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workbench.services.gws.shape import (
    OVERLAP_THRESHOLD,
    VERDICT_ACTIONS,
    compare_shapes,
    diff_key_sets,
    explain_gap,
    key_profile,
    normalize_key,
)

ROOT = Path(__file__).resolve().parents[1]
SESSIONS = ROOT / "factory-tuning" / "data" / "sessions"

# ---------------------------------------------------------------- 真机基线（钉死的常量）
#
# 来源：factory-tuning/data/sessions/*/traffic.jsonl，2026-09-22 实测 25 条
# `POST /api/platform/Layout/SaveLayoutDetail`（23 条 17 键 + 2 条 11 键建壳）。
# 与 docs/PLATFORM-OPERATIONS-LEARNED.md §1/§2 的口径逐字一致。

LAYOUT_SAVE_17_KEYS = (
    "activePageCode", "activeSectionCode", "code", "controls", "formEditStatus", "id",
    "isMain", "labelCol", "name", "objectId", "objectTypeId", "pages", "rules",
    "sections", "skipRequiredValidation", "source", "status",
)
#: 建壳 = **不带 `id`** 的 11 键（响应 `data` 即新布局 id）。
LAYOUT_SHELL_11_KEYS = (
    "code", "controls", "labelCol", "name", "objectId", "objectTypeId", "pages",
    "rules", "sections", "source", "status",
)
#: 控件**新建**时的精简形状。
CONTROL_NEW_8_KEYS = ("attrs", "code", "fieldId", "gridSpan", "id", "name", "sectionId", "type")
#: 部分**新建**时的精简形状。
SECTION_NEW_5_KEYS = ("code", "columnsNum", "id", "name", "type")

CONTROLS_UNION_SIZE = 23
SECTIONS_UNION_SIZE = 62
#: 实测见过几种控件元素形状（段 3 的"14/15/16/23 键"在这里复现）。
CONTROL_SHAPE_SIZES_SEEN = {8, 14, 15, 16, 23}


def load_layout_saves() -> list[dict]:
    if not SESSIONS.is_dir():
        return []
    out: list[dict] = []
    for session in sorted(SESSIONS.iterdir()):
        traffic = session / "traffic.jsonl"
        if not traffic.is_file():
            continue
        for line in traffic.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if "SaveLayoutDetail" in str(rec.get("path")) and isinstance(rec.get("postData"), dict):
                out.append(rec)
    return out


# ---------------------------------------------------------------- 纯逻辑


def test_diff_key_sets_splits_three_ways():
    d = diff_key_sets(["a", "b", "c"], ["b", "c", "d"], scope="顶层")
    assert d.common == ("b", "c")
    assert d.only_reference == ("a",)
    assert d.only_candidate == ("d",)


def test_jaccard_of_two_empty_sets_is_zero_not_one():
    """**两边都空 = 0，不是 1。**

    定义成 1 的话，"两份都没取到"会被算成"完全吻合"——这正是本仓
    「判据自己会撒谎」那一族（PITFALLS #78 同族）。
    """
    d = diff_key_sets([], [], scope="顶层")
    assert d.jaccard == 0.0
    assert d.verdict == "C"


def test_verdict_boundaries():
    """A/B/C 的分档边界要**显式可复算**，不能靠"看着差不多"。"""
    # 10 个键里 6 个相同（并集 14）⇒ Jaccard = 6/14 ≈ 0.43 ⇒ B
    ref = [f"k{i}" for i in range(10)]
    cand = [f"k{i}" for i in range(6)] + [f"x{i}" for i in range(4)]
    d = diff_key_sets(ref, cand, scope="顶层")
    assert d.jaccard < OVERLAP_THRESHOLD
    assert d.verdict == "B"

    # 10 个键里 9 个相同（并集 11）⇒ Jaccard = 9/11 ≈ 0.82 ⇒ A
    d2 = diff_key_sets(ref, [*ref[:9], "extra"], scope="顶层")
    assert d2.jaccard >= OVERLAP_THRESHOLD
    assert d2.verdict == "A"

    # 一个都不交 ⇒ C
    assert diff_key_sets(["a"], ["b"], scope="顶层").verdict == "C"


def test_every_verdict_has_a_declared_next_action():
    """判定出来之后"下一步做什么"不能靠记忆——每个字母都必须在表里有处置。"""
    for letter in ("A", "B", "C", "?"):
        assert VERDICT_ACTIONS[letter].strip()


def test_compare_shapes_never_reports_c_when_one_side_is_empty():
    """**空侧不是"不吻合"，是"没得比"。**

    这是本模块最容易骗人的地方：候选侧没取到实例时，如果把空的键集丢进集合运算，
    会得到一个漂亮的 `verdict == "C"`（0 交集 ⇒ C），于是"这次没取到样本"
    被读成"两套模型完全对不上"——一个会直接改变改造方向的假结论。
    """
    report = compare_shapes({"a": 1, "b": 2}, {}, scopes=("顶层",))
    assert report.verdict == "?", "空侧竟然给出了 A/B/C 判定"
    assert report.diffs == []
    assert any("候选侧为空" in n for n in report.notes)

    report2 = compare_shapes({}, {"a": 1}, scopes=("顶层",))
    assert report2.verdict == "?"
    assert any("参考侧为空" in n for n in report2.notes)


def test_compare_shapes_takes_the_weakest_scope():
    """顶层再像，只要元素层对不上，整体就不是 A —— **最弱环节决定可行性**。"""
    ref = {"name": "x", "controls": [{"code": "a", "type": "AKInput"}]}
    cand = {"name": "x", "controls": [{"fieldCode": "a", "ctrlType": "input"}]}
    report = compare_shapes(ref, cand, scopes=("顶层", "controls[]"))
    scored = {d.scope: d for d in report.scored}
    assert scored["顶层"].verdict == "A"
    assert scored["controls[]"].verdict == "C"
    assert report.verdict == "C"


def test_normalize_key_erases_convention_but_keeps_meaning_apart():
    """归一化只抹**拼写**，不抹**语义**。

    `rules` 与 `rules_json` 归一后是 `rules` / `rulesjson`，**仍然不相等** ——
    这是对的：后者是"被序列化成字符串的一列"，与"一个数组"不是同一个东西。
    归一化一旦激进到把它们并成一个，判据就开始撒谎了。
    """
    assert normalize_key("activePageCode") == normalize_key("active_page_code")
    assert normalize_key("isMain") == normalize_key("is_main")
    assert normalize_key("objectTypeId") == normalize_key("object_type_id")
    assert normalize_key("rules") != normalize_key("rules_json")


def test_verdict_uses_the_normalized_view_and_reports_the_missing_content_keys():
    """★ 回归：**同一套模型、两种拼写**时，结论必须是"身份键全对得上"，
    同时**点名**参考侧那几个在新通道里找不到对应的键。

    2026-09-22 真机实测踩到两次，两次的错法不同：

    1. 第一版只看裸键集 ⇒ `Jaccard 0.14 ⇒ B（另一套模型）`，而真相是 snake_case ↔ camelCase；
    2. 第二版只归一、只看 Jaccard ⇒ `0.48 ⇒ B`，而这次 B **是对的**，只是理由说错了：
       真正的问题不是"拼写"，是**拿行投影去比写入体**——候选侧多出 9 个行元数据列
       （`created_by` / `full_path` / `sort`…），而参考侧缺的正是
       `controls` / `sections` / `pages` / `rules` 这四个**内容本体**。

    所以这条断言三件事一起钉：归一后召回升上去、多出来的只是行元数据、**缺的那四个要被点名**。
    """
    reference = dict.fromkeys(LAYOUT_SAVE_17_KEYS)
    # 真机 component instance 的行投影：全部 snake_case + 若干行元数据
    candidate = {
        "id": "x", "object_id": "x", "object_type_id": "x", "status": 1, "source": 2,
        "is_main": True, "label_col": 0, "active_page_code": None, "active_section_code": None,
        "form_edit_status": 0, "rules_json": "[]", "skip_required_validation": None,
        "belong_id": "x", "code": "x", "full_path": "x", "name": "x", "active": True,
        "sort": 0, "description": None, "created_by": "x", "created_time": "x",
        "modified_by": "x", "modified_time": "x",
    }
    report = compare_shapes(reference, candidate, scopes=("顶层",))
    assert len(report.diffs) == 2, "每个作用域应当出裸 + 归一两份"
    raw, norm = report.diffs
    assert not raw.normalized and norm.normalized
    assert norm.recall_reference > raw.recall_reference, "归一后召回必须显著抬升"
    assert norm.jaccard > raw.jaccard

    # 归一把"身份键"全部对上：17 个参考键里 13 个能在候选侧找到
    assert len(norm.common) == 13, f"归一后只对上 {len(norm.common)} 个，与真机实测不符"
    # 缺的正是内容本体——**必须被点名**，否则"B"这个字母没有信息量
    assert set(norm.only_reference) == {"controls", "pages", "rules", "sections"}
    # 多出来的应当全是行元数据，而不是"另一套业务字段"
    assert {"createdby", "createdtime", "modifiedby", "modifiedtime", "fullpath", "belongid"} <= set(
        norm.only_candidate
    )
    assert norm.verdict == "B", "身份键齐、内容键缺 ⇒ 仍然是 B（不是 A）"
    assert "命名约定差异显著" in report.convention_gap


def test_verdict_needs_both_similarity_and_coverage():
    """**A 要"像"与"覆盖得住"同时成立。**

    只看 Jaccard：候选侧多一批行元数据列就会被判成模型不同；
    只看召回：最要紧的内容键缺失会被放过去。两个反例各钉一次。
    """
    # 覆盖满、但候选侧多了一堆列 ⇒ Jaccard 被压到 0.5 ⇒ 不进 A
    ref = {"a", "b"}
    cand = {"a", "b", "c", "d"}
    d = diff_key_sets(ref, cand, scope="顶层")
    assert d.recall_reference == 1.0
    assert d.verdict == "B"

    # 像、但只覆盖了参考侧一小半 ⇒ 也不进 A
    ref2 = {"a", "b", "c", "d", "e"}
    cand2 = {"a", "b", "c", "d", "e", "a2", "b2", "c2", "d2", "e2"}
    d2 = diff_key_sets(ref2, cand2, scope="顶层")
    assert d2.recall_reference == 1.0
    assert d2.verdict == "B"


def test_explain_gap_normalizes_before_deciding_what_is_missing():
    """★ 回归：**先归一判缺，再对确实缺的做归因**。

    2026-09-22 实测踩到：第一版拿**裸键集**做差，于是 `combinationDisplayFieldId`
    （候选侧其实有 `combination_display_field_id`）被算进"真正需要查的"——
    13 条里 9 条是假的。判据自己撒谎，而且撒得"看起来很专业"（一长串待查项）。
    """
    reference = {
        "code", "name",                          # 逐字相同
        "combinationDisplayFieldId",             # 归一后对得上
        "gridSpan",                              # 归一后对得上
        "attrs",                                 # 序列化变体
        "sectionId",                             # 序列化变体（→ section_code，见下表）
        "dataType",                              # 真缺
    }
    candidate = {
        "code", "name", "combination_display_field_id", "grid_span",
        "attrs_json", "section_code", "created_by", "modified_time", "width",
    }
    gap = explain_gap(reference, candidate)
    assert gap["serialized"] == ["attrs → attrs_json（序列化）"]
    assert gap["unexplained"] == ["dataType"], (
        f"归一把对得上的键误判成缺口了，或已知映射没被认出来：{gap['unexplained']}"
    )
    assert gap["row_meta"] == ["created_by", "modified_time"]
    # `section_code` 是 `sectionId` 的已知对应，所以**不该**再出现在"候选侧多出来的"里
    assert gap["candidate_only_extra"] == ["width"]


def test_explain_gap_knows_the_id_to_code_reference_rename():
    """`sectionId` → `section_code` 是**引用改编码**（结构差异），不是拼写差异，
    但它**同样是已知的**（真机对照出来的），不该被列进"需要查的"。"""
    gap = explain_gap({"sectionId"}, {"section_code"})
    assert gap["known"] == ["sectionId → section_code（引用改编码）"]
    assert gap["unexplained"] == []


def test_explain_gap_does_not_list_a_serialized_twin_as_candidate_extra():
    """序列化列是**同一概念的两种写法**，不该在"候选侧多出来的"里再出现一次。"""
    gap = explain_gap({"attrs"}, {"attrs_json"})
    assert gap["serialized"] == ["attrs → attrs_json（序列化）"]
    assert "attrs_json" not in gap["candidate_only_extra"]


def test_convention_gap_stays_quiet_when_there_is_no_convention_difference():
    same = {"code": "x", "name": "y", "source": 2}
    report = compare_shapes(same, dict(same), scopes=("顶层",))
    assert "不显著" in report.convention_gap
    assert report.verdict == "A"


def test_compare_shapes_skips_a_scope_absent_on_both_sides():
    report = compare_shapes({"a": 1}, {"a": 1}, scopes=("顶层", "controls[]"))
    # 每个存活的作用域出两份（裸 + 归一），所以是 2 条而不是 1 条
    assert {d.scope for d in report.diffs} == {"顶层"}
    assert len(report.diffs) == 2
    assert any("两边都没有" in n for n in report.notes)


# ---------------------------------------------------------------- 形状抽取


def test_key_profile_reports_a_histogram_not_just_a_union():
    """并集会把"这次全是新建元素"讲成"模型不一样"——所以必须同时报直方图。"""
    profile = key_profile({"controls": [{"a": 1, "b": 2}, {"a": 1, "b": 2, "c": 3}]})
    assert profile["controls[]"]["elemKeys"] == ["a", "b", "c"]
    assert profile["controls[]"]["shapeHistogram"] == {"2": 1, "3": 1}


def test_key_profile_ignores_values_and_order():
    assert key_profile({"b": 1, "a": [1, 2]})["keys"] == ["a", "b"]


# ---------------------------------------------------------------- 真机基线


@pytest.mark.skipif(not load_layout_saves(), reason="factory-tuning 录制数据缺席（该目录不属于产品）")
class TestRecordedBaseline:
    """把 `docs/PLATFORM-OPERATIONS-LEARNED.md` §1/§2 的口径变成**可执行断言**。"""

    def test_top_level_is_17_keys_on_a_save_and_11_on_a_shell(self):
        saves = load_layout_saves()
        sizes = {len(r["postData"]) for r in saves}
        assert sizes == {11, 17}, f"顶层键数出现意外取值：{sizes}"
        for rec in saves:
            body = rec["postData"]
            expected = LAYOUT_SAVE_17_KEYS if len(body) == 17 else LAYOUT_SHELL_11_KEYS
            assert tuple(sorted(body)) == tuple(sorted(expected))
            # 建壳的定义就是**不带 id**
            assert ("id" in body) == (len(body) == 17)

    def test_controls_and_sections_unions(self):
        controls: set[str] = set()
        sections: set[str] = set()
        for rec in load_layout_saves():
            profile = key_profile(rec["postData"])
            controls |= set(profile.get("controls[]", {}).get("elemKeys", []))
            sections |= set(profile.get("sections[]", {}).get("elemKeys", []))
        assert len(controls) == CONTROLS_UNION_SIZE
        assert len(sections) == SECTIONS_UNION_SIZE

    def test_new_control_and_section_have_their_compact_shapes(self):
        assert set(CONTROL_NEW_8_KEYS) <= {
            k for rec in load_layout_saves()
            for k in key_profile(rec["postData"]).get("controls[]", {}).get("elemKeys", [])
        }
        seen_five = False
        for rec in load_layout_saves():
            hist = key_profile(rec["postData"]).get("sections[]", {}).get("shapeHistogram", {})
            if "5" in hist:
                seen_five = True
        assert seen_five, "没见过 5 键的精简部分形状——与段 1/2 的取证口径不符"

    def test_real_data_has_mixed_shapes_inside_one_array(self):
        """★ **这条是"逐元素"口径的反证。**

        2026-09-22 段 2 更正过一次错口径：早期报告把"整份看着精简 / 整份看着完整"
        当成两种请求体，实际上是**同一条请求里，新建元素精简、已存在元素完整**。

        判据：真机数据里必须存在"**同一个数组内出现两种以上键数**"的样本。
        谁把比较简化成"整份请求一个形状"，这条就会红。
        """
        mixed = []
        for rec in load_layout_saves():
            profile = key_profile(rec["postData"])
            for scope in ("controls[]", "sections[]"):
                hist = profile.get(scope, {}).get("shapeHistogram", {})
                if len(hist) > 1:
                    mixed.append((rec["_session"] if "_session" in rec else "?", scope, hist))
        assert mixed, "真机数据里没有任何'同数组多形状'样本 —— 与段 2 的取证结论矛盾"

    def test_control_shape_sizes_are_a_small_known_set(self):
        """形状大小是**离散的少数几种**，不是连续谱——这条让"没见过的大小"一眼可见。"""
        seen: set[int] = set()
        for rec in load_layout_saves():
            hist = key_profile(rec["postData"]).get("controls[]", {}).get("shapeHistogram", {})
            seen |= {int(k) for k in hist}
        assert seen <= CONTROL_SHAPE_SIZES_SEEN, f"出现了从没见过的控件形状大小：{seen - CONTROL_SHAPE_SIZES_SEEN}"
        assert {8, 23} <= seen, "8 键（新建）与 23 键（完整）两种形状都应出现过"
