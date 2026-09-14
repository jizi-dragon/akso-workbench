# ADR-0011：端口归属与会话协商（壳当仲裁者，端口值只允许有一个真相来源）

- **状态**：部分被取代（2026-09-14）。
  **§D-1（被占则顺延）与 §D-6 的顺延部分已由 [ADR-0012](0012-fixed-ports.md) 取代——端口固定，不顺延。**
  §D-2（端口下发）/ §D-3（公布文件）/ §D-4（身份化就绪探测）/ §D-5（扩展发现）**继续有效**。
  本 ADR 的 §1 事故复盘与"必须核对身份"这一核心教训仍然成立，是 ADR-0012 的直接依据。
- **背景**：一次真实的"我改了代码但 UI 没变"事故，暴露出壳 / 服务端 / 扩展三方**各自硬编码同一组
  端口、且没有任何一方负责裁决"谁在用这个口"**。用户提出的问题原文：「现在的端口就有好几个，
  网页端，桌面端，是不是应该控制盒平衡一下」。
- 配套：[ADR-0002](0002-browser-allocation-policy.md)（浏览器分配政策）、
  [ADR-0003](0003-electron-desktop-shell.md)（Electron 壳）、[ADR-0006](0006-browser-pool-single-thread.md)（browser_pool 单线程）。

---

## 1. 事故复盘（这就是本 ADR 的存在理由）

**现象**：菜单里已经把 `inventory` / `understand` 两个命令删掉并推送了，但用户在桌面上打开
应用，下拉里还是四个命令。

**根因**：桌面上跑的是 **9/9 的旧打包副本**（`dist/AksoWorkbench/_internal/.../insight.html`，
静态页是打包那一刻的快照）。而当时端口 18765 上活着的正是那个旧实例。

**为什么壳没有察觉**（这才是真正的问题，不是"打包副本过期"这件小事）：

```js
// desktop/main.js:75-88（改造前）
function waitReady(timeoutMs = 40000) {
  const tick = () => {
    const req = http.get(serverUrl('/'), { timeout: 2000 }, (res) => {
      res.resume();
      resolve(res.statusCode === 200);   // ← 只看「有人应答」，不看「是不是我们」
    });
```

`waitReady()` 的判据是 **"这个端口上有个 HTTP 服务回了 200"**。它**不校验对方身份**。于是：

1. 旧实例先占住 18765（或另有任何程序占着并回 200）；
2. 新壳 spawn 自己的服务端 → 那个进程 `bind(18765)` **失败**（端口占用）；
3. 新壳去探 18765 → 旧实例回 200 → **判定"就绪"**；
4. 主窗打开的是**旧实例**的页面。

一个端口被占，表现成了"代码改动没生效"。这类故障的排查成本极高，因为所有组件都自认为正常。

## 2. 现状实证（改造前的三个真相来源）

| 端口 | 用途 | 硬编码处（实证） |
|---|---|---|
| 18765 | 服务端业务面 + 静态页 | `desktop/main.js:17` `SERVER_PORT`；`workbench/config.py:66` `WORKBENCH_PORT` 默认值；扩展 `background/sync.ts:20` `const DESKTOP = 'http://127.0.0.1:18765'`；`core/parallel-session.ts:74` 白名单 `127.0.0.1:18765` / `localhost:18765` |
| 18766 | 壳 Chromium 的 CDP 调试口 | `desktop/main.js:19`；`browser_pool.py:39` `WORKBENCH_CDP_PORT` |
| 18767 | 壳控制服务（开户窗 / 聚焦 / 更新状态） | `desktop/main.js:18`；`browser_pool.py:38`；`api/routes_extension.py`；`api/routes_update.py`（2 处） |

四个具体缺陷：

**D1｜壳不把端口告诉 Python，Python 也不告诉壳。**
打包态 `spawnServer()` 起 `AksoServer.exe --server`，**不传端口、不设环境变量**；Python 侧
`config.PORT` 自己从环境变量读、默认 18765（`desktop/main.js:47` 与 `workbench/config.py:66`）。
两边各自默认到同一个数字，**没有一次握手**。更糟的是开发态反而传了 `--port`（`main.js:59`）——
"打包态与开发态走的不是同一条路径"，这类耦合只会在打包后才咬人。

**D2｜"端口被占"与"端口被自己占"分不清。** 见 §1 的 `waitReady`。

**D3｜扩展端口写死且没有下发通道。** 扩展能自动发现桌面端的**指令队列**
（长轮询 `/extension/commands`），却发现自己要访问的**业务面端口**只能靠猜同一个常量。
后果有二：桌面换端口 = 扩展静默失联；18765 上坐着别的实例 = 扩展把凭据
（Fernet 密文 + 原始密钥）发给**那一个**实例。

**D4｜壳与 Python 之间没有"配置下发"通道，端口只是第一个受害者。**
`WORKBENCH_SESSION_MODE` 全仓库只有 `browser_pool.py:37` 读它，`desktop/main.js` **从不设置**——
于是 `cdp` 模式永远不生效，托管会话实际跑的是原生 Playwright Chromium，而不是壳的 Chromium
（这正是 ADR-0002 想要的）。**同一个病根，另一个症状。**

### 2.1 一个意外的纵深防御（值得记下来）

凭据是 **Fernet 密文**，而密钥存在**本机数据库** `settings.fernet_key`。所以"旧实例先占端口"
这种情形下，即使扩展被路由到了旧实例，旧实例**能**解密（同一个 `%APPDATA%` 数据库 → 同一把密钥）。
→ **不要把"密文"当作抵御错误路由的防线**，它抵御的是**网络窃听**，不是**本机实例串号**。

## 3. 决策

**原则：每个端口只有一个权威来源（壳），其余全部靠协商获得，禁止各自默认。**

### D-1 壳是唯一的仲裁者

壳是先启动、且唯一需要知道全部三个端口的进程，因此由它决定端口：

```
优先固定基准（18765 / 18766 / 18767），被占用则在该端口起向上顺延（最多 N 次）
```

**不做全局随机**：固定端口对使用者更友好（扩展、书签、手输地址）；随机只会把
"我要打开工作台"变成"我得先查它这次在哪个口"。所以是**优先固定、占用则顺延、并把实际值公布出去**。

### D-2 端口值经环境变量 + 显式参数下发给 Python

壳 spawn 服务端时同时：
- 设 `WORKBENCH_PORT` / `WORKBENCH_CDP_PORT` / `WORKBENCH_CONTROL_PORT` / `WORKBENCH_SESSION_MODE`
  环境变量（**Python 侧因此不再需要"默认值"这个第二真相来源**）；
- 继续传 `--port`（打包态 `server_entry.main()` 已支持解析），两者必须一致。

同时把 `WORKBENCH_SESSION_MODE=cdp` 一并下发 → 顺带修掉 **D4**。

### D-3 落一份端口公布文件 `desktop-ports.json`（权威事实）

壳在 spawn 服务端**之前**原子写 `<数据目录>/runtime/desktop-ports.json`：

```jsonc
{
  "app": "AksoWorkbench",
  "schemaVersion": 1,
  "pid": 12345,              // 壳的 pid：用于判断「这个文件是不是当前活着那个实例写的」
  "version": "0.3.5",
  "startedAt": "2026-09-14T10:00:00+08:00",
  "ports": { "server": 18765, "cdp": 18766, "control": 18767 }
}
```

- 壳**退出时删除**该文件（正常退出路径；`before-quit` 已有清理动作）。
- 服务端与工具都能读它；读不到 = 只有服务端在跑（开发者手起场景），此时按传入参数/约定回落。
- **这份文件直接消灭 §1 的故障形态**：新壳启动时读到 `pid` 仍存活 + `version` 是别的版本 →
  立刻能给出"已有另一个实例（PID / 版本）在运行"这种**可执行**的提示，而不是静默等 200。

### D-4 就绪探测必须带身份

服务端新增 `GET /api/health`（**无副作用**，与既有 `/extension/health` 的分工见下）：

```jsonc
{ "app": "AksoWorkbench", "service": "workbench-server", "version": "0.3.5",
  "pid": 4242, "ports": { "server": 18765, "cdp": 18766, "control": 18767 } }
```

壳的 `waitReady()` **必须核对 `app === "AksoWorkbench"`**：从"有人应答"升级为"是我们的服务在应答"。
> 分工：`GET /api/health` = **进程身份**（无副作用、供探测）；
> `GET /extension/health` = **执行面健康**（会写 `everConnected`，有副作用，供 UI）。
> 不要让探测去打一个会写库的端点。

### D-5 扩展从壳的控制服务发现服务端口（回退固定值）

- 壳控制服务新增 `GET /whoami`（无副作用）→ 返回 §D-3 的同一份信息（含 `serverPort`）。
  控制服务是**壳自己的**，因此它的回答天然比"18765 上是谁"可信。
- 扩展新增 `background/core/desktop-endpoint.ts`：把写死的 `DESKTOP` 换成
  `desktopBase()`——**先用上次成功发现的值**（`chrome.storage.local` 缓存），
  **失败再按窗口探测** `18767` 起（`/whoami` 带 `app` 校验，短超时），
  **全都失败则回退固定 `http://127.0.0.1:18765`**（保证旧桌面端仍可用）。
- `parallel-session.ts` 的同步宿主白名单改为**动态**：`127.0.0.1`/`localhost` + **端口 ∈ 已发现集合**
  （默认含 18765）。语义不变（仍然只信本机回环的桌面同步口），但不再绑死一个数字。
- 扩展已有 `host_permissions: ["<all_urls>"]`，**探测无需新增权限**（这是本方案可行的前提，
  若当初收窄过权限则必须先扩权限才能做发现）。

### D-6 失败要出声

- 端口全部顺延失败 / 被别的实例占住 → 壳给出**可执行**的 MessageBox：
  「端口 18765 被 PID 1234（AksoWorkbench v0.3.5，启动于 …）占用。请先退出那个实例再启动。」
- 探测超时不再只是"等 40 秒然后白屏"，而是把"我探到了什么"（对方 app 名 / 版本 / 无应答）写进
  `shell-state.json`，供排障。

## 4. 被否决的方案

| 方案 | 否决理由 |
|---|---|
| **每次启动随机端口** | 使用者要的是"打开工作台"，不是"先查这次端口"。扩展、书签、手输地址全部变复杂；收益（避免冲突）用"顺延 + 公布"就能拿到 |
| **改成 Unix domain socket / 命名管道** | Windows 上 Chrome 扩展**无法**访问命名管道；扩展是执行面，它必须走 HTTP 回环 |
| **用单实例锁（`app.requestSingleInstanceLock`）强行独占** | 好，但**不够**：它只能防"第二个我们的实例"，防不了"18765 被别的程序占"。两者都要做，但那属于 §5 的后续项，不是本 ADR 的前置 |
| **扩展内置端口扫描整个 18700-18800** | 无谓放大攻击面与启动延迟；控制服务知道答案，问它就行 |
| **把 ports 文件放注册表** | 安装态/免安装态不一致；文件已有既定位置（`runtime/`），且 Python 侧读文件最自然 |

## 5. 落地清单（本次范围）

- [x] 壳：端口选择器 + 下发（env `WORKBENCH_PORT`/`_CDP_PORT`/`_CONTROL_PORT`/`_SESSION_MODE` + `--port`）
- [x] 壳：`desktop-ports.json` 原子写 / 退出清理 / 启动时读它做"旧实例在跑"提示
- [x] 壳：身份化 `waitReady`（校验 `app`）+ 占用即报可执行错误
- [x] 服务端：`GET /api/health` 带身份与端口（无副作用）
- [x] 壳控制服务：`GET /whoami`
- [x] 扩展：`desktop-endpoint.ts` + 动态宿主白名单
- [x] 测试：Python 侧 pytest 覆盖 health / ports 文件契约；工具侧脚本仍须能跑
- [x] 文档：CONFIG / API / CODEBASE_OVERVIEW / PITFALLS / CHANGELOG 同步

## 6. 已知边界（本 ADR **没有**解决的）

1. **扩展没有"被通知"通道**：桌面端换了端口，扩展只能在下次轮询失败后重新发现（最长一个探测窗口的延迟）。
   要彻底解决得让扩展订阅，而 MV3 SW 没有稳定的常驻连接渠道。
2. **`app.requestSingleInstanceLock()` 仍未做**：两个我们的实例同时跑（不同端口）仍是可能的，
   只是不再互相误认。要做需连同"第二个实例应把主窗交给第一个"一起设计。
3. **端口顺延会改变"书签"**：固定端口失效时，写死的书签/收藏会失效——这是选择"可顺延"的代价，
   由 `desktop-ports.json` 与 `/api/health` 补偿（UI 可读它给出正确链接）。
4. **控制服务若也被占**：`/whoami` 的发现链断掉，扩展回退固定 18765。此时若服务端也顺延了，
   扩展就找不到——故顺序上**先用固定基准尝试**，顺延只作为兜底（与 §D-1 一致）。
