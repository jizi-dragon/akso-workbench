# 代码库总览（CODEBASE_OVERVIEW）

> **2026-10-19 精简重写**：原文件是 2026-10-07 的 498 行快照，其中相当一部分
> （"停止键坏了""build_context 零调用方""测试 1011 项"）**已经修掉或过期**，
> 继续维护它只会变成第二个事实源。现在它只承担一件事：
> **一张能用的代码地图 + 一个指向当前风险台账的索引**。
>
> 想要"为什么这么设计"看 [CORE-ARCHITECTURE.md](CORE-ARCHITECTURE.md) 与
>；想要"和 WorkBuddy 差在哪"看 [AGENT-BENCHMARK.md](AGENT-BENCHMARK.md)；
> 想要"真机踩过什么"看 [PITFALLS.md](PITFALLS.md)。

---

## 一、规模（2026-10-19 实测，物理行）

| 区域 | 文件 | 行 |
|---|---:|---:|
| `workbench/**/*.py` | 95 | 24,684 |
| `workbench/static`（js+css+html） | 34 | 13,717 |
| `tests/**/*.py` | 73 | 19,241 |
| `tools/*.py` + `*.mjs`/`*.js`/`*.ps1` | 34 | 7,568 |
| `docs/**/*.md` | 20 | 7,934 |
| 受版本控制文件 | 291（含本轮新增） | — |

口径：Python `splitlines()`（`Get-Content | Measure-Object -Line` 会漏空行，不要用）。

## 二、技术栈

| 项 | 实际 |
|---|---|
| 语言 | Python 3.12（唯一真源 `.python-version`）、原生 ES module（无框架/打包器） |
| 服务端 | FastAPI + uvicorn；SSE（`text/event-stream`） |
| 桌面壳 | **Tauri 2（Rust）+ WebView2**，动态端口、Python sidecar、NSIS per-user（2026-10-19 Electron 退役） |
| 浏览器自动化 | **已下线**（2026-10-19：Playwright / Chromium / CDP / browser_pool 全部删除） |
| 数据 | SQLite（WAL + 外键 + 单连接 RLock），迁移 1–26 |
| 凭据 | 口令 Fernet；会话令牌 Fernet；平台 JWT 明文缓存（已知口径不齐） |
| 模型 | DeepSeek OpenAI 兼容协议（流式 + function calling + 前缀缓存） |
| MCP | 自研 stdio 客户端（2026-10-19 新增，见 [MCP.md](MCP.md)） |
| 图分析 | networkx（蜘蛛五步织网） |
| 附件 | python-docx / openpyxl / pypdf（纯 Python 轮子） |

## 三、八层结构（叙述权威在 CORE-ARCHITECTURE §2）

```
L8 传输  routes_chat.py（SSE） · routes_agent.py（REST） · routes_mcp.py（MCP 状态）
L7 循环  harness/loop.py：call → tool → observe → repeat
L6 中枢  plugin_api.ToolHub：core 插件 + installed 层（Skill / MCP 工具）
L5 确认门 approvals.py：单次令牌 · 参数指纹 · 唯一审计入口
L4 沙箱  sandbox/：Fail-Closed 策略 + 工作区根/权限预检 + 解释器白名单 + Job Object 树杀
L3 技能  skills/：SKILL.md（wraps / script）+ ZIP 准入
L2 上下文 cache.py + harness/context.py：append-only 前缀 + 冻结摘要
L1 模型  services/deepseek.py：SSE 流式 + function calling
```

★ **工具只有一个中枢**：`ToolHub` 是 `_tools` 的唯一写入者。三条来源最终都是 `ToolSpec`：

| 来源 | 谁注册 | 何时 |
|---|---|---|
| 内置插件 | `plugins/__init__.py::_MODULES` | 导入期一次 |
| 用户 Skill | `skills.library` → `register_installed` | 运行期 |
| MCP server | `mcp_registry.McpRegistry` → `register_installed` | 启动后台 / 手动重连 |

## 四、进程与端口

| 进程/端口 | 角色 |
|---|---|
| Tauri 主进程 | 单实例、窗口、外链、动态选端口、拉起 sidecar、退出清理 |
| Python sidecar（动态端口） | 业务、Agent、MCP 全在这里 |
| ~~Electron 控制口（`18767`）~~ | 2026-10-19 随 Electron 删除 |
| ~~Chromium CDP（`18766`）~~ | 2026-10-19 已删除（内置浏览器下线） |

Tauri 用 `free_port()` 选空闲端口，经 `WORKBENCH_PORT` 下发；Web 模式仍默认 18765。
父进程非正常死亡时 sidecar 自退（`lifecycle.watch_parent`）。

## 五、关键模块

| 路径 | 职责 |
|---|---|
| `workbench/api/__init__.py` | 应用装配、lifespan、**可选路由逐个容错**、MCP 后台装载 |
| `workbench/harness/loop.py` | 回合引擎：步数/停止/超时/确认；事件全在这里产生 |
| `workbench/cache.py` | 稳定前缀、冻结摘要、用量账 |
| `workbench/plugin_api.py` | `ToolSpec` / `ToolHub` / 风险与信任 / 截断 / 审计 |
| `workbench/approvals.py` | 风险门 + 模型发起的确认（两条通道，一张表） |
| `workbench/hub.py` | 宿主装配器（`approvals.install` 必须先于 `plugins.load_all`） |
| `workbench/mcp_registry.py` | MCP 连接、工具注册、状态、健康巡检与自动重连 |
| `workbench/services/mcp.py` | stdio + Streamable HTTP JSON-RPC 客户端、配置解析、环境白名单、`ping` / `list_changed` |
| `workbench/services/egmp/` | 平台知识层：`kernel/`（纯知识）· `client.py` · `insight/`（Monitor 已于 2026-10-19 删除） |
| `workbench/sandbox/` | 不受信代码的限损层（**不是安全边界**） |
| `workbench/skills/` | SKILL.md 能力包：装/卸/删 + ZIP 准入 |
| `workbench/static/` | 原生 ES module 前端；`index.html` 是唯一入口；云端登录入口在账号中心，独立 `cloud.html` 已删除（2026-10-20） |
| `workbench/static/js/artifact-preview.js` | 产物预览：Markdown / drawio / JSON / CSV / 图片 / 日志，离线自足 |
| `src-tauri/` | Tauri 2 壳（Rust）：窗口、sidecar、单实例、外链、updater；页面链接一律交系统浏览器 |

## 六、数据与配置

- 数据目录：`%APPDATA%\Workbench`（`WORKBENCH_DATA` 可覆盖）。
- 数据库：`workbench.db`，迁移 1–26（**只增不改**）。
- 配置五层：环境变量 / settings 表 / 代码常量 / 数据目录文件 / 前端常量。
- 新增的数据目录文件：`mcp.json`（MCP server 配置，可含凭据，已列入敏感路径）、
  `sandbox-workspace.json`（工作区根 / 只读 / 联网 / 派生进程开关）。

## 七、入口

| 场景 | 命令 |
|---|---|
| 开发态 | `.\.venv\Scripts\python.exe -m workbench.main` |
| 冻结态 | `WorkbenchServer.exe --server --port N`（`server_entry.py`） |
| 壳开发态回退 | `.\.venv\Scripts\python.exe -m uvicorn workbench.api:app` |
| 前端 | `/static/index.html` → `static/js/app.js` |
| 测试 | `.\.venv\Scripts\python.exe -m pytest`（默认跳过 slow） |

## 八、约定（新协作者必须模仿）

1. 工具定义只有一处：内置在 `plugins/*.py`；MCP 工具由 `mcp_registry.py` 在运行期生成。
   两者都必须显式声明 `risk` / `trust` / `reversibility`。
2. 同步阻塞一律 `asyncio.to_thread`（I11）。
3. 插件**不得** import `workbench/api/**`；`services/` 不 import `plugin_api`。
4. DB 只走迁移；凭据不落明文；审计维度不含凭据。
5. 文档分层：决策 = `CORE-ARCHITECTURE.md`；`API/CONFIG/SCHEMA/TOOLS` = 随代码走；PITFALLS = 真机坑。
6. 写完断言先问「拆掉什么它才会红」。

## 九、当前风险索引（2026-10-19）

> 详细的对标与优先级见 [AGENT-BENCHMARK.md](AGENT-BENCHMARK.md) §3。
> 这里只列**今天仍然成立**的条目，修掉就从这里删掉（本文件不再是完整台账）。

| 级别 | 项 | 位置 |
|---|---|---|
| P1 | 云端账号库支持只读拉取与「本地→云端」一次性推送；仍不做双向自动合并/冲突 UI | `services/vault.py` |
| P1 | **桌面壳日志面未统一**：`tauri-sidecar.log` 是原始文本（未走统一脱敏），渲染层 JS 错误不入日志 | `src-tauri/src/main.rs` · `logging_setup.py` |
| P1 | **sidecar 运行期退出无自动重启**；Tauri 启动页会提示超时，但运行中崩溃后主窗不会自愈 | `src-tauri/src/main.rs` |
| P1 | 无长期记忆（只有会话摘要） | 缺失 |
| P1 | MCP 只支持 stdio；健康巡检 / 自动重连已补 | `services/mcp.py` |
| P2 | 前端一次只能跑一个回合（`isRunning` 全局） | `static/js/chat.js` |
| P2 | 对象分析产物已有卡片墙 / CSV / 图片预览；仍无通用附件 / Skill 产物 / diff 面板 | `static/js/insight.js` |
| P2 | 开发态 `workbench-<日期>.jsonl` 默认写**文本**（打包态才 JSONL）；`tail()` 有回退，但外部消费者会误判格式 | `logging_setup.py` |
| P2 | Tauri 更新器只完成签名/清单/构建，检查→提示→安装 UI 未接；Windows Authenticode 未配置 | `src-tauri/src/main.rs` · `tools/build_tauri.ps1` |
| P2 | 无调度器 | 缺失 |
| P2 | 附件 HTTP 层无 pytest | `routes_attachments.py` |
| P3 | 平台 JWT 明文缓存 | `services/egmp/client.py` |
| P3 | `plugin_api.py` / `loop.py` 偏大 | — |
| P3 | 0.4.1 Tauri 安装包已本地构建，尚未发布到内部 HTTPS 源 | `src-tauri/target/release/bundle/nsis/` |

**刻意保留、不要当缺陷修**：`/api/*` 无认证（本机回环单用户）、沙箱不是安全边界、
待确认操作跨重启不恢复、并发只对逐个举证的工具开放、硬上限只算消息不算工具 schema。

## 十、怎么保持这张地图不过期

- 改模块边界 / 加一层 → 改本文 §三/§五；
- 修掉一条风险 → 从 §九删掉（不要留着再标"已修"）；
- 新增判据 → [AGENTS.md](../AGENTS.md) §3 的矩阵加一行；
- 数字（工具/迁移/坑）由脚本核对，**不要手抄**：`gen_tools_doc.py`、
  `tests/test_agents_md.py`、`check_doc_links.py`。
