"""egmp 原生化验收：洞察织网 / Monitor 录制解读 / 平台端点常量（离线，全量 mock）。

> ## 2026-09-22：写路径用例随配置工厂整体移除
>
> 本文件原先还覆盖**写路径**：蓝图两层校验与规范化、审阅三件套、拓扑排序、
> `run_full_workflow` 的幂等与闸门、`writers.objects` / `writers.fields` /
> `writers.layouts` 的响应解析与线格式、"对象不存在不许静默新建"、
> "命名列表布局必须有 code"。这些代码（`egmp/writers/**` + `egmp/orchestrate.py`）
> 已整体删除，**用例一并删除**——留着一批 import 不到模块的用例只会让整个文件 collect 失败。
>
> **它们钉住的平台事实没有跟着丢**：PITFALLS #62（平台只回成功标志、不回 id）、
> #67（选项集读路径与 `data` 是选项数组）、#69（`minValue`/`maxValue`/`scale`）、
> #82（建对象自动派生 `<code>_table__ak`；`Listlayout/Save` 要求 `code` 非空）
> 等条目的**平台侧结论**已迁进
> [docs/PLATFORM-OPERATIONS-LEARNED.md](../../docs/PLATFORM-OPERATIONS-LEARNED.md)
> 的「平台事实台账」，原始真机取证在 `factory-tuning/reports/**`。
>
> 这里只保留**不依赖写路径**的部分：洞察织网、Monitor 录制/解读/裁剪，
> 以及**端点常量**这一类纯平台事实（`OBJECT_PICKLIST` 的读路径形状曾被"猜"错过一次，
> 值得单独钉死）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fakes import FakeEgmpClient

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

# ---------------------------------------------------------------- 洞察命令


def test_insight_spider_networkx(tmp_path: Path) -> None:
    client = FakeEgmpClient()
    # 数据种子：直接建一个对象（原先借 `orchestrate.run_full_workflow` 播种，
    # 那条链已随配置工厂移除；这里改成直接打平台的建对象端点，语义更直白）。
    client.post("/api/platform/BasicObject/SaveBasicObject",
                {"name": "培训管理", "code": "training_mgr__c", "enableLifeCycle": True})
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
    """复现计划只记**平台侧事实**（方法/路径/参数），不记"调用哪个函数"。

    这一段是 2026-09-22 改造后的判据：写路径随配置工厂移除时，`API_MAP`
    （端点 → `writers.*` 函数）被删掉了——留着它会产出一份**指向不存在模块**的计划。
    所以这里断言的正是"计划里没有 module/fn，且写入的分类符合 PLANNABLE_CLASSES"。
    """
    (tmp_path / "monitor-log.json").write_text(json.dumps([
        {"method": "POST", "path": "/api/platform/BasicObject/SaveBasicObject",
         "url": "https://fake.example.com/api/platform/BasicObject/SaveBasicObject",
         "timestamp": 100, "postData": json.dumps({"name": "A", "code": "a__c"}),
         "known": True, "status": 200},
        {"method": "GET", "path": "/api/openapi/v1.0/BasicObject/a__c",
         "url": "https://fake.example.com/api/openapi/v1.0/BasicObject/a__c",
         "timestamp": 110, "known": True, "status": 200},
        {"method": "POST", "path": "/api/platform/Workflow/DeleteWorkflowStep",
         "url": "https://fake.example.com/api/platform/Workflow/DeleteWorkflowStep",
         "timestamp": 120, "postData": json.dumps({"id": "step-1"}),
         "known": True, "status": 200},
    ]), encoding="utf-8")
    (tmp_path / "checkpoints.jsonl").write_text(
        json.dumps({"checkpoint": "start", "timestamp": 50}) + "\n"
        + json.dumps({"checkpoint": "user-query", "timestamp": 200}) + "\n",
        encoding="utf-8")
    outcome = interpret_segment(tmp_path / "monitor-log.json", tmp_path, question="创建对象A")
    assert outcome["segmentEntries"] == 3
    plan = json.loads((tmp_path / "reproduce-plan.json").read_text(encoding="utf-8"))
    assert plan["baseUrl"] == "https://fake.example.com"
    assert plan["executor"] is None, "本仓库不提供回放执行器（写路径已移除）"

    steps = plan["steps"]
    assert [s["path"] for s in steps] == ["/api/platform/BasicObject/SaveBasicObject"], (
        f"只应收集 WRITE 类：READ 无需回放、DANGEROUS 永不入计划；实际 {steps}"
    )
    assert all("module" not in s and "fn" not in s for s in steps), (
        "计划里不该再出现'调用哪个函数'——那些模块已随配置工厂删除"
    )
    assert steps[0]["params"] == json.dumps({"name": "A", "code": "a__c"}), (
        "原始参数必须原样保留（它是平台侧事实）"
    )

    report = (tmp_path / "interpretation.md").read_text(encoding="utf-8")
    assert "待 Agent 填充" in report and "是否已迭代学习: 否" in report


# ---------------------------------------------------------------- 端点常量（纯平台事实）


def test_picklist_readback_path_is_a_basicobject_subresource() -> None:
    """回读路径必须是 `BasicObject/picklist/{code}`。

    旧常量写成平级的 `/api/openapi/v1.0/ObjectPicklist/{code}` —— **实测恒 404**
    （真机同时刻对照：`BasicObject/{code}` / `field/` / `lifecycleStatus/` 全 200）。
    OpenAPI 的结构是「子资源挂在对象下」，这里把它钉死，防止再被"猜"回去。

    这条不依赖写路径（消费它的 `GaiaQueries.get_picklist` 仍在），因此留在这里当**平台事实的守门人**。
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
