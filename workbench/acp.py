"""ACP（Agent Client Protocol）v1 适配器：NDJSON JSON-RPC over stdio。

## 这是什么、不是什么

这是**最小可用**的 ACP agent，供编辑器/DSH 以子进程方式驱动本工作台：

- 支持 `initialize` / `session/new` / `session/prompt` / `session/cancel`；
- 只流式回传**助手文本**（`agent_message_chunk`），不实现 permission / plan / tool 卡片；
- `session/load` / `resume` / `list` / `delete` 暂不支持，返回 `-32601`；
- 每轮的工具调用在服务端执行；写工具会因 `approval_wait_s=0` **快速失败**（不会挂住客户端）。

## 为什么不做权限卡片

ACP 的 `session/request_permission` 要求客户端实现交互；本仓的确认门是桌面 UI 的一部分。
没有卡片时若继续等待，客户端会挂死。所以 v1 明确"只读优先"，并把写工具按拒绝处理。
这比"看起来支持 ACP、实际一按写就僵住"诚实。

## 启动

    python -m workbench.acp

stdout 只放协议消息，诊断走 stderr。
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

PROTOCOL_VERSION = 1


@dataclass
class Session:
    id: str
    cwd: str
    messages: list[dict[str, str]] = field(default_factory=list)
    cancel: asyncio.Event = field(default_factory=asyncio.Event)


TurnRunner = Callable[[Session, str, Callable[[str], None]], Awaitable[str]]


class AcpServer:
    def __init__(self, *, write: Callable[[dict[str, Any]], None] | None = None,
                 turn_runner: TurnRunner | None = None,
                 api_key_ready: Callable[[], bool] | None = None) -> None:
        self._sessions: dict[str, Session] = {}
        self._write = write or self._stdout_write
        self._turn_runner = turn_runner or self._default_turn
        self._api_key_ready = api_key_ready or self._default_api_key_ready

    # ------------------------------------------------------------ 传输
    @staticmethod
    def _stdout_write(message: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def _result(self, req_id: Any, result: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "id": req_id, "result": result})

    def _error(self, req_id: Any, code: int, message: str) -> None:
        self._write({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    # ------------------------------------------------------------ 方法
    async def handle(self, message: dict[str, Any]) -> None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            self._error(None, -32600, "Invalid Request")
            return
        method = str(message.get("method") or "")
        req_id = message.get("id")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            self._error(req_id, -32602, "params 必须是对象")
            return

        if method == "initialize":
            self._result(req_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "agentCapabilities": {
                    "loadSession": False,
                    "promptCapabilities": {"image": False, "embeddedContext": False},
                },
                "authMethods": [],
            })
        elif method == "session/new":
            if not self._api_key_ready():
                self._error(req_id, -32000, "Authentication required：请先在设置中配置 DeepSeek API Key")
                return
            cwd = str(params.get("cwd") or "").strip()
            if not cwd or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
                self._error(req_id, -32602, "cwd 必须是已存在的绝对路径")
                return
            session = Session(id=uuid.uuid4().hex[:16], cwd=cwd)
            self._sessions[session.id] = session
            self._result(req_id, {"sessionId": session.id, "cwd": cwd})
        elif method == "session/prompt":
            session = self._sessions.get(str(params.get("sessionId") or ""))
            if session is None:
                self._error(req_id, -32602, "未知 sessionId")
                return
            text = _prompt_text(params.get("prompt"))
            if not text:
                self._error(req_id, -32602, "prompt 里没有文本内容")
                return
            session.cancel.clear()
            stop = "end_turn"
            try:
                stop = await self._turn_runner(session, text, self._chunk(session.id))
            except asyncio.CancelledError:
                stop = "cancelled"
            except Exception as exc:  # noqa: BLE001 —— 一轮失败不能杀掉 ACP 进程
                self._notify("session/update", {
                    "sessionId": session.id,
                    "update": {"sessionUpdate": "agent_message_chunk",
                               "content": {"type": "text", "text": f"\n[错误] {type(exc).__name__}: {exc}"}},
                })
                stop = "refusal"
            self._result(req_id, {"stopReason": stop})
        elif method == "session/cancel":
            session = self._sessions.get(str(params.get("sessionId") or ""))
            if session is not None:
                session.cancel.set()
            # 通知无 id：不回复
        else:
            self._error(req_id, -32601, f"Method not found: {method}")

    def _chunk(self, session_id: str) -> Callable[[str], None]:
        def emit(text: str) -> None:
            if not text:
                return
            self._notify("session/update", {
                "sessionId": session_id,
                "update": {"sessionUpdate": "agent_message_chunk",
                           "content": {"type": "text", "text": text}},
            })
        return emit

    # ------------------------------------------------------------ 真实回合
    @staticmethod
    def _default_api_key_ready() -> bool:
        from .services.settings import load_deepseek

        return bool(load_deepseek().api_key)

    async def _default_turn(self, session: Session, text: str,
                            emit: Callable[[str], None]) -> str:
        from .harness.loop import run_agent_loop
        from .harness.prompts import build_system_prompt
        from .harness.types import AgentConfig
        from .hub import get_hub
        from .services.deepseek import DeepSeekClient
        from .services.settings import load_deepseek, load_max_steps

        cfg = load_deepseek()
        session.messages.append({"role": "user", "content": text})
        client = DeepSeekClient(cfg.api_key, model=cfg.model)
        config = AgentConfig(
            temperature=cfg.temperature,
            system_prompt=build_system_prompt(),
            max_steps=load_max_steps(),
            approval_wait_s=0.0,  # 没有 ACP 权限卡片：写工具快速失败，不挂客户端
        )
        answer = ""
        stop = "end_turn"
        async for event in run_agent_loop(
            list(session.messages), client, get_hub(), config,
            conversation_id=session.id,
            should_stop=session.cancel.is_set,
        ):
            kind = event.get("type")
            if kind == "token":
                answer += str(event.get("text") or "")
                emit(str(event.get("text") or ""))
            elif kind == "final":
                answer = str(event.get("content") or answer)
            elif kind == "cancelled":
                stop = "cancelled"
            elif kind == "error":
                stop = "refusal"
        if answer:
            session.messages.append({"role": "assistant", "content": answer})
        return stop

    async def serve(self) -> None:
        """读 stdin 的 NDJSON；一行一条消息。EOF 退出。"""
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self._error(None, -32700, "Parse error")
                continue
            await self.handle(message)


def _prompt_text(prompt: Any) -> str:
    if not isinstance(prompt, list):
        return ""
    parts: list[str] = []
    for item in prompt:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(part for part in parts if part)


async def main() -> None:
    await AcpServer().serve()


if __name__ == "__main__":
    asyncio.run(main())
