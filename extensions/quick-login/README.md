# Workbench 浏览器扩展（执行面）

> **权威文档不在此处。** 本目录是 Workbench 的一个**普通模块**——它曾经是上游 `quick-login`
> 的 fork，**该协作已于 2026-09-29 终止**：上游文档存档已删除，本项目不再同步上游。
> 扩展侧的原理、协议、验证基线与安全边界**都在根侧**：
> `../../docs/EXTENSION-PLANE.md`（**该文件已于 2026-09-29 删除**）。

## 一句话

扩展 = **执行面**：收桌面指令（`par.open` / `wheel.toggle` / `par.list`），在**用户自己的 Chrome** 里
做多账号会话切换与自动填表（六平面隔离：存储 / AUTH / COOKIE / CACHE / SW·CacheStorage / IndexedDB）。
账号数据的增删改一律归桌面账号中心，扩展侧只在 `src/background/sync.ts` 里对账。

## 目录结构

```
extensions/quick-login/
├── src/                    TypeScript 源（background / content / shared / storage / ui）
├── manifest.json           MV3 清单（**版本真源**，tools/bump.py 五写同步）
├── assets/                 图标（Icon16/48/128）
├── scripts/                build.mjs（esbuild 打包）· probe-page.mjs（配置页结构探针）
├── tsconfig.json           tsc --noEmit 用
├── package.json            依赖与 npm scripts
└── dist/                   构建产物（**不入库**，`npm run build` 生成）
```

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
2. 版本真源 = `manifest.json`（`tools/bump.py` 五写同步）→
   **不要再引入手写版本常量**；`extVersion()` 读 `chrome.runtime.getManifest().version`。
3. 改完必跑三个回归工具（扩展端无单测框架）：
   `tools/verify_extension_boot.py`、`tools/verify_extension_isolation.py`、`tools/verify_host_logic.mjs`。

## 关于 `playwright-core`（`scripts/probe-page.mjs` 用）

它**只服务那一个探针脚本**，钉在 **`1.62.0`**（不是 `^` 范围）。两条理由：

- 探针**不装自己的浏览器**：它用 `findPwChromium()` 找 **Python 侧 `playwright` 装的那份 Chromium**
  （`%LOCALAPPDATA%\ms-playwright\chromium-*`，即 `uv sync` 的产物），再以 `executablePath` 传进去
  ⇒ **Playwright 自己的版本注册表在这里不起作用**，所以升它不会换来"浏览器对得上"。
- 那个 Python 侧的版本就是 **1.62.0**（`pyproject.toml` 的 `playwright>=1.49` 实际解析到它）。
  两边对齐，是为了让 `npm install` + `uv sync` 这套组合**在任何一台机器上复现同一个配对**，
  而不是各自漂到"看起来最新"的那一版。

> 判据：`node -e "console.log(require('playwright-core/package.json').version)"` ==
> `.\.venv\Scripts\python.exe -c "import importlib.metadata as m; print(m.version('playwright'))"`。

## 文档索引

| 文档 | 位置 | 内容 |
|---|---|---|
| **扩展执行面（权威）** | `../../docs/EXTENSION-PLANE.md`（**该文件已于 2026-09-29 删除**） | 六平面隔离原理、与桌面的协议、私有改造史、验证基线、安全边界 |
| **改造与踩坑史** | [`CHANGELOG.md`](CHANGELOG.md) | 本项目对扩展的改造记录（含"哪些改动别误删"清单） |
| 桌面侧配置项 | [`docs/CONFIG.md`](../../docs/CONFIG.md) | 扩展相关端口、`proxy.txt`、`ext_*` settings 键 |
| 桌面侧接口 | [`docs/API.md`](../../docs/API.md) | `/extension/*` 全部端点的权威契约（长轮询与 seq 游标语义） |
| 环境坑与代码地图 | [`docs/PITFALLS.md`](../../docs/PITFALLS.md)、`../../docs/CODEBASE_OVERVIEW.md`（**2026-09-29 曾删除，2026-10-07 重新生成**） | 环境坑、已知问题、验收基线 |
