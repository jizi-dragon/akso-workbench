"""蜘蛛计划五步织网（akso-cc spider/step1-5 的 Python 化）。

step5 图模型用 networkx 分析（9/4 调研认定的最大潜在收益在此兑现）：
节点=对象/状态，边=归因边（按钮/进入动作/工作流步骤）+ 关联边；
来源未知/目标未匹配的边挂对象节点，绝不猜测。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx

from ..auth import is_complete_status, is_init_status, is_reference_field
from ..client import ApiError
from ..kernel import endpoints
from ..kernel.semantics import (
    BEHAVIOR_LAUNCH_WORKFLOW,
    BEHAVIOR_UPDATE_FIELD,
    BEHAVIOR_UPDATE_STATUS,
    EXECUTE_ENTER_ACTION,
    EXECUTE_USER_ACTION,
    STEP_TYPE_NAMES,
)
from . import drawio as drawio_mod
from .lifecycle import LifecycleApi
from .report import _table  # noqa: PLC2701 —— 同包渲染工具

# 签名语义对象（电子签名类对象不参与织网的关系推导）。
# 原定义在 crawler.py，随 inventory/understand 下线一并迁到这里——它现在的唯一使用者是本模块。
SIGNATURE_RE = re.compile(r"签名|sign|esign", re.IGNORECASE)

#: **容忍**的读失败：网络/HTTP 状态/平台信封 code≠0/本地 IO。这类失败记进 `failures`
#: 并让覆盖度降级，但整次织网继续（读不到一个端点不该让产物全废）。
#:
#: ⚠ 刻意**不**容忍回放台架的缺口异常（`ReplayMiss` / `ReplayRecordedError`，都是 RuntimeError）：
#: 那是夹具覆盖度问题、或是我们自己的 bug，必须炸穿——否则「夹具少一条交互」会伪装成
#: 「平台没配」。真机上同一处的失败是 httpx/ApiError，走容忍分支。
READ_FAILURES = (httpx.HTTPError, ApiError, OSError)


class SpiderReadErrors(RuntimeError):
    """有**目标对象**读不到。

    不能吞掉：`get_object` 拿到空账（对象不存在、无权限、令牌失效）时，流程会一路走完并产出
    一个「对象节点 + 0 状态」的空网，落到 manifest 里还是 `complete` —— 那是谎报。
    用异常把「这次没读到」顶到调用方，任务状态与产物才不会互相矛盾。

    只对**目标对象**（用户点名要看的那些）生效：相关对象读不到是正常的（部分对象本就在
    织网范围外），那种失败记进 ``failures`` 即可，不该炸掉整次运行。
    """

    def __init__(self, codes: list[str]) -> None:
        self.codes = codes
        super().__init__(f"目标对象读不到实体：{', '.join(codes)}")


# ---------------------------------------------------------------- 第 1 步

def reference_target_of(field: dict[str, Any]) -> dict[str, str]:
    """引用型字段 → 目标对象标识（**唯一解析点**）。

    实证（2026-09-14 真机 `change_plan__c` + 本仓夹具 `spider-training-mgr.json`）：
    平台的 FieldPage 里 **`referenceObjectCode` 恒为 null**，目标对象在

    - `referenceObjectId`（GUID，一定有）；
    - `objectRelation.objectName` / `objectRelation.objectId`（有名字，`objectCode` 也为 null）。

    历史缺陷：`collect_relations` 只读 `referenceObjectCode`，于是**任何对象都推不出关系边**
    （夹具里那条 `dataType=15` 的引用字段同样被丢掉，重放测试的黄金值还把
    `relationEdges: 0` 固化了下来）。现在按「编码 → 名称 → id」取第一个可用的作为标识，
    三种来源都缺才返回空。
    """
    relation = field.get("objectRelation") or {}
    code = str(field.get("referenceObjectCode") or relation.get("objectCode") or "").strip()
    name = str(field.get("referenceObjectName") or relation.get("objectName") or "").strip()
    object_id = str(field.get("referenceObjectId") or relation.get("objectId") or "").strip()
    key = code or name or object_id
    if not key:
        return {}
    return {"code": code, "name": name, "id": object_id, "key": key}


def collect_relations(fields: list[dict[str, Any]], self_code: str,
                      self_object_id: str = "") -> dict[str, Any]:
    """目标对象 + 相关对象信息表（签名语义对象排除）。

    标识口径（用户定稿 2026-09-14）：**以对象名称作节点标识**——因为平台对这类目标
    常常只给 id + 名称（`objectCode` 为 null），名称是唯一稳定可读的键。
    """
    targets: dict[str, dict[str, Any]] = {}
    excluded: list[dict[str, str]] = []
    unexpandable: list[dict[str, str]] = []
    for field in fields:
        if not is_reference_field(int(field.get("dataType") or 0)):
            continue
        target = reference_target_of(field)
        if not target:
            continue
        via = str(field.get("name") or field.get("code") or "")
        is_self = bool(
            (self_code and target["key"] == self_code)
            or (self_object_id and target["id"] == self_object_id)
        )
        if is_self:
            entry = targets.setdefault(self_code, {"key": self_code, "via": [], "selfLoop": True})
            entry["via"].append(f"{via}（自环）")
            continue
        joined = f"{field.get('name') or ''}{field.get('code') or ''}{target['key']}"
        if SIGNATURE_RE.search(joined):
            excluded.append({"object": target["key"], "field": str(field.get("code"))})
            continue
        entry = targets.setdefault(target["key"], {
            "key": target["key"], "code": target["code"], "name": target["name"],
            "id": target["id"], "via": [], "selfLoop": False,
            "systemField": str(field.get("code") or "").endswith("__sys"),
        })
        entry["via"].append(via)
        if not entry.get("code") and target["code"]:
            entry["code"] = target["code"]
        if not entry.get("name") and target["name"]:
            entry["name"] = target["name"]
    related = [
        entry for key, entry in sorted(targets.items()) if key != self_code
    ]
    for entry in related:
        if not entry.get("code"):
            # 只有 id/名称 → 后续无法按编码展开它的生命周期，必须显式记边界（不静默留白）
            unexpandable.append({
                "object": entry["key"],
                "id": entry.get("id") or "",
                "via": "、".join(entry["via"]),
            })
    return {
        "related": related,
        "excluded": excluded,
        "selfLoop": self_code in targets,
        "unexpandable": unexpandable,
    }


# ---------------------------------------------------------------- 第 2 步

def action_names_of(row: dict[str, Any]) -> list[str]:
    """GetList 行的动作名。

    平台两种形态（都实测过）：`actions` 是**字符串列表**（真机：`["提交申请"]`），
    夹具里是**单个字符串**（`"提交"`）；另有 `actionList[{name}]` 兜底。
    """
    names: list[str] = []
    raw = row.get("actions")
    if isinstance(raw, str) and raw.strip():
        names.append(raw.strip())
    elif isinstance(raw, (list, tuple)):
        names.extend(str(x).strip() for x in raw if str(x).strip())
    for item in row.get("actionList") or []:
        if isinstance(item, dict) and str(item.get("name") or "").strip():
            names.append(str(item["name"]).strip())
    if not names and str(row.get("name") or "").strip():
        names.append(str(row["name"]).strip())
    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return seen


def _execute_type_of(row: dict[str, Any]) -> int:
    """执行类型：1=用户触发（按钮）/ 2=进入状态自动执行。缺省按用户触发。"""
    try:
        return int(row.get("executeType") or EXECUTE_USER_ACTION)
    except (TypeError, ValueError):
        return EXECUTE_USER_ACTION


def _action_annotation(bar: dict[str, Any]) -> str:
    """按 behaviorType 给动作名加注（不改变它归属哪一列）。"""
    behavior = bar.get("behaviorType")
    if behavior == BEHAVIOR_UPDATE_STATUS:
        return f"→{bar.get('targetName') or bar.get('targetId')}"
    if behavior == BEHAVIOR_LAUNCH_WORKFLOW:
        return "（发起工作流）"
    if behavior == BEHAVIOR_UPDATE_FIELD:
        return "（更新字段值）"
    return ""


def compose_status_cells(status_rows: list[dict[str, Any]],
                         bar_rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """状态行 + 动作栏 → 进入条件/进入动作/用户动作 三格。

    **归类按 `executeType` 走**（规格 §2.3 的口径）：1=用户动作、2=进入动作。
    历史缺陷：这里是按动作栏的 `behaviorType`（9/7 → 用户动作、其余 → 进入动作）归类的——
    于是一个 `behaviorType=5`（更新字段值）的用户按钮会被错记成「进入动作」，
    而没有动作栏行的用户动作也会掉进「进入动作」，四列表两列同时出错。
    `behaviorType` 现在只用于给名字**加注**（→目标状态 / （发起工作流））。
    """
    by_id = {str(r.get("id")): r for r in bar_rows}
    cells = []
    for row in status_rows:
        user_actions, enter_actions, conditions = [], [], []
        names = action_names_of(row)
        if not names:
            names = [str(a) for a in row.get("ids", []) if a]
        execute_type = _execute_type_of(row)
        for index, name in enumerate(names):
            action_id = str((row.get("ids") or [""])[index]) if index < len(row.get("ids") or []) else ""
            bar = by_id.get(action_id) or {}
            label = f"{name}{_action_annotation(bar)}"
            (enter_actions if execute_type == EXECUTE_ENTER_ACTION else user_actions).append(label)
        description = str(row.get("description") or "")
        if description:
            conditions.append(description)
        cells.append({
            "statusName": str(row.get("name") or ""),
            "enterCondition": "；".join(conditions) or "—",
            "enterAction": "、".join(enter_actions) or "—",
            "userAction": "、".join(user_actions) or "—",
        })
    return cells


# ---------------------------------------------------------------- 第 3 步

def attribute_status_changes(status_rows: list[dict[str, Any]],
                             bar_rows: list[dict[str, Any]],
                             workflows: list[dict[str, Any]],
                             status_details: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """状态变化归因：button / enterAction / workflowStep 三类证据链（+ workflowCancel 备注）。

    归类同样按 `executeType`（1=用户动作 → button/workflowStep；2=进入动作 → enterAction），
    `behaviorType` 只决定「改状态」还是「发起工作流」。行里额外带 `trigger` 与 `targetId`：
    前者进表格（让人一眼看出是按钮还是自动执行），后者供第 4 步按 workflowBasicId 取流程图。
    """
    target_re = re.compile(r"修改状态为[［\[]([^\]］]+)")
    rows: list[dict[str, Any]] = []
    unverified_notes: list[str] = []
    workflow_by_basic_id = {str(w.get("workflowBasicId")): w for w in workflows if w.get("workflowBasicId")}
    bars_by_id = {str(b.get("id")): b for b in bar_rows}

    for row in status_rows:
        status_name = str(row.get("name") or "")
        execute_type = _execute_type_of(row)
        trigger = "enter" if execute_type == EXECUTE_ENTER_ACTION else "user"
        names = action_names_of(row)
        ids = [str(a) for a in row.get("ids", []) if a]
        for index, action_id in enumerate(ids):
            bar = bars_by_id.get(action_id) or {}
            name = str(bar.get("name") or (names[index] if index < len(names) else "") or action_id)
            behavior = bar.get("behaviorType")
            if behavior == BEHAVIOR_UPDATE_STATUS:
                rows.append({"source": status_name, "carrier": name, "trigger": trigger,
                             "mechanism": "enterAction" if trigger == "enter" else "button",
                             "from": status_name,
                             "to": str(bar.get("targetName") or bar.get("targetId")),
                             "targetId": str(bar.get("targetId") or ""),
                             "evidence": f"GetActionBar behaviorType=9（executeType={execute_type}）"})
            elif behavior == BEHAVIOR_LAUNCH_WORKFLOW:
                workflow = workflow_by_basic_id.get(str(bar.get("targetId"))) or {}
                rows.append({"source": status_name, "carrier": name, "trigger": trigger,
                             "mechanism": "workflowStep",
                             "from": status_name,
                             "to": f"工作流「{workflow.get('name') or bar.get('targetId')}」",
                             "targetId": str(bar.get("targetId") or ""),
                             "workflowRowId": str(workflow.get("id") or ""),
                             "evidence": f"GetActionBar behaviorType=7 发起（executeType={execute_type}）"})
            elif behavior == BEHAVIOR_UPDATE_FIELD:
                # 更新字段值不改状态 → 不作状态边，但要在报告里可见（不静默丢）
                unverified_notes.append(
                    f"{status_name}：动作「{name}」为更新字段值（behaviorType=5），不构成状态迁移边"
                )
        description = str(row.get("description") or "")
        match = target_re.search(description)
        if match:
            rows.append({"source": status_name, "carrier": "进入动作", "trigger": "enter",
                         "mechanism": "enterAction", "from": status_name, "to": match.group(1),
                         "targetId": "",
                         "evidence": f"description 正则：{description[:60]}"})
        detail = status_details.get(str(row.get("id"))) or {}
        cancel_id = detail.get("workflowCancelStatus")
        if cancel_id:
            unverified_notes.append(
                f"{status_name}：workflowCancelStatus={cancel_id} 为平台全局配置 id，不作边（实证结论）"
            )
    return {"rows": rows, "unverifiedNotes": unverified_notes}


# ---------------------------------------------------------------- 第 4 步

def step_type_name(step_type: Any) -> str:
    """stepType → 中文名（词典缺失时如实回显原始值，不静默留空）。"""
    try:
        return STEP_TYPE_NAMES.get(int(step_type), f"stepType={step_type}")
    except (TypeError, ValueError):
        return f"stepType={step_type}"


def build_workflow_graph(steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
    """工作流步骤行 → drawio 图模型（节点=步骤，边=nextStepList）。

    平台契约（2026-09-14 真机 `the_change_lh__c` 实测）：``GetWorkflowSteps`` 每行带
    ``id/name/stepType/sort/nextStepId/nextStepList[{id,name}]`` —— **分支就在 nextStepList**
    （判断节点会有多条），所以流程图只靠这一次调用就能画完整。
    逐步骤的 ``GetWorkflowStepWithDetails``（审批人/字段/会签或签）属后续增强，
    本版**不调用**：保持请求面最小，也让离线夹具的覆盖度可控。
    """
    nodes = [
        {
            "id": str(step.get("id")),
            "name": str(step.get("name") or ""),
            "stepType": step.get("stepType"),
            "behaviors": [{"summary": step_type_name(step.get("stepType"))}],
        }
        for step in steps if step.get("id")
    ]
    known = {node["id"] for node in nodes}
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for step in steps:
        source = str(step.get("id") or "")
        if source not in known:
            continue
        targets = [str(item.get("id")) for item in (step.get("nextStepList") or []) if item.get("id")]
        if not targets and step.get("nextStepId"):
            targets = [str(step["nextStepId"])]
        for target in targets:
            if target in known and (source, target) not in seen:
                seen.add((source, target))
                edges.append({"source": source, "target": target, "label": ""})
    return {"name": name or "未命名工作流", "nodes": nodes, "edges": edges,
            "stepCount": len(nodes)}


# ---------------------------------------------------------------- 第 5 步

def build_network_model(objects: list[dict[str, Any]], attributions: list[dict[str, Any]],
                        relations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """对象节点 + 状态节点 + 归因边 + 关联边（未知来源/目标挂对象节点）。

    关联边的目标节点按 **编码 → 名称 → 标识** 依次命中已有对象节点；都命中不到时
    建一个 `stub` 占位节点（平台对这种目标常常只给 id + 名称），并记进
    `unexpandedRelations` —— 边界外显，不静默丢边。
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    by_code: dict[str, str] = {}
    by_label: dict[str, str] = {}
    unexpanded: list[dict[str, Any]] = []

    for obj in objects:
        code = obj["code"]
        name = str(obj.get("name") or code)
        node_id = f"obj:{code}"
        nodes.append({"id": node_id, "kind": "object", "label": name, "code": code})
        by_code.setdefault(code, node_id)
        by_label.setdefault(name, node_id)
        for status in obj.get("statuses") or []:
            status_name = str(status.get("name") or status.get("code"))
            kind = "init" if is_init_status(str(status.get("code") or "")) else (
                "complete" if is_complete_status(str(status.get("code") or "")) else "state")
            nodes.append({"id": f"st:{code}:{status.get('id')}", "kind": kind, "label": status_name,
                          "object": code})

    for obj in objects:
        code = obj["code"]
        source_id = f"obj:{code}"
        for related in (relations.get(code, {}).get("related") or []):
            key = str(related.get("key") or related.get("code") or related.get("name") or "")
            label = str(related.get("name") or related.get("code") or key)
            target_id = (
                by_code.get(str(related.get("code") or ""))
                or by_label.get(str(related.get("name") or ""))
                or by_code.get(key)
                or by_label.get(key)
            )
            if target_id is None:
                target_id = f"obj:{key}"
                if not any(node["id"] == target_id for node in nodes):
                    nodes.append({"id": target_id, "kind": "object", "label": label,
                                  "code": str(related.get("code") or ""), "stub": True})
                    by_label.setdefault(label, target_id)
                unexpanded.append({"id": target_id, "label": label,
                                   "via": "、".join(related.get("via") or [])})
            edges.append({"kind": "relation", "source": source_id, "target": target_id,
                          "label": "、".join(related.get("via") or [])})

    for attr in attributions:
        source_status = attr.get("from")
        target = str(attr.get("to") or "")
        source_node = _find_status_node(nodes, source_status)
        # 目标解析三级：状态节点 → 已爬对象节点 → 兜底挂到「来源状态所属对象」。
        # 修复前的写法 `_find(...) or f"obj:{target}" if target else None` 因运算符优先级
        # 解析为 `(_find(...) or f"obj:{target}") if target else None`，会为工作流类目标
        # （`to` = 工作流名）造出 `obj:工作流「X」` 这种**永不存在的节点 id** → drawio 悬空引用。
        target_node = _find_status_node(nodes, target) if target else None
        if target_node is None and target:
            candidate = f"obj:{target}"
            if any(n["id"] == candidate for n in nodes):
                target_node = candidate
        if target_node is None:
            target_node = _object_anchor_of(nodes, source_node)
        edge: dict[str, Any] = {
            "kind": f"attr:{attr.get('mechanism')}", "source": source_node,
            "target": target_node, "label": attr.get("carrier"), "evidence": attr.get("evidence"),
        }
        if source_node is None or target_node is None:
            # 两端都定不了位：显式标记 unresolved（产物里可见），drawio 侧不画悬空边
            edge["unresolved"] = True
            edge["rawTarget"] = target
        edges.append(edge)

    stats = {
        "objectNodes": sum(1 for n in nodes if n["kind"] == "object"),
        "stateNodes": sum(1 for n in nodes if n["kind"] in {"state", "init", "complete"}),
        "attrEdges": sum(1 for e in edges if e["kind"].startswith("attr:")),
        "relationEdges": sum(1 for e in edges if e["kind"] == "relation"),
    }
    model: dict[str, Any] = {"nodes": nodes, "edges": edges, "stats": stats}
    if unexpanded:
        model["unexpandedRelations"] = unexpanded
    return model


def _object_anchor_of(nodes: list[dict[str, Any]], state_node_id: Any) -> str | None:
    """状态节点 id（``st:<code>:<statusId>``）→ 其所属对象节点 id（若该对象在图中）。"""
    text = str(state_node_id or "")
    if not text.startswith("st:"):
        return None
    code = text[len("st:"):].rsplit(":", 1)[0]
    candidate = f"obj:{code}"
    return candidate if any(n["id"] == candidate for n in nodes) else None


def _find_status_node(nodes: list[dict[str, Any]], status_name: Any) -> str | None:
    for node in nodes:
        if node["kind"] in {"state", "init", "complete"} and node["label"] == str(status_name or ""):
            return str(node["id"])
    return None


def analyze_network(model: dict[str, Any]) -> dict[str, Any]:
    """networkx 分析：关键节点（度中心性）、孤立状态、最长归因链。"""
    import networkx as nx

    graph = nx.DiGraph()
    for node in model["nodes"]:
        graph.add_node(node["id"], **{k: v for k, v in node.items() if k != "id"})
    for edge in model["edges"]:
        if graph.has_node(edge["source"]) and graph.has_node(edge["target"]):
            graph.add_edge(edge["source"], edge["target"], kind=edge["kind"], label=edge.get("label"))
    centrality = nx.degree_centrality(graph)
    top = sorted(centrality.items(), key=lambda kv: kv[1], reverse=True)[:10]
    label_of = {n["id"]: n["label"] for n in model["nodes"]}
    orphans = [node["id"] for node in model["nodes"]
               if graph.degree(node["id"]) == 0]
    return {
        "hubs": [{"id": node_id, "label": label_of.get(node_id, node_id), "centrality": round(score, 3)}
                 for node_id, score in top],
        "orphans": [label_of.get(node_id, node_id) for node_id in orphans],
        "isDag": nx.is_directed_acyclic_graph(graph),
    }


# ---------------------------------------------------------------- 编排

def run_spider(client: Any, codes: list[str]) -> dict[str, Any]:
    """五步织网（单次调用跑完 codes 的 step1-5 模型，产物由调用方渲染落盘）。

    目标对象读不到实体即抛 :class:`SpiderReadErrors`——宁可这次没有产物，也不要一张假网
    （详见该类文档）。

    **读失败一律进 `failures`（带 stage）**，不再静默换成空列表：否则「平台没配」与
    「我们没读到」在产物里长得一模一样（都是空表），而 coverage 还写着 complete ——
    2026-09-14 实测就是这种形态：`except Exception: rows = []` 把私有端点的失败全吞了。
    `notes` 记的是**能力边界**（未启用生命周期、平台没给编码所以没展开、对象本来就没配动作），
    与 `failures`（我们没读到）分开，两者都进产物。
    """
    api = LifecycleApi(client)
    result: dict[str, Any] = {"objects": [], "perObject": {}, "failures": [], "notes": []}
    crawled: list[dict[str, Any]] = []
    all_attrs: list[dict[str, Any]] = []
    unreadable: list[str] = []

    def fail(code: str, stage: str, exc: Any) -> None:
        result["failures"].append({"code": code, "stage": stage, "error": str(exc)[:300]})

    def note(text: str) -> None:
        if text not in result["notes"]:
            result["notes"].append(text)

    for code in codes:
        meta = client.get_object(code) or {}
        if not meta.get("id"):
            unreadable.append(code)
            result["failures"].append({"code": code, "stage": "step1", "error": "对象实体未读到"})
            continue
        object_id = str(meta.get("id"))
        try:
            fields = client.paged_post(endpoints.FIELD_PAGE, {"objectId": object_id})
        except READ_FAILURES as exc:  # 读失败要留痕，不能换空列表了事
            fail(code, "step1-fields", exc)
            fields = []
        relations = collect_relations(fields, code, object_id)
        if not fields:
            note(f"{code}：字段页为空或未读到 → 关系网（关联对象）必然为空")

        # 相关对象全量信息（含生命周期）：**只有拿到编码才展开**，只有 id/名称的记边界
        scoped: list[dict[str, Any]] = [{"code": code, "name": meta.get("name"), "meta": meta,
                                         "fields": fields, "statuses": []}]
        for related in relations["related"]:
            related_code = str(related.get("code") or "")
            if not related_code:
                note(f"{code}：关联对象「{related.get('name') or related.get('key')}」"
                     "平台未给出对象编码 → 只作占位节点，不展开其字段与状态")
                continue
            try:
                rmeta = client.get_object(related_code) or {}
                rfields = (client.paged_post(endpoints.FIELD_PAGE,
                                             {"objectId": str(rmeta.get("id") or "")})
                           if rmeta.get("id") else [])
                rstatuses = (client.get_object_statuses(related_code)
                             if rmeta.get("enableLifeCycle") else [])
            except READ_FAILURES as exc:  # 相关对象读不到不应炸整次织网
                fail(related_code, "step1-related", exc)
                continue
            if not rmeta.get("id"):
                fail(related_code, "step1-related", "对象实体未读到")
                continue
            scoped.append({"code": related_code, "name": rmeta.get("name"), "meta": rmeta,
                           "fields": rfields, "statuses": rstatuses})

        if meta.get("enableLifeCycle"):
            try:
                scoped[0]["statuses"] = client.get_object_statuses(code) or []
            except READ_FAILURES as exc:  # 状态表读不到：仍然出网，但要记下来
                fail(code, "step1-statuses", exc)
            if not scoped[0]["statuses"]:
                note(f"{code}：生命周期状态读不到或为空 → 第 2/3 步无内容可织")
        else:
            note(f"{code}：未启用生命周期（enableLifeCycle=false）→ 无状态与流转可织")

        # step2/3：目标+相关对象状态表与归因
        try:
            workflows = client.list_workflows() or []
        except READ_FAILURES as exc:
            fail(code, "step4-workflowList", exc)
            workflows = []
        for entry in scoped:
            status_rows: list[dict[str, Any]] = []
            bar_rows: list[dict[str, Any]] = []
            status_details: dict[str, dict[str, Any]] = {}
            for status in entry["statuses"]:
                status_id = str(status.get("id"))
                try:
                    rows = api.user_actions(status_id)
                except READ_FAILURES as exc:  # 逐状态的读失败要能定位到状态
                    fail(entry["code"], f"step2-userActions:{status.get('name')}", exc)
                    rows = []
                status_rows.append({
                    "id": status.get("id"), "name": status.get("name"),
                    "ids": [r.get("id") for r in rows],
                    "description": "；".join(str(r.get("description") or "") for r in rows),
                    # 原始 GetList 行：归类要按**每行自己的 executeType**（规格 §2.3）
                    "rows": rows,
                })
                if not rows:
                    note(f"{entry['code']}/{status.get('name')}：平台未配置用户动作（GetList 空）")
                for row in rows:
                    try:
                        bar_rows.extend(api.action_bar([str(row.get("id"))]))
                    except READ_FAILURES as exc:
                        fail(entry["code"], f"step3-actionBar:{status.get('name')}", exc)
                try:
                    status_details[str(status.get("id"))] = api.status_detail(status_id)
                except READ_FAILURES as exc:
                    fail(entry["code"], f"step3-statusDetail:{status.get('name')}", exc)
            cells = compose_status_cells(status_rows, bar_rows)
            attrs = attribute_status_changes(status_rows, bar_rows, workflows, status_details)
            all_attrs.extend(attrs["rows"])
            result["perObject"][entry["code"]] = {
                "step1": relations if entry["code"] == code else {"related": [], "excluded": []},
                "step2Cells": cells,
                "step3": attrs,
                "statuses": entry["statuses"],
            }
        result["objects"].append({"code": code, "name": meta.get("name"),
                                  "statuses": scoped[0]["statuses"]})
        crawled.extend(scoped)

    relations_map = {obj["code"]: collect_relations(obj.get("fields") or [], obj["code"],
                                                   str((obj.get("meta") or {}).get("id") or ""))
                     for obj in crawled}
    model = build_network_model(crawled, all_attrs, relations_map)

    # step4：动作里「发起工作流」取流程图；另补「由本对象状态触发」的工作流
    graphs = collect_workflow_graphs(client, api, crawled, all_attrs, fail, note)
    result["workflowGraphs"] = graphs
    model["analysis"] = analyze_network(model)
    result["model"] = model
    if unreadable:
        raise SpiderReadErrors(unreadable)
    return result


def collect_workflow_graphs(client: Any, api: LifecycleApi, crawled: list[dict[str, Any]],
                            attrs: list[dict[str, Any]], fail: Any, note: Any) -> list[dict[str, Any]]:
    """第 4 步的取数：动作发起的工作流 + 状态触发的工作流 → 步骤图。

    两种来源都要（规格 §2.3 的并集口径）：动作里的 `behaviorType=7` 只是其中一条通道，
    另有工作流行自带 `lifecycleStatusIds`（由状态触发）。
    """
    try:
        workflows = client.list_workflows() or []
    except READ_FAILURES as exc:
        fail(",".join(obj["code"] for obj in crawled), "step4-workflowList", exc)
        return []
    by_row_id = {str(w.get("id")): w for w in workflows if w.get("id")}
    by_basic_id = {str(w.get("workflowBasicId")): w for w in workflows if w.get("workflowBasicId")}

    wanted: dict[str, str] = {}  # 工作流行 id → 触发来源说明
    for attr in attrs:
        if attr.get("mechanism") != "workflowStep":
            continue
        row = by_row_id.get(str(attr.get("workflowRowId") or "")) or \
            by_basic_id.get(str(attr.get("targetId") or ""))
        if row and row.get("id"):
            wanted.setdefault(str(row["id"]), f"动作「{attr.get('carrier')}」发起")
    crawled_codes = {obj["code"] for obj in crawled}
    status_ids = {str(s.get("id")) for obj in crawled for s in (obj.get("statuses") or [])}
    for row in workflows:
        if str(row.get("objectCode") or "") not in crawled_codes:
            continue
        triggers = [str(x) for x in (row.get("lifecycleStatusIds") or []) if x]
        if triggers and status_ids.intersection(triggers) and row.get("id"):
            wanted.setdefault(str(row["id"]), "由生命周期状态触发")

    graphs: list[dict[str, Any]] = []
    for row_id, origin in wanted.items():
        row = by_row_id.get(row_id) or {}
        name = str(row.get("name") or row_id)
        try:
            steps = api.workflow_steps(row_id) or []
        except READ_FAILURES as exc:  # 单个工作流读不到不该炸整次织网
            fail(name, "step4-workflowSteps", exc)
            continue
        if not steps:
            note(f"工作流「{name}」读不到步骤 → 流程图缺这一页")
            continue
        graph = build_workflow_graph(steps, name)
        graph["origin"] = origin
        graph["workflowRowId"] = row_id
        graphs.append(graph)
    return graphs


def _fenced(text: str) -> str:
    return text if text.strip() else "—"


def _write_step1_md(spider: dict[str, Any], output_dir: Path) -> Path:
    """第 1 步产物：关联对象表（对象 → 关联对象 + 经由字段 + 是否展开）。"""
    lines = ["# 蜘蛛计划 · 第 1 步：关联对象", "",
             "> 关系边来自字段页里的引用型字段（dataType 13/15/16/23）。平台给的是"
             "`referenceObjectId` + `objectRelation.objectName`（`referenceObjectCode` 常为 null），"
             "因此**节点标识以对象名称为准**。", ""]
    any_related = False
    for code, per in (spider.get("perObject") or {}).items():
        step1 = per.get("step1") or {}
        related = step1.get("related") or []
        if not related and not (step1.get("excluded") or step1.get("unexpandable")):
            continue
        lines.append(f"## {code}")
        lines.append("")
        rows = [
            [code, entry.get("name") or entry.get("key"), "、".join(entry.get("via") or []),
             entry.get("code") or "（平台未给编码）",
             "系统字段" if entry.get("systemField") else "自定义字段"]
            for entry in related
        ]
        if rows:
            any_related = True
            lines.append(_table(["来源对象", "关联对象", "经由字段", "目标对象编码", "字段来源"], rows))
        else:
            lines.append("（无引用型字段 → 该对象在平台配置里不指向其它对象）")
        lines.append("")
        if step1.get("selfLoop"):
            lines.append("- 自环：该对象有指向**自身**的引用字段")
        for item in step1.get("excluded") or []:
            lines.append(f"- 已排除（签名语义对象，不参与关系推导）：{item.get('object')}"
                         f"（字段 {item.get('field')}）")
        for item in step1.get("unexpandable") or []:
            lines.append(f"- 未展开：{item.get('object')}（只有 id `{item.get('id')}`，"
                         "平台未给出对象编码，故未读其字段与状态）")
        lines.append("")
    if not any_related and len(lines) <= 5:
        lines.append("本次所有目标对象都没有引用型字段 → 关系网为空（这是平台事实，不是读失败）。")
        lines.append("")
    path = output_dir / "step1-relations.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_step2_md(spider: dict[str, Any], output_dir: Path) -> Path:
    """第 2 步产物：状态明细表（规格 C2 的**写死列口径**）。"""
    lines = ["# 蜘蛛计划 · 第 2 步：生命周期状态明细", "",
             "> 列口径（规格 §2.1 C2，写死为契约）：状态名称 / 进入动作 / 用户动作 / 进入条件。",
             "> 归类按 `executeType`：1=用户动作、2=进入动作（不是按 behaviorType）。", ""]
    any_cells = False
    for code, per in (spider.get("perObject") or {}).items():
        cells = per.get("step2Cells") or []
        if not cells:
            continue
        any_cells = True
        lines.append(f"## {code}")
        lines.append("")
        lines.append(_table(
            ["生命周期状态名称", "进入动作", "用户动作", "进入条件"],
            [[c.get("statusName"), c.get("enterAction"), c.get("userAction"), c.get("enterCondition")]
             for c in cells],
        ))
        lines.append("")
    if not any_cells:
        lines.append("本次没有读到任何状态行（对象未启用生命周期，或状态/动作读失败——见 manifest 的"
                     "`coverage` 与 `failures`）。")
        lines.append("")
    path = output_dir / "step2-status-table.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_step3_md(spider: dict[str, Any], output_dir: Path) -> Path:
    """第 3 步产物：状态变化归因明细（button / enterAction / workflowStep）。"""
    attr_rows = []
    notes: list[str] = []
    for per in (spider.get("perObject") or {}).values():
        for row in per["step3"]["rows"]:
            attr_rows.append([row.get("from"), row.get("carrier"),
                              "进入" if row.get("trigger") == "enter" else "用户",
                              row.get("mechanism"), row.get("to"), row.get("evidence")])
        notes.extend(per["step3"].get("unverifiedNotes") or [])
    lines = ["# 蜘蛛计划 · 第 3 步：状态变化归因", "",
             "> 每条边都带证据（按钮/进入动作/工作流发起），不猜。", ""]
    if attr_rows:
        lines.append(_table(["来源状态", "载体", "触发", "机制", "变化后", "依据"], attr_rows))
    else:
        lines.append("本次没有归因边：平台在这两个状态上都没有配用户动作/进入动作"
                     "（`UserAction/GetList` 返回空）。空的原因见 manifest 的 `coverage`/`failures`/`notes`。")
    lines.append("")
    if notes:
        lines.append("## 不入边的平台事实（如实记录）")
        lines.append("")
        lines.extend(f"- {text}" for text in notes)
        lines.append("")
    path = output_dir / "step3-attribution.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_step4_drawio(spider: dict[str, Any], output_dir: Path) -> Path | None:
    """第 4 步产物：工作流流程图（每个工作流一页，左→右分层）。"""
    graphs = spider.get("workflowGraphs") or []
    usable = [g for g in graphs if g.get("nodes")]
    if not usable:
        return None
    used: set[str] = set()
    pages: list[tuple[str, str]] = []
    for graph in usable:
        origin = str(graph.get("origin") or "")
        name = f"{graph.get('name')}·流程" + (f"（{origin}）" if origin else "")
        body = "".join(drawio_mod.workflow_cells(graph, axis="col"))
        pages.append((drawio_mod.safe_page_name(name, used), body))
    path = output_dir / "step4-workflows.drawio"
    path.write_text(drawio_mod.mxfile(pages, host="akso-workbench-spider"), encoding="utf-8")
    return path


def write_spider_artifacts(spider: dict[str, Any], output_dir: Path) -> list[Path]:
    """落盘五步产物（每步一件：step1 关联表 / step2 状态表 / step3 归因 / step4 流程图 / step5 网络）。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    model = spider["model"]

    # spider-network.json
    import json

    net_json = output_dir / "spider-network.json"
    net_json.write_text(json.dumps(
        {"meta": {"generatedFrom": "egmp-native"}, "model": {k: v for k, v in model.items() if k != "edges"} | {"edges": model["edges"]}},
        ensure_ascii=False, indent=2, default=str,
    ), encoding="utf-8")
    written.append(net_json)

    # spider-network.drawio：总览页 + 每对象状态机页
    # id 一律经 drawio_mod.cell_id() 归一（纯函数，故节点与边的引用天然对得上），
    # 且边只在**两端都在本页节点集内**时才画——否则会产生悬空引用（如 source=None、
    # 或 target 指向不存在的 obj:工作流「X」）。
    pages: list[tuple[str, str]] = []
    overview_cells: list[str] = []
    obj_nodes = [n for n in model["nodes"] if n["kind"] == "object"]
    overview_ids = {drawio_mod.cell_id(n["id"]) for n in obj_nodes}
    for idx, node in enumerate(obj_nodes):
        x, y = 40 + (idx % 3) * 290, 40 + (idx // 3) * 110
        overview_cells.append(drawio_mod.node_cell(
            drawio_mod.cell_id(node["id"]), [node["label"]],
            "rounded=1;fillColor=#dae8fc;strokeColor=#6c8ebf;" if not node.get("stub")
            else "rounded=1;dashed=1;fillColor=#f5f5f5;strokeColor=#999999;",
            x, y, 220, 70))
    used: set[str] = set()
    for edge in model["edges"]:
        if edge["kind"] != "relation":
            continue
        src, tgt = drawio_mod.cell_id(edge.get("source")), drawio_mod.cell_id(edge.get("target"))
        if src in overview_ids and tgt in overview_ids:
            overview_cells.append(drawio_mod.edge_cell(src, tgt, edge.get("label") or ""))
    for node in obj_nodes:
        code = node["code"]
        cells: list[str] = []
        obj_anchor = f"obj:{code}"
        anchors = {drawio_mod.cell_id(obj_anchor)}
        cells.append(drawio_mod.node_cell(
            drawio_mod.cell_id(obj_anchor), [node["label"]],
            "rounded=1;fillColor=#ffe6cc;strokeColor=#d79b00;", 40, 40, 180, 40))
        states = [n for n in model["nodes"] if n.get("object") == code and n["kind"] != "object"]
        state_style = {
            "init": "ellipse;fillColor=#d5e8d4;strokeColor=#82b366;",
            "complete": "ellipse;fillColor=#f8cecc;strokeColor=#b85450;",
            "state": "rounded=1;fillColor=#dae8fc;strokeColor=#6c8ebf;",
        }
        for idx, state in enumerate(states):
            cell = drawio_mod.cell_id(state["id"])
            anchors.add(cell)
            cells.append(drawio_mod.node_cell(cell, [state["label"]],
                                              state_style[state["kind"]], 320, 40 + idx * 92, 160, 36))
        for edge in model["edges"]:
            if not edge["kind"].startswith("attr:"):
                continue
            src, tgt = drawio_mod.cell_id(edge.get("source")), drawio_mod.cell_id(edge.get("target"))
            if src in anchors and tgt in anchors:
                cells.append(drawio_mod.edge_cell(src, tgt, edge.get("label") or ""))
        pages.append((drawio_mod.safe_page_name(f"{node['label']}·状态机", used), "".join(cells)))
    pages.insert(0, (drawio_mod.safe_page_name("总览·对象网络", used), "".join(overview_cells)))
    net_drawio = output_dir / "spider-network.drawio"
    net_drawio.write_text(drawio_mod.mxfile(pages, host="akso-workbench-spider"), encoding="utf-8")
    written.append(net_drawio)

    # 第 1/2/3/4 步各自的产物
    written.append(_write_step1_md(spider, output_dir))
    written.append(_write_step2_md(spider, output_dir))
    written.append(_write_step3_md(spider, output_dir))
    step4 = _write_step4_drawio(spider, output_dir)
    if step4 is not None:
        written.append(step4)

    # spider-network.md（步骤索引 + 归因统计 + networkx 分析 + 边界）
    analysis = model.get("analysis", {})
    graphs = spider.get("workflowGraphs") or []
    lines = ["# 蜘蛛计划 · 第 5 步：总体生命周期网络", ""]
    lines.append("## 步骤与产物")
    lines.append("")
    lines.append(_table(["步骤", "产物", "本次结果"], [
        ["1 关联对象", "step1-relations.md",
         f"{model['stats']['relationEdges']} 条关系边"],
        ["2 状态明细", "step2-status-table.md",
         f"{model['stats']['stateNodes']} 个状态"],
        ["3 状态变化归因", "step3-attribution.md",
         f"{model['stats']['attrEdges']} 条归因边"],
        ["4 工作流流程图", "step4-workflows.drawio" if step4 is not None else "（本次没有可画的工作流）",
         f"{len(graphs)} 个工作流"],
        ["5 网络与分析", "spider-network.json / .drawio",
         f"{model['stats']['objectNodes']} 个对象节点"],
    ]))
    lines.append("")
    lines.append("## 归因边统计")
    lines.append(_table(["指标", "数量"], [[k, v] for k, v in model["stats"].items()]))
    lines.append("")
    lines.append("## networkx 分析（Python 化收益点）")
    lines.append(f"- 有向无环：{'是' if analysis.get('isDag') else '否（存在环——归因回边）'}")
    lines.append(f"- 孤立节点：{'、'.join(map(str, analysis.get('orphans', []))) or '—'}")
    lines.append("")
    lines.append("| 枢纽节点（度中心性 Top） | 中心性 |")
    lines.append("|---|---|")
    for hub in analysis.get("hubs", []):
        lines.append(f"| {hub['label']} | {hub['centrality']} |")
    lines.append("")
    lines.append("## 边界与缺口（不允许静默留白）")
    lines.append("")
    notes = spider.get("notes") or []
    failures = spider.get("failures") or []
    unexpanded = model.get("unexpandedRelations") or []
    if notes:
        lines.extend(f"- {text}" for text in notes)
    if unexpanded:
        lines.append("- 未展开的关联对象（平台未给编码）："
                     + "、".join(str(item.get("label")) for item in unexpanded))
    if failures:
        lines.append(f"- 读失败 {len(failures)} 项（详见 manifest.json 的 failures）：")
        lines.extend(f"  - [{item.get('stage')}] {item.get('code')}：{item.get('error')}"
                     for item in failures[:20])
    if not (notes or unexpanded or failures):
        lines.append("- 本次没有记录到边界或读失败。")
    net_md = output_dir / "spider-network.md"
    net_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    written.append(net_md)
    return written
