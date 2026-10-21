"""egmp 原生化验收：洞察织网 / 平台端点常量（离线，全量 mock）。

> ## 2026-10-19：Monitor 随内置浏览器下线
>
> 用户确认未来不再需要 UI 自动登录 / 页面抓取 / 截图，`egmp.monitor` 与
> `browser_pool` / `autologin` 一并删除；本文件只保留**不依赖浏览器**的
> 洞察织网与平台端点常量。历史 Monitor 结论不再有代码承载，故用例一并删除。

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
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fakes import FakeEgmpClient

from workbench.services.egmp.insight import run_spider
from workbench.services.egmp.kernel import endpoints as egmp_endpoints
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
