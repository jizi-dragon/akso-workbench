"""记忆插件：让模型**按需**检索/记录事实（不是每轮注入）。

## 为什么是工具而不是自动注入

自动注入会改 provider-visible 消息（I1/I3）；而"哪些事实与这一轮相关"本身需要模型判断。
工具形态把决定权交给模型，也把"用过哪条记忆"留在工具轨迹里。

`memory_add` / `memory_delete` 都是 WRITE + IRREVERSIBLE：它们改持久状态，必须过确认门。
"""

from __future__ import annotations

import asyncio
import json

from pydantic import BaseModel, Field

from .. import memory
from .. import plugin_api as pa

PLUGIN_ID = "memory"


class SearchArgs(BaseModel):
    query: str = Field(..., description="要查的主题/关键词；空串 = 最近记忆")
    scope: str = Field("", description="global / project；空 = 全部")
    limit: int = Field(5, ge=1, le=20, description="返回条数上限")


class AddArgs(BaseModel):
    title: str = Field(..., max_length=memory.MAX_TITLE, description="一句话标题")
    body: str = Field(..., max_length=memory.MAX_BODY, description="事实正文，不含指令")
    scope: str = Field("project", description="global / project")
    type: str = Field("project", description="reference / project / user / feedback")
    keywords: str = Field("", max_length=memory.MAX_KEYWORDS,
                          description="别名/翻译，帮助检索")
    expiresAt: int | None = Field(None, description="毫秒时间戳；null = 不过期")


class DeleteArgs(BaseModel):
    memory_id: str = Field(..., min_length=1, max_length=64, description="记忆 id")


async def search(args: SearchArgs, _ctx: pa.ToolContext) -> str:
    scope = args.scope or None
    rows = await asyncio.to_thread(memory.search, args.query, scope=scope, limit=args.limit)
    if not rows:
        return "没有匹配的长期记忆。"
    return json.dumps({"memories": rows}, ensure_ascii=False)


async def add(args: AddArgs, _ctx: pa.ToolContext) -> str:
    row = await asyncio.to_thread(
        memory.add, title=args.title, body=args.body, scope=args.scope,
        type=args.type, keywords=args.keywords, expires_at=args.expiresAt)
    return json.dumps({"ok": True, "memory": row}, ensure_ascii=False)


async def delete(args: DeleteArgs, _ctx: pa.ToolContext) -> str:
    ok = await asyncio.to_thread(memory.delete, args.memory_id)
    return json.dumps({"ok": ok, "id": args.memory_id}, ensure_ascii=False)


PLUGIN = pa.Plugin(
    id=PLUGIN_ID,
    title="长期记忆",
    description="按需检索/记录事实（不自动注入，不存指令）",
    tools=[
        pa.ToolSpec(
            name="memory_search",
            description=(
                "检索长期记忆里的事实（发布分支、服务约束、用户偏好等）。"
                "**在需要跨会话背景时先查**；返回空不代表事实不存在，只代表没匹配上。"
            ),
            args_model=SearchArgs,
            handler=search,
            risk=pa.Risk.READ,
            trust=pa.Trust.LOCAL,
        ),
        pa.ToolSpec(
            name="memory_add",
            description=(
                "记录一条**事实**（不是指令）。指令写进 AGENTS/CORE-ARCHITECTURE，不要写这里。"
                "会改持久状态，需用户确认。"
            ),
            args_model=AddArgs,
            handler=add,
            risk=pa.Risk.WRITE,
            trust=pa.Trust.LOCAL,
            reversibility=pa.Reversibility.IRREVERSIBLE,
            audit_extra=lambda args: {"memoryScope": args.scope},
        ),
        pa.ToolSpec(
            name="memory_delete",
            description="删除一条长期记忆（按 id）。会改持久状态，需用户确认。",
            args_model=DeleteArgs,
            handler=delete,
            risk=pa.Risk.WRITE,
            trust=pa.Trust.LOCAL,
            reversibility=pa.Reversibility.IRREVERSIBLE,
            audit_extra=lambda args: {"memoryId": args.memory_id},
        ),
    ],
)


def register(hub: pa.ToolHub) -> None:
    hub.add_plugin(PLUGIN)
