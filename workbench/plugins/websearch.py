"""联网检索插件：给 Agent 一个**真的能查网页**的工具。

## 为什么必须自建（2026-09-15 官方文档核实结论）

用户问过"DeepSeek API 是不是有联网搜索能力"。答案是**没有**，两处文档都写死了：

| 接口 | 官方文档原话 | 出处 |
|---|---|---|
| Responses API | Tools 表里 `web_search` / `file_search` / `code_interpreter` / `computer_use` / `mcp` **等内置工具 = 忽略**（"Built-in tool types are ignored"） | [使用 Responses API](https://api-docs.deepseek.com/zh-cn/guides/responses_api) |
| Chat Completions | `tools` 只支持 `function`；内置工具类型同样被忽略 | [Responses API 参考](https://api-docs.deepseek.com/api/create-response) |

> 那条"`input` 中回传的 `web_search_call` item（例如**旧模型**此前请求产生的搜索结果）
> 仍会被还原并拼接进上下文"的注记，是"以前有过、现在没了"的官方旁证：
> **搜索结果作为输入被容忍，但服务端不会被驱动去搜**。想在 API 里联网只有一条路——
> 自己实现一个 `function` 工具。

## 后端选择：**实测驱动**（2026-09-15，本机国内网络，各打 6 次）

| 后端 | 成功率 | 平均耗时 | 结论 |
|---|---|---|---|
| `www.bing.com` RSS | 4/6 | ~3.0s | ✅ **主通道**：结构化 XML + **真实直链** |
| `cn.bing.com` RSS | 2/6 | ~8.2s | ❌ 换域名不解决问题 |
| `www.baidu.com/s` | 6/6 | ~0.4s | ❌ 返回 **1.4KB 反爬页**（无结果结构） |
| `www.sogou.com/web` | 6/6 | ~0.6s | ❌ 同上，5.4KB 反爬页 |
| `www.so.com/s`（360） | 6/6 | ~0.7s | ✅ **兜底**：466KB 真结果，但链接是 `so.com/link?m=…` 需解跳转 |
| `lite.duckduckgo.com` | 1/6 | ~3.1s | ❌ 基本不可用 |

两条可用通道的取舍很清楚：

- **Bing RSS 给直链**（`read_web_page` 能直接用），但成功率只有 ~67–80%，且失败是
  **突发成簇**的（连打 8 次可能是 ERR×4 然后 OK×4）——所以重试 + 冷却才有意义，
  单纯重试会把失败耗时从 4s 拉到 10s 而不提高成功率（这个反直觉的结果是实测出来的）。
- **360 几乎必成**，但只给跳转链接。因此兜底通道要**多发一次 HEAD 把跳转解开**
  （`_resolve_redirect`），代价是每条结果一次请求，只在主通道失败时才付。

> ⚠️ 已知边界：两条通道都依赖外网可达。企业网/隔离网里可能整条被拦，那时工具返回
> 明确错误（含 `read_web_page` 兜底建议），**绝不返回空列表让模型读成"网上没有"**。
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import time
from typing import Any
from urllib.parse import quote_plus, urlparse
from xml.etree import ElementTree

import httpx
from pydantic import BaseModel

from .. import net_guard
from .. import plugin_api as pa

PLUGIN_ID = "websearch"

_BING_RSS = "https://www.bing.com/search?format=rss&q={query}&count={count}"
_SO360 = "https://www.so.com/s?q={query}&rn={count}"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
MAX_RESULTS = 10
MAX_SNIPPET = 400
_ATTEMPTS = 2
_BACKOFF_S = 0.6
_HEADERS = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}


class SearchArgs(BaseModel):
    query: str = pa.Arg("检索词（自然语言或关键词；可用 site: 限定站点，如 site:nmpa.gov.cn）")
    count: int = pa.Arg("返回多少条结果", default=6, ge=1, le=MAX_RESULTS)
    freshness: str = pa.Arg(
        "时间范围限定：空 = 不限；day / week / month / year", default=""
    )


# ---------------------------------------------------------------- 主通道：Bing RSS


async def _bing_rss(query: str, count: int, freshness: str) -> list[dict[str, Any]]:
    url = _BING_RSS.format(query=quote_plus(query), count=count)
    if freshness:
        url += f"&filters=ex1%3a%22ez{_freshness_code(freshness)}%22"
    text = await _fetch(url)
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise ValueError(f"Bing 返回的不是合法 XML（{exc}）") from exc

    items: list[dict[str, Any]] = []
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        if not link.startswith(("http://", "https://")):
            continue
        items.append({
            "title": _clean(item.findtext("title") or ""),
            "url": link,
            "snippet": _clean(item.findtext("description") or ""),
        })
        if len(items) >= count:
            break
    return items


# ---------------------------------------------------------------- 兜底通道：360


async def _so360(query: str, count: int) -> list[dict[str, Any]]:
    """360 搜索 HTML → 结果。**解不开跳转的条目会被丢掉**，见下。

    坑（实测）：360 的结果链接是 `so.com/link?m=…`，而那个 `m` 参数只在**当次会话/短时间内**
    有效——事后重放会得到 `400`（实测 HEAD/GET 都是 400）。所以"解跳转"不是可靠操作：
    解不开时**宁可丢掉这条结果，也不要把它原样交给模型**——那会让模型拿着一个
    打不开的地址去调 `read_web_page`，把一次检索失败变成两次失败，而且它还会
    据此引用一个自己根本没读到的页面。
    """
    url = _SO360.format(query=quote_plus(query), count=count)
    text = await _fetch(url)
    if len(text) < 20_000:
        raise ValueError(f"360 返回的疑似反爬页（仅 {len(text)} 字符）")

    raw: list[dict[str, Any]] = []
    # 结果块：<h3 ...><a href="…" ...>标题</a></h3>
    for match in re.finditer(
        r'<h3[^>]*>\s*<a[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
        text, re.S,
    ):
        href = html.unescape(match.group("href"))
        if not href.startswith(("http://", "https://")):
            continue
        if "so.com/link" not in href and "so.com/ns" in href:
            continue  # 站内聚合页（新闻/图库入口），不是检索结果
        title = _clean(match.group("title"))
        if not title:
            continue
        raw.append({"title": title, "url": href, "snippet": ""})
        if len(raw) >= count:
            break

    if not raw:
        raise ValueError("360 的结果页里没解析出条目（前端结构可能已变）")

    resolved = await asyncio.gather(
        *(_resolve_redirect(i["url"]) for i in raw), return_exceptions=True,
    )
    items: list[dict[str, Any]] = []
    for item, real in zip(raw, resolved, strict=True):
        # 只收**解开了的真实地址**（空串/异常/仍是跳转地址的一律丢，见 _resolve_redirect）；
        # 再排掉 360 自家的垂类聚合页（文库/新闻/图库）——那些不是检索结果，
        # 交给模型只会让它去读一个聚合入口
        if (isinstance(real, str) and real.startswith(("http://", "https://"))
                and "so.com/link" not in real
                and not _is_search_vertical(real)):
            item["url"] = real
            items.append(item)
    return items


#: 360 自家的垂类聚合入口（不是检索结果，别当结果交出去）
_VERTICAL_HOSTS = ("wenku.so.com", "news.so.com", "image.so.com", "tv.360kan.com",
                   "baike.so.com", "zhidao.so.com", "map.so.com")


def _is_search_vertical(url: str) -> bool:
    host = _host_of(url)
    return any(host == v or host.endswith("." + v) for v in _VERTICAL_HOSTS)


async def _resolve_redirect(url: str) -> str:
    """HEAD 跟随跳转拿真实 URL；**解不开返回空串**（调用方据此丢弃该条）。

    返回空串而不是原地址，是这里唯一容易写错的地方：原地址是个"看起来能用、
    实际打不开"的跳转链接，把它交出去会让模型接着拿它去调 `read_web_page`
    再失败一次，而且会据此引用一个它根本没读到的页面。

    360 的 `m=` 参数会过期（实测事后重放得到 400），所以这不是"偶尔失败"，
    而是多数情况下解不开——因此 `_so360` 会把这类条目直接丢掉。

    ⚠️ 走 `net_guard.guarded_get` 而不是裸 httpx：这条路径**也在发请求**，
    而它拿到的地址来自**外部搜索结果**。少了守卫就等于"检索通道"是绕过出站
    策略的后门。守卫拒绝时同样返回空串（该条结果被丢弃），与本函数的契约一致。
    """
    if "so.com/link" not in url:
        return url
    try:
        res = await net_guard.guarded_get(
            url, headers=_HEADERS, timeout=12.0, method="HEAD"
        )
        final = str(res.url)
        if (res.status_code < 400 and final.startswith(("http://", "https://"))
                and "so.com/link" not in final):
            return final
    except (httpx.HTTPError, ValueError):
        pass
    return ""


# ---------------------------------------------------------------- 取数


async def _fetch(url: str) -> str:
    """取一次页面，带**少量**重试。

    为什么不重试 3 次以上：实测失败是**突发成簇**的（连打 8 次可能前 4 次全 ERR），
    重试全落在同一个窗口里，只会把失败耗时从 4s 拉到 10s 而不提高成功率。
    两次足够覆盖单次抖动，剩下的交给兜底通道。

    走 `net_guard.guarded_get` 而不是裸 httpx：这里的目标主机是**硬编码**的
    （bing / so.com），风险本来就低，但**盲目跟随重定向**会让"公网地址 302 到内网"
    这条路径重新出现。统一走守卫之后，"`workbench/` 里不存在 `follow_redirects=True`"
    就成为一条可机检的不变量（见 `tests/test_net_guard.py`），不必逐个模块记。
    """
    last: Exception | None = None
    for attempt in range(_ATTEMPTS):
        try:
            res = await net_guard.guarded_get(url, headers=_HEADERS, timeout=30.0)
            res.raise_for_status()
            return res.text
        except (httpx.HTTPError, ValueError) as exc:
            last = exc
            if attempt < _ATTEMPTS - 1:
                await asyncio.sleep(_BACKOFF_S)
    raise ValueError(f"请求失败（{_ATTEMPTS} 次，最后一次 {type(last).__name__}: {last}）")


def _clean(text: str) -> str:
    """HTML 片段 → 纯文本（检索结果是外部内容，绝不按 HTML 渲染）。"""
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return " ".join(text.split())[:MAX_SNIPPET]


def _freshness_code(freshness: str) -> str:
    """Bing 的新鲜度代码（ez1=过去一天 / ez2=过去一周 / ez3=过去一月 …）。"""
    return {"day": "1", "week": "2", "month": "3", "year": "4"}.get(freshness.lower(), "")


def _host_of(url: str) -> str:
    try:
        return urlparse(url).netloc
    except ValueError:
        return ""


# ---------------------------------------------------------------- 工具


async def web_search(args: SearchArgs, ctx: pa.ToolContext) -> str:
    """两通道链式检索：Bing RSS（直链）→ 360（解跳转）。"""
    query = str(pa.require(args.query, "query 不能为空")).strip()
    count = args.count
    freshness = args.freshness.strip()

    started = time.perf_counter()
    errors: list[str] = []
    backend = ""
    results: list[dict[str, Any]] = []

    try:
        results = await _bing_rss(query, count, freshness)
        backend = "bing-rss"
    except Exception as exc:  # noqa: BLE001 —— 主通道失败转兜底，不是终态
        errors.append(f"bing-rss: {exc}")

    if not results:
        try:
            results = await _so360(query, count)
            backend = "so360"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"so360: {exc}")

    if not results and errors:
        # 两条通道都挂了：必须**明确报错**。返回空列表会被模型读成"网上没有这东西"，
        # 那是比失败更坏的结局（它会据此下结论）
        raise ValueError(
            "联网检索通道全部不可达（" + "；".join(errors) + "）。"
            "可以改用 read_web_page 直接读已知网址的页面原文"
        )

    return json.dumps(
        {
            "query": query,
            "backend": backend,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "count": len(results),
            "results": [
                {**r, "host": _host_of(r["url"])} for r in results
            ],
            "note": (
                "以下是搜索引擎返回的标题+摘要，**可能过时或与原文不符**。"
                "要引用具体条文/数据时，先用 read_web_page 打开对应 URL 核对原文"
                + (
                    "。（本次走的是兜底通道：360 的跳转链接多数已失效，解不开的条目"
                    "已被丢弃——**结果偏少是正常的**，可换个检索词重试，"
                    "或对已知网址直接用 read_web_page）"
                    if backend == "so360" else ""
                )
            ),
            "empty_reason": "" if results else "两条检索通道都返回了空结果（检索词可能太窄）",
        },
        ensure_ascii=False,
    )


PLUGIN = pa.Plugin(
    id=PLUGIN_ID,
    title="联网检索",
    description="自建联网检索（DeepSeek API 不支持内置 web_search，故由本工作台实现）",
    version="1.1.0",
    tools=[
        pa.ToolSpec(
            name="web_search",
            description=(
                "联网检索网页（返回标题/链接/摘要）。适合查法规更新、官方公告、"
                "报错与版本信息这类训练数据里没有或已过时的内容；"
                "拿到 URL 后用 read_web_page 读原文核对"
            ),
            args_model=SearchArgs,
            handler=web_search,
            risk=pa.Risk.READ,
            # 预算给足：主通道最慢实测 18.5s（×2 次重试）+ 兜底通道解析跳转
            timeout_s=120,
            audit_extra=pa.audit_env_key("query"),
        ),
    ],
)


def register(hub: pa.ToolHub) -> None:
    hub.add_plugin(PLUGIN)
