"""复杂度评估（akso-auto gmp/complexity.js 的 Python 化，规则忠实移植）。"""

from __future__ import annotations

import re
from typing import Any

# 中文关键词计数（上游口径）
KEYWORDS = {
    "对象": 2, "生命周期": 3, "工作流": 5, "权限": 2, "字段": 1,
}


def assess_complexity(requirement: str) -> dict[str, Any]:
    text = requirement or ""
    scores = {name: len(re.findall(re.escape(word), text)) for word, name in
              {"对象": "objects", "生命周期": "lifecycle", "工作流": "workflow",
               "权限": "permission", "字段": "field"}.items()}
    hits = sum(1 for word in KEYWORDS if word in text)

    if scores["workflow"]:
        level = "high"
    elif scores["lifecycle"] and scores["objects"] > 1:
        level = "high"
    elif scores["lifecycle"]:
        level = "medium"
    elif hits >= 2:
        level = "medium"
    else:
        level = "low"

    if scores["permission"] and level != "high":
        level = "medium" if level == "low" else "high"

    strategy = {
        "low": {"depth": "basic", "useKnowledgeBase": False, "requireIteration": False},
        "medium": {"depth": "standard", "useKnowledgeBase": True, "requireIteration": True},
        "high": {"depth": "full", "useKnowledgeBase": True, "requireIteration": True},
    }[level]

    estimated = {
        "objects": max(scores["objects"], 1 if hits else 0),
        "fields": max(scores["field"] * 2, scores["objects"] * 3 if scores["objects"] else 0),
    }
    return {"level": level, "scores": scores, "strategy": strategy, "estimated": estimated}
