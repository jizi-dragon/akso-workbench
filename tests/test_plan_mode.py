"""计划模式：只暴露 READ 工具；写工具被点名也不执行，但必须回填 tool 消息。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from workbench.harness.loop import run_agent_loop
from workbench.harness.tools import default_registry
from workbench.harness.types import AgentConfig


class FakeClient:
    def __init__(self, script: list[dict]) -> None:
        self.script = list(script)
        self.tools_seen: list[list[dict]] = []
        self.model = "fake"

    async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
        self.tools_seen.append(list(tools or []))
        step = self.script.pop(0)
        yield {"type": "delta", "text": step.get("content", "")}
        yield {
            "type": "end",
            "content": step.get("content", ""),
            "tool_calls": step.get("tool_calls") or [],
            "usage": {},
            "finish_reason": "stop",
        }

    async def chat(self, *args, **kwargs):  # pragma: no cover - 计划模式不触发摘要
        return ""


def _call(name: str, args: dict) -> dict:
    return {"id": "call-1", "name": name, "arguments": json.dumps(args)}


def test_plan_mode_hides_write_tools_and_skips_their_calls() -> None:
    client = FakeClient([
        {"content": "", "tool_calls": [_call("memory_add", {"title": "x", "body": "y"})]},
        {"content": "计划：先只读检查，再等确认。"},
    ])
    events = asyncio.run(_collect(run_agent_loop(
        [{"role": "user", "content": "整理一下"}],
        client,
        default_registry(),
        AgentConfig(system_prompt="你是测试助手", plan_mode=True),
        conversation_id="plan-test",
    )))

    offered = {item["function"]["name"] for item in client.tools_seen[0]}
    assert "memory_add" not in offered, f"计划模式仍暴露了写工具：{offered}"
    assert "memory_search" in offered, "计划模式误伤只读工具"

    observations = [event for event in events if event["type"] == "observation"]
    assert observations, "被点名的写工具必须回填 observation"
    assert observations[0]["invocation"]["status"] == "skipped"
    assert "计划模式" in observations[0]["result"]["summary"]
    assert all(event["type"] != "action" for event in events), "计划模式不应真的执行写工具"
    assert any(event["type"] == "final" for event in events)


async def _collect(gen) -> list[dict]:
    return [event async for event in gen]


def test_plan_mode_ui_is_wired() -> None:
    root = Path(__file__).resolve().parents[1] / "workbench" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    chat = (root / "js" / "chat.js").read_text(encoding="utf-8")
    api = (root / "js" / "api.js").read_text(encoding="utf-8")
    css = (root / "css" / "chat-tools.css").read_text(encoding="utf-8")
    assert 'id="btn-plan-mode"' in html and 'aria-pressed="false"' in html
    assert "planMode" in chat and "plan: planMode" in chat
    assert "plan = false" in api and "attachmentIds, plan" in api
    assert '.tool-palette-btn[aria-pressed="true"]' in css
