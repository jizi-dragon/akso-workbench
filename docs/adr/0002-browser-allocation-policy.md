# ADR-0002：浏览器分配政策（快捷登录用用户 Chrome，监听/自动化用内置 Chromium）

- **状态**：已采纳（0.2.2 用户定稿）
- **决策者**：用户定稿
- **相关**：[ADR-0003](0003-electron-desktop-shell.md)、[ADR-0004](0004-extension-as-execution-plane.md)、
  [EXTENSION-PLANE.md](../EXTENSION-PLANE.md)、[API.md](../API.md) §12.5

## 背景

项目同时具备两种「在浏览器里以某账号身份动作」的能力：

1. **扩展执行面**：Chrome MV3 扩展跑在**用户自己的 Chrome** 里，做六平面隔离与会话切换；
2. **托管浏览器**：`browser_pool` 用 Playwright（sync）驱动浏览器，做自动登录、免密持久化、
   状态墙、以及 Monitor 监听录制。

两者都能「打开某个账号」。若不定政策，会出现同一功能有两条实现路径、以及**同一账号被两处同时操作**
的风险。更早的形态（0.2.2 之前）确实存在这种混杂：pytest 与运行时夹具都曾各自起 chromium。

## 决策

**按用途分配浏览器，而不是按功能模块分配：**

| 用途 | 浏览器 | 理由 |
|---|---|---|
| **快捷登录 / 打开账号**（用户手动切换到某身份去操作） | **用户自己的 Chrome**（经扩展 `par.open`） | 要的是「以该身份操作的真实效果」——真实 profile、真实代理、真实插件、真实书签。若用内置 Chromium，用户面对的是一个空 profile，很多站点体验不一致 |
| **监听 / 自动化 / 录制 / 批量动作** | **应用内置 Chromium**（`browser_pool`，CDP 模式即 Electron 壳的 Chromium） | 要的是可控、可录制、可复现、**不污染用户浏览器**。用户 Chrome 里塞自动化会污染其真实 profile，且扩展的隔离平面会干扰录制 |

配套硬性规定：

1. **Playwright sync 单线程单实例**：所有浏览器操作必须走 `BrowserPool` 专职工作线程队列；
   **测试不得另起 chromium 夹具**（见 [ADR-0006](0006-browser-pool-single-thread.md)）。
2. **`local` / `cdp` 双模式**：`WORKBENCH_SESSION_MODE=cdp` 时托管会话复用 Electron 壳的 Chromium
   （先 `:18767` 开户再 `connect_over_cdp(:18766)`）；否则本地 Playwright chromium。
   这样开发/无壳环境仍能跑全链路，测试也不依赖 Electron。
3. 快捷登录链路必须有 `launch-chrome` 前置：**Chrome 未运行时扩展 SW 不会轮询指令**，
   此时点击轮盘选人，指令只会滞留在队列里（所以桌面在派发前先探测/拉起 Chrome）。

## 取舍

### 被放弃的方案 A：全部用用户 Chrome

- ✗ 无法录制/回放：扩展不暴露网络录制能力，Monitor 的三级降噪（`dropped`/`trimmed`/`full`）
  需要 Playwright 的 `page.on("response")` 与 DOM 动作缓冲。
- ✗ 六平面隔离会**主动改写**请求（AUTH/CACHE 平面），录制到的流量不是平台的真实行为。
- ✗ 用户关闭浏览器即中断所有自动化。

### 被放弃的方案 B：全部用内置 Chromium

- ✗ 失去「真实 profile 效果」——用户最常抱怨的正是「登录进去了但站点行为不对」。
- ✗ 凭据要经内置浏览器中转，扩大凭据暴露面。
- ✗ 平台的设备指纹/SSO 策略对全新 profile 未必友好。

### 被放弃的方案 C：按功能模块分（洞察走 A、工厂走 B）

- ✗ 政策会随模块演进反复漂移，无法作为维护红线。
- ✗ 同一账号在两处并发操作的问题依然存在。

### 代价（已接受）

- 两套浏览器链路都要维护（扩展 TS + Playwright Python），且**两边的自动登录节奏门控参数各有一套**
  （扩展 `auto-login.ts` / Python `autologin.py`，见 [CONFIG.md](../CONFIG.md) §4）。
- 扩展侧必须保证「桌面不可达时静默跳过」（离线回退），否则用户不开桌面端时扩展会反复报错。

## 后果

- 端口的职责被固化（[API.md](../API.md) §0）：`:18765` 服务、`:18766` CDP、`:18767` 壳控制服务。
  **CDP 端口必须在 Electron `app ready` 之前设置**（`app.commandLine.appendSwitch`），
  否则 Playwright 无法挂接。
- 「同一账号同时被扩展与托管会话操作」在语义上**未被禁止**——目前靠 UI 入口区分（卡片上的
  「打开」vs「快捷登录」/「开始监听」）。这是一处仍待产品决策的边界。
- 真机验收基线因此分两侧：托管侧 `tests/test_browser_parallel.py` / `test_browser_state.py`；
  扩展侧 `tools/verify_extension_*.py` / `acceptance_extension_e2e.py`。
