# Changelog

本项目遵循语义化版本（SemVer），格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。
从第一天开始记录（对齐行业月更节奏惯例）。

<!--
  ⚠ 下面这一行 `## [Unreleased]` 是 tools/publish_release.py 的**机器接口**，不是随手写的标题：

  `notes_from_changelog()`（该文件第 224 行）是**逐字**查找 `## [Unreleased]`，找到之后
  从下一行开始收集 `- `/`### ` 开头的块，**遇到下一个 `## ` 就停**。
  本仓正文一直写中文「未发布」，于是 2026-09-17 首次用它发版时抽到的是**空字符串**——
  而它是静默 `return ""`、**不报错**，release notes 就那样空着发出去了。

  因此这里**只能有一行标题**，且必须是英文 `## [Unreleased]`：
  它下面直接就是内容（任何插在中间的注释/中文标题都会被当成终止符，抽取结果又变回空）。
  标题的中文说明放在本注释里，就是你现在读的这段。

  发版时把该行改成 `## [<版本号>] - <日期>`，并在文件顶部补一行新的 `## [Unreleased]`。
-->

## [Unreleased]

### Added

- **MCP Streamable HTTP + 行业命名（2026-10-23）**：`mcp.json` 支持
  `transport: "streamable-http"` + `url` / `headers`；响应兼容 `application/json` 与请求级
  `text/event-stream`，使用 `Mcp-Session-Id`；工具名改为 `mcp__<server>__<tool>`；
  server `instructions` 以「外部说明」追加系统提示词（≤8k）。`tests/test_mcp_http.py`（7 条）。

### Added

- **Agent Skills 文件夹运行时（2026-10-23）**：支持标准 `SKILL.md` 文件夹发现
  （`WORKBENCH_SKILLS_DIRS` / `.agents/skills` / `.workbench/skills` / 数据目录 `skills/folders`），
  新增 `list_skills`（只读目录）与 `read_skill`（按需读正文）两个 READ 工具；工具面 18→20，
  文件夹 Skill **不执行代码**。ZIP 工具包继续用于需要 `wraps`/`script` 的场景。
  `tests/test_skill_folders.py`（6 条）；eval 夹具已按新工具面重录。

### Added

- **Windows read-only 真限制（2026-10-23）**：`strict-readonly` 在 `CREATE_SUSPENDED`
  阶段附加 `WRITE_RESTRICTED` 受限令牌（logon + Everyone），写工作区被运行时拒绝；
  附加失败终止并 fail-closed。能力矩阵新增 `read_only_token` / `workspace_write_token`；
  `tests/test_sandbox.py` 增加 slow 反证。workspace-write 的 capability SID 仍未通过
  访问检查，继续保持“声明而非强制”。

### Added

- **计划模式（2026-10-23）**：对话输入区新增「计划」开关；开启后只向模型暴露 READ 工具，
  写/危险工具即使被点名也按"未执行"回填（`plan_mode`，`AgentConfig` + `/api/chat/stream`）。
  UI 入口与前后端 wiring 有静态判据；`tests/test_plan_mode.py` 2 条。

### Added

- **工作区快照 UI（2026-10-23）**：沙箱页新增「工作区快照」卡片；
  `GET/POST /api/checkpoints` + `POST /api/checkpoints/{id}/restore`，只认沙箱工作区根；
  创建/恢复走 `to_thread`，恢复前 `confirm()`。`tests/test_checkpoint_routes.py` 1 条。

### Fixed

- **测试数据隔离（2026-10-22，PITFALLS #131）**：`config.has_explicit_data_dir()` 在显式
  `WORKBENCH_DATA` / `WORKBENCH_DB` 时**不再接管遗留库**；`tests/conftest.py` 改用
  `TemporaryDirectory`，会话结束自动清理（`WORKBENCH_TEST_KEEP=1` 保留现场）。
  本机清掉历史残留 **219 个目录 / 327 MB**。判据：`pytest tests/test_data_isolation.py`。
- **沙箱 Job 绑定竞态（2026-10-22，PITFALLS #132）**：Windows 改为
  `CREATE_SUSPENDED` → 绑定 Job Object → `NtResumeProcess`；绑定后枚举并补绑已存在的后代、
  `IsProcessInJob` 校验；`terminate()` 用 `taskkill /T /F` + `TerminateJobObject`；
  `_run` 的启动段包 `try/finally`；新增 `sandbox.capability_matrix()` 并接入
  `/api/sandbox/status`。判据：`pytest tests/test_sandbox.py -m slow`。

- **错误身份（2026-10-22，PITFALLS #133）**：`VaultError.kind`（auth/network/format/validation/upstream）、`McpError.code`、`queries` 只吞 401/403/404 与 `ApiError`；删除 `"401" in str(exc)` 与 `"-32601" in text`。新增 `tests/test_error_identity.py`（8 条，含"消息带 401 但 kind=upstream 必须映射 502"的反证）。
- **I11 规则 T（2026-10-22）**：`async def` 里直接调 `services|skills|sandbox` 的同步 IO 入口（`upload` / `start_job` / `run_job`）会被结构化扫描拦下；`routes_skills.upload` 与 `routes_insight.start_job` 已挪 `asyncio.to_thread`，并加"上传 0.4s 期间事件循环仍在转"的效果判据。
- **有界分页/总超时/响应体上限（2026-10-22）**：`paged_post` 上限 20 页（恒 hasNext 的桩曾打出 501 页）；`guarded_get` 的 `timeout` 改为**整次调用总预算**（含重定向），响应体默认上限 5 MB 并带 `x-workbench-truncated` 标记。
- **权限策略 v1（2026-10-22）**：`workbench/permissions.py`，`deny > ask > allow`、`ask`/`auto` 模式、`tool(subject)` 规则；`ToolHub` 注入权限门，`deny` 硬阻断、`ask` 对只读工具复用一次性令牌门、无确认门时 Fail-Closed；`GET/PUT /api/settings/permissions`。

- **能力诊断（2026-10-22）**：新增 `/api/diagnostics` 与 `tools/doctor.py`；稳定 issue code（`mcp.config_invalid` / `permissions.invalid_config` / `sandbox.job_unavailable` 等），默认静态、`live` 只读内存注册表，路径脱敏为 `<data>/…` / `<repo>/…`。
- **结构门禁（2026-10-22）**：`tools/repolint.py` —— 800 行文件预算棘轮（基线只降不升）、error-text 规则、前端覆盖登记（`tools/frontend_parity.json`；新端点未接 UI 且未登记即红）。
- **MCP 状态投影（2026-10-22）**：`/api/mcp/servers` 只输出白名单字段，`env`/`headers`/未知字段永不进响应；错误文本限长 200。
- **Cache guard（2026-10-22）**：新增 `tests/test_cache_guard.py`，断言 system + tools schema逐字节稳定、参数 schema 无易变字段。

- **Wire parity（2026-10-22）**：`tests/test_wire_parity.py` + `tools/wire_contract.json` —— `api.js` 的每个路径必须命中后端路由；7 个关键端点的 required 字段必须存在，consumed 字段必须在前端源码里出现。
- **工作区快照接缝（2026-10-22）**：`workbench/checkpoints.py`（capture/restore/list，文件数/字节上限、路径逃逸拒绝）；`sandbox.run` 在 `WORKBENCH_SANDBOX_CHECKPOINTS=1` 且策略可写时创建工作区快照，默认关闭。`tests/test_checkpoints.py`（5 条，含沙箱集成）。

- **长期记忆 v1（2026-10-22，P3）**：迁移 26 新增 `agent_memory`（scope/type/revision/expires_at）；`workbench/memory.py` 提供 add/get/list/search/delete（中文单字/双字 + 英文词元重叠排序，过期不参与自动检索）；`GET/POST/DELETE /api/memory`；新增 `memory_search`（read）/ `memory_add` / `memory_delete`（write，过确认门）三个工具，core 工具数 15 → 18、插件 4 → 5。**不自动注入**（不改 system 前缀）。
- **Eval fixtures 重录（2026-10-22）**：工具面新增 memory 三件后请求指纹变化，10 份夹具按 `tools/run_evals.py --record` 重录；`--offline` 全过。

- **死代码清理（2026-10-22，P3）**：删除从未被加载的 `static/css/cloud.css`（21 KB / 1,077 行）、零引用的 `account-dock.js::currentSelection` 与 `modules-common.js::logLine`；新增 `tests/test_static_assets.py`（孤儿 CSS / 指不到的静态资源 / 重复 script 三条）。

- **对象分析覆盖度自证修正（2026-10-22，PITFALLS #134）**：`run_spider` 的 `succeeded` 从 `len(codes)` 改为实际 `len(spider["objects"])`；全部读失败时 coverage=degraded、ok=False，不再把计划数当成功数。`tests/test_insight_coverage.py`（2 条，含部分失败/全失败反证）。

- **ACP v1 适配器（2026-10-22，P3）**：`python -m workbench.acp` 提供 stdio NDJSON JSON-RPC；支持 `initialize` / `session/new` / `session/prompt` / `session/cancel`，流式 `agent_message_chunk`；写工具因 `approval_wait_s=0` 快速失败（无权限卡片，不挂客户端）；见 `docs/ACP.md`。
- **沙箱 runs/ 保留策略（2026-10-22，P3 工程债）**：`layout.cleanup_runs()` + `tools/cleanup_runs.py` —— 按天数/数量/体积从最旧开始清理，默认 dry-run、`--yes` 才删；不碰 `staged/` 与非 run 目录。`tests/test_cleanup_runs.py`（5 条）。

### Removed

- **云端账号库独立页面（2026-10-20）**：删除 `static/pages/cloud.html`、
  `static/js/cloud.js`、`tests/test_cloud_page.py`、`tools/verify_cloud_ui.py`；
  用户选择只保留账号中心，云端设备流登录/身份胶囊仍在账号中心右上角，`/api/vault` 后端保留。
- **Electron 退役（2026-10-19）**：删除 `desktop/`（主进程、端口 broker、代理、更新器、
  electron-builder 配置与旧产物）、`tests/test_desktop_launcher.py` /
  `test_ports_contract.py` / `test_shell_control.py` / `test_update.py`、
  `tools/build.ps1` / `publish_release.py` / `verify_update_flow.py` /
  `verify_updater_logic.mjs` / `verify_update_proxy.js` / `verify_port_broker.mjs` /
  `verify_wheel_page.py`，以及 Python 的 `routes_update` / `routes_shell`。
  Tauri 2 + Python sidecar 成为唯一桌面分发路径。
- **Electron 专属配置面**：`WORKBENCH_CONTROL_PORT`、`desktop-ports.json`、
  `shell-state.json`、`/api/update`、`/api/shell/*`、固定 18767 控制服务、
  electron-updater 的 `latest.yml`/`.updaterId` 不再产生或读取。
- **版本副本**：`desktop/package.json` / `desktop/package-lock.json` 从 `bump.py`
  的同步与检查中删除；Tauri 的 `tauri.conf.json` / `Cargo.toml` 成为必备副本。

### Changed

- **侧边导航栏 v2（2026-10-20）**：宽度 **268 → 180px**（削减约三分之一）；左上角
  隐藏/显示按钮，折叠状态存 `localStorage` 并在页面间保持；账号中心/对象分析/
  Skill/沙箱改为共享 `sidebar.css` + `sidebar.js` 注入侧栏，点击导航不丢侧栏；
  **Skill 管理 / 沙箱两个 Agent 菜单从侧栏隐藏**。
- **账号中心改为云端数据源（2026-10-20）**：移除本地站点/账号/盒子/分配池/备份管理；
  未登录时整页遮罩并提示登录；登录后读取 `/api/vault/library` 展示云端账号/站点/盒子。
  新增「本地推到云端」：按 akso-cloud merge 契约带 `baseRevision` 推送，409 提示先刷新。
- **启动只保留一段加载动画（2026-10-20）**：Tauri 窗口带 `?shell=tauri`，Web 端
  `app.js` 跳过自己的 splash；只保留 Tauri 启动页。窗口默认占工作区 80% 并居中。
- **启动窗口默认居中、占工作区 80%（2026-10-20）**：Tauri 按主显示器工作区计算
  `80% × 80%` 并居中，不再固定 `1440×920` 或最大化；1440×900 工作区下窗口约
  `1152×720`。
- **Tauri 启动不再弹浏览器（2026-10-20）**：`on_navigation` 现在把 Windows 的
  `http://tauri.localhost/` 也视为本地 URL；此前它被当外部链接交给 Edge，
  必须先关网页才能看到主窗（PITFALLS #129）。
- 账号中心版本角标改为只读 `/api/health` 的版本号；不再提供“检查更新”点击入口
  （Tauri updater 的检查/安装 UI 尚未接入）。
- 打包静态核对（`tools/verify_packaging.py`）改为检查 Tauri 资源映射、构建脚本 BOM、
  Tauri UI/图标与插件 `hiddenimports`；**7 项**。
- `README` / `USER-MANUAL` / `CONFIG` / `API` / `CONTRIBUTING` / `AGENTS` 同步为
  Tauri 动态端口、内部 HTTPS 静态源、手动覆盖安装的当前口径。

### Added

- **Tauri 2 壳 + Python sidecar（2026-10-19，Phase 1 骨架）**：新增 `src-tauri/`
  （Tauri 2、single-instance、updater、opener），Rust 侧负责选空闲端口、拉起
  `WorkbenchServer`、健康检查、splash→主窗、外链交系统浏览器、退出按进程树清理；
  sidecar 下发 `WORKBENCH_PARENT_PID`，父进程死亡时由 Python `lifecycle` 兜底。
  已在本机完成 release 构建：sidecar **60.7 MB**、NSIS 安装包 **26.95 MiB**、
  updater `.sig` 已生成；构建脚本 `tools/build_tauri.ps1`，更新清单生成器
  `tools/tauri_update_manifest.py`，静态契约测试 `tests/test_tauri_config.py` /
  `tests/test_tauri_update_manifest.py`。
- **版本真源 `VERSION`（2026-10-19）**：根目录新增 `VERSION`，`tools/bump.py` 改为
  **读 `VERSION`、写全部派生副本**（`pyproject.toml` / `workbench/__init__.py` /
  `desktop/package.json` / `desktop/package-lock.json` / `uv.lock` / `.version.json`；
  未来 Tauri 工程存在时还会写 `src-tauri/tauri.conf.json` 与 `Cargo.toml`）。
  CI 在 `uv sync` 之前跑 `bump.py check`；新增 `tests/test_version_contract.py`（11 条）。
- **桌面壳启动器（2026-10-19）**：`npm start` 改走 `desktop/launch.js`，在子进程环境里
  删除 `ELECTRON_RUN_AS_NODE`。此前外层 Electron/IDE/Agent 宿主注入该变量时，
  `electron .` 会以纯 Node 运行，`require('electron').app` 为 `undefined`，报错落在
  `app.whenReady()`（看起来像 main.js 的 bug）。`main.js` 同时加守卫，给出可操作错误。
- **sidecar 兜底日志加固（2026-10-19）**：`sidecar-stdio-<日期>.log` 打开前超过 16 MiB
  先轮转一份 `.1`；写入流监听 `error` 并降级到 console，避免磁盘异常把 Electron 主进程
  带崩；管道仍持续消费，不阻塞子进程。
- **云端账号库页面（2026-10-19）**：新增「云端账号库」页（`pages/cloud.html` +
  `js/cloud.js` + `css/cloud.css`），按 akso-cloud / akso-pass 的视觉口径展示站点 / 账号 /
  盒子；设备流登录、盒子芯片筛选、搜索、站点/盒子台账、账号详情、同步数据与导出视图。
  后端新增 `GET /api/vault/library`：**只读**快照 + 脱敏（剔除 `passwordEnc` / `fernetKey`）
  + 5 分钟缓存 + 断网回落（`stale=true`）；401 清会话并回登录页。新增
  `tools/verify_cloud_ui.py`（14/14）与 `tests/test_cloud_page.py`。
- **沙箱工作区与权限（2026-10-19）**：工作区根可配，新增只读/读写、联网、派生进程三项权限
  预检；策略新增 `writes` 声明（`strict-readonly` 只读、`skill-script`/`mcp-stdio` 会写）。
  `cwd` 必须在工作区根内；只读模式拒绝写策略；配置不可用时 Fail-Closed。新增
  `GET/PUT /api/sandbox/workspace` 与 `POST /api/sandbox/workspace/reset`，沙箱页新增
  「工作区与权限」。**诚实边界**：不是内核级只读。
- **MCP 稳定性（2026-10-19）**：30 秒健康巡检（`ping`）、进程退出 / ping 失败 /
  `notifications/tools/list_changed` 自动重连（指数退避，上限 5 分钟）、单 server 手动重连
  `POST /api/mcp/servers/{name}/reconnect`、按工具 `risk` 覆盖、状态显示协议/版本/工具名；
  测试 13 → 22 条。
- **产物面板（2026-10-19）**：对象分析产物由表格改为卡片墙 + 类型筛选 + 搜索；预览新增
  CSV 表格、图片、复制正文、新窗口打开；`tools/verify_insight_preview.py` 更新到 18/18，
  另加 Node 直跑 CSV 解析判据。
- **MCP（Model Context Protocol）最小可用接入（2026-10-19）**：用户级 `mcp.json`
  （与 WorkBuddy / Claude Desktop 同形）、stdio JSON-RPC、`initialize` / `tools/list` /
  `tools/call`；工具在运行期进入 `ToolHub` 的 installed 层，默认
  `risk=write` / `trust=UNTRUSTED` / `reversibility=IRREVERSIBLE`；设置页「MCP 服务器」
  显示状态并可重新连接；新增 `GET /api/mcp/servers`、`POST /api/mcp/reload`、
  `docs/MCP.md` 与 13 条测试（假 server，不联网）。自动重连 / 健康检查 / 按工具 risk
  在同日「MCP 稳定性」条目里补齐；仍**不做**远程传输、resources/prompts/sampling
  （见 `docs/MCP.md` §8）。
- **前端契约判据（2026-10-19）**：`tests/test_frontend_contract.py` 用 Node 直接 import
  真实的 `static/js/api.js`，断言**发出去的请求体**（不需要浏览器，0.35 秒）。
- **路由装配判据（2026-10-19）**：`tests/test_api_routers.py` 覆盖"单域失败不拖垮应用"，
  并断言动态导入的 13 个路由模块都进了 `server.spec` 的 hiddenimports。
- **对象分析 SSE 终态判据（2026-10-19）**：`tests/test_insight_sse.py` 覆盖正常结束 /
  客户端断开 / worker 失败三个出口。
- **云端账号库测试（2026-10-19）**：`tests/test_vault.py`（7 条）——会话密文落库、
  `status` 不打网络、退出清本地、路由 502/401 映射。
- **对标文档（2026-10-19）**：`docs/AGENT-BENCHMARK.md`（WorkBuddy 基础五件套矩阵 +
  仍存在的缺陷与优先级）。

### Fixed

- **停止键实际不可用（2026-10-19）**：`static/js/api.js` 的 `jsonFetch` 现在统一序列化
  对象 body。此前 `stopChat` 实发 `"[object Object]"`，服务端 422，前端把异常吞成
  "按钮退回停止"——用户点了没反应。判据见 `tests/test_frontend_contract.py`（PITFALLS #122）。
- **对象分析任务永远 `running`（2026-10-19）**：`routes_insight.run_sse` 的终态写入移入
  `finally`；客户端断开且 worker 未结束时用 `add_done_callback` 收尾。判据见
  `tests/test_insight_sse.py`（PITFALLS #123）。
- **确认门未装配时 fail-open（2026-10-19）**：`ToolHub.execute` 对 `risk != READ` 且
  `_approval_gate is None` 的调用返回 `REJECTED`，handler **不执行**；补两条判据
  （`test_write_tool_is_refused_when_approval_gate_is_not_installed` 与只读对照）。
- **可选路由注释承诺的容错没有实现（2026-10-19）**：`_optional_routers` 改为逐个 import /
  注册、单域失败只记 WARNING；`server.spec` 补 13 个 hiddenimports。
- **云端账号库默认地址仍是 `:8443`（2026-10-19）**：改为 443（`AKSO_VAULT_URL` 仍可覆盖）。
- **锁文件漂移（2026-10-19）**：`uv.lock` 与 `desktop/package-lock.json` 从 0.3.13 同步到
  0.4.1；`bump.py` 从四写扩为**六写**并纳入 `check`；CI 在 `uv sync` **之前**跑
  `uv lock --check`（PITFALLS #124）。
- **文档事实漂移（2026-10-19）**：`CONTRIBUTING` 测试基线、`VERSIONING` 的 tag/Release 数字、
  `` 的扩展发现状态、README 的"云端账号数据接管"表述，按当日实测校正。

### Changed

- **测试提速（2026-10-19）**：快层 **66.9s → 25.8s**。I11 的 AST 扫描从 O(n²) 降为
  O(n)（9.7s → 0.8s）；账号隔离用例的 DNS 等待改为注入失败（5.2s → 0.2s）；
  自动登录验收（真实 2.5s/4s 时间轴）归入 `slow` 层，CI 仍显式跑 `-m slow`。
- **文档合并精简（2026-10-19）**：`docs/VERSIONING.md` 正文并入
  `CONTRIBUTING.md` §3.6（文件只留指针）；`docs/CODEBASE_OVERVIEW.md` 从 498 行重写为
  约 150 行的代码地图 + 风险索引；`docs/USER-MANUAL.md` 删除已失效的扩展/轮盘章节。
- **工具面文档（2026-10-19）**：`docs/TOOLS.md` 增加 installed 层（Skill / MCP）说明；
  README 的文档地图删掉 5 行"已删除文件"的条目。

### Removed

- **内置浏览器 / Monitor / 自动登录整体下线（2026-10-19，用户确认未来不再需要 UI 自动登录 /
  页面抓取 / 截图）**。删除面：
  - `services/browser_pool.py`、`services/autologin.py`、`api/routes_browser.py`、
    `api/routes_monitor.py`、`services/egmp/monitor/**`；
  - 工具 `accounts_browser_open` / `accounts_browser_sessions` / `accounts_browser_close`；
    产品工具面 **18 → 15**（15 read / 0 write）；
  - CDP 18766、`WORKBENCH_CDP_PORT`、`WORKBENCH_SESSION_MODE`、`/windows*` 控制端点、
    `routes_shell.py` 的 `/launch-chrome`；
  - 账号中心的会话徽标 / 打开 / 聚焦 / 关闭 / 监听按钮；`runtime/browser-states/` 与
    `runtime/monitor/` 不再产生（旧残留可手工删除）；
  - 打包链路的 Playwright / Chromium / `ms-playwright` 收集；`verify_packaging.py` 的
    P8 / P8b（**9 项 → 7 项**）；Playwright 仅保留在 dev 依赖，供 Web UI E2E 使用；
  - `tests/test_browser_parallel.py` / `test_browser_state.py` / `test_autologin.py`；
    `tools/verify_confirmation.py`（产品暂无 WRITE 工具，端到端确认门没有真实目标）与
    `tools/diag_electron_typing.py`（依赖已删除的 CDP 18766）。
- **浏览器扩展整体移出本仓库（2026-10-09，用户决策：桌面端与扩展不耦合，本仓只需账号库管理页
  来承接云端的数据环境）**。删除面：
  - 源码树 `extensions/quick-login/`（42 文件 / 约 324 KB，含 manifest、TS 源、构建脚本）；
  - `/extension/*` 整段蓝图（`api/routes_extension.py`）与访问令牌中间件
    （`api/extension_auth.py`，P0-1 的 `X-Workbench-Token` + 24h grace 窗口）；
  - 桌面壳的扩展协作：`extensionDir()` / `findOpenExtensionSetup()` / 托盘「安装浏览器扩展…」、
    控制服务的 `/extension-setup`、透明**轮盘窗**与全局热键 `Alt+Q`；
  - 账号中心的扩展 UI：卡片「快捷登录」按钮、四态徽标、两条提示条（未安装 / 版本过旧）、
    以及 `pages/wheel-picker.html` 与 `static/css/wheel.css`；
  - 打包链路：`desktop/package.json` 的 `extraResources` 第二项、`build.ps1` 的扩展重建步骤
    （8 步 → 7 步）、`bump.py` 的第五/六写（2026-10-19 起为**六写**）、`verify_packaging.py` 的
    P4a/P4b/P4c/P10（**13 项 → 9 项**）、CI 的 `npm ci && npm run build` 步骤；
  - 验证工具：`verify_extension_sync.mjs` / `_boot.py` / `_isolation.py` / `_version.py` /
    `acceptance_extension_e2e.py` / `verify_command_latency.py` / `verify_host_logic.mjs` /
    `diag_fernet_node.mjs` / `make_app_icon.py`，以及 `tests/test_extension_auth.py` /
    `tests/test_extension_blueprint.py`。
- **随之删除的能力**：在**用户自己的 Chrome** 里多账号并行（六平面隔离、账号轮盘、免密复制语义）。
  该能力改由独立仓库 [`jizi-dragon/quick-login`](https://github.com/jizi-dragon/quick-login) 承担。
  **托管浏览器（内置 Chromium，`browser_pool`）成为本仓唯一的浏览器路径**，
  账号中心保留**账号库 CRUD + 盒子/环境/备份导入导出 + 云端账号库登录（设备流）**。

### Changed

- `routes_shell.py` 只剩 `/launch-chrome` 与 `/update-state`（`setup-helper` 与它用的
  `control_base` 一并删除）；`workbench/api/__init__.py` 不再装 `ExtensionAuthMiddleware`。
- `/api/health` 成为**唯一**的健康端点（原 `/extension/health` 的 `everConnected` 闩锁与
  `extStale` 判定随之消失）。
- `tests/test_ports_contract.py` 的"探测端点无副作用"改为直接断言 settings 表不变
  （不再依赖被删模块的私有键名）。测试基线：**1016 → 1005 项**（删两类扩展专属用例；2026-10-19 起含本轮新增 32 条，含 slow）。
- **仓库更名为 `akso-workbench`（2026-10-11，用户决策）**：`jizi-dragon/akso-mogul` →
  `jizi-dragon/akso-workbench`。随更名同步的**外部标识**：`desktop/package.json` 的
  `build.publish.repo`（`owner` 仍 `jizi-dragon`）、`desktop/proxy.js` 的直连探测 `PROBE_URL`、
  `tools/verify_update_proxy.js` 的 Release 资产 URL、`tools/publish_release.py` 的 `REPO`
  常量与 `GH_TOKEN` 提示文案、`README.md` 的「仓库地址」行、`CONTRIBUTING.md` §3.6 与
  `CONTRIBUTING.md` 里可见性/规则集的实测 URL。
  ★ **产品展示名与全部产物名一个字未动**（`Workbench-x.y.z-setup.exe` / `dist/WorkbenchServer` /
  `%APPDATA%\Workbench` / `Workbench/` 数据目录本来已是新形态）。
  ★ **保护清单一字未改**： 的决策文字（D-4 只**追加**了一段
  「2026-10-11 后补」说明）、本文件的历史条目、`docs/PITFALLS.md` 既有条目、
  `adapters/*.json` 指向的四个上游项目目录。
  ★ `git remote` **未改**：GitHub 侧改名后再执行 `git remote set-url`（见本条的更名说明）。

> 仓库更名的**决策留痕**在 §2.4 D-4 的
> 「2026-10-11 后补」—— 原「暂不改」的决策文字**原样保留**，两段认知都能看见。

> ⚠️ 本次同时**修正了一处自己的失误**：用脚本批量重排 `build.ps1` 时把它的 **UTF-8 BOM 吃掉了**，
> `verify_packaging.py` 的 P5 当场报红（8/9）。已补回并复跑 9/9 —— 这正是 P5 存在的理由，
> 判据见 `AGENTS.md` §3 的 `tools/build.ps1` 一行。

> 新条目写在这里（`tools/publish_release.py` 逐字认这一行标题，说明与约束见文件顶部注释）。
>
> ★ **2026-10-19 已清理**：本段里描述已删除能力（配置工厂 / `factory_*` / `` / ``）
> 的条目已不在。发版前仍建议跑一次
> `grep -n '配置工厂\|factory\|FACTORY' CHANGELOG.md`，取行号落在本段内的那些确认。
> 原因：`notes_from_changelog()` **逐字**把 `- ` 与 `### ` 开头的块抽成发布说明，
> **不清理就会把已删功能当"新增"对外发布，且不报错**。
> （本警示写成引用块是刻意的：`notes_from_changelog()` 只收 `- `/`### ` 开头的行。）

## [0.4.1] - 2026-09-29

### Fixed

- **全面审计（三路并行 + 逐条复核）：修掉一处安全漏网、一处静默失效的门禁、一处
  被 `.gitignore` 掩盖的残留、以及若干假注解与空操作抑制指令**（2026-09-29）。

  **① 安全：真实部署域名漏网** —— `services/egmp/kernel/urls.py` 的 docstring 里写着
  `https://tonbridge-config.aksoegmp.com/login`。本仓做过 `filter-repo` 全历史脱敏，规则明写
  「真实部署域名 → `*.example.com`、客户租户名 → `example-config`」，而 `egmp/client.py` 用的
  正是占位值 ⇒ **这一处是漏网**。已改为占位值；全仓该域名已清零。

  **② 门禁静默失效** —— `tools/gen_tools_doc.py` 的 `_claim_problems()` 对
  `CLAIM_DOCS` 里不存在的路径是 `continue`（**静默跳过**）。2026-09-29 的文档瘦身删掉了
  其中两个目标 ⇒ 那两块"N 个工具/插件"核对**从此不再执行，而门禁照报 PASS**。
  已改为报出一条 problem，并**做了反证**（注入一条不存在的路径 ⇒ 退出码 1 且打印正确消息；
  还原后逐字节相等）。这是 PITFALLS #108 那一族："一个**自己坏掉的**检查会**静默地什么都不查**"。

  **③ `.gitignore` 掩盖了 28 个孤儿文件** —— `factory-tuning/data/` 下
  `factory-runs/` / `layout-snapshots/` / `where/` 共 28 个文件一直在工作区里
  （含产出它的 `services/egmp/checkpoint.py` **早已删除**的 `checkpoint.json`）。
  它们被 `.gitignore` 忽略 ⇒ **`git status` 是干净的**，所以从没人发现。
  三方各说一套：README 说"删了"、`.gitignore` 说"重跑即得"、工作区实际还在。
  ★ 而"重跑即得"这条理由**已失效**——生成它们的工具链随配置工厂一并删除了。
  已删除三类残留，并订正 README 与 `.gitignore` 的口径。

  **④ 11 处空操作的 `# noqa: E402`** —— `pyproject.toml` 的 `per-file-ignores`
  已豁免整个 `tools/` 目录。**反证**：`ruff check tools --ignore-noqa --select E402`
  → `All checks passed`（把 noqa 全关掉也报不出 E402）⇒ 这些抑制指令什么都没抑制。
  其中 `gen_tools_doc.py` 里写的是 **`# noqa: E40202`**（多一位数字，该码不存在）——
  **它因为从不承重，所以从未被发现**。

  **⑤ 假注解与不可达兜底**（`skills/library.py`）：
  `tools: list[str] = None` + `# type: ignore[assignment]`（**注解是假的**，靠 `to_dict()`
  的 `or []` 兜着）→ 改 `list[str] | None`；两处
  `args.model_dump(...) if hasattr(args, "model_dump") else dict(args)` → 直接调用
  （`ToolSpec.valid()` 在**装配期**强制 `args_model` 是 `BaseModel` 子类 ⇒ `hasattr` 恒真、
  `else` 分支**不可达**；对自己边界内的对象做防御性 `hasattr` 会掩盖真正的契约违约）。

  **⑥ 修一个被这次模块化弄坏的脚本** —— `extensions/quick-login/scripts/probe-page.mjs`
  的 `ROOT` 仍按旧位置（`tools/e2e/`）计算（用了两级 `..`）⇒ 搬到 `scripts/` 后算出
  `extensions/dist`（不存在），**脚本当场抛错**。

  **清理**：`tmp/` **36 项 / 43.5 MB**（其中 36 MB 是 `gws-v1.0.0` —— 一份下载后
  从未被任何代码引用的第三方 CLI 多平台发行包）。删前扫过凭据：**0 处真凭据**。

  **★ 一条被撤回的清理建议（值得留痕）**：审计报「整个扩展 `site-auth` 子系统已死
  （9 个符号在打包产物里 0 命中）」。判据本身成立，但**结论错了** ——
  `extensions/quick-login/CHANGELOG.md` 写着它是 **0.2.21 定稿保留的「休眠源码」**，

  且 `parallel-session.ts` 确实在 `import type { Scheme } from './site-auth'`。
  ⇒ "打包后 0 命中"是**保留决策的预期结果，不是缺陷证据**。
  这正是本仓「零调用方的扩展点是刻意保留」那条纪律要防的误报。

### Removed

- **文档瘦身：删掉 234 个文件里的过程件、调研件与第三方逐字存档**（2026-09-29，用户下达
  「清理非必要文档，包括以前的设计文档和调研文档」）。`docs/` 从 **24 篇 → 7 篇**。

  **删了什么**：`docs/gaia平台技术手册/`（vendor 手册逐字存档，186 文件）、`docs/archive/`（4）、
  `docs/research/`（2）、`factory-tuning/reports/`（41）—— 以及用户同批删掉的
  `CORE-AGENT-PLAN` / `AGENT-TOOL-AUDIT` / `LOGGING-DESIGN` / `LOGGING-VERIFICATION` /
  `OPENSOURCE-LANDSCAPE` / `PLUGIN-UPSTREAM-RESEARCH` / `FRAMEWORK-INVENTORY` / `INSIGHT-SPEC` /
  `HISTORY-REWRITE-NOTICE` / `AGENT-FILE-INPUT-PLAN` / `CODEBASE_OVERVIEW` /
  `PLATFORM-OPERATIONS-LEARNED` / `FACTORY-PLATFORM-COGNITION` / `EXTENSION-PLANE` /
  `EXTENSION-INSTALL` / `PLUGIN-SDK`。

  **★ 两个文件被恢复 —— 它们不是文档，是承重的**：

  - `adapters/akso-cc.json`：**运行期数据**。`services/modules.py` 读这个目录 ⇒ 删掉后
    `/api/modules/{id}/recheck` 变 **404**、`module_status("insight")["checks"]` 变 **`[]`**，
    4 条测试红。日志原话 `WARNING workbench.modules: adapters 目录不存在`。
  - `docs/CORE-ARCHITECTURE.md`：`tests/test_invariant_coverage.py:76` **按路径读它** ⇒
    删掉后 **18 条不变量的覆盖率对账整体失效**。

  **断链处置（104 处）**：40 处来自活文件（`CHANGELOG` 16 / `AGENTS` 7 / `CONTRIBUTING` 6 /
  6 篇决策记录 11）→ 去链接化 + 行内标注删除事实（命中检查器的 `RETIRED_MARKERS` 口径）；
  其余 64 处来自已删文档彼此互引，随文件消失。**`AGENTS.md` 的路由表按"只列现存文档"重写**，
  并把 `CORE-ARCHITECTURE` 上移到第一行（它是 §5 不变量的载体）。

  **顺带**：`tools/check_doc_links.py` 的 `ARCHIVES` 删掉两条已不存在的路径
  （`docs/gaia平台技术手册`、`extensions/quick-login/docs/archive`）—— 有一条判据断言
  「`ARCHIVES` 里的路径必须真实存在」，留着它们会让门禁红，而红的原因与真正的问题对不上号。

  ⚠️ **诚实边界**：删掉的 16 篇 `docs/*.md` 的**内容没有搬走**（用户明确"都不需要"）。
  要看它们只能 `git show <本提交之前的提交>:docs/<文件名>`。

### Removed（2026-09-22 · 配置工厂）

- **「配置工厂」整体移除——平台知识留下，实现与工具链全删**（2026-09-22，用户下达
  「完全清理配置工厂的内容，清理相关逻辑和功能链路，仅保留配置工厂相关的核心知识文档」）。

  **删掉的是实现**：`plugins/factory.py`（7 个 `factory_*` 工具）、`api/routes_factory.py`
  （8 个 `/api/factory/*` 端点）、`services/egmp/writers/**`（13 模块）、
  `services/egmp/orchestrate.py`、`services/egmp/checkpoint.py`（零调用方的死代码）、
  `adapters/akso-auto.json`（模块 `factory` 随之消失，`/api/modules/factory` 与
  `/api/modules/akso-auto` 均为 404）、前端 `pages/factory.html` + `js/factory.js` +
  侧边栏入口、`tools/verify_factory_{agent,page}.py`、
  `factory-tuning/` 的采集与分析工具链（`tools/`、`analyze/`、`tests/`、`data/`）、
  以及两份当时的决策文档（配置工厂×Agent 接口、不许静默新建对象）与三份过程文档
  （FACTORY-AGENT-GAP / FACTORY-DECISION-BRIEF / FACTORY-VERIFICATION-PLAN）。

  **知识先迁出、再删源**，这是本轮的第一条纪律。迁进两份**不依赖任何产品代码**的文档：

  - `docs/PLATFORM-OPERATIONS-LEARNED.md`（**该文件已于 2026-09-29 删除**） 重写为
    **平台事实台账**：§4 逐条 `| # | 事实 | 证据形式 | 后果 |` 共 **80 行**（F1–F72 平台侧的全部
    + 新增 F73–F86），另含五层速查、通用规律、覆盖边界与复算方式。
    **`F*` 编号逐字保留**，因为存活报告里到处在引它——跳号的 6 个（F13/F14/F43/F45/F63/F68）
    在编号说明里逐条交代了去向。
  - `docs/FACTORY-PLATFORM-COGNITION.md`（**该文件已于 2026-09-29 删除**） 修剪为
    **纯手册认知**（454 → 320 行）：留手册语义、术语对照、**无撤销键资源总表**、创建顺序与
    平台自动派生、覆盖边界；删掉"工厂现状/缺口"两列、缺动词清单、代码层发现、决策卡与取证计划。
  - 原始真机取证报告原地保留：`factory-tuning/reports/`，
    其 README 改写成**方法论与口径留档**（脱敏口径、"录不到≠平台没发请求"、
    "`total:0` 是截至此刻没有"、"`timestamp` 是事件分发时刻"、"分不清窗口的实验无效"）。

  **顺带拆掉一处依赖倒灌**：`plugins/factory.py` 曾 import `api/routes_factory.py`，
  是规则 7（`plugins → plugin_api` 单向）唯一的一处违反，随删除自动消失。

  **顺带修掉 Monitor 身上一条会指向空处的功能链**：`monitor/interpreter.py` 的 `API_MAP`
  （端点 → 已删的 `writers.*` 函数）移除，`reproduce-plan.json` 改为只承载平台侧事实
  （`method`/`path`/`params` + 显式 `"executor": null`）——否则它会产出一份**指向不存在模块**的
  复现计划，一份"看起来正常、点下去才知道是坏的"产物。DANGEROUS 永不入计划的边界不变；
   的后补说明。

  **一个必须被看见的架构后果**：`factory_create` 曾是产品里**唯一的 `Risk.DANGEROUS` 工具**。
  删掉后产品**一个 dangerous 工具都没有了**——确认门的 dangerous 分支、审批弹窗的高危样式
  从此只剩测试构造的样本在走。`tests/test_app.py::test_agent_selfcheck` 现在断言
  `riskCounts["dangerous"] == 0`：将来任何"不可逆地改生产数据"的工具进来，它必须声明 DANGEROUS，
  而那条断言会在那一刻变红，提醒作者回来读注释而不是把 0 改大。
  同理，`ToolSpec.summary_fn` 与 `ToolSpec.precheck` 两个扩展点也失去了它们唯一的产品内使用者
  （机制都保留，由 `tests/test_plugin_api.py` 的**合成 spec** 钉住——零调用方的扩展点是
  下一次重构里最容易被无声改坏的东西）。

  **PITFALLS 一行没删**：它自己的 §维护规则 第 1 条写着"不要删行"，并带一条机检判据
  （编号不许有缺号），而文件里正好记着一次删行事故（#44 被误删后恢复）。因此按它**自己规定的
  机制**处理：行全部保留，在文件头加一份对照表——A 组 11 条（#35/#60/#61/#65/#66/#72/#75/#77/#79/#83）
  的**主体已不在仓库中，判据命令失效，按结论读**；B 组其余条目内容**仍然成立**
  （平台行为、Monitor 与 browser_pool 的坑、以及 #59/#84/#85 那三条已经升格为活的核心机制）。

  **顺带**：`docs/TOOLS.md` 的清单块重生成（**4 个插件 / 18 个工具** = 16 read + 2 write + 0 dangerous，
  工具 schema 预算从 26 下调到 18——留一个虚高的上限等于让"工具面又长回来了"变得看不见）；
  `docs/CODEBASE_OVERVIEW.md` / `API.md` / `SCHEMA.md`（`blueprint_jobs` 标注为已退役、
  表与迁移照留）/ `CONFIG.md` / `USER-MANUAL.md` / `PLUGIN-SDK.md` / `AGENTS.md` / `README.md` /
  `CONTRIBUTING.md` 同步收口；两份决策文档按"历史只增不改"追加**后补**说明。
  改动前后都跑了 AGENTS.md §3 的同一套门禁。

### Fixed

- **写给模型的那两句附件提示，会让它否掉自己刚答对的东西**（2026-09-22 真机联调，n=5 次运行；
  [PITFALLS #89](docs/PITFALLS.md)）。

  两处，都是**我们自己写给模型的话**：

  - **旧轮的图片被说成"内容没发过"**：那句引用话术是图片与文档**共用**的——
    「这份文件在更早的轮次里已交给过你，**正文**没有重复发送。要看内容用 `attachment_read`」。
    图片没有"正文"，而 `attachment_read` **也给不了像素** ⇒ 模型读到的是"我从来没见过这张图"。
    现场实录：它先答对了纯蓝色块与黑方块（**都不是能猜出来的**），第三轮却主动补一句
    「前两轮关于色块和形状的图片，**我并没有真的读取附件内容，回答是瞎猜的**」。
    现在按类型分写：图片那条说「**你在那一轮看过它的像素**，本轮不重复发送；
    要再看请让用户重新贴一遍（`attachment_read` 只给文件名与类型）」。
  - **提示里提起了失败模式**：我一度把内联提示写长，加上「不要用 `attachment_list` 的
    `textChars` 判断它能不能读」——本意防误判，实际**把"读不出"这个念头递给了模型**：
    那一轮的回答变成「看不出颜色——这个文件只有 168 字节、textChars=0，像是空文件或损坏的图」，
    而同一次运行的下一问它又答对了"正方形"。现在最新轮的提示**缩成一句正向指令**
    （`[附件 …：用户交上来的图片，见图]`），"图片没有文本、像素随消息给你看"那句挪进
    `attachment_list` 的**输出**里（只有模型真去调那个工具时才看得到）。

  **判据**：① 新增 `python tools\verify_attachments_live.py`（**真机联调 · 要花 token**，
  从 `tmp\` 挪进 `tools\` 成为正式门禁）——五问：颜色 / 形状 / 表格数字 / **不撤回自己** /
  负样本，**实测连续 3 次全绿**；② `tests\test_attachments.py`（**26 条**）新增两条**措辞**判据，
  把"不许说成没发过""不许提起能不能读"钉死；③ 顺带用 `tmp\probe_color.py` 直连 API 量了感知本身：
  64×64 与 256×256 的纯蓝/纯红各 2 次 ⇒ **8/8 全对** ⇒ 读不准**不是**模型能力问题。

- **上下文占用那个图标根本读不出比例 —— `conic-gradient` 在场，用户看见的是一块看不懂的小色块**
  （2026-09-22，同一行上线当天用户就报；[PITFALLS #87](docs/PITFALLS.md)）。用户原话：

  > 「上下文占用比例的图标 UI，应该是一个圆形，由当前上下文占模型最大上下文的比例进行填充。
  >  图标 UI 旁则为上下文占用比例。」

  病根在"用 `box-shadow: inset 0 0 0 2px <页面背景色>` 抠圆心"：**inset 阴影只覆盖外缘那一圈**，
  圆心那块原样留着 ⇒ 12px 的图标渲染成「半块饼 + 一圈背景色光环」，比例只活在那 8px 内圆里。
  而我为它写的那条断言（`backgroundImage` 里有 `conic-gradient`、`--usage-ratio == 0.5`）
  **当时是绿的**——它查的是**我写下的 CSS 值**，而形状是浏览器算出来的。

  现在：空心改用 `mask: radial-gradient(farthest-side, transparent 62%, #000 63%)`（**真透明**，
  不依赖背景色），图标 12→**14px**，填充色抽成 `--usage-tone`（sky/amber/red 跟档位走，
  与旁边那个百分比**同色**）；图标旁按用户要求只留**占用比例**（`上下文 50%`），
  精确的 `66k / 131k` 挪进悬浮提示（信息不丢，但那两个数不是"一眼要判断的东西"）。

  **判据（这次是像素级的）**：`tools\verify_composer_ui.py` 的 H4–H6 —— 4× 密度截元素图 →
  **自写 PNG 解码**（venv 里没有 Pillow/numpy）→ 在环带半径 72% 处按 10 个角度取像素、
  逐个判给最近的已知颜色：比例内那半圈必须全是填充色、比例外那半圈必须全是轨道色、
  半径 22% 处必须是背景色（真空心）。**反证实测**：修前 10 个角度**全部**判成背景色
  （H4 0/5、H5 0/5、H6 红，`COMPOSER_UI: 16/20`），修后 5/5 + 5/5 + 空心（**20/20**）。
  同一脚本另存两张图供肉眼复核：`tmp\composer-strip-4x.png`（用量行 4×）、
  `tmp\composer-ring-4x.png`（圆环 60×60）。

- **风险门放行之后"没有任何人在等" —— 现在就地等答复，放行即执行**（2026-09-22 晚，用户下达
  「执行 G2」；[PITFALLS #84](docs/PITFALLS.md)）。真机现场：用户点「放行」后**什么都没发生**
  （15:08:43 那条 `POST /pending/{id}/approve` 之后进程日志**零行**）——因为风险门**只发事件不等**，
  它假设"模型会重试同一次调用"，而模型往往已经说完这一轮。同一场会话里前一次放行却是生效的
  （那次模型走的是"先问再做"⇒ 有等待者），**两条路一个有等待者一个没有**，肉眼看不出来。

  现在两条路**对称**：`harness/loop.py` 发完 `approval_required` **就地等答复**
  （新增 `AgentConfig.approval_wait_s`，默认 300s；**超时一律按未同意**），放行则由**服务端**
  用完全相同的参数重投那次调用（`claim_approved` 按指纹认领，一次性）并把**真实结果**喂回模型
  ——用户只点一次；同时发 `confirm_resolved` 让确认卡就地收口。
  `approvals._settle_locked` 顺带修好同族隐患：**无论有没有等待者都存一份答复**
  （`_decisions` 加 64 条上限兜底），否则"答复早到一步"仍然会丢（PITFALLS #57 同族）。
  界面文案同步改成「已放行 · 正在用相同参数执行」（规则 32：界面文案是承诺）。

- **不再为"注定失败"的写操作问用户放行**（2026-09-22 晚，用户下达「执行 G3」；
  [PITFALLS #85](docs/PITFALLS.md)）。工厂的预检（"对象不存在但存在相近对象"）是**确定性**拒绝，
  而它跑在确认门**之后** ⇒ 用户被要求为一次必然失败的写操作授权，点完还是同一个拒绝
  （15:08 实测：同一份蓝图连问两次放行，两次都白点）。

  新增可选钩子 **`ToolSpec.precheck`**：只读、**在确认门之前**跑，返回非空即"这次调用注定失败"
  ⇒ **不问放行、不执行**，理由原样成为模型观测（`status=rejected`）。
  **异常/超时一律放行**（它只买"省一次白问"，拦截责任仍在确认门与 handler）；
  `factory_create` 声明它，且与真正执行时**同一段** `precheck_blueprint`（不是第二套规则）。

- **配置工厂：`createIfMissing` 显式意图位 —— 「用户确认新建」第一次有了出口**（2026-09-22 晚，
  用户下达「方案1」；补记 —— **该决策文档 已随配置工厂一并移除**）。
  真机实测暴露：防误建的预检闸门是**确定性**检查，**看不见对话** ——
  用户被问了「确认要新建吗」、答了"确认"、蓝图也校验通过，`factory_create` **仍被同一个理由拒回**，
  且重试多少次都一样；而本平台的测试对象是**流水号命名**（`测试09-01/02/03`），
  下一个编号必然"相近" ⇒ 越按习惯建越建不出来（15:08 复盘的 G1）。
  现在：蓝图对象上写 **`createIfMissing: true`** 即放行（不写则行为完全不变），
  被放过的相近对象进 `precheck.overridden` 并在运行日志留一行 —— **放行 ≠ 装作没看见**。

- **配置工厂：`Listlayout/Save` 缺 `code` 必失败 / 字段层"幂等可重跑"是假的**（2026-09-22 晚，
  真机创建 `test_0904__c` 时连撞两次；[PITFALLS #82/#83](docs/PITFALLS.md)）。

  - 蓝图给了 `listLayout.name` 就走"新建布局"分支，而**那条分支从没有真机样本**
    （`keygate` 一直把它标成「未核」）—— 第一次跑就被平台拒：
    `code=500 组件[listlayout]的Code字段不能为空`。现在目标选择改成
    `code → name → 默认(source=2)`，**没有 `code` 就不新建**（不自己编 code 形状），
    退回默认时在 message 里**明说退到了哪个布局**。
  - `batch_create_fields` **无条件** POST `SaveField` ⇒ 重跑同一份蓝图撞
    `code=500 字段名称重复`；更糟的是步骤异常**穿出整条管道**，
    连 `result.json` 都写不出来（现场只剩"跑了一半"的平台数据）。
    现在字段层先**读一次** `FieldPage` 全表、已存在则**复用其 id**；
    `record()` 把异常转成**该步失败**；对象拿不到 `objectId` 时提前收尾。

  **真机验证**：`run_blueprint.py` 跑同一份蓝图 ⇒ `success=True`（六步全绿），
  回读确认对象 `test_0904__c` + 字段 `remark__c` + 默认表单布局 1 个控件（`AKInput`）
  + 默认列表布局 1 列全部落位。证据见
  `factory-tuning/reports/2026-09-22-真机创建-测试09-04.md`（**该目录已于 2026-09-29 删除**，
  原文用 `git show <删除前提交>:factory-tuning/reports/...` 取回）。

- **配置工厂：布局写入器按真机线格式收口**（2026-09-22，用户逐条下达；判据 = 真机样本 + `keygate.py`）。
  此前"表单布局"这一步在真机上**必失败**，两道坎叠在一起：

  - **读**：`Layout/LayoutList` 回的是**分页信封**（`{count,…,datas:[…]}`），而 `get_form_layouts`
    写的是 `list(client.get(…))` ⇒ 拿到的是**键名**，`layout.get("source")` 抛
    `AttributeError: 'str' object has no attribute 'get'`。**表单一保存就断在读默认布局**
    （此前台账记的失败形态"返回 success:False"与真机不符）。
  - **写**：读修好之后，`SaveLayoutDetail` 被平台以 **`code=500 message=数据不存在`** 拒绝 ——
    根因是顶层对象键发 `basicObjectId`，真机是 `objectId`。

  收口内容（每条的判据见 `docs/FACTORY-DECISION-BRIEF.md` §5 与
  `factory-tuning/reports/2026-09-22-真机验证-默认布局加控件.md`）：
  顶层改按真机 **17 键**提交（`code`/`name`/`source`/`isMain`/`objectTypeId`… **身份键从读回的详情原样带回**）；
  新增控件改发真机 **8 键**最小形状（`code`/`type` = `AK*`/`fieldId`/`sectionId`/`gridSpan`/`attrs`），
  并新增 `AK_BY_DATA_TYPE` 表（实测 9 种 dataType → 7 个 `AK*`，**多对一**；未知类型 Fail-Closed 不提交）；
  **新增"换稳定 id"这一步**（`Layout/GetStableComponentIds`，平台客户端保存新建组件前必调，工厂此前从不调）；
  列布局改为**读旧列 → 合并 → 提交**（`AddColumns` 是全量替换，旧实现不读旧列 ⇒ 有清空列的风险），
  且**读不到旧列就不提交**；数组键名 `columns` → 真机 **`listlayoutColumns`**；
  布局/列表字段改为**从平台读字段全表**（此前只认"本次新建成功的字段"，其余**静默跳过**），
  放不进去的字段进 `skipped` 并让该步可见失败；`SaveField` **不再发**真机从不发的 `isRequired`
  （必填由控件层表达，蓝图里的必填**升级为人工项**，不静默失效）；
  `maxLength` 上限从写死的 `17` 改为**按 dataType** 判（手册：单行文本 ≤1500、长文本/富文本 ≤32000，
  含 `spec.py` 给模型看的注解）。

  **真机验证**：`test_0903__c` 上一次跑通 —— 4 个新字段 + 默认布局**新增两个部分** + 4 个控件全部落位，
  旧内容零丢失、布局身份（`source=2`/`isMain=true`/id）未变。

### Added

- **消息里能看见"我到底发出去了哪几个文件" —— 服务端那条 `attachment` 事件此前没人接**
  （2026-09-22，给"用户传文件"这条链补行为判据时查出来；[PITFALLS #88](docs/PITFALLS.md)）。

  `routes_chat` 从 `start` 之后**逐条**发 `attachment`（写它就是为了让界面知道"这几个文件
  跟着这条消息走了"），而前端的事件回调里**没有这个分支**（`grep '"attachment"' workbench/static/js/*.js`
  **零命中**）。两个后果：① 发送成功后输入框上那条附件条被清空，消息里又什么都没留下 ⇒
  用户没法确认自己发出去了什么；② **刷新页面后更难看**——库里存的是
  `看看这三个文件 [附件 att-8b763c25：编制.xlsx]`（标记是给模型/工具用的坐标），
  而 `renderMessages` 把整条正文当纯文本渲染，于是气泡里**原样显示机器记号**。

  现在：`chat.js` 新增 `splitMarkers()`（正则与 `services/attachments.MARKER_RE` **逐字对应**）
  与 `attachChip()`；气泡上方画出附件 chip（图片给缩略图、文档给文件图标，点击在新标签打开
  原文件），正文只留人话；`sendMessage` 接住 `attachment` 事件往**那一条**用户消息里追加 chip。
  壳侧同时补上 `setWindowOpenHandler` —— 页面里的 `target="_blank"`（回答里的 Markdown 链接、
  产物预览、附件 chip）一律交给系统浏览器，不再开一个没有返回路径的裸 Electron 窗口。

  判据：`tools\verify_composer_ui.py` 新增 **J/K 两组共 13 项**（该脚本 **32 项**）——
  真点「📎 附件」按钮（`expect_file_chooser`）、真派发带 `File` 的 paste（**粘的是真 PNG**，
  走图片那条分支）与 drop（带高亮反馈），发送后断言**拦截下来的请求体**里真有 3 个
  `attachmentIds`、服务端真落了盘、气泡里真长出 3 个 chip；再**放开 SSE 拦截**发第二轮
  （临时库那把 Key 是假的 ⇒ 立刻 401，**不花 token**）让**真服务端**把标记写进库，
  刷新后断言画回 2 个 chip、`href` 指向各自附件、正文里**没有** ``docs/AGENT-FILE-INPUT-PLAN.md`（**该文件已于 2026-09-29 删除**）。
  用户拍板：**图片直连模型多模态**（实测 `deepseek-flash` 真的看得懂图）、
  **加三个纯 Python 解析依赖**、**只把最新一轮的附件展开进上下文**、本轮做 P0+P1+P2。

  | 面 | 做了什么 |
  |---|---|
  | **输入** | 三条入口都通：📎 按钮 / **粘贴**（截图后最自然的动作）/ **拖拽**；附件条显示"已解析多少字 / 扫描件读不出字"；只有附件没文字也能发 |
  | **落盘** | `POST /api/attachments`（multipart）→ 校验（类型白名单 + **魔数复核** + OOXML zip 体检 + 20MB / 5 个上限）→ 落 `<RUNTIME_DIR>/attachments/<会话>/`（**不接受任何来自 URL 的路径**）→ 解析 → 入库（迁移 21 `attachments`，含**坐标目录** JSON） |
  | **进模型** | 唯一扼流点 `_model_history` 展开：图片 → `image_url`（data URL）；文档 → 文件头 + 正文（有上限）+ 坐标 + 解析说明。**只有最新一轮**展开，历史轮只留一句引用（否则 token 线性膨胀 + 前缀缓存失效，成本 ×50） |
  | **工具面** | 新增 `attachment_list` / `attachment_read`（按页 / 工作表 / 表 / 行 / 字符区间取；**跨会话读取一律拒**）。工具数 23 → 25 |
  | **解析** | `.md/.txt/.csv`（零依赖）/ `.docx` / `.xlsx` / `.pdf` / 图片；每种都给**可复算的坐标**（页/表/段的 `offset`+`chars`）——这是"回答能指到出处"的全部依据 |

  **真机联调（走产品自己的接口）**：64×64 纯蓝图 ⇒ 模型答「蓝色」；白底黑方块 ⇒ 答「正方形」；
  xlsx 里的"市场部 8 人" ⇒ 答「8」；问文件里**没有**的"研发部" ⇒ 答「没有」（**没编造**）。

  这条链一次抓到两个**单元测试照不到**的缺陷（`_pending` 永不绑定 / 这一轮的消息绕过扼流点，
  当时 pytest 23 passed 全绿）——记在 [PITFALLS #86](docs/PITFALLS.md)，判据也补进了测试。

- **对象不存在时不许静默新建**（2026-09-22，用户定稿； —— **已随配置工厂移除**）：
  工厂的对象步骤是**幂等**的（code 存在→复用、不存在→**新建**），于是"code 打错一个字"的后果
  **不是报错，而是静默多出一个空对象**。现在三层拦：① 规格文本要求先确认；
  ② 新增只读工具 **`factory_resolve_object`**（查不到就回**相近对象清单** + 给用户的话术）；
  ③ 预检发现"不存在但存在相近对象"时，`run_full_workflow` **拒绝写入**并回候选清单。
  相近对象由**机械变体 + 逐个真机探测**得到（后缀/下划线/大小写 + **末位数字 ±1/±2**）——
  因为平台**没有可枚举对象的端点**（14 个候选路径实测 404），这一限制写进了决策记录的「诚实边界」。

- **`factory-tuning/`：配置工厂调优用的平台操作采集与分析（⭐ 不属于产品）**（2026-09-20，
  用户定稿："监听功能本不属于本项目的功能，只是为了优化而做，请单独起文件夹"）。

  配置工厂的蓝图 schema **本来就是从监听里得来的**；现在要接着优化它，
  就得有"人正常配一遍时平台到底发了什么"的数据。产品里**已经有**录制能力
  （`services/egmp/monitor/monitor.py`：三级降噪 + 每条请求附带最近 5 次页面点击/输入），
  但入口埋在「账号中心 → 托管会话 → 开始监听」里，产物也躺在运行时目录没人分析。
  这个目录补上两头：**一条命令调起来**、**收进来分析**。

  | 组成 | 干什么 |
  |---|---|
  | `tools/capture.py` | 起/停监听（调产品已有的 `/api/monitor/*`，**不改产品一行代码**） |
  | `tools/redact.py` | 脱敏规则唯一事实源（ingest 与扫描共用） |
  | `tools/ingest.py` | 产物**脱敏后**收进 `data/`（落盘即脱敏，磁盘上不存在原始流量） |
  | `tools/scan_secrets.py` | 提交前兜底扫描 |
  | `analyze/coverage.py` | **哪些真实用到的端点我们没建模**（对照 `kernel/endpoints.py`） |
  | `analyze/shapes.py` | 真实请求体的键 vs 蓝图模型 → **schema 缺口** |
  | `analyze/report.py` | 出 `reports/<日期>-<会话>.md` |
  | `tests/selfcheck.py` | 本目录自检（**不进仓库 pytest**，手动跑） |

  **隔离是可机检的**：`tests/test_factory_tuning_isolation.py` 断言
  `workbench/` 里零引用、打包清单里没有它、它不注册任何工具
  ——**删掉这个目录，产品一行都不会变**。

  ⚠️ **脱敏是硬规矩**。用户选了"数据与报告都进仓库"，而仓库要推到 GitHub、
  平台流量里带着 JWT / 会话令牌 / 口令——原始流量进了 git 历史很难真正删掉。
  所以按本仓铁律 19 / 26，脱敏在**写文件之前**做（不是提交前补），
  且扫描规则与脱敏规则**共用同一份**（各写一份 = "脱敏漏掉的形状、扫描也认不出来"）。

  开发中两个方向各暴露一个真 bug，都已修并各配一条回归：
  - **脱敏不足**：base64 规则的字符类含 `/`，于是**每条端点路径都被当成令牌**（32 处误报）
    ——一个动辄误报的扫描器会被忽略，等于没有（PITFALLS #43 同族）；
  - **过度脱敏**：键名规则里的 `sign(ature)?` 打掉了 `enableSignatures`
    ——那是 `ObjectDef` 的**真实业务字段**，打掉它会让采集到的结构缺一块，
    分析据此得出"平台不发这个键"的错误结论。**过度脱敏与脱敏不足一样是缺陷。**


- **模型可以主动"先问再做"：新工具 `request_user_confirmation`（请求用户确认）**
  （2026-09-20）。


  用户的原话是"当 Agent 准备执行修改权限、提交表单等敏感、高风险或不可逆操作前，
  调用该工具向用户弹出确认框"。落地形态经过两个决策点：**工具形态选 B**
  （确认可以带上 `targetTool` + `targetArgs`，于是用户同意的是**那一次具体调用**，
  不是一个含糊的"我打算做点什么"）、**交互形态选"循环内等待"**。

  **它与已有的风险门是两套东西，互补而非替代**：

  | | 谁发起 | 拦的是什么 | 权威在 |
  |---|---|---|---|
  | `risk >= write`（既有） | **宿主**，按声明自动拦 | 这一次**调用** | 宿主，模型绕不过 |
  | `needs_confirmation`（新增） | **模型**，主动调工具 | 模型**打算做的事** | 模型自律 + 用户当场判断 |

  所以它**不是安全边界**（模型不调用它就绕过去了），也不改变任何 `Risk` 分级——
  写操作照样走它自己的门。它补的是分级表的盲区：模型知道它打算干什么，声明表不知道。

  **等待只能在循环层，不能在工具 handler 里**——`ToolHub.execute` 一旦 `await`，
  调用它的异步生成器就挂起了，`confirm_required` 事件发不出去，界面根本不知道有东西要确认。
  于是拆成两步：`execute` **只登记**（建 `pending_action` 行 + 一个 `Future`）并返回
  `needs_confirmation`；`harness/loop.py` 先把事件 yield 出去，**然后**才等答复。
  新增两个 SSE 事件 `confirm_required` / `confirm_resolved`，前端一张独立的确认卡
  （天蓝，与风险门那张琥珀色的**文案与配色都分开**——"点完之后要不要再发一条消息"是相反的）。
  **不需要额外一轮模型调用**：答复就是那次工具调用的结果。

  **安全性没有放松**：带 `targetTool` 时，那条待确认挂在**目标工具**头上、绑定**目标参数指纹**，
  用户同意后模型直接调用目标工具走的是与风险门**完全相同**的 `claim_approved()` 通道
  （按「工具 + 参数指纹」认领、**一次性**、5 分钟内有效）。模型换个参数再调，指纹对不上，
  照样重新过门——回归断言 `test_confirming_one_call_does_not_authorize_another`。
  不带 `targetTool` 的纯意图确认**不产生任何可认领的批准**。

  **Fail-Closed 全覆盖**：宿主没接确认通道 → `ERROR` 且明说"**没有**向用户问过任何问题"；
  没人能等 → 按未同意；`targetTool` 不存在 / 是只读 / 自己也要确认 → `BAD_ARGS`
  （**不降级**成纯意图确认——那会让用户以为批准了 A 而实际什么都没批准）；用户超时或对话中断
  → 条目置 `expired`（**事后补点同意无效**，不留事后通行证）；HTTP 面 409 + `confirm` 内容，
  **不替用户等**。

  回归测试 14 条见 `tests/test_user_confirmation.py`。

- **`tools/verify_confirmation.py`：实时确认的端到端检查（19 项）**（2026-09-20）。

  真起生产入口（`python -m workbench.server_entry`）、真发 HTTP、**真把三个静态文件拉下来看有没有新代码**，
  用临时数据目录、跑完即清。组件测试用的是 `TestClient` 与假客户端——它证明得了逻辑，
  证明不了链路，而"打包/静态目录漏收"这类事故只会在这里现形（PITFALLS #20 / #25 / #53 同族）。

  覆盖：目录里 `needs_confirmation` 是否**真的**透到了 HTTP（不是被装配丢成默认值）、
  前端三文件是否含新代码、`invoke → 409 → approve → 同参数不再被拦 / 换参数仍被拦`、
  只读目标与不存在目标都是 400、纯意图确认同意后**不放行任何写操作**。


- **全项目运行日志系统**（2026-09-20）。改造前：全仓 **38 处** `logger.*` 调用
  （18 info · 18 warning · 1 error · 1 debug），而 `basicConfig` / `FileHandler` /
  `addHandler` / `dictConfig` **一处都没有**；数据目录里**只有 `workbench.db`，零日志文件**。

  分级后果：19 处 `info`/`debug` → 无 handler → `logging.lastResort` 是 **WARNING 级** → **完全丢弃**；
  19 处 `warning`/`error` → 写 stderr → 而桌面壳以 `stdio: 'ignore'` 启动 sidecar
  （`desktop/main.js`）→ **打包态丢弃**。

  > **最能说明问题的一条**：`api/__init__.py::_tool_selfcheck()` 的注释写着
  > 「**必须把结果喊出来**：少了哪个插件一眼可见」，而它的 `logger.error` **喊不出去**——
  > PITFALLS #32（打包漏收插件）唯一的启动期报警在打包态 **100% 听不到**。

  落地：新增 `workbench/logging_setup.py`（唯一装配点）+ `api/routes_logs.py`（检索面）；
  两个入口（`main.py` / `server_entry.py`）最先装配；`api.__init__` 的 lifespan 幂等兜底。

  **四层分工**（刻意不重造已有的审计表）：L0 进程日志 / L1 请求日志 → `logs/workbench-<日期>.jsonl`；
  L2 领域事件 → **既有 DB 表**；L3 审计 → 不碰。日志只带 `auditId`/`jobId` **指过去**，不复制内容。

  **关联 ID 用 `contextvars`**（`traceId`/`conversationId`/`auditId`/`jobId`/`tool`），
  于是不必在几十个函数签名里穿参数；`traceId` 同时回在响应头 **`X-Trace-Id`** 里——
  用户报故障时给出这个号即可还原整条链路。

  **四条不变量**（每条都有回归断言）：① 日志失败**绝不影响主流程**
  （`logging.Handler.handle` 本身**不包 try**，故自己包了一层）；
  ② 凭据**永不入日志**（落盘前统一 `RedactionFilter`，不依赖调用方自觉）；
  ③ 不依赖 stdio / 不依赖 CWD；④ 单进程单写者。

  **刻意不做 `QueueHandler`**：队列满时它会**丢弃记录**——那是本仓最忌讳的静默失败，
  比同步写的开销糟得多（理由写进了模块文档串）。

- **`tools/verify_logging.py`：日志系统的端到端检查（32 项）**。真起服务（真实入口
  `workbench.server_entry`、专用端口 18793、临时数据目录）→ 真发请求（含工具调用、
  确认门、未知工具、错误路径）→ 读**真落盘的文件**逐条断言。
  **它第一次跑就抓到了两个组件测试抓不到的缺陷**（见下 `Fixed`）。

- **`tools/verify_regression_tests.py`：回归断言反证台（6 例）**。把三份近乎相同的反证脚本
  （前缀 / 日志 / 审批 + 敏感表）**收成一张 `CASES` 表**——三份复制品就是三个会各自漂的事实源，
  正是本仓 2026-09-15「两套工具表」与 2026-09-20「两张敏感表」的同一种错误。加一条反证 = 加一行。

- **决策记录新增「外部参照」一栏**（2026-09-20）。

  历史决策记录记了"我们决定什么"，但没记"**谁解决过同一问题、他们怎么做、我们为何不同**"。
  那部分权衡在写下决策时**确实存在过**（在脑子里），只是没落纸——于是半年后没人能重建它。

  模板要求：一致的地方 = 行业共识，写一句"我们跟随"即可；**分歧的地方 = 真权衡，
  必须写清"我们为何不同"，那一栏的价值几乎全在这里**。给不出来源的写"未核实"，
  **不许凭印象写**（那会变成一条没人敢质疑的假共识）。

- **`tests/test_cache.py` 新增 3 条链路级测试**（2026-09-20）。动因：该文件原有 4 条
  前缀稳定性断言，但全部覆盖"普通问答轮次"——而 `run_agent_loop` **内部**还有一处会改动
  context：`_one_model_turn` 拿到 tool_calls 后 append `assistant(+tool_calls)` 与 `role=tool`
  两条。**那是 append-only 最容易被悄悄破坏的地方，此前零覆盖。**

  1. `test_tool_turn_keeps_prefix_within_one_run` —— 工具轮次的第二次 LLM 调用，
     其 messages 必须以第一次为**逐字节前缀**，且确实只多出那两条。
  2. `test_prefix_stable_over_history_containing_tool_messages` —— 覆盖工具轮次特有的消息形状
     （`content: None` + `tool_call_id`），用的是 `sort_keys=False` 的指纹，
     因此**键的插入顺序漂移也会被抓到**（dict 的 `==` 忽略键序，而 DeepSeek 的前缀匹配
     发生在**序列化后的字节**上——单靠 `==` 会漏掉这类漂移）。
  3. `test_injected_tool_result_is_bounded` —— 回填进**上下文**的工具结果受硬上限约束。
     单列这条的理由是本仓 PITFALLS #42 的教训：`test_plugin_api` 验的是 `_truncate()`
     **这个函数**，而"loop 回填进 context 的那条 tool 消息真的被截断了"是**另一件事**。

  **三条均已实测对模拟回归 FAIL**（反证脚本 [`tools/verify_regression_tests.py`](tools/verify_regression_tests.py)：
  分别注入"每轮重新注入系统提示词"与"`_truncate` 空转"两种**看似无害**的改动，对应测试立刻红；
  脚本跑完对源文件做**字节级还原核验**，还原失败则 `exit 2`）。
  这遵循本仓纪律：回归断言必须"实测对旧实现 FAIL"——**一条从没红过的测试，你不知道它测的是什么**。
  该脚本**手动诊断用、不进 CI**（它会临时改写生产源码）。

  > **写这个脚本时又踩出一个新坑（已记为 PITFALLS #51）**：初版用 `read_text()` +
  > `write_text(..., newline="")` 读写源文件，并在结束时核验"字节级还原"。
  > 那段核验**是假的**——`read_text()` 会做通用换行转换，把 CRLF 源文件静默改成 LF，
  > 而它比的是**已经转换过的字符串**，所以一路报"还原成功"；
  > 真相只在 `git status` 里露头（两个生产文件显示 modified + `LF will be replaced by CRLF`）。
  > **"看起来还原了"不构成还原正确性证明，要比的是字节。** 修法：一律 `read_bytes()`/`write_bytes()`，
  > 核验比字节；且匹配锚点要用归一化视图（CRLF 文件里 `anchor in text` 会永远找不到）、
  > 落盘用原风格。两条都写进了该脚本的文档串。

- **`PITFALLS.md` #50 / #51 / #52 / #53**：四个"看起来正常但其实是坏的"实例（见下 `Changed` 与 `Fixed`）。

- **`AGENTS.md`（新增，仓库根目录）：给 AI 协作者的规则入口**（2026-09-20）。

  本仓已有决策记录 / `PITFALLS.md` / `CODEBASE_OVERVIEW.md`，但那些**都是写给人看的**。
  用 AI 助手改这个仓库时会出现一类特定的失败：它会把我**没写下来的约定**当成
  **不存在这个约定**，然后用**看起来完全正常**的代码违反它。已有的失效证据不止一条——
  `cache.py` 的 append-only 前缀靠一句文档串警告守着，而 PITFALLS #41 / #42 记的正是
  "约定在纸上、没在代码里"造成的静默故障。

  内容：**25 条铁律**（每条注明出处 PITFALLS 编号）+ 门禁矩阵（改哪跑哪）+
  本机环境速查 + "不要做的事" + 留痕纪律。格式遵循
  [agents.md](https://agents.md) 约定——它与 MCP 同属 Linux Foundation 旗下
  **AAIF（Agentic AI Foundation）的创始项目**，即"仓库根目录的该文件就是 AI 的规则入口"。

  与 `CONTRIBUTING.md` 的分工是刻意的：**这里只写"做与不做"**（规则形状、可执行、带判据），
  "为什么与怎么做"留在 CONTRIBUTING 与 CORE-ARCHITECTURE。两份不重复。

- **决策记录新增「外部参照」一栏**（2026-09-20）。

  历史决策记录记了"我们决定什么"，但没记"**谁解决过同一问题、他们怎么做、我们为何不同**"。
  那部分权衡在写下决策时**确实存在过**（在脑子里），只是没落纸——于是半年后没人能重建它。

  模板要求：一致的地方 = 行业共识，写一句"我们跟随"即可；**分歧的地方 = 真权衡，
  必须写清"我们为何不同"，那一栏的价值几乎全在这里**。给不出来源的写"未核实"，
  **不许凭印象写**（那会变成一条没人敢质疑的假共识）。

- **`tests/test_cache.py` 新增 3 条链路级测试**（2026-09-20）。动因：该文件原有 4 条
  前缀稳定性断言，但全部覆盖"普通问答轮次"——而 `run_agent_loop` **内部**还有一处会改动
  context：`_one_model_turn` 拿到 tool_calls 后 append `assistant(+tool_calls)` 与 `role=tool`
  两条。**那是 append-only 最容易被悄悄破坏的地方，此前零覆盖。**

  1. `test_tool_turn_keeps_prefix_within_one_run` —— 工具轮次的第二次 LLM 调用，
     其 messages 必须以第一次为**逐字节前缀**，且确实只多出那两条。
  2. `test_prefix_stable_over_history_containing_tool_messages` —— 覆盖工具轮次特有的消息形状
     （`content: None` + `tool_call_id`），用的是 `sort_keys=False` 的指纹，
     因此**键的插入顺序漂移也会被抓到**（dict 的 `==` 忽略键序，而 DeepSeek 的前缀匹配
     发生在**序列化后的字节**上——单靠 `==` 会漏掉这类漂移）。
  3. `test_injected_tool_result_is_bounded` —— 回填进**上下文**的工具结果受硬上限约束。
     单列这条的理由是本仓 PITFALLS #42 的教训：`test_plugin_api` 验的是 `_truncate()`
     **这个函数**，而"loop 回填进 context 的那条 tool 消息真的被截断了"是**另一件事**。

  **三条均已实测对模拟回归 FAIL**（反证脚本 `tools/verify_prefix_tests.py` —— **该脚本后来被
  `tools/verify_regression_tests.py` 取代，已不存在**：
  分别注入"每轮重新注入系统提示词"与"`_truncate` 空转"两种**看似无害**的改动，对应测试立刻红；
  脚本跑完对源文件做**字节级还原核验**，还原失败则 `exit 2`）。
  这遵循本仓纪律：回归断言必须"实测对旧实现 FAIL"——**一条从没红过的测试，你不知道它测的是什么**。
  该脚本**手动诊断用、不进 CI**（它会临时改写生产源码）。

  > **写这个脚本时又踩出一个新坑（已记为 PITFALLS #51）**：初版用 `read_text()` +
  > `write_text(..., newline="")` 读写源文件，并在结束时核验"字节级还原"。
  > 那段核验**是假的**——`read_text()` 会做通用换行转换，把 CRLF 源文件静默改成 LF，
  > 而它比的是**已经转换过的字符串**，所以一路报"还原成功"；
  > 真相只在 `git status` 里露头（两个生产文件显示 modified + `LF will be replaced by CRLF`）。
  > **"看起来还原了"不构成还原正确性证明，要比的是字节。** 修法：一律 `read_bytes()`/`write_bytes()`，
  > 核验比字节；且匹配锚点要用归一化视图（CRLF 文件里 `anchor in text` 会永远找不到）、
  > 落盘用原风格。两条都写进了该脚本的文档串。

- **`PITFALLS.md` #50 / #51**：两个"看起来正常但其实是坏的"实例（见下 `Changed` 与本条末段）。

- **`tests/test_factory_agent.py`（13 条）**：钉住"蓝图 → `blueprint_id` → 创建"这条链路通不通，
  而不只是每个工具自己对不对。含"一句『去页面传』都不许出现"、审阅材料在审批之前、
  确认摘要在讲蓝图要点、规格示例真能过校验、旧 `orchestrate` 命令必须被拒且指明改用什么。

- **`tools/verify_factory_agent.py`：配置工厂链路的端到端检查（24 项）**。

  真起生产入口、真发 HTTP，**并且真去磁盘上看那份暂存的蓝图在不在**——
  "返回了个 id 但没落盘"是这条链路最可能的**假通过**，只有落到文件系统上才验得掉。
  临时数据目录（账号表为空），所以任何"真去连平台"的步骤都会失败在"账号不存在"上，
  **不会碰到真实环境**。覆盖：旧工具已消失 / 规格没被截断 / 闭环落盘 / 校验不过不暂存 /
  旧 `orchestrate` 命令被拒且指明改用什么 / 未勾选环境确认仍 409。


- **`docs/FACTORY-VERIFICATION-PLAN.md`：配置工厂逐条验证与优化方案（⛔ 待办，尚未执行）**
  （2026-09-20，用户要求"做成方案，存储下载，注明为待办事项"）。

  用户原话：「配置工厂的整体逻辑应该是有问题的，我需要单独对规则一条一条优化……
  我与你对话，你就根据规则，一个功能一个功能的测。从单个创建对象，再到字段，
  再到列表，生命周期。」

  方案内容：协作方式（一条一轮、用户提供环境与平台侧核对结果）、分层顺序
  （L0 只读预检 → L1 单对象 → L2 字段按 dataType 逐个 → L3 布局 → L4 生命周期 →
  L5 工作流 → L6 菜单/声明区），每层固定给出六项（测什么 / 测试蓝图 / 怎么跑 /
  平台侧核对点 / 接口侧证据 / 不通过时看哪）。

  **附带两处已有判据、尚未真机复现的结构性缺陷**（按本仓纪律，没看到真机现象之前
  不写进 PITFALLS）：
  - **T1**：`menu` 节从来没被执行过——`execute_menu` 零调用方。
    判据用 **AST 而不是 grep**（名字在文档串里出现过，grep 会骗人）：
    `ast.Name` 扫全仓 → 空列表。
  - **T2**：顶层声明区（`rules` / `permissions` / `reportTemplates`）**既不执行、
    也不进 `manualItems`**——它们在 `to_object_blueprints` 归一化时就被丢掉了
    （`manual_items` 只从 `obj.get("rules")` 收集，而那些键长在顶层）。
    后果是用户写了 5 条规则、2 个权限，跑完**没有任何地方告诉他"这几项要手工配"**。

  另有 5 条一并要验的"宽松判据"（T3–T7）：`record()` 的 `success` 默认 True、
  两套成功判定叠加、`verify` 只数数量不比内容、多对象 checkpoint 前缀不一致、
  `precheck` 冲突只告警不阻断。

### Changed

- **界面上两个模块更名：「平台洞察 → 对象分析」「配置工厂 → 配置」**（2026-09-22，用户下达
  「UI 上做一些更名」）。**只改显示名**——模块 id（`insight` / `factory`）、工具名
  （`insight_*` / `factory_*`）、接口路径（`/api/insight`、`/api/factory`）、
  落盘的产物目录（`runtime/insight/…`、`我的文档\Workbench\洞察`）、
  审计与历史数据里的 `module` 值**全部不动**：改了它们就等于让"改名前后"的留痕不可比
  （同族教训：`insight_runs.module` 曾先后出现过三个值，见 `test_old_module_id_is_gone`）。

  改了 **10 个文件**，其中最容易漏的是**工具清单弹窗里那个插件标题**——它取的是
  `Plugin.title`（`insight.py` / `factory.py`），不在静态目录里：

  | 面 | 位置 |
  |---|---|
  | 侧边栏两项 | `static/index.html`（`nav-insight` / `nav-factory`） |
  | 两个模块页 | `<title>` 与 `<h1>`（`pages/insight.html`、`pages/factory.html`；顺带把「执行洞察命令」改成「执行分析命令」） |
  | 插件显示名 | `plugins/insight.py::PLUGIN.title`、`plugins/factory.py::PLUGIN.title`（**工具清单弹窗就显示它**） |
  | 一句给人看的提示 ×5 | 设置的推理参数说明、撞预算时聊天里那句、`factory.js` 的状态徽标与命令回显、`accounts.html` 的分配池说明 |
  | 两条错误文案 | `routes_factory` 的 409（「违反配置…原则」）、`routes_insight` 的运行日志与 502 |

  判据：`pytest tests/test_app.py::test_module_display_names_are_object_analysis_and_config`
  —— 按**链接块**断言侧边栏两项、断言两个页面的 `<title>`/`<h1>`、断言
  `/api/agent/plugins` 里两个插件标题，**并且**断言六个界面文件里不再出现旧名
  （注释不算，注释是给读代码的人看的）。**反证实测**：把侧边栏改回旧名 ⇒ 该用例立刻红
  （`1 failed`），随后字节级还原。
  文档同步：[USER-MANUAL](docs/USER-MANUAL.md)（§2 表 + §5/§6 标题 + FAQ + 数据位置）、
  [README](README.md)（含**改名记录**一节，说明旧名出现在 `docs/INSIGHT-SPEC.md`（**该文件已于 2026-09-29 删除**） /
   / 坑清单里是正常的）、[API.md](docs/API.md) §6/§7 标题、
  `docs/PLUGIN-SDK.md`（**该文件已于 2026-09-29 删除**） 插件表、[SCHEMA.md](docs/SCHEMA.md) §3.6/§3.7、
  [CONFIG.md](docs/CONFIG.md) 的 `insight_workspace_root`、`docs/TOOLS.md`（`gen_tools_doc.py --write`）。
  ⚠️ **没动**（有意）：`INSIGHT-SPEC.md`、`FACTORY-*.md`，`docs/PITFALLS.md`
  等**领域/历史文档**——它们记的是能力与决策，不是界面标签。

- **输入框那一行重排：模型名挪到发送键旁，下方常驻「缓存命中 / 命中率 / 上下文」三个数字**
  （2026-09-22，用户下达「模仿 DeepSeek 的 UI 美学和功能要点」）。

  三件事，都不是装饰：

  - **提示文字换成模型名**（`#chat-model-badge` 从页头搬进输入框右侧、发送键左边，做成
    可点的 `.model-chip`，点了跳设置）。原来的灰色提示句占着输入框最好的一行位置，
    却只说了一句用户看一次就记住的话；模型名是**每轮都影响结果**的信息（换模型 = 换答案），
    而且它必须离"发送"足够近——用户按下去之前那一眼能看见。
    左下角的 `＋` 与权限选择器**刻意不做**（用户明确排除）：本产品的入口是「工具」和「附件」
    两个有名字的按钮，多一个 `＋` 只会让人多点一次才知道里面是什么。
  - **输入框下方新增一行小字**（`#usage-strip`，12px、右对齐、`tabular-nums` 等宽数字）
    ——`缓存命中 X tok · NN% · ◔ 上下文 A / B（NN%）`。

    | 段 | 数据来源 | 语义 |
    |---|---|---|
    | 缓存命中 | `GET /api/agent/cache?conversationId=` | **本会话累计**：命中价是未命中价的 1/50，这个数直接对应账单 |
    | 命中率 | 同上（命中 ÷（命中+未命中）） | ≥60% 绿 / 20–60% 灰 / <20% 琥珀——用颜色说"你这轮全价了" |
    | 上下文 | `usage` 事件新增的 `contextTokens` | **本轮真实 `prompt_tokens` ÷ `context_window_tokens`**，≥60% 起警示 |

    两处口径是**刻意不同**的：缓存要**累计**才有意义（单轮命中率天然接近 100%，看着好看但没用），
    上下文只能是**本轮**的（累计毫无意义）。为此 `cache.UsageStats` 新增 `last_prompt_tokens`
    ——取平台的 `prompt_tokens` 真值，不用本地估算（估出来的数字不该跟账单并排显示）。
    `harness/loop.py` 的两处 `usage` 事件现在都并入 `_context_usage(meter, config)`；
    页面加载/切会话时只画缓存两项，上下文留空——**服务端不持久化本轮上下文，编一个数字比留空更坏**。
    这一行**始终在**（没有数据时显示「—」）：若做成"有数据才出现"，第一次问答成功时整行会突然
    冒出来、把输入框往上顶一格——那一下跳动比三个「—」难看，而且会让人以为"这功能是刚刚才有的"。
  - **会话统计（`69 轮 1418 步 · 244 tok/s`）本轮不做**（用户判断）。它需要的
    "轮/步/吞吐"三个量目前只有 monitoring 有，而 monitoring **不是产品功能**（见
    [factory-tuning/](factory-tuning/README.md)）；要做就得先把采集搬进产品，
    那是另一个决定，不塞进这次 UI 改动里。

  判据：新增 `python tools\verify_composer_ui.py` —— **20 项**，真起生产入口 + 真开 chromium，
  点会话、按发送，然后读**渲染后**的几何与计算样式（模型名在发送键左侧同一行 / 用量行在输入框
  **下方**且可见 / 三段数字与颜色档位 / 那个圆环**按比例填充且真的空心**（H4–H6，见下面的
  `### Fixed`）/ 渲染字号 ≥12px / 零 JS 错误）——**20/20 PASS**。
  为什么不满足于在 `verify_attachments.py` 的 A6–A9 上多加几条字符串断言：那些只能证明"源码里有
  这些字样"，而这一整类失败恰恰是**源码全对、界面是坏的**（那一行还 `hidden` 着、字号被别处一条
  更具体的规则压回 11px、事件里少了 `contextTokens` 于是永远显示「—」）。
  配套 `tests/test_agent_loop.py` 新增两条钉**发出方**：`usage` 事件必须带
  `contextTokens / contextWindow / contextThreshold / contextRatio`，且窗口配成 0 时不许算出
  `NaN`（JSON 里的 `NaN` 会让前端 `JSON.parse` **抛错、整条流断掉**）。
  文档见 [USER-MANUAL §7.1](docs/USER-MANUAL.md)，门禁行见 [AGENTS §3](AGENTS.md)。

- **确认弹窗改成「标题 + 要点」的可扫读排版，不再把说明文字堆在脸上**（2026-09-22，用户下达）。

  > 「弹出的蓝图/审核界面，请你不要放一堆文字上去，请设计排版，言简意赅，
  >  用户在执行时没有时间去仔细看 UI 上比蚂蚁还小的字」

  改了三处（都在弹窗这一层，不改协议）：

  - **说明文字切成「标题 + 要点」**：第一行做 16px/600 的标题，其余按 `·` 前缀或句读切开
    成要点（13.5px，行高 1.55）；要点**超过 4 条折进「展开其余 N 条」**（渐进披露）。
    实测：15:07 那种"一大段 details"从 6 行散文变成 4 条要点 + 2 条可展开。
  - **技术参数默认收起**：`blueprint_id`/`account_id` 那堆 UUID 收进
    「▸ 技术参数（blueprint_id、account_id）」，不再用等宽 11px 摊在弹窗里。
  - **字号下限**：标题 14→**16px**、正文/要点 12→**13.5px**、次要文字 11→**12px**
    （`tests/test_user_confirmation.py::test_decision_modal_is_scannable_not_a_wall_of_text`
    在 CSS 里断言这四个下限，改小就红）。

  同时**修掉一条过时文案**（规则 32）：风险门那条还写着「本轮对话已经结束，放行后要让模型
  重试同一次调用」——那是 G2 之前的语义，现在是「放行后立即用相同参数执行，结果回到这一轮」。
  工厂的确认摘要也按新形状重写（标题 = 在哪个环境建什么；要点 = 编码 / 字段 / 布局 /
  引擎不执行的部分），形状约定写进 `plugin_api.ToolSpec.summary_fn` 的说明。

- **需要用户拍板的决定改成「模态弹窗」，不再放在对话流里**（2026-09-20，用户反馈）。

  > 「提供给用户审批的，应该设计为弹出窗，而不是放在对话框中随着输出消失，
  >  理想中的行为应该是 agent 暂停 - 弹出确认窗口 - 得到反馈 - 注入上下文 -
  >  根据用户的回答判断下一步。」

  内联卡片有三个绕不过去的问题：**会滚走**（模型再说两句就出了视口，而它是一次
  真实写入的闸门）、**不要求答复**（可以继续打字、切页面）、**不显眼**（它长得像一条消息）。

  改法：**交互与留痕分开**。
  - 交互全部搬进新模块 `static/js/approval-modal.js`：原生 `<dialog>` + `showModal()`，
    带倒计时（超时按未同意处理）、参数预览、风险徽章，标题按 `kind` 区分
    （「模型请求确认」vs「需要你放行」）。
  - 轨迹里只留一条**不可交互的回执**（`decision-receipt`）：`⏸ 确认 · <问题> · 已同意/已拒绝`。
    能点的东西不留在会滚走的地方，但"当时问了什么、结果是什么"必须查得到。
  - **三态答复**：同意 / 拒绝（可填理由）/ **稍后**（Esc 同）。第三态是承重的——
    只给两态时，用户想先看一眼对话就只剩"关掉弹窗"，而那等于**悄悄丢掉一次确认**。
    「稍后」把决定收成输入框上方的待办条，循环继续等，到点仍算未同意。
  - **焦点给弹窗本身**而不是"同意"按钮：焦点落在主按钮上时，一个顺手的 Enter
    就等于同意了一次真实写入。
  - **页面加载时认领遗留在服务端的待确认**（`recoverPending`）：没有这一步时，
    一条待确认只活在当前这条 SSE 流里，刷新一下就永远没人处理了。
    配套给 `PendingAction` 加了内存字段 `kind`（`approval` / `confirmation`，不落库），
    `GET /api/agent/pending` 一并返回——弹窗与认领都靠它区分两类决定。

- **配置工厂**：`blueprint_summary_line` 不再替引擎做它做不到的承诺（2026-09-20）。

  确认框此前会写"含菜单挂载"，而 `orchestrate.execute_menu` **至今没有任何调用方**
  ——菜单根本不会被创建。现在菜单与声明区（规则/权限/报表模板）一律归到
  "**引擎不执行**，需要你人工配置"里。这是"如实描述现状"，不是修复；
  底层缺陷与判据见 FACTORY-VERIFICATION-PLAN.md 的 T1 / T2（**该台账已随配置工厂移除**；
  其中的平台事实已迁进 `docs/PLATFORM-OPERATIONS-LEARNED.md`（**该文件已于 2026-09-29 删除**））。


- **配置工厂 × Agent 的接口改造：四条实测问题一次收口**（2026-09-20，
   —— **已随配置工厂移除**；取证过程见 FACTORY-AGENT-GAP.md，**同已移除**）。

  用户在真机跑配置工厂后报了四条问题。逐条取证后发现**共同根因**：这条能力链是**照着
  "人在网页上点按钮"设计的**，接上对话 Agent 之后只补了"读"与"写"两端。

  **② agent 无法闭环（最硬的一条）**：全插件 5 个工具里**没有一个能产生 `blueprint_id`**
  ——它只能由网页上的 multipart 上传产生。于是校验工具的返回值只能写
  "暂存走配置工厂页面上传，或让用户把文件拖进页面"。**缺的不是能力，是一个动词**，
  而它的表现是"模型去求人"（PITFALLS #59）。

  - `factory_validate_blueprint` → **`factory_preview_blueprint`**（问题③的改名）：
    **校验 + 暂存 + 出审阅材料**一次完成。校验不过就不暂存；过了就落盘并返回
    `blueprint_id` 与**人话审阅清单**。id 用内容指纹（同一份蓝图重复预览不堆垃圾）。
    `risk=read`：只写应用自己的 runtime 工作区、可整体删除、不碰平台与凭据（与 `insight_run` 同级）。
  - 新增 **`factory_blueprint_spec`**：把蓝图规格交给模型。**结构从运行期 pydantic 模型生成**
    （`writers/spec.py`），语义取自 `kernel/semantics.py` 的实证词典，
    注解完整性由 `test_every_model_field_is_annotated` 机检——**不手写第二份 schema**。

  **③ 审阅材料前移**：`generate_review_spec` / `generate_checklist` 原来只在
  `run_full_workflow` **内部**（即执行期）被调用——用户审批时手里没有它，
  而它存在的全部目的就是给他审批用。现在在**预览这一步**生成。配套新增
  `ToolSpec.summary_fn`：`factory_create` 的确认摘要从"工具描述 + `blueprint_id=…` + 危险级"
  变成**"将创建 1 个对象（training_record__c）、2 个字段…"**——用户要判断的是后果，不是参数名。

  **④ 删掉第二个「需求 → 蓝图」循环**：`command="orchestrate"` 连同
  `services/egmp/generate.py`、`services/egmp/complexity.py` 一并删除。它的输入只有一个
  `requirement` 字符串，是对话链路的**降级复制品**。**执行引擎一个字都没删**：
  8 步管道、拓扑排序、幂等、断点续跑、回读验证全部保留——判据是"确定性 vs 非确定性"
  与"不可回滚 vs 可重做"（**该决策文档已随配置工厂一并移除**，
原文可取回：`git show 1fa2301:）。手工上传 `blueprint.json` 那条路保留。

  **① 可对话轮次非常少**：`AgentConfig.max_steps` 默认 **12 → 40**，并接通设置页
  （`settings` 表的 `maxSteps` 键，范围 4~200，越界夹住、坏值回默认）。
  配套两条可行动性要求：预算耗尽带 `reason="budget_exhausted"`，文案说清"已完成的部分都保留着、
  回一句「继续」即可"（前端用中性色渲染，不再是红色报错）；输出截断带
  `reason="output_truncated"` 并明说"**没有**执行任何工具"。


- **敏感路径表收归唯一事实源**（2026-09-20，P0-B 的结构性修法）。
  `config.SENSITIVE_PATH_PARTS` 成为**定义处**（取原两张表的并集，并补上 `.npmrc` /
  `.netrc` / `known_hosts` / `.env` 等）；`sandbox/policy.py` 改为再导出
  （`from ..config import SENSITIVE_PATH_PARTS, is_sensitive_path`），
  `plugins/builtins.py` 的 `_BLOCKED_PARTS` **删除**。
  新增 `config.is_sensitive_path()` 作为全项目唯一判定口径
  （大小写不敏感、`\` 与 `/` 等同、按路径成分包含判定）。
  回归断言**直接查源码里的定义处**，而不是比对象 identity ——
  后者会被 `tests/test_ports_contract.py` 的 `importlib.reload(config)` 弄成
  **测试顺序相关**的（本仓第一版就是这么写错的）。

- **文档与配置同步**（2026-09-20）：
  - [CONFIG.md](docs/CONFIG.md) 新增 **§2.1 运行日志**（5 个 `WORKBENCH_LOG_*` 变量）
    与 §6 的三个日志文件条目（含 `server.log` 与主日志的**分工说明**）；
    §1 的入口表改为"两者共用 `logs/` 运行日志"。
  - [API.md](docs/API.md) 新增 **§19 `/api/logs`**（四个端点 + `X-Trace-Id` 用法 + 安全边界）。
  - `workbench/static/blank.html` 的「服务端没有应答」提示改为
    **先指向 `logs/`**（主日志 + sidecar stdio），再提 `server.log` 面包屑。
  - [AGENTS.md](AGENTS.md) 补 **§1「日志与留痕」铁律 26–30**（脱敏 / 失败隔离 /
    不复制审计 / 关联 ID / 敏感表唯一），并在 §3 门禁矩阵里加上日志与敏感表两行。
  - 新增 `docs/LOGGING-VERIFICATION.md`（**该文件已于 2026-09-29 删除**）：
    端到端验证报告（32 项检查矩阵、as-built 差异、**已知边界**）。

- **`uv` / `ruff` 的口径订正——实测与文档记载正好相反**（2026-09-20）。

  `CONTRIBUTING.md` §1 与 `CODEBASE_OVERVIEW.md` 的现行口径是
  "`.venv\Scripts\` 里**有** `uv.exe`、**没有** `ruff.exe`，故 lint 必须走 `uv tool run ruff`"。
  **2026-09-20 实测**：

  | 事实 | 实测结果 |
  |---|---|
  | `.venv\Scripts\ruff.exe` | **存在**（`ruff 0.16.0`），`check .` → `All checks passed!` |
  | `.venv\Scripts\uv.exe` | **不存在**（只有 `uvicorn.exe`；`python -m uv` 报 `No module named uv`；PATH 上也没有） |

  按旧口径敲命令会直接 `CommandNotFoundException`。**故 lint 门禁命令改为
  `.\.venv\Scripts\ruff.exe check .`，并把两条命令都写下 + 给出探测命令**
  （`Get-ChildItem .venv\Scripts -Filter "uv*.exe","ruff*.exe"`）。

  **值得单独记的不是"写反了"，而是这类事实为什么必然漂**：这条口径在本仓**已翻转过三次**
  （原 `AGENT.md`"两个都没有" → 2026-09-15"uv 存在" → 2026-09-16"uv 不存在、ruff 存在"
  → 2026-09-17 再更正回来）——**每一版在当时都是实测的**。问题不在诚实，
  在于**把一个会漂的环境事实固化成了散文**。故记为 PITFALLS #50，其"推广"给出三种正确形态：
  探一次再决定 / 写成按角色断言的机检（如 P8/P8b）/ 干脆别写进文档。

- **4 份关键决策文档追加「外部参照」栏**（2026-09-20）：（扩展=执行面）、
  （浏览器池单线程）、
  （沙箱）、
  （访问令牌）。

  均**显式标注"本栏为后补"**，正文一字未改（遵循本仓决策纪律：改已采纳的决策 = 在原处标注取代关系）。

  其中两条对照推出了**可执行的认知**，不只是记录：

  - **沙箱设计参照**：OpenAI Codex 的 Windows 沙箱**完全不用 Job Object**，用的是受限令牌 +
    独立低权限沙箱用户 + ACL + 防火墙。⇒ 若要把"限损层"升级为"权限边界"，
    下一个原语是**受限令牌 / AppContainer + ACL，而不是继续加固 Job Object**——
    并据此给该决策的 §5 触发器补了一条（产品开始面向"用户会装陌生来源 Skill"的场景时再评估）。
  - **浏览器池设计参照**：主流浏览器 agent（browser-use / Skyvern / Stagehand）一律用 **async API**，
    因此不需要专职线程。本项目用 sync + 单线程队列是**与主流相反的选择**——
    代价（全局串行、事件回调纪律）集中体现在该决策的"代价（已接受）"表里。
    对照栏的作用**不是证明我们错了，是让代价可见**。

- **`CONTRIBUTING.md`**：新增 §5.1 说明「外部参照」栏为什么存在、怎么填、历史决策如何后补；
  开头加一段指向 `AGENTS.md`（含"AI 会把没写下来的约定当成不存在"的动因）；
  决策记录流程改为**必须写「外部参照」**；§1 与 §7 的 lint 命令按上述实测订正。

### Fixed

- **输出被长度上限截断，被系统报成"参数解析失败"**（2026-09-20，PITFALLS #58）。

  `DeepSeekClient.stream_chat` 的流式解析**丢掉了 `choices[0].finish_reason`**——
  而 `finish_reason == "length"` 是"这次输出被截断"的唯一信号。于是被截断的 `tool_call`
  带着**半截 JSON** 进工具层，观测是「参数解析失败」；模型完全推不出真正的原因，
  最可能的反应是原样重试——**又截断一次**，把步数烧光。

  修法：① 收 `finish_reason` 并放进 `end` 事件；② `_one_model_turn` 里**先看它再动工具**，
  `length` 时直接发 `error(reason="output_truncated")` 并终止本轮，
  **绝不把半截参数喂给工具**（文案里还说清"没有执行任何工具"——半截参数执行出去比不执行危险得多）。

- **`tools/verify_regression_tests.py` 会在打印第一行结果时崩掉**（2026-09-20，PITFALLS #5 同族）。

  它打印 `✅` / `❌` 却没像兄弟脚本那样把 stdout 重配成 UTF-8，于是在 GBK 控制台上
  抛 `UnicodeEncodeError`。虽然 `finally` 仍会还原被改写的源码，但"跑到一半崩掉"
  会让人以为生产源码被改坏了。已补 `sys.stdout.reconfigure(encoding="utf-8")`。


- **给 `ToolSpec` 新增的声明字段会在装配时被静默丢掉**（2026-09-20，PITFALLS #56）。

  `ToolHub._validate_tools()` 为了绑上 `plugin_id`，**逐字段手写重建**了一份 `ToolSpec`。
  于是新加的字段没被列进那份清单时，插件的声明在装配后**变成默认值**——工具照常出现在清单里、
  照常能调，只有新行为不生效。实测后果：`request_user_confirmation` 的
  `needs_confirmation` 变成 `False`，那个"永远不该被执行"的 handler **真的被执行了**。

  改用 `dataclasses.replace(spec, plugin_id=plugin.id)`——`replace` 对"以后再加字段"是
  **结构免疫**的，手写清单不是。同类写法（重建不可变对象只为改一个字段）一律照此处理。

- **用户已经点了「同意」，对话循环却干等满一个超时才继续**（2026-09-20，PITFALLS #57）。

  界面在收到 `confirm_required` 那一刻就能点，而循环要等这个事件 yield 出去**之后**才走到
  `await wait_decision()`——"用户答复"几乎总是**先于**"等待者登记"到达。当时的实现是
  "等待者到了才建 `Future`"，答复无处落地被丢弃，循环新建一个永远不会被 resolve 的 future，
  于是用户明明答复了，循环卡到 `timeoutS`（默认 300 秒）才动。

  这是并发里经典的 **lost wakeup**。修法两件事一起做：**登记确认时就把接收端 `Future` 建好**
  （与 `pending_action` 行同一把锁内），**并且**把答复**存一份**（`_decisions`）供
  "答复先到、等待者后到"这一种时序取用，`wait_decision` 在 `finally` 里清掉它。
  回归用例 `test_loop_pauses_then_resumes_and_preapproval_works` 刻意在
  `confirm_required` 的**回调里**就答复——修复前它会挂到外层超时。

- **CI 首次被判红：P8 查的是"本机装没装浏览器"这条环境事实，在 runner 上永远修不好**（2026-09-18，run #17）。

  昨天给 `server.spec` 加"缺 chromium 就 `raise`"守卫时，顺手在 `verify_packaging.py` 加了
  **P8「chromium 内核可打包」**——而它的判据是**本机 `%LOCALAPPDATA%\ms-playwright` 下有没有
  `chromium*`**。CI runner 上**没有浏览器**（它也不负责打包），于是 `P8 FAIL, 11/12`，
  `Packaging static checks` 整个步骤红掉，后面三步（工具清单门禁 / ruff / …）**根本没跑**。

  **这是"门禁设计"层面的错，不是代码错**：一条在自己环境里**永远无法转绿**的断言，
  价值是负的——它把整条流水线变成噪声，真问题会被这条恒定红掩盖。

  **修法（把两件事分开，各自放在能被判定的地方）**：
  - **P8 = 静态契约**：`server.spec` 里**有没有**那道"缺 chromium 就 `raise`"的守卫。
    这才是代码正确性——守卫在，任何构建机上一个浏览器都没有也会**明确失败**。
    （已验证会抓：把 `server.spec` 里的 `raise SystemExit` 改掉，P8 立刻 FAIL。）
  - **P8b = 环境事实**：本机真的装了 chromium。**仅当这台机器被角色要求时**才断言，
    由 `build.ps1` 设 `WORKBENCH_REQUIRE_CHROMIUM=1` 声明"我是构建机"。
    CI 不设它 → 该项**恒 PASS**，于是**两种环境下项数都是稳定的 13/13**。

  > ⚠ 实现时踩了自己一个坑：P8b 最初写成 `present == expect`，本意是"期望值随环境翻转"，
  > 但语义变成了**"不要求时必须没有"** → 有浏览器的开发机反被判红。
  > 正确语义是**"按角色要求"**（不要求 = 不设限 = PASS）。四个角色场景已逐一验证：
  > CI runner / 开发机 / 构建机(有浏览器) → 13/13 PASS；构建机(无浏览器) → 12/13 FAIL。

  **推广**：**门禁只能断言"在自己环境里可判定且可通过修复解决"的事实**。
  跨环境的差异（依赖是否安装、有没有浏览器、网络通不通）要么交给声明了角色的那一侧
  （`build.ps1`），要么降级为**信息输出**——不要让它决定红绿。

- **`publish_release.py` 的出网通道改为探测（移植自 `build.ps1`）**（2026-09-18）。

  **起因是一次实测踩坑**：首次真跑发布时撞到——脚本取用顺序是
  `--no-proxy` → 环境变量 → **`git config http.proxy`** → 直连，
  而本机那份 `git config http.proxy = http://127.0.0.1:7890` 是**陈旧配置**
  （代理时通时不通，当天正处 TLS 失败状态），于是 urllib 报
  `SSL: UNEXPECTED_EOF_WHILE_READING` —— 而**同一时刻直连是通的**
  （`curl` 直连成功取到 release）。**真正的处置（换通道）根本不在错误信息里。**

  这与 `build.ps1` 修掉的 `-c http.proxy=` 是**同一个病**：把"哪条通道通"当成常量。
  当时加的 `Test-GitChannel()` 探测只修了 git 侧，Python 这条没跟上。

  **修法**：启动时打一次 `GET /rate_limit`，在〔直连 → 环境变量代理 →
  `git config http.proxy` → 常见本机代理端口（7890/7897/10809）〕里挑**实测能通**的那条，
  **之后所有 API 调用与资产上传复用它**（探测一次，不是每个请求一次）。输出形如：

  ```
    通道探测：直连 ✗（不可用，试下一条）
    通道探测：代理 http://127.0.0.1:7890 ✓
  通道：代理 http://127.0.0.1:7890
  ```

  **为什么必须真发请求**：端口在听 ≠ 能出网（实测代理进程在听但 TLS 握手 EOF）。
  判据只看"有没有拿到 HTTP 响应"，**401/403 也算通**（说明 TLS 与 HTTP 都工作）。
  **全不通时不中止**：探测只发一次 GET，可能恰好错过一次抖动；
  让真正的调用去失败并给出它自己的错误（上传另有 3 次重试），比在探测处断言"网络死了"更准。

  `--no-proxy` 的语义随之更新：从"绕过陈旧配置的必需开关"变成"**强制直连、跳过探测**"
  （旧痛点已由探测消除，但开关保留——它仍有明确用途）。

  验证（四个分支逐一实测）：
  | 场景 | 结果 |
  |---|---|
  | 直连通（当天多数时刻） | 探 **1** 次即选定直连 |
  | 直连挂、7890 通（当天 06:35 的真实状态） | 探 **2** 次后回落到代理 |
  | 全部不通 | 4 条各试一次 → 告警 → 按直连继续（**不中止**） |
  | `--no-proxy` | 候选只剩直连一条 |

  > 判据（与 PITFALLS #45 同源）：**读配置 ≠ 探现状**。凡"通道/端点/凭据来源"这类
  > 会随时间翻转的外部事实，都要在**使用时**验证一次，而不是只在配置里读一次。
  > 当天实测的翻转记录：`直连 25s 超时/代理通` → `直连通/代理 TLS 失败`
  > → `直连被重置/代理通` —— **四次里翻转三次**，写死任何一侧都会有一半机会失败。

- **撤销误发的 `v0.6.1`：版本号回到 `0.3` 线段**（2026-09-18，用户定稿）。

  起因是 `tools/build.ps1` 的 `-Bump` **默认 `build`（MINOR+1）**，而版本演进排在**第 1 步**：
  任何后续失败都已经吃掉了版本号。实测连续两次失败（`uv sync`）后，
  版本号从 **0.3.12 一路跳到 0.6.1** —— `0.4.x` / `0.5.x` 从未存在过，
  用户也从未打算发这些版本。

  **处置**：版本号回到 **`0.3.12`**（它**从未发布过**——无 tag、无 Release、无安装包，
  见 [VERSIONING.md](docs/VERSIONING.md) §3.3 的带 token 复核结论，故可安全重用）；
  **删除**误发的 GitHub Release `v0.6.1` 与本地/远端 tag。

  **这次是首次真跑通 `publish_release.py`，顺带留下两条实测事实**（与撤稿无关，值得留）：

  1. **发布说明一开始是空的**：`notes_from_changelog()` 逐字查找 `## [Unreleased]`，
     而本仓正文写的是中文「未发布」→ 它静默 `return ""`、**不报错**，说明就那样空着发出去了。
     **是靠 `--dry-run` 打出"说明长度：0 字符"才发现的** —— 又一条"看起来成功"的静默链
     （PITFALLS #47 同族）。修法与"为什么标题与内容之间不能插任何东西"见本文件顶部注释。
  2. **匿名校验必然失败（本仓是私有仓库）**：脚本末尾"校验 latest.yml 可匿名读取"报
     `HTTP 404`，这是**预期**而非故障——GitHub 对私有仓库的未认证请求一律回 404。
     由此确认一条**既有限制**：`electron-updater` 是**匿名**读取 Release 的
     （`desktop/updater.js`），故**私有仓库下自动更新静默失效**。
     当前无实际影响——`AUTO_UPDATE` 本就默认关闭（用户定稿 2026-09-15），安装包靠手工下载。
     **要开自动更新，前提是仓库转为公开。**

  > **教训（值得单独记）**：版本号是**不可回收的资产**，而"默认帮用户升版"听起来贴心，
  > 实际是在**替用户做不可逆决策**。默认值应当选"什么都不改"，把不可逆的选择留给显式参数。
  > 配套的结构修正已落在 `build.ps1`：默认 `-Bump none`（升版必须显式写）、
  > bump 降到第 3 步（前两步 `uv sync` / 静态体检的失败不再消耗版本号）、
  > 新增第 7 步打包后复检（bump 改过版本文件后确认产物仍自洽）。
  > 现在失败构建**原样重跑即可**——默认 `none` 是幂等的，不会再吃号。


- **P0-A：危险工具在对话链路里**永远无法执行**（2026-09-20，实测复现后修复）**。

  **症状**（用真实 `ToolHub` + `PendingStore` + `run_agent_loop` 走完整流程）：

  ```
  轮1 被拦（pendingId=A）→ 人放行 → 轮2 让模型重试同一次调用
        → **仍被拦，且新建了 pendingId=B** → 工具从未执行
  ```

  ⇒ `accounts_browser_open` / `accounts_browser_close` / `factory_create`
  （**全部 3 个写 / 危险工具**）在对话里不可用。

  **根因**（三处合起来才成立）：① 确认门判据是 `ctx.approval_token`；
  ② 全仓**唯一**的令牌赋值点在 HTTP 链路（`routes_agent.invoke`），
  而 `harness/loop.py` 构造 `ToolContext` 时**不传**它；③ `PendingStore.request()`
  只复用 `status == "pending"` 的条目，而被批准的那条已是 `"approved"` → 匹配不到 → 新建。

  **三方说法互相矛盾**（这是它能潜伏至今的原因）：`routes_agent.py` 的文档串说
  「模型重试同一次调用（**原样带 `approvalToken`**）」、`plugin_api` 回给模型的话说
  「等界面放行后再重试同一次调用」、而前端 `chat-tools.js` 说「**刻意不自动重发**……
  请在输入框里让模型继续或重试」。**三句话各自都像对的，合起来走不通。**

  **修法**：`PendingStore.claim_approved()` —— 无令牌时按「**工具 + 参数指纹**」认领一条
  **已批准**的条目，**一次性**（认领后置 `consumed`）。门槛与令牌**完全一致**
  （同工具、同参数、5 分钟窗口、只用一次），只是"凭据"从令牌换成了服务端内存里的事实——
  与 `approvals` 既有的「**权威状态在内存、DB 只留痕**」完全一致。
  同时**统一了三处说法**，并补了回归 `tests/test_agent_approval.py`（8 项）。

  > 顺带修掉一个既有缺陷：`request()` 原用 `_fingerprint(item.tool, item.args)` 现算，
  > 而 `item.args` 是 `redact_args()` **之后**的副本 —— 参数名命中 `_REDACT_KEYS`
  > （如 `token`）时两边指纹不同，"同一工具同一参数复用"会**静默失效**、每次新建一条 pending。
  > 现在指纹在申请时算一次并存下来（`PendingAction.fingerprint`）。

- **P0-B：只读工具能**完整读出**cookie / 平台 JWT / 含 Fernet 密钥的账号备份**（2026-09-20，实测复现后修复）**。

  **症状**（临时 `WORKBENCH_DATA` + 真实形态的产物，实测）：

  ```
  runtime/browser-states/acct-1.json    ❌ 放行，含 cookie + localStorage token
  runtime/egmp-tokens/token-env1.json   ❌ 放行，**完整吐出**，含平台 JWT
  runtime/accounts-backup/accounts.json ❌ 放行，**完整吐出**，含 Fernet 密钥 + 密文口令

  对照：同样三个路径，沙箱 is_sensitive_path = True / True / True
  ```

  而 `read_local_file` 是 `risk=read`、**不过确认门**。主库更直接：
  handler 返回 **1,056,412 字符**（本次密钥落在 `MAX_RESULT_CHARS` 截断之外，
  **但那是运气不是控制**——那个上限是**上下文预算**手段，不是保密手段）。

  **根因**：仓里**两张敏感路径表**——沙箱那张含 `browser-states` / `egmp-tokens` /
  `workbench.db` / `accounts.json`，而 `plugins/builtins.py` 的 `_BLOCKED_PARTS`
  **一个都不含**（只有 Chrome/Firefox profile 那类**应用之外**的路径）。
  再加上 `_guard_path()` 的顺序是「先查自己那张残表 → 再 `is_inside_app` 就 `return`」，
  而 `is_inside_app()` 的根包含 `DATA_DIR` 与 `RUNTIME_DIR` —— **凭据恰好都在那里**，
  且 `return` 之后连"文本后缀 + 2MB 上限"两道也不生效。

  **与 2026-09-15「两套工具表并存」是同一类错误**：两份会各自漂移的事实源。

  **修法**：① 表收归 **`config.SENSITIVE_PATH_PARTS`**（唯一事实源，取两边并集），
  `sandbox/policy.py` 只做再导出、`_BLOCKED_PARTS` 删除；
  ② `_guard_path()` 把敏感表判定放到 `is_inside_app` **之前**（**与位置无关**）；
  ③ 工具描述从"应用目录内的文件任意读"改成实际边界
  （原文读起来像受限，实际是"应用目录内**完全不受限**"）。
  回归 `tests/test_sensitive_paths.py`（含"收紧不能把正常用途一起打死"的反向断言）。

- **开发态启动路径没有启动横幅**（2026-09-20，用户问"日常怎么测、要不要编译"时实测发现）。

  桌面壳的**开发态**是直接 spawn `python -m uvicorn workbench.api:app`
  （`desktop/main.js::spawnServer` 的开发态回退分支），**不经过任何入口脚本**。
  而启动横幅原先只在 `main.py` / `server_entry.py` 里记 → **日常开发测试时日志里没有
  "跑的是哪一版、在哪些路径上"**（版本/pid/frozen/端口/数据目录/日志路径全缺）。

  **修法**：把横幅移到 `api.lifespan` —— 它是**所有启动路径的唯一交集**
  （生产入口、裸 uvicorn、测试夹具都经过）。同时把工具自检从横幅里**摘出去**
  （那是 `_tool_selfcheck()` 的职责），否则"工具中枢：N 个插件 / M 个工具"会记两遍——
  日志里的重复不是小事，它让"这条是什么时候记的"变得难判断。
  新增端到端检查 **H1–H4**（`tools/verify_logging.py`，总计 **36/36**），
  其中 H2 在修复前**必然 FAIL**。

- **`routes_agent` 的 HTTP 路径未知工具没有工具级留痕**（2026-09-20，端到端检查抓到）。
  `invoke` 在**路由层就 404 短路了**，所以 `ToolHub.execute()` 里那条"未知工具调用"告警
  根本不执行 → 两条链路对同一事件的口径不一致（对话链路有，HTTP 链路只有一条 404 访问日志）。
  **修法是路由层也记一条**，并补了走 `TestClient` 的单元断言。

- **壳被强杀时 sidecar 变孤儿、占着固定端口**（2026-09-20，实测复现后根治，PITFALLS #54）。

  **症状**：桌面壳被强杀（任务管理器结束 / 崩溃）后 `python.exe` 继续运行并占着 18765
  → 下次启动 `portPreflight` 立刻失败，弹窗却报「**占用者不是 Workbench 实例**」
  （因为 `portBroker.clearStale()` 清掉了公布文件，`prev` 变 null）——
  **明明是我们自己的孤儿，却认不出来**，只能手工清理。

  **根因**：Electron 的 `child_process.spawn` 在 Windows 下**不使用 Job Object**；
  清理只挂在 `before-quit → killServer()` 上，而**强杀路径根本不触发 `before-quit`**。

  **实测**（`subprocess.Popen` 精确模拟 Electron 的 spawn）：
  对父执行 `taskkill /F`（**刻意不带 `/T`**）→ 3 秒后 sidecar 仍在、端口仍在 LISTEN。

  **修法**：新增 `workbench/lifecycle.py::watch_parent()` —— 壳 spawn 时下发
  `WORKBENCH_PARENT_PID`，sidecar 起守护线程 `OpenProcess(SYNCHRONIZE)` +
  `WaitForSingleObject(INFINITE)`，父一死立刻自退。**选句柄等待而不是轮询 pid**：
  句柄会**钉住那个进程对象**，因此不存在"pid 被复用、误等成别人"的问题（轮询方案最难处理的一点）。
  三条防自杀约束：未设变量不启用（Web 模式）；拿不到句柄一律**不杀自己**
  （只有 `ERROR_INVALID_PARAMETER` 才立即退出）；幂等只启动一次。
  装在 `api.lifespan` —— **所有启动路径的唯一交集**（与"启动横幅"同一个理由）。

  回归 `tests/test_lifecycle.py`：快层 11 项验"什么时候**不**启用"，slow 层 1 项真起父子进程
  验"父死子退"（**该断言在无监视的实现上 27.8 秒后变红**，反证见 `verify_regression_tests.py`）。

- **菜单标签在说谎：「关闭窗口（应用继续在托盘运行）」，而关窗其实会退出应用**（2026-09-20，PITFALLS #55）。

  `desktop/main.js` 里**没有 `mainWindow.on('close')` 拦截器**——只有 `on('closed')`
  （只把引用置空）。真实链路是 关窗 → `closed` → `window-all-closed` → `app.quit()`
  → `before-quit` → `killServer()` → **应用与 sidecar 一起退出**。
  于是托盘菜单的「显示主窗」在关窗之后**永远点不出窗口**（应用已经没了）。

  **处置**（用户定稿）：**统一成"关闭 = 退出"**，标签改为「关闭窗口（应用退出）」，
  并在代码旁写清"要做托盘常驻必须**同时**加 `close` 拦截 + `isQuitting` 标志"。
  同时把托盘「显示主窗」补上 `isMinimized() → restore()` ——
  **`show()` 不会取消最小化**，而定"关闭=退出"之后托盘最主要的用途恰恰是"把最小化的窗口捞回来"，
  不补这条那个菜单等于废的。判据：`Select-String -Path desktop/main.js -Pattern "on\('close'"`（零命中即它）。

- **`PITFALLS.md` 的 `#44` 被误删，已恢复**（2026-09-20 发现）。

  该行在 `be3b59f` 加入，被紧接着的 `d10272e`（一次"文档同步"提交）**整行删除**，
  而**它记录的修复至今仍在代码里**（`browser_pool.chromium_executable_path()` 走 async API、
  `test_warmup_check_leaves_no_asyncio_noise` 仍在跑）——**成果还在、机构记忆没了**，
  正是该文件 §维护规则 第 1 条所禁止的。已恢复原行并在末尾标注了这次误删。

  顺带暴露一个**结构性缺口**：本仓对"文档里的数字"（工具数/插件数/迁移号）有
  `gen_tools_doc.py` 机检，但**坑清单的编号连续性没有任何机检**——少一行没人会发现。
  已在 §维护规则 里补上判据（一行 Python，断言缺号列表为空）。

## [0.3.12] - 2026-09-18

> **本版范围说明**：这是自最后一个**已发布**版本 `0.3.4`（2026-09-11）以来首次对外发布，
> 故本节的条目**跨 0.3.5 → 0.3.12**，不是单次迭代的内容
> （背景：`bump.py` 在本地演进过 12 次版本号，但只有 2 次真正走完发布，
> 见 [VERSIONING.md](docs/VERSIONING.md) §3.3 的带 token 复核结论）。
> 另有两条关于**误发版本 `v0.6.1` 与撤稿**的说明，见 `[Unreleased]` 一节。

### Fixed

### Security

> **提交归属说明（2026-09-17，如实记录）**：本批改动按主题拆了 6 个提交，
> 但**扩展侧的 P0-2 改动（`pending-login.ts` / `service-worker.ts` / `parallel-session.ts`）
> 被一并卷进了 P0-1 的提交**（当时按目录 `git add extensions/.../src` 暂存，
> 而这两个主题都改了同一个目录）。后果是 `security(extension): /extension/* 加访问令牌`
> 那个提交里同时含 P0-1 与 P0-2 的代码改动，而紧随其后
> `security(extension)!: 待登录凭据改为一次性宿主校验（P0-2）` 实际只含测试文件。
> **代码内容与验证都是完整的**，只是 `git log` 的归因不准。
> 不重写历史（本仓 2026-09-14 已做过一次历史重写并留下
> `docs/HISTORY-REWRITE-NOTICE.md`（**该文件已于 2026-09-29 删除**）；再改一次会让协作者的本地副本
> 又要做一次迁移，代价大于归因不准本身）。**教训**：按主题拆提交时，
> `git add` 要落到**文件**而不是目录——目录会把"同目录下的另一个主题"一起吞掉。

- **`/extension/*` 无鉴权：本机任意进程一次 GET 拿到全部账号明文（P0-1 / R1）**
  （2026-09-17）。


  `GET /extension/snapshot` 会**同时**下发 `fernetKey`（密钥本体）与每个账号的 `passwordEnc`——
  密钥与密文一起给出去，**等于明文口令**。而整条 `/extension/*` 此前**没有任何鉴权**，
  唯一屏障是"服务只监听 `127.0.0.1`"。于是本机**任何**代码都能敲门：
  用户浏览器里的任意网页（读不到响应但**能注入指令**——`POST /extension/commands` 可触发
  扩展去打开页签并自动填口令）、任意本机进程、别的应用里的 WebView。

  **这条在台账里挂了很久没修，原因是它没有症状**：功能一切正常，注入的指令也能跑。

  **修法**：整段前缀由 `workbench/api/extension_auth.py` 的 **ASGI 中间件**统一要求
  `X-Workbench-Token`。

  | 决策 | 内容 |
  |---|---|
  | **按前缀守，不逐路由 `Depends`** | 逐路由漏加一条就静默开一个口子；按前缀守让"新增路由自动被守住"成为**结构保证** |
  | **令牌落库** | `settings.extension_access_token`（`secrets.token_hex(32)`），**跨重启稳定**——每次重启换令牌会让 Chrome 里的扩展狂刷 401，看起来像"扩展坏了" |
  | **自举端点** | `POST /extension/hello` **免令牌**（否则死锁），**只回** `{format, version, token, desktopVersion, header}` |
  | **宽限期 24h** | 令牌是新协议要求，已装的**旧扩展不会带它**；硬失败 = "快捷登录静默失灵"。窗口起点**落库且只记一次**（放内存态会被每次重启重置 = 永久开窗）。`WORKBENCH_EXTENSION_AUTH_GRACE=0` = 严格模式 |
  | **豁免面只有两条** | `/extension/hello`（自举）与 `/extension/health`（只读徽标判据），由测试与 **P10** 机检双重钉住 |

  **为什么不用"校验 `Origin`"代替令牌**：扩展 service worker 发跨源 `fetch` 时是否带 `Origin`，
  取决于浏览器实现与请求模式，**本机无法实证**——不能把安全建立在没验证过的浏览器行为上。
  来源允许清单实现为**可选**的第二层（`WORKBENCH_EXTENSION_ORIGIN`），主防线是令牌。

  **这一层挡不住什么（如实记录）**：能读 `%APPDATA%\Workbench\workbench.db` 的本机进程
  **总能**拿到令牌（它就在同一张 `settings` 表里，与 Fernet 密钥同处）。
  令牌把攻击面从"任何能发 HTTP 的东西"收窄到"愿意读我们数据库或调自举端点的进程"，
  **消灭的是"零知识的一次 GET"，不是"本机已被攻陷"**。完整分层表见
  `extension_auth.py` 的模块文档串。

- **加守备时发现的冲突：桌面 UI 自己也在调 `/extension/*`**
  （PITFALLS #48）。

  账号中心的 `POST /extension/commands`（Alt+Q 轮盘选人）、`POST /extension/launch-chrome`、
  `POST /extension/setup-helper`、`GET /extension/state` 全在这个前缀下，而**令牌只发给扩展**——
  UI 拿不到也不该拿到（页面里放令牌等于交给任何能读 DOM 的东西）。
  给它们开豁免的话，"整个前缀需要令牌"就退化成一份人工维护的清单。

  **按"碰不碰凭据"重划边界**：`/extension/*` 只留"扩展 ↔ 桌面"的数据与指令通道；
  三条"给 UI 用、不碰凭据"的端点搬到 **`/api/shell/*`**（新模块 `routes_shell.py`）。
  于是"这个前缀整段需要令牌"才真的成立、且可机检（`verify_packaging.py` 新增 **P10**）。
  前端两处路径已同步（`accounts.js` / `wheel-picker.html`）。

- **防静默故障：401 必须可诊断，且"没重新加载扩展"要能被看见**（PITFALLS #49）。

  宽限期结束后，没重新加载扩展的用户会开始收 401——而扩展侧对失败的纪律是**静默吞掉**
  （`sync.ts` 的离线回退：桌面不可达是常态）→ 用户只会看到"快捷登录不灵了"，
  没有任何线索指向"去点一次『重新加载』"。两条对策：
  - **401 带机器可读 `code: "extension_auth_required"`**：扩展端据此"作废令牌 → 重新握手 →
    **重试一次**"（新增 `background/core/desktop-auth.ts::authFetch`，`sync.ts` 全部请求改走它）。
    只回 HTTP 语义的话，401 会被当普通网络错误吞掉；
  - **`/extension/health` 新增 `authBlocked` / `tokenlessRequests`**：中间件把"本该带令牌的端点
    被无令牌访问"记数（= Chrome 里那份扩展还没重新加载），账号中心复用既有的
    「扩展版本过旧」提示条引导用户重新加载。
    ⚠ 豁免路径**不计入**——`/extension/health` 是 UI 每 3s 打一次的徽标判据，
    算进去会让提示条被 UI 自己的轮询永久点亮（实测踩到，已写成回归用例）。

  **兼容矩阵**：新桌面+新扩展 = 自举令牌；新桌面+旧扩展 = 宽限期内可用、过后 401 且 UI 提示；
  旧桌面+新扩展 = `/extension/hello` 返回 404/405 → 视为"不需要令牌"，照常发请求；
  桌面不可达 = 离线回退（既有纪律）。

### Fixed

- **明文口令可被投递到"已导航走的绑定页签"：读后不删 + 不校验索取方 + 解绑不清（P0-2）**
  （2026-09-17，逐条对码确认，CODEBASE_OVERVIEW R2）。

  **三个条件同时成立，构成一条真实可利用的凭据泄漏路径**，而它**没有任何报错、没有任何日志**：
  1. `pending-login.ts` 的 `getPendingAutoLogin` **读后不删** → 60s TTL 内可**重复取**；
  2. `service-worker.ts` 只取 `sender.tab?.id`，**不校验发送方 URL、不校验绑定关系**；
  3. `parallel-session.ts::unbindTab` **不清**该条目。

  而 `manifest.json` 是 `host_permissions: ["<all_urls>"]` + 4 个内容脚本全 `all_frames: true`——
  于是绑定页签在 60s 内导航到**任意站点**（或该站点的任意 iframe 索取），
  就能拿到该账号**明文口令**并打进其 DOM。

  **修法**（三处一起收口，`pending-login.ts` 成为唯一真源）：
  - `getPendingAutoLogin` → **`claimPendingAutoLogin(tabId, url)`**：**先删后判**，
    任何早退路径都不会留下可复取的条目（"一次性"是结构保证，不是调用约定）；
  - pending 条目**自带 `host`**（下发时由 `parallel-session.open()` 传入 `account.siteHost`），
    索取方 `sender.url` 必须与它 `hostRelated()`——**含端口身份口径**，与页签绑定/DNR 同一把尺子，
    同主机不同端口视为不同站点；
  - `unbindTab` 同时 `clearPendingAutoLogin(tabId)`（解绑后该页签导航去向不可控）。

  > ⚠️ **刻意不按"只信顶层 frame（`sender.frameId === 0`）"收口**：实测 `auto-login.ts` 的
  > **子 frame 也会各自索取并兜底填表**（模块末尾无条件 `void requestCredentials()`，
  > `runIframeFlow` 填自身 frame 内的密码框，**跨域 SSO 登录框正靠这条路径**）——
  > 按 frame 收口会把合法的跨域登录 iframe 一起打死，**必须按宿主身份收口**。

  验证：`npm run typecheck` + `npm run build` 通过。**`manifest` 权限面未动**（仍是 `<all_urls>`，即 R3 仍开放）。

- **发版链路会在"不报错"的情况下产出坏安装包（P0-3）**（2026-09-17，逐条实测确认）。

  这一批的共同形态是**"看起来成功"**——每一步单看都无害，合起来把"发布"变成赌博：

  | 缺陷 | 后果 |
  |---|---|
  | `server.spec` 是 `if ms_pw.exists():`（无 else 无报错） | 缺 chromium 的机器打出**没有浏览器**的"成功"安装包，只在客户运行期暴露 |
  | `build.ps1` 的推送写死 `git -c http.proxy=`（**强制直连**） | 本机"直连被重置、只有系统代理通"的时段 → **必然三次全败并仅打印一行继续** |
  | `build.ps1` 不查 `bump.py` 的退出码、不校验格式 | `bump.py` 的错误文本（它在 **stdout**）被当成版本号，一路流到产物命名 |
  | `verify_packaging.py` **从未被 `build.ps1` 调用**（只在 CI 跑） | 本地发版可绕过全部静态核对 |
  | `git commit` 的 stderr 被 `2>$null` 吞掉 | "git 身份没配"被伪装成"无改动可提交" → 随后静默跳过打 tag |

  **修法**（每一环都给显式失败出口）：
  - **`bump.py` 的 stdout 是机器接口**：成功路径只输出版本号一行，一切诊断/错误走 **stderr**（`_die()` 统一）；
    新增 **`bump.py check`**（只读核对五写同步，不同步 exit 1）；
  - `build.ps1` 第 1 步校验 `$LASTEXITCODE` + `^\d+\.\d+\.\d+$` 格式 + 跑 `bump.py check`；
  - `build.ps1` 新增**第 4 步**接入 `verify_packaging.py`（放在提交与打包**之前**，返工最便宜）；
  - `server.spec` 缺 chromium 直接 `raise SystemExit`（带 `playwright install chromium` 的处置），
    与 `verify_packaging.py` 新增的 **P8** 构成**双保险**；
  - **推送通道改为探测**（`Get-PushProbeArgs` / `Test-GitChannel`）：直连与代理各试一次 `ls-remote`，
    用实测能通的那条；**探测全败或推送失败即中止构建**；
  - commit 的 stderr 保留，并把"无改动"与"真失败"分开（后者 `exit 1`）。

  `verify_packaging.py` 另新增 **P9**（版本五写同步，复用 `bump.py check`，不另写一份正则）——
  体检项从 9 项增至 **11 项**。见 PITFALLS #45 / #47。

  > **顺带**：编辑 `build.ps1` 会丢 UTF-8 BOM（本轮实测丢了两次），P5 立刻报红。
  > 这正是 P5 存在的理由——**改完 `build.ps1` 必须重跑体检**（PITFALLS #37）。

- **C1 与 C4：两处"恒假分支"级的确定性缺陷**（2026-09-17，均为本次对码确认仍在）。

  - **C1（`browser_pool._close_entry`）**：先 `entry.status = "stopped"`，**再**判断
    `entry.status == "online"` → 该分支**恒假**，于是"关窗时登录态已落盘"这件事**永远传不到 UI**
    （用户只看到含糊的「已关闭」）。改为关窗前先取 `was_online`，再用它决定 `detail`。
  - **C4（`routes_extension` 的指令队列永不排空）**：回收条件是"队列内**每一条**都在已 ack 集合里"，
    而扩展端（`sync.ts:241-243`）**只上报最大 cursor**（`{"seqs": [cursor]}`）→
    `_commands.clear()` **恒不执行**：队列与 `_acked` 只增不减，每次 `/commands` 都要重扫全部历史指令。
    改为**水位线** `_ack_watermark = max(acked)`，按 `seq <= watermark` 回收、队列空则归零。
    语义依据：客户端 cursor 是**单调推进**的（`Math.max`），达到某个 seq 即意味着它及之前都已处理过。

  **两条回归都实测过"对旧实现 FAIL"**：
  `test_close_entry_reports_saved_state_when_session_was_online`、
  `test_ack_watermark_drains_queue_even_when_client_acks_a_prefix`。

- **最该被自动化的两个模块恰恰最没被自动化（P0-4）**（2026-09-17）。

  `routes_extension.py` 是**桌面 ↔ 扩展**的唯一数据与指令通道，同时是
  **凭据下发口（快照里带 Fernet 密钥）**、指令队列宿主、以及"扩展是否需要重新加载"的判定源——
  却**没有任何 pytest 覆盖**（全仓仅 3 处测试提及），唯一回归 `node tools/verify_extension_sync.mjs`
  还要起真实服务读真实账号库，**进不了 CI**。上面 C4 能在仓库里躺这么久，根因就是这条。

  新增 `tests/test_extension_blueprint.py`（**12 项**）：快照契约（字段名逐条钉住——
  `passwordEnc` 驼峰写错会让扩展**静默丢弃全部账号**，0.2.3 实锤断点）、`snapshotId` 的内容哈希语义、
  游标过滤、**序号跨重启单调递增**、ack 水位线（C4 回归）、`/health` 四态与 `extStale`、`/state` 的 TTL。
  另有一条**记录 R1 现状**的断言（`/extension/*` 无鉴权，注释写明"**加鉴权时必须让它失败**"）。

  另为 C1 补 `tests/test_browser_state.py` 的 4 项（含"落盘必须发生在 `context = None` 之前"）。
  测试总数 **501 项**（3.12 口径为 493 项——差的 8 项是 slow 层，CI 分两步显式跑）。

- **开发机 3.14 / CI 3.12 的假信号（P0-5）**（2026-09-17）。

  仓库**没有 `.python-version`**：本机主力 `.venv` 是 conda 的 **3.14.6**，而 CI 钉 **3.12**。
  两边**跳过项与语义都不同**，于是"本地全绿"推不出"CI 全绿"——CHANGELOG 上一条记的
  "CI 从建立那天起就没绿过"那起事故，正是被这个差集掩盖了**九次运行**。
  3.14 起 `from __future__ import annotations` 的注解不再于运行时求值，
  所以"注解里写了未导入的名字"这类错误**只在 3.12 上炸**（`routes_insight.py` 的 `Any` 就是这么潜藏的）。

  **本轮实测差集**（同一份代码、同一台机器）：
  | 解释器 | 命令 | 结果 |
  |---|---|---|
  | `.venv`（3.14.6） | `pytest -o addopts=""`（含 slow 层） | **501 passed, 0 skipped** |
  | `.venv312`（3.12.14） | `pytest -o addopts=""`（默认 `-m 'not slow'`） | **493 passed, 8 skipped** |

  **修法**：新增 **`.python-version`（= `3.12`）作为唯一真源** +
  `tests/test_python_version_contract.py`（断言它与 `.github/workflows/ci.yml` 的 `python-version`
  **逐字一致**、必须是具体版本而非范围表达式、且未被 gitignore）+ CI 新增
  `Interpreter version contract` 步骤（**打印实际解释器版本**并跑该文件）。
  CONTRIBUTING §1.1 补上本地复现 CI 的完整命令与**"那 8 项 skip 的根因是 Playwright 版本而非解释器"**的说明。

  > **不**断言"当前解释器必须是 3.12"：3.14 是**合法**的开发环境（`requires-python = ">=3.12"`），
  > 判红会惩罚合法环境。被禁止的是"两边版本不同步却以为结论一样"。

### Added

- **CI 从建立那天起就没绿过：4 项 P0 浏览器验收在无 chromium 的 runner 上"失败"而非"跳过"**
  （2026-09-17，用户发来 CI 截图后定位）。

  **两层原因，都不是它看起来的样子**：
  1. 截图里的 `KeyboardInterrupt` + `Error: The operation was canceled.` 是
     `ci.yml` 的 `concurrency: cancel-in-progress: true` 把**上一次 push 的运行顶掉**了——
     **不是测试失败**。同一 ref 只留最新一次运行是刻意的（旧提交的结论没有价值），
     现在把这条写进工作流头部的"设计取舍"里，免得下次再被这个显示误导。
  2. 但**失败是真失败**：`tests/test_browser_parallel.py` 的模块 docstring 写着
     "需要 chromium，否则整组 skip"——**这句承诺只在注释里**，代码里没有任何守卫。
     于是这 4 项 P0 验收（多账号并行托管、自愈重登、免密直达）在任何没装浏览器的机器上
     表现为 failed。`test_autologin.py` 有守卫所以它 skip，这个文件没有所以它红。

  **定位手段**（顺带订正一条旧结论）：`GET /repos/…/actions/runs` 用本机 `git credential fill`
  取到的 token **能读**（`conclusion` / `head_sha` / 每个 step 的结论都能读），
  只有 job 日志是 401。一眼就看到**九次运行没有一次 success**——问题不在最近这次改动，
  而在工作流建立之初。`docs/VERSIONING.md` §7.2 原来记的"Actions 结论无法在本环境验证"已订正为
  **"结论可读、日志不可读"**。

  **修法**：
  - `test_browser_parallel.py` 补 `chromium_ready` 守卫，复用既有的**生产**体检入口
    `browser_pool.warmup_check()`——不自己写一份（打包态浏览器路径只有那一个单点实现，
    自己写必然在打包态误报，PITFALLS #28）；
  - CI 的 pytest 命令加 **`-rs`**：**绿灯不等于全跑了**，一项 skip 与一项 pass
    在界面上只差一行小字，不加 `-rs` 门禁会悄悄退化；
  - 开发机是 **3.14 而 CI 是 3.12**，用 `uv venv --python 3.12` 复现 CI 环境后
    `472 passed, 8 skipped`（真绿），耗时也从 71s 降到 19s。

- **体检探测往日志里灌 asyncio 噪声：`sync_playwright()` 起停会留下 pending task**
  （PITFALLS #44）。

  CI 日志（与用户截图）里那两行 `Task was destroyed but it is pending!` +
  `TargetClosedError: … has been closed`（never retrieved）**看着像测试崩了，其实测试全过**。
  根因是 Playwright 的 **sync API** 每次起停都留下一个 pending connection task，
  进程 GC/退出时才被销毁 → asyncio 处理器把它打出来；**只要起停就会留，不需要 launch**
  （实测：单独跑一句 `warmup_check()` 就会打；换成 async API 就干净）。

  之所以真要修：`warmup_check()` 是**体检**入口，`/api/modules`（模块体检页）与
  `/api/browser/check`（账号中心）会**反复调用**它——等于往服务端日志里持续灌噪声，
  而噪声会掩盖真错误。改动：
  - `browser_pool.chromium_executable_path()` 新增，改用 **async API** 探测，
    并跑在**独立线程**里（sync 端点与将来的 async 端点调用都安全；否则 `asyncio.run()`
    在事件循环里会抛错并被 `except` 吞成"检测失败"——正是最忌讳的静默降级）；
  - `modules._check_playwright_browser()` 改为复用这**同一个**实现（原先两处各写一份 sync 探测）；
  - `test_autologin` 的夹具改为"先无副作用体检，再决定要不要起 sync Playwright"，跳过路径不再留噪声；
  - 回归 `test_warmup_check_leaves_no_asyncio_noise`：**在子进程里**断言 stderr 干净
    （那两行在解释器退出阶段才打印，同进程断言看不见）。已实测该用例对旧实现 FAIL、对新实现 PASS。

### Added

> ⚠️ **本节的端口协商/顺延条目已于同日被撤回**——见下方 `### Changed` 的
> 条目。保留原文是为了留痕；
> **现行行为是端口固定、不顺延**，读本条时勿把"顺延"当成现状。
>

- **脚本式 Skill：上传的脚本在沙箱里跑起来**（2026-09-17，同日接在"共享库 + 声明式工具"之后）。

  用户定的路线是「先建沙箱，再接入 skill」——沙箱底座与声明式链路都已就绪，
  本条补上**执行上传代码**这最后一段：manifest 的工具有了第二种形态 `script`。

  ```yaml
  tools:
    - name: demo_double
      description: 把数字加倍
      script: scripts/double.py     # ← 在沙箱里执行包内 Python 脚本
      args:                         # 脚本式**必填**（没有"被包装工具的 schema"可继承）
        n: {type: integer, description: 要加倍的整数}
  ```

  **入口约定**（`workbench/skills/script_runner.py`）：stdin 收一个 JSON 对象（模型的工具入参，
  原样传）→ stdout 出一个 JSON 对象（`{"ok":true,"result":…}` 或 `{"ok":false,"error":"…"}`）
  → stderr 只进日志 → **退出码优先于 stdout**。
  用 stdin/stdout 而不是命令行参数，是因为**不经过 shell**：参数里带引号/换行/中文时，
  命令行拼接是最经典的注入面。这套约定与 MCP 的 stdio 传输同构，将来接 MCP 可直接复用。

  **沙箱策略不由 Skill 选**：固定走 `skill-script`（不联网、不派生进程、目录限在沙箱根内），
  上限 120s。让上传件挑策略等于**让它给自己发权限**。
  执行前整包复制进一次性沙箱运行目录，**库目录保持只读的权威副本**——
  否则一次执行就能改掉"下次安装会得到什么"的答案。

  **脚本式工具的风险不得低于 `write`**（`library._build_plugin`，只升不降）：
  上传的代码**无法被静态证明**只读，标成 `read` 等于"模型可以不经确认随便跑"。
  声明式可以 `read`，因为被包装工具的语义是我们自己定的。

  **manifest 层面的两条硬约束**：`wraps` 与 `script` **二选一**（"先转发、失败再跑脚本"的
  回退语义会让失败路径不可预测）；声明的 `script` **必须真的在包里**——上传期就查，
  不等装完调用时才报"文件不存在"。

  **路径校验分两层**（踩过才改的，见 PITFALLS #40）：形状正则判字符，
  `validate_script_path()` 再逐段拒 `""` / `.` / `..`。初版只有正则，
  于是注释里"不得含 `..`"这句承诺是**空的**（`.` 是合法字符，`a/../../b.py` 照样通过）。
  同时把 `/abs/x.py` 从"静默改写成 `abs/x.py` 放行"改成**直接拒**。

  **本轮抓到并修掉三个真 bug**（都是"看起来装好了、其实跑不了"那一类）：
  1. `upload()` **从没解压过包**，只落了 `SKILL.md`/`manifest.json`/`source.zip`
     → 库目录里根本没有脚本文件，脚本式工具**装得上、一调用必然失败**。
     初版 32 项 Skill 测试全绿，因为没有一项真的调用过工具（PITFALLS #39）。
     现在解压整包进库目录：**库是可执行的权威副本**，`source.zip` 仅留作复核原件；
     解压走暂存目录 + `rename` 整体换入，`manifest.json` **最后**写作为"完整落地"的标志。
  2. `script_runner.find_python()` 用了 `os.environ` 却**没 import os** →
     一配 `WORKBENCH_SKILL_PYTHON` 就 `NameError`。默认路径不碰它，所以本机全绿；
     而这个分支的设计意图恰恰是"**打包态必须显式配置**"，即只在客户现场才会走（PITFALLS #41）。
  3. 版本枚举原本在 `list_library` / `install` 各写一份，解压暂存目录会被当成一个版本。
     收成 `layout.library_versions()` 一个入口（跳过点开头目录）。

  **`/api/skills/summary` 的边界文案同步改写**：原文写着"本期只支持声明式 Skill，
  因此**安装 Skill 不会执行任何上传的代码**"——接入脚本式之后**这句是错的**，
  留着就是虚假安全声明。现在是：沙箱是真实强制层（凭据不注入、目录限在沙箱根内、
  进程树可终止），但**不是内核级隔离**，它降的是风险，不是"跑恶意脚本也安全"的保证。
  库列表也新增 `script_tools` 字段，界面用「含脚本 N」标出**哪些工具会执行上传的代码**。

  **测试**：`tests/test_skill_scripts.py` 31 项 —— manifest 契约与互斥、脚本必须在包内、
  路径两层校验、风险下限、结果翻译的 Fail-Closed（空 stdout / 非 JSON / 缺 `ok` /
  非零退出码压过合法 stdout / 超时带进程树终止手段 / 结果截断显式告知）、
  **真的起子进程**跑通的端到端用例（含"脚本拿不到宿主凭据"与"死循环被超时杀掉"），
  以及一条**走真实 HTTP 的全程用例**（multipart 上传 → 安装 → 工具可见 → 409 确认门
  → 批准 → 重投 → **真的跑出 `{"doubled": 42}`** → 查沙箱审计 → 卸载 ≠ 删除 → 从库删除）。

  > **这条 HTTP 端到端用例是本轮最有价值的一条**：它当场逼出了第四个真 bug——
  > **沙箱执行审计从来没有被写过**（`/api/sandbox/runs` 永远是空表）。
  > `sandbox/audit.py` 实现完整、`_sandbox_setup()` 也老实注入了写入器，
  > 但 `runner.run()` 的 7 条 `return` 路径没有一条调用 `record`；
  > 而既有的三条审计测试**全部**是直接调 `audit.record(...)`——**测的是函数，不是链路**。
  > D-2 的 **E4「可观测」因此在文档上写着"已实现"、实际空转**（PITFALLS #42）。
  > 修法：`audit.record` 移到 `run()` 这个**唯一执行入口**（原主体改名 `_run`），
  > 于是没有一条返回路径能绕过，且**被拒的执行也留痕**（被拒同样是执行尝试）；
  > 补两条回归（被拒路径 + 成功路径，后者还钉住"审计只存长度不存正文"）。
  > **全量 479 项通过**（468 fast + 11 slow）。

- **Skill 共享库：上传 → 从库里安装 → 一键卸载**（2026-09-17，用户定的形态：
  「skill 脚本是用户先上传共享库，随后从共享库中点击安装」）。

  新增 `workbench/skills/`（5 模块：`layout` / `manifest` / `security` / `library` / `audit`）
  + 迁移 **20**（`skill_audit` 表）+ `GET/POST /api/skills/*` + 新页面
  **Skill 管理**（`/static/pages/skills.html`，导航「Agent 能力」）。

  **两处都不进 git（用户明确要求）**：共享库与已安装目录都落在
  `<DATA_DIR>/skills/{library,installed}/`——`.gitignore` 已覆盖整个数据目录，
  **结构上不可能被 `git add -A` 带进仓库**（有回归断言之）。
  用户说明有一台云服务器可放共享库，故"库"的路径口径收敛在 `layout.py` 一处，
  将来换远端同形搬迁，上层不动。

  > ⚠️ **同日后补**：本段原写"本轮只支持声明式 Skill，因此
  > **安装任何 Skill 都不会执行上传的代码**——脚本执行面待下一期接入沙箱"。
  > 脚本式执行面已于同日接入（见本节上方的"脚本式 Skill"条目），
  > **那句话已不成立**，故改成现状描述。保留这段是为了留痕：
  > 「这一期不做」写进文档后，很容易被后来的人（包括我自己）当成「现在也不做」。

  **声明式工具**（`wraps` 一个已有工具）先跑通"上传 → 安装 → 生效 → 卸载"整条链路，
  **脚本式工具**（`script`）随后接入、在沙箱里执行包内脚本。
  这是用户路线（"先建沙箱，再接入 skill"）的直接结果：沙箱先立住，Skill 才敢跑代码。

  **安全分层（诚实标注，不把扫描当边界）**：
  | 层 | 作用 | 是否安全边界 |
  |---|---|---|
  | `security.inspect_zip` | 上传期结构校验 + 可疑模式扫描 | ❌ **准入闸门**（挡坏形状、摊开可疑处） |
  | 沙箱 | 执行期凭据不注入 / 目录不越界 / 进程树可终止 | ✅ 真正的强制层 |
  | 确认门（approvals） | 写/危险操作需人放行 | ✅ |
  - 结构规则抄自 ADP 的公开规范：ZIP ≤10 MB、≤300 文件、**禁一切二进制**、
    `SKILL.md` 必须在**根目录**；路径穿越一律拒（`../`、盘符、绝对路径）。
  - **扫描到高危模式时不会"点一下上传就入库"**：第一次只回 `findings` 让人看，
    带 `allow_warn=true` 重投才收。
  - 被包装工具不存在、或工具名与**核心工具**冲突 → 上传期就拒（不等安装时才失败）。

  **三条硬不变量（都有回归断言）**：
  1. 安装后工具**立刻**对模型可见（`list_api_for(None)` 里出现）；
  2. 卸载后**立刻消失**，且核心工具数不变（这正是文档门禁能继续成立的原因）；
  3. **卸载 ≠ 删除**：卸载只断指针，共享库里的版本一个都不删。

  **工具中枢为此扩成两层**（`PluginOrigin.CORE` / `INSTALLED`）：
  `_tools` 改为由 `_rebuild()` **派生**（唯一写入点），
  因此"摘了插件但工具还在"在结构上不可能发生。
  `tools/gen_tools_doc.py` 只数 core（`hub.core_specs()`）——
  否则**每台装过 Skill 的机器都会让文档门禁变红**。
  核心插件**可禁用、不可卸载**。

  **测试**：`tests/test_skills.py` 32 项，覆盖格式契约、ZIP 准入、schema→pydantic 转换、
  以及上面三条不变量。其中抓到并修掉一个**真 bug**：
  核心插件的 `capability_id` 是空串，初版"找不到目标就早返回"的实现
  会让 `deregister_capability("")` **静默无事发生**，把"拒绝卸载核心插件"的守卫整个绕过。

  **一个 YAML 反直觉点（记下来免得再查一遍）**：`version: 1.0` 会被 YAML 解析成 **float**
  而非字符串，于是报错是"缺少字段 version"而不是"格式不对"。不是 bug，是类型推断。

- **沙箱：不受信代码的唯一执行底座**（2026-09-17，详见 [docs/CORE-ARCHITECTURE.md](docs/CORE-ARCHITECTURE.md) §3.4 与 §7.10）。

  用户定的路线是「**先构建沙箱，优先补充整个项目的沙箱管理，再考虑接入 skill**」——所以本期只做底座，
  Skill 与 MCP 是它的下一期消费方（MCP 用户明确暂缓）。

  新增 `workbench/sandbox/`（5 模块，依赖方向单向 `runner → {policy, job, layout}`，
  **`policy` 不依赖本包任何其他模块**，以便在没装依赖的机器上单测）：

  | 模块 | 职责 |
  |---|---|
  | `layout.py` | **唯一**决定"什么算沙箱根"的地方（`<DATA_DIR>/sandbox`），防第二套口径 |
  | `policy.py` | 策略声明与校验（纯数据）：3 条具名策略、环境变量**白名单**、敏感路径拒绝表 |
  | `job.py` | 进程树的**生命周期强制**：Windows Job Object / POSIX 进程组 |
  | `runner.py` | 执行器：把四层强制组装起来（E1 生命周期 / E2 环境 / E3 文件系统 / E4 可观测） |
  | `audit.py` | 留痕：注入式写入器（与 `plugin_api` 对审计的处理同构），落新表 `sandbox_run`（**迁移 19**） |

  **顺带修掉的两个实测缺口**（`services/proc.py` 一直如此，只是此前没人执行不受信代码）：
  1. **超时只 kill 直接子进程** —— `proc.kill()` 不杀孙进程，`node` 起的服务会残留（占端口/句柄）。
     现在绑 Job Object，超时 `TerminateJobObject` **杀整棵树**（有真实孙进程的回归断言）。
  2. **环境全量继承** —— `dict(os.environ)` 把宿主一切交给子进程（含各种 `*_KEY`/`*_TOKEN`）。
     现在是白名单，且**凭据类变量名一律拒绝注入**（`validate()` 直接抛）。

  **两条实测校准（都是先踩后改，不是拍脑袋）**：
  - **不用 `CREATE_SUSPENDED`**：教科书写法（挂起→绑 Job→`ResumeThread`）在本机做不干净——
    `OpenThread(THREAD_SUSPEND_RESUME, False, pid)` 返回 NULL/err=87（它要的是**线程 id**，不是 pid），
    而不 resume 的后果是**进程永远挂着**（实测 `communicate()` 超时）。改为"正常创建 + 立刻绑定"，
    竞态窗口在微秒级，且已存在的子进程也会被一并移入 Job。**成本从"一段易失灵的内核代码"降到零。**
  - **`ActiveProcessLimit` 不取 1，取 10**：**venv 的 `python.exe` 是 launcher，它会 re-exec 真正的解释器**，
    于是"跑一个脚本"本身占 2 个进程槽。取 1 的实测结果是**脚本根本起不来**（rc=101 + launcher 乱码报错），
    比不限制更糟。取 10 仍挡得住失控派生，而"杀整棵树"的保证本就不依赖它。

  **诚实边界（写进代码、API 与界面三处）**：这**不是**安全边界。
  它保证"整棵树能停 / 凭据不注入 / 目录不越界 / 输出可截断 / 每次留痕 / 失败即拒"，
  **不提供**内核级隔离。故界面把「✅ 代码强制」与「⚠️ 只是声明」**分两栏**显示，
  `/api/sandbox/policies` 单列 `enforced` 字段——避免任何人（包括将来的我）把它读成"跑了就安全"。

  管理面：`GET /api/sandbox/{policies,status,runs,selfcheck}` + 新页面
  **沙箱**（`/static/pages/sandbox.html`，导航"Agent 能力"分组）。
  **刻意不提供通用的 `/api/sandbox/exec`**：本仓服务端只监听回环且无认证
  （`docs/CODEBASE_OVERVIEW.md` R1），开一个"跑任意代码"的接口等于把 R1 从"可读凭据"升级成"可执行任意代码"。

  测试：`tests/test_sandbox.py` 31 项，**分快慢两层**——纯逻辑层 27 项毫秒级每跑必过；
  真实执行层 4 项打 `slow` 标记（默认 `-m 'not slow'` 排除，**CI 显式补跑 `-m slow`**）。
  那 4 项恰恰是沙箱存在的理由（真的杀进程树、真的验凭据不泄漏），不能因为慢就永远不跑。

  > **推翻的既有结论**：`PLUGIN-UPSTREAM-RESEARCH.md:189` 与
  > `core-plugin-separation-research.md:126` 都写着"**不值得做**：进程沙箱"。
  > 那两条的**前提**是"插件作者只有你自己、全是可信代码"——用户要的"上传 skill"换掉了这个前提。
  > 所以不是它们错了，是适用范围变了；按本仓决策纪律新增**显式取代**，历史结论一字未改。

- **工具插件层：Agent 核心有了唯一的能力扩展面，三大业务以插件形式接入**（用户定稿：先提升核心中枢，
  再把三个工具做成插件与 Agent 关联）。契约见 **`docs/PLUGIN-SDK.md`（**该文件已于 2026-09-29 删除**）**。
  改造前的事实是：`harness/tools.py` 有自己一套工具（三个基础工具），`api/routes_agent.py` 另有一套
  （四个域工具 `login_platform`/`read_config`/`run_insight`/`browser_status`），**两套并存且互不可见**
  ——于是「对话里的模型只能聊天，一个平台能力都看不到」，而那四个域工具又只对 HTTP 调用方可见。
  现在只有一个事实源 `plugin_api.ToolHub`，工具 **20 个 / 5 个插件**：

  | 插件 | 工具 | 风险 |
  |---|---|---|
  | `builtin` | `get_current_time` / `read_local_file` / `read_web_page` | read |
  | `websearch` | `web_search` | read |
  | `insight` | `insight_accounts` / `insight_read_config` / `insight_run` / `insight_artifacts` / `insight_read_artifact` / `insight_runs` | read |
  | `factory` | `factory_validate_blueprint` / `factory_list_blueprints` / `factory_jobs` / `factory_status` | read |
  | `factory` | `factory_create` | **dangerous** |
  | `accounts` | `accounts_list` / `accounts_login` / `accounts_browser_sessions` | read |
  | `accounts` | `accounts_browser_open` / `accounts_browser_close` | write |

  - **单一事实源**：`GET /api/agent/tools`、对话 Agent 的 function calling schema、审计三处读同一份定义。
  - **插件 API 就是 `ToolHub`**（`workbench/plugin_api.py`）。不做 entry_points 自动发现 / 独立进程 /
    热插拔 / 版本矩阵——研究结论是"插件作者只有你自己时，那些是为第三方生态付的成本"
    （`docs/research/core-plugin-separation-research.md` §A5；**该文件已于 2026-09-29 删除**，
    论证已并入 的「外部参照」栏）。

  - **装配纪律**：插件清单显式（`plugins/__init__.py::_MODULES`），单个插件抛异常只记一条 failure
    **并跳过**，绝不阻断启动；依赖方向 `plugins → plugin_api` **单向**，`plugin_api` 里不出现任何
    平台术语（连 `db` 都不 import，审计与确认门是注入的）。
  - **启动自检**：`GET /api/agent/selfcheck` + 启动日志（`REQUIRED_TOOLS` 缺一即报）。
    坏插件被隔离的另一面是**静默**——"应用照常启动但功能少了几个"必须能被看见。
  - **PyInstaller**：插件是 `importlib.import_module` 动态导入的，`server.spec` 的 `hiddenimports`
    逐个列出（漏收 = 装完没功能且不报错）。
  - 旧工具名不再存在：`read_config`→`insight_read_config`、`run_insight`→`insight_run`、
    `login_platform`→`accounts_login`、`browser_status`→`accounts_browser_sessions`。

- **危险操作确认门：风险分级从"声明"变成"强制"**（`workbench/approvals.py` + 迁移 16）。
  `Risk.WRITE`/`DANGEROUS` 的工具**没有单次令牌绝不执行**——不是"执行后警告"，
  测试断言的是 handler 的调用记录为空。
  - 两条链路一套机制：对话链路 SSE 发 `approval_required` 事件；HTTP 链路 `POST /api/agent/invoke`
    回 **409 + pendingId**（"需要确认"不是错误，是流程中止）；两边都走 `/api/agent/pending/*` 决策。
  - 令牌**一次性 / 5 分钟 / 绑定工具+参数指纹**（换参数要重新确认）；批准一次不能重放多次。
  - 摘要说**后果**不说参数：`approvals.summarize()` 生成"这个工具要做什么 + 影响谁 + ［几级］"。
  - `pending_action` 只做**留痕**，权威状态在内存；重启时上一进程未决的操作标 `expired` 而**不恢复**。
  - `factory_create` 与引擎侧"环境强制确认"（`confirmed=true` 否则 409）是**两条独立闸门**：
    工具级挡 Agent 链路，引擎侧挡"绕过页面直接打 HTTP"的人。

- **工具调用可观测：对话里的每次调用都入审计 + 前端实时卡片**。
  - 审计唯一入口 `approvals.write_audit()`，HTTP 面与对话循环共用——改造前 `harness` **完全不写**
    `agent_audit`（对话里跑 30 次平台调用，审计里一条没有）。
  - 新增事件：`action` 带 `tool:{name,risk,plugin}` + `auditId`，`observation` 带
    `invocation:{status,durationMs,auditId,…}` + 人话 `summary`；**靠 `auditId` 就地更新卡片**，
    同一 id 也是 `GET /api/agent/audit/{id}` 的主键。
  - `GET /api/agent/audit` 不再 `SELECT *`（此前会把 `args` 原始 JSON 一起回吐）；
    细节走新增的 `GET /api/agent/audit/{audit_id}`。
  - 前端：工具调用卡片（风险徽章 / 参数 / 耗时 / 结果摘要，错误态默认展开）、
    `approval_required` 的内联「放行·拒绝」卡片、对话输入区的工具面板。

- **工具资产的其他新端点**：`GET /api/agent/plugins`（插件维度）、`GET /api/agent/selfcheck`（自检）。
- **回归**：`tests/test_plugin_api.py`（装配隔离 / 命名与 schema 契约 / 风险分级 / 令牌一次性 /
  审计脱敏）、`tests/test_agent_loop.py`（事件序列 / 结果回填 / 确认门 / 步数预算 / 重复调用切断）。
  测试数 **335 → 375**。

- **Agent 缓存机制：把 DeepSeek 的上下文硬盘缓存从"碰运气"变成可测可优化**（用户目标 ①）。
  官方规则是"后续请求必须**完整匹配**一个缓存前缀单元"才命中，而**命中价是未命中价的 1/50**
  （`deepseek-flash` 非高峰 $0.003 vs $0.15 / 1M input tokens，见
  `docs/research/deepseek-api-capabilities.md` —— **该文件已于 2026-09-29 删除**）。改造前的三个问题各自都会让命中率归零：

  | 问题 | 现在 |
  |---|---|
  | `_manage_context` **每轮都重新调 LLM 生成摘要** → 请求前缀每轮都变 | 摘要**按边界冻结**（`conversation_context.summarized_upto`，只前进不回退），只在超预算时折叠一次；之后连续多轮前缀逐字节不变 |
  | `usage` 里的 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` **被整个丢掉** → 命中率无从得知 | 强制 `stream_options.include_usage`，`UsageStats` 跨调用累计（工具轮次 + 摘要重算）并落 `deepseek_usage` 表 |
  | 没有任何地方能看到"缓存到底有没有生效" | 设置页「缓存与用量」面板（命中率 / 命中与未命中 token / 摘要重算次数）+ 回答旁的 `缓存 N%` 徽章 + `GET /api/agent/cache` |

  - 请求组装改为 **append-only**：`[system] + [冻结摘要] + [原文历史] + [新消息]`，
    已发出的前缀永不改写（`workbench/cache.py::plan_prefix`，`tests/test_cache.py` 用
    逐字节指纹钉住这条不变量）。
  - **纪律（写进 cache.py 与提示词模块的文档）**：不要在运行时按轮次拼装 system 提示词——
    system 是每个请求的第一个字节，按轮次拼 = 每轮都让缓存从头失效。
  - 新增 `tests/test_cache.py`（14 项）：前缀逐字节稳定、摘要只前进、折叠确实缩短上下文、
    两种 usage 口径都吃下、无缓存字段时按全 miss 记（**宁可低估命中率也不虚报省钱**）。

- **自建 `web_search` 工具**（用户目标 ②，结论见 `docs/research/deepseek-api-capabilities.md` §2
  —— **该文件已于 2026-09-29 删除**；结论已落在
  `docs/PLUGIN-SDK.md`（**该文件已于 2026-09-29 删除**） 的 `websearch` 一行）。
  用户问"DeepSeek API 是否支持联网搜索"——**不支持**，两处官方文档互证：
  Responses API 的 Tools 表把 `web_search` 等内置工具列为**忽略**，API 参考里 `tools`
  参数写着 "Built-in tool types are ignored"。那条"`web_search_call` item 仍会被还原"
  的注记说的是"**旧模型**此前产生的搜索结果作为输入被容忍"，**不是**"服务端会替你搜"。
  - **两条通道**（选型是实测出来的，见 §2.1）：主通道 **Bing RSS**（结构化 XML + **真实直链**），
    兜底 **360**（几乎必成，但链接是需解跳转的 `so.com/link?m=…`）。
    百度/搜狗虽然 6/6，但返回的是**反爬页**——"没有失败"和"给了结果"是两回事。
  - **三个反直觉的实测结论**（都改掉了初版实现，见 §2.2）：
    ① **重试反而更糟**（失败突发成簇，3 次重试把失败耗时从 4s 拉到 10s 而不提高成功率；
    降到 2 次后连打 8 次实测 8/8、平均 1.1s）；
    ② **解不开的跳转地址必须丢弃**（360 的 `m` 会过期，事后重放 400；把废链接交给模型
    会让它再失败一次并据此**引用一个没读到的页面**）；
    ③ **"没有结果"与"通道挂了"必须分开报**（都返回空列表的话，模型会把通道不可达
    读成"网上没有这东西"然后据此下结论）。
  - `Risk.READ`，不过确认门；工具描述里要求引用条文前用 `read_web_page` 读原文核对。
  - 验证脚本 `tools/verify_websearch_fallback.py` 逼工具走兜底通道，断言它只会给出
    **两种可接受结局**：可直读的真实链接，或明确报错（**不允许**交出废地址/空列表）。
    （2026-09-17 由 `tmp/` 迁入 `tools/`——`tmp/` 在 `.gitignore` 里，**验证资产放在那儿等于不入库、迟早丢失**。
    它此前已被本 CHANGELOG 引用，即"文档引用了一个不受版本控制的文件"。同期 `tmp/diag_search_dual.py` 迁为
    `tools/verify_websearch_channels.py`，`tmp/` 现已清空。）

- **设置页开放系统提示词编辑**（用户目标 ③）。提示词分基线（代码里的
  `DEFAULT_SYSTEM_PROMPT`）与用户版（`settings.systemPrompt`，空 = 用基线），
  新增 `GET/PUT /api/settings/prompt`；「填入默认内容」只填框不保存，
  保存必须显式点按钮（改提示词会打断一次缓存前缀，得让用户自己决定何时生效）。

- **新增 [docs/VERSIONING.md](docs/VERSIONING.md)：版本管理机制与 GitHub 能帮哪一段**（2026-09-17）。
  把"版本管理"拆成三层（**L1 版本号 / L2 发布事实 / L3 用户可见**），逐层给出实测现状与选型。三条实测结论：
  1. **L1 已做得很好**：单一真源（`pyproject.toml`）+ 五写同步 + `bump.py` 两档制，**不需要引入任何工具**。
  2. **L2 有真缺口**：**发布了 12 个版本却只有 3 个 tag**（`v0.3.3`/`v0.3.4`/一个遗留 tag）。
     后果是**版本号在 git 里定位不到**——`git checkout v0.3.11` 直接报错，回滚与"用户跑的是哪一版"都无锚点。
     另：`.github/` **不存在**（无 CI 门禁），无 tag 保护规则，`.git/hooks` 只有 `.sample`。
  3. **L3 有一条"三方叠加才暴露"的静默缺陷**：更新器**匿名**读 Release ⇒ 要求仓库公开，
     而 **2026-09-17 匿名探测 `api.github.com/repos/jizi-dragon/akso-mogul` 返回 404**（同 owner 其余仓库均 200 公开）。
     叠加"自动更新默认关闭（`WORKBENCH_AUTO_UPDATE`）"与"有 `AKSO_UPDATE_OVERRIDE` 离线自测通道"，
     结果是**自测全绿、线上永远收不到更新，且没有任何一方会报错**。
     ⚠️ 本 CHANGELOG 早先写的"本仓库即公开"**与现状不符**，已在 `CONTRIBUTING.md` §6 记入已知状态。
  - 文档同时给出**方案甲（GitHub 只做记录+门禁，版本决策仍归 `bump.py`，推荐）**、
    方案乙（release-please 接管，**但会与两档制冲突，须先统一版本语义**）、方案丙（只自动化 CHANGELOG）的取舍，
    以及可直接照抄的 `ci.yml` 与"`build.ps1` 自动打 tag"改法。
  - **一个有利条件被记下来了**：实测 95 个提交里约 80% 已是 Conventional Commits（`fix` 30 / `feat` 28 / `chore` 13 / `docs` 6，含 `feat(insight)!` 破坏性标记），
    即"将来上自动化不用还债"。

- **落地"方案甲"：GitHub 只做记录 + 门禁，版本决策仍归 `tools/bump.py`**（2026-09-17 用户选型）。两项：
  1. **新增 `.github/workflows/ci.yml`**——push / PR 上跑
     `扩展构建 → uv sync → pytest → verify_packaging.py → ruff`，`windows-latest`，`timeout-minutes: 20`。
     **这是 `tools/verify_packaging.py` 自诞生以来的第一个自动调用方**（CONTRIBUTING §6 长期记着"未被 build.ps1 调用、无 CI"）。
     三个设计取舍都写进了 workflow 注释：① **Windows runner**（交付物是 Windows 安装包，且 P5 查 `.ps1` 的 BOM；
     门禁在"与发版不同的平台"上跑，绿灯不代表能发版）② **不自动发版**（故无需任何 secret，也不存在"CI 误发一版"）
     ③ **不加 `paths-ignore`**（被跳过的 workflow 在分支保护下会以 pending 卡住合并）。
     两个"必须照抄"的坑也写在注释里：`-o addopts=""`（否则 ultra-quiet 连汇总行都不打印）、
     **必须在 pytest 之前 `npm run build`**（`verify_packaging.py` 的 P4b/P4c 读扩展 `dist/`，而它被 gitignore ⇒ 全新 checkout 里不存在）。
  2. **`tools/build.ps1` 自动打 tag**（release commit 推送成功之后）——补上"标签"这一层缺口。
     四条守卫都不会打 tag：`-Bump none` / commit 未产生 / **commit 未推上去**（否则 tag 指向远端不存在的 commit）/ tag 已存在；
     失败只告警不改退出码（安装包已经出来了，一个 tag 推不上去不该作废整次构建）。
  - **动因**（**2026-09-17 带 token 复核后已更正**）：初稿据匿名探测称"发布过 12 个版本却只有 3 个 tag"，
    需给 9 个版本补 tag。**带 token 查明这是错的**——只发布过 **2 个 Release**（`v0.3.3`/`v0.3.4`），
    tag 与 Release **一一对应、无缺口**；那 12 次 `chore(release)` 是**本地版本演进，其中 10 次从未发布**。
    所以**不补历史 tag**（补了会制造假历史）。自动打 tag 的意义相应改为"**今后构建即留锚点**"。
  - 本地逐条复现 CI 的同一命令序列：`pytest` 全绿 / `PACKAGING_CHECKS: 9/9` / ruff `All checks passed`。
  - 相关文档：新增 [docs/VERSIONING.md](docs/VERSIONING.md)；`CONTRIBUTING.md` 增 §3.3.1（tag 与 CI 纪律）。

### Fixed

- **文档订正：仓库可见性与 Release 现状（2026-09-17 带 token 复核）**。三处基于**匿名探测**的结论是错的：
  | 位置 | 错误结论 | 带 token 查到的真相 |
  |---|---|---|
  | `docs/VERSIONING.md` §1.2/§3.2 | "发布了 12 个版本却只有 3 个 tag，需补 9 个 tag" | **只发布过 2 个 Release**（`v0.3.3`/`v0.3.4`，各 3 个资产），tag 与 Release **一一对应、无缺口** → **不需要补 tag** |
  | `docs/VERSIONING.md` §5.2/§7.1 | "配 Rulesets 保护 main 与 tag（5 分钟）" | `GET /repos/.../rulesets` → **403 `Upgrade to GitHub Pro or make this repository public`**。**免费版 + 私有仓库不提供 Rulesets**，该步骤**做不了** |
  | `CONTRIBUTING.md` §6 | "仓库必须是公开的"（作为要求） | 实测 `private=True`——**该要求从未被满足**，故**自动更新在这条链路上从未真正生效过**（更新器匿名读 Release） |
  - **根因是一条方法学错误，值得记下来**：匿名 API 的 **404 既可能表示"不存在"，也可能表示"不可见"**；
    私有仓库上两者无法区分，我却把 404 读成了"没有 Release"。
    **凡是要靠"接口返回 404 / 空"下结论时，必须先确认自己有权限看到全部数据。**
    本仓同类错误已出现两次（另一次是"用某文档页没写证明平台没此能力"）。
  - `docs/VERSIONING.md` §3.1 现在明确给出 **A/B/C 三个更新源选项及代价**
    （A 转公开 = 一次解决自动更新 + Rulesets；B 自托管更新源；C 不做自动更新）。
    **仓库转公开是业务决策，文档只列事实与代价，不替用户决定。**
- **修正 lint 命令口径（2026-09-17 实测）**：`CONTRIBUTING.md` §1/§7 与 `docs/CODEBASE_OVERVIEW.md`
  多处写着 lint 走 `.\.venv\Scripts\ruff.exe check .`——**该文件不存在**（直接调用报 `CommandNotFoundException`）。
  实测 `.venv\Scripts\` 里**有** `uv.exe` / `uvx.exe` / `uvw.exe`，**没有** `ruff.exe`。
  正确且唯一可用的命令是 **`.\.venv\Scripts\uv.exe tool run ruff check .`**（首次下载 ruff 约 10 MB，需联网）。
  > 这条尤其值得记：**2026-09-16 那次"更正"把两个事实都搞反了**——它说 `uv.exe` 不存在（其实存在）、
  > `ruff.exe` 存在（其实不存在）。已改为唯一权威口径，并在历史条目上标注"那次更正本身也是错的"。
- **`tests/test_proc.py` 两处 Windows-only 硬编码路径改为跨平台**（2026-09-17）。
  `Z:/definitely/missing.exe` 与 `Z:/no/such/dir` 在 POSIX 下**不是绝对路径**，
  而 `proc.check_spec` 只在 `os.path.isabs(exe)` 成立时才查存在性 ⇒ Linux 上该用例会以
  "没抛出 `ProcError`"失败——**看着像 `proc.py` 的 bug，其实是用例不可移植**。
  改为 `_absent_absolute_path()`（`tempfile.gettempdir()` + uuid，保证跨平台都是绝对路径且必然不存在）。
  > 这是为 CI 做的准备：**门禁要在和发版一致的环境里跑，但用例本身不该被平台绑死**。
  > 顺带记一条判据：凡"某用例只在某个平台过"，先怀疑用例而不先怀疑被测代码。

- **CI 补入工具清单门禁**（2026-09-17）。`.github/workflows/ci.yml` 在 `verify_packaging.py` 之后增加
  `tools/gen_tools_doc.py`（2026-09-16 随"工具清单治理"引入的"文档清单 == 运行期 ToolHub"门禁）。
  `tests/test_tools_doc.py` 已覆盖它，但**单独跑一次能让失败原因在 CI 界面上一眼可见**。
  CI 最终 10 步：checkout → python → node → uv → **扩展构建** → uv sync → pytest → verify_packaging → gen_tools_doc → ruff。
- **本轮已推送**：`5830da6`（文档漂移 + CI + 自动打 tag + 清理）、`0494ed6`（CI 补工具清单门禁）、
  以及本条的文档订正。推送经由本机代理 `127.0.0.1:7890`（直连被重置；`git` 已配 `http.proxy`）。

- **`tests/test_proc.py` 两处 Windows-only 硬编码路径改为跨平台**（2026-09-17）。
  `Z:/definitely/missing.exe` 与 `Z:/no/such/dir` 在 POSIX 下**不是绝对路径**，
  而 `proc.check_spec` 只在 `os.path.isabs(exe)` 成立时才查存在性 ⇒ Linux 上该用例会以
  "没抛出 `ProcError`"失败——**看着像 `proc.py` 的 bug，其实是用例不可移植**。
  改为 `_absent_absolute_path()`（`tempfile.gettempdir()` + uuid，保证跨平台都是绝对路径且必然不存在）。
  > 这条是为 CI 做的准备：**门禁要在和发版一致的环境里跑，但用例本身不该被平台绑死**。
  > 顺带记一条判据：凡"某用例只在某个平台过"，先怀疑用例而不先怀疑被测代码。

- **打包门禁从 8/9 恢复到 9/9：扩展产物版本落后于项目版本**（2026-09-17 实测发现并修复）。
  `tools/verify_packaging.py` 的 **P4b** 断言"扩展 manifest 版本 == 项目版本"**失败**：
  项目 `0.3.12`，而 `extensions/quick-login/dist/manifest.json` 停在 **`0.3.11`**。
  **根因**：`dist/` 是构建产物（`.gitignore` 已忽略），最后一次 `tools/bump.py` 只改了**源码** manifest，
  而 `dist/` 是上一次构建留下的——**源码对、产物旧**，正是 P4b 要抓的那类不一致。
  **修法**：`cd extensions/quick-login && npm run build` 重建 → `PACKAGING_CHECKS: 9/9`。
  **教训**：`bump` 之后必须重建扩展，否则发出去的安装包里带的是旧版扩展（用户侧表现为"升级了但扩展还是老的"，
  而仓库里看不出来——因为 `dist/` 不进 git）。
- **`docs/CODEBASE_OVERVIEW.md` §仓库卫生 整节重写**：原内容已全部失效（声称"两份方向调研文档未提交、
  `tmp/` 未 ignore"，而前者**从未入库**、后者**自 2026-09-14 起已 ignore**）。新版按实测列出已清理项与仍存在的两处缺口。
- **`docs/CODEBASE_OVERVIEW.md` C14 条目订正**：它声称已加 `tmp/verify_factory_frontend.py`，但**全库无此文件**。
  已改为如实标注"**已修但无自动化回归**"（C14 属"页面不报错、测试也不报错"的静默缺陷，恰恰最需要那条断言）。

### Removed

- **删除 `docs/方向调研-平台洞察独立发展.md`**（2026-09-17，用户定稿；**内容已作废，勿从历史里恢复当现状依据**）。
  废除理由有据可查，不是"看着碍眼"：
  1. **它 100% 依附两份本仓根本不存在的文档**——正文开头即声明"接续《方向调研-公司级Agent平台.md》与
     《方向调研-钉钉MCP落地细节.md》"，并多处直接引用后者的 §2/§4.2 结论。**外部依赖缺失使它的结论无法复核**。
  2. **它的技术前提已被否决**：全文建立在「把能力经钉钉 MCP 供给出去」这一方案上，
     而该方向已明确不走（钉钉技能中心与 ADP 一样**只接受 SSE / Streamable HTTP、不支持 stdio**，
     且 2026-09-17 的调研结论是**用 Stream 模式机器人取代 MCP 注册**）。
  3. 它引用的 §1 数据目录勘查结论（`platform_env` 行数、`insight_runs` 从未在真机落记录等）
     **已被后续实测推翻**：2026-09-17 实测 `insight_runs` **15 行**、`agent_audit` 1 行、`blueprint_jobs` 1 行，
     即**已经跑过真实任务**。继续挂着它会让"零真实运行数据"这个错误前提被反复引用。
  - **不保留副本**：如需追溯，`git show d0efa84:docs/方向调研-平台洞察独立发展.md` 可取回原文。
  - 同期清理了一批**未跟踪**的 ADP 调研残留（根目录 24 个 `_body_*` / `_cn_*` / `_raw_*` 抓取产物、
    `adp_research/`、`adp-{api,kb,multiagent-routing}-research.md`、`docs/research/adp-migration-research/`，约 5.8 MB）。
    这些是过程产物、不属于项目资产，删除不进 git 历史。

- **文档漂移修复（数量类断言，全部按 2026-09-17 实测重写）**。漂移不是笔误，是"能力清单靠人工同步"的必然结果：
  | 位置 | 原写 | 实测 |
  |---|---|---|
  | `README.md` / `CODEBASE_OVERVIEW.md` / `PLUGIN-SDK.md` / `API.md` | 4 插件 19 工具 | **5 插件 20 工具**（漏 `websearch`） |
  | `README.md` / `CODEBASE_OVERVIEW.md` | 迁移 1–13 | **迁移 1–18**（`SCHEMA.md` 一直是对的，是这两份没跟上） |
  | `README.md` | 10 篇决策记录 | **12 篇** |
  | `README.md` | 22 条环境坑 | **35 条**（`PITFALLS.md` 本身一直是对的） |
  | `README.md` | "60+ 端点" | 改为不写死数目（实测 84 个） |
  | `CODEBASE_OVERVIEW.md` 验收基线 | "63 passed, 1 failed"（2026-09-13） | **378 passed, 0 failed**（2026-09-17）；旧基线移入"历史验收基线"留痕 |
  - **README 文档地图补入两份被漏掉的跟踪文档**：`docs/FRAMEWORK-INVENTORY.md`、`docs/PLUGIN-UPSTREAM-RESEARCH.md`
    （此前只能在 `PLUGIN-SDK.md` / `PITFALLS.md` 里被顺带引用，入口处看不到），
    以及 `docs/research/core-plugin-separation-research.md`（`plugin_api.py` 文件头的依据）。
  - README 的"它解决什么问题"补了**Agent 工具层**一行（此前只列四大业务，读者看不出本仓有 Agent 能力与确认门）。

### Changed

- **品牌中立化：`AksoWorkbench` → `Workbench`**（用户定稿 2026-09-16：「更名，剥离 akso 的信息，
  按照 workbench 进行后续发展（逐渐将项目转变成可复用的工作台，核心差距就在于插件/tools 功能）」）。
  完整决策、改动面与"刻意不改"清单见 [CHANGELOG](CHANGELOG.md) 与 [docs/CORE-ARCHITECTURE.md](docs/CORE-ARCHITECTURE.md)。

  - **改动面**：58 个文件 / 200 处。包名 `akso-workbench`→`workbench`、产品名与 appId
    （`AksoWorkbench`/`com.akso.workbench`→`Workbench`/`com.workbench.app`）、安装包
    `Workbench-<版本>-setup.exe`、sidecar 产物 `dist/WorkbenchServer/`、
    数据目录 `%APPDATA%\Workbench`、环境变量 `WORKBENCH_PROXY` / `WORKBENCH_NO_PROXY` /
    `WORKBENCH_HTTPS_PROXY` / `WORKBENCH_UPDATE_OVERRIDE`、洞察默认工作区 `<我的文档>/Workbench/洞察`。
  - **数据不丢**：`config.legacy_db_candidates()` 新增最高优先级候选
    `%APPDATA%\AksoWorkbench\workbench.db`，**只在目标库不存在时**接管（复制不移动、不覆盖现有库），
    连 `-wal`/`-shm` 一起。真机实测：接管后 `/extension/snapshot` 读出 5 个账号全部通过过滤。
  - **兼容读取（写出端只发新名）**：旧环境变量名继续认；`import_backup` 同时接受
    `akso-workbench-backup`；扩展 `sync.ts` 同时接受 `akso-workbench-snapshot`
    （扩展要手动「重新加载」才升级，只认新名会让旧扩展静默丢账号）；`ports.js` 的占用判定
    同时认 `workbench.exe` 与 `aksoworkbench.exe`；洞察工作区在旧目录存在时继续沿用。
  - **刻意不改**：上游项目名（`akso-cc` / `akso-auto` / 仓库名 `akso-mogul` / `adapters/akso-*.json`
    / `AKSO_CC_REPO` / `AKSO_AUTO_REPO`）、平台适配层目录 `services/egmp/`、
    扩展内部存储键 `akso:*`（改名会让指令游标复位 → [PITFALLS](docs/PITFALLS.md) #15）、
    历史决策/archive/CHANGELOG 条目。仓库名本次**不改**（用户决定），故 `publish.repo` 仍指向 `akso-mogul`。
  - **升级注意**：`productName` 变了 → NSIS 安装目录与卸载项是新的一套，**旧版不会被覆盖安装**，请先卸载旧版；
    发布产物更名后旧客户端的 `latest.yml` 检查会断（自动更新默认已关闭，影响面小）。
  - 经验固化：[PITFALLS](docs/PITFALLS.md) #36「改名真正会坏的是协议与持久化标识」。

- **模块 id `akso-auto` → `factory`**（用户定稿：将 akso-auto 更名）。与 2026-09-14 的
  `akso-cc` → `insight` 同一处理：**能力已是本工作台的，界面与接口上都不该再出现上游项目名**。
  - 改名范围：模块 id（`GET /api/modules/factory`）、模块标题（原「Akso Auto 配置自动化引擎」）、
    页面文案、前端体检与日志前缀。**迁移 18** 把 `blueprint_jobs.module` 的历史值
    （`akso-auto` / `egmp-native`）统一为 `factory`——`insight_runs.module` 曾出现三个值
    （迁移 15 才收口），这次不重犯。
  - **存档文件名仍是 `adapters/akso-auto.json`**：它是"这份存档对应哪个来源项目"的引用真源
    （`repoPathEnv=AKSO_AUTO_REPO`、`repoPath` 都指向它），改名会让 `repoPathEnv` 的语义失真。
    id 与文件名不一致是刻意的，与 `akso-cc.json` 的 `id=insight` 同构。
  - 旧 id `akso-auto` / `akso-cc` 一律 **404**（不留"还能用"的第二名字），有回归断言。

### Fixed

- **配置工厂的执行日志从来什么都不显示**（真缺陷，潜伏至今）。`factory.js` 读的是
  `result.stdout_tail` / `stderr_tail` / `code` / `duration_s`，而 `POST /api/factory/run`
  返回的是 `{job_id, status, result, log_tail}`——**那四个字段它从来不返回**（它们是
  `services/proc.py` 的进程结果形状，而工厂在阶段 3 原生化之后没有进程了）。
  表现为"点执行 → 转一圈 → 日志区还是空的、收尾行写着 `exit=undefined`"，
  且**页面不报错、测试也不报错**：`undefined` 在 JS 里不抛错，`for...of undefined` 也被
  `|| []` 兜住了。→ 现在渲染 `log_tail` + 逐对象步骤状态 + **`manualItems`（平台上建不出来、
  必须人工补的项）** + `checkpoint.completedCount/totalSteps`；成功态不再依赖只在失败时才有值的
  `result.message`。
  **防复发**：新增 `tools/verify_factory_page.py` —— 把**真实后端返回**喂给**真实前端渲染函数**
  （DOM 桩，只需 node，不装任何 npm 包），断言渲染结果含成功/步骤/断点/任务 id 且**不含
  `undefined`**，并检查前端代码里不再引用那三个幽灵字段（只看代码不看注释）。
  跨语言字段名是**没有类型系统保护的接口**，只能靠这种对照验证守。

- **`services/modules.py::module_status` 按 id 硬解析适配器**：`mod = adapter or {}` 只认调用方
  显式传进来的存档，于是 `GET /api/modules/<id>` 对任何**适配器模块**都返回空清单 +
  `status=missing`。改名时正是这样暴露的——工厂模块的体检项整片消失，而代码一处都没错。
  → 缺省时自己 `get_adapter(mod_id)`。

- **`status` 三态映射写反**（风险台账 C5）：原写法把"全挂"显示成 `degraded`、"部分可用"显示成
  `missing`，而状态词是运维唯一的判据，反了就没人去修。→ 改为显式四分支
  （空=missing / 全通过=ok / 部分=degraded / 全挂=missing）。

- **`tests/test_modules.py::test_adapter_paths_exist` 常驻红**（自 2026-09-13 起，是本仓
  `pytest` 唯一的失败项）。它断言"只读存档指向的原仓库目录存在"，而存档的 `mode` 已是
  `native-python-internalized` + `frozen: true`——**能力全部内化、运行时零依赖原仓库**。
  把"原仓库在不在"当断言，等于给一个已经不需要它的系统加一道环境门禁。
  → 拆成两条守**契约**（而非守**环境**）的断言：路径口径解析得出且与存档声明一致；
  缺失时报信息项、模块状态不变红。**不要**改回 `assert repo.exists()`。

- 工厂页两处过时文案：`pages/factory.html` 的副标题仍写"封装 akso-auto"，
  且声称"凭证导出为临时 env 文件（任务后删除）"——原生化后凭证是**内存直连、从不落盘**，
  该描述与实现不符。

### Changed

- **系统提示词简化为最小形态**（用户定稿 ③）：只回答**你是谁、工具从哪找、信息从哪来**，
  不再写身份设定（ISPE GAMP5 / 阿克索 / 纯 4 类可配置平台）。**约 1,500 字 → 511 字**。
  去掉的三类内容及理由：
  - **工具清单**（原提示词逐个点名 9 个工具）：工具 schema 每次请求都会下发，每个工具自带
    `description`。写两份必然漂移——本轮加 `web_search` 时就得改两个地方，下次谁忘了改，
    模型就会以为自己没有某个工具。**这不是"更简洁"，是消除了一个必然出错的同步点。**
  - **回答格式**（结论先行 / 依据溯源 / ⚠️ 风险提示）：那是产品偏好，该由用户在设置页决定。
  - **身份设定**：对行为没有约束力，却占着每个请求最前面的字节——**前缀越靠前越值钱**。
  回归里加了"不许再往提示词里硬编码工具名"的断言（`test_default_prompt_is_minimal`）。

- **默认模型名 `deepseek-v4-flash` → `deepseek-flash`**，并新增 `normalize_model()`
  把历史名在读设置时就归一（含 `deepseek-v4-flash-vision-exp`）。
  依据：官方 2026-09-10 起 **V4 Flash 已退役**，旧名靠一条"temporarily routed"的兼容路由
  活着。归一化不改行为（同模型同价格），但不再依赖一条随时可能撤掉的路由。

- **`read_local_file` 收边界**（安全）。它原来是**无限制的绝对路径读**——任何凭据文件都能被模型
  一句话读出来，结果还会原样进对话进审计。现在：① 敏感路径（凭据库 / 浏览器 cookie 库 / 系统目录）
  一律拒；② 应用目录之外只收"文本类后缀 + ≤2MB"。
  **刻意不用确认门**：那样每次合法读文件都要点一下，而"读用户指定的报表/日志"是高频真实用法，
  过度打扰会让人习惯性点"放行"，反而把确认门用废。

- **`harness/tools.py` 降级为兼容垫片**（只剩再导出）。`@tool` 装饰器**已下线**并显式抛
  `NotImplementedError`——它的形态会诱导写出没有风险分级的工具，而"没有分级"意味着不过确认门。

### Fixed

- `include_usage` 未开启导致流式响应**根本收不到 usage**——缓存命中量再准也无从取得。
  同时兼容两种形态：usage 独立成帧（OpenAI 约定）与夹在带 choices 的帧里（部分网关）。
- 摘要调用的用量此前完全不进账。现在**单独计数**（`summaryCalls`）：它的输入是历史原文，
  几乎必然全 miss，是"缓存优化到底有没有省到钱"最该看的那笔开销。

### Fixed

- `ToolContext.timeout_s` 的默认值**静默盖掉**所有工具的 `timeout_s`
  （实测：声明 0.05s 超时的探针工具照样跑完 0.5s）。改为 `float | None = None`。
- 确认门闭包捕获旧 `PendingStore` 实例：`reset_store()`（测试隔离用）之后中枢仍捏着旧店，
  表现为"申请到 pendingId 却查不到 / 批不了"，且**只在测试里出现**。改为每次现取单例。

- **产物就地预览：应用内直接看 markdown 与 drawio**（用户要求）。此前 markdown 只能在新标签页里看
  **源码**、drawio 只能下载——五步产物虽然齐了，读起来还是得离开应用另找工具。
  现在洞察页产物表每行给「预览 · 下载」，预览走应用内弹窗（原生 `<dialog>`，与账号中心同一套）：
  - **markdown**：受控子集渲染（标题 / 表格 / 列表 / 引用 / 围栏代码 / 行内代码与强调 / 链接），
    **先转义再拼装**、链接只放行 http(s)——产物里混着平台来的对象名与字段名，直接当 HTML 插进 DOM
    就是注入面；右上角「原始」可随时切回源文本比对。
  - **drawio**：自己解析 mxfile 画成 SVG（多页带页签、节点按 stepType 上色、正交连线带箭头），
    **完全离线**——官方 viewer 是联网服务，打包态用不了；解析失败退回源码并说明原因。
  - `json` 美化、`txt`/`log`/`csv` 原样，其余后缀走下载；后端把 `.drawio` 纳入**内联可读**
    并给准 media type（`text/markdown`、`application/xml` …）。
  - 新增回归工具 `tools/verify_insight_preview.py`（14 项，真浏览器断言：表格列口径、引用块、
    节点/连线计数、多页切页、零 JS 错误）。夹具不用手写 md，而是**拿回放夹具离线跑一遍真 spider**
    再验它的产物——否则只能证明"渲染器能渲染我以为的形状"。

- **端口协商与公布（用户提出的"端口好几个，是不是该控制平衡一下"）**。

  改造前 18765/18766/18767 被**壳、Python 服务端、浏览器扩展三方各自硬编码**，且没有任何一方
  负责裁决"谁在用这个口"。**壳现在是唯一仲裁者**：优先取基准端口，被占则顺延（每端口最多 +8，
  不做全局随机——固定端口对书签/扩展更友好），然后把实际值
  ①经 `WORKBENCH_PORT`/`_CDP_PORT`/`_CONTROL_PORT`/`_SESSION_MODE` **下发**给服务端、
  ②**公布**到 `<数据目录>/runtime/desktop-ports.json`（原子写、退出时清理）、
  ③由壳控制服务新增的 `GET /whoami` **答给扩展**（`desktop/ports.js`）。
  - **服务端新增 `GET /api/health`**：`{app:"AksoWorkbench", version, pid, ports, portsSource, shell}`，
    无副作用（与会写 `everConnected` 闩锁的 `/extension/health` 明确分工）。
  - **扩展不再把桌面地址当编译期常量**：新增 `background/core/desktop-endpoint.ts`
    （缓存 → 问 `/whoami` → 回退固定 18765），`parallel-session` 的"桌面同步口"白名单也随之
    从"一个数字"变成"已知端口集合"。扩展已有 `host_permissions: ["<all_urls>"]`，发现无需新增权限。
  - **顺带修掉 的一半没生效**：`WORKBENCH_SESSION_MODE` 此前**从没被壳设置过**，
    托管会话实际一直跑原生 Chromium 而不是壳的 Chromium；现在随端口一起下发。
  - 端口值单点声明进 `config.py`（`PORT`/`CDP_PORT`/`CONTROL_PORT`），
    `routes_update` / `routes_extension` / `browser_pool` 一律读它，不再各自持常量。
  - 测试：`tests/test_ports_contract.py`（13 项，含"源码里除 config 外不得再出现这两个端口字面量"
    的扫描门禁）+ `tools/verify_port_broker.mjs`（23 项，真占用端口来验顺延与旧实例判定）。

### Changed

- **自动更新默认关闭**（用户定稿 2026-09-15）。此前：启动 +20s 静默检查 → 后台下载 →
  退出应用时自动安装——即**应用会自己把自己换掉**；在需要比对版本、反复构建的机器上这很危险
  （上一轮就出现过"跑的到底是哪个副本"难以判断的事故，见 PITFALLS #25）。
  现在：不开开关就完全不检查、不下载、不安装；托盘的「检查更新…」变成一条说明项
  （写明"自动更新已关闭（`WORKBENCH_AUTO_UPDATE=1` 可开启）"），"关于"对话框也照实说明。
  开关**刻意做成环境变量而非设置项**：它决定的是"这个应用会不会自己替换自己"，属部署期决定，
  放在运行时设置里迟早会被误点。`tools/verify_updater_logic.mjs` 增补 7 项"默认关闭"用例（54/54）。
- **洞察页布局重构**（用户定稿）：`历史任务` 上移到命令卡片下方、**一页 10 条 + 翻页**
  （`GET /api/insight/runs` 新增 `offset`/`total`）；`产物` 移到历史任务之下；
  删掉常驻的`执行日志`卡片，但**日志能力保留**——每行任务的「日志」直接在预览弹窗里打开
  `proc.log`，执行中的单行反馈显示在命令卡片下方（SSE 日志行只刷新那一行）。

### Fixed

- **五步织网取数层重写：三条通道全读错了**（用户实测："进入条件/进入动作/用户动作完全弄错"、
  "流程图没理解需求"、"关联对象是多余的"）。经平台前端 bundle 反查 + 真机（通达 `change_plan__c`，
  19 状态）逐条验证：
  - **进入动作**原先读 `GetActionBar`（该环境**恒返回空**）→ 改读 **`EnterAction/GetListByStatusId`**
    + `EnterAction/GetById`（详情给 `behaviorType` 与目标对象/状态）；
  - **用户动作**原先读 `UserAction/GetList`（只回运行时形态、**没有 code**）→ 改读
    **`UserAction/GetListByStatusId`**（带 `code`）；动作类型取自 `GetUserAction.behaviorTypes`
    （7=发起工作流 / 9=修改状态 / **46=修改相关对象生命周期状态**）；
  - **进入条件**原先填的是动作名（把动作名当条件）→ 现在把 `condition` 对象渲染成可读文本
    （如 `name__ak 不为空`）；平台没有独立条件端点（4 条候选实测 404）时如实写"平台未配置"。
  - **工作流归属**原先按 `objectCode` 过滤，而真机该字段**恒为 null** → 永远 0 条工作流（流程图
    当然出不来）；改按 **`objectId`/`lifecycleId`** 归属，触发状态取 `lifecycleStatusIds`
    （**逗号分隔字符串**，不是数组）。实测同一对象：0 条 → **15 张流程图**。
  - **关联对象**（用户定稿"阉割"）：不再用字段引用推（很多根本不在生命周期里起作用），只认
    **生命周期/工作流里真的被改动的相关对象**——即 `behaviorType=46` 的
    `relationId/relatedObjectId/targetStatusId`。实测 6 条跨对象关联，含用户给的那个例子：
    状态「等待风险影响评估完成」的进入动作「修改风险评估任务状态为[评估中]」→
    相关对象「变更关联的评估矩阵」→ 状态[评估中]。
  - 真机整跑（通达 `change_plan__c`）：**7 对象 / 19 状态 / 36 归因边 / 6 关系边 / 15 张流程图**，
    8.4s，覆盖度 complete。回放夹具按新契约重录（19 条交互），测试全绿。

- **五步织网吐不出有效产物（用户实测）**——三个根因，各自独立：
  - **step1 关系网恒为空**：平台字段页给的是 `referenceObjectId` + `objectRelation.objectName`，
    **`referenceObjectCode` 恒为 null**，而 `collect_relations` 只读后者 → **任何对象都推不出关系边**。
    实测 `the_change_lh__c`：修复前 0 条关系边，修复后 2 条（用户对象经由 7 个字段、部门对象经由 2 个）。
    连本仓夹具里那条 `dataType=15` 字段也被丢掉，重放测试还把 `relationEdges: 0` 固化成黄金值。
    现按「编码 → 名称 → id」解析，**以对象名称作节点标识**（用户定稿）；平台没给编码的作占位节点
    并记入 `unexpandedRelations`，不再静默丢边。
  - **step2/step3 归类用错了判据**：按动作栏 `behaviorType`（9/7 才算用户动作、其余算进入动作）归类，
    而规格 §2.3 的口径是**按 `executeType`**（1=用户动作 / 2=进入动作）。于是一个 `behaviorType=5`
    （更新字段值）的用户按钮会被错记成「进入动作」，没有动作栏行的用户动作也一起掉进去——四列表两列同时错。
    `behaviorType` 现在只用于给名字加注（→目标状态 /（发起工作流））。
  - **读失败被静默吞掉**：`except Exception: rows = []` 让「平台没配」与「我们没读到」长得一模一样，
    而 `coverage` 还写着 `complete`。现在读失败一律进 `failures`（带 stage）并让覆盖度降级为 `partial`；
    平台的**能力边界**（未启用生命周期、没给对象编码、该状态本来就没配动作）另记 `notes`，
    两者都写进 manifest、报告与任务日志。容忍范围刻意收窄为「平台侧读失败」
    （`httpx.HTTPError`/`ApiError`/`OSError`）——回放夹具的缺口异常必须炸穿，不许伪装成平台没配。
- **补齐五步各自的产品级产物**（用户定稿的「有效产物」清单）：新增 `step1-relations.md`（关联对象表 + 未展开边界）、
  `step2-status-table.md`（规格 C2 写死列口径：状态名称/进入动作/用户动作/进入条件）、
  `step4-workflows.drawio`（每个被发起的工作流一页流程图，来源含动作发起与状态触发两条通道）；
  `spider-network.md` 增加「步骤与产物」索引与「边界与缺口」段。实测 `the_change_lh__c`：
  3 对象 / 21 状态 / 4 归因边 / 2 关系边 / 3 张工作流图，共 7 件产物；`change_plan__c` 这类
  平台没配动作的对象则如实写明「平台未配置用户动作」，而不是给一张看不懂的空网。
- **`login` 连通测试报「无法连通」的根因：环境 baseUrl 存成了「登录页地址」**（用户实测，2026-09-14）。
  从浏览器地址栏复制的 `https://host/login` 存进 `platform_env.base_url` 后，AUTH 会被拼成
  `.../login/api/openapi/v1.0/Auth` → 网关 **405 Not Allowed**；而**同主机根地址** POST 同名端点
  返回的是平台信封 `{"code":500,...}`——这就是「地址存错」与「真不通」的判据（真不通表现为超时/502）。
  - 新增归一化单点 `egmp/kernel/urls.py::normalize_base_url`：剥 `/login`、丢 query、**其余子路径不猜**、幂等；
  - **写时**（`accounts.create_env/update_env`、备份导入）+ **读时**（`client` / `routes_insight` /
    `routes_agent` / `browser_pool`）两侧都过它，存量脏数据不必手工改也能用；
  - 实测：两个「通达」账号修复前均 405，修复后**真实登录成功**（796 / 693 ms，非缓存票据）。
- **洞察任务失败时不再「没有任何现场」**：`on_log` 原先只在 login 成功之后才被调用，于是任何 login
  失败（地址错 / 405 / 502 / 超时）都让 `proc.log` **根本不存在**，界面只剩一句无从下手的「无法连通」。
  `_run_job` 现在先落「命令 + 平台环境」表头，失败时落 `执行失败：<类型>: <消息>` 再抛出。
- **壳的 CDP 端口静默不监听**（v0.3.6 真机实测，见 [PITFALLS #27](docs/PITFALLS.md)）：
  `applyCdpSwitch()` 被搬进 `bootstrapPorts()`，而它在 `app.whenReady()` **之后**才被调用 →
  `remote-debugging-port` 完全不生效，实测 18766 从不 LISTEN、`/json/version` 拒绝连接。
  同一轮又刚让壳下发 `WORKBENCH_SESSION_MODE=cdp`，于是托管会话必然报「CDP 连接失败…」（无回退）。
  调用点移回**模块加载期**（0.3.5 的位置，也是 PITFALLS #3 的原始约束）。
- **打包态「chromium 未安装」是体检误报**（见 [PITFALLS #28](docs/PITFALLS.md)）：playwright 默认找
  `%LOCALAPPDATA%\ms-playwright`，包内那份在 `_MEIPASS` 下，而只有 `browser_pool._worker` 会设
  `PLAYWRIGHT_BROWSERS_PATH`，两个体检入口（`/api/modules`、`/api/browser/check`）跑在它之前 →
  `accounts` 恒为 `degraded`。抽成单点 `browser_pool.ensure_bundled_browsers_path()`，三处共用。
- **"改了代码但 UI 没变"（用户实测，一次极难排查的事故）**。桌面壳跑的是 9/9 的旧打包副本、
  占着 18765；**新的壳因为 `waitReady()` 只看"这个端口上有人回了 200"就判定自己的服务已就绪**，
  于是主窗打开的是**旧实例**的页面——菜单里那两个明明已删除的命令又出现了。
  - `waitReady()` 改为打 `GET /api/health` 并**核对 `app === "AksoWorkbench"`**：
    从"有人应答"升级为"是我们的服务在应答"。
  - 启动时先读上一次的 `desktop-ports.json`：若其中的 `pid` 仍活着（且不是自己），
    弹**可执行**的对话框（PID / 版本 / 启动时间 / 它占了哪些端口 + "仍然启动 / 退出"），
    而不是静默等 40 秒后白屏。
  - 就绪失败的错误页不再只说"超时"：把"探到了什么"（`wrong-app` / `no-response` / `timeout` +
    对方身份）一并显示，并对"端口上是别的程序"给出针对性文案。
  - 端口顺延窗口全部被占时**明确报错退出**，并提示可用 `WORKBENCH_*_PORT` 指定端口。
- **端口协商的互斥顺序错误（本轮自测抓出）**：`pickPort` 原写"先看 `taken` 就跳过、否则再探"，
  于是一个端口被占会导致**三级连带顺延**（server 让给 18766 → cdp 因 18766 被预留而跳到 18767 →
  control 再跳到 18768）。改成"先探可绑、再看预留"后，只有真正首选端口不可用时才移动。
- **`desktop/ports.js` 未加入 electron-builder 的 `files` 白名单**（被 `verify_packaging.py` P6 当场拦下）——
  否则打包态会以 `require('ports.js')` 失败的形式崩，而开发态完全正常。
- **端口公布文件的清理时机**：原先挂在 `before-quit`，而启动失败分支走 `app.exit(0)`
  并**不触发** `before-quit` → 文件残留成陈旧数据（实测复现）。改挂 `will-quit`（对所有退出路径都触发），
  且删之前核对文件里的 `pid`，避免"新实例已写、旧实例才退出"时误删新实例的文件。

### Removed

- **平台洞察下线两个命令：`inventory`（全量盘点）与 `understand`（三层理解报告）**。
  用户定稿：这两个功能的**应用场景模糊、实际使用效果不理想**，留着只会让人在命令下拉里猜
  「我到底该用哪个」。现存命令收敛为两个：`login`（连通性验证 + token 预热）、
  `spider`（五步织网 + networkx 图分析）。
  - 删除代码：`insight/assemble.py`、`annotate.py`、`flowgraph.py`、`relations.py`、`render.py`、
    `understand.py`、`crawler.py`；`workspace.coverage_for_understand()`；
    `routes_insight.InsightRequest` 的 `known_objects` / `llm` 字段与 `_llm_summary_fn()`。
    `insight/` 因此从 **12 文件 1,766 行降到 6 文件 1,145 行**。
  - **保留的是通用产物契约而非「理解报告」那套**：`workspace.py`（工作区根 + 三态覆盖度 +
    manifest）、`kernel/`、`replay.py`、`drawio.py`、`report.py`、`lifecycle.py` —— spider 在直接用。
  - 页面：命令下拉只剩两项，去掉「已知对象白名单」「LLM 职责摘要」两行输入，
    对象编码输入框仅在 `spider` 下显示。
  - 测试删减：`tests/test_lifecycle_coverage.py`（13 项）与 `tests/fixtures/egmp/understand-capa-plan.json`
    一并删除（它们是 `understand` 的专属门禁）。全量测试 330 → **281 项**。
  - **历史数据不受影响**：`insight_runs` 里 `command` 为 `inventory`/`understand` 的旧行照常列出，
    产物读取仍按 `workspace_dir` → `artifact_dir` 回落（迁移 1–14 一行未动）。

### Fixed

- **平台洞察：对象读不到时产出一张「假网」还报 `complete`（正确性，本轮新发现）**。
  `client.get_object(code)` 拿到**空实体**（对象不存在 / 无权限 / token 失效）时并不抛异常，
  而 spider 原来只做 `meta = ... or {}` 就继续：流程一路走完，产出「**1 个对象节点 + 0 状态**」的
  空网，`stats` 照记，manifest 的 `coverage.status` 还是 `complete` —— **任务绿的、图是假的**，
  与 `INSIGHT-SPEC` §4.3「产物自证覆盖度」直接冲突。
  - 现改为：**目标对象**读不到实体即抛 `SpiderReadErrors`（带 code，任务记 `failed`，不写 manifest）；
    **相关对象**读不到只记进 `failures`（部分对象本就在织网范围外，不该炸整次运行）；
    状态表读不到则出网但如实记录。
  - 回归：`tests/test_workspace.py::test_run_spider_missing_object_raises_instead_of_empty_graph`。
  - 同一处还修掉一个**被静默吞掉的失败**：`get_object_statuses` 原先不在任何 `try` 里，
    而 `paged_post` 的翻页失败会返回空列表——现在失败会进 `failures` 而不是悄悄变成「没有状态」。
- **平台洞察：产物没有落到用户配的工作区（承诺与实现不符）**。`workspace.py` 的工作区契约
  （`<root>/<对象code>/<时间戳>/`）此前**只有 `understand` 在用**，而 `understand` 下线后
  `run_spider` 仍是「直接写 `runtime/insight/<job_id>/`」，同时 `routes_insight` 把
  `outcome.get("workspaces")` 写进 `insight_runs.workspace_dir` —— 而 spider **从不返回该键**，
  于是该列永远是空的，产物面板只能靠 `artifact_dir` 回落。现在改为**先定落点再爬**：
  路由先 `workspace.run_dir()` 拿到工作区目录，把它当 `output_dir` 交给 spider，并回填
  `workspaces`（同时在工作区留一份 `proc.log` 副本，好让产物面板在同一处列出日志与产物）；
  工作区不可写则退回任务目录，此时 `workspaces` 为空数组而**不谎报**路径。
  （没有做事后重试——那会把平台调用重放一遍。）
- **`run_insight` Agent 工具仍在跑已删除的 `understand` 命令**（会直接 400「未知命令」）。
  现改跑 `spider`，并且**改为调用 `routes_insight.run` 公共入口**而不是 `_start_job`/`_run_job`
  内部函数——原先绕过去就漏掉了 `workspace_dir` / `coverage_status` 的回填。
- **CHANGELOG 自身**：`[未发布]` 段出现两个 `Fixed` 标题（合并）；上一条 Security 条目里
  历史重写的替换规则因「替换结果又被再次替换」而全部显示成 `A → A` 的空转表，现已补回**左侧原文**，
  替换规则这才读得懂。

### Changed

- **模块 id 由上游项目名 `akso-cc` 改为 `insight`（用户定稿：「模块 id 要改名」）**。
  上一轮只改了界面文案，接口里仍是 `GET /api/modules/akso-cc`——名不副实的那半个还在。
  - `adapters/akso-cc.json` 的 `id` → `insight`；**文件名保持不变**，并在 `idNote` 里写明理由：
    文件名是「这份存档对应哪个来源项目」的引用真源（`repoPathEnv=AKSO_CC_REPO`、`repoPath`
    都指向它），改名会让该变量的语义失真。**id 与文件名不一致是刻意的**，由
    `tests/test_modules.py::test_adapter_id_matches_its_filename_reference` 守住。
  - 模块标题 → `平台洞察（登录 / 蜘蛛五步织网）`，原上游标题移入 `legacyTitle` 留档。
  - 前端改调 `/api/modules/insight`；新增 `test_old_module_id_is_gone` 钉住**旧 id 返回 404**——
    改名必须一次改干净，**不许留"第二个还能用的名字"**。
  - **迁移 15 `rename_insight_module_id`**（数据行改写，**不动历史迁移 1–14**）：
    `insight_runs.module` 统一为 `insight`。⚠️ 该列**历史上先后出现过三个值**——
    `akso-cc`（迁移 8 的列默认值）、`egmp-native`（原生化后 `routes_insight` 实际写入的）、
    `insight`（现行）。只改其中一个就会留下"同一个模块三个名字"，模块维度的统计/筛选**静默漏行**。
    真实库实测：`akso-cc` 1 行 + `egmp-native` 11 行 → **12 行全部归一为 `insight`**，
    且重复连接不再变化（可重入）。`routes_insight` 的 INSERT 同步改为写字面量 `'insight'`。
    > 列默认值仍是旧的 `'akso-cc'`：改默认值只能靠"新迁移 + 重建表"，而代码**始终显式写入**
    > 该列，默认值不会被用到。已在 [SCHEMA.md](docs/SCHEMA.md) §3.7 用表格记全三个值的来历。
  - 回归：`tests/test_app.py` / `test_modules.py` 同步（3 项失败当场指出旧 id 的调用方——
    这正是门禁的价值）；全量 299 → **301 项**。

- **「UI 永远最新」的兜底入口：应用菜单（用户提出要有这个习惯）**。
  页面一旦加载进渲染进程内存，服务端怎么改都不会反映到**已打开的那个窗口**——这是本轮
  "改了看不见"反复出现的机制性原因。正确动作是**重新加载**（服务端已加 `no-store`，重载必拿最新版），
  但 `Ctrl+R` 对非开发者不是可发现的操作。故：
  - 新增应用菜单 `文件 / 视图 / 窗口`，其中 **`重新加载界面（F5 / Ctrl+R）— 改完代码后按这个`**
    用菜单文案直说用途，不让人猜；F5 与 Ctrl+R 都收（F5 是 Windows/浏览器用户的肌肉记忆）。
  - 另给 `强制重新加载（忽略缓存）`、缩放、全屏、`开发者工具（Ctrl+Shift+I）`——
    排障时能自己开 DevTools 看 Network/Console，不必再靠"服务端实吐字节数"间接判断。
  - 与全局热键 `Alt+Q`（账号轮盘）不冲突：那个走 `globalShortcut`，菜单只在窗口聚焦时生效。

- **端口改为固定：撤回"壳当仲裁者、被占则顺延"（取代
   §D-1）**。用户定稿原话：

  「我建议还是固定端口号吧，别拿所谓的桌面壳引导去调整，就固定端口号」。
  - **为什么推翻**： 要治的病（旧实例占端口 → 新壳认错服务）是真的，但**解法选错了**——
    它把"端口漂移"当成常规能力，于是引入协商窗口、实际端口公布、扩展按窗口探测、书签失效
    一整套活动部件；而本机端口冲突的唯一现实来源就是**工作台自己的旧实例**，
    这种情况**应该说清是谁占的**，而不是换号偷偷跑起来（用户不知道自己在 18768 上）。
  - **现在的行为**：三个端口是固定常量（18765/18766/18767，壳侧真源 `desktop/ports.js::PORTS`，
    Python 侧默认值必须一致）；启动顺序 = 清陈旧公布文件 → 判"是不是我们另一个实例" →
    探三个端口是否可绑 → **任一被占即 MessageBox + `app.exit(1)`**，文案区分
    "我们自己的实例"（给 PID/版本，去托盘退出它）与"无关程序"（给排查命令），
    并**不再提"可用 WORKBENCH_* 换端口重试"**（那等于教用户制造漂移）。
  - **结构上变成单实例**：端口固定后"活着的实例必然占着 18765"，原来那条
    「仍然启动 / 退出」的询问分支成了**永远走不到的死代码**，已删除。顺带消灭了
     的两个待办与 [PITFALLS](docs/PITFALLS.md) #23 的"两个实例共用同一份数据目录、
    凭据互相解密"那一族问题；`app.requestSingleInstanceLock()` 因此**明确不做**（端口占用已挡住）。
  - **扩展侧简化**：`desktop-endpoint.ts` 取消"按窗口探测控制端口"（那是为跟顺延而生的），
    只剩**一次** `GET /whoami` 身份确认 + 回退固定 18765。这一步保留的理由不是"跟上换端口"，
    而是确认"**18765 上是不是我们的服务端**"——凭据经过这条通道，而密钥就在本机库里。
  - **仍然保留的判据**（与顺延无关，别一起删）：`/api/health` 的 `app` 身份核对、
    "旧实例"必须核对**映像名**（`tasklist`）而非只看 pid 存活、启动时 `clearStale()`。
    这三条是本轮踩坑踩出来的真防线，见本节下方两条 Fixed。
  - **测试**：`tools/verify_port_broker.mjs` 整体改写为固定端口语义（27 项，含"不再导出协商 API"
    与"被占时点名固定端口号"）；`tests/test_ports_contract.py` 增 3 项——
    跨语言一致性（壳 `PORTS` == Python 默认值，这条壳侧脚本验不了）、固定值本身即契约、
    以及"不许把 `negotiate`/`MAX_DRIFT` 加回来"。
    > 跨语言一致性那条试过用 Node 正则解析 `config.py`，越改越脆（`PORT` 是
    > `env or env or "默认值"` 的跨行写法）——**这类检查本该放能真正 `import` 的那一侧**，故移入 pytest。

### Fixed

- **静态页与首页禁止缓存（根因级修复：重建了产物，界面却一直显示旧版本）**。
  这是"用户两次报『已下线的功能还在下拉里』"的**真正根因**——服务端实吐的已是新版
  （实测 2563 bytes / 2 选项），但 Electron 渲染进程带 `If-Modified-Since` 复验、
  `StaticFiles` 回 **304 未修改** → **渲染进程继续用自己那份旧副本**。
  两侧各自都"正确"，所以从代码完全反推不出来，只能靠"服务端实吐字节数 vs 界面"对比才发现。
  - 现对 `/` 与 `/static/*` 统一加 `Cache-Control: no-store, must-revalidate`
    （+ `Pragma: no-cache` / `Expires: 0`）；`/api/*` **不动**（避免影响 SSE 与将来可能的缓存语义）。
  - 本机回环读几十 KB 的 IO 成本可以忽略——**不值得为省这点开销换来"改了看不见"**。
  - 回归：`tests/test_ports_contract.py` 增 2 项（静态面带 `no-store`；`/api/*` 不受影响）。
  - **用户实测确认（2026-09-14，决定性证据）**：启动后首次打开仍是 4 项，
    **`Ctrl+R` 重新加载后立刻变 2 项**，之后两次启动都直接是 2 项。
    `Ctrl+R` 正是"绕过缓存重新加载"——一变就新，证明**服务端与产物一直是对的**，
    卡住的只是渲染进程里那份旧缓存；后两次正常则说明 `no-store` 已生效。
    > **方法论教训（本轮最贵的一课）**：判"界面为什么不更新"必须去读**渲染层的真实 DOM**，
    > 而不是停在"服务端实吐的字节数对不对"。两者可以**同时都"正确"**，而用户看的是前者——
    > 本轮为此绕了四轮。取证手段：用 `desktop\node_modules\electron\dist\electron.exe`
    > 配 `--remote-debugging-port` 起探针（⚠ **别用 `npx electron`**，它会另下一份不完整的
    > Electron 并报 "Electron failed to install correctly"），再用 CDP `Runtime.evaluate` 读 DOM。
  - 附带排查记录：磁盘上三份 `insight.html`（源码 / `dist/AksoServer` / 安装包内）
    **哈希一致且都为修复版**，`dist\AksoWorkbench\` 旧产物与扩展的 Chromium `Cache\` 目录
    均已排除——证据共同指向"服务端对、客户端缓存旧"，与结论自洽。
    另经全盘普查确认：本机**没有安装任何应用副本**、**没有孤儿 Python 服务**、
    含 `AksoWorkbench` 的 `package.json` **全盘只有仓库自己这一份**；桌面 `akso.lnk`
    指向 `D:\akso`（用户自己的文档目录，与工作台无关）。
- **界面上的上游项目名（`akso-cc`）已清除**（用户提出：「右上角 akso-cc 标识以及背后的功能
  现在功能逻辑是单独成工具的，并不依赖上游文件」）。能力早已原生化，界面不该再自称"封装某项目"。
  - 状态徽标 `akso-cc 就绪（ok）` → **`依赖就绪（ok）`**（失败态为 `依赖体检 ok：<项> 未就绪`）——
    它本来就是**本工作台自己的依赖自检**（`services/modules.py`），不是"上游项目健康度"。
  - 页头副标题 `封装 akso-cc：登录 / 蜘蛛（原仓库只读）` → **`本工作台原生实现：登录 / 蜘蛛五步织网（不依赖任何外部仓库）`**。
  - 执行日志前缀 `$ akso-cc spider …` → **`$ insight spider …`**。
  - ⚠️ 模块 **id 仍是 `akso-cc`**（`adapters/*.json` 的存档键，只读不改；改它会牵动
    `docs/SCHEMA.md` 契约与多份文档引用）——**界面上不出现，但接口里还在**，这是刻意的。
- **`inventory` / `understand` 的残留彻底清掉（用户提出这两个功能"太直接和庞大，一般实施用不到"）**。
  命令本身早在 `2cc09bb` 已下线，本轮清的是**会让人以为它还在的残留**：
  - **死代码**：`insight/report.py::to_markdown()`（把 `inventory.json` 渲染成「Gaia 系统配置清单」）
    **零调用方**，且模块名会让人以为还存在一条全量盘点读路径。已删；该文件只剩 `_table`
    （`spider.py` 在用的同包渲染工具），并改名/改述为"Markdown 表格渲染工具"。
  - **存档与现值不符**：`adapters/akso-cc.json` 的 `commands` 里仍列着 `inventory` / `understand`，
    而它会经 `GET /api/modules` 暴露给设置页的模块体检视图。已移除这两条
    （`commands` 现为 `login` / `spider`），并在 `deprecation` 里写明来由。
  - **刻意保留**（不是遗漏）：`insight/lifecycle.py` 的 `LifecycleApi` 被 `spider.py` 使用；
    `drawio.workflow_to_drawio` 被 `tests/test_drawio.py` 与 `tools/verify_insight_drawio.py`
    共 20+ 项断言锁定，且是"单页 mxfile"这一输出形态的唯一实现。删它们会伤到在用的能力。
- **「另一个实例在运行」对话框被 pid 复用触发，应用再也起不来（严重，实测踩到）**。
  壳启动时读 `runtime/desktop-ports.json` 判「是否已有实例在跑」，判据是
  **「文件里的 pid 还活着」**——而 Windows 的 pid 会被回收复用，于是**任何**撞上同一 pid 的
  无关进程都会让它判定为真：对话框弹出 → 用户按提示选「退出」→ `app.exit(0)` →
  **应用一行都没起来**。实测现场：对话框点名 `PID 3572`（而那台机器上根本没有工作台在跑）。
  - **判据升级为三要素：活着 + 映像名是我们**。新增 `ownImageNames()` / `looksLikeOurs()`：
    Windows 用 `tasklist /FI "PID eq N" /NH /FO CSV` 取映像名（一次调用同时回答"活着吗"和"是谁"），
    必须命中 `AksoWorkbench.exe`（打包态）/ `electron.exe`（开发态）/ 本进程自己的映像名。
  - `isAlive()` 在 Windows 上改走 `tasklist`：`process.kill(pid, 0)` 对同用户进程**恒不抛错**，
    对已消失的 pid 也不一定抛，本来就判不准。
  - **连带的现场污染也被治掉**：应用被强杀（`taskkill /F`、崩溃）时 `will-quit` 不触发 →
    陈旧公布文件残留 → 下次启动误报。新增 `clearStale()`（**启动时先清**，且只清"写它的进程
    确实不是我们"的那种），非我们实例的文件一律不碰。
  - **回归**：`tools/verify_port_broker.mjs` 由 23 项 → **32 项**。⚠️ 其中**原有那条
    「pid 仍活着时能识别出另一个实例」用的正是 `System`(pid 4)** —— 它把假阳性当成正确行为
    固化了下来，是这次事故的"绿灯帮凶"，已重写为「活着的**无关**进程不得被误认」。
  - **该脚本另补前置检查**：它靠"真占住基准端口"验顺延，所以**必须先确认基准端口空闲**。
    实测在"边跑着工作台边验收"时会先误判顺延、再抛未捕获的 `EADDRINUSE` 直接崩（报出来的是
    栈而不是结论）。现在会先给出人话提示并指明怎么退出工作台后重跑。
- **占用提示报错端口号（顺着 的病根，在文案里复现了一次）**。
  `describeOccupant()` 硬编码 `端口 ${BASE[key]}`（恒为 18765），而实例**实际**可能在
  18766+（端口顺延是 的核心机制）。用户照对话框去查 18765 只会查到空气，
  正是 要根治的「端口信息不可信」。现改为报 `prev.ports` 里的**实际端口**，
  顺延时附「（基准 18765 被占后顺延至此）」说明来由；公布文件缺端口信息时回落到基准号。
  `bootstrapPorts()` 里另一处 `另占一组端口（server=${ports.server}）` 同样改为不谎报具体值。
- **平台洞察页头副标题没跟着命令下线走（用户实测发现：界面上看着像四个功能都还在）**。
  `insight.html` 的副标题仍写着「登录 / 盘点 / 理解 / 蜘蛛」——**下拉框里早只剩 `login` / `spider` 两项**，
  但页头仍在给用户看已下线的「盘点 / 理解」，于是「到底该用哪个」的困惑又以另一种形式回来了
  （这正是当初下线这两个命令要消灭的东西）。改为「登录 / 蜘蛛」；
  `services/egmp/__init__.py` 模块地图里的「读路径（盘点/理解/蜘蛛/networkx）」同样改掉。
  - **误报排查记录**：用户报「两个被阉割的功能还在选项里」时，先核过三轮才定性——
    ① 源码/产物 `grep` 无 `inventory`/`understand` 选项；② 打包态 `GET /static/pages/insight.html`
    实吐 `<option>` 只有 login/spider；③ 与用户截图逐字比对，发现截图里的
    「对象编码（understand/spider 必填）」「inventory 兜底白名单（可选）」两行**只存在于
    `2cc09bb^` 那一版**。结论：截图来自**构建之前就在跑的旧实例**，不是本次构建的产物。
  - **教训（值得记）**：`dist/` 下会残留**上一代 PyInstaller 产物**（`dist/AksoWorkbench/`，
    2026-09-09，含 4 选项的旧 `insight.html`）。它与本次产物 `dist/AksoServer/` **同级并存**，
    光看目录名很像「当前构建」。判断「你看到的是哪一版」不能靠目录名或印象，
    要看 ① 进程的 `ExecutablePath`、② 端口占用者、③ 服务端实吐的字节数。

- **`Alt+Q` 在桌面端未运行时被浏览器扩展抢走（用户实测 bug）**：只有扩展挂在浏览器里、
  工作台没开时，按 `Alt+Q` 会在**浏览器里弹出浏览器版轮盘**（内容与桌面轮盘重复，
  且此刻桌面数据面不可达——弹出的轮盘点谁都意义不大）。
  - **根因**：`manifest.json` 给 `quick-wheel` 声明了 `suggested_key: {default: "Alt+Q"}`，
    注释写着「桌面壳运行时 Alt+Q 优先」——**这个假设是错的**：桌面没运行时没人抢，
    Chrome 就把 `Alt+Q` 交给扩展。扩展是**执行面**，它的轮盘只应由桌面经
    `/extension/commands` 下发 `wheel.toggle` 触发；桌面没运行 = 没有触发源。
  - **修法**：删除 manifest 的 `commands` 段（`Alt+Q` 归还桌面壳全局热键独占）；
    同步删掉 SW 里对应的 `chrome.commands.onCommand` 监听器；
    弹窗页脚文案由「`Alt+Q` 呼出账号轮盘」改为「轮盘由桌面端 `Alt+Q` 呼出」。
  - **⚠️ 连带修一个由本次改动暴露的 SW 顶层崩溃（严重）**：Chrome 在扩展**未声明任何命令**时
    **根本不提供 `chrome.commands` 这个 API**（值为 `undefined`），而 SW 顶层仍无条件执行
    `chrome.commands.onCommand.addListener(...)` → 抛
    `TypeError: Cannot read properties of undefined (reading 'onCommand')`。
    **顶层抛出 = 其后所有代码永不执行**，包括 `startDesktopSync()` ——
    表现是「账号不再下发、扩展徽标离线、状态上报全停」这种**看起来完全无关的静默故障**。
    同时修掉 `background/core/site-auth.ts:148` 里同一处无条件访问（加了 `chrome.commands?.` 判空）。
  - **测试缺口已补**：此次事故里扩展启动冒烟 B1~B8 **全绿**（SW 进程活着、宿主页零错误），
    只有拿 `chrome.alarms.getAll()` 才看得出同步桥压根没启动。故新增
    **B9「桌面同步桥已启动（`akso:sync` 闹钟存在）」**（顶层跑到最后一行的可靠证据）
    与 **B10「无 commands 声明时 SW 仍跑到底」**；已用「摘掉闹钟创建」模拟顶层崩溃反向验证：
    10/10 → 8/10，B9/B10 精准变红。
  - 另修 `tools/verify_extension_boot.py` 的既有 flake：宿主页 `goto` 失败后的
    `about:blank` 兜底未加保护，服务未起时整个脚本直接崩、连结果都不打印（改为二次 try）。
  - 回归工具输出脱敏：`tools/verify_extension_sync.mjs` 原先直接打印快照里的
    **真实账号名**（运行期账号库内容会泄进终端/CI 日志），改为掩码输出。

### Security

- **清除仓库中的客户/环境标识与个人身份信息（含提交历史重写）**：真实部署域名、客户租户名、
  内网地址、真机账号名全部替换为文档占位值。⚠️ **本次重写了全部提交历史（所有分支与标签），
  提交 SHA 全变** —— 协作者必须按 `docs/HISTORY-REWRITE-NOTICE.md`（**该文件已于 2026-09-29 删除**）
  迁移本地副本，不要 merge 旧历史。
  - 替换规则（工作区与全历史一并执行）：真实平台/部署域名 → `*.example.com`；客户租户名 →
    `example-config`；环境代号 → `demo-val`；中文租户名 → `示例租户`；内网地址 →
    `192.0.2.10`（RFC 5737 文档网段）；真机管理员账号名 → `dev-admin`；真机普通账号名 →
    `dev-user`；Java 包标识里的个人标识 → `com.example`；商业 VPN 产品名 → `VPN`；
    文档叙述中的 `jizi-dragon` → `example-org`。
    原文清单见 `docs/HISTORY-REWRITE-NOTICE.md`（**该文件已于 2026-09-29 删除**）。
  - **刻意保留的功能锚点**（改了会坏自动更新/发布）：`desktop/package.json` 的 `publish.owner`、
    `desktop/proxy.js` 的 `PROBE_URL`、`tools/publish_release.py` 的 `REPO`、
    `tools/verify_update_proxy.js` 的资产 URL 基准。该标识本就公开写在仓库 URL 里。
  - **核查结论：历史中无密钥泄漏**。对 72 个提交全量扫描 GitHub PAT / AWS `AKIA` / Slack `xox*` /
    JWT / `PRIVATE KEY` / `password|token|secret|api_key = "…"` 赋值形态，**全部 0 命中**；
    `sk-` 命中均为 CSS 类名，44 位 base64 命中均为 `package-lock.json` 的 SRI 哈希。
    Fernet 密钥与 API Key 只存在于未入库的 `%APPDATA%\AksoWorkbench\workbench.db`。

- **`ruff` 由 19 条违规清零（`uvx ruff check .` 全绿）**，恢复 lint 门禁：
  - `workbench/api/routes_insight.py:175` **F821 `Any` 未导入**（`_workspace_dirs(row: Any)`）——
    因模块有 `from __future__ import annotations`，注解被惰性化才没在运行时炸；补 `from typing import Any`。
  - `tests/test_env_isolation.py:127` **B017 裸 `Exception` 断言**改为断言 `httpx.HTTPError`：
    原写法把「实现里换了个完全无关的异常」也判成通过，等于什么都没钉住。
  - 删除 3 处真正未使用的导入（`tools/diag_electron_typing.py` 的 `time`、
    `tools/verify_accounts_ui.py` 的 `json`、`tests/test_env_isolation.py` 的 `Path`、
    `tests/test_replay.py` 的 `run_understand`）；修正 6 处 import 排序。
  - `tools/publish_release.py:360` B007 未使用的循环变量 `i` → `_attempt`。
  - **新增 `[tool.ruff.lint.per-file-ignores]`：`"tools/*.py" = ["E402"]`** ——
    `tools/` 下的验证脚本是「会被执行的一次性脚本」而非可导入模块：必须先 `stdout.reconfigure`
    设编码、拉起服务、等端口就绪，**然后**才 import playwright（放顶部会让驱动逻辑在导入期就跑）。
    这是刻意的引导式写法，仅豁免 E402；未定义名（F）与排序（I）等规则仍生效。

- **平台洞察：关系网的自环判定恒假（拿编码比显示名）**。`build_relation_graph` 用字段里的
  `referenceObjectCode`（**编码**）去比 `object_name`（**显示名**），比较永远不成立 →
  `enableTree=false` 时树形自引用**根本没被过滤**，`selfLoop` 标记也永远是 false。
  现改为比编码（新增 `object_code` 参数，`understand` 传入）。
- **平台洞察：任务状态会把「部分成功」记成成功**。`run_understand` 原先按「没抛异常」计数成功，
  平台返回空响应也会走到成功分支 → 10 个对象只读到 1 个仍记 `succeeded`。
  现改为按**每对象覆盖度**计数：只有 `coverage.status != degraded` 才算成功，
  并映射为任务状态 `complete→succeeded / partial→partial / degraded→failed`。
- **测试台架：`FakeEgmpClient.get` 的前缀路由顺序写反**。`BasicObject/` 的通用分支排在
  `BasicObject/field/` 与 `BasicObject/lifecycleStatus/` **之前**，后者被误判成对象元数据
  （真实平台靠路由区分，不靠前缀）。此前未暴露是因为假客户端自己实现了便捷方法绕过 `get()`；
  录制回放（走 `get()`）把它翻了出来。
- **平台洞察：跨环境/跨账号共用 token 与元数据缓存串台（正确性）**。
  token 缓存原先**只按 `env_id` 分键**、缺省回落 `token-cli.json` —— A 环境的 token 会被交给
  B 环境的 base_url 使用；同一环境的两个账号（真实数据里示例租户就有 `dev-user` 与 `T0901`）共享同一张票，
  第二个账号实际以第一个的身份运行。元数据缓存 `QueryCache` 又是进程级全局单例、键不含环境，
  四个真实部署在同一进程内查询同名对象（如 `init_status__c`）时会互相污染。现改为：
  - token 缓存按 **`(base_url, username)`** 分键，载入时校验归属（`baseUrl`/`username` 双重比对），
    按 JWT `exp` 提前判失效，改**原子写**（临时文件 + rename），`env_id` 退为文件内元数据；
  - 缓存键加环境作用域 `{scope}|{kind}:{code}`，`scope` = 归一化 base_url；
    按对象失效改为**跨全部环境**清（否则另一个环境会留旧值）；
  - 顺带修：`get_or_set` 原先用 `None` 同时表示 miss 与「缓存了 None」，
    导致平台合法返回空响应时每次重新发请求，现用哨兵区分。
  - 验收：`tests/test_env_isolation.py` **19 项**。
- **平台洞察：生命周期四列表两列同时出错（进入动作与用户动作混归类）**。`executeType=2`
  （进入状态时自动执行）的动作若经动作栏解析出「修改状态」，会被塞进 `userActionEdges`
  → 报告的「进入动作」列**缺项**、「用户动作」列**多项**。现按 `executeType` 归类，
  并给每条边加 `mechanism`（`button` / `enterAction` / `lifecycleStatusIds`）。
- **平台洞察：同名按钮跨状态错配**。`_consume_match` 的 docstring 写「同状态内」，代码却遍历
  **全部状态**的动作栏行——实测 `sa` 的「提交」被配到 `sb` 的动作栏行。现只在「本状态自身的动作」
  与「无法归属任何状态的动作」里按名称匹配，**明确属于别的状态的 id 一律排除**
  （后者保留，因为 GetActionBar 返回的 id 有时确实不解析，这正是该兜底存在的理由）。
- **平台洞察：有「发起工作流」的动作却不产出 drawio（键空间错配）**。`GetActionBar` 的 `targetId` 是
  **workflowBasicId**，而 `workflows_by_row_id` 与 `workflow_graphs` 都按**工作流行 id** 建键——
  原先直接拿 basicId 去查，既取不到工作流名也取不到流程图。现建一次反查索引修正。
- **平台洞察：工作流触发状态未使用**。工作流行上的 `lifecycleStatusIds`（上游实测 195/707 条携带）
  在移植版中完全没用，只靠按钮发现工作流、覆盖面窄于上游。现新增 `workflowTriggerEdges`
  与按钮通道**取并集**，只认已知状态 id（不产出悬空触发），报告新增「工作流触发状态」章节。
- **平台洞察：报告未标注进入条件为推断值**。四列表头改为 **`进入条件（描述推断）`** 并加固定注脚——
  平台没有进入条件的读接口（上游六轮 48+ 路径探测均不存在），不能让使用者误以为是结构化事实。
- **平台洞察：drawio 产物对任何输入都必须良构（三处缺陷）**。此前 `.drawio` 在特定输入下
  **完全无法打开**，且标签显示错误：
  - **双重转义**：`_label` 先转义、`_node_xml` 又转义一次 → 换行变成字面 `&lt;br&gt;`、`&` 变成 `&amp;`。
    现改为**单层转义**，并以实证口径修正换行表示：draw.io 自身用 **`&#xa;` 字符引用**而非 `<br>`
    （字面 `<` 在 XML 属性值里非法）；多行标签另补 `whiteSpace=wrap`。
  - **非法 XML 字符未清除**：C0 控制字符在 XML 1.0 中即使写成字符引用也非法，
    名字里出现一个（如 U+000B）即让整个文件报 `not well-formed`。现已统一清除。
  - **节点 id 与根单元冲突**：平台步骤 id 若为 `"0"`/`"1"`，会与 `<mxCell id="0"/>`/`id="1"` 重复。
    现 `cell_id()` 为**纯函数**（`n_<清洗名>_<短哈希>`），跨模块天然一致且永不撞根单元。
- **平台洞察：spider 网络图产出悬空边引用（运算符优先级 bug）**。`build_network_model` 原写
  `_find(...) or f"obj:{target}" if target else None`，实际解析为
  `(_find(...) or f"obj:{target}") if target else None` —— 为「工作流名」这类目标造出
  `obj:工作流「X」` 这种**永不存在的节点 id**（已用真实 spider 数据实测确认）。
  现改为三级解析：状态节点 → 已爬对象节点 → 兜底挂到「来源状态所属对象」；两端都定不了位时
  置 `unresolved: True`（产物里可见），drawio 侧不再画悬空边。
- **平台洞察：对象 code 路径穿越（可写到产物目录之外）**。对象 code 由用户输入且此前只做 `strip()`
  就拼进文件名，实测 `../../PWNED` 的产物 resolve 后落在 job 目录之外、`C:/Temp/pwn` 会写到 `C:\Temp\`。
  现新增 `workbench/services/paths.py`：`safe_path_component()` 剥离路径分隔符/Windows 非法字符/
  零宽与双向控制符/首尾点空格并处理保留设备名（**正常编码与中文恒等通过，不改名**），
  `resolve_within()` 落盘前断言仍在目标根内。同时 `routes_insight.py` 的产物读取守卫由
  **字符串前缀比较**改为 `Path.is_relative_to()`，并拒绝 `artifact_dir` 为空的行
  （原先会退化为「CWD 下任意文件」）。
- **全站权限切换后遗留的误导性诊断文案（0.3.5）**：`manifest` 早在 0.2.21 就改成声明式
  `host_permissions: ["<all_urls>"]`（装载即得、永不弹窗），但扩展内若干提示仍在暗示
  "浏览器未授权、需去站点页面点一次授权"，排障时会把人引向不存在的动作。本轮只改文案与
  注释、不动逻辑：
  - `parallel-session.ts` 401 自愈：`仍未获站点授权（需在该站点页面完成一次授权）` →
    `在手动停用名单内，规则不生效（如非本意请在扩展弹窗恢复）`；`授权有效` → `网络平面可用`；
  - `syncAccountRules` 的安装尾注：`未获站点授权，规则待授权后自动生效` →
    `在手动停用名单内，规则不生效`；
  - 模块头、`selfHealRules` / `statusOf` / `reapplyAllRules` 注释、webRequest 监听注释同步
    改为"网络平面健康 = 未被手动停用"的口径（webRequest 注释另更正：改声明式权限后事件
    覆盖全部站点，归属仍只认绑定页签）。
  - 复跑：`npm run typecheck` 零错、`verify_extension_boot.py` 7/7、
    `verify_extension_isolation.py` 8/8、`verify_host_logic.mjs` 22/22。

### Added

- **新增 `workbench/services/egmp/kernel/` 平台知识层（读写路径共用）**：把此前「各写一遍」的
  平台约定收成单点。
  - `kernel/endpoints.py`：**唯一**端点表（49 条），含命名空间归属（版本化 `/api/openapi/v1.0/*`
    vs 私有无版本 `/api/platform|config/*`）、`render()` 模板展开（路径参数做 URL 编码）、
    `namespace_of()` / `is_versioned()`；补齐此前表外缺失的 6 条，以及上游给出但从未实现的
    3 条发现链探针（`BasicObject/QueryList|GetPageList|Page`）。
  - `kernel/semantics.py`：dataType / behaviorType / stepType / dispatchMode / 后缀约定等实证词典
    ＋判定函数；新增 `object_suffix_of()` 与 `*_name()` 助手——**未登记的枚举值回
    `dataType=<n>` 而不是静默消失**。
  - `auth.py` 与 `writers/endpoints.py` 保留为**转发垫片**，既有调用点一行未动；
    旧名（`MENUGROUP_QUERY` / `OPENAPI_*`）以 `LEGACY_ALIASES` 显式标注保留。
  - **insight 与 egmp 顶层的硬编码端点字面量归零（36 处）**；后缀约定不再在
    `crawler.py` / `annotate.py` 各写一遍。`tests/test_kernel.py`（35 项）用**源码扫描**钉死：
    出现硬编码端点、模板未展开、常量重复定义都会立刻红。
- **新增 `workbench/services/egmp/replay.py` + 离线回放夹具**：把真实平台的响应录成夹具，
  之后无需联网即可跑通洞察全链路。含 `Recorder`（包住真实客户端录制，**失败也录**）、
  `ReplayClient`（按夹具回放，返回值深拷贝）、夹具格式与版本校验。
  两条覆盖度保证：回放时不得有请求缺失（`ReplayMiss` 会列出可用键），
  也**不得有夹具条目从未被用到**（能抓住夹具腐化）。
  配套 `tests/fixtures/egmp/spider-training-mgr.json`（**真实形态**夹具：结构取自 `Recorder`
  对假平台的实跑录制 11 条交互，ID 与业务命名换成确定性占位值，**不含真实平台数据**）
  与 `tools/record_insight_fixture.py`（`--command spider` 对着真实环境录一次即可长期复用；只走只读链路）。
  > 原名 `understand-capa-plan.json` 的夹具随 `understand` 下线删除（2026-09-14）。
- **新增 `workbench/services/egmp/insight/workspace.py` + 迁移 14**：洞察产物落到**用户可配的工作区**
  `<root>/<对象code>/<时间戳>/`，每对象一格并各带一份 `manifest.json`（含 `schemaVersion`、环境、
  覆盖度三态、产物 sha256、失败清单）。新增设置键 `insight_workspace_root` 与接口
  `GET/PUT /api/insight/workspace`；`insight_runs` 增列 `workspace_dir` / `coverage_status`
  （**只追加迁移 14，1–13 不动**）；产物读取接口优先从工作区取、历史行回落 `artifact_dir`。
- **新增 `tests/test_replay.py` 与 `tests/test_workspace.py`**：
  离线回放与工作区/manifest 门禁（`inventory`/`understand` 下线后重挂到 spider 夹具：
  回放 17 项、工作区 34 项）。
- **新增 `tests/test_lifecycle_coverage.py`（13 项）**：L3 生命周期图的覆盖面与语义门禁——
  同名按钮跨状态不得错配、进入动作必须归入「进入动作」、`lifecycleStatusIds` 与按钮通道取并集、
  未知状态 id 不得产出悬空触发、进入条件仍取 `description`。
  > **2026-09-14 已随 `understand` 下线删除**：这些断言全部挂在 understand 的 L3 产物上。
- **新增 `tests/test_env_isolation.py`（19 项）**：环境与身份隔离门禁——token 跨环境/跨账号不泄漏、
  归属校验、JWT `exp` 优先、原子写、缓存作用域、假值可缓存。
- **新增 `tests/test_drawio.py`（57 项）**：drawio 良构门禁——对抗性标签（`&`/`<`/`"`/换行/C0 控制字符/
  中文/emoji/空名）、id 为 `"0"`/`"1"` 的节点、重复步骤 id、并行同源边、成环分层、坐标重叠。
  断言分两层：**先良构**（`ET.fromstring` 必须成功），**再语义**（解析回来的属性值等于期望文本）。
- **新增 `tests/test_paths.py`**：路径安全门禁——正常编码恒等性、逐条对抗性输入
  （`../`、绝对路径、保留设备名、控制字符、双向覆盖字符、尾点空格、超长）。
  真实落盘的端到端断言 2026-09-14 移到 `tests/test_workspace.py`（`understand` 下线时一并搬）。
- **新增 `tools/verify_insight_drawio.py`（11 项真机验收）**：把对抗性产物交给 **draw.io 桌面版 CLI**
  读一遍再回写，回写结果仍需良构——这是「标准 drawio」的最终判据。
- **新增 `workbench/services/paths.py`**：`safe_path_component()` / `resolve_within()`，
  作为外部输入进入路径成分的统一收口点（详见 Fixed 段的路径穿越条目）。
- **新建 `docs/INSIGHT-SPEC.md`（**该文件已于 2026-09-29 删除**）**：平台洞察（insight）的**功能定位与产品能力规格**——
  定位（只读配置理解引擎，与配置工厂共用同一套 API 知识体系）、5 项核心能力与非目标、
  平台知识层的目标结构与端点表唯一化、产物与存储模型（可配工作区根目录 + `<对象code>/<时间戳>/` + `manifest.json`）、
  6 条已知边界（含「进入条件为描述级」这一产品能力天花板）、验收标准与 4 项质量门禁、数据与重构纪律。
  四项关键决策已拍板：进入条件接受描述级并显式标注 / 工作区可配 + 自动时间戳目录 /
  抽取显式平台知识层供读写两路径共用 / 修原生 drawio 生成器并加 well-formed 校验。
- **新建 [`docs/PITFALLS.md`](docs/PITFALLS.md)**：22 条真机环境坑，原 `AGENT.md` §5 的唯一留存处。
  相对原文新增**复核列**（✅ 已对码核实 / ⚠️ 仍成立但有新变化 / — 本轮未复核）与「只增不改、编号追加」的
  维护规则；其中 #9 网络代理补记 2026-09-13 实测（`build.ps1:71-75` 仍硬编码直连 push，与「直连会被重置」
  的结论相反；`AKSO_PROXY` 到不了 egmp 的 Python 路径），#15 补记 `_next_seq()` 吞掉 DB 写异常会让
  指令序号从 1 重来的残留风险。
- ~~**新建 `docs/方向调研-平台洞察独立发展.md`**~~：平台洞察（insight）
  独立发展调研——现状诊断（真实库零运行记录、平台耦合面未验证、产物会「看起来完整」）、准备清单
  （P0 可验证性 / P1 契约化 / P2 能力深化）与九类边缘规则验收清单。**调研文档，不含代码改动。**
  > **2026-09-17 已删除**（内容作废）：见本文件「未发布 · Removed」。该文引用的两份文档从未入库，
  > 且其「真实库零运行记录」的诊断**已被实测推翻**（`insight_runs` 现为 15 行）。原文 `git show d0efa84:docs/方向调研-平台洞察独立发展.md`。
- **文档体系整体重构（v0.3.4 基准，本轮不动代码）**：把「一份自述版本的架构文档」拆成按
  **变更频率**分层的常驻文档，并新建四份此前缺失的文档。
  - **新建** `AGENT.md`：接手手册——项目快照、代码地图、**六条维护红线**、
    **已知问题清单**（代码缺陷 13 / 安全与产品级 5 / 测试缺口 4 / 构建脚本债 14 / 文档债 3）、
    **22 条环境坑**（含实测出处）、验收基线（每轮必跑 / 改扩展必跑 / 改蓝图必跑 / 改更新必跑）、
    真机验收记录与待人工复验项。
  - **新建** [`docs/API.md`](docs/API.md)：60+ 端点按 13 个路由域手写权威契约（方法/路径/请求/响应/错误码），
    并设**四类特殊通道专章**：SSE 对话事件序列、SSE 洞察进度流、`/extension` 长轮询与 `seq` 游标、
    产物读取的路径守卫；附壳控制服务 `:18767` 全部端点。
  - **新建** [`docs/SCHEMA.md`](docs/SCHEMA.md)：迁移账 1–13 逐条清单、9 张现行表的列级定义、
    非数据库产物路径与表的对应关系、**扩展协议契约**（快照 / 备份文件 / 指令 / 状态上报），
    以及五步「改表清单」。
  - **新建** [`docs/CONFIG.md`](docs/CONFIG.md)：**五层配置全覆盖**（环境变量 / `settings` 表键 /
    代码内行为参数 / 数据目录文件 / 前端硬编码常量），每项标注默认值、实测值与**「改了会怎样」**；
    附「我想改 X，该动哪里」速查表。
  - **新建** [`docs/USER-MANUAL.md`](docs/USER-MANUAL.md)：安装与扩展装载、五个模块的操作步骤、
    两条登录路径的差异说明（用户 Chrome vs 内置 Chromium）、数据位置与安全须知、11 组 FAQ。
  - **新建** `docs/EXTENSION-PLANE.md`（**该文件已于 2026-09-29 删除**）：扩展侧权威文档——六平面隔离原理、
    与上游的差异对照、桌面↔扩展三面协议（数据面/指令面/状态面）及**四条必读护栏**、
    私有改造史、验证基线、安全边界（含平台侧不可根治的越权方向）。
  - **新建** 10 篇决策记录：SQLite 选型（承接原 `docs/数据层决策.md`）、
    浏览器分配政策、Electron 壳、扩展=执行面、四项目原生化、browser_pool 单线程、
    凭据不落明文、更新通道与代理、NSIS 分发、Monitor 不回放危险操作。
    每篇含**背景 / 决策 / 取舍（含被放弃的方案）/ 后果 / 复核触发器**。

### Changed

- **全仓 `AGENT.md` 引用重指**：原 **59 处 / 21 个文件**（7 篇决策记录、`docs/API.md` 7 处、`docs/CONFIG.md`、
  `docs/SCHEMA.md`、`docs/USER-MANUAL.md`、`docs/EXTENSION-PLANE.md`、`extensions/quick-login/README.md`、
  `docs/archive/`）。重指后全仓指向 `AGENT.md` 的 markdown 链接为 **0**；`CHANGELOG.md` 内的两处历史记录
  **保留原文不改写**，仅把链接语法降级为代码体，避免留下死链。
- **订正两处随 `AGENT.md` 一起失效的事实**（源头均为其 B7 / §7.1）：扩展
  `extensions/quick-login/package-lock.json` 的 version **实测已是 `0.3.5`**（原文与 `CONTRIBUTING.md`
  均称停在 `0.2.20`；只有 `desktop/package-lock.json` 确实仍是 `0.2.1`）；
  `CONTRIBUTING.md` 的 `uvx ruff check .` 一行补注「本机 `uv`/`ruff` 均不存在，该门禁当前无法执行」。
- **`docs/CODEBASE_OVERVIEW.md`（**该文件已于 2026-09-29 删除**） 复核重写**：原锚定 v0.2.2（落后 8 个版本，
  其「11 个路由域 / 迁移 1-11 / Inno Setup / 50 测试函数 / 双写版本号」均已失效），本轮按代码实测重写并
  锚定 `d0efa84`（v0.3.5）。行数口径改为**物理行数**；补齐验收基线实测（`pytest` **63 passed / 1 failed**、
  `verify_packaging.py` **8/8**、`ruff` 无法执行）与完整风险台账；并加注 `AGENT.md` 承接说明。
- **README.md 收敛为「产品定位 + 用户向快速开始 + 文档地图」**：删去开发/构建内容
  （全部移入 `CONTRIBUTING.md`），补上「它解决什么问题」对照表与四个来源项目的能力沉淀表。
- **CONTRIBUTING.md 接管全部开发内容**：环境搭建（强调 `--extra build` 不能省、uv 不在 PATH 时的替代命令）、
  依赖变更流程、**构建与发布全链路**（6 步链路、版本规则与五写、发布三道自查、构建前必跑体检、
  PS5.1 脚本纪律、**顺序敏感的两处**）、代码规约、开发流程与分工边界、
  **已知状态诚实记录**（ruff 9 条违规未绿、`verify_packaging.py` 未被 `build.ps1` 调用、
  两份 lockfile 版本陈旧等）、验收纪律。
- **扩展侧文档合并**：`extensions/quick-login/` 下上游 7 份文档（README / PROJECT-STATUS /
  CODEBASE_OVERVIEW / DIAG-GUIDE / USER-MANUAL / BROWSER-ONLY-MULTILOGIN-RESEARCH / DESIGN）
  与上游 CHANGELOG 移入该扩展的 `docs/archive/`（加 `UPSTREAM-` 前缀与归档抬头），
  权威内容合并进根侧 `docs/EXTENSION-PLANE.md`；原位置留指针 stub（README）；
  **上游 CHANGELOG 与本项目私有改造 CHANGELOG 拆成两份**（前者 490 行上游发布史存档，
  后者按本项目版本号组织并标注「同步上游时需重新摘除」的 ⚠️ 条目）。

### Removed

- **`AGENT.md` 删除：内容按变更频率拆分承接（文档体系收敛）**。它把代码地图、六条红线、已知问题台账、
  环境坑、验收基线、文档分工全部混在一份「接手手册」里，随代码演进多处失真。2026-09-13 复核确认的
  具体错误：§8 的 `uv`/`uvx` 路径在本机**并不存在**（venv 已不再内置这两个 shim，`pyvenv.cfg` 记
  `uv = 0.12.10`）、§1 的项目快照与实际库不符（实测 **环境 4 / 账号 5 / 洞察运行 0**，原文记
  「环境 3 / 账号 4 / 洞察运行 4」）、B7 的扩展 `package-lock.json` 版本已过期、全篇行数为**漏算空行**
  的偏低口径（`Get-Content | Measure-Object -Line` 会跳过空行；`workbench/**.py` 实为 10,172 行而非 8,167）。
  删除后按变更频率重新落位：
  - **§5 环境坑 22 条 → 新建 [`docs/PITFALLS.md`](docs/PITFALLS.md)**（只增不改，机构记忆）；
  - **§2 代码地图 / §3 六条红线 / §4 已知问题台账 → `docs/CODEBASE_OVERVIEW.md`（**该文件已于 2026-09-29 删除**）**
    ——台账编号 **C/S/T/B/D 原样保留**，以免打断 `docs/API.md`、`docs/SCHEMA.md` 与 7 篇决策记录中按编号的既有引用；
  - §7 验收基线 → 同文件「验收基线实测」与 [`CONTRIBUTING.md`](CONTRIBUTING.md) §7；
  - §6 数据与运行时目录 → [`docs/SCHEMA.md`](docs/SCHEMA.md) / [`docs/CONFIG.md`](docs/CONFIG.md)（原本已在那里）。
- `docs/架构分析.md`、`docs/SESSION-DIGEST.md`、`docs/数据层决策.md` 移入 `docs/archive/`
  （分别改名 `ARCHITECTURE-2026-09-10.md` / `SESSION-DIGEST-2026-09-10.md` / `DATA-LAYER-DECISION.md`，
  加归档抬头并写明内容去向）。原因：前两份自述版本为 v0.2.2 且与现状有实质偏差
  （迁移账、测试项数、依赖口径、桌面分发方式都已变）；第三份已完整并入。

### 说明

- 本轮**以文档为主，仅附带清掉三处已失效的引用**（均为零行为变更）：
  ① 6 个服务端模块的文档字符串原先指向**已删除**的 `docs/模块契约.md` / `docs/迁移台账.md`，
  已改指 `docs/API.md` / `docs/CONFIG.md` /；
  ② `api/routes_agent.py` 的 `run_insight` 工具描述仍写「子进程封装」，已改「egmp.insight 原生实现」；
  ③ `adapters/*.json` 的 `$schema` 由死链 `docs/模块契约.md#adapters` 改为 `docs/SCHEMA.md`（仅此字段）。
  另把 `tools/setup.ps1` 的 Node 检查从**必检项降为 INFO 信息项**并改写文案（原文案「洞察/工厂功能不可用」
  与 矛盾）。
  修完已复跑：`pytest` **64 passed**、`verify_packaging.py` **8/8 PASS**、两个 `.ps1` 语法检查通过
  （`setup.ps1` 编辑后重补了 UTF-8 BOM）。
- 盘点中发现的其余代码缺陷与安全风险一律以
  已删除的 `AGENT.md` §4 的「已知问题清单」形式落盘（代码缺陷 10 条、安全与产品级 5 条、
  测试缺口 4 条、构建脚本债 11 条），修复另开一轮。

## [0.3.4]

### Added

- **出网通道自动选择（0.3.4，实测倒逼；用户定稿："能直连就别配代理"）**：`v0.3.3` 发布后真机
  验证发现一个致命断层——更新**能发现新版、却下载不下来**。逐跳实测：查版本走
  `github.com/<o>/<r>/releases.atom`（1.9s）、资产走 `github.com/…/releases/download/…`（302）
  → CDN `release-assets.githubusercontent.com`（很快）；而本机**直连 `github.com` 是间歇性的**
  （同一 URL 1.1s 成功与 20s 超时都出现过），Chromium 默认又不真用系统代理（注册表
  `ProxyEnable=1` 摆着，请求仍直连超时）→ 于是表现为"检查得到、下载不动"。
  修法：新增 `desktop/proxy.js`，**默认优先生成直连**，只有直连探测失败才回落到代理：
  **环境变量 `AKSO_PROXY`/`HTTPS_PROXY`（强制代理）→ 数据目录 `proxy.txt`（一行=强制代理；
  空文件=强制直连，终极兜底开关）→ 自动（直连探测 Atom feed，通就直连、不通再用 Windows
  系统代理）**；探测带 6s 超时（直连失败在本机表现为"卡住不返回"，无超时会挂死启动）。
  三个必守细节：**必须显式放行回环**（`proxyBypassRules = <local>;127.0.0.1;localhost;[::1]`，
  否则壳与 sidecar/CDP(18765/18766/18767) 的本地通信会被塞进代理，表现为"网络正常但功能全废"）；
  通道在**任何联网动作之前**定下（`app.whenReady()` 里、updater 之前）；选中的通道与探测结果
  写进 `shell-state.json` 的 `proxy` 字段并透出 `GET /api/update`，排障一眼可见。
  实测：直连可用时选直连（探测 1131ms、取资产 1201ms），**完全不经过代理**。
- **`tools/verify_update_proxy.js`**：用**真实 Electron 网络栈**（`electron.net.fetch`，即
  electron-updater 内部同一套）对真实 Release 资产 URL 做基线 + 应用自动策略后的对比。
  判定以"策略生效后必须拿到 `latest.yml`"为准；直连基线成功不算失败（那正是 auto 想要的结果）。
- **`tools/publish_release.py`**：发布到 GitHub Releases 的可核对工具（建 draft → 传资产 → 转正 →
  匿名复验 `latest.yml`）。发布前三道自检：`latest.yml` 版本号 == 发布版本、其 sha512 == 安装包
  实际 sha512、指向本次安装包——发布错的清单会让**所有客户端**更新失败。资产顺序是"先小后大"
  （350MB 最容易失败，先把重试预算花在大头上）。支持 `--no-proxy`：关掉 Clash 后 **git 自己的
  `http.proxy` 仍写着 127.0.0.1:7890**，脚本会对着没人监听的端口连（`WinError 10061`）——
  "我关了代理"这句话必须在脚本里有对应开关。

### Changed

- 托盘「检查更新…」改为先**同步**把 `phase=checking` 落盘、再弹结论弹窗：手动检查在无新版/开发态
  会走弹窗分支，用户不点按钮时状态会永远停在 checking，排障与自动化验证都看不到结论。

## [0.3.3]

### Added

- **桌面端自动更新（0.3.3，用户定稿：通道 = GitHub Releases；策略 = 启动检查 + 静默后台下载 + 退出时安装 + 手动检查）**：
  - **壳内更新引擎**：新增 `desktop/updater.js`（状态机 + 事件接线，纯数据 `state()` 便于纯 node 验证）。启动后 20s 首次静默检查、此后每 6h 一次；`autoDownload=true` 后台静默下载（**不弹窗、不抢焦点**）；下载完成后只改托盘提示与账号中心角标，真正安装交给「你退出应用时」——`autoInstallOnAppQuit=true`，并在 `before-quit` 里**先 `killServer()` 再拉安装器**（顺序关键：安装器要覆盖 `AksoServer.exe` 与扩展目录，sidecar 还活着会锁文件）。托盘新增「检查更新…」（结论必落弹窗）与「关于 vX.Y.Z」；账号中心右上角版本角标可点击即手动检查。
  - **跨进程状态通道**：壳每 15s 把状态写 `%APPDATA%\AksoWorkbench\shell-state.json`（原子写；`%APPDATA%` 规则与 `workbench/config.py` 的 `DATA_DIR` 对齐），Python 侧 `workbench/api/routes_update.py` 提供 `GET /api/update`（超 60s 无心跳判 `live=false` → UI 显示「桌面壳未运行」）与 `POST /api/update/check|install`（代理到壳控制服务 18767）。开发态（未打包）返回 `phase=unsupported`，弹窗提示改用 `git pull`，**绝不误报**。
  - **为什么读文件而不是每次 HTTP 探壳**：账号中心每 3s 刷新，读本地 JSON 更便宜且壳不在时能优雅降级。
  - **为什么不做「静默自动重启安装」**：会打断正在进行的自动登录/页面操作——静默下载 + 退出时安装是「零打扰」与「必达」的平衡点。
- **扩展升级后自动检测「需重新加载」（0.3.3）**：扩展随安装包更新（`resources/extension` 被覆盖），但 Chrome 只在「重新加载」后才用新代码 → 此前升级后可能长期跑旧扩展而无任何迹象。现在：扩展在 `POST /extension/state` 上报自身版本（`extVersion`，**账号映射为空也照发**——旧实现 `if (!items.length) return` 会让这条事实永远传不出去），桌面端比对版本并把 `extVersion` / `desktopVersion` / `extStale` 加进 `GET /extension/health`；账号中心据此显示「浏览器扩展版本过旧」提示条 →「重新加载引导」= `POST /extension/setup-helper {mode:"reload"}` → 壳打开 `chrome://extensions` 并给出「点 ↻ 重新加载」步骤说明（与首次安装共用同一套壳能力，仅文案不同）。**刻意不做自动 `chrome.runtime.reload()`**：重载会掐断进行中的自动登录，把不可控时序留给用户点一次。
- **`tools/verify_updater_logic.mjs`（47 条断言，纯 node 秒级）**：用 `Module._load` 钩子把 `electron` / `electron-updater` 换成桩，验证开发态不崩、打包态装配（`autoDownload`/`autoInstallOnAppQuit`/`allowPrerelease`）、事件链（checking → available → progress → downloaded）、就绪后再检查走短路、无更新、失败路径（自动静默 / 手动弹窗）、`installOnExit` 参数与幂等、下载完成提示不打断。⚠ 脚本注释记下一个真实踩坑：**桩必须在 `init()` 期间仍然生效**——真实 `electron-updater` 包在模块体里就调用 `app.getVersion()`，只在 load 期间挂钩会让 init 内部那次 require 落到真实包并抛错，而错误会被 `init` 的 try/catch 静默吞掉（表现为 `supported` 恒为 false）。
- **扩展版本号纳入自动同步（0.3.3 修正）**：`tools/bump.py` 的「扩展双写」路径此前指向**不存在**的 `packages/extension/package.json`（唯一存在的是工作区根 `extensions/quick-login/package.json`），因有 `exists()` 守卫而**静默少写一次**——规则本意是"扩展版本随项目演进"，实际只写了 manifest。路径已修正，扩展版本面重新变为 manifest + 工作区 package.json 两处齐步走。

### Changed

- `tools/build.ps1` 的发布说明与 `docs/EXTENSION-INSTALL.md` 第四节新增「发布更新到 GitHub Releases」：含 **GH_TOKEN 的完整获取路径**（头像 → Settings → Developer settings → Personal access tokens → fine-grained（Contents: Read and write，选 `example-org/akso-mogul`）或 classic（勾 `repo`）→ Generate → 只显示一次的复制时机）、两种发布方式（`--publish always` 注入 `$env:GH_TOKEN` / 网页手动传 `setup.exe + latest.yml + blockmap`）、tag 与版本号必须一致的顺序要求、token 有效性与吊销的核对方法。**更新器匿名读取 Release ⇒ 仓库必须公开**（本仓库即公开）。
- 账号中心右上角版本角标从静态 `<span>` 变为可点击按钮：平时只显示版本号，有新版/下载中/已就绪时分别上色（`has-update` / `is-ready`），`title` 给出具体状态；浏览器直开本页（无壳）时依旧是纯展示，不影响任何既有功能。

### Fixed

- **安装引导提示条每次都弹（0.3.2，用户实测反馈）**：「未检测到浏览器扩展」横幅的判据此前放在**浏览器内存**里（`extEverConnected`）→ 刷新页面/重启应用即复位，于是每次打开账号中心都会重新弹出。改为**按安装实例落库的一次性闩锁**：`/extension/state` 收到执行面上报（或 `/extension/health` 判为已连接）的**第一次**即把 `ext_connected_once=1` 写入 settings 表，`/extension/health` 随之返回 `everConnected`；前端只在 `!connected && !everConnected` 时显示横幅。**语义（用户定稿）：以"能否与浏览器连接成功一次"为依据——连上过一次即永久静默**（之后即便关掉 Chrome 也不提示），单纯的 `connected=false` 不再触发提示。找回入口仍在托盘「安装浏览器扩展…」。
  - 实测（隔离实例 + 跨进程重启）：全新装机 `everConnected=false`（会提示）→ 首次上报后 `true`（立即静默）→ **重启服务进程后仍为 `true`**（核心回归点）→ 闩锁确实落在 settings 表。
- **`tools/build.ps1` 新增 `-Bump build|push|none`**：此前版本演进只有 MINOR+1 一条路，补丁修复也会被抬成 MINOR 版本（本次 0.3.2 即为 `-Bump push` 的补丁发布）。

- **打包链路的两个静默断点（0.3.1 首次真机构建暴露）**：
  - **`workbench/server_entry.py` 丢失** → `server.spec` 仍指向它，PyInstaller 直接失败（`script … not found`），而开发态走 venv + uvicorn 完全不受影响，故长期无人察觉。已重建：**绝对导入**（PyInstaller 把入口当顶层脚本执行，相对导入必 ImportError）、接受壳的 `--server` 调用约定、**不打开浏览器**（打包态 UI 由 Electron 主窗承载）、冻结态把启动信息落 `DATA_DIR/server.log`（壳以 `stdio:'ignore'` 启动，现场零输出）。实测：`AksoServer.exe` 正常起服，日志记 `AksoServer v0.3.1 pid=… (frozen=True)`。
  - **`desktop/icon.ico` 自首次提交起就是坏文件**：整份二进制被"当文本另存为 Unicode"了一次（UTF-16LE BOM + 每字节占 2 字节），且该过程**有损、无法还原**（git 历史里也只有坏版本）。后果两处且都静默：`electron-builder` 报 `image … shas unknown format` 导致**构建失败**；Electron 托盘图标一直是空白（被 main.js 的 try/catch 吞掉）。已新增 `tools/make_app_icon.py` 从品牌 PNG 重新生成 7 尺寸 ICO（16/24/32/48/64/128/256）。⚠ 两个坑写进工具注释：Pillow 的 ICO 写出**不会放大**（请求尺寸大于源图会被静默跳过），而 electron-builder 要求至少 256 → 必须先把源图 LANCZOS 放大到 256 再生成。
- 新增 `tools/verify_packaging.py`：**打包前置体检（秒级 7 项）**——spec 入口存在 / 入口只用绝对导入 / 壳 spawn 的 `resources/server/AksoServer.exe` 与 extraResources 映射一致 / 安装包要携带的扩展产物齐备且 manifest 版本 == 项目版本 / `tools/build.ps1` 具备 UTF-8 BOM。上述两个断点都能被它提前拦住，不必烧掉数分钟构建才失败。

### Added

- **安装包携带浏览器扩展 + 应用内一键安装引导（0.3.1）**：`desktop/package.json` 的 `extraResources` 增带 `extensions/quick-login/dist` → 安装后位于 `<安装目录>\resources\extension`，**无需联网下载扩展**。
  - 账号中心新增「未检测到浏览器扩展」提示条（数据源 `GET /extension/health`：TTL 60s 内有无执行面上报；**仅在从未连上过**时出现，关掉 Chrome 不会反复提示），按钮「一键安装引导」→ `POST /extension/setup-helper` → 桌面壳控制服务 `POST /extension-setup`：自动打开 `chrome://extensions` + 打开随包扩展目录 + 弹出分步说明；托盘菜单同样新增「安装浏览器扩展…」。
  - **为什么做不到静默一键装（逐条实证）**：Chrome 在 Windows 上拦截非商店 `.crx` 安装；`ExtensionInstallForcelist` 在 HKCU 下普遍不生效（[SO](https://stackoverflow.com/feeds/question/36208439)）、自托管 `update_url` 亦常见失败（[SO](https://stackoverflow.com/feeds/question/49473933)）；本仓库**无签名私钥**（只有 manifest 公钥 `key`），无法用既有 ID 重打 CRX。取舍与依据见新增 `docs/EXTENSION-INSTALL.md`（含"要真一键只能先上架商店"的后续路径）。代价：用户首次点 4 下，之后永久可用。
  - `tools/build.ps1` 增补：bump 之后**自动重建扩展**并校验包内 `manifest.json` 版本号 == 本次发布版本号（此前扩展不进安装包，故无此风险）；release commit 一并纳入扩展 manifest/package 与 `.version.json`。

### Changed

- **「桌面点击 → 浏览器打开」延迟优化（0.2.24）：约 1.1s → 约 30ms**。实测定位到延迟主项是**扩展每 2s 轮询一次指令队列**（量化延迟 0~2s，均值 ~1s），再叠加点击路径上一次 `tasklist` 子进程探测（~124ms）且与指令入队**串行**：
  - **长轮询取代轮询节拍**：`GET /extension/commands` 新增 `wait` 参数——无指令时服务端挂起请求（`threading.Condition`；`dispatch_command` 入队即 `notify_all`），指令延迟从「等下一拍」压到一次本机回环。默认 `wait=0` 保持原非阻塞语义（既有调用方与 `tools/verify_extension_sync.mjs` 不受影响）。A/B 实测：**平均 964ms → 20ms（≈50×）**，最坏 1671ms → 34ms。
  - **扩展侧改为长轮询流**（`sync.ts`）：新增 `commandStream()` 独立循环（`wait=15`），与 2s 的数据面 `tick()` 解耦。护栏四项：**消费互斥**（`consuming`——否则同一条 `par.open` 会开两个页签）、**流幂等闸**（`streaming`——alarm 每次触发都会调用它，无闸会累积并发循环）、**兜底轮询**（流 >25s 未取回时由 tick 补一次非阻塞拉取）、**离线退避**（`getJson` 返回 null 时退避 1.5s，避免热循环）。SW 被回收时退化为原 alarm 行为，不会更差。
  - **探测缓存 + 并行化**：`/extension/launch-chrome` 的 `tasklist` 探测加缓存（正结果 10s / 负结果 2s；实测探测成本 124ms → 命中 9ms，14×），拉起后立即标记「运行中」防连点多开；`wheel-picker.html` 与 `accounts.js` 的点击路径改为 launch-chrome 与指令入队**并行**（两者仍都 await 完才关轮盘——关窗会中断在途 fetch，冷启动时若 launch 没发出去会永久滞留指令）。
  - 新增回归工具 `tools/verify_command_latency.py`：独立实例上 A/B 旧节拍与长轮询，钉住该延迟契约（误删 `notify_all` 或 `wait` 会立刻红）。
  - **尚未优化（需产品决策）**：**Chrome 冷启动**（tasklist 124ms + Chrome 启动 1~3s 是硬成本）。可选两条：① `launch-chrome` 直接带登录 URL 打开，省掉「启动 → SW 起来 → 取指令 → 开页签」的往返（需配一条「绑定已开页签」的指令）；② 桌面应用启动或账号中心打开时预热 Chrome。

### Fixed

- **两个静默失效缺陷（0.2.23，红/绿验证）**：
  - **虚拟 Cookie 袋被无关操作清空**：MAIN 壳的 `Storage.prototype.clear` 补丁未区分存储实例，而 Cookie 袋住在 **localStorage** 命名空间——页面调 `sessionStorage.clear()` 会把袋（含 token）一并清空 = 登录态被无关操作销毁。修法：仅当 `this === window.localStorage` 才重置袋子（探测用 `try/catch`，避免沙箱 iframe 里裸读 `win.localStorage` 抛 SecurityError 穿出补丁）；`Storage.prototype` 其余补丁（getItem/setItem/removeItem/key/length）语义不变。`onNamespaceWrite` 的两存储 token 同步**刻意保留**（站点把 token 只写 sessionStorage 时仍需镜像进袋）。
  - **多 iframe 页面自动填表静默失效**：`auto-login.fillPasswordInIframes` 在第一个可访问 iframe 上就 `return Boolean(field)`——若该 iframe 不是登录表单，函数直接返回 false → 上层判「密码未填」→ **永不点提交**。修法：扫描全部 iframe（与 `readPasswordValue` 的全量语义一致），返回语义 = 「是否有 iframe 含密码框」。
  - 验证：新增 `tools/verify_extension_isolation.py`，**先红跑复现**（恰好两条 ★ 回归断言失败）**再绿跑 8/8**。

### Added

- **扩展端三个自动化回归工具（0.2.23）**——此前扩展端**没有任何自动化回归**，改动只能靠 typecheck（保类型不保运行期）：
  - `tools/verify_extension_boot.py`：隔离 profile 启动冒烟（SW 能否启动 / 命令清单 / storage 键 / 桌面数据面同步）。⚠ 必须 headful——MV3 扩展在旧 headless 下不加载（实测 `service_workers` 为空）。
  - `tools/verify_host_logic.mjs`：用 esbuild 单独打包 `host.ts` 后跑 22 条断言，锁住端口口径三平面分工（含 `127.0.0.1:18765` vs `:18996` 不串号、默认端口规范化、IP 父域不含端口）。
  - `tools/verify_extension_isolation.py`：自建 fixture 页 + 装载 dist 内容脚本产物，验 Cookie 袋隔离与多 iframe 自动填表（两条 ★ 即上述缺陷的回归点）。

### Changed

- **quick-login 架构清理（0.2.23，用户定稿：不影响功能）**：以「可达性分析 + 协议面审计」为依据清理死代码、合并重复实现，扩展收敛为**执行面**（收 `par.list` / `par.open` / `wheel.toggle` + 六平面隔离）。
  - **死文件 2,060 行**：`ui/parallel/*`（并行管理页 0.2.13 已退役：1,290 + 130 + 629 行）与 `ui/send.ts`。此前 `copyUiStatics` 仍把该页 html/css 复制进 dist，现 `dist/ui` 只剩 popup / wheel / theme.css。
  - **旧会话模型退役（≈260 行）**：删 `session-manager.ts`、`account-registry.ts`、`navigation.ts`（其唯一活口「页签关闭清凭证」迁入新建 `core/pending-login.ts`）、`Session` 类型、`SESSION_KEYS.sessionTabBindings`、`session.*` 协议与 SW 分支、`onErrorOccurred` 里的会话兜底。依据：`session.*` 在扩展内外**均无发送者**（桌面端只发 `par.open`/`wheel.toggle`），其绑定表恒空故兜底恒为 no-op。
  - **并行页专用协议（SW 死分发 ≈275 行，占该函数 55%）**：删 `par.create/update/delete/moveBox/renameBox/deleteBox/probeScheme`、`data.export/data.import`、`DataBackup`。它们不只是「没人调」——`sync.ts` 每 2s 用桌面快照对账并删除快照外账号，故本地增删改会在 2s 内被撤销，属**语义冲突**而非休眠能力。⚠ `parallelStore` 的增删改**保留**（`sync.ts` 直接调用，是活的数据面）。
  - **按 0.2.21 定稿保留**：`site-auth.ts` 与 `site.grants.*`（授权核心）、`par.grantChanged`（规则重装钩子）、`ql.diag`（台架/现场诊断）、`wheel.toggle`（桌面通道）。如实说明：清掉并行页协议后 `site-auth` 已无运行时入口，成为「休眠源码」（仅 `Scheme` 类型被引用）。
  - **协议键收敛为单一真源**：`__ql_ns_` / `__ql_cookies__` / `__auth_token__` / `__auth_user__` / `__device_fp__` / `QL_PAGE_TO_BRIDGE` / `QL_BRIDGE_TO_PAGE` 此前在 `shield-main.ts` 与 `parallel-session.ts` 各自硬编码（17 处），现全部 import `shared/constants.ts`（新增 `SHIELD_USER_KEY` / `SHIELD_DEVICE_FP_KEY`）；并在该模块注明「会被 MAIN world import，顶层禁止任何扩展 API 求值」。
  - **合并重复实现**：`applyTitle`（navigation 与 parallel-session 逐字重复）→ `background/tabs/tab-title.ts`；待登录凭证「键格式 + 读写」两份 → `background/core/pending-login.ts`；host/端口五函数（分居 tab-rules 与 parallel-session）→ 新建 `background/core/host.ts`；`sync.ts` 的盒子键字面量 → `LOCAL_KEYS`。
  - **顺带修掉潜伏 bug**：`parentDomainOf` 带端口入参时两个 `return` 仍返回**带端口的原 host**（契约要求 DNR 域不含端口；此前调用方都预剥端口故未暴露）。
  - 死代码：`parallelSession.isBoundTab` / `hostOfTab` / `deleteAccount` / `refreshTitle`、恒真分支 `details.tabId <= 0`、恒真表达式 `tabId !== null && reusedFlag`。
  - **验证**：typecheck 0 错误；build OK；隔离 profile 启动冒烟 7/7；`host.ts` 行为断言 23/23（端口口径三平面分工逐条锁定）；协议键复查——全仓库仅 `constants.ts` 保留定义，两个 bundle 内仍各出现 1 次（跨进程约定未变）。
  - **未做（需单独决策/回归）**：审查另发现 2 个**真功能缺陷**（`shield-main` 的 `Storage.clear` 补丁会让 `sessionStorage.clear()` 清空虚拟 Cookie 袋 → token 丢失；`auto-login.fillPasswordInIframes` 首个可访问 iframe 无密码框即 early-return → 多 iframe 页面自动填表静默失效），以及 bridge 上行入口缺 `.catch`、`title-hook` 观察器过宽等加固项——均属**行为变更**，不纳入本次「不影响功能」的清理，另行排期。

### Added

- **数据层决策与维护工具（0.2.22）**：实测 `workbench.db` 曾达 **82.02 MB**，其中 **74.03 MB 是 `doc_chunks.embedding`**——知识库功能下线后遗留的向量数据，运行时代码**零处读取**（全仓库仅 `db.py` 的迁移定义与 `storage.py` 的一句注释提及这两张表）；空闲页仅 0.25 MB，说明不是碎片而是「仍然存活但已无人使用」的行。真实业务数据合计不到 20 KB（4 账号 / 3 环境 / 5 条任务台账 / 11 项设置），`job_logs` 建了索引却零处写入（日志走磁盘 `log_path`，不入库）。
  - **决策定稿：继续使用 SQLite，不引入 MySQL，不引入 Redis。** 依据与量化触发器见新增 `docs/数据层决策.md`。要点：① Redis 不是数据库——RDB 间隔快照 / AOF `everysec` 均有丢失窗口，而本项目核心资产是 Fernet 加密凭据（`password_enc` + `account_fernet_key`），**丢失不可重建**，且 Redis 无关系约束（现依赖 `ON DELETE CASCADE`），数据还须全驻内存；② MySQL 的决定性理由是**交付形态而非性能**——单机安装包（Electron + 本机 sidecar + Inno Setup）多装一个服务端守护进程（服务账号/端口/root 密码/升级/防火墙）会直接损害「一键安装」卖点，而 `uvicorn` 默认 `workers=1`、`db.py` 单连接 + 全局 RLock 的单写入负载下，MySQL 相对 SQLite WAL 没有任何收益；③ 反例佐证：`routes_extension.py` 的扩展指令队列**刻意**用内存 `_commands` + ack + 单调 `ext_cmd_seq` 游标实现，未拉 Redis——⚠️ 该队列与 `_STATE` 均为进程内状态，是**多 worker/多机部署时最先静默失效的地方**，届时修法是把指令队列落成数据库表，而不是上 Redis。
  - 新增 `tools/db_maintenance.py`：默认只读诊断（表规模 / 大列体积 / 遗留 settings 键 / 独占锁探测），`--all` = `VACUUM INTO` 一致性备份 → 置空遗留向量（**保留文档与分块正文**）→ `VACUUM` 回收。刻意**不做成自动迁移**：迁移历史不可变，且「自动删除用户数据」的迁移风险过高。真机执行结果：**82.02 MB → 2.90 MB（回收 79.12 MB）**，`PRAGMA integrity_check = ok`、`foreign_key_check` 无违规，129 份文档 / 3,420 个分块正文全保留、4 账号 3 环境与盒子/分配池/导出备份全部正常，桌面端重启后服务 200、模块 4/4。
- **迁移 13 `add_created_at_indexes`**：补齐列表查询索引 `agent_audit(created_at)`、`blueprint_jobs(created_at)`、`insight_runs(created_at)`——三处列表接口均为 `ORDER BY created_at DESC LIMIT ?`，而既有索引只建在 `tool` / `status` 上；另加 `account(box)` 对齐 `rename_box` 的 `UPDATE ... WHERE box = ?`。已在真实数据副本上先行验证：升至版本 13、四个索引到位、账号/分配池/盒子/导出备份查询全部正常，随后 50 项 pytest 全绿、ruff 通过。

### Changed

- **`db.py` 增设「方言边界」一节（0.2.22）**：为「万一将来改判为服务端多人部署」预留切换缝，SQLite 特有构造全部收拢并注明——`PRAGMA` → `_PRAGMAS`；逗号串包含匹配 `(',' || col || ',') LIKE ?` → 新增 `db.csv_like()`（`pool_members` 已改用，组合值语义由既有测试覆盖）；`?` 占位符全库统一（psycopg/PyMySQL 为 `%s`，改 wrapper 即可）；`INTEGER PRIMARY KEY AUTOINCREMENT` 仅存于迁移 8 的 `job_logs.id`（历史不可变，保留），**新表不再使用**（其余表均为 UUID 文本主键，本身与方言无关）。换库 = 改这一节 + 数据搬迁，而非全库搜索替换。
- **扩展弹窗改版（0.2.22，用户定稿三点）**：① **「站点授权」区块整块从 UI 下线**——manifest 已声明 `host_permissions: ["<all_urls>"]`，装载即获全站权限，逐站点授权列表既无操作价值、又误导用户以为仍需手动授权；**只删展示层**，授权/停用名单核心逻辑（`site-auth.ts`、`par.grantChanged` 消息、`ql:blockedHosts`、`isEnforceable`）一律保留，随之删除的仅是 `renderGrantList()`（含其生成的「授权」按钮）与 `.grant*` 样式。② **「导出诊断」移入品牌头右上角**（柔和描边胶囊，保留文字标签而非纯图标，可读屏识别，`title` 说明用途）。③ **版本号从右上角移入页脚右下角**。弹窗高度 303px → 174px。
  - 页脚排版为实测结论：10.5px 字号 + 8px 间距时「提示 + 版本」总宽 305px > 300px 内容宽 → 提示尾字（「盘」「换」）被挤到第二行；改 10px + 6px 后总宽 290px 单行放下，**文案一字未改**。另将品牌头水平内边距 2px → 0，使彩环左缘与「导出诊断」右缘和统计卡两端对齐。
- **撤销上游 Page Monitor（0.2.22，用户定稿）**：该功能监听页面（MAIN 壳嗅探平台名称型 API → 桥上行 `pageNames` → 按 URL 分类解析主体名）并把结果**作用于页签**（合成标题 `账号别名 · 主体名·类型`，未解析时先占位 `类型 · guid前8位`），副产物是「最近配置页 MRU」与其 Alt+W 浮层轮盘。整块下线：
  - **删除**：`background/core/page-monitor.ts`、`content/pages-overlay.ts`、设计文档 `docs/FEASIBILITY-RECENT-PAGES.md`；`build.mjs` 的 `content/pages-overlay` 入口；manifest 的 `quick-pages` 命令（Alt+W）；桥上行 `pageNames` 载荷类型及其在 `parallel-session` 的消费分支；`shield-main` 的名称嗅探（`NAME_API_RE` / `extractNamePairs` / `reportPageNames` 及 fetch/XHR 两处钩子）；`messages.ts` 的 `pages.recent` / `pages.jump` 与 `RecentPageEntry`；`constants.ts` 的 `recentPages` 键与 `RECENT_PAGES_MAX`；`service-worker` 的对应消息分支、`quick-pages` 命令分支与 `togglePagesOverlay`。
  - **保留**：页签标题仍由既有 title 管线（`tabs/tab-title.ts` + `content/title-hook.ts`）权威写入**页签名**——撤销的是「用页面信息改写标题」，不是「标题显示页签名」；账号轮盘（Alt+Q）与浏览器池的监听录制（`services/browser_pool.monitor_start/stop` + `runtime/monitor/`，另一套东西）均未受影响。
  - 隔离 profile 启动冒烟 7/7：SW 可启动且未崩、`commands.getAll()` 仅剩 `quick-wheel`、storage 无 `ql:recentPages`、session 无 `ql:pageNames`、桌面数据面 `akso:acctMap` 仍同步成功。
  - ⚠ 上游同步注意：该功能来自上游 v3.11/v3.13，下次同步会把它带回来（manifest 命令 / 桥上行 op / 内容脚本入口 / SW 注册 / 构建入口），需按本清单再次摘除。
- **桌面轮盘扇区名改为「页签名优先」（0.2.22，用户定稿）**：`wheel-picker.html` 此前用 `a.username`，导致设了页签名的账号在轮盘上仍显示账号名；改为 `(a.tab_name || '').trim() || a.username`，与扩展端 `ui/wheel/wheel-core.ts` 的口径完全一致。另修一处连带缺陷：轮盘防重绘指纹只含 `id`，改页签名不触发重建（标签会一直停在旧名），现将显示名并入指纹。真机数据验证 5/5：`T0901`（页签名 TTTTT）显示 TTTTT，未设页签名的 `dev-admin`/`dev-user` 回落账号名。

### Fixed

- **弹窗/徽标/管理页版本号不随项目升版（0.2.22）**：`shared/constants.ts` 曾硬编码上游版本常量（`EXT_VERSION = '3.13.2'`），而 `tools/bump.py` 的五写只同步 manifest.json / package.json 等、**从不写这个 TS 常量** → 上游同步后版本号永久停旧（实测：项目已 0.2.21，弹窗仍显示 v3.13.2、扩展图标徽标显示 v3.13）。改为 `extVersion()` 读取 `chrome.runtime.getManifest().version`——**唯一真源 = manifest.json**，随后续 `bump.py` 五写自动跟随；弹窗、徽标、管理页、诊断包四处版本号一并同步。用函数而非模块级常量，避免内容脚本 import 本模块时的求值风险（`try/catch` 兜底 `'0.0.0'`）。
  - ⚠ 上游文档 `extensions/quick-login/README.md`、`extensions/quick-login/CHANGELOG.md`、`extensions/quick-login/docs/CODEBASE_OVERVIEW.md` 仍写「`EXT_VERSION` 三处必须一致」，已过时（上游文件，待下次同步时一并校正）。
- **带端口站点误判：0.2.21 那次修正只做了一半（0.2.22）**：0.2.21 给 `hostRelated` 加了「两端都带端口时必须端口一致」的守卫，但调用方仍用 `URL.hostname` 取 host——它**永远不含端口**，守卫 `up && bp` 里的 `up` 恒为空 → 「同主机不同端口是不同站点」从未真正生效。统一改走新增的 `urlHostOf()`（`new URL(url).host`；URL API 已把 http:80/https:443 规范化掉，不会造成假不匹配）：
  - `authHeaderForUrl`（下载归属 ②③ 层）：同主机其它端口的账号会互相串号（内网 `host:8080` / `host:18996`、桌面自身 `127.0.0.1:18765` 即典型）；
  - `adopt-candidate`（继承页签收编）：落到同主机别的端口会被当成"本站"而收编（身份头经 DNR 端口无关地注入）；
  - `onNavigation`：「是否本站」继续按无端口比（与 DNR `requestDomains` 同口径），但停用名单与健康缓存改用**带端口** host 查——名单条目来自 origin 推导（`192.0.2.10:8080`）、查询却用无端口 `192.0.2.10` → 永远查不到，**带端口站点的停用此前静默失效**。
  - **口径分工（勿混）**：身份平面（归属/停用名单/收编）端口参与比较；规则覆盖平面（DNR `requestDomains`）与 Cookie 作用域（RFC 6265）端口天然不参与，那两处按无端口比较是**正确**的，本次未改。
- **停用名单命中改双形匹配（0.2.22）**：新增 `blockedHit()`——带端口精确命中，或条目不带端口时覆盖该 host 全部端口（历史数据/仅填 host 的配置兼容）；带端口条目不跨端口误伤，与站点身份口径一致。`isEnforceable` 与 `syncAccountRules` 同步改用它。
- **`parentDomainOf` 入参先剥端口（0.2.22）**：带端口内网 host（`192.0.2.10:8080`）会让"全数字段=IP 字面量"判定失配，进而拼出 `0.105:8080` 这类无意义父域。当前调用方均已预剥端口，此为幂等加固（防后续踩坑）。

### Changed

- **扩展改为声明式全站权限，按站点授权整套下线（0.2.21，用户定稿）**：`manifest.host_permissions` 从"仅 127.0.0.1:18765 + `optional_host_permissions: ["*://*/*"]`（逐站点申请）"改为 **`["<all_urls>"]`**。
  - **为什么**：逐站点授权意味着每加一个新站点都要在浏览器弹窗点一次「允许」，未授权期间 DNR 改头规则**能装上但不生效**（实测：`permissions.contains` 为 false 的源上 `modifyHeaders` 静默失效）→ 多账号隔离与下载补身份全部哑火，且没有显式报错。这是用户实测"下载 401 却看不出原因"的根源。改声明式后**装载即获得全站权限、永不弹窗**（实测：`permissions.getAll().origins = ["<all_urls>"]`，且任意站点上 DNR `modifyHeaders` 立即生效）。代价是一次性授予全站访问权限、权限升级时可能需要在 `chrome://extensions` 重新加载/启用一次扩展。
  - **随之移除的组件**：内容脚本 `content/site-grant.ts`（页面授权提示条）、授权页 `ui/grant/`、消息 `site.grant.state` / `site.grant.request`、`site.grant.open` 桌面指令、桌面站点行授权徽标与「授权」按钮、账号卡「未授权」徽标、`site.grant.*` 相关样式与提示位；`grantStateForHost` / `requestHostGrant` / `probeHostAccess` 一并删除。
  - `isEnforceable` 语义随之简化：全站权限下"浏览器授权"不再是变量，唯一能关停某站点网络平面的只剩**用户手动停用名单**（`ql:blockedHosts`）；20s TTL 缓存保留（名单变更即时生效）。
- **规则安装不再被授权判定门控（0.2.21）**：`syncAccountRules` 与 `parallelSession.restore` 无条件安装绑定规则（DNR host access 自身就是生效门控，未授权源上规则静默不生效、无害）。此前"授权判定误判为 false"会让规则**永久不装**且 UI 无任何提示——用户实测下载 401 的直接根因。唯一保留的硬门控是手动停用名单。
- **401/403 自愈（0.2.21）**：绑定页签被服务端拒绝时（节流 10s/页签）强制刷新网络平面健康缓存并重装该页签规则——"规则没装上/装的是旧 token"这类静默失效无需重启扩展即可自愈。

### Fixed

- **下载失败"无法单账号归属"（0.2.21，用户实测仍未修复）**：`authHeaderForUrl` 旧实现要求"全库中该 host 只有 1 个账号"才敢重试，而实测站点是**一站两账号**（示例租户：dev-user / T0901）→ 永远返回 null → 下载 401 后从不重试（诊断包原话：`下载失败（无法单账号归属，不自动重试）`）。改为分层归属：①同 URL 被 webRequest 观察到的绑定页签账号（最精确）→ ②该 host 上**唯一有活跃绑定页签**的账号（正在浏览该站点的账号）→ ③全库唯一账号（原兜底）；都不成立才不注入。实测 `chrome.downloads.download({headers})` 不需要 host 权限即可带出 Authorization（Chromium 早已移除该校验），故该重试路径不受授权状态影响。
- **带端口站点被误判为"漫游到非授权域"而解绑（0.2.21）**：`onNavigation` 用 `URL.hostname`（永不含端口）与 `binding.host` 直接比较，站点自身 host 带端口（如 `192.0.2.10:8080`）时永远不相等 → 一打开就被解绑（规则不装、身份不注入）。比较前先去端口。
- **同主机不同端口被当成同一站点（0.2.21）**：`hostRelated` 仅比较域名，导致桌面自身页面 `127.0.0.1:18765` 会被当作内网站点 `127.0.0.1:18996`（下载归属、Cookie 归属、站点判定的误判来源）。现两端都带端口时要求端口一致。
- **诊断日志被桌面同步轮询淹没（0.2.21）**：桌面同步端点（`127.0.0.1:18765`）的响应此前被归属给"唯一绑定账号"，每 2s 刷两条 `captureResponseCookies：无页签响应…`，把 122 条环形诊断缓冲冲掉、真问题被挤出。此类 URL 不再计入。

### Changed

- **账号中心 UI 复刻一期（0.2.8）**：按上游 quick-login 管理页视觉与功能基线重写（`accounts.html`/`accounts.js` + 移植 `ql-theme.css`/`ql-parallel.css`）。落地：顶栏品牌头 + 实时统计（账号/在线/盒子）+ 版本 chip；盒子 chips 悬停操作（✎ 重命名 / ⏸▶ 禁用启用 / ✕ 两步删除——连同账号删除或并入默认盒）；**批量管理模式**（勾选 + 已选计数 + 批量移盒/删除）；**移入盒子弹窗**（单选带计数 + 新盒名自动创建，替代裸 prompt）；**四态实时徽标**（在线×N/待登录/离线/未授权·已暂停——新增扩展执行面状态回传通道 `/extension/state`，扩展每 ~6s 上报绑定页签数/token/授权暂停）；**指纹防闪烁渲染**（数据未变不重建 DOM）；轮盘 0 键=第 10 账号（页内覆盖层 + Electron 轮盘窗）；禁用盒轮盘跳过（settings 表 `disabled_boxes`）；平台环境管理收进对话框、批量添加收进折叠面板——消除提示冗杂。DOM 验证 11/11（`tools/verify_accounts_ui.py`）。

### Fixed

- **Fernet 双 bug（0.2.7，E2E 最后一公里）**：①扩展端 fernetDecrypt 键位写反——Fernet 规范 sign-key=key[:16]/enc-key=key[16:32]，原实现互换导致 HMAC 全失败、账号被静默跳过；②WebCrypto AES-CBC 已自动去 PKCS7 填充，原代码再按尾字节手工剥离，把口令尾字符当填充长度剥掉。修复后**隔离 Chrome 全链路真机验收通过（E2E_PASS）**：快照同步 2/2 → par.open 开登录页 → 自动填表提交 → 进入平台工作台。新增 `tools/acceptance_extension_e2e.py` 可复用验收脚本。

### Changed

- **扩展基线上游同步（0.2.6）**：`extensions/quick-login` 整体前移 **v3.11.0 → v3.13.2**（36 文件），重放全部私有改造（manifest 18765/alarms/Ctrl+Shift+Q、sync.ts+account-wheel.ts 保留、service-worker 挂载+抽取、parallel.html 隐藏数据区块、wheel-overlay interval 修复）。带入上游 8 个版本的能力：登录态生命周期跟随页签（3.12.0 免密复制语义/最后页签关闭终结登录态）、绑定时 Cookie 袋权威同步（3.12.1）、自动登录逐事件取证黑匣子+管理页导出诊断（3.12.2）、AuthCode 入时效集（3.12.3）、亲子继承候选期零种子（3.13.0）、AUTH 规则补 main_frame（3.13.1）与全资源类型（3.13.2）。typecheck 零错误验证 sync.ts 与新 API 兼容。

### Added

- **扩展连通性专项（0.2.5）**：桌面↔quick-login 扩展链路断点修复——快照 host 字段错位（envBaseUrl）、指令 seq 跨重启持久化（settings 表单调递增）、host 保留端口 + scheme 随快照下发、扩展 chrome.alarms 30s 保活复活、wheel.toggle 改直调（SW 自消息死链）、毒指令逐条隔离（不再卡死队列）、快照空载删除护栏、tabName 缺失护栏、凭据解密失败留痕、wheel-overlay 双 interval 泄漏修复；扩展 quick-wheel 热键让位（Alt+Q→Ctrl+Shift+Q，避免与 Electron 全局热键抢占）；账号中心页内轮盘选人补 launch-chrome；新增 `tools/verify_extension_sync.mjs` 离线模拟扩展验收脚本。
- **浏览器分配政策落地 + Monitor 监听接入托管会话（0.2.2）**：政策定稿——快捷登录/打开 = 用户 Chrome（扩展指令 par.open + launch-chrome）；监听/自动化 = 应用内置 Chromium（browser_pool，cdp = Electron 壳）。`routes_monitor`（/api/monitor/start|stop|status）；browser_pool 新增 monitor_start/stop（自动开户 → MonitorSession 挂 page → 产物落 runtime/monitor/）；卡片 UI（快捷登录主按钮 + 监听会话/开始/停止监听 + 监听中 chip）；routes_browser 补 `/navigate/{account_id}`。真机验收：248 请求捕获（dropped 238 / trimmed 2 / full 8），三级降噪正确，monitor-log 落盘。
- **桌面壳迁移 Electron（pywebview 退役）+ browser_pool CDP 复用模式**：`desktop/main.js`——Python sidecar 生命周期（打包态 AksoServer.exe --server / 开发态 venv uvicorn）、主窗、托盘、全局热键 Alt+Q、electron-updater 自动更新（latest.yml）；会话控制服务 :18767（按 windowId 开户，每账号 `persist:` 独立持久分区 / 聚焦 / 关闭）；browser_pool 经 `connect_over_cdp(:18766)` 复用内置 Chromium（`_CdpState` 单实例持有者）。
- **quick-login 混合架构（v0.1.2）**：扩展执行面 + 桌面数据面/触发面。`routes_extension` 同步蓝图（快照：账号/盒子/站点，凭据以「Fernet 密文 + 密钥」经 127.0.0.1 回环下发；指令队列 wheel.toggle/par.open，内存态 + ack）+ 扩展 `sync.ts`（2s 轮询、snapshotId 内容哈希幂等、WebCrypto Fernet 解密直连 parallelStore、桌面不可达离线回退）。扩展源码入库 `extensions/quick-login/`。
- **轮盘 v2**：径向扇形无边框半透明独立窗（Electron 承载）+ 扩展 sync v2 直连 store + Chrome 拉起。
- **桌面壳重构与实机调通**：`shell/shell.py` 改为「uvicorn 子进程 + webview 主线程」架构（服务与窗口生命周期解耦，关闭窗口即退出并回收服务）；pythonw 的 stdout/stderr=None 兜底；pywebview 以 `desktop` extra 入库（`uv sync --extra desktop`）；新增 `tools/start-desktop.vbs` 双击启动器。实机验证：原生窗口可见、服务就绪、首屏体检线程正常。
- **quick-login 全量内化收口**：托管浏览器会话持久化（storage_state——登录成功/正常关闭落盘，重开免密直达、跨进程重启有效；Cookie 袋/DNR 回放的原生等价物）；会话自愈（直达首页被踢回登录页 → 自动重跑节奏门控）；`POST /api/browser/forget/{id}` 登出语义 + `GET /api/browser/saved/{id}`；状态墙持久会话徽章；`tests/test_browser_state.py`（5 项）。真机验证：重开 restored=True 且引擎 phase=idle（完全未走登录页）。迁移台账「不迁清单」重新定性：各项均为"被 Playwright 原生机制等价替代"，原仓库不再是功能归宿。
- **阶段 3 全量完成：akso-cc / akso-auto 原生化（运行时零依赖原项目）**
  - 读路径 `egmp/insight/`：crawler（对象发现链）/assemble+report（盘点）/lifecycle+flowgraph+relations（L3/L2）/annotate/render/drawio/understand/spider（五步织网，step5 用 networkx 图分析）。
  - 写路径 `egmp/writers/`：blueprint（pydantic 两层校验+规范化+审阅三件套，替代 ajv）/idempotency/objects/fields/picklists/lifecycle/workflows（a-i 管道+两次提交连线）/layouts（全量替换语义）/menus/endpoints（自原仓库只读提取的实证常量表）。
  - 编排 `egmp/orchestrate.py`（runFullWorkflow/拓扑排序/checkpoint 落盘）+ `complexity.py`（规则移植）+ `generate.py`（DeepSeek 蓝图生成融合：规范化+校验回炉）。
  - Monitor `egmp/monitor/`：Playwright 录制三级降噪/查询层与参数推断/segment 解读+reproduce-plan（API_MAP 对位 Python writers；DANGEROUS 永不回放）。
  - `routes_insight` / `routes_factory` 切换原生调用；`routes_agent` 工具同步原生；`adapters/*.json` 降级为只读参考存档；Node 不再是运行时依赖。
  - 测试：`tests/test_native.py`（12 项，FakeEgmpClient 内存平台离线验收校验/编排幂等/盘点/理解/蜘蛛/Monitor）+ `tests/fakes.py`；真机验收：原生 login / understand（training_hjy__c，1.4MB 理解模型）/ spider 均在真实平台跑通。
- 架构分析文档 `docs/架构分析.md`：总体架构图、四条设计底线、模块分布地图（11 组 40+ 模块的职责/血统/依赖）、数据流图、DB 迁移版本账、三层测试策略与逐模块回归集、排障速查表、维护红线、新模块 checklist、演进触发器。
- 双人协作基建：`CONTRIBUTING.md`（开发协作规范）、`.editorconfig`、`tools/setup.ps1`（环境自检脚本）；uv 默认走清华镜像（`[[tool.uv.index]]`，随仓库分发）。
- 质量门禁：ruff 规则集（E4/E7/E9/F/I/B）入 `pyproject.toml`，全仓通过；修复 19 处（未用导入/未排序 import/zip strict/异常链 from exc 等）。
- 模块契约 §1 补强 pydantic 约定：请求体强制 BaseModel，新代码响应强制 response_model。

### Changed

- **构建分发链 v3（Electron）**：PyInstaller 服务端 sidecar（`dist/AksoServer` onedir，含 chromium 本体）→ electron-builder NSIS（`desktop/dist/AksoWorkbench-<ver>-setup.exe` + `latest.yml`，GitHub Releases 承载 electron-updater 增量更新）。Inno Setup（`tools/installer.iss`、`tools/ChineseSimplified.isl`）退役，2026-09-10 删除。
- **bump.py 三写机制**：版本写入 pyproject + `workbench/__init__.py` + `desktop/package.json`（electron-builder 以 package.json 版本命名安装包与 latest.yml；此前漏写导致版本错位，需手工对齐提交补救）。
- `pyproject.toml`：uvicorn 对齐为 `[standard]` 变体（与实测环境一致）；依赖变更一律走 `uv lock && uv sync`。

### Fixed

- **sync-Playwright 事件回调内禁止连接调用**（response.text()/page.evaluate 重入 = worker 死锁）——事件只入队，worker 作业间隙统一 drain（响应体补捞 + 动作缓冲出栈）。
- **cdp 模式三修**：pw 单线程单实例持有者 / 先开户后连接（Electron33 挂接怪癖）/ 平台异步鉴权复核；logging_in 会话复用（禁二次开户）；close_all 连接池清理。
- 轮盘单例 toggle + js_api 关闭/拖动（从误 stash 中恢复的关键修复）；启动会话 openAccount ReferenceError；账号编辑对话框与批量添加链路。

- 立项：四项目融合重建（akso-cc / mogul_simulator / akso-auto / quick-login），原项目冻结不动。
- 阶段 0：uv 工程定义（`pyproject.toml`）、模块契约（`docs/模块契约.md`）、迁移台账（`docs/迁移台账.md`）、只读适配器声明（`adapters/akso-auto.json`、`adapters/akso-cc.json`）。
- 阶段 1A：fork mogul_simulator/mogul → `workbench/`（包名、数据目录、端口可并存；数据库兼容接管 mogul.db）。
- 阶段 1B：Node 子进程统一封装 `services/proc.py`；模块注册表 `services/modules.py` + `adapters`；模块健康体检路由 `api/routes_modules.py`。
- 阶段 1B：平台洞察路由 `api/routes_insight.py`（封装 akso-cc login/inventory/understand/spider，产物读取回传）。
- 阶段 1B：配置工厂路由 `api/routes_factory.py`（封装 akso-auto create/编排/monitor，蓝图暂存 + 断点状态展示）。
- 阶段 1B：前端页 `factory.html` / `insight.html` + 对应 js/css（复用 mogul 设计系统，四模块 Tab 导航）。
- 阶段 1B：pywebview 桌面壳 `shell/shell.py`（起 uvicorn + 开窗口 + 依赖体检首屏）。
- 阶段 2A：统一账号库（DB 迁移 7：platform_env / account 表，Fernet 凭据加密；卡片墙 UI；三项目 env 一键导入脚本）。
- 阶段 2B：托管浏览器 `services/browser_pool.py`（Playwright 账号↔context 池）；自动登录引擎 `services/autologin.py`（quick-login 节奏门控全量迁移：齐备门槛/提交前回读/MutationObserver 即时填充/用户点击接管/失败让位 + srcdoc iframe 探测 + token 捕获）；路由 `api/routes_browser.py`；前端页 `browser.html`；机器验收 `tests/test_autologin.py`（9 项断言）。
- 阶段 3：egmp 平台客户端 Python 化内核（client/auth/cache/checkpoint + writers/insight/monitor 骨架）与 Agent 工具层（`routes_agent.py`：自然语言→登录/读配置/写配置/查知识）。
