r"""评测执行器（计划 §6.3）。

## 三个刻意的设计选择

### 1. **真工具面 + 桩 handler**

评测要量的是"给定模型的输出，链路把它走对了吗"。所以工具的
**声明**（名字 / 描述 / 参数模型）必须是**真的** —— 那才是模型看到的东西，
也是"工具选择"这个词有意义的前提；而 **handler 是桩**：
不碰平台、不碰网络、不写库。

★ 这不只是"为了安全"，更是为了**可复现**：真 handler 会让评测依赖平台状态，
于是"这次没过"与"平台上那个对象刚好不在了"分不开。

### 2. **模型侧走 `record.ReplayLLM`，不走脚本化客户端**

夹具按**请求指纹**匹配，指纹含 `messages + tools + temperature + ...`。
所以改了系统提示词、工具描述、或消息装配方式，回放会 **miss 并列出可用键**。

这正是计划 §6.3 要的那条门禁（"任何改 `harness/` / `prompts.py` / 工具描述的改动，
必须附离线评测 before/after 数"）—— 而**脚本化客户端做不到这件事**：
那种写法下改提示词不会有任何反应，评测会一直绿着骗人。

### 3. 报告里的数字要能**被质疑**

`工具选择准确率` 的分母是**所有用例**（不是"跑了工具的用例"），
否则"少调工具"与"没调对"的用例会被悄悄排除出分母，指标只会更好看。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from workbench import approvals, cache, db
from workbench import plugin_api as pa
from workbench.harness import record
from workbench.harness.loop import run_agent_loop
from workbench.harness.tools import default_registry
from workbench.harness.types import AgentConfig

from . import cases as cases_mod
from .cases import MODEL, Case

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

#: 合成夹具的统一标注。★ 它**必须**出现在每个夹具里 —— 一份看起来像真机录制的
#: 合成夹具，比没有夹具更坏：它会让人以为"工具选择准确率"量的是模型。
SYNTHETIC_NOTE = "合成：由脚本化客户端录制（非真实模型回答）。换真机：tools/run_evals.py --record"
LIVE_NOTE = "真机：由真实模型回答录制。★ 期望值一行都不用改（夹具锁的是请求形状）"


# ---------------------------------------------------------------- 桩工具面


class StopAfter:
    """前 `n` 次问"要停吗"都答 False，之后答 True（把停止精确落在某个检查点上）。"""

    def __init__(self, n: int) -> None:
        self.n = n
        self.reads = 0

    def __call__(self) -> bool:
        self.reads += 1
        return self.reads > self.n


def _stub(spec: pa.ToolSpec, calls: list[str], case: Case):
    """给一个真实 `ToolSpec` 造桩 handler。**保留声明，只换实现**。"""

    async def handler(args, ctx) -> str:  # noqa: ANN001
        calls.append(spec.name)
        if spec.name in case.stub_raises:
            raise RuntimeError(f"桩：{spec.name} 故意失败（用例 {case.id}）")
        if spec.name in case.stub_returns:
            return case.stub_returns[spec.name]
        return json.dumps({"ok": True, "stub": True, "tool": spec.name}, ensure_ascii=False)

    return handler


def eval_hub(case: Case) -> tuple[pa.ToolHub, list[str]]:
    """真实工具面 + 桩 handler。返回 `(hub, 被桩记录到的工具调用序列)`。"""
    real = default_registry()
    hub = pa.ToolHub()
    calls: list[str] = []
    by_plugin: dict[str, list[pa.ToolSpec]] = {}
    for spec in real.specs():
        by_plugin.setdefault(spec.plugin_id, []).append(
            replace(spec, handler=_stub(spec, calls, case))
        )
    for plugin_id, specs in by_plugin.items():
        hub.add_plugin(pa.Plugin(id=plugin_id, title=plugin_id, tools=specs))
    return hub, calls


# ---------------------------------------------------------------- 录制


class ScriptedClient:
    """按脚本"回答"的假模型 —— **只用于录制夹具**，不参与回放。

    ⚠️ 它故意**不**在回放路径上：让评测跑在脚本上就等于评测没测到
    "请求形状变了没有"（见模块文档串第 2 条）。
    """

    def __init__(self, script: list[dict], *, model: str = MODEL) -> None:
        self._script = list(script)
        self.model = model
        self.last_usage: dict = {}

    async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
        step = self._script.pop(0) if self._script else {"content": "（脚本耗尽）",
                                                        "tool_calls": []}
        yield {
            "type": "end",
            "content": step.get("content", ""),
            "tool_calls": step.get("tool_calls", []),
            "usage": {"prompt_tokens": 100, "prompt_cache_hit_tokens": 60,
                      "prompt_cache_miss_tokens": 40, "completion_tokens": 10},
            "finish_reason": step.get("finish_reason", "stop"),
        }

    async def chat(self, messages, temperature=0.7, max_tokens=None, json_mode=False):
        self.last_usage = {"prompt_tokens": 5}
        return "摘要"


async def _drive_case(case: Case, client, hub: pa.ToolHub) -> list[dict]:
    """跑一条用例，返回事件列表。"""
    cache.clear_state(_conv(case))
    # 停止令牌按会话造，但**不注册**到 routes_chat 的登记表里 ——
    # 评测要的是停止真的发生在检查点上，而 StopAfter 直接提供那个信号。
    # （登记表那条链路由 tests/test_agent_stop.py 覆盖，两处不重复。）
    config = AgentConfig(system_prompt=_system_prompt(), max_steps=case.max_steps,
                         approval_wait_s=case.approval_wait_s)
    should_stop = StopAfter(case.stop_after) if case.stop_after is not None else None

    out: list[dict] = []
    async for event in run_agent_loop(
        [{"role": "user", "content": case.user}], client, hub, config,
        conversation_id=_conv(case), should_stop=should_stop,
    ):
        out.append(event)
        if event["type"] == "confirm_required" and case.answer_confirm is not None:
            store = approvals.pending_store()
            if case.answer_confirm:
                store.approve(event["pendingId"])
            else:
                store.reject(event["pendingId"])
    return out


def _conv(case: Case) -> str:
    return f"eval-{case.id}"


def _system_prompt() -> str:
    """与生产**同一个**系统提示词。

    ★ 不自己写一份：评测要验的是"生产那条链路"，而系统提示词是链路的一部分
    （它进指纹，所以改了它评测会 miss —— 那正是我们要的）。
    """
    from workbench.harness.prompts import build_system_prompt

    return build_system_prompt()


def _prepare(case: Case) -> tuple[pa.ToolHub, list[str]]:
    """装好 approvals + 桩工具面，返回 `(hub, calls)`。

    ⚠️ **它改全局状态**（`pa._hub` 是进程级单例）。所以调用方**必须**走
    `_eval_environment()`，不要直接用它 —— 见那个上下文管理器的文档串。
    """
    approvals.reset_store()
    hub, calls = eval_hub(case)
    approvals.install(hub)
    pa._hub = hub  # noqa: SLF001 —— 内置插件（确认工具）按单例取中枢
    return hub, calls


@contextlib.contextmanager
def _eval_environment(case: Case):
    """装好评测环境，**退出时逐字还原全局状态**。

    ★★ 这里必须还原 `pa._hub`：它是**进程级单例**，而评测把它换成
    "真声明 + 桩 handler"的工具面。不换回去的话，**之后所有用例看到的都是桩**。

    实测（2026-09-24，第一次跑全量时）：不还原 ⇒ 全量测试 **8 条红**，
    报的是 `Invocation(... content='{"ok": true, "stub": true, ...}')` ——
    也就是"工具全都返回桩的返回值"。而★ **评测自己是全绿的**：
    它先跑、把单例换掉，然后一切看起来都很正常，只有别处的用例在莫名其妙地失败。

    这条与本仓那句老话是同一件事：**"测试之间共享的全局状态"是归因错误的温床**
    （PITFALLS #71 同族）。卸载比装载更需要被写出来。
    """
    previous_hub = pa._hub  # noqa: SLF001
    try:
        hub, calls = _prepare(case)
        yield hub, calls
    finally:
        pa._hub = previous_hub  # noqa: SLF001
        approvals.reset_store()


# ---------------------------------------------------------------- 录制 / 回放


class NoApiKey(RuntimeError):
    """没有配置 API Key —— **在线层唯一的入口条件**。"""


def live_client():
    """造一个**真实**的 `DeepSeekClient`（计划 §6.3 的在线层）。

    ## ★ 它为什么是个函数，而不是文档里的一句话

    在此之前，「换成真 `DeepSeekClient`」只写在 `run_evals.py` 的文档串与这里的一行注释里
    —— 而**文档里的一句话不是一个能跑的东西**：要真按它做，得手工改源码、记住改回来、
    还得自己处理"没有 Key 时怎么办"。★ 本仓对这类东西的态度见 PITFALLS #110：
    **声明了但没有入口的能力，等于没有**。

    ## 没有 Key 时**抛错**，不回落

    回落成脚本化客户端是最坏的一种"友好"：用户以为自己在量真模型，
    而量到的还是脚本 —— 与 `ReplayLLM(strict=False)` 当年那个 180 秒挂起是同一族
    （**看起来在工作，实际不是你以​为的那件事**）。所以这里直接抛 `NoApiKey`，
    由调用方把它变成一句看得懂的提示。
    """
    from workbench import db as _db
    from workbench.services.deepseek import DeepSeekClient
    from workbench.services.settings import load_deepseek

    if not getattr(_db, "_conn", None):  # 还没连库 —— 读设置要走 DB
        _db.connect()
    cfg = load_deepseek()
    if not cfg.ready:
        raise NoApiKey(
            "在线评测需要一个真实可用的 DeepSeek API Key（设置页里那份），"
            "当前读不到。它**不会**回落到脚本化客户端 —— 那样量到的不是真模型。"
        )
    return DeepSeekClient(cfg.api_key, model=cfg.model)


def record_case(case: Case, client=None) -> dict:
    """录一条用例的模型侧夹具。

    `client is None` ⇒ **合成**（`ScriptedClient`，编好的回答）；
    给了 `client` ⇒ **真机**（`live_client()`，真花钱）。

    ★ `note` 字段**如实**写清是哪一种 —— 一份自称"合成"而其实是真机录的夹具
    没有害处，反过来（自称真机、实为脚本）会让人把"链路走对了"读成"模型选对了"，
    而那正是这个字段存在的唯一理由。
    """
    live = client is not None
    with _eval_environment(case) as (hub, _calls):
        inner = client if live else ScriptedClient(case.script)
        recorder = record.RecordingClient(inner, name=case.id)
        asyncio.run(_drive_case(case, recorder, hub))
    fixture = recorder.fixture
    fixture["note"] = LIVE_NOTE if live else SYNTHETIC_NOTE
    fixture["case"] = case.id
    return fixture


def record_all(client=None) -> int:
    """录全部用例并落盘。返回写了几个文件。

    ⚠️ **它会覆盖 `tests/eval/fixtures/*.json`**（受版本控制）。
    真机重录之后，`--offline` 回放的就是真实模型的回答了 —— 那是本节的本意
    （见 `run_evals` 的文档串），但**期望值一行都不该改**：夹具锁的是请求形状。
    """
    db.connect()
    written = 0
    for case in cases_mod.CASES:
        record.save_fixture(record_case(case, client=client), FIXTURE_DIR / f"{case.id}.json")
        written += 1
    return written


def estimate_live_calls() -> int:
    """真机重录大约要发多少次模型请求 —— **按现有夹具里的调用数估**。

    ★ 不编一个数字：用户要凭它决定"这一下要花多少钱"，
    而一个拍出来的估计值与一个拍出来的价格一样没用。
    """
    total = 0
    for case in cases_mod.CASES:
        path = FIXTURE_DIR / f"{case.id}.json"
        try:
            total += len(json.loads(path.read_text(encoding="utf-8")).get("interactions") or [])
        except (OSError, ValueError):
            # 没有夹具 ⇒ 保守按"每条用例至少 1 次"算（模型至少要回一句）
            total += 1
    return total


@dataclass
class CaseResult:
    case_id: str
    title: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    """模型**选择**的工具序列（`action` 事件）—— 与 `handlers` **不是**同一个东西。"""

    handlers: list[str] = field(default_factory=list)
    """**真的执行了** handler 的工具序列（安全判据看这个）。"""

    tools_match: bool = False
    steps: int = 0
    events: list[str] = field(default_factory=list)
    prompt_tokens: int = 0
    cache_hit_tokens: int = 0
    replay_misses: list[str] = field(default_factory=list)


def _attempted(events: list[dict]) -> list[str]:
    """模型**选择**调用了哪些工具（按顺序）。

    ★ 取 `action` 事件而不是"handler 被调用的记录"，两者是不同的问题：

    - `action` = **模型的工具选择** —— 这是"工具选择准确率"该量的东西。
      被确认门拦下的那次**确实是一次选择**，把它排除掉会让指标失去意义
      （等于只统计"成功了的"）。
    - handler 调用 = **实际发生了什么** —— 那是**安全**判据，落在
      `expect_handler_not_run` 上。
    """
    return [str(e.get("call", {}).get("name") or "") for e in events
            if e["type"] == "action"]


def _check(case: Case, events: list[dict], calls: list[str],
           misses: list[str]) -> list[str]:
    """把期望逐条对一遍。返回失败原因（空 = 过）。"""
    bad: list[str] = []
    kinds = [e["type"] for e in events]
    attempted = _attempted(events)

    if misses:
        bad.append(f"回放 miss {len(misses)} 条 —— 请求形状变了（提示词/工具描述/消息装配）")

    if attempted != case.expect_tools:
        bad.append(f"工具选择不符：期望 {case.expect_tools}，实际 {attempted}")

    for name in case.expect_handler_not_run:
        # ★ 这条**只能**看 handler 调用记录，不能看 `action`：
        # "被拦下"与"没被选择"是两件事，而前者才是安全上要钉住的那件。
        if name in calls:
            bad.append(f"{name} 的 handler **被执行了**（应当被拦下/跳过）")

    for name in case.expect_handlers:
        # ★ 正向那一半：「预批准真的放行了」这类性质必须由"它跑了"来证，
        # 否则一个什么都不执行的实现也能满足全部否定式断言。
        if name not in calls:
            bad.append(f"{name} 的 handler **没有执行**（期望它真的跑了）：实际 {calls}")

    # ★ `error.message` 也算"用户看得见的正文"：错误路径上**没有** `final`，
    # 那句话就是用户看到的全部内容。不算进来的话，那条用例的 `expect_content`
    # 只能写成空 —— 而"错误路径也要把话说到位"恰恰是最该被评测钉住的。
    text = "".join(
        str(e.get("content") or e.get("message") or "")
        for e in events if e["type"] in ("final", "token", "cancelled", "error")
    )
    for fragment in case.expect_content:
        if fragment not in text:
            bad.append(f"最终回答里没有「{fragment}」：{text[:80]!r}")

    for kind in case.expect_events:
        if kind not in kinds:
            bad.append(f"事件流里没有 {kind}：{kinds}")

    if case.expect_event_order:
        cursor = -1
        for kind in case.expect_event_order:
            try:
                cursor = kinds.index(kind, cursor + 1)
            except ValueError:
                bad.append(f"事件顺序不符（找不到 {kind} 在 {case.expect_event_order} 中的位置）：{kinds}")
                break
    return bad


def run_offline_case(case: Case) -> CaseResult:
    """回放一条用例并核对期望。"""
    fixture_path = FIXTURE_DIR / f"{case.id}.json"
    if not fixture_path.exists():
        return CaseResult(case_id=case.id, title=case.title, passed=False,
                          failures=[f"缺少夹具 {fixture_path.name}"
                                    "（跑 `python tools\\run_evals.py --record` 生成）"])

    with _eval_environment(case) as (hub, calls):
        return _replay_case(case, hub, calls, fixture_path)


def _replay_case(case: Case, hub, calls: list[str], fixture_path) -> CaseResult:
    # ★ **严格模式**：夹具 miss 要当场抛，不能静默继续。
    #
    # 用 `strict=False` 的话，miss 时 `stream_chat` 一个事件都不发 ⇒
    # 请求形状一变（改了提示词/工具描述），评测不是"红"，而是**挂住**
    # —— 实测：反证改一句系统提示词，`--offline` 跑满 180 秒没结束。
    # 那正是 PITFALLS #101 记着的形态：**失败一点都不清楚，人只会以为脚本坏了**。
    client = record.ReplayLLM(record.load_fixture(fixture_path), strict=True)
    try:
        events = asyncio.run(_drive_case(case, client, hub))
    except record.ReplayMiss as exc:
        return CaseResult(case_id=case.id, title=case.title, passed=False,
                          replay_misses=[client.calls[-1] if client.calls else ""],
                          failures=[
                              "★ 回放 miss —— **不是行为变了，是请求形状变了**"
                              "（系统提示词 / 工具描述 / 消息装配）。\n"
                              "     处置：`python tools\\run_evals.py --record` 重录，"
                              "再看 `git diff tests/eval/fixtures/` —— 那个 diff 就是"
                              "「工具面变了多少」的答案，把它附在提交里（计划 §6.3 的门禁）。\n"
                              f"     {str(exc).splitlines()[0]}"
                          ])

    usage = next((e.get("usage") or {} for e in reversed(events)
                  if e["type"] == "usage"), {})
    attempted = _attempted(events)
    result = CaseResult(
        case_id=case.id, title=case.title, passed=False,
        tools=attempted, tools_match=(attempted == case.expect_tools),
        steps=len([e for e in events if e["type"] in ("action", "observation")]),
        events=[e["type"] for e in events],
        prompt_tokens=int(usage.get("promptTokens") or 0),
        cache_hit_tokens=int(usage.get("cacheHitTokens") or 0),
        replay_misses=list(client.misses),
    )
    result.handlers = list(calls)
    result.failures = _check(case, events, calls, client.misses)
    result.passed = not result.failures
    return result


def run_offline(only: list[str] | None = None) -> list[CaseResult]:
    db.connect()
    selected = [c for c in cases_mod.CASES if not only or c.id in only]
    return [run_offline_case(case) for case in selected]


# ---------------------------------------------------------------- 报告


def summarize(results: list[CaseResult]) -> dict:
    """汇总成一份**可比较**的报告（计划 §6.3 点名的五项 + 回放覆盖度）。

    ★ `工具选择准确率` 的分母是**所有用例**：把"没调对/少调工具"的用例排除出分母
    会让指标天然好看，而那正是评测最该暴露的部分。
    """
    total = len(results) or 1
    passed = sum(1 for r in results if r.passed)
    tools_ok = sum(1 for r in results if r.tools_match)
    steps = sum(r.steps for r in results) / total
    prompt = sum(r.prompt_tokens for r in results)
    hit = sum(r.cache_hit_tokens for r in results)
    return {
        "cases": len(results),
        "successRate": round(passed / total, 4),
        "toolSelectionAccuracy": round(tools_ok / total, 4),
        "avgSteps": round(steps, 2),
        "promptTokens": prompt,
        "cacheHitTokens": hit,
        "cacheHitRate": round(hit / prompt, 4) if prompt else 0.0,
        "replayMisses": sum(len(r.replay_misses) for r in results),
        "failed": [r.case_id for r in results if not r.passed],
    }


def format_report(summary: dict, results: list[CaseResult]) -> str:
    lines = [
        "离线评测（夹具回放，不联网不花 token）",
        f"  用例 {summary['cases']}  成功率 {summary['successRate']:.0%}  "
        f"工具选择准确率 {summary['toolSelectionAccuracy']:.0%}  平均步数 {summary['avgSteps']}",
        f"  输入 token {summary['promptTokens']}  缓存命中 {summary['cacheHitTokens']} "
        f"（{summary['cacheHitRate']:.0%}）  回放 miss {summary['replayMisses']}",
    ]
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        lines.append(f"  [{mark}] {r.case_id:32} {len(r.tools)} 次工具调用")
        for reason in r.failures:
            lines.append(f"         └ {reason}")
    return "\n".join(lines)
