"""对象分析覆盖度**自证**：`succeeded` 必须是实际读到的对象数，不是计划数。

旧实现写死 `succeeded=len(codes)`：即使每个端点都失败，只要 `failures` 为空（例如
spider 自己吞了异常），覆盖度仍可能显示 `complete`。这条判据把"谎报"变成红。
"""

from __future__ import annotations

import pytest

from workbench.services.egmp import insight


def test_coverage_uses_actual_objects_not_planned_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = {
        "objects": [{"code": "A"}],
        "failures": [{"code": "B", "stage": "step1", "error": "未读到"}],
        "notes": [],
        "workflowGraphs": [],
        "model": {
            "stats": {"objectNodes": 1, "stateNodes": 0, "attrEdges": 0, "relationEdges": 0},
            "unexpandedRelations": [],
        },
    }
    monkeypatch.setattr(insight, "_run_spider", lambda *_a, **_k: fake)
    result = insight.run_spider(None, ["A", "B"])  # type: ignore[arg-type]
    assert result["coverage"]["total"] == 2
    assert result["coverage"]["succeeded"] == 1, "覆盖度把没读到的对象也算成功了"
    assert result["coverage"]["status"] != "complete"


def test_all_failed_is_degraded_not_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = {
        "objects": [],
        "failures": [{"code": "A", "stage": "step1", "error": "未读到"}],
        "notes": [],
        "workflowGraphs": [],
        "model": {
            "stats": {"objectNodes": 0, "stateNodes": 0, "attrEdges": 0, "relationEdges": 0},
            "unexpandedRelations": [],
        },
    }
    monkeypatch.setattr(insight, "_run_spider", lambda *_a, **_k: fake)
    result = insight.run_spider(None, ["A"])  # type: ignore[arg-type]
    assert result["coverage"]["succeeded"] == 0
    assert result["coverage"]["status"] == "degraded"
    assert result["ok"] is False
