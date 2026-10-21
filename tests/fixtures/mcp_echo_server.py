"""最小 MCP stdio server：给 `tests/test_mcp.py` 当被连接的对端。

它不是产品代码，也不进打包。行为刻意只覆盖本仓客户端用到的协议面：
`initialize` / `notifications/initialized` / `tools/list` / `tools/call`。

工具：
- `echo(text)`            → 原样返回文本
- `add(a, b)`             → 返回 a+b
- `env_probe()`           → 返回环境里有没有 `MCP_FAKE_SECRET`
- `fail()`                → `isError: true`
- `slow(seconds)`         → 睡一会儿再返回（测超时）

★ 它把"收到 initialize 之前就发请求"也当成错误：客户端握手顺序错了要能看见，
而不是被 server 默默容忍。
"""

from __future__ import annotations

import json
import os
import sys
import time

INITIALIZED = False

TOOLS = [
    {
        "name": "echo",
        "description": "原样返回 text",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "要回显的文本"}},
            "required": ["text"],
        },
    },
    {
        "name": "add",
        "description": "两个整数相加",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "integer", "description": "第一个数"},
                "b": {"type": "integer", "description": "第二个数"},
            },
            "required": ["a", "b"],
        },
    },
    {
        "name": "env_probe",
        "description": "报告 MCP_FAKE_SECRET 是否被传进来",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "fail",
        "description": "总是返回错误",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "slow",
        "description": "睡一会儿",
        "inputSchema": {
            "type": "object",
            "properties": {"seconds": {"type": "number"}},
            "required": ["seconds"],
        },
    },
    {
        "name": "notify_change",
        "description": "发一条 tools/list_changed 通知再返回",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _text(value: str, *, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": value}], "isError": is_error}


def _handle(message: dict) -> None:
    global INITIALIZED
    method = message.get("method")
    req_id = message.get("id")
    if method == "initialize":
        _send({
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-mcp", "version": "1.0.0"},
            },
        })
        return
    if method == "notifications/initialized":
        INITIALIZED = True
        return
    if method == "ping":
        _send({"jsonrpc": "2.0", "id": req_id, "result": {}})
        return
    if not INITIALIZED:
        _send({"jsonrpc": "2.0", "id": req_id,
               "error": {"code": -32002, "message": "not initialized"}})
        return
    if method == "tools/list":
        _send({"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}})
        return
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            if name == "echo":
                _send({"jsonrpc": "2.0", "id": req_id, "result": _text(str(args.get("text", "")))})
            elif name == "add":
                total = int(args.get("a", 0)) + int(args.get("b", 0))
                _send({"jsonrpc": "2.0", "id": req_id, "result": _text(str(total))})
            elif name == "env_probe":
                present = "yes" if os.environ.get("MCP_FAKE_SECRET") else "no"
                _send({"jsonrpc": "2.0", "id": req_id, "result": _text(present)})
            elif name == "fail":
                _send({"jsonrpc": "2.0", "id": req_id,
                       "result": _text("这个东西坏了", is_error=True)})
            elif name == "slow":
                time.sleep(float(args.get("seconds", 0)))
                _send({"jsonrpc": "2.0", "id": req_id, "result": _text("醒了")})
            elif name == "notify_change":
                _send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
                _send({"jsonrpc": "2.0", "id": req_id, "result": _text("已通知")})
            else:
                _send({"jsonrpc": "2.0", "id": req_id,
                       "error": {"code": -32602, "message": f"unknown tool {name}"}})
        except Exception as exc:  # noqa: BLE001
            _send({"jsonrpc": "2.0", "id": req_id,
                   "error": {"code": -32603, "message": str(exc)}})
        return
    if req_id is not None:
        _send({"jsonrpc": "2.0", "id": req_id,
               "error": {"code": -32601, "message": f"unknown method {method}"}})


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict):
            _handle(message)


if __name__ == "__main__":
    main()
