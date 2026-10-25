# 配置项说明（CONFIG）

> **本文档的读者**：现场排障的人（「这个行为能不能调」）、要加配置项的人。
> 变更频率 O(配置)：**新增/修改任何可配置项或行为参数必须同步更新本文档**（含「改了会怎样」一列）。
>
> 本项目的「配置」散在**五层**，任何一层都可能被误认为「不存在所以不可调」：
> ① 环境变量 ② `settings` 表键 ③ 代码内行为参数（多数不可调） ④ 数据目录文件（文件级开关）
> ⑤ 前端硬编码常量。
>
> 相关文档：[API.md](API.md)（端口与端点）、[SCHEMA.md](SCHEMA.md)（表结构）、
> （代理与更新）、[PITFALLS.md](PITFALLS.md)（环境坑清单）。

## 1. 端口与进程（先看这张表）

**Tauri 壳使用动态端口**：Rust `free_port()` 绑定 `127.0.0.1:0` 选空闲端口，
通过 `WORKBENCH_PORT` 下发给 Python sidecar；不再有 Electron 控制服务，也不再有固定 18767。

| 端口 | 归属 | 用途 | 谁在监听 |
|---|---|---|---|
| **动态**（Tauri） | FastAPI sidecar | 全部业务 HTTP + 静态页 + `/api/health` | `WorkbenchServer.exe` |
| **18765**（仅 Web 模式） | `python -m workbench.main` | 开发调试；自动开系统浏览器 | 手工启动的 uvicorn |

> 2026-10-19：内置浏览器下线（18766 CDP 删除），Electron 壳退役（18767 控制服务删除）。
> Tauri 启动多个实例时由 single-instance 插件聚焦已有窗口；端口由 OS 分配，不再“被占就退出”。

| 进程 | 开发态 | 打包态 |
|---|---|---|
| 服务端入口 | `python -m workbench.main`（**自动开浏览器**）或 Tauri 自动拉起的 `python -m workbench.server_entry` | `WorkbenchServer.exe --server --port <动态>`（**不开浏览器**）；日志在 `<数据目录>/logs/` |
| 壳 | `npx --yes @tauri-apps/cli@2 dev` | `Workbench.exe` + WebView2 |
| 数据库 | 同左（默认 `%APPDATA%`） | 同左 |

> **服务地址不是编译期常量**：Tauri 窗口打开 `http://127.0.0.1:<动态端口>/`；
> Python 的 `/api/health` 用 `app=Workbench` 自证身份。Web 模式仍用 18765。
> 常见陷阱：孤儿 sidecar 占着数据库/端口，见 [PITFALLS.md](PITFALLS.md) #20 / #54。

## 2. 第一层：环境变量

### 2.1 服务端（`workbench/config.py`）

| 变量 | 默认 | 改了会怎样 |
|---|---|---|
| `WORKBENCH_DATA` | `%APPDATA%\Workbench`（Win）/ `~/.workbench` | 整个数据目录（DB、runtime、storage_state、token 缓存）一起迁移。**旧数据不会自动搬**——空目录意味着账号全空。⚠️ 例外：2026-09-16 更名前的 `%APPDATA%\AksoWorkbench` 会在首次启动时被**自动接管**（复制、不覆盖现有库）。★ **显式设置 `WORKBENCH_DATA` / `WORKBENCH_DB` 时不接管**——"用这个目录"就是"别自己找"（测试与验收脚本依赖此语义） |
| `MOGUL_DATA` | — | `WORKBENCH_DATA` 的兼容别名（**仅当 `WORKBENCH_DATA` 未设时生效**） |
| `WORKBENCH_DB` | `<数据目录>/workbench.db` | 单文件指向。测试用（`conftest.py` 指向临时目录）。**指向不存在的路径会新建空库**，表现为「账号全没了」 |
| `MOGUL_DB` | — | `WORKBENCH_DB` 的兼容别名 |
| `WORKBENCH_HOST` | `127.0.0.1` | 改成 `0.0.0.0` 会把**无认证**的业务面暴露到局域网——**不要这么做** |
| `WORKBENCH_PORT` | `18765`（仅 Web 模式） | 服务端业务面 + 静态页。**Tauri 会把它设成动态端口**；手工启动时可用它指定端口。 |
| `MOGUL_HOST` / `MOGUL_PORT` | — | 上一对的兼容别名 |
| `WORKBENCH_ADAPTERS` | `<项目根>/adapters` | 模块体检读取的适配器目录。测试用（指向仓库 `adapters/`，因为测试数据目录里没有） |
| `AKSO_CC_REPO` / `AKSO_AUTO_REPO` | 见 `adapters/akso-cc.json` 的 `repoPath` / `D:\ai_assistant\akso-auto`（写死在 `services/accounts.py`） | **只影响两件事**：模块体检的 `source-reference` 提示（只有 `AKSO_CC_REPO` 走这条，它是 `adapters/akso-cc.json` 的 `repoPathEnv`）、「原项目 env 导入」去哪里扫 `env.json`（`accounts.scan_original_env_files` 仍同时扫 akso-cc 与 akso-auto 两处，**只读**）。**不影响任何业务功能**（能力已原生化） |
| `NODE_COMMAND` | `node` | 遗留（`services/proc.py` 的 Node 子进程封装，仅被 `tests/test_proc.py` 使用）。**运行时链路不再依赖 Node** |
| `WORKBENCH_WINDOWS_ACL_RUNNER` | （空） | **可选：DSH Windows ACL runner 的 `lib/runner.js` 路径**。设置后 `workspace-write` 策略由 DSH runner 真正强制（capability SID + Low label）；未设置时自动找 `<数据目录>/sandbox-runner/.../runner.js`，都没有则退回原“声明”路径。可用 `python tools\install_sandbox_runner.py` 安装；runner 缺失/失败 → 拒绝执行 |
| `WORKBENCH_NODE` | PATH 上的 `node` | 上一条 runner 使用的 Node 可执行文件；打包/多 Node 环境可显式指定。**这两个变量只影响 sandbox，不影响其他功能** |
| `WORKBENCH_SKILL_PYTHON` | （空） | **脚本式 Skill 用的 Python 解释器**（`workbench/skills/script_runner.py`）。留空 = 用 `sys.executable`（开发态正确）。⚠️ **打包态必须显式设置**：冻结态下 `sys.executable` 是 `WorkbenchServer.exe`、**不是解释器**，拿它跑脚本必然失败——所以 `find_python()` 在"冻结态 + 本变量为空"时**直接报错并说清怎么配**（不做"试一下再说"，因为这条报错会出现在用户现场，必须自解释）。给的路径**必须是真实存在的文件**，否则同样报错。用户视角见 [USER-MANUAL.md](USER-MANUAL.md) §10 |

### 2.2 Tauri 壳（`src-tauri/src/main.rs`）

| 变量 | 默认 | 改了会怎样 |
|---|---|---|
| `WORKBENCH_DATA` | `%APPDATA%\Workbench` | 由壳继承给 sidecar；手动测试时设成临时目录可隔离账号库与日志 |
| `WORKBENCH_PARENT_PID` | 空 | **由 Tauri 壳下发**（`std::process::id()`）。设了它之后 sidecar 等父进程句柄，父结束就自退（[lifecycle.py](../workbench/lifecycle.py)，PITFALLS #54）。**不要手工设** |
| `TAURI_SIGNING_PRIVATE_KEY` | — | **仅构建/签名时**使用；指向 updater 私钥文件。绝不要写进仓库或安装包 |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | 空 | 私钥密码；生成密钥时设置过才需要 |
| `AKSO_VAULT_URL` | `https://www.dragonrain.top`（443） | **云端账号库地址**；写别的地址会让设备流指到另一套账号库 |

> **2026-10-09 已删除的两个变量**：`WORKBENCH_EXTENSION_AUTH_GRACE` 与
> `WORKBENCH_EXTENSION_ORIGIN`——它们服务的是 `/extension/*` 的访问令牌与来源校验，
> 而浏览器扩展已整体移出本仓库（用户决策：桌面端与扩展不耦合）。
> 令牌、中间件、自举端点同时删除。

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
| `WORKBENCH_PARENT_PID` | 空（= 不监视） | **由 Tauri 壳下发**（`std::process::id()`）。sidecar 起守护线程等父句柄，父一结束就自退（[lifecycle.py](../workbench/lifecycle.py)，PITFALLS #54）。**未设 = 不监视**：Web 模式与独立运行都属这种情况，**不要手工设它**（设错会让自己被误杀） |

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
| `permissions` | JSON 对象字符串 | `{"mode":"ask","allow":[],"ask":[],"deny":[]}` | 工具权限策略：`deny > ask > allow`，`mode` 为 `ask`/`auto`。**非法配置会让所有工具调用被拒（Fail-Closed）**；改前先 `GET /api/settings/permissions` |
| `account_fernet_key` | urlsafe base64 32 字节 | **首次使用时生成** | ⚠️ **改它 = 全部已存口令不可解密**（接口仍能列出账号，但口令再也解不开）。 |
| `disabled_boxes` | JSON 数组字符串 | `[]` | 禁用盒在账号中心的默认视图里被跳过（管理不受影响） |
| `rememberedBoxes` | JSON 数组字符串 | `[]` | 记忆盒子清单（含空盒）。**重命名走原位替换**，手工追加会让盒名顺序错乱 |
| `defaultBoxName` | 字符串 | 空 → 显示「默认盒子」 | 只改默认盒的**显示名**（`box=''` 是存储值，不变） |
| `insight_workspace_root` | 绝对路径字符串 | 空 → `我的文档\Workbench\洞察` | **对象分析产物的落盘根**（`<root>/<对象code>/<时间戳>/`）。默认目录名仍是 `洞察`（更名没动磁盘结构）。改它只影响**后续**运行，已产出的目录不会被搬动；填相对路径/指向文件/无法创建时接口返回 400。清空即回默认 |
| `vault_session_token` | **Fernet 密文** | 空 = 未登录云端 | 云端账号库会话令牌。⚠️ 手改成明文会让 `session_token()` 解密失败并当作"没登录"；不要在这里写任何原文 |
| `vault_profile` | JSON 字符串（非凭据） | `{}` | 显示名/邮箱/头像；**不是凭据**，可读 |
| `vault_base_url` | URL 字符串 | 空 → `AKSO_VAULT_URL` 或默认 443 | 上一次登录用的云端地址；改它等于换账号库 |

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

### 4.1 自动登录节奏门控（已下线）

2026-10-19：内置浏览器 / 自动登录下线，`services/autologin.py` 已删除。
原五重门参数不再生效；账号连通性验证走 `accounts_login`（httpx 直连）。

### 4.2 `browser_pool`（已下线）

2026-10-19：`browser_pool` 已删除；会话自愈、CDP 开户、Monitor drain 等参数均不再存在。

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
| 账号中心轮询 | `static/js/accounts.js` | 3 s | 版本角标从 `/api/health` 读 |
| 主窗尺寸 | `src-tauri/src/main.rs` | 启动页 `560×420`，就绪后 `1440×920` | WebView2 |
| 服务就绪等待 | `src-tauri/src/main.rs` `wait_health` | 40 s，每 300ms 探一次 `/api/health` | 超时在启动页显示日志路径 |

## 6. 第四层：数据目录文件（文件级开关）

根目录：`%APPDATA%\Workbench`（Windows）或 `WORKBENCH_DATA`。

| 路径 | 谁写 | 谁读 | 能否手改 |
|---|---|---|---|
| `workbench.db` (+ `-wal` / `-shm`) | 服务端 | 服务端 | ⚠️ 手改需关闭应用；WAL 模式下直接拷 `.db` 可能不含最新事务 |
| ~~`shell-state.json`~~ | Electron 旧壳 | — | 2026-10-19 起不再产生；旧残留可删 |
| ~~`runtime/desktop-ports.json`~~ | Electron 旧壳 | — | 2026-10-19 起不再产生；Tauri 动态端口由 Rust 直接下发，不落文件 |
| ~~`proxy.txt`~~ | Electron 旧壳 | — | 2026-10-19 起不再读取；系统代理由 Tauri/WebView2 使用 |
| `logs/workbench-<日期>.jsonl` | 服务端（**全部运行日志**） | 人 + 工具 | 只读。**主日志**：启动横幅 / 路由注册 / 工具自检 / 工具调用（带 `auditId`）/ 请求行 / 异常栈。按天 + 按体积双滚动，保留 14 份。读口是 `GET /api/logs/tail`（[API.md](API.md) §13） |
| `logs/tauri-sidecar.log` | **Tauri 壳**（sidecar 的原始 stdout/stderr） | 人 | 只读。**兜底通道**：Python 日志系统**装配之前**的失败（解释器引导、`import` 期异常）只走 stderr，只有这里抓得到。 |
| `server.log` | 服务端（**仅打包态**） | 人 | 只读。**启动面包屑，不是日志**：一行（版本/pid/URL），用裸 `open()` 写，因此**在日志系统自身失效时仍然有效** |
| ~~`.updaterId`~~ | electron-updater 旧版 | — | 2026-10-19 起不再使用；旧残留可删 |
| `runtime/insight/<job_id>/` | 洞察 | `routes_insight` 产物接口 | 可删（历史产物） |
| ~~`runtime/browser-states/<account_id>.json`~~ | 旧版 `browser_pool` | — | 2026-10-19 起不再产生；旧残留可删 |
| ~~`runtime/monitor/<acc8>-<stamp>/`~~ | 旧版 Monitor | 人 | 2026-10-19 起不再产生；旧残留可查看/删 |
| `runtime/egmp-tokens/token-<env_id>.json` | `egmp.client` | 同上 | ✅ 删 = 强制重新登录换取 token（55 分钟 TTL） |
| **`mcp.json`** | **用户** | `mcp_registry`（启动后台 + 「重新连接」） | ✅ **给用户的开关**：`{"mcpServers": {…}}`，形状见 MCP.md。⚠️ 可含 server 的 webhook/token ⇒ 已列入 `SENSITIVE_PATH_PARTS`，`read_local_file` 不会把它交给模型；改完必须点「重新连接」才生效 |
| **`sandbox-workspace.json`** | **沙箱页**（`PUT /api/sandbox/workspace`） | `sandbox.layout`（每次执行读取） | ✅ **给用户的开关**：`{root,mode,allowNetwork,allowSubprocess,updatedAt}`。默认根 `%APPDATA%\Workbench\sandbox`、`mode=read-write`、联网/派生进程关闭。⚠️ **手改也要重启/重进沙箱页**；`mode=read-only` 拒绝声明会写的策略，且 Windows 上 `strict-readonly` 会附加 WRITE_RESTRICTED 受限令牌（partial；workspace-write 仍无内核级隔离） |
| ~~`Partitions/` / `Cache/` / `Code Cache/` / `Local Storage/` …~~ | Electron 旧版 | — | 2026-10-19 起不再产生；旧残留可删，**不会**丢账号（DB 凭据仍在） |
| `workbench.backup-YYYYMMDD-HHMMSS.db` | `tools/db_maintenance.py --backup` | 人 | ✅ 归档对象；实测有 85.7 MB 的历史备份 |

## 7. 「我想改 X，该动哪里」速查

| 想改 | 动哪里 | 注意 |
|---|---|---|
| 监听端口 | `WORKBENCH_PORT` + `main.js` 的 `SERVER_PORT` | 两处必须同步（端口本身固定，见 §1） |
| 换模型 | 设置页（仅两个选项）或 DB `settings.model` | UI 无自定义输入框 |
| 加一个新配置项 | 优先放 `settings` 表（`services/settings.py` 集中建模）；环境变量只用于**进程级**旋钮 | 加表列要新增迁移（[SCHEMA.md](SCHEMA.md) §6） |
| 让更新包走代理 | 系统代理 / 内部源直连 | 当前无自动更新界面；手动下载安装包 |
| 接一个外部 MCP server | `%APPDATA%\Workbench\mcp.json` 写 `mcpServers`，设置页点「重新连接」 | 默认 risk=write；命令只从配置来，模型不能指定 |
| 改沙箱工作区 / 权限 | 沙箱页「工作区与权限」或 `sandbox-workspace.json` | 只读模式只允许 `strict-readonly`；不是内核级只读 |
| 让某盒子不出现在账号中心默认视图 | `POST /api/accounts/boxes/disable` | 只影响默认视图，不影响账号管理 |
| ~~清空某账号的免密登录态~~ | — | 2026-10-19 起内置浏览器下线，该能力不存在 |
| ~~调整自动登录节奏~~ | — | 2026-10-19 起自动登录下线，该能力不存在 |
