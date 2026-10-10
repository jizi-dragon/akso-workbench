# ADR-0003：桌面壳、更新通道、NSIS 分发、品牌中立化

> **合订说明（2026-10-07）**：本文是**主题合订本**，吸收 **原 ADR-0003 ＋ 0008 ＋ 0009 ＋ 0013**。
> 仓库在 2026-10-07 把 22 篇 ADR 按主题合并成 8 篇（用户授权：「快速合并历史的 ADR」
> 「未来的开发存在变更是正常的」）。**每节末尾的「（原 ADR-00XX）」标明该条决策当时出自哪一篇。**
> 合并只做组织与去重，**决策措辞未改写**。
> 合并口径见 [AGENTS.md](../../AGENTS.md) §5。

- **状态**：已采纳（Electron 33 换代 / 0.3.3 更新状态机 / 0.3.1 首个可安装版本 / 2026-09-16 更名）
- **决策者**：用户定稿（更名原话：「更名，剥离 akso 的信息，按照 workbench 进行后续发展
  （逐渐将项目转变成可复用的工作台，核心差距就在于插件/tools 功能）」）
- **影响面**：`desktop/**`（壳 4 文件约 850 行 JS）、`.github/workflows` + `tools/build.ps1`、
  `tools/verify_packaging.py`、`tools/bump.py`；更名面 **58 个文件 / 200 处字符串
  + 6 处协议与持久化标识**（会静默坏数据，见 §2.4）
- **相关**：[ADR-0002](0002-browser-allocation-policy.md)（浏览器与扩展两面）、
  [ADR-0011](0011-port-governance.md)（端口固定）、[CONFIG.md](../CONFIG.md) §1/§2/§5、
  [API.md](../API.md) §14、[VERSIONING.md](../VERSIONING.md)、[PITFALLS.md](../PITFALLS.md)
- **不做**：不改仓库名（用户决定「暂不改」）、不改上游项目名与平台适配层（见 §2.4 D-4）

---

## 1. 背景

### 1.1 pywebview 壳的三条硬伤（原 ADR-0003）

项目早期桌面端是 **pywebview**（纯 Python 单进程，`shell/shell.py`）。它带来的问题：

1. **拿不到可控的浏览器**：pywebview 的 WebView2 实例无法被 Playwright 挂接，
   而「内置 Chromium + CDP 托管」是 [ADR-0002](0002-browser-allocation-policy.md) 的核心依赖。
2. **没有现成的分发/更新体系**：需要自研安装器（当时的 Inno Setup 链路）与更新逻辑。
3. **UI 能力受限**：透明无边框窗口、置顶轮盘、失焦自动关闭这类交互在 WebView2 上代价高。

同时另有一条被明确排除的路线：**Node 作为终端用户的运行时依赖**。
原四个被融合的项目里有三个是 Node 项目，若延续子进程调用，终端用户的安装包就得带 Node 环境。

### 1.2 两块独立的问题被同一次真机验证串在一起（原 ADR-0008）

**问题 1：用户不知道怎么升级。** 0.3.1 交付了首个可安装版本（349.7 MB NSIS 安装包），
但升级路径是「用户自己去 GitHub 找新版下载」。用户定稿的期望是**零打扰自动更新**。

**问题 2：能发现新版，却下载不下来（0.3.4 真机实测的诊断）。** 逐跳实测更新链路：

```
github.com/<o>/<r>/releases.atom          查版本，1.9s，稳定
  → github.com/…/releases/download/…      302
  → release-assets.githubusercontent.com  CDN，很快
```

**本机直连 `github.com` 是间歇性的**：同一 URL 有时 1.1s 成功、有时 20s 超时（都出现过）。
而 **Chromium 默认按 `mode:'system'` 解析代理，实测并不会真的用上系统代理**——
注册表里 `ProxyEnable=1` 摆着，请求仍直连并超时。于是表现为「检查得到版本、下载不动」。

代理（Clash 之类）**稳定但慢**，所以把它当默认通道也是错的。

### 1.3 分发要解决四件事，且它们互相牵制（原 ADR-0009）

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

### 1.4 `akso` 长在了四类地方（原 ADR-0013）

`akso` 是**首个适配平台的厂商名**，不是这个工作台的名字：

| 类别 | 例子 | 为什么是问题 |
|---|---|---|
| 产品标识 | `AksoWorkbench` / `akso-workbench` / `com.akso.workbench` / `AksoServer.exe` | 换个平台实施就要改一遍代码，改名成本随代码量线性上涨 |
| 部署契约 | `%APPDATA%\AksoWorkbench`、`~/.akso-workbench` | 用户数据目录绑死厂商名，换平台 = 换数据目录 = 看起来"账号全没了" |
| 环境变量 | `AKSO_PROXY` / `AKSO_UPDATE_OVERRIDE` / `AKSO_NO_PROXY` | 部署脚本、快捷方式、排障手册全被厂商名污染 |
| 协议/持久化标识 | 备份 `format`、快照 `format`、扩展存储键 | **最危险的一类**：改错就是静默丢账号（见 §2.4） |

判断标准（本次实际使用的分界线）：**"这是我们的名字"还是"别人的名字"**。
`akso-cc` / `akso-auto` 是**来源项目**的名字（[ADR-0005](0005-native-internalization-of-four-projects.md) 的引用真源），
`services/egmp` 是**平台适配层**，它们描述的是事实，不是我们的品牌 —— 保留（§2.4 D-4）。

---

## 2. 决策

### 2.1 桌面壳 = Electron 33，且壳只做壳（**原 ADR-0003**）

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
3. **Playwright sync 单线程单实例**（见 [ADR-0002](0002-browser-allocation-policy.md) §2.3）。
4. **凭据不落明文**（见 [ADR-0007](0007-credentials-sandbox-and-trust.md)）。

#### 选 Electron 换来什么（原 ADR-0003）

- **CDP 可控**：壳在 `app ready` 前设 `remote-debugging-port=18766`，
  `browser_pool` 经 `connect_over_cdp` 接管壳开的会话窗（每账号 `persist:<windowId>` 独立分区）。
- **自动更新免费**：`electron-updater` + GitHub Releases，见 §2.2。
- **UI 能力到位**：无边框透明置顶窗（轮盘 560×640，失焦自动关闭）、托盘、全局热键都是一等 API。
- **安装包自包含**：Electron 运行时随包，用户机器不需要 Node。

#### 代价（已接受）（原 ADR-0003）

| 代价 | 具体表现 |
|---|---|
| 包体巨大 | 安装包 349.7 MB（Electron 运行时 + PyInstaller sidecar + 内置 chromium + 扩展） |
| 双进程复杂度 | sidecar 必须显式管理生命周期；退出顺序关键（先 kill sidecar 释放文件句柄，再让 updater 拉安装器覆盖文件——**顺序反了安装器会因文件被占用而失败**） |
| 单实例限制 | 只支持单实例；第二实例端口冲突，只出窗口不连服务 |
| 孤儿进程风险 | 壳被强杀（任务管理器）时 sidecar 可能残留并占住 18765——见 [PITFALLS.md](../PITFALLS.md) 环境坑 #20 |
| 开发态两套入口 | 打包态 `WorkbenchServer.exe --server`（不开浏览器、日志落 `server.log`）；开发态 venv + uvicorn（自动开浏览器）。`server_entry.py` 与 `main.py` 是两个入口 |

#### 后果（原 ADR-0003）

- **端口分工成了契约**（[API.md](../API.md) §0/§16）：`:18765` 业务、`:18766` CDP、`:18767` 壳控制服务。
  完整决策见 [ADR-0011](0011-port-governance.md)。
- **跨进程状态通道只能是文件**：壳每 15s 把更新状态原子写入 `%APPDATA%\Workbench\shell-state.json`，
  Python 侧 `GET /api/update` 读它（超 60s 判 `live=false`）。
  为什么不每次 HTTP 探壳：账号中心每 3s 刷新，读本地文件更便宜，且壳不在时能优雅降级。
- **壳的每个源文件都必须在 electron-builder 的 `files` 白名单里**，否则开发态一切正常、
  装出来的应用 `require` 失败。这条已由 `tools/verify_packaging.py` 的 P6 项守住。
- **已删除、勿恢复**：`shell/shell.py`（pywebview 壳）、Inno Setup 链路（`tools/installer.iss`、
  `tools/ChineseSimplified.isl`）、`tools/start-desktop.vbs`。

### 2.2 更新通道 = GitHub Releases；出网通道默认直连优先（**原 ADR-0008**）

#### 2.2.1 更新通道 = GitHub Releases + `electron-updater`

- **传输**：`electron-updater` 读 GitHub Releases 的 `latest.yml`（含 `sha512` 校验）。
- **触发策略**（用户定稿）：启动后 **20s 首次静默检查** → 之后**每 6h** 一次；
  `autoDownload=true` **后台静默下载**（不弹窗、不抢焦点）；`autoInstallOnAppQuit=true`
  **退出时安装**；托盘「检查更新…」走同一路径但**结论必须弹窗**。
- **为什么不做「静默自动重启安装」**：会打断正在进行的自动登录/页面操作。
  静默下载 + 退出时安装是「零打扰」与「必达」的平衡点。
- **退出顺序不可交换**：`before-quit` 里**先 `killServer()` 再 `updater.installOnExit()`**——
  安装器要覆盖 `WorkbenchServer.exe` 与扩展目录，sidecar 还活着会锁文件。
- **手动检查先落盘再弹窗**（`main.js:checkUpdateFromTray`）：先把 `phase=checking` 同步写进
  `shell-state.json`，再弹结论。否则用户不点按钮时状态会永远停在 `checking`，
  排障与自动化验证都看不到结论。
- ⚠️ **`electron-updater` 匿名读 Release ⇒ 仓库必须公开**（本仓库即公开）。
  私有仓库的自动更新会静默失效。

#### 2.2.2 出网通道：默认直连，探测失败才回落代理

优先级（`desktop/proxy.js`）：

```
① WORKBENCH_PROXY / AKSO_PROXY / HTTPS_PROXY / HTTP_PROXY 有值 → 强制用该代理（排障用）
② 数据目录 proxy.txt：一行代理串 = 强制代理；存在但为空 = 强制直连（终极兜底开关）
③ 否则自动：直连探测 GitHub Atom feed
      通   → 直连
      不通 → 回落 Windows 系统代理（读注册表 Internet Settings）
             无系统代理 → 退回直连，并把失败原因记进状态
```

三条必须守住的细节：

1. **必须显式放行本机回环**：`proxyBypassRules = <local>;127.0.0.1;localhost;[::1]`。
   否则壳与 sidecar/CDP（18765/18766/18767）的本机通信会被塞进代理，
   表现是「网络正常但功能全废」。
2. **通道必须在任何联网动作之前定下来**（`app.whenReady()` 里、updater 之前）。
3. **探测必须带超时**（6s）：直连失败在本机表现为「卡住不返回」，无超时会挂死启动流程。

#### 2.2.3 跨进程状态通道：文件，不是 HTTP

壳每 15s（updater 心跳）把状态原子写入 `%APPDATA%\Workbench\shell-state.json`，
Python 侧 `GET /api/update` 读它，**超 60s 判 `live=false`**（壳已退出/崩溃）。
选中的通道与探测结果一并写进 `shell-state.json` 的 `proxy` 字段并透出到 API——**排障一眼可见**。

#### 换来什么（原 ADR-0008）

- 用户侧零操作升级：不需要知道 GitHub、不需要手动下载。
- 出网通道自适应：能直连的机器完全不经过代理（实测直连可用时：探测 1131ms、取资产 1201ms，零代理）。
- 排障可视化：`GET /api/update` 直接给出 `phase` / `availableVersion` / `percent` / `proxy` / `lastError`。

#### 代价（已接受）（原 ADR-0008）

| 代价 | 说明 |
|---|---|
| 依赖 GitHub 可达性 | 受限网络下更新失效；`WORKBENCH_PROXY` / `proxy.txt` 是兜底开关 |
| 启动多付最多 6s | 直连探测超时预算。不探测则可能拿到「发现新版但下载不动」的更差体验 |
| 状态不是实时 | 文件 + 60s TTL。壳异常退出后 UI 最长 60s 才显示「未运行」 |
| 发布是两步手工 | `build.ps1` 只出产物（`--publish never`），发布要另跑 `tools/publish_release.py` 或网页手工上传——见 §2.3 |

#### 后果（原 ADR-0008）

- **`shell-state.json` 成为壳与服务的契约文件**：字段改名会静默让 UI 降级（`live=false`），
  `workbench/api/routes_update.read_shell_state()` 返回**结构恒定不缺键**（缺键走 `inactive` 分支）。
- **代理配置是用户可干预的**：`proxy.txt` 空文件 = 强制直连，是「本机代理坏了但用户不想改环境变量」
  时的终极开关（见 [CONFIG.md](../CONFIG.md) §5）。
- **本机实测现状**：直连探测失败（6006ms 超时）→ 回落 `http://127.0.0.1:7890`。
  这是设计行为，但在该机器上「直连优先」路径是失败的——排查更新问题时应先看
  `GET /api/update` 的 `shell.proxy.source`。
- 回归工具（改本条链路必跑）：
  `tools/verify_updater_logic.mjs`（47 断言，纯 node 桩 electron，验状态机与装配）、
  `tools/verify_update_proxy.js`（**真实 Electron 网络栈**对比直连基准与策略生效后的结果，
  判定以「策略生效后必须拿到 `latest.yml`」为准）、
  `tools/verify_update_flow.py`（离线自测整条链：`WORKBENCH_UPDATE_OVERRIDE` 指向本地静态目录、
  用 generic provider 替代 GitHub，避免真拉 350MB）。

### 2.3 分发 = NSIS + 同号发布；扩展随包携带并引导装载（**原 ADR-0009**）

#### 2.3.1 安装器 = electron-builder 的 NSIS

`desktop/package.json` 的 `build` 段：`nsis` + `oneClick: false` + `allowToChangeInstallationDirectory: true`
+ `artifactName: Workbench-${version}-setup.${ext}`。产物连同 `latest.yml` 一起发布
（后者供 §2.2 的增量检查）。

#### 2.3.2 三样东西全部随包携带（`extraResources`）

> ★ **2026-10-09 后补（只追加，上文一字未改）**：下面表格的第二行**已不存在**——
> 浏览器扩展整体移出本仓库（用户决策：桌面端与扩展不耦合，本仓只需账号管理页承接云端数据环境），
> `extraResources` 现在**只带 sidecar 一项**。
> 取回当时形态：`git show 34b5e94:desktop/package.json`。
> 「三样东西全部随包携带」这个标题所指的第三样（扩展）也随之下线，**只剩 sidecar + chromium**。

| 从 | 到（安装后） | 内容 |
|---|---|---|
| `../dist/WorkbenchServer` | `resources/server` | PyInstaller onedir sidecar（含 **chromium 本体**，即 `ms-playwright`） |
| ~~`../extensions/quick-login/dist`~~ | ~~`resources/extension`~~ | ~~浏览器扩展构建产物~~（**2026-10-09 移除**） |

- **sidecar 打包**：`workbench/server.spec` → `dist/WorkbenchServer`（onedir）。
  打包态走 `workbench/server_entry.py`：`WorkbenchServer.exe --server`，**不开浏览器**（UI 由 Electron 主窗承载），
  启动信息落 `%APPDATA%\Workbench\server.log`（因为壳以 `stdio: 'ignore'` 启动它，现场零输出）。
- **chromium 随包**：`browser_pool` 的工作线程在 `sync_playwright()` **之前**把
  `PLAYWRIGHT_BROWSERS_PATH` 指向 `_MEIPASS/ms-playwright`（顺序错了就找不到）。
  ⚠️ `server.spec` 只有在 `%LOCALAPPDATA%\ms-playwright\chromium*` **存在时**才收集它——
  构建机没装 chromium 会打出没有浏览器的包（见 `../CODEBASE_OVERVIEW.md` §九 风险台账中的待修项）。

#### 2.3.3 扩展：随包 + 应用内引导（不追求「静默一键」）

**实证结论：静默一键装扩展在 Windows 上做不到，不要再试。** 已尝试并失败的路径：

- 拖入 `.crx`：Chrome Windows 拦截非商店扩展。
- `ExtensionInstallForcelist` 策略：**在 HKCU 下普遍不生效**（需 HKLM + 管理员 + 域策略）。
- 自托管 `update_url`：亦常装不上。
- 本仓库**无签名私钥**（只有 manifest 公钥 `key`）→ 无法用既有 ID 重打 CRX。
- **要真·一键只有一条正路：上架商店（可不公开列出）后把商店 ID 写进策略。**

因此采用「**随包携带 + 一键引导**」这条零依赖、零管理员、离线可用的路径：

```
账号中心提示条 / 托盘「安装浏览器扩展…」
  → POST /api/shell/extension-setup  → 壳 :18767 /extension-setup
  → 打开扩展目录（shell.openPath）+ 打开 chrome://extensions + 弹步骤说明
用户点 4 下：开发者模式 → 加载已解压的扩展程序 → 选目录
之后永久可用；账号由桌面端快照自动下发，无需在扩展里另建
```

升级后的同一套能力复用于 `mode="reload"`（引导点一次「重新加载」）。

#### 2.3.4 版本：与扩展同号发布，真源唯一

- 真源 = `pyproject.toml` 的 `version`；`tools/bump.py` **五写**同步：
  `pyproject.toml` + `workbench/__init__.py` + `desktop/package.json`
  + 扩展 `manifest.json` + 扩展工作区 `package.json`（+ `.version.json` 缓存）。
- 规则（方案 A）：`push` = PATCH+1；`build` = MINOR+1 且 PATCH 重置 1（`0.0.2 → 0.1.1 → 0.2.1`）。
- **为什么要同号**：`extStale` 判定建立在「桌面安装包版本 == 扩展版本」之上
  （`GET /extension/health` 比较两者），不同号会让「扩展是否需要重新加载」的判断失去意义。
- **改版本只走 `bump.py`，勿手改单一文件**——漏写 `desktop/package.json` 会导致安装包命名与产物校验错位。

#### 2.3.5 前置体检必须存在（对 §1.3 那次事故的直接回应）

`tools/verify_packaging.py`（秒级、只读静态核对；**今天已长到 13 项**）：
P1 spec 入口脚本存在 / P2 入口只用绝对导入 / P3 壳 spawn 路径与 `extraResources` 一致 /
P4 扩展产物存在且版本 == 项目版本且关键产物齐全 / P5 `build.ps1` 具 UTF-8 BOM /
P6 壳全部 `.js` 都在 electron-builder `files` 白名单里 / … / **P10** 扩展令牌中间件已装且豁免面最小。

#### 换来什么（原 ADR-0009）

- 一个 `.exe` 装完即可用：零 Python、零 Node、零管理员权限、零联网（扩展初次装载也离线）。
- 产物可校验、可自动更新（`latest.yml` + `sha512`）。
- 构建断点被前置体检拦住，不必跑完 PyInstaller（数分钟）才发现入口文件不存在。

#### 代价（已接受）（原 ADR-0009）

| 代价 | 说明 |
|---|---|
| 包体 349.7 MB | Electron 运行时 + sidecar + chromium + 扩展。用户定稿接受 |
| 扩展需用户点 4 下 | 无法规避（见上实证）；代价是「首次体验多一步」 |
| 扩展目录随桌面端存在 | 卸载桌面端后扩展失效（已在引导文案里说明） |
| 构建链长且成对依赖 | PyInstaller 必须在 electron-builder 之前；扩展重建必须在 bump 之后（否则打进安装包的 manifest 版本停在上一版） |
| 发布是第二步手工 | `build.ps1` 用 `--publish never` 只出产物，发布需 `tools/publish_release.py` 或网页上传 `setup.exe + latest.yml` |

#### 后果（原 ADR-0009）

- **`files` 是白名单**：新增壳的 `.js` 模块若忘了加进 `desktop/package.json` 的 `build.files`，
  开发态一切正常、装出来的应用 `require` 失败。P6 项已守住。
- **`build.ps1` 的脚本纪律**（PS5.1）：必须 UTF-8 **BOM**（编辑后极易丢失，中文会乱码）；
  禁用 `$ErrorActionPreference='Stop'`（PS5.1 会把 git/uv 写 stderr 的正常进度当终止错误）。
- **`uv sync` 必须在 release commit 之前**：`uv sync` 会把项目版本写进 `uv.lock`，
  顺序反了则 commit 里的 lock 立刻过期、工作区再次变脏（0.3.2 实测踩到）。
- **`uv sync` 必须带 `--extra build`**，否则 PyInstaller 会被移出环境，第 5 步直接失败。
- 打包态与开发态的差异是长期风险源（`frozen=True` 分支、`_MEIPASS` 路径、`stdio: ignore`），
  **改动打包链路后务必真跑一次 `build.ps1`，不要只看开发态**。

### 2.4 品牌中立化：`AksoWorkbench` → `Workbench`（**原 ADR-0013**）

#### D-1 自有品牌标识一次性改净

| 位置 | 旧 | 新 |
|---|---|---|
| Python 包 | `akso-workbench` | `workbench` |
| 桌面包 / 产品名 | `akso-workbench-desktop` / `AksoWorkbench` | `workbench-desktop` / `Workbench` |
| appId | `com.akso.workbench` | `com.workbench.app` |
| 安装包产物 | `AksoWorkbench-<版本>-setup.exe` | `Workbench-<版本>-setup.exe` |
| 服务端 sidecar 产物 | `dist/AksoServer/`、`AksoServer.exe` | `dist/WorkbenchServer/`、`WorkbenchServer.exe` |
| 数据目录 | `%APPDATA%\AksoWorkbench`、`~/.akso-workbench` | `%APPDATA%\Workbench`、`~/.workbench` |
| 环境变量 | `AKSO_PROXY` / `AKSO_NO_PROXY` / `AKSO_HTTPS_PROXY` / `AKSO_UPDATE_OVERRIDE` | `WORKBENCH_*` 同名 |
| 洞察默认工作区 | `<我的文档>/AksoWorkbench/洞察` | `<我的文档>/Workbench/洞察` |

#### D-2 数据目录：改名 + **一次性自动接管**（不许出现"账号全没了"）

`workbench/config.py::legacy_db_candidates()` 新增**最高优先级**候选
`%APPDATA%\AksoWorkbench\workbench.db`，沿用既有 bootstrap 机制：

- **只在目标库不存在时**接管（`bootstrap_database` 的既有语义）——**绝不覆盖**现有数据；
- 连同 `-wal` / `-shm` 一起复制（WAL 里有未落盘的事务）；
- **复制而非移动**，旧目录原样保留（可回滚、可对比）；
- 已实测：真机 `%APPDATA%\AksoWorkbench` 的库被接管后，`/extension/snapshot` 读出 **5 个账号**全部通过过滤。

> ⚠️ **迁移后请只使用新版本**：复制语义意味着旧版本若继续写旧库，两份数据会分叉；
> 下次启动新版本时**不会**再次接管（目标库已存在）。

#### D-3 兼容读取（旧名继续认，写出端只发新名）

改名不能把用户手上的东西变成废纸。以下**读**路径保留旧名兼容：

| 对象 | 兼容做法 | 不兼容的后果 |
|---|---|---|
| 环境变量 | `WORKBENCH_PROXY` 缺失时回落 `AKSO_PROXY`（`WORKBENCH_UPDATE_OVERRIDE` / `WORKBENCH_NO_PROXY` / `WORKBENCH_HTTPS_PROXY` 同理） | 用户手写的启动脚本、快捷方式静默失效 |
| 备份文件 | `import_backup` 接受 `akso-workbench-backup` 与 `workbench-backup` 两种 `format` | 已有备份变成"不是有效的备份文件" |
| 桌面→扩展快照 | `sync.ts` 同时接受 `akso-workbench-snapshot` 与 `workbench-snapshot` | 扩展要手动「重新加载」才升级，**旧扩展会静默丢弃全部账号**（PITFALLS #14 的形态） |
| 进程占用判定 | `ports.js::ownImageNames` 同时认 `workbench.exe` 与 `aksoworkbench.exe` | 同机还跑着旧版实例时，把它误判成"外人"，报错信息指向错误的占用者 |
| 洞察工作区 | `<我的文档>/AksoWorkbench/洞察` 已存在且新目录不存在时**继续沿用旧目录** | 用户已落盘的产物"消失"（表现为"我的产物去哪了"） |

#### D-4 刻意不改（写下来免得下次有人"顺手统一"）

- **上游项目名**：`akso-cc` / `akso-auto` / `akso-mogul`（仓库名）、`adapters/akso-*.json` 文件名、
  `AKSO_CC_REPO` / `AKSO_AUTO_REPO`（由 `repoPathEnv` 派生，见 [CONFIG](../CONFIG.md) §2.3）——
  它们是"这份存档对应哪个原仓库"的引用真源，改了会让
  [ADR-0005](0005-native-internalization-of-four-projects.md) 的语义失真。
- **平台适配层**：`workbench/services/egmp/` 目录名与 `eGMP` 措辞——它描述被适配的平台，不是我们的品牌。
- **扩展内部存储键**：`akso:acctMap` / `akso:snapshotId` / `akso:cmdCursor` / `akso:desktopEndpoint` / `akso:sync` 告警名。
  它们对用户不可见，但**改名会让指令游标与快照缓存复位**——游标不单调正是 [PITFALLS](../PITFALLS.md) #15
  "指令通道整体哑火且无报错"的成因。**收益为零、风险为正，故不动。**
- **历史留痕**：`docs/adr/*`（本 ADR 之前）、`docs/archive/*`、扩展的 `docs/archive/*`、`CHANGELOG.md` 的历史条目
  （历史产物名是事实）、`PITFALLS.md` 既有条目——本仓约定「只增不改」。
- **仓库名**：仍为 `jizi-dragon/akso-mogul`（用户 2026-09-16 决定「暂不改」）。
  **这是一处刻意保留的不一致**：`desktop/package.json` 的 `publish.repo`、`README`、发布脚本继续指向它。

> ★ **2026-10-11 后补（只追加，本节上文一字未改）**：用户 **2026-10-11** 决定改名 ⇒
> `jizi-dragon/akso-mogul` **已更名为 `jizi-dragon/akso-workbench`**。
> 上面 D-4 的「仓库名：仍为 `jizi-dragon/akso-mogul`（用户 2026-09-16 决定『暂不改』）」
> 记录的是**决策当时的认知**，原样保留 —— 据此，本节的 D-4、后果表的「仓库名未改」行、
> §3 的「~~改仓库名~~」、§5.1 第二条「仓库名仍未改」与 §7.1 第 1 条
> 「用户决定改仓库名 → 同步 `publish.repo`、remote URL」**这些触发器均已触发**。
>
> 本次随更名同步改动的位置（★ **产品展示名与全部产物名一个字都没动**：
> `Workbench-x.y.z-setup.exe` / `dist/WorkbenchServer` / `%APPDATA%\Workbench` / `Workbench/` 数据目录
> 本来就已经是新形态）：

| 位置 | 改动 |
|---|---|
| `desktop/package.json` | `build.publish.repo` → `akso-workbench`（`owner` 仍 `jizi-dragon`） |
| `desktop/proxy.js` | 直连探测端点 `PROBE_URL` → `…/akso-workbench/releases.atom` |
| `tools/verify_update_proxy.js` | 端到端探测的资产 URL → `…/akso-workbench/releases/latest/download/latest.yml` |
| `tools/publish_release.py` | `REPO` 常量与 `GH_TOKEN` 提示文案 |
| `README.md` | 「仓库地址」一行改为新名并标注本次决策 |
| `docs/VERSIONING.md` / `CONTRIBUTING.md` | 可见性 / 规则集实测记录里的仓库 URL（探测结论 404 / 403 不变） |
| `CHANGELOG.md` | `## [Unreleased]` 追加一条更名说明（历史条目一字未改） |

> ★ **仓库名与 2026-09-16 退役的旧包名同串，别据此"顺手统一"**：本次取的
> `akso-workbench` 恰好与 §2.4 表格左列那个**已退役的旧 Python 包名** `akso-workbench` 同串。
> 它今天只是 **GitHub 仓库标识** —— 包名仍是 `workbench`、产品名仍是 `Workbench`，
> D-3 的 `akso-workbench-backup` / `akso-workbench-snapshot` 兼容分支是**另一件事**。

#### D-5 身份握手必须四处同步改（改名最容易踩的坑）

`/api/health` 与 `/whoami` 答的 `app` 字段是**壳的存活判据**与**扩展的身份判据**，改一处不改另一处
= 壳认为自己的服务没起来（[PITFALLS](../PITFALLS.md) #20 的完整形态）。本次同步改动的四处：

| 位置 | 作用 |
|---|---|
| `workbench/api/__init__.py` | 服务端 `/api/health` 的 `app` 字段（真源） |
| `desktop/ports.js` | 壳控制服务 `/whoami` 的 `app` 字段 |
| `desktop/main.js` | 壳就绪探测核对 `data.app === 'Workbench'` |
| `extensions/.../background/core/desktop-endpoint.ts` | 扩展核对桌面端身份后才把凭据发给它 |

外加 `tests/test_ports_contract.py`、`tools/verify_port_broker.mjs`、`tools/verify_update_flow.py` 三处断言/夹具。

#### 后果（原 ADR-0013 §3）

**正面**

- 换平台实施不再需要改产品名与数据目录；"适配新平台"从此只走插件与适配层（正是用户说的"核心差距在插件/tools"）。
- 工具清单与插件契约有了独立文档与机检（见 [TOOLS.md](../TOOLS.md)），不再出现"我不知道有 19 个工具"。
- 数据接管机制顺带把"更名 = 用户丢账号"这一类事故封死。

**负面 / 风险**

| 风险 | 说明 | 处置 |
|---|---|---|
| **旧版不会被覆盖安装** | NSIS 的安装目录与卸载项由 `productName` 决定，改名后是**并存的两套程序** | 升级时先卸载旧版；`README` 已写明 |
| **发布产物名变化** | `Workbench-<版本>-setup.exe`；旧客户端的 `latest.yml` 检查会断 | 自动更新**默认已关闭**（[CONFIG](../CONFIG.md) §2.2），影响面小 |
| **迁移后旧版本继续写 = 数据分叉** | 复制语义的固有代价 | 文档写明「迁移后只用新版本」；旧目录不删以便回滚 |
| 仓库名未改 | 代码里 `publish.repo` 与新品牌不一致 | 已在 D-4 记录；将来改仓库名时同步 `publish.repo` + remote + 本 ADR 补一条 |

---

## 3. 本期做与不做

**做**：Electron 33 壳（壳只做壳）；GitHub Releases 自动更新 + 直连优先的出网通道；
NSIS 分发 + 三样东西随包 + 应用内引导装载 + 前置体检 13 项；品牌标识一次性改净 + 数据目录自动接管 + 兼容读取。

**不做（明确）**：

- ~~静默一键装扩展~~ —— **Windows 上做不到，实证见 §2.3.3**。
- ~~Inno Setup 链路~~ —— 已废弃删除（中文语言包维护成本 + 与 Electron 产物不对接）。
- ~~pywebview 壳~~ —— 已删除（`shell/shell.py`），**勿恢复**。
- ~~把 Node 作为终端用户运行时依赖~~ —— 见 §2.1 底线 2。
- ~~改仓库名~~ —— 用户决定「暂不改」（§2.4 D-4）。
- ~~改上游项目名 / 平台适配层名~~ —— §2.4 D-4。
- ~~改扩展内部存储键~~ —— **收益为零、风险为正**（§2.4 D-4）。
- ~~静默自动重启安装~~ —— 会打断正在进行的自动登录/页面操作（§2.2.1）。

---

## 4. 后果

### 好处

- 一键安装成立：零 Python / 零 Node / 零管理员 / 零联网（装扩展除外）。
- 用户侧零操作升级；出网通道自适应；排障信息（`GET /api/update`）一眼可见。
- 换平台实施不再需要改产品名与数据目录；"更名 = 用户丢账号"这一类事故被封死。

### 代价

- 包体 349.7 MB；双进程生命周期管理；构建链长且成对依赖；发布是两步手工。
- 打包态与开发态的差异是**长期风险源**（`frozen=True` / `_MEIPASS` / `stdio: ignore`）。
- 更名后旧版与新版本**并存**（NSIS 不覆盖安装）。

### 风险

- **孤儿 sidecar**：壳被强杀时 sidecar 可能残留并占住 18765（PITFALLS #20）。
  这条纪律见 [AGENTS.md](../../AGENTS.md) 铁律 31（"父非正常死亡时谁释放"）。
- **壳与服务的跨进程契约是文件**（`shell-state.json`）：字段改名会**静默**让 UI 降级。
- **构建机的环境事实会被打进产物**：构建机没装 chromium 就打出没有浏览器的包。

---

## 5. 诚实边界

> 四篇原 ADR 各自写下的"做不到什么"**原样保留**，一句未软化。

### 5.1 更名这件事**没有解决**的（**原 ADR-0013** §3 负面表 / §4）

- **旧版不会被覆盖安装**：改名后是**并存的两套程序**。升级要用户先手动卸载旧版 ——
  **这不是"更名完成"，是"更名 + 一次手工迁移"**。
- **仓库名仍未改**：`jizi-dragon/akso-mogul` 与新品牌**不一致**，且这是**刻意保留**的。
  读到"品牌中立化"不要以为名字全改干净了 —— `publish.repo`、remote、`adapters/akso-*.json`
  全都还是旧名。
- **兼容读取（D-3）是暂时的**：它的清理需要另开一次决策（建议保留一个大版本），
  **今天所有旧名回落分支都还在代码里**。

### 5.2 更新通道**挡不住**什么（**原 ADR-0008**）

- **「直连优先」在本机是失败的**：实测直连探测失败（6006ms 超时）→ 回落代理。
  这是设计行为，但意味着**"默认直连"这条路径没有在本机被验证过是通的**。
- **受限网络下更新整体失效**：`WORKBENCH_PROXY` / `proxy.txt` 只是兜底开关，不是解决方案。
- **状态最多滞后 60s**：文件 + TTL 语义。壳异常退出后 UI 最长 60s 才显示「未运行」。
- **私有仓库的自动更新会静默失效**（`electron-updater` 匿名读 Release）——本仓库恰好是公开的，
  这条边界只是在"万一转私有"时会咬人。

### 5.3 分发**挡不住**什么（**原 ADR-0009**）

- **扩展初次装载必须用户点 4 下**：静默一键在 Windows 上做不到（§2.3.3 列了四条已失败的路径）。
  唯一的真·一键路径是**上架商店**，本期没做。
- **构建断点只在"跑完整构建"时暴露**：错误输出被脚本吞掉是历史形态（§1.3 的两个静默断点）。
  前置体检（13 项）守的是**静态契约**，它守不住"打包态与开发态行为不同"。
- **`server.spec` 对 chromium 的收集依赖构建机的环境事实**：构建机没装就静默打出没有浏览器的包。

### 5.4 壳这一层**挡不住**什么（**原 ADR-0003**）

- **壳被强杀时 sidecar 可能残留**并占住 18765（PITFALLS #20）。
  `before-quit` 那条链在强杀与崩溃时**根本不跑**（见 [AGENTS.md](../../AGENTS.md) 铁律 31）。
- **"关窗即退出"是承诺，不是结构**：任何"应用继续在托盘运行"式的文案改动都必须同步实现
  （[AGENTS.md](../../AGENTS.md) 铁律 32，PITFALLS #55 的形态）。
- **端口分工成了契约**，因此"旧进程还活着"与"测试戳到了真端口"这两件事**都不报错** ——
  完整处置见 [ADR-0011](0011-port-governance.md)。

---

## 6. 外部参照

> 四篇原 ADR 中，原 ADR-0013 / 0008 / 0009 写于 `0000-template.md` 建立前后不一，
> **只有原 ADR-0013 带「重新评估触发器」，没有任何一篇带「外部参照」栏**。
> 本栏为**本次合并时后补**（2026-10-07）；能给出处的写，给不出的写"未核实"。

| 参照对象 | 它的做法 | 与本决策的关系 | 我们的理由 |
|---|---|---|---|
| [electron-builder · NSIS](https://www.electron.build/nsis) + [electron-updater](https://www.electron.build/auto-update) | 官方同时提供 NSIS 目标与 `latest.yml` 增量检查；**它们的默认形状就是本节的形状** | **一致（跟随）** | 这套组合是 Electron 生态的标准解法，不必自证。我们额外做的只有"三样东西随包"与"前置体检" |
| [VS Code](https://code.visualstudio.com/docs/supporting/faq#_how-do-i-opt-into-the-latest-release) / [Obsidian](https://obsidian.md/) 一类桌面应用的更新策略 | 后台静默下载 + **退出时安装**；托盘提供"检查更新"入口并**弹结论** | **一致** | §2.2.1 的策略与它们同构。★ 共同点：都**刻意不做"静默自动重启"** —— 会打断用户正在进行的工作 |
| Chrome 对非商店扩展的态度（[Chrome 官方：Load unpacked](https://developer.chrome.com/docs/extensions/get-started/tutorial/hello-world#load-unpacked)） | Windows 上**拦截**非商店扩展的直接安装；开发者模式逐次加载是官方给出的路径 | **一致（约束）** | §2.3.3 的"引导用户点 4 下"就是这条约束下的**唯一零管理员路径**。我们**没有**绕过它 |
| [Squirrel.Windows](https://github.com/Squirrel/Squirrel.Windows) 一类的"更新即重启" | 更新后**立即**重启应用 | **★ 分歧** | 我们的应用里可能正跑着自动登录或页面操作 —— 重启会**打断真实业务**。这是场景决定的，不是技术判断 |
| 品牌中立化的一般做法（多平台工具把厂商名从产品名里剥离） | 常见做法是新旧名并存一段时间，旧 `format` / 旧存储键**永久保留**或按大版本清理 | **一致** | §2.4 D-3 的"写出端发新名 + 读取端两个都收"就是它。★ 分歧只在**保留多久**：我们把它留成一条显式触发器（§7.3），而不是默认永久 |
| 上游 quick-login / akso-cc / akso-auto 的命名 | 它们是**来源项目**的名字，不是我们的品牌 | **一致（保留）** | §2.4 D-4：改了会让 [ADR-0005](0005-native-internalization-of-four-projects.md) 的存档语义失真 |

**由本次对照得出的结论**：

- **可以简化的**：NSIS + `latest.yml` + 退出时安装 —— Electron 生态的标准形状，直接跟随。
- **必须自己定的**：① **哪些标识属于"协议/持久化"，哪些只是文案** ——
  这是本项目的历史决定的（PITFALLS #36 那四类静默故障）；② **兼容读取保留多久**；
  ③ **"静默一键装扩展"要不要放弃** —— 我们的答案是"放弃，走引导"，因为已实证无管理员路径。
- **未核实**：同类桌面应用在"改名时如何接管旧数据目录"上的做法，本次**没有取证**。
  §2.4 D-2 的"复制而非移动 + 只在目标库不存在时接管"是本仓自己定的。

---

## 7. 重新评估的触发器

### 7.1 原 ADR-0013 §4（原文）

- 用户决定改仓库名（`akso-mogul` → 任意名）→ 同步 `publish.repo`、remote URL、
  `../HISTORY-REWRITE-NOTICE.md`（**该文件已于 2026-09-29 删除**）。
- 第二个平台适配层落地 → 那时应把 `services/egmp/` 提升为"平台适配目录 + 各平台子包"，
  本条 ADR 的 D-4 需要重新审视（平台名会从"唯一"变成"之一"）。
- 兼容读取（D-3）建议**保留一个大版本**后清理；清理时删掉旧名回落与旧 `format` 分支，并补一条 CHANGELOG。

### 7.2 原 ADR-0008（无专列触发器栏，以下为本次合并时按正文与代价归拢）

| 触发器 | 到时再考虑 |
|---|---|
| 仓库转为**私有** | `electron-updater` 匿名读 Release 失效 ⇒ §2.2.1 整条更新通道要重做（或改用带凭据的 provider） |
| 更新产物体积成为痛点（350MB / 次） | 增量更新（blockmap）或差分通道；届时 §2.3 的"第二步手工发布"要一并自动化 |
| 本机直连路径被证实长期不通 | §2.2.2 的"默认直连"取向要重新评估 —— 那是一条**产品取向**（用户定稿"能直连就别配代理"），不是技术默认 |

### 7.3 原 ADR-0009（无专列触发器栏，以下为本次合并时按正文与代价归拢）

| 触发器 | 到时再考虑 |
|---|---|
| 扩展**上架商店**（可不公开列出） | §2.3.3 的"点 4 下"可以升级为真·一键（商店 ID 写进策略）；这是本节唯一被记下的正路 |
| 构建链要在 CI 上真跑 | §1.3 的两个静默断点（入口脚本丢失 / 坏图标）说明"失败输出被脚本吞掉"是主要风险；届时日志口径要一起改 |
| `verify_packaging.py` 的项数再次增长 | 它是**秒级静态核对**，加项请同时确认它跑得仍然够快（它进 CI） |

### 7.4 原 ADR-0003（无专列触发器栏，以下为本次合并时按正文与代价归拢）

| 触发器 | 到时再考虑 |
|---|---|
| 需要"关窗后继续在托盘运行"（或反向撤销） | §2.1 底线 1 与 §5.4 第二条：**改行为与改文案必须一起做**（铁律 32） |
| 换掉 Electron（例如回到系统 WebView） | §2.1 的 CDP 可控性前提消失，[ADR-0002](0002-browser-allocation-policy.md) 的 `cdp` 模式要重新设计 |
| sidecar 的孤儿问题被真实用户撞到 | §5.4 第一条；届时该走"子进程自己盯着父"或"内核对象担保"，而不是加固 `before-quit`（铁律 31） |

---

_本篇由 **原 ADR-0003 ＋ 0008 ＋ 0009 ＋ 0013** 四篇合并而成（2026-10-07）。
原文件名分别为 `0003-electron-desktop-shell.md`、`0008-update-channel-and-proxy.md`、
`0009-nsis-distribution-and-update-channel.md`、`0013-brand-neutralization-workbench.md`；
其中 0008 / 0009 / 0013 三篇已**退役**，取回命令见 `CHANGELOG.md` 与 `docs/PITFALLS.md` 的退役说明。
`0003-electron-desktop-shell.md` 沿用原名（本合订本取该组最小号）。_
