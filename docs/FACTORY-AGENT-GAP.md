# 配置工厂 × Agent 的接口缺口（问题分析）

_2026-09-20，由用户实测反馈驱动。四条问题逐条取证；每条结论都落在可执行的改动上。_

> **本文档的性质**：**问题分析 + 处置提案**，不是已采纳的决策。
> 拍板后要沉淀成 ADR（`docs/adr/`）与坑（`docs/PITFALLS.md`），本文档届时只作证据链保留。
>
> 四条问题的共同背景：**这个项目刚把「对话 Agent」接成配置工厂的操作者**，
> 而工厂原先是为「人在网页上点按钮」设计的。接口是照着那个场景长的，
> 于是每一条问题都能追溯到同一个根因——**给机器用的入口，缺了一半**。

---

## 0. 一页话结论

| # | 用户报的问题 | 根因（一句话） | 处置 |
|---|---|---|---|
| ① | 可对话轮次非常少 | `max_steps=12` 且**任何地方都改不了**；而每次工具调用（含只读探索）都扣 1 步，耗尽即整轮终止 | 提高 + 可配置 + 耗尽不再是"静默终止"；顺带修掉一个**让步数白烧**的静默缺陷（输出截断不可见） |
| ② | 增加能力不能让 agent 直接理解，需要绕圈子 | **agent 无法暂存蓝图**：唯一入口是网页上的 multipart 文件上传，于是校验工具的返回值直接教模型"让用户把文件拖进页面" | 新增一个工具，把「校验 → 暂存 → 出审阅材料」合成一步，闭环 |
| ③ | "输出校验蓝图"该改名 | 它的名字说"校验"，真实角色是"把将要执行的东西摆给用户看"；而**给用户看的材料生成得太晚**（在 `run_full_workflow` 内部，即审批之后） | 改名 + 把审阅材料前移到预览这一步 |
| ④ | 编排器可以删减 | 现在有**两套**「需求 → 蓝图」：对话 agent 一套，`command="orchestrate"` 里另一个 LLM 循环一套。后者拿不到对话上下文，是前者的降级复制品 | 删掉第二套（LLM 生成路径 + 复杂度评估 + 前端选项）；**保留确定性执行引擎**；把执行逻辑与要求写成 agent 可读的规格 |

**一句话**：问题②是主因，①③④都有一部分是它的后果或它的镜像。

---

## 1. 问题①：可对话轮次非常少

### 1.1 事实（可复核）

| 事实 | 位置 |
|---|---|
| 步数预算是个常量 `12` | `workbench/harness/types.py:21`（`max_steps: int = 12`） |
| 计费口径 = **每一次工具调用扣 1 步**，只读与危险**一样贵** | `workbench/harness/loop.py:251`（`safety.consume_step()` 在 `for call in assistant["tool_calls"]` 里） |
| 耗尽即**整轮终止**，用户只看到一句"已达到步数预算上限，已终止本轮" | `workbench/harness/loop.py:111-118` |
| **这个数字改不了**：`routes_chat` 组装 `AgentConfig` 时只传了 `temperature` 与 `system_prompt` | `workbench/api/routes_chat.py:120-123` |

判据（一行，确认"确实没接线"）——**实测输出：8 处命中全在 `harness/` 内部，路由与前端零命中**：

```powershell
Get-ChildItem -Recurse workbench -Include *.py,*.js |
    Select-String -Pattern 'maxSteps|max_steps' | ForEach-Object { $_.Path + ':' + $_.LineNumber }
# loop.py:99,109,113 · safety.py:9,10,17 · types.py:21,33 —— 没有任何一处来自设置或路由
```

### 1.2 一次配置工厂任务今天要花多少步

按**今天**的工具面（`workbench/plugins/factory.py`），最省的一条路：

| 步骤 | 工具调用数 |
|---|---|
| 找到写哪个环境的账号 | 1（`insight_accounts` 或 `accounts_list`） |
| 写蓝图 → 校验 → 改 → 再校验 … | **1 ~ N**（每轮失败都是一次调用 + 一次完整重贴蓝图） |
| （人肉：切到配置工厂页面，拖入文件，拿 blueprint_id） | **0 步，但流程中断** |
| 确认暂存成功 | 1（`factory_list_blueprints`） |
| 真机创建 | 1（`factory_create`，过确认门） |
| 看结果 / 复跑 | 0 ~ 2（`factory_status`） |
| **合计** | **4 ~ 8 步（顺利）／ 12+ 步（多对象、校验反复）** |

⇒ **12 步的预算对一个"稍微长一点"的任务是必然不够的**：多对象蓝图（含生命周期与工作流）
几乎必然撞上限，而撞上限的表现是**整轮被砍掉**，不是"再走一轮"。

### 1.3 三层原因（按影响排序）

**① 预算本身太小，且不可调。**
`12` 这个数字来自单循环 Agent 的通用默认值（`safety.py:9`），它假设的是"问答 + 偶尔查一次"。
配置工厂是**多步、有重试、有依赖关系**的作业，量级不同。而且用户连调大的手段都没有。

**② 每一步都比它应该的贵（问题②的后果）。**
因为 agent 不能暂存，一次校验失败后的重试必须**在工具参数里重新贴一整份蓝图**——
这既费输出 token，也意味着"小改一处"要重发全部内容。闭环补上之后，重试的代价才有意义。

**③ 有一段步数是被"看不见的失败"烧掉的（独立缺陷）。**
`workbench/services/deepseek.py:159-191` 的流式解析**丢掉了 `finish_reason`**。
当输出因为长度上限被截断时（`finish_reason == "length"`），一个正在生成的 tool_call
会带着**半截 JSON**交给 `_validate` → `BAD_ARGS: 参数解析失败`。
模型看到的是"参数解析失败"，**完全不知道是自己的输出被截断了**，于是最可能的反应是重试一次
——又烧一步，而且大概率再截断一次。

> 这条不是猜测：DeepSeek 官方现行文档写明 `max_tokens` 不设置时默认 **非思考模式 8K、
> 思考模式 64K**（[Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)）。
> 本仓**从不发送 `max_tokens`**（`deepseek.py:138-139` 只在显式传入时才带），
> 也就是说我们在赌"默认值一定够大"。一份 3 对象 + 工作流的蓝图很容易到 10K+ token。

### 1.4 处置

| 编号 | 改动 | 判据 |
|---|---|---|
| **D1** | 预算默认值提高 + 在设置页可配（`AgentConfig.max_steps` ← `settings` 键），并在设置页写清"一次回答里允许的工具调用次数" | 改设置 → 下一次对话生效；`GET /api/settings` 能看到该字段 |
| **D2** | 耗尽**不再只是 error**：明确告诉用户"已完成什么、没完成什么、回一句「继续」即可接着做"，并在事件里区分"预算耗尽"与"真错误" | 前端不再把预算耗尽渲染成红色错误 |
| **D3** | `finish_reason` 必须落成显式观测：`length` → 明确报"输出被长度上限截断"，并给出「把这份蓝图拆小 / 分两次」的指引 | 造一个必然超长的调用 → 观测里出现"截断"字样，而不是"参数解析失败" |

> D3 属于"**看不到的失败路径**"，按本仓纪律必须同时进 `docs/PITFALLS.md`（带判据）。

---

## 2. 问题②：agent 无法闭环，必须"绕圈子"

### 2.1 事实（四条里最硬的一条）

**工厂插件一共 5 个工具，没有一个是"把蓝图交出去"的**（`workbench/plugins/factory.py:201-247`）：

| 工具 | 能干什么 |
|---|---|
| `factory_validate_blueprint` | 校验一份 JSON 字符串 |
| `factory_list_blueprints` | 列出**已经**暂存的蓝图 |
| `factory_jobs` / `factory_status` | 查历史/进度 |
| `factory_create` | 按 **blueprint_id** 真机创建 |

**唯一能产生 `blueprint_id` 的地方是网页上的文件上传**：
`POST /api/factory/blueprint`（multipart，`workbench/api/routes_factory.py:33-60`），
而它只被前端页面调用（`workbench/static/js/factory.js:74`）。工具侧没有任何入口。

于是最好的证据就是工具自己的返回值 —— `workbench/plugins/factory.py:85-87`：

```python
"next_step": (
    "校验通过：把蓝图暂存为 blueprint_id 后才能 create"
    "（暂存走配置工厂页面上传，或让用户把文件拖进页面）"
    ...
)
```

**这句话是模型唯一能得到的指引，而它要求模型去求人。** 模型能校验、能执行，
**但不能把校验通过的东西交给执行**——中间那一步必须由人离开对话、切到另一个页面手工完成。
这就是"绕圈子"的字面含义，也解释了为什么"增加能力不能让 agent 直接理解"：
不是模型不懂，是**动词缺失**。

### 2.2 第二个缺口：agent 拿不到蓝图规格

蓝图长什么样，全项目**只有一处**写清楚过——`workbench/services/egmp/generate.py:19-37`
那 36 行 system prompt。而它只被 `command="orchestrate"` 用（见问题④）。

判据（一行，确认"对话 agent 看不到规格"）：

```powershell
Select-String -Path docs\*.md,workbench\plugins\*.py -Pattern "referenceObjectCode|picklistCode" -Encoding UTF8
# docs 里只有 PITFALLS #29 提到 referenceObjectCode（讲的是读路径的坑）
# plugins/ 零命中 —— 蓝图字段名从未出现在任何模型可见的地方
```

⇒ 模型只能靠**试错**去猜字段名与语义，而每次试错 = 1 步（问题①）。
`factory_validate_blueprint` 的错误信息质量其实不错（`writers/blueprint.py:252-352` 逐条给出
路径与原因），但那是**负样本**：它只说"错在哪"，从不说"对的是什么"。

### 2.3 处置

**D4｜新增 `factory_preview_blueprint`（暂名）——把「校验 + 暂存 + 出审阅材料」合成一步。**

| 情形 | 行为 |
|---|---|
| 校验有 `error` | **不暂存**，返回 issues（沿用现有质量）**并附带该路径的规格片段**（自解释） |
| 校验通过 | 写 `RUNTIME_DIR/factory/<id>/blueprint.json`，返回 `blueprint_id` + 人话审阅材料 |

- `risk = read` 的依据：它只写**应用自己的 runtime 工作区**，不碰平台、不碰凭据，且可整体删除。
  这与既有先例同级——`insight_run` 同样在 runtime 工作区落一整套产物，而它是 `read`。
  （**不设 `write`**：暂存本身不是"对环境生效的动作"，真正的闸门是 `factory_create` 的
  `dangerous` 门；让用户为"暂存"点一次确认是纯噪音，还会训练他习惯性点同意。）
- **不新增第二个"暂存"工具**：校验与暂存分开会让 agent 的循环从 N 次变成 2N 次，
  而且要继续回答"我该先调哪个"。合起来之后，"把蓝图交出去"就是一次调用。

**D5｜把蓝图规格做成 agent 可查的东西，而且让它不可能漂移。**

做法：**从运行期的 pydantic 模型 + 实证词典生成**，不手写第二份文档：

| 规格的组成部分 | 来源（唯一事实源） |
|---|---|
| 键名 / 类型 / 必填 / 取值范围 | `writers/blueprint.py` 的 `Blueprint` / `ObjectDef` / `FieldDef` / … 模型本身 |
| `dataType` / `stepType` / `behaviorType` / 派发模式的中文含义 | `kernel/semantics.py`（上游实证词典） |
| 编码约定（`__c` 后缀、`status_*__c`、系统状态不可删） | `writers/blueprint.py:18-20` 的正则与 `semantics.py:76-77` |
| 执行逻辑与要求（8 步为什么是这个顺序、什么必须人工配置） | `orchestrate.py` 的 `PIPELINE_STEPS` 与各步注释（**从代码里读，不另写散文**） |

⇒ 与 `tools/gen_tools_doc.py` 同一纪律：**文档从代码生成，并有机检钉住**。
一份手写的 schema 文档半年后必然与模型漂移，而漂移的表现是"模型照着文档写、校验全红"。

---

## 3. 问题③：`factory_validate_blueprint` 该改名

### 3.1 事实

**名字与角色不符。** 工具名说的是"校验"，但它在流程里的真实位置是
**"把将要执行的东西摆给用户看，让他决定要不要动手"**——校验只是达成这个目的的手段之一。

**更值得改的是时序。** 给用户看的材料其实**已经存在**：
`writers/blueprint.py:422` 的 `generate_review_spec()`（对象/字段/布局/生命周期/工作流的
人话清单）与 `:487` 的 `generate_checklist()`（逐项勾选清单）。但它的全部调用点只有两处，
**没有一处发生在"用户审批之前"**：

| 调用点 | 什么时候跑 | 问题 |
|---|---|---|
| `orchestrate.py:137-138`（在 `run_full_workflow` 内部） | **执行期**——用户已经批准、引擎已经开始建对象了 | 审批时用户手里没有这份东西 |
| `routes_factory.py:159/162`（`orchestrate` 分支） | 只有走"需求→LLM 生成蓝图"那条路才有 | 对话链路（`create`）根本不经过它 |

而 `run_full_workflow` 是**执行**函数。也就是说：走对话链路时，
**spec.md / checklist.md 是在用户已经批准之后才落盘的**——而它们存在的全部目的就是给他审批用。

> 这与问题②是同一个形状的毛病：**能力都在，但接错了位置**。

### 3.2 处置

| 编号 | 改动 |
|---|---|
| **D6** | 改名（候选见 §6，需要拍板）。名字要能回答"这一步是干什么的"：**让用户审，而不是让机器检** |
| **D7** | 审阅材料前移到预览步骤：`generate_review_spec` / `generate_checklist` 在**暂存时**就生成，落盘 **并**随工具返回值给模型一份人话摘要，让模型在对话里直接讲清"将要执行什么" |
| **D7b** | `factory_create` 的确认门摘要升级：现在它走通用 `approvals.summarize()`（"描述 + 参数 + 风险级"），应改为**蓝图要点**（对象数 / 字段数 / 生命周期 / 工作流 / 是否含人工配置项），让弹窗本身就能支撑判断 |

> D7b 与刚做完的 `request_user_confirmation` 是互补的：那个是模型主动问，
> 这个是宿主在危险操作上**必然**要问的那一次——它更应该问得具体。

---

## 4. 问题④：编排器可以删减

### 4.1 事实：现在有两套「需求 → 蓝图」

| | 对话 Agent（新） | `command="orchestrate"`（旧） |
|---|---|---|
| 入口 | 用户说一句话 | `routes_factory.py:143-157` |
| 生成者 | 模型在对话里写蓝图 | `services/egmp/generate.py:40-78` 里**另一个** LLM 调用 |
| 上下文 | 完整对话 + 用户前面说的所有约束 + 工具查到的真实对象结构 | **只有一个 `requirement` 字符串** |
| 反馈 | 用户当场纠正、追问、改需求 | 复杂度评估（`complexity.py` 的确定性关键词规则）→ 生成 → 校验 → **回炉一次** |
| 规格来源 | 工具描述（今天没有，见 D5） | `generate.py:19-37` 的 36 行 prompt |

⇒ 旧的那套是**新那套的降级复制品**：同样的活，更少的输入，更死的流程，外加一个只在这里
存在的复杂度打分器。用户说"分析和编排的操作可以拿给 agent"，指的正是它。

### 4.2 处置

| 编号 | 改动 |
|---|---|
| **D8** | 删除 `orchestrate` 命令与它下面的一切：`generate.py` 的 LLM 生成路径（`generate_blueprint` / `_extract_json` / `format_review_prompt`）、`complexity.py`、`routes_factory.run` 的 `orchestrate` 分支、前端下拉项（`static/pages/factory.html:42`） |
| **D9** | **保留**执行引擎，一个字都不删：8 步管道（`orchestrate.py:29-30`）、拓扑排序（`:74-95`）、idempotency、checkpoint / 断点续跑、verify 回读、`precheck_blueprint`。理由见 §4.3 |
| **D10** | 把执行逻辑与要求变成 agent 可读的规格（并进 D5）：8 步顺序及原因、拓扑排序为什么存在、哪些项引擎不执行（`rules` / `permissions` / `reportTemplates` 是人工配置项）、失败与续跑语义 |

### 4.3 为什么执行引擎一个字都不删（这条要写清楚，容易被"删减"误伤）

问题④说的是"编排器可以删减"。**要删的是"决策"，不是"执行"**，两条判据：

1. **确定性 vs 非确定性**：拓扑排序、幂等查询、断点续跑、回读验证都是**确定性**的——
   同样的输入永远同样的结果，且它们的存在**减少**而不是增加 agent 的负担。
   把"先建选项集再建字段"这种顺序判断交给 agent，等于用一个非确定性组件替换一个确定性组件，
   换来的只是更多的步数与更多的不一致。
2. **不可回滚 vs 可重做**：执行引擎直接改真实平台。它必须留在能被测试、能被审计、
   能被执行侧强制的地方（`writers/` 有黄金值回归）。让 agent "自己编排"真实写入的步骤顺序，
   与 `docs/PITFALLS.md` 反复记的那类事故是同一种形状。

⇒ **删"另一个 LLM 循环"，留"确定性的执行管道"，并把管道的逻辑与要求写给 agent 看。**

---

## 5. 目标形态

### 5.1 改造后的工具面

| 工具 | 干什么 | risk | 备注 |
|---|---|---|---|
| `factory_blueprint_spec`（暂名） | 返回蓝图规格 + 执行逻辑与要求 + 最小示例（**从运行期模型生成**） | read | 一次对话通常只需调一次 |
| `factory_preview_blueprint`（暂名，取代 `factory_validate_blueprint`） | **校验 + 暂存 + 出审阅材料**，返回 `blueprint_id` | read | 失败不暂存；错误自带规格片段 |
| `factory_list_blueprints` | 列出已暂存蓝图（含**用户手工上传**的） | read | 保留 |
| `factory_create` | 真机创建（过 `dangerous` 门） | dangerous | 确认摘要改为蓝图要点（D7b） |
| `factory_status` / `factory_jobs` | 进度与历史 | read | 保留 |

净变化：**21 → 22 个工具**（+1 个新工具，`validate` 被 `preview` 取代而非并存）。

### 5.2 一次任务的步数对比

以「建 1 个对象 + 6 字段 + 生命周期」为例：

| | 今天 | 改造后 |
|---|---|---|
| 找账号 | 1 | 1（或由用户直接给） |
| 写蓝图 → 校验 → 修（假设改 2 轮） | 3 | 3（`preview` 同时完成校验与暂存） |
| 人肉切页面拖文件 | **中断** | **无** |
| 确认暂存 | 1 | 0（`preview` 已返回 id） |
| 真机创建 | 1 | 1 |
| 看结果 | 1 | 1 |
| **合计** | **7 步 + 一次人工跳转** | **6 步，全程无人工跳转** |

多对象场景差距更大：今天每多一个对象就多一轮"贴全文 → 校验 → 改"，而改造后
**至少不再有"攒够了再去页面传一次"的中断点**。

> 步数并没有戏剧性下降——**真正的收益是"闭环"与"不再中断"**。
> 预算（D1）解决"够不够"，闭环（D4）解决"走不走得通"。两者缺一不可，
> 但**闭环优先**：预算再大，缺了那个动词也还是走不通。

---

## 6. 需要拍板的决策

| # | 问题 | 候选 | 建议 |
|---|---|---|---|
| A | 改名成什么？ | ① `factory_preview_blueprint`（预览蓝图）② `factory_review_blueprint`（审阅蓝图）③ `factory_stage_blueprint`（暂存蓝图）④ `factory_plan_create`（创建预案） | **①**：`preview` 同时表达了"看"与"还没生效"，与它的 `read` 分级一致；②偏"人工评审流程"，③偏"内部实现"，④英文语义偏"计划" |
| B | 步数预算默认调到多少？ | ① 30 ② 40 ③ 60 ④ 保持 12、只做可配置 | **② 40**：一次多对象配置工厂任务实测量级在 6~20 步，40 留一倍余量；再高则"跑飞"的代价开始显著（每步都是一次 LLM 往返） |
| C | `orchestrate` 命令**直接删除**还是先标废弃？ | ① 直接删（含前端选项）② 保留但只回一句"请改用对话" ③ 保留 | **①**：本仓「不留两套会漂的事实源」是既有纪律（两张敏感表 / 两套工具表都栽过）；留着第二个 LLM 循环，早晚有人用它 |
| D | 规格与审阅材料返回给模型的体量 | ① 规格全量（含示例）② 规格分节，模型按需取 ③ 只给键名与约定，示例另取 | **①**：一次性全量约 1.5~2K token，只在需要时调用一次，比"试错 5 次"便宜得多 |
| E | 这次做几件？ | ① 只做 ①②（闭环 + 预算）② 做 ①②③ ③ 四件全做 | **③**：③④ 的改动面与 ② 高度重叠（同一个工具、同一批文件），分两次做反而要动两遍同样的地方 |

---

## 7. 不做 / 风险

**不做**：

- **不把执行引擎交给 agent**（§4.3）。
- **不给"暂存"加确认门**（§2.3）——那会把确认门用废，见 `docs/TOOLS.md` 里
  `read_local_file` 的同类取舍。
- **不扩大系统提示词**。规格走工具（D5），不走 prompt：prompt 是每个请求的第一个字节，
  塞 2K token 的 schema 进去等于每轮都付它的钱，而绝大多数轮次用不到。
- **不新增"分步建蓝图"的工具族**（add_object / add_field / …）。它会让模型面对
  "先建哪个、什么时候算完"的新负担，而蓝图本来就是**一次性可校验的整体**。

**风险**：

| 风险 | 说明 | 缓解 |
|---|---|---|
| 预算调大后"跑飞"更贵 | 每一步都是一次 LLM 往返 + 可能的工具副作用 | 危险操作仍过确认门；`is_loop` 仍在；D2 让耗尽可见 |
| 生成的规格与模型漂移 | 手写文档必然漂 | **规格从运行期模型生成**（D5），并加机检断言"规格里的键名 == 模型字段名" |
| 删 `orchestrate` 影响既有用户习惯 | 配置工厂页面上那个选项会消失 | 在页面与 `CHANGELOG` 里写明"改用对话"，并保留 `blueprint.json` 的手工上传（那条路不动） |
| `preview` 的 `read` 分级被质疑 | 它确实写了磁盘 | 判据写进 `ToolSpec` 文档串与 ADR：只写自有 runtime 工作区、可整体删除、不碰平台与凭据，与 `insight_run` 同级 |

---

## 附：本次核查用到的判据命令（都实跑过，输出已核对）

```powershell
# ① 预算不可配置 —— 实测 8 处命中全在 harness/ 内部
Get-ChildItem -Recurse workbench -Include *.py,*.js |
    Select-String -Pattern 'maxSteps|max_steps' | ForEach-Object { $_.Path + ':' + $_.LineNumber }

# ② agent 无法暂存蓝图 —— 实测只有 5 个工具，且都是"校验/列举/查询/创建"
Select-String -Path workbench\plugins\factory.py -Pattern 'name="factory'
Select-String -Path workbench\api\routes_factory.py -Pattern 'async def upload_blueprint'

# ③ 审阅材料的调用点 —— 实测 10 处，两处调用点都在"执行期"或"orchestrate 分支"
Get-ChildItem -Recurse workbench -Include *.py |
    Select-String -Pattern 'generate_review_spec|generate_checklist'

# ④ 第二套「需求→蓝图」还在 —— 实测 routes_factory 13 处 + 前端下拉项 1 处
Select-String -Path workbench\api\routes_factory.py,workbench\static\pages\factory.html -Pattern 'orchestrate'

# ⑤ 输出截断不可见 —— 实测 finish_reason 在 deepseek.py 里零命中
(Select-String -Path workbench\services\deepseek.py -Pattern 'finish_reason' | Measure-Object).Count

# ⑥ 蓝图字段名从未出现在模型可见处 —— 实测 plugins/ 零命中
(Get-ChildItem workbench\plugins\*.py | Select-String -Pattern 'referenceObjectCode|picklistCode' | Measure-Object).Count
```
