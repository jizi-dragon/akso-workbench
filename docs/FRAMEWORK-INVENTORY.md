# 可复用框架清单（FRAMEWORK）

> **目的**：把本项目里**与任何具体平台、客户、行业无关**的能力盘点清楚，形成一个可以
> 复用到下一个产品的框架边界。判据只有一条：
>
> **换一个目标平台后，这个文件/模块是否需要改动？**
> 不需要 → 属于框架；需要 → 属于下游（业务/平台适配层）。
>
> **配套**：[PLUGIN-UPSTREAM-RESEARCH.md](PLUGIN-UPSTREAM-RESEARCH.md)（剥离为插件的可行性、
> 工作量、POC 验证、风险登记）。本文只做**清单与判据**，不重复论证。
>
> 实测时间：2026-09-14（v0.3.5）。扫描已排除 `node_modules/`、`dist/`、`build/`、`.venv/`、`__pycache__/`。

---

## 1. 一句话结论

**框架 = 12,069 行，其中真正的"平台泄漏面"只有 2 个文件、4 处。**

| 类别 | 行数 | 说明 |
|---|---|---|
| 桌面壳 `desktop/` | 1,002 | 窗口/托盘/热键/自动更新/出网通道/CDP 会话窗 |
| 扩展 `extensions/quick-login/.../src` | 5,514 | 六平面隔离 + 自动填表 + 桌面同步桥 |
| 通用服务 `workbench/services/{accounts,browser_pool,autologin,storage,settings,deepseek}.py` | 1,968 | 账号库 / 托管浏览器 / 自动登录门控 / 存取 / 设置 / LLM 客户端 |
| Agent 运行时 `workbench/harness/` | 533 | 循环 + 工具注册表 + 上下文裁剪 + 步数门控 |
| 数据与配置层 `workbench/{db,config}.py` | 422 | SQLite + 迁移机制 + 路径/端口 |
| 前端骨架（10 个通用文件） | 2,630 | 对话/设置/账号中心/轮盘 + 令牌与组件样式 |
| **合计** | **12,069** | |

> ⚠️ 注意：`workbench/services/paths.py` 只有 47 行但**含 3 处平台命名**，故**不列入框架**（见 §3 D 类）。

---

## 2. 判据（把任何文件套进来就能判断）

对每个候选文件问三个问题，**任一为"是"即不属框架**：

1. **换平台要改吗？** （端点、字段名、状态机、对象编码、租户名 → 是）
2. **含客户/平台标识吗？** （域名、appId、租户名、产品名硬编码 → 是，但可参数化）
3. **只在某个业务流里才有意义吗？** （洞察报告渲染、监控解读 → 是）

补充判据（本项目特有）：
- **协议中立性**：框架与下游之间的通信必须是"通用动词"（打开账号/切换身份/取状态），
  不能出现"生成 eGMP 理解报告"这类动词。
- **可无业务运行**：删掉 `egmp/` 后框架仍能启动、能跑测试、能打包（功能自然缺失）。

---

## 3. 逐项清单

### A. 桌面壳 `desktop/`（4 文件 1,002 行）—— **全部属于框架**

| 文件 | 行数 | 通用能力 | 复用时要改什么 |
|---|---|---|---|
| `main.js` | 460 | sidecar 生命周期（spawn/kill/taskkill 回收）· 主窗 · 透明置顶轮盘窗（失焦自关）· 托盘 · 全局热键 · 会话窗开户（每账号 `persist:` 分区）· CDP 端口就绪前设置 · 扩展安装引导 | 端口常量（3 个）、热键、窗口尺寸、产品名 `Akso`×18、托盘文案 |
| `updater.js` | 349 | 更新状态机（启动 20s 首查 / 每 6h / 静默下载 / 退出时安装 / 手动检查）、`state()` 纯数据快照便于桩测 | 无（更新源在 `package.json`） |
| `proxy.js` | 150 | **出网通道自动选择**（直连优先→系统代理回落；`proxy.txt` 文件级开关；回环 bypass）——本项目实测踩过"能发现新版但下载不动" | 探测 URL 的 owner/repo |
| `shell-state.js` | 43 | 壳→服务的单向状态通道（原子写 JSON，15s 心跳） | 数据目录名 |

**复用价值最高的四个点**（自研一遍至少 1-2 周且坑极多）：
① 退出顺序（先 kill sidecar 释放句柄再拉安装器）；② 代理通道自动选择 + 回环 bypass；
③ 更新状态先落盘再弹窗；④ CDP「先开户后连接」的会话窗模式。

### B. 浏览器扩展 `extensions/quick-login/packages/extension/src`（23 文件 5,514 行）

| 模块 | 行数 | 归属 | 说明 |
|---|---|---|---|
| `content/shield-main.ts` | 806 | ✅ 框架 | 六平面中的 4 个（存储命名空间 / Cookie 袋 / 缓存分区 / SW·CacheStorage / IndexedDB） |
| `content/shield-bridge.ts` | 49 | ✅ 框架 | ISOLATED↔MAIN↔background 中继 |
| `content/auto-login.ts` | 377 | ✅ 框架（**需参数化**） | 自动填表；选择器（`请输入用户名` 等）应抽成"站点登录页配置" |
| `content/title-hook.ts` + `background/tabs/tab-title.ts` | 32 + 34 | ✅ 框架 | 页签名维持 |
| `background/core/parallel-session.ts` | 1,589 | ✅ 框架 | 绑定表 / token 双通道捕获 / Cookie 快照回放 / 种子下发（**单文件最大，也是通用性最强的资产**） |
| `background/core/tab-rules.ts` | 278 | ✅ 框架 | DNR 规则族构建与逐条降级安装 |
| `background/core/{parallel-store,credentials,pending-login,host}.ts` | 121+95+62+84 | ✅ 框架 | 档案 CRUD / AES-GCM 凭据 / 待登录凭证 / host 口径 |
| `background/core/site-auth.ts` | 159 | ⚠️ 休眠 | 授权/停用核心，声明式全站权限后已成死代码（保留防回退） |
| `background/account-wheel.ts` + `ui/wheel/{wheel-core,wheel}.ts` + `content/wheel-overlay.ts` | 89+307+140+282 | ✅ 框架 | 轮盘双形态（浮层/小窗）+ 扇区几何 |
| `background/sync.ts` | 368 | ⚠️ **框架，但要解耦** | 桌面同步桥。协议通用（快照/指令/状态），但**桌面地址与端点路径是编译期常量** → 应改构建期注入 |
| `background/service-worker.ts` | 300 | ✅ 框架 | 消息分发总装（**注意：不能无脑访问 `chrome.commands`**，见 [PITFALLS](PITFALLS.md)） |
| `ui/popup/popup.ts` | 59 | ✅ 框架 | 弹窗（统计 + 导出诊断 + 版本号） |
| `shared/{constants,messages,types}.ts` | 116+39+77 | ✅ 框架 | 协议键单一真源 |

> 扩展**没有一行**是平台专属的（唯一的平台痕迹是几处注释与产品名）。六平面隔离是通用多账号技术。

### C. 通用服务 `workbench/services/`（6 文件 1,968 行）

| 文件 | 行数 | 通用能力 | 复用时要改什么 |
|---|---|---|---|
| `browser_pool.py` | 624 | **Playwright 单线程池**：专职 worker + Future 队列（消除跨线程 greenlet）· 双模式（local/CDP）· 每账号独立 context · `storage_state` 免密 · 自愈 `heal_count≤2` · 事件只入队+间隙 drain | **1 处 `from .egmp.monitor import MonitorSession`** → 改为扩展点（见 §4） |
| `accounts.py` | 606 | 统一账号库：Fernet 加密 · 环境/账号 CRUD · 盒子分组 · 分配池 · 备份导出导入（含密钥） · 只读扫描导入 | 无（表结构与平台无关） |
| `autologin.py` | 371 | **节奏门控引擎**（MAIN world JS 注入）：字段齐备门槛 / 提交前 500ms 回读 / 100ms 去抖 + 800ms 轮询 / 用户点击接管 / 失败让位 2 次 / 跨 realm setValue / TokenCapture(JWT) | 登录页选择器（见 B 类 `auto-login.ts`） |
| `storage.py` | 121 | 会话/消息/设置/度量存取（只做存取不含业务） | 无 |
| `settings.py` | 66 | 设置集中建模 + camelCase 契约 | 键名清单（`apiKey`/`model`…） |
| `deepseek.py` | 180 | LLM 客户端：SSE 流式 + function calling + 错误映射 + JSON 模式 | 默认模型名/base_url |

### D. Agent 运行时 `workbench/harness/`（7 文件 533 行）

| 文件 | 行数 | 归属 |
|---|---|---|
| `loop.py` | 128 | ✅ 框架：`call → tool → observe → repeat`，事件流驱动，与传输层解耦 |
| `tools.py` | 230 | ✅ 框架：声明式工具定义（pydantic 生成 JSON Schema）+ 注册表 + 参数校验失败回传 |
| `types.py` | 71 | ✅ 框架：配置/消息/事件/工具结果 |
| `context.py` | 42 | ✅ 框架：token 估算 + 阈值裁剪 + 保最近 N 轮 |
| `safety.py` | 25 | ✅ 框架：步数预算 + 重复调用循环检测 |
| `prompts.py` | 35 | ❌ **不是框架**：内容是药企合规领域提示词 → 应改为"提示词由下游注入" |

### E. 数据与配置层（2 文件 422 行）

| 文件 | 行数 | 归属 |
|---|---|---|
| `db.py` | 340 | ✅ 框架：单连接 + RLock · `_sqlx_migrations` 兼容旧库 · 方言边界收拢（`_PRAGMAS` / `csv_like`） |
| `config.py` | 78 | ✅ 框架：数据目录解析（含旧库接管）· 路径锚点 · 冻结节（`_MEIPASS`） |

### F. 前端骨架（10 文件 2,630 行）

| 文件 | 行数 | 归属 |
|---|---|---|
| `static/index.html` + `js/app.js` + `js/api.js` | ~330 | ✅ 框架：外壳、视图路由、API 封装（**导航项应改为插件贡献**） |
| `js/chat.js` + `js/markdown.js` | ~420 | ✅ 框架：SSE 消费 + Markdown 渲染 |
| `js/settings.js` | ~80 | ✅ 框架：设置表单 |
| `js/modules-common.js` | ~90 | ✅ 框架：模块页公共件（`el`/`logLine`/`fmtBytes`/`badgeFor`/`postSSE`） |
| `pages/accounts.html` + `js/accounts.js` | ~1,300 | ✅ 框架：账号中心（盒子/卡片墙/四态徽标/批量/分配池/备份） |
| `pages/wheel-picker.html` | ~300 | ✅ 框架：轮盘（独立窗） |
| `css/*`（tokens/base/app/settings/splash/modules/ql-theme/ql-parallel） | ~1,100 | ✅ 框架：设计令牌与组件样式 |
| `pages/insight.html`/`js/insight.js`/`css/wheel.css` | ~400 | ❌ 下游（业务页）；`wheel.css` 是**无引用死文件**可删。<br>~~`pages/factory.html`/`js/factory.js`~~ → **2026-09-22 随配置工厂整体删除**（本行的 ~400 是删除后实测：83 + 219 + 96） |

---

## 4. 需要"框架化改造"的四类工作（这是真正的成本所在）

盘点显示框架的**代码几乎现成**，成本集中在下面四类"改造"，而非"重写"：

### 类型 1：参数化硬编码标识（**必修，否则第二个产品会串数据**）

| 标识 | 出现位置 | 建议做法 |
|---|---|---|
| 数据目录名 `Workbench` | `config.py`×2、`shell-state.js`×2、`desktop/package.json`×3、`proxy.js`×1、`blank.html`×1 | 收敛为 `APP_NAME` 常量（Python/JS 各一处，构建期注入） |
| `workbench` / `workbench-snapshot` | 11 个文件（协议名、备份 format、导入式扫描） | 保留为**协议常量**（协议名不必随产品改名，但要在契约文档里声明"协议名与产品名解耦"） |
| `com.workbench.app` / 更新源 `owner` `repo` | `desktop/package.json` | 抽为 electron-builder 的构建参数 |
| 产品名 `Akso`（31 处字符串） | `desktop/main.js`×18、`proxy.js`×4、`updater.js`×6、`shell-state.js`×3 | 抽为品牌配置 |
| 三个端口 `18765/18766/18767` | 壳、服务、扩展（**扩展里是编译期常量**） | 保留默认值但全部走配置；扩展侧改构建期注入 |

> ⚠️ **最大的隐患是端口与数据目录**：如果第二个产品直接复用框架而没改这两项，
> 两个产品会**争抢同一个 `%APPDATA%` 目录与同一个端口** —— 表现为"数据串台"或"第二个起不来"。

### 类型 2：把下游调用改成扩展点（**选修但强烈建议**）

当前框架里有 **2 处**下游调用，都要改成经 Registry 解析：

| 位置 | 现状 | 改成 |
|---|---|---|
| `browser_pool.py:534` | `from .egmp.monitor import MonitorSession`（写死在 `monitor_start` 里） | `reg.get("monitor.recorder")` 扩展点，框架只定义协议 |
| `services/paths.py`（63 行） | 含 `capa_plan`×1、`__c`×2 —— **核查后确认命中都在文档字符串里**（对象编码正则的举例 `capa_plan__c`），**不是平台硬编码** | 但它的职责是"平台产物命名规范"，换平台仍要改 → **移出框架**，改为下游提供命名策略 |

> ✅ **修正一个此前的误判**：我第一轮扫描曾在 `paths.py` 上标了"3 处平台硬编码"，
> 逐行核对后确认是**文档字符串里的举例**（对象编码正则的示例），实际代码是通用的。
> 这说明**特征词门禁必须逐条人工确认，不能只看命中数**（见 §6 的噪音说明）。

> 这 2 处就是全部。**框架与业务之间的代码耦合只有这么多**——这是本仓库最值钱的一个事实：
> 换平台要改的不是"框架的大部分"，而是 **2 个文件的 3 处**。

### 类型 3：领域内容外置（**必修**）

| 内容 | 现状 | 改成 |
|---|---|---|
| 系统提示词 | `harness/prompts.py` 写死药企合规提示词 | 框架提供 `build_system_prompt(pack)`，提示词包由下游提供 |
| 自动填表的登录页选择器 | `autologin.py` 与扩展 `auto-login.ts` 硬编码 `请输入用户名`/`input[type=password]` 等 | 抽成"站点登录页配置"（选择器列表 + iframe 策略），随站点配置下发 |
| 模块注册表 | `services/modules.py` 里 `insight` 是写死的适配器概念（模块 id 现为 `{workbench, accounts, insight}`；~~`akso-auto` / `factory`~~ 已于 2026-09-22 随配置工厂移除） | 泛化为"下游模块声明"（下游插件自己声明体检项） |

### 类型 4：前端导航与页面注册（**选修**）

`static/index.html` 的左侧导航当前写死了「对象分析 / 账号中心 / Skill 管理 / 沙箱」
（~~配置工厂~~ 那一项于 2026-09-22 随功能删除）。
框架化后应收敛为：**框架只提供账号中心 + 对话 + 设置；业务页由插件贡献导航项与静态页**
（POC 已验证 `add_nav` 可行、`GET /api/plugins` 能返回导航清单）。

---

## 5. 工作量（在 v0.3.5 实测基线上）

| 阶段 | 内容 | 人日 |
|---|---|---|
| **F0** | 本清单评审 + 划定"框架文件清单"（用 §2 判据逐个过一遍，约 120 个文件） | 0.5 |
| **F1** | 类型 1 参数化（含端口/数据目录，**最高优先级**） | 1.0 |
| **F2** | 类型 2 扩展点（2 处 + 引入 Registry 自检） | 1.0 |
| **F3** | 类型 3 领域外置（提示词 / 登录页选择器 / 模块声明） | 1.5 |
| **F4** | 类型 4 前端导航插件化 | 0.5 |
| **F5** | 框架可独立运行验证：删掉 `egmp/` 与业务页后能启动 + 能跑"框架自测" + 能打包 | 1.0 |
| **F6** | 框架边界门禁（CI 脚本：框架文件里出现平台特征词即失败） | 0.5 |
| **合计** | | **6.0 人日** |

> 比之前估的"方案 A 3-5 人日"略高，因为那版只做"可拆除"，**没有把框架真正抽干净**。
> 这一版的目标是"**可复用到下一个产品**"，多了领域外置与独立运行验证。

---

## 6. 框架边界门禁（防泄漏，建议做成 CI）

平台知识外泄进框架是**最容易发生、最难发现**的退化。建议在 CI 里加一道扫描：

```powershell
# 伪代码：框架文件清单（§3 的 A-F 类）中出现平台特征词即失败
$FRAMEWORK = @("desktop/*.js", "extensions/quick-login/packages/extension/src/**/*.ts",
               "workbench/harness/*.py", "workbench/services/{accounts,browser_pool,autologin,storage,settings,deepseek}.py",
               "workbench/{db,config}.py", "workbench/static/{index.html,js/app.js,js/api.js,js/chat.js,js/markdown.js,js/settings.js,js/modules-common.js,js/accounts.js,pages/accounts.html,pages/wheel-picker.html}")
$FORBIDDEN = @("egmp", "capa_plan", "change_control", "FieldPage", "BasicObject",
               "GetWorkflowBasicPageViewList", "GetMenuList", "lifecycleStatus", "Gaia", "<租户名>", "<平台域名>")
```

> 注意排除 `node_modules/`、`dist/`、`build/`（本次盘点若不过滤会得到 1,379 处误报，
> 绝大多数来自 `lodash`/`typescript` 里的 `__c` 变量名与打包产物）。**`pageIndex` 也属误报**
> （轮盘分页与平台分页同名）——特征词表要按实际噪音收敛。

---

## 7. 与"插件化"的关系（两件事，别混为一谈）

| | 目的 | 判据 | 工作量 | 做完的收益 |
|---|---|---|---|---|
| **框架化**（本文） | 让通用能力**可复用到下一个产品** | 换平台后是否需要改？ | 6 人日 | 新产品直接复用 12,069 行，省 2-4 周（尤其壳/更新/扩展那三项） |
| **插件化**（[另一份文档](PLUGIN-UPSTREAM-RESEARCH.md)） | 让业务能力**可独立演进/拆除** | 能否在不碰宿主的前提下新增业务功能？ | 3-5 人日 | 业务迭代不污染框架；权属/交付边界清晰 |

**两者重叠部分只有"类型 2 扩展点"这一项**（约 1 人日）。建议顺序：
**先 F1（参数化，防串数据）→ 再做插件化的扩展点 → 最后 F3/F4（领域外置）**。
理由：F1 是**风险**（不做会串台），扩展点是**架构**（不做会返工），F3/F4 是**整洁度**（可以后置）。
