# AGENTS.md —— 给在这个仓库里干活的 AI 协作者

> **这份文件是写给 AI 编码助手的，不是写给人看的。**
>
> 人能容忍模糊的说明——他会问、会试、会从上下文推断。AI 不会：它会把"没写下来的约定"
> 当成"不存在这个约定"，然后用**看起来完全正常**的代码违反它。
>
> 所以下面每一条都是**规则形状**的（祈使句 + 判据），且**每条都注明了它来自哪次真实事故**。
> 没有事故背书的偏好不写在这里——`CONTRIBUTING.md` 才是人的入口。
>
> | 想了解 | 去哪 |
> |---|---|
> | 代码地图 / 红线 / 风险台账 | [docs/CODEBASE_OVERVIEW.md](docs/CODEBASE_OVERVIEW.md) |
> | 真机环境坑（89 条，只增不改） | [docs/PITFALLS.md](docs/PITFALLS.md) |
> | 不再变的决策（16 篇 ADR） | [docs/adr/](docs/adr/) |
> | **★ 平台事实台账（端点 / 真机线格式 / 样本量 n / 平台约束，逐条带证据）** | [docs/PLATFORM-OPERATIONS-LEARNED.md](docs/PLATFORM-OPERATIONS-LEARNED.md) |
> | **★ 平台配置面整体认知（Gaia V3.8 手册语义与配置分层）** | [docs/FACTORY-PLATFORM-COGNITION.md](docs/FACTORY-PLATFORM-COGNITION.md) |
> | **★ 用户传图片/文件给 agent（方案 + 判据 + 已知限制）** | [docs/AGENT-FILE-INPUT-PLAN.md](docs/AGENT-FILE-INPUT-PLAN.md) |
> | 平台取证报告存档（真机报告 + 手册笔记；**不属于产品，工具链已移除**） | [factory-tuning/reports/](factory-tuning/README.md) |
> | 核心 Agent 架构与分层契约 | [docs/CORE-ARCHITECTURE.md](docs/CORE-ARCHITECTURE.md) |
> | 有哪些工具、怎么加一个 | [docs/TOOLS.md](docs/TOOLS.md) |
> | 人的协作规范 | [CONTRIBUTING.md](CONTRIBUTING.md) |

---

## 0. 动手之前（跳过这三步是绝大多数返工的来源）

1. **读** [docs/CORE-ARCHITECTURE.md](docs/CORE-ARCHITECTURE.md) §5 的「不变量清单」——
   那是本仓库的地基，违反它们**不会报错**，只会静默变差。
2. **先跑一遍门禁，确认"本来是绿的"**（§3）。不先确认基线，你无法区分
   "我弄坏的"和"本来就坏的"。
3. **改完必须重跑同一套门禁**。**别把"上次的绿"当成"这次的绿"**——
   PITFALLS #41 就是这么栽的：pytest 绿了，ruff 没重跑，而那一行恰好是 ruff 能抓的 `F821`。

---

## 1. 铁律

> 违反这些**不会抛异常**，只会让某个指标在几天到几个月后悄悄归零。
> 每条后面的括号是它的出处，改之前先读那一条。

### 上下文与缓存（直接决定 API 账单，价差 50×）

| # | 规则 |
|---|---|
| 1 | **已发出的 messages 前缀永不改写。** 上下文只能 `append`。不要"每轮按最近 N 轮裁剪"——那样前缀每轮滑动，DeepSeek 前缀缓存命中率**恒为 0**，成本 ×50（`cache.py:143` 有明确警告） |
| 2 | **摘要边界（`summarized_upto`）只前进、不回退。** 它用"消息条数"而不是 id，正是为了单调递增能做到（`cache.freeze_summary`） |
| 3 | **每次请求的 messages 必须满足** `[system] + [冻结摘要?] + [原文历史] + [新消息]`。摘要一旦冻结就不再重算 |
| 4 | **注入上下文的一切都必须有硬上限。** 工具结果经 `plugin_api.MAX_RESULT_CHARS` 截断，且截断必须**显式告知模型**，不许静默丢弃 |
| 5 | **不要在运行时按轮次拼系统提示词。** system 是前缀的第一个字节，每轮变一次 = 每轮全 miss（`harness/prompts.py`） |

### 工具体系

| # | 规则 |
|---|---|
| 6 | **工具只有一个定义处**：`workbench/plugins/*.py` 里的 `ToolSpec`，且**必须显式声明 `risk`**。**不要**在 `routes_agent.py` 或 `harness/` 里另建工具表——2026-09-15 之前两套并存，后果是"对话里的模型看不到任何平台能力" |
| 7 | **依赖方向单向**：`plugins → plugin_api`，**永不反向**。`plugin_api.py` 里出现平台术语、import 插件、或 import `db` 都是错的（它连审计与确认门都靠注入） |
| 8 | **`_tools` 是派生视图**：只有 `ToolHub._rebuild()` 能写它。不要在任何地方单独改 `_tools`——那会造出"摘了插件但工具还在"的静默泄漏 |
| 9 | **风险工具没有令牌就不执行。** 这是 `ToolHub.execute` 的硬闸门，不是提示层。回归断言的是"handler 调用记录为空"，不是返回码 |
| 10 | **工具名必须匹配 `[A-Za-z0-9_-]{1,64}` 且是 ASCII**。中文工具名会被 DeepSeek/OpenAI **在请求侧拒绝**，本地校验发现不了 |
| 11 | **工具异常 = 观测，不是异常。** `execute()` 里任何失败都要转成 `ToolStatus`，让它成为模型可自我纠正的观测，不要抛出去打断循环 |
| 12 | **同步阻塞调用一律 `asyncio.to_thread`**（`egmp.client` / `browser_pool` / `proc` / `sandbox.run` 都是同步的）。否则表现是**整个服务卡住**，不是报错 |
| 33 | **模型发起的确认（`needs_confirmation`）不是安全边界**，它买的是"模型可以先问再做"，`Risk` 买的是"不做就不能做"（[ADR-0016](docs/adr/0016-model-initiated-confirmation.md) §5）。**不要**把它写成"高风险操作的批准环节"，**不要**因为它存在而放宽任何 `Risk` 分级。判据：`grep -rn "needs_confirmation=True" workbench/plugins/` 里每一个的 `risk` 都必须是 `READ`（`ToolSpec.valid()` 在装配期强制） |
| 34 | **不要在工具 handler 里等用户**。`execute()` 一旦 `await`，调用它的异步生成器就挂起了，事件发不出去 → 界面不知道有东西要确认 → 用户永远点不了。跨"通知 → 等答复 → 继续"的交互**必须把等待放在循环层**（`harness/loop.py` 发完 `confirm_required` 再 `await registry.await_confirmation`）。这条不是偏好，是架构约束（[ADR-0016](docs/adr/0016-model-initiated-confirmation.md) §D-2） |
| 35 | **"发通知 → 等对方回 → 我再登记等待"的形状，接收端必须提前建好**。对方（用户/子进程/worker）的响应会比你开始等更早到达，晚建的 `Future` 接不住它 → 表现为"对方明明答复了，我方干等满一个超时"（PITFALLS #57 实测：实时确认卡了整整一个 `timeoutS`）。判据：`pytest tests/test_user_confirmation.py -o addopts=""`——它**在 `confirm_required` 事件的回调里**就答复，命中这个时序；挂起不返回 = 本条复发 |
| 36 | **给 Agent 接一条既有能力链时，先问"这条路上有哪几步只有人能做的"，把答案逐个变成工具**。缺一个动词不会报错、不会让测试变红——它只让流程走不通，而走不通的表现是**模型开始输出"请你去做某事"**（PITFALLS #59 实测：模型能校验蓝图、能创建，却**不能把校验通过的东西交给执行**，于是只能让用户"把文件拖进页面"）。判据：`Get-ChildItem -Recurse workbench -Include *.py,*.js \| Select-String '拖进页面\|页面上传\|让用户上传'` —— **必须零命中** |
| 37 | **"输出太长被截断"必须与"参数写错了"分开**。`finish_reason == "length"` 是唯一的信号，丢了他模型只会看到「参数解析失败」，于是原样重试、又截断一次（PITFALLS #58）。收流式响应时**必须收 `finish_reason`**；截断时**绝不让半截参数进工具层**，并明说"没有执行任何工具"。判据：`pytest tests/test_agent_loop.py::test_truncated_output_is_not_reported_as_bad_args -o addopts=""` |

### 沙箱与技能

| # | 规则 |
|---|---|
| 13 | **沙箱 Fail-Closed**：未知策略名、`cwd` 越界、命中敏感路径、解释器不在白名单 —— **一律拒**，不回落到默认策略 |
| 14 | **审计写在唯一扼流点**（`sandbox.run()`，`_run()` 只被它调用）。不要在调用方补审计——PITFALLS #42：`audit.record` 一度**没有任何调用方**，E4 层在文档上写着"已实现"而实际永远空转 |
| 15 | **脚本式 Skill 的 `risk` 不得低于 `write`**。上传的代码无法被静态证明只读，标 `read` 等于给模型一张"不经确认随便跑"的通行证 |
| 16 | **PowerShell 与 `cmd` 刻意不在沙箱解释器白名单内**。加进去等于把"执行脚本"变成"执行任意命令" |
| 17 | **沙箱不是安全边界，是限损层**（[ADR-0014](docs/adr/0014-sandbox-for-untrusted-code.md) §D-3）。不要在任何地方把它描述成"安全"，也不要把 `allow_network` 当成强制——它是声明 |

### 数据与凭据

| # | 规则 |
|---|---|
| 18 | **DB 迁移只增不改**，历史迁移（尤其 1–6）不可变。`job_logs` 建了索引但零写入是**故意的**，不要"顺手"改成入库 |
| 19 | **凭据不落明文。** 新通道必须沿用「密文 + 密钥」回环语义；接口响应必须剔除 `password_enc`；文档与日志里一律用占位值 |
| 20 | **审计维度不许含凭据。** `audit_extra` 只抽环境/对象/账号；`redact_args` 会再过一遍，**但不要指望它兜底** |
| 21 | **请求体一律 pydantic `BaseModel` 且逐字段显式建模。** `BaseModel` **默认忽略多余字段** → 漏字段会**静默失效**（`AccountPatch` 漏 `box` 是已发生事故，PITFALLS #6） |
| 22 | **`Path.is_relative_to()`，不要用字符串前缀**判路径包含。`.../insight/abc` 在字符串上"包含" `.../insight/abc_evil` |

### Playwright

| # | 规则 |
|---|---|
| 23 | **sync API 单线程单实例**：所有浏览器操作**必须**走 `browser_pool` 专职线程队列；同线程禁止第二个 `sync_playwright()` |
| 24 | **事件回调内禁止连接调用**（`response.text()` / `page.evaluate` 重入 = worker 死锁）。回调只入队，worker 空隙统一 `drain()` |
| 25 | **测试不得另起 chromium 夹具**（PITFALLS #1） |

### 日志与留痕

| # | 规则 |
|---|---|
| 26 | **凭据永不入日志。** 打码在**落盘前的唯一通道**上做（`logging_setup.RedactionFilter`），不依赖调用方自觉；新增 logger 不需要额外做什么，但**不要绕过 `configure()` 自己装 handler** |
| 27 | **日志失败绝不影响主流程。** `DailySizeRotatingFileHandler.handle()` 是强制点（`logging.Handler.handle` 本身**不包 try**，`emit` 抛异常会一路传到业务代码）。新增 handler 必须同样包一层 |
| 28 | **日志不复制审计的内容。** 权威在 `agent_audit` 等表，日志只带 `auditId` / `jobId` **指过去**。两处都写全量 = 第二个会漂的事实源 |
| 29 | **关联 ID 用 `logging_setup.bind(...)`**（`traceId` / `conversationId` / `auditId` / `jobId` / `tool`），不要靠函数参数层层传 |
| 30 | **敏感路径表只有一张**（`config.SENSITIVE_PATH_PARTS`）。沙箱与只读工具都从它读——**曾经有两张**，只读工具用的是弱的那张，实测能完整读出 cookie/JWT/Fernet 密钥 |

### 进程生命周期

| # | 规则 |
|---|---|
| 31 | **凡"由父进程负责清理"的资源**（子进程 / 端口 / 锁文件 / 临时目录），必须回答一句 **"父非正常死亡时谁释放"**。答案只有两个：**子进程自己盯着父**（`workbench/lifecycle.py::watch_parent()` 用 `WaitForSingleObject` 等父的句柄），或**由 OS 内核对象担保**（Job Object / cgroup / `prctl(PDEATHSIG)`）。**不要**把清理只挂在 `before-quit` 这类正常退出链上——强杀与崩溃时那条链根本不跑（PITFALLS #54 实测复现：父被 `taskkill /F` 后 sidecar 存活并占着 18765） |
| 32 | **界面文案是承诺**：菜单项 / 提示条 / 错误页指引里描述的每个行为，都必须能在代码里找到实现。**改行为与改文案必须一起做**（PITFALLS #55：「关闭窗口（应用继续在托盘运行）」而实际关窗即退出，托盘「显示主窗」因此永远点不出窗口）。判据：`Select-String -Path desktop/main.js -Pattern "on\('close'"` —— 零命中就说明"关窗不退出"没有实现 |
| 38 | **交付给用户实测之前，最后一步必须是「杀干净 + 重启」，不是「跑完测试」。** 违反它的后果**不是报错，是归因错误**——而错误归因会烧掉整轮排查。2026-09-21 实录：用户三次报告「自动更新弹窗自己冒出来」，前两轮我在 `workbench/` 与 `desktop/` 里翻遍了调用方（先后误判成"误点角标""外部网页"），真凶是**我自己跑的测试**：`tests/test_update.py` 里一条用例**真的**去 POST 了本机控制端口，于是每跑一次全量，用户屏幕上就弹一次模态框——而它**始终是绿的**（PITFALLS #71）。同一个晚上还有另一半：改了壳侧代码却没重启，用户测的还是旧进程（#20/#25 的交付侧版本）。<br>**判据（交付前跑，三条都要过）**：① `Get-NetTCPConnection -LocalPort 18765,18766,18767 -State Listen` —— **必须为空，或全部属于同一个刚启动的进程**；② 改过 `workbench/**` 或 `desktop/**`（Python / 壳）**必须重启应用**（只刷新页面对 `static/**` 才够）；③ **跑测试与用户实测分成两段**，跑完测试要重启一次再交付——别让测试的副作用留在现场。<br>**为什么单列一条**：固定端口（[ADR-0012](docs/adr/0012-fixed-ports.md)）让"旧进程还活着"和"测试戳到了真端口"这两件事**都不报错**，只让现象指向错误的嫌疑人 |

---

## 2. 命名与形状约定

- **工具名**：`<plugin_id>_<动作>`，ASCII，见规则 10。
- **插件 id**：小写字母/数字/`.`,`_`,`-`。
- **沙箱策略名**：**必须用 ASCII 正则校验，不能用 `str.isalnum()`**——那个对中文返回 `True`（PITFALLS #40 同族）。
- **路径校验分两层**：正则管"由哪些字符构成"，`split("/")` 逐段管"分成哪几段"。
  正则**表达不了段语义**：`a/../../b.py` 会通过形状正则（`skills/manifest.py` 里有两条 `assert` 把这件事钉死，别把两层误合成一层）。
- **字段名双语义**：DB 行 `snake_case`（`env_base_url`）vs 导出/快照 `camelCase`（`envBaseUrl`）。
  读错键的后果是**扩展静默丢弃全部账号**（PITFALLS #14）。
- **前端字段是 Python→JS 的无类型接口**：改任何接口响应体，必须跟进消费它的前端字段。
  判据：`grep` 那个字段名在 `workbench/static/` 里的命中（PITFALLS #35）。

---

## 3. 改哪跑哪（门禁矩阵）

> **本机的真实命令**（`uv` / `ruff` 不在 PATH，见 §4）：

```powershell
# 基线（动手前 + 改完后各跑一次）
.\.venv\Scripts\python.exe -m pytest -o addopts=""        # 全量；默认 addopts 跳过 slow 层
.\.venv\Scripts\uv.exe tool run ruff check .              # ★ lint —— 先探一次，见 §4（这条会漂；2026-09-22 实测走这条）
.\.venv\Scripts\python.exe tools\gen_tools_doc.py         # 工具清单/数字 == 运行期中枢
.\.venv\Scripts\python.exe tools\verify_packaging.py      # 打包静态核对 13 项
```

| 你改了什么 | 必须额外跑 |
|---|---|
| `workbench/plugins/**` | `tools\gen_tools_doc.py --write`（重生成 [docs/TOOLS.md](docs/TOOLS.md) 清单块）**+** 确认 `server.spec` 的 `hiddenimports` 有该模块 **+** `REQUIRED_TOOLS`。<br>★ **新增/删除插件时三处必须同时改**，否则 `load_all` 会把 ImportError **吞成一条 failure**（它的隔离设计），表现为 `selfcheck` 静默变红而启动照常——2026-09-22 删配置工厂时正是这三处（`_MODULES`、`server.spec`、`REQUIRED_TOOLS`）一处都不能漏 |
| **附件链路**：`services/attachments.py`、`services/parsing.py`、`api/routes_attachments.py`、`api/routes_chat.py` 的扼流点与**写给模型的附件提示**、`plugins/builtins.py` 的 `attachment_*` | `pytest tests/test_attachments.py -o addopts=""`（**26 条**：落盘/拒收/解析坐标/展开成 parts/按坐标取/会话隔离/**提示措辞**）**+** `python tools\verify_attachments.py`（**24 项**端到端：真起生产入口、真发 multipart、**字节级**读回、三条拒收、扼流点展开、删除是真删）**+** `python tools\verify_attachments_live.py`（**真机联调 · 要花 token**：颜色 / 形状 / 表格 / **不撤回自己** / 负样本，五问全绿才算过）<br>★ **这条链路上"函数对"与"链路通"是两件事**：2026-09-22 真机联调抓到两个单测看不见的缺陷（`_pending` 未绑定 / **这一轮的消息绕过扼流点**）——见 [AGENT-FILE-INPUT-PLAN](docs/AGENT-FILE-INPUT-PLAN.md) §6.1。**改这条链路后必须另跑一次真机联调**。<br>★ **写给模型的那句话也是链路的一部分**（[PITFALLS #89](docs/PITFALLS.md)）：旧轮图片的引用话术一处措辞不当，模型就会**撤回自己刚答对的内容**（"前面是瞎猜的"）；内联提示里提起"能不能读"会让它**拒绝看图**。改任何 `[附件 …]` 提示文案后，措辞判据在 `tests/test_attachments.py`，端到端那条是 `verify_attachments_live.py` 的 2b |
| **输入区 / 附件 UI**：`static/index.html` 的输入框那一段、`static/js/chat.js` 的用量行与消息里的附件、`static/js/attachments.js`、`static/css/app.css` 的 `.usage-strip` / `.model-chip` / `.usage-ring` / `.msg-attachments` | `python tools\verify_composer_ui.py`（**32 项**：真起生产入口 + 真开 chromium，点会话、按发送，然后读**渲染后**的几何与计算样式）。<br>★ 为什么字符串断言不够（`verify_attachments.py` 的 A1–A9 只证明"源码里有这些字样"）：它抓不到这一整类**看起来完全正常**的失败——那一行还是 `hidden` / 被父元素裁掉、字号被别处一条更具体的规则压回 11px（用户上一轮的原话是「比蚂蚁还小的字」）、模型名换行掉到发送键**下面**、事件里少了 `contextTokens` 于是永远显示「上下文 —」。<br>★ **J/K 两组钉的是"三条输入入口真的能用"与"界面真的接住了服务端的事件"**：真点按钮（`expect_file_chooser`）、真派发带 `File` 的 paste/drop、发送后读**拦截下来的请求体**看 `attachmentIds`，再断言消息气泡里长出 chip；K 组还会**放开拦截**让真服务端把标记写进库（临时库那把 Key 是假的 ⇒ 401，不花 token），刷新后断言标记被画回 chip 而**没有**漏成 `[附件 att-…]` 原文（见 [PITFALLS #88](docs/PITFALLS.md)：服务端为界面发的 `attachment` 事件此前**前端没人接**）。<br>★ **凡"几何交给浏览器算"的样式（`conic-gradient` / `mask` / `clip-path`），判据必须落在像素上**：H4–H6 把那个 14px 的圆环按 4× 截图 → 自写 PNG 解码 → 逐角度判色（见 [PITFALLS #87](docs/PITFALLS.md)：修前 10 个角度全判成背景色，而当时那条"看 `backgroundImage` 里有没有 `conic-gradient`"的断言**是绿的**）。改了圆环尺寸/轨道色/填充色，`TRACK_RGB` / `TONE_WARN_RGB` 两个常量要跟着改。<br>★ 改了**用量行的数据口径**（哪一段是会话累计、哪一段是本轮真值）还要跑 `pytest tests/test_agent_loop.py -k context -o addopts=""`——键名对不上时前端**静默**显示占位，不报错 |
| ★ 给 `services/parsing.py` **加一个新的文件类型** | 三处一起改：`SUPPORTED` 白名单 + 对应的 `_parse_*`（**必须给 locator 记 `offset`/`chars`**，否则"第 N 页"不可复算）+ `tests/test_attachments.py` 的 C 组加一条"抽到正文 **且** 坐标切得回原文" |
| `workbench/sandbox/**` | `pytest tests/test_sandbox.py -o addopts=""`（含 slow 层：真起进程、验 Job Object 树杀） |
| `workbench/skills/**` | `pytest tests/test_skills.py tests/test_skill_scripts.py -o addopts=""` |
| `workbench/harness/**`、`cache.py`、`plugin_api.py`、`approvals.py` | `pytest tests/test_cache.py tests/test_agent_loop.py tests/test_agent_approval.py tests/test_user_confirmation.py`（**前缀稳定性 + 确认门两条通道 + 模型发起的实时确认**都在这里）<br>★ 动了实时确认（`needs_confirmation` / `confirm_required` / `pending_action`）**或风险门的等待语义**（`approval_required` / `approval_wait_s`）还要跑 `python tools\verify_confirmation.py`——**40 项**端到端（2026-09-22 实测 40/40）：真起生产入口、真发 HTTP、真拉三个静态文件（"打包/静态目录漏收"只在这里现形）<br>★ **风险门现在会就地等答复**（决策卡 G2）：跑任何"驱动对话循环"的用例时，`AgentConfig.approval_wait_s` 必须调小——真值 300 秒，忘了它就是"用例挂住"而不是"断言变红"（`test_user_confirmation.py` 把它默认成 0.05s 就是这个原因） |
| `workbench/logging_setup.py`、`api/routes_logs.py`、任何 `logger.*` | `pytest tests/test_logging.py`（落盘/幂等/脱敏/关联 ID/滚动/检索/失败隔离）<br>★ 动了日志系统还要跑 `python tools\verify_logging.py`——它**真起服务、真发请求、真读日志文件**，**36 项**端到端检查（含**两条启动路径**：生产入口与开发态裸 uvicorn）。组件测试证明不了链路，PITFALLS #53 就是这么栽的 |
| `config.SENSITIVE_PATH_PARTS`、`plugins/builtins.py`、`sandbox/policy.py` | `pytest tests/test_sensitive_paths.py`（**唯一事实源 + 只读工具的边界**） |
| `workbench/lifecycle.py`、`api/__init__.py` 的 lifespan、`desktop/main.js`（stderr/stdio/菜单/链接） | `pytest tests/test_lifecycle.py -o addopts=""`（**含 slow 层**：真起父子进程验"父死子退"）+ `node --check desktop\main.js`。<br>★ 页面里的链接（`target="_blank"` / `window.open`）在壳里**不许再开一个裸 Electron 窗口**，一律交给系统浏览器：`Select-String -Path desktop\main.js -Pattern 'setWindowOpenHandler'` —— 零命中就说明"点链接/点开附件原文件"没有实现（本应用里这类链接不止一处：回答里的 Markdown 链接、产物预览、消息气泡里的附件 chip） |
| `factory-tuning/**`（**不属于产品，且已只剩 `reports/`**） | **没有门禁**——它的工具链（`tools/`、`analyze/`、`tests/selfcheck.py`、`data/`）随配置工厂一并移除，产品也从不 import 它。**唯一纪律**：往 `reports/` 里放新报告前，先读 [factory-tuning/README.md](factory-tuning/README.md) §四 的**脱敏口径**——那份报告是要提交的，而平台流量里带令牌 |
| ★ 动了上面任一处 | 还要跑 `python tools\verify_regression_tests.py`——它**反证**那 **14 条**断言真的会红（手动诊断用，不进 CI，会临时改写源码并在结束时字节级还原；含一条 29 秒的 slow 项）。**它按单条 120 秒超时收口**：缺陷版的失败形态可能是"挂住"而不是"断言红"，没有这个超时，一次失误会让上层把脚本杀掉，`finally` 的还原**跑不到**——2026-09-20 实测留下过一份缺陷代码<br>★ 2026-09-22 配置工厂移除时**删了 3 条、补了 1 条**：删的是靶子已随功能消失的三条（预览暂存 / 规格注解 / 危险摘要讲蓝图），补的是把「确认摘要」那条**改挂到合成 spec 的用例**上——**机制还在的断言不该跟着实现一起丢** |
| `workbench/api/extension_auth.py` 或 `/extension/**` 蓝图 | `node tools\verify_extension_sync.mjs`（该蓝图**无 pytest 覆盖**） |
| `extensions/quick-login/**`（TS） | `npm run typecheck` + `npm run build`，再跑 `verify_extension_boot.py` + `verify_extension_isolation.py` + `node tools\verify_host_logic.mjs` |
| `tools/build.ps1` | **立刻补 UTF-8 BOM**，再跑 `verify_packaging.py`（P5 就是为这件事存在的，PITFALLS #37） |
| 端口号 / `desktop/ports.js` / `workbench/config.py` | `node tools\verify_port_broker.mjs`（**先退出工作台**）+ `pytest tests/test_ports_contract.py` |
| 任何 `docs/` 里的数字（N 个工具/插件） | `tools\gen_tools_doc.py`（它扫 README + TOOLS + PLUGIN-SDK + API + CODEBASE_OVERVIEW） |
| **任何代码** | 改了就必须重跑 §3 基线四条（PITFALLS #41） |

**加一个工具必须同步 5 处**（漏任何一处 = 装完静默没功能，PITFALLS #32）：

1. `workbench/plugins/<插件>.py` 的 `ToolSpec`（含显式 `risk`）
2. `workbench/plugins/__init__.py::_MODULES`（新插件才要）
3. `workbench/server.spec` 的 `hiddenimports`（新插件才要）——动态导入 PyInstaller 看不见
4. `workbench/api/routes_agent.py::REQUIRED_TOOLS`（关键工具才要）
5. `docs/TOOLS.md` 清单块 —— **跑 `gen_tools_doc.py --write`，不要手写**

---

## 4. 本机环境速查（与常规 Linux 项目不同，别按习惯来）

| 项 | 实际情况 |
|---|---|
| Python | `.\.venv\Scripts\python.exe`（**不在 PATH**；AI 直接敲 `python` 会拿到系统里另一个解释器） |
| **`ruff` / `uv` 在不在 venv 里？** | ⚠️ **这条会漂，别背命令——先探一次**：<br>`Get-ChildItem .venv\Scripts -Filter "uv*.exe","ruff*.exe"`<br>**2026-09-20 实测**：`ruff.exe` **存在**（0.16.0）→ 用 `.\.venv\Scripts\ruff.exe check .`；`uv.exe` **不存在**（只有 `uvicorn.exe`，`python -m uv` 报 `No module named uv`）。若哪天 `uv.exe` 回来，回退是 `.\.venv\Scripts\uv.exe tool run ruff check .`。<br>**2026-09-22 实测（又翻回来了）**：`ruff.exe` **不存在**、`uv.exe` **存在**（还有 `uvw.exe` / `uvx.exe`；`python -m ruff` 报 `No module named ruff`）→ 本次用的是 **`.\.venv\Scripts\uv.exe tool run ruff check .`**，实测 `All checks passed!`（exit 0）。<br>**为什么写这么啰嗦**：这条口径在本仓**已翻转过三次**（见 PITFALLS #50）——它是**环境事实**，而环境会变。**两条都记着**，别只记一条 |
| pytest 汇总行 | `addopts` 已有 `-q -m 'not slow'`；**再加 `-q` 会进 ultra-quiet，连 `=== N passed ===` 都不打印**。要看汇总行用 `-o addopts=""` |
| slow 层 | 默认跳过。全量：`-o addopts=""`；只跑慢层：`-m slow`（无慢层用例时 pytest 以 **exit 5** 退出，这是刻意的守卫，不是故障） |
| 解释器口径 | `.python-version`（= 3.12）是**唯一真源**，`tests/test_python_version_contract.py` 断言它与 CI 逐字一致。开发机是 3.14 —— **两边跳过项不同，"本地全绿"推不出"CI 全绿"** |
| 写文本文件 | 用 Python 或编辑器，**不要用 PowerShell `>`**——它按 UTF-16LE 落盘（PITFALLS #38） |
| 中文乱码 | **先跑判据再动手**：`python -c "import pathlib;t=pathlib.Path('F').read_text(encoding='utf-8');print('U+FFFD=',t.count('\ufffd'))"`。`U+FFFD=0` 就是好的，**不要改文件**（控制台渲染 ≠ 文件内容） |
| GitHub 通道 | **哪个通道通是随时刻变化的**，不是常量。推送前先探测（PITFALLS #9 / #45），别把上次的成功当依据 |

---

## 5. 文档纪律

- **文档按变更频率分层**（`CONTRIBUTING.md` §5）：
  ADR 记**不再变的决策**；`API.md` / `CONFIG.md` / `SCHEMA.md` 记**随代码走的事实**（改必更）；
  `PITFALLS.md` 记**真机环境坑**（只增不改，机构记忆）。
- **`docs/adr/` 只增不改**：改已采纳的决策 = 新增一篇取代它。新增决策请用
  [docs/adr/0000-template.md](docs/adr/0000-template.md) —— 它比历史 ADR 多一栏
  **「外部参照」**（谁解决过同一问题、他们怎么做、我们为何不同），**别省掉那一栏**。
- **补写历史 ADR 时**：只允许追加，且必须像 ADR-0014 那样**显式标注"这段是后补的"**，
  让"决策当时的认知"与"后来的认知"都能看见。
- **`CHANGELOG.md` 顶部的 `## [Unreleased]` 是机器接口**，`tools/publish_release.py` **逐字**认它。
  不要在它下面插中文标题或注释——抽取结果会变回空字符串，而且**静默不报错**。
- **PITFALLS 条目要有判据**："症状 / 根因 / 判据 / 处置 / 推广"五段，
  其中**判据**是可执行的一行命令或一个断言。没有判据的坑记不下来。
- **用"标题"当锚点插入新节时，必须把原标题写回**（2026-09-22 实录：同一次会话里犯了 **3 次**）。
  形态是 **静默** 的——新内容进去了、原标题没了、正文照旧、**没有任何报错**；
  只有"跑一遍标题清单"才看得出来（该文件此后会少一节，而读的人以为它不存在）。
  判据（改完任何文档立刻跑一次）：`Select-String -Path <文件> -Pattern '^#{2,3} '` ——
  **逐行看编号是否连续**（`§0 → §1 → §1.5 → §2 …`）。本次三次都靠这条才发现并修回。

---

## 6. 不要做的事

| 不要 | 为什么 |
|---|---|
| 在 `routes_agent.py` 或 `harness/` 里另建工具表 | 规则 6。这已经发生过一次，代价是模型完全看不到平台能力 |
| 让插件 import `workbench/api/**` | 规则 7。业务能力必须能整体拆除而不碰宿主 |
| 把 `harness/tools.py` 当成工具定义处 | 它**已降级为兼容垫片**，只剩再导出。定义在 `plugins/` |
| 在沙箱里放行 `powershell` / `cmd` | 规则 16 |
| 给"缺失的上游仓库目录"加 `assert exists()` 门禁 | `adapters/*.json` 是**只读存档**，能力已原生化。守"环境"而非守"契约"会让没克隆原仓库的机器变红，而功能完全正常 |
| 用 `dict(os.environ)` 全量继承环境变量给子进程 | 规则 13。凭据就在宿主环境里 |
| 恢复跨进程的待确认操作 | `approvals.py` 启动时把遗留 pending 标 `expired` 是**刻意的**——用户重启了应用，凭什么还替他记住一个未确认的写操作 |
| 用 `"".replace` 生成/替换文档块 | `str.replace("", x)` 会在**每个字符之间**插入 x（实测把 100 行文档炸成 11 万行，见 `gen_tools_doc.splice_block` 的注释） |
| 把"源码看起来对"当成正确性证明 | PITFALLS #37 / #38 / #40：**编码、BOM、换行属于语义**。要验的是字节 |
| 顺手清理 `dist/Workbench/` 之外的历史产物命名 | PITFALLS #25：两代 PyInstaller 产物同级并存时，**目录名分不出谁是新构建** |
| 改 `adapters/*.json` 指向的原四仓库目录 | **冻结**，任何情况下不写 |

---

## 7. 提交信息与留痕

- 提交信息格式：`阶段/模块: 动作`（如 `2B: 自动登录引擎节奏参数对齐上游`）——沿用既有历史。
- **每修一个"看起来正常但其实是坏的"缺陷，就把它的判据写进 [docs/PITFALLS.md](docs/PITFALLS.md)。**
  本仓库的 PITFALLS 是这个项目最有价值的技术资产，因为**它记录的是"看不到的失败路径"**，
  而那些恰恰是测试和类型系统都拦不住的一类。
- 遇到"改了代码界面没变 / 删了功能还在"，**先怀疑跑的是旧产物，不要先怀疑代码**（PITFALLS #20 / #25）。

---

_本文件遵循 [AGENTS.md](https://agents.md) 约定（它是 Linux Foundation 旗下
[Agentic AI Foundation](https://aaif.io/) 的创始项目之一，与 MCP 同属一套）。
约定很简单：**仓库根目录的 `AGENTS.md` 就是 AI 协作者的规则入口**——短、规则形状、可执行。
如果你发现自己需要在这里加"背景介绍"，那说明它该去 `CONTRIBUTING.md` 或 ADR。_
