"""请求体形状分析：**真实发的键 vs 我们蓝图模型声明的键**。

## 它回答什么

    _ 平台实际接受这些键，而我们的模型里没有：xxx, yyy      ← 蓝图 schema 的缺口
    _ 我们声明了这些键，但真实流量从没见过：aaa, bbb        ← 可能是我们编的

这两句就是"配置工厂跟人手套的差在哪"最硬的证据。上一版蓝图 schema 是从上游
36 行 prompt 抄来的；这个分析把它换成**从真实流量反推**。

## 端点 → 模型的对应关系是**手写的**，这是刻意的

没有机器可读的"这个端点对应那个模型"映射——`writers/fields.py` 里是命令式代码。
硬凑一个自动推断（比如按名字相似度）会产出看起来合理、实际错位的结论，
而那比"只覆盖少数几个端点"坏得多。所以下面这张表**只列能确认的**，
其余端点只报"见过哪些键"，不下结论。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from workbench.services.egmp.kernel import endpoints as EP

#: 端点模板 → (蓝图模型名, 说明)。**只放能确认的对应关系。**
ENDPOINT_MODELS: dict[str, tuple[str, str]] = {
    EP.SAVE_BASIC_OBJECT: ("ObjectDef", "建对象"),
    EP.SAVE_FIELD: ("FieldDef", "建字段"),
    EP.STATUS_CREATE: ("StatusDef", "建生命周期状态"),
    EP.WORKFLOW_ADD: ("WorkflowDef", "建工作流"),
    EP.WORKFLOW_ADD_TASK_STEP: ("WorkflowStepDef", "建审批步骤"),
    EP.WORKFLOW_ADD_DECISION_STEP: ("WorkflowStepDef", "建判断步骤"),
    EP.WORKFLOW_ADD_ACTION_STEP: ("WorkflowStepDef", "建动作步骤"),
}

#: 这些键是**平台包装字段**（信封/租户/追踪），不是业务结构，比对时要排除，
#: 否则报告会被 id/tenantId 这类噪声淹没，真正的缺口看不见。
ENVELOPE_KEYS = frozenset({
    "id", "tenantid", "tenant_id", "appid", "app_id", "version", "lang", "language",
    "traceid", "trace_id", "requestid", "request_id", "timestamp", "pagesize", "pageindex",
})

#: 这些键是**工厂自己的控制键**：只给编排层读，**从不发给平台**。
#: ⇒ 它们出现在「我们声明了、真实流量从没见过」那一栏是**必然的**，不是缺口；
#: 不排除就等于在报告里长期挂一行**永远修不掉的噪声**（而那份报告的读者是"据此判断
#: schema 该不该改"的人，噪声会直接变成错误结论）。
#: 加键的判据：**产品代码里从来没有任何一处把它写进请求体**。
LOCAL_CONTROL_KEYS = frozenset({
    "createifmissing",   # 2026-09-22 加：对象不存在时是否按新建处理（ADR-0018 §8）
})


@dataclass
class EndpointShape:
    endpoint: str
    model: str = ""
    note: str = ""
    seen_keys: list[str] = field(default_factory=list)
    modeled_keys: list[str] = field(default_factory=list)
    unmodeled: list[str] = field(default_factory=list)
    unseen: list[str] = field(default_factory=list)
    samples: int = 0


def model_fields(model_name: str) -> list[str]:
    """从**运行期模型**取字段名（不在这里抄第二份）。"""
    from workbench.services.egmp.writers import blueprint as bp

    model = getattr(bp, model_name, None)
    if model is None or not hasattr(model, "model_fields"):
        return []
    return list(model.model_fields)


def _keys_of(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {str(k) for k in value}
    return set()


def analyze(entries: list[Any]) -> list[EndpointShape]:
    """按端点聚合"真实见过的请求体键"，与对应模型的字段做双向差集。"""
    buckets: dict[str, dict[str, Any]] = {}
    for entry in entries:
        endpoint = entry.endpoint
        bucket = buckets.setdefault(endpoint, {"keys": set(), "samples": 0})
        keys = _keys_of(entry.post_data)
        if keys:
            bucket["keys"] |= keys
            bucket["samples"] += 1

    shapes: list[EndpointShape] = []
    for endpoint, bucket in sorted(buckets.items()):
        seen = sorted(k for k in bucket["keys"] if k.lower() not in ENVELOPE_KEYS)
        model_name, note = "", ""
        for template, (name, why) in ENDPOINT_MODELS.items():
            if _template_matches(endpoint, template):
                model_name, note = name, why
                break
        declared = model_fields(model_name) if model_name else []
        lower_declared = {name.lower(): name for name in declared}
        shapes.append(EndpointShape(
            endpoint=endpoint, model=model_name, note=note,
            seen_keys=seen, modeled_keys=declared,
            unmodeled=[k for k in seen if k.lower() not in lower_declared],
            unseen=[name for name in declared
                    if name.lower() not in {k.lower() for k in seen}
                    and name.lower() not in LOCAL_CONTROL_KEYS],
            samples=bucket["samples"],
        ))
    return shapes


def _template_matches(observed: str, template: str) -> bool:
    if "{" not in template:
        return observed == template
    left, right = observed.split("/"), template.split("/")
    return len(left) == len(right) and all(
        r.startswith("{") or l == r for l, r in zip(left, right, strict=True))
