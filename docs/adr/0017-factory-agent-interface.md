# ADR-0017：配置工厂的"执行"留给引擎，"编排"交给对话 Agent

- **状态**：已采纳
- **日期**：2026-09-20
- **决策人**：用户定稿（四项问题逐条拍板，见 §1 的证据表）
- **影响面**：`plugins/factory.py`（工具面 5 → 6）、`services/egmp/writers/spec.py`（新）、
  `services/egmp/{generate,complexity}.py`（**删除**）、`api/routes_factory.py`（`orchestrate` 命令下线）、
  `harness/{types,safety,loop}.py` + `services/{deepseek,settings}.py`（步数预算与截断可见性）、
  `approvals.py` + `plugin_api.py`（`summary_fn`）、设置页与配置工厂页
- **取代**：无。但它**推翻了 [ADR-0005](0005-native-internalization-of-four-projects.md) 里
  "蓝图生成由 mogul 的 DeepSeek 客户端按结构化提示词承担"这一条的**前提**——
  那条前提是"生成蓝图的只有那个一次性调用"，而现在**对话 Agent 自己就是生成者**。

---

## 1. 背景：四条实测问题与它们的共同根因

用户在用真机跑配置工厂后，报了四条问题。逐条取证的过程见
[FACTORY-AGENT-GAP.md](../FACTORY-AGENT-GAP.md)（含可复核的判据命令与实测输出），这里只放结论：

| # | 现象 | 取证到的根因 |
|---|---|---|
| ① | 可对话轮次非常少 | `max_steps=12` 且**任何地方都改不了**（`routes_chat` 组装 `AgentConfig` 时只传 temperature 与 system_prompt）；而每次工具调用（含只读探索）都扣 1 步，耗尽即**整轮终止** |
| ② | 增加能力不能让 agent 直接理解，需要绕圈子 | **agent 无法暂存蓝图**：全插件 5 个工具里没有一个能产生 `blueprint_id`，唯一入口是网页上的 multipart 上传。于是校验工具的 `next_step` 只能写"让用户把文件拖进页面" |
| ③ | "输出校验蓝图"该改名 | 名字描述的是**手段**（校验），真实角色是**把将要执行的东西摆给用户看**；而给用户看的材料（`generate_review_spec`）**只在执行期生成**——用户审批时手里没有它 |
| ④ | 编排器可以删减 | 存在**两套**「需求 → 蓝图」：对话 Agent 一套，`command="orchestrate"` 里另一个 LLM 循环一套（输入只有一个 `requirement` 字符串） |

**共同根因**：这条能力链是**照着"人在网页上点按钮"设计的**。接上对话 Agent 之后只补了
"读"与"写"两端，中间的"交出产物"没人补（②）；而原有的"生成"能力因为是为无人对话的场景写的，
变成了对话链路的降级复制品（④）。

另有一条**独立缺陷**在同一轮排查中暴露：`DeepSeekClient.stream_chat` 丢弃 `finish_reason`，
于是"输出被长度上限截断"在整条链路上不可见，被翻译成"参数解析失败"，模型据此原样重试
（PITFALLS #58）。

## 2. 决策

### D-1 删掉第二个「需求 → 蓝图」循环，而不是废弃它

`command="orchestrate"`（复杂度评估 → LLM 生成 → 规范化 → 校验 → 审阅三件套）连同
`services/egmp/generate.py`、`services/egmp/complexity.py` **一并删除**。

理由不是"它不好"，是**它比对话链路弱**：它的输入只有一个 `requirement` 字符串——
拿不到对话上下文、拿不到用户前面说的约束、也拿不到 `insight_read_config` 查到的真实对象结构。
留着它意味着**同一个问题有两个会各自漂的实现**，而本仓对"两套会漂的事实源"已经栽过两次
（两张敏感路径表、两套工具表）。

**手工上传 `blueprint.json` 那条路保留**（`POST /api/factory/blueprint`）：用户手里已经有文件时，
不该被迫让模型把内容抄一遍。它是"人给物"的入口，不是"另一个 orchestrator"。

### D-2 执行引擎一个字都不删

`orchestrate.run_full_workflow` 的 8 步管道、`topological_sort_objects`、idempotency、
checkpoint / 断点续跑、verify 回读、`precheck_blueprint` **全部保留**。

**判据（两条，缺一不可）**：

1. **确定性 vs 非确定性**。拓扑排序、幂等查询、断点续跑、回读验证都是确定性的——同样的输入永远
   同样的结果，而且它们的**存在减少**而不是增加 agent 负担。把它们交给 agent，等于用一个
   非确定性组件替换一个确定性组件，换来的只有更多步数与更多不一致。
2. **不可回滚 vs 可重做**。执行引擎直接改真实平台，它必须留在能被测试、能被审计、
   能被**执行侧强制**的地方（`writers/` 有黄金值回归）。让 agent"自己编排"真实写入的步骤顺序，
   与 PITFALLS 反复记录的那类事故是同一种形状。

> 一句话：**"编排器可以删减"要读成"删决策，不删执行"。**

### D-3 「校验 + 暂存 + 出审阅材料」合成一个工具

`factory_validate_blueprint` 被 **`factory_preview_blueprint`** 取代（不是并存）：

| 情形 | 行为 |
|---|---|
| 有 `error` | **不暂存**；返回 issues，**并附上相关键的写法提示**（错误只说"错在哪"，模型要的是"对的是什么"） |
| 校验通过 | 写 `RUNTIME_DIR/factory/<id>/blueprint.json` + `spec.md` + `checklist.md`，返回 `blueprint_id` 与**人话审阅清单** |

- **id 用内容指纹**：同一份蓝图重复预览得到同一个 id，不在运行时目录里堆垃圾；
  内容变了 id 就变（否则用户会拿旧蓝图去创建）。
- **`risk = read`**：它只写应用自己的 runtime 工作区（可整体删除），不碰平台、不碰凭据
  ——与既有 `insight_run`（在 runtime 工作区落一整套产物）同级。
  **不给它加确认门**：暂存不是"对环境生效的动作"，真正的闸门是 `factory_create` 的 `dangerous` 门；
  为"暂存"再点一次确认是纯噪音，还会训练用户习惯性点同意，把确认门用废
  （同 `read_local_file` 的取舍，见 [TOOLS.md](../TOOLS.md)）。
- **不拆成"校验工具 + 暂存工具"两个**：那会让 agent 的循环从 N 次变成 2N 次，
  而且它还得先回答"我该先调哪个"。

### D-4 蓝图规格交给 Agent，而且让它不可能漂

新增 `factory_blueprint_spec`（read）：返回蓝图规格 + 执行逻辑与要求 + 最小示例。

| 组成 | 唯一事实源 | 漂移防护 |
|---|---|---|
| 键名 / 类型 / 必填 / 取值范围 | `writers/blueprint.py` 的 pydantic 模型（`model_json_schema`） | **生成**，不可能漂 |
| 每个键是什么意思 | `writers/spec.py` 的 `_NOTES` 注解表 | `test_every_model_field_is_annotated` 断言**覆盖完整**：模型加了字段而没补注解 → 测试红 |
| `dataType` / `stepType` / `behaviorType` 等枚举 | `kernel/semantics.py`（上游实证词典） | 直接引用 |
| 编码约定 | `writers/blueprint.py` 的正则与常量 | 直接引用 |
| 执行顺序（8 步管道） | `orchestrate.PIPELINE_STEPS` | 函数内懒 import 同一个常量 |

**为什么要求注解"完整覆盖"而不是"重要字段就够"**：漏一条的后果是模型对着一个没有解释的键名
猜语义，而**注解缺失是静默的**——规格照样生成得出来，只是少一行字。静默的东西只能靠机检。

**为什么不做成文档**：手写的 schema 文档必然与模型漂移，而漂移的表现是
"模型照着文档写、校验全红"。规格是**从运行期对象生成**的，与 `tools/gen_tools_doc.py` 同一纪律。

### D-5 审阅材料前移到审批之前

`generate_review_spec` / `generate_checklist` 现在**在预览这一步**生成并落盘，同时随工具返回值
给模型一份——模型据此在对话里讲清"将要执行什么"。

配套：`ToolSpec.summary_fn`（新 SDK 字段）+ `approvals.summarize()` 优先用它。
`factory_create` 的确认摘要因此从"工具描述 + `blueprint_id=…` + 危险级"变成
**"将创建 1 个对象（training_record__c）、2 个字段、…"**——用户要判断的是后果，不是参数名。
摘要实现**必须自己兜住异常**：它坏掉绝不能让危险操作连确认框都弹不出来。

### D-6 步数预算是"旋钮"，不是常量

`AgentConfig.max_steps` 默认 **12 → 40**，并接通设置页（`settings` 表的 `maxSteps` 键，
范围 4~200，越界**夹住**而不是拒——旋钮语义，不是校验语义；坏值一律回默认）。

配套两条**可行动性**要求：

- **耗尽 ≠ 出错**。事件带 `reason="budget_exhausted"`，文案说清"已完成的部分都保留着、
  回一句「继续」即可、去哪儿调大"；前端用中性色而不是红色渲染。
  （"这一轮到头了"与"真出错了"对用户要做的动作完全不同，印成同一种红会让人对着一个
  其实能接着走的状态去查日志。）
- **截断必须显式**（PITFALLS #58）：`finish_reason == "length"` → 终止本轮 + 明说
  "**没有**执行任何工具"，绝不让半截参数进工具层。

## 3. 本期做与不做

**做**：D-1 ~ D-6 全部；`types.py` / `safety.py` / `loop.py` / `deepseek.py` / `settings.py` /
路由 / 设置页 / 配置工厂页 / `REQUIRED_TOOLS` / 文档数字 / 回归与反证。

**不做（明确）**：

- ~~把执行引擎交给 agent~~（D-2）。
- ~~给"暂存"加确认门~~（D-3）——会把确认门用废。
- ~~把蓝图规格塞进系统提示词~~。prompt 是每个请求的第一个字节，塞 ~5K token 的 schema 进去
  等于**每轮都付它的钱**，而绝大多数轮次用不到。规格走工具，按需取。
- ~~新增"分步建蓝图"的工具族~~（add_object / add_field / …）。它会让模型面对
  "先建哪个、什么时候算完"的新负担，而蓝图本来就是**一次性可校验的整体**。
- ~~给 `factory_blueprint_spec` 加 `section` 参数~~。返回值约 13.9K 字符，在
  `MAX_RESULT_CHARS=16_000` 之内；**加了参数就要模型先想"我要哪一节"**，负担更大。
  取而代之：加一条**尺寸断言**，将来涨过上限时由测试报红，而不是运行期静默截断。

## 4. 后果

**好处**：

- 「一句话 → 蓝图 → 审阅 → 创建」在**对话里**闭合，不再有"切到另一个页面手工上传"的中断点。
- 蓝图知识从一段死代码路径（`generate.py` 的 36 行 prompt，只被 `orchestrate` 用）
  搬到了活路径上，而且**从模型生成**，不可能漂。
- 用户审批时手里有材料（审阅清单 + 说清后果的确认摘要）。
- 少了一个 LLM 循环、两个模块、一个前端下拉项与一条会漂的口径。

**代价**：

- 模型侧多一次规格查询（约 13.9K 字符，一次性）。换来的是不再靠试错猜键名。
- `factory_preview_blueprint` 同时做两件事（校验 + 暂存），职责比"单一职能"的工具宽。
  这是刻意的取舍：拆开会让每次迭代多花一步，而步数是这个场景里最贵的资源。
- 步数上限从 12 提到 40，**"模型跑飞"的单轮成本上限也跟着涨**。缓解：危险操作仍过确认门、
  `is_loop` 仍在、耗尽可见且可继续。

**风险**：

| 风险 | 缓解 |
|---|---|
| 规格与模型漂移 | 结构从运行期模型生成 + 注解完整性机检（D-4） |
| 删 `orchestrate` 影响既有习惯 | 页面与 CHANGELOG 写明"改用对话"；手工上传保留 |
| `preview` 的 `read` 分级被质疑 | 判据写进 `ToolSpec` 文档串与本 ADR §D-3：只写自有 runtime 工作区、可整体删除、不碰平台与凭据 |
| 模型把 preview 当"已经生效" | 工具描述与返回值都明写"只写工作台运行时目录，不碰平台"；创建另有确认门 |

## 5. 诚实边界

- **`factory_preview_blueprint` 的"预览"不等于"用户已经看过"**。它只是**产出了材料**；
  模型有没有把材料讲给用户听、用户有没有真的看懂，系统无法强制。
  唯一的强制闸门是 `factory_create` 的 `dangerous` 确认门——而那道门展示的摘要
  （`summary_fn`）本身也是"尽力而为"：读不到暂存蓝图时会退回通用口径。
- **`risk=read` 是"按后果分级"的结论，不是"它不写磁盘"**。它确实写磁盘，写的是应用自己的
  runtime 工作区。把它描述成"纯只读"是错的；把它当成需要授权的高风险动作也是错的。
- **删掉 `orchestrate` 之后，"一句话生成一份复杂蓝图"没有再走一遍独立校验的机会**。
  对话链路靠的是：模型写 → preview 校验 → 模型改 → 再校验。校验器本身没变强，
  但**反馈循环更短**（一次调用而不是"生成→整份重来"）。
- **步数预算是刹车，不是配额**。`exhausted` 只在两次模型轮次之间检查，所以一次模型轮次里发出
  N 个工具调用时实际会花掉 `max_steps + N` 左右。文案里不承诺精确数字。

## 6. 外部参照

| 参照对象 | 它的做法 | 与本决策的关系 | 我们的理由 |
|---|---|---|---|
| [MCP 规范 · Tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) | tool 的 `description` 是模型选工具与用参数的**唯一**依据；规范建议把用法写进描述而不是靠外部文档 | **分歧** | 蓝图 schema 有上百个字段，塞进 `description` 会让**每个请求**都付它的 token。我们的做法是**另一个工具按需返回**——描述里只说"先看这个"，正文按需取。这是"描述 vs 按需资源"的取舍，不是不认同它 |
| [Anthropic · Tool Search Tool / `defer_loading`](https://www.anthropic.com/engineering/advanced-tool-use) | 工具定义 >10K token 或工具数 >10 时，把定义改成**按需发现**，上下文省 85% | **一致（跟随）** | 我们**没有**接 Tool Search，但同一动机下做了一件更小的事：把最大的一块知识（蓝图规格）从"常驻描述"改成"按需取一次"。判据一样——**用不到的上下文不该常驻** |
| [OpenAI Agents SDK · Human-in-the-loop](https://openai.github.io/openai-agents-python/human_in_the_loop/) | 危险工具在 **runner** 层暂停，`interruptions` 带 `tool_name` 与 `arguments`，宿主批准后**恢复同一次 run** | **一致（跟随）** | 与 [ADR-0016](0016-model-initiated-confirmation.md) §D-2 同一条：暂停/恢复属于循环层。本决策的 D-5（把"将要建出什么"放进确认摘要）是它的直接推论——**审批界面必须带够判断所需的信息**，否则"人确认"只是形式 |
| [OpenAI Agents SDK · `needs_approval` / `RunState`](https://openai.github.io/openai-agents-python/human_in_the_loop/) | 拒绝文案可定制（`tool_error_formatter` / `rejection_message`），默认也给模型一段明确文本 | **一致（跟随）** | D-6 的"预算耗尽要说清怎么继续"、PITFALLS #58 的"截断要说清没执行任何工具"都是同一件事：**给模型的观测必须能支撑它的下一步决策** |
| [LangGraph · Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) | 中断点由**图结构**声明（在哪个节点前暂停），恢复时从该点继续；不支持"工具内部随便暂停" | **一致（跟随）** | 与我们"等待只能在循环层"（ADR-0016 §D-2）同构。这条行业已经统一：**暂停点是结构，不是实现细节** |
| [Anthropic · Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) | 工具应"合并同一条工作流里的相邻步骤"，减少往返；错误信息要**可操作**（告诉模型怎么改，而不只是哪里错了） | **一致（跟随）** | D-3 就是这条：把「校验 + 暂存 + 出材料」合成一个，并把校验错误配上键的写法提示。**这两条是本决策里最直接采纳行业共识的部分**，不必自己论证 |
| 同上 | 工具应返回"高信号、少噪音"的结果，必要时分页/按需取 | **分歧（本期）** | 我们的规格工具**一次返回全量**（约 13.9K 字符）。理由：它是"写蓝图前看一次"的资源，分页反而要求模型先知道该要哪一节。加的是**尺寸断言**（超上限则测试红），不是分页参数 |

**由本次对照得出的结论**：

- **可以简化的**（行业已统一，直接跟随）：暂停点是结构而非实现；审批界面必须带够判断信息；
  工具应合并同一工作流里的相邻步骤；错误信息要可操作。这四条构成了 D-2 / D-3 / D-5 的主体。
- **必须自己定的**：① 蓝图规格用什么形态交给模型（描述 vs 按需资源 vs 手写文档）——
  行业对本例这种"上百字段的领域 DSL"没有统一答案；② 哪些步骤留在引擎、哪些交给 agent
  （D-2 的两条判据）；③ `preview` 的风险分级（写自有工作区算不算 read）。

## 7. 重新评估的触发器

| 触发器 | 到时再考虑 |
|---|---|
| 规格返回值涨到接近 `MAX_RESULT_CHARS`（测试会先红） | 改成按节返回，或把"注解"拆成独立的小工具 |
| 出现"模型照着自己脑补的键名写蓝图，反复失败"的实际记录 | 说明规格没被调用或没被读懂 → 检查工具描述，或把最常用的 10 个键写进 `description` |
| 接 **MCP**（[CORE-ARCHITECTURE](../CORE-ARCHITECTURE.md) §7.2） | 蓝图规格更适合做成 MCP resource（按需读、可版本化）；`factory_*` 工具是否需要拆成 MCP server 另议 |
| 配置工厂的写路径改为**异步/长任务**（现在是同步等 900s） | `preview` 与 `create` 的边界要重画：预览可能也该产出"计划书"而不是直接可执行的暂存 |
| 用户仍报"轮次不够"（默认已 40） | 说明瓶颈不在刹车，而在**每一步太贵**（如规格查询、蓝图整份重发）→ 该优化的是往返，不是继续加预算 |
