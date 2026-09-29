"""验证：双通道 web_search 的实际成功率、走的是哪条通道、耗时。"""
import asyncio
import json
import sys
import time

sys.path.insert(0, ".")

from workbench.plugin_api import ToolContext
from workbench.plugins.websearch import SearchArgs, web_search


async def main():
    ok, backends, times = 0, {}, []
    for i in range(8):
        started = time.perf_counter()
        try:
            data = json.loads(await web_search(
                SearchArgs(query=f"NMPA 计算机化系统验证 第{i}次", count=3, freshness=""),
                ToolContext(),
            ))
            ok += 1
            backends[data["backend"]] = backends.get(data["backend"], 0) + 1
            verdict = f"OK  {data['backend']:9} {data['count']} 条"
            if i == 0:
                for r in data["results"][:2]:
                    print("     样例:", r["title"][:40], "|", r["url"][:60], "|", r["host"])
        except Exception as exc:  # noqa: BLE001
            verdict = f"ERR {type(exc).__name__}"
        ms = int((time.perf_counter() - started) * 1000)
        times.append(ms)
        print(f"{i + 1:2d}. {verdict:28} {ms:6}ms")
    print(f"\n成功 {ok}/8（{ok / 8:.0%}）  通道分布 {backends}")
    print(f"平均 {sum(times) // len(times)}ms  最慢 {max(times)}ms")


asyncio.run(main())
