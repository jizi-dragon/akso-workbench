r"""LLM 调用的重试与退避（计划 §5.5）。

## 为什么需要这一层

`deepseek.py` 原先把 429/5xx 映射成一句中文就抛，`routes_chat` 接住 → **整轮终止**。
而那一轮可能**已经烧掉 20 步工具调用** —— 用户看到的是"跑了半天白跑"。
全仓 `Retry-After` 零命中。一次网络抖动废掉整轮，代价与抖动本身完全不成比例。

## 三条场景（计划里点名的）

| # | 场景 | 断言 |
|---|---|---|
| ① | 连续 2 次 429 后成功 | 最终成功，且**用量只记 1 次** |
| ② | 首个 delta 之后流中断 | **不重试**，转 `DeepSeekError`，且正文**无重复** |
| ③ | 401 | **只请求 1 次**（重试不会变好，只会更慢地失败） |

## 测试缝

`deepseek._sleep` 是**刻意留的**（同 `net_guard.guarded_get(transport=...)`）：
不抽掉它，场景①要真等 0.5 + 1.0 秒、退避上界那条要等 8 秒 —— 用例会慢到没人跑。
**延迟数值本身仍被断言**（记录每次 sleep 的秒数），所以"抽掉等待"不等于"不验退避"。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from workbench.services import deepseek

_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _sse(*frames: dict) -> bytes:
    body = "".join(f"data: {json.dumps(f)}\n\n" for f in frames) + "data: [DONE]\n\n"
    return body.encode("utf-8")


def _delta(text: str) -> dict:
    return {"choices": [{"delta": {"content": text}}]}


def _end_frame(**usage: int) -> dict:
    return {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": usage}


class _BoomMidStream(httpx.AsyncByteStream):
    """先吐**一帧正文**，再抛连接错误 —— 这正是"不能重试"的那个时刻。"""

    async def __aiter__(self):
        yield _sse(_delta("半"))
        raise httpx.ReadError("模拟：吐了正文之后连接断了")


def _install(monkeypatch: pytest.MonkeyPatch, handler) -> list[float]:
    """把 `httpx.AsyncClient` 换成注入 `MockTransport` 的版本；返回**退避秒数记录**。"""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    def factory(**kwargs):  # noqa: ANN001, ANN202
        kwargs.pop("timeout", None)
        return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(deepseek.httpx, "AsyncClient", factory)
    # ⚠️ `_sleep` 只在**修复后**的代码里存在。这里刻意用 `hasattr` 兜一下：
    # 反证时（`git stash push -- workbench/services/deepseek.py`）如果不兜，
    # **每一条**用例都会在 setup 阶段抛 `AttributeError` —— 那是**基础设施错误**，
    # 而不是"断言抓住了缺陷"。两者在输出上都叫"红"，但只有后者能证明判据有效
    # （同一个教训在第 3 轮的反证里踩过：那次是 ImportError 造成的收集错误）。
    if hasattr(deepseek, "_sleep"):
        monkeypatch.setattr(deepseek, "_sleep", fake_sleep)
    return sleeps


def _client() -> deepseek.DeepSeekClient:
    return deepseek.DeepSeekClient(api_key="test-key")


async def _drain(gen) -> list[dict]:
    return [event async for event in gen]


# ---------------------------------------------------------------- ① 429 后成功


def test_two_429s_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """① 连续 2 次 429 后成功：最终成功、退了两次、**用量只记 1 次**。"""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) <= 2:
            return httpx.Response(429, json={"error": {"message": "rate limited"}},
                                  headers={"retry-after": "0.1"})
        return httpx.Response(200, content=_sse(
            _delta("好"), _end_frame(prompt_tokens=100, prompt_cache_hit_tokens=80,
                                     prompt_cache_miss_tokens=20, completion_tokens=5),
        ))

    sleeps = _install(monkeypatch, handler)
    events = asyncio.run(_drain(_client().stream_chat([{"role": "user", "content": "hi"}])))

    assert len(calls) == 3, f"应当请求 3 次（1 次首发 + 2 次重试），实际 {len(calls)}"
    assert len(sleeps) == 2, f"应当退避 2 次，实际 {len(sleeps)} 次：{sleeps}"
    # `Retry-After: 0.1` 比退避基准（0.5 / 1.0）小，所以取基准；含 jitter ⇒ 判区间
    assert all(0.5 <= s <= 0.5 * 1.25 for s in sleeps[:1]), f"第 1 次退避越界：{sleeps}"
    assert 1.0 <= sleeps[1] <= 1.0 * 1.25, f"第 2 次退避越界：{sleeps}"

    deltas = [e for e in events if e["type"] == "delta"]
    assert [d["text"] for d in deltas] == ["好"], f"正文重复或缺失：{deltas}"
    ends = [e for e in events if e["type"] == "end"]
    assert len(ends) == 1, "重试之后必须只有一个 end 事件"
    assert ends[0]["usage"]["prompt_tokens"] == 100, "用量丢了"
    # ★ 用量只该出现一次：重试不重新计费（平台只对成功那次计费）
    assert sum(1 for e in events if e["type"] == "end") == 1


def test_retry_after_is_honored_and_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """`Retry-After` 大得离谱时取上限 30s —— 平台让我们等 10 分钟也不能真等。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, content=b"slow down", headers={"retry-after": "600"})

    sleeps = _install(monkeypatch, handler)
    with pytest.raises(deepseek.DeepSeekError):
        asyncio.run(_drain(_client().stream_chat([{"role": "user", "content": "hi"}])))

    assert sleeps, "应当至少退避一次"
    assert all(s <= deepseek.RETRY_AFTER_CAP_S * 1.25 for s in sleeps), (
        f"Retry-After 没被 30s 上限截住：{sleeps}"
    )


# ---------------------------------------------------------------- ② 吐字后断流


def test_no_retry_after_the_first_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ ② 首个 delta 之后断流：**不重试**、抛错、且正文**没有重复**。

    这条是整个重试设计的**铁律**：已经吐字了再重试 = 用户看到同一段话说两遍。
    实现上的分界线是 `sent_delta`，它只在 `yield {"type": "delta"}` 的**同一行旁边**置位。
    """
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, stream=_BoomMidStream())

    sleeps = _install(monkeypatch, handler)

    async def run() -> tuple[list[dict], Exception | None]:
        seen: list[dict] = []
        try:
            async for event in _client().stream_chat([{"role": "user", "content": "hi"}]):
                seen.append(event)
        except Exception as exc:  # noqa: BLE001 —— 这里就是要看抛的是什么
            return seen, exc
        return seen, None

    seen, exc = asyncio.run(run())

    assert len(calls) == 1, f"吐过正文就**不许**重试，实际请求了 {len(calls)} 次"
    assert sleeps == [], f"不该退避：{sleeps}"
    assert isinstance(exc, deepseek.DeepSeekError), f"错误类型不对：{exc!r}"
    texts = [e["text"] for e in seen if e["type"] == "delta"]
    assert texts == ["半"], f"正文重复了（这正是重试要防的）：{texts}"


# ---------------------------------------------------------------- ③ 不该重试的状态码


@pytest.mark.parametrize("status", [400, 401, 402, 422])
def test_non_retryable_status_requests_exactly_once(
    monkeypatch: pytest.MonkeyPatch, status: int,
) -> None:
    """③ 400/401/402/422 **只请求 1 次** —— 重试不会变好，只会更慢地失败。

    401 是密钥错、402 是余额不足、400/422 是请求本身不合法：三个都是"改请求才有救"，
    盲目重试只是把"立刻失败"变成"几秒后失败"，还多烧 3 次配额。
    """
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(status, json={"error": {"message": "bad"}})

    sleeps = _install(monkeypatch, handler)
    with pytest.raises(deepseek.DeepSeekError):
        asyncio.run(_drain(_client().stream_chat([{"role": "user", "content": "hi"}])))

    assert len(calls) == 1, f"{status} 不该重试，实际请求了 {len(calls)} 次"
    assert sleeps == [], f"{status} 不该退避：{sleeps}"


def test_retries_are_capped_at_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    """全部重试都失败时，请求次数恰好等于 `MAX_ATTEMPTS`（不会无限重试）。"""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, content=b"unavailable")

    sleeps = _install(monkeypatch, handler)
    with pytest.raises(deepseek.DeepSeekError):
        asyncio.run(_drain(_client().stream_chat([{"role": "user", "content": "hi"}])))

    assert len(calls) == deepseek.MAX_ATTEMPTS, f"实际请求 {len(calls)} 次"
    assert len(sleeps) == deepseek.MAX_ATTEMPTS - 1


def test_accumulators_are_reset_between_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 重试时**必须清空累加器**：上一次的半截 tool_call 分片不能拼进这次。

    不清的后果是"参数错乱的工具调用"—— 它**看起来**像模型自己写错了参数，
    排查会一路查错方向（真正的元凶是重试）。
    """
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            # 第一次：吐半个 tool_call 分片，然后 500
            return httpx.Response(500, content=b"boom")
        return httpx.Response(200, content=_sse(
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "function": {"name": "echo_text",
                                                          "arguments": '{"text":"ok"}'}}]}}]},
            _end_frame(prompt_tokens=10),
        ))

    _install(monkeypatch, handler)
    events = asyncio.run(_drain(_client().stream_chat([{"role": "user", "content": "hi"}])))

    end = next(e for e in events if e["type"] == "end")
    assert len(end["tool_calls"]) == 1, f"工具调用被拼重了：{end['tool_calls']}"
    assert end["tool_calls"][0]["arguments"] == '{"text":"ok"}', (
        f"参数被上一次尝试的碎片污染了：{end['tool_calls'][0]['arguments']}"
    )
    assert end["tool_calls"][0]["name"] == "echo_text", "工具名被拼重了"


# ---------------------------------------------------------------- 退避函数本身


def test_retry_delay_is_none_when_attempts_are_exhausted() -> None:
    assert deepseek._retry_delay(deepseek.MAX_ATTEMPTS - 1, 503, None) is None
    assert deepseek._retry_delay(deepseek.MAX_ATTEMPTS, 503, None) is None


@pytest.mark.parametrize("status", [400, 401, 402, 422, 200, 404])
def test_retry_delay_is_none_for_non_retryable_status(status: int) -> None:
    assert deepseek._retry_delay(0, status, None) is None, f"{status} 不该出现在可重试集合里"


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retry_delay_is_positive_for_retryable_status(status: int) -> None:
    delay = deepseek._retry_delay(0, status, None)
    assert delay is not None and delay > 0


def test_retry_delay_grows_and_is_capped() -> None:
    """退避按 2 的幂增长，且**封顶在 8s**（不含 jitter）。"""
    assert deepseek._retry_delay(0, 503, None) == pytest.approx(0.5, rel=0.3)
    assert deepseek._retry_delay(1, 503, None) == pytest.approx(1.0, rel=0.3)
    capped = deepseek._retry_delay(2, 503, None)
    assert capped is not None and capped <= deepseek._BACKOFF_CAP_S * 1.25


def test_retry_delay_prefers_the_larger_of_backoff_and_retry_after() -> None:
    """`Retry-After` 比退避基准**大**时取它；**小**时仍用退避（不要更早去撞）。"""
    small = deepseek._retry_delay(0, 429, "0.01")
    assert small is not None and small >= 0.5, f"Retry-After 更小时不该缩短退避：{small}"

    large = deepseek._retry_delay(0, 429, "5")
    assert large is not None and 5.0 <= large <= 5.0 * 1.25, f"没尊重 Retry-After：{large}"


def test_retry_delay_ignores_a_malformed_retry_after() -> None:
    """`Retry-After` 也可能是 HTTP-date 形态（RFC 允许）—— 解析不了就**忽略**，不要炸。"""
    delay = deepseek._retry_delay(0, 429, "Wed, 21 Oct 2026 07:28:00 GMT")
    assert delay is not None and delay > 0


def test_retry_delay_has_jitter() -> None:
    """必须有 jitter：同时重试的多个客户端会形成新的尖峰，而那正是 429 的成因。"""
    samples = {deepseek._retry_delay(1, 503, None) for _ in range(20)}
    assert len(samples) > 1, "退避没有抖动，多个客户端会整齐地一起重试"
