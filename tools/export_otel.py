"""把 `agent_run` / `agent_span` 导出成 OTLP/JSON（OpenTelemetry GenAI 语义约定）。

## 为什么是"导出层"而不是"改表"

计划 §6.1 / [ADR-0021](../docs/adr/0021-agent-run-and-otel-export.md) 的决策：
**不动**既有的三张表（它们是审计/计费/轨迹各自的事实源），额外做一层映射。
两个好处：

- **零运行时依赖** —— 不为一个导出功能把 otel-sdk 拖进 sidecar（那是几 MB 与一棵依赖树）；
- **表结构保持本仓的读法** —— 前端直接 SQL 查、可聚合，不必懂 OTLP。

## ★ 属性名的核实状态（写清楚，别让它变成假共识）

**写这份映射时（2026-09-24）网络不可达**（`github.com` / `raw.githubusercontent.com`
均 DNS 失败），**没有逐条对着规范核**。本仓此前也明确记着这条边界
（`CORE-ARCHITECTURE.md` §7.5："`gen_ai.*` 属性约定存在并被采用；**具体属性名未逐条核**"）。

所以这里把映射**集中成一张表**（`OTEL_ATTRS`），每条带一个核实状态。
于是"逐条核对"从一句空泛的提醒变成一条**可执行的检查**：

    python tools\\export_otel.py --list-attributes

打出来，对着 <https://opentelemetry.io/docs/specs/semconv/gen-ai/> 逐行确认，
把 `verify` 改成 `ok` 即可 —— **改一处，全仓一致**。

## 只做 OTLP，不做 Langfuse 专有格式

OTLP/JSON 是**标准**（有公开的 wire format）。Langfuse / Phoenix 等生态工具的
**导入格式**我没有核实过，所以**不凭印象造第二份 schema** ——
接它们应当走各自的 OTLP 入口，而不是在这里伪造它们的私有格式。

## 用法

    python tools\\export_otel.py --dry-run          # 打印摘要 + 结构片段（不写文件）
    python tools\\export_otel.py --out traces.json   # 写文件
    python tools\\export_otel.py --list-attributes   # 打印待核实的属性名清单
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workbench import db  # noqa: E402
from workbench.harness import trace  # noqa: E402

#: 我们的数据 → OTel GenAI 属性名。`(属性名, 核实状态)`。
#:
#: ★ **所有条目当前都是 `verify`** —— 见模块文档串。`ok` 只应在
#: 真的对着规范确认过之后才写。**不要**因为"看起来对"就改成 `ok`。
OTEL_ATTRS: dict[str, tuple[str, str]] = {
    # —— 操作与提供方 ——
    "operation_agent": ("gen_ai.operation.name", "verify"),
    "operation_chat": ("gen_ai.operation.name", "verify"),
    "operation_tool": ("gen_ai.operation.name", "verify"),
    "provider": ("gen_ai.provider.name", "verify"),
    # —— 请求 ——
    "request_model": ("gen_ai.request.model", "verify"),
    "request_temperature": ("gen_ai.request.temperature", "verify"),
    # —— 用量 ——
    "input_tokens": ("gen_ai.usage.input_tokens", "verify"),
    "output_tokens": ("gen_ai.usage.output_tokens", "verify"),
    # —— 工具 ——
    "tool_name": ("gen_ai.tool.name", "verify"),
    "tool_call_id": ("gen_ai.tool.call.id", "verify"),
    # —— 会话 ——
    "conversation_id": ("gen_ai.conversation.id", "verify"),
}

#: 我们自己的属性一律走这个前缀。**它们不是标准**，但把"标准属性"与"本仓特有"
#: 分开是必要的：混在一起之后，没人分得清哪些能被通用工具读懂。
WB_PREFIX = "workbench."

_SERVICE_NAME = "workbench-agent"


def _attr(key: str, value: Any) -> dict:
    """OTLP/JSON 的 `KeyValue` 形状。值类型必须显式标注（这是 wire format 的要求）。"""
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    return {"key": key, "value": {"stringValue": str(value)}}


def _otel_name(slot: str) -> str:
    return OTEL_ATTRS[slot][0]


def _root_span_id(run_id: str) -> str:
    """根 span 的 id：由 run id **确定性派生**（同一份数据导两次必须一样）。

    不用随机数：导出是幂等的，而随机 id 会让"两次导出做 diff"永远全是差异。
    """
    return hashlib.sha256(f"root:{run_id}".encode()).hexdigest()[:16]


def _model_span(run: dict, span: dict, parent: str) -> dict:
    usage = (span.get("attrs") or {}).get("usage") or {}
    attrs = [
        _attr(_otel_name("operation_chat"), "chat"),
        _attr(_otel_name("request_model"), run.get("model") or ""),
        _attr(_otel_name("input_tokens"), int(usage.get("promptTokens") or 0)),
        _attr(_otel_name("output_tokens"), int(usage.get("completionTokens") or 0)),
        _attr(_otel_name("conversation_id"), run.get("conversationId") or ""),
        # 本仓特有：标准里没有对应物的那几个
        _attr(WB_PREFIX + "cache.hit_tokens", int(usage.get("cacheHitTokens") or 0)),
        _attr(WB_PREFIX + "finish_reason", (span.get("attrs") or {}).get("finishReason") or ""),
        _attr(WB_PREFIX + "step", span["step"]),
    ]
    return _span(run, span, parent, f"chat {run.get('model') or ''}", attrs)


def _tool_span(run: dict, span: dict, parent: str) -> dict:
    a = span.get("attrs") or {}
    attrs = [
        _attr(_otel_name("operation_tool"), "execute_tool"),
        _attr(_otel_name("tool_name"), span.get("name") or ""),
        _attr(_otel_name("conversation_id"), run.get("conversationId") or ""),
        # 指针，不是副本：审计才是参数与账号的事实源（铁律 28）
        _attr(WB_PREFIX + "audit.id", a.get("auditId") or ""),
        _attr(WB_PREFIX + "tool.plugin", a.get("plugin") or ""),
        _attr(WB_PREFIX + "tool.risk", a.get("risk") or ""),
        _attr(WB_PREFIX + "step", span["step"]),
    ]
    return _span(run, span, parent, f"execute_tool {span.get('name') or ''}", attrs)


def _span(run: dict, span: dict, parent: str, name: str, attrs: list[dict]) -> dict:
    start_ns = int(span["startedAt"]) * 1_000_000
    end_ns = start_ns + int(span.get("durationMs") or 0) * 1_000_000
    out: dict[str, Any] = {
        "traceId": str(run["id"]),
        # span id 取我们主键的前 16 位十六进制（8 字节）—— OTLP 要求 8 字节
        "spanId": str(span["id"])[:16],
        "parentSpanId": parent,
        "name": name,
        "kind": 1,  # SPAN_KIND_INTERNAL
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": attrs,
    }
    if span.get("status") not in ("ok", "", None):
        # OTLP status.code：1 = OK，2 = ERROR。被中止/截断也按 ERROR 报 ——
        # 通用工具只认这两个值，多出来的语义放在 `workbench.*` 属性里。
        out["status"] = {"code": 2, "message": str(span.get("error") or span["status"])}
    return out


def _root_span(run: dict) -> dict:
    start_ns = int(run["startedAt"]) * 1_000_000
    end_ns = int(run.get("endedAt") or run["startedAt"]) * 1_000_000
    failed = run.get("status") != trace.RUN_SUCCEEDED
    attrs = [
        _attr(_otel_name("operation_agent"), "invoke_agent"),
        _attr(_otel_name("request_model"), run.get("model") or ""),
        _attr(_otel_name("input_tokens"), int(run.get("promptTokens") or 0)),
        _attr(_otel_name("output_tokens"), int(run.get("completionTokens") or 0)),
        _attr(_otel_name("conversation_id"), run.get("conversationId") or ""),
        _attr(WB_PREFIX + "run.status", run.get("status") or ""),
        _attr(WB_PREFIX + "run.steps", int(run.get("steps") or 0)),
        _attr(WB_PREFIX + "cache.miss_tokens", int(run.get("cacheMissTokens") or 0)),
        _attr(WB_PREFIX + "summary.calls", int(run.get("summaryCalls") or 0)),
    ]
    span: dict[str, Any] = {
        "traceId": str(run["id"]),
        "spanId": _root_span_id(str(run["id"])),
        "parentSpanId": "",
        "name": "invoke_agent",
        "kind": 1,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": attrs,
    }
    if failed and run.get("status") != trace.RUN_ABORTED:
        span["status"] = {"code": 2, "message": str(run.get("error") or run.get("status"))}
    return span


def to_otlp(limit: int = 50) -> dict:
    """把最近若干次运行导成 OTLP/JSON。**纯函数**（同样的库 → 同样的字节）。"""
    spans: list[dict] = []
    for run in trace.recent_runs(limit):
        root = _root_span(run)
        spans.append(root)
        for span in trace.run_spans(str(run["id"])):
            builder = _tool_span if span["spanKind"] == trace.SPAN_TOOL else _model_span
            spans.append(builder(run, span, root["spanId"]))
    return {
        "resourceSpans": [{
            "resource": {
                "attributes": [_attr("service.name", _SERVICE_NAME)],
            },
            "scopeSpans": [{
                "scope": {"name": "workbench.agent", "version": "1"},
                "spans": spans,
            }],
        }],
    }


def list_attributes() -> list[tuple[str, str, str]]:
    """`(槽位, 属性名, 核实状态)`，按属性名排序 —— 供人工逐条核对。"""
    return sorted(((slot, name, status) for slot, (name, status) in OTEL_ATTRS.items()),
                  key=lambda item: (item[1], item[0]))


def _print_attributes() -> None:
    print("OTel GenAI 属性名核实清单")
    print("  对着 https://opentelemetry.io/docs/specs/semconv/gen-ai/ 逐行确认，")
    print("  把 export_otel.OTEL_ATTRS 里对应的 verify 改成 ok。\n")
    width = max(len(n) for _s, n, _st in list_attributes())
    for slot, name, status in list_attributes():
        flag = "  " if status == "ok" else "❓"
        print(f"  {flag} {name.ljust(width)}  <- {slot}")
    pending = sum(1 for _s, _n, st in list_attributes() if st != "ok")
    print(f"\n  待核实 {pending} / {len(OTEL_ATTRS)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="把 agent_run/agent_span 导成 OTLP/JSON")
    parser.add_argument("--out", default="", help="写入的文件（省略则只打印摘要）")
    parser.add_argument("--limit", type=int, default=50, help="导出最近多少次运行")
    parser.add_argument("--dry-run", action="store_true", help="只打印摘要与结构片段")
    parser.add_argument("--list-attributes", action="store_true",
                        help="打印待核实的 OTel 属性名清单")
    args = parser.parse_args()

    if args.list_attributes:
        _print_attributes()
        return 0

    db.connect()
    payload = to_otlp(args.limit)
    spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    runs = sum(1 for s in spans if not s["parentSpanId"])

    pending = sum(1 for _s, _n, st in list_attributes() if st != "ok")
    print(f"运行 {runs} 条 / span {len(spans)} 条 / 待核实属性 {pending} 个")

    if args.out and not args.dry_run:
        Path(args.out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已写入 {args.out}")
    else:
        # `--dry-run`：打印**结构片段**而不是整个 payload —— 后者在有历史数据时很大，
        # 而这一步要验的是"结构合法"，不是"数据好看"。
        sample = spans[:2]
        print(json.dumps({"resourceSpans": [{"resource": payload["resourceSpans"][0]["resource"],
                                             "scopeSpans": [{"scope": payload["resourceSpans"][0]["scopeSpans"][0]["scope"],
                                                             "spans": sample}]}]},
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
