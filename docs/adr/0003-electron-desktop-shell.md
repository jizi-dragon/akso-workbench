# ADR-0003：桌面壳选 Electron，且壳只做壳、业务全在服务

- **状态**：已采纳（0.2.x 换代，替代已删除的 pywebview 壳）
- **相关**：[ADR-0002](0002-browser-allocation-policy.md)、[ADR-0009](0009-nsis-distribution-and-update-channel.md)、
  [ADR-0010](0010-monitor-no-dangerous-replay.md)、[CONFIG.md](../CONFIG.md) §1

## 背景

项目早期桌面端是 **pywebview**（纯 Python 单进程，`shell/shell.py`）。它带来的问题：

1. **拿不到可控的浏览器**：pywebview 的 WebView2 实例无法被 Playwright 挂接，
   而「内置 Chromium + CDP 托管」是 [ADR-0002](0002-browser-allocation-policy.md) 的核心依赖。
2. **没有现成的分发/更新体系**：需要自研安装器（当时的 Inno Setup 链路）与更新逻辑。
3. **UI 能力受限**：透明无边框窗口、置顶轮盘、失焦自动关闭这类交互在 WebView2 上代价高。

同时另有一条被明确排除的路线：**Node 作为终端用户的运行时依赖**。
原四个被融合的项目里有三个是 Node 项目，若延续子进程调用，终端用户的安装包就得带 Node 环境。

## 决策

**桌面壳换成 Electron 33，并且严格限定壳的职责为「壳」：**

```
Electron 壳（desktop/main.js，4 文件约 850 行 JS）
  职责：sidecar 生命周期 · 主窗 · 透明轮盘窗 · 托盘 · 全局热键 · 会话窗开户 · 自动更新 · 出网通道
  不做：任何业务逻辑
        ↓ spawn
FastAPI 服务（workbench/，约 8.2k 行 Python）
  全部业务：13 个路由域 + egmp 内核 + SQLite + 浏览器池
```

四条设计底线（引自原架构分析，仍然有效）：

1. **窗口只做壳，业务全在服务**：Electron 主窗/轮盘/会话窗都只加载 FastAPI 页面或空壳分区；
   关闭窗口即整体退出并回收服务（`before-quit` → `killServer()` → `taskkill /T /F`）。
2. **运行时零依赖原仓库与 Node**：akso-cc / akso-auto / quick-login 的能力全部内化到 Python
   （`services/egmp/`）与扩展；原仓库只读参考。终端用户无需 Node
   （桌面壳是 Electron 自带运行时；开发态桌面壳与扩展构建仍需 npm）。
3. **Playwright sync 单线程单实例**（见 [ADR-0006](0006-browser-pool-single-thread.md)）。
4. **凭据不落明文**（见 [ADR-0007](0007-credentials-never-plaintext.md)）。

## 取舍

### 选 Electron 换来什么

- **CDP 可控**：壳在 `app ready` 前设 `remote-debugging-port=18766`，
  `browser_pool` 经 `connect_over_cdp` 接管壳开的会话窗（每账号 `persist:<windowId>` 独立分区）。
- **自动更新免费**：`electron-updater` + GitHub Releases，见 [ADR-0009](0009-nsis-distribution-and-update-channel.md)。
- **UI 能力到位**：无边框透明置顶窗（轮盘 560×640，失焦自动关闭）、托盘、全局热键都是一等 API。
- **安装包自包含**：Electron 运行时随包，用户机器不需要 Node。

### 代价（已接受）

| 代价 | 具体表现 |
|---|---|
| 包体巨大 | 安装包 349.7 MB（Electron 运行时 + PyInstaller sidecar + 内置 chromium + 扩展） |
| 双进程复杂度 | sidecar 必须显式管理生命周期；退出顺序关键（先 kill sidecar 释放文件句柄，再让 updater 拉安装器覆盖文件——**顺序反了安装器会因文件被占用而失败**） |
| 单实例限制 | 只支持单实例；第二实例端口冲突，只出窗口不连服务 |
| 孤儿进程风险 | 壳被强杀（任务管理器）时 sidecar 可能残留并占住 18765——见 [AGENT.md](../../AGENT.md) 环境坑 #20 |
| 开发态两套入口 | 打包态 `AksoServer.exe --server`（不开浏览器、日志落 `server.log`）；开发态 venv + uvicorn（自动开浏览器）。`server_entry.py` 与 `main.py` 是两个入口 |

## 后果

- **端口分工成了契约**（[API.md](../API.md) §0/§16）：`:18765` 业务、`:18766` CDP、`:18767` 壳控制服务。
- **跨进程状态通道只能是文件**：壳每 15s 把更新状态原子写入 `%APPDATA%\AksoWorkbench\shell-state.json`，
  Python 侧 `GET /api/update` 读它（超 60s 判 `live=false`）。
  为什么不每次 HTTP 探壳：账号中心每 3s 刷新，读本地文件更便宜，且壳不在时能优雅降级。
- **壳的每个源文件都必须在 electron-builder 的 `files` 白名单里**，否则开发态一切正常、
  装出来的应用 `require` 失败。这条已由 `tools/verify_packaging.py` 的 P6 项守住。
- **已删除、勿恢复**：`shell/shell.py`（pywebview 壳）、Inno Setup 链路（`tools/installer.iss`、
  `tools/ChineseSimplified.isl`）、`tools/start-desktop.vbs`。
