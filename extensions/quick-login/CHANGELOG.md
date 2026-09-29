# Changelog · 浏览器扩展（执行面）

> **性质**：本文件记录**本项目对扩展的改造**。它曾经只记"对上游 quick-login 的私有改造"，
> 并另存一份上游发布史——**那段协作已于 2026-09-29 终止**：
> 上游文档存档（7 份 + 490 行上游 CHANGELOG）已删除，本项目**不再同步上游**。
> 想看更早的上游发布史，只能 `git show <2026-09-29 之前的提交>:extensions/quick-login/docs/archive/`。
>
> 格式：扩展相关变更按根 `CHANGELOG.md` 的版本号组织。
> 凡 ⚠️ 标注的条目是**"删掉过、别误加回来"**的清单——上游协作期内它表示"下次同步上游要再摘一次"，
> 现在它的含义只剩**"这是刻意移除的，不要恢复"**。

## 未发布

- **模块化收口**（2026-09-29，用户下达「将 quick-login 当做正常模块进行开发」）：
  - 目录：`packages/extension/{src,manifest.json}` → 模块根（`src/` + `manifest.json`）；
    `tools/e2e/probe-page.mjs` → `scripts/probe-page.mjs`。`packages/` 与 `tools/` 两层消失。
  - `package.json` 去掉 `workspaces`（只剩一个包，这层是 fork 期的考古层）；
    包名 `quick-login` → `workbench-extension`。
  - `.gitignore` **重写**：它此前**不是合法 UTF-8**（168 字节含 17 个 U+FFFD，中文注释是 GBK），
    并含三行指向**不存在**目录的 `packages/engine/*`（上游 v3.9.2 就删了本地引擎）。
  - `package-lock.json` 版本 **0.3.5 → 0.3.13**（漂了 8 个版本；
    `bump.py check` 的同步清单**不含它**，所以没人发现）。
  - 上游文档存档 `docs/`（7 份 `UPSTREAM-*` + README）**删除**；
    本 README 与 CHANGELOG 去掉 fork/同步叙述。
  - 路径同步：`scripts/build.mjs`、`tsconfig.json`、`tools/bump.py`、`tools/build.ps1`、
    `tools/verify_host_logic.mjs`。判据：`npm run typecheck` + `npm run build` +
    `verify_host_logic.mjs` 22/22 + `verify_packaging.py` 13/13。
  - **修一个坏掉的脚本**：`scripts/probe-page.mjs` 第 15 行 `import { chromium } from 'playwright-core'`
    **一直解析不到**（`playwright-core` 既不在本模块 `node_modules`、也不在全局），
    实测 `ERR_MODULE_NOT_FOUND` —— 也就是说这个探针**当时是跑不起来的**。
    现补进 `devDependencies`，并**钉在 `1.62.0`**（不是 `^` 范围）：它不装自己的浏览器，
    而是用 Python 侧 `playwright` 装的那份 Chromium（`executablePath` 覆盖 ⇒
    Playwright 的版本注册表在这里不起作用），所以对齐 Python 侧的 `1.62.0` 才能让
    `npm install` + `uv sync` 在任何机器上复现同一配对。理由与判据写在 README。

## 0.3.4（2026-09-11）

- ⚠️ **归档上游文档、建立权威文档**（文档重构，不改代码）：上游 7 份文档 + CHANGELOG 移入
  `docs/archive/` 并加归档抬头；权威内容合并进根侧 `docs/EXTENSION-PLANE.md`。
  此后**上游文档不再承担现状描述职责**，同步上游时只需把新版原文继续放进 `docs/archive/`。

## 0.3.3（2026-09-11）

- **扩展版本上报**（配合桌面端「升级后自动检测需重新加载」）：`sync.ts` 的 `reportState()`
  **无条件**上报 `extVersion`——早期实现有 `if (!items.length) return`，导致「扩展是旧版」这一事实
  在账号映射为空时永远传不到桌面端。现在扩展版本进 `GET /extension/health`，
  桌面据此显示「浏览器扩展版本过旧」提示条 → 引导点「重新加载」。
- **刻意不做自动 `chrome.runtime.reload()`**：重载会掐断进行中的自动登录，
  把不可控时序留给用户点一次。

## 0.2.24（2026-09-11）

- **指令面改长轮询**（实测延迟均值 964ms → 20ms，约 50 倍）：`sync.ts` 新增 `commandStream()`
  长轮询流（`GET /extension/commands?after=N&wait=15`），与原 2s 数据面 tick 解耦。
- 四条护栏（**改 `sync.ts` 必读**）：
  - `consuming` 互斥——长轮询流与 tick 兜底都可能拿到同一批指令，并发消费会让同一条 `par.open` 开两个页签；
  - `streaming` 幂等——`chrome.alarms` 每次触发都会调 `commandStream()`，无闸会累积并发循环；
  - 流超过 25s 未取回时由 tick 兜底（`streamAliveAt` 判定），避免指令滞留；
  - `getJson` 返回 `null` 时退避 1.5s——**旧版服务端不认 `wait` 参数时会热循环打满 CPU**（已加 `data.longPoll !== true` 分支）。

## 0.2.23（2026-09-11）

- **架构收敛（用户定稿：不影响功能）**：扩展 = **执行面**（收 `par.list` / `par.open` / `wheel.toggle` + 六平面隔离）；
  账号数据的增删改一律归桌面端，扩展侧只在 `sync.ts` 里对账。
- ⚠️ **删了什么**（净删 2,849 行 / 18 文件）：
  ① 死文件 `ui/parallel/*`（1,290+130+629 行）与 `ui/send.ts`；
  ② 旧会话模型 `session-manager.ts` / `account-registry.ts` / `navigation.ts` + `session.*` 协议 + `Session` 类型 + `sessionTabBindings`；
  ③ 并行页专用协议 `par.create/update/delete/moveBox/renameBox/deleteBox/probeScheme` + `data.export/import`（SW 死分发约 275 行）。
- **保留（别误删）**：`parallelStore` 的增删改（`sync.ts` 直接调用 = 活数据面）；
  `site-auth.ts` + `site.grants.*` + `par.grantChanged`（0.2.21 定稿保留的授权/停用核心，现为**休眠源码**）；
  `ql.diag`（诊断）；`wheel.toggle`（桌面通道）。
- **单一真源**：协议键（`__ql_ns_` / `__ql_cookies__` / `__auth_token__` / `__auth_user__` / `__device_fp__` /
  `QL_PAGE_TO_BRIDGE` / `QL_BRIDGE_TO_PAGE`）只在 `shared/constants.ts` 定义；
  host/端口五函数收归 `background/core/host.ts`；`applyTitle` → `tabs/tab-title.ts`；
  待登录凭证 → `core/pending-login.ts`。
- **硬约束**：`shared/constants.ts` 会被 **MAIN world**（`content/shield-main.ts`）import →
  顶层**禁止**任何扩展 API 求值（`extVersion()` 因此写成函数而非常量）。
- **修两个静默失效缺陷**（红/绿验证过）：
  ① `shield-main` 的 `Storage.prototype.clear` 补丁未区分存储实例 → 页面调 `sessionStorage.clear()`
  会清空虚拟 Cookie 袋（含 token）；现仅 `this === window.localStorage` 才重置袋子；
  ② `auto-login.fillPasswordInIframes` 首个可访问 iframe 无密码框即 early-return →
  多 iframe 页面自动填表静默失效；现扫描全部 iframe。

## 0.2.22（2026-09-10）

- ⚠️ **Page Monitor 整块撤销（用户定稿）**：删除 `background/core/page-monitor.ts`、
  `content/pages-overlay.ts`、上游 `docs/FEASIBILITY-RECENT-PAGES.md`；摘除 manifest `quick-pages`(Alt+W) 命令、
  桥上行 `pageNames`、`shield-main` 名称嗅探、`pages.recent` / `pages.jump` / `RecentPageEntry`、
  `ql:recentPages` / `RECENT_PAGES_MAX`、SW 的对应消息与命令分支及 `togglePagesOverlay`、`build.mjs` 入口。
  - 页签标题**仍然是页签名**（`tabs/tab-title.ts` + `content/title-hook.ts` 未动）——撤销的是「用页面信息改写标题」，
    不是「标题显示页签名」。
  - **上游同步会把它带回来**（v3.11/v3.13 特性）→ 按上清单再摘一次。
- **弹窗改版**：站点授权区块下线（**只删 UI**——`site-auth.ts` / `par.grantChanged` / `ql:blockedHosts` 全保留）、
  导出诊断入品牌头右上角、版本号入页脚右下角。
- **版本真源改 manifest**：`extVersion()` 读 `chrome.runtime.getManifest().version`
  （`bump.py` 五写含 manifest → 自动跟随项目升版）；
  不要再引入手写版本常量（上游文档仍写「EXT_VERSION 三处必须一致」，已过时）。
- **身份平面端口口径修复**：`urlHostOf()`（= `new URL(u).host`，带端口）统一用于归属/停用名单/页签收编；
  规则覆盖（DNR `requestDomains`）与 Cookie 作用域**故意**无端口。
  DNR 无法表达端口 → 同主机跨端口的网络平面隔离仍是结构性限制（真要做得换 `urlFilter`）。
- **轮盘扇区名口径**：页签名优先、空则账号名（`ui/wheel/wheel-core.ts`）——桌面侧同名口径见
  `wheel-picker.html` 的 `labelOf()`。

## 0.2.21（2026-09-10）

- ⚠️ **声明式全站权限**：manifest `host_permissions: ["<all_urls>"]`，上游的**逐站点授权链路整套退役**。
  上游文档中的「授权健康门控 / 未授权 · 已暂停」在现状下只剩「用户手动停用名单」语义。
- 配套：0.2.22 的端口口径修复补完了本次只修一半的端口守卫。

## 0.2.20（2026-09-10）

- **下载归属分层修复 + 权限**：新增 `downloads` 权限；下载失败时记录归属判定日志到 `ql:diag`；
  单账号站点下载带 Bearer 自动重发（覆盖 `tabId=-1` 场景，即下载管理器发起的请求）。

## 0.2.x 更早（上游同步期，条目从简）

| 版本 | 改造 |
|---|---|
| 0.2.18 | 轮盘视觉：移除外圈描边盘、极淡外投影、渐变轨道移至正右侧 150 度同心弧沿弧渐变 |
| 0.2.17 | 扩展弹窗恢复站点授权入口（并行页退役后缺失）；授权即重装规则；模态升级原生 dialog |
| 0.2.16 | 账号中心用户实测七项：空盒操作静默化、重命名原位排序、默认盒哨兵出 DOM（NUL 被解析器吞）、下载问题确认为需重载扩展 |
| 0.2.15 | 用户实测六项：默认盒独立过滤、轮盘浅色磨砂 + 右侧 150 度渐变轨道、主页按钮醒目、盒子操作行为级回归工具 |
| 0.2.13 | 并行管理页桌面化；`_forget_box` 修复（删/重命名后旧名不再保留为幽灵空盒 chip） |
| 0.2.11 | E2E 收口：数据面 2/2 同步、`par.open` 指令面 1s 内开登录页、自动登录成功离开 `/login` |
| 0.2.6 | 两个实锤断点修复：Fernet 键位规范（sign-key = key[0:16]、enc-key = key[16:32]，曾写反导致扩展端全员解密失败）；WebCrypto AES-CBC **自动去 PKCS7 填充**，不可再手工剥离尾字节 |
| 0.2.5 / 0.2.6 | 上游基线校正：同步至上游 v3.13.2（36 文件前移）+ 私有改造清单落档 |
| 0.2.4 | 字段名双语义实锤（`env_base_url` vs `envBaseUrl` → host 恒空 → 扩展静默丢弃全部账号）；扩展游标必须跨服务端重启单调递增（`ext_cmd_seq` 落 settings 表） |
| 0.2.1 | 上游同步：**版本真源改为 manifest**；六平面隔离开启 |
| 0.1.2 | 混合架构落地：桌面写扩展指令（`par.open` + `launch-chrome`），扩展执行面做会话切换与自动填表 |

## 同步上游的操作清单（**2026-09-29 作废**）

> ⚠️ 上游协作已终止，**本节不再执行**。保留它是为了说明一件事：
> 下面这份清单当年是"每次同步都要重跑一遍"的活流程，而**它依赖上游文档存档存在**——
> 存档既已删除，这条流程整体失效。历史原文见
> `git show <2026-09-29 之前的提交>:extensions/quick-login/CHANGELOG.md`。

同步上游新版本时，按顺序执行：

1. 把上游 `README.md` / `docs/*` / `CHANGELOG.md` 的新版原文覆盖进 `docs/archive/`（保持 `UPSTREAM-` 前缀）。
2. 重跑本文件的 ⚠️ 条目：**Page Monitor / Alt+W 撤销**、**`ui/parallel` 保持删除**、
   **声明式全站权限**、**版本真源 = manifest（删掉 `EXT_VERSION` 常量）**。
3. `npm run typecheck && npm run build`，再跑 `tools/verify_extension_boot.py`、
   `tools/verify_extension_isolation.py`、`tools/verify_host_logic.mjs`（三者全绿才算通过）。
4. 更新根侧 `docs/EXTENSION-PLANE.md` 的「与上游差异」与「私有改造史」两节。

> ★ 第 2 步那份"别误加回来"的清单**今天仍然有效**——它已上移到本节之外的 ⚠️ 条目里，
> 与"要不要同步上游"无关。