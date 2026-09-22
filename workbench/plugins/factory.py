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


class ResolveArgs(BaseModel):
    account_id: str = pa.Arg("凭证来源账号 id（决定查哪个平台环境）")
    query: str = pa.Arg("用户说的对象 **code 或名称**（例如 `test_0903__c` / `测试09-03`）")


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


async def create_precheck(args: CreateArgs, ctx: pa.ToolContext) -> str:
    """`factory_create` 的**前置检查**：在问用户放行**之前**跑（决策卡 G3）。

    ## 为什么要有它

    工厂的预检（`orchestrate.precheck_blueprint`）是**确定性**的：对象不存在、而平台上存在
    相近对象时，`run_full_workflow` 会**拒绝写入**（防"打错一个字 ⇒ 静默多出一个空对象"，
    [ADR-0018](../../docs/adr/0018-no-silent-object-creation.md)）。

    而那道检查原本在**确认门之后**的 handler 里跑 ⇒ 用户被要求为一次**必然失败**的写操作
    授权：点完「放行」，拿到的还是同一个拒绝理由（2026-09-22 15:08 真机实测：同一份蓝图
    连着问了两次放行，两次都白点；见决策简报批五 G3）。

    现在把它提前到门前跑一遍：**注定被拒的调用不会被问、也不会执行**，
    拒绝理由直接成为模型的观测（含"要新建就在对象上写 `createIfMissing: true`"这条出路）。

    ## 三条纪律

    - **与真正执行时用同一段代码**（`precheck_blueprint`），不是第二套规则——
      否则就会出现"门前说不许、门后说可以"这种最难查的分歧；
    - **只读**：几次对象查询，不写平台、不落盘；
    - **失败即放行**：读不到蓝图、账号不可用、平台查询失败时返回空串，
      让这次调用照常走确认门 —— 前置检查只买"省一次白问"，不承担拦截责任
      （拦截由确认门与 handler 里那一遍负责）。
    """
    from ..services.egmp import orchestrate
    from ..services.egmp.client import egmp_client_for_account

    path = staged_dir(str(args.blueprint_id or "").strip()) / "blueprint.json"
    if not path.is_file():
        return ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""

    def _check() -> str:
        with egmp_client_for_account(args.account_id) as client:
            precheck = orchestrate.precheck_blueprint(client, raw)
        blocking = list(precheck.get("needsObjectChoice") or [])
        if not blocking:
            return ""
        lines = [f"找不到对象 {item['code']}，但平台上存在相近的："
                 + "、".join(f"{c['code']}（{c['name']}）" for c in item["candidates"])
                 for item in blocking]
        return ("预检查未通过（**这次调用没有被问放行、也没有写入任何东西**）："
                + "；".join(lines)
                + " ⇒ 请让用户选择：① 改成上面某个对象重跑；"
                  "② 用户确认**确实要新建** ⇒ 在该对象上写 `createIfMissing: true` 再重跑；"
                  "③ 或者把 code 改成不与既有对象雷同的。")

    return await _in_thread(_check)


async def latest_jobs(_: BaseModel, ctx: pa.ToolContext) -> str:
    """最近工厂任务（先看有没有跑过、跑成没跑成）。"""
    rows = await _in_thread(
        db.query,
        "SELECT id, command, status, created_at, updated_at FROM blueprint_jobs "
        "ORDER BY created_at DESC LIMIT 10",
    )
    return json.dumps({"jobs": rows}, ensure_ascii=False, default=str)


# ---------------------------------------------------------------- 插件声明


async def resolve_object(args: ResolveArgs, ctx: pa.ToolContext) -> str:
    """查"用户说的这个对象在平台上到底存不存在"；不存在就**列出相近的**交人选择。

    ## 这条是用户 2026-09-22 定的规则（原话）

    > 「当用户描述的对象不存在时，要给用户提出找不到该对象，并列举出相似对象给用户选择，
    > 并询问用户若都不存在时，是否需要新建。」

    ## 为什么必须有它（实测风险）

    工厂的对象步骤是**幂等**的：code 存在就复用、不存在就**建一个新的**。
    于是"用户把 code 打错一个字"的后果不是报错，而是**静默多出一个空对象**，
    而用户以为改的是他那个。2026-09-22 我手工踩过一次这个坑（跑之前先只读核对才没出事）。

    ## 返回值的用法

    它返回的就是**给用户看的话术素材**：`found` 有值时按它的 code 写蓝图；
    `candidates` 有值时必须**先把候选念给用户**、让他选；都没有时**先问是否要新建**。
    管道层还有第二道闸：`run_full_workflow` 在"对象不存在但存在相近对象"时**拒绝写入**
    （见 `orchestrate.precheck_blueprint` 的 `needsObjectChoice`）。
    """
    from ..services.egmp import orchestrate
    from ..services.egmp.client import egmp_client_for_account
    from ..services.egmp.queries import GaiaQueries

    def _run() -> dict[str, Any]:
        with egmp_client_for_account(args.account_id) as client:
            info = GaiaQueries(client).get_object_info(args.query)
            if info:
                return {
                    "found": {"code": str(info.get("code") or args.query),
                              "name": str(info.get("name") or ""),
                              "id": str(info.get("id") or "")},
                    "candidates": [],
                    "guidance": "对象存在 ⇒ 蓝图里用这个 code，工厂会**复用**它、不会新建。",
                }
            candidates = orchestrate.similar_object_codes(client, args.query)
        if candidates:
            listing = "、".join(f"{c['code']}（{c['name']}）" for c in candidates)
            return {
                "found": None, "candidates": candidates,
                "guidance": (f"平台上**找不到对象 {args.query}**。相近的有：{listing}。"
                             "⇒ 请把这几个念给用户，问他要改的是哪一个；"
                             "**若都不是**，再问他是否要**新建**一个。"
                             "★ 用户明确说要新建之后：在蓝图那个对象上写 "
                             "`createIfMissing: true` 再重跑——预检**看不见对话**，"
                             "口头/界面上的确认对它无效，只有这个键能让它放行。"),
            }
        return {
            "found": None, "candidates": [],
            "guidance": (f"平台上**找不到对象 {args.query}**，也没有相近的。"
                         "⇒ 先别建：请用户复核对象的 code/名称（最好让他把界面上的 code 贴过来），"
                         "并问他是不是要**新建**；他确认后，在蓝图那个对象上写 "
                         "`createIfMissing: true` 再走创建。"),
        }

    return json.dumps(await _in_thread(_run), ensure_ascii=False)


def _account_label(account_id: str) -> str:
    """账号 id → 「环境名 · 用户名」（**绝不带凭据**，只这两列）。读不到就回空串。"""
    try:
        row = db.query_one(
            "SELECT a.username AS username, e.name AS env_name "
            "FROM account a JOIN platform_env e ON e.id = a.env_id WHERE a.id = ?",
            (account_id,),
        ) or {}
    except Exception:  # noqa: BLE001 —— 摘要里少一行环境名，绝不能影响确认流程
        return ""
    env = str(row.get("env_name") or "").strip()
    user = str(row.get("username") or "").strip()
    return " · ".join(p for p in (env, user) if p)


def _create_summary(args: CreateArgs) -> str:
    """`factory_create` 的确认摘要：**第一行是标题，`·` 开头的行是要点**。

    形状约定见 `plugin_api.ToolSpec.summary_fn`（弹窗按它排版）。

    ## 为什么是"标题 + 要点"而不是一句话（2026-09-22 用户下达）

    > 「弹出的蓝图/审核界面，请你不要放一堆文字上去，请设计排版，言简意赅，
    >  用户在执行时没有时间去仔细看 UI 上比蚂蚁还小的字」

    确认框是**一次真实写入的闸门**，用户只有几秒钟做判断。所以这里只给：
    标题（在哪个环境、建什么）+ 每行一件事（编码 / 字段 / 布局 / 引擎不执行的部分），
    长清单一律"数量 + 前几个名字"，剩下的留在审阅材料 `review_markdown` 里。
    """
    from ..services.egmp.writers import spec as spec_mod

    try:
        path = staged_dir(args.blueprint_id) / "blueprint.json"
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 —— 摘要失败不该阻断确认流程
        return ""
    try:
        objects = spec_mod.to_object_blueprints(data)
        h = spec_mod.blueprint_highlights(data)
    except Exception:  # noqa: BLE001
        return ""

    def _name(obj: dict[str, Any]) -> str:
        name = str(obj.get("name") or "").strip()
        code = str(obj.get("code") or "").strip()
        return f"{name}（{code}）" if name and code else (name or code or "（未命名）")

    def _few(items: list[str], limit: int = 3) -> str:
        return "、".join(items[:limit]) + ("…" if len(items) > limit else "")

    # ── 标题：在哪个环境、建什么（一行）
    env = _account_label(args.account_id)
    if len(objects) == 1:
        head = f"新建对象 {_name(objects[0])}"
    else:
        head = f"新建 {len(objects)} 个对象：{_few([_name(o) for o in objects])}"
    points: list[str] = [f"· 环境：{env}"] if env else []

    # ── 要点：每个对象一行（编码 + 字段/布局/工作流的量）
    for obj in objects[:3]:
        bits: list[str] = []
        fields = [str(f.get("code") or "") for f in obj.get("fields") or []]
        if fields:
            bits.append(f"字段 {len(fields)} 个（{_few(fields, 2)}）")
        layout_bits: list[str] = []
        form = obj.get("formLayout") or {}
        if form:
            layout_bits.append(f"表单 {len(form.get('sections') or [])} 个部分")
        list_layout = obj.get("listLayout") or {}
        if list_layout:
            layout_bits.append(f"列表 {len(list_layout.get('columns') or [])} 列")
        if layout_bits:
            bits.append("布局 " + " + ".join(layout_bits))
        lifecycle = obj.get("lifecycle") or {}
        statuses = len(lifecycle.get("statuses") or []) + len(lifecycle.get("renameStatuses") or [])
        if statuses:
            bits.append(f"状态变更 {statuses} 项")
        if obj.get("workflows"):
            bits.append(f"工作流 {len(obj['workflows'])} 条")
        prefix = "· " if len(objects) == 1 else f"· {_name(obj)}："
        points.append(prefix + (("；".join(bits)) if bits else "（只建对象，无字段/布局）"))
    if len(objects) > 3:
        points.append(f"· 另有 {len(objects) - 3} 个对象（见审阅清单）")

    # ── 只承诺引擎真的会做的事：菜单/规则/权限/报表模板一律标成"要人工配"
    if h.get("needsManual"):
        points.append("· **引擎不执行**（需人工配）：" + "、".join(h["needsManual"]))

    return "\n".join([head, *points[:5]])


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
            name="factory_resolve_object",
            description=(
                "**用户说的对象在平台上到底存不存在**——按 code 或名称查。"
                "存在：回 id/name（工厂会复用它）；不存在：回**相近对象清单**，"
                "此时必须先把候选念给用户、让他选，**都不对再问他是否要新建**；"
                "用户确认新建后，在蓝图那个对象上写 `createIfMissing: true` 再重跑。"
                "★ 规则（用户 2026-09-22 定）：对象不存在时**不许静默新建**——"
                "工厂的对象步骤是幂等的，code 打错一个字不会报错，只会静默多出一个空对象。"
                "写蓝图 / 调 factory_create **之前**先查一次，比事后在平台上删对象便宜"
            ),
            args_model=ResolveArgs,
            handler=resolve_object,
            risk=pa.Risk.READ,
            timeout_s=60,
            # 维度里必须带 `query`：本工具的**全部意义**就是"模型问的是哪个对象"，
            # 而它原来的维度只有 account_id ⇒ 事后复盘时审计里查不到查了什么，
            # 只能翻 L0 日志里的 httpx URL 反推（2026-09-22 复盘 `测试09-04` 那次实测踩到）。
            # 纯增留痕，不改行为。`audit_extra` 的口径见 plugin_api.ToolSpec.audit_extra。
            audit_extra=pa.audit_env_key("account_id", "query"),
        ),
        pa.ToolSpec(
            name="factory_create",
            description=(
                "【危险】在真实平台上按蓝图批量创建对象/字段/选项集/生命周期/工作流/布局/菜单。"
                "幂等可重跑，但会真的改平台数据，**必须经用户确认后才会执行**。"
                "入参 blueprint_id 来自 factory_preview_blueprint 或用户在页面上的上传。"
                "调它之前先把审阅清单讲给用户听。"
                "★ 若蓝图里的对象在平台上不存在、却又存在相近对象，本工具会**拒绝写入**（零写入）"
                "并回候选清单——那是防「code 打错一个字 ⇒ 静默多出一个空对象」。"
                "用户确认**确实要新建**之后，在蓝图该对象上写 `createIfMissing: true` 再重跑即可放行"
            ),
            args_model=CreateArgs,
            handler=create,
            risk=pa.Risk.DANGEROUS,
            timeout_s=900,
            precheck=create_precheck,
            audit_extra=pa.audit_env_key("blueprint_id", "account_id"),
            summary_fn=_create_summary,
        ),
    ],
)


def register(hub: pa.ToolHub) -> None:
    hub.add_plugin(PLUGIN)
