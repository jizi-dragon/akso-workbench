# QuickLogin · 多账号并行

纯浏览器 Chrome MV3 扩展：在**同一个浏览器窗口**内，对内部低代码平台（`example-config.example.com`，无状态 JWT Bearer 鉴权）**并行在线多个账号**。每个账号一个独立标签页，账号间呈现层与网络层全链路隔离；账号轮盘（Alt+Q / Ctrl+Shift+Q）一键切换，密码 AES-GCM 加密存本机。

## 它解决什么问题

该平台的登录态是多份共享资源的叠加（localStorage、Cookie jar、HTTP 缓存、IndexedDB、Service Worker），同 origin 多账号天然互相污染——「先开谁，谁的身份分发给别人」。QuickLogin 用**六平面隔离**逐层切断：

| # | 平面 | 机制 | 实现位置 |
|---|---|---|---|
| 1 | 存储 | localStorage 重定向到 `__ql_ns_<accountId>__` 命名空间；`document.cookie` 虚拟化为账号「Cookie 袋」 | `content/shield-main.ts` |
| 2 | AUTH | DNR 按 tabId 强制改写 `Authorization: Bearer <token>`（xhr / websocket / sub_frame，host+父域） | `background/core/tab-rules.ts` |
| 3 | COOKIE | 出站 Cookie 头**按账号回放**（登录时点经 `chrome.cookies` 采集含 HttpOnly 的全量快照，身份类键过滤；空快照回退剥离） | `tab-rules.ts` + `parallel-session.ts` |
| 4 | CACHE | 同源 GET 请求追加 `_qlck=t<tabId>`，共享 HTTP 缓存按标签硬分区（页面层实现——Chrome DNR 无 `urlTransform`） | `shield-main.ts` |
| 5 | SW/Cache | 拦截站点 `serviceWorker.register` + 注销既有注册；`CacheStorage` 按账号命名空间键控 | `shield-main.ts` |
| 6 | IndexedDB | `indexedDB.open/deleteDatabase/databases()` 按账号前缀化（平台把 `isAdmin`+菜单树缓存在共享 IDB，是四象限串号的直接载体） | `shield-main.ts` |

> 历史教训：v3.3 的 CACHE 平面曾用 DNR `redirect.urlTransform` 实现——该字段 Chrome 从未支持（Firefox 专属），且 `updateSessionRules` 是原子批量，导致同批 COOKIE/AUTH 规则全部被拒、网络平面全死。v3.5 起改为页面层实现 + **逐条安装降级**。

## 快速开始

```bash
npm install        # 安装依赖
npm run build      # esbuild → dist/（三处版本号保持一致）
npm run typecheck  # tsc --noEmit
```

装载测试：`chrome://extensions` → 开发者模式 → 「加载已解压的扩展程序」→ 选 `dist/`。
**更新代码后务必在扩展卡片点「重新加载」**（Chrome 会缓存扩展 Service Worker 脚本，直接重启浏览器也可能复用旧脚本）。

## 文档索引

| 文档 | 内容 |
|---|---|
| [`docs/USER-MANUAL.md`](docs/USER-MANUAL.md) | **用户手册**：功能点清单 + 使用说明 |
| [`docs/PROJECT-STATUS.md`](docs/PROJECT-STATUS.md) | **现状快照**：六平面架构表、版本里程碑、安全边界 |
| [`docs/CODEBASE_OVERVIEW.md`](docs/CODEBASE_OVERVIEW.md) | 代码库导读：架构、关键模块、数据流、约定、风险 |
| [`CHANGELOG.md`](CHANGELOG.md) | 版本明细（含每轮缺陷的根因定位过程） |
| [`docs/BROWSER-ONLY-MULTILOGIN-RESEARCH.md`](docs/BROWSER-ONLY-MULTILOGIN-RESEARCH.md) | 历史调研：纯扩展多账号并行的方案论证与先例（v3.4 时代） |
| [`packages/extension/docs/DESIGN.md`](packages/extension/docs/DESIGN.md) | 历史设计文档（v2.1 免密切换时代；其「无法并行」结论已被推翻） |

## 已知边界

- **主动提权方向无法在客户端根治**：把普通用户抬成管理员需「接管服务端应答」，客户端篡改会导致白屏（无回退渲染路径，已实证）。真正的权限边界应在平台服务端（管理侧读接口未过滤、单设备登录未启用），建议向平台方反馈。
- **Web Worker 内打开的 IndexedDB** 不受主世界补丁（待观测）。
- Cookie 快照仅登录时点采集一次；若服务端在会话中轮转会话标识，该账号需重新登录刷新快照（JWT 主体不受影响）。
- 轮盘页面浮层需站点授权；未授权站点按设计降级为独立小窗。

---

版本号约定：根 `package.json`、`packages/extension/manifest.json`、`src/shared/constants.ts` 的 `EXT_VERSION` 三处必须一致。
