# akso-workbench-ts

一个 DSH 插件，把「eGMP 平台的域知识」做成 agent 可调用的工具和可点的页面：

- **`plugin/`**：插件本体（host 半边：路由 + 工具 + 审批 + 会话 + 运行日志；浏览器半边：账号中心 / 配置工具 / 运行报告 / 对象分析 / 运行日志）。
- **`capture/`**：接口监听工具（CDP 抓真实流量，用来扩展能力目录）；**这是独立工具，不属于插件运行时**。
- **`docs/`**：现行规范、外部资料、历史台账 —— 先看 [`docs/README.md`](docs/README.md)。

不是重写整个 workbench：DSH 已经提供的（agent 循环、沙箱、技能、MCP、Web GUI）一律不要，只保留"只有我们知道"的平台语义。

## 目录一览

| 路径 | 是什么 |
|---|---|
| `plugin/src/index.ts` | host 半边：HTTP 路由、快照、会话、工具注册、运行日志接入 |
| `plugin/src/config/` | 能力目录、计划引擎、审批门、会话/换票 |
| `plugin/src/client/` | 浏览器半边：页面、账号条、配置工具、对象分析、运行日志 |
| `plugin/test/` | 9 个测试文件（host / journal / analysis-history / 文档校验等） |
| `capture/` | 监听工具：`capture/bin/akw-capture.mjs` + CDP 实现 + 5 个测试 |
| `docs/knowledge/` | 现行规范（使用时先看这里） |
| `docs/archive/` | 带日期的实测台账（历史证据，不是规范） |

## 保留 / 丢弃（早期拆分清单）

| workbench 原实现 | 处理 | 理由 |
|---|---|---|
| `harness/` 循环、缓存、上下文 2,262 行 | **丢弃** | DSH 的 agent 循环与上下文管理 |
| `sandbox/` 1,844 行 | **丢弃** | DSH 三档沙箱（read-only / workspace-write / danger-full-access） |
| `skills/` 1,775 行 | **丢弃** | DSH 的 skill（SKILL.md + 脚本） |
| MCP 客户端 1,032 行 | **丢弃** | DSH 内置 MCP 客户端 |
| `static/` 11,041 行 + 对话 UI | **丢弃** | DSH 的 Web GUI / client-ui 插件 |
| `approvals.py` 772 行 | **降到一个引擎门 + 一个弹窗** | 计划指纹落在 `config-approvals/`，对话里点「同意」才写 |
| `services/egmp/**` 3,851 行 | **移植进 `plugin/src/`** | 平台语义只有我们知道 |
| `vault.py` / `accounts.py` | **改为调 akso-cloud** | 不再两处实现同一套账号库 |

## 当前进度

| 模块 | 状态 | 判据 |
|---|---|---|
| 账号门 + 换票（akso-cloud 代登录） | ✅ | `plugin/test/host.test.ts`（未登录 401 / 非池内账号拒绝） |
| 计划文件 + apply（写对象） | ✅ | `plugin/test/host.test.ts`（审批门 / 目标绑定 / 回读复验） |
| DSH 工具注册（`withToolUid` + `ctx.tools`） | ✅ | 四个工具：账号快照 / 配置计划 / 配置执行 / 对象分析（时序见 `docs/knowledge/agent调用时序.md`） |
| 对象分析历史（成功与失败都落盘） | ✅ | `plugin/test/analysis-history.test.ts` |
| 运行日志（`plugin/src/journal.ts` + 页面「运行日志」） | ✅ | `plugin/test/journal.test.ts`；下载 7/30 天带回用户现场 |
| 接口监听工具 `capture/` | ✅ | `capture/test/`（CDP 连接、录制、打码、落盘） |

## 跑

```powershell
# 仓库根：只校验 capture + 文档工具
npm install
npm run typecheck          # tsc（capture/**）
npm test                   # node --test capture/test

# 插件（主要工作量在这里）
cd plugin
npm install
npm run typecheck
npm test                   # 9 个测试文件
npm run build              # → lib/index.js + lib/client.js
```

一键全量（根目录）：`npm run check` —— 根 typecheck + capture 测试 + 插件 typecheck + 插件测试 + 插件构建。

零运行时依赖：HTTP 用全局 `fetch`；测试用 `node --test`（Node 24 直接跑 `.ts`）。

## 三条从 Python 版继承的"静默失败"防线

1. **信封校验**：平台回 HTTP 200 但 `code != 0` 仍是失败 —— 漏掉它，"失败"会显示成"没有数据"。
2. **分页硬上限**：`hasNext` 恒 true 的坏代理会无限请求；到 `MAX_PAGES` **抛错**，不返回半截。
3. **会话按 `(baseUrl, 账号)` 绑定**：不绑定会把 A 环境的令牌发到 B 环境（偶发 401）。

---

## DSH 客户端插件（`plugin/`）

两个座位，**不是**改 DSH 源码：

| 需求 | 座位 | 位置证据 |
|---|---|---|
| 左栏一个 `akso-workbench` 菜单 + 中栏页面 | `sidebar.panellist`（id）+ `main`（key），两处 id 必须同值 | `sidebar.panellist` 是「插件 / 自动化任务 / 技能中心 / 更多」那一组 |
| 输入框**下方**、**上下文比例右侧**的账号条 | `conversation.composer.dock`（order 20） | 这一行是 `renderSlot('conversation.composer.dock')` → `ContextMeter`；slot 包裹层是 `display:contents`，所以账号条上的 `order:99; margin-left:auto` 直接作用在**这一行**上，把它排到 meter 右侧、贴行右边缘 |

> ★ 规范模板写的是 `conversation.input.dock`（卡片**上方**）。用户要的是"输入框下方、在上下文比例右侧"，
> 而实测用量（StatsPills / ContextMeter）在 composer.dock 那一行 ⇒ 这里**故意**用 composer.dock，
> 不是笔误（规范 §8 坑 4 说的正是这两个座位一上一下）。

**五个模块**（左栏一级导航）：账号中心 / 配置工具 / 运行报告 / 对象分析 / 运行日志。
账号中心内是 5 个 tab：并行账号 / 配置池 / 添加账号 / 站点管理 / 盒子管理。

**版式**：顶部品牌条（品牌标 + 标题 + 统计芯片 + 同步状态 + 操作）→ 下划线式 tab → 内容区
（卡片网格 / 表单 / 行式列表）→ 页内滚动。颜色全部走 DSH 主题 token，深/浅色跟随宿主。

**交互口径**：

- **只能加、不能删**：账号/站点/盒子都不提供删除，host 侧也**没有**对应路由（有单测钉着"删除端点不存在"）。
- **卡片优先显示页签名**：标题取 `tabName`（空则回落平台账号），副标题是站点；事实区是「平台账号 / 盒子」。
- 「设为当前」改叫 **「连接agent」**；已连接的卡片显示「已连接」徽章 + 「已连接给 agent」，不再有操作按钮。
- **底部账号条只显示"已连接"的那一个账号**，内容是 `页签名 · 站点名`；**已连接时不出现 +**。
  未连接时只有一个按钮：有账号=「＋ 连接 Agent」，没账号=「添加账号」。
- **点已连接的胶囊 → 弹层**：列出全部账号（当前项带 ✓）可换，底部「清除连接」可断开；点外面或 Esc 关闭。
- **边框**：卡片/输入/行用 `l3`（12%）、按钮 `l4`（16%）；已连接卡片再叠一圈 1px 品牌色描边。
- **卡片紧凑**：网格最小列宽 276px、卡片内边距 11/12px、头像 27px；「连接agent」按钮 24px 高。
- **按盒子筛选**：并行账号页在卡片网格上方给一排 chip —— `全部 N` + 每个盒子一条（带计数）。
  计数就是每个盒子的账号数；筛选按卡片上**显示的那个盒子**（账号没填就回落默认盒）来。
  选中的盒子从快照里消失时自动退回「全部」；只有一个盒子时这一行不渲染。

### 数据来自 akso-cloud

插件是**服务型**的：host 半边登录 akso-cloud 并把账号库搬到本机，浏览器半边只跟本机说话。

```
浏览器（五个模块 + 账号条） ──同源相对路径──▶ host /api/akso-workbench/* ──Bearer──▶ akso-cloud
                                                      │
                                   $DSH_HOME/akso-workbench/cloud.json（令牌，0600）
                                   $DSH_HOME/akso-workbench/prefs.json（当前账号，纯偏好）
                                   $DSH_HOME/akso-workbench/journal/（运行日志，JSONL）
```

- **登录（主路径：设备流 / 跳浏览器）**：`POST /api/auth/device-start` 拿到 `userCode` + 服务端拼好的
  `verificationUrl`，host **直接唤起系统默认浏览器**；浏览器里本来就有 akso-cloud 的登录态，点一下「批准」即可，
  host 每 `interval` 秒 `device-poll`，批准后立刻把令牌落盘并回拉快照。`deviceCode` 只留在 host 内存。
  邮箱密码登录保留为备用路径（折叠在「或用邮箱密码登录」里）。
- **服务地址固定**：`https://www.dragonrain.top` 写死在 host 侧，**界面上没有服务地址输入框**；
  只有 `AKSO_CLOUD_BASE`（自建部署 / 测试）与 `AKSO_WORKBENCH_NO_OPEN=1`（验证时不弹浏览器）两个环境变量。
- **令牌只落本机文件，永不下发浏览器**。
- **给 agent 的四个工具**：`akso_workbench_accounts`（只读快照）、`akso_workbench_config_plan`、
  `akso_workbench_config_apply`、`akso_workbench_object_analysis`；都带固定 UID，日志里按 UID grep。
- **一次调用的完整时序**：工具外壳与 `_uid`、账号门与换票、四个工具各自做了什么、审批窗如何轮询、
  引擎如何复验与回读，见 `docs/knowledge/agent调用时序.md`（附渲染好的 PNG）。
- **出问题：先让用户下载运行日志**。页面「运行日志」显示日志目录、事件数与起止时间，
  一键下载 7 天或 30 天的 JSONL。日志里没有口令与令牌，账号打码成 `q***@example.com`；
  排查顺序见 `docs/knowledge/日志与排查.md`。
- **一致性怎么保证**：读侧只认 akso-cloud 的 `/api/ui/state` —— 它在**一次响应**里给出
  accounts + sites + boxes + revision。host 把这份快照缓存在**一个变量**里；任何写操作都走
  「云端写入 → 重新拉一次 → 返回整份新快照」，浏览器侧用返回的快照**整体替换**本地状态。
  于是五个模块与账号条永远显示同一个 revision，不存在"站点页 3 个、账号页 2 个"的拼盘。
- **多端一致**：别处（akso-pass / 网页控制台）改了云端，点顶栏「同步」即整份刷新；
  顶栏显示 `版本 N · 同步于 hh:mm:ss`。同步失败时保留旧快照并标 `stale`，不会把界面清空。
- **★ 错误体是 `detail`，不是 `error`**：`POST /api/auth/login` 错密码回
  `401 {"detail":"邮箱或密码不正确"}`；`detail` **还可能是对象**，要取它的 `message`。
  `cloud.ts` 的 `errorMessage()` 按 `detail → error → message → 对象.message` 兼容，并有单测钉着。

### 结构

```
plugin/
  package.json          # dsh.client.platform=web + exports["./client"]
  cordis.patch.yml      # 一行 insert：id=akso-workbench
  build.mjs             # esbuild：node 半边出 ESM；client 半边出 __ModuleLoader__ factory
  src/cloud.ts          # akso-cloud 客户端（login / me / ui:state / 六种写操作）
  src/store.ts          # 本机持久化：cloud.json（令牌 0600）+ prefs.json（当前账号）
  src/journal.ts        # 运行日志：JSONL 落盘 + 打码 + 30 天保留 + 导出
  src/analysis*.ts      # 对象分析：读取、字段压缩、历史、drawio/文档产物
  src/tool.ts           # 账号快照工具（手写 ToolDefinition，零运行时依赖）
  src/tool-uids.ts      # 四个工具的固定 UID + invoke/done/fail 日志
  src/index.ts          # node 半边：30 条路由 + 唯一一份快照 + 工具注册（loopback 围栏）
  src/client/           # page.tsx / strip.tsx / state.ts / api.ts / i18n.ts / styles.ts
                        # tools.tsx（配置工具）/ analysis.tsx（对象分析）/ logs.tsx（运行日志）
  test/fake-cloud.mjs   # 假 akso-cloud（真 HTTP + 真 revision），单测用
  test/                 # host / journal / analysis-history / 文档校验等 9 个测试文件
  lib/                  # 构建产物（index.js / client.js），不入库

capture/
  bin/akw-capture.mjs   # 监听工具入口（只用回环地址）
  src/                  # CDP 连接、录制、打码、落盘
  test/                 # 5 个测试
```

### 构建

```powershell
cd D:\ai_assistant\Akso-AI\akso-workbench\plugin
npm install
npm run build          # → lib/index.js + lib/client.js
npm test               # 9 个测试文件
```

`npm test` 起一个**真 HTTP** 的假云端（`test/fake-cloud.mjs`，带真设备流），用手搓的 req/res 打真实 handler：
路由注册/卸载、**非 loopback 一律 403**、方法不对 405、未登录即空态、**登录响应里不含令牌**、
登录失败 400 且不落会话、建站点→建账号后 revision 递增且账号落进默认盒、缺字段在 host 侧就被挡掉、
**盒子改名后账号的 box 跟着变**、**云端在别处被改后一次 sync 就追上**、退出登录清空会话、
运行日志下载与打码、对象分析成功/失败都能读回。

`lib/client.js` 必须是 `window.__ModuleLoader__.load({ id, factory })` 形态；react / react-dom 走 external
（基线提供，打第二份会双实例）。构建脚本已把 CJS 产物套进这个信封。

### 安装

```powershell
# 普通 profile（web / 自建）：
dsh plugin --profile web add "D:\ai_assistant\Akso-AI\akso-workbench\plugin"

# ★ desktop profile 例外：CLI 会直接拒绝
#   （error: profile "desktop" is managed exclusively by the Electron application）
#   两条可行路径：
#   ① 应用内插件管理器安装本地插件；
#   ② 手工等价操作：往 profiles/desktop/package.json 的 dependencies 加
#      "akso-workbench-dsh-ui": "link:D:/ai_assistant/Akso-AI/akso-workbench/plugin"，
#      并把同名项追加到 dsh.profile.bundles，然后在该目录跑 pnpm install。
#   之后**完全退出并重开** DeepSeek Harness（新包需要换 JS 模块世代）。
```

### 已知边界

- 登录后会话**只有一个**（`cloud.json`），多账号切换 = 退出再登录；没有做"多云端 profile"。
- 平台口令随「添加账号」POST 给 akso-cloud（它加密存储），**本机不落地**；插件不提供"查看口令"。
- 账号的**编辑**（改名/改备注/改盒子/改状态）还没做；站点/盒子的写操作只有建站点、盒子改名、绑定站点。
- 「当前账号」是纯 UI 偏好 + 会话入口，**不影响**模型 provider / 工具鉴权。
- `desktop` profile 被 Electron 独占；要在同一套 bundle 组合里验收，复制 profile 换名跑（见 git 历史里的做法）。
