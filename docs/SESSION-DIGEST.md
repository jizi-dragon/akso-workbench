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

## 扩展基线校正（0.2.5 摸底 · 0.2.6 已同步）
- `extensions/quick-login/` = **上游 v3.13.2（2026-09-10 同步，36 文件前移）+ akso-mogul 私有改造**。私有改造清单：① manifest host_permissions:18765 + alarms 权限 + quick-wheel 热键 Ctrl+Shift+Q；② `src/background/sync.ts` 桌面同步桥（含 seq/快照/毒指令全套护栏）；③ `src/background/account-wheel.ts`（toggleAccountWheel 抽取，sync 直调）；④ service-worker 挂载 sync + import 轮盘；⑤ parallel.html 隐藏账号增删改区块 + 会话视图文案；⑥ wheel-overlay 双 interval 修复
- 上游 v3.11.1→v3.13.2 已带入：登录态生命周期跟随页签、Cookie 袋权威同步、取证黑匣子+导出诊断（parallel 页新增「导出诊断」按钮）、AuthCode 时效集、AUTH main_frame/全资源类型
- **dist 真实位置 = `extensions/quick-login/dist/`**（不是 packages/extension/dist）；构建 `npm run build`（workspace 根）
- 账号中心 UI 复刻基线（上游 parallel 管理页 14 项差距）见目标档案：四态徽标/批量管理/diff 防闪烁/移盒弹窗/盒子禁用/删盒两步处置/诊断导出/站点授权健康/顶栏统计/数字键0=第10/轮盘动效等

## 待办/可选（未做）
- **真机 Chrome 全链路验收**：装载 dist → Alt+Q 轮盘 → 选账号 → Chrome 自动登录 → 可用
- **账号中心 UI 复刻**（按 14 项基线，优先级纪律：quick-login 本体优先，akso-auto/akso-cc/Monitor 结合后置）
- egmp writers 真机首跑验证（create 写配置需测试环境授权；monitor 侧已真机验收）
- NSIS 安装器静默装 UAC 未落盘验证；若需"关主窗后会话常驻"：服务与壳解耦为独立进程
- 安全加固（扩展侧产品级隐患，暂挂）：明文凭据 60s 投递窗口（getPendingAutoLogin 读后不删——3.13.2 是否已改需复核）、同步通道无认证

## 运维速记
- 启动：服务 `uv run python -m workbench.main`；桌面 `cd desktop && npm install && npm start`
- 构建：`powershell -File tools\build.ps1`；测试：`.venv\Scripts\python -m pytest`（或 uv run pytest）；lint：`uvx ruff check .`
- 账号中心真机账号：dev-admin / dev-user（标准验证 + example-config 环境）
