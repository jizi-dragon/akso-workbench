"""egmp —— eGMP 平台客户端 Python 化（只读 HTTP 路径）。

原 akso-cc 的 Node 子进程封装已由本包原生承接（`routes_insight` 直接调用），
运行时不再依赖 node 与原仓库；`adapters/akso-cc.json` 是只读参考存档。

模块地图：
- client.py / auth.py / cache.py / queries.py   HTTP 内核（信封/分页/token 缓存/查询层）
- kernel/                                        **端点与枚举的唯一事实源**（endpoints/semantics/urls）
- insight/                                       读路径（登录 / 蜘蛛五步织网 + networkx）
- replay.py                                      离线回放夹具客户端（insight 的离线门禁用）

> ## 2026-10-19：Monitor 随内置浏览器一起下线
>
> 用户确认未来不再需要 UI 自动登录 / 页面抓取 / 截图，`monitor/`（Playwright 录制引擎）
> 与 `browser_pool` / `autologin` 一并移除。账号连通性验证仍走 `accounts_login`
> （httpx 直连），不依赖浏览器。

> ## 2026-09-22：写路径整体移除
>
> 配置工厂（蓝图两层校验 / 幂等创建 / 8 步管道 / 拓扑编排 / 断点续跑）**已整体删除**：
> `writers/`（含 blueprint/spec/layouts/fields/objects/workflows/picklists/menus/lifecycle/
> idempotency/responses/endpoints 垫片）、`orchestrate.py`、`checkpoint.py`、
> `api/routes_factory.py`、`plugins/factory.py` 与前端工厂页一并下线。
>
> **平台知识没有跟着丢**——它被迁进了两份**不依赖本包代码**的文档：
> [docs/PLATFORM-OPERATIONS-LEARNED.md](../../../docs/PLATFORM-OPERATIONS-LEARNED.md)
> （五层端点 / 真机线格式 / 样本量 n / 逐条平台事实台账）与
> [docs/FACTORY-PLATFORM-COGNITION.md](../../../docs/FACTORY-PLATFORM-COGNITION.md)
> （手册语义与配置分层认知），原始取证报告在 `factory-tuning/reports/**`。
>
> 更早的 `complexity.py` / `generate.py` 已于 2026-09-20 删除，理由同族。
> 因此本包现在**只读**：写平台的能力不在这个仓库里。
>
> ⚠️ `cache.py` + `queries.py`（进程内元数据缓存与查询层）**刻意保留**：它们不是工厂专属，
> 而是平台读路径的元数据层，且承担一条**环境隔离不变量**（`QueryCache` 键必须带环境作用域，
> 否则同进程的四个部署会互相污染）——那条不变量有独立门禁
> （`tests/test_env_isolation.py` 的「缓存隔离」一节），删了它会连带删掉一个与工厂无关的安全属性。
"""

__all__ = ["client", "auth", "cache", "queries", "kernel", "replay", "insight"]
