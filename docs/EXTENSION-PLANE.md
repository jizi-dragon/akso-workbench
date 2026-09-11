# 扩展执行面（EXTENSION-PLANE）

> **本文档的读者**：要改 `extensions/quick-login/` 的人、要排查「点了账号没反应 / 登不上 / 串号」的人。
> **本文档是扩展侧的权威说明**。上游 quick-login 自带的 7 份文档已归档到
> [`extensions/quick-login/docs/archive/`](../extensions/quick-login/docs/archive/README.md)（只作追溯）。
>
> 变更频率较高（O(改动)）：每次改动扩展行为、协议或私有改造清单，请同步更新本文档与
> [`extensions/quick-login/CHANGELOG.md`](../extensions/quick-login/CHANGELOG.md)。
>
> 相关文档：[API.md](API.md)（`/extension/*` 端点契约）、[CONFIG.md](CONFIG.md)（端口与 settings 键）、
> [ADR-0004](adr/0004-extension-as-execution-plane.md)（为什么扩展只做执行面）。

## 一、定位与职责边界

扩展跑在**用户自己的 Chrome** 里（不是应用内置的 Chromium），职责被严格收敛为**执行面**：

| 归属 | 内容 |
|---|---|
| **扩展负责** | 六平面隔离（同一浏览器窗口内多账号不串号）、会话切换（`par.open`）、登录表单自动填表、轮盘呼出（`wheel.toggle`）、状态上报 |
| **桌面负责** | 账号/盒子/站点的增删改（唯一真源是 SQLite）、凭据 Fernet 加密存储、托管浏览器与监听录制、界面与入口 |

一句话：**数据的增删改归桌面，扩展只在 `sync.ts` 里对账并执行动作**。这条分工是 0.2.23 的架构收敛决定
（净删 2,849 行），它同时解决了「两套账号数据谁为准」的问题。

浏览器分配政策（用户定稿，见 [ADR-0002](adr/0002-browser-allocation-policy.md)）：

- **快捷登录 / 打开账号** → 用户 Chrome（扩展执行面）——要的是「以该身份操作的真实效果」；
- **监听 / 自动化 / 录制** → 应用内置 Chromium（`browser_pool` CDP 模式）——要的是可控、不污染用户浏览器。

## 二、六平面隔离（核心机制）

绑定到标签页后，后台为每个 tab 安装至多三条 DNR session 规则（id 区间互不重叠，见 `core/tab-rules.ts`）。
六个平面各自切断一份**同 origin 共享资源**——平台的登录态正是这些资源的叠加，不切就会「先开谁，谁的身份分发给别人」。

| # | 平面 | 机制 | 实现位置 |
|---|---|---|---|
| 1 | 存储 | `localStorage` 重定向到 `__ql_ns_<accountId>__` 命名空间；`document.cookie` 虚拟化为账号「Cookie 袋」（`__ql_cookies__`）；bind 时携带 token/身份/指纹快照静默直灌 | `content/shield-main.ts` |
| 2 | AUTH | DNR 按 tabId 强制改写 `Authorization: Bearer <token>`（xhr / websocket / sub_frame，host + 父域） | `core/tab-rules.ts` |
| 3 | COOKIE | 出站 `Cookie` 头**按账号回放**（登录时点经 `chrome.cookies` 采集全量快照含 HttpOnly，身份类键过滤；空快照回退 remove） | `core/tab-rules.ts` + `core/parallel-session.ts` |
| 4 | CACHE | 同源 GET 请求追加 `_qlck=t<tabId>`，把全 profile 共享的 HTTP 缓存按标签硬分区（**页面层实现**——Chrome DNR 无 `urlTransform`） | `content/shield-main.ts` |
| 5 | SW/Cache | 拦截站点 `serviceWorker.register` + 注销既有注册；`CacheStorage.prototype` 按账号命名空间键控、`match` 一律 miss | `content/shield-main.ts` |
| 6 | IndexedDB | `indexedDB.open/deleteDatabase/databases()` 按账号前缀化。**平台把 `isAdmin` 标志与全量菜单树缓存在 origin 级共享 IDB（库 `DBFetch`，键=URL 哈希无账号维度）**，这是四象限串号的直接载体 | `content/shield-main.ts` |

### 配套机制

- **Token 双通道捕获**：命名空间 `__auth_token__` 写事件上报（主）+ 出站 fetch/XHR `Authorization` 嗅探（备）；
  漂移于 `chrome.storage.session`（会话级，SW 冷启可恢复）。
- **首捕即触发 Cookie 快照**：`chrome.cookies` 全量采集 → 过滤身份键（`IDENTITY_COOKIE_BLACKLIST`：
  `__auth_token__` / `__auth_user__` / `__device_fp__`）→ 存账号档案 → 以其回放值重建 COOKIE 规则。
- **`main_frame` 导航刻意不改写**（保护静态资源与 SSO 跳转语义）。
  ⚠️ 例外：**下载**链路需要 AUTH 覆盖全资源类型——`<a download>` 在 DNR 里常归型为 `other`，
  低代码平台的导出接口是主框架下载导航（上游 3.13.1/3.13.2 的实测教训）。
- **`urlHostOf()` 端口口径**：归属判定、停用名单、页签收编一律用带端口的 `new URL(u).host`；
  规则覆盖（`requestDomains`）与 Cookie 作用域**故意**无端口——**DNR 无法表达端口**，
  所以「同主机跨端口的网络平面隔离」是结构性限制（真要做得换 `urlFilter`）。

## 三、与上游的差异（本项目私有改造）

来源：上游 `v3.13.2`（2026-09-10 同步，36 文件前移）+ 本项目改造。
**完整改造史与「同步上游时要重新摘除的清单」见 [CHANGELOG](../extensions/quick-login/CHANGELOG.md)**，本节只列结论性差异：

| 维度 | 上游 | 本项目 |
|---|---|---|
| 定位 | 自带并行管理页 `ui/parallel/`，用户直接在扩展里增删账号 | **纯执行面**；`ui/parallel/` 源码已删（0.2.13/0.2.22），账号数据归桌面 |
| 版本号 | 独立递增（`3.13.2`） | 与桌面同号（`0.3.4`），真源 = `manifest.json`，`tools/bump.py` 五写同步 |
| 版本常量 | `constants.ts` 的 `EXT_VERSION`「三处必须一致」 | **`EXT_VERSION` 已删**；`extVersion()` 读 `chrome.runtime.getManifest().version` |
| 站点权限 | 逐站点授权链路 + `optional_host_permissions` | **声明式全站权限** `host_permissions: ["<all_urls>"]`（0.2.21）；授权 UI 下线（0.2.22），`site-auth.ts` 保留为休眠源码 |
| 页面监视 | Page Monitor + 最近页面轮盘（Alt+W） | **整块撤销**（0.2.22）；页签标题仍是页签名 |
| 桌面联动 | 无 | 新增 `background/sync.ts` 桌面同步桥（快照 + 指令面 + 状态面） |
| 快捷键 | 仅扩展 `quick-wheel` | 扩展 `quick-wheel`（Ctrl+Shift+Q）+ 桌面壳全局 `Alt+Q`；**桌面运行时 Alt+Q 优先**，扩展快捷键被压制 |
| 会话模型 | `session-manager.ts` / `account-registry.ts` / `navigation.ts` + `session.*` 协议 | **已删**（0.2.23），净删 2,849 行 |

## 四、与桌面的协议（数据面 / 指令面 / 状态面）

桌面地址是**编译期常量** `http://127.0.0.1:18765`（`sync.ts:20`）。这有一个运维后果：
**验证扩展相关行为时必须确保 18765 上是本项目服务**（见 [AGENT.md](../AGENT.md) 的环境坑）。

### 4.1 数据面（每 2s tick，`GET /extension/snapshot`）

```
桌面 SQLite ──export_backup()──> {format, snapshotId, desktopVersion, fernetKey,
                                  accounts[{desktopId, host, scheme, tabName,
                                            username, passwordEnc, box}],
                                  boxes{remembered, defaultName, disabled}}
                                        │
                      扩展 getJson('/extension/snapshot') 每 2s
                                        │
   snapshotId（内容哈希）未变 → 整批跳过（幂等）
   passwordEnc + fernetKey → WebCrypto Fernet 解密 → 明文仅在内存
                                        │
                    parallelStore 增删改 + 本地 AES-GCM 加密落盘
                    desktopId ↔ 扩展 accountId 映射存 chrome.storage.local['akso:acctMap']
```

**四个必须知道的护栏**（都在 `sync.ts`，改它之前先读）：

1. **`snapshotId` 幂等**：内容哈希未变则整批跳过——否则每 2s 全量重写本地档案。
2. **空快照护栏**：`if ((snap.accounts ?? []).length > 0)` 才执行删除同步。
   没有它，桌面瞬时返回空/半量快照会把扩展本地账号**全量删光**（曾发生过）。
3. **用户名变更走删除重建**：`parallelStore` 无「更新用户名」接口；用户名或密码变更时，
   与原记录比较后决定原位更新（box/tabName/凭据）还是删除重建。
4. **凭据解密失败必须留痕**：`console.warn` 后跳过该账号。静默跳过会让「密钥轮换后全员停更」无任何迹象
   （Fernet 键位写反那次就是靠这条日志定位的）。

### 4.2 指令面（长轮询，`GET /extension/commands?after=N&wait=15`）

```
桌面点轮盘/卡片
  → POST /extension/commands {type: par.open|wheel.toggle, payload}
  → 服务端 _commands 入队 + 递增 seq（落 settings.ext_cmd_seq，跨重启单调）
  → _cond.notify_all() 唤醒正在长轮询的扩展
  → 扩展取出 → parallelSession.open(extId) / toggleAccountWheel()
  → 持久化游标 chrome.storage.local['akso:cmdCursor'] + POST /extension/ack {seqs}
```

- **为什么是长轮询**：原 2s 轮询让「桌面点击 → 浏览器打开」有 0~2s 的量化延迟（实测均值 964ms）。
  长轮询把它压到一次本机回环（实测均值 **20ms**，约 50 倍）。见 [ADR-0008](adr/0008-update-channel-and-proxy.md) 附近的延迟优化记录。
- **seq 必须跨服务端重启单调递增**：扩展游标持久在 `chrome.storage.local`，若服务端重启后 seq 从 1 重来，
  所有新指令会因 `seq > after` 过滤被**永久吞掉**（0.2.4 实锤）。所以序号落 settings 表（`ext_cmd_seq`）。
- **扩展侧四条护栏**（`sync.ts`）：
  `consuming` 互斥（防同一 `par.open` 开两个页签）、`streaming` 幂等（`chrome.alarms` 每 0.5min 触发都会调
  `commandStream()`）、流 >25s 未取回由 tick 兜底、`getJson` 返回 `null` 时退避 1.5s（防旧版服务端热循环）。
- **SW 复活通道**：MV3 SW 空闲约 30s 被杀，`setInterval` 随之消失 → `chrome.alarms`（0.5min）拉起并跑一次同步。

### 4.3 状态面（每 ~6s，`POST /extension/state`）

上报 `{items:[{desktopId, tabs, hasToken}], extVersion, desktopVersion}`，
桌面存内存（TTL 60s）供账号中心四态徽标；`extVersion` **无条件上报**（账号映射为空也照发），
否则「扩展是旧版」这一事实永远传不到桌面端。

### 4.4 版本一致性判定

桌面安装包与扩展**同号发布**（`bump.py` 把项目版本写进 `manifest.json`）。
`GET /extension/health` 返回 `extVersion` / `desktopVersion` / `extStale`；
`extStale=true` 时账号中心显示「浏览器扩展版本过旧」→ 引导用户在扩展卡片点**重新加载**。

⚠️ 安装包升级会覆盖 `resources/extension`，但 **Chrome 只在「重新加载」后才用新代码**——
升级后长期跑旧扩展且毫无迹象，是这里最容易出现的问题。**刻意不做自动 `chrome.runtime.reload()`**：
重载会掐断进行中的自动登录。

## 五、私有改造史（按版本）

完整版见 [extensions/quick-login/CHANGELOG.md](../extensions/quick-login/CHANGELOG.md)。高频踩坑条目摘要：

| 版本 | 改动 | 为什么值得记住 |
|---|---|---|
| 0.3.3 | 扩展版本无条件上报 | `if (!items.length) return` 让旧版检测永远失效 |
| 0.2.24 | 指令面改长轮询 | 延迟 964ms → 20ms；附带四条防热循环/防重复消费护栏 |
| 0.2.23 | 收敛为执行面，净删 2,849 行 | 删 `ui/parallel` + 旧会话模型；保留 `site-auth.ts` 为休眠源码 |
| 0.2.22 | 撤销 Page Monitor；版本真源改 manifest；端口口径修复 | 上游同步会带回来，需再摘一次 |
| 0.2.21 | 声明式全站权限 | 逐站点授权链路整套退役 |
| 0.2.6 | Fernet 键位规范 + AES-CBC 自动去填充 | 两个断点都表现为「扩展端静默全员失效」 |
| 0.2.4 | 字段名双语义（`env_base_url` vs `envBaseUrl`）+ 游标持久化 | host 恒空 → 扩展静默丢弃全部账号 |

## 六、构建与装载

```bash
cd extensions/quick-login
npm install        # 首次
npm run typecheck  # tsc --noEmit —— 零错误才算通过
npm run build      # esbuild → dist/（build.mjs 先 rmSync 清空 dist 再重建）
```

装载：`chrome://extensions` → 开发者模式 → 「加载已解压的扩展程序」→ 选
`extensions/quick-login/dist/`（**注意是工作区根下的 `dist/`，不是 `packages/extension/dist/`**）。

**更新 dist 后必须在扩展卡片点「重新加载」**（Chrome 缓存扩展 SW 脚本，实测会长期复用旧脚本，
造成「新代码从未生效」的假象；顽固时删档案 `Default/Service Worker/` 后重开）。

## 七、验证基线

扩展端**没有单元测试框架**，回归依赖以下工具（改扩展必跑前三项）：

| 工具 | 验什么 | 基线 |
|---|---|---|
| `tools/verify_extension_boot.py` | 启动冒烟（隔离 profile 装载 dist） | 7/7：SW 启动未崩 / 命令清单仅 `quick-wheel` / 无 `ql:recentPages` / 无 `ql:pageNames` / `akso:acctMap` 数据面同步成功 |
| `tools/verify_extension_isolation.py` | Cookie 袋隔离 + 多 iframe 自动填表 | 8/8（含 0.2.23 两个缺陷的回归点） |
| `tools/verify_host_logic.mjs` | host/端口口径（`host.ts` 五函数） | 22/22 |
| `tools/acceptance_extension_e2e.py` | 端到端：数据面同步 / `par.open` 开页 / 自动登录离开 `/login` | E2E_PASS（A1 2/2、A2 <1s、A3 进 `/web`） |
| `tools/verify_extension_version.py` | 真实 Chrome 验「扩展版本上报 → `extStale`」 | 6/6；**必须在 18765 且该端口空闲**（扩展的桌面地址是编译期常量） |
| `tools/verify_extension_sync.mjs` | 桌面侧蓝图（`/extension/*` 无 pytest 覆盖，此脚本就是它的回归测试） | SIMULATION_OK / SEQ_PERSIST_OK / SYNCDISABLED_OK |
| `tools/verify_wheel_page.py` | 轮盘页渲染（SVG 扇区/Hub/动画/零 JS 错误） | — |

冒烟要点：**必须有头模式** `+ --load-extension=dist`（headless 下 MV3 扩展不加载，`service_workers` 为空）。

## 八、安全边界与已知风险

### 8.1 平台侧（不是本项目的缺陷，但要知道）

1. **主动提权方向无法在客户端根治**：把普通用户抬成管理员需「接管服务端应答」，
   客户端篡改 IDB → 白屏（无回退渲染路径，已实证）。
   权限真边界在服务端（管理侧读接口未过滤 + 单设备登录未启用）——应向平台方反馈。
2. **Web Worker 内打开的 IndexedDB** 不受主世界补丁（已知残余，待观测）。
3. Cookie 快照仅在登录时点采集一次；服务端若在会话中轮转会话标识，该账号需重新登录刷新
   （JWT 主体不受影响）。

> 安全边界声明：本扩展只保证**客户端呈现层**的账号分离。若平台服务端以某凭证返回他人数据，
> 属平台越权缺陷。

### 8.2 本项目侧（待排期，见 [AGENT.md](../AGENT.md) §4 已知问题）

1. **明文凭据 60s 投递窗口**：`getPendingAutoLogin(tabId)` **读后不删**；
   窗口内任何能读 `chrome.storage.session` 的上下文都能拿到明文口令（上游 3.13.2 仍未修）。
2. **同步通道无认证**：`127.0.0.1:18765` 的 `/extension/*` 无任何凭据校验——
   本机任意进程都能读快照（含 Fernet 密文与密钥）。
3. **`host_permissions: ["<all_urls>"]`**：声明的权限面比功能所需更宽（换来的是免逐站点授权）。
4. **Alt+Q 双绑**：桌面全局热键与扩展 `quick-wheel` 都是 Alt+Q，桌面运行时扩展侧被压制；
   扩展命令的 `suggested_key` 与实际生效键位不一致（用户可在 `chrome://extensions/shortcuts` 改）。

## 九、关键文件索引

| 文件 | 职责 |
|---|---|
| `packages/extension/manifest.json` | MV3 清单：权限、4 个内容脚本（含 MAIN world）、`quick-wheel` 命令、`<all_urls>` |
| `src/background/sync.ts` | **桌面同步桥**：数据面 tick / 指令长轮询 / 状态上报 + Fernet WebCrypto 解密 |
| `src/background/service-worker.ts` | 消息分发（`par.*` / `ql.diag` / `wheel.toggle`）、轮盘触发链、角标诊断 |
| `src/background/core/parallel-session.ts` | 绑定表 tabId↔accountId、token 双通道捕获、Cookie 快照与回放、种子下发、授权健康门控 |
| `src/background/core/tab-rules.ts` | DNR 规则族（AUTH 改写 + COOKIE 回放/剥离）：构建/换值/冷启恢复/孤儿清理；**逐条安装降级** |
| `src/background/core/parallel-store.ts` | 账号档案 CRUD（IndexedDB v2 `accounts`） |
| `src/background/core/credentials.ts` | 凭据 AES-GCM 加密（设备绑定种子） |
| `src/background/core/pending-login.ts` | 待自动登录凭证（`sb:pendingAutoLogins:<tabId>`，会话级） |
| `src/background/core/host.ts` | host/端口口径五函数（`urlHostOf` 等）单一真源 |
| `src/background/account-wheel.ts` | `toggleAccountWheel()` 抽取（SW 自消息是死链，跨模块复用须直调函数） |
| `src/content/shield-main.ts` | MAIN world 壳：六平面中的 1/4/5/6 + 种子直灌 + 写入上报 |
| `src/content/shield-bridge.ts` | ISOLATED 桥：`window.postMessage` ↔ `chrome.runtime` |
| `src/content/auto-login.ts` | 登录表单自动填表（顶层直填同源 srcdoc iframe 密码框） |
| `src/content/title-hook.ts` + `src/background/tabs/tab-title.ts` | 页签标题维持为页签名 |
| `src/content/wheel-overlay.ts` + `src/ui/wheel/*` | 账号轮盘双形态（页面内 Shadow DOM 浮层 / 独立小窗） |
| `src/ui/popup/*` | 弹窗：导出诊断（品牌头右上）、版本号（页脚右下） |
| `src/shared/constants.ts` | 协议键单一真源；**MAIN world 会 import → 顶层禁止求值扩展 API** |
