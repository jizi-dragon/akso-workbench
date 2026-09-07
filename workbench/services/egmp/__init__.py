"""egmp —— eGMP 平台客户端 Python 化（阶段 3 全量实现：读路径 + 写路径 + Monitor）。

原 akso-cc / akso-cc 的 Node 子进程封装已由本包原生承接（routes_insight /
routes_factory 直接调用），运行时不再依赖 node 与两个原仓库；
adapters/*.json 降级为只读参考存档。

模块地图：
- client.py / auth.py / cache.py / queries.py   HTTP 内核（信封/分页/token 缓存/查询层）
- insight/                                       读路径（盘点/理解/蜘蛛/networkx）
- writers/                                       写路径（蓝图两层校验/幂等/全部创建器）
- orchestrate.py / complexity.py / generate.py   编排闭环 + LLM 蓝图生成（mogul DeepSeek 融合）
- monitor/                                       Monitor 录制/查询/解读/复现（Playwright 载体）
"""

__all__ = ["client", "auth", "cache", "checkpoint", "queries", "complexity",
           "generate", "orchestrate", "insight", "writers", "monitor"]
