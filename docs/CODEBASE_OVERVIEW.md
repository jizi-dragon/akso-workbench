# Codebase Overview（代码库总览）

_由 learn-codebase 技能生成；**2026-09-16 复核**，锚定 `c8753ce`（v0.3.12，main）。_

> **修订轨迹**：`1e82439`（v0.1，pywebview 壳，已被 git 历史取代）→ `2e725a5`（v0.2.2，Electron sidecar 改造）→
> `d0efa84`（v0.3.5，2026-09-13 全面重写）→ **`c8753ce`（v0.3.12，2026-09-16 复核）。**
> 上一版锚定 v0.3.5：正文的插件层是新的，但**页头与 Summary 的版本号、代码体量口径、插件与迁移计数**三处已过期。
> **★ 2026-09-24 现状（配置工厂整体移除后）**：**4 个插件 / 18 个工具**（16 read + 2 write + 0 dangerous）、
> **迁移 1–22**。紧接下面那句"五插件 / 20 工具 / 迁移 1–20"是 `c8753ce` 当时（v0.3.12）的实测，**已过期**——
> 保留它是为了留痕，**引用前先按 §关键模块 的数字**。
> 本轮按代码实测逐条重校（口径见下）：**五插件 / 20 工具**（原写四插件 / 19 工具）、**迁移 1–20**（原写 1–16）、
> 行数口径全面上修。失效的历史架构文档见 [docs/archive/](archive/README.md)。
>
> **2026-09-16 品牌中立化**：产品名 `AksoWorkbench` → **`Workbench`**（包名 `workbench`、数据目录
> `%APPDATA%\Workbench`、env `WORKBENCH_*`、sidecar 产物 `WorkbenchServer.exe`）。**代码里仍会出现
> `akso-cc` / `akso-auto` / `akso-mogul` / `services/egmp`**——那些是上游项目名与平台适配层，**刻意保留**；
> 也会看到少量 `AKSO_*` / `akso-workbench-*` 字面量，那是**兼容读取**（旧 env / 旧备份格式 / 旧快照格式）。
> 判断标准与"刻意不改"清单见 [ADR-0013](adr/0013-brand-neutralization-workbench.md)。
>
> **2026-09-17 增补（最新）**：新增 **`workbench/sandbox/`**（不受信代码的执行底座，
> [ADR-0014](adr/0014-sandbox-for-untrusted-code.md)）与 **`workbench/skills/`**
> （Skill 共享库与安装，含**脚本式 Skill** 的沙箱执行）；迁移 **19（`sandbox_run`）/ 20（`skill_audit`）**
> 随之落地，本文件的模块表、迁移账、行数口径与端点计数已同步。
>
> **承接说明**：`AGENT.md`（原维护者接手手册）已于 2026-09-13 因整体滞后被删除。其可用内容按变更频率拆分承接：
> §2 代码地图 / §3 六条红线 → 本文件；§4 已知问题台账 → 本文件「风险与毛边」（**编号 C/S/T/B/D 原样保留**）；
> §5 环境坑 22 条 → [PITFALLS.md](PITFALLS.md)；§7 验收基线 → 本文件「验收基线实测」与 [CONTRIBUTING.md](../CONTRIBUTING.md) §7。
> 原文可用 `git show d0efa84:AGENT.md` 取回。

## Summary（定位）

**Workbench（`workbench` v0.3.12，仓库 `akso-mogul`）** 是阿克索实施工程师的**单机 Windows 工作台**，把四个来源项目（akso-cc / mogul_simulator / akso-auto / quick-login）的能力**内化**为一个桌面应用：对象分析（蜘蛛织网 + networkx 图分析，原「平台洞察」）、统一账号库（Fernet 加密）、多账号快捷登录（一个 Chrome 内并行身份）、Monitor 监听录制解读。
（~~配置：蓝图校验 + 幂等创建 + 拓扑编排，原「配置工厂」~~ → **2026-09-22 整条链已删除**，见 §Architecture 的说明。）

架构分工（用户定稿）：

- **桌面应用（Electron + FastAPI sidecar）= 数据面 + 触发面**；
- **用户自己的 Chrome 里装载的 MV3 扩展 = 执行面**（六平面隔离 / 会话切换 / 自动填表）；
- **内置 Chromium（Playwright）= 监听与自动化面**（Monitor 录制 / 免密直达 / 自愈）。

终端用户**零依赖原四仓库、零依赖 Node、零联网**（首次装载扩展除外）。服务端仅监听 `127.0.0.1`，**无鉴权**（单用户本机模型，见风险 R1）。

## Tech stack（技术栈）

- **语言：** Python ≥3.12（服务端全部逻辑，`workbench/**.py` **94 文件 17,850 行**）；TypeScript（扩展，`extensions/quick-login/**/*.ts` **24 文件 5,674 行**，不含 `node_modules`/`dist`）；JavaScript（Electron 主进程，`desktop/*.js` **5 文件 1,540 行**）；原生 ES 模块 + CSS（前端 `workbench/static` **23 个 js/css 文件 7,466 行**，另有 8 个 html + 2 个字体，**无框架、无构建步骤**）
  > 口径说明：本表行数为**物理行数**（含空行），`workbench/**` 实测于 2026-09-17（沙箱层与 Skill 共享库落地后）。用 `Get-Content \| Measure-Object -Line` 统计会漏掉空行（例如 `client.py` 会得 153 而非 197），已删除的原 `AGENT.md` 里的 8,167 行即为此类偏低的计数。**上一版（v0.3.5）的 70 文件 10,172 行 / 24 前端文件 5,947 行 / 4 个壳文件 1,006 行均已过期**——增量主要来自插件层（`plugin_api` 646 + `approvals` 350 + `cache` 287 + `plugins/` 1,374）、**不受信代码执行层（`sandbox/` 1,150 + `skills/` 1,775）**，以及新增的前端 `chat-tools.js` / `settings.js` / `sandbox.js` / `skills.js`。
- **框架 / 运行时：** FastAPI + uvicorn[standard]（REST + SSE + 静态托管）；pydantic v2；**Electron 33** 桌面壳（窗口 / 托盘 / 全局热键 / 更新器 / 出网通道 / sidecar 生命周期）
- **构建 / 包管理：** uv（`uv.lock`，默认清华镜像 `pyproject.toml:37-39`）；hatchling；ruff（`E4/E7/E9/F/I/B`，line-length 110）；**PyInstaller**（`build` extra，`workbench/server.spec` → `dist/WorkbenchServer/` onedir）；**electron-builder → NSIS**（`tools/build.ps1` 一键出安装包）；esbuild 0.24（扩展打包）；版本真源 `pyproject.toml`
- **存储 / 外部服务：** SQLite（WAL，**迁移 1–22**）；Playwright chromium；cryptography Fernet；DeepSeek API；networkx；GitHub Releases（electron-updater）
- **端口：** **固定常量** 18765 服务 · 18766 CDP · 18767 壳控制服务，**不顺延**（[ADR-0012](adr/0012-fixed-ports.md) 取代
  [ADR-0011](adr/0011-port-and-session-negotiation.md) §D-1）。被占则报错退出并说清占用者是谁；
  仍经环境变量下发 + 公布到 `runtime/desktop-ports.json` + 由 `/whoami` 答给扩展（那三条是**身份**手段，与顺延无关）。
  壳侧真源 `desktop/ports.js::PORTS`，Python 侧默认值必须一致（`tests/test_ports_contract.py` 钉住）
- **已应用技能：** `learn-codebase`（本文件）；分析期另派两个子代理分别深挖**扩展执行面**与**构建/发布链**，其结论已并入下文并逐条对码核验。

## Architecture（架构）

```
Electron 壳 desktop/（只做壳：sidecar 生命周期 / 窗口 / 托盘 / 热键 / 更新 / 出网通道 / 端口协商）
  │  main.js · ports.js（端口协商与公布）· updater.js · proxy.js · shell-state.js
  ├─ spawn → FastAPI workbench/（全部业务，基准 :18765）——端口经 env + --port 下发
  ├─ 壳控制服务（基准 :18767；按账号 persist: 分区开户/聚焦/关窗 + /whoami 端口发现）
  └─ CDP（基准 :18766；必须在 app ready 前设置，故端口在 whenReady 里先协商完再设）→ browser_pool.connect_over_cdp
        ├─ 用户 Chrome 扩展 = 执行面：六平面隔离 + 会话切换 + 自动填表
        └─ 内置 Chromium   = 监听/自动化：Monitor 录制 / 自动登录 / 免密持久化
```

服务端分层（`workbench/`）：

```
api/（15 个路由域 = 3 个 fork 直挂 + 12 个容错注册 + lifespan + 静态托管，见 api/__init__.py:34-65）
  ├─ fork 存量：routes_conversations / routes_settings / routes_chat(SSE)
  ├─ 聚合层：routes_modules（模块注册表 + 依赖体检）
  ├─ egmp 原生：routes_insight（洞察两命令 login/spider）　~~routes_factory（蓝图/创建/编排）~~ → 2026-09-22 删除
  ├─ 账号中心：routes_accounts + routes_browser（托管浏览器）
  ├─ 执行面蓝图：routes_extension（快照 + 指令长轮询 + 状态）**整段需访问令牌**
  ├─ 壳协作面：routes_shell（/api/shell/*：拉起 Chrome / 安装引导 / 读更新状态；**不碰凭据、不需令牌**）
  ├─ 监听：routes_monitor　│　更新：routes_update（读壳状态文件）
  └─ Agent：routes_agent（**工具目录/调用/待确认/审计/自检**，全部转发给 plugin_api）
extension_auth.py（**`/extension/*` 访问令牌**：ASGI 中间件按前缀守备 + 自举端点 + 宽限期）
  ├─ 为什么用中间件而不是逐路由 Depends：漏加一条依赖就静默开一个口子，而按前缀守是结构保证
  └─ 豁免面只有两条（/extension/hello 自举、/extension/health 徽标判据），有测试与 P10 机检钉住
plugin_api.py（**插件 SDK 契约**：ToolHub / ToolSpec / Risk / ToolContext / Plugin）
  ├─ 唯一的能力扩展面；不 import 任何插件、不给插件暴露 app
  └─ 审计写入器与确认门由 approvals.install(hub) 注入（故本文件连 db 都不 import）
approvals.py（**危险操作确认门 + 审计写入器 + 人话摘要**）
plugins/（**四个工具插件** = 18 个工具：**16 read + 2 write + 0 dangerous**；`plugins/__init__.py::_MODULES` 是显式清单）
  ├─ builtins.py（时间 / 读文件 / 读网页 / 读附件 / 请求用户确认）　├─ websearch.py（自建联网检索，官方 API 无此工具）
  ├─ insight.py（读元数据 / 跑织网 / 取产物）
  └─ accounts.py（登录验证 / 托管会话）
services/
  ├─ browser_pool.py 625 行（专职工作线程 / 双模式 / CDP 托管 / 免密 / 自愈）
  ├─ accounts.py 607 · autologin.py 372 · proc.py 236 · deepseek.py 181 · modules.py 172 · storage.py 122
  └─ egmp/（平台内核，20 文件 3,851 行 —— 2026-09-22 实测，配置工厂链路删除后）
       ├─ 顶层 6 文件 746 行（replay 259（离线回放台架）· client 258 · queries 94 · cache 87 · __init__ 36 · auth 12）
       ├─ kernel/ 4 文件 394 行（endpoints / semantics / urls 等）
       ├─ insight/  6 文件 1,890 行（spider 1,107 · drawio 309 · workspace 219 · lifecycle 151 · __init__ 83 · report 21）
       └─ monitor/   4 文件 821 行（monitor 467 · query_layer 167 · interpreter 155 · __init__ 32）

> **2026-09-20 删除**：`egmp/generate.py`（104 行）与 `egmp/complexity.py`——
> 它们服务的是 `command="orchestrate"` 那条"一句话 → 另一个 LLM 循环 → 蓝图"的路，
> 而那条路的输入只有一个字符串，是**对话链路的降级复制品**（ADR-0017 §D-1）。
>
> **2026-09-22 追加（推翻上一段的结论）**：**配置工厂整条链已删除**——`writers/`（13 文件 2,087 行）、
> `orchestrate.py`（执行引擎，316 行）、`checkpoint.py`（45 行）、`plugins/factory.py`、
> `api/routes_factory.py`、`static/pages/factory.html` + `static/js/factory.js`（含侧栏 `nav-factory`）、
> `tools/verify_factory_agent.py` + `tools/verify_factory_page.py`、`tests/test_factory_agent.py` +
> `tests/test_factory_tuning_isolation.py`、`adapters/akso-auto.json` 全部下线，
> 故上一段"**执行引擎（orchestrate.py）一行没删**"**已失效**。
> **刻意留下的**是平台元数据读层（`queries.py` + `cache.py`，见 [ADR-0010](adr/0010-monitor-no-dangerous-replay.md)）、
> Monitor，以及知识文档 [PLATFORM-OPERATIONS-LEARNED.md](PLATFORM-OPERATIONS-LEARNED.md) /
> [FACTORY-PLATFORM-COGNITION.md](FACTORY-PLATFORM-COGNITION.md) 与 `factory-tuning/reports/**`。
> 两份决策记录 ADR-0017 / ADR-0018 随本链路下架（原文可取回：`git show 1fa2301:docs/adr/0017-factory-agent-interface.md`）。
harness/（对话 Agent 循环：loop 事件流 · tools **已降级为兼容垫片** · types/prompts/safety/context）
db.py（SQLite 单连接 + **22 条**迁移常量表 + 方言边界）
```

> **2026-09-15 变更（工具插件层）**：新增 `plugin_api.py` / `approvals.py` / `plugins/`（**5 插件 20 工具**）。
> 动因是**两套工具表并存**：`harness/tools.py` 有三个基础工具、`api/routes_agent.py` 有四个域工具，
> 两套互不可见 → 对话里的模型看不到任何平台能力。现已收口为 `ToolHub` 单一事实源；
> `harness/tools.py` 只剩再导出，`@tool` 装饰器下线。契约与三个真踩过的坑见
> **[PLUGIN-SDK.md](PLUGIN-SDK.md)**；环境坑见 PITFALLS #32–#34。
> （`websearch` 是 2026-09-15 之后加入的插件；此处的清单口径已多次变动，**不要引用本文的旧快照**。
> **2026-09-22 现状**：core **4 个插件**（`builtins` / `websearch` / `insight` / `accounts`）、
> **18 个工具**（**16 read + 2 write + 0 dangerous**）——配置工厂删除后由五插件 / 25 工具回落到这个数。
> 确切数字一律以 `GET /api/agent/selfcheck` 为准。）

> **2026-09-14 变更**：`insight/` 从 12 文件 1,766 行降到 **6 文件 1,145 行**——`inventory` / `understand`
> 下线，`assemble.py` / `annotate.py` / `flowgraph.py` / `relations.py` / `render.py` / `understand.py`
> / `crawler.py` 删除；`crawler.py` 的 `SIGNATURE_RE` 内联进 `spider.py`（这就是 spider 从 359 涨到 437 的原因）。

**四个来源项目的内化方式**（[ADR-0005](adr/0005-native-internalization-of-four-projects.md)）：不再是“子进程封装”，而是 Python 原生重写；`adapters/*.json` 降级为**只读参考存档**（`"mode": "native-python-internalized"`，`"frozen": true`），仅保存端点/产物口径对照。

## Key modules（关键模块）

| 路径 | 职责 |
| --- | --- |
| `workbench/server_entry.py` | 冻结态入口（PyInstaller 顶层脚本，只用绝对导入——`verify_packaging.py` P2 强制） |
| `workbench/main.py` | Web 模式入口（13 行，起 uvicorn + 开浏览器） |
| `workbench/api/__init__.py` | `create_app`：fork 路由显式注册 + `_optional_routers` 容错注册 12 个新域（含 `sandbox` / `skills`）+ lifespan(`db.connect` + 沙箱/技能装配) + 静态托管 |
| `workbench/api/routes_extension.py` | **执行面蓝图**：`/snapshot` `/commands`(长轮询) `/ack` `/state` `/health`。**整段前缀需访问令牌**（见下条）。⚠ 本模块**只放"扩展 ↔ 桌面"的数据与指令通道**——给桌面 UI 用、且不碰凭据的端点放 `routes_shell.py`，否则它们拿不到令牌、只能靠开豁免 |
| `workbench/api/extension_auth.py` | **`/extension/*` 访问令牌**（[ADR-0015](adr/0015-extension-access-token.md)）：按**前缀**守备的 ASGI 中间件 + `/extension/hello` 自举端点 + 24h 宽限期 + 无令牌访问的可见性（`authBlocked`）。**它挡不住"能读 `%APPDATA%` 的本机进程"**——令牌就在同一张 `settings` 表里；详见该模块文档串的分层表 |
| `workbench/api/routes_shell.py` | **壳协作面** `/api/shell/*`：`launch-chrome` / `update-state` / `setup-helper`。**不碰凭据**，故无需令牌（边界判据见该模块文档串） |
| `workbench/services/browser_pool.py` | **专职工作线程**（Playwright sync 单线程单实例约束；操作走队列作业）；headful/headless 双模式；storage_state 持久化；`heal_count≤2` 自愈；CDP 挂接内置 Chromium |
| `workbench/services/accounts.py` | Fernet 密钥管理、环境/账号/盒子 CRUD、分配池（config/monitor）、备份导出导入（`fernetKey` 随文件下发） |
| `workbench/services/autologin.py` | 节奏门控五重门（MAIN world JS 注入 + TokenCapture JWT） |
| `workbench/services/egmp/` | 平台内核：`client`（code==0 信封 / paged_post / 55min token 缓存）、`kernel`（端点表 + 语义词典）、`queries`（**平台元数据读层**，与 `cache.py` 一并刻意保留）、`insight`、`monitor`。~~`writers`（含 `spec.py`：给 agent 的蓝图规格 + 给用户的审阅材料）/ `orchestrate`（**只做确定性执行**：拓扑排序 / 8 步管道 / checkpoint）~~ → **2026-09-22 随配置工厂整体删除** |
| `workbench/db.py` | SQLite + `MIGRATIONS`（**1–20**；13 = `add_created_at_indexes`，14 = 洞察工作区与覆盖度，15 = 模块 id 数据改写，16 = `tool_plugin_layer`（`agent_audit` 加列 + `pending_action`），17 = `agent_cache_layer`，18 = `rename_factory_module_id`，19 = `sandbox_run`（沙箱执行审计），20 = `skill_audit`（能力供给链留痕））；迁移**只增不改** |
| `workbench/plugin_api.py` | **插件 SDK 契约**：`ToolHub`（工具注册表/执行器，**分 core / installed 两层**）+ `ToolSpec`（含 `Risk` 分级）+ `ToolContext` + `Plugin`（含 `PluginOrigin`）。零领域知识（不 import 插件、不 import `services/egmp`、连 `db` 都不 import） |
| `workbench/approvals.py` | **危险操作确认门**（单次令牌 / 5 分钟 / 参数指纹）+ **审计写入器**（唯一入口）+ 人话摘要 |
| `workbench/plugins/` | 四个工具插件（`builtins` / `websearch` / `insight` / `accounts`，共 18 个工具；风险分布：**16 read + 2 write + 0 dangerous**）。`plugins/__init__.py::_MODULES` 是显式清单，坏插件隔离成 failure |
| `workbench/sandbox/` | **不受信代码的执行底座**（[ADR-0014](adr/0014-sandbox-for-untrusted-code.md)，6 文件 ≈1150 行）：`layout`（沙箱根/运行目录）、`policy`（**策略 + 环境白名单 + 敏感路径**）、`job`（Windows Job Object 进程树终止）、`runner`（`ExecRequest`/`run`/`run_async`）、`audit`。**不是安全边界**，是限损层（D-3） |
| `workbench/skills/` | **Skill 共享库与安装**（7 文件 ≈1775 行）：`layout`（库/已安装目录，**不入 git**）、`manifest`（`SKILL.md` 契约，两种工具形态）、`security`（ZIP 准入 + 可疑模式扫描）、`library`（上传/安装/卸载/删除）、`script_runner`（脚本式工具的入口约定）、`audit`。共享库与已安装目录都在 `<DATA_DIR>/skills/` |
| `workbench/harness/` | 对话 Agent 循环（`loop.py` 事件流）。⚠️ `harness/tools.py` **已降级为兼容垫片**（只剩再导出 + `default_registry()` 转发 `get_hub()`），工具定义全部搬进 `plugins/` |
| `extensions/quick-login/` | MV3 + TS 扩展源（npm workspace，单包 `packages/extension`）：`src/background/core/parallel-session.ts` 1,606 行（≈28% TS）、`src/content/shield-main.ts` 806 行（MAIN world，平面 1/4/5/6）、`src/content/auto-login.ts` 377 行 |
| `tools/build.ps1` | 六步打包：bump → 扩展重建 → `uv sync` → release commit/push → PyInstaller → electron-builder NSIS |
| `tools/*.py` `tools/*.mjs` | 33 个**脚本**（28 个 `.py` + 5 个 `.mjs`）：构建/发布 + **20 个 `verify_*.py`/生成脚本**（另有 4 个 `verify_*.mjs`）+ `db_maintenance.py`；`gen_tools_doc.py`（工具清单从运行期中枢生成 + 各文档数字机检，见 [TOOLS.md](TOOLS.md)）。~~`verify_factory_page.py`（真实后端返回 → 真实前端渲染函数的对照验证，见 PITFALLS #35）~~ → **2026-09-22 随配置工厂删除**（`verify_factory_agent.py` 同批） |

## Data & control flow（数据流）

**① 人工登录（执行面 = 用户 Chrome）**：账号中心 / 轮盘（Alt+Q）→ `routes_extension` 写指令队列 → 用户 Chrome 里的扩展每 **2s** 拉 `/snapshot`（`snapshotId` 未变则跳过），并用一条独立长轮询（`wait=15`）拉 `/commands?after=<cursor>` → 会话切换 / 自动登录。凭据以 **Fernet 密文 + 原始密钥一同**下发（`accounts.py:432-464` → `routes_extension.py:126`），扩展端在 SW 内存解密后本地用 AES-GCM-256 落盘。指令序号跨重启单调递增（`settings.ext_cmd_seq`）。

**② 自动化 / 监听（执行面 = 内置 Chromium）**：`routes_browser.open {account_id}`（默认 headful）→ browser_pool 专职线程：解密凭据 → 一账号一 BrowserContext（`storage_state` 持久化 `runtime/browser-states/`）→ TokenCapture → 注入节奏门控 → 免密直达 / 自愈重登 → `routes_monitor` 在该会话上启停录制。

**③ 洞察（对象分析）**：`insight.html` → SSE 路由 → `egmp.insight` 直连 eGMP → **产物落用户可配工作区** `<工作区根>/<对象code>/<时间戳>/`（工作区不可写才退回 `runtime/insight/`），`insight_runs` 记账。~~`factory.html` → `orchestrate.run_full_workflow`，工厂产物落 `runtime/factory/`，`blueprint_jobs` 记账~~ → **2026-09-22 随配置工厂删除**。

**④ 更新链（默认关闭）**：`AUTO_UPDATE_ENABLED` 只在 `WORKBENCH_AUTO_UPDATE=1` 时为真（`updater.js:36`，用户定稿 2026-09-15）。**关闭时**：不启动定时检查、不静默下载、退出不安装，托盘不挂更新提示，手动检查只回一句说明。**开启时**：Electron 启动 +20s 首查、之后每 6h → 静默下载 → 下载完成后才弹一次对话框（默认「退出时安装」）→ `before-quit` **先 `killServer()` 再 `installOnExit()`**（顺序是承重的，安装器要覆写 `WorkbenchServer.exe`）。更新状态经原子写的 `shell-state.json` 暴露给 Python 的 `GET /api/update`。

**状态存放**：SQLite（`%APPDATA%\Workbench\workbench.db`，WAL）；任务产物 `runtime/`；浏览器快照 `runtime/browser-states/`；配置在 `settings` 表；用户可干预的文件级开关 `proxy.txt`（强制代理 / 强制直连）。

## Entry points（入口）

- **桌面版（终端用户）**：`Workbench-<版本>-setup.exe`（NSIS，`oneClick:false`，artefact 名由 `desktop/package.json:57` 决定）
- **桌面版（开发态）**：`cd desktop; npm ci; npm start`
- **Web 模式**：`.\.venv\Scripts\python.exe -m workbench.main`（127.0.0.1:18765，自动开浏览器）
- **打包**：`powershell -File tools\build.ps1`（默认 `-Bump build`）；发布：`tools/publish_release.py`（独立手工步骤，**build.ps1 不调用**）
- **CLI / 维护**：`tools/bump.py show|push|build`、`tools/import_accounts.py`、`tools/setup.ps1`、`tools/db_maintenance.py --report|--backup|--trim|--vacuum|--all`

## Build, run, and test（构建 / 运行 / 测试）

```powershell
# 安装（两个 extra 都要装；省掉 build 会让打包第 5 步失败）
.\.venv\Scripts\python.exe -m uv sync --extra dev --extra build

# 运行
.\.venv\Scripts\python.exe -m workbench.main      # Web 模式
cd desktop; npm ci; npm start                     # Electron 开发态

# 测试 / 体检（2026-09-17 实测，见下方"验收基线实测"）
.\.venv\Scripts\python.exe -m pytest              # 493 passed（默认跳过 slow 层）
.\.venv\Scripts\python.exe -m pytest -o addopts=""     # 501 passed（含 slow 层；见下方"两层测试"）
.\.venv\Scripts\python.exe -m pytest -m slow      # 8 passed（只跑慢层：真起子进程/浏览器那批）
.\.venv\Scripts\python.exe tools\verify_packaging.py   # 11/11（P4b 需先重建扩展，见下）
.\.venv\Scripts\python.exe tools\gen_tools_doc.py      # 工具清单/数字 == 运行期中枢（配置工厂删除后：4 插件 / 18 工具 = core）
.\.venv\Scripts\uv.exe tool run ruff check .      # All checks passed（venv 内没有 ruff.exe）
.\.venv\Scripts\python.exe tools\bump.py check    # 五写版本同步自查（stdout 只有版本号）

# 按 CI 的解释器口径复现（3.12；见 CONTRIBUTING §1.1）
.\.venv312\Scripts\python.exe -m pytest -o addopts="" -q -rs   # 493 passed, 8 skipped

# 扩展 / 前端（改扩展时）
cd extensions\quick-login; npm run build; npm run typecheck
python tools\verify_extension_boot.py; python tools\verify_extension_isolation.py
node tools\verify_host_logic.mjs; node tools\verify_extension_sync.mjs

# 打包
powershell -File tools\build.ps1
```

> **出网通道（本机必读）**：本机唯一的 GitHub 通道是**系统代理** `127.0.0.1:7890`（FlClash 在听）——
> `git` 没有配 `http.proxy` 时走直连，实测**超时约 21s 后失败**（`Failed to connect to github.com port 443`），
> 而 `Invoke-WebRequest` 因为读系统代理能拿到 200。所以 `git fetch` / `git pull` / `git push` 都要显式带代理：
> `git -c http.proxy=http://127.0.0.1:7890 pull`。**"哪个通道通"随时刻变化**（PITFALLS #9），
> 失败时先 `Get-NetTCPConnection -LocalPort 7890 -State Listen` 探测，别把上一次的成功当依据。

### 验收基线实测（2026-09-17，本机 `main` = v0.3.12 + P0-2/3/4/5 与 C1/C4 修复）

**本节为当前权威基线**；下面两节（2026-09-16 / 2026-09-13）保留以留痕。

| 命令 | 实测 | 判定 |
|---|---|---|
| `.\.venv\Scripts\python.exe -m pytest -o addopts=""` | **501 passed, 0 skipped**（3.14.6，52.7s） | ✅ 含 slow 层 |
| `.\.venv312\Scripts\python.exe -m pytest -o addopts=""` | **493 passed, 8 skipped**（3.12.14，19.3s） | ✅ **CI 的解释器口径**；8 项跳过是浏览器验收（CI runner 同样跳过） |
| `tools\verify_packaging.py` | **13/13 PASS**。**2026-09-18 修正**：原 **P8「chromium 内核可打包」查的是"本机装没装浏览器"**——那是**环境事实**，在没装浏览器的 CI runner 上**必然 FAIL 且永远修不好**，首次把 CI 判红的就是它（run #17）。现拆成两项且**项数在两环境下都稳定 13/13**：**P8**（静态契约：`server.spec` 里**有**"缺 chromium 就失败"的守卫——任何环境都能判定）+ **P8b**（本机真有 chromium，**仅当这台机器被角色要求时**才断言；`build.ps1` 设 `WORKBENCH_REQUIRE_CHROMIUM=1` 声明自己是构建机）。⚠ P8b 的判据是"按角色要求"，**不是** `present == expect`——后者会要求"不要求时必须没有"，把有浏览器的开发机判红（实测踩到） | ✅ |
| `tools\gen_tools_doc.py` | `TOOLS_DOC_CHECKS: PASS（5 插件 / 20 工具 = core）`<br>⚠️ **2026-09-22 变更**：配置工厂删除后应为 **4 插件 / 18 工具**，本行其余数字仍是 2026-09-17 的实测，未重跑 | ✅ |
| `tools\bump.py check` | `0.3.12`（stdout 只有版本号；明细在 stderr） | ✅ |
| `uv tool run ruff check .` | `All checks passed!` | ✅（**注意**：PyPI 直连 TLS 失败时用 `--index https://pypi.tuna.tsinghua.edu.cn/simple`） |
| `extensions/quick-login`: `npm run typecheck` + `npm run build` | 无错误 / `BUILD_OK` | ✅ P0-2 的扩展侧改动 |

> **两个口径的差集是刻意的**：3.14 有浏览器（0 skipped），3.12 环境里的 playwright 版本更新、
> 而本机没有那版 chromium（8 skipped）。**"skip 数"必须和"passed 数"一起读**——
> 这正是 CI 要求 `-rs` 的原因（PITFALLS #43）。

### 验收基线实测（2026-09-16，本机 `c8753ce` = v0.3.12）— 留痕，现状见上一节

拉取上游 `c8753ce` 后重跑一遍，**三项全绿**（此前的红项均已消失）：

| 命令 | 上一轮（`d0efa84`，v0.3.5） | 本轮实测（`c8753ce`，v0.3.12） | 判定 |
|---|---|---|---|
| `python -m pytest` | 63 passed / 1 failed | **378 passed，exit 0**（19 个测试文件 292 个测试函数，参数化后 378 项） | ✅ 本仓首次全绿 |
| `tools\verify_packaging.py` | 8/8 PASS | **8/9 → 重建扩展后 9/9** | ⚠️→✅ 见下 |
| `ruff check .` | 无法执行 | **All checks passed!**（正确命令：`.\.venv\Scripts\uv.exe tool run ruff check .`） | ✅ 门禁在本机成立 |

- **P4b 的红是「拉取后未重建扩展」，不是代码错**：`extensions/quick-login/dist/manifest.json` 是**构建产物**，
  拉取只更新了源 `packages/extension/manifest.json`（0.3.12）→ dist 仍是上一轮本机构建的 0.3.11。
  检查器自己的提示就是处置办法：`cd extensions/quick-login && npm run build`（`build.ps1` 第 1.5 步做同一件事，
  注释写明"必须在 bump 之后，否则打进安装包的扩展 manifest 版本号会停在上一版"）。
  **教训**：`verify_packaging.py` 的 P4b 检的是"**产物与版本号一致**"，所以**每次 `git pull` 后先重建扩展**再跑体检，
  否则你会看到一个只反映本地构建残留、与上游无关的红项。
- **`uv` / `uvx` / `ruff.exe` 的现状（2026-09-17 实测，此为准确口径）**：
  `.venv\Scripts\` 里**有** `uv.exe` / `uvx.exe` / `uvw.exe`，**没有** `ruff.exe`。
  所以——**2026-09-16 记的"venv 内有 `ruff.exe`"是错的**（该文件不存在，直接调用报 `CommandNotFoundException`），
  同时"`uv.exe` 不存在"也是错的。**可用且唯一正确的 lint 命令**：
  `.\.venv\Scripts\uv.exe tool run ruff check .`（首次会下载 ruff 约 10 MB，需要网络；
  若 `pypi.org` TLS 握手失败则重试或走代理）。CI 用的就是这条，**本地绿 == CI 绿**。
  `CONTRIBUTING.md` 里 `uv sync` 的写法在**已建好的 venv** 上可跳过（依赖已装齐），换机器时仍需先装 uv。


### 验收基线实测（2026-09-13，本机 `d0efa84`）—— 留痕，现状见上一节

| 命令 | 实测 | 判定 |
|---|---|---|
| `.\.venv\Scripts\python.exe -m pytest` | **378 passed, 0 failed**（43.7s） | ✅ **全绿** |
| `.\.venv\Scripts\python.exe tools\verify_packaging.py` | 8/8 PASS（2026-09-13 实测，未重跑） | ✅ |
| `uv tool run ruff check .` | ❌ **本机不可执行**：`uv` 需要联网取 ruff，当前 `pypi.org` TLS 握手失败（`Request failed after 3 retries`） | ⚠️ 环境依赖，非代码问题 |

> **口径提醒（真踩过）**：`pyproject.toml` 里 `addopts = "-q"`；**若命令行再加一个 `-q` 会进入 ultra-quiet 模式，
> 连 `=== 378 passed ===` 汇总行都不打印**，只剩进度点阵，很容易误判成"没跑出结果"。
> 想看到汇总行用：`python -m pytest -o addopts=""`。
> **另注**：`addopts` 里还有 `-m 'not slow'`——**默认不跑慢层**（真起子进程/浏览器那批）。
> 完整跑一遍用 `python -m pytest -o addopts=""`；只跑慢层用 `-m slow`（没有任何慢层用例时 pytest 以 **exit 5** 退出，这是 CI 刻意的守卫，不是故障）。

**代码量实测（2026-09-17）**：`workbench/**.py` **94 文件 17,850 物理行**（含新增的 `sandbox/` 6 文件 1,150 行与 `skills/` 7 文件 1,775 行）；
`workbench/static` 的 js+css **23 文件 7,466 行**（8 个 html）；
HTTP 端点 **97 个**（`@router.` 计数）。
**这些数字随代码变动，引用前先复核**（`docs/` 里凡写"19 工具 / 4 插件 / 迁移 1–13"的段落均已过期）。

### 历史验收基线（2026-09-13，本机 `d0efa84`）— 保留以留痕

| 命令 | 原 `AGENT.md` 声称 | 当时实测 | 判定 |
|---|---|---|---|
| `python -m pytest` | 64 passed | **63 passed, 1 failed** | ⚠️ 唯一失败是环境依赖（见下） |
| `uvx ruff check .` | 9 条违规 | **无法执行**：`.venv\Scripts\uvx.exe` / `uv.exe` 不存在，venv 内也无 `ruff` 模块，PATH 上无 ruff | ❌ 门禁在本机不成立 |
| `python tools\verify_packaging.py` | 8/8 PASS | **8/8 PASS** | ✅ |

- **pytest 唯一失败（2026-09-15 已修）**：`tests/test_modules.py::test_adapter_paths_exist` —— `AssertionError: akso-auto repoPath 无效：D:\ai_assistant\akso-auto`。该断言校验只读存档指向的原仓库**在不在**，而 `adapters/*.json` 的 `mode` 已是 `native-python-internalized` + `frozen: true`——**能力全部内化，运行时零依赖原仓库**。把"原仓库目录存在"当断言，等于给一个已经不需要它的系统加一道环境门禁：没克隆那四个仓库的机器上测试就红，而**功能完全正常**。
  **修法**：拆成两条守"契约"而不是守"环境"的断言——`test_frozen_adapters_do_not_require_the_upstream_repo`（路径口径解析得出且与存档声明一致）+ `test_missing_upstream_repo_is_reported_as_info_not_failure`（缺失时报信息项、模块状态不变红）。**不要**改回 `assert repo.exists()`。
- **`uv` 现在可用**（2026-09-15 实测）：`.venv\Scripts\uv.exe` 存在，`uv tool run ruff check .` 全绿。本节早先"本机无 uv/ruff、lint 门禁不成立"的记录**已过期**（保留原文是为了留痕）。
  > ⚠️ **2026-09-16 曾"更正"说本条有误，那次更正本身也是错的**——2026-09-17 实测：`.venv\Scripts\uv.exe` **存在**
  > （本条原判断正确），而 `ruff.exe` **不存在**（2026-09-16 那条更正错在这里）。
  > **准确口径见上一节的"`uv` / `uvx` / `ruff.exe` 的现状"，那里是唯一权威版本。**
- **沙箱影响**：在默认 `workspace-write` 沙箱下 pytest 表现为 `38 passed / 7 failed / 19 errors`，全部由 `PermissionError: [WinError 5]` 引起（`conftest.py:12` 用 `tempfile.mkdtemp()` 在系统临时目录建隔离数据目录，且测试需要创建子进程管道）。**在放开临时目录与管道限制后复跑即得上表的 63/1**。据此判断：**仓库的测试基线本身是健康的，已删除的原 `AGENT.md` 记录的“64 passed”与实测只差上述 1 项环境失败**。
- **原 `AGENT.md` §8 的依赖纪律已过期**（该文档于 2026-09-13 因整体滞后被删除）：它称“`uv` / `ruff` 不在 PATH，可用的是 `.venv\Scripts\uv.exe` / `uvx.exe`”——**这两个文件在本机并不存在**（`pyvenv.cfg` 记录 `uv = 0.12.10`，但 venv 已不再内置这两个 shim）。因此 `CONTRIBUTING.md` 里的 lint 门禁与 `uv sync` 命令在本机都需要先装 uv。

## Conventions（约定）

- **路由注册**：新路由在 `api/__init__.py` 显式登记（fork 域直挂，新域走 `_optional_routers` 容错表，单域装配失败不拖垮应用）。
- **`/extension/*` 整段需要访问令牌**（[ADR-0015](adr/0015-extension-access-token.md)）：新增端点前先问
  **「它碰凭据吗？」**——碰 → 放 `routes_extension.py`（令牌自动生效，因为是按**前缀**守的）；
  不碰且是给**桌面 UI** 用的 → 放 `routes_shell.py`（`/api/shell/*`，UI 拿不到令牌）。
  **不要在 `/extension/*` 下加"给 UI 用"的端点**：那只能靠 `EXEMPT_PATHS` 开豁免，
  而豁免一开，"整个前缀需要令牌"就退化成人工维护的清单。豁免面由
  `tests/test_extension_auth.py::test_exempt_surface_is_minimal` 与 `verify_packaging.py` 的 **P10** 双重钉住。
  改扩展侧协议（`desktop-auth.ts` / 令牌头名）必须同步 `extension_auth.TOKEN_HEADER` 与各 `tools/verify_*` 脚本。
- **工具只有一个定义处**：新工具写进 `workbench/plugins/*.py` 的 `ToolSpec`（**必须显式声明 `risk`**），
  经 `plugins/__init__.py::_MODULES` 装配。**不要在 `routes_agent.py` 或 `harness/` 里另建工具表**——
  2026-09-15 之前那两套并存，后果是"对话里的模型看不到任何平台能力"。同时记得同步
  `server.spec` 的 `hiddenimports`（动态导入的插件打包器看不见）与 `REQUIRED_TOOLS`。
- **工具清单与数字由机检守，不靠人记**：改插件后跑 `python tools/gen_tools_doc.py --write` 重生成
  [TOOLS.md](TOOLS.md) 的清单块；`python tools/gen_tools_doc.py`（以及 `tests/test_tools_doc.py`）
  会核对"文档清单 == 运行期中枢"，并把各活跃文档里的"N 个工具 / N 个插件"与实际比对。
  动因是 2026-09-15 加 `web_search` 后四处文档全写旧数字、**由用户先发现**（PITFALLS #36 同批教训）。
- **品牌标识与协议标识分开看**：改产品名/包名/env 变量是"文案级"改动，但**同一个字符串可能同时是协议**
  （`/api/health` 的 `app` 字段、快照/备份的 `format`、扩展存储键）。改名先把命中按"文案 / 标识 / 协议"
  三分类，协议类一律"写出端发新名 + 读取端两个都收"。完整清单见 [ADR-0013](adr/0013-brand-neutralization-workbench.md) 与 PITFALLS #36。
- **插件依赖方向单向**：`plugins → plugin_api`，永不反向。`plugin_api.py` 里出现平台术语/端点/
  `services.egmp` 的 import 都是错的（它连 `db` 都不 import，审计与确认门靠注入）。
- **同步阻塞调用挪线程**：工具 handler 是 `async`，但 `egmp.client`/`browser_pool`/`proc` 都是同步的，
  一律 `asyncio.to_thread`——否则阻塞事件循环（表现为整个服务卡住，而不是报错）。
- **请求体一律 pydantic `BaseModel`**：pydantic 默认**忽略多余字段**，漏字段会**静默失效**（`AccountPatch` 漏 `box` 是已发生事故，环境坑 #6）。
- **Playwright 纪律**：sync API 单线程单实例 —— 所有浏览器操作**必须**走 `browser_pool` 专职线程队列；**事件回调内禁止连接调用**（只入队，worker 空隙 `drain()`）；同线程禁第二个 `sync_playwright()`；**测试不得另起 chromium 夹具**。
- **DB 迁移只增不改**：历史迁移（尤其 1–6）不可变。`job_logs` 表建了索引但零写入是**故意**的（日志落磁盘），不要“顺手”改成入库。
- **凭据不落明文**：新通道必须沿用「密文 + 密钥」回环语义；接口响应必须剔除 `password_enc`；文档与日志中出现的一律是占位值。
- **Monitor 危险操作永不回放**：原先那张"端点 → writer 函数"的 `API_MAP` 已随 `writers/**` 删除
  （2026-09-22）。现在 `reproduce-plan.json` **只承载平台侧事实**（方法 / 路径 / 原始参数）+ `"executor": null`，
  **本仓库不再提供任何回放执行器**（[ADR-0010](adr/0010-monitor-no-dangerous-replay.md)）。
  不要再把 writer 常量当成可用映射，也不要凭观察猜端点——需要回放时由人按计划自己操作平台。
- **原四仓库只读**：任何情况下不修改 `adapters/*.json` 指向的目录。
- **版本只走 `tools/bump.py`**：五写同步（`pyproject.toml` / `workbench/__init__.py` / `desktop/package.json` / 扩展 `package.json` + `manifest.json` / `.version.json`）+ CHANGELOG 同步；手改单一文件会导致安装包命名与产物校验错位。
- **扩展协议护栏**：改 `/extension/*` 蓝图必跑 `node tools/verify_extension_sync.mjs`（该蓝图**无 pytest 覆盖**）；改扩展必跑 `verify_extension_boot.py` + `verify_extension_isolation.py` + `verify_host_logic.mjs` + `npm run typecheck`。
- **文档按变更频率分层**：ADR 记不再变的决策；`API.md` / `CONFIG.md` / `SCHEMA.md` 记随代码走的事实（改必更）；`PITFALLS.md` 记真机环境坑（只增不改，机构记忆）；本文件记整体结构与风险台账；`archive/` 只增不改。

## Risks & rough edges（风险与毛边）

> **本节是当前仓库的权威风险台账。** 原 `AGENT.md` §4 的清单已于 2026-09-13 随该文档删除；编号（C/S/T/B/D）**沿用不变**，
> 以保持与 `docs/API.md`、`docs/adr/*`、`docs/SCHEMA.md` 等既有引用的对应关系。本节只列本轮**对码复核确认仍然成立**的高优先级项，
> 并补充本轮新发现。环境坑（真机事故经验）另见 [PITFALLS.md](PITFALLS.md)。

### 安全（最高优先级）

| # | 问题 | 证据 |
|---|---|---|
| **R1** | ~~**`/extension/*` 与 `/api/*` 全无认证**：本机任意进程可拉快照（快照里**同时含 Fernet 密钥与全部密文口令**，等于直接拿到所有测试账号明文）、可下发指令、可读导出备份~~ | → **`/extension/*` 已于 2026-09-17 修复**（P0-1，[ADR-0015](adr/0015-extension-access-token.md)）：整段前缀由 `api/extension_auth.py` 的 **ASGI 中间件**统一要求 `X-Workbench-Token`（按**前缀**守而非逐路由 `Depends` → "新增路由自动被守住"是结构保证）；令牌落 `settings.extension_access_token`（跨重启稳定），扩展经 `POST /extension/hello` 自举领取；豁免面只有 `/extension/hello`（只回令牌与版本号）与 `/extension/health`（只读徽标判据），有测试钉住。**为什么中间件而不是来源校验**：扩展 SW 的 `Origin` 头行为无法在本机实证，不能把安全建立在没验证过的浏览器行为上 |
| **R1b** | **`/api/*`（95 个端点）仍无认证**——含 `GET /api/accounts/export`（导出备份，**随文件下发 Fernet 密钥**）与 `GET /api/browser/sessions/{id}/token`。它们与 `/extension/snapshot` 同属敏感面 | **2026-09-17 有意保留**（ADR-0015 **D-7**）：这些端点**同源**——桌面 UI 就是本服务加载的页面，加令牌要么在页面里放令牌（等于没加），要么改页面引导流程（净增复杂度、收益低于本 ADR）。**如实记录**：R1 的"零知识一次 GET"已在 `/extension/*` 上关闭；`/api/*` 仍依赖"只监听回环 + 无 CORS 授权"这一层。重新评估的触发器见 ADR-0015 §7 |
| **R2** | ~~**明文凭据可达未绑定页面**：待登录凭据仅以 `tabId` 为键、`getPendingAutoLogin` **读后不删**（60s 内可重复取）、SW 处理器不校验发送方 URL 也不校验绑定、`unbindTab` 不清该条目，而 `auto-login.js` 以 `all_frames` 注入 `<all_urls>`，子帧回退会填**任意**密码框 —— 绑定标签页在 60s 内导航到非绑定源，口令会被打进该页 DOM~~ → **2026-09-17 已修**（P0-2）：三条一起收口——① `claimPendingAutoLogin(tabId, url)` **先删后判**（任何早退路径都不留可复取的条目）；② pending 条目**自带 `host`**，索取方 `sender.url` 必须与它 `hostRelated()`（含端口身份口径，与页签绑定/DNR 同一把尺子）；③ `unbindTab` 同时 `clearPendingAutoLogin`。**刻意不按 `sender.frameId === 0` 收口**：`auto-login.ts` 的子 frame 也会各自索取并兜底填表（跨域 SSO 登录框靠这条），按 frame 收口会打死合法路径。验证：`extensions/quick-login && npm run typecheck` + `npm run build` 通过；`manifest` 权限面**未动**（仍是 `<all_urls>`，见 R3） |
| **R3** | 扩展 `host_permissions: ["<all_urls>"]` 权限面宽于功能所需 | `manifest.json:35` |
| **R4** | 扩展端落盘密钥 `sb:encryptionSeed` 是**明文 `randomUUID()`** 存在同一 `storage.local`，PBKDF2-100k + 硬编码盐派生 → 只是设备绑定混淆；且每次加解密都重跑 PBKDF2，无缓存 | `credentials.ts:10-42,74-90` |
| **R5** | `settings.account_fernet_key` **密钥轮换无实现**：手改它会让扩展静默丢弃全部账号（接口仍能读） | ADR-0007 后果 |

### 代码缺陷（2026-09-16 于 `c8753ce` 逐条对码复核；已修项保留原文并标注）

| # | 位置 | 问题 |
|---|---|---|
| **C1** | `browser_pool.py:280-281`（原 247-248） | ~~状态判断写反：先置 `entry.status="stopped"`，再判断 `== "online"` → 该分支恒假，首次登录后关窗误报「已关闭」。~~ → **2026-09-17 已修**：改为关窗前先取 `was_online = entry.status == "online"`，再用它决定 `detail`。回归 `tests/test_browser_state.py::test_close_entry_reports_saved_state_when_session_was_online`（**已实测对旧实现 FAIL**） |
| **C2** | `routes_insight.py:318-321` | ~~产物路径守卫用**纯字符串前缀**（未补分隔符）：`.../insight/abc` 会“包含” `.../insight/abc_evil` → 应改 `Path.is_relative_to()`~~ → **2026-09-16 复核：已修**（现为 `candidate = (root / name).resolve()` + `candidate.is_relative_to(root) and candidate.is_file()`） |
| **C3** | `routes_agent.py:173` | ~~`GET /api/agent/audit` 用 `SELECT *` 全列回吐，含 `args` 原始 JSON（可能含凭据）~~ → **2026-09-15 已修**：列表只回摘要列，细节走新增的 `GET /api/agent/audit/{audit_id}`；`args` 里的凭据键在写入时即被打码（`approvals.write_audit` + `plugin_api.redact_args`），有回归断言 |
| **C4** | `routes_extension.py:240-242` | ~~ack 回收是「全部 ack 才清」：一条指令永不被 ack 则队列既不回收也不前进，`_acked` 只增不减（且客户端只 ack 最大 cursor，`sync.ts:229-235` → **实际上永不排空**）。~~ → **2026-09-17 已修**：`_acked: set[int]` 换成**水位线** `_ack_watermark = max(acked)`，按 `seq <= watermark` 回收、队列空则归零（水位线只增不减）；返回体加 `watermark`/`pending` 便于断言。回归 `tests/test_extension_blueprint.py::test_ack_watermark_drains_queue_even_when_client_acks_a_prefix`（**已实测对旧实现 FAIL**）+ `test_ack_watermark_never_goes_backwards` |
| **C14** | ~~`static/js/factory.js` 的 `run()`~~ | **工厂执行日志从来什么都不显示**：前端读 `result.stdout_tail` / `stderr_tail` / `code` / `duration_s`，而 `POST /api/factory/run` 返回的是 `{job_id,status,result,log_tail}`——那四个字段是 `services/proc.py` 的进程结果形状，原生化后**没有进程了**。表现为"点执行、转一圈、日志区还是空的、收尾行写着 `exit=undefined`"，且**页面不报错、测试也不报错**。→ **2026-09-15 已修**：渲染 `log_tail` + 逐对象 `steps` + `manualItems` + `checkpoint.completedCount`，并加 `tmp/verify_factory_frontend.py`（真实后端返回 → 真实前端渲染函数，两侧字段任何一边改名立刻红）。<br>**2026-09-22：整条链（页面 / 路由 / 验证脚本）已随配置工厂删除，本条所描述的缺陷面不再存在**；保留原文以留痕 |
| C5–C7, C10–C13 | `services/modules.py:145`、~~`routes_factory.py:47,63`~~（2026-09-22 随配置工厂删除）、`routes_agent.py:78`、`static/js/app.js:78`、`static/css/wheel.css`、`browser_pool.py` 的 `_close_entry` / `_submit` | ~~`status` 映射语义反了（全挂 = `degraded`、部分挂 = `missing`）~~ → **2026-09-15 已修**（改为显式三态：空=missing / 全通过=ok / 部分=degraded / 全挂=missing）/ `native_orchestrate_imported_validate` 定义在调用点之后 / `routes_agent` 跨模块调用 `routes_insight` 私有函数（**已随工具插件化消除**：`insight_run` 改为经 `ToolHub` 调 `routes_insight.run`）/ `app.js:78` 死分支 / `wheel.css` 是无引用死文件 / 关窗不停监听、多数浏览器作业无超时 |
| **C15** | `services/modules.py::module_status` | **按 id 硬解析适配器**：`mod = adapter or {}` 只认调用方显式传进来的存档，于是 `GET /api/modules/<id>` 这条路径对任何**适配器模块**都返回空清单 + `status=missing`。2026-09-15 把 `akso-auto` 改名 `factory` 时暴露：体检项整片消失、状态变 missing，而代码一处都没错——**这层耦合会把"改名"变成静默故障**。→ **已修**：缺省时自己 `get_adapter(mod_id)`，并加回归 `test_module_status_resolves_adapter_without_being_handed_one` |

### 构建与打包

| # | 问题 | 证据 |
|---|---|---|
| **B-a** | ~~**chromium 静默不打包**：`server.spec` 仅在 `%LOCALAPPDATA%\ms-playwright\chromium*` **存在时**收集，无 else 无报错 → 缺它的机器会打出一个“成功”的**没有浏览器**的安装包，只在运行期暴露；`build.ps1` 与 `verify_packaging.py` **都不做前置检查**~~ → **2026-09-17 已修**（P0-3，双保险）：`server.spec` 缺 chromium 直接 `raise SystemExit`（带 `playwright install chromium` 处置），`verify_packaging.py` 新增 **P8** 在 PyInstaller **之前**拦（省掉一次几分钟的打包），且 `build.ps1` 第 4 步已接入 `verify_packaging.py` | ~~`server.spec:44-48`；ADR-0009:46-47~~ 已修 |
| **B-b** | **`dist/WorkbenchServer` 从不清理却被整目录拷进安装包** → 陈旧 chromium 目录累积膨胀（≈350MB 量级），且可能发出旧浏览器 | `desktop/package.json:35-37`。<br>**2026-09-17 复核：仍未修**——清理会与"增量打包提速"冲突，需单独决策；`server.spec` 的 `chromium*` 通配收集会把历史残留一并打进包（PITFALLS #28 已量化：清理旧代后 561MB → 350MB） |
| **B-c** | ~~`git add` 全有全无 + `git commit` 的 stderr 被 `2>$null` 吞掉 + `git push` 三次失败后**仅打印一行并继续** → 可能出现「安装包标着 vX 但树里不是 vX」~~ → **2026-09-17 已修**（P0-3）：① 推送通道改为**探测**（直连 / 注册表 `ProxyServer` / 7890·7897·10809，各试一次 `ls-remote`），不再写死 `-c http.proxy=`；② 探测全败或推送 3 次仍败 → **中止构建**；③ commit 的 stderr 保留，并把"无改动可提交"与"真失败（如 git 身份未配）"区分开，后者 `exit 1`。见 PITFALLS #45 | 已修 |
| **B-d** | ~~`bump.py` 退出码未检查（`build.ps1:36,39`）→ 失败时 `$version` 为空或混入错误文本，一路流到产物命名~~ → **2026-09-17 已修**（P0-3）：`build.ps1` 校验 `$LASTEXITCODE` + `^\d+\.\d+\.\d+$` 格式；**根因同时修在源头**——`bump.py` 的诊断/错误全部改走 **stderr**（stdout 只允许出现版本号本身），并新增 `bump.py check` 做五写同步只读自查（`verify_packaging.py` 的 **P9** 复用它）。见 PITFALLS #47 | 已修 |
| **B-e** | `bump.py` 的扩展侧写盘是**非锚定**正则 `("version"\s*:\s*")` `count=1`（首个命中即改）；pyproject 用无 `count` 的 `re.sub` 而 `_read` 只取首个匹配 → 存在改错键/读写分歧的余地；~~文件缺失时**静默跳过**（B8）~~；递增**不幂等**（B9：手工 `bump push` 后再 `build.ps1 -Bump push` 连跳两版） | `bump.py:45,52,63,66,72,83-85`。<br>**2026-09-17 部分已修**：扩展两个文件缺失时不再静默跳过（`_die`：缺文件 = 扩展版本无法同步，直接失败）；非锚定正则与递增不幂等**仍未修**（属"手滑才会撞上"，且 `bump.py check` 现在能把不同步暴露出来） |
| **B-f** | `desktop/package-lock.json` 版本仍是 **0.2.1**（`package.json` = 0.3.12），`bump.py` 不写它 → 发布提交后 `npm install` 立刻把工作区弄脏。（注：已删除的原 `AGENT.md` B7 与 `CONTRIBUTING.md` 均称扩展 `package-lock.json` 停在 0.2.20 —— **该说法已过期**，实测扩展侧已是 0.3.5） | `desktop/package-lock.json`、`extensions/quick-login/package-lock.json`。<br>**2026-09-17 复核：仍未修**（安装包命名与产物校验都不读它，影响是"工作区变脏"而非产物错） |
| **B-g** | 桌面壳 `files` 白名单**不含 `desktop/icon.ico`**，而托盘要读它、失败被 `catch` 吞掉 → 打包态很可能**没有托盘图标**；`verify_packaging.py` P6 只 glob `*.js`，**非 JS 资源无人守**。（本机无 `desktop/dist`，未实机复验）<br>**2026-09-14 印证**：P6 对 `.js` 是有效的——本轮新增 `desktop/ports.js` 时忘了加白名单，P6 立刻报红（否则会在打包态以 `require` 失败的形式炸）。但它确实管不到 `icon.ico` 这类资源 | `desktop/package.json` 的 `files`、`main.js` 托盘图标读取、`verify_packaging.py` P6。<br>**2026-09-17 复核：仍未修**（`files` 仍只列 6 个 `.js`+`package.json`，无 `icon.ico`；P6 的 glob 也仍只管 `.js`） |
| **B-h** | `verify_update_flow.py:40` 硬编码 `%APPDATA%\Workbench\shell-state.json` 且忽略 `WORKBENCH_DATA`，而 `shell-state.js:16` 是认这个变量的 → 设了该变量时该流程**必然超时**，且会写进真实用户数据目录 | 两处。**2026-09-17 复核：仍未修** |
| **B-i** | ~~**仓库无 CI**：无 `.github` 目录、无任何 pipeline YAML、**无 git hook**（`bump.py:13` 提到的 pre-push hook 是空想）。验收全靠本地手工纪律~~ | → **已修**：`.github/workflows/ci.yml` 已存在并跑 `test + packaging + lint`（12 步）。**但"无 git hook"仍未变**——发布前的版本自查靠 `build.ps1` 调用 `bump.py check`，不靠 hook。<br>**2026-09-17 追加**：CI 新增 `Interpreter version contract` 步骤（打印实际解释器版本并跑 `tests/test_python_version_contract.py`），见 **P0-5 / B-j** |
| **B-j** | **开发机 3.14 / CI 3.12 的假信号**（2026-09-17 新记，P0-5）：仓库**没有 `.python-version`**，本机 `.venv` 是 conda 的 **3.14.6** 而 CI 钉 **3.12**，两边**跳过项与语义都不同** → "本地全绿"推不出"CI 全绿"。实测差集：3.14 = `501 passed, 0 skipped`（含 slow 层）；3.12 = `493 passed, **8 skipped**` | → **2026-09-17 已修**：新增 **`.python-version`（= 3.12，唯一真源）** + `tests/test_python_version_contract.py`（断言它与 CI 的 `python-version` **逐字一致**、是具体版本而非范围、未被 gitignore）+ CI 新增版本契约步骤。复现手法与"8 项 skip 的根因是 Playwright 版本而非解释器"一并写入 CONTRIBUTING §1.1 |

### 扩展隔离面的实际边界（本轮子代理对码复核）

「六平面隔离」的实现在 `shield-main.ts`（MAIN world 页面补丁）+ `tab-rules.ts`（DNR）+ `parallel-session.ts`（真实 cookie jar 卫生）。**措辞掩盖的缺口**：

1. **Web Worker 域未被补丁覆盖**（IDB 已被代码自认，`shield-main.ts:614`）；
2. **`about:srcdoc` 帧不注入内容脚本**（`auto-login.ts:5-9`）→ 该类 realm 的 `localStorage` / `document.cookie` 完全未打补丁，而**那正是目标平台的登录面**；
3. **SW 注销是源级（origin-wide）的**：绑定标签页会连带把原生标签页的站点 SW 一并注销（`shield-main.ts:513-526`），与自称的“不影响原生页面”原则相悖（`parallel-session.ts:501-507`）；
4. 页面补丁只在 `activate()` 安装（`shield-main.ts:672-687`），且一次性 boot guard 之后**第二次晚激活会在页面中途继续而不重载**（`:745-757`）；
5. `preJarMap` 与 cookie 归属映射**仅在内存**（`parallel-session.ts:435-436,477-479`），SW 重启即丢；
6. **DNR 无法表达端口** → 同主机跨端口隔离只是结构性声称（`host.ts:46-58`、`tab-rules.ts:86`）。

另有多处**文档/代码漂移**：`EXTENSION-PLANE.md:50` 称 `main_frame` 刻意不重写，但代码覆盖全部 10 种资源类型；`:70` 说 `Ctrl+Shift+Q` 而 `manifest.json:10-15` 绑的是 `Alt+Q`；`parallel-session.ts:1500` 称“不引入 webNavigation 权限”而 `manifest.json:34` 已声明。

### 测试缺口

| # | 缺口 | 影响 |
|---|---|---|
| T1 | ~~`/extension/*` 蓝图**无 pytest 覆盖**~~ → **2026-09-17 已修**（P0-4 + P0-1）：新增 `tests/test_extension_blueprint.py`（12 项）与 `tests/test_extension_auth.py`（20 项）——快照契约（字段名逐条钉住，`passwordEnc` 驼峰写错会让扩展**静默丢弃全部账号**）、`snapshotId` 内容哈希语义、游标过滤、**序号跨重启单调递增**、ack 水位线（C4 回归，已实测对旧实现 FAIL）、`/health` 四态与 `extStale`、`/state` 的 TTL、**访问令牌门禁**（严格模式真拦 / 豁免面最小 / 宽限期语义 / 401 带机器可读 code / 静默故障可见性）。<br>原「R1 无鉴权」哨兵断言**已按设计翻转成守卫**（中间件必须在 app 上、豁免面只允许 health）。<br>**仍未覆盖**：`node tools/verify_extension_sync.mjs` 仍不可进 CI（它要起真实服务读真实账号库），改该蓝图**仍必跑它**——但它已改为**复刻扩展的令牌自举流程**，验的是同一条协议 |
| T2 | Monitor 的 **DANGEROUS 分类正确性无断言** | 不验「危险操作不会被放进可执行步骤」这一安全属性 |
| T3 | `browser_pool` 的**事件回调纪律无法被测试拦住** | 违规表现为运行期死锁，不是断言失败 |
| T4 | 扩展端**无单测框架**（in-tree 无测试） | 依赖 3 个 verify 工具 + 人工装载 |
| T5 | `egmp` HTTP 内核无单测；前端无 JS 测试框架（`accounts.js` 1,136 行是最大且回归面最薄的模块） | 改动风险靠人工 |
| T6 | **工具插件的真实平台路径无自动化回归**（`insight_run`/`accounts_browser_open` 只能真机手验；原先的 `factory_create` 已随配置工厂删除） | 契约层（分级/确认门/审计/schema）已有 `tests/test_plugin_api.py` 钉住；"调下去真的对"仍靠真机冒烟 |
| T7 | 前端工具卡片（`chat.js` 的 action/observation/approval 渲染）无自动化回归 | 与 `accounts.js` 同一处境：靠人工看。事件契约本身在 `tests/test_agent_loop.py` 里钉住了，前端只消费它 |
| T8 | **`tools/verify_insight_preview.py` 在 `main` 上长期是 `11/14`，而且它不在 [AGENTS.md](../AGENTS.md) §3 的门禁矩阵里** | 2026-09-24 实测（`aa57acc`）：**P8**「step1 关联表列」、**P9**「列表项渲染」、**P11**「drawio 节点文字」三条红。判据：同一次改动**回退前后结果逐条一致**（都是 11/14、红的是同三条）⇒ 是**既有失败**，与当轮重构无关。<br>★ **两个问题分开看**：① 三条红本身还没定性（夹具过期？还是渲染器真坏了？——P8/P9 吃的是 `step1-relations.md`，P11 吃 `spider-network.drawio` 的节点文字，都在前端渲染侧）；② **更要紧的是它不在矩阵里** ⇒ 改洞察页 / 产物渲染时**没有人会去跑它**，"它红了"这件事传不出来。**待办**：先定性三条，再决定是修渲染还是修期望值，**然后把它补进 §3 矩阵** |

### 仓库卫生（**2026-09-17 重写**）

上一版本节的内容已全部失效（它写"两份新方向调研文档未提交、`tmp/` 未 ignore"——前者从未入库、后者早已 ignore）。以下是**经实测**的现状：

- **已清理（2026-09-17）**：
  - 删除 `docs/方向调研-平台洞察独立发展.md`（**内容已作废**）。它引用的两份文档（《方向调研-公司级Agent平台.md》《方向调研-钉钉MCP落地细节.md》）**从未在本仓存在**，
    其技术前提「钉钉MCP」已否决（改用 Stream 模式机器人）。原文可取回：`git show d0efa84:docs/方向调研-平台洞察独立发展.md`。
  - 清理未跟踪的 ADP 调研残留（根目录 `_body_*` / `_cn_*` / `_raw_*` 抓取产物、`adp_research/`、`adp-{api,kb,multiagent-routing}-research.md`、
    `docs/research/adp-migration-research/`），约 5.8 MB；并删除过期构建产物 `dist/` + `build/`（948 MB，`tools/build.ps1` 可重建）。
  - **修正过期数字**：本文件与 `README.md` / `PLUGIN-SDK.md` / `API.md` 里"4 插件 19 工具 / 迁移 1–13 / 10 篇 ADR / 22 条环境坑"全部按实测重写（详见 CHANGELOG「未发布」）。
- **仍存在的卫生缺口**（未修，需你决定）：
  - ~~**C14 引用的验证脚本不存在**：正文写"并加 `tmp/verify_factory_frontend.py`"，但**全库无此文件**（`tmp/` 现只剩两个 websearch 探针）。
    C14 是一条"页面不报错、测试也不报错"的静默缺陷，**它恰恰是最需要那条断言的一类**。→ 要么补一个 `tools/verify_factory_frontend.py`（真实后端返回 → 真实前端渲染函数，字段改名即红），要么把 C14 里的引用删掉、改为"已修但无自动化回归"。~~
    → **2026-09-22 作废**：C14 与那条断言所属的整条链（`factory.html` / `factory.js` / `/api/factory/*`）已随配置工厂删除，
    补 `verify_factory_frontend.py` 这件事不再有意义。
  - **删除模块的残留 `.pyc`**：`workbench/api/__pycache__/` 下仍有 `routes_knowledge` / `routes_sync` 的字节码，对应源码已按产品方向删除（迁移 1–6 保留历史不可变）；2026-09-22 删除 `egmp/writers/**` 等模块后，同类残留 `.pyc` 也会继续留在 `__pycache__/` 里。**影响仅限目录整洁**，不影响运行；`.gitignore` 已忽略 `__pycache__/`。

## Glossary / where to look next（术语与入口阅读）

| 术语 | 含义 | 从哪读起 |
| --- | --- | --- |
| 数据面 / 触发面 / 执行面 | 三方分工：桌面应用 / 本地热键轮盘 / 用户 Chrome 扩展 | `api/routes_extension.py` 模块文档串 |
| 同步蓝图 | 桌面↔扩展的数据 + 指令通道（快照 2s 轮询 + 指令长轮询） | `api/routes_extension.py`、`extensions/.../sync.ts` |
| 六平面隔离 | 存储 / 鉴权 / Cookie / 缓存 / SW+CacheStorage / IndexedDB（+ 未编号的 BroadcastChannel） | `extensions/.../shield-main.ts`、`docs/EXTENSION-PLANE.md` |
| 专职工作线程 | browser_pool 的单线程 Playwright 队列约束 | `services/browser_pool.py`、ADR-0006 |
| 节奏门控 | 自动登录五重门（填全 / 回读一致 / 提交可用 / 用户接管 / 服务端拒绝） | `services/autologin.py`、`extensions/.../auto-login.ts:108-316` |
| 内化（internalization） | 原四项目能力以 Python 原生重写，而非子进程封装 | ADR-0005、`adapters/*.json` |
| 轮盘（wheel） | Alt+Q 半透明操作盘（三层降级：overlay → popup 窗 → 标签页） | `static/js/accounts.js`、`extensions/.../account-wheel.ts` |
| 分配池 | 账号分配给 config / monitor 用途 | `services/accounts.py` |
| sidecar | Electron 与 Python 服务的伴生进程关系 | `desktop/main.js`、`workbench/server.spec` |
| 出网通道 | 「能直连就不配代理」的更新/发布会话选择 | `desktop/proxy.js`、ADR-0008 |

**首选阅读顺序**：本文件 §Summary/§Architecture（整体结构与风险快照）→ [TOOLS.md](TOOLS.md)（**现有工具与治理**）→ [PITFALLS.md](PITFALLS.md)（真机环境坑）→ `README.md`（产品视角）→ `docs/USER-MANUAL.md`（用户视角）→ `docs/adr/` 十篇（为什么这么设计）→ `docs/SCHEMA.md`（数据契约）→ `docs/API.md`（接口契约）→ `docs/EXTENSION-PLANE.md`（扩展机制与护栏）。
