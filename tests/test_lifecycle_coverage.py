"""L3 生命周期图的覆盖面与语义门禁（离线、纯函数）。

对应 docs/INSIGHT-SPEC.md §2.3 的三条缺口——它们直接影响用户最关心的那张四列表
（生命周期状态名称 / 进入动作 / 用户动作 / 进入条件）的准确性：

1. **同名按钮跨状态错配**：`_consume_match` 的 docstring 写「同状态内」，代码却遍历**全部**状态
   的动作栏行 → 不同状态间的同名按钮互相错配。
2. **进入动作被误记为用户动作**：`executeType=2`（进入状态时自动执行）的动作若经动作栏解析出
   「修改状态」，会被塞进 `userActionEdges` → 报告的「进入动作」列**缺项**、「用户动作」列**多项**。
3. **工作流触发状态未使用**：工作流行上的 `lifecycleStatusIds` 直接给出触发状态（上游实测
   195/707 条携带），移植版完全没用 → 只靠按钮发现工作流，覆盖面窄于上游。
"""

from __future__ import annotations

from workbench.services.egmp.insight.flowgraph import build_lifecycle_graph

STATUS_A = {"id": "sa", "code": "draft__c", "name": "草稿"}
STATUS_B = {"id": "sb", "code": "review__c", "name": "待审批"}
STATUS_C = {"id": "sc", "code": "done__c", "name": "已完成"}
STATUSES = [STATUS_A, STATUS_B, STATUS_C]


def _action(aid: str, name: str, execute_type: int, description: str = "") -> dict:
    return {"id": aid, "actions": name, "name": name,
            "executeType": execute_type, "description": description}


def _bar(aid: str, name: str, behavior: int, target_id: str, target_name: str = "") -> dict:
    return {"id": aid, "name": name, "behaviorType": behavior,
            "targetId": target_id, "targetName": target_name}


def _build(actions_by_status, bars, workflows=None, graphs=None):
    return build_lifecycle_graph(
        STATUSES, actions_by_status, bars, workflows or {}, graphs or {},
    )


# ---------------------------------------------------------------- 缺口 1：同状态限定


def test_duplicate_button_name_across_statuses_is_not_cross_matched() -> None:
    """两个状态各有一个同名「提交」按钮，指向不同目标——必须各自配对，不得互相借用。"""
    actions = {
        "sa": [_action("a1", "提交", 1)],
        "sb": [_action("b1", "提交", 1)],
    }
    # 只有 sb 的按钮在动作栏里能解析出行为；sa 的那条应当**配不上**，而不是借用 sb 的
    bars = {"b1": _bar("b1", "提交", 9, "sc", "已完成")}
    result = _build(actions, bars)
    edges = result["userActionEdges"]
    assert len(edges) == 1, f"同名按钮被跨状态错配：{edges}"
    assert edges[0]["source"] == "sb"
    assert edges[0]["target"] == "sc"


def test_consumed_bar_row_is_not_reused_within_status() -> None:
    """同一状态内两条同名动作对一行动作栏：消耗式匹配只能命中一次。"""
    actions = {"sa": [_action("a1", "提交", 1), _action("a2", "提交", 1)]}
    bars = {"x1": _bar("x1", "提交", 9, "sb", "待审批")}
    result = _build(actions, bars)
    assert len(result["userActionEdges"]) == 1, "同一行动作栏被重复使用"
    assert len(result["unmatchedButtons"]) == 1, "未匹配的那条应被记为 unmatched"


# ---------------------------------------------------------------- 缺口 2：进入动作归类


def test_enter_action_with_bar_row_lands_in_enter_not_user() -> None:
    """executeType=2（进入状态自动执行）必须进「进入动作」，不能混进「用户动作」。"""
    actions = {"sa": [_action("a1", "自动流转", 2)]}
    bars = {"a1": _bar("a1", "自动流转", 9, "sb", "待审批")}
    result = _build(actions, bars)
    assert result["userActionEdges"] == [], "进入动作被误记为用户动作"
    assert len(result["enterActionEdges"]) == 1
    edge = result["enterActionEdges"][0]
    assert edge["source"] == "sa" and edge["target"] == "sb"
    assert edge.get("mechanism") == "enterAction"


def test_user_action_with_bar_row_still_lands_in_user() -> None:
    """对照组：executeType=1（用户按钮）仍必须进「用户动作」。"""
    actions = {"sa": [_action("a1", "提交", 1)]}
    bars = {"a1": _bar("a1", "提交", 9, "sb", "待审批")}
    result = _build(actions, bars)
    assert len(result["userActionEdges"]) == 1
    assert result["enterActionEdges"] == []


def test_enter_action_launching_workflow_is_marked() -> None:
    """进入动作里「发起工作流」这条边现在也必须被识别（原来只认描述正则）。"""
    actions = {"sa": [_action("a1", "自动送审", 2)]}
    bars = {"a1": _bar("a1", "自动送审", 7, "wf-1")}
    workflows = {"wf-1": {"id": "wf-1", "name": "培训审批流", "workflowBasicId": "wf-1"}}
    result = _build(actions, bars, workflows)
    assert len(result["launchEdges"]) == 1
    assert result["launchEdges"][0]["mechanism"] == "enterAction"
    assert any(w.get("name") == "培训审批流" for w in result["involvedWorkflows"])


def test_user_action_launching_workflow_is_marked_button() -> None:
    actions = {"sa": [_action("a1", "提交审批", 1)]}
    bars = {"a1": _bar("a1", "提交审批", 7, "wf-1")}
    workflows = {"wf-1": {"id": "wf-1", "name": "培训审批流", "workflowBasicId": "wf-1"}}
    result = _build(actions, bars, workflows)
    assert result["launchEdges"][0]["mechanism"] == "button"


def test_enter_condition_still_comes_from_description() -> None:
    """进入条件仍取 description（平台无结构化读接口）——不得因归类改动而丢失。"""
    actions = {"sa": [_action("a1", "自动流转", 2, "修改状态为[已完成]")]}
    result = _build(actions, {})
    assert len(result["enterActionEdges"]) == 1
    assert result["enterActionEdges"][0]["targetName"] == "已完成"
    assert result["enterActionEdges"][0]["evidence"] == "description"
    # 报告侧取 description 的那一列仍能拿到原文
    assert result["states"][0]["actions"][0]["description"] == "修改状态为[已完成]"


# ---------------------------------------------------------------- 缺口 3：lifecycleStatusIds


def test_workflow_trigger_statuses_come_from_lifecycle_status_ids() -> None:
    """工作流行带 lifecycleStatusIds 时，必须据此产出触发关系（上游口径）。"""
    workflows = {
        "wf-1": {"id": "wf-1", "name": "培训审批流", "workflowBasicId": "wf-1",
                 "lifecycleStatusIds": ["sa", "sb"]},
    }
    result = _build({}, {}, workflows)
    triggers = result["workflowTriggerEdges"]
    assert {t["statusId"] for t in triggers} == {"sa", "sb"}
    assert {t["statusName"] for t in triggers} == {"草稿", "待审批"}
    assert all(t["workflowName"] == "培训审批流" for t in triggers)
    assert all(t["mechanism"] == "lifecycleStatusIds" for t in triggers)


def test_workflow_without_lifecycle_status_ids_yields_no_trigger_edges() -> None:
    workflows = {"wf-1": {"id": "wf-1", "name": "无绑定流程"}}
    result = _build({}, {}, workflows)
    assert result["workflowTriggerEdges"] == []


def test_trigger_edges_ignore_unknown_status_ids() -> None:
    """lifecycleStatusIds 里可能有已删除/不可见的状态 id——不得产出悬空触发。"""
    workflows = {"wf-1": {"id": "wf-1", "name": "X", "lifecycleStatusIds": ["sa", "ghost"]}}
    result = _build({}, {}, workflows)
    assert {t["statusId"] for t in result["workflowTriggerEdges"]} == {"sa"}


def test_button_and_lifecycle_status_channels_are_unioned() -> None:
    """两条通道取并集：按钮发现的 + lifecycleStatusIds 声明的，都要在。"""
    actions = {"sb": [_action("b1", "提交", 1)]}
    bars = {"b1": _bar("b1", "提交", 7, "wf-1")}
    workflows = {"wf-1": {"id": "wf-1", "name": "培训审批流", "workflowBasicId": "wf-1",
                          "lifecycleStatusIds": ["sa"]}}
    result = _build(actions, bars, workflows)
    assert [e["source"] for e in result["launchEdges"]] == ["sb"], "按钮通道"
    assert [t["statusId"] for t in result["workflowTriggerEdges"]] == ["sa"], "绑定通道"


# ---------------------------------------------------------------- 稳健性


def test_empty_inputs_do_not_crash() -> None:
    result = _build({}, {})
    assert result["states"] and result["userActionEdges"] == []
    assert result["workflowTriggerEdges"] == []


def test_states_carry_their_actions() -> None:
    actions = {"sa": [_action("a1", "提交", 1), _action("a2", "自动", 2, "修改状态为[已完成]")]}
    result = _build(actions, {})
    state = next(s for s in result["states"] if s["id"] == "sa")
    assert len(state["actions"]) == 2
    assert {a["executeType"] for a in state["actions"]} == {1, 2}
