# Changelog

本项目遵循语义化版本（SemVer），格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。
从第一天开始记录（对齐行业月更节奏惯例）。

## [Unreleased]

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
