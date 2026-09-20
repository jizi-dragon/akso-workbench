# ADR-0016：模型可以主动"先问再做"，但它不是安全边界

- **状态**：已采纳
- **日期**：2026-09-20
- **决策人**：用户定稿（工具形态选 B、交互形态选"循环内等待"）
- **影响面**：新增工具 `request_user_confirmation`；`plugin_api`（`ToolSpec.needs_confirmation` / `confirm_payload`、`ToolStatus.NEEDS_CONFIRMATION`、`ToolHub.set_confirmation_gate/set_confirmation_waiter`）；`approvals`（`request_confirmation` / `wait_decision` / 等待者）；`harness/loop.py` 事件契约（+`confirm_required` / `confirm_resolved`）；`approvals.PendingStore` 与**风险门共用一张表**
- **取代**：无（与 [ADR-0007](0007-credentials-never-plaintext.md) 同族但不同面；与 `Risk` 确认门**互补而非替代**）

---

## 1. 背景：为什么现在需要这个决策

`Risk`（read / write / dangerous）解决的是**宿主**这一侧的问题：*"这一次调用要不要先经过人"*。
它的权威在 `ToolHub.execute` 的硬闸门里，模型绕不过去——这是本仓安全模型的地基
（AGENTS.md 规则 9）。

但它有一个**结构性盲区**：它只能对"**已经发起的调用**"表态。以下三种情形它无能为力：

1. **模型想做的事，落在某个不受门控的工具上**。例如它打算先读一圈平台元数据、
   再决定要不要改——读是 `read`，不过门；但"要不要按这个思路往下走"是个值得问人的问题。
2. **问了比做了再被拦更省事**。用户看到的顺序是"模型要执行 X（被拦）→ 放行/拒绝"。
   如果模型能先问一句"我打算做 X，可以吗"，用户的判断发生在**调用被构造出来之前**，
   而且不会白白多一个待确认条目、多一次重试。
3. **不可逆但风险分级说不清的操作**。分级是**声明**，声明是粗的；有些动作（提交表单、
   发布、批量通知）在参数上看起来人畜无害，但业务后果很重。模型自己知道它打算干什么，
   分级表不知道。

于是需要一个**模型可以主动调用**的确认工具。用户的原始需求就是这么写的：
"当 Agent 准备执行修改权限、提交表单等敏感、高风险或不可逆操作前，调用该工具向用户弹出确认框"。

### 不决策的代价

模型只能"先做再被拦"或"直接做"。前者浪费一轮往返且用户的判断缺少上下文；
后者在分级表的盲区里**没有任何东西挡着**。

## 2. 决策

### D-1 它是**模型发起**的，因此**不是**安全边界

| | 谁发起 | 拦的是什么 | 权威在 |
|---|---|---|---|
| `risk >= write`（既有） | **宿主**，按声明自动拦 | 这一次**调用** | 宿主，模型无法绕过 |
| `needs_confirmation`（本决策） | **模型**，主动调工具 | 模型**打算做的事** | 模型的自律 + 用户当场判断 |

**两个方向的误读都要防死**：

- 不能把它当高风险调用的放行依据——**模型不调用它就绕过去了**。
- 也不能因为它存在就削弱 `Risk` 门——**两者同时生效**：模型先用确认工具问过一次，
  真正执行那次写操作时**风险门照样会再拦一次**（除非用户同意时按参数指纹预先批准了那一次，
  见 D-3）。

这条在代码里不是靠文档，是靠**校验**：`ToolSpec.valid()` 在装配期断言
`needs_confirmation=True` 的工具必须是 `Risk.READ`（否则"确认工具自己先过确认门"= 无限递归），
且 `target_tool` **必须是 write/dangerous 级**（只读目标一律拒——预先批准一次只读调用
只会留下一条永远不会被认领的行，属于"用户以为批准了、其实什么也没发生"）。

### D-2 **暂停发生在循环层，不在工具处理函数里**

工具 handler **无法**在 `execute()` 中途把事件发给界面：

```
run_agent_loop:  yield action
                 await registry.execute(...)   ← 异步生成器挂起在这里
                 yield observation
```

`execute` 一旦 `await`，生成器就挂起了，`confirm_required` 事件发不出去，界面不知道有东西要确认，
用户也就永远点不了。**这是本决策最硬的技术约束**，也是"工具 handler 里阻塞等待"这个
看起来最直观的做法在此架构下不可行的原因。

所以分两步：

1. `ToolHub.execute` **只登记**（建 `pending_action` 行 + 一个 `Future`），立刻返回
   `ToolStatus.NEEDS_CONFIRMATION`，把规范化后的确认内容放进 `Invocation.confirm`。
2. `harness/loop.py` 把 `confirm_required` **yield 出去**（SSE 落盘、界面渲染卡片），
   **然后**才 `await registry.await_confirmation(...)`。答复回来后 yield `confirm_resolved`，
   并把答复当作这次工具调用的**终态观测**回填给模型。

**不需要额外一轮模型调用**：答复就是那一次工具调用的结果。

> ⚠️ `request_user_confirmation` 的 handler **永远不会被调用**（`_apply_confirmation` 直接替换
> invocation）。留着它只是为了满足 `ToolSpec.handler` 必填；真被执行到，它**抛异常**——
> 返回一句"已确认"会让模型以为用户真的同意了，而那正是本工具唯一要防的事。

### D-3 同意的是**一次具体调用**（参数指纹），不是"模型的意图"

`confirm_payload` 可以带 `targetTool` + `targetArgs`。填了它：

- 待确认条目挂在**目标工具**头上（`tool=目标工具`、指纹 = 目标参数指纹），
  于是用户同意后模型直接调用目标工具时，走的是与风险门**完全相同**的
  `PendingStore.claim_approved()` 通道——按「工具 + 参数指纹」认领、**一次性**、5 分钟内有效。
- **模型换个参数再调，指纹对不上，照样重新走一遍风险门。**
  回归断言：`test_confirming_one_call_does_not_authorize_another`。
- 不带 `targetTool` 的纯意图确认**不产生任何可认领的批准**（那条行是只读工具名下的，
  永远走不到 `claim_approved`）。回归断言：`test_intent_only_confirmation_authorizes_nothing`。

**安全性没有放松**：实时确认与风险门共用同一张表、同一套指纹、同一个一次性语义，
只是"等待者"不同（一个是没人等，下次重试来认领；一个是对话循环当场等）。

### D-4 Fail-Closed 全覆盖

| 情形 | 行为 |
|---|---|
| 宿主没接确认通道（`set_confirmation_gate` 未调用） | `ToolStatus.ERROR`，内容明说"**没有**向用户问过任何问题，不要当成已获同意" |
| 登记了但没人能等（`set_confirmation_waiter` 未调用） | 按**未同意**返回 |
| `targetTool` 不存在 / 是只读 / 自己也要确认 | `BAD_ARGS`，**不降级**成纯意图确认（降级 = 用户以为批准了 A，实际什么都没批准） |
| `targetArgs` 不合法 | 用**目标自己的** `args_model` 校验，`BAD_ARGS` + 具体字段错误 |
| 用户超时未答复 | 条目置 `expired`，按**未同意**返回 |
| 对话被中断（用户关页面 / SSE 断开） | 条目置 `expired`（否则它停在 `pending`，用户回来随手一点就成了事后通行证） |
| HTTP 面（`POST /api/agent/invoke`） | 409 + `confirm` 内容，**不替用户等**（一次 HTTP 请求挂 5 分钟既会被网关掐断，也让"谁在等"变含糊） |

### D-5 确认的**权威答复**是服务端事件，不是前端的那次点击

界面点击只是提交；卡片的终态由 `confirm_resolved` 事件决定。这条让"用户点了同意但服务端判定超时"
这类竞态有一个明确的、可测的收口。

## 3. 本期做与不做

**做**：

- `needs_confirmation` + `confirm_payload` 作为 SDK 的一等声明（装配期自检）。
- 循环内等待 + 两个新 SSE 事件（`confirm_required` / `confirm_resolved`）。
- 与风险门共用的 `claim_approved` 认领通道（预先批准一次具体调用）。
- `confirm_payload` 可选，**缺省回落 `description`**：宁可问得含糊，也不要因为少填一个字段就拒掉
  ——"问不出口"的后果是模型转向不做确认，那比问得含糊危险得多。
- HTTP 面的 409 语义。
- 确认卡（天蓝）与风险门卡（琥珀）**视觉与文案都分开**，因为"点完之后要不要再发一条消息"是相反的。

**不做（明确）**：

- ~~"始终同意这个工具"（sticky approval）~~：本项目没有"记住这个决定"的能力，
  而且与"权威状态在内存、跨重启失效"的既有原则冲突（见 `approvals.py` 模块文档）。
  一次性 + 指纹绑定是刻意的。
- ~~用确认工具去批准另一次确认~~（嵌套）：`targetTool` 指向另一个 `needs_confirmation` 工具一律拒。
- ~~在工具 handler 里阻塞等待~~：见 D-2，架构上做不到，且会让事件发不出去。
- ~~把答案做成自由文本~~：本期只有同意 / 拒绝 / 超时三态（与 MCP elicitation 的三态模型对齐，
  见 §6）。要收结构化输入是另一件事，不在本期。

## 4. 后果

**好处**：

- 模型能在"做"之前问，用户判断发生在调用被构造出来之前，且不多花一轮往返。
- 确认的语义可机检：`needs_confirmation` 是声明，装配期强制；目标工具与参数在**宿主**侧校验，
  插件说了不算。
- 与风险门复用同一套指纹/一次性语义 → 没有第二套安全逻辑要维护、要审计。

**代价**：

- 对话循环里出现了**唯一一处长阻塞**（最长 30 分钟上限、默认 5 分钟）。等待期间该会话的
  SSE 连接被占住，`max_steps` 预算不推进。这是有意的：模型此刻**应该**停住。
- `PendingStore` 现在服务两种语义（有人等 / 没人等），多了一组 `_waiters` / `_decisions`
  状态要维护——两处真实的时序竞态就是从这里来的（PITFALLS #57）。
- 事件契约多两个类型，前端多一个渲染分支。

**风险**：

- **模型滥用**：把每个动作都拿去问一遍会让人习惯性点"同意"，反而把确认门用废。
  缓解：工具描述里写清适用场景（不可逆 / 影响真实平台 / 批量），并在描述里点明
  "write/dangerous 工具自己仍会走风险门，两者互补"。
- **等待卡住会话**：`timeoutS` 有上限 1800s；超时必标 `expired`，不留事后通行证。

## 5. 诚实边界

**这个工具买的是"模型可以先问再做"，不是"危险操作一定会被问"。**

模型完全可以不调用它，直接去调那个危险工具——那时拦住它的是 `Risk` 门，不是本工具。
所以：

- **不要**在任何文档、界面文案或提示词里把它描述成"高风险操作的批准环节"。
- **不要**因为接入了它而放宽任何 `Risk` 分级。
- **不要**把它当作审计意义上的"用户授权记录"——权威在 `pending_action` 表，而那张表的作用是
  留痕，不是授权凭证；授权凭证是"内存里那条一次性、指纹绑定、5 分钟内有效的批准"。
- 它的**等待上限**是真实的：用户不在电脑前，确认就是会超时，而超时等于拒绝。
  这是刻意的方向（fail-closed），代价是"模型问了一句没人应，于是什么也没做"。

## 6. 外部参照

| 参照对象 | 它的做法 | 与本决策的关系 | 我们的理由 |
|---|---|---|---|
| [MCP 规范 · Elicitation](https://modelcontextprotocol.io/specification/2025-06-18/client/elicitation)（2025-06-18） | server 可发 `elicitation/create` 向用户要结构化输入，**嵌套在其它 server 功能内部**；响应是三态 `accept` / `decline` / **`cancel`**；客户端**必须**在初始化时声明 `elicitation` capability；规范明确 "Servers **MUST NOT** use elicitation to request sensitive information"，并要求界面"说清是**谁**在问、为什么问" | **一致（跟随）** | ① "嵌套在一次调用内部、答复后继续"与我们 D-2 的形状一致——这条**行业已经统一**，不必自己发明；② 我们也用三态：同意 / 拒绝 / **超时（= cancel）**，并把"用户明确拒绝"与"用户没看见"在观测里区分开（前者模型不该重试，后者可以重问）；③ capability 缺失就不问 → 我们的 `set_confirmation_gate(None)` 一律 fail-closed 是同一件事；④ 界面显示"目标工具 + 风险徽章"对应它"说清谁在问" |
| 同上 | elicitation 的**用途是"取数据"**，规范没有"批准一次动作"的语义（在 MCP 里"要不要跑这个工具"由 **host** 决定） | **分歧（真权衡）** | 我们要的恰恰是"批准一次动作"。MCP 不这么做是因为它的 host 本来就能拦下任何工具调用；而我们的盲区正是"host 分级表覆盖不到、但模型自己知道该问"的那一块（§1）。**这个分歧是语境差异，不是我们更聪明** |
| 同上 | `requestedSchema` 只允许**扁平原始类型**（string/number/boolean/enum），刻意不支持嵌套对象 | **分歧** | 我们直接拿**目标工具自己的 `args_model`** 当 schema——它已经存在、已经过校验、与真实调用**同一份定义**，再造一套扁平表单等于造第二个事实源 |
| [OpenAI Agents SDK · Human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/) | `needs_approval` 是**工具声明**，由 **runner** 求值；命中就**暂停整个 run**，`RunResult.interruptions` 里带 `ToolApprovalItem`（含 `tool_name`/`arguments`）；宿主 `state.approve(...)`/`state.reject(...)` 后**`Runner.run(agent, state)` 恢复同一次 run**，从暂停处继续 | **一致（跟随，且是对 D-2 的最强背书）** | 它把"暂停/恢复"放在 **runner（= 我们的 `run_agent_loop`）**、而不是工具实现里，和我们的 D-2 完全同构。**"在工具 handler 里等"在这个行业里不是主流做法**，我们的架构约束（异步生成器）与之一致，不是将就 |
| 同上 | 批准**按 call ID 绑定**（"Per-call approvals are scoped to the specific call ID"）；要跨调用复用必须**显式** `always_approve=True`，且 sticky 决定要能序列化进 `RunState` | **一致（跟随）** | 我们的指纹绑定是同一件事的等价物（我们没有 call ID 作为稳定键——模型重试时 call ID 会变，而**参数指纹**不会）。跟随它"默认一次性、复用必须显式"的方向；但我们**不提供** `always_approve` 的等价物（见 §3 不做） |
| 同上 | 拒绝文案有两层可定制（run 级 `tool_error_formatter` + 单次 `rejection_message`），默认也会给模型一段标准拒绝文本 | **一致（跟随）** | 我们把用户填的拒绝理由原样回给模型，并明确写"不要重试、也不要换个工具绕过去"（`_answer()`）；这对应它 `rejection_message` 的用途 |
| 同上 | **可调用式**批准规则在参数无法安全检视时**一律要求人工批准**（args 缺失/空白/非法 JSON/非对象/含 `NaN` 等） | **一致（跟随）** | 我们更严：无法检视就**拒**而不是"转人工"——因为我们的确认工具有 `targetTool` 预批准能力，含糊的 target 一旦被当成"纯意图确认"，用户会以为批准了某次调用而实际什么都没批准（D-4） |

**由本次对照得出的结论**：

- **可以简化的**：暂停/恢复的位置（循环层）、三态答复模型、"capability 缺失就不问"、
  "按那次具体调用绑定、默认一次性"——这四条行业已经统一，**直接跟随，不必自己论证**。
  本次决策真正需要自己定的只剩"确认工具的**目标**是批准动作而不是取数据"这一条。
- **必须自己定的**：① 模型可主动发起（行业都把批准权放在 host，我们**额外**给模型一个问的入口，
  且明确声明它不是安全边界）；② 目标 schema 直接复用 `args_model`；
  ③ 不提供 sticky approval（与"权威在内存、跨重启失效"的既有决策一致）。

## 7. 重新评估的触发器

| 触发器 | 到时再考虑 |
|---|---|
| 我们接 **MCP**（[CORE-ARCHITECTURE](../CORE-ARCHITECTURE.md) §7.2 已留口子） | 用标准的 `elicitation/create` 取代本工具，或让本工具在 MCP server 内部转译为 elicitation；届时 D-1 的"不是安全边界"结论仍要保留 |
| 出现"用户抱怨每次都要重新点一遍同一个确认" | 是否引入 **sticky approval**（`always_approve` 等价物）。引入时必须同时决定它**存哪、活多久、怎么失效**——按现有原则它不该跨进程重启存活 |
| 出现"模型绕开确认工具直接做"的实际事故 | 说明"模型自律"这一档不够；届时该改的是 **`Risk` 分级或提示词**，**不是**把本工具升级成强制门（那会造出第二个安全权威） |
| 对话循环引入**并行工具调用**（CORE-ARCHITECTURE §7.4） | 多个确认并发挂起时的展示顺序、以及 `_waiters` / `_decisions` 的并发正确性要重新审 |
| 观察日志发现 `needs_confirmation` 的**超时率很高** | 默认 `timeoutS`（300s）是否合适；或改为"超时不置 expired、而是留给下一次显式确认"（那会改变 fail-closed 方向，必须重开一次决策） |
