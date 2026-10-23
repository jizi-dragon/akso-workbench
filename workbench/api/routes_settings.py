"""引导数据 + 设置路由（含**系统提示词**与缓存概览）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import cache, permissions
from ..harness import prompts
from ..services import storage
from ..services.settings import all_settings_json, save_deepseek, save_max_steps

router = APIRouter(prefix="/api", tags=["settings"])


class SettingsBody(BaseModel):
    apiKey: str | None = None
    model: str | None = None
    temperature: float | None = None
    maxSteps: int | None = None
    """一次回答里允许的**工具调用**次数（空 = 不动）。越界值会被夹到允许范围内。"""


class PromptBody(BaseModel):
    prompt: str = ""
    """系统提示词正文；**空串 = 恢复基线**（见 harness/prompts.py）。"""


class PermissionsBody(BaseModel):
    """权限策略更新（`None` = 不动该字段）。规则形状：`tool` 或 `tool(subject)`。"""

    mode: str | None = None
    allow: list[str] | None = None
    ask: list[str] | None = None
    deny: list[str] | None = None


@router.get("/bootstrap")
def bootstrap() -> dict:
    metrics = storage.list_metrics(200)
    rated = [m for m in metrics if m["feedback"] is not None]
    adopted = [m for m in rated if m["feedback"] == 1]
    return {
        "conversations": storage.list_conversations(),
        "stats": {
            "qaTotal": len(metrics),
            "adoptRate": round(len(adopted) / len(rated) * 100) if rated else None,
        },
        "settings": {**all_settings_json(), "systemPrompt": prompts.prompt_state(),
                     "permissions": permissions.settings_json()},
        "cache": cache.usage_overview(),
    }


@router.put("/settings")
def put_settings(body: SettingsBody) -> dict:
    save_deepseek(
        api_key=body.apiKey,
        model=body.model,
        temperature=body.temperature,
    )
    save_max_steps(body.maxSteps)
    return {"ok": True, "settings": {**all_settings_json(), "systemPrompt": prompts.prompt_state()}}


# ---------------------------------------------------------------- 权限策略


@router.get("/settings/permissions")
def get_permissions() -> dict:
    """当前权限策略（`deny > ask > allow`）。"""
    return permissions.settings_json()


@router.put("/settings/permissions")
def put_permissions(body: PermissionsBody) -> dict:
    """更新权限策略。**非法规则返回 400**，不静默忽略。"""
    try:
        policy = permissions.save(
            mode=body.mode, allow=body.allow, ask=body.ask, deny=body.deny)
    except permissions.PermissionConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    return policy.to_json()


# ---------------------------------------------------------------- 系统提示词


@router.get("/settings/prompt")
def get_prompt() -> dict:
    """当前生效的提示词 + 基线 + 是否自定义（设置页据此渲染编辑框与「恢复默认」）。"""
    return prompts.prompt_state()


@router.put("/settings/prompt")
def put_prompt(body: PromptBody) -> dict:
    """改提示词（立即生效）。

    ⚠️ 会**让缓存前缀失效一次**：system 消息是每个请求的第一个字节，DeepSeek 要求
    "完整匹配一个缓存前缀单元"才算命中。改设置是低频动作，改完之后的每一轮都重新
    稳定命中——但如果哪天有人想"按轮次动态拼提示词"，那等于每轮都让缓存从头失效。
    """
    prompts.save_prompt(body.prompt)
    return prompts.prompt_state()


# ---------------------------------------------------------------- 缓存


@router.get("/agent/cache")
def cache_overview(days: int = 7, conversationId: str = "") -> dict:
    """近 N 天的 token 用量与**缓存命中率**（命中价是未命中价的 1/50，见 cache.py）。

    `conversationId` 非空 ⇒ 只统计这一段对话：输入框下方那行常驻指标（用户 2026-09-22
    下达的 UI 优化）靠它，也靠它做到"刷新页面之后数字还在"。
    """
    return cache.usage_overview(max(1, min(days, 90)), conversation_id=conversationId)
