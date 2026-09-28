"""Agent 运行记录：把**一次用户回合**记成一个 `agent_run`，把里面每一步记成一个 `agent_span`。

## 为什么需要它（计划 §6.1）

现在"一次用户回合"在数据里**不是一个实体**：`agent_audit` 记的是**单次工具调用**，
`deepseek_usage` 记的是**一次回答的用量**，两者只能靠**时间戳猜**关联。后果三条：

1. **失败回合的账无处落** —— §5.4a 之前异常出口连用量都不落（已修），但"这一回合失败了"
   这件事本身仍然没有载体；
2. **"这次为什么慢"只能翻日志** —— 而日志是给人看的文本，不是可聚合的数据；
3. **跨步的因果表达不了** —— "第 3 步的超时导致第 5 步重试"在审计表里是两行无关的记录。

## 三张表的分工（**不要合并**）

| 表 | 它是什么 | 权威性 |
|---|---|---|
| `agent_audit` | **审计**：谁在什么时候对哪个对象做了什么 | 有合规含义，只增不改 |
| `deepseek_usage` | **计费**：token 与缓存命中 | 成本口径的**唯一**事实源 |
| `agent_run` / `agent_span` | **运行轨迹**：一次回合里每一步的耗时/状态/用量 | 只为"把一次回合当成一个整体看" |

★ 三者是"同一个事件的三份投影"，而**权威各自独立** —— 这是铁律 28 的同一条
（"两处都写全量 = 第二个会漂的事实源"）。所以这里**不复制**审计维度：
`attrs_json` 只放与该步有关的最小内容 + 指向审计的 `auditId`，**不复制参数与账号**。

## OTel 对齐靠**导出层**，不靠改表

表结构保持本仓的读法（前端直接查、可 SQL 聚合）；`gen_ai.*` 语义约定在
`tools/export_otel.py` 里做映射 —— **零运行时依赖**（不为一个导出功能把 otel-sdk
拖进 sidecar）。**属性名逐条核实的状态记在那张映射表旁边**，不在本模块。

## 失败绝不影响回答

本模块的每一次写库都吞异常并计数（同 `cache.UsageStats.persist` / PITFALLS #96）：
**轨迹是给人事后看的，不是回答的一部分**。但"刻意吞掉"必须有能被看见的地方 ——
所以失败进 `trace_write_failures()`，由 `/api/agent/selfcheck` 暴露。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from .. import db
from ..services.storage import now_ms

logger = logging.getLogger("workbench.trace")

#: span 的种类。`sandbox` 与 `confirm` 是**预留**：沙箱执行目前记在 `sandbox_run` 表，
#: 实时确认记在 `pending_action` 表 —— 它们各自的权威表都更详细，
#: 这里只在这两类步骤**发生在一次回合内**时补一条占位 span，好让时间线不断链。
SPAN_MODEL = "model"
SPAN_TOOL = "tool"
SPAN_SANDBOX = "sandbox"
SPAN_CONFIRM = "confirm"

SPAN_KINDS = (SPAN_MODEL, SPAN_TOOL, SPAN_SANDBOX, SPAN_CONFIRM)

#: `agent_run.status` 的取值。
#:
#: `aborted` 与 `failed` **刻意分开**：前者是"用户关掉了页面/切了会话"
#: （消费方 `aclose()`，抛的是 `GeneratorExit`），后者是"它真的坏了"。
#: 两者对用户的含义完全不同 —— 与 §5.4c 的「（已中止）」vs「错误：…」是同一条区分。
RUN_RUNNING = "running"
RUN_SUCCEEDED = "succeeded"
RUN_FAILED = "failed"
RUN_ABORTED = "aborted"

_TRACE_WRITE_FAILURES = 0


def trace_write_failures() -> int:
    """轨迹写入失败的进程内累计次数（`/api/agent/selfcheck` 暴露它）。

    与 `cache.usage_persist_failures` 同一个理由：**把"刻意吞掉的失败"变成"看得见的失败"**。
    一条永远不会被读到的 `logger.warning` 等于没有。
    """
    return _TRACE_WRITE_FAILURES


def _write(sql: str, params: tuple) -> bool:
    global _TRACE_WRITE_FAILURES
    try:
        db.execute(sql, params)
        return True
    except Exception:  # noqa: BLE001 —— 轨迹失败绝不影响回答
        _TRACE_WRITE_FAILURES += 1
        logger.warning("运行轨迹写入失败（进程内累计 %d 次）",
                       _TRACE_WRITE_FAILURES, exc_info=True)
        return False


def _dumps(value: Any) -> str:
    try:
        return json.dumps(value or {}, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return "{}"


class RunRecorder:
    """一次用户回合的记录器。**出口只有一个**：`finish()`。

    ## 为什么是"记录器"而不是一组自由函数

    run 的生命周期跨越整个 `run_agent_loop`，而循环有**多个出口**
    （final / error / 预算耗尽 / 上下文超限 / 异常 / 消费方断开）。
    把"开一条、收一条"交给调用方记得，就会重演 §5.4a 与 §5.4c 那两个缺陷
    （异常出口不收尾）。所以它被设计成**一个对象**，由循环在 `finally` 里收，
    与 `_accounted`（用量入账）同一个形状、同一个理由。

    ## 写入点只有两处

    `span()` 由循环在两个地方调用：模型调用前后、工具调用前后。
    与审计同一条纪律 —— **写在扼流点，不靠调用方记得**（I7 的同一条教训）。
    """

    def __init__(self, conversation_id: str = "", model: str = "") -> None:
        self.run_id = uuid.uuid4().hex
        self.conversation_id = conversation_id
        self.model = model
        self.steps = 0
        self.status = RUN_RUNNING
        self.error = ""
        self._started = time.perf_counter()
        _write(
            "INSERT INTO agent_run (id, conversation_id, started_at, status, model) "
            "VALUES (?, ?, ?, ?, ?)",
            (self.run_id, conversation_id, now_ms(), RUN_RUNNING, model),
        )

    # ---- span ----

    def span(self, kind: str, name: str, *, started: float, status: str = "ok",
             error: str = "", attrs: dict | None = None,
             duration_ms: int | None = None) -> None:
        """记一步。`started` 是 `time.perf_counter()` 的读数（调用方在动作**之前**取）。

        `step` 从 1 开始、**在一次 run 内单调递增** —— 时间线视图按它排序，
        而它比时间戳可靠：同一毫秒内的两步在时间戳上分不出先后。

        `duration_ms`：给了就用它，不再按 `started` 现算。工具那一步必须这样 ——
        耗时应取 `ToolInvocation.duration_ms`（`ToolHub` 自己测的，**不含**用户等待），
        而不是从"发起"到"拿到结果"的墙钟（后者会把"用户点了 3 分钟才同意"算成工具耗时，
        于是"哪个工具慢"这个问题的答案完全失真）。
        """
        self.steps += 1
        if duration_ms is None:
            duration_ms = int((time.perf_counter() - started) * 1000)
        _write(
            "INSERT INTO agent_span (id, run_id, step, span_kind, name, started_at, "
            "duration_ms, status, error, attrs_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, self.run_id, self.steps, kind, name, now_ms(),
             int(duration_ms), status, error[:500], _dumps(attrs)),
        )

    def fail(self, message: str) -> None:
        """标记这一回合**失败**（终态由 `finish()` 落库）。

        多个失败原因时**保留第一条**：第一条是根因，后面的多半是它的连锁反应
        （"网络断了"之后的每一个错都是它的下游）。
        """
        if self.status != RUN_FAILED:
            self.status = RUN_FAILED
            self.error = message

    def abort(self) -> None:
        """标记这一回合**被中止**（消费方断开，不是它坏了）。见 `RUN_ABORTED`。"""
        if self.status == RUN_RUNNING:
            self.status = RUN_ABORTED

    # ---- 收尾 ----

    def finish(self, usage: dict | None = None) -> None:
        """落终态。**必须在 `finally` 里调用**（见类文档串）。

        `usage` 传 `UsageMeter.finish()` 的结果（camelCase），
        没传就按 0 记 —— 那一回合确实没产生可计的调用（例如在第一次模型调用前就失败了）。
        """
        if self.status == RUN_RUNNING:
            self.status = RUN_SUCCEEDED
        usage = usage or {}
        _write(
            "UPDATE agent_run SET ended_at = ?, status = ?, error = ?, steps = ?, "
            "prompt_tokens = ?, cache_hit_tokens = ?, cache_miss_tokens = ?, "
            "completion_tokens = ?, summary_calls = ?, latency_ms = ? WHERE id = ?",
            (now_ms(), self.status, self.error[:500], self.steps,
             int(usage.get("promptTokens") or 0),
             int(usage.get("cacheHitTokens") or 0),
             int(usage.get("cacheMissTokens") or 0),
             int(usage.get("completionTokens") or 0),
             int(usage.get("summaryCalls") or 0),
             int((time.perf_counter() - self._started) * 1000),
             self.run_id),
        )


# ---------------------------------------------------------------- 查询（前端时间线用）


def recent_runs(limit: int = 20) -> list[dict]:
    """最近若干次运行（含状态、步数、耗时、用量）。"""
    size = max(1, min(int(limit), 200))
    return db.query(
        "SELECT id, conversation_id AS conversationId, started_at AS startedAt, "
        "ended_at AS endedAt, status, error, steps, model, prompt_tokens AS promptTokens, "
        "cache_hit_tokens AS cacheHitTokens, cache_miss_tokens AS cacheMissTokens, "
        "completion_tokens AS completionTokens, summary_calls AS summaryCalls, "
        "latency_ms AS latencyMs FROM agent_run ORDER BY started_at DESC LIMIT ?",
        (size,),
    )


def run_spans(run_id: str) -> list[dict]:
    """一次运行的全部 span，**按 `step` 升序**（时间线视图按它排）。"""
    rows = db.query(
        "SELECT id, run_id AS runId, step, span_kind AS spanKind, name, "
        "started_at AS startedAt, duration_ms AS durationMs, status, error, "
        "attrs_json AS attrsJson FROM agent_span WHERE run_id = ? ORDER BY step ASC",
        (run_id,),
    )
    for row in rows:
        try:
            row["attrs"] = json.loads(row.pop("attrsJson") or "{}")
        except (TypeError, ValueError):
            row["attrs"] = {}
    return rows
