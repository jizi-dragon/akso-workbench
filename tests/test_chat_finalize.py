r"""一轮对话的**收尾**：错误不进模型上下文、中止也留痕（计划 §5.4b / §5.4c）。

## 两个缺陷（都"看起来完全正常"）

**(b) 报错被当成模型的话写进历史。** `routes_chat` 收尾时
`final_content = f"错误：{error_message}" if error_message else result["content"]`，
然后以 `role="assistant"` 落库；而 `_model_history` 把整段历史原样翻给模型 ⇒
**模型读到一句它从没说过的话**。后果不是报错，是模型据此继续推理
（把一次网络错误当成"我已经回答过了"），表现为"它怎么突然开始胡说"。
刷新后那条错误还挂在气泡里，一切看起来正常。

**(c) 取消 / 断连留下半截会话。** 收尾代码原先在 `async for` **之后**，
而消费方断开时 `aclose()` 会抛 `GeneratorExit` —— **那几行根本不会执行**。
于是库里只有用户那句话，助手那侧什么都没有；刷新后那一轮凭空消失，
而用户明明看着它跑过。

## 这一层此前完全没有测试

没有任何用例驱动过 `_chat_events`（`_model_history` 有，但那只是链路的一半）。
所以本文件的重点是**驱动整条生成器**，并把"断开"这件事**真的做出来**
（`aclose()`），而不是绕过它。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from workbench import db
from workbench.api import routes_chat
from workbench.services import storage

#: `messages.kind` 的取值，**刻意写字符串字面量而不是 import 常量**。两个理由：
#:
#: 1. **避免同义反复**：断言 `kind == storage.MESSAGE_KIND_ERROR` 而代码写的也是那个常量，
#:    等于"变量等于它自己"—— 常量哪天被改错，用例照样绿。
#:    判据要钉的是**落库里的那个字面值**，因为它是数据契约（`kind` 是持久化字段）。
#: 2. 反证时要能在旧代码上被**收集**：模块级 `from … import MESSAGE_KIND_ERROR`
#:    在回退源文件后会让整份文件 ImportError，那是**基础设施错误**而非断言红
#:    （同一个坑本会话已踩三次：`_fill_unexecuted` / `_sleep` / 这一处）。
KIND_MESSAGE = "message"
KIND_ERROR = "error"


@pytest.fixture()
def ready():
    db.connect()
    return db


class _ReadyCfg:
    """`load_deepseek()` 的替身：只要 `ready=True`，`_chat_events` 就不走"没配 Key"分支。"""

    ready = True
    api_key = "test-key"
    model = "deepseek-flash"
    temperature = 0.7


def _install(monkeypatch: pytest.MonkeyPatch, loop) -> None:
    monkeypatch.setattr(routes_chat, "load_deepseek", lambda: _ReadyCfg())
    monkeypatch.setattr(routes_chat, "run_agent_loop", loop)


def _scripted(events: list[dict]):
    async def fake_loop(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        for event in events:
            yield event
    return fake_loop


def _raising(events: list[dict], exc: Exception):
    async def fake_loop(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        for event in events:
            yield event
        raise exc
    return fake_loop


async def _drain(body) -> list[str]:
    return [sse async for sse in routes_chat._chat_events(body)]


async def _abort_after_token(body) -> list[str]:
    """跑起来、**在收到第一个 token 之后断开** —— 这正是"用户关了页面"的形状。

    `break` 只是让消费者不再要下一个事件，**生成器仍挂在那里**；
    `aclose()` 才是真正把 `GeneratorExit` 抛进去的那一步 —— 收尾代码能不能跑，
    取决于它有没有写在 `finally` 里。
    """
    gen = routes_chat._chat_events(body)
    seen: list[str] = []
    async for sse in gen:
        seen.append(sse)
        if '"type": "token"' in sse:
            break
    await gen.aclose()
    return seen


def _conversation_of(seen: list[str]) -> str:
    for sse in seen:
        if sse.startswith("data: "):
            event = json.loads(sse[6:].strip())
            if event.get("type") == "start":
                return str(event["conversationId"])
    raise AssertionError(f"start 事件里没有会话 id：{seen}")


def _rows(conv_id: str) -> list[dict]:
    return storage.list_messages(conv_id)


# ---------------------------------------------------------------- (b) 错误不进上下文


def test_model_history_drops_error_rows() -> None:
    """★ `kind='error'` 的行**绝不进模型上下文**，但同一批里的真实消息必须留下。

    反向断言（"真实消息还在"）是必需的：只断言"错误不在"的话，
    一个把整段历史都丢掉的实现也能变绿。
    """
    rows = [
        {"role": "user", "content": "帮我看看", "kind": KIND_MESSAGE},
        {"role": "assistant", "content": "错误：网络连接失败", "kind": KIND_ERROR},
        {"role": "assistant", "content": "我看过了", "kind": KIND_MESSAGE},
    ]
    out = routes_chat._model_history(rows)

    texts = [str(m["content"]) for m in out]
    assert not any("错误" in t for t in texts), f"错误进了模型上下文：{texts}"
    assert any("帮我看看" in t for t in texts), "真实消息被误伤"
    assert any("我看过了" in t for t in texts), "真实消息被误伤"


def test_rows_without_a_kind_column_still_work() -> None:
    """历史行可能没有 `kind` 字段（老夹具、老查询）—— 缺省必须当作 `message`。

    若这里判成"非 message"，**整段历史会被静默丢掉**，而现象是
    "模型突然不记得刚才说过什么"——查起来会一路查到缓存上去。
    """
    out = routes_chat._model_history([{"role": "user", "content": "没有 kind 列"}])
    assert out == [{"role": "user", "content": "没有 kind 列"}]


def test_an_error_turn_is_stored_as_kind_error(ready, monkeypatch) -> None:
    """失败的一轮落 `kind='error'`，界面仍看得见（`list_messages` 不过滤）。"""
    _install(monkeypatch, _raising([{"type": "token", "text": "半"}],
                                   RuntimeError("上游炸了")))
    seen = asyncio.run(_drain(routes_chat.ChatBody(message="你好")))
    conv_id = _conversation_of(seen)

    rows = _rows(conv_id)
    assistants = [r for r in rows if r["role"] == "assistant"]
    assert len(assistants) == 1, f"应当恰好一条助手消息：{rows}"
    assert assistants[0]["kind"] == KIND_ERROR
    assert "错误" in assistants[0]["content"]
    # ★ 界面那侧必须还能看见它 —— 过滤只发生在模型那一侧
    assert any(r["kind"] == KIND_ERROR for r in rows)


def test_the_error_of_the_previous_turn_is_not_fed_back(ready, monkeypatch) -> None:
    """★ 端到端：上一轮的错误，**下一轮的模型上下文里没有它**。

    这是 (b) 的正题。分两轮跑同一个会话：第一轮失败（落 error），
    第二轮正常 —— 然后检查第二轮**实际发给模型的 history**。
    """
    sent: list[list[dict]] = []

    async def capturing_loop(history, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        sent.append(list(history))
        yield {"type": "final", "content": "这轮正常"}

    _install(monkeypatch, _raising([{"type": "token", "text": "半"}],
                                   RuntimeError("上游炸了")))
    first = asyncio.run(_drain(routes_chat.ChatBody(message="第一轮")))
    conv_id = _conversation_of(first)
    assert any(r["kind"] == KIND_ERROR for r in _rows(conv_id)), "前提不成立"

    _install(monkeypatch, capturing_loop)
    asyncio.run(_drain(routes_chat.ChatBody(message="第二轮", conversationId=conv_id)))

    assert sent, "第二轮没有调用模型"
    flat = json.dumps(sent[0], ensure_ascii=False)
    assert "上游炸了" not in flat, f"上一轮的错误被喂回模型了：{flat}"
    assert "第一轮" in flat, "真实历史被误伤（用户那句话应当还在）"


# ---------------------------------------------------------------- (c) 中止也留痕


def test_an_aborted_turn_still_records_the_assistant_message(ready, monkeypatch) -> None:
    """★ 断开之后，助手那侧**必须**留下一条带「（已中止）」的消息。

    这是 (c) 的正题。不修的话，`aclose()` 一抛 `GeneratorExit`，收尾那几行就跳过了，
    库里只剩用户那句话。

    ## 这条断言逼出来的一个平台事实（写下来，因为它反直觉）

    半截正文**不能**靠"在 `_stream_agent` 自己的 `finally` 里补一份到 `result`"来保住 ——
    第一版就是这么写的，然后这条用例报 `'（模型未返回内容）（已中止）'`。
    实测（2026-09-24）确认了原因：

        外层 finally 跑时，内层生成器还挂在它上次 yield 的地方；
        内层的 finally 是在 `aclose()` **返回之后**才跑的。

    因为**被打断的 `async for` 不会关闭内层生成器**（只有 `with` 才会），
    它只是挂在那里等垃圾回收，由事件循环 finalize。
    ⇒ **跨生成器的"收尾顺序"不能靠 `finally` 保证，只能"边发生边记"**
    （现在每收到一个 token 就同步一份到 `result`）。
    """
    _install(monkeypatch, _scripted([
        {"type": "token", "text": "半截回答"},
        {"type": "final", "content": "完整回答"},
    ]))
    seen = asyncio.run(_abort_after_token(routes_chat.ChatBody(message="你好")))
    conv_id = _conversation_of(seen)

    assistants = [r for r in _rows(conv_id) if r["role"] == "assistant"]
    assert len(assistants) == 1, f"中止后助手那侧是空的（半截会话）：{_rows(conv_id)}"
    content = assistants[0]["content"]
    assert "半截回答" in content, f"用户已经看见的半截正文丢了：{content!r}"
    assert "（已中止）" in content, f"中止没有标注 —— 刷新后看起来像模型答坏了：{content!r}"
    assert assistants[0]["kind"] == KIND_MESSAGE, "中止不是错误，别混成 error"


def test_an_aborted_turn_does_not_emit_done(ready, monkeypatch) -> None:
    """★ 中止时**不发** `done` 事件 —— 钉住"`finally` 里不许 `yield`"这条约定。

    `finally` 可能正因 `GeneratorExit` 而执行，此时 `yield` 会抛
    `RuntimeError: async generator ignored GeneratorExit`。
    与 `harness/loop._accounted` 同一条理由：**落库是账本的事，事件是界面的事。**
    谁"顺手"在 `finally` 里补一个 `yield`，这条就会红。
    """
    _install(monkeypatch, _scripted([{"type": "token", "text": "半截"}]))
    seen = asyncio.run(_abort_after_token(routes_chat.ChatBody(message="你好")))

    assert not any('"type": "done"' in sse for sse in seen), f"中止却发了 done：{seen}"


def test_an_abort_with_no_output_still_does_not_store_an_empty_string(ready, monkeypatch) -> None:
    """★ 中止 + 一个字都没吐 ⇒ 用既有的「（模型未返回内容）」兜底，**绝不落空串**。

    空串在界面上是"一个空气泡"，在模型上下文里是一条空的 assistant 消息 ——
    两者都让用户无法判断"到底发生了什么"。复用既有兜底文案而不是新造一句，
    是为了不让同一个状态有两种说法。
    """
    async def silent_then_wait(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        yield {"type": "thought", "text": "（还没有正文）"}
        await asyncio.sleep(3600)  # 挂住，等消费者断开

    _install(monkeypatch, silent_then_wait)

    async def drive() -> list[str]:
        gen = routes_chat._chat_events(routes_chat.ChatBody(message="你好"))
        seen: list[str] = []
        async for sse in gen:
            seen.append(sse)
            if '"type": "thought"' in sse:
                break
        await gen.aclose()
        return seen

    seen = asyncio.run(drive())
    conv_id = _conversation_of(seen)
    assistants = [r for r in _rows(conv_id) if r["role"] == "assistant"]
    assert len(assistants) == 1
    assert assistants[0]["content"] != "", "落了空串"
    assert "（模型未返回内容）" in assistants[0]["content"]
    assert "（已中止）" in assistants[0]["content"]


# ---------------------------------------------------------------- 正常路径没被改坏


def test_a_normal_turn_stores_exactly_one_message_and_emits_done(ready, monkeypatch) -> None:
    """正常跑完：一条助手消息（`kind='message'`）＋ 一个 `done` 事件。"""
    _install(monkeypatch, _scripted([
        {"type": "token", "text": "你"},
        {"type": "token", "text": "好"},
        {"type": "final", "content": "你好"},
        {"type": "usage", "usage": {"promptTokens": 10}},
    ]))
    seen = asyncio.run(_drain(routes_chat.ChatBody(message="在吗")))
    conv_id = _conversation_of(seen)

    assistants = [r for r in _rows(conv_id) if r["role"] == "assistant"]
    assert len(assistants) == 1, f"正常路径落了不止一条：{assistants}"
    assert assistants[0]["content"] == "你好"
    assert assistants[0]["kind"] == KIND_MESSAGE

    done = [json.loads(s[6:].strip()) for s in seen if '"type": "done"' in s]
    assert len(done) == 1, "正常路径必须发 done"
    assert done[0]["content"] == "你好"
    assert done[0]["usage"] == {"promptTokens": 10}, "用量没随 done 带出去"


def test_turn_final_text_prefers_error_over_abort() -> None:
    """口径优先级：**错误优先于中止标注** —— 说清"它失败了"比"它被中止了"有用。"""
    content, kind = routes_chat._turn_final_text({"error": "上游炸了"}, completed=False)
    assert kind == KIND_ERROR
    assert content == "错误：上游炸了"

    content, kind = routes_chat._turn_final_text({"content": "半截"}, completed=False)
    assert kind == KIND_MESSAGE
    assert content == "半截（已中止）"

    content, _ = routes_chat._turn_final_text({"content": ""}, completed=True)
    assert content == "（模型未返回内容）"
