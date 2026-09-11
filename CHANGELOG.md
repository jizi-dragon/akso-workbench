# Changelog

本项目遵循语义化版本（SemVer），格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。
从第一天开始记录（对齐行业月更节奏惯例）。

## [0.3.4]

### Added

- **出网通道自动选择（0.3.4，实测倒逼；用户定稿："能直连就别配代理"）**：`v0.3.3` 发布后真机
  验证发现一个致命断层——更新**能发现新版、却下载不下来**。逐跳实测：查版本走
  `github.com/<o>/<r>/releases.atom`（1.9s）、资产走 `github.com/…/releases/download/…`（302）
  → CDN `release-assets.githubusercontent.com`（很快）；而本机**直连 `github.com` 是间歇性的**
  （同一 URL 1.1s 成功与 20s 超时都出现过），Chromium 默认又不真用系统代理（注册表
  `ProxyEnable=1` 摆着，请求仍直连超时）→ 于是表现为"检查得到、下载不动"。
  修法：新增 `desktop/proxy.js`，**默认优先生成直连**，只有直连探测失败才回落到代理：
  **环境变量 `AKSO_PROXY`/`HTTPS_PROXY`（强制代理）→ 数据目录 `proxy.txt`（一行=强制代理；
  空文件=强制直连，终极兜底开关）→ 自动（直连探测 Atom feed，通就直连、不通再用 Windows
  系统代理）**；探测带 6s 超时（直连失败在本机表现为"卡住不返回"，无超时会挂死启动）。
  三个必守细节：**必须显式放行回环**（`proxyBypassRules = <local>;127.0.0.1;localhost;[::1]`，
  否则壳与 sidecar/CDP(18765/18766/18767) 的本地通信会被塞进代理，表现为"网络正常但功能全废"）；
  通道在**任何联网动作之前**定下（`app.whenReady()` 里、updater 之前）；选中的通道与探测结果
  写进 `shell-state.json` 的 `proxy` 字段并透出 `GET /api/update`，排障一眼可见。
  实测：直连可用时选直连（探测 1131ms、取资产 1201ms），**完全不经过代理**。
- **`tools/verify_update_proxy.js`**：用**真实 Electron 网络栈**（`electron.net.fetch`，即
  electron-updater 内部同一套）对真实 Release 资产 URL 做基线 + 应用自动策略后的对比。
  判定以"策略生效后必须拿到 `latest.yml`"为准；直连基线成功不算失败（那正是 auto 想要的结果）。
- **`tools/publish_release.py`**：发布到 GitHub Releases 的可核对工具（建 draft → 传资产 → 转正 →
  匿名复验 `latest.yml`）。发布前三道自检：`latest.yml` 版本号 == 发布版本、其 sha512 == 安装包
  实际 sha512、指向本次安装包——发布错的清单会让**所有客户端**更新失败。资产顺序是"先小后大"
  （350MB 最容易失败，先把重试预算花在大头上）。支持 `--no-proxy`：关掉 Clash 后 **git 自己的
  `http.proxy` 仍写着 127.0.0.1:7890**，脚本会对着没人监听的端口连（`WinError 10061`）——
  "我关了代理"这句话必须在脚本里有对应开关。

### Changed

- 托盘「检查更新…」改为先**同步**把 `phase=checking` 落盘、再弹结论弹窗：手动检查在无新版/开发态
  会走弹窗分支，用户不点按钮时状态会永远停在 checking，排障与自动化验证都看不到结论。

## [0.3.3]

### Added

- **桌面端自动更新（0.3.3，用户定稿：通道 = GitHub Releases；策略 = 启动检查 + 静默后台下载 + 退出时安装 + 手动检查）**：
  - **壳内更新引擎**：新增 `desktop/updater.js`（状态机 + 事件接线，纯数据 `state()` 便于纯 node 验证）。启动后 20s 首次静默检查、此后每 6h 一次；`autoDownload=true` 后台静默下载（**不弹窗、不抢焦点**）；下载完成后只改托盘提示与账号中心角标，真正安装交给「你退出应用时」——`autoInstallOnAppQuit=true`，并在 `before-quit` 里**先 `killServer()` 再拉安装器**（顺序关键：安装器要覆盖 `AksoServer.exe` 与扩展目录，sidecar 还活着会锁文件）。托盘新增「检查更新…」（结论必落弹窗）与「关于 vX.Y.Z」；账号中心右上角版本角标可点击即手动检查。
  - **跨进程状态通道**：壳每 15s 把状态写 `%APPDATA%\AksoWorkbench\shell-state.json`（原子写；`%APPDATA%` 规则与 `workbench/config.py` 的 `DATA_DIR` 对齐），Python 侧 `workbench/api/routes_update.py` 提供 `GET /api/update`（超 60s 无心跳判 `live=false` → UI 显示「桌面壳未运行」）与 `POST /api/update/check|install`（代理到壳控制服务 18767）。开发态（未打包）返回 `phase=unsupported`，弹窗提示改用 `git pull`，**绝不误报**。
  - **为什么读文件而不是每次 HTTP 探壳**：账号中心每 3s 刷新，读本地 JSON 更便宜且壳不在时能优雅降级。
  - **为什么不做「静默自动重启安装」**：会打断正在进行的自动登录/页面操作——静默下载 + 退出时安装是「零打扰」与「必达」的平衡点。
- **扩展升级后自动检测「需重新加载」（0.3.3）**：扩展随安装包更新（`resources/extension` 被覆盖），但 Chrome 只在「重新加载」后才用新代码 → 此前升级后可能长期跑旧扩展而无任何迹象。现在：扩展在 `POST /extension/state` 上报自身版本（`extVersion`，**账号映射为空也照发**——旧实现 `if (!items.length) return` 会让这条事实永远传不出去），桌面端比对版本并把 `extVersion` / `desktopVersion` / `extStale` 加进 `GET /extension/health`；账号中心据此显示「浏览器扩展版本过旧」提示条 →「重新加载引导」= `POST /extension/setup-helper {mode:"reload"}` → 壳打开 `chrome://extensions` 并给出「点 ↻ 重新加载」步骤说明（与首次安装共用同一套壳能力，仅文案不同）。**刻意不做自动 `chrome.runtime.reload()`**：重载会掐断进行中的自动登录，把不可控时序留给用户点一次。
- **`tools/verify_updater_logic.mjs`（47 条断言，纯 node 秒级）**：用 `Module._load` 钩子把 `electron` / `electron-updater` 换成桩，验证开发态不崩、打包态装配（`autoDownload`/`autoInstallOnAppQuit`/`allowPrerelease`）、事件链（checking → available → progress → downloaded）、就绪后再检查走短路、无更新、失败路径（自动静默 / 手动弹窗）、`installOnExit` 参数与幂等、下载完成提示不打断。⚠ 脚本注释记下一个真实踩坑：**桩必须在 `init()` 期间仍然生效**——真实 `electron-updater` 包在模块体里就调用 `app.getVersion()`，只在 load 期间挂钩会让 init 内部那次 require 落到真实包并抛错，而错误会被 `init` 的 try/catch 静默吞掉（表现为 `supported` 恒为 false）。
- **扩展版本号纳入自动同步（0.3.3 修正）**：`tools/bump.py` 的「扩展双写」路径此前指向**不存在**的 `packages/extension/package.json`（唯一存在的是工作区根 `extensions/quick-login/package.json`），因有 `exists()` 守卫而**静默少写一次**——规则本意是"扩展版本随项目演进"，实际只写了 manifest。路径已修正，扩展版本面重新变为 manifest + 工作区 package.json 两处齐步走。

### Changed

- `tools/build.ps1` 的发布说明与 `docs/EXTENSION-INSTALL.md` 第四节新增「发布更新到 GitHub Releases」：含 **GH_TOKEN 的完整获取路径**（头像 → Settings → Developer settings → Personal access tokens → fine-grained（Contents: Read and write，选 `jizi-dragon/akso-mogul`）或 classic（勾 `repo`）→ Generate → 只显示一次的复制时机）、两种发布方式（`--publish always` 注入 `$env:GH_TOKEN` / 网页手动传 `setup.exe + latest.yml + blockmap`）、tag 与版本号必须一致的顺序要求、token 有效性与吊销的核对方法。**更新器匿名读取 Release ⇒ 仓库必须公开**（本仓库即公开）。
- 账号中心右上角版本角标从静态 `<span>` 变为可点击按钮：平时只显示版本号，有新版/下载中/已就绪时分别上色（`has-update` / `is-ready`），`title` 给出具体状态；浏览器直开本页（无壳）时依旧是纯展示，不影响任何既有功能。

### Fixed

- **安装引导提示条每次都弹（0.3.2，用户实测反馈）**：「未检测到浏览器扩展」横幅的判据此前放在**浏览器内存**里（`extEverConnected`）→ 刷新页面/重启应用即复位，于是每次打开账号中心都会重新弹出。改为**按安装实例落库的一次性闩锁**：`/extension/state` 收到执行面上报（或 `/extension/health` 判为已连接）的**第一次**即把 `ext_connected_once=1` 写入 settings 表，`/extension/health` 随之返回 `everConnected`；前端只在 `!connected && !everConnected` 时显示横幅。**语义（用户定稿）：以"能否与浏览器连接成功一次"为依据——连上过一次即永久静默**（之后即便关掉 Chrome 也不提示），单纯的 `connected=false` 不再触发提示。找回入口仍在托盘「安装浏览器扩展…」。
  - 实测（隔离实例 + 跨进程重启）：全新装机 `everConnected=false`（会提示）→ 首次上报后 `true`（立即静默）→ **重启服务进程后仍为 `true`**（核心回归点）→ 闩锁确实落在 settings 表。
- **`tools/build.ps1` 新增 `-Bump build|push|none`**：此前版本演进只有 MINOR+1 一条路，补丁修复也会被抬成 MINOR 版本（本次 0.3.2 即为 `-Bump push` 的补丁发布）。

- **打包链路的两个静默断点（0.3.1 首次真机构建暴露）**：
  - **`workbench/server_entry.py` 丢失** → `server.spec` 仍指向它，PyInstaller 直接失败（`script … not found`），而开发态走 venv + uvicorn 完全不受影响，故长期无人察觉。已重建：**绝对导入**（PyInstaller 把入口当顶层脚本执行，相对导入必 ImportError）、接受壳的 `--server` 调用约定、**不打开浏览器**（打包态 UI 由 Electron 主窗承载）、冻结态把启动信息落 `DATA_DIR/server.log`（壳以 `stdio:'ignore'` 启动，现场零输出）。实测：`AksoServer.exe` 正常起服，日志记 `AksoServer v0.3.1 pid=… (frozen=True)`。
  - **`desktop/icon.ico` 自首次提交起就是坏文件**：整份二进制被"当文本另存为 Unicode"了一次（UTF-16LE BOM + 每字节占 2 字节），且该过程**有损、无法还原**（git 历史里也只有坏版本）。后果两处且都静默：`electron-builder` 报 `image … shas unknown format` 导致**构建失败**；Electron 托盘图标一直是空白（被 main.js 的 try/catch 吞掉）。已新增 `tools/make_app_icon.py` 从品牌 PNG 重新生成 7 尺寸 ICO（16/24/32/48/64/128/256）。⚠ 两个坑写进工具注释：Pillow 的 ICO 写出**不会放大**（请求尺寸大于源图会被静默跳过），而 electron-builder 要求至少 256 → 必须先把源图 LANCZOS 放大到 256 再生成。
- 新增 `tools/verify_packaging.py`：**打包前置体检（秒级 7 项）**——spec 入口存在 / 入口只用绝对导入 / 壳 spawn 的 `resources/server/AksoServer.exe` 与 extraResources 映射一致 / 安装包要携带的扩展产物齐备且 manifest 版本 == 项目版本 / `tools/build.ps1` 具备 UTF-8 BOM。上述两个断点都能被它提前拦住，不必烧掉数分钟构建才失败。

### Added

- **安装包携带浏览器扩展 + 应用内一键安装引导（0.3.1）**：`desktop/package.json` 的 `extraResources` 增带 `extensions/quick-login/dist` → 安装后位于 `<安装目录>\resources\extension`，**无需联网下载扩展**。
  - 账号中心新增「未检测到浏览器扩展」提示条（数据源 `GET /extension/health`：TTL 60s 内有无执行面上报；**仅在从未连上过**时出现，关掉 Chrome 不会反复提示），按钮「一键安装引导」→ `POST /extension/setup-helper` → 桌面壳控制服务 `POST /extension-setup`：自动打开 `chrome://extensions` + 打开随包扩展目录 + 弹出分步说明；托盘菜单同样新增「安装浏览器扩展…」。
  - **为什么做不到静默一键装（逐条实证）**：Chrome 在 Windows 上拦截非商店 `.crx` 安装；`ExtensionInstallForcelist` 在 HKCU 下普遍不生效（[SO](https://stackoverflow.com/feeds/question/36208439)）、自托管 `update_url` 亦常见失败（[SO](https://stackoverflow.com/feeds/question/49473933)）；本仓库**无签名私钥**（只有 manifest 公钥 `key`），无法用既有 ID 重打 CRX。取舍与依据见新增 `docs/EXTENSION-INSTALL.md`（含"要真一键只能先上架商店"的后续路径）。代价：用户首次点 4 下，之后永久可用。
  - `tools/build.ps1` 增补：bump 之后**自动重建扩展**并校验包内 `manifest.json` 版本号 == 本次发布版本号（此前扩展不进安装包，故无此风险）；release commit 一并纳入扩展 manifest/package 与 `.version.json`。

### Changed

- **「桌面点击 → 浏览器打开」延迟优化（0.2.24）：约 1.1s → 约 30ms**。实测定位到延迟主项是**扩展每 2s 轮询一次指令队列**（量化延迟 0~2s，均值 ~1s），再叠加点击路径上一次 `tasklist` 子进程探测（~124ms）且与指令入队**串行**：
  - **长轮询取代轮询节拍**：`GET /extension/commands` 新增 `wait` 参数——无指令时服务端挂起请求（`threading.Condition`；`dispatch_command` 入队即 `notify_all`），指令延迟从「等下一拍」压到一次本机回环。默认 `wait=0` 保持原非阻塞语义（既有调用方与 `tools/verify_extension_sync.mjs` 不受影响）。A/B 实测：**平均 964ms → 20ms（≈50×）**，最坏 1671ms → 34ms。
  - **扩展侧改为长轮询流**（`sync.ts`）：新增 `commandStream()` 独立循环（`wait=15`），与 2s 的数据面 `tick()` 解耦。护栏四项：**消费互斥**（`consuming`——否则同一条 `par.open` 会开两个页签）、**流幂等闸**（`streaming`——alarm 每次触发都会调用它，无闸会累积并发循环）、**兜底轮询**（流 >25s 未取回时由 tick 补一次非阻塞拉取）、**离线退避**（`getJson` 返回 null 时退避 1.5s，避免热循环）。SW 被回收时退化为原 alarm 行为，不会更差。
  - **探测缓存 + 并行化**：`/extension/launch-chrome` 的 `tasklist` 探测加缓存（正结果 10s / 负结果 2s；实测探测成本 124ms → 命中 9ms，14×），拉起后立即标记「运行中」防连点多开；`wheel-picker.html` 与 `accounts.js` 的点击路径改为 launch-chrome 与指令入队**并行**（两者仍都 await 完才关轮盘——关窗会中断在途 fetch，冷启动时若 launch 没发出去会永久滞留指令）。
  - 新增回归工具 `tools/verify_command_latency.py`：独立实例上 A/B 旧节拍与长轮询，钉住该延迟契约（误删 `notify_all` 或 `wait` 会立刻红）。
  - **尚未优化（需产品决策）**：**Chrome 冷启动**（tasklist 124ms + Chrome 启动 1~3s 是硬成本）。可选两条：① `launch-chrome` 直接带登录 URL 打开，省掉「启动 → SW 起来 → 取指令 → 开页签」的往返（需配一条「绑定已开页签」的指令）；② 桌面应用启动或账号中心打开时预热 Chrome。

### Fixed

- **两个静默失效缺陷（0.2.23，红/绿验证）**：
  - **虚拟 Cookie 袋被无关操作清空**：MAIN 壳的 `Storage.prototype.clear` 补丁未区分存储实例，而 Cookie 袋住在 **localStorage** 命名空间——页面调 `sessionStorage.clear()` 会把袋（含 token）一并清空 = 登录态被无关操作销毁。修法：仅当 `this === window.localStorage` 才重置袋子（探测用 `try/catch`，避免沙箱 iframe 里裸读 `win.localStorage` 抛 SecurityError 穿出补丁）；`Storage.prototype` 其余补丁（getItem/setItem/removeItem/key/length）语义不变。`onNamespaceWrite` 的两存储 token 同步**刻意保留**（站点把 token 只写 sessionStorage 时仍需镜像进袋）。
  - **多 iframe 页面自动填表静默失效**：`auto-login.fillPasswordInIframes` 在第一个可访问 iframe 上就 `return Boolean(field)`——若该 iframe 不是登录表单，函数直接返回 false → 上层判「密码未填」→ **永不点提交**。修法：扫描全部 iframe（与 `readPasswordValue` 的全量语义一致），返回语义 = 「是否有 iframe 含密码框」。
  - 验证：新增 `tools/verify_extension_isolation.py`，**先红跑复现**（恰好两条 ★ 回归断言失败）**再绿跑 8/8**。

### Added

- **扩展端三个自动化回归工具（0.2.23）**——此前扩展端**没有任何自动化回归**，改动只能靠 typecheck（保类型不保运行期）：
  - `tools/verify_extension_boot.py`：隔离 profile 启动冒烟（SW 能否启动 / 命令清单 / storage 键 / 桌面数据面同步）。⚠ 必须 headful——MV3 扩展在旧 headless 下不加载（实测 `service_workers` 为空）。
  - `tools/verify_host_logic.mjs`：用 esbuild 单独打包 `host.ts` 后跑 22 条断言，锁住端口口径三平面分工（含 `127.0.0.1:18765` vs `:18996` 不串号、默认端口规范化、IP 父域不含端口）。
  - `tools/verify_extension_isolation.py`：自建 fixture 页 + 装载 dist 内容脚本产物，验 Cookie 袋隔离与多 iframe 自动填表（两条 ★ 即上述缺陷的回归点）。

### Changed

- **quick-login 架构清理（0.2.23，用户定稿：不影响功能）**：以「可达性分析 + 协议面审计」为依据清理死代码、合并重复实现，扩展收敛为**执行面**（收 `par.list` / `par.open` / `wheel.toggle` + 六平面隔离）。
  - **死文件 2,060 行**：`ui/parallel/*`（并行管理页 0.2.13 已退役：1,290 + 130 + 629 行）与 `ui/send.ts`。此前 `copyUiStatics` 仍把该页 html/css 复制进 dist，现 `dist/ui` 只剩 popup / wheel / theme.css。
  - **旧会话模型退役（≈260 行）**：删 `session-manager.ts`、`account-registry.ts`、`navigation.ts`（其唯一活口「页签关闭清凭证」迁入新建 `core/pending-login.ts`）、`Session` 类型、`SESSION_KEYS.sessionTabBindings`、`session.*` 协议与 SW 分支、`onErrorOccurred` 里的会话兜底。依据：`session.*` 在扩展内外**均无发送者**（桌面端只发 `par.open`/`wheel.toggle`），其绑定表恒空故兜底恒为 no-op。
  - **并行页专用协议（SW 死分发 ≈275 行，占该函数 55%）**：删 `par.create/update/delete/moveBox/renameBox/deleteBox/probeScheme`、`data.export/data.import`、`DataBackup`。它们不只是「没人调」——`sync.ts` 每 2s 用桌面快照对账并删除快照外账号，故本地增删改会在 2s 内被撤销，属**语义冲突**而非休眠能力。⚠ `parallelStore` 的增删改**保留**（`sync.ts` 直接调用，是活的数据面）。
  - **按 0.2.21 定稿保留**：`site-auth.ts` 与 `site.grants.*`（授权核心）、`par.grantChanged`（规则重装钩子）、`ql.diag`（台架/现场诊断）、`wheel.toggle`（桌面通道）。如实说明：清掉并行页协议后 `site-auth` 已无运行时入口，成为「休眠源码」（仅 `Scheme` 类型被引用）。
  - **协议键收敛为单一真源**：`__ql_ns_` / `__ql_cookies__` / `__auth_token__` / `__auth_user__` / `__device_fp__` / `QL_PAGE_TO_BRIDGE` / `QL_BRIDGE_TO_PAGE` 此前在 `shield-main.ts` 与 `parallel-session.ts` 各自硬编码（17 处），现全部 import `shared/constants.ts`（新增 `SHIELD_USER_KEY` / `SHIELD_DEVICE_FP_KEY`）；并在该模块注明「会被 MAIN world import，顶层禁止任何扩展 API 求值」。
  - **合并重复实现**：`applyTitle`（navigation 与 parallel-session 逐字重复）→ `background/tabs/tab-title.ts`；待登录凭证「键格式 + 读写」两份 → `background/core/pending-login.ts`；host/端口五函数（分居 tab-rules 与 parallel-session）→ 新建 `background/core/host.ts`；`sync.ts` 的盒子键字面量 → `LOCAL_KEYS`。
  - **顺带修掉潜伏 bug**：`parentDomainOf` 带端口入参时两个 `return` 仍返回**带端口的原 host**（契约要求 DNR 域不含端口；此前调用方都预剥端口故未暴露）。
  - 死代码：`parallelSession.isBoundTab` / `hostOfTab` / `deleteAccount` / `refreshTitle`、恒真分支 `details.tabId <= 0`、恒真表达式 `tabId !== null && reusedFlag`。
  - **验证**：typecheck 0 错误；build OK；隔离 profile 启动冒烟 7/7；`host.ts` 行为断言 23/23（端口口径三平面分工逐条锁定）；协议键复查——全仓库仅 `constants.ts` 保留定义，两个 bundle 内仍各出现 1 次（跨进程约定未变）。
  - **未做（需单独决策/回归）**：审查另发现 2 个**真功能缺陷**（`shield-main` 的 `Storage.clear` 补丁会让 `sessionStorage.clear()` 清空虚拟 Cookie 袋 → token 丢失；`auto-login.fillPasswordInIframes` 首个可访问 iframe 无密码框即 early-return → 多 iframe 页面自动填表静默失效），以及 bridge 上行入口缺 `.catch`、`title-hook` 观察器过宽等加固项——均属**行为变更**，不纳入本次「不影响功能」的清理，另行排期。

### Added

- **数据层决策与维护工具（0.2.22）**：实测 `workbench.db` 曾达 **82.02 MB**，其中 **74.03 MB 是 `doc_chunks.embedding`**——知识库功能下线后遗留的向量数据，运行时代码**零处读取**（全仓库仅 `db.py` 的迁移定义与 `storage.py` 的一句注释提及这两张表）；空闲页仅 0.25 MB，说明不是碎片而是「仍然存活但已无人使用」的行。真实业务数据合计不到 20 KB（4 账号 / 3 环境 / 5 条任务台账 / 11 项设置），`job_logs` 建了索引却零处写入（日志走磁盘 `log_path`，不入库）。
  - **决策定稿：继续使用 SQLite，不引入 MySQL，不引入 Redis。** 依据与量化触发器见新增 `docs/数据层决策.md`。要点：① Redis 不是数据库——RDB 间隔快照 / AOF `everysec` 均有丢失窗口，而本项目核心资产是 Fernet 加密凭据（`password_enc` + `account_fernet_key`），**丢失不可重建**，且 Redis 无关系约束（现依赖 `ON DELETE CASCADE`），数据还须全驻内存；② MySQL 的决定性理由是**交付形态而非性能**——单机安装包（Electron + 本机 sidecar + Inno Setup）多装一个服务端守护进程（服务账号/端口/root 密码/升级/防火墙）会直接损害「一键安装」卖点，而 `uvicorn` 默认 `workers=1`、`db.py` 单连接 + 全局 RLock 的单写入负载下，MySQL 相对 SQLite WAL 没有任何收益；③ 反例佐证：`routes_extension.py` 的扩展指令队列**刻意**用内存 `_commands` + ack + 单调 `ext_cmd_seq` 游标实现，未拉 Redis——⚠️ 该队列与 `_STATE` 均为进程内状态，是**多 worker/多机部署时最先静默失效的地方**，届时修法是把指令队列落成数据库表，而不是上 Redis。
  - 新增 `tools/db_maintenance.py`：默认只读诊断（表规模 / 大列体积 / 遗留 settings 键 / 独占锁探测），`--all` = `VACUUM INTO` 一致性备份 → 置空遗留向量（**保留文档与分块正文**）→ `VACUUM` 回收。刻意**不做成自动迁移**：迁移历史不可变，且「自动删除用户数据」的迁移风险过高。真机执行结果：**82.02 MB → 2.90 MB（回收 79.12 MB）**，`PRAGMA integrity_check = ok`、`foreign_key_check` 无违规，129 份文档 / 3,420 个分块正文全保留、4 账号 3 环境与盒子/分配池/导出备份全部正常，桌面端重启后服务 200、模块 4/4。
- **迁移 13 `add_created_at_indexes`**：补齐列表查询索引 `agent_audit(created_at)`、`blueprint_jobs(created_at)`、`insight_runs(created_at)`——三处列表接口均为 `ORDER BY created_at DESC LIMIT ?`，而既有索引只建在 `tool` / `status` 上；另加 `account(box)` 对齐 `rename_box` 的 `UPDATE ... WHERE box = ?`。已在真实数据副本上先行验证：升至版本 13、四个索引到位、账号/分配池/盒子/导出备份查询全部正常，随后 50 项 pytest 全绿、ruff 通过。

### Changed

- **`db.py` 增设「方言边界」一节（0.2.22）**：为「万一将来改判为服务端多人部署」预留切换缝，SQLite 特有构造全部收拢并注明——`PRAGMA` → `_PRAGMAS`；逗号串包含匹配 `(',' || col || ',') LIKE ?` → 新增 `db.csv_like()`（`pool_members` 已改用，组合值语义由既有测试覆盖）；`?` 占位符全库统一（psycopg/PyMySQL 为 `%s`，改 wrapper 即可）；`INTEGER PRIMARY KEY AUTOINCREMENT` 仅存于迁移 8 的 `job_logs.id`（历史不可变，保留），**新表不再使用**（其余表均为 UUID 文本主键，本身与方言无关）。换库 = 改这一节 + 数据搬迁，而非全库搜索替换。
- **扩展弹窗改版（0.2.22，用户定稿三点）**：① **「站点授权」区块整块从 UI 下线**——manifest 已声明 `host_permissions: ["<all_urls>"]`，装载即获全站权限，逐站点授权列表既无操作价值、又误导用户以为仍需手动授权；**只删展示层**，授权/停用名单核心逻辑（`site-auth.ts`、`par.grantChanged` 消息、`ql:blockedHosts`、`isEnforceable`）一律保留，随之删除的仅是 `renderGrantList()`（含其生成的「授权」按钮）与 `.grant*` 样式。② **「导出诊断」移入品牌头右上角**（柔和描边胶囊，保留文字标签而非纯图标，可读屏识别，`title` 说明用途）。③ **版本号从右上角移入页脚右下角**。弹窗高度 303px → 174px。
  - 页脚排版为实测结论：10.5px 字号 + 8px 间距时「提示 + 版本」总宽 305px > 300px 内容宽 → 提示尾字（「盘」「换」）被挤到第二行；改 10px + 6px 后总宽 290px 单行放下，**文案一字未改**。另将品牌头水平内边距 2px → 0，使彩环左缘与「导出诊断」右缘和统计卡两端对齐。
- **撤销上游 Page Monitor（0.2.22，用户定稿）**：该功能监听页面（MAIN 壳嗅探平台名称型 API → 桥上行 `pageNames` → 按 URL 分类解析主体名）并把结果**作用于页签**（合成标题 `账号别名 · 主体名·类型`，未解析时先占位 `类型 · guid前8位`），副产物是「最近配置页 MRU」与其 Alt+W 浮层轮盘。整块下线：
  - **删除**：`background/core/page-monitor.ts`、`content/pages-overlay.ts`、设计文档 `docs/FEASIBILITY-RECENT-PAGES.md`；`build.mjs` 的 `content/pages-overlay` 入口；manifest 的 `quick-pages` 命令（Alt+W）；桥上行 `pageNames` 载荷类型及其在 `parallel-session` 的消费分支；`shield-main` 的名称嗅探（`NAME_API_RE` / `extractNamePairs` / `reportPageNames` 及 fetch/XHR 两处钩子）；`messages.ts` 的 `pages.recent` / `pages.jump` 与 `RecentPageEntry`；`constants.ts` 的 `recentPages` 键与 `RECENT_PAGES_MAX`；`service-worker` 的对应消息分支、`quick-pages` 命令分支与 `togglePagesOverlay`。
  - **保留**：页签标题仍由既有 title 管线（`tabs/tab-title.ts` + `content/title-hook.ts`）权威写入**页签名**——撤销的是「用页面信息改写标题」，不是「标题显示页签名」；账号轮盘（Alt+Q）与浏览器池的监听录制（`services/browser_pool.monitor_start/stop` + `runtime/monitor/`，另一套东西）均未受影响。
  - 隔离 profile 启动冒烟 7/7：SW 可启动且未崩、`commands.getAll()` 仅剩 `quick-wheel`、storage 无 `ql:recentPages`、session 无 `ql:pageNames`、桌面数据面 `akso:acctMap` 仍同步成功。
  - ⚠ 上游同步注意：该功能来自上游 v3.11/v3.13，下次同步会把它带回来（manifest 命令 / 桥上行 op / 内容脚本入口 / SW 注册 / 构建入口），需按本清单再次摘除。
- **桌面轮盘扇区名改为「页签名优先」（0.2.22，用户定稿）**：`wheel-picker.html` 此前用 `a.username`，导致设了页签名的账号在轮盘上仍显示账号名；改为 `(a.tab_name || '').trim() || a.username`，与扩展端 `ui/wheel/wheel-core.ts` 的口径完全一致。另修一处连带缺陷：轮盘防重绘指纹只含 `id`，改页签名不触发重建（标签会一直停在旧名），现将显示名并入指纹。真机数据验证 5/5：`T0901`（页签名 TTTTT）显示 TTTTT，未设页签名的 `dev-admin`/`dev-user` 回落账号名。

### Fixed

- **弹窗/徽标/管理页版本号不随项目升版（0.2.22）**：`shared/constants.ts` 曾硬编码上游版本常量（`EXT_VERSION = '3.13.2'`），而 `tools/bump.py` 的五写只同步 manifest.json / package.json 等、**从不写这个 TS 常量** → 上游同步后版本号永久停旧（实测：项目已 0.2.21，弹窗仍显示 v3.13.2、扩展图标徽标显示 v3.13）。改为 `extVersion()` 读取 `chrome.runtime.getManifest().version`——**唯一真源 = manifest.json**，随后续 `bump.py` 五写自动跟随；弹窗、徽标、管理页、诊断包四处版本号一并同步。用函数而非模块级常量，避免内容脚本 import 本模块时的求值风险（`try/catch` 兜底 `'0.0.0'`）。
  - ⚠ 上游文档 `extensions/quick-login/README.md`、`extensions/quick-login/CHANGELOG.md`、`extensions/quick-login/docs/CODEBASE_OVERVIEW.md` 仍写「`EXT_VERSION` 三处必须一致」，已过时（上游文件，待下次同步时一并校正）。
- **带端口站点误判：0.2.21 那次修正只做了一半（0.2.22）**：0.2.21 给 `hostRelated` 加了「两端都带端口时必须端口一致」的守卫，但调用方仍用 `URL.hostname` 取 host——它**永远不含端口**，守卫 `up && bp` 里的 `up` 恒为空 → 「同主机不同端口是不同站点」从未真正生效。统一改走新增的 `urlHostOf()`（`new URL(url).host`；URL API 已把 http:80/https:443 规范化掉，不会造成假不匹配）：
  - `authHeaderForUrl`（下载归属 ②③ 层）：同主机其它端口的账号会互相串号（内网 `host:8080` / `host:18996`、桌面自身 `127.0.0.1:18765` 即典型）；
  - `adopt-candidate`（继承页签收编）：落到同主机别的端口会被当成"本站"而收编（身份头经 DNR 端口无关地注入）；
  - `onNavigation`：「是否本站」继续按无端口比（与 DNR `requestDomains` 同口径），但停用名单与健康缓存改用**带端口** host 查——名单条目来自 origin 推导（`192.0.2.10:8080`）、查询却用无端口 `192.0.2.10` → 永远查不到，**带端口站点的停用此前静默失效**。
  - **口径分工（勿混）**：身份平面（归属/停用名单/收编）端口参与比较；规则覆盖平面（DNR `requestDomains`）与 Cookie 作用域（RFC 6265）端口天然不参与，那两处按无端口比较是**正确**的，本次未改。
- **停用名单命中改双形匹配（0.2.22）**：新增 `blockedHit()`——带端口精确命中，或条目不带端口时覆盖该 host 全部端口（历史数据/仅填 host 的配置兼容）；带端口条目不跨端口误伤，与站点身份口径一致。`isEnforceable` 与 `syncAccountRules` 同步改用它。
- **`parentDomainOf` 入参先剥端口（0.2.22）**：带端口内网 host（`192.0.2.10:8080`）会让"全数字段=IP 字面量"判定失配，进而拼出 `0.105:8080` 这类无意义父域。当前调用方均已预剥端口，此为幂等加固（防后续踩坑）。

### Changed

- **扩展改为声明式全站权限，按站点授权整套下线（0.2.21，用户定稿）**：`manifest.host_permissions` 从"仅 127.0.0.1:18765 + `optional_host_permissions: ["*://*/*"]`（逐站点申请）"改为 **`["<all_urls>"]`**。
  - **为什么**：逐站点授权意味着每加一个新站点都要在浏览器弹窗点一次「允许」，未授权期间 DNR 改头规则**能装上但不生效**（实测：`permissions.contains` 为 false 的源上 `modifyHeaders` 静默失效）→ 多账号隔离与下载补身份全部哑火，且没有显式报错。这是用户实测"下载 401 却看不出原因"的根源。改声明式后**装载即获得全站权限、永不弹窗**（实测：`permissions.getAll().origins = ["<all_urls>"]`，且任意站点上 DNR `modifyHeaders` 立即生效）。代价是一次性授予全站访问权限、权限升级时可能需要在 `chrome://extensions` 重新加载/启用一次扩展。
  - **随之移除的组件**：内容脚本 `content/site-grant.ts`（页面授权提示条）、授权页 `ui/grant/`、消息 `site.grant.state` / `site.grant.request`、`site.grant.open` 桌面指令、桌面站点行授权徽标与「授权」按钮、账号卡「未授权」徽标、`site.grant.*` 相关样式与提示位；`grantStateForHost` / `requestHostGrant` / `probeHostAccess` 一并删除。
  - `isEnforceable` 语义随之简化：全站权限下"浏览器授权"不再是变量，唯一能关停某站点网络平面的只剩**用户手动停用名单**（`ql:blockedHosts`）；20s TTL 缓存保留（名单变更即时生效）。
- **规则安装不再被授权判定门控（0.2.21）**：`syncAccountRules` 与 `parallelSession.restore` 无条件安装绑定规则（DNR host access 自身就是生效门控，未授权源上规则静默不生效、无害）。此前"授权判定误判为 false"会让规则**永久不装**且 UI 无任何提示——用户实测下载 401 的直接根因。唯一保留的硬门控是手动停用名单。
- **401/403 自愈（0.2.21）**：绑定页签被服务端拒绝时（节流 10s/页签）强制刷新网络平面健康缓存并重装该页签规则——"规则没装上/装的是旧 token"这类静默失效无需重启扩展即可自愈。

### Fixed

- **下载失败"无法单账号归属"（0.2.21，用户实测仍未修复）**：`authHeaderForUrl` 旧实现要求"全库中该 host 只有 1 个账号"才敢重试，而实测站点是**一站两账号**（示例租户：dev-user / T0901）→ 永远返回 null → 下载 401 后从不重试（诊断包原话：`下载失败（无法单账号归属，不自动重试）`）。改为分层归属：①同 URL 被 webRequest 观察到的绑定页签账号（最精确）→ ②该 host 上**唯一有活跃绑定页签**的账号（正在浏览该站点的账号）→ ③全库唯一账号（原兜底）；都不成立才不注入。实测 `chrome.downloads.download({headers})` 不需要 host 权限即可带出 Authorization（Chromium 早已移除该校验），故该重试路径不受授权状态影响。
- **带端口站点被误判为"漫游到非授权域"而解绑（0.2.21）**：`onNavigation` 用 `URL.hostname`（永不含端口）与 `binding.host` 直接比较，站点自身 host 带端口（如 `192.0.2.10:8080`）时永远不相等 → 一打开就被解绑（规则不装、身份不注入）。比较前先去端口。
- **同主机不同端口被当成同一站点（0.2.21）**：`hostRelated` 仅比较域名，导致桌面自身页面 `127.0.0.1:18765` 会被当作内网站点 `127.0.0.1:18996`（下载归属、Cookie 归属、站点判定的误判来源）。现两端都带端口时要求端口一致。
- **诊断日志被桌面同步轮询淹没（0.2.21）**：桌面同步端点（`127.0.0.1:18765`）的响应此前被归属给"唯一绑定账号"，每 2s 刷两条 `captureResponseCookies：无页签响应…`，把 120 条环形诊断缓冲冲掉、真问题被挤出。此类 URL 不再计入。

### Changed

- **账号中心 UI 复刻一期（0.2.8）**：按上游 quick-login 管理页视觉与功能基线重写（`accounts.html`/`accounts.js` + 移植 `ql-theme.css`/`ql-parallel.css`）。落地：顶栏品牌头 + 实时统计（账号/在线/盒子）+ 版本 chip；盒子 chips 悬停操作（✎ 重命名 / ⏸▶ 禁用启用 / ✕ 两步删除——连同账号删除或并入默认盒）；**批量管理模式**（勾选 + 已选计数 + 批量移盒/删除）；**移入盒子弹窗**（单选带计数 + 新盒名自动创建，替代裸 prompt）；**四态实时徽标**（在线×N/待登录/离线/未授权·已暂停——新增扩展执行面状态回传通道 `/extension/state`，扩展每 ~6s 上报绑定页签数/token/授权暂停）；**指纹防闪烁渲染**（数据未变不重建 DOM）；轮盘 0 键=第 10 账号（页内覆盖层 + Electron 轮盘窗）；禁用盒轮盘跳过（settings 表 `disabled_boxes`）；平台环境管理收进对话框、批量添加收进折叠面板——消除提示冗杂。DOM 验证 11/11（`tools/verify_accounts_ui.py`）。

### Fixed

- **Fernet 双 bug（0.2.7，E2E 最后一公里）**：①扩展端 fernetDecrypt 键位写反——Fernet 规范 sign-key=key[:16]/enc-key=key[16:32]，原实现互换导致 HMAC 全失败、账号被静默跳过；②WebCrypto AES-CBC 已自动去 PKCS7 填充，原代码再按尾字节手工剥离，把口令尾字符当填充长度剥掉。修复后**隔离 Chrome 全链路真机验收通过（E2E_PASS）**：快照同步 2/2 → par.open 开登录页 → 自动填表提交 → 进入平台工作台。新增 `tools/acceptance_extension_e2e.py` 可复用验收脚本。

### Changed

- **扩展基线上游同步（0.2.6）**：`extensions/quick-login` 整体前移 **v3.11.0 → v3.13.2**（36 文件），重放全部私有改造（manifest 18765/alarms/Ctrl+Shift+Q、sync.ts+account-wheel.ts 保留、service-worker 挂载+抽取、parallel.html 隐藏数据区块、wheel-overlay interval 修复）。带入上游 8 个版本的能力：登录态生命周期跟随页签（3.12.0 免密复制语义/最后页签关闭终结登录态）、绑定时 Cookie 袋权威同步（3.12.1）、自动登录逐事件取证黑匣子+管理页导出诊断（3.12.2）、AuthCode 入时效集（3.12.3）、亲子继承候选期零种子（3.13.0）、AUTH 规则补 main_frame（3.13.1）与全资源类型（3.13.2）。typecheck 零错误验证 sync.ts 与新 API 兼容。

### Added

- **扩展连通性专项（0.2.5）**：桌面↔quick-login 扩展链路断点修复——快照 host 字段错位（envBaseUrl）、指令 seq 跨重启持久化（settings 表单调递增）、host 保留端口 + scheme 随快照下发、扩展 chrome.alarms 30s 保活复活、wheel.toggle 改直调（SW 自消息死链）、毒指令逐条隔离（不再卡死队列）、快照空载删除护栏、tabName 缺失护栏、凭据解密失败留痕、wheel-overlay 双 interval 泄漏修复；扩展 quick-wheel 热键让位（Alt+Q→Ctrl+Shift+Q，避免与 Electron 全局热键抢占）；账号中心页内轮盘选人补 launch-chrome；新增 `tools/verify_extension_sync.mjs` 离线模拟扩展验收脚本。
- **浏览器分配政策落地 + Monitor 监听接入托管会话（0.2.2）**：政策定稿——快捷登录/打开 = 用户 Chrome（扩展指令 par.open + launch-chrome）；监听/自动化 = 应用内置 Chromium（browser_pool，cdp = Electron 壳）。`routes_monitor`（/api/monitor/start|stop|status）；browser_pool 新增 monitor_start/stop（自动开户 → MonitorSession 挂 page → 产物落 runtime/monitor/）；卡片 UI（快捷登录主按钮 + 监听会话/开始/停止监听 + 监听中 chip）；routes_browser 补 `/navigate/{account_id}`。真机验收：248 请求捕获（dropped 238 / trimmed 2 / full 8），三级降噪正确，monitor-log 落盘。
- **桌面壳迁移 Electron（pywebview 退役）+ browser_pool CDP 复用模式**：`desktop/main.js`——Python sidecar 生命周期（打包态 AksoServer.exe --server / 开发态 venv uvicorn）、主窗、托盘、全局热键 Alt+Q、electron-updater 自动更新（latest.yml）；会话控制服务 :18767（按 windowId 开户，每账号 `persist:` 独立持久分区 / 聚焦 / 关闭）；browser_pool 经 `connect_over_cdp(:18766)` 复用内置 Chromium（`_CdpState` 单实例持有者）。
- **quick-login 混合架构（v0.1.2）**：扩展执行面 + 桌面数据面/触发面。`routes_extension` 同步蓝图（快照：账号/盒子/站点，凭据以「Fernet 密文 + 密钥」经 127.0.0.1 回环下发；指令队列 wheel.toggle/par.open，内存态 + ack）+ 扩展 `sync.ts`（2s 轮询、snapshotId 内容哈希幂等、WebCrypto Fernet 解密直连 parallelStore、桌面不可达离线回退）。扩展源码入库 `extensions/quick-login/`。
- **轮盘 v2**：径向扇形无边框半透明独立窗（Electron 承载）+ 扩展 sync v2 直连 store + Chrome 拉起。
- **桌面壳重构与实机调通**：`shell/shell.py` 改为「uvicorn 子进程 + webview 主线程」架构（服务与窗口生命周期解耦，关闭窗口即退出并回收服务）；pythonw 的 stdout/stderr=None 兜底；pywebview 以 `desktop` extra 入库（`uv sync --extra desktop`）；新增 `tools/start-desktop.vbs` 双击启动器。实机验证：原生窗口可见、服务就绪、首屏体检线程正常。
- **quick-login 全量内化收口**：托管浏览器会话持久化（storage_state——登录成功/正常关闭落盘，重开免密直达、跨进程重启有效；Cookie 袋/DNR 回放的原生等价物）；会话自愈（直达首页被踢回登录页 → 自动重跑节奏门控）；`POST /api/browser/forget/{id}` 登出语义 + `GET /api/browser/saved/{id}`；状态墙持久会话徽章；`tests/test_browser_state.py`（5 项）。真机验证：重开 restored=True 且引擎 phase=idle（完全未走登录页）。迁移台账「不迁清单」重新定性：各项均为"被 Playwright 原生机制等价替代"，原仓库不再是功能归宿。
- **阶段 3 全量完成：akso-cc / akso-auto 原生化（运行时零依赖原项目）**
  - 读路径 `egmp/insight/`：crawler（对象发现链）/assemble+report（盘点）/lifecycle+flowgraph+relations（L3/L2）/annotate/render/drawio/understand/spider（五步织网，step5 用 networkx 图分析）。
  - 写路径 `egmp/writers/`：blueprint（pydantic 两层校验+规范化+审阅三件套，替代 ajv）/idempotency/objects/fields/picklists/lifecycle/workflows（a-i 管道+两次提交连线）/layouts（全量替换语义）/menus/endpoints（自原仓库只读提取的实证常量表）。
  - 编排 `egmp/orchestrate.py`（runFullWorkflow/拓扑排序/checkpoint 落盘）+ `complexity.py`（规则移植）+ `generate.py`（DeepSeek 蓝图生成融合：规范化+校验回炉）。
  - Monitor `egmp/monitor/`：Playwright 录制三级降噪/查询层与参数推断/segment 解读+reproduce-plan（API_MAP 对位 Python writers；DANGEROUS 永不回放）。
  - `routes_insight` / `routes_factory` 切换原生调用；`routes_agent` 工具同步原生；`adapters/*.json` 降级为只读参考存档；Node 不再是运行时依赖。
  - 测试：`tests/test_native.py`（12 项，FakeEgmpClient 内存平台离线验收校验/编排幂等/盘点/理解/蜘蛛/Monitor）+ `tests/fakes.py`；真机验收：原生 login / understand（training_hjy__c，1.4MB 理解模型）/ spider 均在真实平台跑通。
- 架构分析文档 `docs/架构分析.md`：总体架构图、四条设计底线、模块分布地图（11 组 40+ 模块的职责/血统/依赖）、数据流图、DB 迁移版本账、三层测试策略与逐模块回归集、排障速查表、维护红线、新模块 checklist、演进触发器。
- 双人协作基建：`CONTRIBUTING.md`（开发协作规范）、`.editorconfig`、`tools/setup.ps1`（环境自检脚本）；uv 默认走清华镜像（`[[tool.uv.index]]`，随仓库分发）。
- 质量门禁：ruff 规则集（E4/E7/E9/F/I/B）入 `pyproject.toml`，全仓通过；修复 19 处（未用导入/未排序 import/zip strict/异常链 from exc 等）。
- 模块契约 §1 补强 pydantic 约定：请求体强制 BaseModel，新代码响应强制 response_model。

### Changed

- **构建分发链 v3（Electron）**：PyInstaller 服务端 sidecar（`dist/AksoServer` onedir，含 chromium 本体）→ electron-builder NSIS（`desktop/dist/AksoWorkbench-<ver>-setup.exe` + `latest.yml`，GitHub Releases 承载 electron-updater 增量更新）。Inno Setup（`tools/installer.iss`、`tools/ChineseSimplified.isl`）退役，2026-09-10 删除。
- **bump.py 三写机制**：版本写入 pyproject + `workbench/__init__.py` + `desktop/package.json`（electron-builder 以 package.json 版本命名安装包与 latest.yml；此前漏写导致版本错位，需手工对齐提交补救）。
- `pyproject.toml`：uvicorn 对齐为 `[standard]` 变体（与实测环境一致）；依赖变更一律走 `uv lock && uv sync`。

### Fixed

- **sync-Playwright 事件回调内禁止连接调用**（response.text()/page.evaluate 重入 = worker 死锁）——事件只入队，worker 作业间隙统一 drain（响应体补捞 + 动作缓冲出栈）。
- **cdp 模式三修**：pw 单线程单实例持有者 / 先开户后连接（Electron33 挂接怪癖）/ 平台异步鉴权复核；logging_in 会话复用（禁二次开户）；close_all 连接池清理。
- 轮盘单例 toggle + js_api 关闭/拖动（从误 stash 中恢复的关键修复）；启动会话 openAccount ReferenceError；账号编辑对话框与批量添加链路。

- 立项：四项目融合重建（akso-cc / mogul_simulator / akso-auto / quick-login），原项目冻结不动。
- 阶段 0：uv 工程定义（`pyproject.toml`）、模块契约（`docs/模块契约.md`）、迁移台账（`docs/迁移台账.md`）、只读适配器声明（`adapters/akso-auto.json`、`adapters/akso-cc.json`）。
- 阶段 1A：fork mogul_simulator/mogul → `workbench/`（包名、数据目录、端口可并存；数据库兼容接管 mogul.db）。
- 阶段 1B：Node 子进程统一封装 `services/proc.py`；模块注册表 `services/modules.py` + `adapters`；模块健康体检路由 `api/routes_modules.py`。
- 阶段 1B：平台洞察路由 `api/routes_insight.py`（封装 akso-cc login/inventory/understand/spider，产物读取回传）。
- 阶段 1B：配置工厂路由 `api/routes_factory.py`（封装 akso-auto create/编排/monitor，蓝图暂存 + 断点状态展示）。
- 阶段 1B：前端页 `factory.html` / `insight.html` + 对应 js/css（复用 mogul 设计系统，四模块 Tab 导航）。
- 阶段 1B：pywebview 桌面壳 `shell/shell.py`（起 uvicorn + 开窗口 + 依赖体检首屏）。
- 阶段 2A：统一账号库（DB 迁移 7：platform_env / account 表，Fernet 凭据加密；卡片墙 UI；三项目 env 一键导入脚本）。
- 阶段 2B：托管浏览器 `services/browser_pool.py`（Playwright 账号↔context 池）；自动登录引擎 `services/autologin.py`（quick-login 节奏门控全量迁移：齐备门槛/提交前回读/MutationObserver 即时填充/用户点击接管/失败让位 + srcdoc iframe 探测 + token 捕获）；路由 `api/routes_browser.py`；前端页 `browser.html`；机器验收 `tests/test_autologin.py`（9 项断言）。
- 阶段 3：egmp 平台客户端 Python 化内核（client/auth/cache/checkpoint + writers/insight/monitor 骨架）与 Agent 工具层（`routes_agent.py`：自然语言→登录/读配置/写配置/查知识）。
