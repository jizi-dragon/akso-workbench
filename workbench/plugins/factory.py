"""配置工厂插件：蓝图规格 / 预览暂存 / 任务查询 / **真机创建**。

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

## 2026-09-20 的接口改造（见 docs/FACTORY-AGENT-GAP.md）

用户实测反馈「增加能力不能让 agent 直接理解，需要绕圈子」，根因是**这个插件缺一个动词**：
它能校验、能执行，但**不能把校验通过的东西交给执行**——`blueprint_id` 只能由网页上的
文件上传产生。于是 `factory_validate_blueprint` 的返回值只能教模型去求人：

    "校验通过：把蓝图暂存为 blueprint_id 后才能 create（暂存走配置工厂页面上传，
     或让用户把文件拖进页面）"

现在改成两个工具，闭环：

| 工具 | 干什么 |
|---|---|
| `factory_blueprint_spec` | 把**蓝图规格**交给模型（从运行期 pydantic 模型 + 实证词典生成） |
| `factory_preview_blueprint` | **校验 + 暂存 + 出审阅材料**，一次调用闭合；校验不过不暂存 |

`factory_validate_blueprint` 这个名字消失了，因为它描述的是**手段**（校验），
而这一步的真实角色是**把将要执行的东西摆给用户看**（见该问题③）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from .. import config, db
from .. import plugin_api as pa

PLUGIN_ID = "factory"


# ---------------------------------------------------------------- 参数模型


class PreviewArgs(BaseModel):
    blueprint: str = pa.Arg(
        "蓝图 JSON 正文（字符串）。校验通过会**暂存**并返回 blueprint_id，"
        "同时给出给用户看的审阅清单；有 error 则不暂存"
    )


class ListBlueprintsArgs(BaseModel):
    limit: int = pa.Arg("最多返回多少份暂存蓝图", default=20, ge=1, le=100)


class CreateArgs(BaseModel):
    blueprint_id: str = pa.Arg("已暂存蓝图 id（factory_preview_blueprint 返回，或用户在页面上传的）")
    account_id: str = pa.Arg("凭证来源账号 id（决定写进哪个平台环境）")


class StatusArgs(BaseModel):
    job_id: str = pa.Arg("工厂任务 id（factory_create 返回）")
    log_lines: int = pa.Arg("回传日志末尾多少行", default=60, ge=0, le=400)


async def _in_thread(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return await asyncio.to_thread(lambda: fn(*args, **kwargs))


def staged_dir(blueprint_id: str) -> Path:
    """暂存目录（**唯一的一处口径**：工具、页面、引擎都从它派生路径）。"""
    return config.RUNTIME_DIR / "factory" / blueprint_id


# ---------------------------------------------------------------- 工具实现


def _entry_path() -> Path:
    return config.RUNTIME_DIR / "factory"


async def blueprint_spec_tool(_: BaseModel, ctx: pa.ToolContext) -> str:
    """把蓝图规格交给模型（结构从运行期模型生成，见 writers/spec.py）。"""
    from ..services.egmp.writers.spec import blueprint_spec_json

    return await _in_thread(blueprint_spec_json)


async def preview_blueprint(args: PreviewArgs, ctx: pa.ToolContext) -> str:
    """**校验 + 暂存 + 出审阅材料**——把「写好的蓝图交出去」这一步闭合。

    与改造前的 `factory_validate_blueprint` 的关键差别：**它有产出**。
    校验通过就落盘并返回 `blueprint_id`，模型不必再求用户去网页上传。

    `risk` 仍然是 `read`：它只写应用自己的 runtime 工作区（可整体删除），
    不碰平台、不碰凭据——与 `insight_run`（在 runtime 工作区落一整套产物）同级。
    **不给它加确认门**：暂存本身不是"对环境生效的动作"，真正的闸门是 `factory_create`；
    为"暂存"再点一次确认是纯噪音，还会训练用户习惯性点同意（把确认门用废）。
    """
    from ..services.egmp.writers import spec as spec_mod
    from ..services.egmp.writers.blueprint import (
        generate_checklist,
        generate_review_spec,
        validate_blueprint,
    )

    raw = pa.require(args.blueprint, "blueprint 不能为空")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"blueprint 不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("蓝图根节点必须是 JSON 对象")

    issues = await _in_thread(validate_blueprint, data)
    dumped = [i.model_dump() if hasattr(i, "model_dump") else dict(i) for i in issues]
    errors = [i for i in dumped if i.get("level") == "error"]

    if errors:
        # 校验不过**不暂存**：半成品留在运行时目录里，下一次 list 就会看到它，
        # 用户还得自己分辨哪份是能用的
        return json.dumps(
            {
                "valid": False,
                "error_count": len(errors),
                "warning_count": len(dumped) - len(errors),
                "issues": dumped[:40],
                # 错误信息只说"错在哪"，这里补上"对的是什么"——省掉一次规格查询
                "field_hints": spec_mod.hints_for_issues(issues),
                "next_step": "先修掉 error 级问题再调一次；不确定某个键怎么写时调 "
                             "factory_blueprint_spec",
            },
            ensure_ascii=False, default=str,
        )

    # 内容指纹当 id：同一份蓝图重复预览得到同一个 id，不会在运行时目录里堆垃圾
    blueprint_id = hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    workdir = staged_dir(blueprint_id)

    def stage() -> dict[str, str]:
        workdir.mkdir(parents=True, exist_ok=True)
        files = {
            "blueprint": workdir / "blueprint.json",
            "spec": workdir / "spec.md",
            "checklist": workdir / "checklist.md",
        }
        files["blueprint"].write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        # ⚠️ 审阅材料在**这一步**生成（审批之前），而不是等 `run_full_workflow` 里再生成。
        # 改造前它只在执行期落盘——用户审批时手里没有那份东西，而它存在的全部目的
        # 就是给他审批用（见 docs/FACTORY-AGENT-GAP.md 问题③）。
        files["spec"].write_text(generate_review_spec(data), encoding="utf-8")
        files["checklist"].write_text(generate_checklist(data), encoding="utf-8")
        return {k: str(v) for k, v in files.items()}

    paths = await _in_thread(stage)
    highlights = await _in_thread(spec_mod.blueprint_highlights, data)
    summary_line = await _in_thread(spec_mod.blueprint_summary_line, data)
    review = await _in_thread(generate_review_spec, data)
    checklist = await _in_thread(generate_checklist, data)

    return json.dumps(
        {
            "valid": True,
            "blueprint_id": blueprint_id,
            "warning_count": len(dumped),
            "warnings": dumped[:20],
            "summary_line": summary_line,
            "highlights": highlights,
            # 给用户看的材料：模型应当把它讲给用户听，让他决定要不要真的创建
            "review_markdown": review,
            "checklist_markdown": checklist,
            "files": paths,
            "next_step": (
                "把 review_markdown 讲给用户听（这是他要审的东西），确认环境账号后调用 "
                f"factory_create(blueprint_id=\"{blueprint_id}\", account_id=…)。"
                "创建会再过一次人工确认门，所以**不要**在用户没表态前就调它"
            ),
        },
        ensure_ascii=False, default=str,
    )


async def list_blueprints(args: ListBlueprintsArgs, ctx: pa.ToolContext) -> str:
    """列出运行时目录里已暂存的蓝图（create 的入参来源）。

    列表里同时会有**两种来源**：本插件预览暂存的（内容指纹当 id）与用户在配置工厂
    页面上传的（uuid）。两条路都保留——用户手里已经有 blueprint.json 时，
    不该被迫再让模型把内容抄一遍。
    """
    root = _entry_path()

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

    workdir = staged_dir(blueprint_id)
    if not (workdir / "blueprint.json").is_file():
        raise ValueError(
            f"暂存蓝图不存在：{blueprint_id}"
            "（先调 factory_preview_blueprint 暂存，或用 factory_list_blueprints 查可用 id）"
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


def _create_summary(args: CreateArgs) -> str:
    """`factory_create` 的确认摘要：**说清将要建出什么**，而不是复述参数。

    通用的 `approvals.summarize()` 口径是"工具描述 + 参数 + 风险级"，
    对这里等于没说——用户要判断的不是"blueprint_id 是多少"，而是"这一按下去会多出什么"。
    所以从暂存的蓝图里现算要点。**读不到就回空串**：摘要退化成通用口径即可，
    绝不能因为读文件失败而让危险操作连确认框都弹不出来。
    """
    from ..services.egmp.writers import spec as spec_mod

    try:
        path = staged_dir(args.blueprint_id) / "blueprint.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return spec_mod.blueprint_summary_line(data)
    except Exception:  # noqa: BLE001 —— 摘要失败不该阻断确认流程
        return ""


PLUGIN = pa.Plugin(
    id=PLUGIN_ID,
    title="配置工厂",
    description="蓝图规格与预览暂存、真机批量创建（akso-auto 能力原生化）",
    version="2.0.0",
    tools=[
        pa.ToolSpec(
            name="factory_blueprint_spec",
            description=(
                "【先看这个】返回 eGMP 配置蓝图的**完整规格**：每个键叫什么、什么类型、"
                "哪些必填、dataType/stepType 等枚举的含义、编码约定、校验规则、"
                "以及一份能直接跑通的最小示例。"
                "规格是从运行期模型生成的，所以永远与校验器一致。"
                "写蓝图前先调一次，比试错便宜得多（返回值较大，一次就够）"
            ),
            args_model=pa.ZERO_ARGS,
            handler=blueprint_spec_tool,
            risk=pa.Risk.READ,
            timeout_s=20,
        ),
        pa.ToolSpec(
            name="factory_preview_blueprint",
            description=(
                "提交一份蓝图：**校验 → 暂存 → 出一份给用户看的审阅清单**，一次完成。"
                "校验通过会返回 blueprint_id（创建时要用）与人话清单——"
                "**把那份清单讲给用户听**，他据此决定要不要真的创建。"
                "有 error 时不暂存，返回的问题清单里附带相关键的写法提示。"
                "只写工作台自己的运行时目录，不碰平台"
            ),
            args_model=PreviewArgs,
            handler=preview_blueprint,
            risk=pa.Risk.READ,
            timeout_s=90,
        ),
        pa.ToolSpec(
            name="factory_list_blueprints",
            description=(
                "列出已暂存的蓝图（id/名称/涉及对象）。含**用户在配置工厂页面上传的**那些，"
                "所以用户说自己传了一份时用它确认"
            ),
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
                "幂等可重跑，但会真的改平台数据，**必须经用户确认后才会执行**。"
                "入参 blueprint_id 来自 factory_preview_blueprint 或用户在页面上的上传。"
                "调它之前先把审阅清单讲给用户听"
            ),
            args_model=CreateArgs,
            handler=create,
            risk=pa.Risk.DANGEROUS,
            timeout_s=900,
            audit_extra=pa.audit_env_key("blueprint_id", "account_id"),
            summary_fn=_create_summary,
        ),
    ],
)


def register(hub: pa.ToolHub) -> None:
    hub.add_plugin(PLUGIN)
