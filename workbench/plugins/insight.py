"""平台洞察插件：把 akso-cc 沉淀的「读平台元数据 → 跑织网 → 取产物」接进 Agent。

四个工具都走**已在仓库里的原生实现**，不新增平台调用面。插件层只做三件事：
参数校验（pydantic）、风险分级、把结果压成模型读得懂的文本。

## 两条刻意不做的设计

1. **不提供"列出平台全部对象"工具**。平台没有实证过的对象清单端点
   （`kernel/endpoints.py:74-77` 那三条探针是上游 README 给的，可用性需按环境实测、
   可能全 404）。CODEBASE_OVERVIEW 的红线是「端点常量必须实证来源，不许凭观察猜」，
   所以宁可不给这个能力——模型应当先问用户对象编码，再用 `insight_read_config` 验证。
2. **跑洞察必须复用页面同一条入口**（`api.routes_insight.run`）。绕过去就会漏掉
   `workspace_dir` / `coverage_status` 的回填（2026-09-14 修的真实缺陷，见
   `routes_insight._workspace_paths` 的文档）。插件另写一套 spider 调用会造成
   「页面上看不到产物、Agent 却说自己跑完了」——这比调用层次不干净严重得多。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from .. import db
from .. import plugin_api as pa

PLUGIN_ID = "insight"


# ---------------------------------------------------------------- 参数模型


class AccountScoped(BaseModel):
    account_id: str = pa.Arg("统一账号库里的账号 id（凭证来源；只有唯一账号时可留空）", default="")


class ReadConfigArgs(AccountScoped):
    code: str = pa.Arg("对象编码，如 capa_plan__c")


class RunInsightArgs(AccountScoped):
    objects: str = pa.Arg("逗号分隔的对象编码，如 capa_plan__c,change_plan__c")
    command: str = pa.Arg("login（只验证登录）或 spider（五步织网，默认）", default="spider")


class ArtifactsArgs(BaseModel):
    job_id: str = pa.Arg("洞察任务 id（insight_run 返回的 job_id）")


class ReadArtifactArgs(ArtifactsArgs):
    name: str = pa.Arg("产物文件名（相对路径，如 spider-network.md / step2-status-table.md）")


class ListRunsArgs(BaseModel):
    limit: int = pa.Arg("最近多少条任务", default=10, ge=1, le=50)


# ---------------------------------------------------------------- 账号解析


def pick_account(account_id: str) -> str:
    """账号解析：显式传则用之；只有唯一账号时自动选；**多账号时报错不猜**。

    猜错账号 = 拿别人的身份跑真机。宁可让模型去问用户，也不要"随便挑一个"。
    （旧 `routes_agent._tool_login_platform` 只校验存在性，多账号时靠调用方自觉。）
    """
    from ..services import accounts as accounts_svc

    if account_id:
        if not accounts_svc.get_account(account_id):
            raise ValueError(f"账号不存在：{account_id}（可先问用户，或让其在账号中心确认）")
        return account_id

    rows = accounts_svc.list_accounts()
    if len(rows) == 1:
        return str(rows[0].get("id") or "")
    if not rows:
        raise ValueError("统一账号库里还没有账号：请先在「账号中心」新增环境与账号")
    names = "、".join(f"{r.get('username')}#{r.get('id')}" for r in rows[:8])
    raise ValueError(
        f"账号库有 {len(rows)} 个账号，必须显式指定 account_id —— "
        f"**不要猜**：调 insight_accounts 看完整清单，或直接问用户要用哪个。可选：{names}"
    )


def account_choices() -> list[dict[str, Any]]:
    """给模型看的账号清单（**不含任何凭据**，只回 id/用户名/环境名）。"""
    from ..services import accounts as accounts_svc

    return [
        {
            "account_id": r.get("id"),
            "username": r.get("username"),
            "env": r.get("env_name"),
            "pool": r.get("pool"),
        }
        for r in accounts_svc.list_accounts()
    ]


async def _in_thread(fn: Callable[..., Any], *args: Any) -> Any:
    """同步平台调用挪到线程——工具 handler 跑在事件循环上，不能阻塞它。"""
    return await asyncio.to_thread(fn, *args)


# ---------------------------------------------------------------- 工具实现


async def read_config(args: ReadConfigArgs, ctx: pa.ToolContext) -> str:
    """只读对象元数据（egmp 内核，httpx）。"""
    from ..services.egmp import client as egmp_client

    account_id = pick_account(args.account_id)
    code = str(pa.require(args.code, "code 不能为空")).strip()

    def call() -> dict:
        with egmp_client.egmp_client_for_account(account_id) as http:
            return http.get_object(code)

    meta = await _in_thread(call)
    return json.dumps({"object": code, "meta": meta}, ensure_ascii=False, default=str)


async def run_insight(args: RunInsightArgs, ctx: pa.ToolContext) -> str:
    """跑一次洞察任务（login / spider），与洞察页同一条入口。"""
    from ..api import routes_insight

    account_id = pick_account(args.account_id)
    command = (args.command or "spider").strip()
    if command not in {"login", "spider"}:
        raise ValueError(f"未知命令：{command}（login/spider）")

    req = routes_insight.InsightRequest(
        command=command, account_id=account_id, objects=args.objects or "",
    )
    outcome = await _in_thread(routes_insight.run, req)
    result = outcome.get("result") or {}
    coverage = result.get("coverage") or {}
    return json.dumps(
        {
            "job_id": outcome.get("job_id"),
            "status": outcome.get("status"),
            "coverage": coverage.get("status") or outcome.get("coverage_status"),
            "duration_ms": outcome.get("duration_ms"),
            "workspaces": outcome.get("workspaces") or [],
            "summary": coverage.get("summary") or "",
            "notes": (coverage.get("notes") or [])[:10],
            "failures": (coverage.get("failures") or [])[:10],
            "hint": "调 insight_artifacts(job_id) 看产物清单，再调 insight_read_artifact 读某一件",
        },
        ensure_ascii=False, default=str,
    )


async def artifacts(args: ArtifactsArgs, ctx: pa.ToolContext) -> str:
    """产物清单（含日志与工作区落点）。"""
    from ..api import routes_insight

    job_id = str(pa.require(args.job_id, "job_id 不能为空")).strip()
    data = await _in_thread(routes_insight.list_artifacts, job_id)
    return json.dumps(data, ensure_ascii=False, default=str)


async def read_artifact(args: ReadArtifactArgs, ctx: pa.ToolContext) -> str:
    """读一件产物正文（markdown/drawio/日志）。

    复用页面的路径守卫（`routes_insight.read_artifact` 里的 `is_relative_to` 检查）：
    自己拼路径会绕过守卫，等于给模型开一个任意文件读。
    """
    from ..api import routes_insight

    job_id = str(pa.require(args.job_id, "job_id 不能为空")).strip()
    name = str(pa.require(args.name, "name 不能为空")).strip()
    response = await _in_thread(routes_insight.read_artifact, job_id, name, False)
    raw = getattr(response, "body", b"")
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    return json.dumps(
        {
            "job_id": job_id, "name": name, "suffix": Path(name).suffix.lower(),
            "chars": len(text), "text": text,
        },
        ensure_ascii=False,
    )


async def list_runs(args: ListRunsArgs, ctx: pa.ToolContext) -> str:
    """最近的洞察任务（判断"上次到底跑成没跑成"）。"""
    rows = await _in_thread(
        db.query,
        "SELECT id, command, objects, status, coverage_status, created_at, workspace_dir "
        "FROM insight_runs ORDER BY created_at DESC LIMIT ?",
        (args.limit,),
    )
    return json.dumps({"runs": rows}, ensure_ascii=False, default=str)


async def list_accounts_tool(_: BaseModel, ctx: pa.ToolContext) -> str:
    """列出可用账号（**只回 id/用户名/环境名，永不回凭据**）。

    存在的理由：`pick_account` 在多账号时会要求显式 account_id，模型得有办法知道
    有哪些可选值——否则它会开始猜 id，或者拿一个错账号去跑真机。
    """
    rows = await _in_thread(account_choices)
    return json.dumps({"accounts": rows}, ensure_ascii=False, default=str)


# ---------------------------------------------------------------- 插件声明


PLUGIN = pa.Plugin(
    id=PLUGIN_ID,
    title="平台洞察",
    description="读平台对象元数据、跑五步织网洞察、取回产物（akso-cc 能力原生化）",
    version="1.0.0",
    tools=[
        pa.ToolSpec(
            name="insight_accounts",
            description="列出工作台账号库里的账号（id/用户名/环境），用于确定 account_id；不含口令",
            args_model=pa.ZERO_ARGS,
            handler=list_accounts_tool,
            risk=pa.Risk.READ,
            timeout_s=15,
        ),
        pa.ToolSpec(
            name="insight_read_config",
            description="只读读取某个 eGMP 对象的元数据（对象结构/基本信息）",
            args_model=ReadConfigArgs,
            handler=read_config,
            risk=pa.Risk.READ,
            timeout_s=60,
            audit_extra=pa.audit_env_key("account_id", "code"),
        ),
        pa.ToolSpec(
            name="insight_run",
            description=(
                "跑一次平台洞察：login=只验证登录连通；spider=五步织网（关系网 / 状态-动作表 / "
                "生命周期流转图），产物落用户工作区，返回 job_id 与覆盖度"
            ),
            args_model=RunInsightArgs,
            handler=run_insight,
            risk=pa.Risk.READ,
            timeout_s=300,
            audit_extra=pa.audit_env_key("account_id", "objects", "command"),
        ),
        pa.ToolSpec(
            name="insight_artifacts",
            description="列出某次洞察任务的产物文件（含 proc.log 与工作区落点）",
            args_model=ArtifactsArgs,
            handler=artifacts,
            risk=pa.Risk.READ,
            timeout_s=30,
            audit_extra=pa.audit_env_key("job_id"),
        ),
        pa.ToolSpec(
            name="insight_read_artifact",
            description="读取某件洞察产物正文（spider-network.md / step2-status-table.md / *.drawio 等）",
            args_model=ReadArtifactArgs,
            handler=read_artifact,
            risk=pa.Risk.READ,
            timeout_s=30,
            audit_extra=pa.audit_env_key("job_id", "name"),
        ),
        pa.ToolSpec(
            name="insight_runs",
            description="最近的洞察任务列表（状态/覆盖度/工作区），用于确认上次是否跑成",
            args_model=ListRunsArgs,
            handler=list_runs,
            risk=pa.Risk.READ,
            timeout_s=15,
        ),
    ],
)


def register(hub: pa.ToolHub) -> None:
    hub.add_plugin(PLUGIN)
