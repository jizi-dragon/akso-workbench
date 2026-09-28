"""Agent 工具层：工具目录 / 调用面 / 审计 / **危险操作确认门**。

## 单一事实源（2026-09-15 收口）

本文件**不再定义工具**。工具只有一个来源：`plugin_api.ToolHub`（`plugins/` 里的四个
插件装配而成）。因此下面三条路径看到的是同一份工具清单：

    GET  /api/agent/tools    ← hub.catalog()
    GET  /api/agent/plugins  ← hub.plugins
    Agent 对话的 function calling schema ← hub.list_api_for()

改造前这里是**第二个工具表**（四个域工具：login_platform / read_config / run_insight /
browser_status），与 `harness/tools.py` 的三个基础工具并存且互不可见——对话里的模型
只能聊天，看不到任何平台能力；而这四个工具又只对 HTTP 调用方可见。两个表合成一个。

## 危险操作确认门（人机协同）

`Risk.WRITE` / `Risk.DANGEROUS` 的工具**没有单次令牌就不执行**（`approvals.py`）。
两类调用方各走各的路：

- **对话链路**：模型调用 → 被拦 → SSE `approval_required` → 界面上出现「放行/拒绝」
  → 人点放行 → **用户让模型重试同一次调用** → 执行。
  这条链路**不传令牌**（令牌只回给批准它的那一次 HTTP 响应，不回 DOM、不落日志）。
  放行的凭据是服务端内存里的**事实**：`PendingStore.claim_approved()` 按
  「工具 + 参数指纹」认领那条已批准的条目，一次性。详见 `approvals.py` 的
  `gate()` 与 `claim_approved()`（2026-09-20 修 P0-A 时补的）。
- **HTTP 链路**：`POST /api/agent/invoke` 直接返回 `409 + pendingId`，调用方拿
  `pendingId` 去 `POST /api/agent/pending/{id}/approve` 换令牌，再带令牌重投。

令牌**一次性**（用掉即废）、**5 分钟**过期、**绑定工具+参数指纹**（换个参数就重新确认）。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..approvals import current_selection, option_provider, pending_store
from ..plugin_api import Risk, ToolContext, ToolStatus, get_hub

logger = logging.getLogger("workbench.agent")

router = APIRouter(prefix="/api/agent", tags=["agent"])


class InvokeIn(BaseModel):
    tool: str = Field(..., min_length=1, description="工具名（GET /api/agent/tools 查）")
    args: dict[str, Any] = Field(default_factory=dict)
    approvalToken: str = Field("", description="危险操作的单次确认令牌（由 /pending/{id}/approve 换得）")


class RejectIn(BaseModel):
    reason: str = Field("", description="拒绝理由（会回给模型，让它别再试）")


class ApproveIn(BaseModel):
    """放行时提交的答复。

    `value` 只对**选择型确认**有意义（那条待确认带 `options`）——它是用户在卡片上
    点中的那一项的 value。普通确认不带它，带了也会被忽略（`approvals.approve` 里有留痕）。

    ⚠️ 逐字段显式建模是硬要求：`BaseModel` **默认忽略多余字段**，漏一个字段就是
    **静默失效**（AGENTS.md 规则 21，`AccountPatch` 漏 `box` 是已发生事故）。
    """

    value: str = Field("", description="选择型确认里用户点中的那一项（普通确认留空）")


class PickIn(BaseModel):
    """界面发起「挑一个账号」时的入参。

    只有会话 id（挂靠）+ 可选的问题文案——**候选项不由调用方给**：它们由服务端
    按账号表展开。让前端（或模型）把候选传进来，等于把"卡片上写着哪个账号"这件事
    交给一个会漂的来源，而卡片上的 `value` 是要当参数用下去的。
    """

    conversationId: str = Field("", description="这次选择属于哪个会话（空 = 拒绝）")
    question: str = Field("", description="弹窗上问什么，留空用默认文案")


# ---------------------------------------------------------------- 目录


@router.get("/tools")
def list_tools() -> dict:
    """工具目录：插件 → 工具 → 参数，含**风险分级**与装配失败清单。"""
    return get_hub().catalog()


@router.get("/plugins")
def list_plugins() -> dict:
    """插件清单（id / 标题 / 版本 / 贡献的工具数）。

    与 `/tools` 的分工：这里回答"装了哪些插件"，`/tools` 回答"能调什么"。
    `failures` 是**装配期**的失败记录——静默少一个插件比启动崩溃更难查，
    所以它在两个端点里都出现。
    """
    hub = get_hub()
    return {
        "plugins": [
            {
                "id": p.id, "title": p.title, "version": p.version,
                "description": p.description, "tools": [s.name for s in p.tools],
            }
            for p in hub.plugins
        ],
        "failures": hub.failures,
    }


# ---------------------------------------------------------------- 调用


@router.post("/invoke")
async def invoke(body: InvokeIn) -> dict:
    """同步调用一个工具。风险工具无令牌 → 409 + pendingId（不是 400/500）。"""
    hub = get_hub()
    if hub.get(body.tool) is None:
        # 与 `ToolHub.execute()` 里那条同一个事件、同一种记法。
        # 为什么这里也要记：本端点**在路由层就短路了**（下面直接 404），
        # 所以那个"模型/调用方编了一个不存在的工具名"的信号在工具层根本不会产生；
        # 只留一条 404 访问日志的话，两条链路对同一件事的留痕口径就不一致了
        # （对话链路有 `未知工具调用`，HTTP 链路什么都没有）。
        logger.warning("未知工具调用（HTTP）：%s（可用：%s）",
                       body.tool, ", ".join(sorted(s.name for s in hub.specs())))
        raise HTTPException(404, f"未知工具：{body.tool}（GET /api/agent/tools 查清单）")

    invocation = await hub.execute(
        body.tool, body.args,
        ToolContext(source="http", approval_token=body.approvalToken),
    )
    payload: dict[str, Any] = {
        "tool": invocation.tool,
        "ok": not invocation.is_error,
        "status": invocation.status.value,
        "risk": invocation.risk.value,
        "plugin": invocation.plugin_id,
        "auditId": invocation.audit_id,
        "durationMs": invocation.duration_ms,
    }

    if invocation.status is ToolStatus.NEEDS_CONFIRMATION:
        # 实时确认在**对话链路**里由 `harness/loop.py` 就地等答复；
        # 本端点是同步调用，没有"就地等"这回事——所以如实返回 409 + 确认内容，
        # 由调用方去看 GET /api/agent/pending 并自行决定何时 approve。
        # ⚠️ 绝不能在这里**替用户等**：一次 HTTP 请求挂 5 分钟既会被网关掐断，
        # 也会让"谁在等"变得含糊。也绝不能顺手当成"已同意"。
        payload.update({
            "pendingId": invocation.pending_id,
            "confirm": invocation.confirm,
            "message": (
                "这是模型发起的**实时确认**，需要用户答复；本端点不等待答复。"
                f"用 GET /api/agent/pending 查看，POST /api/agent/pending/"
                f"{invocation.pending_id}/approve 表示同意（会按「目标工具 + 参数指纹」"
                "预先批准那一次调用）"
            ),
        })
        raise HTTPException(409, detail=payload)

    if invocation.status is ToolStatus.NEEDS_APPROVAL:
        payload.update({
            "pendingId": invocation.pending_id,
            "summary": invocation.risk_reason,
            "message": (
                "该工具会改变真实状态，需要先确认："
                f"POST /api/agent/pending/{invocation.pending_id}/approve 换令牌，"
                "再带 approvalToken 重投一次"
            ),
        })
        raise HTTPException(409, detail=payload)

    if invocation.is_error:
        # 400 = 参数/前置条件问题（调用方能自己修）；502 = 执行期失败（多半是平台侧）
        code = 400 if invocation.status in (ToolStatus.BAD_ARGS,
                                            ToolStatus.UNKNOWN_TOOL,
                                            ToolStatus.REJECTED) else 502
        payload["error"] = invocation.error
        raise HTTPException(code, detail=payload)

    payload["result"] = _parse_result(invocation.content)
    return payload


def _parse_result(content: str) -> Any:
    """工具结果的 JSON 正文还原成结构（前端才能按字段渲染）；不是 JSON 就原样回。"""
    import json

    try:
        return json.loads(content)
    except (ValueError, TypeError):
        return content


# ---------------------------------------------------------------- 确认门


@router.get("/pending")
def list_pending(status: str = "pending", limit: int = 50) -> dict:
    """待确认的（危险）操作清单。`status` 空串 = 全部状态。"""
    return {"pending": pending_store().list(status=status, limit=min(limit, 200))}


@router.get("/pending/{pending_id}")
def pending_detail(pending_id: str) -> dict:
    item = pending_store().get(pending_id)
    if item is None:
        raise HTTPException(404, f"待确认操作不存在或已过期：{pending_id}")
    return item.public()


@router.post("/pending/{pending_id}/approve")
def approve_pending(pending_id: str, body: ApproveIn | None = None) -> dict:
    """放行：返回**一次性令牌**，由调用方原样带回去重投那次调用。

    令牌不写进审计、不进日志、不回给模型——只回给这次 HTTP 响应。

    `value` 只对**选择型确认**（待确认里带 `options`）有意义：它是用户在卡片上点中的
    那一项。**没有 `value` 就放行不了**——"同意"一个选择题等于什么都没答，
    与其让模型拿着空答复继续，不如让用户再点一次（`approvals.approve` 里做的校验）。
    """
    item = pending_store().approve(pending_id, value=(body.value if body else "") or "")
    if item is None:
        raise HTTPException(
            409,
            "该操作不可放行（不存在 / 已决策 / 已过期），"
            "或者它是一次「选择题」而提交的选项不在候选项里",
        )
    return {
        "pendingId": item.id,
        "approvalToken": item.token,
        "expiresInMs": max(0, item.token_expires_at - item.created_at),
        "summary": item.summary,
        "decidedValue": item.decided_value,
        "retry": "带 approvalToken 重投 POST /api/agent/invoke（或在对话里让模型重试同一次调用）",
    }


@router.get("/selection")
def get_selection(conversationId: str = "") -> dict:
    """这个会话当前选中的账号 / 环境（用户在弹窗卡片上点的那个）。

    界面刷新后用 `conversationId` 问一次，把右下角那张卡片画回来。
    **空 `conversationId` 直接返回 None**，而不是"最近一次选择"——那会让 A 会话
    显示出 B 会话选的账号，比不显示更糟。
    """
    if not conversationId:
        return {"selection": None}
    return {"selection": current_selection(conversationId)}


@router.post("/selection/pick")
def pick_selection(body: PickIn) -> dict:
    """**界面发起**的"挑一个账号"：右下角那张卡的默认入口（2026-09-21 用户要求）。

    > 「在右下角选择卡片的位置，默认开放。意思是用户进入新对话时，就可以立即选择账号。」

    它和模型发起的那条（`request_user_confirmation` + `options_source`）**共用同一张
    待确认表**：候选项同样由服务端按账号表展开、`value` 同样要在候选里、批准路径
    一行不差。区别只有一个——**没有对话循环在等它**（用户还没开口），所以批准之后
    由服务端往会话里补一条 note，让模型下一轮知道用哪个账号。

    没有会话 id 时**拒绝**：一次记不住的"选择"是假的选择——用户选完发现下一轮
    模型还在问，会以为界面坏了。
    """
    conversation_id = str(body.conversationId or "").strip()
    if not conversation_id:
        raise HTTPException(400, "缺少 conversationId：界面发起的选账号必须挂在某个会话上，"
                                 "否则下一次对话读不到它")
    try:
        options = option_provider("accounts")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not options:
        raise HTTPException(400, "账号中心里还没有账号——先去「账号中心」加一个，再回来选")

    spec = next((t for t in get_hub().specs() if t.name == CONFIRM_TOOL), None)
    if spec is None:
        raise HTTPException(500, f"工具 {CONFIRM_TOOL} 不在运行期中枢里")

    question = str(body.question or "").strip() or "要用哪个账号执行？"
    pending_id = pending_store().request_selection(spec, options, conversation_id, question)
    return {
        "pendingId": pending_id,
        "kind": "selection",
        "question": question,
        "details": "选定的账号会成为这次对话的默认执行账号。",
        # 不设超时：没有循环在等它，用户什么时候点都算数
        "timeoutS": 0,
        "options": options,
    }


@router.post("/pending/{pending_id}/reject")
def reject_pending(pending_id: str, body: RejectIn | None = None) -> dict:
    """拒绝：模型会看到拒绝理由，并被要求不要再试。"""
    item = pending_store().reject(pending_id, (body.reason if body else "") or "")
    if item is None:
        raise HTTPException(409, "该操作不可拒绝（不存在 / 已决策 / 已过期）")
    return {"pendingId": item.id, "status": item.status, "summary": item.summary}


# ---------------------------------------------------------------- 审计


@router.get("/audit")
def audit_log(limit: int = 50) -> dict:
    """工具调用审计（HTTP 与对话链路**共用同一张表**）。

    刻意**不** `SELECT *`：`args` 里可能有用户输入的敏感片段，只回摘要与元数据；
    需要细节时按 audit id 单查（见下一条）。
    """
    rows = db.query(
        "SELECT id, tool, plugin, risk, status, result_summary, duration_ms, created_at "
        "FROM agent_audit ORDER BY created_at DESC LIMIT ?",
        (min(limit, 500),),
    )
    return {"audit": rows}


@router.get("/audit/{audit_id}")
def audit_detail(audit_id: str) -> dict:
    row = db.query_one("SELECT * FROM agent_audit WHERE id = ?", (audit_id,))
    if not row:
        raise HTTPException(404, f"审计记录不存在：{audit_id}")
    if row.get("args"):
        import json

        try:
            row["args"] = json.loads(row["args"])
        except (ValueError, TypeError):
            pass
    if row.get("meta"):
        import json

        try:
            row["meta"] = json.loads(row["meta"])
        except (ValueError, TypeError):
            pass
    return row


# ---------------------------------------------------------------- 自检


@router.get("/selfcheck")
def selfcheck() -> dict:
    """启动自检的可查视图：插件数、工具数、**必需工具是否齐**、装配失败、**用量入账失败**。

    与启动期 `verify_required_tools()` 同一判据——这样"打包后静默少一个插件"
    不必等到用户点下去才发现（PLUGIN-UPSTREAM-RESEARCH §R3 的公开失败案例）。

    `usagePersistFailures` 是**进程内累计**的用量入账失败次数（2026-09-24 加）。
    它是"刻意吞掉的失败"唯一能被看见的地方：`UsageStats.persist` 继续吞异常
    （度量不该影响回答），但没有这个计数时，持续失败只表现为"用量曲线莫名其妙变低"。
    """
    from ..cache import usage_persist_failures
    from ..plugin_api import verify_required_tools

    hub = get_hub()
    missing = verify_required_tools(hub, REQUIRED_TOOLS)
    risks = {r.value: 0 for r in Risk}
    for spec in hub.specs():
        risks[spec.risk.value] += 1
    return {
        "ok": not missing and not hub.failures,
        "plugins": len(hub.plugins),
        "tools": len(hub.specs()),
        "riskCounts": risks,
        "requiredTools": list(REQUIRED_TOOLS),
        "missingRequired": missing,
        "failures": hub.failures,
        "usagePersistFailures": usage_persist_failures(),
    }


#: 必需工具：缺任何一个都说明插件没装好（打包漏收 = 装完没功能，PLUGIN-UPSTREAM-RESEARCH §R3）。
REQUIRED_TOOLS = (
    "get_current_time",
    "web_search",
    "insight_run",
    "insight_artifacts",
    "accounts_login",
    "accounts_browser_open",
)

#: 模型主动请求确认的那个工具（声明在 `plugins/builtins.py`）。
#:
#: 界面发起的"挑一个账号"（`POST /selection/pick`）要借它的 `ToolSpec`：待确认表是按
#: spec 建的（工具名 / 插件 / 风险 / 摘要），借同一个 spec 也就**顺带复用了同一套批准
#: 路径与 `value` 校验**——换个入口就松开校验，才是真正危险的做法。
CONFIRM_TOOL = "request_user_confirmation"
