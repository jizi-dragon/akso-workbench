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
  → 人点放行 → 模型重试同一次调用（原样带 `approvalToken`）→ 执行。
- **HTTP 链路**：`POST /api/agent/invoke` 直接返回 `409 + pendingId`，调用方拿
  `pendingId` 去 `POST /api/agent/pending/{id}/approve` 换令牌，再带令牌重投。

令牌**一次性**（用掉即废）、**5 分钟**过期、**绑定工具+参数指纹**（换个参数就重新确认）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..approvals import pending_store
from ..plugin_api import Risk, ToolContext, ToolStatus, get_hub

router = APIRouter(prefix="/api/agent", tags=["agent"])


class InvokeIn(BaseModel):
    tool: str = Field(..., min_length=1, description="工具名（GET /api/agent/tools 查）")
    args: dict[str, Any] = Field(default_factory=dict)
    approvalToken: str = Field("", description="危险操作的单次确认令牌（由 /pending/{id}/approve 换得）")


class RejectIn(BaseModel):
    reason: str = Field("", description="拒绝理由（会回给模型，让它别再试）")


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
def approve_pending(pending_id: str) -> dict:
    """放行：返回**一次性令牌**，由调用方原样带回去重投那次调用。

    令牌不写进审计、不进日志、不回给模型——只回给这次 HTTP 响应。
    """
    item = pending_store().approve(pending_id)
    if item is None:
        raise HTTPException(409, "该操作不可放行（不存在 / 已决策 / 已过期）")
    return {
        "pendingId": item.id,
        "approvalToken": item.token,
        "expiresInMs": max(0, item.token_expires_at - item.created_at),
        "summary": item.summary,
        "retry": "带 approvalToken 重投 POST /api/agent/invoke（或在对话里让模型重试同一次调用）",
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
    """启动自检的可查视图：插件数、工具数、**必需工具是否齐**、装配失败。

    与启动期 `verify_required_tools()` 同一判据——这样"打包后静默少一个插件"
    不必等到用户点下去才发现（PLUGIN-UPSTREAM-RESEARCH §R3 的公开失败案例）。
    """
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
    }


#: 必需工具：缺任何一个都说明插件没装好（打包漏收 = 装完没功能，PLUGIN-UPSTREAM-RESEARCH §R3）。
REQUIRED_TOOLS = (
    "get_current_time",
    "web_search",
    "insight_run",
    "insight_artifacts",
    "factory_validate_blueprint",
    "factory_create",
    "accounts_login",
    "accounts_browser_open",
)
