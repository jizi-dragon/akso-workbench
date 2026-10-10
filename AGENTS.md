# AGENTS.md —— 给在这个仓库里干活的 AI 协作者

> **这份文件是写给 AI 编码助手的，不是写给人看的。**
>
> 人能容忍模糊的说明——他会问、会试、会从上下文推断。AI 不会：它会把"没写下来的约定"
> 当成"不存在这个约定"，然后用**看起来完全正常**的代码违反它。
>
> 所以下面每一条都是**规则形状**的（祈使句 + 判据），且**每条都注明了它来自哪次真实事故**。
> 没有事故背书的偏好不写在这里——`CONTRIBUTING.md` 才是人的入口。
>
> **本文件只放「祈使句 + 判据 + 出处指针」，不复述事故经过。** 完整故事在
> [docs/PITFALLS.md](docs/PITFALLS.md)（每条都指到编号）。这是**把本文件自己的规则 28
> 用在自己身上**："两处都写全量 = 第二个会漂的事实源"——叙事抄一遍，两处迟早不一致，
> 而读的人**不知道哪份是真的**。所以看到"事故见 PITFALLS #NN"就去读那一条，
> 别在这里找细节。
>
> **规则编号是稳定 ID，不是排序号，也永不重编**：全仓有 **38 处**按编号引用它们
> （`CHANGELOG.md` / `PITFALLS.md` / 源码注释 / ADR）。分节按**主题**，所以**同一节里的
> 编号可能不连续**；但**整份文件的阅读顺序必须单调递增**——这条有机器守卫，
> 见 `pytest tests/test_agents_md.py -o addopts=""`。
>
> | 想了解 | 去哪 |
> |---|---|
> | ★ **核心 Agent 架构与分层契约 ＋ 不变量 I1–I18（地基）** | [docs/CORE-ARCHITECTURE.md](docs/CORE-ARCHITECTURE.md) |
> | 真机环境坑（**120 条**，只增不改） | [docs/PITFALLS.md](docs/PITFALLS.md) |
> | 不再变的决策（**8 篇** ADR ＋ `0000-template.md` 模板 = 9 个文件） | [docs/adr/](docs/adr/) |
> | 有哪些工具、怎么加一个 | [docs/TOOLS.md](docs/TOOLS.md) |
> | 数据库与迁移账（改必更的事实源） | [docs/SCHEMA.md](docs/SCHEMA.md) |
> | HTTP 端点契约 / 配置项 | [docs/API.md](docs/API.md) · [docs/CONFIG.md](docs/CONFIG.md) |
> | 版本与发版机制 | [docs/VERSIONING.md](docs/VERSIONING.md) |
> | 用户手册（给使用者的） | [docs/USER-MANUAL.md](docs/USER-MANUAL.md) |
> | 平台取证的方法论与脱敏口径（**报告已删，只剩口径**） | [factory-tuning/README.md](factory-tuning/README.md) |
> | 人的协作规范 | [CONTRIBUTING.md](CONTRIBUTING.md) |
>
> **2026-09-29 文档清理**：上表中原先指向 `PLATFORM-OPERATIONS-LEARNED` /
> `FACTORY-PLATFORM-COGNITION` / `AGENT-FILE-INPUT-PLAN` 的三行**已删除**（用户下达的文档瘦身）。
> 那三份的内容没有搬走 —— 要看它们只能 `git show <删除前提交>:docs/<文件名>`。
> ★ **`CODEBASE_OVERVIEW` 那一行已于 2026-10-07 恢复**：该文件曾于 2026-09-29 随上述清理
> 一并删除，**2026-10-07 重新生成**（`docs/CODEBASE_OVERVIEW.md`，497 行，锚定 `main@ce3bf3d`）。
> 它不在上表里（上表只放"入口级"指针），但**它确实存在** —— 仓库里任何写「该文件已于
> 2026-09-29 删除」的地方都已同步为"曾删除、已重新生成"。
> ★ **`docs/CORE-ARCHITECTURE.md` 已上移到第一行**：它是 §5 不变量的载体，
> 而 `tests/test_invariant_coverage.py` **按这个路径读它** —— 删了它，18 条不变量的覆盖率对账当场失效。

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
| 1 | **已发出的 messages 前缀永不改写。** 上下文只能 `append`。不要"每轮按最近 N 轮裁剪"——那样前缀每轮滑动，DeepSeek 前缀缓存命中率**恒为 0**，成本 ×50（`cache.py:191` 有明确警告） |
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

### 沙箱与技能

| # | 规则 |
|---|---|
| 13 | **沙箱 Fail-Closed**：未知策略名、`cwd` 越界、命中敏感路径、解释器不在白名单 —— **一律拒**，不回落到默认策略 |
| 14 | **审计写在唯一扼流点**（`sandbox.run()`，`_run()` 只被它调用）。不要在调用方补审计——PITFALLS #42：`audit.record` 一度**没有任何调用方**，E4 层在文档上写着"已实现"而实际永远空转 |
| 15 | **脚本式 Skill 的 `risk` 不得低于 `write`**。上传的代码无法被静态证明只读，标 `read` 等于给模型一张"不经确认随便跑"的通行证 |
| 16 | **PowerShell 与 `cmd` 刻意不在沙箱解释器白名单内**。加进去等于把"执行脚本"变成"执行任意命令" |
| 17 | **沙箱不是安全边界，是限损层**（[ADR-0014](docs/adr/0007-credentials-sandbox-and-trust.md) §D-3）。不要在任何地方把它描述成"安全"，也不要把 `allow_network` 当成强制——它是声明 |

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
| 32 | **界面文案是承诺**：菜单项 / 提示条 / 错误页指引里描述的每个行为，都必须能在代码里找到实现。**改行为与改文案必须一起做**（事故见 PITFALLS #55：「关闭窗口（应用继续在托盘运行）」而实际关窗即退出，托盘「显示主窗」因此永远点不出窗口）。判据：`Select-String -Path desktop/main.js -Pattern "on\('close'"` —— 零命中就说明"关窗不退出"没有实现 |

### 循环、确认门与工具完整性

| # | 规则 |
|---|---|
| 33 | **模型发起的确认（`needs_confirmation`）不是安全边界**：它买的是"模型可以先问再做"，`Risk` 买的是"不做就不能做"。**不要**把它写成"高风险操作的批准环节"，**不要**因为它存在而放宽任何 `Risk` 分级（[ADR-0016](docs/adr/0007-credentials-sandbox-and-trust.md) §5）。判据：`grep -rn "needs_confirmation=True" workbench/plugins/` 里每一个的 `risk` 都必须是 `READ`（`ToolSpec.valid()` 在装配期强制） |
| 34 | **不要在工具 handler 里等用户。** `execute()` 一旦 `await`，调用它的异步生成器就挂起了，事件发不出去 → 界面不知道有东西要确认 → 用户永远点不了。跨"通知 → 等答复 → 继续"的交互**必须把等待放在循环层**（`harness/loop.py` 发完 `confirm_required` 再 `await registry.await_confirmation`）。这条不是偏好，是架构约束（[ADR-0016](docs/adr/0007-credentials-sandbox-and-trust.md) §D-2） |
| 35 | **"发通知 → 等对方回 → 我再登记等待"的形状，接收端必须提前建好。** 对方的响应会比你开始等更早到达，晚建的 `Future` 接不住它 → 表现为"对方明明答复了，我方干等满一个超时"（事故见 PITFALLS #57：实时确认卡了整整一个 `timeoutS`）。判据：`pytest tests/test_user_confirmation.py -o addopts=""`——它**在 `confirm_required` 事件的回调里**就答复，命中这个时序；挂起不返回 = 本条复发 |
| 36 | **给 Agent 接一条既有能力链时，先问"这条路上有哪几步只有人能做的"，把答案逐个变成工具。** 缺一个动词**不会报错、不会让测试变红**——它只让流程走不通，而走不通的表现是**模型开始输出"请你去做某事"**（事故见 PITFALLS #59）。判据：`Get-ChildItem -Recurse workbench -Include *.py,*.js \| Select-String '拖进页面\|页面上传\|让用户上传'` —— **必须零命中** |
| 37 | **"输出太长被截断"必须与"参数写错了"分开。** `finish_reason == "length"` 是唯一的信号，丢了它模型只会看到「参数解析失败」，于是原样重试、又截断一次（事故见 PITFALLS #58）。收流式响应时**必须收 `finish_reason`**；截断时**绝不让半截参数进工具层**，并明说"没有执行任何工具"。判据：`pytest tests/test_agent_loop.py::test_truncated_output_is_not_reported_as_bad_args -o addopts=""` |

### 交付纪律

| # | 规则 |
|---|---|
| 38 | **交付给用户实测之前，最后一步必须是「杀干净 + 重启」，不是「跑完测试」。** 违反它的后果**不是报错，是归因错误**——而错误归因会烧掉整轮排查（两次实录见 PITFALLS #71）。<br>**判据（交付前跑，三条都要过）**：① `Get-NetTCPConnection -LocalPort 18765,18766,18767 -State Listen` —— **必须为空，或全部属于同一个刚启动的进程**；② 改过 `workbench/**` 或 `desktop/**`（Python / 壳）**必须重启应用**（只刷新页面对 `static/**` 才够）；③ **跑测试与用户实测分成两段**，跑完测试要重启一次再交付——别让测试的副作用留在现场。<br>**为什么单列一条**：固定端口（[ADR-0012](docs/adr/0011-port-governance.md)）让"旧进程还活着"和"测试戳到了真端口"这两件事**都不报错**，只让现象指向错误的嫌疑人 |
| 39 | **每个工具都要声明 `trust`（`LOCAL` / `UNTRUSTED`），而且它没有缺省值。** 问的是「这个工具的**返回值是谁写的**」：外部抓来的（网页 / 搜索 / 上传的脚本产物）标 `UNTRUSTED`，自己边界内的（平台 API / 本机文件）标 `LOCAL`。未声明 ⇒ `ToolSpec.valid()` 在**装配期**报错（[ADR-0022](docs/adr/0007-credentials-sandbox-and-trust.md)）。<br>★ **为什么比 `risk` 严**：`risk` 的缺省 `READ` 落到**最安全的那一档**（不过确认门），而 `trust` **没有安全的缺省** —— 缺省成 `LOCAL` 等于把外部内容**静默**声明为自己边界内的数据，**不报错**。**同一类字段，缺省值的后果可以完全相反**，所以"照抄隔壁字段的默认值"是一个会静默出错的直觉（PITFALLS #105）。<br>★ **系统提示词里写"工具结果比用户的话可信"是劝告，贴在内容上的标签才是结构** —— 前者与外部内容在上下文里**并列**，后者与它**同生共死**。<br>判据：`pytest tests/test_trust_labels.py -o addopts=""`（**12 条**）。其中 `test_the_real_web_tools_are_declared_untrusted` 是**对着手写清单**核的 —— 把 `read_web_page` 改成 `LOCAL`，I18 当场失效而**别的测试一条都不会红** |
| 40 | **`parallel_safe` 要逐工具举证，缺省 `False`。**标它之前必须回答一句 **「这个工具并发跑两个，会不会碰同一份可变状态？」** —— ★ **"只读"不足以支撑并发**：HTTP 会话（`requests.Session` 非线程安全）、浏览器那条**单工作线程队列**（并发只排队，不加速）、sqlite 连接（跨线程直接抛）都是"只读但会碰"的例子，而其中两类**不报错**。今天全仓只有 `get_current_time` 标了它（纯计算）；其余 17 个保持缺省不是"判定安全"，而是**还没核实**（[ADR-0023](docs/adr/0019-in-turn-context-and-budget-governance.md)）。<br>★ **为什么口径比计划原文窄**：§7.4 原文是「整批都是 `Risk.READ` 就并发」，按它实现会让**计划自己 §5.6 的两条停止判据 + 一条评测用例变红** —— 因为并发必然把停止的检查点从"每次调用之间"退到"每批之间"。**新功能让已批准的判据变红时，默认假设是新功能错了，不是判据该改**（PITFALLS #107）。<br>★ **预检必须是纯的**：`SafetyGate.is_loop` / `_stopped(should_stop)` **都不能**拿来做资格判定 —— 一个改滑窗、一个消费检查点（后者一度让停止测试报出"该只执行第 0 个，实际 `[]`"）。用只读探针 `would_loop`。<br>判据：`pytest tests/test_agent_parallel.py -o addopts=""`（**14 条**），其中 `test_a_tool_that_did_not_opt_in_stays_serial` 与 `test_real_tools_are_not_opted_in_by_default` 是守门人 —— 它们红了**不要改它们**，回去读 ADR-0023 §1 |
| 41 | **会改状态的工具必须声明 `reversibility`，而且它没有缺省值。** 问的是 **「这一次写做错了能不能撤」**：能撤就 `UNDOABLE` ＋给一条**具体指令**（`undo_recipe`，落 `agent_audit.meta`）；撤不了就明说 `IRREVERSIBLE`；确实不留残留才 `REVERSIBLE`（[ADR 见 §8.3 与 `ToolSpec.reversibility`](workbench/plugin_api.py)）。<br>★ **不是只对 `DANGEROUS` 强制**：计划 §8.3 原文说"接第一个不可逆写工具时"才要，而**全仓今天 0 个 DANGEROUS** ⇒ 那样这个字段**没有调用方**，而零调用方的扩展点是本仓明确要防的东西（见 `summary_fn` 的文档串）。<br>★ **幂等不是回滚**：幂等层防的是"重复执行同一次调用"，防不了"这一次本来就做错了" —— 所以"再跑一遍就好了"**不是** `REVERSIBLE` 的理由。<br>★ **第三方来源只能声明最保守的那一档**：脚本式 Skill 一律 `IRREVERSIBLE`（上传的代码无法被静态证明可逆，与"它的 risk 不得低于 write"同一条推理）；转发式 Skill **继承**被包装工具的档位与配方（不继承的话，一个包装写工具的 Skill 会**静默地**把不可逆操作说成"没有残留"）。<br>判据：`pytest tests/test_undo_recipe.py -o addopts=""`（**8 条**）。★ 其中 `test_the_recipe_ends_up_in_the_audit_row` 是**端到端**的（真调一次写工具、真查审计行）—— 其余七条当时全绿而它红，因为"函数算对了"与"真的落库了"是两件事（PITFALLS #110） |

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
#   ★ 2026-10-11：执行层已是 pwsh 7.6.6（`Console.OutputEncoding` = utf-8）。
#     下面若有 `python -c` 且要打印非 ASCII，**加 `-X utf8`**（或设 `PYTHONUTF8=1`）——
#     否则报的是 **Python 自己的 GBK stdout 错**（`'gbk' codec can't encode character`），
#     与 shell 无关。见 §4「中文乱码」与 PITFALLS #38 的复核注。
.\.venv\Scripts\python.exe -m pytest -o addopts=""        # 全量；默认 addopts 跳过 slow 层
.\.venv\Scripts\uv.exe tool run ruff check .              # ★ lint —— 先探一次，见 §4（这条会漂；2026-09-24 实测走这条）
.\.venv\Scripts\python.exe tools\gen_tools_doc.py         # 工具清单/数字 == 运行期中枢
.\.venv\Scripts\python.exe tools\verify_packaging.py      # 打包静态核对 9 项
```

| 你改了什么 | 必须额外跑 |
|---|---|
| `workbench/plugins/**` | `tools\gen_tools_doc.py --write`（重生成 [docs/TOOLS.md](docs/TOOLS.md) 清单块）**+** 确认 `server.spec` 的 `hiddenimports` 有该模块 **+** `REQUIRED_TOOLS`。<br>★ **新增/删除插件时三处必须同时改**，否则 `load_all` 会把 ImportError **吞成一条 failure**（它的隔离设计），表现为 `selfcheck` 静默变红而启动照常——2026-09-22 删配置工厂时正是这三处（`_MODULES`、`server.spec`、`REQUIRED_TOOLS`）一处都不能漏 |
| **附件链路**：`services/attachments.py`、`services/parsing.py`、`api/routes_attachments.py`、`api/routes_chat.py` 的扼流点与**写给模型的附件提示**、`plugins/builtins.py` 的 `attachment_*` | `pytest tests/test_attachments.py -o addopts=""`（**26 条**：落盘/拒收/解析坐标/展开成 parts/按坐标取/会话隔离/**提示措辞**）**+** `python tools\verify_attachments.py`（**24 项**端到端：真起生产入口、真发 multipart、**字节级**读回、三条拒收、扼流点展开、删除是真删）**+** `python tools\verify_attachments_live.py`（**真机联调 · 要花 token**：颜色 / 形状 / 表格 / **不撤回自己** / 负样本，五问全绿才算过）<br>★ **这条链路上"函数对"与"链路通"是两件事**：2026-09-22 真机联调抓到两个单测看不见的缺陷（`_pending` 未绑定 / **这一轮的消息绕过扼流点**）——见 `docs/AGENT-FILE-INPUT-PLAN.md`（**该文件已于 2026-09-29 删除**） §6.1。**改这条链路后必须另跑一次真机联调**。<br>★ **写给模型的那句话也是链路的一部分**（[PITFALLS #89](docs/PITFALLS.md)）：旧轮图片的引用话术一处措辞不当，模型就会**撤回自己刚答对的内容**（"前面是瞎猜的"）；内联提示里提起"能不能读"会让它**拒绝看图**。改任何 `[附件 …]` 提示文案后，措辞判据在 `tests/test_attachments.py`，端到端那条是 `verify_attachments_live.py` 的 2b |
| **输入区 / 附件 UI**：`static/index.html` 的输入框那一段、`static/js/chat.js` 的用量行与消息里的附件、`static/js/attachments.js`、`static/css/app.css` 的 `.usage-strip` / `.model-chip` / `.usage-ring` / `.msg-attachments` | `python tools\verify_composer_ui.py`（**32 项**：真起生产入口 + 真开 chromium，点会话、按发送，然后读**渲染后**的几何与计算样式）。<br>★ 为什么字符串断言不够（`verify_attachments.py` 的 A1–A9 只证明"源码里有这些字样"）：它抓不到这一整类**看起来完全正常**的失败——那一行还是 `hidden` / 被父元素裁掉、字号被别处一条更具体的规则压回 11px（用户上一轮的原话是「比蚂蚁还小的字」）、模型名换行掉到发送键**下面**、事件里少了 `contextTokens` 于是永远显示「上下文 —」。<br>★ **J/K 两组钉的是"三条输入入口真的能用"与"界面真的接住了服务端的事件"**：真点按钮（`expect_file_chooser`）、真派发带 `File` 的 paste/drop、发送后读**拦截下来的请求体**看 `attachmentIds`，再断言消息气泡里长出 chip；K 组还会**放开拦截**让真服务端把标记写进库（临时库那把 Key 是假的 ⇒ 401，不花 token），刷新后断言标记被画回 chip 而**没有**漏成 `[附件 att-…]` 原文（见 [PITFALLS #88](docs/PITFALLS.md)：服务端为界面发的 `attachment` 事件此前**前端没人接**）。<br>★ **凡"几何交给浏览器算"的样式（`conic-gradient` / `mask` / `clip-path`），判据必须落在像素上**：H4–H6 把那个 14px 的圆环按 4× 截图 → 自写 PNG 解码 → 逐角度判色（见 [PITFALLS #87](docs/PITFALLS.md)：修前 10 个角度全判成背景色，而当时那条"看 `backgroundImage` 里有没有 `conic-gradient`"的断言**是绿的**）。改了圆环尺寸/轨道色/填充色，`TRACK_RGB` / `TONE_WARN_RGB` 两个常量要跟着改。<br>★ 改了**用量行的数据口径**（哪一段是会话累计、哪一段是本轮真值）还要跑 `pytest tests/test_agent_loop.py -k context -o addopts=""`——键名对不上时前端**静默**显示占位，不报错 |
| ★ 给 `services/parsing.py` **加一个新的文件类型** | 三处一起改：`SUPPORTED` 白名单 + 对应的 `_parse_*`（**必须给 locator 记 `offset`/`chars`**，否则"第 N 页"不可复算）+ `tests/test_attachments.py` 的 C 组加一条"抽到正文 **且** 坐标切得回原文" |
| `workbench/sandbox/**` | `pytest tests/test_sandbox.py -o addopts=""`（含 slow 层：真起进程、验 Job Object 树杀） |
| `workbench/skills/**` | `pytest tests/test_skills.py tests/test_skill_scripts.py -o addopts=""` |
| `workbench/harness/**`、`cache.py`、`plugin_api.py`、`approvals.py` | `pytest tests/test_cache.py tests/test_agent_loop.py tests/test_agent_approval.py tests/test_user_confirmation.py`（**前缀稳定性 + 确认门两条通道 + 模型发起的实时确认**都在这里）<br>★ 动了**取消/停止**（`harness/stop.py` / `should_stop` / `cancelled` 事件 / `POST /api/chat/stop`）还要跑 `pytest tests/test_agent_stop.py -o addopts=""`（**20 条**，计划 §5.6 的四条判据：未执行调用被回填 / assistant 消息落库 / pending 变 `expired` / 事件流以 `cancelled` 结尾）。<br>★ **写这类反证时注意**：测试里"等对方答复"的**每一个超时都要显式给小值**（`needs_confirmation` 不写 `confirm_payload.timeoutS` 就走 `DEFAULT_CONFIRM_TIMEOUT_S = 300.0`），并且**还原动作必须放 `finally`** —— 排在可能挂住的命令之后，一次挂住就会把缺陷版留在工作区（[PITFALLS #101](docs/PITFALLS.md)，2026-09-24 实测）<br>★ 动了实时确认（`needs_confirmation` / `confirm_required` / `pending_action`）**或风险门的等待语义**（`approval_required` / `approval_wait_s`）还要跑 `python tools\verify_confirmation.py`——**40 项**端到端（2026-09-22 实测 40/40）：真起生产入口、真发 HTTP、真拉三个静态文件（"打包/静态目录漏收"只在这里现形）<br>★ **风险门现在会就地等答复**（决策卡 G2）：跑任何"驱动对话循环"的用例时，`AgentConfig.approval_wait_s` 必须调小——真值 300 秒，忘了它就是"用例挂住"而不是"断言变红"（`test_user_confirmation.py` 把它默认成 0.05s 就是这个原因） |
| `workbench/logging_setup.py`、`api/routes_logs.py`、任何 `logger.*` | `pytest tests/test_logging.py`（落盘/幂等/脱敏/关联 ID/滚动/检索/失败隔离）<br>★ 动了日志系统还要跑 `python tools\verify_logging.py`——它**真起服务、真发请求、真读日志文件**，**36 项**端到端检查（含**两条启动路径**：生产入口与开发态裸 uvicorn）。组件测试证明不了链路，PITFALLS #53 就是这么栽的 |
| `config.SENSITIVE_PATH_PARTS`、`plugins/builtins.py`、`sandbox/policy.py` | `pytest tests/test_sensitive_paths.py`（**唯一事实源 + 只读工具的边界**） |
| `workbench/lifecycle.py`、`api/__init__.py` 的 lifespan、`desktop/main.js`（stderr/stdio/菜单/链接） | `pytest tests/test_lifecycle.py -o addopts=""`（**含 slow 层**：真起父子进程验"父死子退"）+ `node --check desktop\main.js`。<br>★ 页面里的链接（`target="_blank"` / `window.open`）在壳里**不许再开一个裸 Electron 窗口**，一律交给系统浏览器：`Select-String -Path desktop\main.js -Pattern 'setWindowOpenHandler'` —— 零命中就说明"点链接/点开附件原文件"没有实现（本应用里这类链接不止一处：回答里的 Markdown 链接、产物预览、消息气泡里的附件 chip） |
| `factory-tuning/**`（**不属于产品；2026-09-29 起只剩一份 `README.md`**） | **没有门禁**——它的工具链（`tools/`、`analyze/`、`tests/selfcheck.py`、`data/`）随配置工厂一并移除，报告（`reports/`）与 `data/` 残留也已于 2026-09-29 删除，产品从不 import 它。**唯一纪律**：[factory-tuning/README.md](factory-tuning/README.md) §四 保留着**脱敏口径**——将来若要重建采集工具链，先读那一节再动手（平台流量里带令牌） |
| ★ 动了上面任一处 | 还要跑 `python tools\verify_regression_tests.py`——它**反证**那 **27 条**断言真的会红（手动诊断用，**不进 CI**，会临时改写源码并在结束时字节级还原；含一条 29 秒的 slow 项）。**它按单条 120 秒超时收口**：缺陷版的失败形态可能是"挂住"而不是"断言红"，没有这个超时，一次失误会让上层把脚本杀掉，`finally` 的还原**跑不到**。2026-09-24 实测：**27/27 有效**、逐字节还原 14 个文件。<br>★★ **但它自己会悄悄烂掉** —— 它不进 CI ＋ 手动跑 ＋ **遇到第一个坏锚点就停**，三条叠起来意味着"14 条里第 1 条坏了"与"14 条全过"的输出几乎一样长（PITFALLS #115：一次**正确的**重构在锚点中间插了两行，它烂了整整一轮没人发现）。⇒ 所以另有一条**可进 CI 的那一半**：`pytest tests\test_regression_harness.py -o addopts=""`（**26 条**，**0.09 秒**，**不改写任何源码**）—— 它验清单非空／目标文件存在／目标用例还在／**锚点仍在源码里找得到**／**锚点在文件里唯一**／**缺陷版替换与锚点真的不同**（一个"替换 == 原文"的用例**永远绿**，而它占着"这里验过了"的位置）。**改了任何被反证锚定的源码，就跑这一条。** |
| `workbench/api/routes_shell.py` | 无专属单测；`pytest tests/test_shell_control.py` 覆盖壳控制服务的跨源闸门（它与本模块的 `/api/shell/*` 配对） |
| `tools/build.ps1` | **立刻补 UTF-8 BOM**，再跑 `verify_packaging.py`（P5 就是为这件事存在的，PITFALLS #37）。★ **它极易在脚本化编辑后丢失**：任何"读进来 → 改 → 写回去"的批处理都会顺手把 BOM 吃掉（2026-10-09 实测踩到一次，P5 当场报红——这正是它存在的理由） |
| 端口号 / `desktop/ports.js` / `workbench/config.py` | `node tools\verify_port_broker.mjs`（**先退出工作台**）+ `pytest tests/test_ports_contract.py` |
| 任何 `docs/` 里的数字（N 个工具/插件） | `tools\gen_tools_doc.py`（它扫 `README.md` + `docs/TOOLS.md` + `docs/API.md`；★ `docs/PLUGIN-SDK.md` **已于 2026-09-29 删除**，`docs/CODEBASE_OVERVIEW.md` **曾于同批删除、2026-10-07 重新生成**，两者都不在 `CLAIM_DOCS` 里） |
| ★ **运行时间线 / 可观测性面**（`api/routes_agent.py` 的 `/runs`、`harness/trace.py`、`static/js/run-timeline.js`、`static/css/app.css` 的 `.rt-*`） | `pytest tests\test_agent_timeline.py -o addopts=""`（**5 条**：空结果是 200 不是 404 / run 与 span 一次返回 / 按 `step` 升序 / 会话过滤真的生效 / **摘要 span 自己声明 `attrs.summary`**）**+** `python tools\verify_agent_ui.py`（**19 项**端到端）。<br>★ **为什么另要一条端到端**：`verify_agent_ui.py` 读的是**计算样式**（`getComputedStyle`）而不是类名 —— 我写那个面板时凭印象编了 `badge-ok` / `badge-danger` 这些**不存在的类名**（真实的是 `badge-lime` / `badge-red`），后果是徽章退化成**灰底灰字**，而**界面上看不出哪里错了**：它只是"颜色不太对"，不是"少了一个元素"。同 PITFALLS #87 的教训：**类名对不对推不出渲染出来是什么样**。<br>★ **摘要那次调用必须标红且解释**：它几乎必然全 miss（摘要读完整段历史，前缀命中概率最低），而 miss 的价是命中的 **50 倍**。不标出来的话，用户看到"这一轮贵"只会去怪工具调用 |
| ★ **任何 `docs/**` 或根目录的 markdown**（改链接 / 挪文件 / 提 ADR 编号 / 加存档目录） | `pytest tests\test_doc_links.py -o addopts=""`（**6 条**：断链 / 幻影 ADR 引用 / 幻影坑号 / **代码块里的链接（疑似正文被吞）** / 排除项必须真实存在且有理由 / **N 个反引号的代码段要剥干净**）。<br>★ **三类缺陷的处置口径不同，别一律改链接**：① 文件还在、只是改了名 ⇒ **改链接**；② 引用的 ADR **已退役** ⇒ 在那一行（或紧随两行）**说明它已移除**并给 `git show` 取回命令（★ 别把编号删了了事 —— 读的人需要知道"当时有一条 ADR 支撑这个判断"）；③ 那是**带日期的取证报告 / vendor 逐字存档** ⇒ 加到 `tools/check_doc_links.py` 的 `ARCHIVES` 并**写明理由** —— ★ **改存档里的链接是改写历史**（与"ADR 只增不改""CHANGELOG 是历史"同一条纪律）。<br>★ **一个整天误报的检查会把真缺陷埋掉**（实测：第一版报 789 处，其中 700+ 是假的，真正的 3 处幻影引用就埋在噪音里）—— 所以这个脚本的**作用域**（`ARCHIVES` 排除清单、剥代码块、URL 解码）是它的核心设计，不是可选项；见 PITFALLS #108 / #109 |
| ★ 改了 `harness/**`、`prompts.py`、`cache.py`、`plugin_api.py` **或任何工具描述** | `python tools\run_evals.py --offline` + `pytest tests\eval -o addopts=""`（**12 条**离线评测，不联网不花 token）。<br>★ **这是计划 §6.3 的门禁（G3 的落地形式）：提交里必须附 before/after 数**（成功率 / 工具选择准确率 / 平均步数 / token / 缓存命中率）。<br>★ **看到「回放 miss」不要先怀疑行为**：那是**请求形状变了**（提示词 / 工具描述 / 消息装配），不是功能坏了。处置：`python tools\run_evals.py --record` 重录，然后 `git diff tests/eval/fixtures/` —— **那个 diff 就是"工具面变了多少"的量化答案**。<br>★ 夹具按**请求指纹**匹配（含 `messages + tools + temperature + max_tokens + json_mode`），这条门禁的全部价值就在于此：**改了提示词或工具描述会变成一次可见的失败**。<br>★ 夹具当前是**合成**的（`note` 字段逐份标注）：它锁的是"请求形状 + 链路行为"，**不是**"模型选得对不对"。换真机 `--record` 即可，**期望一行都不用改** |
| ★ **想量"模型选得对不对"**（计划 §6.3 的在线层） | `python tools\run_evals.py --live`（**只预览**：算出要发几次请求，**一次都不发**）→ `--live --yes`（真跑，**花真钱**）。<br>★ **两道闸门都是承重的**：① `--live` 会**覆盖受版本控制的 `tests/eval/fixtures/*.json`** 且每次请求都花钱 —— 不带 `--yes` 时一次都不发，并在最后明说"这只是预览"（本仓在固定端口上留过教训：**"预览"与"真跑"都不报错时，人分不清自己在哪一个里**，[ADR-0012](docs/adr/0011-port-governance.md)）；② **没有 Key 时抛错，绝不回落脚本** —— 回落是最坏的"友好"（以为在量真模型，量到的还是脚本），与 `ReplayLLM(strict=False)` 那个 180 秒挂起同族。<br>★ 估算按**现有夹具里的调用数**算（不拍数字）；`note` 如实区分"合成"与"真机"，**反过来会让人把"链路走对了"读成"模型选对了"**。<br>★ **它不进 CI、不进本矩阵**（要联网、花钱、依赖模型当天状态）。判据：`pytest tests\test_eval_live_mode.py -o addopts=""`（**8 条**） |
| ★ **message 落库 / 对话历史装配**（`services/storage.py` 的 `insert_message` / `list_messages` / `model_transcript`、`harness/transcript.py`、`harness/loop.py` 的落点） | `pytest tests\test_transcript_roundtrip.py -o addopts=""`（**7 条**：落库那一段与**当时发出去的**逐字段相等（串行 + **并发快路径**各一条）/ `tool_call_id` 与 `tool_calls[].id` 配对 / `record` **拒绝**不带 `tool_calls` 的 assistant / `seq` 会话内单调 / **排序键是 `seq` 不是毫秒时间戳**）。<br>★ **两条口径**：① 落库的必须是**当时逐字节发出去的那一条**（不是重新构造的近似）—— 落点就是 `context.append(...)` 那处的**同一个对象**（`_tool_message` 是单一构造点），所以「落库那份和发出去那份长得像但不是同一个」在这里没有余地；② **一个回合里只有最终那条 assistant 由 `routes_chat` 落**（带正确的 `kind`），`transcript.record` **只落带 `tool_calls` 的** —— 写重会让模型看到自己说了两遍。<br>★ **排序键必须是 `seq`**：`created_at` 是**毫秒**，一个回合里 `assistant` 与它的 `tool` 结果**背靠背**写入，并列是常态；没有决胜列的 `ORDER BY` 顺序在 SQL 语义上**未定义**，而顺序错了会让整段历史**非法**（I16）。<br>★ **反证是这条纪律的一部分**（PITFALLS #112）：本轮 5 条反证里**一开始有 3 条没红** —— 要么锚点被当子串匹配、要么改动没真的拆掉那个环节。**写完断言先问「拆掉什么它才会红」**，答不上来就说明它没在验任何东西 |
| ★ **上下文估算 / 折叠判定**（`harness/context.py` 的 `estimate_*`、`cache._over_budget` / `plan_prefix`、`loop._build_context` / `_over_hard_limit`） | `pytest tests\test_context_tokens.py -o addopts=""`（**13 条**）**+** `python tools\compare_token_estimators.py`（**偏差表**，计划 §7.1 的前置件）。<br>★ **两个量必须分开算，因为它们服务方向相反的判定**：`estimate_messages_tokens`（消息，**含** `tool_calls`/`tool_call_id`）与 `estimate_request_tokens`（**再加**工具 schema）。**折叠判定用全量** —— 超窗的是**请求**，而 schema 是每轮都发的常量（实测 18 个工具约 2,104 token，是同一段对话正文的 **11 倍**；不算它，窗口设小就**永远不折叠**，见 PITFALLS #113 的实测）。**硬上限刻意只用消息那一份**（宁可漏判也不要误判）—— ★ 那是**已决策**，改它要**新增 ADR**；有一条判据专门钉住它。<br>★ 偏差表**没有样本时如实说没有**（`estimatedTokens` 才加进 span，样本要靠真实对话积累）—— 不编一个「平均偏差 0%」：那比没有偏差表更坏，**它看起来像验过了** |
| ★ **transcript 的完整性 / 跨重启收尾**（`harness/transcript.py` 的 `dangling_calls` / `repair_dangling`、`harness/loop.py` 运行开头那一次调用） | `pytest tests\test_agent_resume.py -o addopts=""`（**8 条**）。<br>★ **为什么需要它**：`loop._fill_unexecuted` 覆盖的是**进程内**的三个出口（步数预算 / 用户取消 / 超时），而**进程死亡**时那三条**一条都跑不到** ——已写进库的 `assistant(tool_calls)` 永远没有配对结果，而「**重放一段非法历史没有意义**」（I16）。那条缺口**没有代码位置**，所以它永远不会出现在「我数了 N 个调用方」里（PITFALLS #114）。<br>★ **两条不许越过的界线**：① **只补历史，绝不重新执行**那些调用（进程可能死在「已发出、未写回」那个窗口里，我们**无法知道**它执行到哪了 ——一个写操作重放一次的后果与「少一条观测」不是一个量级）；② **一个审批令牌都不碰**（跨重启失效是 `approvals` 的**刻意**行为，有一条判据专门钉住那条界线）。<br>★ 补写的正文必须**明说「未执行」**且**不含「已完成」**字样 ——模型看得见它，写成「已完成」会让它据此下结论，**比失败更坏** |
| ★ **`tools/verify_*.py`（那一排手动跑的端到端校验）** | `pytest tests\test_verify_scripts_are_self_contained.py -o addopts=""`（**5 条**，**0.06 秒**，**可进 CI**）：起服务的脚本必须①**数据隔离**②**端口被占就拒跑**③**端口唯一**。<br>★ **为什么单列一条**：它们是**会跑的程序**（起服务、连数据库、点按钮），不是文档 —— 而 2026-09-24 核出**3 个脚本读写用户真实数据库、4 个绑生产端口且无守卫**，其中 `verify_box_ops.py` 会**删除盒子**且 `dialog` 一律 accept。三条叠起来：应用在跑时 `uvicorn` 起不来（报错进 DEVNULL）、就绪探测却连上**正在运行的应用** ⇒ 整套验收打在真实实例上，**一个错都不报**（PITFALLS #116）。<br>★ **例外要写下来**：`# verify-guard: <规则> —— 理由：…`（**理由非空**）。<br>★ 加/改这些脚本时，跑一次它们自己确认还绿（它们不进 CI，需要 chromium） |
| ★ **产物预览的 markdown 渲染**（`static/js/artifact-preview.js` 的 `renderMarkdown`） | `python tools\verify_insight_preview.py`（**18 项**：真起服务 + 真开 chromium，产物由**产品真跑一遍**回放夹具生成）。<br>★ **判据要断言「渲染出来的 == 源文件里的」，不要断言产品的文案** —— 三条判据曾同时坏在这一点上：`P8` 写死了列名（产品换过说法后静默过期）、`P11` 把子串写成了**列表精确匹配**、`P9` 在**错的产物**上找东西（PITFALLS #117）。<br>★ **空洞断言比没有更坏**：`0 == 0`、`all(... for [])` 在缺陷版上照样绿 —— 真产物今天**一条列表都没有**，所以列表那几条必须落在**探针产物** `markdown-probe.md` 上（`P9a`–`P9d`），且 `P9d` 带 `bool(li_texts) and` 前提。<br>★ 反证：把渲染器的列表分支改成 `if (false)` ⇒ P9a/P9b/P9c 红 |
| ★ **token 估算的校准**（`harness/context.py` 的 `calibration_factor`、`harness/trace.py` 的 `recent_token_pairs`、`cache._over_budget` 的 `factor`、`loop.py` 运行开头那一次） | `pytest tests\test_token_calibration.py -o addopts=""`（**12 条**）。★ 它**取代**了计划 §7.1(b) 的 `tiktoken cl100k_base` 方案 —— 那是 **OpenAI 的分词器**，数 DeepSeek 的 token **是另一种近似**；而 **API 自报的 `prompt_tokens` 是真值**，且与估算值记在**同一条 span** 上（[ADR-0024](docs/adr/0019-in-turn-context-and-budget-governance.md)）。<br>★ **三道闸门都朝向「宁可不校准」**：样本 < 5 ⇒ 1.0（两三个样本算出来的系数**比不校准更坏**，而「我按数据校准过」让人更不敢质疑它）；夹 `[0.5, 2.0]`；丢掉非正配对（`prompt_tokens=0` 是**没有这个数据**，不是「0 个 token」）。<br>★ **用中位数不用平均值**，且**一次运行只算一次**（一轮之内换系数 = 「这一轮按哪个阈值折」说不清）。**系数 ≠ 1 必须留痕**，读样本失败**回落 1.0**（校准是优化，不是前提）。<br>★ 反证：用平均值 / 去掉闸门 / 不夹范围 / 系数没被用上 / 循环不读样本 —— **5/5 有效** |
| ★ **不变量 I1–I18 的守卫**（`CORE-ARCHITECTURE.md` §5） | `pytest tests\test_invariant_i11.py tests\test_invariant_coverage.py -o addopts=""`（**4 ＋ 8 条**）。<br>★ **I11**（同步阻塞一律 `to_thread`）此前**连判据都没有** —— 而它的后果是「**整个服务卡住（不是报错）**」。现在两条规则：**规则 A**（`async def` 里不得直接调已知阻塞名）＋ ★★ **规则 S（结构）**（`async def` 里的**嵌套同步 `def`** 不得被**直接调用**—— 它存在的理由就是被 `to_thread` 挪走）。<br>★★ **名单式判据必须先反证再相信**（PITFALLS #119）：第一版用名字名单，实测 0 处违规，而**把 `to_thread` 拆掉它照样绿** —— 那种判据的「全绿」与「没有违规」**在输出上长得一模一样**。结构规则才抓得到，且**实测 0 处误报**。<br>★ **覆盖率也要机检**：注册表里的每条反证**必须真的存在**；覆盖表 ＋ 缺口表合起来必须正好是 §5 里那些编号；★ `PENDING` **现在是空的**（18 条全部有反证），而它**不允许再变大**。<br>★ 反证：拆掉 `routes_attachments` 的 `to_thread` ⇒ 当场红 |
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
| **`ruff` / `uv` 在不在 venv 里？** | ⚠️ **这条会漂（已翻转过三次，见 PITFALLS #50）——别背命令，先探一次**：<br>`Get-ChildItem .venv\Scripts -Filter "uv*.exe","ruff*.exe"`<br>**2026-09-24 实测**：`uv.exe` **在**（还有 `uvw.exe` / `uvx.exe`）、`ruff.exe` **不在**（`python -m ruff` 报 `No module named ruff`）⇒ 用 **`.\.venv\Scripts\uv.exe tool run ruff check .`**（首次会按需下载 ruff，约 10 MiB）。<br>**若哪天翻回去**（`ruff.exe` 在）⇒ 用 `.\.venv\Scripts\ruff.exe check .`。**两条都记着，别只记一条** |
| pytest 汇总行 | `addopts` 已有 `-q -m 'not slow'`；**再加 `-q` 会进 ultra-quiet，连 `=== N passed ===` 都不打印**。要看汇总行用 `-o addopts=""` |
| slow 层 | 默认跳过。全量：`-o addopts=""`；只跑慢层：`-m slow`（无慢层用例时 pytest 以 **exit 5** 退出，这是刻意的守卫，不是故障） |
| 解释器口径 | `.python-version`（= 3.12）是**唯一真源**，`tests/test_python_version_contract.py` 断言它与 CI 逐字一致。**2026-09-24 实测**：项目 `.venv` 已经是 **uv 管理的 3.12.14**（`pyvenv.cfg` 的 `home` 指向 `…\uv\python\cpython-3.12-…`）—— **不再是** conda 3.14 ⇒ 当年"本机 3.14 vs CI 3.12"那条差集**只剩 chromium 那一半**（CI runner 不装 chromium）。**"本地全绿"仍然推不出"CI 全绿"** |
| 写文本文件 | 用 Python 或编辑器（PITFALLS #38）。★ **2026-10-11 复核**：原文"不要用 PowerShell `>` —— 它按 UTF-16LE 落盘"**是 PS 5.1 的行为**；执行层升到 **`pwsh` 7.6.6** 后逐字节实测 `>`／`Set-Content`／`Out-File` **三条路径全部落 UTF-8 无 BOM** ⇒ **该禁令不再成立**。但**推荐做法不变**（Python/编辑器仍更可控），**判据也不变**（看字节，不看渲染） |
| 中文乱码 | **先跑判据再动手**：`python -c "import pathlib;t=pathlib.Path('F').read_text(encoding='utf-8');print('U+FFFD=',t.count('\ufffd'))"`。`U+FFFD=0` 就是好的，**不要改文件**（控制台渲染 ≠ 文件内容）。★ **2026-10-11 复核**：`chcp` 仍是 **936**，所以**屏幕照样会乱码** ⇒ 这条判据**完全有效**。但**新增了一条根因不同的乱码**：Python 子进程自己的 stdout 默认 GBK（`'gbk' codec can't encode character '\u2713'`），处置是 **`PYTHONUTF8=1`** 或 **`python -X utf8`**，与 shell 无关 |
| GitHub 通道 | **哪个通道通是随时刻变化的**，不是常量。推送前先探测（PITFALLS #9 / #45），别把上次的成功当依据 |

---

## 5. 文档纪律

- **文档按变更频率分层**（`CONTRIBUTING.md` §5）：
  ADR 记**不再变的决策**；`API.md` / `CONFIG.md` / `SCHEMA.md` 记**随代码走的事实**（改必更）；
  `PITFALLS.md` 记**真机环境坑**（只增不改，机构记忆）。
- **`docs/adr/` 按主题合订、定期合并**（2026-10-07 起）：ADR 是**不再变的决策**，
  但它**不是"只增不改"的流水账**——2026-10-07 已把 22 篇按主题合并成 **8 篇合订本**
  （用户授权：「快速合并历史的 ADR」「未来的开发存在变更是正常的」）。
  三条规矩：
  1. **合订本取该组最小的编号**（`0001` / `0002` / `0003` / `0005` / `0007` / `0010` / `0011` / `0019`），
     被吸收的编号**整篇并入后删除文件**，**不留 stub**（留了会让 `test_agents_md.py` 的篇数对不上）。
  2. **合并只做组织与去重，不改写决策措辞**；每节要标**出处**（`（原 ADR-00XX）`），
     让读者能看出"这条决策是当时哪一篇定的"。
  3. **改已采纳的决策时，在合订本里保留原决策并标注取代关系**
     （划线 + 「已被本文 §X 取代」），**不要删掉** —— 让"曾经的方案"与"取代它的方案"都能看见。
- **`docs/adr/` 的"诚实边界"类小节不许在合并中弱化**：`已知风险（已登记，未修）` /
  `诚实边界` / `这一层挡不住什么` / `本期做与不做` / `重新评估的触发器` / `外部参照`
  六类小节必须**逐条保留**（可重组位置、可改标题，不可丢条目、不可软化措辞）。
  ★ 被吸收的原文件删除后，**取回命令写进 `CHANGELOG.md` / `docs/PITFALLS.md` 的退役说明**
  （在那两处只允许**追加**说明，**不许改写原有文字** —— 它们是历史记录）。
- **新增决策请用** [docs/adr/0000-template.md](docs/adr/0000-template.md) —— 它比历史 ADR 多一栏
  **「外部参照」**（谁解决过同一问题、他们怎么做、我们为何不同），**别省掉那一栏**。
  新增**新编号**（现有最大值 +1），**不要复用已退役的编号**。
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
- **本文件自己的纪律也走机检，不靠自觉**：`pytest tests/test_agents_md.py -o addopts=""`
  （**6 条**）守四件事 —— 铁律编号**无缺号**、铁律在**阅读顺序上单调递增**、
  头部声明的**坑数与 ADR 篇数**与 `PITFALLS.md` / `docs/adr/` 实际一致、**全部链接存活**。
  ★ 每一条都对应一次**已经发生过**的漂移（"89 条"其实已是 92；"16 篇 ADR"其实是 17 个文件；
  阅读顺序 1–12 → 33–37 → 13–32）。**加了规则 / 坑 / ADR 就跑它** —— 本文件是入口，
  它写错的数字会被逐字继承。

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
