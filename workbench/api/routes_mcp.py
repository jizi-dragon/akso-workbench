"""MCP 管理面：设置页看的服务器状态 + 手动重连。

## 为什么只有两个端点

- `GET /api/mcp/servers`：纯内存状态，**不打网络、不启动进程**（设置页轮询它没有副作用）；
- `POST /api/mcp/reload`：重读 `mcp.json` 并重建连接（启动 server 可能要几秒，走 `to_thread`）。

"启动 / 停止某个 server"不单独做：配置就是唯一开关（`enabled`），
多一套运行期开关就多一个"界面说停了、实际还在跑"的状态源。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from .. import mcp_registry

logger = logging.getLogger("workbench.routes_mcp")

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


@router.get("/servers")
def list_servers() -> dict:
    """当前 MCP 服务器状态（配置路径、每个 server 的连接/工具数/错误）。"""
    try:
        return mcp_registry.get_registry().status()
    except Exception as exc:  # noqa: BLE001 —— 状态读不出来要说清，不要 500 空白
        raise HTTPException(503, f"MCP 注册表不可用：{exc}") from exc


@router.post("/servers/{name}/reconnect")
async def reconnect_server(name: str) -> dict:
    """手动重连单个 server。**立即尝试**，不走退避。"""
    try:
        state = await asyncio.to_thread(mcp_registry.get_registry().reconnect, name)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"MCP 重连失败：{exc}") from exc
    return {"server": state, **mcp_registry.get_registry().status()}


@router.post("/reload")
async def reload_servers() -> dict:
    """重读配置并重建全部连接。

    ★ 单个 server 失败**不让整次重连失败**：失败会出现在返回的 `servers[].error`
    与 `errors[]` 里，其余 server 照常可用（与 `plugins.load_all` 同一语义）。
    """
    try:
        return await asyncio.to_thread(mcp_registry.get_registry().reload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("MCP 重连失败", exc_info=True)
        raise HTTPException(502, f"MCP 重连失败：{exc}") from exc
