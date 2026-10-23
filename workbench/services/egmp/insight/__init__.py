"""insight 命令门面（akso-cc 命令的原生 Python 实现）。

调用方（routes_insight）只需 run_login / run_spider；
产物统一落 runtime 目录，返回 {ok, artifacts, coverage}。不再依赖 node 与原仓库。

> **2026-09-14：`inventory` 与 `understand` 已下线**（用户定稿：应用场景模糊、使用效果不理想）。
> 随之删除 `assemble.py` / `annotate.py` / `flowgraph.py` / `relations.py` / `render.py`
> / `understand.py`。现存两命令：`login`（连通性验证 + token 预热）、
> `spider`（五步织网 + networkx 图分析）。
> `workspace`（工作区根 + 三态覆盖度 + manifest）**保留**——它是通用产物契约，spider 在用。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..client import EgmpClient, login
from . import workspace
from .spider import run_spider as _run_spider
from .spider import write_spider_artifacts


def run_login(client: EgmpClient, base_url: str, username: str, password: str,
              env_id: str = "") -> dict[str, Any]:
    """登录验证 + token 预热（缓存逻辑在 egmp.client.login）。"""
    token = login(base_url, username, password, env_id)
    return {"ok": True, "baseUrl": base_url, "tokenHead": token[:8] + "…"}


def run_spider(client: EgmpClient, codes: list[str], *, output_dir: Path | None = None,
               workspace_root: Path | None = None,
               on_log: Callable[[str], None] | None = None,
               environment: dict[str, Any] | None = None) -> dict[str, Any]:
    """五步织网：跨对象关系网络 + networkx 图分析，产物落一格工作区。

    落点二选一（两者都给时以 ``workspace_root`` 优先，它不是偶然的便利参数——见下）：

    - ``workspace_root``：走工作区契约 ``<root>/<对象code>/<时间戳>/``（规格 §4），
      **同一对象历次运行可横向比对**，并回填 ``workspaces`` 供任务表记录；
    - ``output_dir``：直接写进给定目录（路由的 ``runtime/insight/<job_id>/`` 兜底路径）。
    """
    log = on_log or (lambda line: None)
    log(f"蜘蛛计划启动：目标对象 {', '.join(codes)}")
    spider = _run_spider(client, codes)
    artifacts: list[str] = []
    workspaces: list[str] = []
    stats = spider["model"]["stats"]
    notes = spider.get("notes") or []
    graphs = spider.get("workflowGraphs") or []
    unexpanded = spider["model"].get("unexpandedRelations") or []
    coverage = workspace.build_coverage(
        total=len(codes),
        # 实际读到的对象数，不是"我打算读几个"：全部端点都失败时也必须显示 degraded
        # （旧实现写死 len(codes)，覆盖度会谎报 complete —— 见 2026-10-22 审计 2.11）。
        succeeded=len(spider.get("objects") or []),
        failures=spider.get("failures") or [],
        objectNodes=stats.get("objectNodes"), stateNodes=stats.get("stateNodes"),
        attrEdges=stats.get("attrEdges"), relationEdges=stats.get("relationEdges"),
        # 自证「这次织出来多大的网」以及**边界在哪**（规格：不允许静默留白）
        workflowGraphs=len(graphs), unexpandedRelations=len(unexpanded), boundaryNotes=len(notes),
    )
    target = output_dir
    if workspace_root is not None:
        target = workspace.run_dir(",".join(codes), root=workspace_root)
        workspaces = [str(target)]
    if target is not None:
        written = write_spider_artifacts(spider, target)
        artifacts = [str(p) for p in written]
        workspace.write_manifest(target, workspace.build_manifest(
            command="spider", object_code=",".join(codes), coverage=coverage,
            artifacts=written, environment=environment, failures=spider.get("failures") or [],
            notes=notes,
        ))
    log(f"■ 织网完成（覆盖度 {coverage['status']}）：{stats['objectNodes']} 对象 / "
        f"{stats['stateNodes']} 状态 / {stats['attrEdges']} 归因边 / "
        f"{stats['relationEdges']} 关系边 / {len(graphs)} 张工作流图 "
        f"（产物 {len(artifacts)} 件，networkx 分析已生成）")
    for item in notes[:5]:
        log(f"  · 边界：{item}")
    if spider.get("failures"):
        log(f"  ! 读失败 {len(spider['failures'])} 项（已记入 manifest.failures）")
    return {"ok": coverage["status"] != "degraded", "stats": stats,
            "artifacts": artifacts, "coverage": coverage, "workspaces": workspaces,
            "notes": notes}
