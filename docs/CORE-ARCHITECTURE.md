# 核心 Agent 架构与设计（CORE-ARCHITECTURE）

_2026-09-18 撰写。锚定 `15b847e`（main，v0.3.12 + 3）。本文是**新增文档**，不替代任何既有文档：
它把散落在 `plugin_api.py` / `loop.py` / `cache.py` / `approvals.py` / `sandbox/` / `skills/` 模块文档串里的
设计意图**收拢成一份自上而下的结构说明**，并逐条对照行业当前做法。_

> **与既有文档的分工**（避免第四份会漂移的事实源）：
>
> | 文档 | 回答什么 | 性质 |
> |---|---|---|
> | 本文 | **核心 Agent 长什么样、为什么这样分层、离行业标准差在哪** | 结构说明 + 评估，随架构变 |
> | `CODEBASE_OVERVIEW.md`（**2026-09-29 曾删除，2026-10-07 重新生成**） | 整个代码库的模块地图、风险台账、验收基线 | 全仓视角，随代码变 |
> | [TOOLS.md](TOOLS.md) | **现在到底有哪些工具**、加一个工具要同步哪五处 | **清单由脚本生成**，机检守着 |
> | `PLUGIN-SDK.md`（**该文件已于 2026-09-29 删除**） | 怎么写一个插件（契约细节） | 面向插件作者 |
> | [adr/](adr/) | **不再变的决策**及其后果 | 只增不改 |
>
> 本文中所有"工具数 / 风险分布"的数字都以 `python tools/gen_tools_doc.py` 的机检输出为准，不手抄。

---

## 1. 定位与设计目标

### 1.1 它是什么

Workbench 的核心 Agent 是一个**单用户、本机、工具驱动的对话执行体**。它不追求"通用智能体平台"，
而是解决一个具体问题：**让模型能够安全地操作一个真实的、有生产数据的 SaaS 平台（eGMP）**。

这个定位直接决定了后面所有设计取舍：

| 前提 | 推导出的设计 |
|---|---|
| 单用户、单机、无多租户 | 不做鉴权矩阵、不做配额、不做租户隔离；`/api/*` 至今无认证（R1b，**有意保留**） |
| 工具全部由**自己**编写 | 不做 entry_points 自动发现 / 独立进程 / 版本协商——那些是给"第三方不可信插件 + 大规模生态"付的成本（`plugin_api.py` 文档串） |
| 操作会**真的改生产数据** | 风险分级 + 确认门 + 全量审计是**必需品**，不是加分项 |
| 会执行**用户上传的代码**（脚本式 Skill） | 必须有沙箱执行底座，且必须**诚实标注它不是安全边界** |
| 无人值守的长时间任务（五步织网） | 任务记账表、SSE 长任务流（~~断点续跑 checkpoint~~ 随配置工厂于 2026-09-22 删除） |

### 1.2 显式非目标（**刻意不做**）

这些不是"还没做"，是"判断过不做"。写下来是为了避免每次评审都重新讨论一遍：

- **不是安全边界**：沙箱是**限损层**（[ADR-0014](adr/0007-credentials-sandbox-and-trust.md) §D-3）。跑起来的代码仍能读它有权读的一切。
- **不做自动发现**：插件装配是 `plugins/__init__.py::_MODULES` 显式清单。研究结论是"发现机制的收益接近零"（`docs/research/core-plugin-separation-research.md` §0）。
- **不做热插拔（核心层）**：core 插件导入期一次装配、之后只读；只有 `installed` 层（Skill）运行期可变。
- **不做多 Agent 编排**：无 planner/worker 分工、无子 Agent 派生。理由见 §7.3。
- **不做向量检索/知识库**：迁移 6 已把 `knowledge_nodes` / `faq_cards` 删除，Embedding 配置随之移除。
- **不做 MCP 客户端**：本期未接入，但**通道已预留**（`mcp-stdio` 策略 + stdio/JSON 同构的脚本协议）。见 §7.2。

---

## 2. 总览：八层结构

```
┌──────────────────────────────────────────────────────────────────────────┐
│ L8 传输层                                                                │
│   routes_chat.py  POST /api/chat/stream  → SSE（对话链路）                │
│   routes_agent.py GET  /api/agent/*      → REST（目录/调用/确认/审计）    │
│   ▲ 两条链路消费**同一个事件契约与同一个中枢**，不存在两套工具表          │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │ dict 事件（types.EventType）
┌───────────────────────────────▼──────────────────────────────────────────┐
│ L7 循环引擎层  harness/loop.py                                            │
│   run_agent_loop(): call → tool → observe → repeat                        │
│   SafetyGate（步数预算 12 / 重复调用检测）· UsageMeter（用量与缓存命中）  │
│   事件流与传输解耦：SSE / 测试断言消费**同一序列**                        │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │ ToolHub.execute(name, args, ctx)
┌───────────────────────────────▼──────────────────────────────────────────┐
│ L6 工具中枢层  plugin_api.ToolHub                                         │
│   ToolSpec{name, description, args_model, handler, risk, timeout_s}       │
│   Risk: READ / WRITE / DANGEROUS  ·  Invocation  ·  ToolContext           │
│   core 层（导入期只读） / installed 层（运行期可变）                       │
│   _tools 是 **派生视图**（_rebuild 从启用中的插件推导）                    │
└──────┬────────────────────────────────────────────┬──────────────────────┘
       │ 风险工具先过门                              │ handler(args, ctx)
┌──────▼──────────────────────────┐   ┌──────────────▼──────────────────────┐
│ L5 确认门与审计层 approvals.py   │   │ L4 沙箱执行层 sandbox/              │
│   PendingStore（进程内权威）     │   │   runner.run(ExecRequest) → E1~E4   │
│   单次令牌 · 5min · 参数指纹     │   │   policy 有限枚举策略 + 环境白名单  │
│   write_audit（唯一审计入口）    │   │   job   Windows Job Object 树杀     │
│   pending_action 表（仅留痕）    │   │   audit 审计写在**唯一扼流点**      │
└──────────────────────────────────┘   └──────────────▲──────────────────────┘
                                                       │ 脚本式工具
┌──────────────────────────────────────────────────────┴───────────────────┐
│ L3 技能供给层  skills/                                                   │
│   manifest.py  SKILL.md 契约（wraps 声明式 / script 脚本式，二选一）      │
│   security.py  ZIP 准入 + 可疑模式扫描  ·  library.py 装/卸/删            │
│   script_runner.py  stdin JSON → stdout JSON（**与 MCP stdio 同构**）     │
└──────────────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────────────┐
│ L2 上下文与缓存层  cache.py + harness/context.py                          │
│   plan_prefix(): [system] + [冻结摘要] + [原文历史] + [新消息]            │
│   **append-only**：已发出的前缀逐字节不改 → DeepSeek 前缀缓存可命中       │
│   freeze_summary() 摘要边界只前进 · UsageStats 命中量落库                 │
└──────────────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────────────┐
│ L1 模型接入层  services/deepseek.py                                       │
│   stream_chat() SSE 流式 + function calling 增量聚合 + include_usage      │
│   chat() 非流式（摘要折叠）· 模型名归一化                                 │
│   ⚠ 官方**无**内置 web_search——这是 L6 里 websearch 插件存在的原因        │
└──────────────────────────────────────────────────────────────────────────┘
```

**一条不可违反的依赖纪律**：`L6 ← L3/L4`，即 `skills → sandbox`、`plugins → plugin_api`，
**永不反向**。`plugin_api.py` 里出现平台术语、import 插件、或 import `db` 都是错的
（它连审计与确认门都靠注入）。这条纪律是"业务能力能整体拆除而不碰宿主"的唯一保证。

---

## 3. 分层契约详解

### 3.1 L1 模型接入层（`services/deepseek.py`，255 行）

**职责**：把 DeepSeek 的 OpenAI 兼容协议翻译成本仓的事件词汇。**不含任何 agent 逻辑**。

关键实现点：

- **工具调用增量聚合**：流式响应里 `tool_calls` 是按 `index` 分片到达的，`name` 与 `arguments`
  必须**按 index 累加字符串**（`:180-191`）。这是流式 function calling 最容易写错的地方。
- **强制 `stream_options.include_usage: True`**（`:133`）。不打开它，流式响应**不带 usage**，
  于是缓存命中量永远看不到——而那正是 L2 存在的前提。
- **usage 双形态兼容**：Chat Completions 的 `prompt_cache_hit_tokens` 与 Responses API 的
  `input_tokens_details.cached_tokens` 两种口径都吃（`cache.UsageStats.add`）。
- **`last_usage` 走属性而不是改签名**（`:111`）：`chat()` 的调用点（摘要折叠）
  只关心文本，为它们全都改签名不值得。
- **模型名归一化**（`normalize_model`）：`deepseek-v4-flash` 等旧名/别名统一为 `deepseek-flash`，
  避免"库里存着旧名的用户某天突然 404"。

**边界**：这一层不知道工具、不知道对话、不知道缓存策略。换供应商只需替换本文件 + L2 的
缓存假设（DeepSeek 是自动前缀缓存，Anthropic 是显式 `cache_control`，语义不同——见 §6.3）。

### 3.2 L2 上下文与缓存层（`cache.py` 308 行 + `harness/context.py` 83 行）

**这是全仓最"反直觉但最值钱"的一层**，值得单独理解。

**问题**：DeepSeek 的上下文硬盘缓存默认开启，但命中条件极硬——后续请求必须**完整匹配**一个
已持久化的**缓存前缀单元**；前缀里改一个字节，从那一字节起全部算 miss。而价格差是 **50×**
（命中 $0.003 vs 未命中 $0.15 / 1M tokens）。

**改造前的错**：`_manage_context` **每轮都重新调 LLM 生成摘要** → 前缀每轮都变 → 命中率恒为 0。
既慢、又烧 token、还把最贵的 50× 差价全部付掉。

**现在的三条保证**：

1. **append-only 前缀**：`plan_prefix()` 返回的数组**就是**后续每轮复用的那个数组；工具轮次往里
   `append`，因此下一轮发出的前缀与这一轮**逐字节相同**。
2. **摘要边界只前进**：`freeze_summary()` 用 `summarized_upto`（覆盖到的消息**条数**）表达边界，
   `if upto <= state.summarized_upto: return state` 保证单调。超预算时才折叠一次，之后连续多轮重新稳定。
3. **命中量入账**：`prompt_cache_hit_tokens` / `miss` 落 `deepseek_usage` 表，算命中率与省钱比例。

**为什么用"条数"而不是消息 id**：历史消息不会删除也不会重排，条数天然单调递增，
正好满足"冻结边界只前进"这一缓存前提（`cache.py:60-64`）。

**刻意的边界**：已经只剩最近 `keep_recent_turns=5` 轮还超预算时，`plan_prefix` 如实返回
（`truncated=[]`），**不装作已处理**（`:157-159`）。上下文估算用的是一个粗略的
CJK 字符数 + 英文词数估算器（`context.estimate_tokens`），**不是真 tokenizer**——这是已知的精度妥协。

### 3.3 L3 工具中枢层（`plugin_api.py`，1,366 行）

**全仓最重要的一个文件**：Agent 核心的唯一公开扩展面，也是工具清单的**单一事实源**。

#### 3.3.1 三条不变量

1. **宿主拥有装配权**：插件只提供 `register(hub)`。加载顺序、失败隔离、审计写入全在宿主手里；
   插件抛异常 = 跳过该插件，**绝不阻断应用启动**（与 `api/__init__.py::_optional_routers` 同一语义）。
2. **零领域知识的反向依赖**：不 import 插件、不 import `services/egmp`、连 `db` 都不 import。
3. **一个工具只有一处定义**：HTTP `/api/agent/tools`、对话的 function calling schema、审计表，
   三处都必须从 `ToolHub` 读。

> 第 3 条是**用一次真实事故换来的**：2026-09-15 之前 `harness/tools.py` 有 3 个基础工具、
> `routes_agent.py` 有 4 个域工具，**两套并存且互不可见**。后果是"对话里的模型只能聊天，
> 看不到任何平台能力"。现在 `harness/tools.py` 已降级为兼容垫片（只剩再导出）。

#### 3.3.2 风险分级是**安全属性**，不是元数据装饰

```python
class Risk(str, Enum):
    READ      = "read"       # 不过门
    WRITE     = "write"      # 过门
    DANGEROUS = "dangerous"  # 过门 + UI 二次确认 + 永不进入 Monitor 可执行回放
```

当前分布（机检输出）：**16 read · 2 write · 0 dangerous**（共 18 个 core 工具）。

> ⚠️ **产品当前一个 `DANGEROUS` 工具都没有**——唯一的那个（`factory_create`）随配置工厂在 2026-09-22 整体删除。
> **删的是工具，不是分级能力**：枚举三档照旧、确认门的 dangerous 分支照旧（§3.5 与 §5 的 I6 都没变），
> 只是今天没有产品工具走到那一档；该分支目前由 `tests/test_plugin_api.py` 用合成 spec 覆盖。
> 重新出现 DANGEROUS 工具没有额外动作——显式声明 `risk=Risk.DANGEROUS` 即自动过门。

#### 3.3.3 `_tools` 是派生视图——一条防静默泄漏的结构性设计

`ToolHub._rebuild()` 是**唯一**允许写 `_tools` 的地方，它从**启用中的插件**重新推导。
装配、卸载、禁用都走它。因此"摘了插件但工具还在表里"这类泄漏**在结构上不可能发生**。

改造前 `_tools` 与 `_plugins` 是两份手写同步的字典——一旦支持卸载就会出现静默泄漏。
这是"支持运行期可变"倒逼出来的重构。

#### 3.3.4 core / installed 双层

由 `PluginOrigin` 区分，动机是一条**真实冲突**：`tools/gen_tools_doc.py` 断言"文档里的 N 个工具
== 运行期中枢里的工具数"，而用户能自己装 Skill 之后，运行期工具数**因人而异**。

> **解法不是放宽门禁，而是把"产品自带的"与"用户装的"分开统计**：文档只承诺 `core_specs()` 的数量，
> `installed` 的数量由 API/界面实时回答。

`register_installed()` 比 `add_plugin()` 多三道校验：强制 `origin=INSTALLED`、`removable=True`
（不信任调用方传对）、**不许顶掉核心工具名**（冲突直接拒——核心工具是产品面，
被一个上传的 Skill 覆盖会变成安全事故）。

`deregister_capability("")` 的守卫（`:369-373`）值得单独记：核心插件的 `capability_id` 是**空串**，
于是传空会把所有核心插件误判成可卸载目标。**先查"请求是否合法"再查"是否存在"**——
顺序反了守卫就被绕过。

#### 3.3.5 工具结果的失败语义

`execute()` 的契约是 **"任何失败都转为结果，不抛出"**（`:578-582`）。这是刻意的：
Agent 循环里工具异常必须是**可自我纠正的观测**，而不是终止循环的异常。

`ToolStatus` 六态：`ok / error / needs_approval / rejected / timeout / unknown_tool / bad_args`。
结果回填给模型有 **16,000 字符上限**（`MAX_RESULT_CHARS`），超限**显式告知模型**
（"…结果过长已截断，原文 N 字符"），不是静默丢弃。

#### 3.3.6 装配期自检

`ToolSpec.valid()` 在装配期就断言：工具名必须匹配 `[A-Za-z0-9_-]{1,64}`
（**DeepSeek/OpenAI 的 function name 限制，中文工具名会在请求侧被拒——不是本地校验能发现的错误**）、
`risk` 必须是 `Risk` 枚举、`description` 不能空（模型靠它选工具）、`args_model` 必须是 pydantic 模型。

`verify_required_tools()` 在启动时核对 `REQUIRED_TOOLS` 清单（**6 个**关键工具），
把"打包漏收插件 / 插件 import 失败"从**"用户点下去才发现"**提前到**"启动日志里就有"**。

### 3.4 L4 沙箱执行层（`sandbox/`，6 文件 1,171 行）

**唯一被允许执行不受信代码的入口**。对照 [ADR-0014](adr/0007-credentials-sandbox-and-trust.md) 的四层：

| 层 | 强制手段 |
|---|---|
| **E1 生命周期** | Windows **Job Object** 绑定；超时/取消 → 杀**整棵进程树**（`job.terminate`） |
| **E2 环境** | `build_env()` **白名单**筛环境变量（`ENV_ALLOW_EXACT` 20 项 + `LC_` 前缀）。**默认什么都不给** |
| **E3 文件系统** | `cwd` 必须在**沙箱根**内（`Path.resolve()` 后比较，防 `..` 与符号链接）+ 敏感路径拒绝表 |
| **E4 可观测** | 返回体带 `policy` / `policy_fingerprint` / `tree_kill` / `output_truncated` |

**Fail-Closed 的具体含义**（本层不做"尽力而为"）：未知策略名 → 拒（**不回落到默认策略**）；
`cwd` 越界 → 拒；命中敏感路径 → 拒；可执行文件不在解释器白名单 → 拒。

**可执行文件白名单**（`ALLOWED_EXECUTABLES`）：python / node / deno / bun。
**PowerShell 与 cmd 刻意排除**——它们让"执行脚本"等价于"执行任意命令"。

**一条设计上的自我修正**：`audit.record()` 一度**没有任何调用方**，于是 `/api/sandbox/runs`
永远是空表、管理页的"执行审计"永远没内容，而 ADR 的 E4 层在文档上写着"已实现"。
修法不是"记得在调用方写审计"，而是**把审计放在唯一扼流点**（`run()` 是唯一对外入口，
有 7 处 `return` 的 `_run` 只被它调用）——这样"漏"在结构上不可能发生。

**诚实边界**：`allow_network` 是**声明不是强制**（真强制需要 WFP/防火墙）。
`SandboxPolicy.enforced` 属性把**真正被代码强制**的 6 项逐条列出，就是为了让"声明"与"强制"不被读混。

### 3.5 L5 确认门与审计层（`approvals.py`，877 行）

**把 `Risk` 从声明变成真的安全属性**。没有这一层，"危险操作需人确认"就只是一行注释——
模型照样能一句话把 30 个对象建到真机上。

```
模型调用风险工具
  → 无令牌 → 建 pending_action 行 + 返回 needs_approval 观测（**不执行**）
  → 界面拿 pending_id + 人话摘要问用户
  → **对话链路（SSE）**：这一轮**就地等答复**（`AgentConfig.approval_wait_s`，超时＝未同意）
      → 用户点「放行」→ 服务端用**完全相同的参数**重投那次调用（按指纹认领）→ 真正执行
      → 用户点「拒绝」→ 记 rejected → 模型看到拒绝理由，不许重试
  → **HTTP 链路（/api/agent/invoke）**：回 `409 + pendingId`
      → 用户点「放行」→ 兑换**单次令牌**（一次性 · 5 分钟 · 绑定工具+参数指纹）
      → 调用方带令牌重投 → 门开 → 真正执行
```

几个刻意的决定：

- **权威状态在内存，DB 只做留痕**。理由：单机单人应用，待确认操作的生命周期是"几十秒"，
  跨进程重启后**本来就应该失效**（用户重启了应用，凭什么还替他记住一个未确认的写操作？）。
  启动时把上一进程遗留的 pending 标 `expired`，**刻意不恢复**。
- **对话链路自己执行那次调用**（2026-09-22，决策卡 G2）：旧实现只发事件不等，指望"模型重试
  同一次调用"——真机上模型往往已经说完这一轮，用户点放行时**没有任何人在等**，那条批准被搁置。
  现在与实时确认对称：发完 `approval_required` **就地等**，放行后由服务端**按指纹认领**并执行
  （`claim_approved`，一次性），用户只点一次。**超时一律按未同意处理**。
- **同一工具+同一参数在有效期内复用** pending（参数指纹比对），避免界面刷屏。
- **人话摘要**（`summarize`）刻意不做成"工具名 + JSON"——用户要判断的是**后果**，不是参数名。
- **令牌只回给批准它的那一次 HTTP 响应**：不写审计、不进日志、**不回给模型**。
- 门用 **lambda 而不是直接绑 `pending_store().gate`**：闭包里若是当初那个实例，
  `reset_store()`（测试隔离）之后中枢还捏着旧店，表现为"申请到 pendingId 却查不到/批不了"，
  而且**只在测试里出现、生产看不出**——最难查的那种。

**与"环境强制确认"的关系（2026-09-22 起只剩一层）**：配置工厂曾有一条 `confirmed=true` 的
**引擎侧页面级闸门**（`routes_factory.py`）——它随配置工厂整体删除，**今天不存在了**。
当时的口径仍然成立、也仍然值得照抄：两条闸门**独立生效、不是替代关系**——工具级挡 Agent 链路，
引擎侧挡"绕过页面直接打 HTTP"的人，写平台的动作两条都要过。
**但本仓库现在没有任何"写平台数据"的工具**（0 个 `DANGEROUS`），所以工具级确认门是当前唯一站着的那道；
将来重新引入写平台的能力时，**引擎侧那道闸门要一并重建**，不要以为工具级确认门能替代它。

**审计唯一入口** `write_audit()`：HTTP 面与对话循环共用，因此"对话里的工具调用不入审计"
这类缺口不会再出现。写入前 `redact_args()` 剔除凭据类键；
`GET /api/agent/audit` **刻意不 `SELECT *`**（`args` 可能有敏感片段），列表只回摘要列。

### 3.6 L6 技能供给层（`skills/`，7 文件 ≈1775 行）

**格式与 Agent Skills 开放标准同源**：ZIP 包根目录的 `SKILL.md` + YAML frontmatter。

**两种工具形态，二选一**：

| 形态 | 字段 | 执行方式 | 沙箱 | 风险下限 |
|---|---|---|---|---|
| **声明式** | `wraps` | 转发给已有工具（走同一套确认门与审计） | 否 | 被包装工具自己的分级 |
| **脚本式** | `script` | 在沙箱子进程里跑包内 Python | ★ 是 | **≥ `write`** |

**脚本式风险下限为什么必须 ≥ `write`**：它执行的是**上传的代码**，而"这段代码只读"**无法被
静态证明**。标成 `read` 等于给模型一张"不经确认随便跑"的通行证。声明式可以 `read`，
因为它转发的那个工具的语义是我们自己定的。

**为什么二选一而不允许回退**："先转给已有工具、转不了再跑脚本"这种回退语义会让失败路径
变得不可预测，而 GxP 场景要的是**失败可见**，不是"尽力而为"。

**capabilities 是声明需求，不是授予权限**：`network` / `subprocess` 本期**拒绝**
（不是忽略——静默忽略会让你以为权限给了、实际没给，最终表现为"装上了但功能不对"）。
Fail-Closed 比"看起来装上了"好。

**`MAX_TOOLS_PER_SKILL = 20`** 的存在理由与 §6.1 的行业结论一致：工具超过约 20 个后模型
选错的概率显著上升。"装一个 Skill 塞 50 个工具"是把这个坑从产品自带转移到用户身上。

**路径校验的形状/语义两层**（`validate_script_path`）：一条正则**表达不了"段"的语义**——
`.` 是合法字符，所以 `a/../../b.py` 会被形状正则接受。代码里有两条 `assert` 把这件事钉死，
免得后人以为一条正则就够了。初版做的是"静默 LStrip 改写路径"，这也是错的：
它让"我写的是什么"与"实际跑的是什么"不一致，而排障时看的是前者。

### 3.7 L7 循环引擎层（`harness/loop.py`，485 行）

**循环**：`call → tool → observe → repeat`，逐事件 yield。

**事件契约**（与前端 SSE 一一对应，改这里必须同步 `chat.js`）：

| 事件 | 载荷 | 何时发 |
|---|---|---|
| `token` | `{text}` | 正文增量 |
| `thought` | `{text}` | 工具调用轮次里模型的叙述 |
| `action` | `{call, tool:{name,risk,plugin}, auditId}` | 发起工具调用 |
| `observation` | `{result:{ok,summary,status}, invocation:{…}}` | 工具结果 |
| `approval_required` | `{pendingId, tool, risk, summary}` | 风险工具被门拦下 |
| `usage` | 用量与**缓存命中量** | **一次回答只发一条，收尾时** |
| `final` / `error` | — | 终止 |

**`usage` 为什么在收尾才发**：一次回答可能有**多次** LLM 调用（工具轮次、摘要重算），
提前发会让前端显示一个偏低的数字。`summary_calls` 单独计数——摘要那次调用的输入是历史原文，
**几乎必然全 miss**，恰恰是最该被看见的开销。

**安全门控**（`harness/safety.py`，25 行）：`max_steps=12` 步数预算 +
**重复调用检测**（`deque(maxlen=4)` 窗口内同一 `name:arguments` 出现 ≥3 次即终止）。

**与传输层解耦**：本模块只 yield 事件，SSE / WebSocket / 测试断言**消费同一事件序列**。

**一个已留的口子**：`run_agent_loop(plugins=[...])` 是插件白名单，"按页面裁剪工具面"用。
`list_api_for(plugins)` 已实现。理由写在文档串里：**工具 schema 是真实的 token 成本，
全量注入约 2k token/轮**。

### 3.8 L8 传输层（`routes_chat.py` 330 行 + `routes_agent.py` 389 行）

**两条链路，一个中枢**：

| 链路 | 入口 | 风险工具被拦时 | 适用 |
|---|---|---|---|
| **对话** | `POST /api/chat/stream`（SSE） | 发 `approval_required` 事件，界面出「放行/拒绝」 | 人机协同 |
| **HTTP** | `POST /api/agent/invoke` | **409 + pendingId**（不是 400/500） | 脚本/自动化调用 |

两条链路共用 `/api/agent/pending/*` 与同一张 `agent_audit` 表。

HTTP 侧的错误码语义是刻意的：**400 = 参数/前置条件问题（调用方能自己修）；
409 = 需要确认；502 = 执行期失败（多半是平台侧）**。

> 回归断言的是**"handler 调用记录为空"**，不是返回码——即验证的是"真的没执行"，
> 而不是"返回了个看起来对的码"。

---

## 4. 端到端时序（一次带危险工具的对话）

```
用户  POST /api/chat/stream {message}
  │
  ├─ routes_chat: 准备会话 → 读历史 → 写入 user 消息 → SSE {type:start}
  │
  ├─ loop.run_agent_loop
  │    ├─ _build_context → cache.plan_prefix
  │    │     ├─ 未超预算 → 直接返回 [摘要?] + 历史（前缀与上轮逐字节相同）
  │    │     └─ 超预算   → _summarize() 一次 → freeze_summary(边界前进) → 重新 plan
  │    │
  │    └─ while True:
  │         ├─ deepseek.stream_chat(context, tools=hub.list_api_for(plugins))
  │         │     → SSE token*  ────────────────────────────────► 前端逐字显示
  │         │     → end{content, tool_calls, usage} → meter.feed()
  │         │
  │         ├─ 无 tool_calls → yield final + usage（meter.finish + persist）→ return
  │         │
  │         └─ 有 tool_calls → context.append(assistant)
  │              for call in tool_calls:
  │                ├─ safety.is_loop(call)? → yield error，终止
  │                ├─ yield action {call, tool:{name,risk,plugin}, auditId}
  │                │
  │                ├─ hub.execute(name, args, ctx)
  │                │    ├─ _validate() → pydantic 校验（失败 → bad_args 观测，不抛）
  │                │    ├─ spec.precheck?（可选·只读·**在门前**）→ 拒绝则不再问放行
  │                │    ├─ risk != READ 且门未过
  │                │    │    → PendingStore.request() → needs_approval
  │                │    │    → yield approval_required ──────────► 界面出「放行/拒绝」
  │                │    │    → **就地等答复**（超时＝未同意）→ yield confirm_resolved
  │                │    │    → 用户放行 ⇒ 服务端用同参数重投（claim_approved 认领）
  │                │    ├─ asyncio.wait_for(handler(args, ctx), timeout_s)
  │                │    │    └─ handler 内部：同步阻塞调用一律 asyncio.to_thread
  │                │    │       └─ 脚本式 Skill → sandbox.run_async(ExecRequest)
  │                │    │            ├─ policy.get_policy()（未知 → Fail-Closed）
  │                │    │            ├─ resolve_executable()（白名单）
  │                │    │            ├─ cwd 必须在沙箱根内 + 非敏感路径
  │                │    │            ├─ build_env() 白名单 → Job Object → Popen
  │                │    │            └─ run() 扼流点落 audit → /api/sandbox/runs
  │                │    └─ _audit() → write_audit()（唯一入口，redact_args 打码）
  │                │
  │                ├─ yield observation {result:{ok,summary,status}, invocation:{…}}
  │                └─ context.append({role:"tool", tool_call_id, content})
  │              return  # 工具结果已回填，外层 while 发起下一轮
  │
  └─ routes_chat 收尾：持久化回答 + qa_metrics → SSE {type:done}
```

---

## 5. 不变量清单（改核心前必读）

| # | 不变量 | 违反后果 |
|---|---|---|
| I1 | `append-only`：已发出的 messages 前缀**永不改写** | DeepSeek 缓存命中率归零，成本 ×50 |
| I2 | 摘要边界（`summarized_upto`）**只前进** | 同上 |
| I3 | 工具**只有一处定义**（`plugins/*.py` 的 `ToolSpec`） | 模型看不到平台能力（已发生过） |
| I4 | 依赖方向 `plugins → plugin_api`，**永不反向** | 业务能力不再"可整体拆除" |
| I5 | `_tools` **只能**由 `_rebuild()` 写 | "摘了插件但工具还在"的静默泄漏 |
| I6 | 风险工具**没有令牌就不执行**（不是"警告后执行"） | 模型一句话改生产数据 |
| I7 | 审计写在**唯一扼流点**，不靠调用方记得 | E4 层静默空转（已发生过） |
| I8 | 沙箱未知策略/越界路径/非白名单解释器一律**拒** | 沙箱退化成"通用命令执行器" |
| I9 | 脚本式 Skill 的 risk **≥ `write`** | 给模型一张"不经确认随便跑"的通行证 |
| I10 | 凭据**不落明文**；`redact_args` 只是兜底 | 凭据进日志/审计/对话 |
| I11 | 同步阻塞调用**一律** `asyncio.to_thread` | 整个服务卡住（不是报错） |
| I12 | 待确认操作**跨重启失效**，不恢复 | 用户重启后替他记住一个未确认的写操作 |
| I13 | 工具异常 = **观测**，不是终止循环的异常 | 一次网络抖动打断整个任务 |
| I14 | core 插件**不可卸载**（只能禁用） | 产品自带能力被用户删掉 |
| I15 | **system 消息永不被折叠**：折叠窗口只覆盖"非 system 的正文体"，`plan.messages[0]` 恒为**原始** system | 长会话里模型丢掉全部行为约束，表现为"越用越不听话" |
| I16 | **`assistant.tool_calls` 与 `tool` 回填必须成对**：预算耗尽 / 取消 / 超时 / 崩溃，未执行的**每一条**都要回填一条 tool 观测 | 历史不可回放（API 报 `tool_call_id not found`），对话级断点续跑永远做不成 |
| I17 | **上下文预算在每次模型调用前复检**，不只在一次用户回合开始时 | 请求超窗 → 整轮失败，而失败形态是一句无信息量的中文错误 |
| I18 | **从外部读入的内容一律带可信度标签**，且标签随本回合传播到确认门与审计 | 提示注入（OWASP LLM #1）无结构防线 |

> **I15–I18 来自 `CORE-AGENT-PLAN.md`（**该文件已于 2026-09-29 删除**） §2。**
> 落地状态：**I15 ✅ / I16 ✅ / I17 ✅ / I18 ✅**（2026-09-24 全部落地；I15–I17 的判据见
> `tests/test_cache.py` 与 `tests/test_agent_contract.py`，前两条的 PITFALLS 是 #90 / #93 / #94）。
> 三条上下文类的合并记录在 **[ADR-0019](adr/0019-in-turn-context-and-budget-governance.md)**
> （上下文窗口的**轮内**治理：折叠永不吞 system ＋ 预算每次调用前复检 ＋ 未执行的调用必须回填）。
> **I18 单独成篇：[ADR-0022](adr/0007-credentials-sandbox-and-trust.md)** —— 它管的不是上下文**大小**
> 而是上下文**来源**：`ToolSpec.trust` 装配期必填（`LOCAL` / `UNTRUSTED`）、外部结果带机器可读前缀、
> 读过外部内容后过确认门时**给用户**带提示。判据 `tests/test_trust_labels.py`（**12 条**）。
> ⚠️ **编号是 0019 不是 0018** —— 0017/0018 曾被已删除的配置工厂 ADR 占用，而本仓
> （`PLATFORM-OPERATIONS-LEARNED.md` / `factory-tuning/reports/**`）**至今仍引用那个
> 已不存在的 ADR-0018**，复用会让那些引用悄悄指向错误的文档。
>
> ⚠️ **写在这里的才算数**：I15–I17 先前只在代码注释与 PITFALLS 里被引用，
> 而本表**没有它们** —— 于是那些引用指向的是**不存在的不变量**。
> 引用一个不存在的编号比不引用更坏：读的人会以为自己漏读了哪一节。

---

## 6. 与行业标准做法的对照

> 本节的事实基座见文末「参考与核实」。判断分三档：**✅ 符合行业主流** / **🟡 行业有共识但做法不同** / **❌ 缺失**。

### 6.1 工具体系：契约、发现、注入

| 维度 | 行业当前做法 | 本项目 | 判定 |
|---|---|---|---|
| **工具契约** | JSON Schema + 名字 + 描述；Anthropic 2025-11 追加 `input_examples` | pydantic `args_model` → `_openai_schema()` 自动派生，**schema 不手写第二份** | ✅ **优于主流**：类型即契约，schema 由类型派生，天然不会漂移 |
| **工具来源标准化** | **MCP** 已是事实标准：2025-12-09 由 Anthropic 捐给 Linux Foundation 下的 Agentic AI Foundation（Anthropic/Block/OpenAI 共同创立，Google/Microsoft/AWS/Cloudflare/Bloomberg 支持），1 万+ 公开 server、97M+ 月 SDK 下载，ChatGPT/Cursor/Gemini/Copilot/VS Code 均已采用 | 自有 `ToolHub` 插件协议，**未接 MCP** | ❌ **缺失**，但**通道已预留**（见 §7.2） |
| **大规模工具注入** | Anthropic Tool Search Tool（`defer_loading`）：>10K token 或 10+ 工具时按需发现，上下文省 85%；纯工具定义在 5 server 场景可达 55K token，Anthropic 内部见过 134K | **全量注入**，自估 ~2k token/轮；`list_api_for(plugins)` 已留白名单口子 | 🟡 当前工具规模（见 [TOOLS.md](TOOLS.md)）、~2k token **远低于 10K 阈值**，当前全量注入是**正确选择**；但缺"工具变多之后的退路" |
| **风险分级** | 框架普遍有（OpenAI Agents SDK `needsApproval`、LangGraph `interrupt()`） | `Risk` 三档 + 显式声明（装配期强制，不声明不给过） | ✅ 符合 |
| **工具结果体量** | 普遍靠截断；前沿做法是 Programmatic Tool Calling（工具在代码沙箱里编排，中间结果不进上下文，token 降 37%） | `MAX_RESULT_CHARS=16_000` **显式告知**截断 + 沙箱已具备执行能力，但**未做工具编排** | 🟡 截断处理比多数项目诚实（告知而非静默）；PTC 是明确的下一步 |
| **参数准确率** | `input_examples` 实测把复杂参数准确率从 72% 提到 90% | 靠 `description` 里写清口径（**后果类措辞必须落到描述里**：会写平台数据的工具要写明"会真的改平台数据"——本仓的教训是一次误调用不报错、却会真的建出来） | 🟡 无示例机制；当前工具规模下描述够用 |
| **并行工具调用** | OpenAI/Anthropic 均支持一次返回多个 tool_call，框架普遍并发执行 | **串行** `for call in assistant["tool_calls"]`（`loop.py:211`） | 🟡 功能正确（回填顺序无所谓），但多工具场景延迟是 N 倍 |

**一个值得点出的优点**：`ToolSpec.valid()` 在**装配期**就断言工具名符合
`[A-Za-z0-9_-]{1,64}`。这是踩过坑的——**中文工具名会在请求侧被拒，而本地校验发现不了**。
多数项目的工具名约束只写在文档里。

### 6.2 人在环路（HITL）与安全

| 维度 | 行业当前做法 | 本项目 | 判定 |
|---|---|---|---|
| **审批机制** | OpenAI Agents SDK `needsApproval`、LangGraph `interrupt()` 提供暂停/恢复原语 | 单次令牌 + 5 min TTL + **参数指纹绑定** + 复用 pending 防刷屏 | ✅ **强于主流**：指纹绑定与"令牌只回给批准那次响应"是多数框架没有的 |
| **审批 = 安全边界？** | 业界明确警告**不是**（如 Arcjet 的 "needsApproval and LangGraph interrupt() are not a security policy"）——审批是 UX 原语，可被绕过 | **结构性强制**：无令牌时 `ToolHub.execute` **根本不调用 handler**；回归断言的是"handler 调用记录为空"而非返回码 | ✅ **优于主流**：把审批做成了执行侧的硬闸门，而不是提示层 |
| **双重闸门** | 少见 | 工具级确认门（挡 Agent）+ 引擎侧 `confirmed=true`（挡直连 HTTP）——**引擎侧那道随配置工厂于 2026-09-22 删除，当前只剩工具级这一层** | 🟡 曾是优秀实践；现在"双重"这半句已不成立 |
| **审计** | 前沿用 OTel GenAI 语义约定做 span/trace | 自建 `agent_audit` 表（工具/插件/风险/状态/参数打码/耗时）+ `sandbox_run` + `skill_audit` + `pending_action` | 🟡 留痕充分、可查询；**不是 OTel 标准格式**，无法直接对接既有可观测后端 |
| **代码执行沙箱** | 云侧：E2B / Daytona / microsandbox（microVM 级隔离）；本地：容器 / gVisor；**Windows 原生**：受限令牌 + ACL + 防火墙规则（OpenAI Codex `elevated`/`unelevated` 双模式）、Sandboxie（内核态重定向） | **无 microVM、无容器、无受限令牌**；Windows **Job Object**（进程树终止）+ 环境白名单 + 路径拒绝 + 解释器白名单，**自称"限损层不是安全边界"** | 🟡 **诚实且务实**，但**定位需要说准**（见下） |
| ↳ **Job Object 在行业里的位置** | 它解决的是**进程树生命周期**（`KILL_ON_JOB_CLOSE` 一次终结全树），**不是权限边界**。OpenAI Codex 的 Windows 方案里**完全不用 Job Object**——它用受限令牌 / 独立低权限沙箱用户 + ACL + 防火墙 + 私有桌面 | 本项目把它当作沙箱的**主体** | ⚠️ **若要升级隔离，下一个原语是受限令牌（`CreateRestrictedToken`）/ AppContainer + ACL，而不是继续加固 Job Object**。代价是**需要管理员做一次性设置**（Codex 正因此必须提供 `unelevated` 回退）。本项目"用户自己电脑上零配置"的取向与提权天然冲突 —— **建议维持限损层定位，把这件事写进用户可见文档** |
| **`/api/*` 认证** | 本地桌面应用普遍也做 token | `/extension/*` 已有令牌（P0-1）；**`/api/*` 95 个端点仍无认证**，依赖"只监听回环 + 无 CORS" | 🟡 **有意的技术债**，已记录为 R1b 并写明重新评估的触发器 |

### 6.3 上下文与成本工程

| 维度 | 行业当前做法 | 本项目 | 判定 |
|---|---|---|---|
| **提示缓存** | Anthropic 显式 `cache_control` 断点；OpenAI/DeepSeek 自动前缀缓存 | **为自动前缀缓存做了显式工程**：append-only + 冻结摘要边界 + 命中量入账 | ✅ **罕见**：多数项目只是"用了支持缓存的 API"，本项目把命中率当成**可度量、可优化**的指标 |
| **上下文压缩** | 普遍是"超阈值摘要"或"滑动窗口" | 冻结式摘要（边界只前进）+ `keep_recent_turns=5` | ✅ 优于"每轮重算摘要"和"滑动窗口"（后者缓存命中率恒为 0） |
| **token 计数** | 用供应商 tokenizer（tiktoken 等）做精确计数 | **粗略估算器**：CJK 字符数 + 英文词数 | ❌ **精度缺口**：估偏会导致该折叠时没折叠，或过早折叠 |
| **用量可观测** | 普遍记 token 与成本 | `deepseek_usage` 表 + 命中率 + `savedRatio`（按 50× 价差折算）+ `summary_calls` 单列 | ✅ **优于主流**：把"摘要调用几乎必然全 miss"这个隐性开销显式暴露 |
| **子 Agent / 上下文隔离** | LangGraph 子图、CrewAI/AutoGen 多角色、Claude Code Task 工具 | **无** | ❌ 缺失（但见 §7.3 的"是否该补"判断） |

### 6.4 技能与插件生态

| 维度 | 行业当前做法 | 本项目 | 判定 |
|---|---|---|---|
| **技能包目录** | **Agent Skills 规范**：`SKILL.md` + `scripts/` + `references/` + `assets/`（[agentskills.io/specification](https://agentskills.io/specification)） | `SKILL.md` + `scripts/` + `references/`，**没有 `assets/`** | 🟡 基本对齐 |
| **技能是什么** | 规范里 Skill = **指令与知识**（+ 可选脚本）；核心机制是 **progressive disclosure 四阶段**：① Advertise（每技能约 100 token，只注入 name+description）→ ② Load（任务匹配时拉取正文，建议 <5000 token）→ ③ Read resources（按需读 `references/`）→ ④ Run scripts | 本项目 Skill = **工具提供者**：`tools:` 数组声明贡献哪些工具，`SKILL.md` **正文明确"本期不注入上下文"** | ❌ **语义分歧**：借用了规范的外壳，但**恰好放弃了规范的核心机制**（正文按需注入）。本项目是"能力包"，规范是"知识包" |
| **frontmatter 字段** | 必填仅 `name`（≤64、**仅小写字母/数字/连字符**、不得首尾连字符、**必须与父目录名一致**）与 `description`（≤1024）；可选 `license` / `compatibility`（≤500，环境要求）/ `metadata` / `allowed-tools`（实验） | 必填 `id` / `name` / `version` / `description` / `tools`；另有 `capabilities` | ❌ **字段不兼容**：规范**没有** `id` / `version` / `tools`，也**不要求** `name` 与目录名一致 → 本项目的包**装不进**任何遵循规范的客户端（已验证微软 Agent Framework、CrewAI、Cline 均支持该规范） |
| **"技能贡献工具"这件事规范怎么做** | 规范**不让技能声明工具**。客户端只在"至少一个技能带资源"时才注册 `read_skill_resource`、只在"至少一个技能带脚本"时才注册 `run_skill_script`——**工具面随实际能力动态收缩** | 技能**直接声明** `tools:`，装配期全量并入 `ToolHub` | 🟡 **两条路都能走通**，但规范的做法**天然抑制工具面膨胀**；本项目 18 个 core 工具 + 每个 Skill 最多 20 个，**没有"按需注册"这一层** |
| **技能工具的审批粒度** | 微软 Agent Framework：三个技能工具（`load_skill` / `read_skill_resource` / `run_skill_script`）**默认全部需要审批**，靠 `auto_approval_rules` 放行；`ReadOnlyToolsAutoApprovalRule` 自动批准前两个，**但不批准 `run_skill_script``** | 只有三档 `Risk`；技能工具整体按"声明式 ≥ 被包装工具 / 脚本式 ≥ write"定级 | 🟡 **本项目的分级更粗**：微软做到了"同一机制内按能力粒度分级"，本项目是"按工具整档分级"。本项目脚本式 ≥ `write` 的结论与微软一致（都不自动放行脚本执行） |
| **`capabilities` 的语义** | 规范的 `compatibility` 是**信息性**的（"需要什么环境"）；`allowed-tools` 是预先批准的工具清单 | `capabilities` 是**权限申请 + Fail-Closed 校验**：声明 `network`/`subprocess` 直接**安装失败** | ✅ **强于规范**：把权限声明做成了强制准入，而非文档说明 |
| **脚本执行** | 规范只说 `scripts/` 是"可执行代码"，**由客户端自行决定怎么跑**；微软官方 `SubprocessScriptRunner` 被自标为"**仅供演示**"，并要求生产环境补上沙箱 + 资源限制 + 输入校验 + 结构化审计。MCP 归档里的脚本**永不执行**，只作只读资源 | 沙箱子进程 + **stdin JSON → stdout JSON**，文档明确写"**与 MCP stdio 传输同构**"；已接沙箱、策略、审计 | ✅ **强于参照物**：本项目把微软"让你自己补"的那一层**已经补好了** |
| **完整性校验** | 微软 `MCPSkillsSource`：archive 条目可带 `digest`，格式 `sha256:` + 64 位小写十六进制，**解压前校验**，不匹配则跳过该条目；另有 `ArchiveMaxFileCount`(20) / `ArchiveMaxSizeBytes`(1 MB) / `ArchiveMaxUncompressedSizeBytes`(1 MB) / 扩展名白名单 | ZIP 准入 + **可疑模式扫描**（`eval(` 等标记 high）；**未见 sha256 摘要校验** | 🟡 **静态扫描领先多数生态**（多数组中不做扫描），但**缺摘要校验**与**解压后大小上限**（防 zip bomb）。**摘要校验约一天工作量，投入产出比最高** |
| **工具名冲突** | 规范未规定 | **不许覆盖核心工具名**（`register_installed` 直接拒） | ✅ 规范未覆盖的有效防护 |
| **插件发现** | entry_points / 插件市场 / MCP registry / 技能市场 | **显式清单**，无自动发现 | 🟡 有充分论证的**刻意选择**（作者只有自己、全可信代码）。⚠️ 但**"技能市场"已是行业现实**（`anthropics/skills` 177,188 ★、`obra/superpowers` 288,874 ★，均实测）——不跟是对的，**要清楚这是主动放弃生态位** |
| **供应链安全** | 沙箱 + 签名 + 准入扫描 + 版本/吊销 | ZIP 准入 + 可疑模式扫描 + 路径形状/语义两层校验 | 🟡 对齐扫描；**无版本治理与吊销机制** |

> **这一格值得展开**：本项目 `manifest.py` 的文档串写"与 ADP 的 Skills 规范同源，都遵循 Agent Skills 开放标准"。
> 实读该规范（并交叉微软 Agent Framework、CrewAI、Cline 的实现）后，更准确的表述是：
> **借用了 Agent Skills 的"`SKILL.md` + 目录"外壳，但把它从"知识包"改造成了"能力包"，
> 同时放弃了规范的 progressive disclosure 机制与字段契约。**
>
> 两者的取舍可以理解——本项目需要的是**给模型加工具**，而规范的 progressive disclosure 是给模型加**指令**。
> 但代价是**生态不互通**，而且这个代价比想象的更具体：
> `anthropics/skills`（**177,188 ★**）与 `obra/superpowers`（**288,874 ★**，MIT）两个仓库实测合计
> 超过 46 万 star，说明**技能生态已经是 2026 年 agent 工程的主流形态**——
> 本项目站在这条主流之外（这是主动选择，但应当被记录为**主动放弃生态位**，而不是"还没做"）。
>
> 如果希望未来互通，最低成本的一步是**兼容读取规范字段**（`name` 已同名；
> `capabilities` 可同时接受规范的 `compatibility` 作为别名），与 [ADR-0013](adr/0003-electron-desktop-shell.md)
> 处理品牌字符串时"写出端发新名 + 读取端两个都收"的手法一致。
> **建议列入 §7，但不紧急**——当前没有跨生态分发的需求。详见 §7.8。

### 6.5 工程治理（本项目的**真正强项**）

这一块不是"agent 能力"，但它决定了上面的东西**能不能长期不腐化**，值得单独列：

| 实践 | 行业普遍状态 | 本项目 |
|---|---|---|
| 文档数字与代码一致 | 靠人记，必然漂移 | **`gen_tools_doc.py` 机检**：文档清单 == 运行期中枢，且校验各文档里的"N 个工具" |
| 启动自检 | 少见 | `verify_required_tools()` + `GET /api/agent/selfcheck`，把"打包漏收插件"提前到启动日志 |
| 失败可见性 | 静默降级常见 | 插件装配失败进 `hub.failures` 并在两个端点暴露；沙箱拒绝也带完整审计信息 |
| 决策留痕 | 散在 PR 描述里 | **22 份 ADR**，含"为什么不做"（D-3 / D-7 这类"诚实边界"条目） |
| 坑的机构记忆 | 少见 | `PITFALLS.md` 只增不改，记真机环境坑与实测证据 |
| CI | 常规 | 12 步，含解释器版本契约、工具清单门禁、打包静态检查 13 项 |

**这一层是本项目与"同类开源项目"最大的差异点**：多数开源 agent 项目在能力上跑得更快，
但很少有 `PITFALLS.md` + ADR + 机检门禁这种"给六个月后的自己留路"的纪律。

---

## 7. 缺口与演进建议

按"投入产出比 × 紧迫度"排序。每条都给出**触发条件**——本项目的一贯风格是"不到触发条件不做"。

> **⚠️ 与 `OPENSOURCE-LANDSCAPE.md`（**该文件已于 2026-09-29 删除**） 的优先级存在一处差异，以那份为准**：
> 本文按"架构完整性"排列，而生态调研得出的**实际动手顺序**是
> **① 评估集（§7.6）→ ② 追踪（§7.5）→ ③ 精确 token 计数（§7.1）→ ④ 并行工具调用（§7.4）
> → ⑤ 技能包 digest（§7.8/生态报告 §6.6）→ ⑥ 沙箱内工具编排（§7.3）**。
> **评估集排第一**的理由是：它是**唯一能回答"我这次改动让 agent 变差了吗"的手段**，
> 而本项目已经有过"改提示词、加插件、换模型名"这类改动，**每次都没有回归能力**。
> 下面是逐条的详细分析。

### 7.1 【低成本，建议优先】精确 token 计数

**缺口**：`context.estimate_tokens()` 是 CJK 字符数 + 英文词数的粗略估算。
`context_threshold=0.8` × `context_window_tokens=1048576` 的判定建立在这个估算上。
（窗口默认 2026-09-24 起取满 `deepseek-flash` 的 1M 上限，见 `harness/types.py`。）

**风险**：估偏低 → 该折叠没折叠 → 请求超长被 API 拒；估偏高 → 过早折叠 → 无谓的摘要开销与信息损失。
两种都是"看起来正常但行为不对"。

**做法**：DeepSeek 用 OpenAI 兼容协议，`tiktoken` 的 `cl100k_base` 是可用近似；
或退一步，用 `len(text.encode('utf-8'))/3` 这类更保守的字符换算并**加安全系数**。

**触发条件**：出现一次"请求超长"报错，或 `summary_calls` 明显偏离预期。

### 7.2 【中成本，看需求】MCP 客户端接入

**缺口**：MCP 已是事实标准（2025-12 捐给 Linux Foundation 下的 AAIF，1 万+ server）。
本项目有 `mcp-stdio` 沙箱策略（预留未启用）、`script_runner` 的 stdio/JSON 协议与 MCP 同构。

**现状判断**：**当前不接是对的选择**。理由：
1. 本项目的价值在**领域工具**（eGMP 对象分析 / 账号），不在"接入尽可能多的工具"；
2. MCP 的收益随工具数与生态规模增长，而本项目工具数在"几十"这个量级、单一领域；
3. MCP 会引入**不可信 server** 这个新信任边界，而现有的 `capabilities` Fail-Closed 设计
   是按"包是我们自己审过的"来设计的。

**做法（真要做时）**：把 MCP server 当作 `installed` 层的一个特殊 `Plugin`，
每个 server 声明所需 `capabilities` → 映射到沙箱策略 + 确认门下限。
`mcp-stdio` 策略已经在那了。

**触发条件**：需要接入的**外部**能力（不只 eGMP）超过 5 个，或需要复用社区 server。

### 7.3 【需先想清楚】多 Agent 与子上下文隔离

**缺口**：无 planner/worker、无子 Agent。

**判断**：**不建议按主流框架的方式补**。理由：
- 本项目是**单用户、单领域、有明确工作流**的场景。多 Agent 框架的收益主要来自
  "任务空间开放、需要动态分工"；这里的流程（五步织网 / 账号登录）本身就是**显式编排**，
  比让模型自己规划更可靠（且更符合 GxP 的"失败可见"）。
  （原有一个显式编排引擎 `orchestrate.run_full_workflow`，已随配置工厂于 2026-09-22 删除。）
- **真正值得补的不是多 Agent，是子上下文隔离**：`insight_run` 五步织网的中间产物很大，
  目前全量进对话上下文。Anthropic 的 Programmatic Tool Calling 解决的正是这个
  （token 降 37%）。而本项目**已经有沙箱了**——让一个工具在沙箱里编排多个下游工具、
  只回最终结果，比重构出多 Agent 划算得多。

**建议**：不引入 planner/worker；评估给 `insight_run` 这类
"多步骤、大中间产物"的工具加一个 `orchestration` 形态（在沙箱里跑编排脚本）。

### 7.4 【低成本】工具调用并行化与工具面裁剪

- **并行**：`loop.py:211` 的 `for call in assistant["tool_calls"]` 改 `asyncio.gather`
  即可（需注意 `safety.consume_step()` 与 `context.append` 的顺序）。
  收益：模型一次发 N 个独立工具调用时延迟从 N× 降到 1×。
  风险：确认门 + 并发的交互需要想清楚（多个风险工具同时被拦时的 pending 展示顺序）。
- **裁剪**：`plugins` 白名单口子已留。可按页面/意图注入子集，进一步压低每轮 schema 成本。
  当前工具数下不必要，但**是接 MCP 之前必须有的能力**（MCP 会让工具数暴涨）。

### 7.5 【中成本】可观测性对齐 OTel —— **2026-09-24 已落地一半**

**缺口**：审计是自建表，不是 OTel GenAI 语义约定。好处是零依赖、可 SQL 查询；
代价是无法直接接入既有可观测后端（Jaeger / Langfuse / Phoenix 等）。

**建议**：不动现有表（它是审计**事实源**，且有合规含义），**额外**导出一层 OTel span：
把 `agent_audit` + `deepseek_usage` + `sandbox_run` 的字段映射到
`gen_ai.*` 语义约定。这样既保住审计，又能接入通用工具。

**落地状态（2026-09-24）**：✅ 已实现，见 [ADR-0021](adr/0019-in-turn-context-and-budget-governance.md)。
- **粒度选择与本节原建议不同**：没有映射 `agent_audit`，而是新增了
  `agent_run` / `agent_span`（**迁移 24**）—— 理由是"一次用户回合"此前在数据里
  **不是一个实体**，而 span 需要那个父子结构（详见该 ADR §2 D-1）。
- 导出层是 `tools/export_otel.py`（OTLP/JSON，**零运行时依赖**，离线跑）。
- ⚠️ **仍差一半**：`gen_ai.*` 的**具体属性名未逐条核实**（见本文档尾部的边界说明），
  当前全部标着 `verify`；另**不做** Langfuse 等生态工具的私有导入格式。

**触发条件**：需要给外部（客户/审计方）看调用链 —— **已触发**。

### 7.6 【低成本，值得现在做】评估集（Evals）

**缺口**：有 `qa_metrics`（记录问答 + 人工点赞/点踩）与 `agent_audit`，但**没有回归评估集**。

**风险**：提示词一改（`prompts.py` 支持用户自定义）、模型一换（`normalize_model` 说明模型名会漂移）、
工具描述一调，**没有任何机制能发现能力退化**。这是当前最容易被忽视的缺口。

**做法**：建一个 `tests/evals/` 目录，放 20–50 条**有确定答案的**任务
（"列出环境 X 下的对象 A 的字段"、"把这次 Monitor 录制解读成一段可读结论"），
跑一遍算通过率与工具选择准确率。不需要复杂框架——`pytest` + 真实 API 调用即可，
用 `-m eval` 标记、不进门禁。

**这条的投入产出比最高**：它是唯一能回答"我改的东西让 agent 变差了吗"的手段。

### 7.7 【需决策】`/api/*` 认证（R1b）

已在 [ADR-0015](adr/0002-browser-allocation-policy.md) §D-7 记录为**有意保留**：
桌面 UI 就是本服务加载的页面，加令牌要么在页面里放令牌（等于没加），要么改引导流程。
**不是遗漏，是权衡**。重新评估的触发器已写在该 ADR §7。

### 7.8 【低优先级】Agent Skills 生态互通

**缺口**：本项目 Skill 包**装不进**任何遵循 Agent Skills 规范的客户端，反之亦然——因为
本项目的 frontmatter 必填 `id` / `version` / `tools`，而规范里没有这些字段，
且规范还要求 `name` **与父目录名一致**（本项目无此约束）。详见 §6.4。

**判断**：**当前不需要做**。这里没有跨生态分发的需求，而"能力包"这个语义是本项目真正需要的
（规范的知识包解决不了"给模型加一个 eGMP 工具"这件事）。

**但有一个数字值得记住**：`anthropics/skills` **177,188 ★** + `obra/superpowers` **288,874 ★**
（均为 2026-09-20 GitHub API 实测）——技能生态的体量已经**超过 langchain（146,694 ★）**。
不参与是主动选择，代价是**永远用不上社区技能，也拿不到生态的网络效应**。

**真要做时的最低成本路径**：只做**读取端兼容**——`manifest.parse_skill_md` 接受规范的 `name`
作为 `id` 的别名、`compatibility` 作为 `capabilities` 的别名（信息性，不授予权限），
`tools` 缺失时把该 Skill 当作"纯知识包"装进……**但这里会碰到一个真问题**：
本项目的正文**不注入上下文**，所以"纯知识包"在本项目里等于没有作用。
所以互通的前提是先实现 progressive disclosure，而那是一个**产品决策**，不是兼容性补丁。

**触发条件**：出现"想用社区现成 Skill"或"想把自己的 Skill 分发出去"的真实需求。

### 7.9 【半天，建议尽快】修掉 `LICENSE` 与 `pyproject.toml` 的许可冲突

**这是一处实测发现的真实不一致**（2026-09-20 复核）：

| 位置 | 声明 |
|---|---|
| `LICENSE`（文件正文） | **Apache License 2.0** |
| `pyproject.toml:7` | `license = { text = "MIT" }` |

**两个都是发布物的一部分**：`LICENSE` 是仓库里给人看的，`pyproject.toml` 会被打进
Python 包的元数据（并出现在 PyPI / 安装器的许可展示里）。**同一个产品声明两个不同的许可，
在法律上是"许可不确定"，在工程上是自动化工具会读错。**

**建议**：以 `LICENSE` 文件为准（Apache-2.0，含专利授权条款，对企业分发更友好），
把 `pyproject.toml` 改成 `license = "Apache-2.0"`（PEP 639 写法）或
`license = { text = "Apache-2.0" }`（旧写法），并跑一次 `bump.py check` 确认五写版本同步没被影响。

> ⚠️ **本文只报告事实，不提供法律意见**。最终选哪个许可应由项目所有者决定；
> 但**两个声明必须一致**，这一点没有争议。

### 7.10 【可选，需权衡】把限损层升级为权限边界

**当前状态**：沙箱的强制项只有 6 条（§3.4），**没有网络强制隔离、没有文件系统写隔离、没有权限降级**。
作者已诚实声明"不是安全边界"（ADR-0014 §D-3）。

**行业做法**（已核实）：OpenAI Codex 的 Windows 沙箱**完全不用 Job Object**，
而是 `elevated`（独立低权限沙箱用户 + 文件系统权限边界 + 防火墙规则 + 本地策略）
与 `unelevated`（**从当前用户派生的受限 Windows 令牌** + ACL 边界 + 环境级离线控制）双模式。

**为什么本项目现在不该做**：`elevated` 需要管理员权限做一次性设置——
这与本项目"工程师自己电脑上零配置、零依赖安装"的核心取向**直接冲突**。
Codex 之所以必须提供 `unelevated` 回退，正是因为企业策略常常禁止提权。

**建议**：**维持限损层定位**，但做两件低成本的事：
1. 把"这个沙箱拦不住什么"写进**用户可见文档**（现在只写在 ADR 与代码文档串里，用户看不到）；
2. 在安装脚本式 Skill 时，明确提示"这个能力会执行你不认识的代码，它**可以读你有权限读的文件、可以联网**"。
   —— 用户做决定需要的是**后果**，不是机制名。

**触发条件**：产品开始面向"用户会装陌生来源 Skill"的场景，或出现合规要求。

---

## 8. 附：规模与度量（2026-09-18 实测）

> 行数一律是**物理行数**（含空行）。用 `Get-Content | Measure-Object -Line` 统计会漏掉空行，
> 得到偏低的数字（本仓已踩过，见 CODEBASE_OVERVIEW 的口径说明）——下表由 Python `splitlines()` 实测。
>
> ⚠️ **2026-09-22 重测**：配置工厂链路整体删除（`plugins/factory.py`、`writers/`、`orchestrate.py`、
> `checkpoint.py`、工厂页/工具/测试）后，下表的行数与文件数**按当日实测更新**；
> **测试那两行没有重跑**（仍是 2026-09-18 的记录），引用前请自行复核。

| 项 | 值 | 取法 |
|---|---|---|
| **Agent 核心**（L1–L7） | `plugin_api` 1,366 + `approvals` 877 + `cache` 308 + `harness/` 884 ≈ **3,435 行** | 物理行数 |
| **沙箱执行层**（L4） | `sandbox/` 6 文件 **1,171 行** | 同上 |
| **技能供给层**（L3） | `skills/` 7 文件 **1,775 行** | 同上 |
| 工具插件实现 | `plugins/` **4 文件 1,420 行**（删掉 `factory.py` 之后） | 同上 |
| 模型接入（L1） | `deepseek.py` **255 行** | — |
| 工具规模 | **4 插件 / 18 core 工具**（**16 read · 2 write · 0 dangerous**） | `python tools/gen_tools_doc.py` |
| HTTP 端点 | **99** | `@router.` 计数 |
| 数据库迁移 | **1–25** | `workbench/db.py::MIGRATIONS` |
| **仓库全量** | `workbench/**.py` **85 文件 20,201 行**；前端 `static` js+css **25 文件 10,039 行**；扩展 TS **25 文件 5,917 行**；测试 **35 文件 10,216 行**；`tools/` **36 文件 6,846 行**（`.py`/`.mjs`/`.ps1`/`.js`）；Electron 壳 `desktop/*.js` **5 文件 1,697 行** | 同上 |
| 测试 | **516 passed / 11 deselected**（默认跳过 slow 层）——**2026-09-18 记录，未重跑** | `pytest -o addopts="" -q -m "not slow"` |
| 打包静态检查 | **13/13** | `tools/verify_packaging.py` |

> **Agent 核心约占全仓 17%**（3,435 / 20,201）。这符合定位：本项目的难点不在 agent 循环本身，
> 而在**它要操作的那个平台**（`services/egmp/` **3,851 行**，写路径删除后）与**让操作安全的那套约束**
> （`sandbox/` + `skills/` + `approvals` 合计 **3,823 行**）。

---

## 参考与核实

本文 §6 的行业事实均经联网核实，核实时间 **2026-09-18**：

- **MCP 现状**：Anthropic《Donating the Model Context Protocol and establishing the Agentic AI Foundation》（2025-12-09）— MCP 捐给 Linux Foundation 下的 AAIF（Anthropic/Block/OpenAI 共同创立，Google/Microsoft/AWS/Cloudflare/Bloomberg 支持）；10,000+ 公开 MCP server；97M+ 月 SDK 下载；ChatGPT/Cursor/Gemini/Copilot/VS Code 已采用；2025-11-25 规范引入异步操作、无状态化、server 身份与官方扩展。AAIF 创始项目还包括 Block 的 goose 与 OpenAI 的 AGENTS.md。<https://www.anthropic.com/news/donating-the-model-context-protocol-and-establishing-of-the-agentic-ai-foundation>
- **工具规模与上下文成本**：Anthropic《Introducing advanced tool use on the Claude Developer Platform》（2025-11-24）— 5 server / 58 工具 ≈ 55K token；内部见过 134K token 的工具定义；Tool Search Tool（`defer_loading`）省 85% 上下文，Opus 4 准确率 49%→74%；Programmatic Tool Calling token 降 37%、消除 19+ 次推理往返；Tool Use Examples 把复杂参数准确率从 72% 提到 90%；**Tool Search 的适用阈值是"工具定义 >10K token 或 10+ 工具"**。<https://www.anthropic.com/engineering/advanced-tool-use>
- **审批原语不是安全边界**：Arcjet《needsApproval and LangGraph interrupt() are not a security policy》— 该文主张框架提供的审批/中断原语属于 UX 层，不应被当作安全策略；安全必须由执行侧的强制机制承担。（**仅核实到标题与论点，正文未完整读取**，本文引用时按此限定表述。）<https://arcjet.com/learn/needsapproval-and-interrupt-are-not-a-policy>
- **Agent Skills 规范**：<https://agentskills.io/specification> — 已实读规范页并逐条比对目录结构与 frontmatter 字段：
  目录为 `SKILL.md` + `scripts/` + `references/` + `assets/`；必填字段只有 `name`（≤64 字符，**仅小写字母/数字/连字符**）
  与 `description`（≤1024 字符），可选 `license` / `compatibility`（≤500 字符，环境要求）/ `metadata` / `allowed-tools`（实验）；
  规范以 **progressive disclosure**（按需加载正文）为核心机制。**规范中没有 `id` / `version` / `tools` 字段**——
  §6.4 的"字段不兼容"判定基于此。
- **Agent Skills 的落地实现（跨厂商印证）**：Microsoft Agent Framework 的 Skills 文档
  （<https://learn.microsoft.com/en-us/agent-framework/agents/skills>，由子代理实读并回报）确认了三件事：
  ① 技能工具为 `load_skill` / `read_skill_resource` / `run_skill_script` 三个，且**仅在技能带资源/脚本时才注册**；
  ② 三者**默认全部需要审批**，靠 `ToolApprovalMiddleware` 的 `auto_approval_rules` 放行，
  其中 `ReadOnlyToolsAutoApprovalRule` 自动批准前两个、**不批准 `run_skill_script`**；
  ③ archive 准入参数为 `ArchiveMaxFileCount`(20) / `ArchiveMaxSizeBytes`(1 MB) / `ArchiveMaxUncompressedSizeBytes`(1 MB)，
  并支持 `digest`（`sha256:` + 64 位小写十六进制，**解压前校验**）。
  官方 `SubprocessScriptRunner` **自标为"仅供演示"**，要求生产环境自行补上沙箱、资源限制、输入校验与审计。
  规范亦已在 CrewAI 与 Cline 中落地。⇒ **"SKILL.md + 脚本"是 2026 年的行业标准，不是本项目的自创。**
- **Windows 上的代码执行隔离（行业做法）**：OpenAI Codex 官方文档《Building a safe and effective sandbox
  for Codex on Windows》（<https://openai.com/index/building-codex-windows-sandbox/>，正文页在本次调研中返回 403；
  机制描述取自可访问的官方文档镜像 `learn.chatgpt.com/docs/windows/windows-sandbox`，由子代理实读回报）：
  分 `elevated`（独立低权限沙箱用户 + 文件系统权限边界 + 防火墙规则 + 本地策略）与
  `unelevated`（**受限 Windows 令牌** + ACL 边界 + 环境级离线控制，官方自述 "weaker than elevated"）两模式，
  另有私有桌面做 UI 隔离；**方案中不含 Job Object**。
  ⇒ **Job Object 是进程树生命周期手段，不是权限边界**——这一条是 §6.2 与 §7.10 的判断基础。
  ⚠️ 该文档的**存在性与要点已由子代理核实，但主代理未能直接读取原文**，引用时按此限定。
- **MCP 的风险元数据与 HITL 层级**：MCP 的 `ToolAnnotations`（`readOnlyHint` 默认 false、
  `destructiveHint` 默认 **true**、`idempotentHint`、`openWorldHint`）是**提示**——
  规范明确指出客户端**不应基于不可信服务端提供的 annotations 做工具使用决策**；
  人工介入在规范层面只到 "SHOULD always be a human in the loop"。
  ⇒ 本项目把 `Risk` 做进执行器（无令牌不调 handler）**在安全语义上强于 MCP 的提示层**。
  ⚠️ 本节结论来自子代理对 MCP 规范页（2025-06-18 与 draft）的实读回报，**主代理未逐字复核规范全文**。
- **OTel GenAI 语义约定**：用于 span/trace 的 `gen_ai.*` 属性约定存在并被生态工具（Traceloop、Splunk 等）采用；本文 §7.5 的建议基于此。**具体属性名未逐条核实**，落地前需查 OpenTelemetry 官方语义约定文档。
  > **2026-09-24 更新（仍未核实，但已变成一条可执行的检查）**：[ADR-0021](adr/0019-in-turn-context-and-budget-governance.md)
  > 落地了导出层，映射**集中在一张表**里 —— `tools/export_otel.py` 的 `OTEL_ATTRS`，
  > 每条带一个核实状态（`ok` / `verify`）。核对只需一条命令：
  > `python tools\export_otel.py --list-attributes`。
  > **写那份映射时网络不可达**（`github.com` / `raw.githubusercontent.com` 均 DNS 失败），
  > 所以 11 条**全部标着 `verify`** —— 它们是按记忆写下的候选名，**语义方向可信、拼写未核**。
  > ★ 这条从"一句空泛的提醒"变成了"一条命令 + 一张表"：
  > **`verify` 不许因为"看起来对"就改成 `ok`**（`tests/test_agent_spans.py` 有一条断言守着它）。
- **star 数**：`anthropics/skills` 177,188 与 `obra/superpowers` 288,874 为 **2026-09-20 GitHub REST API 直接实测**
  （两个渠道独立采集结果一致：主代理走 `api.github.com`，子代理走 `ungh.cc`）。
  `microsoft/playwright-mcp` 37,362 ★ / Apache-2.0、`langchain` 146,694 ★ 等同批实测，完整表见
  `OPENSOURCE-LANDSCAPE.md`（**该文件已于 2026-09-29 删除**） §7。

> ⚠️ **本文中"行业普遍做法"的表述带有判断成分**。凡涉及具体项目/版本/数字的，均在上方列出了来源；
> 凡属工程共识类判断（如"多数项目只用了支持缓存的 API"）则是分析者观点，**未做全样本统计**，读者应自行权衡。
