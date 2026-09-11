# 浏览器扩展安装说明（终端用户 + 技术取舍）

> 桌面端安装包本身一键装完即可用；**唯一需要手动一次**的是把 Chrome 扩展加载进浏览器。
> 扩展已随安装包携带（`<安装目录>\resources\extension`），不需要联网下载。

## 一、用户操作（一次性，约 30 秒）

1. 打开**账号中心**（桌面端主窗），点右上角提示条里的 **「一键安装引导」**
   （或右键任务栏托盘图标 → **「安装浏览器扩展…」**）
2. 桌面端会自动：**打开 `chrome://extensions`** + **打开扩展所在文件夹**（资源管理器窗口）
3. 在 Chrome 里：
   - 打开右上角 **「开发者模式」** 开关
   - 点 **「加载已解压的扩展程序」**
   - 在文件夹选择框里选中桌面端刚打开的那个目录（`resources\extension`，**选它本身，不要进子目录**）
4. 完成。回到账号中心，提示条消失、「在线」徽标与 **Alt+Q** 轮盘随即可用。

账号数据由桌面端自动下发（快照每 2 秒对账），**不需要在扩展里再建账号**。

### 注意事项

- **不要移动/删除** `<安装目录>\resources\extension`：Chrome 是"就地加载"该目录，删了扩展即失效。
  卸载桌面端前请先在 `chrome://extensions` 里移除该扩展。
- Chrome 启动时可能提示"请停用以开发者模式运行的扩展程序"——**点 × 忽略即可**，不要点"停用"。
- 扩展 ID 为 `bingdkdlocnmdheghbmpnjilamcbciek`（由 manifest 的 `key` 固定，不随目录变化）。

## 一之二、桌面端升级后：扩展需要"重新加载"一次

扩展文件随安装包一起更新（新版安装包覆盖 `<安装目录>\resources\extension`），但 **Chrome 只在
"重新加载"后才会用上新版本**——不重载就会一直跑旧代码。因此：

- 桌面端升版后，账号中心会显示「**浏览器扩展版本过旧**」提示条，点「**重新加载引导**」即可
  （或托盘 →「安装浏览器扩展…」）；照提示在 `chrome://extensions` 点一次该扩展的「重新加载」（↻）。
- **重新加载只重启扩展本身，账号与登录态不会丢**（数据在桌面端 SQLite + 扩展本地加密存储里）。
- 判据（自动检测，无需用户判断）：扩展每 ~6s 上报自身版本（`POST /extension/state` 的 `extVersion`），
  桌面端与自身版本比对 → `GET /extension/health` 返回 `extVersion` / `desktopVersion` / `extStale`。
  两者**同号发布**（`tools/bump.py` 一次写全），所以"不等"就等于"Chrome 里是旧扩展"。
- 为什么不自动 `chrome.runtime.reload()`：重载会掐断进行中的自动登录（SW 重启、页签脚本失效），
  风险不可控——改为**提示用户点一次**，把不可控的时序留在用户手上。

## 二、为什么做不到"静默一键装"（实证取舍）

Chrome 对**非 Chrome 网上应用店**的扩展有硬限制，逐条查证如下：

| 方案 | 结论 | 依据 |
| --- | --- | --- |
| 双击/拖入 `.crx` 安装 | ❌ Windows 上被 Chrome 直接拦截（"只能从 Chrome 网上应用店添加"） | [官方分发文档](https://developer.chrome.com/docs/extensions/mv2/external-extensions) |
| `ExtensionInstallForcelist` 策略强制安装（HKCU） | ❌ 普遍不生效（社区实测"won't install when using HKCU"） | [SO 36208439](https://stackoverflow.com/feeds/question/36208439) |
| 策略 + 本地/自托管 `update_url` | ❌ 本地服务器托管常装不上；且 Windows 上自托管扩展另有域加入（domain-joined）等前置条件 | [SO 49473933](https://stackoverflow.com/feeds/question/49473933) |
| 随包 `.crx` + 签名私钥 | ❌ 本仓库**没有签名私钥**（只有 manifest 里的公钥 `key`），无法用既有 ID 重新打包 | 仓库实测：全库无 `.pem`/`.key` |
| 上架 Chrome 网上应用店（不公开列出） | ⚠️ 唯一能做到"真·一键"的路径，但需开发者账号（$5）+ 审核周期，且更新需重新过审 | 官方分发文档 |
| `chrome.exe --load-extension=<dir>` | ⚠️ 仅在 Chrome **未运行**时有效（已运行则参数被转发忽略），且属于临时加载、重启浏览器即消失，会造成"装了又没了"的困惑 | 实测语义 |
| **随包携带 + 应用内一键引导（当前方案）** | ✅ 零管理员、零联网、零签名、任何机器可用；代价是用户首次点 4 下 | 本方案 |

补充：`HKCU` 与 `HKLM` 的策略差异、以及"本地 update_url 被拒"这两条，是社区反复踩坑点；
若将来要做真·静默分发，正确顺序是 **上架商店** → 再把商店 ID 写进策略（`ExtensionInstallForcelist`
指向商店 ID 是官方支持路径），届时本节可整体替换。

## 三、给开发/运维

- 打包：`extensions/quick-login/dist` 由 `npm run build`（在工作区根 `extensions/quick-login`）生成；
  `tools/build.ps1` 已在 bump 之后自动重建它，保证**包内扩展版本号与桌面端一致**。
- 打包产物校验：`desktop/dist/win-unpacked/resources/extension/manifest.json` 的 `version`
  应等于本次发布版本号。
- 安装引导的接口：
  - `POST /extension/setup-helper`（Python，18765）→ 转发给桌面壳控制服务；`body={"mode":"reload"}`
    走"重新加载"文案（安装 vs 重载共用同一套壳能力）
  - `POST /extension-setup`（Electron 主进程，18767）→ 打开 `chrome://extensions` + 扩展目录 + 步骤弹窗
  - `GET /extension/health` → `{connected, everConnected, extVersion, desktopVersion, extStale}`
    （`connected` = TTL 60s 内是否有执行面上报；`everConnected` = 本安装实例是否连上过 ≥1 次；
    `extStale` = Chrome 里的扩展版本 < 桌面端版本 → 账号中心提示条据此显示"需重新加载"）
  - `GET /extension/snapshot` 回传 `desktopVersion`，扩展据此自查版本（并在 SW 控制台留痕）
- 更新面的接口：
  - `GET /api/update`（Python，18765）→ `{version, shell:{live, phase, availableVersion, percent, updateAvailable}}`；
    数据源是壳写的 `%APPDATA%\AksoWorkbench\shell-state.json`（壳每 15s 心跳，超 60s 视为陈旧 → `live=false`）
  - `POST /api/update/check` / `POST /api/update/install` → 代理到壳控制服务
    `POST /update-check` / `POST /update-install`（弹窗与安装动作都由壳执行，服务端只转发结论）

## 四、发布更新到 GitHub Releases（维护者）

桌面安装版用 `electron-updater` 自动更新（通道 = **GitHub Releases**；策略 = 启动静默检查
+ 后台静默下载 + 退出应用时安装 + 托盘「检查更新…」手动检查）。**私有仓库不行**——更新器
匿名读取 Release，仓库必须公开（本仓库即公开）。

发布用到的 GitHub Token（**只做一次配置，且只用于"推 Release"这一步**）：

### 1. 取得 Token（唯一官方路径）

1. 登录 GitHub → 右上角**头像** → **Settings**
2. 左栏拉到最底 → **Developer settings**
3. **Personal access tokens** → 二选一：

   | 类型 | 直达路径 | 需要的权限 | 有效期 |
   | --- | --- | --- | --- |
   | **Fine-grained**（GitHub 现推荐） | Developer settings → Personal access tokens → **Fine-grained tokens** → `Generate new token` | **Repository access** = `Only select repositories` → 选 `jizi-dragon/akso-mogul`；**Permissions → Repository permissions → Contents = Read and write**（`Metadata` 会自动变 Read-only） | 最长 1 年（可自定义） |
   | **Classic**（最省事） | Developer settings → Personal access tokens → **Tokens (classic)** → `Generate new token (classic)` | 勾选 **`repo`**（一次勾选即含 Release 资产读写） | 可设 90 天 / 无到期 |

4. **Generate token** → 页面只显示一次，**立刻复制**（形如 `ghp_…` 或 `github_pat_…`）。
   离开页面后再也看不到，只能重新生成。

页面直链（登录后可用）：<https://github.com/settings/tokens>（fine-grained）
/ <https://github.com/settings/tokens/new>（classic）

**不需要**的权限：`workflow`、`admin:*`、`packages`、`delete_repo` 一律不勾。

### 2. 用它推 Release（二选一）

```powershell
# 方式 A：只在本次构建时注入（推荐，用完即弃的环境变量）
$env:GH_TOKEN = "ghp_你的token"
cd D:\ai_assistant\akso-mogul\desktop
npx electron-builder --win nsis --publish always      # 上传安装包 + latest.yml(+blockmap) 到 Release

# 方式 B：不带 token 构建，然后在 GitHub 网页上手动传
#   仓库 → Releases → Draft a new release → 选 tag（如 v0.3.4）→ 上传
#   desktop\dist\AksoWorkbench-<版本>-setup.exe、latest.yml、（有则）*.exe.blockmap
```

- 用方式 A 时 **tag 必须与版本号一致**（`npx electron-builder` 会用 `package.json` 的 version
  自动建 `v<版本>` tag）；先 bump 再发布，顺序反了会发布错版本。
- 校验 Token 是否有效：`curl.exe -H "Authorization: Bearer $env:GH_TOKEN" https://api.github.com/user`
  → 返回 200 与你的用户名即正常（401 = token 无效/过期）。
- Token 泄露或不再需要：Settings → Developer settings → Tokens → **Delete/Revoke**。
- 更新器读取 Release 的 `latest.yml` 判断是否有新版（`publish` 配置见 `desktop/package.json`）。
  用户侧：应用启动后 20s 静默检查 → 有新版后台下载 → **退出应用时自动安装**；
  也可点托盘「检查更新…」或账号中心右上角版本角标手动检查。

