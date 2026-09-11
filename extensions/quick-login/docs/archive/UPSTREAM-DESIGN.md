> ⚠️ **已归档（{date}）· 上游历史文档，勿作为本项目现状依据**
>
> 本文原属上游 quick-login 项目（随源码同步进本仓库），描述的是**上游 v3.13.2 及更早**的形态。
> 本项目（Akso Workbench）已在其上叠加私有改造，**权威文档请看根侧 `docs/EXTENSION-PLANE.md`**。
> 保留原文仅为追溯上游设计意图与取证结论（尤其是六平面隔离的攻防过程）。
>
> 上游文档索引见 `docs/archive/README.md`。

---

# SessionBox 多账号快速切换 — 设计文档

> ⚠️ **历史文档（v2.1.0 时代，2026-08-26）**：本文 §3「纯扩展无法实现同站多账号并行在线」
> 的结论已被 v3.x 推翻——DNR `tabIds` session 规则 + MAIN 壳注入 + 六平面隔离（v3.7.2）
> 已实现并行在线。现状请读根目录 `docs/PROJECT-STATUS.md` 与 `docs/CODEBASE_OVERVIEW.md`；
> 方案论证史见 `docs/BROWSER-ONLY-MULTILOGIN-RESEARCH.md`。

> 状态：`已实现（免密快速切换版 v2.1.0）`（2026-08-26）
> 本文档与最终源码一一对应；实现阶段若改动行为，必须同步更新本文件。

---

## 1. 背景与目标

旧仓库 `d:\ai_assistant\SessionBox` 是 SessionBox 商业扩展 **v1.3.20（2020-01 构建快照）** 的还原产物，存在以下硬伤，不做补丁式修复而是整体重写：

- `manifest_version: 2`，Chrome 已停用，现代浏览器基本无法加载。
- 依赖 `webRequestBlocking` + `proxy` + `<all_urls>` + Firebase 第三方同步，权限过大、隐私与攻击面大、且依赖在线后端。
- `background.js` 等为 webpack 压缩产物，无源码、无构建配置、无测试，不可维护。
- 靠重写 `document.cookie` 单点隔离，`HttpOnly`/ServiceWorker/严格 CSP 场景会失效。

**要解决的需求：**

> 单浏览器内，对同一网站支持多个账号；切换账号时**免输密码**；切换后**通过标签页标题显示账号名**以区分不同账号。

## 2. 关键决策（已与需求方对齐）

| 决策点 | 结论 | 理由 |
|---|---|---|
| 目标浏览器 | **Chrome / Edge（MV3）** | 覆盖用户群最大 |
| 产品形态 | **纯浏览器扩展** | 贴合轻量需求，可快速落地 |
| 账号能力 | **免密快速切换（串行）**，非「同时在线」 | 见 §3 实测结论，纯扩展无法实现同站多账号并行在线 |
| 持久化 | IndexedDB **本地优先** | 不把多账号凭证外发给第三方 |
| 凭证安全 | 账号密码 **AES-GCM 加密**后落库 | 仅设备绑定，见 §6 |
| 权限策略 | **最小权限 + 按站点动态授权** | 彻底移除 `<all_urls>` / proxy / webRequestBlocking / Firebase |

非本期范围（避免过度工程）：指纹伪装、IP/代理替换、桌面伴随进程、团队协作、同站多账号并行在线。

## 3. 为什么不是「多账号同时在线」（实测结论）

初版曾按「Cookie 罐焦点仲裁 + localStorage 虚拟化」实现多账号并行隔离，但用 Playwright 对目标站点 `example-config.example.com` 实测后，发现该方案在架构上必然失效。关键证据：

1. **鉴权不用 Cookie，用 Bearer token**。所有 `/api/*` 请求带 `Authorization: Bearer <JWT>` 头，Cookie 里的 `__auth_token__` 只是 token 的持久化副本，不是鉴权载体。因此「切换 Cookie 罐」对鉴权无效。

2. **服务端不互踢**。dev-user 的 token 在 T0601 登录后单独调用 API 仍返回 `200`，两个 token 各自独立有效。互踢并非服务端行为。

3. **前端有「会话失效自检」**。清除 Cookie token 后，页面**未刷新即自动跳转登录页**。也就是说，扩展「切 Cookie」的动作被前端识别为「登录态失效」，反而把别的账号踢下线。

4. **共享存储被最后登录者覆盖**。同 origin 的 `localStorage.__auth_user__`、`__device_fp__` 全局共享，后登录账号覆盖前一个。

结论：目标站点是「无状态 JWT + 前端内存 token + Bearer 鉴权 + 前端失效自检」的 SPA。纯 Chrome 扩展无法为每个账号提供独立 Cookie 罐与独立 localStorage（Chrome 无 Firefox 容器 API，`declarativeNetRequest` 不支持按 tabId 匹配）。因此产品定位收敛为**免密快速切换**——每次切换走正规「登出 → 自动登录」，服务端与前端都认，全程免输密码。

## 4. 总体架构

```
┌────────────────────── 浏览器外壳（MV3） ──────────────────────┐
│                                                              │
│  【 Background Service Worker 】                             │
│   ┌───────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│   │Session-   │  │Account-      │  │ Navigation           │ │
│   │Manager    │  │Registry      │  │ tabId <-> sessionId   │ │
│   │(CRUD/持久化)│  │(账号名/用户名) │  │ + 切换编排            │ │
│   └─────┬─────┘  └──────┬───────┘  └──────────┬───────────┘ │
│         │               │                     │              │
│   ┌─────▼───────────────▼─────────────────────▼───────────┐  │
│   │  切换账号 = 登出（清 Cookie + localStorage）→ 导航登录页  │  │
│   │            → 自动登录（凭证解密填表）→ 改标签标题         │  │
│   └─────────────────────────┬───────────────────────────┘  │
│                             │ IPC (chrome.runtime)          │
├─────────────────────────────┼──────────────────────────────┤
│    【 Content Script 】                                       │
│   title-hook.ts（标题维持）│ auto-login.ts（自动填表提交）    │
└─────────────────────────────┼──────────────────────────────┘
                              │
                网站登录页 → 登录成功 → 首页
```

## 5. 核心流程：免密快速切换

### 5.1 切换账号（`navigation.switchAccount`）

```
点击弹窗某账号 → ① 登出（清 localStorage + 删该 host 全部 Cookie）
             → ② 导航到 https://{host}/login
             → ③ 标签标题改写为账号名
             → ④ 用该账号已保存凭证自动填表提交（免输密码）
```

- **登出**：`chrome.scripting.executeScript` 清 `localStorage`（MAIN world），`chrome.cookies` 删该 host 全部 Cookie。删 Cookie 是失去登录态的关键；localStorage 会在登录时被覆盖，这里一并清空以干净登出。
- **自动登录**：凭证经 `credentials.decryptCredentials` 解密后，缓存至 `chrome.storage.session`（按 tabId），由内容脚本就绪后主动索取（`sb:autoLoginRequest`）或即时下发（`sb:autoLogin`）。
- **标题改写**：`setTabTitle`（executeScript 权威写入）+ `sb:setTitle`（通知 title-hook 持续维持，扛住 SPA 内部导航重置标题）。

### 5.2 标签复用

`tabId ↔ sessionId` 绑定表（`chrome.storage.session` 持久化，service worker 重启可恢复）用于**复用已打开标签**，避免切换同一账号时盲目新建标签页。

### 5.3 自动填表（`auto-login.ts`）

密码框位于同源 `srcdoc` iframe 内（Ant Design 登录表单），`<all_urls>` 的 content script 不注入 `about:srcdoc` frame，因此由**顶层 frame 直接访问 `iframe.contentDocument` 填密码**；用户名、协议勾选、登录按钮在顶层直接处理。React 受控组件用原生 `HTMLInputElement.prototype.value` setter + `input`/`change` 事件触发。

## 6. 数据模型与安全

```
Session {
  id: string
  name: string              // 展示名
  accountAlias: string      // 账号名/用户名（进标签标题）
  color: string
  siteHost: string          // 绑定的站点 host
  credentials?: EncryptedCredentials  // AES-GCM 加密的账号密码
  createdAt, updatedAt: number
}
```

- **持久化**：IndexedDB（`db.ts`），仅 `sessions` 一张 store。
- **凭证加密**：`credentials.ts` 用 `crypto.subtle` AES-GCM 加密账号密码（各字段独立 IV），密钥由 `chrome.storage.local` 中的随机种子经 PBKDF2 派生。该方案为**设备绑定**——密钥种子存于本机扩展存储，不跨设备同步，不触碰站点登录态之外的任何数据。

## 7. 权限与安全（最小权限）

```json
{
  "manifest_version": 3,
  "permissions": ["tabs", "scripting", "cookies", "storage", "contextMenus"],
  "host_permissions": [],
  "optional_host_permissions": ["*://*/*"]
}
```

- 彻底移除：`proxy`、`webRequest`、`webRequestBlocking`、`<all_urls>`、`unlimitedStorage`、Firebase 相关 CSP 白名单。
- 站点访问由用户**显式添加**后动态授权（快捷键 `Ctrl+Shift+E` 或右键菜单「将当前站点添加为会话站点」），默认不触碰任何网站。
- UI 蓝白色系：主色 `#1E6FFF`，浅色背景 `#F5F8FF`，纯白卡片；CSS 自定义属性集中管理，杜绝硬编码色值。

## 8. 目录结构（当前态）

```
docs/DESIGN.md
manifest.json
src/
  background/
    service-worker.ts       # 消息路由 + 事件装载
    core/
      session-manager.ts    # 会话 CRUD
      account-registry.ts   # alias 缓存
      site-auth.ts          # 站点动态授权 + 快捷键/右键菜单入口
      credentials.ts        # 账号密码 AES-GCM 加密存储
      navigation.ts         # tabId↔sessionId 绑定 + 切换账号编排
    tabs/
      tab-title.ts          # 标签标题权威写入
  content/
    title-hook.ts           # 标题持续维持（MutationObserver）
    auto-login.ts           # 登录表单自动填充（all_frames，覆盖 iframe 密码框）
  storage/
    db.ts                   # IndexedDB 会话存取
  ui/
    send.ts                 # 后台调用助手
    popup/                  # 弹窗：会话列表（蓝白色系）
    options/                # 设置页：站点与会话管理
    theme.css               # 蓝色系设计令牌
  shared/
    types.ts
    messages.ts             # IPC 协议
    constants.ts            # storage 键、消息 type、配色
package.json
tsconfig.json
scripts/build.mjs           # esbuild 构建
build/                      # 构建产物（dist/，不提交）
```

## 9. 已知限制与升级路径

| 限制 | 触发条件 | 升级路径 |
|---|---|---|
| 无法同站多账号并行在线 | 需要同时操作同一站点的多个账号 | 桌面伴随进程（每账号独立浏览器 profile，对标 Multilogin / AdsPower） |
| 无指纹/网络层隔离 | 平台利用设备指纹关联封号 | 升级桌面方案 + 每账号代理 |
| 自动填表依赖站点登录页结构 | 登录表单非标准 Ant Design 布局 | 用户可录制选择器 + 启发式识别 |

---

## 待办（已收敛为免密快速切换）

- [x] 定位互踢根因：Bearer token + 前端失效自检 + 共享存储覆盖（Playwright 实测）
- [x] 产品定位收敛：免密快速切换（串行），废弃 Cookie 罐焦点仲裁与存储虚拟化
- [x] 切换流程：登出 → 导航登录页 → 自动登录 → 标题改写
- [x] 清理废弃模块：cookie-fence / storage-fence / virtual-storage
- [ ] Phase 2（可选增强）：会话导入导出、登录页选择器录制
