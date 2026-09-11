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
  - `POST /extension/setup-helper`（Python，18765）→ 转发给桌面壳控制服务
  - `POST /extension-setup`（Electron 主进程，18767）→ 打开 `chrome://extensions` + 扩展目录 + 步骤弹窗
  - `GET /extension/health` → `{connected}`（TTL 60s 内是否有执行面上报），账号中心提示条据此显示
