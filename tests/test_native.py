"""egmp 原生化验收：蓝图校验/编排管道/洞察命令/Monitor（离线，全量 mock）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fakes import FakeEgmpClient

from workbench.services.egmp import orchestrate
from workbench.services.egmp.insight import run_spider
from workbench.services.egmp.kernel import endpoints as egmp_endpoints
from workbench.services.egmp.monitor import (
    classify_api,
    compare_recordings,
    interpret_segment,
    query_monitor_log,
    read_log_entries,
)
from workbench.services.egmp.monitor.monitor import TRIM_SIZE, trim_body
from workbench.services.egmp.queries import GaiaQueries
from workbench.services.egmp.writers import fields as writers_fields
from workbench.services.egmp.writers import objects as writers_objects
from workbench.services.egmp.writers.blueprint import (
    generate_checklist,
    generate_review_spec,
    has_blocking,
    normalize_blueprint,
    to_object_blueprints,
    validate_blueprint,
)


def _sample_blueprint() -> dict:
    return {
        "description": "测试对象",
        "objects": [{
            "name": "培训管理", "code": "training_mgr__c", "enableLifeCycle": True,
            "picklists": [{"name": "培训类型", "code": "training_type__c",
                           "options": [{"name": "内训"}, {"name": "外训"}]}],
            "fields": [
                {"name": "名称", "code": "title", "dataType": 1, "isRequired": True},
                {"name": "类型", "code": "type", "dataType": 4, "picklistCode": "training_type__c"},
                {"name": "开始日期", "code": "start_date", "dataType": 5},
                {"name": "负责对象", "code": "owner_obj", "dataType": 15,
                 "referenceObjectCode": "training_mgr__c"},
            ],
            "formLayout": {"sections": [{"name": "基本信息", "code": "basic_info__ak", "type": 1,
                                         "columnsNum": 2,
                                         "fields": ["title", "type", "start_date", "owner_obj"]}]},
            "listLayout": {"columns": [{"fieldCode": "title", "sort": 0},
                                       {"fieldCode": "type", "sort": 1}]},
            "lifecycle": {"renameStatuses": [{"fromCode": "init_status__c", "toName": "草稿"}],
                          "statuses": [{"name": "进行中", "code": "status_doing__c"}]},
            "workflows": [{"name": "培训审批流", "code": "training_flow__c", "autoEnable": True,
                           "bindToStatusCode": "status_doing__c",
                           "participants": [{"name": "培训管理员", "code": "trainer_admin"}],
                           "steps": [{"name": "审批", "code": "step_approve", "type": "task"},
                                     {"name": "改状态", "code": "step_done", "type": "action",
                                      "behaviorType": 9, "behaviorValue": "status_doing__c"}]}],
        }],
    }


# ---------------------------------------------------------------- 蓝图校验


def test_validate_passes_sample() -> None:
    issues = validate_blueprint(_sample_blueprint())
    assert not has_blocking(issues), [i.model_dump() for i in issues if i.level == "error"]


def test_validate_layer1_rejects_bad_structure() -> None:
    issues = validate_blueprint({"objects": [{"name": "缺编码"}]})
    assert has_blocking(issues)


def test_validate_layer2_rules() -> None:
    raw = {"objects": [{
        "name": "坏对象", "code": "BadCode",
        "fields": [
            {"name": "a", "code": "dup", "dataType": 1},
            {"name": "b", "code": "dup", "dataType": 1},
            {"name": "c", "code": "opt", "dataType": 4},
            {"name": "d", "code": "num", "dataType": 2, "max": 1, "min": 5},
        ],
        "formLayout": {"sections": [{"name": "s", "code": "s__ak", "type": 1, "columnsNum": 2,
                                     "fields": ["ghost_field"]}]},
        "workflows": [{"name": "流", "code": "flow__c",
                       "steps": [{"name": "审批", "code": "step1", "type": "task"}]}],
    }]}
    issues = validate_blueprint(raw)
    errors = [i.message for i in issues if i.level == "error"]
    assert any("__c" in m for m in errors)          # 编码规范
    assert any("重复" in m for m in errors)          # 字段去重
    assert any("picklistCode" in m for m in errors)  # 选项字段条件必填
    assert any("max" in m for m in errors)           # 数字 max>min
    assert any("ghost_field" in m for m in errors)   # 布局引用白名单
    assert any("未启用生命周期" in i.message for i in issues if i.level == "warning")


def test_normalize_and_single_object_form() -> None:
    raw = {"name": "培训管理", "code": "TrainingMgr",
           "fields": [{"name": "名称", "code": "Title", "dataType": 1}]}
    normalized, notes = normalize_blueprint(raw)
    assert normalized["code"] == "training_mgr__c"
    assert normalized["fields"][0]["code"] == "title"
    assert normalized["fields"][0]["statisticsArrange"] == {"id": 1}
    assert any("__c" in note for note in notes)
    objects = to_object_blueprints(normalized)
    assert len(objects) == 1


def test_review_artifacts_render() -> None:
    spec = generate_review_spec(_sample_blueprint())
    checklist = generate_checklist(_sample_blueprint())
    assert "培训管理" in spec and "培训审批流" in spec
    assert "- [ ] 对象创建" in checklist and "- [ ] 验证（OpenAPI 回读）" in checklist


# ---------------------------------------------------------------- 编排管道


def test_topological_sort_objects() -> None:
    """引用者后建（被引用者先建）——**排序由引擎做，不交给模型**。

    这条留着并**加了断言强度**：`complexity.assess_complexity` 的断言随
    `complexity.py` 一起删了（那是 `command="orchestrate"` 那条已下线路径的组件，
    见 docs/FACTORY-AGENT-GAP.md 问题④），但拓扑排序是**确定性执行逻辑**，不动。
    """
    objects = [
        {"code": "b__c", "fields": [{"code": "f", "dataType": 13, "referenceObjectCode": "a__c"}]},
        {"code": "a__c", "fields": []},
    ]
    ordered = orchestrate.topological_sort_objects(objects)
    assert [o["code"] for o in ordered] == ["a__c", "b__c"]  # 被引用者先建

    # 成环时保持声明序（不抛异常、不丢对象）——上游口径，别"顺手"改成报错
    cyclic = [{"code": "x__c", "fields": [{"code": "f", "dataType": 13,
                                           "referenceObjectCode": "y__c"}]},
              {"code": "y__c", "fields": [{"code": "g", "dataType": 13,
                                           "referenceObjectCode": "x__c"}]}]
    assert [o["code"] for o in orchestrate.topological_sort_objects(cyclic)] == ["x__c", "y__c"]


def test_run_full_workflow_idempotent(tmp_path: Path) -> None:
    blueprint = _sample_blueprint()
    client = FakeEgmpClient()
    first = orchestrate.run_full_workflow(client, blueprint, artifact_dir=tmp_path)
    assert first["success"], json.dumps(first, ensure_ascii=False, default=str)[:800]
    assert first["objects"][0]["steps"]["verify"]["success"]
    verify_field_count = first["objects"][0]["steps"]["verify"]["fieldCount"]
    assert verify_field_count == 4

    second = orchestrate.run_full_workflow(client, blueprint, artifact_dir=tmp_path)
    assert second["success"]
    assert second["objects"][0].get("skipped") or \
        second["objects"][0]["steps"]["object"].get("status") == "skipped"


def test_run_full_workflow_rejects_invalid_and_unconfirmed(tmp_path: Path) -> None:
    client = FakeEgmpClient()
    bad = orchestrate.run_full_workflow(client, {"objects": [{"name": "x"}]}, artifact_dir=tmp_path)
    assert not bad["success"] and bad["message"] == "蓝图校验未通过"
    unconfirmed = orchestrate.run_full_workflow(client, _sample_blueprint(),
                                                confirmed_env=False, artifact_dir=tmp_path)
    assert "强制确认" in unconfirmed["message"]
    unapproved = orchestrate.run_full_workflow(client, _sample_blueprint(),
                                               approved=False, artifact_dir=tmp_path)
    assert "审批" in unapproved["message"]


# ---------------------------------------------------------------- 洞察命令


def test_insight_spider_networkx(tmp_path: Path) -> None:
    client = FakeEgmpClient()
    orchestrate.run_full_workflow(client, _sample_blueprint())
    spider = run_spider(client, ["training_mgr__c"], output_dir=tmp_path)
    assert spider["ok"]
    stats = spider["stats"]
    assert stats["objectNodes"] >= 1 and stats["attrEdges"] >= 0
    assert (tmp_path / "spider-network.json").exists()
    assert (tmp_path / "spider-network.drawio").exists()
    network_md = (tmp_path / "spider-network.md").read_text(encoding="utf-8")
    assert "networkx" in network_md
    # drawio 基本结构
    drawio_text = (tmp_path / "spider-network.drawio").read_text(encoding="utf-8")
    assert drawio_text.startswith("<mxfile") and 'relative="1"' in drawio_text


# ---------------------------------------------------------------- Monitor


def test_monitor_classify_and_query(tmp_path: Path) -> None:
    assert classify_api("GET", "/api/platform/BasicObject/FieldPage") == "READ"
    assert classify_api("POST", "/api/platform/BasicObject/SaveBasicObject") == "WRITE"
    assert classify_api("POST", "/api/platform/Workflow/DeleteWorkflowStep") == "DANGEROUS"

    log_path = tmp_path / "monitor-log.json"
    entries = [
        {"method": "POST", "path": "/api/platform/BasicObject/SaveBasicObject",
         "url": "https://fake.example.com/api/platform/BasicObject/SaveBasicObject",
         "timestamp": 1, "postData": json.dumps({"name": "A", "code": "a__c", "extra": 1}),
         "known": True, "status": 200, "body": {"code": 0}},
        {"method": "POST", "path": "/api/platform/BasicObject/SaveBasicObject",
         "url": "https://fake.example.com/api/platform/BasicObject/SaveBasicObject",
         "timestamp": 2, "postData": json.dumps({"name": "B", "code": "b__c"}),
         "known": True, "status": 200, "body": {"code": 0}},
        {"method": "GET", "path": "/api/openapi/v1.0/BasicObject/a__c",
         "url": "https://fake.example.com/x", "timestamp": 3, "known": True},
    ]
    # 格式一：纯数组
    log_path.write_text(json.dumps(entries), encoding="utf-8")
    assert len(read_log_entries(log_path)) == 3
    result = query_monitor_log(log_path, keyword="SaveBasicObject", unknown_only=False)
    assert result["total"] == 2
    # 格式二：{entries,_actions}
    log_path.write_text(json.dumps({"entries": entries, "_actions": []}), encoding="utf-8")
    assert len(read_log_entries(log_path)) == 3

    samples = [{"postData": e.get("postData")} for e in entries[:2]]
    inference = compare_recordings(samples)
    assert inference["params"]["$.name"]["required"] is True
    assert inference["confidence"] > 0


def test_monitor_interpret_segment(tmp_path: Path) -> None:
    (tmp_path / "monitor-log.json").write_text(json.dumps([
        {"method": "POST", "path": "/api/platform/BasicObject/SaveBasicObject",
         "url": "https://fake.example.com/api/platform/BasicObject/SaveBasicObject",
         "timestamp": 100, "postData": json.dumps({"name": "A", "code": "a__c"}),
         "known": True, "status": 200},
    ]), encoding="utf-8")
    (tmp_path / "checkpoints.jsonl").write_text(
        json.dumps({"checkpoint": "start", "timestamp": 50}) + "\n"
        + json.dumps({"checkpoint": "user-query", "timestamp": 200}) + "\n",
        encoding="utf-8")
    outcome = interpret_segment(tmp_path / "monitor-log.json", tmp_path, question="创建对象A")
    assert outcome["segmentEntries"] == 1
    plan = json.loads((tmp_path / "reproduce-plan.json").read_text(encoding="utf-8"))
    assert plan["baseUrl"] == "https://fake.example.com"
    assert plan["steps"][0]["module"] == "writers.objects"
    report = (tmp_path / "interpretation.md").read_text(encoding="utf-8")
    assert "待 Agent 填充" in report and "是否已迭代学习: 否" in report


# ---------------------------------------------------------------- 写路径响应解析（PITFALLS #62）


def test_create_object_reads_back_id_when_platform_returns_success_flag() -> None:
    """平台只回成功标志时，objectId 必须是**回读到的真实 id**，不能是布尔或 `"True"`。

    `FakeEgmpClient` 的 `SaveBasicObject` / `SaveField` 现已改回**真机形状**：
    只回 `{code:0, data:true}`（经 `_envelope` 就是布尔 `True`），**不回 id**。
    台架原先回 `{"id": ...}` —— 那正是真实平台**不**做的事，于是旧实现里
    `isinstance(resp, dict)` 那条正确分支被反复执行、`else resp` 那条
    **从未被测过**，缺陷因此长期藏着（见 [docs/PITFALLS.md](../../docs/PITFALLS.md) #62）。
    """
    client = FakeEgmpClient()
    out = writers_objects.create_object(client, name="培训管理", code="training_mgr__c")
    assert out["success"] is True
    assert out["objectId"] not in (True, "True", None), f"拿到了假 id：{out['objectId']!r}"
    assert out["objectId"] == client.objects["training_mgr__c"]["id"]


def test_create_field_reads_back_id_when_platform_returns_success_flag() -> None:
    """同上，字段这一层。旧实现会让 `fieldMap` 里存进字符串 `"True"`。"""
    client = FakeEgmpClient()
    obj = writers_objects.create_object(client, name="培训管理", code="training_mgr__c")
    result = writers_fields.create_field(client, object_id=obj["objectId"],
                                         name="培训主题", code="topic__c", data_type=1)
    assert result["success"] is True
    assert result["fieldId"] not in (True, "True", None), f"拿到了假 id：{result['fieldId']!r}"
    assert result["fieldId"] == client.fields_by_object["training_mgr__c"][0]["id"]


def test_create_object_reports_failure_when_id_is_unavailable() -> None:
    """回读也拿不到 id 时**必须如实报失败** —— 旧实现无条件 `success: True`，
    会让空 id 一路流到建字段那一步，症状变成"平台报了个看不懂的错"。"""

    class _Blind(FakeEgmpClient):
        def post(self, path: str, body: dict | None = None) -> Any:
            return True

        def get(self, path: str, params: dict | None = None) -> Any:
            return {}

        def paged_post(self, path: str, body: dict | None = None,
                       page_size: int = 1000) -> list:
            return []

    out = writers_objects.create_object(_Blind(), name="无回读", code="blind__c")
    assert out["success"] is False
    assert out["objectId"] is None
    assert "回读不到 id" in out["message"]


# ------------------------------------------------- 选项集 → 字段 的 id 解析（PITFALLS #67）


def test_picklist_readback_path_is_a_basicobject_subresource() -> None:
    """回读路径必须是 `BasicObject/picklist/{code}`。

    旧常量写成平级的 `/api/openapi/v1.0/ObjectPicklist/{code}` —— **实测恒 404**
    （真机同时刻对照：`BasicObject/{code}` / `field/` / `lifecycleStatus/` 全 200）。
    OpenAPI 的结构是「子资源挂在对象下」，这里把它钉死，防止再被"猜"回去。
    """
    assert egmp_endpoints.OBJECT_PICKLIST == "/api/openapi/v1.0/BasicObject/picklist/{code}"


def _make_picklist(client: Any, code: str = "training_type__c") -> str:
    client.post("/api/platform/ObjectPicklist/save", {
        "name": "培训类型", "code": code,
        "options": [{"name": "内训"}, {"name": "外训"}]})
    return client.picklists[code]["objectPicklistId"]


def test_get_picklist_normalizes_option_array_to_picklist_id() -> None:
    """真机 `data` 是**选项数组**，选项集 id 在 `objectPicklistId` 里，不在 `data["id"]`。

    旧实现 `return data or None` 把 list 原样抛出，调用方 `meta.get("id")` 会
    `AttributeError`，而那个异常被调用方的 `try/except` 吞掉 —— 表现为
    「拿不到 id」而不是「报错」，所以查不出来。
    """
    client = FakeEgmpClient()
    picklist_id = _make_picklist(client)
    meta = GaiaQueries(client).get_picklist("training_type__c")
    assert isinstance(meta, dict), f"必须是归一化后的 dict，拿到 {type(meta).__name__}"
    assert meta["id"] == picklist_id
    assert len(meta["options"]) == 2


def test_resolve_picklist_id_finds_id_by_code_lookup() -> None:
    """本地映射里没有时必须能**查出来** —— 这是字段拿到选项集的唯一途径。"""
    client = FakeEgmpClient()
    picklist_id = _make_picklist(client)
    got = writers_fields.resolve_picklist_id(client, "training_type__c", {})
    assert got == picklist_id, f"应解析出真实选项集 id，拿到 {got!r}"


def test_picklist_field_body_carries_picklist_id() -> None:
    """端到端：选项字段的请求体里**必须真的有 `picklistId`**。

    旧实现拿不到 id 就**不往请求体里放**这个键 ⇒「选项字段」被静默建成了一个
    没有选项集的选项字段，而接口照样回 `data:true`。
    """
    client = FakeEgmpClient()
    picklist_id = _make_picklist(client)
    obj = writers_objects.create_object(client, name="培训管理", code="training_mgr__c")

    sent: list[dict] = []
    original = client.post

    def recording_post(path: str, body: dict | None = None) -> Any:
        if path.endswith("/SaveField"):
            sent.append(body or {})
        return original(path, body)

    client.post = recording_post  # type: ignore[method-assign]
    writers_fields.batch_create_fields(
        client, object_id=obj["objectId"],
        fields=[{"name": "类型", "code": "type__c", "dataType": 4,
                 "picklistCode": "training_type__c"}],
        picklist_map={})
    assert sent, "没抓到 SaveField 请求"
    assert sent[0].get("picklistId") == picklist_id, \
        f"选项字段的请求体里没有正确的 picklistId：{sent[0]}"


# ------------------------------------------------- 数字字段的范围键名（PITFALLS #69）


def test_number_field_extras_use_platform_key_names() -> None:
    """数字字段的 min/max/小数位，**必须用平台的键名**：`minValue`/`maxValue`/`scale`。

    蓝图侧叫 `min`/`max`/`decimalPlaces`，直接发这个名字平台**静默丢弃** ——
    接口回 `data:true`、字段也建出来了，只有回读才会发现是空的。
    真机对照：工厂发 `min`/`max` → `minValue=""`；界面发 `minValue`/`maxValue` → 落上。
    """
    extras = writers_fields.collect_extras(
        {"dataType": 2, "min": 0, "max": 100, "decimalPlaces": 2})
    assert extras.get("minValue") == 0
    assert extras.get("maxValue") == 100
    assert extras.get("scale") == 2
    assert "min" not in extras and "max" not in extras and "decimalPlaces" not in extras


def test_number_field_body_carries_platform_range_keys() -> None:
    """端到端：`SaveField` 请求体里出现的必须是 `minValue`/`maxValue`。"""
    client = FakeEgmpClient()
    obj = writers_objects.create_object(client, name="培训管理", code="training_mgr__c")

    sent: list[dict] = []
    original = client.post

    def recording_post(path: str, body: dict | None = None) -> Any:
        if path.endswith("/SaveField"):
            sent.append(body or {})
        return original(path, body)

    client.post = recording_post  # type: ignore[method-assign]
    writers_fields.batch_create_fields(
        client, object_id=obj["objectId"],
        fields=[{"name": "数量", "code": "qty__c", "dataType": 2,
                 "min": 1, "max": 999}],
        picklist_map={})
    assert sent, "没抓到 SaveField 请求"
    assert sent[0].get("minValue") == 1, f"没有用平台的键名：{sent[0]}"
    assert sent[0].get("maxValue") == 999, f"没有用平台的键名：{sent[0]}"


# ---------------------------------------------------------------- 大响应裁剪（PITFALLS #63）

def test_trim_body_keeps_data_under_real_platform_envelope() -> None:
    """真机信封是 `{code, errorCode, message, data, requestID, traceId}` —— **内容全在 `data` 里**。

    旧白名单是 `("code", "message", "id", "name", "success")`，**漏了 `data`**；
    实测一次 20 条写请求的采集里 11 条响应被裁成只剩 `{code, message}`。
    """
    big = {"code": 0, "errorCode": 0, "message": None,
           "data": {"datas": [{"code": f"f{i}__c", "name": f"字段{i}"} for i in range(400)],
                    "hasNext": False, "total": 400},
           "requestID": "0HNON16GD5ER9:00000063", "traceId": "t" * 40}
    text = json.dumps(big, ensure_ascii=False)
    assert len(text) > TRIM_SIZE, "样例不够大，测不到裁剪分支"

    out = trim_body(text)
    assert isinstance(out, dict)
    assert out["_trimmed"] is True, "截断必须显式告知（AGENTS.md 规则 4）"
    assert "data" in out, "data 被整个丢掉了 —— 这正是 #63"
    assert "f0__c" in str(out["data"]), "data 留下了但内容没保住"


def test_trim_body_leaves_small_bodies_untouched() -> None:
    small = json.dumps({"code": 0, "data": True})
    assert trim_body(small) == small
    assert trim_body(None) is None


# ------------------------------------------- 对象不存在时不许静默新建（用户规则 2026-09-22）


def test_missing_object_with_similar_candidate_blocks_the_run() -> None:
    """★ **对象不存在时不许静默新建** —— 用户 2026-09-22 定的规则。

    规则原文：「当用户描述的对象不存在时，要给用户提出找不到该对象，并列举出相似对象给用户选择，
    并询问用户若都不存在时，是否需要新建。」

    **为什么必须钉住**：对象步骤是幂等的（code 存在→复用，不存在→**新建**），
    于是"code 打错一个字"的后果**不是报错**，而是**静默多出一个空对象**，
    而用户以为改的是他那个（2026-09-22 实测踩过：跑之前先只读核对才没出事）。

    判据两条：① 相近对象能被**机械**找到（末位数字 ±1/±2 的变体逐个探测）；
    ② 有候选时 `run_full_workflow` **拒绝执行**、回候选清单，且**一个对象都没建**。
    """
    from workbench.services.egmp import orchestrate

    client = FakeEgmpClient()
    client.objects["test_0903__c"] = {"id": "obj-0903", "name": "测试09-03", "code": "test_0903__c"}

    # ① 机械相似：`test_0905__c` 的 ±2 变体命中 `test_0903__c`
    candidates = orchestrate.similar_object_codes(client, "test_0905__c")
    assert [c["code"] for c in candidates] == ["test_0903__c"], candidates

    # ② 有候选 ⇒ 拒绝写入，并把候选交回给人选
    blueprint = {
        "objects": [{
            "name": "测试09-03", "code": "test_0905__c",
            "fields": [{"name": "验证文本", "code": "probe_text__c", "dataType": 1}],
            "formLayout": {"sections": [{"name": "验证部分A", "code": "probe_a__cs",
                                        "type": 1, "columnsNum": 2, "fields": ["probe_text__c"]}]},
        }],
    }
    result = orchestrate.run_full_workflow(client, blueprint, approved=True, confirmed_env=True)
    assert result["success"] is False
    blocked = result.get("needsObjectChoice") or []
    assert blocked and blocked[0]["code"] == "test_0905__c", blocked
    assert "test_0903__c" in result["message"]
    assert "test_0905__c" not in client.objects, "**拒绝写入被违反了**：错 code 的对象还是被建了出来"
