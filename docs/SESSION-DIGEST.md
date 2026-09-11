# 会话状态压缩摘要（供后续上下文快速恢复 · 更新于 2026-09-10）

## 项目定位
- 仓库：`D:\ai_assistant\akso-mogul` → https://github.com/jizi-dragon/akso-mogul（main，本地代理失效时用 `git -c http.proxy= push`）
- Akso Workbench：四项目融合（akso-cc 洞察 / mogul_simulator 知识工作台 / akso-auto 配置工厂 / quick-login 账号+自动登录），**运行时零依赖原仓库**（能力内化到 Python + 扩展执行面）
- 技术栈：Python 3.12+ / FastAPI / SQLite(迁移1-11) / Playwright / Fernet / networkx / **Electron 桌面壳**（pywebview 已退役）+ quick-login Chrome 扩展执行面；uv 管理（清华镜像已配）

## 版本规则（用户定稿·方案A）· 当前 v0.2.2
- 真源 `pyproject.toml` version；`tools/bump.py push|build|show` **三写**：pyproject + `workbench/__init__.py` + `desktop/package.json`（+ `.version.json` 缓存）——electron-builder 以 package.json 版本命名安装包，勿手改单一文件
- push=PATCH+1；build=MINOR+1且PATCH重置1（0.0.2→0.1.1→0.2.1）；构建后推送从新基线续增

## 架构速查
- `workbench/api/routes_*`：conversations/settings/chat（fork mogul，SSE 对话）、modules（体检）、insight/factory（egmp 原生）、accounts（盒子/池/备份/导入）、browser（open默认headful/focus/close/forget/saved/navigate）、agent（工具+审计表）、**extension（扩展同步蓝图：快照+指令队列 wheel.toggle/par.open，凭据"密文+密钥"回环下发）**、**monitor（start/stop/status，挂托管会话）**
- `services/browser_pool.py`：**专职工作线程**（Playwright sync 单线程单实例约束，全部操作走 Future 队列；事件回调内禁止连接调用——只入队，worker 间隙 drain）；双模式浏览器（headful 默认/headless）；**CDP 模式**（connect_over_cdp :18766 挂接 Electron 内置 Chromium；`_CdpState` 单实例持有者；**先开户后连接**——先 POST :18767 /windows 开户；logging_in 会话复用）；storage_state 持久化（免密直达）；自愈 heal_count≤2；bring_to_front 聚焦；monitor_start/stop（MonitorSession 挂 page → runtime/monitor/）
- `services/autologin.py`：节奏门控引擎（30s/4次/2错误让位/800ms轮询/100ms去抖/500ms回读/3500ms观察/srcdoc iframe/cross-realm setValue）+ TokenCapture(JWT)
- `services/accounts.py`：Fernet（密钥存 settings 表）、环境/账号 CRUD、盒子（rename 空目标=并入默认、create 空盒）、分配池（config/monitor，`,包夹 LIKE` 匹配）、备份导出导入（fernetKey 随文件，同站同名去重）
- `services/egmp/`：client（信封 code==0 / paged_post / 55min token 缓存）、insight（crawler发现链/understand三层报告/spider五步+networkx）、writers（blueprint pydantic两层校验+规范化+审阅件 / idempotency / objects/fields/picklists/lifecycle/workflows a-i管道 / layouts 全量替换语义 / menus / endpoints 实证常量）、orchestrate（拓扑排序+checkpoint）、generate（DeepSeek 蓝图生成融合）、monitor（录制三级降噪/查询层/参数推断/解读+reproduce-plan API_MAP，DANGEROUS 不回放）
- **浏览器分配政策（用户定稿）**：快捷登录/打开 = 用户 Chrome（扩展指令 par.open + launch-chrome）；监听/自动化 = 应用内置 Chromium（browser_pool CDP 模式）
- 扩展执行面 `extensions/quick-login/`：MV3 + 六平面隔离（存储/AUTH/COOKIE/CACHE/SW/IDB）；`background/sync.ts` 每 2s 轮询桌面快照（snapshotId 幂等，Fernet WebCrypto 解密直连 parallelStore，离线回退）；构建 `npm run build` → dist，更新后须在扩展卡片"重新加载"
- 前端：`static/pages/{insight,factory,accounts}.html` + `modules.css v3`（Akso 蓝白令牌）+ 轮盘（Alt+Q 透明独立窗，`wheel-picker.html?transparent=1`）
- 账号中心（合并原两页）：盒子过滤芯片+内联管理行 → 卡片墙（会话+凭据+池芯片+监听中 chip）→ 分配池 → 环境/新增 → 备份/导入
- 桌面壳 `desktop/main.js`（Electron 33）：sidecar（打包态 AksoServer.exe --server / 开发态 venv uvicorn）+ 主窗 + 透明轮盘窗（Alt+Q 单例 toggle）+ 托盘 + 会话控制服务 :18767（每账号 persist: 分区开户/聚焦/关闭）+ electron-updater 自动更新；退出 killServer（taskkill /T /F）

## 已下线（源码已删，勿恢复）
知识库/钉钉同步全链路（routes_knowledge/sync、chunking/embedding/retrieval/ddkb/dingtalk_sync、harness 知识工具、前端 knowledge 视图）；DB 迁移 1-6 按不可变纪律保留；**pywebview 桌面壳（shell/shell.py，壳换代 Electron）**；**Inno Setup（tools/installer.iss、ChineseSimplified.isl，2026-09-10 删，NSIS 接管）**；tools/start-desktop.vbs

## 打包分发
- 一键：`powershell -File tools\build.ps1`（bump build 三写 → release commit+push → uv sync → PyInstaller onedir `dist/AksoServer`（含 chromium 本体）→ electron-builder NSIS → `desktop/dist/AksoWorkbench-<ver>-setup.exe` + `latest.yml`）
- electron-builder 配置在 `desktop/package.json`（extraResources 带 `../dist/AksoServer`）；发布 Release 上传 exe + latest.yml（electron-updater 自动更新）；ELECTRON_MIRROR=npmmirror 已设
- 旧 Inno Setup 链路已废：ISCC / installer.iss 相关流程勿再使用

## 验收基线
50 项 pytest 全绿；ruff 全绿（uvx ruff check .）；真机通过：原生 login/understand(1.4MB模型)/spider(networkx)、双账号并行 headful 2/2 在线、免密直达（引擎 idle）、自愈 heal_count=1、Monitor 真机 248 请求捕获（dropped 238/trimmed 2/full 8）、Electron 壳 CDP 挂接开户

## 环境坑清单（高价值，勿重踩）
1. Playwright sync 单线程单实例 → 所有操作必须走 BrowserPool 专职线程队列；测试勿另起 chromium 夹具
2. **事件回调内禁止连接调用**（response.text()/page.evaluate 重入 = worker 死锁）——事件只入队，worker 作业间隙统一 drain（0.2.2 严重修复）
3. **CDP 挂接三律**：remote-debugging-port 必须 app ready 前设置；先 :18767 开户后 connect（Electron33 挂接怪癖）；pw 实例由 _CdpState 单一持有
4. ES 模块严格模式重复函数声明 = 整模块加载失败（页面停在静态"加载中"）——accounts.js isOnline 事故
5. PS5.1：.ps1 必须 UTF-8 **BOM**；不支持 `??`；heredoc `<<` 不可用；中文经命令行参数传 curl 会乱码（写文件 + `--data-binary @file`）
6. pydantic BaseModel 默认忽略多余字段（AccountPatch 漏 box 字段导致静默失效）
7. build.ps1 禁用 `$ErrorActionPreference=Stop`（PS5.1 把 git/uv 的 stderr 进度当终止错误）
8. 关窗回收：退出 taskkill /T /F 防孤儿 chromium；同线程禁止第二个 sync_playwright
9. 网络：GitHub/PyPI 间歇抖动 → 重试循环；uv 清华镜像已配；**全局 git 代理 127.0.0.1:7890 常失效**，push 用 `git -c http.proxy=`
10. 自愈计数成功时不可清零（掩盖已自愈事实）
11. 桌面壳只开一个实例（第二实例端口冲突只出窗口不连服务）
12. 扩展更新 dist 后必须在扩展卡片点"重新加载"（Chrome 缓存 SW 脚本）
13. 版本号只走 `tools/bump.py` 三写，勿手改单一文件（安装包命名会错位）
14. **字段名双语义**：DB 行 snake_case（env_base_url），export_backup 输出 camelCase（envBaseUrl）——routes_extension 曾读错键 → 快照 host 恒空 → 扩展静默丢弃全部账号（0.2.4 实锤的主断点）
15. **扩展游标持久 vs 服务端内存 seq**：指令序号必须跨重启单调递增（已落 settings 表 ext_cmd_seq），否则重启一次指令通道整体哑火且无报错
16. **SW 自消息死链**：SW 内 chrome.runtime.sendMessage 不投递给自身 onMessage——跨模块复用行为请直调函数（toggleAccountWheel 已抽至 account-wheel.ts）
17. **Fernet 键位规范**：sign-key = key[:16]（HMAC），enc-key = key[16:32]（AES-CBC）——曾写反导致扩展端全员解密失败（0.2.6 实锤断点之一）
18. **WebCrypto AES-CBC 自动去 PKCS7 填充**：decrypt 结果不可再按尾字节手工剥离，否则把口令尾字符当填充长度剥掉（0.2.6 实锤断点之二）
19. **Playwright 1.62 测 MV3 扩展**：`ctx.service_workers` 需先开一个页面才暴露目标；chrome://extensions 页可见扩展卡片与错误

## 验收基线（扩展连通性专项 · 0.2.11 收口）
- **E2E_PASS**（`tools/acceptance_extension_e2e.py`，隔离 Chrome 装载 dist）：A1 数据面 acctMap 2/2 同步 ✔ / A2 par.open 指令面 1s 内开登录页 ✔ / A3 自动登录成功 URL 离开 /login 进 /web ✔（0.2.10/0.2.11 两轮回归均过）
- **UI_CHECKS 11/11**（`tools/verify_accounts_ui.py`）：CSS 生效/统计/卡片结构/四态徽标/批量条/零 JS 错误
- 桌面侧模拟：`tools/verify_extension_sync.mjs` SIMULATION_OK；SEQ_PERSIST_OK；禁用盒透传 SYNCDISABLED_OK
- 50 pytest 全绿；扩展 typecheck 零错误
- 待人工复验：用户真实 Chrome 装载 dist + 桌面 Alt+Q 轮盘选人 + 真实 profile 登录
- ⚠ 教训：/extension/* 无 pytest 覆盖——`verify_extension_sync.mjs` 就是它的回归测试，改该蓝图必跑

## 扩展基线校正（0.2.5 摸底 · 0.2.6 已同步）
- `extensions/quick-login/` = **上游 v3.13.2（2026-09-10 同步，36 文件前移）+ akso-mogul 私有改造**。私有改造清单：① manifest host_permissions:18765 + alarms 权限 + quick-wheel 热键 Ctrl+Shift+Q；② `src/background/sync.ts` 桌面同步桥（含 seq/快照/毒指令全套护栏）；③ `src/background/account-wheel.ts`（toggleAccountWheel 抽取，sync 直调）；④ service-worker 挂载 sync + import 轮盘；⑤ parallel.html 隐藏账号增删改区块 + 会话视图文案；⑥ wheel-overlay 双 interval 修复；⑦ **撤销上游 Page Monitor**（0.2.22：页签标题不再被页面信息改写，Alt+W 最近配置页轮盘整套下线，见文末 0.2.22 节）；⑧ 弹窗改版（0.2.22：站点授权 UI 下线、导出诊断入品牌头右上角、版本号入页脚右下角）；⑨ 版本真源改 manifest（0.2.22：`extVersion()` 取代硬编码 `EXT_VERSION`）；⑩ 身份平面端口口径修复（0.2.22：`urlHostOf()`，补完 0.2.21 只修一半的端口守卫）；⑪ 声明式全站权限（0.2.21：`host_permissions: ["<all_urls>"]`，逐站点授权链路整套退役）
- 上游 v3.11.1→v3.13.2 已带入：登录态生命周期跟随页签、Cookie 袋权威同步、取证黑匣子+导出诊断（parallel 页新增「导出诊断」按钮）、AuthCode 时效集、AUTH main_frame/全资源类型
- **dist 真实位置 = `extensions/quick-login/dist/`**（不是 packages/extension/dist）；构建 `npm run build`（workspace 根）
- 账号中心 UI 复刻基线（上游 parallel 管理页 14 项差距）见目标档案：四态徽标/批量管理/diff 防闪烁/移盒弹窗/盒子禁用/删盒两步处置/诊断导出/站点授权健康/顶栏统计/数字键0=第10/轮盘动效等

## 待办/可选（未做）
- **账号中心 UI 复刻二期**（基线剩余 polish 项）：轮盘扇区入场/节点滑移动效、扩展端授权清单展示（state 已回传 enforcementOff，细化到 host）
  - ~~账号别名（tabName，需 account 表加列）~~ **已落地**（0.2.22）：`account.tab_name` 列早已存在，账号卡（`accounts.js`）与**轮盘扇区名**（`wheel-picker.html` 的 `labelOf()`）均走「页签名优先、空则账号名」
- **人工复验**：真实 Chrome 装载 dist → 桌面 Alt+Q 轮盘选人 → 真实 profile 自动登录（自动化侧已 E2E_PASS）
- egmp writers 真机首跑验证（create 写配置需测试环境授权；monitor 侧已真机验收）
- NSIS 安装器静默装 UAC 未落盘验证；若需"关主窗后会话常驻"：服务与壳解耦为独立进程
- 安全加固（扩展侧产品级隐患，暂挂）：明文凭据 60s 投递窗口（getPendingAutoLogin 读后不删——3.13.2 复核仍未修）、同步通道无认证

## 运维速记
- 启动：服务 `uv run python -m workbench.main`；桌面 `cd desktop && npm install && npm start`
- 构建：`powershell -File tools\build.ps1`；测试：`.venv\Scripts\python -m pytest`（或 uv run pytest）；lint：`uvx ruff check .`
- 账号中心真机账号：dev-admin / dev-user（标准验证 + example-config 环境）
20. **孤儿 uvicorn 占 18765**：测试脚本异常退出会遗留服务进程——新起的服务绑定失败、HTTP 验证全部打到旧代码上，表现为"修复无效"。先 `Get-NetTCPConnection -LocalPort 18765` 查占再起服务；盒子操作行为级回归 = `tools/verify_box_ops.py`

## 0.2.20 复验顺序（下载修复 + 轮盘视觉 · 缺一不可）
1. `chrome://extensions` 重载 QuickLogin（新增 downloads 权限；版本应显示 v0.2.20）
2. 任务管理器确认无 AksoServer.exe / python.exe 残留进程，然后完全重启桌面壳（`cd desktop && npm start`）——sidecar 不重启就是旧代码（孤儿进程教训见环境坑 #20）
3. ~~扩展弹窗 → 站点授权 → 各站点点「授权」~~ **已废弃**（0.2.21 声明式全站权限后无需授权；0.2.22 起弹窗的站点授权区块整体下线——别再找这个按钮）
4. 快捷登录 → 登录成功后下载文件（若首次失败，扩展会自动带 Bearer 重发并落地；重试日志在 ql:diag）
5. 盒子新建/编辑输入框复测（原生 dialog，Electron 焦点最稳）
6. 若下载仍失败：扩展弹窗「**导出诊断**」（0.2.22 起位于品牌头右上角）→ 把 JSON 发开发者（ql:diag 里有每次下载失败的错误码与归属判定日志）

## 0.2.23 扩展架构清理（用户定稿：不影响功能 · 未提交）
- **收敛定位**：扩展 = **执行面**（收 `par.list` / `par.open` / `wheel.toggle` + 六平面隔离）；账号数据的增删改一律归桌面端，扩展侧只在 `sync.ts` 里对账。
- **删了什么**：① 死文件 `ui/parallel/*`（1,290+130+629）与 `ui/send.ts`；② 旧会话模型 `session-manager.ts`/`account-registry.ts`/`navigation.ts` + `session.*` 协议 + `Session` 类型 + `sessionTabBindings`；③ 并行页专用协议 `par.create/update/delete/moveBox/renameBox/deleteBox/probeScheme` + `data.export/import`（SW 死分发 ≈275 行）。净删 **−2,849 行**（18 文件）。
- **保留（别误删）**：`parallelStore` 的增删改（`sync.ts` 直接调用＝活数据面）；`site-auth.ts` + `site.grants.*` + `par.grantChanged`（0.2.21 定稿保留的授权/停用核心，现为**休眠源码**）；`ql.diag`（诊断）；`wheel.toggle`（桌面通道）。
- **单一真源**：协议键（`__ql_ns_`/`__ql_cookies__`/`__auth_token__`/`__auth_user__`/`__device_fp__`/`QL_PAGE_TO_BRIDGE`/`QL_BRIDGE_TO_PAGE`）只在 `shared/constants.ts` 定义；host/端口五函数收归 `background/core/host.ts`（口径分工注释随迁）；`applyTitle` → `tabs/tab-title.ts`；待登录凭证 → `core/pending-login.ts`。
- **⚠ 改扩展时的硬约束**：`shared/constants.ts` 会被 **MAIN world**（`content/shield-main.ts`）import → 顶层**禁止**任何扩展 API 求值（`extVersion()` 因此写成函数）。
- **验证基线**：`npm run typecheck` + `npm run build` + 启动冒烟 7/7 + `host.ts` 断言 23/23（`%TEMP%\ql_boot_smoke.py`、`%TEMP%\ql_host_test.mjs`，建议提升进 `tools/`）。
- **待排期（真功能缺陷，属行为变更，本次故意未动）**：① `shield-main` 的 `Storage.prototype.clear` 补丁未区分 storage 实例 → 页面调 `sessionStorage.clear()` 会**清空虚拟 Cookie 袋（含 token）**；② `auto-login.fillPasswordInIframes` 首个可访问 iframe 无密码框即 early-return → **多 iframe 页面自动填表静默失效**；③ bridge 上行入口缺 `.catch`（storage 故障时 unhandled rejection + `sendResponse` 永不调用）；④ `title-hook` 观察器挂 `documentElement` 全树（性能税）；⑤ `pendingAdoptions` 的 TTL 是惰性的（注释承诺的"超时自动放弃"永不发生，加定时器＝行为变更）。

## 0.2.22 撤销与改版（Page Monitor 下线 · 轮盘页签名 · 弹窗 · 版本真源 · 端口口径）
- **Page Monitor 整块撤销（用户定稿）**：删除 `background/core/page-monitor.ts`、`content/pages-overlay.ts`、`docs/FEASIBILITY-RECENT-PAGES.md`；摘除 manifest `quick-pages`(Alt+W) 命令、桥上行 `pageNames`、`shield-main` 名称嗅探、`pages.recent`/`pages.jump`/`RecentPageEntry`、`ql:recentPages`/`RECENT_PAGES_MAX`、SW 的对应消息与命令分支及 `togglePagesOverlay`、`build.mjs` 入口。
  - 页签标题**仍然是页签名**（`tabs/tab-title.ts` + `content/title-hook.ts` 未动）——撤销的是「用页面信息改写标题」，不是「标题显示页签名」。
  - 别混淆：`services/browser_pool.monitor_start/stop` + `runtime/monitor/`（托管会话请求录制）是**另一套**，未动。
  - ⚠ 上游同步会把它带回来（v3.11/v3.13 特性）→ 按上清单再摘一次。
- **轮盘扇区名口径**：页签名优先、空则账号名（桌面 `wheel-picker.html` 的 `labelOf()` ≡ 扩展 `ui/wheel/wheel-core.ts`）；防重绘指纹已含显示名（否则改页签名后标签不刷新）。
- **弹窗**：站点授权区块下线（**只删 UI**——`site-auth.ts` / `par.grantChanged` / `ql:blockedHosts` 全保留）、导出诊断入品牌头右上角、版本号入页脚右下角。
- **版本真源**：`extVersion()` 读 `chrome.runtime.getManifest().version`（bump.py 五写含 manifest → 自动跟随项目升版）；**不要再引入手写版本常量**（上游文档仍写「EXT_VERSION 三处必须一致」，已过时）。
- **身份平面端口口径**：`urlHostOf()`（= `new URL(u).host`，带端口）统一用于归属/停用名单/页签收编；规则覆盖（DNR requestDomains）与 Cookie 作用域**故意**无端口。DNR 无法表达端口 → 同主机跨端口的网络平面隔离仍是结构性限制（真要做得换 `urlFilter`）。
- **验证基线（0.2.22 本轮）**：扩展启动冒烟 7/7（隔离 profile：SW 启动未崩 / 命令清单仅 `quick-wheel` / 无 `ql:recentPages` / 无 `ql:pageNames` / `akso:acctMap` 数据面同步成功）；轮盘真机数据 5/5（`T0901`→TTTTT、未设页签名回落账号名）；弹窗渲染 8/8。
- **扩展端仍无单测框架** → 改扩展必跑 `npm run typecheck` + `npm run build` + 启动冒烟。冒烟要点：**有头模式** + `--load-extension=dist` + 读 SW 的 manifest/commands/storage（headless 下 MV3 扩展不加载，实测 `service_workers` 为空）；本轮为临时脚本，建议提升为 `tools/verify_extension_boot.py`。
