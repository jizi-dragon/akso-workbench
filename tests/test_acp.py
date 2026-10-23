"""ACP v1 适配器：initialize / session/new / session/prompt 的协议形状。

不做真实模型调用：`turn_runner` 注入一个假回合，只验证协议与流式文本映射。
"""

from __future__ import annotations

import asyncio

from workbench.acp import AcpServer, Session, _prompt_text


def _server(messages: list[dict], *, ready: bool = True) -> AcpServer:
    async def fake_turn(session: Session, text: str, emit) -> str:
        session.messages.append({"role": "user", "content": text})
        emit("你")
        emit("好")
        session.messages.append({"role": "assistant", "content": "你好"})
        return "end_turn"

    return AcpServer(write=messages.append, turn_runner=fake_turn,
                     api_key_ready=lambda: ready)


def test_initialize_advertises_protocol_v1() -> None:
    out: list[dict] = []
    asyncio.run(_server(out).handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"}))
    assert out[0]["result"]["protocolVersion"] == 1
    assert out[0]["result"]["agentCapabilities"]["loadSession"] is False


def test_session_new_requires_absolute_existing_cwd(tmp_path) -> None:
    out: list[dict] = []
    server = _server(out)
    asyncio.run(server.handle({"jsonrpc": "2.0", "id": 2, "method": "session/new",
                               "params": {"cwd": "relative"}}))
    assert out[-1]["error"]["code"] == -32602

    asyncio.run(server.handle({"jsonrpc": "2.0", "id": 3, "method": "session/new",
                               "params": {"cwd": str(tmp_path)}}))
    assert out[-1]["result"]["sessionId"]


def test_session_new_without_api_key_is_authentication_required(tmp_path) -> None:
    out: list[dict] = []
    asyncio.run(_server(out, ready=False).handle({
        "jsonrpc": "2.0", "id": 4, "method": "session/new", "params": {"cwd": str(tmp_path)}}))
    assert out[-1]["error"]["code"] == -32000


def test_prompt_streams_chunks_and_returns_stop_reason(tmp_path) -> None:
    out: list[dict] = []
    server = _server(out)
    asyncio.run(server.handle({"jsonrpc": "2.0", "id": 1, "method": "session/new",
                               "params": {"cwd": str(tmp_path)}}))
    session_id = out[-1]["result"]["sessionId"]
    asyncio.run(server.handle({
        "jsonrpc": "2.0", "id": 2, "method": "session/prompt",
        "params": {"sessionId": session_id,
                   "prompt": [{"type": "text", "text": "你好"}]},
    }))
    chunks = [
        item["params"]["update"]["content"]["text"]
        for item in out
        if item.get("method") == "session/update"
    ]
    assert chunks == ["你", "好"]
    assert out[-1]["result"]["stopReason"] == "end_turn"


def test_unknown_method_is_method_not_found() -> None:
    out: list[dict] = []
    asyncio.run(_server(out).handle({"jsonrpc": "2.0", "id": 9, "method": "session/load"}))
    assert out[-1]["error"]["code"] == -32601


def test_prompt_text_extraction() -> None:
    assert _prompt_text([{"type": "text", "text": "a"}, {"type": "image"}]) == "a"
    assert _prompt_text([]) == ""
    assert _prompt_text("not-a-list") == ""
