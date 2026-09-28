r"""离线评测的门禁入口（计划 §6.3）。

CI 里的形态：`pytest tests/eval -o addopts=""`（这些用例**进 CI**，
不花 token、不联网 —— 它们跑的是夹具回放）。

## 这里的每一条用例都对应一个**手写**的期望

见 `cases.py` 的模块文档串：夹具可以是合成的，**期望必须是人写的**。
从一次运行结果里抄期望 = 同义反复，那种评测只能抓回归、答不了"对不对"。

## ★ 一条用例失败时最先看什么

`FAILED ... - AssertionError: 回放 miss N 条` ⇒ **不是**行为变了，是**请求形状变了**
（改了系统提示词 / 工具描述 / 消息装配）。这时该做的是：

1. `python tools\run_evals.py --record` 重录夹具；
2. `git diff tests/eval/fixtures/` —— **那个 diff 就是"工具面变了多少"的量化答案**；
3. 把 before/after 数附在提交里（计划 §6.3 的门禁）。

★ 这条路径是这套东西存在的理由：**它让"悄悄改了提示词"变成一次可见的失败**。
"""

from __future__ import annotations

import pytest

from . import cases as cases_mod
from .runner import run_offline_case


@pytest.mark.parametrize("case", cases_mod.CASES, ids=[c.id for c in cases_mod.CASES])
def test_offline_case(case) -> None:  # noqa: ANN001
    result = run_offline_case(case)

    assert result.passed, (
        f"用例 {case.id}（{case.title}）没过：\n  - "
        + "\n  - ".join(result.failures)
        + f"\n  实际事件流：{result.events}"
        + ("\n  ★ 回放 miss ⇒ 请求形状变了（提示词 / 工具描述 / 消息装配）。"
           "重录：python tools\\run_evals.py --record" if result.replay_misses else "")
    )


def test_the_case_table_has_no_duplicate_ids() -> None:
    """用例 id 唯一 —— 夹具文件名就是 id，重名会让两条用例悄悄共用一份夹具。"""
    ids = [c.id for c in cases_mod.CASES]
    assert len(ids) == len(set(ids)), f"有重复的用例 id：{sorted(ids)}"


def test_every_case_has_a_hand_written_expectation() -> None:
    """★ **每条用例都要有可判定的期望**（计划 §6.3 原话："否则不是评测是感觉"）。

    判据是"至少有一项非空期望"：工具序列 / 关键结论 / 事件类型，三者至少要有一个。
    一个只有 `user` + `script` 的用例是**不可判定**的 —— 它会永远绿，
    而它绿的理由只是"程序跑完了"。
    """
    empty = [c.id for c in cases_mod.CASES
             if not (c.expect_tools or c.expect_content or c.expect_events
                     or c.expect_event_order or c.expect_handler_not_run)]
    assert not empty, f"这些用例没有任何期望，等于没测：{empty}"


def test_cases_that_expect_no_tools_say_so_explicitly() -> None:
    """`expect_tools=[]` 与"忘了写"是两件事。

    这条防的是"忘了填期望"被当成"期望就是不调工具"—— 后者是一个**正当**的用例
    （`no_tool_needed`），所以它必须由 `note` 明说自己是有意为之。
    """
    suspects = [c.id for c in cases_mod.CASES
                if not c.expect_tools and not c.note and not c.expect_handler_not_run]
    assert not suspects, (
        f"这些用例期望「不调工具」但没有 `note` 说明 —— 分不清是有意还是漏填：{suspects}"
    )
