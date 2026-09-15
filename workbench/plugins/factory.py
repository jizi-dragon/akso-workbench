"""配置工厂插件：蓝图校验 / 生成 / 任务查询 / **真机创建**。

## 为什么 `factory_create` 是 DANGEROUS 而不是 WRITE

它做的是「按蓝图在真实 eGMP 平台上批量建对象、字段、选项集、生命周期、工作流、布局、菜单」，
并且是**幂等可重跑**的设计——也就是说一次误调用不会报错，会**真的建出来**。回滚手段只有
平台侧手工删除。因此它过确认门（`Risk.DANGEROUS` + 人确认令牌），且描述里必须写明后果。

## 与「环境强制确认」原则的关系（两条独立的闸门）

1. **页面级**：`routes_factory.run` 要求 `confirmed=true`，否则 409（原 akso-auto 原则，
   UI 的勾选框承担）。这条在这里**照旧生效**——本插件的 create 传 `confirmed=True`，
   因为**工具的确认门已经替代了那个勾选框的作用**（人确认过一次才拿到令牌）。
2. **工具级**：`Risk.DANGEROUS` → 无令牌不执行（`approvals.py`）。

两条闸门不是替代关系：谁绕过页面（比如直接用 HTTP），谁就仍然被引擎侧的 `confirmed` 挡住。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from pydantic import BaseModel

from .. import config, db
from .. import plugin_api as pa

PLUGIN_ID = "factory"


# ---------------------------------------------------------------- 参数模型


class ValidateArgs(BaseModel):
    blueprint: str = pa.Arg(
        "蓝图 JSON 正文（字符串）。两层校验：结构 + 业务规则；不上传暂存、不碰平台"
    )


class ListBlueprintsArgs(BaseModel):
    limit: int = pa.Arg("最多返回多少份暂存蓝图", default=20, ge=1, le=100)


class CreateArgs(BaseModel):
    blueprint_id: str = pa.Arg("已暂存蓝图 id（factory_list_blueprints 可查）")
    account_id: str = pa.Arg("凭证来源账号 id（决定写进哪个平台环境）")


class StatusArgs(BaseModel):
    job_id: str = pa.Arg("工厂任务 id（factory_create 返回）")
    log_lines: int = pa.Arg("回传日志末尾多少行", default=60, ge=0, le=400)


async def _in_thread(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(lambda: fn(*args, **kwargs))


# ---------------------------------------------------------------- 工具实现


async def validate_blueprint(args: ValidateArgs, ctx: pa.ToolContext) -> str:
    """蓝图两层校验（纯本地，没有副作用，不过确认门）。"""
    from ..services.egmp.writers.blueprint import validate_blueprint as _validate

    raw = pa.require(args.blueprint, "blueprint 不能为空")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"blueprint 不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("蓝图根节点必须是 JSON 对象")

    issues = await _in_thread(_validate, data)
    dumped = [i.model_dump() if hasattr(i, "model_dump") else dict(i) for i in issues]
    errors = [i for i in dumped if i.get("level") == "error"]
    return json.dumps(
        {
            "valid": not errors,
            "error_count": len(errors),
            "warning_count": len(dumped) - len(errors),
            "issues": dumped[:40],
            "top_keys": sorted(data.keys())[:20],
            "next_step": (
                "校验通过：把蓝图暂存为 blueprint_id 后才能 create"
                "（暂存走配置工厂页面上传，或让用户把文件拖进页面）"
                if not errors else "先修掉 error 级问题再创建"
            ),
        },
        ensure_ascii=False, default=str,
    )


async def list_blueprints(args: ListBlueprintsArgs, ctx: pa.ToolContext) -> str:
    """列出运行时目录里已暂存的蓝图（create 的入参来源）。"""
    root = config.RUNTIME_DIR / "factory"

    def scan() -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if not root.exists():
            return items
        for path in sorted(root.glob("*/blueprint.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            items.append({
                "blueprint_id": path.parent.name,
                "name": data.get("name") or path.parent.name,
                "description": str(data.get("description") or "")[:200],
                "objects": [o.get("code") for o in (data.get("objects") or []) if isinstance(o, dict)],
                "mtime": int(path.stat().st_mtime * 1000),
            })
        return items

    items = await _in_thread(scan)
    items.sort(key=lambda i: i["mtime"], reverse=True)
    return json.dumps(
        {"total": len(items), "blueprints": items[: args.limit]},
        ensure_ascii=False, default=str,
    )


async def status(args: StatusArgs, ctx: pa.ToolContext) -> str:
    """工厂任务详情（状态 / 日志尾 / checkpoint），用来判断"跑到哪一步了"。"""
    from ..api import routes_factory

    job_id = str(pa.require(args.job_id, "job_id 不能为空")).strip()
    detail = await _in_thread(routes_factory.job_detail, job_id)
    tail = detail.get("log_tail") or []
    return json.dumps(
        {
            "job_id": job_id,
            "status": detail.get("status"),
            "command": detail.get("command"),
            "created_at": detail.get("created_at"),
            "updated_at": detail.get("updated_at"),
            "checkpoint": detail.get("checkpoint") or {},
            "log_tail": tail[-args.log_lines:] if args.log_lines else [],
        },
        ensure_ascii=False, default=str,
    )


async def create(args: CreateArgs, ctx: pa.ToolContext) -> str:
    """**真机创建**（已过人确认门才可能走到这里）。"""
    from ..api import routes_factory

    blueprint_id = str(pa.require(args.blueprint_id, "blueprint_id 不能为空")).strip()
    account_id = str(pa.require(args.account_id, "account_id 不能为空")).strip()

    workdir = config.RUNTIME_DIR / "factory" / blueprint_id
    if not (workdir / "blueprint.json").is_file():
        raise ValueError(
            f"暂存蓝图不存在：{blueprint_id}（用 factory_list_blueprints 查可用 id）"
        )

    # confirmed=True 是**刻意的**：到这一步已经过了工具级确认门（人点过放行），
    # 引擎侧的 confirmed_env 闸门因此得到满足。绕过工具直接打 HTTP 的人仍然过不了它。
    body = routes_factory.FactoryRunIn(
        blueprint_id=blueprint_id, command="create", account_id=account_id, confirmed=True,
    )
    outcome = await _in_thread(routes_factory.run, body)
    result = outcome.get("result") or {}
    steps = result.get("steps") or result.get("checkpoint") or {}
    return json.dumps(
        {
            "job_id": outcome.get("job_id"),
            "status": outcome.get("status"),
            "success": result.get("success"),
            "message": result.get("message"),
            "artifact_dir": result.get("artifact_dir"),
            "steps": steps,
            "created": result.get("created") or result.get("summary") or {},
            "log_tail": (outcome.get("log_tail") or [])[-30:],
            "hint": "用 factory_status(job_id) 看后续进度与完整日志",
        },
        ensure_ascii=False, default=str,
    )


async def latest_jobs(_: BaseModel, ctx: pa.ToolContext) -> str:
    """最近工厂任务（先看有没有跑过、跑成没跑成）。"""
    rows = await _in_thread(
        db.query,
        "SELECT id, command, status, created_at, updated_at FROM blueprint_jobs "
        "ORDER BY created_at DESC LIMIT 10",
    )
    return json.dumps({"jobs": rows}, ensure_ascii=False, default=str)


# ---------------------------------------------------------------- 插件声明


PLUGIN = pa.Plugin(
    id=PLUGIN_ID,
    title="配置工厂",
    description="蓝图两层校验、暂存蓝图清单、真机批量创建（akso-auto 能力原生化）",
    version="1.0.0",
    tools=[
        pa.ToolSpec(
            name="factory_validate_blueprint",
            description="校验一份蓝图 JSON（结构 + 业务规则两层），返回 error/warning 清单；纯本地无副作用",
            args_model=ValidateArgs,
            handler=validate_blueprint,
            risk=pa.Risk.READ,
            timeout_s=60,
        ),
        pa.ToolSpec(
            name="factory_list_blueprints",
            description="列出已暂存到运行时的蓝图（id/名称/涉及对象），create 的入参来源",
            args_model=ListBlueprintsArgs,
            handler=list_blueprints,
            risk=pa.Risk.READ,
            timeout_s=20,
        ),
        pa.ToolSpec(
            name="factory_jobs",
            description="最近的配置工厂任务（状态/时间），用于确认此前是否已经创建过",
            args_model=pa.ZERO_ARGS,
            handler=latest_jobs,
            risk=pa.Risk.READ,
            timeout_s=15,
        ),
        pa.ToolSpec(
            name="factory_status",
            description="某个工厂任务的详情：状态、日志末尾、断点续跑 checkpoint",
            args_model=StatusArgs,
            handler=status,
            risk=pa.Risk.READ,
            timeout_s=30,
            audit_extra=pa.audit_env_key("job_id"),
        ),
        pa.ToolSpec(
            name="factory_create",
            description=(
                "【危险】在真实平台上按蓝图批量创建对象/字段/选项集/生命周期/工作流/布局/菜单。"
                "幂等可重跑，但会真的改平台数据，必须经用户确认后才会执行"
            ),
            args_model=CreateArgs,
            handler=create,
            risk=pa.Risk.DANGEROUS,
            timeout_s=900,
            audit_extra=pa.audit_env_key("blueprint_id", "account_id"),
        ),
    ],
)


def register(hub: pa.ToolHub) -> None:
    hub.add_plugin(PLUGIN)
