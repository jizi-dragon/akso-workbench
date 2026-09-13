# Codebase Overview（代码库总览）

_由 learn-codebase 技能生成，本轮复核重写于 **2026-09-13**，锚定 `d0efa84`（v0.3.5，main）。_

> **修订轨迹**：`1e82439`（v0.1，pywebview 壳，已被 git 历史取代）→ `2e725a5`（v0.2.2，Electron sidecar 改造）→ **本轮 `d0efa84`（v0.3.5）**。
> 上一版本文档锚定 v0.2.2，已严重过期（其“11 个路由域 / 迁移 1-11 / Inno Setup / 50 测试函数 / 双写版本号”均已失效），
> 本轮按代码实测全面重写。失效的历史架构文档见 [docs/archive/](archive/README.md)。
>
> **承接说明**：`AGENT.md`（原维护者接手手册）已于 2026-09-13 因整体滞后被删除。其可用内容按变更频率拆分承接：
> §2 代码地图 / §3 六条红线 → 本文件；§4 已知问题台账 → 本文件「风险与毛边」（**编号 C/S/T/B/D 原样保留**）；
> §5 环境坑 22 条 → [PITFALLS.md](PITFALLS.md)；§7 验收基线 → 本文件「验收基线实测」与 [CONTRIBUTING.md](../CONTRIBUTING.md) §7。
> 原文可用 `git show d0efa84:AGENT.md` 取回。

## Summary（定位）

**Akso Workbench（`akso-workbench` v0.3.5，仓库 `akso-mogul`）** 是阿克索实施工程师的**单机 Windows 工作台**，把四个来源项目（akso-cc / mogul_simulator / akso-auto / quick-login）的能力**内化**为一个桌面应用：平台洞察（对象三层理解 + 蜘蛛织网）、配置工厂（蓝图校验 + 幂等创建 + 拓扑编排）、统一账号库（Fernet 加密）、多账号快捷登录（一个 Chrome 内并行身份）、Monitor 监听录制解读。

架构分工（用户定稿）：

- **桌面应用（Electron + FastAPI sidecar）= 数据面 + 触发面**；
- **用户自己的 Chrome 里装载的 MV3 扩展 = 执行面**（六平面隔离 / 会话切换 / 自动填表）；
- **内置 Chromium（Playwright）= 监听与自动化面**（Monitor 录制 / 免密直达 / 自愈）。

终端用户**零依赖原四仓库、零依赖 Node、零联网**（首次装载扩展除外）。服务端仅监听 `127.0.0.1`，**无鉴权**（单用户本机模型，见风险 R1）。

## Tech stack（技术栈）

- **语言：** Python ≥3.12（服务端全部逻辑，`workbench/**.py` **70 文件 10,172 行**）；TypeScript（扩展，`extensions/quick-login/**/*.ts` **23 文件 5,518 行**）；JavaScript（Electron 主进程，`desktop/*.js` **4 文件 1,006 行**）；原生 ES 模块 + CSS（前端 `workbench/static` **24 个 js/css 文件 5,947 行**，含字体共 26 文件 7,761 行，**无框架、无构建步骤**）
  > 口径说明：本表行数为**物理行数**（含空行）。用 `Get-Content \| Measure-Object -Line` 统计会漏掉空行（例如 `client.py` 会得 153 而非 197），已删除的原 `AGENT.md` 里的 8,167 行即为此类偏低的计数。
- **框架 / 运行时：** FastAPI + uvicorn[standard]（REST + SSE + 静态托管）；pydantic v2；**Electron 33** 桌面壳（窗口 / 托盘 / 全局热键 / 更新器 / 出网通道 / sidecar 生命周期）
- **构建 / 包管理：** uv（`uv.lock`，默认清华镜像 `pyproject.toml:37-39`）；hatchling；ruff（`E4/E7/E9/F/I/B`，line-length 110）；**PyInstaller**（`build` extra，`workbench/server.spec` → `dist/AksoServer/` onedir）；**electron-builder → NSIS**（`tools/build.ps1` 一键出安装包）；esbuild 0.24（扩展打包）；版本真源 `pyproject.toml`
- **存储 / 外部服务：** SQLite（WAL，**迁移 1–13**）；Playwright chromium；cryptography Fernet；DeepSeek API；networkx；GitHub Releases（electron-updater）
- **端口：** 18765 服务 · 18766 CDP · 18767 壳会话控制服务
- **已应用技能：** `learn-codebase`（本文件）；分析期另派两个子代理分别深挖**扩展执行面**与**构建/发布链**，其结论已并入下文并逐条对码核验。

## Architecture（架构）

```
Electron 壳 desktop/（只做壳：sidecar 生命周期 / 窗口 / 托盘 / 热键 / 更新 / 出网通道）
  │  main.js 461 行 · updater.js 350 · proxy.js 151 · shell-state.js 44
  ├─ spawn → FastAPI workbench/（全部业务，:18765）
  ├─ :18767 会话控制服务（按账号 persist: 分区开户/聚焦/关窗）
  └─ :18766 CDP（必须在 app ready 前设置）→ browser_pool.connect_over_cdp
        ├─ 用户 Chrome 扩展 = 执行面：六平面隔离 + 会话切换 + 自动填表
        └─ 内置 Chromium   = 监听/自动化：Monitor 录制 / 自动登录 / 免密持久化
```

服务端分层（`workbench/`）：

```
api/（12 个路由域 + lifespan + 静态托管，见 api/__init__.py:74-86）
  ├─ fork 存量：routes_conversations / routes_settings / routes_chat(SSE)
  ├─ 聚合层：routes_modules（模块注册表 + 依赖体检）
  ├─ egmp 原生：routes_insight（洞察四命令）/ routes_factory（蓝图/创建/编排）
  ├─ 账号中心：routes_accounts + routes_browser（托管浏览器）
  ├─ 执行面蓝图：routes_extension（快照 + 指令长轮询 + 状态）
  ├─ 监听：routes_monitor　│　更新：routes_update（读壳状态文件）
  └─ Agent：routes_agent（工具层 + 审计）
services/
  ├─ browser_pool.py 625 行（专职工作线程 / 双模式 / CDP 托管 / 免密 / 自愈）
  ├─ accounts.py 607 · autologin.py 372 · proc.py 236 · deepseek.py 181 · modules.py 172 · storage.py 122
  └─ egmp/（平台内核，36 文件 4,781 行）
       ├─ client 197 · orchestrate 317 · generate 115 · auth 61 · queries 60 · cache 45 · checkpoint 46 · complexity 46
       ├─ insight/  12 文件 1,766 行（spider 359 · flowgraph 270 · crawler 212 · render 175 · drawio 156 · understand 146 · report 91 · assemble 87 · lifecycle 69 · annotate 55 · relations 41）
       ├─ writers/  11 文件 1,558 行（blueprint 513 · workflows 322 · layouts 167 · lifecycle 159 · fields 101 · endpoints 85 · idempotency 80 · menus 49 · picklists 41 · objects 19）
       └─ monitor/   4 文件 553 行（monitor 207 · query_layer 168 · interpreter 148）
harness/（对话 Agent 循环 7 文件 540 行：tools 231 · loop 129 · types 72 · context 43 · prompts 36 · safety 26）
db.py 341 行（SQLite 单连接 + 13 条迁移常量表 + 方言边界）
```

**四个来源项目的内化方式**（[ADR-0005](adr/0005-native-internalization-of-four-projects.md)）：不再是“子进程封装”，而是 Python 原生重写；`adapters/*.json` 降级为**只读参考存档**（`"mode": "native-python-internalized"`，`"frozen": true`），仅保存端点/产物口径对照。

## Key modules（关键模块）

| 路径 | 职责 |
| --- | --- |
| `workbench/server_entry.py` | 冻结态入口（PyInstaller 顶层脚本，只用绝对导入——`verify_packaging.py` P2 强制） |
| `workbench/main.py` | Web 模式入口（13 行，起 uvicorn + 开浏览器） |
| `workbench/api/__init__.py` | `create_app`：fork 路由显式注册 + `_optional_routers` 容错注册 9 个新域 + lifespan(`db.connect`) + 静态托管 |
| `workbench/api/routes_extension.py` | **执行面蓝图**（399 行）：`/snapshot` `/commands`(长轮询) `/ack` `/state` `/health` `/launch-chrome`；指令队列内存态，`ext_cmd_seq` 落 settings |
| `workbench/services/browser_pool.py` | **专职工作线程**（Playwright sync 单线程单实例约束；操作走队列作业）；headful/headless 双模式；storage_state 持久化；`heal_count≤2` 自愈；CDP 挂接内置 Chromium |
| `workbench/services/accounts.py` | Fernet 密钥管理、环境/账号/盒子 CRUD、分配池（config/monitor）、备份导出导入（`fernetKey` 随文件下发） |
| `workbench/services/autologin.py` | 节奏门控五重门（MAIN world JS 注入 + TokenCapture JWT） |
| `workbench/services/egmp/` | 平台内核：`client`（code==0 信封 / paged_post / 55min token 缓存）、`insight`、`writers`、`orchestrate`、`monitor` |
| `workbench/db.py` | SQLite + `MIGRATIONS`（**1–13**；13 = `add_created_at_indexes`）；迁移**只增不改** |
| `workbench/harness/` | 对话 Agent 循环；注意 `harness/tools.py` 是**独立于** `routes_agent` 的另一套工具表 |
| `extensions/quick-login/` | MV3 + TS 扩展源（npm workspace，单包 `packages/extension`）：`parallel-session.ts` 1,589 行（≈29% TS）、`shield-main.ts` 806 行（MAIN world，平面 1/4/5/6）、`auto-login.ts` 377 行 |
| `tools/build.ps1` | 六步打包：bump → 扩展重建 → `uv sync` → release commit/push → PyInstaller → electron-builder NSIS |
| `tools/*.py` `tools/*.mjs` | 23 个工具：构建/发布 + 13 个回归/验收脚本 + `db_maintenance.py` |

## Data & control flow（数据流）

**① 人工登录（执行面 = 用户 Chrome）**：账号中心 / 轮盘（Alt+Q）→ `routes_extension` 写指令队列 → 用户 Chrome 里的扩展每 **2s** 拉 `/snapshot`（`snapshotId` 未变则跳过），并用一条独立长轮询（`wait=15`）拉 `/commands?after=<cursor>` → 会话切换 / 自动登录。凭据以 **Fernet 密文 + 原始密钥一同**下发（`accounts.py:432-464` → `routes_extension.py:126`），扩展端在 SW 内存解密后本地用 AES-GCM-256 落盘。指令序号跨重启单调递增（`settings.ext_cmd_seq`）。

**② 自动化 / 监听（执行面 = 内置 Chromium）**：`routes_browser.open {account_id}`（默认 headful）→ browser_pool 专职线程：解密凭据 → 一账号一 BrowserContext（`storage_state` 持久化 `runtime/browser-states/`）→ TokenCapture → 注入节奏门控 → 免密直达 / 自愈重登 → `routes_monitor` 在该会话上启停录制。

**③ 洞察 / 工厂**：`insight.html` / `factory.html` → SSE 路由 → `egmp.insight` / `orchestrate.run_full_workflow` 直连 eGMP → 产物落 `runtime/{insight,factory}/`，`insight_runs` / `blueprint_jobs` 记账。

**④ 更新链**：Electron 启动 +20s 首查、之后每 6h（`updater.js:19-20`）→ 静默下载 → 下载完成后才弹一次对话框（默认「退出时安装」）→ `before-quit` **先 `killServer()` 再 `installOnExit()`**（顺序是承重的，安装器要覆写 `AksoServer.exe`）。更新状态经原子写的 `shell-state.json` 暴露给 Python 的 `GET /api/update`。

**状态存放**：SQLite（`%APPDATA%\AksoWorkbench\workbench.db`，WAL）；任务产物 `runtime/`；浏览器快照 `runtime/browser-states/`；配置在 `settings` 表；用户可干预的文件级开关 `proxy.txt`（强制代理 / 强制直连）。

## Entry points（入口）

- **桌面版（终端用户）**：`AksoWorkbench-<版本>-setup.exe`（NSIS，`oneClick:false`，artefact 名由 `desktop/package.json:57` 决定）
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

# 测试 / 体检（本轮实测，见下方“验收基线实测”）
.\.venv\Scripts\python.exe -m pytest              # 63 passed / 1 failed
.\.venv\Scripts\python.exe tools\verify_packaging.py   # 8/8 PASS
.\.venv\Scripts\uvx.exe ruff check .              # ⚠️ 本机 uvx 不存在，无法执行

# 扩展 / 前端（改扩展时）
cd extensions\quick-login; npm run build; npm run typecheck
python tools\verify_extension_boot.py; python tools\verify_extension_isolation.py
node tools\verify_host_logic.mjs; node tools\verify_extension_sync.mjs

# 打包
powershell -File tools\build.ps1
```

### 验收基线实测（2026-09-13，本机 `d0efa84`）

| 命令 | 原 `AGENT.md` 声称 | 本轮实测 | 判定 |
|---|---|---|---|
| `python -m pytest` | 64 passed | **63 passed, 1 failed** | ⚠️ 唯一失败是环境依赖（见下） |
| `uvx ruff check .` | 9 条违规 | **无法执行**：`.venv\Scripts\uvx.exe` / `uv.exe` 不存在，venv 内也无 `ruff` 模块，PATH 上无 ruff | ❌ 门禁在本机不成立 |
| `python tools\verify_packaging.py` | 8/8 PASS | **8/8 PASS** | ✅ |

- **pytest 唯一失败**：`tests/test_modules.py::test_adapter_paths_exist` —— `AssertionError: akso-auto repoPath 无效：D:\ai_assistant\akso-auto`。`adapters/*.json` 指向的四个来源仓库（`D:\ai_assistant\{akso-auto,akso-cc,mogul_simulator,quick-login}`）**在本机全部不存在**。这是**环境依赖型断言**，不是代码缺陷：该测试校验只读存档的路径有效性，而存档在本机已失去意义（`frozen: true`）。
- **沙箱影响**：在默认 `workspace-write` 沙箱下 pytest 表现为 `38 passed / 7 failed / 19 errors`，全部由 `PermissionError: [WinError 5]` 引起（`conftest.py:12` 用 `tempfile.mkdtemp()` 在系统临时目录建隔离数据目录，且测试需要创建子进程管道）。**在放开临时目录与管道限制后复跑即得上表的 63/1**。据此判断：**仓库的测试基线本身是健康的，已删除的原 `AGENT.md` 记录的“64 passed”与实测只差上述 1 项环境失败**。
- **原 `AGENT.md` §8 的依赖纪律已过期**（该文档于 2026-09-13 因整体滞后被删除）：它称“`uv` / `ruff` 不在 PATH，可用的是 `.venv\Scripts\uv.exe` / `uvx.exe`”——**这两个文件在本机并不存在**（`pyvenv.cfg` 记录 `uv = 0.12.10`，但 venv 已不再内置这两个 shim）。因此 `CONTRIBUTING.md` 里的 lint 门禁与 `uv sync` 命令在本机都需要先装 uv。

## Conventions（约定）

- **路由注册**：新路由在 `api/__init__.py` 显式登记（fork 域直挂，新域走 `_optional_routers` 容错表，单域装配失败不拖垮应用）。
- **请求体一律 pydantic `BaseModel`**：pydantic 默认**忽略多余字段**，漏字段会**静默失效**（`AccountPatch` 漏 `box` 是已发生事故，环境坑 #6）。
- **Playwright 纪律**：sync API 单线程单实例 —— 所有浏览器操作**必须**走 `browser_pool` 专职线程队列；**事件回调内禁止连接调用**（只入队，worker 空隙 `drain()`）；同线程禁第二个 `sync_playwright()`；**测试不得另起 chromium 夹具**。
- **DB 迁移只增不改**：历史迁移（尤其 1–6）不可变。`job_logs` 表建了索引但零写入是**故意**的（日志落磁盘），不要“顺手”改成入库。
- **凭据不落明文**：新通道必须沿用「密文 + 密钥」回环语义；接口响应必须剔除 `password_enc`；文档与日志中出现的一律是占位值。
- **Monitor 危险操作永不回放**：`API_MAP` 中对应 writers 的常量必须**实证来源**，不许凭观察猜端点。
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
| **R1** | **`/extension/*` 与 `/api/*` 全无认证**：本机任意进程可拉快照（快照里**同时含 Fernet 密钥与全部密文口令**，等于直接拿到所有测试账号明文）、可下发指令、可读导出备份 | `routes_extension.py:26`；`accounts.py:432-464` |
| **R2** | **明文凭据可达未绑定页面**：待登录凭据仅以 `tabId` 为键、`getPendingAutoLogin` **读后不删**（60s 内可重复取）、SW 处理器不校验发送方 URL 也不校验绑定、`unbindTab` 不清该条目，而 `auto-login.js` 以 `all_frames` 注入 `<all_urls>`，子帧回退会填**任意**密码框 —— 绑定标签页在 60s 内导航到非绑定源，口令会被打进该页 DOM | `pending-login.ts:37-44`、`service-worker.ts:161-174`、`parallel-session.ts:1037-1052`、`manifest.json:57-61`、`auto-login.ts:319-330` |
| **R3** | 扩展 `host_permissions: ["<all_urls>"]` 权限面宽于功能所需 | `manifest.json:35` |
| **R4** | 扩展端落盘密钥 `sb:encryptionSeed` 是**明文 `randomUUID()`** 存在同一 `storage.local`，PBKDF2-100k + 硬编码盐派生 → 只是设备绑定混淆；且每次加解密都重跑 PBKDF2，无缓存 | `credentials.ts:10-42,74-90` |
| **R5** | `settings.account_fernet_key` **密钥轮换无实现**：手改它会让扩展静默丢弃全部账号（接口仍能读） | ADR-0007 后果 |

### 代码缺陷（本轮逐条对码确认仍在 `d0efa84` 成立）

| # | 位置 | 问题 |
|---|---|---|
| **C1** | `browser_pool.py:247-248` | 状态判断写反：先置 `entry.status="stopped"`，再判断 `== "online"` → 该分支恒假，首次登录后关窗误报「已关闭」 |
| **C2** | `routes_insight.py:215` | 产物路径守卫用**纯字符串前缀**（未补分隔符）：`.../insight/abc` 会“包含” `.../insight/abc_evil` → 应改 `Path.is_relative_to()` |
| **C3** | `routes_agent.py:173` | `GET /api/agent/audit` 用 `SELECT *` 全列回吐，含 `args` 原始 JSON（可能含凭据） |
| **C4** | `routes_extension.py:240-242` | ack 回收是「全部 ack 才清」：一条指令永不被 ack 则队列既不回收也不前进，`_acked` 只增不减（且客户端只 ack 最大 cursor，`sync.ts:229-235` → **实际上永不排空**） |
| C5–C7, C10–C13 | `services/modules.py:145`、`routes_factory.py:47,63`、`routes_agent.py:78`、`static/js/app.js:78`、`static/css/wheel.css`、`browser_pool.py` 的 `_close_entry` / `_submit` | `status` 映射语义反了（全挂 = `degraded`、部分挂 = `missing`）/ `native_orchestrate_imported_validate` 定义在调用点之后 / `routes_agent` 跨模块调用 `routes_insight` 私有函数 / `app.js:78` 死分支 / `wheel.css` 是无引用死文件 / 关窗不停监听、多数浏览器作业无超时 |

### 构建与打包

| # | 问题 | 证据 |
|---|---|---|
| **B-a** | **chromium 静默不打包**：`server.spec` 仅在 `%LOCALAPPDATA%\ms-playwright\chromium*` **存在时**收集，无 else 无报错 → 缺它的机器会打出一个“成功”的**没有浏览器**的安装包，只在运行期暴露；`build.ps1` 与 `verify_packaging.py` **都不做前置检查** | `server.spec:44-48`；ADR-0009:46-47 |
| **B-b** | `dist/AksoServer` 从不清理却被整目录拷进安装包 → 陈旧 chromium 目录累积膨胀（≈350MB 量级），且可能发出旧浏览器 | `desktop/package.json:35-37` |
| **B-c** | `git add` 全有全无 + `git commit` 的 stderr 被 `2>$null` 吞掉 + `git push` 三次失败后**仅打印一行并继续** → 可能出现「安装包标着 vX 但树里不是 vX」 | `build.ps1:67-79` |
| **B-d** | `bump.py` 退出码未检查（`build.ps1:36,39`）→ 失败时 `$version` 为空或混入错误文本，一路流到产物命名 | `build.ps1:35-41`（未校验 `$LASTEXITCODE` 与版本号格式） |
| **B-e** | `bump.py` 的扩展侧写盘是**非锚定**正则 `("version"\s*:\s*")` `count=1`（首个命中即改）；pyproject 用无 `count` 的 `re.sub` 而 `_read` 只取首个匹配 → 存在改错键/读写分歧的余地；文件缺失时**静默跳过**（B8）；递增**不幂等**（B9：手工 `bump push` 后再 `build.ps1 -Bump push` 连跳两版） | `bump.py:45,52,63,66,72,83-85` |
| **B-f** | `desktop/package-lock.json` 版本仍是 **0.2.1**（`package.json` = 0.3.5），`bump.py` 不写它 → 发布提交后 `npm install` 立刻把工作区弄脏。（注：已删除的原 `AGENT.md` B7 与 `CONTRIBUTING.md` 均称扩展 `package-lock.json` 停在 0.2.20 —— **该说法已过期**，实测已是 0.3.5） | `desktop/package-lock.json`、`extensions/quick-login/package-lock.json` |
| **B-g** | 桌面壳 `files` 白名单只列 4 个 `.js` + `package.json`，**不含 `desktop/icon.ico`**，而托盘要读它、失败被 `catch` 吞掉 → 打包态很可能**没有托盘图标**；`verify_packaging.py` P6 只 glob `*.js`，非 JS 资源无人守。（本机无 `desktop/dist`，未实机复验） | `desktop/package.json:26-32`、`main.js:325-328`、`verify_packaging.py:108-110` |
| **B-h** | `verify_update_flow.py:40` 硬编码 `%APPDATA%\AksoWorkbench\shell-state.json` 且忽略 `WORKBENCH_DATA`，而 `shell-state.js:16` 是认这个变量的 → 设了该变量时该流程**必然超时**，且会写进真实用户数据目录 | 两处 |
| **B-i** | **仓库无 CI**：无 `.github` 目录、无任何 pipeline YAML、**无 git hook**（`bump.py:13` 提到的 pre-push hook 是空想）。验收全靠本地手工纪律 | 全仓库搜索确认 |

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
| T1 | `/extension/*` 蓝图**无 pytest 覆盖** | 唯一回归是 `node tools/verify_extension_sync.mjs`——改该蓝图必跑 |
| T2 | Monitor 的 **DANGEROUS 分类正确性无断言** | 不验「危险操作不会被放进可执行步骤」这一安全属性 |
| T3 | `browser_pool` 的**事件回调纪律无法被测试拦住** | 违规表现为运行期死锁，不是断言失败 |
| T4 | 扩展端**无单测框架**（in-tree 无测试） | 依赖 3 个 verify 工具 + 人工装载 |
| T5 | `egmp` HTTP 内核无单测；前端无 JS 测试框架（`accounts.js` 1,137 行是最大且回归面最薄的模块） | 改动风险靠人工 |

### 仓库卫生（本轮新发现）

- **工作区有未提交内容**：`docs/CODEBASE_OVERVIEW.md`（本文件）、两份**新方向调研文档**（`docs/方向调研-公司级Agent平台.md`、`docs/方向调研-钉钉MCP落地细节.md`，2026-09-09）、以及 `tmp/`（含一份 `quicklogin-diag-*.json` 诊断产物与 `verify_rules_and_download.py` 临时脚本）。`tmp/` 已出现在 git status，**未 ignore**。
- **删除模块的残留 `.pyc`**：`workbench/api/__pycache__/` 下仍有 `routes_knowledge` / `routes_sync` 的字节码，对应源码已按产品方向删除（迁移 1–6 保留历史不可变）。

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

**首选阅读顺序**：本文件 §Summary/§Architecture（整体结构与风险快照）→ [PITFALLS.md](PITFALLS.md)（真机环境坑）→ `README.md`（产品视角）→ `docs/USER-MANUAL.md`（用户视角）→ `docs/adr/` 十篇（为什么这么设计）→ `docs/SCHEMA.md`（数据契约）→ `docs/API.md`（接口契约）→ `docs/EXTENSION-PLANE.md`（扩展机制与护栏）。
