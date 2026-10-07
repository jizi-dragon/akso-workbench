# 配置项说明（CONFIG）

> **本文档的读者**：现场排障的人（「这个行为能不能调」）、要加配置项的人。
> 变更频率 O(配置)：**新增/修改任何可配置项或行为参数必须同步更新本文档**（含「改了会怎样」一列）。
>
> 本项目的「配置」散在**五层**，任何一层都可能被误认为「不存在所以不可调」：
> ① 环境变量 ② `settings` 表键 ③ 代码内行为参数（多数不可调） ④ 数据目录文件（文件级开关）
> ⑤ 前端硬编码常量。
>
> 相关文档：[API.md](API.md)（端口与端点）、[SCHEMA.md](SCHEMA.md)（表结构）、
> [ADR-0008](adr/0003-electron-desktop-shell.md)（代理与更新）、[PITFALLS.md](PITFALLS.md)（环境坑清单）。

## 1. 端口与进程（先看这张表）

**端口是固定常量**（决策见 [ADR-0012](adr/0011-port-governance.md)；它取代了
[ADR-0011](adr/0011-port-governance.md) §D-1 的"被占则顺延"）：壳启动时**只认下面这三个号**，
被占就报错退出并说清是谁占的，**不会换号继续跑**。壳把它们
①经环境变量下发给服务端、②公布到 `<数据目录>/runtime/desktop-ports.json`、③由控制服务的
`GET /whoami` 答给浏览器扩展。**别处不要再写这些数字**——`tests/test_ports_contract.py` 会扫描源码钉住这点，
并额外核对壳侧 `PORTS` 与 Python 侧默认值**完全一致**（不一致会表现为"永远等不到就绪"的白屏）。

| 端口（固定） | 归属 | 用途 | 谁在监听 |
|---|---|---|---|
| **18765** | FastAPI 服务（uvicorn / `WorkbenchServer.exe`） | 全部业务 HTTP + 静态页 + `/extension/*` + `/api/health` | sidecar 进程 |
| **18766** | Electron 内置 Chromium | CDP 调试端口，`browser_pool` `connect_over_cdp` 用 | Electron 主进程 |
| **18767** | Electron 控制服务（Node `http`） | 会话窗开户/聚焦/关窗、更新动作、扩展安装引导、`/whoami` | Electron 主进程 |

> **端口被占 = 应用打不开，这是刻意的**：不换号是为了让"打开工作台"永远是同一个地址
> （扩展、书签、运维脚本都指向它们）。报错对话框会区分两种情况：占用者是**我们自己的另一个实例**
> （给 PID/版本，让你去托盘退出它）还是**无关程序**（给 `Get-NetTCPConnection -LocalPort <port>`
> 的排查命令）。见 [ADR-0012](adr/0011-port-governance.md) §D-2。
>
> 判"端口上是不是我们的服务"必须核对身份，不能只看"有人应答"：
> 读 `desktop-ports.json`，或打 `GET /api/health` 看 `app` 字段（`Workbench` 才是我们）。
> 壳的就绪探测本身就会核对这个字段。

| 进程 | 开发态 | 打包态 |
|---|---|---|
| 服务端入口 | `python -m workbench.main`（**自动开浏览器**） | `WorkbenchServer.exe --server`（**不开浏览器**）；两者共用 `<数据目录>/logs/` 下的运行日志（§2.1），打包态另留 `server.log` 面包屑 |
| 壳 | `cd desktop && npm start` | `Workbench.exe` |
| 数据库 | 同左（默认 `%APPDATA%`） | 同左 |

> **扩展也不再是编译期常量**（2026-09-14 起）：它会先读缓存、再问壳的 `/whoami`（**核对 `app` 身份**）、
> 最后回退固定 18765。端口固定后这一步的目的不再是"跟上顺延"，而是**确认 18765 上是不是我们的服务端**——
> 凭据（Fernet 密文 + 原始密钥）会经过这条通道，而密钥就在本机库里（[PITFALLS.md](PITFALLS.md) #23）。
> 常见陷阱：孤儿 uvicorn 占端口，见 [PITFALLS.md](PITFALLS.md) 环境坑 #20。

## 2. 第一层：环境变量

### 2.1 服务端（`workbench/config.py`、`services/browser_pool.py`）

| 变量 | 默认 | 改了会怎样 |
|---|---|---|
| `WORKBENCH_DATA` | `%APPDATA%\Workbench`（Win）/ `~/.workbench` | 整个数据目录（DB、runtime、storage_state、token 缓存、`shell-state.json` 读取路径）一起迁移。**旧数据不会自动搬**——空目录意味着账号全空。⚠️ 例外：2026-09-16 更名前的 `%APPDATA%\AksoWorkbench` 会在首次启动时被**自动接管**（复制、不覆盖现有库；见 [ADR-0013](adr/0003-electron-desktop-shell.md) §D-2） |
| `MOGUL_DATA` | — | `WORKBENCH_DATA` 的兼容别名（**仅当 `WORKBENCH_DATA` 未设时生效**） |
| `WORKBENCH_DB` | `<数据目录>/workbench.db` | 单文件指向。测试用（`conftest.py` 指向临时目录）。**指向不存在的路径会新建空库**，表现为「账号全没了」 |
| `MOGUL_DB` | — | `WORKBENCH_DB` 的兼容别名 |
| `WORKBENCH_HOST` | `127.0.0.1` | 改成 `0.0.0.0` 会把**无认证**的业务面暴露到局域网——**不要这么做** |
| `WORKBENCH_PORT` | `18765` | 服务端业务面 + 静态页。**壳会把这个固定值下发到这里**（ADR-0011 §D-2 的下发通道，端口值本身已按 ADR-0012 固定）。手工改它只影响自己起的进程；**桌面壳运行时它会拒绝启动**（壳只认 18765） |
| `MOGUL_HOST` / `MOGUL_PORT` | — | 上一对的兼容别名 |
| `WORKBENCH_ADAPTERS` | `<项目根>/adapters` | 模块体检读取的适配器目录。测试用（指向仓库 `adapters/`，因为测试数据目录里没有） |
| `WORKBENCH_SESSION_MODE` | `local` | `cdp` = 托管会话复用 Electron 壳的 Chromium（先壳控制服务开户再 `connect_over_cdp`）；`local` = 原生 Playwright chromium。**桌面壳运行时会下发 `cdp`**（2026-09-14 修：此前壳从不设置它，导致永远跑 `local`，与 [ADR-0002](adr/0002-browser-allocation-policy.md) 的意图相反）。开发者手起服务端时留空即 `local` |
| `WORKBENCH_CONTROL_PORT` | `18767` | 壳控制服务端口（固定）。由壳下发；服务端侧的 `routes_update` / `routes_extension` 与 `browser_pool` 都读 `config.CONTROL_PORT`，**不得再写常量** |
| `WORKBENCH_CDP_PORT` | `18766` | 同上，壳的 Chromium 调试口；与 `desktop/ports.js` 的 `PORTS.cdp` 对应 |
| `PLAYWRIGHT_BROWSERS_PATH` | （仅打包态自动设置） | 打包态由 `browser_pool` 工作线程指向 `_MEIPASS/ms-playwright`，**必须在 `sync_playwright()` 之前**。手工覆盖会导致找不到 chromium |
| `AKSO_CC_REPO` / `AKSO_AUTO_REPO` | 见 `adapters/akso-cc.json` 的 `repoPath` / `D:\ai_assistant\akso-auto`（写死在 `services/accounts.py`） | **只影响两件事**：模块体检的 `source-reference` 提示（只有 `AKSO_CC_REPO` 走这条，它是 `adapters/akso-cc.json` 的 `repoPathEnv`）、「原项目 env 导入」去哪里扫 `env.json`（`accounts.scan_original_env_files` 仍同时扫 akso-cc 与 akso-auto 两处，**只读**）。**不影响任何业务功能**（能力已原生化，见 [ADR-0005](adr/0005-native-internalization-of-four-projects.md)） |
| `NODE_COMMAND` | `node` | 遗留（`services/proc.py` 的 Node 子进程封装，仅被 `tests/test_proc.py` 使用）。**运行时链路不再依赖 Node** |
| `WORKBENCH_SKILL_PYTHON` | （空） | **脚本式 Skill 用的 Python 解释器**（`workbench/skills/script_runner.py`）。留空 = 用 `sys.executable`（开发态正确）。⚠️ **打包态必须显式设置**：冻结态下 `sys.executable` 是 `WorkbenchServer.exe`、**不是解释器**，拿它跑脚本必然失败——所以 `find_python()` 在"冻结态 + 本变量为空"时**直接报错并说清怎么配**（不做"试一下再说"，因为这条报错会出现在用户现场，必须自解释）。给的路径**必须是真实存在的文件**，否则同样报错。用户视角见 [USER-MANUAL.md](USER-MANUAL.md) §10 |

### 2.2 桌面壳（`desktop/*.js`）

| 变量 | 默认 | 改了会怎样 |
|---|---|---|
| `WORKBENCH_PROXY` | — | **强制**使用该代理串（最高优先级）。用于「本机代理坏了但环境变量还在」的排查。**更名前的 `AKSO_PROXY` 仍然认**（用户手写的启动脚本不该因为改名失效，见 [ADR-0013](adr/0003-electron-desktop-shell.md) §D-3） |
| `HTTPS_PROXY` / `HTTP_PROXY` | — | 同上（`WORKBENCH_PROXY` 优先）。⚠️ 这两个是**通用变量名**，很多工具会设——壳会因此走代理 |
| `WORKBENCH_UPDATE_OVERRIDE` | — | 把 `electron-updater` 的 feed 换成 `generic` provider 指向的 URL（本地静态目录）。**只影响更新源，不改判定逻辑**；用于离线跑通整条更新链（`tools/verify_update_flow.py`）。更名前的 `AKSO_UPDATE_OVERRIDE` 仍然认 |
| `WORKBENCH_NO_PROXY` | — | 发布脚本（`tools/publish_release.py`）的**强制直连**开关。更名前的 `AKSO_NO_PROXY` 仍然认；同族的 `AKSO_HTTPS_PROXY` 亦然 |
| `WORKBENCH_AUTO_UPDATE` | **未设 = 关闭** | **应用会不会自己替换自己**（用户定稿 2026-09-15）。只认 `1`/`true`/`yes`/`on`；未设时不启动定时检查、不静默下载、不在退出时安装，托盘里的手动检查也只回一句「已关闭」。刻意做成**环境变量而非设置项**——它是部署期决定，放运行时设置里迟早被误点。开启方式：设 `WORKBENCH_AUTO_UPDATE=1` 后重启应用 |
| `WORKBENCH_EXTENSION_AUTH_GRACE` | `86400`（24h） | `/extension/*` 访问令牌的**宽限期**（秒），[ADR-0015](adr/0002-browser-allocation-policy.md) D-5。令牌是新协议要求，已装的**旧扩展不会带它**；窗口内无令牌照常放行并留 warning，窗口过后 401（且 `/extension/health` 的 `authBlocked` 会让账号中心提示"重新加载扩展"）。**`0` = 严格模式**：现场排障时用来分诊"扩展连不上"是不是令牌问题。⚠ 非整数时回落默认值并留 warning |
| `WORKBENCH_EXTENSION_ORIGIN` | 空（= 不做来源校验） | 逗号分隔的 `Origin` 允许清单，**配了就强制校验**（ADR-0015 D-7 的可选加固层）。留空的理由：桌面壳目前不声明扩展 id（用户手装的扩展 id 由 manifest 公钥决定、跨机一致，但不在本仓配置里）。**主防线是令牌，不是它**——本机进程可以任意伪造 `Origin` 头 |
| `ELECTRON_MIRROR` | `https://npmmirror.com/mirrors/electron/`（`build.ps1` 设） | 只影响 electron 二进制下载源 |
| `GH_TOKEN` | — | 发布到 GitHub Releases 时注入（`--publish always`）；不设则只能手工上传 |
| `APPDATA` | 系统 | 壳的 `shell-state.js` / `ports.js` 用它解析数据目录，与 Python 侧 `config.DATA_DIR` 必须一致（`desktop-ports.json` 就落在这里的 `runtime/` 下） |

> **令牌本身不是环境变量**：它由服务端生成并落 `settings.extension_access_token`
> （跨重启稳定——否则每次重启都会让 Chrome 里的扩展拿旧令牌狂刷 401）。
> 扩展经 `POST /extension/hello` 自举领取，见 [API.md](API.md) §11。

### 2.3 适配器声明的变量（`adapters/*.json`，**现存只有 `akso-cc.json`**）

| 键 | 值 | 说明 |
|---|---|---|
| `id` | `insight` | **模块 id（本工作台自己的名字）**，不是来源项目名 |
| `repoPath` | `D:\ai_assistant\akso-cc` | 只读参考存档路径（原仓库所在处） |
| `repoPathEnv` | `AKSO_CC_REPO` | 环境变量可覆盖 `repoPath`（环境变量优先） |
| `mode` | `native-python-internalized` | 表示能力已内化；`frozen: true` |
| `deprecation` | 文本 | 说明原生化事实与文件降级为只读参考 |

> **文件名是来源项目名，`id` 是本工作台的模块名，两者刻意不一致**：
> 2026-09-14 `akso-cc` → `insight`，但**文件名没动**——`akso-cc.json` 是"这份存档对应哪个
> 原仓库"的引用真源（`repoPathEnv=AKSO_CC_REPO` 也据此命名）。改文件名会让 `repoPathEnv`
> 的语义失真。这条口径由 `tests/test_modules.py` 的断言守住（不许泄漏上游名 / 引用不许断）。
>
> ⚠️ **2026-09-22：`adapters/akso-auto.json` 随配置工厂删除**（那份存档的 `id` 曾是
> `factory`）。模块清单里因此不再有 `akso-auto` / `factory` 两个 id（都返回 404），
> 模块维度也不再有工厂体检项。但「原项目 env 导入」那条**只读**通路没动：
> `services/accounts.py` 仍按 `AKSO_AUTO_REPO` 去扫 akso-auto 那棵树（见 §2.1 服务端环境变量表）。
>
> ℹ️ `adapters/*.json` 的 `$schema` 字段原指向已删除的 `docs/模块契约.md#adapters`，
> 已于本轮文档重构改为 `docs/SCHEMA.md`（只动这一个字段）。除此之外这些文件属于
> 「原项目只读引用」区，改动需谨慎（见 `CODEBASE_OVERVIEW.md`（**2026-09-29 曾删除，2026-10-07 重新生成**） 的「原四仓库只读」约定）。

### 2.1 运行日志（`workbench/logging_setup.py`，2026-09-20 引入）

改造前全仓 38 处 `logger.*` 调用**一个字节都落不下来**（没有任何 handler）：
19 处 `info`/`debug` 被 `logging.lastResort`（WARNING 级）静默丢弃，
19 处 `warning`/`error` 写 stderr 而壳以 `stdio:'ignore'` 丢弃。这几个变量是它的开关：

| 变量 | 默认 | 说明 |
|---|---|---|
| `WORKBENCH_LOG_DIR` | `<数据目录>/logs` | 日志目录。测试用它指向临时目录 |
| `WORKBENCH_LOG_LEVEL` | `INFO` | 根日志级别（`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`）。**非法值回落 `INFO` 而不是报错**——日志配置不该阻断启动 |
| `WORKBENCH_LOG_FORMAT` | 打包态 `jsonl`，开发态 `text` | `jsonl` = 一行一个 JSON 对象（可 `jq`/聚合）；`text` = 人读单行。**两者是同一套字段，不是两套日志** |
| `WORKBENCH_LOG_MAX_BYTES` | `8388608`（8 MB） | 单文件体积上限；超过即强制归档一次。**安全阀，不是容量规划** |
| `WORKBENCH_LOG_BACKUP_COUNT` | `14` | 保留多少个归档 |
| `WORKBENCH_PARENT_PID` | 空（= 不监视） | **由桌面壳下发**（`desktop/main.js::spawnServer` 填自己的 pid）。设了它之后，sidecar 起一个守护线程等**父进程句柄**，父一结束就自退 —— 防的是"壳被强杀/崩溃 → sidecar 变孤儿占着固定端口 → 下次启动失败"（[lifecycle.py](../workbench/lifecycle.py)，PITFALLS #54）。**未设 = 不监视**：Web 模式（`python -m workbench.main`）与任何独立运行都属这种情况，**不要手工设它**（设错会让自己被误杀） |

> ⚠️ **这几个变量全部失败安全**：日志目录建不起来时 `configure()` 返回 `None` 并往 stderr
> 写一行，**应用照常启动**。这是刻意的——本仓对留痕的一贯纪律是
> 「留痕是义务，不是执行的前置条件」（同 `approvals.write_audit` / `cache.persist`）。

## 3. 第二层：`settings` 表键

读写唯一入口：`services/storage.get_setting` / `set_setting`（UPSERT）。
**按用途分组，`值形态` 一列决定你能不能手改。**

### 3.1 现行功能键

| 键 | 值形态 | 默认/缺失行为 | 改了会怎样 |
|---|---|---|---|
| `apiKey` | DeepSeek API Key 明文 | 空 → `ready=false`，对话直接回「请先在设置中配置」 | 换 Key 即换计费账号；**明文存库**是本机信任边界内的既有契约 |
| `model` | 模型 id 字符串 | `deepseek-v4-flash`（`deepseek.DEFAULT_MODEL`） | 见 §5 模型清单；**写错模型名会让对话整体 502/报错** |
| `temperature` | 十进制字符串 | `0.7` | 解析失败回落 0.7 |
| `account_fernet_key` | urlsafe base64 32 字节 | **首次使用时生成** | ⚠️ **改它 = 全部已存口令不可解密**（接口能读、扩展静默丢弃全部账号）。见 [ADR-0007](adr/0007-credentials-sandbox-and-trust.md) |
| `disabled_boxes` | JSON 数组字符串 | `[]` | 禁用盒在**轮盘**中被跳过（账号中心与管理不受影响） |
| `rememberedBoxes` | JSON 数组字符串 | `[]` | 记忆盒子清单（含空盒）。**重命名走原位替换**，手工追加会让盒名顺序错乱 |
| `defaultBoxName` | 字符串 | 空 → 显示「默认盒子」 | 只改默认盒的**显示名**（`box=''` 是存储值，不变） |
| `ext_cmd_seq` | 整数字符串 | `0` | ⚠️ **指令序号游标**。清空/回退会让扩展持久游标把所有新指令 `seq > after` **永久吞掉**（0.2.4 实锤）。见 [API.md](API.md) §12.3 |
| `ext_connected_once` | `"1"` | 无 | **一次性闩锁**：写入后账号中心的扩展安装提示条**永久静默**。清掉它会重新弹提示 |
| `insight_workspace_root` | 绝对路径字符串 | 空 → `我的文档\Workbench\洞察` | **对象分析产物的落盘根**（`<root>/<对象code>/<时间戳>/`）。默认目录名仍是 `洞察`（更名没动磁盘结构）。改它只影响**后续**运行，已产出的目录不会被搬动；填相对路径/指向文件/无法创建时接口返回 400。清空即回默认 |

### 3.2 已下线功能的残留键（勿读勿写）

| 键 | 来源 | 处置 |
|---|---|---|
| `dingtalkLastSync` | 钉钉同步（已下线） | `--purge-legacy-settings` 可清 |
| `embeddingApiKey` | 知识库向量化（已下线） | 同上 |
| `embeddingBaseUrl` | 同上 | 同上 |
| `embeddingModel` | 同上 | 同上 |

> 实测现状（2026-09-11）：settings 共 12 键，其中 2 个密钥类（值已被掩码展示），
> `ext_cmd_seq=77`、`ext_connected_once=1`、`model=deepseek-v4-flash`、`temperature=0.7`、
> `rememberedBoxes=[]`、`disabled_boxes` 含 1 个盒名（真实库经控制台显示为乱码，是**控制台编码**问题，非数据问题）。

## 4. 第三层：代码内行为参数（**多数不可调**）

### 4.1 自动登录节奏门控（五重门）

Python 侧（`services/autologin.py`，从 quick-login 全量迁移，参数与原实现一致）：

| 参数 | 值 | 作用 |
|---|---|---|
| 总截止 `deadline` | `30000` ms | 超过即放弃本次自动登录 |
| 提交前回读 `CLICK_DELAY` | `500` ms | 填完 → 等 500ms → 复核两字段 → 才 click（受控组件状态未落地则推迟） |
| 点击后观察期 `OBSERVE_WINDOW` | `3500` ms | 观察期内不重复点击 |
| 即时填充去抖 | `100` ms | `MutationObserver` + `window load` → 去抖后 attempt |
| 轮询兜底 | `800` ms | `setInterval` 兜底尝试；也是「提交按钮连续两轮检测不到 = 登录成功」的判定节拍 |
| 失败感知让位 | 观察期内 antd 错误元素新增 ≥1 记一次，**累计 2 次停手** | 防在错误口令下反复重试 |
| 用户点击接管 | `userTouched` 永久让位 | trusted input/click 后引擎永久停止 |

扩展侧（`content/auto-login.ts`）另有一套同源实现，参数独立——**两侧不对齐时表现为
「桌面托管能登、扩展里登不上」或反之**。改一侧必须对照另一侧。

### 4.2 `browser_pool`

| 参数 | 值 | 说明 |
|---|---|---|
| 会话自愈上限 | `heal_count ≤ 2` | 成功时**不可清零**（掩盖已自愈事实）；上限防死循环 |
| CDP 开户等待 | `time.sleep(0.5)` | 等 BrowserWindow 完成创建与首次 `loadURL` |
| CDP 定位标记页超时 | `8` s（每 0.2s 轮询） | 找不到 `blank.html?w=` 标记页即报错 |
| 平台异步鉴权复核 | `2.5` s | `online` 后稍候复核，避免 online → 自愈 的状态抖动 |
| `page.goto` 超时 | `30000` ms | |
| `monitor_start` 作业超时 | `90` s | **唯一**显式设超时的公共 API（因为可能触发开户） |
| 其余作业超时 | **无** | 页面卡住时 HTTP 请求会一直挂着——见 `CODEBASE_OVERVIEW.md`（**2026-09-29 曾删除，2026-10-07 重新生成**） 的风险台账 |
| 事件 drain 时机 | 每个作业结束后 | Monitor 的 `monitor.drain()`（[ADR-0006](adr/0002-browser-allocation-policy.md)） |

### 4.3 `routes_extension`（执行面）

| 参数 | 值 | 说明 |
|---|---|---|
| `_STATE_TTL_MS` | `60_000` | 执行面状态快照 TTL（扩展每 ~6s 上报） |
| `_PROBE_TTL_RUNNING_MS` | `10_000` | 「Chrome 在运行」正结果缓存（Chrome 不会无声消失） |
| `_PROBE_TTL_STOPPED_MS` | `2_000` | 负结果只信 2s（刚退出时仍要能拉起） |
| 长轮询上限 | `25` s | `min(max(wait,0),25)`；扩展请求 `wait=15` |

### 4.4 其他

| 参数 | 位置 | 值 |
|---|---|---|
| eGMP token 缓存 TTL | `egmp/client.py` `TOKEN_TTL_S` | `55 * 60` 秒 |
| httpx 超时 | 同上 | 登录 30s；客户端默认 60s |
| DeepSeek 流式/非流式超时 | `services/deepseek.py` | 流式 120s（连接 15s）；非流式 90s |
| 最大步数预算 | `harness/types.DEFAULT_MAX_STEPS`（默认 40），**设置页可改**（`settings.maxSteps`，范围 4~200） | 一次用户消息里允许的**工具调用**次数。只读探索与危险写一样计费；耗尽只是"这一轮到头了"，回一句「继续」即接着做 |
| 输出截断判据 | `services/deepseek.py` 收 `finish_reason` | `length` = 被长度上限截断 → 本轮终止且**不执行任何工具**。本仓**从不发送 `max_tokens`**，用服务端默认值（官方文档：非思考 8K / 思考 64K） |
| 附件上传上限 | `config.ATTACHMENTS_MAX_BYTES` / `ATTACHMENTS_MAX_PER_TURN` | 20 MB（单文件）/ 5 个（每轮） |
| 产物文本截断 | `routes_insight.read_artifact` | 2,000,000 字符 |
| 审计字段截断 | `routes_agent._audit` | `args` 2,000 / `result_summary` 1,000 字符 |
| 列表接口上限 | 各 `limit` 参数 | 200（insight）、500（agent audit / skills / sandbox） |
| `version_tuple` 容错 | `routes_update` | 非数字段按 0 处理 |

## 5. 第五层：前端硬编码常量

| 常量 | 位置 | 值 | 说明 |
|---|---|---|---|
| 模型清单 | `static/js/settings.js` `MODEL_OPTIONS` | `deepseek-v4-flash`（经济版）、`deepseek-v4-pro`（旗舰版） | ⚠️ **只有这两个选项，UI 无法输入自定义模型**。若平台不接受这两个名字，用户只能改 DB 的 `model` 键 |
| 默认模型回落 | `static/js/chat.js` | `deepseek-v4-flash` | 仅用于角标显示的回落 |
| API 基址 | `static/js/api.js` | 同源相对路径 | 无跨域配置（[API.md](API.md) §0） |
| 温度范围 | `settings.js` + `index.html` | `0 ~ 2`，步长 `0.1`，默认 `0.7` | |
| 账号中心轮询 | `static/js/accounts.js` | 3 s | 更新状态角标的数据源（读 `/api/update` 的本地文件） |
| 轮盘窗尺寸 | `desktop/main.js` | `560 × 640`，无边框透明置顶，失焦自动关（400ms 启动宽限） | |
| 主窗尺寸 | `desktop/main.js` | `1440 × 920`（最小 1100×700） | |
| 会话窗尺寸 | `desktop/main.js` | `1440 × 900` | |
| 全局热键 | `desktop/main.js` | `Alt+Q`（轮盘单例 toggle） | **桌面独占**：扩展已不注册任何快捷键（2026-09-14 删 manifest `commands`），桌面未运行时不会弹出浏览器版轮盘（见 `EXTENSION-PLANE.md`（**该文件已于 2026-09-29 删除**） §8） |
| 服务就绪等待 | `desktop/main.js` `waitReady` | 40 s，每 300ms 探一次 `/` | 超时则主窗加载 `blank.html?error=timeout` |
| 更新检查节拍 | `desktop/updater.js` | 启动后 **20s** 首查，之后每 **6h** | |
| 壳状态心跳 | `desktop/updater.js` | 15 s 写一次 `shell-state.json` | 服务侧 TTL 60s |
| 代理探测超时 | `desktop/proxy.js` | 6000 ms | 超时即回落系统代理 |
| 扩展轮询节拍 | `extensions/.../sync.ts` | 数据面 **2s**；状态上报每 3 tick（≈6s）；指令走长轮询（`wait=15`） | |
| SW 复活节拍 | `extensions/.../sync.ts` | `chrome.alarms` 0.5 min | MV3 SW 空闲约 30s 被杀，这是唯一复活通道 |
| 扩展诊断缓冲 | `extensions/.../parallel-session.ts` | `ql:diag` 环形 **60** 条；`ql:forensics` 环形 **120** 条（**密码绝不入日志**） | |

## 6. 第四层：数据目录文件（文件级开关）

根目录：`%APPDATA%\Workbench`（Windows）或 `WORKBENCH_DATA`。

| 路径 | 谁写 | 谁读 | 能否手改 |
|---|---|---|---|
| `workbench.db` (+ `-wal` / `-shm`) | 服务端 | 服务端 | ⚠️ 手改需关闭应用；WAL 模式下直接拷 `.db` 可能不含最新事务 |
| `shell-state.json` | **壳**（每 15s 原子写） | 服务端 `GET /api/update` | 只读更好：手改会污染 UI 判定；删除 → UI 显示「桌面壳未运行」 |
| `runtime/desktop-ports.json` | **壳**（启动时原子写，退出时删） | 服务端 `/api/health`、人排障、工具 | 只读。**这是「当前端口是多少」的权威答案**（[ADR-0011](adr/0011-port-governance.md)）；手改会让排障信息失真。壳异常退出时可能残留，下次启动会按其中的 `pid` 判断它是否还有效 |
| **`proxy.txt`** | **用户** | 壳（`proxy.apply`） | ✅ **这是给用户的开关**：一行代理串 = 强制代理；**存在但为空 = 强制直连**（终极兜底） |
| `logs/workbench-<日期>.jsonl` | 服务端（**全部运行日志**） | 人 + 工具 | 只读。**主日志**：启动横幅 / 路由注册 / 工具自检 / 工具调用（带 `auditId`）/ 请求行 / 异常栈。按天 + 按体积双滚动，保留 14 份。读口是 `GET /api/logs/tail`（[API.md](API.md) §13） |
| `logs/sidecar-stdio-<日期>.log` | **壳**（sidecar 的原始 stdout/stderr） | 人 | 只读。**兜底通道**：Python 日志系统**装配之前**的失败（解释器引导、`import` 期异常）只走 stderr，只有这里抓得到 |
| `server.log` | 服务端（**仅打包态**） | 人 | 只读。**启动面包屑，不是日志**：一行（版本/pid/URL），用裸 `open()` 写，因此**在日志系统自身失效时仍然有效**。`blank.html` 的「服务端没有应答」页面直接引用它 |
| `.updaterId` | electron-updater | 同上 | 勿删（更新通道标识） |
| `runtime/insight/<job_id>/` | 洞察 | `routes_insight` 产物接口 | 可删（历史产物） |
| `runtime/browser-states/<account_id>.json` | `browser_pool` | 同上 | ✅ 删 = 该账号回到「需登录」（等价于 `POST /api/browser/forget/{id}`） |
| `runtime/monitor/<acc8>-<stamp>/` | Monitor | 人 | 可删（录制产物） |
| `runtime/egmp-tokens/token-<env_id>.json` | `egmp.client` | 同上 | ✅ 删 = 强制重新登录换取 token（55 分钟 TTL） |
| `Partitions/` / `Cache/` / `Code Cache/` / `Local Storage/` … | Electron | Electron | 浏览器缓存/会话窗分区（每账号 `persist:acc-<id>`）。⚠️ **删 Partitions 会清掉所有账号的浏览器登录态**（但 DB 里的凭据仍在） |
| `workbench.backup-YYYYMMDD-HHMMSS.db` | `tools/db_maintenance.py --backup` | 人 | ✅ 归档对象；实测有 85.7 MB 的历史备份 |

## 7. 「我想改 X，该动哪里」速查

| 想改 | 动哪里 | 注意 |
|---|---|---|
| 监听端口 | `WORKBENCH_PORT` + `main.js` 的 `SERVER_PORT` + 扩展 `sync.ts` 的 `DESKTOP` | 三处必须同步，否则扩展静默失联 |
| 换模型 | 设置页（仅两个选项）或 DB `settings.model` | UI 无自定义输入框 |
| 加一个新配置项 | 优先放 `settings` 表（`services/settings.py` 集中建模）；环境变量只用于**进程级**旋钮 | 加表列要新增迁移（[SCHEMA.md](SCHEMA.md) §6） |
| 让更新走固定代理 | `%APPDATA%\Workbench\proxy.txt` 写一行代理串 | 空文件 = 强制直连 |
| 让某盒子不出现在轮盘 | `POST /api/accounts/boxes/disable` | 只影响轮盘，不影响账号管理 |
| 清空某账号的免密登录态 | `POST /api/browser/forget/{account_id}` | 等价于删 `runtime/browser-states/<id>.json` |
| 关掉「未检测到扩展」提示条 | 它已在首次连接后永久静默（`ext_connected_once`） | 若反复出现，说明该键未落库 → 查 DB 写权限 |
| 调整自动登录节奏 | **不要手改参数**——五重门是实测收敛的结果 | 改前先读 §4.1 与 [ADR-0002](adr/0002-browser-allocation-policy.md) |
