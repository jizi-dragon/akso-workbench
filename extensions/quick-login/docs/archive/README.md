# 上游文档归档（quick-login 执行面）

> 本目录**只存上游原文，不反映本项目现状**。要了解扩展在本项目里的真实形态与私有改造，
> 请看根侧权威文档 [`docs/EXTENSION-PLANE.md`](../../../../docs/EXTENSION-PLANE.md)。

## 为什么归档

`extensions/quick-login/` 是**上游 quick-login 项目的 fork**：源码按上游版本整批同步进来，
再叠加本项目的私有改造。上游自带 7 份文档 + 1 份 CHANGELOG，与根侧文档是**两套版本体系**：

| | 上游 | 本项目 |
|---|---|---|
| 版本规则 | 独立递增（最后为 `3.13.2`） | 与桌面端同号，`tools/bump.py` 五写同步 |
| 当前版本 | `3.13.2`（2026-09-10 最后一次同步） | `0.3.4` |
| CHANGELOG | 记录扩展自身功能演进 | 记录项目级变更 |

两套文档并存会导致读者分不清「哪份是现状」。按 2026-09-11 的文档重构决定：
**权威内容合并进根侧 `docs/EXTENSION-PLANE.md`，上游原文归档到此目录保留可追溯性。**

## 归档清单

| 归档文件 | 原位置 | 内容 |
|---|---|---|
| `UPSTREAM-README.md` | `extensions/quick-login/README.md` | 六平面隔离总览表 + 快速开始 + 上游文档索引 |
| `UPSTREAM-PROJECT-STATUS.md` | `docs/PROJECT-STATUS.md` | 现状快照：六平面架构表、版本里程碑 v2.5→v3.7、四象限泄漏收敛过程、安全边界 |
| `UPSTREAM-CODEBASE_OVERVIEW.md` | `docs/CODEBASE_OVERVIEW.md` | 代码库导读：架构图、关键模块表、数据流、约定、风险清单 |
| `UPSTREAM-DIAG-GUIDE.md` | `docs/DIAG-GUIDE.md` | 诊断教学：一键导出诊断包 / SW 控制台 / Network 面板三条路线 |
| `UPSTREAM-USER-MANUAL.md` | `docs/USER-MANUAL.md` | 上游用户手册：功能点清单 + 使用说明 |
| `UPSTREAM-BROWSER-ONLY-MULTILOGIN-RESEARCH.md` | `docs/BROWSER-ONLY-MULTILOGIN-RESEARCH.md` | 184 行调研长文：纯扩展多账号并行的方案论证与业界先例（v3.4 时代） |
| `UPSTREAM-DESIGN.md` | `packages/extension/docs/DESIGN.md` | 历史设计文档（v2.1 免密切换时代；其「无法并行」结论已被 v3.5+ 推翻） |
| `UPSTREAM-CHANGELOG.md` | `CHANGELOG.md` | 490 行上游发布史（v2.5→v3.13.2），含每次缺陷的根因定位过程与取证结论 |

## 读归档时注意（已过时之处）

归档原文写于上游 `v3.13.2` 及更早，以下内容在本项目中**已不成立**，勿据以行事：

1. **版本号**：原文反复出现「三处版本号必须一致（根 package.json / manifest.json / `EXT_VERSION`）」。
   本项目已改为**版本真源 = `manifest.json`**，`constants.ts` 的 `extVersion()` 读 `chrome.runtime.getManifest().version`，`EXT_VERSION` 常量已删。
2. **管理页 `ui/parallel/`**：上游的并行管理主页**源码已删**（0.2.13 桌面化、0.2.22 删源码）。
   账号数据的增删改一律归桌面账号中心，扩展侧只在 `sync.ts` 里对账。
3. **`packages/engine/`、`nm-client.ts`、`session.*` 协议**：上游 v3.9.2 已移除引擎；本项目 0.2.23 进一步删掉旧会话模型
   （`session-manager.ts` / `account-registry.ts` / `navigation.ts`，净删 −2,849 行）。归档里的「遗留未清理」清单已过期。
4. **站点授权链路**：0.2.21 起 manifest 改为声明式 `host_permissions: ["<all_urls>"]`，
   逐站点授权整套退役；0.2.22 起弹窗的站点授权 UI 下线（`site-auth.ts` / `par.grantChanged` 保留为**休眠源码**）。
5. **Page Monitor / 最近页面（Alt+W）**：0.2.22 整块撤销（上游同步时会带回来，需再摘一次）。
6. **DNR 缓存平面 `_qlck`**：归档描述为页面层实现（正确），但上游 v3.3 曾用 DNR `redirect.urlTransform`
   ——该字段 Chrome 从未支持，是「网络平面全死」的根因，勿回退。
