"""DeepSeek API 客户端（httpx 异步，SSE 流式 + function calling + JSON 模式）。

事件契约（stream_chat 依次 yield）：
  {"type": "delta", "text"}                    正文增量
  {"type": "end", "content", "tool_calls"}     流结束；tool_calls 为聚合后的完整调用列表
错误统一抛 DeepSeekError（带 HTTP 状态码），由上层转为事件/HTTP 响应。
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
_STREAM_TIMEOUT = httpx.Timeout(120.0, connect=15.0)
_CHAT_TIMEOUT = httpx.Timeout(90.0, connect=15.0)


class DeepSeekError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _map_error(status: int, body: str) -> DeepSeekError:
    message = body
    try:
        parsed = json.loads(body)
        message = parsed.get("error", {}).get("message") or body
    except (ValueError, AttributeError):
        pass
    if status == 401:
        return DeepSeekError("API Key 无效或未授权", status)
    if status == 402:
        return DeepSeekError("账户余额不足", status)
    if status == 429:
        return DeepSeekError("请求过于频繁或额度不足，请稍后重试", status)
    if status >= 500:
        return DeepSeekError("DeepSeek 服务异常，请稍后重试", status)
    return DeepSeekError(message or f"请求失败（{status}）", status)


def _to_api_message(msg: dict) -> dict:
    if msg.get("role") == "tool":
        return {"role": "tool", "tool_call_id": msg.get("tool_call_id"), "content": msg.get("content")}
    if msg.get("role") == "assistant" and msg.get("tool_calls"):
        return {
            "role": "assistant",
            "content": msg.get("content") or None,
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                }
                for tc in msg["tool_calls"]
            ],
        }
    return {"role": msg.get("role"), "content": msg.get("content")}


class DeepSeekClient:
    def __init__(self, api_key: str, base_url: str | None = None, model: str | None = None):
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or DEFAULT_MODEL

    async def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[dict]:
        """流式对话，yield 事件：{"type": "delta"|"tool_delta"|"end", ...}"""
        body: dict = {
            "model": self.model,
            "messages": [_to_api_message(m) for m in messages],
            "stream": True,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if max_tokens:
            body["max_tokens"] = max_tokens

        try:
            async with httpx.AsyncClient(timeout=_STREAM_TIMEOUT) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.api_key}",
                    },
                    json=body,
                ) as res:
                    if res.status_code != 200:
                        text = (await res.aread()).decode("utf-8", "replace")
                        raise _map_error(res.status_code, text)

                    tool_calls: dict[int, dict] = {}
                    content_parts: list[str] = []
                    async for line in res.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if not payload or payload == "[DONE]":
                            continue
                        try:
                            data = json.loads(payload)
                        except ValueError:
                            continue
                        delta = (data.get("choices") or [{}])[0].get("delta") or {}
                        if delta.get("content"):
                            content_parts.append(delta["content"])
                            yield {"type": "delta", "text": delta["content"]}
                        for tc in delta.get("tool_calls") or []:
                            index = tc.get("index", 0)
                            slot = tool_calls.setdefault(
                                index, {"id": "", "name": "", "arguments": ""}
                            )
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] += fn["name"]
                            if fn.get("arguments"):
                                slot["arguments"] += fn["arguments"]
        except httpx.HTTPError as exc:
            raise DeepSeekError("网络连接失败，请检查网络后重试") from exc

        yield {
            "type": "end",
            "content": "".join(content_parts),
            "tool_calls": [tool_calls[i] for i in sorted(tool_calls)],
        }

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        body: dict = {
            "model": self.model,
            "messages": [_to_api_message(m) for m in messages],
            "temperature": temperature,
        }
        if max_tokens:
            body["max_tokens"] = max_tokens
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        try:
            async with httpx.AsyncClient(timeout=_CHAT_TIMEOUT) as client:
                res = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.api_key}",
                    },
                    json=body,
                )
        except httpx.HTTPError as exc:
            raise DeepSeekError("网络连接失败，请检查网络后重试") from exc

        if res.status_code != 200:
            raise _map_error(res.status_code, res.text)
        data = res.json()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise DeepSeekError("响应缺少消息内容") from exc
