"""egmp —— eGMP 平台客户端 Python 化（阶段 3 全量实现：读路径 + 写路径 + Monitor）。

原 akso-cc / akso-cc 的 Node 子进程封装已由本包原生承接（routes_insight /
routes_factory 直接调用），运行时不再依赖 node 与两个原仓库；
adapters/*.json 降级为只读参考存档。

模块地图：
- client.py / auth.py / cache.py / queries.py   HTTP 内核（信封/分页/token 缓存/查询层）
- insight/                                       读路径（登录 / 蜘蛛五步织网 + networkx）
- writers/                                       写路径（蓝图两层校验/幂等/全部创建器）
  - writers/spec.py                              **给 agent 的蓝图规格 + 给用户的审阅材料**
- orchestrate.py                                 编排闭环（拓扑排序 / 8 步管道 / checkpoint）
- monitor/                                       Monitor 录制/查询/解读/复现（Playwright 载体）

> `complexity.py` 与 `generate.py`（复杂度评估 + LLM 蓝图生成）**已于 2026-09-20 删除**：
> 它们服务的是 `command="orchestrate"` 那条"需求描述 → 另一个 LLM 循环 → 蓝图"的路，
> 而那条路只有一个字符串的输入，是**对话链路的降级复制品**。
> 现在"一句话变成蓝图"由对话 Agent 承担（`factory_blueprint_spec` +
> `factory_preview_blueprint`），见 [docs/FACTORY-AGENT-GAP.md] 问题④。
"""

__all__ = ["client", "auth", "cache", "checkpoint", "queries",
           "orchestrate", "insight", "writers", "monitor"]
