# AGENT.md —— 维护者与 AI 助手的接手手册

> **这份文档是「接手这个仓库」的最短路径**：先看它，再按 §2 的代码地图深入。
> 它替代了旧的两份会不断过期的文档（`docs/架构分析.md`、`docs/SESSION-DIGEST.md`，已归档到
> [docs/archive/](docs/archive/README.md)），把其中的**实测教训与红线**沉淀为常驻内容。
>
> 变更频率：O(结构 / 坑)。发现新的坑、修掉已知问题、改变红线时更新本文档。

## 1. 项目快照（v0.3.4，2026-09-11 实测）

| 项 | 值 |
|---|---|
| 版本真源 | `pyproject.toml`（`tools/bump.py` 五写同步），当前 **0.3.4** |
| 服务端 | Python 3.12+ / FastAPI / SQLite(WAL) / httpx / pydantic / networkx，`workbench/**.py` **70 文件 8,167 行** |
| 桌面壳 | Electron 33，`desktop/*.js` **4 文件 853 行** |
| 前端 | 原生 ES 模块 + CSS，`workbench/static` **24 文件 5,360 行**（无框架、无构建步骤） |
| 浏览器扩展 | MV3 + TypeScript，`extensions/quick-login/**/src` **23 文件 4,852 行**（esbuild 打包） |
| 测试 | `tests/*.py` **11 文件 1,151 行 / 64 项** |
| 工具 | `tools/*` **23 文件 2,572 行**（构建、发布、13 个回归/验收工具） |
| 数据库 | 迁移 **1–13** 全在册；实测活跃数据：环境 3 / 账号 4 / 会话 2 / 洞察运行 4 |
| 端口 | 18765 服务 · 18766 CDP · 18767 壳控制服务 |
| 运行时依赖 | **零**原仓库、**零** Node（终端用户） |

## 2. 代码地图与阅读顺序

### 2.1 三条进程面 + 一个执行面

```
Electron 壳 desktop/（只做壳：sidecar 生命周期 / 窗口 / 托盘 / 热键 / 更新 / 出网通道）
  ├─ spawn → FastAPI workbench/（全部业务）
  ├─ :18767 会话控制服务（按账号 persist: 分区开户/聚焦/关窗）
  └─ :18766 CDP（app ready 前设置）→ browser_pool connect_over_cdp
        ├─ 扩展（用户 Chrome）= 执行面：六平面隔离 + 会话切换 + 自动填表
        └─ 内置 Chromium = 监听/自动化：Monitor 录制 / 自动登录 / 免密持久化
```

### 2.2 服务端 `workbench/`

| 模块 | 职责 | 详细 |
|---|---|---|
| `api/__init__.py` | 应用装配（路由注册 + 静态托管 + lifespan） | [API.md](docs/API.md) §0 |
| `api/routes_conversations/settings/chat` | 会话 / 设置 / SSE 对话 | [API.md](docs/API.md) §1–4 |
| `api/routes_modules` | 模块注册表 + 依赖体检 | §5 |
| `api/routes_insight` | 平台洞察四命令（egmp 原生） | §6 |
| `api/routes_factory` | 配置工厂（蓝图/创建/编排） | §7 |
| `api/routes_accounts` | 统一账号库（环境/账号/盒子/池/备份/导入） | §8 |
| `api/routes_browser` | 托管浏览器（open/focus/close/forget/saved/navigate） | §9 |
| `api/routes_agent` | Agent 工具层 + 审计 | §10 |
| `api/routes_extension` | **执行面蓝图**：快照 + 指令长轮询 + 状态 | §11 / §12.3 |
| `api/routes_monitor` | 监听 start/stop/status | §13 |
| `api/routes_update` | 版本 + 更新状态（读壳状态文件） | §14 |
| `services/browser_pool.py` | 专职线程 + 双模式 + CDP 托管 + 免密 + 自愈 + 监听挂载 | [ADR-0006](docs/adr/0006-browser-pool-single-thread.md) |
| `services/autologin.py` | 节奏门控五重门（MAIN world JS 注入 + TokenCapture） | [CONFIG.md](docs/CONFIG.md) §4.1 |
| `services/accounts.py` | Fernet + 环境/账号 CRUD + 盒子 + 分配池 + 备份导入 | [SCHEMA.md](docs/SCHEMA.md) §3.5 |
| `services/egmp/` | 平台内核：`client`/`auth`/`cache`/`queries` + `insight`(11) + `writers`(9) + `orchestrate`/`complexity`/`generate` + `monitor` | [ADR-0005](docs/adr/0005-native-internalization-of-four-projects.md) |
| `db.py` | SQLite 单连接 + 迁移（`_sqlx_migrations`）+ 方言边界 | [SCHEMA.md](docs/SCHEMA.md) §2 |
| `services/{proc,modules,settings,storage,deepseek}.py` | 遗留子进程封装 / 模块注册 / 设置 / 存取 / DeepSeek 客户端 | §4 已知问题 |
| `harness/` | 对话 Agent 循环（`loop`/`tools`/`prompts`/`context`/`safety`/`types`） | `harness/tools.py` 是**独立于** `routes_agent` 的另一套工具表 |

### 2.3 建议阅读顺序

1. 本文档 §3 红线 → §5 环境坑；
2. [README.md](README.md)（产品视角）→ [docs/USER-MANUAL.md](docs/USER-MANUAL.md)（用户视角）；
3. [docs/adr/](docs/adr/) 十篇（**为什么这么设计**，含被放弃的方案与复核触发器）；
4. [docs/SCHEMA.md](docs/SCHEMA.md)（数据契约）→ [docs/API.md](docs/API.md)（接口契约）；
5. [docs/EXTENSION-PLANE.md](docs/EXTENSION-PLANE.md)（扩展侧机制与协议护栏）；
6. 需要动哪里，再按 §2.2 表格进入具体文件。

## 3. 维护红线（六条）

1. **原四项目仓库只读**：任何情况下不修改 `adapters/*.json` 指向的目录。
   （`adapters/*.json` 自身的 `$schema` 字段是死链，改动需谨慎——见 §4.4。）
2. **数据库迁移只增不改**：一律追加新版本号，**历史迁移不可变**（尤其 1–6）。
   `job_logs` 表建了索引但零写入是**故意**的（日志落磁盘），不要「顺手」改成入库。
3. **`browser_pool` 单线程纪律**：新增浏览器能力一律走队列作业；
   **事件回调内禁止连接调用**（只入队，worker 空隙 `drain()`）；
   **测试不得另起 chromium 夹具**；同线程禁止第二个 `sync_playwright()`。
4. **凭据不落明文**：新通道必须沿用「密文 + 密钥」回环语义；
   接口响应必须继续剔除 `password_enc`；文档与日志中出现的一律是占位值。
5. **Monitor 危险操作永不回放**：`API_MAP` 对位 writers 的常量必须**实证来源**，不许凭观察猜端点。
6. **版本只走 `tools/bump.py` 五写 + CHANGELOG 同步**：手改单一文件会导致安装包命名与产物校验错位。

## 4. 已知问题清单（只记录，未修）

> 2026-09-11 文档重构时的盘点结果。修复需另开一轮——**已同轮修掉的 3 条已在此标注**，
> 其余每条都给了定位与建议动作，可逐条独立处理。

### 4.1 代码缺陷（确定性）

| # | 位置 | 问题 | 建议动作 |
|---|---|---|---|
| C1 | `services/browser_pool.py:247-248` | 状态判断写反：先置 `entry.status="stopped"`，再判断 `entry.status == "online"`，该分支恒假 → 首次登录后关窗误报「已关闭」（应为「已关闭（登录态已保存）」） | 先取 `was_online = entry.status == "online"` 再赋值 |
| C2 | `api/routes_insight.py:215` | 产物路径守卫用**纯字符串前缀**（未补分隔符）：`.../insight/abc` 会「包含」`.../insight/abc_evil` | 改 `Path.is_relative_to()` |
| C3 | `api/routes_agent.py:173` | `GET /api/agent/audit` 用 `SELECT *` 全列回吐，含 `args` 原始 JSON（可能含凭据） | 列白名单 + `args` 脱敏，与 [ADR-0007](docs/adr/0007-credentials-never-plaintext.md) 对齐 |
| C4 | `api/routes_extension.py:240-242` | ack 回收是「全部 ack 才清」：一条指令永不被 ack（扩展取回后崩溃）则队列既不回收也不前进，`_acked` 只增不减 | 单条即时回收，或给队列/ack 加 TTL |
| C5 | `services/modules.py:145` | `status` 映射可读性反了：全挂 = `degraded`，部分挂 = `missing` | 语义对调 |
| C6 | `api/routes_factory.py:47,63` | `native_orchestrate_imported_validate` 定义在调用点之后，命名不符私有约定 | 改名 `_validate_blueprint` 并前置 |
| C7 | `api/routes_agent.py:78` | 跨模块调用 `routes_insight._start_job/_run_job/_finish_job` 等私有函数 | 抽到 service 层 |
| ~~C8~~ | ~~`api/routes_agent.py:112`~~ | ~~`run_insight` 工具描述仍写「子进程封装」，实际已原生化~~ | ✅ **本轮已修**（描述改为「egmp.insight 原生实现」） |
| ~~C9~~ | ~~`services/proc.py:3`、`services/autologin.py:4`、`api/routes_modules.py:1`、`api/routes_agent.py:7`、`services/egmp/generate.py:3`、`workbench/db.py:10`~~ | ~~文档字符串引用**已删除**的 `docs/模块契约.md`、`docs/迁移台账.md`、`docs/数据层决策.md`~~ | ✅ **本轮已修**（改指向 docs/API.md / docs/CONFIG.md / docs/adr/） |
| C10 | `static/js/app.js:78` | 死分支：`if (seen) { sessionStorage.setItem(...); return; }` 内的写入冗余 | 删除该行 |
| C11 | `static/css/wheel.css` | **无引用的死文件**（轮盘页用内联 `<style>`；`static/js/accounts.js:108` 注释还提到它） | 删文件 + 清注释 |
| C12 | `services/browser_pool.py:_close_entry` | 关闭会话**不自动停监听**（未清 `entry.monitor`） | 在 `_close_entry` 中 stop monitor |
| C13 | `services/browser_pool.py:_submit` 调用点 | 多数作业**无超时**（仅 `monitor_start` 给 90s）→ 页面卡住时 HTTP 请求一直挂着 | 按作业性质加超时 |

### 4.2 安全与产品级隐患

| # | 位置 | 问题 | 详见 |
|---|---|---|---|
| S1 | 扩展 `core/pending-login.ts` | **明文凭据 60s 投递窗口**：`getPendingAutoLogin` 读后不删 | [ADR-0007](docs/adr/0007-credentials-never-plaintext.md) R1 |
| S2 | `/extension/*`、`/api/*` | **无任何认证**：本机任意进程可拉快照（含 Fernet 密钥 + 全部密文口令）、可下发指令、可读导出备份 | ADR-0007 R2 |
| S3 | 扩展 `manifest.json` | `host_permissions: ["<all_urls>"]` 权限面宽于功能所需 | ADR-0007 R3 |
| S4 | 桌面 + 扩展 | **Alt+Q 双绑**：桌面全局热键与扩展 `quick-wheel` 同键位，桌面运行时扩展侧被压制 | [EXTENSION-PLANE.md](docs/EXTENSION-PLANE.md) §8.2 |
| S5 | `settings.account_fernet_key` | 密钥轮换**尚无实现**：手改它会让扩展静默丢弃全部账号（接口仍能读） | ADR-0007 后果 |

### 4.3 测试缺口

| # | 缺口 | 影响 |
|---|---|---|
| T1 | `/extension/*` 蓝图**无 pytest 覆盖** | 唯一回归是 `tools/verify_extension_sync.mjs`——改该蓝图必跑 |
| T2 | Monitor 的 **DANGEROUS 分类正确性无断言** | `tests/test_native.py` 只验分类/解读基本行为，不验「危险操作不会被放进可执行步骤」 |
| T3 | `browser_pool` 的**事件回调纪律无法被测试拦住** | 违规表现为运行期死锁，不是断言失败 |
| T4 | 扩展端**无单测框架** | 依赖 3 个 verify 工具 + 人工装载 |

### 4.4 构建 / 脚本 / 元数据债

| # | 位置 | 问题 | 建议动作 |
|---|---|---|---|
| B1 | `tools/build.ps1:36,39` | `bump.py` 退出码**未检查**；失败时 `$version` 为空或混入错误文本，一路流到产物命名 | 校验 `$LASTEXITCODE` + 版本号格式 |
| B2 | `tools/build.ps1:69,71-76` | `git commit` 失败被 `2>$null` 吞掉；`git push` 三次失败后**仅打印一行并继续** | commit 失败改为显式告警；push 失败默认致命 |
| B3 | `tools/build.ps1`（全脚本） | **未调用** `tools/verify_packaging.py`；仓库**无 CI**（无 `.github/workflows`） | 在步骤 1 之前插入体检调用 |
| B4 | `tools/build.ps1:48-57` | 扩展校验只看产物**存在且版本号匹配**，不校验「是本次构建产出的」 | 校验 mtime 或 `BUILD_OK` 标记 |
| B5 | `workbench/server.spec:44-48` | 只在 `%LOCALAPPDATA%\ms-playwright\chromium*` 存在时收集 chromium → 缺它会打出**没有浏览器的包**（仅运行期暴露） | 在 `build.ps1` 前置检查该目录 |
| B6 | `desktop/package-lock.json` | `version` 仍是 **0.2.1**（`package.json` = 0.3.4）；`bump.py` 不写它 | 加进 bump 五写，或移出 git 并 ignore |
| B7 | `extensions/quick-login/package-lock.json` | `version` 仍是 **0.2.20** | 同上 |
| B8 | `tools/bump.py:52-59` | `if DESKTOP_PKG.exists()` 守卫：文件缺失会**静默跳过**（产出版本错位的安装包） | 缺文件改为报错退出 |
| B9 | `tools/bump.py:83-85` | 递增不幂等：手工 `bump push` 后再 `build.ps1 -Bump push` 会**连跳两版** | 记录上次动作或要求显式确认 |
| B10 | `tools/publish_release.py:358-376` | 末尾「匿名复验」只提示不致命（`return 0`） | 加 `--strict` 开关 |
| ~~B11~~ | ~~`tools/setup.ps1` §5~~ | ~~仍把 **Node** 当必检项，文案说「洞察/工厂功能不可用」~~ | ✅ **本轮已修**（改为 `INFO` 级信息项，不计入失败，口径对齐 [ADR-0005](docs/adr/0005-native-internalization-of-four-projects.md)） |
| ~~B12~~ | ~~`adapters/*.json` 的 `$schema`~~ | ~~指向已删除的 `docs/模块契约.md#adapters`~~ | ✅ **本轮已修**（改为 `docs/SCHEMA.md`，仅动 `$schema` 字段） |
| B13 | `tools/bump.py:72` | `.version.json` 写入无尾换行（其他写入点都显式 `newline="\n"`） | 补 `newline="\n"` |
| B14 | `uvx ruff check .` | **9 条违规**（见 §7 验收基线） | 4 条可 `--fix`，其余手工 |

### 4.5 文档债（本轮已处理大部分）

| # | 位置 | 问题 |
|---|---|---|
| D1 | `workbench/static/pages/factory.html:16,58-59` | 文案过时：「封装 akso-auto…（原仓库只读）」「凭证导出为临时 env 文件（任务后删除）」——实际已原生化，**不存在临时 env 文件** |
| D2 | `workbench/static/pages/insight.html:16,56` | 文案过时：「封装 akso-cc…（原仓库只读）」「凭证内联注入（--url/--user/--pass）」——实际已原生化 |
| D3 | 历史留档 | `docs/EXTENSION-INSTALL.md` 仍为独立文档（本轮保留并加交叉引用），其「发布到 GitHub Releases」一节是 `build.ps1` 的发布步骤权威来源 |

## 5. 环境坑清单（20+1 条，全部来自实机事故）

> 这些是**只在真机复现过**的教训，很多无法从代码反推。新增坑请追加编号。

| # | 坑 | 处置 |
|---|---|---|
| 1 | Playwright sync 单线程单实例 | 所有浏览器操作必须走 `BrowserPool` 专职线程队列；**测试勿另起 chromium 夹具** |
| 2 | **事件回调内禁止连接调用**（`response.text()` / `page.evaluate` 重入 = worker 死锁） | 事件只入队，worker 作业间隙统一 `drain()`（0.2.2 严重修复） |
| 3 | **CDP 挂接三律** | `remote-debugging-port` 必须 app ready **前**设置；**先 `:18767` 开户再 connect**（Electron33 怪癖）；Playwright 实例由 `_CdpState` 单一持有 |
| 4 | ES 模块严格模式下**重复函数声明 = 整模块加载失败** | 表现为页面停在静态「加载中」（accounts.js `isOnline` 事故） |
| 5 | PS5.1 脚本 | `.ps1` 必须 UTF-8 **BOM**；不支持 `??`；heredoc `<<` 不可用；中文经命令行参数传 curl 会乱码（写文件 + `--data-binary @file`） |
| 6 | pydantic `BaseModel` **默认忽略多余字段** | 请求体必须显式建模（`AccountPatch` 漏 `box` 导致静默失效） |
| 7 | `build.ps1` **禁用 `$ErrorActionPreference='Stop'`** | PS5.1 把 git/uv 的 stderr 进度当终止错误 |
| 8 | 关窗回收 | 退出走 `taskkill /T /F` 防孤儿 chromium；同线程禁止第二个 `sync_playwright` |
| 9 | 网络：GitHub/PyPI 间歇抖动 | 重试循环；uv 已配清华镜像。**push 的代理口径要按实际情况定**：旧经验是「全局代理常失效 → 用 `git -c http.proxy= push`」，但 2026-09-11 实测本机**直连 GitHub 会被重置**（`Recv failure: Connection was reset`），必须**显式走系统代理**：`git -c http.proxy=http://127.0.0.1:7890 push`。两种都失败时先探测 `Get-NetTCPConnection -LocalPort 7890 -State Listen` 与 `Invoke-WebRequest -Proxy` 哪个通道真的通 |
| 10 | 自愈计数 | 成功时**不可清零**（会掩盖已自愈事实）；上限 2 防死循环 |
| 11 | 桌面壳只开一个实例 | 第二实例端口冲突，**只出窗口不连服务** |
| 12 | 扩展更新 `dist` 后 | **必须在扩展卡片点「重新加载」**（Chrome 缓存 SW 脚本；顽固时删档案 `Default/Service Worker/`） |
| 13 | 版本号 | 只走 `tools/bump.py` 五写，勿手改单一文件（安装包命名会错位） |
| 14 | **字段名双语义** | DB 行 snake_case（`env_base_url`）vs `export_backup` 输出 camelCase（`envBaseUrl`）——`routes_extension` 曾读错键 → 快照 host 恒空 → 扩展**静默丢弃全部账号**（0.2.4 主断点） |
| 15 | **扩展游标持久 vs 服务端内存 seq** | 指令序号必须跨重启单调递增（已落 `settings.ext_cmd_seq`），否则重启一次指令通道整体哑火且无报错 |
| 16 | **SW 自消息死链** | SW 内 `chrome.runtime.sendMessage` **不投递给自身** `onMessage`——跨模块复用行为请直调函数（`toggleAccountWheel` 已抽至 `account-wheel.ts`） |
| 17 | **Fernet 键位规范** | sign-key = `key[:16]`（HMAC）、enc-key = `key[16:32]`（AES-CBC）——写反导致扩展端全员解密失败（0.2.6 断点之一） |
| 18 | **WebCrypto AES-CBC 自动去 PKCS7 填充** | 解密结果**不可**再按尾字节手工剥离（否则把口令尾字符当填充长度剥掉，0.2.6 断点之二） |
| 19 | Playwright 测 MV3 扩展 | `ctx.service_workers` 需先开一个页面才暴露目标；**headless 下 MV3 扩展不加载**（必须 `headless=False` + `--load-extension`） |
| 20 | **孤儿 uvicorn 占 18765** | 测试脚本异常退出会遗留服务进程 → 新服务绑定失败、HTTP 验证全打到**旧代码**上，表现为「修复无效」。先 `Get-NetTCPConnection -LocalPort 18765` 查占用 |
| 21 | **扩展的桌面地址是编译期常量** | `sync.ts` 硬编码 `http://127.0.0.1:18765` → 验证扩展行为时**必须**确保该端口上是本项目服务，且端口空闲（`verify_extension_version.py` 强制要求） |
| 22 | **Electron 不支持 `window.prompt`** | UI 输入一律用原生 `<dialog>`（焦点最稳）——账号中心的「移入盒子」「重命名」即因此改造 |

## 6. 数据与运行时目录

数据库与产物路径的完整说明见 [docs/SCHEMA.md](docs/SCHEMA.md) §4 与 [docs/CONFIG.md](docs/CONFIG.md) §6。要点：

- 数据目录：`%APPDATA%\AksoWorkbench`（`WORKBENCH_DATA` 可覆盖）；
- 数据库：`workbench.db`（WAL，迁移 1–13）；
- 运行时产物：`runtime/{insight,factory,browser-states,monitor,egmp-tokens}/`；
- 浏览器分区与缓存：`Partitions/`、`Cache/`、`Local Storage/` 等（删掉只清浏览器登录态，不丢账号）；
- **用户可干预的文件级开关**：`proxy.txt`（强制代理/强制直连）；
- 维护工具：`tools/db_maintenance.py --report|--backup|--trim|--vacuum|--all|--purge-legacy-settings`。

> 实测遗留体积：`doc_chunks.embedding` 是数据库文件体积主因（知识库下线后的死数据），
> 以及一份 85.7 MB 的历史备份 `workbench.backup-20260911-105548.db`。
> `--all` 可在关闭桌面端后回收（见 [ADR-0001](docs/adr/0001-sqlite-over-mysql-redis.md)）。

## 7. 验收基线

### 7.1 每轮必跑（秒级至分钟级）

| 命令 | 当前结果 |
|---|---|
| `.\.venv\Scripts\python.exe -m pytest` | **64 passed**（对话/设置 7、账号 7、更新 14、原生链路 12、自动登录 4、浏览器并行 4、浏览器状态 5、模块 4、proc 7） |
| `.\.venv\Scripts\uvx.exe ruff check .` | ⚠️ **9 条违规**（见 §4.4 B14）——lint 门禁暂不成立 |
| `.\.venv\Scripts\python.exe tools\verify_packaging.py` | **8/8 PASS** |

### 7.2 改扩展后必跑

| 命令 | 基线 |
|---|---|
| `tools/verify_extension_boot.py` | 7/7（SW 启动未崩 / 命令仅 `quick-wheel` / 无 `ql:recentPages` / 无 `ql:pageNames` / `akso:acctMap` 同步成功） |
| `tools/verify_extension_isolation.py` | 8/8（Cookie 袋隔离 + 多 iframe 自动填表，含 0.2.23 两个缺陷回归点） |
| `node tools/verify_host_logic.mjs` | 22/22 |
| `npm run typecheck`（扩展目录） | 零错误 |

### 7.3 改 `/extension/*` 蓝图后必跑

| 命令 | 基线 |
|---|---|
| `node tools/verify_extension_sync.mjs` | SIMULATION_OK / SEQ_PERSIST_OK / SYNCDISABLED_OK（**该蓝图无 pytest 覆盖**） |
| `tools/verify_command_latency.py` | A/B 钉契约（删 `notify_all`/`wait` 立刻红） |

### 7.4 改更新链路后必跑

| 命令 | 基线 |
|---|---|
| `node tools/verify_updater_logic.mjs` | 47 断言（纯 node 桩 electron） |
| `node tools/verify_update_proxy.js` | 判定以「策略生效后必须拿到 `latest.yml`」为准 |
| `tools/verify_update_flow.py` | 离线整链（`AKSO_UPDATE_OVERRIDE` 指向本地静态目录） |

### 7.5 真机验收记录（历史通过项）

- 原生链路：`login` / `understand`（1.4 MB 模型）/ `spider`（networkx 图分析）；
- 双账号并行 headful 2/2 在线、免密直达（引擎 phase=idle）、自愈 `heal_count=1`；
- Monitor 真机 **248 请求捕获**（dropped 238 / trimmed 2 / full 8）；
- Electron 壳 CDP 挂接开户成功；
- 扩展 E2E_PASS：数据面 2/2 同步、`par.open` 1s 内开登录页、自动登录离开 `/login` 进 `/web`；
- 打包态冒烟：`AksoServer.exe` 起服并记 `frozen=True`，18765/18766/18767 就位，`/extension/health` connected=true；
- 账号中心 UI 检查 11/11（CSS 生效 / 统计 / 卡片结构 / 四态徽标 / 批量条 / 零 JS 错误）。

### 7.6 待人工复验（未完成项）

- 用户真实 Chrome 装载 `dist` → 桌面 `Alt+Q` 轮盘选人 → 真实 profile 自动登录（自动化侧已 E2E_PASS）；
- egmp writers 的**真机首跑**（`create` 写配置需测试环境授权；Monitor 侧已真机验收）；
- NSIS 静默安装的 UAC 落盘验证；
- 「关主窗后会话常驻」——若需要，服务与壳要解耦为独立进程（当前关窗即整体退出）。

## 8. 技术栈与依赖纪律

- **Python 3.12+**（`requires-python = ">=3.12"`）；uv 管理，`uv.lock` 随仓库分发；
  `pyproject.toml` 已配清华镜像为默认 index。
- **两个 extra 都要装**：`uv sync --extra dev --extra build`
  （`dev` = pytest；`build` = PyInstaller。**省掉 `build` 会让构建第 5 步失败**）。
- 依赖变更流程见 [CONTRIBUTING.md](CONTRIBUTING.md) §2（lockfile 变更单独成提交）。
- **本机实测环境差异**：`.venv` 是由 conda 的 Python **3.14.6** 创建（`pyvenv.cfg: home = D:\software\conda`），
  且 `uv` / `ruff` **不在 PATH**（可用的是 `.venv\Scripts\uv.exe` / `.venv\Scripts\uvx.exe`）。
  文档里的 `uv run` / `uvx` 在干净机器上成立；本机请用 `.venv\Scripts\` 下的副本。
- Node 只在**构建桌面壳与扩展**时需要（终端用户不需要）。

## 9. 文档体系（本轮重构后的分工）

| 文档 | 变更频率 | 职责 |
|---|---|---|
| [README.md](README.md) | 低 | 产品定位 + 用户向快速开始 + 文档地图 |
| [docs/USER-MANUAL.md](docs/USER-MANUAL.md) | 低 | 功能详解、操作步骤、FAQ、数据与安全须知 |
| [docs/API.md](docs/API.md) | **高（改接口必更）** | 逐端点权威契约 + 四类特殊通道专章 |
| [docs/CONFIG.md](docs/CONFIG.md) | **高（改配置必更）** | 五层配置全覆盖（含「改了会怎样」） |
| [docs/SCHEMA.md](docs/SCHEMA.md) | 中（改表必更） | 迁移账、表结构、跨进程数据契约、改表清单 |
| [docs/EXTENSION-PLANE.md](docs/EXTENSION-PLANE.md) | **高（改扩展必更）** | 六平面隔离、桌面↔扩展协议与护栏、私有改造史 |
| [docs/adr/](docs/adr/) | 低（决策变更时**新增**一篇） | 背景 / 决策 / 取舍 / 后果 / 复核触发器 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 低 | 环境、依赖、构建发布、代码规约、验收纪律 |
| **本文档 AGENT.md** | 中 | 接手路径、红线、已知问题、环境坑、验收基线 |
| [CHANGELOG.md](CHANGELOG.md) | **每次发布** | 版本变更明细（含每次事故的根因） |
| [docs/archive/](docs/archive/README.md) | 冻结 | 已失效文档，**勿作现状依据** |

**维护原则**：内容按**变更频率**分层。ADR 记不再变的东西；API/CONFIG/SCHEMA 记随代码走的事实；
AGENT.md 记「踩过的坑」；archive 只增不改。
