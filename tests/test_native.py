"""egmp 原生化验收：蓝图校验/编排管道/洞察命令/Monitor（离线，全量 mock）。"""

from __future__ import annotations

import json
from pathlib import Path

from fakes import FakeEgmpClient

from workbench.services.egmp import complexity, orchestrate
from workbench.services.egmp.insight import run_inventory, run_spider, run_understand
from workbench.services.egmp.monitor import (
    classify_api,
    compare_recordings,
    interpret_segment,
    query_monitor_log,
    read_log_entries,
)
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


def test_topological_sort_and_complexity() -> None:
    objects = [
        {"code": "b__c", "fields": [{"code": "f", "dataType": 13, "referenceObjectCode": "a__c"}]},
        {"code": "a__c", "fields": []},
    ]
    ordered = orchestrate.topological_sort_objects(objects)
    assert [o["code"] for o in ordered] == ["a__c", "b__c"]  # 被引用者先建
    assert complexity.assess_complexity("创建对象")["level"] == "low"
    assert complexity.assess_complexity("带工作流的审批对象")["level"] == "high"


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


def test_insight_inventory_and_understand(tmp_path: Path) -> None:
    client = FakeEgmpClient()
    orchestrate.run_full_workflow(client, _sample_blueprint())  # 造平台数据

    inv = run_inventory(client, base_url="https://fake", env_id="t", output_dir=tmp_path / "inv")
    assert inv["ok"] and inv["summary"]["objectCount"] == 1
    assert (tmp_path / "inv" / "inventory.json").exists()
    assert "对象清单" in (tmp_path / "inv" / "inventory.md").read_text(encoding="utf-8")

    und = run_understand(client, ["training_mgr__c"], output_dir=tmp_path / "und")
    assert und["ok"] and und["succeeded"] == 1
    md = (tmp_path / "und" / "training_mgr__c.md").read_text(encoding="utf-8")
    assert "对象理解报告" in md and "mermaid" in md
    assert (tmp_path / "und" / "training_mgr__c.json").exists()


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
