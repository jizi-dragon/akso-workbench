r"""在线层（计划 §6.3）：用真实模型重录夹具的**入口与闸门**。

## 这一节补的是什么

在此之前，「换成真 `DeepSeekClient`」只写在 `run_evals.py` 的文档串与 `runner` 的一行
注释里 —— 而**文档里的一句话不是一个能跑的东西**：要按它做，得手工改源码、记住改回来、
还得自己处理"没有 Key 时怎么办"。★ 同 PITFALLS #110 的族：
**声明了但没有入口的能力，等于没有。**

## ★ 这一层的闸门是承重的，不是形式

`--live --yes` 会：① 发 `estimate_live_calls()` 次**真实**模型请求（花钱）；
② 覆盖**受版本控制**的 `tests/eval/fixtures/*.json`。

而本仓的固定端口那件事（[ADR-0012](../docs/adr/0011-port-governance.md)）留过一条教训：
**"预览"与"真跑"如果都不报错，人就会分不清自己在哪一个里**。所以这里的断言
全部围绕"**能不能分清**"来写：

| 断言 | 它防的是 |
|---|---|
| `--live` 不带 `--yes` ⇒ **一次请求都不发** | 想看一眼要花多少，结果真跑了 |
| 没有 Key ⇒ **抛错**，不回落脚本 | 以为在量真模型，实际量的是脚本（最坏的一种"友好"） |
| 有 Key ⇒ 真的造出 `DeepSeekClient` | 开关接线了但没接上（"能跑"只是文档里的） |
| 两种录制的 `note` **如实**区分 | 把"链路走对了"读成"模型选对了" |
| 估算来自**现有夹具的调用数** | 拍一个数字当成本依据 |
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.eval import runner  # noqa: E402


def _load_tool():
    """把 `tools/run_evals.py` 当模块载进来（它不是包的一部分）。"""
    spec = importlib.util.spec_from_file_location("_run_evals", ROOT / "tools" / "run_evals.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Args:
    def __init__(self, **kw):
        self.live = kw.get("live", True)
        self.yes = kw.get("yes", False)
        self.record = kw.get("record", False)


# ---------------------------------------------------------------- 闸门


def test_live_without_yes_sends_nothing(monkeypatch, capsys) -> None:
    """★★ `--live` 不带 `--yes` ⇒ **一次请求都不发**。

    这是本层最重要的那一条。它防的失败形态很具体：用户想知道"这一下要花多少"，
    打了个 `--live` 想看看 —— 结果钱花了、夹具被覆盖了，而他**以为那只是个预览**。
    """
    tool = _load_tool()
    sent: list[str] = []

    monkeypatch.setattr(runner, "live_client", lambda: type("C", (), {"model": "m"})())
    monkeypatch.setattr(runner, "record_all",
                        lambda client=None: sent.append("called") or 12)

    code = tool._live(_Args(yes=False))
    out = capsys.readouterr().out
    assert code == 0, "预览不是错误，退出码必须是 0（它是正常路径）"
    assert sent == [], f"预览模式下竟然真的录了：{sent}"
    assert "预览" in out and "一次请求都没发" in out, out
    assert "--live --yes" in out, "要给出**能直接复制**的确认命令"


def test_live_with_yes_actually_records(monkeypatch, capsys) -> None:
    """反向断言：带了 `--yes` 就**真的录**。

    没有这一条的话，一个"永远只预览"的实现也能让上面那条通过 ——
    而那意味着这个开关**从来不工作**，且没有任何东西会告诉你。
    """
    tool = _load_tool()
    sent: list[str] = []

    monkeypatch.setattr(runner, "live_client", lambda: type("C", (), {"model": "m"})())
    monkeypatch.setattr(runner, "record_all",
                        lambda client=None: sent.append("called") or 12)

    code = tool._live(_Args(yes=True))
    out = capsys.readouterr().out
    assert code == 0 and sent == ["called"], (code, sent)
    assert "真实模型" in out
    assert "期望值一行都不该改" in out, "要提醒：夹具锁的是请求形状，不是模型选得对不对"


def test_live_refuses_with_a_readable_message_when_there_is_no_key(monkeypatch, capsys) -> None:
    """没有 Key ⇒ 退出码 1 ＋ 一句看得懂的话（不是堆栈）。"""
    tool = _load_tool()

    def _boom():
        raise runner.NoApiKey("测试：没有 Key")

    monkeypatch.setattr(runner, "live_client", _boom)
    code = tool._live(_Args(yes=True))
    out = capsys.readouterr().out
    assert code == 1
    assert "✗" in out and "没有 Key" in out, out
    assert "Traceback" not in out


# ---------------------------------------------------------------- 客户端的选取


def test_no_key_raises_instead_of_falling_back(monkeypatch) -> None:
    """★★ 没有 Key 时**抛错**，绝不回落到脚本化客户端。

    回落是最坏的一种"友好"：用户以为自己在量真模型，量到的还是脚本 ——
    与 `ReplayLLM(strict=False)` 当年那个 **180 秒挂起**是同一族
    （**看起来在工作，实际不是你以为的那件事**）。
    """
    from workbench.services import settings as settings_mod

    monkeypatch.setattr(settings_mod, "load_deepseek",
                        lambda: type("S", (), {"api_key": "", "model": "m", "ready": False})())
    with pytest.raises(runner.NoApiKey) as exc:
        runner.live_client()
    assert "不会**回落到" in str(exc.value) or "不会" in str(exc.value)


def test_a_configured_key_really_builds_the_real_client(monkeypatch) -> None:
    """★ 有 Key ⇒ 真的造出 `DeepSeekClient`。

    只验"抛错路径"是不够的：一个**永远抛错**的实现也能让上一条通过，
    而那意味着在线层根本接不上线（"开关装了但没接线"）。
    """
    from workbench.services import settings as settings_mod

    monkeypatch.setattr(settings_mod, "load_deepseek",
                        lambda: type("S", (), {"api_key": "sk-not-a-real-key",
                                               "model": "deepseek-flash", "ready": True})())
    client = runner.live_client()
    assert type(client).__name__ == "DeepSeekClient"
    assert client.model == "deepseek-flash"
    assert client.api_key == "sk-not-a-real-key"


# ---------------------------------------------------------------- note 如实


def test_the_two_kinds_of_recording_are_labelled_truthfully() -> None:
    """★★ `note` **如实**写清是脚本录的还是真机录的。

    反过来（自称真机、实为脚本）会让人把"**链路走对了**"读成"**模型选对了**" ——
    那正是这个字段存在的唯一理由。
    """
    from tests.eval import cases as cases_mod

    case = cases_mod.CASES[0]
    synthetic = runner.record_case(case)
    assert synthetic["note"] == runner.SYNTHETIC_NOTE
    assert "合成" in synthetic["note"]

    class _FakeReal:
        model = "deepseek-flash"
        last_usage: dict = {}

        async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
            yield {"type": "end", "content": "好", "tool_calls": [],
                   "usage": {"prompt_tokens": 1, "prompt_cache_hit_tokens": 0,
                             "prompt_cache_miss_tokens": 1, "completion_tokens": 1},
                   "finish_reason": "stop"}

    live = runner.record_case(case, client=_FakeReal())
    assert live["note"] == runner.LIVE_NOTE
    assert "真机" in live["note"]
    assert live["note"] != synthetic["note"], "两种录制必须有**不同的** note"


def test_the_offline_gate_still_uses_the_synthetic_fixtures() -> None:
    """★ 落盘的夹具仍然是**合成**的那一份。

    在线层是**手动**的（要花钱），所以"仓库里躺着的是哪一种"必须能被断言 ——
    否则一次手滑的 `--live --yes` 之后，门禁回放的就是真机录的东西，
    而那需要联网、且不再可复现（同一条请求，模型明天可能答得不一样）。
    """
    fixtures = sorted(runner.FIXTURE_DIR.glob("*.json"))
    assert fixtures, "夹具目录是空的"
    for path in fixtures:
        note = json.loads(path.read_text(encoding="utf-8")).get("note", "")
        assert note == runner.SYNTHETIC_NOTE, f"{path.name} 不是合成夹具：{note}"


# ---------------------------------------------------------------- 估算


def test_the_estimate_comes_from_the_real_fixtures() -> None:
    """★ 估算**来自现有夹具里的调用数**，不是拍出来的。

    用户要凭这个数字决定"这一下要花多少钱"。一个拍出来的估计值，
    与一个拍出来的价格一样没用。
    """
    total = sum(len(json.loads(p.read_text(encoding="utf-8")).get("interactions") or [])
                for p in runner.FIXTURE_DIR.glob("*.json"))
    assert runner.estimate_live_calls() == total
    assert total > 0, "估算为 0 说明它没读到夹具（那会让'要花多少'变成一个假数字）"
