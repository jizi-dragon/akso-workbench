# Changelog · 扩展执行面（本项目私有改造）

> 本文件只记录**本项目对上游 quick-login 的改造**，不含上游自身的功能演进史。
> 上游完整发布史（v2.5 → v3.13.2，490 行，含每次缺陷的根因定位与取证结论）见
> [`docs/archive/UPSTREAM-CHANGELOG.md`](docs/archive/UPSTREAM-CHANGELOG.md)。
>
> **为什么拆两份**：扩展的版本号已与本项目统一（`tools/bump.py` 五写同步，当前 `0.3.4`），
> 而上游是独立递增（最后 `3.13.2`）。混在一份里会导致读者无法判断某条记录属于谁，
> 也无法在上游发布新版时做差异比对。**同步上游新版本时，请先读本文件确认要重新摘除哪些改造。**
>
> 格式：本项目扩展相关变更按根 `CHANGELOG.md` 的版本号组织；凡「上游同步会带回来」的条目
> 标注 ⚠️，表示下次同步上游时必须重新执行一次摘除。

## 未发布

（无）

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

## 同步上游的操作清单

同步上游新版本时，按顺序执行：

1. 把上游 `README.md` / `docs/*` / `CHANGELOG.md` 的新版原文覆盖进 `docs/archive/`（保持 `UPSTREAM-` 前缀）。
2. 重跑本文件的 ⚠️ 条目：**Page Monitor / Alt+W 撤销**、**`ui/parallel` 保持删除**、
   **声明式全站权限**、**版本真源 = manifest（删掉 `EXT_VERSION` 常量）**。
3. `npm run typecheck && npm run build`，再跑 `tools/verify_extension_boot.py`、
   `tools/verify_extension_isolation.py`、`tools/verify_host_logic.mjs`（三者全绿才算通过）。
4. 更新根侧 `docs/EXTENSION-PLANE.md` 的「与上游差异」与「私有改造史」两节。