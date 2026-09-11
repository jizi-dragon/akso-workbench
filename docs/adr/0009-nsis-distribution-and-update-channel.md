# ADR-0009：用 NSIS + 同号发布分发，扩展随包携带并引导装载

- **状态**：已采纳（0.3.1 首个可安装版本；Inno Setup 链路已删除）
- **相关**：[ADR-0003](0003-electron-desktop-shell.md)、[ADR-0008](0008-update-channel-and-proxy.md)、
  [CONTRIBUTING.md](../../CONTRIBUTING.md)、[docs/EXTENSION-INSTALL.md](../EXTENSION-INSTALL.md)

## 背景

分发要解决四件事，且它们互相牵制：

1. **桌面端怎么装**：需要安装器（开始菜单、卸载项、可改安装目录）。
2. **Python 服务端怎么带**：终端用户机器上没有 Python、没有 uv、更没有本项目源码。
3. **浏览器内核怎么带**：托管浏览器需要 chromium 本体；用户机器上很可能没装 Playwright。
4. **浏览器扩展怎么装**：Chrome 在 Windows 上**禁止非商店扩展直接安装**。

历史上踩过的坑（都付出过实际代价）：

- **Inno Setup 链路**（`tools/installer.iss` + `ChineseSimplified.isl`）已废弃删除——中文语言包维护成本高，
  且与 Electron 生态的产物（`latest.yml`、增量检查）不对接。
- **首轮真机构建暴露两个静默断点**：① `workbench/server_entry.py` 在某次重构中丢失，而
  `server.spec` 仍指向它 → PyInstaller 失败（开发态走 venv + uvicorn 完全不受影响，故潜伏很久）；
  ② `desktop/icon.ico` 自首次提交起就是坏文件（二进制被「当文本另存为 Unicode」，有损不可还原）
  → electron-builder 失败 + 托盘图标一直空白。
  这两类问题的共同点是「只在跑完整构建时才暴露，且失败输出被脚本吞掉」。

## 决策

### 1. 安装器 = electron-builder 的 NSIS

`desktop/package.json` 的 `build` 段：`nsis` + `oneClick: false` + `allowToChangeInstallationDirectory: true`
+ `artifactName: AksoWorkbench-${version}-setup.${ext}`。产物连同 `latest.yml` 一起发布
（后者供 [ADR-0008](0008-update-channel-and-proxy.md) 的增量检查）。

### 2. 三样东西全部随包携带（`extraResources`）

| 从 | 到（安装后） | 内容 |
|---|---|---|
| `../dist/AksoServer` | `resources/server` | PyInstaller onedir sidecar（含 **chromium 本体**，即 `ms-playwright`） |
| `../extensions/quick-login/dist` | `resources/extension` | 浏览器扩展构建产物 |

- **sidecar 打包**：`workbench/server.spec` → `dist/AksoServer`（onedir）。
  打包态走 `workbench/server_entry.py`：`AksoServer.exe --server`，**不开浏览器**（UI 由 Electron 主窗承载），
  启动信息落 `%APPDATA%\AksoWorkbench\server.log`（因为壳以 `stdio: 'ignore'` 启动它，现场零输出）。
- **chromium 随包**：`browser_pool` 的工作线程在 `sync_playwright()` **之前**把
  `PLAYWRIGHT_BROWSERS_PATH` 指向 `_MEIPASS/ms-playwright`（顺序错了就找不到）。
  ⚠️ `server.spec` 只有在 `%LOCALAPPDATA%\ms-playwright\chromium*` **存在时**才收集它——
  构建机没装 chromium 会打出没有浏览器的包（见 [AGENT.md](../../AGENT.md) §4 待修项）。

### 3. 扩展：随包 + 应用内引导（不追求「静默一键」）

**实证结论：静默一键装扩展在 Windows 上做不到，不要再试。** 已尝试并失败的路径：

- 拖入 `.crx`：Chrome Windows 拦截非商店扩展。
- `ExtensionInstallForcelist` 策略：**在 HKCU 下普遍不生效**（需 HKLM + 管理员 + 域策略）。
- 自托管 `update_url`：亦常装不上。
- 本仓库**无签名私钥**（只有 manifest 公钥 `key`）→ 无法用既有 ID 重打 CRX。
- **要真·一键只有一条正路：上架商店（可不公开列出）后把商店 ID 写进策略。**

因此采用「**随包携带 + 一键引导**」这条零依赖、零管理员、离线可用的路径：

```
账号中心提示条 / 托盘「安装浏览器扩展…」
  → POST /extension/setup-helper  → 壳 :18767 /extension-setup
  → 打开扩展目录（shell.openPath）+ 打开 chrome://extensions + 弹步骤说明
用户点 4 下：开发者模式 → 加载已解压的扩展程序 → 选目录
之后永久可用；账号由桌面端快照自动下发，无需在扩展里另建
```

升级后的同一套能力复用于 `mode="reload"`（引导点一次「重新加载」），见
[EXTENSION-PLANE.md](../EXTENSION-PLANE.md) §4.4。

### 4. 版本：与扩展同号发布，真源唯一

- 真源 = `pyproject.toml` 的 `version`；`tools/bump.py` **五写**同步：
  `pyproject.toml` + `workbench/__init__.py` + `desktop/package.json`
  + 扩展 `manifest.json` + 扩展工作区 `package.json`（+ `.version.json` 缓存）。
- 规则（方案 A）：`push` = PATCH+1；`build` = MINOR+1 且 PATCH 重置 1（`0.0.2 → 0.1.1 → 0.2.1`）。
- **为什么要同号**：`extStale` 判定建立在「桌面安装包版本 == 扩展版本」之上
  （`GET /extension/health` 比较两者），不同号会让「扩展是否需要重新加载」的判断失去意义。
- **改版本只走 `bump.py`，勿手改单一文件**——漏写 `desktop/package.json` 会导致安装包命名与产物校验错位。

### 5. 前置体检必须存在（对上面那次事故的直接回应）

`tools/verify_packaging.py`（秒级、只读静态核对、8 项）：
P1 spec 入口脚本存在 / P2 入口只用绝对导入 / P3 壳 spawn 路径与 `extraResources` 一致 /
P4 扩展产物存在且版本 == 项目版本且关键产物齐全 / P5 `build.ps1` 具 UTF-8 BOM /
P6 壳全部 `.js` 都在 electron-builder `files` 白名单里。

## 取舍

### 换来什么

- 一个 `.exe` 装完即可用：零 Python、零 Node、零管理员权限、零联网（扩展初次装载也离线）。
- 产物可校验、可自动更新（`latest.yml` + `sha512`）。
- 构建断点被前置体检拦住，不必跑完 PyInstaller（数分钟）才发现入口文件不存在。

### 代价（已接受）

| 代价 | 说明 |
|---|---|
| 包体 349.7 MB | Electron 运行时 + sidecar + chromium + 扩展。用户定稿接受 |
| 扩展需用户点 4 下 | 无法规避（见上实证）；代价是「首次体验多一步」 |
| 扩展目录随桌面端存在 | 卸载桌面端后扩展失效（已在引导文案里说明） |
| 构建链长且成对依赖 | PyInstaller 必须在 electron-builder 之前；扩展重建必须在 bump 之后（否则打进安装包的 manifest 版本停在上一版） |
| 发布是第二步手工 | `build.ps1` 用 `--publish never` 只出产物，发布需 `tools/publish_release.py` 或网页上传 `setup.exe + latest.yml` |

## 后果

- **`files` 是白名单**：新增壳的 `.js` 模块若忘了加进 `desktop/package.json` 的 `build.files`，
  开发态一切正常、装出来的应用 `require` 失败。P6 项已守住。
- **`build.ps1` 的脚本纪律**（PS5.1）：必须 UTF-8 **BOM**（编辑后极易丢失，中文会乱码）；
  禁用 `$ErrorActionPreference='Stop'`（PS5.1 会把 git/uv 写 stderr 的正常进度当终止错误）。
- **`uv sync` 必须在 release commit 之前**：`uv sync` 会把项目版本写进 `uv.lock`，
  顺序反了则 commit 里的 lock 立刻过期、工作区再次变脏（0.3.2 实测踩到）。
- **`uv sync` 必须带 `--extra build`**，否则 PyInstaller 会被移出环境，第 5 步直接失败。
- 打包态与开发态的差异是长期风险源（`frozen=True` 分支、`_MEIPASS` 路径、`stdio: ignore`），
  **改动打包链路后务必真跑一次 `build.ps1`，不要只看开发态**。
