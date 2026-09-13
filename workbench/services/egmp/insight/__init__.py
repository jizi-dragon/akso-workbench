"""insight 命令门面（akso-cc main.ts 四命令的原生 Python 实现）。

调用方（routes_insight）只需 run_login / run_inventory / run_understand /
run_spider；产物统一落 runtime 目录，返回 {ok, artifacts, summary}。
不再依赖 node 与原仓库。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..client import EgmpClient, login
from . import workspace
from .assemble import build_inventory
from .crawler import Crawler
from .report import to_markdown as inventory_markdown
from .spider import run_spider as _run_spider
from .spider import write_spider_artifacts
from .understand import understand_object, write_understanding_artifacts


def run_login(client: EgmpClient, base_url: str, username: str, password: str,
              env_id: str = "") -> dict[str, Any]:
    """登录验证 + token 预热（缓存逻辑在 egmp.client.login）。"""
    token = login(base_url, username, password, env_id)
    return {"ok": True, "baseUrl": base_url, "tokenHead": token[:8] + "…"}


def run_inventory(client: EgmpClient, *, base_url: str = "", env_id: str = "",
                  known_objects: list[str] | None = None,
                  output_dir: Path | None = None,
                  on_log: Callable[[str], None] | None = None,
                  environment: dict[str, Any] | None = None) -> dict[str, Any]:
    log = on_log or (lambda line: None)
    crawler = Crawler(client, known_objects)
    log("① 对象发现链：菜单+工作流 → 布局反解 → OpenAPI 验证 → 白名单兜底")
    discovered = crawler.discover_objects()
    log(f"   发现 {len(discovered)} 个对象")
    crawled: list[dict[str, Any]] = []
    for idx, item in enumerate(discovered, 1):
        code = item["code"]
        try:
            meta = client.get_object(code) or {}
            crawled.append(crawler.crawl_object(meta, code))
            log(f"   [{idx}/{len(discovered)}] {code} 深爬完成")
        except Exception as exc:  # noqa: BLE001 —— 单对象失败不阻断（上游口径）
            crawler.failures.append({"item": code, "error": str(exc)})
    workflows = crawler.list_workflows()
    menu_groups = crawler.list_menu_groups()
    menus = crawler.list_menus()
    inventory = build_inventory(
        {"base_url": base_url, "env_id": env_id, "discovery_via": crawler.discovery_via},
        crawled, workflows, menu_groups, menus, crawler.failures,
    )
    artifacts: list[Path] = []
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        import json

        json_path = output_dir / "inventory.json"
        json_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2, default=str),
                             encoding="utf-8")
        md_path = output_dir / "inventory.md"
        md_path.write_text(inventory_markdown(inventory), encoding="utf-8")
        artifacts = [json_path, md_path]
        # 覆盖度：以「发现到的对象」为分母，深爬成功的为分子（原先无条件 ok:True 是谎报）
        coverage = workspace.build_coverage(
            total=len(discovered), succeeded=len(crawled), failures=crawler.failures,
            discoveredObjects=len(discovered), crawledObjects=len(crawled),
            fieldCount=inventory["summary"].get("fieldCount"),
            statusCount=inventory["summary"].get("statusCount"),
            workflowCount=len(workflows or []),
        )
        workspace.write_manifest(output_dir, workspace.build_manifest(
            command="inventory", object_code="", coverage=coverage,
            artifacts=artifacts, environment=environment, failures=crawler.failures,
        ))
    else:
        coverage = workspace.build_coverage(
            total=len(discovered), succeeded=len(crawled), failures=crawler.failures)
    log(f"■ 盘点完成（覆盖度 {coverage['status']}）：{inventory['summary']}")
    return {"ok": coverage["status"] != "degraded", "summary": inventory["summary"],
            "artifacts": [str(p) for p in artifacts], "coverage": coverage}


def run_understand(client: EgmpClient, codes: list[str], *, output_dir: Path | None = None,
                   llm_summary_fn=None, on_log: Callable[[str], None] | None = None,
                   environment: dict[str, Any] | None = None,
                   workspace_root: Path | None = None) -> dict[str, Any]:
    """逐对象三层理解。

    ``output_dir`` 给定时所有对象落到同一目录（显式指定，保持既有语义）；
    未给定时走**工作区模型**：每个对象一个 ``<root>/<code>/<时间戳>/``，
    并各自写一份 manifest 自证覆盖度（见 docs/INSIGHT-SPEC.md §4）。
    """
    log = on_log or (lambda line: None)
    succeeded = 0
    all_artifacts: list[str] = []
    run_dirs: list[str] = []
    per_object: list[dict[str, Any]] = []

    for idx, code in enumerate(codes, 1):
        log(f"[{idx}/{len(codes)}] 理解对象 {code}…")
        try:
            understanding = understand_object(client, code, llm_summary_fn=llm_summary_fn)
            target = output_dir if output_dir is not None else workspace.run_dir(
                code, root=workspace_root)
            written = write_understanding_artifacts(understanding, target)
            coverage = workspace.coverage_for_understand(understanding)
            workspace.write_manifest(target, workspace.build_manifest(
                command="understand", object_code=code, coverage=coverage,
                artifacts=written, environment=environment,
                failures=understanding.get("failures") or [],
            ))
            all_artifacts.extend(str(p) for p in written)
            run_dirs.append(str(target))
            per_object.append({"objectCode": code, "workspace": str(target),
                               "coverage": coverage["status"]})
            # 只有**真的读到了对象**才算成功——「没抛异常」不等于成功（空响应也会走到这里）
            if coverage["status"] != "degraded":
                succeeded += 1
            log(f"   {code} 完成（{len(understanding['fields'])} 字段 / "
                f"{len(understanding['lifecycle']['states'])} 状态 / "
                f"覆盖度 {coverage['status']}）")
        except Exception as exc:  # noqa: BLE001 —— 单对象失败继续（上游口径）
            log(f"   ✗ {code} 失败：{exc}")
            per_object.append({"objectCode": code, "workspace": None, "coverage": "degraded",
                               "error": str(exc)})

    coverage = workspace.build_coverage(
        total=len(codes), succeeded=succeeded,
        failures=[o for o in per_object if o.get("error")],
    )
    log(f"■ 理解完成：{succeeded}/{len(codes)} 个对象成功（覆盖度 {coverage['status']}）")
    return {"ok": succeeded > 0, "succeeded": succeeded, "total": len(codes),
            "artifacts": all_artifacts, "coverage": coverage,
            "workspaces": run_dirs, "perObject": per_object}


def run_spider(client: EgmpClient, codes: list[str], *, output_dir: Path | None = None,
               on_log: Callable[[str], None] | None = None,
               environment: dict[str, Any] | None = None) -> dict[str, Any]:
    log = on_log or (lambda line: None)
    log(f"蜘蛛计划启动：目标对象 {', '.join(codes)}")
    spider = _run_spider(client, codes)
    artifacts: list[str] = []
    stats = spider["model"]["stats"]
    coverage = workspace.build_coverage(
        total=len(codes), succeeded=len(codes),
        failures=spider.get("failures") or [],
        objectNodes=stats.get("objectNodes"), stateNodes=stats.get("stateNodes"),
        attrEdges=stats.get("attrEdges"), relationEdges=stats.get("relationEdges"),
    )
    if output_dir is not None:
        written = write_spider_artifacts(spider, output_dir)
        artifacts = [str(p) for p in written]
        workspace.write_manifest(output_dir, workspace.build_manifest(
            command="spider", object_code=",".join(codes), coverage=coverage,
            artifacts=written, environment=environment, failures=spider.get("failures") or [],
        ))
    log(f"■ 织网完成（覆盖度 {coverage['status']}）：{stats['objectNodes']} 对象 / "
        f"{stats['stateNodes']} 状态 / {stats['attrEdges']} 归因边（networkx 分析已生成）")
    return {"ok": coverage["status"] != "degraded", "stats": stats,
            "artifacts": artifacts, "coverage": coverage}
