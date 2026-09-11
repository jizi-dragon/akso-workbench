# QuickLogin 执行面（本项目 fork）

> **权威文档不在此处。** 本目录是上游 quick-login 的 fork，本体代码 + 本项目私有改造都在这里，
> 但**说明文档已合并到根侧**：[`docs/EXTENSION-PLANE.md`](../../docs/EXTENSION-PLANE.md)。

## 一句话

扩展 = **执行面**：收桌面指令（`par.open` / `wheel.toggle` / `par.list`），在**用户自己的 Chrome** 里
做多账号会话切换与自动填表（六平面隔离：存储 / AUTH / COOKIE / CACHE / SW·CacheStorage / IndexedDB）。
账号数据的增删改一律归桌面账号中心，扩展侧只在 `src/background/sync.ts` 里对账。

## 常用命令

```bash
npm install        # 首次
npm run typecheck  # tsc --noEmit（零错误才算通过）
npm run build      # esbuild → dist/
npm run watch      # 开发态增量构建
```

装载：`chrome://extensions` → 开发者模式 → 「加载已解压的扩展程序」→ 选 `dist/`。
**更新 dist 后必须在扩展卡片点「重新加载」**——Chrome 会缓存扩展 Service Worker 脚本。

## 改扩展的硬约束（三条，来自实锤事故）

1. `src/shared/constants.ts` 会被 **MAIN world**（`content/shield-main.ts`）import →
   顶层**禁止**任何扩展 API 求值（`extVersion()` 因此写成函数）。
2. 版本真源 = `packages/extension/manifest.json`（`tools/bump.py` 五写同步）→
   **不要再引入手写版本常量**；`extVersion()` 读 `chrome.runtime.getManifest().version`。
3. 改完必跑三个回归工具（扩展端无单测框架）：
   `tools/verify_extension_boot.py`、`tools/verify_extension_isolation.py`、`tools/verify_host_logic.mjs`。

## 文档索引

| 文档 | 位置 | 内容 |
|---|---|---|
| **扩展执行面（权威）** | [`docs/EXTENSION-PLANE.md`](../../docs/EXTENSION-PLANE.md) | 六平面隔离原理、与桌面的协议、私有改造史、验证基线、安全边界 |
| **私有改造 CHANGELOG** | [`CHANGELOG.md`](CHANGELOG.md) | 本项目对上游的改造记录（含「同步上游时要重新摘除」清单） |
| 上游文档归档 | [`docs/archive/`](docs/archive/README.md) | 上游 7 份文档 + 490 行上游 CHANGELOG 原文（只作追溯） |
| 桌面侧配置项 | [`docs/CONFIG.md`](../../docs/CONFIG.md) | 含扩展相关端口、`proxy.txt`、`ext_*` settings 键 |
| 桌面侧接口 | [`docs/API.md`](../../docs/API.md) | `/extension/*` 全部端点的权威契约（长轮询与 seq 游标语义） |
| 维护者手册 | [`AGENT.md`](../../AGENT.md) | 环境坑（扩展相关 8 条）、已知问题、验收基线 |