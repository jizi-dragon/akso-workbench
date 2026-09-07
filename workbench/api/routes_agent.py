"""Agent 工具层（阶段 3E）：mogul harness 风格的工具注册 + 审计。

目标：自然语言 → 登录 / 读配置 / 写配置 / 查知识 全链路的工具底座。
本文件提供工具的 HTTP 调用面与审计；与 LLM 的对话循环由前端复用
routes_chat 的 SSE 流（工具结果作为上下文块注入）。

工具清单（命名对齐 docs/模块契约.md §8）：
- search_knowledge   本地三层混合检索（fork mogul retrieval）
- login_platform     akso-cc login（子进程，验证连通 + 预热 token）
- read_config        eGMP 对象元数据只读（egmp.client，阶段 3A 内核）
- run_insight        akso-cc understand（子进程封装）
- browser_status     托管浏览器会话状态墙
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..services.storage import now_ms

router = APIRouter(prefix="/api/agent", tags=["agent"])


class InvokeIn(BaseModel):
    tool: str = Field(..., min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- 工具实现


def _tool_search_knowledge(args: dict[str, Any]) -> dict:
    import asyncio

    from ..services import retrieval

    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("query 不能为空")
    hits = asyncio.run(retrieval.search_knowledge(query, limit=int(args.get("limit") or 5)))
    return {"hits": hits}


def _tool_login_platform(args: dict[str, Any]) -> dict:
    """原生登录验证（egmp client，55min token 缓存）。"""
    from ..services import accounts as accounts_svc
    from ..services.egmp.client import login

    account_id = str(args.get("account_id") or "")
    if not account_id:
        raise ValueError("account_id 不能为空")
    account = accounts_svc.get_account(account_id)
    if not account:
        raise ValueError(f"账号不存在：{account_id}")
    base_url = account.get("env_base_url") or ""
    if not base_url:
        raise ValueError(f"账号 {account['username']} 所属平台环境未配置 baseUrl")
    password = accounts_svc.reveal_password(account_id)
    token = login(base_url, account["username"], password, str(account.get("env_id") or ""))
    return {"ok": True, "baseUrl": base_url, "tokenHead": token[:8] + "…"}


def _tool_read_config(args: dict[str, Any]) -> dict:
    from ..services.egmp import client as egmp_client

    account_id = str(args.get("account_id") or "")
    code = str(args.get("object") or "").strip()
    if not account_id or not code:
        raise ValueError("account_id 与 object 不能为空")
    with egmp_client.egmp_client_for_account(account_id) as http:
        meta = http.get_object(code)
    return {"object": code, "meta": meta}


def _tool_run_insight(args: dict[str, Any]) -> dict:
    """原生 understand 报告（egmp.insight）。"""
    from . import routes_insight

    account_id = str(args.get("account_id") or "")
    objects = str(args.get("objects") or "")
    if not account_id or not objects:
        raise ValueError("account_id 与 objects 不能为空")
    req = routes_insight.InsightRequest(command="understand", account_id=account_id, objects=objects)
    job_id, workdir = routes_insight._start_job(req)
    outcome: dict[str, Any] = {}
    try:
        outcome = routes_insight._run_job(req, workdir)
    except Exception as exc:  # noqa: BLE001 —— 工具层转译为 ValueError 语义
        raise ValueError(f"洞察执行失败：{exc}") from exc
    finally:
        routes_insight._finish_job(job_id, outcome.get("status", "failed"))
    return {"job_id": job_id, **outcome}


def _tool_browser_status(args: dict[str, Any]) -> dict:
    from ..services.browser_pool import get_pool

    account_id = args.get("account_id")
    if account_id:
        snap = get_pool().session(str(account_id))
        return {"session": snap}
    return {"sessions": get_pool().list_sessions()}


TOOLS: dict[str, dict[str, Any]] = {
    "search_knowledge": {
        "fn": _tool_search_knowledge,
        "description": "本地知识库三层混合检索（精确/关键词/向量），返回带来源与更新时间的文档块",
        "args": {"query": "string（必填）", "limit": "int，默认 5"},
    },
    "login_platform": {
        "fn": _tool_login_platform,
        "description": "用统一账号库的账号跑 akso-cc login，验证平台连通并预热 token",
        "args": {"account_id": "string（必填）"},
    },
    "read_config": {
        "fn": _tool_read_config,
        "description": "只读读取 eGMP 对象元数据（egmp 内核，httpx）",
        "args": {"account_id": "string（必填）", "object": "对象编码，如 capa_plan__c"},
    },
    "run_insight": {
        "fn": _tool_run_insight,
        "description": "跑 akso-cc understand 生成对象三层理解报告（子进程封装）",
        "args": {"account_id": "string（必填）", "objects": "逗号分隔对象编码"},
    },
    "browser_status": {
        "fn": _tool_browser_status,
        "description": "托管浏览器会话状态墙（单账号或全部）",
        "args": {"account_id": "string，可选"},
    },
}


def _audit(tool: str, args: dict[str, Any], status: str, summary: str, duration_ms: int) -> None:
    try:
        db.execute(
            "INSERT INTO agent_audit (id, tool, args, status, result_summary, duration_ms, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex[:12], tool,
             json.dumps(args, ensure_ascii=False)[:2000], status,
             summary[:1000], duration_ms, now_ms()),
        )
    except Exception:  # noqa: BLE001 —— 审计失败不影响工具结果
        pass


@router.get("/tools")
def list_tools() -> dict:
    return {
        "tools": [
            {"name": name, "description": meta["description"], "args": meta["args"]}
            for name, meta in TOOLS.items()
        ]
    }


@router.post("/invoke")
def invoke(body: InvokeIn) -> dict:
    meta = TOOLS.get(body.tool)
    if meta is None:
        raise HTTPException(404, f"未知工具：{body.tool}（GET /api/agent/tools 查清单）")
    fn: Callable[[dict[str, Any]], dict] = meta["fn"]
    started = time.perf_counter()
    try:
        result = fn(body.args)
        _audit(body.tool, body.args, "ok",
               json.dumps(result, ensure_ascii=False, default=str)[:500],
               int((time.perf_counter() - started) * 1000))
        return {"tool": body.tool, "ok": True, "result": result}
    except HTTPException:
        raise
    except (ValueError, TypeError) as exc:
        _audit(body.tool, body.args, "bad_request", str(exc),
               int((time.perf_counter() - started) * 1000))
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        _audit(body.tool, body.args, "error", str(exc),
               int((time.perf_counter() - started) * 1000))
        raise HTTPException(502, f"工具执行失败：{exc}") from exc


@router.get("/audit")
def audit_log(limit: int = 50) -> dict:
    rows = db.query("SELECT * FROM agent_audit ORDER BY created_at DESC LIMIT ?", (min(limit, 500),))
    return {"audit": rows}
