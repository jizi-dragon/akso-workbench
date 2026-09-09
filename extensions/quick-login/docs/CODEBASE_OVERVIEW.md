# Codebase Overview

_由 learn-codebase 流程生成，2026-08-29 更新至 commit `57358bf`（v3.7.2）。初版基于 v3.4.0（c713346），已随 v3.5–v3.7.2 的架构演进全面重写。_

## Summary

QuickLogin（v3.7.2）是一个 Chrome MV3 扩展，解决「在同一浏览器窗口内，对内部低代码平台
（`example-config.example.com`，无状态 JWT Bearer 鉴权）并行在线多个账号」的问题。
每个账号一个独立标签页，通过**六平面隔离**（存储 / AUTH / COOKIE 回放 / CACHE 分区 /
SW·CacheStorage 封控 / IndexedDB 命名空间）实现账号间全链路隔离；管理入口为并行管理页与
账号轮盘；账号密码 AES-GCM 加密存本地。v3.5–v3.7.2 依次定位并修复了「四象限账号权限泄漏」
的全部客户端层根因（详见 `CHANGELOG.md` 与 `research/idb-permissions/`）。

## Tech stack

- **Languages:** TypeScript 5.6（strict），少量 Node 脚本（mjs / ps1）
- **Runtime:** Chrome / Edge MV3 extension；Node 20+（构建与工具脚本）
- **Build / package manager:** npm workspaces（`packages/*`）+ esbuild 0.24（`scripts/build.mjs`）
- **Datastores:** 扩展侧 IndexedDB（账号档案）、`chrome.storage.session`（运行时状态）、
  `chrome.storage.local`（配置/封锁名单/加密种子/诊断缓冲）；站点侧 IndexedDB（经第六平面按账号前缀化）
- **Testing:** Playwright（playwright-core）E2E 取证台架 `tools/e2e/harness.mjs` + 四个配套取证工具
- **Skills applied:** learn-codebase

## Architecture

**六平面隔离**（每绑定标签页生效；`docs/PROJECT-STATUS.md` §二 为权威表）：

```
┌─ MV3 扩展 ────────────────────────────────────────────────────────────────┐
│ service-worker.ts（par.* / ql.diag 消息路由、轮盘三级降级、角标诊断）        │
│  ├─ core/parallel-session.ts  绑定表 tabId↔accountId、token 双通道捕获、   │
│  │                            Cookie 登录快照+回放、种子推送、授权健康门控   │
│  ├─ core/tab-rules.ts         每绑定 tab ≤2 条 DNR session 规则：           │
│  │    AUTH: Authorization→Bearer <token>（xhr/ws/sub_frame，host+父域）    │
│  │    COOKIE: set 本账号快照回放（身份键过滤）/ 无快照时 remove             │
│  │    【逐条安装降级——原子批量曾是「平面全死」根因】                         │
│  ├─ core/parallel-store.ts    账号 CRUD（IndexedDB v2 accounts）           │
│  ├─ core/credentials.ts       AES-GCM 凭证加密（设备绑定种子）              │
│  └─ 遗留：navigation/session-manager/account-registry（v2.1 免密切换路径）  │
│ content/                                                                 │
│  ├─ shield-main.ts  MAIN world 壳，六项职责：                              │
│  │    ① localStorage 命名空间化（Storage.prototype 方法级补丁）             │
│  │    ② document.cookie 虚拟 Cookie 袋                                    │
│  │    ③ bind 种子直灌（token/身份/指纹）                                   │
│  │    ④ 写入上报（token 捕获主通道）+ fetch/XHR Authorization 嗅探（备通道）│
│  │    ⑤ SW/CacheStorage 封控（register 拦截 + 注销 + 命名空间 + match miss）│
│  │    ⑥ IndexedDB 命名空间（open/deleteDatabase/databases 前缀化）          │
│  │    + 页面层 CACHE 分区（同源 GET 追加 _qlck=t<tabId>；DNR 无 urlTransform）│
│  ├─ shield-bridge.ts ISOLATED 桥：postMessage ↔ chrome.runtime 中继        │
│  ├─ auto-login.ts   登录表单自动填表（顶层直填 srcdoc iframe 密码框）       │
│  ├─ title-hook.ts   标签标题持续维持                                       │
│  └─ wheel-overlay.ts 页面内 Shadow DOM 轮盘浮层                            │
│ ui/  parallel/（管理主页）· wheel/（小窗兜底）· popup/（启动器）             │
└───────────────────────────────────────────────────────────────────────────┘
```

## Key modules

| Path | Responsibility |
| --- | --- |
| `packages/extension/src/background/service-worker.ts` | 消息分发（`par.*`、`ql.diag`、`wheel.toggle`）、轮盘三级降级链、快捷键命令、角标闪标诊断 |
| `packages/extension/src/background/core/tab-rules.ts` | 两条 DNR session 规则（AUTH 改写 + COOKIE 回放/剥离）的构建/换值重建/冷启恢复/孤儿清理；**逐条安装 + 单条失败降级**；`requestDomains` 含父域；诊断埋点 |
| `packages/extension/src/background/core/parallel-session.ts` | tabId↔accountId 绑定表、token 双通道捕获（storageWrite + Authorization 嗅探）、**Cookie 登录时点快照与回放**（`IDENTITY_COOKIE_BLACKLIST` 过滤 `__auth_token__/__auth_user__/__device_fp__`）、bind 种子（含 tabId）、授权健康门控（`enforcementOff`） |
| `packages/extension/src/content/shield-main.ts` | MAIN world 六项职责（见架构图）；`SW_SHIELD_ENABLED` / `CACHE_PARTITION_ENABLED` 开关；晚到 bind 走 sessionStorage 守卫 reload 一次 |
| `packages/extension/src/content/shield-bridge.ts` | ISOLATED↔MAIN↔background 双向消息中继 |
| `packages/extension/src/content/auto-login.ts` | 自动登录：顶层 frame 直填同源 srcdoc iframe 密码框（React 受控组件原生 setter + input/change 事件） |
| `packages/extension/src/ui/parallel/parallel.ts` | 并行管理主页：站点授权清单、账号增删开、实时徽标（在线/待登录/未授权·已暂停）、3s 轮询 |
| `packages/extension/src/ui/wheel/wheel.ts` + `src/content/wheel-overlay.ts` | 账号轮盘双形态：普通页 Shadow DOM 浮层优先，受限页独立小窗，最终降级标签页 |

## Data & control flow

**打开账号（`par.open`）**：`parallelSession.open(accountId)` → 无既有绑定标签则
`chrome.tabs.create`（有 token 快照直达 `/`，否则 `/login` 自动填表）→ 写绑定表（内存 +
`chrome.storage.session`）→ 有凭证则解密下发待登录（失败仅记诊断，不阻断）→ `pushBind`
（bind 种子 = token/身份/指纹快照 + tabId）→ 标题改写 → `syncAccountRules`（COOKIE 规则
按快照回放值 set / 空快照剥离；AUTH 规则待 token 捕获后安装）。

**登录后捕获链**：站点把 JWT 写进（虚拟化后的）`localStorage.__auth_token__` → MAIN 壳
setItem 补丁上报 → 桥 → background `captureToken`（JWT 形态校验）→ 存 session 存储 →
该账号全部绑定标签换装 AUTH 规则；**首捕即触发 Cookie 登录快照**（`chrome.cookies` 全量采集
→ 过滤身份键 → 存账号档案）并以其回放值重建 COOKIE 规则。二级通道：出站 Authorization 头嗅探。

**SW 冷启恢复**：`parallelSession.restore()` 读 session 存储重建绑定表与 token 快照 →
`tabRules.restore()` 与现存 session 规则差集同步（孤儿清理 + 缺失重建）→ 按授权健康门控重装。

**六平面在页面侧的生效点**：`shield-main.ts` 于 document_start 直通；收到 bind 且
`readyState==='loading'` 即激活（装全部补丁 + 种子直灌）；晚到则经 sessionStorage 守卫
reload 一次强制收敛。

## Entry points

- 扩展入口：`packages/extension/manifest.json`（MV3；background SW + 4 个内容脚本 +
  `quick-wheel` 命令 + `optional_host_permissions: ["*://*/*"]`）。
- 后台入口：`packages/extension/src/background/service-worker.ts`（装载监听器；`restore()` 冷启自举）。
- 构建入口：`scripts/build.mjs`（→ `dist/`）。

## Build, run, and test

```bash
npm install               # 首次；引擎侧 better-sqlite3 为原生模块
npm run typecheck         # tsc --noEmit
npm run build             # esbuild → dist/
npm run e2e               # 交互式取证台架
QL_E2E_SELFTEST=1 npm run e2e   # 冒烟自检
QL_E2E_DRIVE=file npm run e2e   # 文件驱动（检查点写 tmp/e2e-go 推进）
node tools/e2e/peek.mjs <type>  # 事件流判读
```

装载：chrome://extensions → 加载已解压 → 选 `dist/`。**更新 dist 后必须点扩展卡片
「重新加载」**——Chrome 会缓存扩展 SW 脚本（实测档案 ScriptCache 长期复用旧脚本，
曾造成「新代码从未生效」的假象；顽固时删档案 `Default/Service Worker/`）。
E2E 首次运行需 `npx playwright-core install chromium`（国内镜像
`PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright`）；
品牌 Chrome 137+ 禁 `--load-extension`，台架强制用 Playwright Chromium。

## Conventions

- 版本号三处必须一致：根 `package.json`、`packages/extension/manifest.json`、
  `src/shared/constants.ts` 的 `EXT_VERSION`（当前均为 3.7.2）。
- 消息协议集中定义：`src/shared/messages.ts`、`constants.ts`；存储键前缀 `sb:`（v2.1 遗留）、
  `ql:`（现行）。
- DNR 规则：显式写全 `resourceTypes`；session 规则 id 按平面分段（AUTH 1xxxxx / COOKIE 2xxxxx）；
  **逐条安装 + 单条失败降级**（原子批量曾连坐全灭）；`requestDomains` 含父域。
- 诊断埋点：SW 侧统一写 `chrome.storage.local['ql:diag']` 环形缓冲（60 条），台架经扩展页
  `ql.diag` 消息读取（`browserContext.newCDPSession` 不支持 Worker，勿再走 Worker CDP）。
- 后台向内容脚本推送「尽力而为 + 事件重推」；错误处理统一 `{ok, data|error}` Result 包装。

## Risks & rough edges

1. **主动提权方向无法在客户端根治**：需「接管服务端应答」，客户端篡改 IDB → 白屏（已实证，
   `research/idb-permissions/reports/05-minimal-capability.md`）。权限真边界在服务端
   （管理侧读接口未过滤 + 单设备登录未启用）——应向平台方反馈。
2. **Web Worker 内打开的 IndexedDB** 不受主世界补丁（v3.7.0 已知残余，待观测）。
3. Cookie 快照仅登录时点采集一次；服务端会话标识轮转需重登刷新（JWT 主体不受影响）。
4. 轮盘浮层在未授权站点降级独立小窗（设计使然，用户知悉）。
5. 遗留未清理：`packages/engine/`（`build.mjs` 仍打包、tsconfig 仍 typecheck）、
   `nm-client.ts`（孤儿）、v2.1 免密切换的 `session.*` 消息路径与 IDB `sessions` store。
6. 无自动化单元测试；回归依赖 E2E 台架 + 人工四象限操作。
7. 品牌环境差异：E2E 隔离档案与真实档案的 Cookie jar 状态不同——v3.7.2 的根因只在真实档案
   复现过（「真实档案 vs 隔离档案」差异本身是取证线索，见 CHANGELOG 3.7.2）。

## Glossary / where to look next

- **六平面隔离**：存储 → AUTH → COOKIE 回放 → CACHE 分区 → SW/Cache 封控 → IDB 命名空间；
  权威表见 `docs/PROJECT-STATUS.md` §二。
- **四象限泄漏**：已收敛；根因链与终审见 `CHANGELOG.md` 3.5–3.7.2 与 `research/idb-permissions/`。
- 入门阅读顺序：`README.md` → `docs/PROJECT-STATUS.md` → `CHANGELOG.md` →
  `docs/CODEBASE_OVERVIEW.md`（本文）→ `service-worker.ts` → `core/parallel-session.ts` →
  `core/tab-rules.ts` → `content/shield-main.ts` → `tools/e2e/harness.mjs`。
