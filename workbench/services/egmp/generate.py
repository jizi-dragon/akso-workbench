"""蓝图生成（orchestrate 模式的需求 → 蓝图）。

融合决策（记录于 docs/迁移台账.md）：
- 复杂度评估：忠实移植 akso-auto gmp/complexity.js（确定性规则）；
- 蓝图 JSON 生成：**不再移植** 36.8KB 的确定性关键词库，改由 mogul 的
  DeepSeek 客户端按本模块的结构化提示词生成（全家桶灵魂：mogul harness
  即 LLM 底座）→ 经 normalize_blueprint 规范化 → validate_blueprint 两层校验，
  校验不通过带 issues 回炉一次。
"""

from __future__ import annotations

import json
from typing import Any

from .complexity import assess_complexity
from .writers.blueprint import normalize_blueprint, validate_blueprint

_SYSTEM_PROMPT = """你是 eGMP 平台配置蓝图生成器。输出严格 JSON（不要 markdown 围栏）。
蓝图 Schema 要点：
- 顶层：{description, objects:[…], picklists:[…], menu?, rules?, permissions?, reportTemplates?}
- objects[]: {name, code, enableLifeCycle, enableSignatures, picklists?, fields, formLayout?, listLayout?, lifecycle?, workflows?}
- code 规范：对象/选项集/工作流用 snake_case + __c 后缀；状态用 status_xxx__c
- fields[]: {name, code, dataType, isRequired?, picklistCode?(dataType=4 必填),
  referenceObjectCode?(dataType 13/15/16/23 必填), max/min/decimalPlaces?(2),
  attachmentCount/Size/Extension?(8), returnType+formulaScript?(14)}
  dataType: 1文本 2数字 3是否 4选项 5日期 7日期时间 8附件 13查找 14公式 15对象 16父对象 17长文本 18富文本 23对象多选
- lifecycle: {statuses:[{name,code}], renameStatuses:[{fromCode:"init_status__c"|"complete_status__c", toName}]}
  （启用生命周期时系统自动生成 开启 init_status__c 与 关闭 complete_status__c 两个不可删状态，
  蓝图只能 renameStatuses 重命名它们）
- workflows[]: {name, code, autoEnable, bindToStatusCode, participants:[{name,code}],
  steps:[{name, code, type:"task"|"decision"|"action", participantRef?, nextStepCode?,
  behaviorType?(action,9=修改状态), behaviorValue?(目标状态编码), dispatchMode?(10会签/20或签)}],
  rules?:[{decisionStep, sourceStep, jumpTo, conditionLabel, matchValue}]}
- formLayout.sections[]: {name, code, type:1, columnsNum:2, fields:[字段编码]}
- listLayout: {name?, columns:[{fieldCode, sort}]}
- rules/permissions/reportTemplates 为声明区（引擎不执行，人工配置项）"""


def generate_blueprint(requirement: str, *, llm_chat_fn=None,
                       max_repair_rounds: int = 1) -> dict[str, Any]:
    """需求描述 → 复杂度评估 → LLM 蓝图 → 规范化 → 校验（失败回炉）。

    llm_chat_fn(prompt: str) -> str：注入 mogul DeepSeek 客户端。
    """
    complexity = assess_complexity(requirement)
    depth = complexity["strategy"]["depth"]
    prompt = (
        f"{_SYSTEM_PROMPT}\n\n## 需求描述\n{requirement}\n\n"
        f"## 复杂度策略\n深度 {depth}（basic 只输出对象+核心字段；standard 加生命周期"
        f"与默认布局；full 加工作流/菜单/完整布局与声明区）\n\n只输出蓝图 JSON。"
    )
    raw_text = llm_chat_fn(prompt)
    blueprint = _extract_json(raw_text)
    blueprint.setdefault("description", requirement[:200])
    blueprint, notes = normalize_blueprint(blueprint)

    issues = validate_blueprint(blueprint)
    repair_round = 0
    while any(i.level == "error" for i in issues) and repair_round < max_repair_rounds:
        error_lines = "\n".join(f"- [{i.path}] {i.message}" for i in issues if i.level == "error")
        raw_text = llm_chat_fn(
            f"{_SYSTEM_PROMPT}\n\n上一版蓝图校验失败：\n{error_lines}\n\n"
            f"原蓝图：\n{json.dumps(blueprint, ensure_ascii=False)}\n\n只输出修正后的完整蓝图 JSON。"
        )
        blueprint = _extract_json(raw_text)
        blueprint, round_notes = normalize_blueprint(blueprint)
        notes.extend(round_notes)
        issues = validate_blueprint(blueprint)
        repair_round += 1

    return {
        "blueprint": blueprint,
        "complexity": complexity,
        "issues": [i.model_dump() for i in issues],
        "notes": notes,
        "valid": not any(i.level == "error" for i in issues),
    }


def _extract_json(text: str) -> dict[str, Any]:
    """从 LLM 输出提取 JSON（容错 markdown 围栏/前后噪声）。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"LLM 输出中未找到 JSON 对象：{text[:120]}…")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("蓝图根节点必须是 JSON 对象")
    return data


def format_review_prompt(generation: dict[str, Any]) -> str:
    """迭代确认卡片（gmp/iteration.js formatReviewPrompt 口径）。"""
    complexity = generation["complexity"]
    issues = generation["issues"]
    lines = [
        "─── 蓝图审阅 ───",
        f"复杂度：{complexity['level']}（深度 {complexity['strategy']['depth']}）",
        f"校验：{'通过' if generation['valid'] else '存在阻断问题'}",
    ]
    if issues:
        lines.append("问题清单：")
        lines += [f"- [{i['level']}] {i['path']}: {i['message']}" for i in issues[:10]]
    if generation["notes"]:
        lines.append("规范化：")
        lines += [f"- {note}" for note in generation["notes"][:8]]
    lines.append("请审阅蓝图（spec.md/checklist.md），回复「批准」或修改意见（最多 5 轮）。")
    return "\n".join(lines)
