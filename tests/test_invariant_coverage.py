r"""**不变量的反证覆盖率**（`CORE-ARCHITECTURE.md` §5 的 I1–I18）。

## 这一条补的是什么

2026-09-24 的完成度核对里，把 §5 的 18 条不变量逐条映射到「判据」与「反证」，
结果是：**6 条没有反证**（I4 / I5 / I7 / I9 / I11 / I14）。
其中 **I11 连判据都没有** —— 而 §5 给它的后果是「整个服务卡住（不是报错）」。

★ **一条不变量在文档上写着"违反后果：…"，读的人就会以为它被守着。**
而"有判据"与"判据真的会红"是两件事 —— 本会话已经反复撞到
空洞断言、写死文案、选错产物的判据（PITFALLS #112 / #117）。

## 两条机检

1. **注册表里的每一条反证都必须真的存在**（标签对得上 `verify_regression_tests.py` 的 `CASES`）
   —— 否则注册表会在别人删掉那条反证之后**继续宣称它被覆盖着**；
2. ★ **`PENDING` 必须与文档里写的那一份完全一致** ——
   它**不能悄悄变大**（有人删了反证却把它挪进 PENDING），
   而**变小是改进**（补上了反证就把它移出去，这条判据会要求同步文档）。

★ 这一份是**棘轮**：只允许往"覆盖更多"的方向动。
"""

from __future__ import annotations

import importlib.util
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: 不变量 → 守着它的**反证标签**（`verify_regression_tests.py` 的 `CASES` 第一项）。
#: ★ 一个不变量可以有多条；一条反证也可以守多条（那就都写）。
COVERED: dict[str, list[str]] = {
    "I1": ["前缀：工具轮次追加"],
    "I2": ["前缀：工具轮次追加"],
    "I3": ["装配：新增声明字段不被静默丢掉"],
    "I6": ["P0-A：确认门认领已批准条目", "实时确认：同意 A 不等于同意 B"],
    "I8": ["P0-B：敏感表与位置无关"],
    "I10": ["P0-B：敏感表与位置无关", "日志：JSON 形态打码"],
    "I11": ["I11：局部同步助手不得被直接调用"],
    "I12": ["实时确认：答复先到时不能丢（lost wakeup）"],
    "I13": ["问题①：输出截断必须显式说出来"],
    "I15": ["前缀：工具轮次追加"],
    "I16": ["跨重启：运行开头必须补悬空调用"],
    "I17": ["估算：折叠判定必须含工具 schema"],
    "I18": ["I18：外部网页必须标 UNTRUSTED"],
}

#: ★ **已知缺口**：有判据、但**还没有反证**的不变量。
#:
#: ⚠️ **这一份不能变大。** 加进去之前先问：那条判据真的会红吗？
#: 如果答案是"没试过"，那它就该被补上，而不是登记进这里。
PENDING: dict[str, str] = {
    "I4": "判据在 tests/test_dependency_direction.py；反证待补",
    "I5": "判据在 tests/test_plugin_api.py 与 tests/test_tool_face.py；反证待补",
    "I7": "判据在 tests/test_sandbox.py；反证待补",
    "I9": "判据在 tests/test_skill_scripts.py；反证待补",
    "I14": "判据在 tests/test_skills.py；反证待补",
}


def _load_cases() -> list[tuple[str, str, str, str, str]]:
    spec = importlib.util.spec_from_file_location(
        "_vrt_cov", ROOT / "tools" / "verify_regression_tests.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CASES


def _invariants_in_section_5() -> set[str]:
    """§5 里**真的列出来**的不变量编号。"""
    text = (ROOT / "docs" / "CORE-ARCHITECTURE.md").read_text(encoding="utf-8")
    start = text.index("## 5.")
    end = text.find("## 6.", start)
    return set(re.findall(r"^\| (I\d+) \|", text[start:end], re.M))


def test_every_registered_counterproof_actually_exists() -> None:
    """★ 注册表里的标签必须真的在 `CASES` 里。

    ★ 没有这一条的话，别人删掉那条反证之后，注册表会**继续宣称它被覆盖着** ——
    而那正是"看起来验过了"（比没有更坏）。
    """
    labels = {c[0] for c in _load_cases()}
    missing = [(inv, lab) for inv, labs in COVERED.items()
               for lab in labs if lab not in labels]
    assert not missing, (
        "注册表指向了不存在的反证：\n  "
        + "\n  ".join(f"{inv} → {lab!r}" for inv, lab in missing)
    )


def test_the_registry_covers_exactly_the_invariants_of_section_5() -> None:
    """★★ 覆盖表 ＋ 缺口表 **合起来**必须正好是 §5 里的那些不变量。

    ★ 两条方向都要管：
    · §5 **新增**一条不变量而没人登记 ⇒ 红（否则新不变量没有任何守卫，且没人知道）；
    · 登记了一个 §5 里**不存在**的编号 ⇒ 红（幻影引用，同 `check_doc_links` 那一族）。
    """
    declared = _invariants_in_section_5()
    registered = set(COVERED) | set(PENDING)
    assert declared, "§5 里一条不变量都没解析出来 —— 解析坏了还是文档变了？"
    assert not (declared - registered), (
        f"§5 有、而这里没登记的不变量：{sorted(declared - registered)}"
    )
    assert not (registered - declared), (
        f"这里登记了、而 §5 里没有的不变量：{sorted(registered - declared)}"
    )


def test_the_pending_ratchet_does_not_grow() -> None:
    """★★ **棘轮**：已知缺口只能是文档里写的那 5 条。

    ★ 变**小**是改进（补上反证后从 `PENDING` 移到 `COVERED`）；
    变**大**说明有人删了反证却把它登记成"已知缺口" —— 那正是这条要挡的。
    """
    expected = {"I4", "I5", "I7", "I9", "I14"}
    assert set(PENDING) == expected, (
        f"已知缺口变了：{sorted(PENDING)}（应当正好是 {sorted(expected)}）\n"
        "  ⇒ 补上了反证就从 PENDING 移到 COVERED，并同步本文件里的 expected；"
        "若你正要**新增**缺口，先回去把那条反证补上。"
    )


@pytest.mark.parametrize("inv", sorted(PENDING))
def test_a_pending_gap_names_where_its_judge_lives(inv: str) -> None:
    """★ 缺口必须**指得出判据在哪** —— 否则"有判据、只是没反证"这句话没有依据。"""
    reason = PENDING[inv]
    assert re.search(r"tests/[\w/]+\.py", reason), (
        f"{inv} 的缺口说明里没写判据文件：{reason!r}"
    )
    path = re.search(r"tests/[\w/]+\.py", reason).group(0)
    assert (ROOT / path).exists(), f"{inv} 指的判据文件不存在：{path}"
