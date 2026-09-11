# ADR-0008：更新通道 = GitHub Releases，出网通道默认直连优先

- **状态**：已采纳（0.3.3 更新状态机 / 0.3.4 出网通道自动选择）
- **决策者**：用户定稿（「能直连就别配代理」）
- **相关**：[ADR-0009](0009-nsis-distribution-and-update-channel.md)、[CONFIG.md](../CONFIG.md) §2/§5、
  [API.md](../API.md) §14、[AGENT.md](../../AGENT.md) §5 环境坑

## 背景

两块独立的问题被同一次 0.3.3 → 0.3.4 的真机验证串在一起：

### 问题 1：用户不知道怎么升级

0.3.1 交付了首个可安装版本（349.7 MB NSIS 安装包），但升级路径是「用户自己去 GitHub 找新版下载」。
用户定稿的期望是**零打扰自动更新**。

### 问题 2：能发现新版，却下载不下来（0.3.4 真机实测的诊断）

逐跳实测更新链路：

```
github.com/<o>/<r>/releases.atom          查版本，1.9s，稳定
  → github.com/…/releases/download/…      302
  → release-assets.githubusercontent.com  CDN，很快
```

**本机直连 `github.com` 是间歇性的**：同一 URL 有时 1.1s 成功、有时 20s 超时（都出现过）。
而 **Chromium 默认按 `mode:'system'` 解析代理，实测并不会真的用上系统代理**——
注册表里 `ProxyEnable=1` 摆着，请求仍直连并超时。于是表现为「检查得到版本、下载不动」。

代理（Clash 之类）**稳定但慢**，所以把它当默认通道也是错的。

## 决策

### 1. 更新通道 = GitHub Releases + `electron-updater`

- **传输**：`electron-updater` 读 GitHub Releases 的 `latest.yml`（含 `sha512` 校验）。
- **触发策略**（用户定稿）：启动后 **20s 首次静默检查** → 之后**每 6h** 一次；
  `autoDownload=true` **后台静默下载**（不弹窗、不抢焦点）；`autoInstallOnAppQuit=true`
  **退出时安装**；托盘「检查更新…」走同一路径但**结论必须弹窗**。
- **为什么不做「静默自动重启安装」**：会打断正在进行的自动登录/页面操作。
  静默下载 + 退出时安装是「零打扰」与「必达」的平衡点。
- **退出顺序不可交换**：`before-quit` 里**先 `killServer()` 再 `updater.installOnExit()`**——
  安装器要覆盖 `AksoServer.exe` 与扩展目录，sidecar 还活着会锁文件。
- **手动检查先落盘再弹窗**（`main.js:checkUpdateFromTray`）：先把 `phase=checking` 同步写进
  `shell-state.json`，再弹结论。否则用户不点按钮时状态会永远停在 `checking`，
  排障与自动化验证都看不到结论。
- ⚠️ **`electron-updater` 匿名读 Release ⇒ 仓库必须公开**（本仓库即公开）。
  私有仓库的自动更新会静默失效。

### 2. 出网通道：默认直连，探测失败才回落代理

优先级（`desktop/proxy.js`）：

```
① AKSO_PROXY / HTTPS_PROXY / HTTP_PROXY 有值     → 强制用该代理（排障用）
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

### 3. 跨进程状态通道：文件，不是 HTTP

壳每 15s（updater 心跳）把状态原子写入 `%APPDATA%\AksoWorkbench\shell-state.json`，
Python 侧 `GET /api/update` 读它，**超 60s 判 `live=false`**（壳已退出/崩溃）。
选中的通道与探测结果一并写进 `shell-state.json` 的 `proxy` 字段并透出到 API——**排障一眼可见**。

## 取舍

### 换来什么

- 用户侧零操作升级：不需要知道 GitHub、不需要手动下载。
- 出网通道自适应：能直连的机器完全不经过代理（实测直连可用时：探测 1131ms、取资产 1201ms，零代理）。
- 排障可视化：`GET /api/update` 直接给出 `phase` / `availableVersion` / `percent` / `proxy` / `lastError`。

### 代价（已接受）

| 代价 | 说明 |
|---|---|
| 依赖 GitHub 可达性 | 受限网络下更新失效；`AKSO_PROXY` / `proxy.txt` 是兜底开关 |
| 启动多付最多 6s | 直连探测超时预算。不探测则可能拿到「发现新版但下载不动」的更差体验 |
| 状态不是实时 | 文件 + 60s TTL。壳异常退出后 UI 最长 60s 才显示「未运行」 |
| 发布是两步手工 | `build.ps1` 只出产物（`--publish never`），发布要另跑 `tools/publish_release.py` 或网页手工上传——见 [ADR-0009](0009-nsis-distribution-and-update-channel.md) |

## 后果

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
  `tools/verify_update_flow.py`（离线自测整条链：`AKSO_UPDATE_OVERRIDE` 指向本地静态目录、
  用 generic provider 替代 GitHub，避免真拉 350MB）。
