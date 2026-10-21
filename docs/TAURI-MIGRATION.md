# Tauri 迁移与自动更新

> 状态：**Phase 0 已完成；Phase 1 骨架已落地；Electron 已退役**（2026-10-19）。
> `src-tauri/` 是唯一桌面壳（Tauri 2 + Python sidecar + NSIS + updater 配置），
> 本机 release 构建：sidecar 60.7 MB、安装包 26.9 MiB、`.sig` 已生成。
> **剩余收尾**：Tauri updater 的检查/提示/安装 UI、Windows Authenticode、内部源发布与 CI。
> `desktop/` 与 electron-builder / electron-updater 已删除。

## 0. 已经落地的文件

| 路径 | 作用 |
|---|---|
| `src-tauri/src/main.rs` | 启动 Python sidecar、选空闲端口、健康检查、splash→主窗、单实例、外链交系统浏览器、退出杀进程树 |
| `src-tauri/tauri.conf.json` | identifier / NSIS per-user / updater 公钥与内部 HTTPS endpoint / resources |
| `src-tauri/Cargo.toml` | Tauri 2、updater、single-instance、opener |
| `src-tauri/ui/index.html` | 本地启动页（不依赖 Python） |
| `tools/build_tauri.ps1` | 版本检查 → PyInstaller sidecar → Tauri build → latest.json |
| `tools/tauri_update_manifest.py` | 从 `.exe.sig` 生成 `latest.json`，拒绝 HTTP/版本不符/缺签名 |
| `tests/test_tauri_config.py` | 配置/版本/资源/依赖静态契约 |
| `tests/test_tauri_update_manifest.py` | 清单字段、HTTPS、版本错配、缺签名、CLI |


## 1. 约束（用户已确认）

- 未来不再需要 UI 自动登录 / 页面抓取 / 截图；
- 暂时不需要托盘、自动更新，但**一定需要桌面壳**；
- 单机单用户，每人电脑上只有一个实例；
- **未来需要自动更新**；
- 版本号统一变量，以提交 GitHub 升版为主；
- 更新源用**公司内部 HTTPS 静态源**，不要求仓库公开。

## 2. 目标架构

```text
Workbench.exe（Tauri 2 / Rust）
  ├─ WebView2 窗口
  ├─ 更新器（tauri-plugin-updater）
  ├─ 单实例 / 窗口 / 外链
  └─ Python sidecar（resources/server/）
       ├─ FastAPI（127.0.0.1）
       ├─ SQLite（%APPDATA%\Workbench）
       └─ MCP / 沙箱子进程
```

- **整包更新**：Tauri 壳 + Python sidecar + 静态 UI 同一版本，不单独更新 Python；
- sidecar 由 Tauri 拉起；更新安装前通过 `on_before_exit` 停 sidecar 并用 Job Object 兜底；
- 业务 API 仍是 FastAPI；不重写 Python 能力。

## 3. 版本真源

- 唯一手改点：仓库根目录 [`VERSION`](../VERSION)；
- `python tools/bump.py push|build` 同步 `pyproject.toml`、`workbench/__init__.py`、
  `desktop/package.json`、`desktop/package-lock.json`、`uv.lock`、`.version.json`；
  Tauri 工程建立后还会同步 `src-tauri/tauri.conf.json` 与 `Cargo.toml`；
- CI 在 `uv sync` 之前跑 `python tools/bump.py check`；
- 版本升级以提交 / tag 为主；运行时更新器只读发布产物，不自行决定版本。

## 4. 自动更新（已落地的骨架）

使用 **Tauri v2 `tauri-plugin-updater`**：

1. 已生成更新密钥（私钥在 `%USERPROFILE%\.tauri\workbench.key`，**不在仓库**；公钥已写入
   `src-tauri/tauri.conf.json`）；
2. Windows Authenticode 代码签名与 updater 签名分开管理；
3. 已配置 `createUpdaterArtifacts: true`、Windows NSIS per-user；
4. 内部 HTTPS 静态源（示例）：
   - `latest.json`（不缓存）
   - `<version>/Workbench_<version>_x64-setup.exe`
   - 同名 `.sig`
5. 用 `python tools/tauri_update_manifest.py` 生成 `latest.json`：
   ```powershell
   python tools\tauri_update_manifest.py `
     --artifact src-tauri\target\release\bundle\nsis\Workbench_0.4.1_x64-setup.exe `
     --base-url https://updates.example.com/workbench/0.4.1 `
     --notes "..." `
     --output src-tauri\target\release\bundle\nsis\latest.json
   ```
6. 构建一键命令：
   ```powershell
   powershell -ExecutionPolicy Bypass -File tools\build_tauri.ps1 `
     -UpdateBaseUrl https://updates.example.com/workbench/0.4.1
   ```
   **必须替换** `src-tauri/tauri.conf.json` 里的 `https://updates.example.com/...`；
7. 客户端：启动后延迟检查 + 每 6 小时；后台下载；退出时安装；无托盘时用原生弹窗/应用内提示；
8. 更新安装前停止 sidecar；安装失败保留旧版本，数据库迁移前备份。

> 当前 `main.rs` 只初始化 updater 插件并保留构建/签名链路；**还没有**接“检查→提示→安装”
> 的 UI 与 `on_before_exit`（Phase 1 收尾项）。不会在未确认的情况下自动替换应用。

## 5. Electron 退役后的一次性事项

- **已删除** `desktop/`、electron-builder、electron-updater、固定端口控制服务与 `/api/update`；
- 已装旧 Electron 的用户：直接安装 Tauri 包覆盖；数据目录仍是 `%APPDATA%\Workbench`；
- 旧 Electron 进程若还在运行，先在任务管理器退出（它仍可能占着 18765）；
- 旧 `runtime/browser-states/`、`runtime/monitor/`、`Partitions/` 不再产生，可手工清理；
- 安装器已切到 Tauri NSIS per-user；版本号由 `bump.py` 统一写。

## 6. 验收判据（Phase 1）

- [ ] v1 安装 → v2 检查/下载/退出安装/重启成功；
- [ ] 数据库、凭据、MCP、沙箱数据保留；
- [ ] 更新前 sidecar 与子进程全部退出，安装器无文件占用；
- [ ] 篡改 `.sig`/安装包被拒绝；
- [ ] 断网、代理、下载中断可重试；
- [ ] WebView2 缺失有明确提示；
- [ ] 每用户安装不需要管理员权限；
- [ ] 更新日志落在 `%APPDATA%\Workbench\logs`。
