"""DeepSeek API 客户端（httpx 异步，SSE 流式 + function calling + JSON 模式）。

事件契约（stream_chat 依次 yield）：
  {"type": "delta", "text"}                    正文增量
  {"type": "end", "content", "tool_calls", "usage"}
                                               流结束；tool_calls 为聚合后的完整调用列表；
                                               usage 含**缓存命中量**（见下）
  {"type": "reasoning", "text"}                思考模式的思维链增量（可选）
  {"type": "usage", "usage"}                   流式下用量单独到达（部分网关形态）
错误统一抛 DeepSeekError（带 HTTP 状态码），由上层转为事件/HTTP 响应。

## usage 与缓存（2026-09-15）

官方在**每个**响应里回缓存命中情况，此前这里把它整个丢掉了：

- Chat Completions：`prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` / `prompt_tokens`
- Responses API：`input_tokens` + `input_tokens_details.cached_tokens`

（[上下文硬盘缓存](https://api-docs.deepseek.com/zh-cn/guides/kv_cache) §Checking Cache Hit Status）

`stream_options.include_usage` 让流式响应的**最后一帧**带上 usage；本模块强制打开它，
并把 usage 一并挂到 `end` 事件上（单独到达的 usage 帧也会转成 `usage` 事件，
以兼容某些网关的形态差异）。
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

DEFAULT_BASE_URL = "https://api.deepseek.com"

#: 官方现行模型名（2026-09-10 起 `deepseek-v4-flash` 已退役，
#: 旧名仍被兼容路由到 V4.1-Flash，见 https://api-docs.deepseek.com/updates）。
#: 用新名而不是旧名，是因为旧名"迟早不再被接受"，而它现在**没有任何好处**。
DEFAULT_MODEL = "deepseek-flash"

MODEL_ALIASES = {
    "deepseek-v4-flash": "deepseek-flash",
    "deepseek-v4-flash-vision-exp": "deepseek-flash",
    "deepseek-v4.1-flash": "deepseek-flash",
    "deepseek-v4.1flash": "deepseek-flash",
}

_STREAM_TIMEOUT = httpx.Timeout(120.0, connect=15.0)
_CHAT_TIMEOUT = httpx.Timeout(90.0, connect=15.0)


def normalize_model(name: str | None) -> str:
    """把历史/别名模型名归一为官方现行名。

    为什么要有这一步：`deepseek-v4-flash` 在 2026-09-10 随 V4.1-Flash 发布**已退役**，
    官方为兼容把它路由到 V4.1-Flash（"temporarily routed"）。库里存着旧名的用户
    不会报错，但会在某天突然 404——归一化把这件事提前到现在，且对用户**行为不变**
    （同一个模型、同一个价格），只是名字对了。
    """
    key = (name or "").strip()
    return MODEL_ALIASES.get(key.lower(), key) or DEFAULT_MODEL


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
        self.last_usage: dict = {}
        """最近一次调用的 `usage`（含缓存命中量）。

        为什么用属性而不是改返回值签名：`chat()` 的调用点（摘要折叠、蓝图生成）
        只关心文本，为它们全都改签名不值得；用量经属性回传，只有在意的人来读。
        """

    async def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[dict]:
        """流式对话，yield 事件：{"type": "delta"|"usage"|"end", ...}"""
        body: dict = {
            "model": normalize_model(self.model),
            "messages": [_to_api_message(m) for m in messages],
            "stream": True,
            "temperature": temperature,
            # 不打开它，流式响应**不会**带 usage，缓存命中量就永远看不到
            # （OpenAI 兼容协议约定：include_usage 时最后一帧 data 只含 usage）。
            "stream_options": {"include_usage": True},
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
                    usage: dict = {}
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
                        if data.get("usage"):
                            # include_usage 的最后一帧：choices 为空、只有 usage。
                            # 并存一份到局部变量，保证即使这一帧形态有差异（某些网关会
                            # 把它塞进最后一帧的 choices 里），`end` 事件仍带得上用量。
                            usage = data["usage"]
                            yield {"type": "usage", "usage": usage}
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
            "usage": usage,
        }

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        body: dict = {
            "model": normalize_model(self.model),
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
        self.last_usage = data.get("usage") or {}
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise DeepSeekError("响应缺少消息内容") from exc
