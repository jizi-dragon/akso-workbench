# akso-workbench-ts

akso-workbench 的**服务层**（DSH 插件侧的域能力）。不是重写整个 workbench —— 是把它拆成
"DSH 已经提供的"与"只有我们知道的"两半，只保留后者。

## 保留 / 丢弃（用户要求 2 的落地清单）

| workbench 现有 | 处理 | 理由 |
|---|---|---|
| `harness/` 循环、缓存、上下文 2,262 行 | **丢弃** | DSH 的 agent 循环与上下文管理 |
| `sandbox/` 1,844 行 | **丢弃** | DSH 三档沙箱（read-only / workspace-write / danger-full-access） |
| `skills/` 1,775 行 | **丢弃** | DSH 的 skill（SKILL.md + 脚本） |
| MCP 客户端 1,032 行 | **丢弃** | DSH 内置 MCP 客户端 |
| `static/` 11,041 行 + 对话 UI | **丢弃** | DSH 的 Web GUI / client-ui 插件 |
| `approvals.py` 772 行 | **降到 ~100 行** | 计划文件 + `ask_user_question` 弹窗（见 `docs/10`） |
| `services/egmp/**` 3,851 行 | **移植（本仓）** | 平台语义只有我们知道 |
| `vault.py` / `accounts.py` | **改为调 akso-cloud** | 不再两处实现同一套账号库 |

## 当前进度

| 模块 | 状态 | 判据 |
|---|---|---|
| `src/egmp/client.ts`（登录 / 信封 / 分页 / token 缓存） | ✅ | `test/client.test.ts` **7/7** |
| `src/egmp/*`（kernel / queries / replay） | ⏳ | — |
| 计划文件 + apply（写对象） | ⏳ | 设计见 `docs/10` §5 |
| DSH 工具注册（`defineTool`） | ⏳ | 需要 `@deepseek-ai/dsh-tools`（插件包依赖） |

## 跑

```powershell
npm install
npm test
npm run typecheck
```

零运行时依赖：HTTP 用全局 `fetch`，测试用 `node --test`（Node 24 直接跑 `.ts`）。

## 三条从 Python 版继承的"静默失败"防线

1. **信封校验**：平台回 HTTP 200 但 `code != 0` 仍是失败 —— 漏掉它，"失败"会显示成"没有数据"。
2. **分页硬上限**：`hasNext` 恒 true 的坏代理会无限请求；到 `MAX_PAGES` **抛错**，不返回半截。
3. **token 按 `(base_url, username)` 分键**：不分键会把 A 环境的令牌发到 B 环境（偶发 401）。

---

## DSH 客户端插件（`plugin/`）：左栏账号中心 + 输入框下方账号条

按 `docs/07-dsh-client-plugin-ui-spec.md` 做的界面增量。两个座位，**不是**改 DSH 源码：

| 需求 | 座位 | 位置证据 |
|---|---|---|
| 左栏一个 `akso-workbench` 菜单 + 中栏账号中心页 | `sidebar.panellist`（id）+ `main`（key），两处 id 必须同值 | `sidebar.panellist` 是「插件 / 自动化任务 / 技能中心 / 更多」那一组 |
| 输入框**下方**、**上下文比例右侧**的账号条 | `conversation.composer.dock`（order 20） | 这一行是 `renderSlot('conversation.composer.dock')` → `ContextMeter`；slot 包裹层是 `display:contents`，所以账号条上的 `order:99; margin-left:auto` 直接作用在**这一行**上，把它排到 meter 右侧、贴行右边缘 |

> ★ 规范 §4.2 的模板写的是 `conversation.input.dock`（卡片**上方**）。用户要的是"输入框下方、在上下文比例右侧"，
> 而实测用量（StatsPills / ContextMeter）在 composer.dock 那一行 ⇒ 这里**故意**用 composer.dock，
> 不是笔误（规范 §8 坑 4 说的正是这两个座位一上一下）。

**版式**（2026-10-26 按用户要求「学 akso-pass 的页面美学与功能特性、导航栏置顶」重做）：
顶部品牌条（品牌标 + 标题 + 统计芯片 + 同步状态 + 操作）→ 下划线式 tab
→ 内容区（卡片网格 / 表单 / 行式列表）→ 页内滚动。颜色全部走 DSH 主题 token，深/浅色跟随宿主。

**交互口径**（2026-10-26 第二轮，逐条对应用户要求）：

- **只能加、不能删**：账号/站点/盒子都不提供删除，host 侧也**没有**对应路由（有单测钉着"删除端点不存在"）。
- **卡片优先显示页签名**：标题取 `tabName`（空则回落平台账号），副标题是站点；事实区是「平台账号 / 盒子」。
- 「设为当前」改叫 **「连接agent」**；已连接的卡片显示「已连接」徽章 + 「已连接给 agent」，不再有操作按钮。
- **底部账号条只显示"已连接"的那一个账号**，内容是 `页签名 · 站点名`；**已连接时不出现 +**。
  未连接时只有一个按钮：有账号=「＋ 连接 Agent」，没账号=「添加账号」。
- **点已连接的胶囊 → 弹层**：列出全部账号（当前项带 ✓）可换，底部「清除连接」可断开；点外面或 Esc 关闭。
- **线条加深**：边框从 `border-l1`（4% 黑）提到卡片/输入/行 `l3`（12%）、按钮 `l4`（16%），
  已连接卡片再叠一圈 1px 品牌色描边。

### 数据来自 akso-cloud（2026-10-26 接入）

插件现在是**服务型**的：host 半边登录 akso-cloud 并把账号库搬到本机，浏览器半边只跟本机说话。

```
浏览器（四个 tab + 账号条） ──同源相对路径──▶ host /api/akso-workbench/* ──Bearer──▶ akso-cloud
                                                      │
                                   $DSH_HOME/akso-workbench/cloud.json（令牌，0600）
                                   $DSH_HOME/akso-workbench/prefs.json（当前账号，纯偏好）
```

- **登录（主路径：设备流 / 跳浏览器）**：`POST /api/auth/device-start` 拿到 `userCode` + 服务端拼好的
  `verificationUrl`，host **直接唤起系统默认浏览器**（Windows `start` / mac `open` / linux `xdg-open`）；
  浏览器里本来就有 akso-cloud 的登录态，点一下「批准」即可，host 每 `interval` 秒 `device-poll`，
  批准后立刻把令牌落盘并回拉快照。**用户不用输网址，也不用输密码。**
  `deviceCode` 只留在 host 内存，浏览器只拿到"用户码 + 链接"。
  邮箱密码登录保留为备用路径（折叠在「或用邮箱密码登录」里）。
- **服务地址固定**：`https://www.dragonrain.top` 写死在 host 侧，**界面上没有服务地址输入框**；
  只有 `AKSO_CLOUD_BASE`（自建部署 / 测试）与 `AKSO_WORKBENCH_NO_OPEN=1`（验证时不弹浏览器）两个环境变量。
- **令牌只落本机文件，永不下发浏览器**；页面里那行「令牌只存在本机（0600）」就是这条口径的说明。
- **给 agent 的只读工具 `akso_workbench_accounts`**：注册进 `ctx.tools`，参数
  `{ section?: all|accounts|sites|boxes, query?, limit?, refresh? }`，返回与界面**同一份快照**的
  accounts/sites/boxes 摘要（`username / tabName / site / box / note / status / hasPassword / connected` 等，
  另带 `connectedAccountId`），**不含口令、不含令牌**；未登录时返回 `loggedIn:false` + 登录提示，让模型知道该请人去登录。
- **四个页面**：并行账号 / 添加账号 / 站点管理 / 盒子管理（与 akso-pass 的 tab 一致）；**只增不删**。
- **一致性怎么保证**（用户的核心目的）：读侧只认 akso-cloud 的 `/api/ui/state` ——
  它在**一次响应**里给出 accounts + sites + boxes + revision（服务端自己重试到一致）。
  host 把这份快照缓存在**一个变量**里；任何写操作都走「云端写入 → 重新拉一次 → 返回整份新快照」，
  浏览器侧用返回的快照**整体替换**本地状态。于是四个 tab 与账号条永远显示同一个 revision，
  不存在"站点页 3 个、账号页 2 个"的拼盘。
- **多端一致**：别处（akso-pass / 网页控制台）改了云端，点顶栏「同步」即整份刷新；
  顶栏会显示 `版本 N · 同步于 hh:mm:ss`，两处对不上时一眼能看出来。同步失败时保留旧快照并标 `stale`，
  不会把界面清空。
- **★ 错误体是 `detail`，不是 `error`**（2026-10-26 对线上实测）：
  `POST /api/auth/login` 错密码回 `401 {"detail":"邮箱或密码不正确"}`、无令牌访问 `/api/ui/state` 回
  `401 {"detail":"缺少会话令牌"}`。只读 `error` 会让所有云端错误退化成 `HTTP 401/400`（用户看不到原因）；
  而 `detail` **还可能是对象**（删站点 409 那种结构化错误），要取它的 `message`。
  `src/cloud.ts` 的 `errorMessage()` 按 `detail → error → message → 对象.message` 兼容，与 akso-pass 的
  `errorDetail()` 同一套顺序，并有一条单测钉着它。

### 结构

```
plugin/
  package.json          # dsh.client.platform=web + exports["./client"]
  cordis.patch.yml      # 一行 insert：id=akso-workbench
  build.mjs             # esbuild：node 半边出 ESM；client 半边出 __ModuleLoader__ factory
  src/cloud.ts          # akso-cloud 客户端（login / me / ui:state / 六种写操作）
  src/store.ts          # 本机持久化：cloud.json（令牌 0600）+ prefs.json（当前账号）
  src/tool.ts           # 给 agent 的只读工具（手写 ToolDefinition，零运行时依赖）
  src/index.ts          # node 半边：15 条路由 + 唯一一份快照 + 工具注册（loopback 围栏）
  src/client/           # page.tsx（登录门 + 四个 tab）/ strip.tsx / state.ts / api.ts / i18n.ts / styles.ts
  test/fake-cloud.mjs   # 假 akso-cloud（真 HTTP + 真 revision），单测用
  test/host.test.ts     # 12 条 host 判据
  lib/                  # 构建产物（index.js / client.js），不入库
```

### 构建

```powershell
cd D:\ai_assistant\Akso-AI\akso-workbench\plugin
npm install
npm run build          # → lib/index.js + lib/client.js
npm test               # host 半边 18 条判据
```

`npm test` 跑的是 `test/host.test.ts` + `test/fake-cloud.mjs`（假云端带**真设备流**）：不 mock 业务函数，起一个**真 HTTP** 的假云端，
用手搓的 req/res 打真实 handler。覆盖：路由注册/卸载、**非 loopback 一律 403**、
方法不对 405、未登录即空态、**登录响应里不含令牌**、登录失败 400 且不落会话、
建站点→建账号后 revision 递增且账号落进默认盒、缺字段在 host 侧就被挡掉、
当前账号偏好落盘并在删账号后归零、**盒子改名后账号的 box 跟着变**（同一份快照两处一致）、
**云端在别处被改后一次 sync 就追上**、退出登录清空会话。

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

### 验证（实测通过的那一套）

```powershell
# 起一个独立实例（不动正在用的桌面端），拿到带 token 的 URL
node "$env:APPDATA\npm\node_modules\@deepseek-ai\dsh\lib\bin.js" --profile web --no-open --port 0
```

判据（都实跑过；云端那几条是对着 `test/fake-cloud.mjs` 起的**真 HTTP 假云端**跑的）：

- 未登录时整页是**登录门**：只有「在浏览器中登录」（**没有服务地址输入框**）+ 折叠的邮箱密码备用；
- 点「在浏览器中登录」⇒ 出现 `验证码 CODE-N` 与「已在浏览器里打开确认页」；host 已唤起一次浏览器
  （验证时用 `AKSO_WORKBENCH_NO_OPEN=1` 关掉弹窗）；
- 浏览器里批准后（测试用假云端的 `/__approve` 模拟）⇒ 页面**自己轮询到**账号中心，无需刷新；
- 设备流的 `deviceCode` **不在任何响应里**（单测断言 `!raw.includes('deviceCode')`），浏览器只拿到用户码 + 链接；
- 登录后顶栏显示邮箱/显示名、`N 账号 / N 站点 / N 盒子`、`版本 N · 同步于 hh:mm:ss`；
- 四个 tab：**并行账号 / 添加账号 / 站点管理 / 盒子管理**，都带计数，顶部之下是下划线式 tab；
- 站点管理：添加站点后立刻出现一行（`N 个账号`），并自动建同名盒子；**三个页面都没有删除按钮**
  （实测 `getByRole('button', { name: '删除' })` 计数为 0，host 侧也没有删除路由）；
- 盒子管理：默认盒带「默认盒 / 已停用」徽标，支持重命名、绑定站点；
- 添加账号：站点下拉来自同一份快照（实测 2 项）、盒子下拉列出可用盒子；建完账号立刻出现在并行账号；
- 卡片标题是**页签名**（实测 `陈俊松-devops` / `李渝龙-devops` / `李渝龙` / `通桥-李渝龙`），入口是「连接agent」；
- 连接后底部账号条只剩 `页签名 · 站点名` 一个胶囊（实测 `李渝龙-devops · akso-devops`，`+` 计数 0）；
  点它弹层可换账号（实测换成 `李渝龙 · 公司配置环境`）、可「清除连接」（实测回到 `＋ 连接 Agent`）；
- 一致性：建完账号后顶栏统计、tab 计数、卡片、账号条**同时**变成 1 —— 因为它们是同一份快照；
  host `/api/akso-workbench/state` 实测 `{revision:2, accounts:1, sites:1, boxes:2}`；
- 账号条在**输入框下方同一行**、**贴该行最右**：实测行 `522..1192`，账号条 `943..1192`（右边缘齐平）、
  ContextMeter `870..931` 在其左侧，整行高 24px；未连接时只渲染一个按钮（有账号=「＋ 连接 Agent」，没账号=「添加账号」）；
- 控制台 0 报错。

### 在「桌面端那份组合」里验收，而不用重启正在用的桌面端

`desktop` profile 被 Electron 独占（连 `--dump-config` 都会被拒），所以想验证它在**同一套 bundle 组合**里也正常，
就复制一份 profile 换个名字跑（实测可行，且不动正在用的应用）：

```powershell
$src="$env:USERPROFILE\.dsh\profiles\desktop"; $dst="$env:USERPROFILE\.dsh\profiles\desktop-check"
New-Item -ItemType Directory $dst | Out-Null
Copy-Item "$src\package.json","$src\cordis.yml","$src\cordis.patch.yml" $dst
New-Item -ItemType Junction -Path "$dst\node_modules" -Target "$src\node_modules"   # 复用已装的包，不重装
node "$env:APPDATA\npm\node_modules\@deepseek-ai\dsh\lib\bin.js" --profile desktop-check --no-open --port 0
# 用完删 $dst（junction 先删，别把真 node_modules 删了）
```

这一跑暴露过一个真问题：`@linxin666/dsh-client-ui-skill-explorer@0.4.5` 声明 `@deepseek-ai/dsh >= 0.2.0-rc.2`，
在本机 CLI `0.1.7-rc.2` 上会被**跳过**（`dsh: skipping profile bundle ... incompatible`）；本插件不声明 `peerDependencies`，
所以不会被这道闸门拦。★ 也由此确认：桌面端应用内可能跑的是 0.2.x，本插件的注册调用与 0.2 构建的
skill-explorer **逐字同形**（`slots.inject('sidebar.panellist', () => slots.register({ name, id, order, label }, Icon))`
+ `slots.inject('main', () => slots.register({ name, key, inject }, Page))`），两边都能装。

### 已知边界

- 登录后会话**只有一个**（`cloud.json`），多账号切换 = 退出再登录；没有做"多云端 profile"；
- 平台口令随「添加账号」POST 给 akso-cloud（它加密存储），**本机不落地**；插件不提供"查看口令"；
- 账号的**编辑**（改名/改备注/改盒子/改状态）还没做，目前只有"创建 / 删除 / 设为当前"；
- 站点与盒子页只做了最常用的写操作（站点：建/删；盒子：改名/绑站点/删），没有排序与批量；
- 「当前账号」是纯 UI 偏好，**不影响**模型 provider / 工具鉴权（规范 §4.1 的"切换影响什么"仍待定）；
- `src/egmp/client.ts`（eGMP 平台客户端）与插件尚未打通 —— 拿到账号后还不会自动去平台登录。
