# 环境坑清单（PITFALLS）

> **性质**：真机事故攒下来的机构记忆。原文明确写着「这些是**只在真机复现过**的教训，**很多无法从代码反推**」——
> 因此本文件按 **只增不改** 维护（同 ADR），新增坑请**追加编号**，不要重排或删除既有条目。
>
> **来源**：本文件是 `AGENT.md` §5 的**唯一留存处**。`AGENT.md` 已于 2026-09-13 因整体滞后被删除
> （删除原因与遗留引用处理见 [CODEBASE_OVERVIEW.md](CODEBASE_OVERVIEW.md)）；其 §2 代码地图 / §3 红线 /
> §7 验收基线已分别由 [CODEBASE_OVERVIEW.md](CODEBASE_OVERVIEW.md) 与 [CONTRIBUTING.md](../CONTRIBUTING.md) §7 承接。
> 原文可用 `git show <删除前提交>:AGENT.md` 取回。
>
> **复核状态**：`✅` = 2026-09-13 本轮对码复核，仍成立；`⚠️` = 仍成立但有新增变量，见备注；`—` = 本轮未复核，按原文保留。

| # | 复核 | 坑 | 处置 |
|---|---|---|---|
| 1 | ✅ | Playwright sync 单线程单实例 | 所有浏览器操作必须走 `BrowserPool` 专职线程队列；**测试勿另起 chromium 夹具**（见 [ADR-0006](adr/0006-browser-pool-single-thread.md)） |
| 2 | ✅ | **事件回调内禁止连接调用**（`response.text()` / `page.evaluate` 重入 = worker 死锁） | 事件只入队，worker 作业间隙统一 `drain()`（0.2.2 严重修复） |
| 3 | ✅ | **CDP 挂接三律** | `remote-debugging-port` 必须 app ready **前**设置；**先 `:18767` 开户再 connect**（Electron 33 怪癖）；Playwright 实例由 `_CdpState` 单一持有 |
| 4 | — | ES 模块严格模式下**重复函数声明 = 整模块加载失败** | 表现为页面停在静态「加载中」（`accounts.js` 的 `isOnline` 事故） |
| 5 | ✅ | PS5.1 脚本 | `.ps1` 必须 UTF-8 **BOM**；不支持 `??`；heredoc `<<` 不可用；中文经命令行参数传 curl 会乱码（写文件 + `--data-binary @file`）。BOM 一项由 `tools/verify_packaging.py` P5 持续守着 |
| 6 | ✅ | pydantic `BaseModel` **默认忽略多余字段** | 请求体必须显式建模（`AccountPatch` 漏 `box` 导致静默失效）。**注意**：这一点在反向也成立——`AccountPatch` 用 `exclude_none=True`，传 `null` = 不修改 |
| 7 | — | `build.ps1` **禁用** `$ErrorActionPreference='Stop'` | PS5.1 会把 git/uv 的 stderr 进度当终止错误 |
| 8 | — | 关窗回收 | 退出走 `taskkill /T /F` 防孤儿 chromium；同线程禁止第二个 `sync_playwright` |
| 9 | ⚠️ | 网络：GitHub / PyPI 间歇抖动 | 重试循环；uv 已配清华镜像。**push 的代理口径必须按实际情况现场判定**：旧经验是「全局代理常失效 → 用 `git -c http.proxy= push`」，但 2026-09-11 实测本机**直连 GitHub 会被重置**（`Recv failure: Connection was reset`），必须**显式走系统代理**：`git -c http.proxy=http://127.0.0.1:7890 push`。两种都失败时先探测 `Get-NetTCPConnection -LocalPort 7890 -State Listen` 与 `Invoke-WebRequest -Proxy` 哪个通道真的通。<br>**2026-09-13 新增**：`build.ps1:71-75` 仍**硬编码直连**（`git -c http.proxy= push`），与上面的实测结论相反；且 `AKSO_PROXY` 环境变量**只对 shell 生效、到不了 egmp 的 Python 路径**（`httpx` 走 `trust_env`），会出现「shell 里代理通、insight 卡死」<br>**2026-09-13 再补（同一次会话内实测到通道会翻转）**：同一天前两次 `git -c http.proxy= push` **直连成功**，第三次却报 `Recv failure: Connection was reset`；改用系统代理时才发现 **7890 已不在监听**（`Get-NetTCPConnection` 计数 0，`HKCU\\...\\Internet Settings` 的 `ProxyEnable=0`），而直连 `Invoke-WebRequest https://github.com` **完全超时**。结论：**「哪个通道通」是随时刻变化的，不是可以固化成脚本的常量**——每次推送前先探测，别把上一次的成功当作这次的依据 |
| 10 | — | 自愈计数 | 成功时**不可清零**（会掩盖已自愈事实）；上限 2 防死循环 |
| 11 | ⚠️ | 桌面壳只开一个实例 | 第二实例端口冲突，**只出窗口不连服务**。<br>**2026-09-14 起行为变了两次，以下是最终状态**：先说协商（被占则顺延）——但**当天就按用户定稿撤回**（[ADR-0012](adr/0012-fixed-ports.md)）：**端口固定，不顺延**，被占就报错退出并说清是谁占的。就绪探测核对 `/api/health` 的 `app` 字段，不再"有人回 200 就认"。<br>**连带结论：现在是结构上的单实例**——活着的实例必然占着 18765，所以"仍然启动"那条路已不存在（死代码已删）。因此 `app.requestSingleInstanceLock()` **不再必要**（端口占用已挡住），ADR-0012 §D-3 明确不做 |
| 12 | — | 扩展更新 `dist` 后 | **必须在扩展卡片点「重新加载」**（Chrome 缓存 SW 脚本；顽固时删档案 `Default/Service Worker/`） |
| 13 | ✅ | 版本号 | 只走 `tools/bump.py` 五写，勿手改单一文件（安装包命名会错位）。**注意**：五写**不含**两个 `package-lock.json`，`desktop/package-lock.json` 因此长期停在 0.2.1 |
| 14 | ✅ | **字段名双语义** | DB 行 snake_case（`env_base_url`）vs `export_backup` 输出 camelCase（`envBaseUrl`）——`routes_extension` 曾读错键 → 快照 host 恒空 → 扩展**静默丢弃全部账号**（0.2.4 主断点） |
| 15 | ✅ | **扩展游标持久 vs 服务端内存 seq** | 指令序号必须跨重启单调递增（已落 `settings.ext_cmd_seq`），否则重启一次指令通道整体哑火且无报错。**残留**：`_next_seq()` 的 DB 写异常被吞（`routes_extension.py:66-67`），失败时 seq 会从 1 重来，本条会复现 |
| 16 | — | **SW 自消息死链** | SW 内 `chrome.runtime.sendMessage` **不投递给自身** `onMessage`——跨模块复用行为请直调函数（`toggleAccountWheel` 已抽至 `account-wheel.ts`） |
| 17 | ✅ | **Fernet 键位规范** | sign-key = `key[:16]`（HMAC）、enc-key = `key[16:32]`（AES-CBC）——写反导致扩展端全员解密失败（0.2.6 断点之一）。已对 `sync.ts:50-66` 复核仍一致 |
| 18 | ✅ | **WebCrypto AES-CBC 自动去 PKCS7 填充** | 解密结果**不可**再按尾字节手工剥离（否则把口令尾字符当填充长度剥掉，0.2.6 断点之二）。`sync.ts:70-72` 保留了这条注释 |
| 19 | — | Playwright 测 MV3 扩展 | `ctx.service_workers` 需先开一个页面才暴露目标；**headless 下 MV3 扩展不加载**（必须 `headless=False` + `--load-extension`） |
| 20 | ⚠️ | **孤儿 uvicorn 占 18765** | 测试脚本异常退出会遗留服务进程 → 新服务绑定失败、HTTP 验证全打到**旧代码**上，表现为「修复无效」。先 `Get-NetTCPConnection -LocalPort 18765` 查占用。<br>**2026-09-14 补充**：这条坑的**后果被扩大了**，因为实测到过完整形态——桌面壳当时跑的是 9/9 的旧打包副本、占着 18765，而**新的壳因为 `waitReady` 只看"有人回 200"就认为自己的服务起来了**，于是主窗打开的是旧实例页面，表现为「菜单里那两个命令明明删了却还在」。修法与自查口径见 [ADR-0011](adr/0011-port-and-session-negotiation.md) §1：**读 `desktop-ports.json`，或打 `/api/health` 看 `app` 是否为 `AksoWorkbench`**——别用"端口有响应"当判据 |
| 21 | ⚠️ | ~~**扩展的桌面地址是编译期常量**~~ | ~~`sync.ts` 硬编码 `http://127.0.0.1:18765`~~ → **2026-09-14 已改为运行时确认身份**（`background/core/desktop-endpoint.ts`：缓存 → 问壳 `/whoami` 并核对 `app` → **回退固定 18765**）。<br>**坑的形态又变了一次**：该模块一度还实现了"按窗口探测控制端口"（因为当时桌面端会顺延）；**顺延已于当天撤回**（[ADR-0012](adr/0012-fixed-ports.md)），探测窗口随之取消，只剩**一次身份确认请求**。<br>**剩下的真问题**：这一步现在不是为了"跟上换端口"，而是为了确认"**18765 上是不是我们的服务端**"——凭据（Fernet 密文 + 原始密钥）会经过这条通道，密钥就在本机库里（见 #23）。验证扩展行为时：① 确认扩展已「重新加载」到当前版本（`verify_extension_version.py` 强制要求端口上的服务是本项目的）；② 打 `GET /api/health` 看 `app` 是否为 `AksoWorkbench` |
| 22 | ✅ | **Electron 不支持 `window.prompt`** | UI 输入一律用原生 `<dialog>`（焦点最稳）——账号中心的「移入盒子」「重命名」即因此改造 |
| 23 | 🆕 | **凭据是密文，但密钥就在同一台机器的同一个库里** | 扩展收到的账号凭据是 Fernet 密文 + 原始密钥，而密钥是本机 DB 的 `settings.fernet_key`。因此**同机同 `%APPDATA%` 的另一个实例能解密**——2026-09-14 复盘 ADR-0011 时确认：**不要把"密文"当成抵御错误路由的防线**，它抵御的是网络窃听，不是本机实例串号。真正的防线是"认准端口上的服务是谁"（`/api/health` 的 `app` 字段 + `/whoami`） |
| 24 | 🆕 | **`app.exit()` 不触发 `before-quit`** | 端口公布文件的清理原先挂在 `before-quit`，而启动失败分支走 `app.exit(0)` → 文件残留成陈旧数据（实测复现）。清理挪到 **`will-quit`**（它对所有退出路径都触发），并核对文件里的 `pid` 才删，避免"新实例已写、旧实例才退出"时误删新实例的文件 |
| 25 | 🆕 | **`dist/` 下两代 PyInstaller 产物同级并存，目录名分不出谁是新构建**（#20 的**复发**，但触发形态不同：这次端口没冲突） | **症状**：用户报「两个已下线的功能（盘点/理解）还在命令下拉里」，而源码 `grep` 干净、打包态 `GET /static/pages/insight.html` 实吐只有 login/spider——**证据与观察直接矛盾**。<br>**根因**：`dist/` 里同时躺着**上一代完整产物** `dist/AksoWorkbench/`（2026-09-09 的 Electron+sidecar 组合，内含 4 选项的旧 `insight.html`）与**本次 sidecar** `dist/AksoServer/`，两者同级、名字都像"当前构建"；用户当时看的窗口来自**本次构建之前就在跑的旧实例**（其资源就是 9/9 那份）。#20 的判据（`/api/health` 的 `app` 字段）在本例**帮不上忙**——旧实例同样回 `app="AksoWorkbench"`，两项都"验明正身"。<br>**处置**：① 判「你看到的是哪一版」**不能靠目录名或印象**，要三件套一起看：进程的 `ExecutablePath`、端口占用者 `Get-NetTCPConnection`、**服务端实吐的字节数**（旧页 2581→4 选项版与新生效版字节数不同）；② 与用户截图**逐字比对**是最快的定性手段——旧页独有的「对象编码（understand/spider 必填）」「inventory 兜底白名单（可选）」两行只存在于 `2cc09bb^`；③ 长期做法：重建前先清掉/改名 `dist/AksoWorkbench/` 这类上一代产物，别让两代产物并列。**注意**：本坑与 #20 同源，凡遇到"改了代码界面没变/删了功能还在"，先怀疑**跑的是旧产物**，而不是先怀疑代码 |
| 26 | 🆕 | **环境 baseUrl 存成「登录页地址」→ AUTH 被拼成 `/login/api/...` → 网关 405** | **症状**：`login` 连通测试报「无法连通」，但同主机根地址其实完全可达。**根因**：用户把浏览器地址栏里的登录页地址整段粘进环境 baseUrl（`https://host/login`），而地址拼接只做 `rstrip("/")` → `.../login/api/openapi/v1.0/Auth`，nginx 直接 **405 Not Allowed**。**判据**（区分「地址错」与「真不通」）：根地址 POST 同名端点返回平台信封 `{"code":500,...}`，带 `/login` 则是 405。**处置**：归一化单点 `egmp/kernel/urls.py::normalize_base_url`（剥 `/login`、丢 query、其余子路径不猜、幂等），写时（`accounts.create_env/update_env`）与读时（client / routes_insight / routes_agent / browser_pool）两侧都过它。**2026-09-14 真机实测**：归一化后两个「通达」账号真实登录成功（796 / 693 ms），修复前均为 405。<br>**同批发现的连带坑**：login 失败时 `proc.log` **根本不存在**——`on_log` 只在登录成功之后才被调用，界面只剩一句无从下手的「无法连通」。已在 `_run_job` 补「命令 / 平台环境」表头与失败行（失败也是现场） |
| 27 | 🆕 | **`appendSwitch` 挪到 `app.whenReady()` 之后就等于没设**（#3 的**复发**，且与「壳下发 cdp 模式」叠成硬故障） | **症状**：壳在跑、控制面 18767 正常，但 **18766 从不 LISTEN**、`http://127.0.0.1:18766/json/version` 拒绝连接。**根因**：`applyCdpSwitch()` 被搬进了 `bootstrapPorts()`，而后者在 `app.whenReady().then(...)` **内部**才被调用——晚于 ready 就完全不生效（0.3.5 里它在模块顶层，注释还写着「必须 ready 前设置」）。**为什么会炸**：同一轮刚让壳首次下发 `WORKBENCH_SESSION_MODE=cdp`，于是 `browser_pool` 走 `connect_over_cdp(18766)` 分支（**无回退**，失败即 `BrowserError`）。**处置**：调用点移回模块加载期（`applyCdpSwitch()` 紧跟函数定义）。**验收**：启动壳后 `Get-NetTCPConnection -LocalPort 18766 -State Listen` 必须有监听——别只看控制面端口活着就以为壳是好的 |
| 28 | 🆕 | **打包态「chromium 未安装」是**体检**误报，不是浏览器没打进去** | **症状**：`/api/modules` 里 `accounts` 恒为 `degraded`，`/api/browser/check` 也报 `chromium 未安装`，而包内 `_internal/ms-playwright/chromium-1234/chrome-win64/chrome.exe` 明明在。**根因**：playwright 的浏览器目录默认取 `%LOCALAPPDATA%\ms-playwright`，打包态那份在 `_MEIPASS` 下；只有 `browser_pool._worker` 会设 `PLAYWRIGHT_BROWSERS_PATH`，而两个体检入口跑在 worker 之前，于是「运行期找得到、体检说没装」。**判据**：把该变量显式指向包内目录后再查，立刻通过并解出包内那个 `chrome.exe`（2026-09-14 打包态实测）。**处置**：抽成单点 `browser_pool.ensure_bundled_browsers_path()`，worker 与两个体检入口共用。**顺带**：`server.spec` 用 `chromium*` 通配收集，会把 ms-playwright 里**历代残留**一并打进包——实测两代并存时安装包 561 MB（其中 685 MB 是无人使用的旧 revision），清掉旧代后回落至 350 MB 量级 |
| 29 | 🆕 | **引用型字段的目标藏在 `referenceObjectId`，`referenceObjectCode` 恒为 null** | **症状**：五步织网的 step1「关联对象」永远是空的——`change_plan__c` 有 5 个 `dataType=15` 引用字段（发起人/发起部门/…），关系边却是 **0** 条；`the_change_lh__c` 同样 0 条，而它明明引用「用户对象」「部门对象」。**根因**：`collect_relations` 只读 `referenceObjectCode`，而真机字段页里这个键**恒为 null**，目标对象在 `referenceObjectId`（GUID）+ `objectRelation.objectName`（连 `objectRelation.objectCode` 也是 null）。**判据**：把字段行整个打出来看键名——`initiator__c` 的 `objectRelation` 里 `objectId/objectName` 有值、`objectCode: null`。<br>**更值得记住的是它为什么一直没被发现**：本仓夹具 `spider-training-mgr.json` 里的引用字段写的就是 `referenceObjectId`（录制时是对的），但**重放测试把 `relationEdges: 0` 写成了黄金值**——缺陷被固化成「预期」，回归测试反而守着错误行为。**处置**：解析按「编码 → 名称 → id」，节点标识用**对象名称**（用户定稿；平台对这类目标常常只给 id + 名称），拿不到编码的作占位节点并记 `unexpandedRelations`。**改黄金值前先问一句：这个数字是推导出来的，还是抄下来的** |
| 30 | 🆕 | **"对象读不到动作"先怀疑环境，别先怀疑代码**——同一个 code 在不同环境里配置天差地别 | **症状**：用户说「对象 `change_plan__c` 的状态『变更申请』有个用户动作 `Initiate_the_review__c`」，而我随手挑的账号查到的 `change_plan__c` 只有 **2 个状态**（草稿/关闭）、0 个动作、0 条工作流——于是顺着"读接口错了"修了一整轮，修的是另一个环境里的空对象。**根因**：库里配了 4 个环境，**同名 code 的对象各自独立**；我按"测试环境"那个账号取数，用户说的是**通达**。<br>**判据**：拿状态数/动作数当指纹——真机 19 个状态、动作带 `code`，与"2 状态 0 动作"一眼可分。**处置**：动手前先跨环境扫一遍（逐账号 `get_object` + 状态表，命中就打印状态清单）；**用户给的具体名字（状态名 / 动作 code / 工作流 code）是最强的定位线索**，先拿它去找"哪个环境有这东西" |
| 31 | 🆕 | **平台没公开的读接口，能从它自己的前端 bundle 里挖出来** | **症状**：`GetActionBar`（写路径文档里的"权威"接口）在通达环境**恒返回空**，于是动作类型/目标状态/发起的工作流全读不到；猜路径又猜不中。**处置**：抓首页 `<script src>`（`/login` 只给 4 个）→ 下载主 bundle（本例 7MB）→ 正则捞 `"/api/..."` 并打印调用上下文，一次就拿到设计器真正在用的接口：`UserAction/GetListByStatusId`（用户动作，带 code）、`EnterAction/GetListByStatusId｜GetById`（进入动作 + 目标对象/状态）、`GetFieldsAndRelationsList`（`targetType=1` 字段 / `2` 关系对象），以及 `belongType` 枚举（1=进入反应 / 2=进入用户动作 / 3=工作流步骤动作…）。bundle 里的 i18n 词典还能直接确认 `behaviorType` 的中文名（**46=修改相关对象生命周期状态**）。**只读抓取**——别拿它当去调写接口的借口 |

---

## 维护规则

1. **只增不改**：新增坑追加编号；既有条目的**事实**若已失效，改「复核」列为 `⚠️` 并在「处置」列补一句现状说明，**不要删行**——坑的价值在于「曾经踩过」。
2. 每条尽量写清：**症状**（怎么表现）→ **根因** → **处置**（下次怎么绕过）。原文多数已具备，新增请照此格式。
3. 只在真机复现过的才进来。从代码能直接读出来的约束属于 `docs/CODEBASE_OVERVIEW.md` 的「约定」或 ADR，不占坑位。
4. 复核建议节奏：每次大版本发布前把 `—` 的条目过一遍，能确认的改成 `✅`。
