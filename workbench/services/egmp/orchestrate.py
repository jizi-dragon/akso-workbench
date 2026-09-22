"""编排闭环（akso-auto orchestrate.js runFullWorkflow 的 Python 化）。

管道：校验 → 环境阻断（调用方已确认）→ 预检查+审阅三件套 → 全局共享选项集 →
拓扑排序（dataType 13/15/16/23 引用关系）→ 逐对象 8 步管道（选项集→对象→字段→
表单布局→列表布局→生命周期→工作流→验证）+ checkpoint → 汇总 manualItems。

断点协议（对齐上游 options.checkpoint）：{steps: [{step, status}]}；
成功对象的 verify 步骤存在 → 整对象跳过；幂等查询兜底（重跑天然安全）。
"""

from __future__ import annotations

import json
import re
import uuid as _uuid
from pathlib import Path
from typing import Any

from .queries import GaiaQueries, invalidate_object_cache
from .writers import blueprint as bp
from .writers import fields as fields_ops
from .writers import layouts as layout_ops
from .writers import lifecycle as lifecycle_ops
from .writers import menus as menu_ops
from .writers import objects as object_ops
from .writers import picklists as picklist_ops
from .writers import workflows as workflow_ops
from .writers.idempotency import Idempotency, with_idempotency

PIPELINE_STEPS = ["picklists", "object", "fields", "formLayout", "listLayout",
                  "lifecycle", "workflows", "verify"]


class CheckpointManager:
    """内存步骤台账 + 文件持久化（断点恢复注入）。"""

    def __init__(self, directory: Path | None = None) -> None:
        self._steps: list[dict[str, Any]] = []
        self._path = (directory / "checkpoint.json") if directory else None
        if self._path and self._path.exists():
            try:
                state = json.loads(self._path.read_text(encoding="utf-8"))
                self._steps = list(state.get("steps", []))
            except (OSError, ValueError):
                self._steps = []

    def record(self, step: str, status: str, message: str = "", meta: Any = None) -> None:
        self._steps.append({"step": step, "status": status, "message": message, "meta": meta})
        self._flush()

    def has_success(self, step: str) -> bool:
        return any(s["step"] == step and s["status"] == "success" for s in self._steps)

    def completed_steps(self) -> set[str]:
        return {s["step"] for s in self._steps if s["status"] == "success"}

    def summary(self) -> dict[str, Any]:
        failed = next((s for s in reversed(self._steps) if s["status"] == "failed"), None)
        return {
            "totalSteps": len(self._steps),
            "completedCount": sum(1 for s in self._steps if s["status"] == "success"),
            "skippedCount": sum(1 for s in self._steps if s["status"] == "skipped"),
            "failedStep": failed["step"] if failed else None,
            "failedReason": failed.get("message") if failed else None,
            "steps": self._steps,
        }

    def _flush(self) -> None:
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps({"steps": self._steps}, ensure_ascii=False, indent=2),
                                  encoding="utf-8")


def topological_sort_objects(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按引用关系拓扑排序（引用者后建）；成环保持声明序。"""
    codes = [str(obj.get("code")) for obj in objects]
    by_code = {str(obj.get("code")): obj for obj in objects}
    deps: dict[str, set[str]] = {code: set() for code in codes}
    for obj in objects:
        code = str(obj.get("code"))
        for field in obj.get("fields", []):
            ref = field.get("referenceObjectCode")
            if ref in by_code and ref != code:
                deps[code].add(str(ref))
    ordered: list[str] = []
    remaining = list(codes)
    while remaining:
        ready = [c for c in remaining if not (deps[c] - set(ordered))]
        if not ready:  # 成环：保持声明序
            ordered.extend(remaining)
            break
        for code in ready:
            ordered.append(code)
            remaining.remove(code)
    return [by_code[code] for code in ordered]


def _code_variants(code: str) -> list[str]:
    """给一个对象 code 生成"长得像"的候选（**机械可得**，按形状不按语义）。

    为什么不用"平台的对象列表接口"：2026-09-22 实测**平台没有可枚举对象的端点**——
    14 个候选路径（`BasicObject/{List,GetList,Query,Search,PageList,GetPageList,…}`）
    GET/POST 全 404，openapi 那条 `/BasicObject/list` 回空。所以"相似"只能靠：
    ① 后缀变体（`x` ↔ `x__c`）、② 下划线/大小写变体、③ **末位数字 ±1/±2**
    （实测这条最有用：`test_0904__c` 能因此发现 `test_0903__c`），然后**逐个真机探测**。
    探测数有上限（下面的 `limit`），不会把一个错误 code 放大成几十次请求。
    """
    base = re.sub(r"(__c|__ak|__sys)$", "", code or "")
    variants: list[str] = []
    for stem in (base, base.lower(), base.replace("_", "")):
        if not stem:
            continue
        for suffix in ("__c", ""):
            variants.append(f"{stem}{suffix}")
    numbers = list(re.finditer(r"\d+", base))
    if numbers:
        last = numbers[-1]
        width = len(last.group(0))
        value = int(last.group(0))
        for delta in (-1, 1, -2, 2):
            if value + delta < 0:
                continue
            swapped = f"{base[:last.start()]}{value + delta:0{width}d}{base[last.end():]}"
            variants.extend([f"{swapped}__c", swapped])
    seen: list[str] = []
    for variant in variants:
        if variant and variant != code and variant not in seen:
            seen.append(variant)
    return seen


def similar_object_codes(client: Any, code: str, limit: int = 5) -> list[dict[str, str]]:
    """平台上**确实存在**的、与 `code` 相近的对象（逐个探测，最多 `limit` 个）。"""
    queries = GaiaQueries(client)
    found: list[dict[str, str]] = []
    for variant in _code_variants(code):
        if len(found) >= limit:
            break
        try:
            info = queries.get_object_info(variant)
        except Exception:  # noqa: BLE001 —— 探测失败就当不存在（不阻断主流程）
            info = None
        if info:
            found.append({"code": variant, "name": str(info.get("name") or ""),
                          "id": str(info.get("id") or "")})
    return found


def precheck_blueprint(client: Any, raw: dict[str, Any]) -> dict[str, Any]:
    """预检查：① 平台已有名称/编码冲突（**失败不阻断，只告警**）；
    ② ★ **蓝图里的对象在平台上不存在**时，列出相近对象交人选择（2026-09-22 用户定的规则）。

    ② 的意义：用户说"在对象 X 上做点什么"时，如果 X 打错了，旧行为是**静默新建一个 X**
    —— 建出来的东西对着一个空对象，而用户以为改的是他那个。实测踩过：`test_0903__c`
    如果写成 `test_0905__c`，工厂就会**建一个新对象**，而平台上看不出异常。

    ## 显式意图位（`createIfMissing`，2026-09-22 晚加，决策卡 G1 选项①）

    本函数是**确定性**检查，**看不见对话**：用户批准过、模型转述过，都对它无效
    ⇒ 加这个键之前，"确认要新建"是一句**没有出口**的话（15:08 真机实测：用户答了"确认"、
    蓝图也校验通过，创建仍被同一个理由拒回；而本平台的测试对象是**流水号命名**，
    下一个编号必然与既有对象"相近" ⇒ 结构性误报）。

    现在：对象上写了 `createIfMissing: true` ⇒ 同一份蓝图**不再被拦**；
    相近对象仍如实记进 `overridden` 并在运行日志里留一行 —— **放行不等于装作没看见**：
    事后看得出"这次是显式新建，且当时存在哪些相近对象"。
    """
    conflicts: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    queries = GaiaQueries(client)
    idem = Idempotency(queries)
    for obj in bp.to_object_blueprints(raw):
        code = str(obj.get("code"))
        explicit_new = bool(obj.get("createIfMissing"))
        existing = idem.object_exists(code)
        if existing:
            conflicts.append({"kind": "object", "code": code,
                              "message": f"对象编码已存在（objectId={existing.get('id')}）→ 建议后缀 _v2"})
            continue
        candidates = similar_object_codes(client, code)
        if explicit_new:
            message = (f"平台上**找不到对象 {code}**" +
                       (f"；相近的有：{[c['code'] for c in candidates]}" if candidates else "") +
                       "——蓝图已显式声明 `createIfMissing: true`（用户确认新建，不是打错字）⇒ **放行新建**")
        else:
            message = (f"平台上**找不到对象 {code}**" +
                       (f"；但有几个很像的：{[c['code'] for c in candidates]}" if candidates
                        else "；也没有找到相近的") +
                       ("——**请先让用户确认**是要改上面某个，还是确实要新建；"
                        "用户确认新建后，在该对象上写 `createIfMissing: true` 再重跑"
                        if candidates else "——若确实要新建，继续即可"))
        missing.append({
            "kind": "object", "code": code, "candidates": candidates,
            "createIfMissing": explicit_new, "message": message,
        })
    phase = "review" if not conflicts and not missing else "precheck"
    blocking = [m for m in missing if m["candidates"] and not m["createIfMissing"]]
    overridden = [m["code"] for m in missing if m["candidates"] and m["createIfMissing"]]
    return {"phase": phase, "conflicts": conflicts, "missing": missing,
            "needsObjectChoice": blocking, "overridden": overridden}


def run_full_workflow(client: Any, raw: dict[str, Any], *, approved: bool = True,
                      confirmed_env: bool = True, checkpoint: dict[str, Any] | None = None,
                      artifact_dir: Path | None = None,
                      on_log: Any = None) -> dict[str, Any]:
    log = on_log or (lambda line: None)
    invalidate_object_cache()  # 每次编排从干净查询状态开始（防跨会话缓存串台）
    if not confirmed_env:
        return {"success": False, "message": "违反「环境强制确认」原则：confirmed_env 必须为 True"}
    if not approved:
        return {"success": False, "message": "蓝图未经审批（approved=False）"}

    issues = bp.validate_blueprint(raw)
    blocking = bp.has_blocking(issues)
    if blocking:
        return {"success": False, "message": "蓝图校验未通过", "issues": [i.model_dump() for i in issues]}

    cp = CheckpointManager(artifact_dir)
    if checkpoint:
        for step in checkpoint.get("steps", []):
            if step.get("status") == "success" and not cp.has_success(str(step.get("step"))):
                cp.record(str(step.get("step")), "success", "断点恢复注入")

    precheck = precheck_blueprint(client, raw)
    # ★ 用户 2026-09-22 定的规则：**"对象不存在"不许静默新建**——若平台上存在相近的对象，
    # 先停下来让用户选（是改那个相近的，还是确实要新建），本轮**一个字节都不写**。
    blocking = list(precheck.get("needsObjectChoice") or [])
    if blocking:
        lines = [f"找不到对象 {item['code']}，但平台上存在相近的："
                 + "、".join(f"{c['code']}（{c['name']}）" for c in item["candidates"])
                 for item in blocking]
        return {
            "success": False,
            "needsObjectChoice": blocking,
            "message": "预检查未通过：**本轮未写入任何东西**。" + "；".join(lines)
                       + " ⇒ 请让用户选择：① 改成上面某个对象重跑；"
                         "② 用户确认**确实要新建** ⇒ 在该对象上写 `createIfMissing: true` 再重跑"
                         "（预检看不见对话，口头确认不算数，必须落到这个键上）；"
                         "③ 或者把 code 改成不与既有对象雷同的。",
        }
    # 显式新建放行了，但**要留痕**：日志里写清"是显式新建 + 当时存在哪些相近对象"，
    # 否则事后回看只剩"跑通了"，看不出它本该被拦。结果里也带一份（`precheck.overridden`）。
    for code in list(precheck.get("overridden") or []):
        entry = next((m for m in precheck["missing"] if m["code"] == code), {})
        near = "、".join(str(c["code"]) for c in (entry.get("candidates") or []))
        log(f"⚠ {code}：平台上有相近对象（{near}），但蓝图显式声明 createIfMissing=true ⇒ 按**新建**执行")
    if artifact_dir is not None:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "spec.md").write_text(bp.generate_review_spec(raw), encoding="utf-8")
        (artifact_dir / "checklist.md").write_text(bp.generate_checklist(raw), encoding="utf-8")

    objects = topological_sort_objects(bp.to_object_blueprints(raw))
    object_results: list[dict[str, Any]] = []
    manual_items: list[str] = []

    for obj in objects:
        code = str(obj.get("code"))
        prefix = f"{code}:" if len(objects) > 1 else ""
        if cp.has_success(f"{prefix}verify"):
            object_results.append({"code": code, "skipped": True,
                                   "message": "该对象已通过验证，跳过（断点恢复）"})
            log(f"⊙ {code} 已通过验证，断点跳过")
            continue
        object_results.append(run_object_workflow(
            client, obj, cp=cp, prefix=prefix, log=log, manual_items=manual_items,
        ))

    result = {
        "success": all(r.get("success", True) for r in object_results),
        "objects": object_results,
        "manualItems": manual_items,
        "checkpoint": cp.summary(),
        "precheck": precheck,
    }
    return result


def run_object_workflow(client: Any, obj: dict[str, Any], *, cp: CheckpointManager,
                        prefix: str, log: Any, manual_items: list[str]) -> dict[str, Any]:
    """单对象 8 步管道（runObjectWorkflow）。"""
    code = str(obj.get("code"))
    name = str(obj.get("name"))
    idem = Idempotency(GaiaQueries(client))
    result: dict[str, Any] = {"code": code, "name": name, "steps": {}}
    picklist_map: dict[str, str] = {}
    status_map: dict[str, str] = {}

    def record(step: str, fn: Any) -> Any:
        """跑一步并记账。**任何异常都就地转成"这一步失败"**（2026-09-22 实测补的）。

        为什么不能让异常穿出去：那样整条管道会以 traceback 结束，
        连 `result.json` 都写不出来，而**前面几步已经写进平台**——现场只剩一个
        "看不懂跑到哪了"的半成品（2026-09-22 建「测试09-04」时实测：重跑撞
        `SaveField 字段名称重复`，异常一路穿到顶层，工具连结论都没打印）。
        转成这一步的 failed 之后：日志与 `steps` 里都看得见，运行也会照常收尾。
        """
        try:
            outcome = fn()
        except Exception as exc:  # noqa: BLE001 —— 见上：宁可记成失败，也不许半路炸掉整条管道
            outcome = {"success": False, "aborted": True,
                       "message": f"{type(exc).__name__}: {exc}"}
            log(f"✗ {step} 步骤异常：{outcome['message']}")
        status = "skipped" if outcome.get("skipped") else ("success" if outcome.get("success", True) else "failed")
        cp.record(f"{prefix}{step}", status, outcome.get("message", ""))
        result["steps"][step] = outcome
        return outcome

    # 1 选项集
    def _picklists() -> dict[str, Any]:
        for p in obj.get("picklists", []):
            outcome = with_idempotency(
                lambda p=p: idem.picklist_exists(str(p.get("code"))),
                lambda p=p: picklist_ops.create_picklist(
                    client, name=str(p.get("name")), code=str(p.get("code")),
                    options=list(p.get("options", []))),
                label=f"选项集 {p.get('name')}",
            )
            if outcome.get("existing"):
                picklist_map[str(p.get("code"))] = str(outcome["existing"].get("id") or "")
            elif outcome.get("picklistId"):
                picklist_map[str(p.get("code"))] = str(outcome["picklistId"])
        return {"success": True, "picklistMap": picklist_map}

    record("picklists", _picklists)

    # 2 对象
    def _object() -> dict[str, Any]:
        outcome = with_idempotency(
            lambda: idem.object_exists(code),
            lambda: object_ops.create_object(
                client, name=name, code=code,
                enable_life_cycle=bool(obj.get("enableLifeCycle")),
                enable_signatures=bool(obj.get("enableSignatures"))),
            label=f"对象 {name}",
        )
        existing = outcome.get("existing") or {}
        object_id = str(outcome.get("objectId") or existing.get("id") or "")
        invalidate_object_cache(code)
        return {**outcome, "objectId": object_id}

    obj_outcome = record("object", _object)
    object_id = obj_outcome.get("objectId", "")
    if not object_id:
        # 对象没拿到 id ⇒ 后面每一步都无从下手（字段/布局/生命周期都按 objectId 写）。
        # 如实收尾，而不是拿着空 id 去平台上撞一串 500（那些 500 的成因看不出是"对象没建成"）。
        result["steps"]["aborted"] = {
            "success": False, "aborted": True,
            "message": "对象步骤没有拿到 objectId ⇒ 后续步骤（字段/布局/生命周期/工作流/验证）全部跳过",
        }
        result["success"] = False
        return result

    # 3 字段（并发 5）
    def _fields() -> dict[str, Any]:
        outcome = fields_ops.batch_create_fields(
            client, object_id=object_id, fields=list(obj.get("fields", [])),
            picklist_map=picklist_map, on_log=log)
        for field_result in outcome["results"]:
            cp.record(f"{prefix}field:{field_result.get('code')}",
                      "success" if field_result.get("success") else "failed",
                      field_result.get("message", ""))
        # 字段层"表达不了"的东西（当前是：必填 isRequired，见 D-H）**升级成人工项**，
        # 而不是静默失效 —— 它会出现在编排结果与检查表里。
        manual_items.extend(outcome.get("notes") or [])
        return {**outcome, "success": all(r.get("success") for r in outcome["results"])}

    fields_outcome = record("fields", _fields)
    field_map = fields_outcome.get("fieldMap", {})

    # 4 表单布局
    if obj.get("formLayout"):
        record("formLayout", lambda: layout_ops.build_form_layout(
            client, object_id=object_id, object_code=code,
            blueprint_layout=obj["formLayout"], field_map=field_map))

    # 5 列表布局
    if obj.get("listLayout"):
        record("listLayout", lambda: layout_ops.build_list_layout(
            client, object_id=object_id, object_code=code,
            blueprint_layout=obj["listLayout"], field_map=field_map))

    # 6 生命周期
    if obj.get("enableLifeCycle"):
        def _lifecycle() -> dict[str, Any]:
            life_id = str((obj.get("meta") or {}).get("lifeCycleId") or "")
            if not life_id:
                meta = GaiaQueries(client).get_object_info(code)
                life_id = str((meta or {}).get("lifeCycleId") or "")
            outcome = lifecycle_ops.build_statuses(
                client, lifecycle_id=life_id,
                blueprint_lifecycle=obj.get("lifecycle") or {}, object_code=code)
            status_map.update(outcome.get("statusMap", {}))
            return {**outcome, "lifeCycleId": life_id}

        record("lifecycle", _lifecycle)

    # 7 工作流
    if obj.get("workflows"):
        def _workflows() -> dict[str, Any]:
            life_id = result["steps"].get("lifecycle", {}).get("lifeCycleId", "")
            built = []
            for workflow in obj.get("workflows", []):
                built.append(workflow_ops.build_workflow(
                    client, object_id=object_id, lifecycle_id=life_id, object_code=code,
                    blueprint=workflow, status_map=status_map))
            return {"success": all(b.get("success") for b in built), "built": built}

        record("workflows", _workflows)

    # 8 验证（OpenAPI 回读）
    def _verify() -> dict[str, Any]:
        invalidate_object_cache(code)
        meta = GaiaQueries(client).get_object_info(code)
        fields_count = len(GaiaQueries(client).get_fields(code))
        statuses_count = len(GaiaQueries(client).get_statuses(code)) if obj.get("enableLifeCycle") else 0
        ok = bool(meta)
        return {"success": ok, "message": f"验证{'通过' if ok else '失败'} — {fields_count} 个字段, {statuses_count} 个状态",
                "fieldCount": fields_count, "statusCount": statuses_count}

    record("verify", _verify)

    # 声明区 → manualItems
    for rule in obj.get("rules", []) or []:
        manual_items.append(f"[{code}] 规则：{rule.get('name')}")
    result["success"] = all(
        s.get("status") != "failed" for s in result["steps"].values()
    ) if result["steps"] else True
    return result


def execute_menu(client: Any, raw: dict[str, Any]) -> dict[str, Any] | None:
    """蓝图 menu 节（引擎执行部分；声明区仍走 manualItems）。"""
    menu = raw.get("menu")
    if not menu:
        return None
    parent = menu.get("parent")
    sub = menu.get("sub")
    parent_id = None
    if parent:
        parent_id = menu_ops.create_parent_menu(
            client, name=str(parent.get("name")), code=str(parent.get("code"))).get("menuId")
    if sub and parent_id:
        from ..queries import GaiaQueries

        meta = GaiaQueries(client).get_object_info(str(sub.get("parentName") or sub.get("name")))
        object_id = str((meta or {}).get("id") or "")
        menu_ops.create_sub_menu(client, name=str(sub.get("name")), code=str(sub.get("code")),
                                 parent_id=str(parent_id), object_id=object_id)
    return {"menu": "done"}


def new_request_id() -> str:
    return str(_uuid.uuid4())
