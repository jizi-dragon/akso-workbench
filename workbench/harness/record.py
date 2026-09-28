"""模型侧录制/回放台架：把真实 LLM 的响应录成**夹具**，之后无需联网、不花 token 跑通整条 Agent 循环。

## 为什么需要它（计划 §0.2）

`CORE-AGENT-PLAN.md` 的后续每一步（评测、上下文治理、工具面裁剪、可信度标签）都要
**反复跑同一条对话**看行为有没有变。没有它，每次验证都要真调模型 —— 于是：

- **不花钱就不能验证**：一次多轮评测动辄几十次调用，没人会天天跑；
- **不可复现**：同一个 prompt 两次回答不同，于是"改坏了"与"模型这次恰好这么说"分不开；
- **夹具覆盖度不可证**：谁也不知道现有用例到底覆盖了哪几条请求。

本模块照抄本仓**已验证**的手法（`services/egmp/replay.py`：`Recorder` / `ReplayClient` /
`ReplayMiss`），只把对象从平台 HTTP 换成 LLM 调用。

## 三个形状约定

1. **按「请求指纹」匹配**，不按调用顺序。顺序式回放一旦中间少调一次，后面全部错位，
   而报出来的错会指向完全无关的地方。
2. **工具 schema 必须进指纹**（计划点名）。否则**改了工具描述还能"回放成功"** ——
   那不是"这段对话仍然成立"，是**夹具在撒谎**，而它撒的谎恰好是评测最想抓的那类变化。
3. **同契约**：`RecordingClient` / `ReplayLLM` 与 `DeepSeekClient` 面一致
   （`stream_chat` / `chat` / `last_usage` / `model`），所以循环层一行都不用改。

## 诚实边界（写在这里，免得被读成更强的承诺）

- **回放不还原真实的分帧边界**。录的是 `end` 事件（`content` / `tool_calls` / `usage` /
  `finish_reason`），`delta` 的切分方式在录制时就丢了；回放**逐字**吐 delta。
  它验证的是"最终内容一致"，**不是**"流式行为一致"。
- 因此它**不能**用来验"用户看到正文逐字出现"这类体验，也**不能**验
  `deepseek.py` 的重试铁律（那需要真实的流中断，见 `tests/test_deepseek_retry.py` 的注入手法）。
- **不是生产运行时代码**：与 `services/egmp/replay.py` 同级定位（离线开发与验收支撑），
  产品代码不 import 它。
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import AsyncIterator

from ..services.deepseek import DeepSeekClient, normalize_model

FIXTURE_FORMAT_VERSION = 1


class ReplayMiss(RuntimeError):
    """回放时请求不在夹具里 —— 说明夹具覆盖度不足（或代码改了请求的形状）。"""

    def __init__(self, key: str, available: list[str], labels: dict[str, str] | None = None) -> None:
        self.key = key
        self.available = available
        labels = labels or {}
        # 列**可读标签**而不是裸哈希：哈希能证明"是哪一条"，但没法让人判断
        # "我该补哪一条"。两者都给。
        preview = "\n".join(
            f"    - {k[:12]}  {labels.get(k, '')}" for k in sorted(available)[:40]
        )
        more = "" if len(available) <= 40 else f"\n    …（共 {len(available)} 条）"
        super().__init__(
            f"夹具缺少这条交互：{key}\n  夹具内已有（哈希前 12 位 + 标签）：\n{preview}{more}\n"
            "  ⇒ 补录：拿同一条 scenario 对着真实模型重跑一次录制。"
        )


class ReplayRecordedError(RuntimeError):
    """回放一条**当时就失败**的交互（录制时模型/网络真的报错了）。

    它必须能和 `ReplayMiss` 分开：前者是"夹具里没有这条"，后者是"夹具里有，
    而当时它就是失败的" —— 处理方式完全不同（补录 vs 接受）。
    """


# ---------------------------------------------------------------- 请求指纹


def _canon(message: dict) -> dict:
    """规范化一条消息，让指纹**只对语义敏感**、不对键序敏感。"""
    return {
        "role": str(message.get("role") or ""),
        "content": message.get("content"),
        # `tool_calls`（assistant 发起）与 `tool_call_id`（tool 回填）都要进指纹：
        # 漏掉它们会让"工具名/参数变了"回放成同一把钥匙。
        "tool_calls": message.get("tool_calls"),
        "tool_call_id": message.get("tool_call_id"),
    }


def fingerprint(
    messages: list[dict],
    *,
    model: str,
    tools: list[dict] | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    json_mode: bool = False,
) -> str:
    """请求指纹：`sha256(model + messages + tools + temperature + max_tokens + json_mode)`。

    ## 为什么这几项**全都**要进

    指纹的语义是"**这次请求的形状**"。少一项，就会出现"请求其实变了、但回放照样命中"——
    而那种情况下夹具给出的答案是**上一次形状下的答案**，评测却会把它当成这次的结论。

    - `tools`：计划点名的那个。改了工具描述/参数模型，模型看到的东西就变了。
    - `max_tokens`：它直接决定**会不会被截断**（`finish_reason == "length"`）。
    - `json_mode`：它改的是 `response_format`，是请求体的一部分。
    - `model` 用 `normalize_model` 归一：别名与正名打的是同一个模型，请求体逐字相同，
      指纹**就该相同**（否则换个写法就要重录一遍夹具，纯属自找麻烦）。
    """
    payload = json.dumps(
        {
            "model": normalize_model(model),
            "messages": [_canon(m) for m in messages],
            "tools": tools or [],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "json_mode": json_mode,
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _label(messages: list[dict], tools: list[dict] | None, kind: str) -> str:
    """给人看的标签。指纹能证明"是哪一条"，但没法让人判断"我该补哪一条"。"""
    last_user = next(
        (m for m in reversed(messages) if str(m.get("role") or "") == "user"), {}
    )
    text = " ".join(str(last_user.get("content") or "").split())[:48]
    return f"{kind:<6} | {len(messages):>2} msgs | {len(tools or []):>2} tools | {text}"


# ---------------------------------------------------------------- 夹具读写


def make_fixture(name: str, *, model: str = "", note: str = "") -> dict:
    return {
        "formatVersion": FIXTURE_FORMAT_VERSION,
        "name": name,
        "model": model,
        "note": note,
        "interactions": [],
    }


def load_fixture(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    version = data.get("formatVersion")
    if version != FIXTURE_FORMAT_VERSION:
        raise ValueError(
            f"夹具格式版本不匹配：文件为 {version}，本代码支持 {FIXTURE_FORMAT_VERSION}"
        )
    if not isinstance(data.get("interactions"), list):
        raise ValueError("夹具缺少 interactions 列表")
    return data


def save_fixture(fixture: dict, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _index(fixture: dict) -> dict[str, dict]:
    return {str(item.get("key")): item for item in fixture.get("interactions", [])}


def _labels(fixture: dict) -> dict[str, str]:
    return {str(i.get("key")): str(i.get("label") or "") for i in fixture.get("interactions", [])}


# ---------------------------------------------------------------- 录制


class RecordingClient:
    """包住真实 `DeepSeekClient`，按请求指纹录制。

    接口与 `DeepSeekClient` 一致（`stream_chat` / `chat` / `last_usage` / `model`），
    所以循环层拿到它和拿到真客户端没有区别 —— 这是刻意的：**录制不改行为**。

    `stream_chat` 是**透传**的：事件原样往上传，只在收到 `end` 时记一条。
    这样"录制中"与"不录制"对上层完全等价，也不会把 delta 的时序打乱。
    """

    def __init__(self, inner: DeepSeekClient, fixture: dict | None = None,
                 *, name: str = "recording") -> None:
        self._inner = inner
        model = str(getattr(inner, "model", "") or "")
        self.fixture = fixture if fixture is not None else make_fixture(name, model=model)
        self.last_usage: dict = {}

    @property
    def model(self) -> str:
        return str(getattr(self._inner, "model", "") or "")

    def _append(self, key: str, kind: str, label: str, event: dict) -> None:
        self.fixture["interactions"].append(
            {"key": key, "kind": kind, "label": label, "event": event}
        )

    async def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[dict]:
        key = fingerprint(messages, model=self.model, tools=tools, temperature=temperature,
                          max_tokens=max_tokens)
        label = _label(messages, tools, "stream")
        try:
            async for event in self._inner.stream_chat(
                messages, tools=tools, temperature=temperature, max_tokens=max_tokens
            ):
                # ★ **边发生边记**：在 `yield` 这个 `end` **之前**就落进夹具。
                #
                # 写在循环之后（"等这一轮跑完再统一记"）是**不行的** —— 而它看起来
                # 完全合理。消费方（`_one_model_turn`）收到 `end` 就 `return` 了，
                # 于是本生成器在 `yield event` 处被 `aclose()`，**循环之后那几行
                # 永远不会执行**，夹具恒空。
                #
                # 这与 PITFALLS #98 是**同一个坑**（写它的人在同一会话里又踩了一次）：
                # 跨生成器的收尾顺序不能靠"结束时汇总"，只能"边发生边记"。
                # 判据：`tests/test_harness_record.py::test_a_whole_agent_turn_can_be_replayed_without_the_model`
                # —— 它断言"两轮模型调用应当录两条"，写在循环之后时它报 `什么都没录到`。
                if event.get("type") == "end":
                    self._append(key, "stream", label, {
                        "ok": True,
                        "content": event.get("content") or "",
                        "tool_calls": event.get("tool_calls") or [],
                        "usage": event.get("usage") or {},
                        "finish_reason": event.get("finish_reason") or "",
                    })
                yield event
        except Exception as exc:  # noqa: BLE001 —— 失败也要录，回放时才能复现失败语义
            self._append(key, "stream", label, {
                "ok": False, "error": str(exc), "errorType": type(exc).__name__,
            })
            raise
        self.last_usage = dict(getattr(self._inner, "last_usage", {}) or {})

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        key = fingerprint(messages, model=self.model, temperature=temperature,
                          max_tokens=max_tokens, json_mode=json_mode)
        label = _label(messages, None, "chat")
        try:
            text = await self._inner.chat(messages, temperature=temperature,
                                          max_tokens=max_tokens, json_mode=json_mode)
        except Exception as exc:  # noqa: BLE001
            self._append(key, "chat", label, {
                "ok": False, "error": str(exc), "errorType": type(exc).__name__,
            })
            raise
        self.last_usage = dict(getattr(self._inner, "last_usage", {}) or {})
        self._append(key, "chat", label, {
            "ok": True, "content": text, "tool_calls": [], "usage": self.last_usage,
            "finish_reason": "stop",
        })
        return text


# ---------------------------------------------------------------- 回放


class ReplayLLM:
    """按夹具回放。与 `DeepSeekClient` **同契约**。

    命中不了就抛 `ReplayMiss` 并**列出可用键与它们的标签** —— 夹具的覆盖度由此可证，
    而不是靠人肉清点（`services/egmp/replay.py` 的原话，这里沿用）。
    """

    def __init__(self, fixture: dict, *, strict: bool = True) -> None:
        self._fixture = fixture
        self._index = _index(fixture)
        self._labels = _labels(fixture)
        self._strict = strict
        self.model = str(fixture.get("model") or "")
        self.last_usage: dict = {}
        self.calls: list[str] = []
        self.misses: list[str] = []

    @property
    def interaction_count(self) -> int:
        return len(self._index)

    def _lookup(self, key: str) -> dict | None:
        self.calls.append(key)
        entry = self._index.get(key)
        if entry is None:
            self.misses.append(key)
            if self._strict:
                raise ReplayMiss(key, list(self._index), self._labels)
            return None
        return entry

    def _event(self, entry: dict, key: str) -> dict:
        event = entry.get("event") or {}
        if not event.get("ok", True):
            raise ReplayRecordedError(
                f"这条交互在录制时就是失败的（{event.get('errorType')}）：{event.get('error')}"
            )
        return event

    async def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[dict]:
        key = fingerprint(messages, model=self.model, tools=tools, temperature=temperature,
                          max_tokens=max_tokens)
        entry = self._lookup(key)
        if entry is None:
            return
        event = self._event(entry, key)
        self.last_usage = dict(event.get("usage") or {})

        # ⚠️ **逐字**回放 delta：真实的 `delta` 切分在录制时就丢了（录的是 `end` 事件）。
        # 它验证"最终内容一致"，**不验证**"流式行为一致" —— 见模块文档串的诚实边界。
        for char in str(event.get("content") or ""):
            yield {"type": "delta", "text": char}
        yield {
            "type": "end",
            "content": event.get("content") or "",
            "tool_calls": copy.deepcopy(event.get("tool_calls") or []),
            "usage": self.last_usage,
            "finish_reason": event.get("finish_reason") or "",
        }

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        key = fingerprint(messages, model=self.model, temperature=temperature,
                          max_tokens=max_tokens, json_mode=json_mode)
        entry = self._lookup(key)
        if entry is None:
            return ""
        event = self._event(entry, key)
        self.last_usage = dict(event.get("usage") or {})
        return str(event.get("content") or "")


__all__ = [
    "FIXTURE_FORMAT_VERSION",
    "RecordingClient",
    "ReplayLLM",
    "ReplayMiss",
    "ReplayRecordedError",
    "fingerprint",
    "load_fixture",
    "make_fixture",
    "save_fixture",
]
