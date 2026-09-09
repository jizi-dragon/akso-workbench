# QuickLogin 纯扩展多账号并行在线 —— Bearer 鉴权规避方案调研

> ⚠️ **历史调研文档（v3.4 时代，2026-08-28）**：本文的方案 C「双平面隔离」已按 §10 落地，
> 并在 v3.5–v3.7.2 演进为六平面隔离（新增 SW/CacheStorage 封控、IndexedDB 命名空间、
> Cookie 按账号回放）。现状见根目录 `docs/PROJECT-STATUS.md`；泄漏终审见
> `research/idb-permissions/`。

> 状态：`调研稿 v2 —— 已并入 15 条先例核实`（2026-08-28）
> v2 变更：注入架构改为「静态 MAIN 壳 + 种子握手」双轨制；存储层首选从 defineProperty 整包劫持改为 **Storage.prototype 方法级前缀补丁**（先例验证）；§8 落地完整先例参考。
> 目标站：`https://example-config.example.com/`（无状态 JWT + 前端内存 token + Bearer 鉴权 + 前端失效自检）
> 前置结论：`packages/extension/docs/DESIGN.md` §3（纯扩展无法同站多账号并行在线的实测四条）
> 本文任务：在「**只用浏览器扩展、不加本地引擎**」的新约束下，重新评估该结论，并给出可落地的规避架构。

---

## 1. 问题重述与破局点

### 1.1 实测四条（DESIGN.md §3，仍是事实前提）

| # | 实测证据 | 对多开的意义 |
|---|---|---|
| 1 | 所有 `/api/*` 带 `Authorization: Bearer <JWT>`，Cookie `__auth_token__` 只是持久化副本 | 切 Cookie 罐对鉴权无效 |
| 2 | 服务端不互踢，多个 token 各自独立有效 | **有利条件**：并行在线的服务端侧是允许的 |
| 3 | 前端有会话失效自检：清 Cookie token → 页面未刷新即跳登录页 | 「切 Cookie」式方案必然误伤其他账号 |
| 4 | 共享 localStorage `__auth_user__`/`__device_fp__` 被最后登录者覆盖 | 多账号身份态互相踩踏 |

### 1.2 关键洞察：被共享的只有两样东西

把 v2.1 的失败拆开看，四个障碍里真正**跨标签页共享**的只有两层：

1. **网络层**：每个请求出口处的 `Authorization` 头（谁都能发任何 token，服务端不校验来源）；
2. **持久层**：localStorage / document.cookie 的同 origin 单一存储。

而第 3 条里的「前端内存 token」（React 状态、闭包变量）是 **天然 per-tab 隔离** 的——每个标签页的 JS 内存互不可见。原方案失败的直接原因不是「内存 token 存在」，而是**启动时刻**：页面脚本一跑就从共享存储把「最后一个登录者」的 token 读进内存。只要让每个 tab 在启动时把内存态初始化为各自账号的值，并且它后续读到的存储视图自洽，互踢链条就在第一环断掉。

Chrome MV3 扩展恰好同时握有接管这两层的官方通道：

- 网络层 → `declarativeNetRequest`（DNR）session 规则，**支持按 `tabIds` 条件改写请求头**；
- 持久层 → `world: "MAIN"` MAIN-world 注入，可在页面首脚本前把 `window.localStorage` / `document.cookie` 劫持为 per-tab 虚拟视图。

这正是纯扩展方案的立足点。

---

## 2. 平台能力核实（本次已查证）

| 能力 | API | 版本 | 关键限制 |
|---|---|---|---|
| 按 tabId 匹配规则改写请求头 | [`declarativeNetRequest.RuleCondition.tabIds`](https://developer.chrome.com/docs/extensions/reference/api/declarativeNetRequest#property-RuleCondition-tabIds) + [`ModifyHeaderInfo`](https://developer.chrome.com/docs/extensions/reference/api/declarativeNetRequest#type-ModifyHeaderInfo) | tabIds: Chrome 92+；modifyHeaders: Chrome 86+ | **仅 session 作用域规则可用**（动态增删、随浏览器会话存活——正好匹配 tabId 生命周期）；`set` 操作必须带 value |
| MAIN world 动态注入 | [`chrome.scripting.executeScript({world:'MAIN'})`](https://developer.chrome.com/docs/extensions/reference/api/scripting) | world: Chrome 95+/102+ | `injectImmediately`（Chrome 102+）官方文档明示 "**not a guarantee that injection will happen before page load**" |
| MAIN world 静态注册 | manifest `content_scripts[].world = "MAIN"` | Chrome 111（[w3c/webextensions#485](https://github.com/w3c/webextensions/issues/485)：'Chrome 111, Chrome supports a new key `world` in `content_scripts` in manifest, which value is "ISOLATED"(default) or "MAIN"'） | 时序最早最稳，但**无法参数化**（不能按 tab 携带不同账号数据） |
| 任意代码字符串注入 | [`chrome.userScripts`](https://developer.chrome.com/docs/extensions/reference/api/userScripts) | API: Chrome 120+；一次性 `userScripts.execute()`: Chrome 135+ | 可动态生成代码串（能内嵌账号数据）；要求用户开启开发者模式 |
| 保证级 prelude 注入 | `chrome.debugger` + CDP `Page.addScriptToEvaluateOnNewDocument` | 全版本 | 承诺先于新文档一切脚本执行且可携带数据；代价是每 tab 显示「正在调试此浏览器」提示条 |

结论：能力齐备。「注入时序保证」从弱到强依次为：`executeScript(injectImmediately)`（官方明示仅为尽力而为）< manifest 静态注册 `world:"MAIN"`@document_start ≈ `userScripts`(document_start，走与 content script 相同管线) < `chrome.debugger`+`Page.addScriptToEvaluateOnNewDocument`（唯一在协议层给出「先于新文档一切脚本」承诺的通道）。「数据参数化能力」则相反（静态注册最弱、executeScript/userScripts/debugger 均可携带数据）。工程上需要组合使用（见 §4.5）。

---

## 3. 候选方案矩阵

### 方案 A：仅 DNR 按标签页改写 Authorization（网络平面单打）

- 做法：为每个绑定了账号的 tab 维护一条 session 规则：`condition: { requestDomains: [目标站], tabIds: [tabId] }`，`action.modifyHeaders: [{header:'Authorization', operation:'set', value:'Bearer <token>'}]`。
- 优点：改动极薄、不碰页面内部、天然覆盖页面脚本管不到的通道（站点自身 ServiceWorker 发起的 fetch 等）。
- 缺点：只修网络头，不改内存/存储视图 → UI 上显示的用户名、`__auth_user__`、自检逻辑仍基于「最后登录者」状态，行为不一致，大概率触发自检或业务误操作。
- 定位：**兜底层 + WS/下载等末梢通道补漏**，单独用不够。

### 方案 B：页面态虚拟化（存储平面单打）

- 做法：MAIN-world prelude 劫持 `window.localStorage`（`Object.defineProperty(window,'localStorage',{get})` 替换为代理对象）与 `document.cookie`，读写经 postMessage 桥接到 background，以 `chrome.storage.session[tabId]` 为 per-tab 权威视图；同时包装 `window.fetch` / `XMLHttpRequest` 处理 401/403。
- 优点：内存态初始 token 由虚拟存储注入后自然正确；身份显示、自检、设备指纹全部 per-tab 自洽。
- 缺点：
  - 桥接是异步的，而 `localStorage.getItem` 是同步 API —— 启动瞬间存在「数据未到」窗口；
  - 不覆盖 ServiceWorker / Worker 发起的网络请求；
  - 同步语义模拟成本高（若站点有读后即写的紧凑序列需小心）。
- 定位：**主战场**，但需与 A 组合才完整。

### 方案 C：A+B 组合 —— 「双平面隔离」★ 推荐

网络平面与存储平面同时接管，见 §4。

### 方案 D：chrome.debugger 全家桶（备选强化）

- 用 CDP attach 每个 tab：`Page.addScriptToEvaluateOnNewDocument` 注入参数化 prelude（数据内嵌闭包，**同步可用、零竞态**），必要时用 `Fetch` domain 全量拦截请求。
- 优点：时序是「保证级」，一劳永逸解决启动竞态。
- 缺点：每个绑定的 tab 都出现调试提示条；与用户手开的 DevTools 互相冲突；整体重、易碎。
- 定位：作为 §4.5 时序三级策略的最高档逃生门，不作为默认通道。

### 方案 E：不做并行，维持现状免密切换

保留为降级路径：绑定流程异常、检测到环境不支持时，自动回落 v2.1 的「登出→免密重登」单账号模式，功能不断。

### 已排除项及理由

| 排除项 | 理由 |
|---|---|
| iframe 同源包装（一个 iframe 一个账号） | 同 origin 下 iframe 与父页共享同一份 localStorage，问题不变 |
| 域名别名 / hosts 映射制造假 origin | TLS SNI/证书绑定真实域名，服务器不配合就不成立；且违背纯扩展约束 |
| ServiceWorker 反向代理 | MV3 扩展注册不了控制第三方站点 SW；站点自己的 SW 已占用作用域 |
| Firefox containers / Chromium contextualIdentities API | Chromium 无此 API |
| 指纹伪造、WebRTC/TLS 层伪装 | 非目标（内部工具无防关联诉求，DESIGN.md 已裁剪） |

---

## 4. 推荐架构：双平面隔离（方案 C）

### 4.1 分层图

```
┌─ Chrome ─────────────────────────────────────────────────────────────┐
│                                                                      │
│  Tab#1 (账号 dev-user)                    Tab#2 (账号 T0601)               │
│  ┌───────────────────────┐          ┌───────────────────────┐        │
│  │ MAIN-world prelude     │          │ MAIN-world prelude    │        │
│  │ · window.localStorage  │          │ （同一套代码，另一份数据）│        │
│  │   => per-tab 虚拟视图   │          │                       │        │
│  │ · document.cookie      │          │                       │        │
│  │   => per-tab 虚拟视图   │          │                       │        │
│  │ · fetch/XHR 401 上报    │          │                       │        │
│  └──────────┬────────────┘          └──────────┬────────────┘        │
│             │ postMessage(viewOps)             │                     │
│  ┌──────────▼──────────────────────────────────▼────────────┐        │
│  │ background service worker                                 │        │
│  │  绑定表 tabId↔accountId (chrome.storage.session 持久化)     │        │
│  │  虚拟存储权威副本 chrome.storage.session["vs:"+tabId]       │        │
│  │  token 库 (AES-GCM 加密, chrome.storage.local, 重启可恢复)  │        │
│  │  免密重登编排 (复用现有 auto-login 填表链路)                 │        │
│  └──────────┬────────────────────────────────────────────────┘        │
│             │ declarativeNetRequest.updateSessionRules                │
│  ┌──────────▼────────────────────────────────────────────────┐        │
│  │ DNR session 规则集                                         │        │
│  │  rule(n): { condition:{requestDomains:[host], tabIds:[t]}, │        │
│  │             action:{modifyHeaders:[Authorization=set]} }   │        │
│  └───────────────────────────────────────────────────────────┘        │
├──────────────────────────────────────────────────────────────────────┤
│              真实 Cookie jar / 真实 localStorage（退居二线）            │
└──────────────────────────────────────────────────────────────────────┘
```

### 4.2 核心洞察之二：冷启动读到「空」反而是安全的

一个容易担忧的技术难点其实不是障碍：

> MAIN-world 无法同步获取 extension 数据（桥接异步），prelude 迟几毫秒怎么办？

回答：我们的**账号打开流程本来就从「未登录」起步**——导航 `/login` → 自动填表提交（v2.1 的既有链路）。这个流程里 prelude 晚到毫无影响：真正依赖「热启动在场」数据的场景只有一个——**已有登录态直达首页**（手动点击已在线账号、F5 刷新）。该场景由「token 内嵌进注入参数 + 尽早注入 + 未就位检测重载一次」兜底即可（§4.5）。

### 4.3 数据内嵌注入（同步零竞态的工程解法）

不追求「先注入、再异步喂数据」，而是把数据藏进注入代码本身：

```ts
// background 在 onCommitted / 或用户点击账号时构造：
await chrome.scripting.executeScript({
  target: { tabId },
  world: 'MAIN',
  injectImmediately: true,
  func: bootstrapPrelude,           // 下面这段函数本体
  args: [{
    token: account.jwtSnapshot,     // 初始 JWT 快照（escape 后嵌入闭包）
    authUser: account.authUser,
    deviceFp: account.deviceFp,
    extraKv: viewSeed,              // 其余 localStorage 种子键值
  }],
});
```

`bootstrapPrelude` 第一行就把 localStorage/cookie 切换为 per-tab 视图，并把 `args` 快照同步灌入——任何早于桥接建立完成的同步读取都拿到正确值。后续增量更新走 postMessage 桥。

> **先例修正（v2）**：executeScript 注入受 service worker 冷启动拖累，从收到导航到真正执行可漂移数百毫秒（[SO 实证](https://stackoverflow.com/questions/71126300/chrome-scripting-executescript-takes-too-long-to-start-at-document-start)）；经 ISOLATED 世界插 `<script src>` 的经典注入法也因脚本资源异步加载而晚于页面 bundle。因此 executeScript 降级为**数据灌入通道与降级路径**，时序骨架改由 manifest 静态注册的 MAIN-world document_start 壳承担（§4.3a）。

### 4.3a 注入双轨制与存储隔离形态（v2，依先例收口）

**轨 1 —— 静态 MAIN 壳（时序权威）**：manifest 声明 `content_scripts: [{ world: 'MAIN', run_at: 'document_start' }]`（Chrome 111+）。壳代码在一切页面脚本前同步执行：
1. 立即对 `Storage.prototype` 的 `getItem/setItem/removeItem/key/clear/length` 做**方法级补丁**——对本站共享键（`__auth_token__`/`__auth_user__`/`__device_fp__`）按账号命名空间加前缀重定向；
2. 同样整体重定义 `document.cookie` getter/setter、给 `history.pushState/replaceState` 挂路由校验钩子；
3. 若本 tab 尚未绑定账号 → 立即短路返回（壳体保持极小，静态注入对所有页面生效需快速退出）。

选 **prototype 方法级前缀补丁**而非 `defineProperty(window,'localStorage')` 整包替换，是先例（[MultiAccountContainers storage-isolator.js](https://github.com/worldof01/MultiAccountContainers/blob/master/content/storage-isolator.js)）验证过的更稳形态：不触碰属性自身的可配置性（V8 下 localStorage 可 redefine，但整包替换后页面缓存引用、toString 探测等保真成本高）、原生 backing store 继续承担持久化（零异步桥、零结构化克隆）、同时覆盖 localStorage 与 sessionStorage、子 iframe 共享同一 prototype 天然继承隔离。代价：Worker 上下文无 Storage 对象，页内 Worker 读共享态的路径 PoC 盘点后再定。

**轨 2 —— 种子握手（数据通路）**：background 在 `tabs.onCreated/onUpdated` 或用户点击账号时，向该 tab 的 ISOLATED 世界发消息（含 accountId + token 快照），ISOLATED bridge 经 `window.postMessage` 转交 MAIN 壳；壳收到种子前以内存 Map 暂存读写。冷启动读「暂存空」无害（§4.2）；热启动场景由后台在导航提交前预写种子。

### 4.4 各平面职责边界

| 关注点 | 归属 | 说明 |
|---|---|---|
| `Authorization: Bearer` 最终改写 | **DNR**（authoritative） | 页面脚本自己设置的 JWT 头也会在网络层被覆盖成绑定账号的 token，杜绝遗漏路径 |
| Authorization 之外的指纹通道（自定义签名头等） | PoC 盘点后再定 | 见 §7 第 1 步 |
| 站点自身 ServiceWorker 发起的请求 | 只能靠 DNR | 内容脚本包装 fetch 覆盖不到 SW context；官方文档明确「DNR 作用于到达网络栈的请求，包括 SW 内部发出的 fetch 调用，但不影响 SW 自行合成或命中 CacheStorage 的响应」 |
| WebSocket 握手鉴权 | 待验证 | 若走 query param/cookie 则需 DNR 附加规则验证；若不涉鉴权则无需处理 |
| localStorage / cookie 视图 | MAIN-world 壳（Storage.prototype 前缀补丁 + cookie 重定义） | per-tab 隔离；原生 backing store 承担持久化，真实存储不再被写坏（形态论证见 §4.3a） |
| 身份展示（`__auth_user__`）、device_fp | 虚拟视图种子 | 每 tab 一份，杜绝覆盖踩踏 |
| 401/403 失效感知 | fetch/XHR 包装上报 | 包装器捕获 401 → 按 accountId 命名空间单飞刷新（`navigator.locks.request('refresh:'+accountId)`，先例模式）→ 成功后通知后台 `updateSessionRules` 热替换该 tab 规则值并重放原请求；失败由扩展侧 hold 住，不触发站点跳登录逻辑（session 规则 value 是静态的，token 轮换 = 重写规则值，DNR 无法表达动态头，OpenReqHeader 已验证此工作法） |
| Set-Cookie 防串写 | 可选加固 | DNR 支持响应头 remove；默认不做，理由：绑定 tab 全部走虚拟 cookie 视图后，真 jar 里是谁不影响这些 tab，只影响未绑定访客 tab，体验等同单账号现状 |

### 4.5 时序保障三级策略（v2 按先例重排）

| 档位 | 机制 | 适用 |
|---|---|---|
| T1（默认） | **双轨制**（§4.3a）：静态 MAIN 壳 document_start 就位 + 种子握手喂数据；壳就绪前读写进内存暂存 Map | 常规打开/刷新/热启动全覆盖；唯一保证先于页面 bundle 的通道（MultiAccountContainers 同款选择） |
| T2（降级） | `executeScript({world:'MAIN', injectImmediately:true})` 数据内嵌注入 + 运行即自检（`document.readyState !== 'loading'` 且检测到未打补丁的原生 Storage → 说明壳缺席）→ `location.reload()` 一次 | 静态壳因故缺席（开发期热载、未来 Chrome 行为变化）；executeScript 受 SW 冷启动漂移数百 ms，仅作降级不担主责 |
| T3（逃生门） | `chrome.debugger` attach + `Page.addScriptToEvaluateOnNewDocument` + `Fetch.enable` 按 tab 动态改头/捕获 Set-Cookie | 需要 per-request 动态头（token 每请求刷新）或 Set-Cookie 捕获时的增强档：官方背书能力（blink-dev 答复），代价是黄条警告、DevTools 互斥、逐请求 pause/continue 开销，用户点掉黄条会断连 |

### 4.6 生命周期与容错

| 事件 | 处理 |
|---|---|
| SW 重启 | 绑定表存 `storage.session`：SW 冷启动时重建 DNR session 规则与虚拟视图映射（tabId 有效才恢复）|
| 浏览器重启 | session 全失 → 有意回落单账号体验；token 库（加密, storage.local）在用户再次点击账号时注入新 tab 恢复 |
| duplicate tab / Ctrl+N 新窗 | 新 tabId 未绑定 → 显示「选择此标签页的账号」轻量弹条；复制场景预选源账号 |
| 用户手动登出某 tab | 仅清该 tab 虚拟视图 + 移除其 DNR 规则，其余 tab 无感 |
| 引擎卸载（回退兼容） | 引擎从未安装的场景本方案完全自足 |

---

## 5. 与实测四条逐条对账

| # | 实测障碍 | 本方案如何绕开 |
|---|---|---|
| 1 | Bearer 鉴权载体在 Authorization 头 | DNR 按 tabId 在网络出口强制改写；不管页面发什么，出浏览器前都是正确 token |
| 2 | 服务端不互踢 | 不是障碍而是红利：N 个 token 并行有效正是并行在线的前提 |
| 3 | 前端失效自检（清 Cookie 即踢） | 我们**永不删真实存储**、永不「切罐」；每 tab 的视图自洽连续，自检永远通过 |
| 4 | 共享 localStorage 被最后登录者覆盖 | localStorage 被劫持为 per-tab 虚拟视图后，写操作根本进不了真实存储；identity/fp 键各 tab 各持一份 |

原结论修正为：**「纯扩展无法并行在线」的成立前提是不做双平面接管；一旦接受 MAIN-world 注入 + DNR 两项官方能力，结论不再成立。**

---

## 6. 已知风险与开放问题

| 风险 | 影响 | 缓解 |
|---|---|---|
| 站点把 token/指纹写入 IndexedDB（而非 localStorage） | IDB 是异步 API 且对象深嵌，per-tab 虚拟化成本陡增 | PoC 第 1 步盘点；若有，优先确认是否参与鉴权决策，多数情况下自检只看内存/localStorage |
| 页面 bundle 在 prelude 前执行的残余竞态 | 极早期读取拿到旧全局值 | §4.5 三级策略 + reload 兜底 |
| 站点反篡改（toString 检查代理、冻结 localStorage） | shim 被识别或报错 | 覆盖 `Function.prototype.toString`、descriptor 保形；内部工具风险低，仅作稳健性处理 |
| fetch/XHR 包装遗漏第三方 HTTP 库（axios 属 XHR/fetch 两者皆有） | 一般无遗漏 | 包装 XMLHttpRequest 构造器而非实例方法，覆盖面即完整 |
| WebSocket 升级请求的改头边界 | 页面上下文发起的 WS：DNR 正常生效；但 **SW/sharedWorker 内发起的 WS Upgrade 两类规则 API 都不生效**（已知 Chromium bug [1285664](https://issues.chromium.org/p/chromium/issues/detail?id=1285664)，社区实测确认） | PoC 实测目标站 WS 是否承载鉴权及发起方；若踩中盲区则 shim 拦 `new WebSocket()` 改 URL 参数 |
| DNR 规则条件省略 `resourceTypes` | **静默漏掉 main_frame**（顶层导航不在默认匹配集），OpenReqHeader 实测 gotcha | 每条规则显式写全 `resourceTypes` 列表（main_frame/sub_frame/xmlhttprequest/websocket 等） |
| RE2 兼容性导致整批规则被拒 | `updateSessionRules` 是原子的：一条 JS 能写但 RE2 不认的 `regexFilter` 会让整批 addRules 被拒（OpenReqHeader 因此实现整批失败后逐条重试） | 规则生成器只用 RE2 安全语法；批量更新失败时降级逐条添加并上报坏规则 |
| 同账号多 tab 并发 refresh 风暴 | 多 tab 各自内存持 token，过期同毫秒各打一次刷新，配 rotation 会互相吊销集体掉线（[dev.to 事故复盘](https://dev.to/nileslabs/the-multiple-browser-tab-token-trap-synchronizing-jwt-refresh-across-browser-tabs-45f6)） | 锁名按 accountId 命名空间化 + 双检（拿锁后再看时间戳，5s 内他人刷过直接复用）；Web Locks 按 origin 共享，必须带账号标识 |
| tab 复用残留规则 | 受管 tab 导航去别家再回来 / 关闭时，旧 session 规则残留会对后续页面继续改头 | 挂 `tabs.onRemoved` 清规则；`onUpdated` 校验 URL host 与绑定表不符即摘除该 tab 全部规则 |
| session 规则上限 | 规则数爆炸 | MAX_NUMBER_OF_DYNAMIC_AND_SESSION_RULES 数千级，远超 tab 数量级 |
| DNR 权限模型选型 | 影响安装提示与生效范围 | 官方确认 `declarativeNetRequest` 与 `declarativeNetRequestWithHostAccess` 「provide the same capabilities」，仅授予时机不同；本项目推荐后者：复用现有 `site-auth.ts` 动态授权（optional_host_permissions），对已授权站点改头即生效，**不增加任何安装期权限警告**，延续最小权限策略 |
| 性能：每 tab 全量 storage.session 视图拷贝 | 大 key 场景变慢 | 本站键少（token/user/fp 几项），量级无忧 |

---

## 7. PoC 验证清单（建议顺序执行）

1. **鉴权载体全量盘点**（半天）：DevTools 抓 `/api/*` 全部请求，确认鉴权只出现在哪些位置（Authorization 头？query？WS 握手？下载链接 token？）；核对 localStorage/cookie/IndexedDB 中各自存放什么键；找出登录响应的 Set-Cookie 结构与 token 刷新方式。
2. **DNR 最小 PoC**：两个手工 tab，手工登记两条 session 规则指向两个不同有效 JWT，验证所有 API 调用在服务端日志分属两个身份（约 0.5 天）。
3. **注入双轨 PoC**：先验静态 MAIN 壳在目标站的时序可靠性（console 打点确认壳早于 bundle）；再联调种子握手，验证 `(a)` 冷启动直连首页读到的 token/user 正确、`(b)` 刷新后仍正确、`(c)` 并行窗口 UI 显示各自身份；对照 executeScript(T2) 降级路径的竞态率。（1~2 天；DNR 骨架参照 [OpenReqHeader dnr-rules.js](https://github.com/vladdenisov/openreqheader/blob/master/src/js/dnr-rules.js)，壳与 Storage 补丁参照 [MultiAccountContainers storage-isolator.js](https://github.com/worldof01/MultiAccountContainers/blob/master/content/storage-isolator.js)）
4. **组合联调**：两账号并行 30 分钟压力观察；期间混合 F5、SPA 内部路由切换、新开同站 tab、退出再登录。
5. **边角回归**：SW 手动终止（chrome://serviceworker-internals）、浏览器重启、duplicate tab。

---

## 8. 先例参考（15 条核实发现，按主题归并）

### 8.1 SessionBox 及其克隆

| 先例 | 链接 | 机制与启示 |
|---|---|---|
| SessionBox 商业版 | [Vivaldi 论坛长帖](https://forum.vivaldi.net/topic/25289/multi-account-containers/) · [sessionbox/toolkit](https://github.com/sessionbox/toolkit) | Chromium 内核每 profile 仅一个存储上下文（无 Firefox contextualIdentity 等价物），是内核级限制；SessionBox 免费版做到标签页级 cookie/storage 虚拟化，完整体验依赖桌面配套体系——纯扩展的能力上界与我们的「JWT 无状态会话」子集正好匹配 |
| emmanuelroecker/SessionBox（~100 行克隆） | [GitHub](https://github.com/emmanuelroecker/SessionBox) | cookie 存 sessionStorage（天然每 tab 一份）+ `visibilitychange`/`onbeforeunload` 回写真实 jar；验证了最小同步闭环，但后台 tag 发请求时 jar 里是别人的值——cookie-swap 类方案的死穴实证 |

### 8.2 DNR 按 tab 改头（主通道先例）

| 先例 | 链接 | 机制与启示 |
|---|---|---|
| **OpenReqHeader**（ModHeader AGPLv3 开源分支）★可直接对照编码 | [GitHub](https://github.com/vladdenisov/openreqheader) · [dnr-rules.js](https://github.com/vladdenisov/openreqheader/blob/master/src/js/dnr-rules.js) | `updateSessionRules` + `condition.tabIds` 的「Tab locking」即我们要的形态；任何变更全量重建规则集；三个工程 gotcha：①规则条件不给 `resourceTypes` 会静默漏 main_frame；②RE2 不认的 regexFilter 会让整批 addRules 被拒，需逐条重试降级；③MV3 禁 eval，头部值必须是预计算静态值（token 刷新 = 重写规则） |
| requestly 官方 MV3 最小 POC | [GitHub](https://github.com/requestly/modify-headers-manifest-v3) | 三行核心演示 modifyHeaders 规则装载，可作 PoC 模板（dynamic → session + tabIds 即为本项目原型） |
| Requestly 主扩展 MV3 源码 | [rulesManager.ts](https://github.com/requestly/requestly/blob/main/browser-extension/mv3/src/service-worker/services/rulesManager.ts) · [ajaxRequestInterceptor.js](https://github.com/requestly/requestly/blob/main/browser-extension/mv3/src/client-scripts/ajaxRequestInterceptor.js) | 全量删+全量加的规则管线 + `getMatchedRules({tabId})` 做「本 tab 命中哪些规则」调试面板；页内 XHR/fetch 补丁的双引擎架构与我们同构 |
| 官方硬约束 | [DNR 文档](https://developer.chrome.com/docs/extensions/reference/api/declarativeNetRequest) · [MDN RuleCondition](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/declarativeNetRequest/RuleCondition) | `tabIds` 仅限 session 规则、空列表非法；`MAX_NUMBER_OF_SESSION_RULES = 5000`（Chrome 120 前 dynamic+session 合计）；`excludedTabIds` 可反向排除（默认账号 tab 免建规则） |

### 8.3 「假容器」反面教材与 Firefox 架构参照

| 先例 | 链接 | 机制与启示 |
|---|---|---|
| oxcl/boxbox ★反面教材 | [GitHub](https://github.com/oxcl/boxbox) | Chrome 上退化为 cookie jar swap：`tabs.onActivated` 时全量 save/restore 真实 jar。实证致命伤：**后台 tab 的轮询/长连接必然读到错误会话**——这是我们必须选 DNR per-tab 改头而非交换的根本论据；其 tabBindings 存 `storage.session` 的结构可抄 |
| mozilla/multi-account-containers | [GitHub](https://mozilla.github.io/multi-account-containers/) | Firefox 独有 contextualIdentities + cookieStoreId；URL→容器分配决策表 + `tabs.onUpdated` 纠偏 + 关闭清理的状态机值得平移到 per-tab 账号模型 |
| stoically/temporary-containers | [GitHub](https://github.com/stoically/temporary-containers) | 临时容器自动分配与生命周期清理（关闭即清 cookie store），对应我们「tab 关闭清虚拟视图 + 清规则」的设计 |

### 8.4 MAIN-world 注入与 CDP 增强（最接近目标的全套活体样本）

| 先例 | 链接 | 机制与启示 |
|---|---|---|
| **worldof01/MultiAccountContainers（Chrome 移植版）**★全套活体样本 | [GitHub](https://github.com/worldof01/MultiAccountContainers) · [storage-isolator.js](https://github.com/worldof01/MultiAccountContainers/blob/master/content/storage-isolator.js) · [debugger-manager.js](https://github.com/worldof01/MultiAccountContainers/blob/master/lib/debugger-manager.js) | 四层组合拳：①manifest 静态 `world:"MAIN", document_start` 同步可靠先行（避开一切注入竞态）；②`Storage.prototype` 方法级前缀补丁 + IndexedDB/CacheStorage 名前缀 + 伪造 SW 注册阻断共享 Service Worker；③`document.cookie` 整体重定义 + 补丁 `pushState/replaceState` 在 SPA 路由后重新校验；④cookie 真实来源走 `chrome.debugger + Fetch.enable(requestStage:'Request')` 按 tab 改写请求头、CDP Network 事件捕获 Set-Cookie 写回专属 jar（页面层拿不到 Set-Cookie——forbidden header，v4.1 注释专门记录放弃原因）。黄条警告全程可见是主要代价 |
| chrome.debugger 动态改头的官方背书 | [blink-dev 答复](https://www.mail-archive.com/blink-dev@chromium.org/msg10000.html) · [chrome.debugger 文档](https://developer.chrome.com/docs/extensions/reference/api/debugger) · [CDP Fetch 域](https://chromedevtools.github.io/devtools-protocol/tot/Fetch/) · [NetScope 实例](https://github.com/dereferencex/NetScope) | Chrome 团队成员确认扩展可用 `Fetch.continueRequest` 改写出站请求头（WebPageTest 生产使用）；attach 天然按 tab 定界；同一 target 只允许一个调试客户端（用户开着 DevTools 即失败），黄条无法编程隐藏（[SO](https://stackoverflow.com/questions/63441002/chrome-extension-clear-infobar-label-after-debug-mode)、[chromium docs #7568](https://github.com/GoogleChrome/developer.chrome.com/issues/7568) 点掉横幅即断连） |
| MAIN 注入时序竞态实证 | [SO: executeScript 太慢](https://stackoverflow.com/questions/71126300/chrome-scripting-executescript-takes-too-long-to-start-at-document-start) · [SO: DOM 插脚本异步加载](https://stackoverflow.com/posts/72607832/revisions) | executeScript 受 SW 冷启动拖累漂移数百 ms；插 `<script src>` 因资源异步加载同样晚于页面 bundle —— 唯一保证先于一切页面脚本的通道是 manifest 静态声明 MAIN@document_start（§4.3a 轨 1 的依据） |
| localStorage 属性覆写语言层陷阱 | [JSC 下 non-configurable 抛错](https://stackoverflow.com/questions/20547744/change-the-value-of-window-localstorage-in-javascriptcore-redefining-un-configu) · [node#59310 legacy platform objects 讨论](https://github.com/nodejs/node/issues/59310) · [模拟 storage 事件](https://stackoverflow.com/questions/38952907/simulate-programmatically-storage-event) | V8 下 `window.localStorage` configurable=true 可整包替换，但整包比方法级更难保真：toString 探测、引用缓存、`getItem` 同步语义被异步后备破坏、shim 必须手动派发合成 StorageEvent——先例因此选择 prototype 方法级补丁（§4.3a） |
| 多 tab JWT refresh 风暴与 Web Locks 双检 | [dev.to 复盘](https://dev.to/nileslabs/the-multiple-browser-tab-token-trap-synchronizing-jwt-refresh-across-browser-tabs-45f6) | 同账号多 tab 并发刷新互相吊销；解法 `navigator.locks` 排他锁 + 双检。映射到本方案：锁名带 accountId（Web Locks 按 origin 共享）、401→单飞刷新→通知后台重写该 tab 规则→重放原请求 |

### 8.5 直接对照编码的两个仓库

1. **[OpenReqHeader](https://github.com/vladdenisov/openreqheader)**（AGPLv3，注意许可证义务）：DNR session 规则引擎骨架直接照抄；
2. **[MultiAccountContainers Chrome 移植版](https://github.com/worldof01/MultiAccountContainers)**：MAIN-world document_start 壳 + Storage.prototype 补丁 + history 钩子的完整参考实现。

---

## 9. 结论

1. 原「纯扩展无法同站多账号并行在线」结论应**收敛限定**为「未做双平面接管的前提下」。MV3 的 DNR(tabIds)+modifyHeaders 与 MAIN-world 注入两项官方能力叠加后，Bearer 无状态鉴权反而从最大障碍变成最有利地形：**服务端不互踢 + 出口可改头 + 内存天然隔离**，三个条件首次在同一架构下同时成立。
2. 推荐按 §4 双平面架构推进（注入走 §4.3a 双轨制，DNR 骨架与 MAIN 壳分别有 §8.5 两个开源仓库可直接对照编码），实施节奏上先跑通 §7 的 5 步 PoC（预计 2~3 个工作日可出真伪判据），PoC 通过后再讨论并入现有扩展的具体模块划分。

---

## 10. 实现落地记录（2026-08-28）

方案 C 已按本文档实现进扩展本体。模块 ↔ 文档映射：

| 模块 | 文件 | 对应章节 |
|---|---|---|
| 账号模型（页签名/账号名/加密密码，IDB v2） | `src/shared/types.ts` · `src/storage/db.ts` · `src/background/core/parallel-store.ts` | 目标 4 |
| 网络平面：DNR session 规则（tabId→Authorization 改写） | `src/background/core/tab-rules.ts` | §4.4 / §8.2 |
| 存储平面：MAIN 壳（Storage.prototype 命名空间补丁 + token 写入上报 + fetch/XHR 头嗅探 + reload 守卫） | `src/content/shield-main.ts` | §4.3a / §8.4 |
| ISOLATED 桥（runtime ↔ postMessage 中继） | `src/content/shield-bridge.ts` | §4.3a |
| 编排：tabId↔accountId 绑定表 / 打开账号 / token 捕获→规则同步 / 标题应用 | `src/background/core/parallel-session.ts` · `service-worker.ts` | §4.5 / §4.6 |
| 管理页（浏览器账号区为主视图；引擎区降级为可选隐藏） | `src/ui/parallel/*` | 目标 1/2/3 |

关键实现决策（v1）：
- **绑定时序**：壳在 document_start 直通；收到 bind 且 `readyState==='loading'` 才装补丁；晚到则消耗一次性 sessionStorage 守卫后 reload 一轮强制收敛（有限次保证）。
- **token 捕获双通道**：命名空间内 `__auth_token__` 的 setItem/removeItem 上报（主）+ 出站 `Authorization` 头嗅探（备）；JWT 形态校验后写入 `chrome.storage.session` 并同步该账号全部绑定 tab 的规则值。
- **DNR 规则范围**：resourceTypes 仅 `xmlhttprequest`+`websocket`（不给导航请求附带 Bearer）；host 权限由管理页在用户手势中显式申请。
- **登录复用**：并行账号的待登录凭证走既有 `sb:pendingAutoLogins` 协议，`auto-login.ts` 零改动可用。
- 待实测校准（§7 清单）：首屏竞态率、WS 是否承载鉴权、IndexedDB 侧键盘点、401 刷新流。
