r"""反证清单本身**不会悄悄烂掉**（`tools/verify_regression_tests.py` 的守卫）。

## ★ 这一条补的是一个真实的失效

`verify_regression_tests.py` 是本仓"**每条断言都要能指名道姓说出拆掉什么它会红**"的
落地机制。它**不進 CI**，理由正当：它会临时改写源码（虽然结束时字节级还原）。

★ 而 2026-09-24 的架构重构里，它**烂掉了**：`§7.3` 在
`context.append(assistant)` 与一个空行之间插了 `transcript.record(...)`，
于是第 1 条的锚点不再匹配。而它的行为是**遇到第一个坏锚点就停并退出** ——
⇒ **其余 13 条一条都没跑**，而**没有任何东西发现这件事**（它不进 CI，也没人天天跑）。

## ★ 关键区分：校验锚点**不改写任何源码**

"它不能进 CI"的理由是**改写源码**。而**锚点新鲜度**只是一次字符串查找 ——
不写文件、不跑子进程、不花时间。⇒ **那一半完全可以进 CI**，
而它恰好是"悄悄烂掉"的那一半。

## 本文件钉住四件事

| 断言 | 它防的是 |
|---|---|
| 每条的目标文件**存在** | 文件改名/挪走之后清单还指着一个幽灵 |
| 每条的目标用例**还在**（`def <name>(`） | 用例被改名/删掉 —— 而那会让反证**变成空跑** |
| 每条锚点**在当前源码里找得到** | ★ 本轮实测撞到的那个：重构改了结构，锚点静默失效 |
| 缺陷版替换与锚点**真的不同** | 一个"替换等于原文"的用例**永远绿**，而它占着"这里验过了"的位置（PITFALLS #112） |

★ 还断言清单**不是空的**、且每个锚点在目标文件里**只出现一次** ——
出现多次时 `apply_patch` 会改错地方，而那同样表现为"反证无效"。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_harness():
    spec = importlib.util.spec_from_file_location(
        "_verify_regression", ROOT / "tools" / "verify_regression_tests.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CASES = _load_harness().CASES


def test_the_list_is_not_empty() -> None:
    """★ 清单非空 —— 一个空清单能让下面每一条都"通过"（`all([])` 为真）。"""
    assert CASES, "反证清单空了 —— 那不是'都验过了'，那是'一条都没验'"


def test_every_target_file_exists() -> None:
    missing = [f"{label} → {path}" for label, _t, path, _a, _r in CASES
               if not (ROOT / path).exists()]
    assert not missing, "反证清单指向了不存在的文件：\n  " + "\n  ".join(missing)


def test_every_target_test_still_exists() -> None:
    """★ 用例被改名/删掉会让反证**变成空跑** —— 而空跑的表现是"pytest 退出码非 0"，
    看起来与"抓到了"一模一样。

    ⚠️ 用 `def <name>(` 的文本查找而不是 `pytest --collect-only`：
    后者要为每个文件起一个 pytest 进程（十几秒），而这条守卫要能进 CI。
    ★ 它抓的是"用例没了/改名了"这一类真实漂移；抓不到的是"用例还在但变成了空壳"
    —— 那由 `verify_regression_tests.py` 自己跑一遍来抓（手动，会改写源码）。
    """
    problems = []
    for label, target, _path, _anchor, _replacement in CASES:
        file_part, _, test_name = target.partition("::")
        source = (ROOT / file_part)
        if not source.exists():
            problems.append(f"{label} → 用例文件不存在：{file_part}")
            continue
        if not test_name:
            problems.append(f"{label} → 目标没写用例名（只给了文件）：{target}")
            continue
        body = source.read_text(encoding="utf-8")
        if not re.search(rf"^def {re.escape(test_name)}\(", body, re.M):
            problems.append(f"{label} → {test_name} 不在 {file_part} 里"
                            "（改名或删掉了？反证会变成空跑）")
    assert not problems, "反证目标失效：\n  " + "\n  ".join(problems)


def test_every_anchor_is_still_present() -> None:
    """★★ **锚点必须还在当前源码里能找到。**

    ★ 这就是本轮实测撞到的那一条：重构在锚点中间插了两行，
    于是这一项**静默失效**，而脚本**遇到第一个坏锚点就停** ——
    其余 13 条一条都没跑，且没有任何东西发现。
    """
    stale = []
    for label, _target, path, anchor, _replacement in CASES:
        body = (ROOT / path).read_text(encoding="utf-8")
        if anchor not in body:
            stale.append(f"{label} → {path}（结构变了？更新这条反证的锚点）")
    assert not stale, "反证锚点失效：\n  " + "\n  ".join(stale)


def test_every_anchor_is_unique_in_its_file() -> None:
    """★ 锚点必须**唯一** —— 出现多次时 `apply_patch` 会改到第一处，
    而那可能是与被测行为无关的地方（同样表现为"反证无效"）。
    """
    ambiguous = []
    for label, _target, path, anchor, _replacement in CASES:
        body = (ROOT / path).read_text(encoding="utf-8")
        count = body.count(anchor)
        if count > 1:
            ambiguous.append(f"{label} → {path}（锚点出现 {count} 次，加长它）")
    assert not ambiguous, "反证锚点不唯一：\n  " + "\n  ".join(ambiguous)


@pytest.mark.parametrize("index", range(len(CASES)))
def test_every_replacement_really_differs(index: int) -> None:
    """★★ 缺陷版替换**必须真的与锚点不同**。

    一个"替换 == 原文"的用例**永远绿**（什么都没改，被测断言当然不红），
    而它占着"这里验过了"的位置 —— 同 PITFALLS #112 那一族。
    ★ 这一条比上面几条更值得单列：它检查的是**清单自己的内容**，
    而不是"锚点还找不找得到"。
    """
    label, _target, _path, anchor, replacement = CASES[index]
    assert replacement != anchor, f"{label}：缺陷版与锚点逐字相同 —— 这条反证是空的"
