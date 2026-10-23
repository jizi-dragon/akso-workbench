"""出站地址守卫：把"模型给的 URL"限制在**公网可达**范围内。

## 为什么需要它（威胁模型，逐条都已在仓库里成立）

`read_web_page` 是 `risk=read`（**不过确认门**，见 [plugin_api.Risk]），且接受**任意 URL**。
把它与下面两件**已经存在**的事实叠起来，就是一条从"外部网页"到"本机内网"的读取链：

1. `/api/*` **无认证**是 ** 的有意保留**，而控制端口固定 18765 / 18767
；
2. `read_web_page` 本身就是一个**无需确认的外发通道** —— 把秘密拼进 query 即可。

于是：恶意页面或搜索结果 → 模型读到"请读 `http://127.0.0.1:18765/api/agent/audit`"
→ `read_web_page`（read 级，不过门）→ 本机 API 内容进上下文 → 随下一次请求外发。

## 这是什么、不是什么（口径与 §D-3 一致）

**这是限损措施，不是 DNS 绑定防护。** `getaddrinfo` 校验与真实连接之间存在 TOCTOU
窗口：控制权威 DNS 的一方可以让第一次解析回公网 IP、第二次解析回 `127.0.0.1`
（DNS rebinding）。要真正封死需要"解析 → 固定 IP → 用该 IP 连接并校验 Host/SNI"，
那要接管 httpx 的连接层。**本模块不声称做到那一步，也不要在文档里把它写成"安全"。**

## 两条纪律

- **唯一事实源**：所有"这个地址能不能出"的判断都从这里走，别在调用方各写一份
  （同 `config.SENSITIVE_PATH_PARTS` 的纪律 —— 本仓曾经有两张敏感路径表，
  只读工具用的是弱的那张，实测能完整读出 cookie/JWT/Fernet 密钥）。
- **Fail-Closed**：解析不出来、地址段不认识、scheme 不是 http(s) —— **一律拒**，
  不回落到"放行"。与本仓沙箱同一条取向。
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from urllib.parse import urljoin, urlparse

#: 明确不许去的 IPv4 段。宁可多列：这些都是"本机/内网/保留"语义，任何一条被漏掉
#: 都等于给模型开一个内网读取口，而**表现是功能完全正常**（公网地址照样能读）。
_BLOCKED_V4 = tuple(
    ipaddress.ip_network(cidr) for cidr in (
        "0.0.0.0/8",          # "本网络"
        "10.0.0.0/8",         # 私网
        "100.64.0.0/10",      # CGNAT（运营商内网，云上常被用作元数据旁路）
        "127.0.0.0/8",        # 回环 ← 本威胁模型的主目标
        "169.254.0.0/16",     # 链路本地，含云元数据 169.254.169.254
        "172.16.0.0/12",      # 私网
        "192.0.0.0/24",       # IETF 协议分配
        "192.0.2.0/24",       # TEST-NET-1
        "192.88.99.0/24",     # 6to4 中继
        "192.168.0.0/16",     # 私网
        "198.18.0.0/15",      # 基准测试
        "198.51.100.0/24",    # TEST-NET-2
        "203.0.113.0/24",     # TEST-NET-3
        "224.0.0.0/4",        # 组播
        "240.0.0.0/4",        # 保留（含 255.255.255.255）
    )
)

#: 明确不许去的 IPv6 段。
_BLOCKED_V6 = tuple(
    ipaddress.ip_network(cidr) for cidr in (
        "::/128",             # 未指定
        "::1/128",            # 回环
        "64:ff9b::/96",       # NAT64（能把 v4 藏进 v6）
        "100::/64",           # 丢弃前缀
        "2001:db8::/32",      # 文档用
        "fc00::/7",           # 唯一本地地址
        "fe80::/10",          # 链路本地
        "ff00::/8",           # 组播
    )
)

#: 最多跟几跳重定向。超过就中止 —— 无限跳转是一种廉价的拖死手段。
MAX_REDIRECT_HOPS = 3

#: 单次响应体上限（默认 5 MB）。超过就**截断并显式标记**，不把整个响应读进内存。
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


def _blocked_reason(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    """这个地址为什么不许去（返回空串 = 放行）。"""
    # v4-mapped（::ffff:127.0.0.1）必须**拆开再看 v4 表**，否则一个回环地址
    # 换个写法就能绕过整张 v4 黑名单。
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _blocked_reason(ip.ipv4_mapped)
    table = _BLOCKED_V4 if isinstance(ip, ipaddress.IPv4Address) else _BLOCKED_V6
    for net in table:
        if ip in net:
            return str(net)
    return ""


def _check_host(host: str) -> None:
    """把 host 解析成 IP 并逐个校验。**Fail-Closed**：解析不出来就拒。"""
    if not host:
        raise ValueError("地址里没有主机名")
    # 字面量 IP 直接判，不走 DNS（也避免把 "127.0.0.1" 交给解析器再拿回同名结果）
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        reason = _blocked_reason(literal)
        if reason:
            raise ValueError(f"拒绝访问 {host}：它落在非公网段 {reason}")
        return

    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"无法解析主机名 {host}（{exc}）") from exc
    if not infos:
        raise ValueError(f"无法解析主机名 {host}（没有任何地址记录）")
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        reason = _blocked_reason(ip)
        if reason:
            # 只要**有一个**解析结果落在非公网就整体拒绝：多记录域名（A 记录轮询）
            # 是 rebinding 的常见载体，放行"另一条是公网"等于把口子留着。
            raise ValueError(f"拒绝访问 {host}：它解析到非公网地址 {ip}（{reason}）")


def guard_public_url(url: str) -> str:
    """校验 `url` 指向公网；通过则返回归一化后的 URL，否则抛 `ValueError`。

    **同步**函数（会做 DNS 解析）。在 async 上下文里请用 `aguard_public_url`，
    否则会阻塞事件循环 —— 本仓对同步阻塞调用的纪律见 AGENTS.md 铁律 12。
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"仅支持 http/https 地址（收到 {parsed.scheme or '无 scheme'}）")
    if not parsed.hostname:
        raise ValueError("地址里没有主机名")
    _check_host(parsed.hostname)
    return url.strip()


async def aguard_public_url(url: str) -> str:
    """`guard_public_url` 的 async 包装：把阻塞的 DNS 解析挪出事件循环（铁律 12）。"""
    return await asyncio.to_thread(guard_public_url, url)


async def _capped_body(res, max_bytes: int) -> tuple[bytes, bool]:
    """流式读响应体，最多 `max_bytes`。返回 `(body, truncated)`。"""
    chunks: list[bytes] = []
    total = 0
    async for chunk in res.aiter_bytes():
        if total + len(chunk) > max_bytes:
            chunks.append(chunk[: max(0, max_bytes - total)])
            return b"".join(chunks), True
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks), False


async def guarded_get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
    method: str = "GET",
    max_hops: int = MAX_REDIRECT_HOPS,
    transport=None,
    max_bytes: int = MAX_RESPONSE_BYTES,
):
    """按**逐跳校验**取一个 URL，返回最终的 `httpx.Response`。

    ## 时间与体积都是**总预算**，不是每跳预算

    - `timeout` 是**整次调用**（含全部重定向）的总时限。旧实现把它当成每跳超时，
      4 跳就能花掉 80 秒；调用方以为 20 秒就结束了。
    - `max_bytes` 是响应体上限（默认 5 MB）。旧实现直接 `res.text`，一个慢速大文件
      就能把内存吃满。超限时**截断并在响应头标记** `x-workbench-truncated: 1`，
      调用方据此告诉用户/模型"这不是完整内容"。

    ## 为什么不用 `follow_redirects=True`

    那等于**只校验第一跳**：一个完全正常的公网地址 `302 Location: http://127.0.0.1:18765/...`
    就绕过了整个守卫 —— 而首跳校验看起来一切正常。这正是本仓最在意的那类失败
    （功能对、断言绿、口子开着）。所以这里 `follow_redirects=False`，手动跟跳，
    **每一跳都重新过 `guard_public_url`**（含相对 Location 的解析）。

    跳数超过 `max_hops` 即中止：无限跳转是廉价的拖死手段。

    `transport` 存在的**唯一**理由是让"跳转逐跳校验"这条判据能不打真网络地测到
    （`httpx.MockTransport`）；生产调用一律不传（同 `GwsClient(argv_prefix=…)` 的手法）。
    """
    import httpx

    deadline = time.monotonic() + timeout
    current = await aguard_public_url(url)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
        follow_redirects=False,
        transport=transport,
    ) as client:
        for _ in range(max_hops + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise httpx.TimeoutException(f"总时限 {timeout:g}s 用尽（含重定向）")
            async with client.stream(
                method, current, headers=headers or {}, timeout=remaining
            ) as res:
                if res.is_redirect:
                    location = res.headers.get("location")
                    if not location:
                        body, truncated = await _capped_body(res, max_bytes)
                        return httpx.Response(
                            res.status_code, headers=dict(res.headers),
                            content=body, request=res.request)
                    # 相对 Location 要先并成绝对地址，否则下一跳校验的是个残缺 URL
                    current = await aguard_public_url(urljoin(current, location))
                    continue

                body, truncated = await _capped_body(res, max_bytes)
                headers_out = dict(res.headers)
                if truncated:
                    headers_out["x-workbench-truncated"] = "1"
                return httpx.Response(
                    res.status_code, headers=headers_out, content=body, request=res.request)
    raise ValueError(f"重定向次数超过 {max_hops} 次，已中止（可能是跳转环）")
