# 把浏览器扩展装进你自己的 Chrome

> **这份文档是四处代码注释共同指向的落点**（`desktop/main.js`、`workbench/api/routes_extension.py`、
> `workbench/api/routes_shell.py`、`tools/publish_release.py`）—— 它一度被删掉，而引用还在，
> 于是那四处**指向一个不存在的文件**：其中三处是**给用户看的指引**。
> 删文档时改了 `USER-MANUAL.md` / ADR-0009 的链接，**漏了代码里的引用**。
>
> ★ 纪律：**改文档的存活状态时，`docs/**` 之外也要搜一遍**（代码里的指引、错误消息里的话术）。
> 判据见 §七。

---

## 一、为什么必须你亲手点几下

「多账号快捷登录」是一个 Chrome 扩展（MV3）。Chrome 在 Windows 上**不允许**非商店扩展静默安装，
而下面四条"自动装"的路本仓库都试过或评估过，**没有一条可用**：

| 路径 | 为什么不用 |
|---|---|
| 拖入 `.crx` 安装 | Chrome 直接拦截非商店来源的 `.crx` |
| 策略强制安装（`ExtensionInstallForcelist`） | HKCU 下普遍不生效（需要域/机器级策略） |
| 自托管 `update_url` | 常见失败，且要让 Chrome 接受就必须有**签名私钥** |
| 一键写入 Chrome 配置目录 | Chrome 会校验扩展的签名与来源，写进去也不会被启用 |

★ 本仓库**没有扩展签名私钥** —— 这也是"自托管更新"不可行的根本原因：
签名密钥一旦更换，扩展 ID 就变，用户已保存的数据随之读不出来。

⇒ 采用「**随包携带 + 一键引导**」：桌面安装包里带着扩展目录，桌面端负责把它打开、
把四步说明摆在你面前，**你只需点几下**。零依赖、零管理员权限、离线可用。

---

## 二、首次安装（四步）

入口：**账号中心** → 顶部提示条「未检测到浏览器扩展」→ 点 **一键安装引导**。
桌面端会同时打开 `chrome://extensions` 和扩展所在目录，并弹出说明：

1. 打开 Chrome，访问 `chrome://extensions`（桌面端已替你打开；没弹出就手动访问）
2. 打开右上角「**开发者模式**」开关
3. 点「**加载已解压的扩展程序**」
4. 在文件夹选择框里选中**已为你打开的那个目录**（选到它本身，**不要进子目录**）

装好后：账号中心的「在线」徽标亮起，`Alt+Q` 轮盘可用；
**账号数据由桌面端自动下发**，不需要在扩展里另建。

⚠️ 该目录随桌面端的安装目录存在 —— **卸载桌面端后扩展会失效**（Chrome 里的加载记录还在，但文件没了）。

---

## 三、桌面端升版之后：点一次「重新加载」

扩展文件**随桌面端一起更新**，但 Chrome 只在**重新加载后**才会用上新版本。
所以桌面端升版后你会看到「扩展版本落后」提示条 ⇒ 点它的**重新加载**按钮：

1. 打开 `chrome://extensions`（桌面端已替你打开）
2. 在扩展列表里找到 **Akso 快捷登录**
3. 点该扩展卡片上的「重新加载」按钮（↻）

★ **数据不会丢**：重新加载只重启扩展本身，账号与登录态都还在。

---

## 四、发布令牌（`GH_TOKEN`）

`tools/publish_release.py` 上传安装包与 `latest.yml` 时需要 GitHub 令牌。
**这一节就是它在错误信息里指向的那一节。**

获取路径：GitHub 头像 → **Settings** → **Developer settings** → **Personal access tokens**

| 类型 | 需要的权限 |
|---|---|
| **fine-grained**（推荐） | `Contents: Read and write`，仓库选 **`jizi-dragon/akso-mogul`** |
| **classic** | 勾 **`repo`** 一项即可 |

用法（PowerShell，仅本次会话有效）：

```powershell
$env:GH_TOKEN = "<你的令牌>"
.\.venv\Scripts\python.exe tools\publish_release.py
```

★ 令牌**不要写进任何文件**（本仓库的凭据纪律：不落明文）。★ 哪个 GitHub 通道通是**随时刻变化的**，
推送/上传前先探一次，别把上次的成功当依据。

---

## 五、开发态怎么装

从仓库直接加载**构建产物**（改完扩展源码先构建）：

```powershell
cd extensions\quick-login
npm run typecheck
npm run build      # 产物在 extensions/quick-login/dist
```

然后在 `chrome://extensions` → 开发者模式 → 「加载已解压的扩展程序」→ 选
`extensions/quick-login/dist`（选到 `dist` 本身）。

桌面端在开发态也认这个目录（`extensionDir()` 的回落分支）。

---

## 六、排错

| 症状 | 处置 |
|---|---|
| 点了「一键安装引导」但 Chrome 没弹出来 | 手动访问 `chrome://extensions`；文件夹选择框里手动找到提示的目录 |
| 装了扩展但账号中心仍显示「未检测到」 | 确认扩展卡片上**没有报错**；点一次「重新加载」；再刷新账号中心页面 |
| 扩展里看不到账号 | 账号由桌面端下发 ⇒ 确认**桌面端在运行**（扩展的在线徽标应当亮起） |
| 升版后行为像旧版 | §三 的「重新加载」没做 —— 扩展文件已更新，Chrome 还持有旧的内存镜像 |
| 选目录时选进了子目录 | 退回到上一级重选：要选的是**含 `manifest.json` 的那一层** |

---

## 七、改这份文档时的判据

```powershell
# ① 文档存在，且 markdown 链接都解析得到
.\.venv\Scripts\python.exe tools\check_doc_links.py

# ② 全仓搜一遍（含代码里的指引与错误消息）—— 引用必须都能落到真实文件上
Get-ChildItem workbench,tools,tests,desktop,docs,extensions -Recurse -Include *.py,*.js,*.ts,*.md,*.spec -File |
  Select-String -Pattern 'EXTENSION-INSTALL' | ForEach-Object { $_.Path }
```

★ 这份文档的**存在性**是一份契约：`tools/publish_release.py` 的错误信息里写着「第四节」，
所以**令牌那一节必须始终是第四节**；插新节时往后挪，别插在它前面。
