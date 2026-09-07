"""确定性职责标注（akso-cc understand/annotate.ts 的确定性部分）。

LLM 摘要（llmSummarize）通过可选回调注入——Workbench 中由 mogul 的
DeepSeek 客户端承担（全家桶融合红利：mogul harness 即 LLM 底座）。
"""

from __future__ import annotations

from typing import Any

from ..auth import is_reference_field


def annotate_purpose(object_meta: dict[str, Any], fields: list[dict[str, Any]],
                     lifecycle_state_count: int, workflow_count: int) -> dict[str, Any]:
    traits: list[dict[str, str]] = []
    code = str(object_meta.get("code") or "")
    name = str(object_meta.get("name") or "")

    if code.endswith("__c"):
        traits.append({"trait": "自定义对象（__c）", "evidence": f"编码 {code}"})
    elif code.endswith("__ak"):
        traits.append({"trait": "标准对象（__ak）", "evidence": f"编码 {code}"})
    elif code.endswith("__sys"):
        traits.append({"trait": "系统对象（__sys）", "evidence": f"编码 {code}"})

    if lifecycle_state_count:
        traits.append({"trait": "启用生命周期", "evidence": f"{lifecycle_state_count} 个状态"})
        if workflow_count:
            traits.append({"trait": "生命周期×工作流驱动",
                           "evidence": f"{workflow_count} 条工作流绑定/发起"})
    if any((f.get("dataType") == 23) for f in fields):
        traits.append({"trait": "含对象多选引用", "evidence": "存在 dataType=23 字段"})
    tree_like = any(f.get("enableTree") for f in fields) or object_meta.get("enableTree")
    if tree_like:
        traits.append({"trait": "树形结构", "evidence": "enableTree 启用（存在自环引用）"})

    reference_fields = [f for f in fields if is_reference_field(int(f.get("dataType") or 0))]
    if len(reference_fields) >= 3:
        traits.append({"trait": "强关联枢纽对象",
                       "evidence": f"{len(reference_fields)} 个引用型字段（13/15/16/23）"})

    if any("审批" in str(f.get("name") or "") for f in fields) or lifecycle_state_count >= 3:
        traits.append({"trait": "审批/流转语义", "evidence": "审批字段或多状态生命周期"})

    summary_bits = [f"{name}（{code}）"]
    if lifecycle_state_count and workflow_count:
        summary_bits.append(f"以生命周期（{lifecycle_state_count} 状态）与工作流（{workflow_count} 条）驱动流转")
    elif lifecycle_state_count:
        summary_bits.append(f"启用 {lifecycle_state_count} 状态的生命周期")
    if reference_fields:
        summary_bits.append(f"关联 {len(reference_fields)} 个引用型字段")
    return {"summary": "：".join(summary_bits[:1]) + "，".join(summary_bits[1:]) + "。",
            "traits": traits}
