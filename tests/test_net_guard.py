"""出站地址守卫（`workbench/net_guard.py`）的判据。

## 为什么这些断言值得写

`read_web_page` 是 `risk=read`（**不过确认门**）且接受任意 URL，而 `/api/*` 无认证是
 的有意保留、控制端口固定 18765/18767。三件事叠起来就是
一条"外部网页 → 本机内网 API → 进上下文 → 外发"的链，而它**不会报任何错**。

其中**最容易被写漏的一条是第一跳之外的跳转**：`follow_redirects=True` 只校验了
起始地址，一个完全正常的公网地址 `302 Location: http://127.0.0.1:18765/...`
就绕过去了 —— 而首跳校验看起来一切正常。本文件里
`test_redirect_to_loopback_is_refused` 与 `test_no_module_follows_redirects_blindly`
两条就是钉它的。

## 判据为什么不打真网络

除"公网放行"用字面量公网 IP 之外，**全部用字面量地址或 `localhost`** —— 它们的
`getaddrinfo` 结果不需要外部 DNS。跳转那几条用 `httpx.MockTransport`（`guarded_get`
的 `transport` 参数存在的唯一理由就是它）。这样本文件在隔离网里也能跑。
"""

from __future__ import annotations

import ast
import asyncio
import pathlib

import httpx
import pytest

from workbench import db, net_guard
from workbench import plugin_api as pa

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture()
def ready():
    db.connect()
    return db


# ---------------------------------------------------------------- 拒：地址段


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:18765/api/health",     # 回环 —— 本威胁模型的主目标
    "http://127.0.0.1/",
    "http://0.0.0.0/",                       # "本网络"
    "http://10.0.0.5/",                      # 私网
    "http://172.16.0.1/",
    "http://192.168.1.1/",
    "http://169.254.169.254/latest/meta-data/",   # 云元数据
    "http://100.64.0.1/",                    # CGNAT
    "http://224.0.0.1/",                     # 组播
    "http://255.255.255.255/",
    "http://[::1]/",                         # IPv6 回环
    "http://[fe80::1]/",                     # 链路本地
    "http://[fc00::1]/",                     # 唯一本地地址
    "http://[::ffff:127.0.0.1]/",            # ★ v4-mapped：换个写法就想绕过 v4 黑名单
    "http://[::ffff:10.0.0.1]/",             # ★ 同上
])
def test_non_public_addresses_are_refused(url: str) -> None:
    with pytest.raises(ValueError):
        net_guard.guard_public_url(url)


def test_localhost_hostname_is_refused() -> None:
    """主机名形态也要拦 —— 只拦字面量 IP 等于把 `localhost` 让过去。"""
    with pytest.raises(ValueError):
        net_guard.guard_public_url("http://localhost:18765/api/health")


# ---------------------------------------------------------------- 拒：scheme 与形状


@pytest.mark.parametrize("url", [
    "file:///C:/Windows/win.ini",            # 本地文件
    "ftp://example.invalid/x",
    "javascript:alert(1)",
    "gopher://127.0.0.1/",
    "example.invalid/x",                     # 没有 scheme
    "http://",                               # 没有主机名
])
def test_non_http_schemes_and_shapes_are_refused(url: str) -> None:
    with pytest.raises(ValueError):
        net_guard.guard_public_url(url)


# ---------------------------------------------------------------- 放行：公网


@pytest.mark.parametrize("url", [
    "http://8.8.8.8/",
    "https://1.1.1.1/",
    "http://[2606:4700:4700::1111]/",
])
def test_public_addresses_pass_and_are_returned(url: str) -> None:
    """放行时返回归一化后的 URL（调用方拿它继续用，不用自己 strip）。"""
    assert net_guard.guard_public_url(url) == url


# ---------------------------------------------------------------- ★ 逐跳校验


def _mock(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_redirect_to_loopback_is_refused() -> None:
    """★ 核心判据：**第一跳是公网、第二跳跳向内网** —— 必须在第二跳被拒。

    同时记录"实际发出过哪些 host"，用来证明那个内网地址**根本没被请求过**
    （只断言"抛了异常"是不够的：一个先请求再报错的实现也能让断言变绿）。
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        if request.url.host == "8.8.8.8":
            return httpx.Response(
                302, headers={"location": "http://127.0.0.1:18765/api/agent/audit"}
            )
        return httpx.Response(200, text="内网内容（绝不该被读到）")

    with pytest.raises(ValueError, match="127.0.0.1"):
        asyncio.run(net_guard.guarded_get("http://8.8.8.8/start", transport=_mock(handler)))

    assert seen == ["8.8.8.8"], f"内网地址被真的请求了：{seen}"


def test_relative_location_is_resolved_before_checking() -> None:
    """相对 Location 要先并成绝对地址再校验，否则下一跳校验的是个残缺 URL。"""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/a":
            return httpx.Response(302, headers={"location": "/b"})
        return httpx.Response(200, text="ok")

    res = asyncio.run(
        net_guard.guarded_get("http://8.8.8.8/a", transport=_mock(handler))
    )
    assert res.status_code == 200
    assert seen == ["http://8.8.8.8/a", "http://8.8.8.8/b"]


def test_redirect_loop_is_capped() -> None:
    """无限跳转是廉价的拖死手段 —— 超过上限必须中止，不能一直跟。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://8.8.8.8/again"})

    with pytest.raises(ValueError, match="重定向次数"):
        asyncio.run(net_guard.guarded_get("http://8.8.8.8/loop", transport=_mock(handler)))


# ---------------------------------------------------------------- ★ 链路级


def test_read_web_page_tool_refuses_loopback(ready) -> None:
    """★ 链路级：走**真工具中枢**调 `read_web_page`，内网地址必须被拒。

    为什么不能只测 `net_guard`：本仓 PITFALLS #42 的教训正是"函数级测过了 ≠ 链路用上了"
    （`audit.record` 当时三条单测全绿，而它一个调用方都没有）。这条断言证明守卫
    **真的接在工具上**，而不是一个没人调用的模块。
    """
    from workbench.harness.tools import default_registry

    hub = default_registry()
    inv = asyncio.run(hub.execute(
        "read_web_page",
        {"url": "http://127.0.0.1:18765/api/agent/audit"},
        pa.ToolContext(source="test"),
    ))
    assert inv.status is not pa.ToolStatus.OK, "内网地址竟然被放行了"
    # ⚠️ 只断言"内容里有 127.0.0.1"是**不够的**：没有守卫时 httpx 连不上 18765，
    # 它自己的连接错误里同样带这个地址 ⇒ 断言会假绿。必须认**守卫特有的理由**，
    # 才能把"守卫拒了"与"恰好连不上"分开。
    assert "非公网段" in inv.content, (
        f"拒绝理由不是出站守卫给出的（可能是恰好连不上而不是被拦）：{inv.content[:300]}"
    )


def test_no_module_follows_redirects_blindly() -> None:
    """★ 机检不变量：`workbench/` 里**不许**再有 `follow_redirects=True` 的**调用**。

    它每次出现都意味着"只校验了第一跳"。这条做成机检而不是逐模块记，是因为
    下一个写 HTTP 调用的人不会读到 `net_guard` 的文档串 —— 但会撞上这条测试。

    ⚠️ **必须用 `ast` 查调用，不能用字符串搜源码**：第一版就是这么写的，结果被
    **三段解释"为什么不要这么做"的注释**判成违规（`net_guard` / `builtins` /
    `websearch` 各一处）。字符串搜源码只能证明"文件里出现过这几个字"，
    与本仓 PITFALLS #87 是同一族错误。
    """
    offenders: list[str] = []
    for path in (ROOT / "workbench").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if (kw.arg == "follow_redirects"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True):
                    offenders.append(f"{path.relative_to(ROOT)}:{kw.value.lineno}")
    assert not offenders, (
        "这些地方在盲目跟随重定向（只校验第一跳）：" + ", ".join(offenders)
        + "。改用 workbench.net_guard.guarded_get（逐跳校验）。"
    )


def test_response_body_is_capped_and_marked() -> None:
    """响应体是**总预算**：超过 `max_bytes` 截断，并用响应头显式标记。"""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 10_000)

    res = asyncio.run(net_guard.guarded_get(
        "http://8.8.8.8/big", transport=_mock(handler), max_bytes=100))
    assert len(res.content) == 100, "响应体没有被上限截断"
    assert res.headers.get("x-workbench-truncated") == "1"
    assert len(res.text) == 100


def test_total_deadline_covers_all_redirects() -> None:
    """`timeout` 是**整次调用**的总时限，不是每跳时限。

    反证：旧实现每跳 0.1s、4 跳能花 0.4s+，这里会变成
    "跳转次数超过上限"而不是 "总时限用尽"。
    """
    import time as _time

    def handler(_request: httpx.Request) -> httpx.Response:
        _time.sleep(0.12)
        return httpx.Response(302, headers={"location": "http://8.8.8.8/next"})

    started = _time.perf_counter()
    with pytest.raises(httpx.TimeoutException, match="总时限"):
        asyncio.run(net_guard.guarded_get(
            "http://8.8.8.8/start", transport=_mock(handler), timeout=0.2, max_hops=3))
    assert _time.perf_counter() - started < 0.5
