"""验证：web_search 的**兜底通道**行为是否诚实。

为什么值得一份专门的验证：兜底通道平时跑不到（主通道成功就返回了），
而"跑不到的分支"最容易腐烂。本脚本把 `_bing_rss` 替换成必然失败，逼工具走 360 通道，
断言的是**两种可接受结局之一**：

  A. 兜底给出**可直读的真实链接**（跳转解开、垂类聚合页排除）；
  B. 兜底也拿不到可用链接 → 工具**明确报错**（含 read_web_page 兜底建议）。

**不可接受的结局**：返回打不开的跳转地址、或返回空列表。前者让模型拿着废链接
再去失败一次并据此引用没读到的页面；后者被读成"网上没有这东西"。
"""
import asyncio
import json
import sys
import time

sys.path.insert(0, ".")

from workbench.plugin_api import ToolContext
from workbench.plugins import websearch as ws


async def main() -> int:
    original = ws._bing_rss

    async def broken(*_args, **_kwargs):
        raise ValueError("模拟主通道不可达（验证脚本注入）")

    ws._bing_rss = broken
    started = time.perf_counter()
    try:
        data = json.loads(await ws.web_search(
            ws.SearchArgs(query="NMPA 药品记录与数据管理规范", count=3, freshness=""),
            ToolContext(),
        ))
    except ValueError as exc:
        ms = int((time.perf_counter() - started) * 1000)
        assert "read_web_page" in str(exc), "报错里必须给出兜底建议"
        print(f"结局 B（诚实报错，{ms}ms）：{str(exc)[:110]}")
        print("FALLBACK_CHANNEL_OK  拿不到可用链接时明确报错，没有交出废地址")
        return 0
    finally:
        ws._bing_rss = original

    ms = int((time.perf_counter() - started) * 1000)
    print(f"结局 A：backend = {data['backend']}  {ms}ms  {data['count']} 条")
    for item in data["results"]:
        print(f"  - {item['title'][:44]:46} {item['url'][:70]}  [{item['host']}]")

    assert data["backend"] == "so360", f"没走兜底通道：{data['backend']}"
    leaks = [i for i in data["results"] if "so.com/link" in i["url"]]
    assert not leaks, f"把打不开的跳转地址交给了模型：{[i['url'] for i in leaks]}"
    verticals = [i for i in data["results"] if ws._is_search_vertical(i["url"])]
    assert not verticals, f"把聚合页当结果交出去了：{[i['url'] for i in verticals]}"
    print(f"FALLBACK_CHANNEL_OK  {data['count']} 条全部是可直读的真实链接")
    return 0


sys.exit(asyncio.run(main()))

