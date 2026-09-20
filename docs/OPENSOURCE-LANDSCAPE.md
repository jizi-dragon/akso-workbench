# 相似开源项目生态分析报告（OPENSOURCE-LANDSCAPE）

_2026-09-20 调研。对照对象：Workbench（`akso-mogul`，v0.3.12+3，`15b847e`）。_

> **本报告由两条独立渠道合并而成，并做了交叉核验**（见 §8 数据可信度）：
> - **渠道 A（主代理）**：GitHub REST API 逐仓库直接实测。
> - **渠道 B（子代理）**：`api.github.com` + `ungh.cc` + `img.shields.io` + 各项目官方文档实读。
>
> 凡两渠道冲突处**以 A 为准并已修正**；凡只有 B 核实、A 未复核处**逐条标注**。
> 本报告在定稿前**修正了两处自己犯的错**（见 §8.2），保留记录以免后来者重犯。

---

## 0. 一句话结论

> **没有找到一个"目的与结构"都高度重合的开源项目。**
> 本项目最独特的组合是**「单机 Windows 桌面 + 用户自己 Chrome 里的 MV3 扩展作执行面 + 面向特定企业 SaaS 平台（eGMP）的实施自动化」**——
> 这个组合在开源世界里**没有对应物**。不是本项目领先，而是**这个细分场景没人做开源**。
>
> 结构上最接近的是 **goose**（54,474 ★，Apache-2.0，on-machine agent + 桌面 + 扩展系统）。
> 本项目**真正稀缺的是两样东西**：**执行面在用户已登录的真实浏览器里 + 六平面存储隔离**，
> 以及 **`cache.py` 为提示缓存做的 append-only 前缀工程**。
> **真正缺的是"工程化外圈"**：评估集、追踪、MCP、可恢复执行。

---

## 1. 结论速览（TL;DR）

1. **工具体系、风险分级、技能包、沙箱这四件事，行业都有对应物；组合方式罕见。**
   本项目的 `ToolHub` + `ToolSpec(name/description/JSON Schema/risk)` 与 MCP 的 Tool 定义基本同构；
   审批门对应 LangGraph `interrupt()` / OpenAI Agents SDK `needsApproval`；
   `SKILL.md` + 沙箱脚本对应 Agent Skills 开放规范（已实测在微软 Agent Framework、CrewAI、Cline 落地）。

2. **两处"比行业更细"的设计，建议保持并写进对外说明**：
   - **Risk 是宿主强制**（装配期断言 + 执行期无令牌不调 handler），而 MCP 的 `readOnlyHint` / `destructiveHint`
     只是**提示**——规范原文明确客户端**不应基于不可信服务端的 annotations 做工具使用决策**。
   - **`cache.py` 的冻结摘要边界**（`summarized_upto` 只前进不回退）是为 **DeepSeek 前缀缓存命中率**做的优化。
     主流框架（LangGraph、Claude 生态）走显式 compaction，**没有对应机制**。

3. **Job Object 的定位需要说准**：它是**进程树生命周期**控制，**不是权限边界**。
   OpenAI Codex 的 Windows 沙箱**完全不用 Job Object**，用的是
   **受限令牌 / 独立低权限沙箱用户 + ACL + 防火墙规则 + 私有桌面**。
   ⇒ 若要把"限损"升级为"隔离"，下一个原语是**受限令牌（`CreateRestrictedToken`）/ AppContainer + ACL**，
   而不是继续加固 Job Object——但那**需要管理员做一次性设置**，与本项目"零配置"取向冲突。

4. **技能生态的体量超出预期**（这是本次调研最意外的发现）：
   `obra/superpowers` **288,874 ★**（MIT）+ `anthropics/skills` **177,188 ★**（未标注许可），
   两个仓库合计 **46.6 万 star**，**超过 langchain（146,694 ★）**。
   ⇒ Skill 生态已是 2026 年 agent 工程的主流形态。本项目站在这条主流之外——
   **这是主动选择，但应当被记录为"主动放弃生态位"，而不是"还没做"**。

5. **缺口集中在"工程化外圈"，不在"能力内核"**：评估集、可观测性/追踪、MCP 兼容、
   对话级断点续跑、可回滚、跨会话记忆、技能包摘要校验。**最该补的是评估集**——
   本项目已经有过"改提示词、加插件、换模型名"这类改动，而**每次都没有回归能力**。

6. **一处与本报告无关但必须报告的实测事实**：本仓 `LICENSE` 是 **Apache-2.0**，
   而 `pyproject.toml:7` 声明 `license = { text = "MIT" }`——**两个发布物声明了不同许可**。

---

## 2. 相似度谱系：七个维度分别对照

### 2.1 维度一：浏览器 / 计算机使用 Agent（**结构像，场景不同**）

| 项目 | ★ | 语言 / 许可 | 与本项目的相同点 | 关键差异 | 可学之处 |
|---|---|---|---|---|---|
| [browser-use/browser-use](https://github.com/browser-use/browser-use) | **115,369** | Python / MIT | 用 Playwright/CDP 驱动浏览器；页面状态→模型→动作的主循环 | **每次任务用受控的全新浏览器上下文**，不依赖用户登录态；目标是通用网页任务 | **敏感字段的结构化遮蔽**：它有独立的 Sensitive Data / Secrets 概念；本项目凭据只在存储层加密，回填模型时只做 key 名黑名单（`_REDACT_KEYS`）——**这件事可以对照补齐** |
| [Skyvern-AI/skyvern](https://github.com/Skyvern-AI/skyvern) | **23,039** | Python / **AGPL-3.0** | **业务层最像**：把重复 Web 操作流程序列化 + 幂等重试 | 面向可重放的公开表单流，无审批门与审计 | ⚠️ **AGPL-3.0 对闭源分发有传染性，只可参考设计，不可复用代码** |
| [browserbase/stagehand](https://github.com/browserbase/stagehand) | **24,559** | TypeScript / MIT | 把浏览器动作暴露成模型可调的原子操作 | 动作空间**收敛成三个动词**（act/observe/extract）；本项目是开放式注册表 | 为高频操作做**语义化复合动作**（如"按编码查对象并展开生命周期"），直接减少多步 tool calling |
| [nanobrowser/nanobrowser](https://github.com/nanobrowser/nanobrowser) | **13,810** | TypeScript / Apache-2.0 | ★ **扩展面最接近**：同为 MV3 扩展内跑 agent、同用用户自有 API Key、同为"用户自己的浏览器" | **没有宿主后端**（无 sidecar / SQLite / 账号库），无法承载业务写路径 + 审计；不做多账号隔离 | 它的 **Planner/Navigator 双角色拆分**是"步数门控"之外降低长链失败率的另一条路。本项目只有 `SafetyGate` 这类**刹车**，没有**分工** |
| [microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp) | **37,362** | TypeScript / Apache-2.0 | `sandbox/policy.py` 已预留 `mcp-stdio` 策略（"预留，本期不启用"） | 它把浏览器当**无状态工具集**；本项目当**有状态、多账号身份的资源池** | **用 MCP 把"浏览器动作"外置**，是最低成本扩大工具面受众的方式 |
| [Steel-dev/steel-browser](https://github.com/Steel-dev/steel-browser) | ~7,668 ᴮ | — | 面向 agent 的浏览器沙箱服务 | 沙箱在**浏览器层**；本项目在**代码执行层** | "浏览器即沙箱、用完即弃" vs 本项目"托管浏览器 + 会话保留"——后者安全上更弱，但实施场景必须复用登录态 |
| [microsoft/OmniParser](https://github.com/microsoft/OmniParser) | **25,424** | Jupyter / CC-BY-4.0 | 屏幕解析（GUI agent 用） | 研究型组件 | 若将来要自动化**没有 API 的遗留系统**，这是截图→可交互元素的事实标准之一 |
| [xlang-ai/OSWorld](https://github.com/xlang-ai/OSWorld) | ~3,151 ᴮ | — | **评测基准** | 研究基准 | 见 §6.3 评估集缺口 |

**评测基准参照**：`xlang-ai/OSWorld`（~3,151 ★ ᴮ）、`MinorJerry/WebVoyager`（~1,125 ★ ᴮ，**最后推送 2024-03，已停更**）。

> **判断**：这一类的共性是"**通用性换来了脆弱性**"——它们要处理任意网站，必须做视觉/DOM 理解；
> 本项目的目标平台有稳定 API，于是走**结构化直连**（`services/egmp/`，5,509 行）。
> **这不是落后，是不同的题。本项目不需要向这一类的架构靠拢。**

### 2.2 维度二：桌面 / 本机 Agent 工作台（**定位最接近**）

| 项目 | ★ | 语言 / 许可 | 与本项目的相同点 | 关键差异 | 可学之处 |
|---|---|---|---|---|---|
| [aaif-goose/goose](https://github.com/aaif-goose/goose)（原 `block/goose`） | **54,474** | Rust / Apache-2.0 | ★ **结构最接近**：on-machine agent、桌面应用、本地工具执行、扩展系统、多 provider（30+）、有 telemetry 文档、可做 custom distribution | **扩展系统 = MCP（stdio）**；**Recipe 系统**（YAML + Jinja2 参数化 + sub-recipe + 结构化输出校验 + 可版本化分发）；另有 **ACP（Agent Client Protocol）** 与 server 形态；CLI / 桌面 / server 三端 | ① **Recipe 系统值得直接借鉴**——本项目的 Skill 是"工具提供者"，goose 的 Recipe 是"**参数化工作流模板**"，正好补上"多步骤任务无复用载体"的缺口；② "同一内核多端暴露"的成熟做法 |
| [OpenHands/OpenHands](https://github.com/OpenHands/OpenHands)（原 OpenDevin / All-Hands-AI） | **88,558** | TypeScript + Python / MIT | 单机可跑、工具体系、风险控制、沙箱执行、技能概念 | **事件溯源 EventStream**（每事件一 JSON 文件，**先持久化再分发**，崩溃不丢）；**9 种 Condenser** 做上下文压缩；**Docker/gVisor/k8s 级沙箱**；**sub-agent as tool**；`SecurityAnalyzer` 风险评分；**MicroAgents**（`.openhands/microagents/*.md`） | ① **事件溯源 + clean replay**：本项目有 `agent_audit` 但**没有可重放的事件日志**；② **Condenser 体系**远比"冻结摘要"丰富；③ **V1 的洞察"sub-agent 就是一个普通工具"**（markdown `AgentDefinition` frontmatter + `permission_mode: "never_confirm"`）比多 agent 框架轻得多 |
| [cline/cline](https://github.com/cline/cline) | **68,804** | TypeScript / Apache-2.0 | ① **扩展形态**（与本项目"扩展即执行面"呼应）；② **每个工具调用都要人工批准**；③ 有 Checkpoints / Subagents / Skills / Plugins / MCP / Hooks / Memory Bank | 编码 agent，宿主是编辑器 | ① **Auto Approve 按工具类别逐次评估**（读项目文件/读全部文件/编辑/执行安全命令/执行全部命令/用浏览器/用 MCP），且**"是否需要审批"由模型自己打标记**（`requires_approval`），**官方明确说不用固定白名单**——**这与本项目"宿主按 Risk 静态分级"是两条相反路线**；② **Checkpoints 可回滚**（本项目最大体验缺口）；③ **Memory Bank / Subagents** |
| [continuedev/continue](https://github.com/continuedev/continue) | **35,957** | TypeScript / Apache-2.0 | 本地 + 多 provider + 配置化 + MCP | 编码辅助为主 | 配置的跨版本兼容与迁移 |
| [RooCodeInc/Roo-Code](https://github.com/RooCodeInc/Roo-Code) | **24,301** | TypeScript / Apache-2.0 | 自定义模式/角色 | **最后推送 2026-05-15，活跃度明显下降** | 模式与工具集的绑定——正是本项目 `list_api_for(plugins)` 想做的事 |
| [langgenius/dify](https://github.com/langgenius/dify) | **156,505** | TypeScript + Python / 自有 | 工具/插件体系 + 工作流编排 + 本地可部署 | 平台级产品（多租户、可视化编排、插件市场） | 插件市场的准入与签名 |
| [n8n-io/n8n](https://github.com/n8n-io/n8n) | **205,399** | TypeScript / fair-code | 可视化流程自动化 + 节点生态 | 通用集成，非 agent-first | 节点输入/输出 schema 契约 |
| [Mintplex-Labs/anything-llm](https://github.com/Mintplex-Labs/anything-llm) | **66,229** | JavaScript / MIT | ★ **"本地服务 + 桌面壳"与本项目"FastAPI sidecar + Electron 壳"是同一架构模式** | RAG 为主 | ⇒ **"桌面壳只做壳、业务逻辑在本地服务进程"是行业已验证的形态**；它用可配置端口 + 服务发现，本项目用固定端口（ADR-0011/0012 已论证，单机单实例场景可接受） |
| [CherryHQ/cherry-studio](https://github.com/CherryHQ/cherry-studio) | **52,004** | TypeScript / **AGPL-3.0** | 桌面客户端 + MCP 支持 | 通用聊天客户端 | MCP server 的桌面端管理 UX；⚠️ **许可为 AGPL-3.0** |
| [lobehub/lobehub](https://github.com/lobehub/lobehub)（原 lobe-chat） | **82,663** | TypeScript / 自有 | 桌面/Web 双形态 + 插件市场 | 通用助手平台 | 插件网关设计；⚠️ 许可非标准 |
| [janhq/jan](https://github.com/janhq/jan) | **44,566** | TypeScript / 自有 | 纯本地桌面 AI 应用 | 本地模型推理为主 | 离线与更新体验 |
| [openai/codex](https://github.com/openai/codex) | ~125,308 ᴮ | — / Apache-2.0 | 桌面 + CLI + IDE；**有 Skills 与 Plugins** | 编码 agent | ★ **它的 Windows 沙箱做法是本报告 §2.4 的关键证据** |
| [google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli) | ~107,076 ᴮ | — | CLI + 审批模式 + 容器沙箱 | 编码/通用 CLI | 审批模式的档位设计 |

> **判断**：这一类里 **goose 是最值得对标的对象**（形态几乎一样），
> 但 **Cline 才是"审批 UX 与可回滚"上最值得抄的**。
> 本项目"零依赖手写 2,083 行核心"的选择是合理的——通用框架的价值随"需要接入的模型/工具/场景数量"增长，
> 而本项目是单模型、单领域、工具自研。**但要诚实记录代价**：自己维护意味着**框架免费送的护栏、追踪、评估、重放都得自己写**。

### 2.3 维度三：Agent 框架与工具体系（**工具体系可对照**）

| 项目 | ★ | 语言 / 许可 | 工具契约 | 风险分级 / HITL | 可观测性 |
|---|---|---|---|---|---|
| [langchain-ai/langchain](https://github.com/langchain-ai/langchain) | **146,694** | Python / MIT | `@tool` + docstring/Pydantic → JSON Schema | 无内建分级 | LangSmith |
| [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) | **41,970** | Python / MIT | 同上 | ★ **`interrupt()` + checkpointer 可恢复** | LangSmith / OTel |
| [openai/openai-agents-python](https://github.com/openai/openai-agents-python) | **29,567** | Python / MIT | 函数签名 → schema | `needsApproval` / guardrails / handoff | 内建 tracing |
| [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai) | **20,061** | Python / MIT | ★ **Pydantic 模型即契约**（与本项目 `args_model` 同路） | deferred tools / 审批回调 | OTel |
| [huggingface/smolagents](https://github.com/huggingface/smolagents) | **29,406** | Python / Apache-2.0 | ★ **代码即动作**（CodeAgent） | 沙箱可选（E2B/Docker） | 基础 |
| [crewAIInc/crewAI](https://github.com/crewAIInc/crewAI) | **58,789** | Python / MIT | 角色/任务/工具 | 无统一分级 | 基础 |
| [microsoft/autogen](https://github.com/microsoft/autogen) | **61,065** | Python / **CC-BY-4.0** | 工具函数注册 | 无 | 基础（**最后推送 2026-04-15，已明显放缓**） |
| [ag2ai/ag2](https://github.com/ag2ai/ag2) | **4,940** | Python / Apache-2.0 | 同 AutoGen 血统 | 有 HITL 模式 | 基础 |
| [microsoft/semantic-kernel](https://github.com/microsoft/semantic-kernel) | **28,580** | C# / MIT | `[KernelFunction]` + 插件 | filters / 审批中间件 | OTel |
| [Eigenwise/atomic-agents](https://github.com/Eigenwise/atomic-agents) | ~6,251 ᴮ | Python | 输入/输出 Pydantic schema 原子化 | 无 | 基础 |
| [humanlayer/humanlayer](https://github.com/humanlayer/humanlayer) | ~11,587 ᴮ | — | — | ★ **专职 HITL：审批即 API/Slack/邮件** | — |
| [langfuse/langfuse](https://github.com/langfuse/langfuse) | ~34,818 ᴮ | — | — | — | ★ **开源 agent tracing + evals 平台（可自托管）** |
| [FoundationAgents/MetaGPT](https://github.com/FoundationAgents/MetaGPT)（原 geekan/MetaGPT） | **70,513** | Python / MIT | 多 agent 软件公司隐喻 | 无 | 基础（**最后推送 2026-01-21**） |
| [openai/swarm](https://github.com/openai/swarm) | **21,992** | Python / MIT | 轻量多 agent | — | **已被 Agents SDK 取代，最后推送 2026-04-15** |
| [langchain-ai/open_deep_research](https://github.com/langchain-ai/open_deep_research) | **12,685** | Python / MIT | 深度研究（多轮检索 + 汇报） | — | 研究场景 |

**与本项目的逐项对照（本节重点）**：

- **工具契约**：本项目与 pydantic-ai 同路（**Pydantic 模型即契约**），
  但**"注册表同时是 HTTP 清单 / function calling schema / 审计表的单一事实源"**在主流框架里**并不常见**——
  多数框架的"展示给 UI 的工具列表"与"发给模型的 schema"是两套代码。**这是正向差异。**
- **HITL**：行业最成熟的是 **LangGraph `interrupt()` + checkpointer**（中断点持久化、重启可恢复）。
  本项目的**一次性令牌 + 5 min TTL + 参数指纹 + 启动清空遗留 pending**在细节上**更严**，
  但**架构上是内存态 + DB 记录，不是可恢复的执行图**——进程一重启整条对话就得重来。
  ⇒ **本项目在安全性上更强、在可恢复性上更弱。**
- **上下文管理**：本项目 `summarized_upto` **只前进不回退**（`cache.py:143` 明确警告"不要改成每轮按最近 N 轮裁剪：那样前缀每轮都在滑动，缓存命中率恒为 0"）——
  **这是针对 DeepSeek 前缀缓存经济性的特化优化，主流框架因为计费模型不同而不做。**
- **可观测性**：行业标准是 **OTel GenAI 语义约定 + Langfuse/Phoenix/LangSmith 三选一**。
  本项目只有 `agent_audit`（谁批准了什么）与 `deepseek_usage`（计数）。
  **审计 ≠ 追踪**：没有 span、没有父子关系、没有工具耗时分解、没有失败率聚合。

### 2.4 维度四：代码执行沙箱（**本项目最"薄"的一环，含 Windows 专题**）

| 项目 | ★ | 许可 | 隔离原语 | Windows |
|---|---|---|---|---|
| [e2b-dev/E2B](https://github.com/e2b-dev/E2B) | **13,886** | Apache-2.0 | 云端 microVM（Firecracker 系） | 否（服务端） |
| [daytonaio/daytona](https://github.com/daytonaio/daytona) | **71,748** | ⚠️ **未识别到许可** | 容器 / microVM | 否 |
| [superradcompany/microsandbox](https://github.com/superradcompany/microsandbox) | **8,300** | Apache-2.0 | 本地优先 microVM | **未核实**（未取到平台支持矩阵） |
| [openinterpreter/openinterpreter](https://github.com/openinterpreter/openinterpreter) | **68,389** | Apache-2.0 | 可选 Docker/E2B；**默认宿主直跑 + 确认提示** | 是（默认非隔离） |
| [pyodide/pyodide](https://github.com/pyodide/pyodide) | ~14,836 ᴮ | MPL-2.0（未复核） | **WASM 沙箱** | 是（跨平台，能力受限） |
| [google/gvisor](https://github.com/google/gvisor) | ~19,360 ᴮ | Apache-2.0 | 应用内核，系统调用拦截 | 否（Linux） |
| [firecracker-microvm/firecracker](https://github.com/firecracker-microvm/firecracker) | ~36,825 ᴮ | Apache-2.0 | microVM | 否（Linux/KVM） |
| [sandboxie-plus/Sandboxie](https://github.com/sandboxie-plus/Sandboxie) | ~19,483 ᴮ | **GPL-3.0** | ★ **Windows 内核态重定向隔离**（文件/注册表/进程虚拟化） | ★ **是（Windows 原生最成熟的开源方案）** |
| [openai/codex](https://github.com/openai/codex) | ~125,308 ᴮ | Apache-2.0 | 分平台：macOS Seatbelt / Linux Landlock+seccomp / **Windows 受限令牌 + ACL + 防火墙** | ★ **是（见下）** |
| [google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli) | ~107,076 ᴮ | Apache-2.0 | 容器沙箱 + 审批模式 | 是（Docker/Podman） |

#### Windows 上的代码执行隔离：行业做法与 Job Object 的位置（本节关键）

**证据来源**：OpenAI Codex 官方《Building a safe and effective sandbox for Codex on Windows》
（<https://openai.com/index/building-codex-windows-sandbox/>）。主代理三次尝试均被 **HTTP 403** 拦下；
机制描述取自**可访问的官方文档镜像** `learn.chatgpt.com/docs/windows/windows-sandbox`，由渠道 B 实读回报。
⚠️ **文章存在性与要点已核实，但主代理未直接读取原文**，引用时按此限定。

| 模式 | 做法 | 代价 |
|---|---|---|
| **`elevated`（首选）** | **专用低权限沙箱用户** + 文件系统权限边界 + **防火墙规则** + 必要的本地策略变更 | **需要管理员权限做一次性设置**（需 IT 配合） |
| **`unelevated`（回退）** | **从当前用户派生的受限 Windows 令牌** + **ACL 文件系统边界** + 环境级离线控制 | 官方自述 "weaker than elevated"，但在企业策略阻止提权时仍可用 |

其余要点：**私有桌面**做 UI 隔离（`windows.sandbox_private_desktop`，默认开启）；
企业可用 `requirements.toml` 的 `allowed_sandbox_implementations = ["elevated"]` **强制禁止回退**；
`/sandbox-add-read-dir <绝对路径>` 做**会话级读权限增量授权**；
排障细节：**Windows 错误 1385** = 沙箱用户缺少登录权限；并会警告"某些目录对 Everyone 可写"意味着沙箱保护不了该目录。

**对目标项目的四条判断**：

1. **Job Object 不是权限边界，行业也不把它当权限边界。** Codex 的 Windows 方案里**完全没有 Job Object**。
   Job Object 解决的是**"进程树泄漏"**——本项目 `sandbox/job.py` 对此的描述
   （"孙进程会被留下继续跑：占端口、占文件句柄、继续联网"）**完全正确且切中要害**，
   用 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 一次终结全树也是**教科书做法**。
2. **本项目已经把 Job Object 用在了该用的地方，且比多数项目更认真**：
   `job.py` 记录了 `CREATE_SUSPENDED → AssignProcessToJobObject → ResumeThread` 的教科书路径，
   同时**诚实记录了实测失败**——`OpenThread(THREAD_SUSPEND_RESUME, ...)` 返回 **NULL, err=87**，
   于是改用"正常创建 → 立刻绑定"并**显式声明竞态窗口**。
   ⇒ **这类"实测推翻教科书"的一手记录在开源项目里很少见，是本仓最值得保留的工程资产之一。**
3. **若要升级为"权限边界"，下一个原语是受限令牌 / AppContainer + ACL，不是继续加固 Job Object。**
   但它**需要提权**——与本项目"工程师自己电脑上零配置"的取向**直接冲突**。
   Codex 之所以必须提供 `unelevated` 回退，正是因为企业策略常常禁止提权。
4. **现实建议**：**维持限损层定位**，但做两件低成本的事——
   ①把"这个沙箱拦不住什么"写进**用户可见文档**（现在只写在 ADR 与代码文档串里，用户看不到）；
   ②安装脚本式 Skill 时明确提示"这个能力会执行你不认识的代码，**它可以读你有权限读的文件、可以联网**"。
   **用户做决定需要的是后果，不是机制名。**

> **判断**：本项目沙箱（`sandbox/`，1,173 行）的强制项只有 **6 条**——
> 进程树终止、环境白名单、cwd 限制、敏感路径拒绝、输出上限、审计。
> **没有网络强制隔离**（`allow_network` 只是声明）、**没有文件系统写隔离**（沙箱根内可写）、**没有权限降级**。
> 在单机 Windows 桌面应用上，**这个折中是务实的**（跑 microVM 需要 Hyper-V/WSL2，会毁掉"零依赖安装"这个卖点）。
> **但必须把边界说清楚**——本项目在 ADR 里做到了，**在用户文档里还没做到**。

### 2.5 维度五：技能 / 扩展包生态（**生态位判断的关键**）

#### 2.5.1 体量：这是本次调研最意外的发现

| 仓库 | ★（2026-09-20 实测） | 许可 | 性质 |
|---|---|---|---|
| [obra/superpowers](https://github.com/obra/superpowers) | **288,874** | MIT | "agentic skills framework & software development methodology"（Shell，创建于 2025-10-09，仍在活跃推送） |
| [anthropics/skills](https://github.com/anthropics/skills) | **177,188** | ⚠️ **API 未返回许可（license: null）** | 官方 "Public repository for Agent Skills" |
| [langchain-ai/langchain](https://github.com/langchain-ai/langchain) | 146,694 | MIT | 对照：**两个技能仓库合计 466,062 ★，是 langchain 的 3.2 倍** |

⇒ **Skill 生态的体量已经超过传统 agent 框架。** 本项目站在这条主流之外。

#### 2.5.2 规范的具体内容（与项目实现逐条比对）

**Agent Skills 规范**（<https://agentskills.io/specification>，主代理实读）：
目录 `SKILL.md` + `scripts/` + `references/` + `assets/`；
必填字段**只有** `name`（≤64，**仅小写字母/数字/连字符**，不得首尾连字符，**且必须与父目录名一致**）
与 `description`（≤1024）；可选 `license` / `compatibility`（≤500，环境要求）/ `metadata` / `allowed-tools`（实验）。

**核心机制是 progressive disclosure 四阶段**（渠道 B 从微软 Agent Framework 文档实录）：
① **Advertise**（每技能约 100 token，只注入 name+description）→
② **Load**（任务匹配时拉取正文，建议 <5000 token）→
③ **Read resources**（按需读 `references/`）→
④ **Run scripts**（执行随包脚本）。

**微软实现的几个关键细节**：
- 三个技能工具 `load_skill` / `read_skill_resource` / `run_skill_script`，
  **仅在"至少一个技能带资源/脚本"时才注册**——**工具面随实际能力动态收缩**。
- 三者**默认全部需要审批**，靠 `ToolApprovalMiddleware` 的 `auto_approval_rules` 放行；
  `ReadOnlyToolsAutoApprovalRule` 自动批准前两个，**但不批准 `run_skill_script`**。
- archive 准入：`ArchiveMaxFileCount`(20) / `ArchiveMaxSizeBytes`(1 MB) / `ArchiveMaxUncompressedSizeBytes`(1 MB) / 扩展名白名单。
- **`digest` 校验**：`sha256:` + 64 位小写十六进制，**解压前校验**，不匹配则跳过该条目。
- 官方 `SubprocessScriptRunner` **自标为"仅供演示"**，要求生产环境自行补上沙箱 + 资源限制 + 输入校验 + 审计。
- **MCP 归档里的脚本永不执行**，只作只读资源。

**与项目实现的比对**：

| 维度 | 行业（规范 + 微软实现） | 本项目 | 判定 |
|---|---|---|---|
| 目录 | `SKILL.md` + `scripts/` + `references/` + `assets/` | 无 `assets/` | 🟡 基本对齐 |
| **技能是什么** | **指令与知识**，核心是 progressive disclosure | **工具提供者**；正文**明确不注入上下文** | ❌ **语义分歧——恰好放弃了规范的核心机制** |
| frontmatter | 必填 `name` / `description`；`name` **须等于目录名** | 必填 `id` / `name` / `version` / `description` / `tools` + `capabilities` | ❌ **字段不兼容**，包装不进任何规范客户端 |
| "技能贡献工具" | 规范**不让技能声明工具**；客户端按需注册三个通用技能工具 | 技能**直接声明** `tools:`，装配期全量并入 `ToolHub` | 🟡 都能走通，但**规范的做法天然抑制工具面膨胀** |
| 技能工具审批粒度 | 三个技能工具默认全需审批，**按能力粒度**放行（脚本执行不自动放行） | 只有三档 `Risk`，按工具整档分级 | 🟡 **本项目分级更粗**；但"脚本式 ≥ `write`"的结论与微软一致 |
| `capabilities` | `compatibility` 是**信息性**的 | **权限申请 + Fail-Closed**：声明 `network`/`subprocess` 直接安装失败 | ✅ **强于规范** |
| 脚本执行 | 规范只说"由客户端决定"；微软官方 runner 是**演示级**并自带免责声明 | **已接沙箱 + 策略 + 审计**，且 stdio/JSON 协议**与 MCP stdio 同构** | ✅ **强于参照物**（把微软"让你自己补"的那层补好了） |
| 完整性校验 | 微软有 `sha256:` digest + 解压上限 | 有 ZIP 准入 + **可疑模式扫描**；**未见 digest** | 🟡 **静态扫描领先多数生态**，但缺摘要与 zip bomb 防护 |
| 工具名冲突 | 规范未规定 | **不许覆盖核心工具名**（直接拒） | ✅ 规范未覆盖的有效防护 |
| 生态互通 | 微软 / CrewAI / Cline 均已支持 | **不互通** | ❌ **主动放弃生态位** |

#### 2.5.3 包管理成熟度对照

| 机制 | 本项目 | 成熟范例 |
|---|---|---|
| 清单 + 版本 | `SKILL.md` | SKILL.md / MCP Registry |
| **完整性摘要（sha256）** | **未发现** | 微软 `MCPSkillsSource` |
| **解压大小/文件数上限** | 有 ZIP 准入（**细节未逐行复核**） | 微软 archive_* 三档 |
| 签名与来源证明 | 无 | Open VSX（~2,040 ★ ᴮ）、SLSA/provenance |
| 吊销机制 | 无 | npm / PyPI / Open VSX |
| 依赖解析 | 无 | npm / Cargo / Homebrew |
| **可疑模式静态扫描** | ✅ **有**（`skills/security.py`） | VS Code / Open VSX、`ossf/malicious-packages` |
| MCP 技能源 | 无 | 微软 `skill://index.json` |

**其他包/规范相关仓库**：
[modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers)（**90,476** ★）、
[modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk)（**24,348** ★，MIT）、
[modelcontextprotocol/registry](https://github.com/modelcontextprotocol/registry)（**7,267** ★，Go）、
[modelcontextprotocol/modelcontextprotocol](https://github.com/modelcontextprotocol/modelcontextprotocol)（规范仓，~9,256 ★ ᴮ）、
[docker/mcp-gateway](https://github.com/docker/mcp-gateway)（~1,575 ★ ᴮ，容器化 MCP 网关）。

### 2.6 维度六：MCP 生态现状

**工具清单标准化已经完成**（渠道 B 实读 MCP 规范 2025-06-18 与 draft 页）：
`name` + `description` + `inputSchema`（JSON Schema，默认 2020-12）+ 可选 `outputSchema`/`structuredContent`。
**这与本项目 `ToolSpec` 的字段集基本同构**，差异只是"协议传输 vs Python 注册表"。

**风险分级只到"提示"层级**——这是本报告最重要的对照之一：

| | MCP `ToolAnnotations` | 本项目 `Risk` |
|---|---|---|
| 字段 | `title` / `readOnlyHint`（默认 false）/ `destructiveHint`（默认 **true**）/ `idempotentHint` / `openWorldHint` | `read` / `write` / `dangerous` |
| 性质 | **提示**。规范原文明确所有 annotations **不保证忠实描述工具行为**，且**客户端绝不应基于不可信服务端发来的 annotations 做工具使用决策** | **宿主强制的安全属性**：装配期断言 + 执行期无令牌**根本不调用 handler** |
| 人工介入 | 规范层面只到 "**SHOULD** always be a human in the loop"；机制上是 `Elicitation`（2025-06-18）；draft 增加 Input Required Tool Results 与 Stateful Tools。**没有令牌、没有 TTL、没有参数指纹** | 一次性令牌 + 5 min TTL + **参数指纹绑定** + 启动清空遗留 |

⇒ **本项目自研审批门不是重复造轮子，而是补上了 MCP 标准不提供的强制层。**
MCP 客户端（Claude Desktop / Cursor / VS Code）的普遍做法是"首次调用逐工具弹窗 + 会话内记忆"，
粒度**比本项目更粗**，但对用户更省心——**这是可以借鉴的体验层改进**：

> **建议**：保留令牌机制做安全兜底，但增加"**本会话内信任该工具**"的快捷授权，减少确认疲劳。

**MCP 与技能包正在合流**：微软 Agent Framework 已实现 **MCP-based skills**
（服务端用 `skill://index.json` 暴露技能，客户端经 `resources/read` 拉取）。
**目标项目当前 MCP 状态：设计已对齐、实现为零**——
`sandbox/policy.py:215` 的 `mcp-stdio` 策略、`db.py:310`、`skills/script_runner.py:17`、
`api/routes_sandbox.py:8` 四处注释都写明"预留 / 将来接 MCP 时可直接复用"。

**MCP 的组织演进**：2025-12-09 由 Anthropic 捐给 **Linux Foundation 旗下的 Agentic AI Foundation（AAIF）**
（Anthropic/Block/OpenAI 共同创立，Google/Microsoft/AWS/Cloudflare/Bloomberg 支持），
**1 万+ 公开 server、97M+ 月 SDK 下载**，ChatGPT / Cursor / Gemini / Copilot / VS Code 均已采用；
**goose 与 AGENTS.md 是 AAIF 的创始项目**——这解释了 `block/goose` → `aaif-goose/goose` 的组织迁移。

### 2.7 维度七：浏览器会话隔离 / 多账号（**最窄但最独有**）

| 项目 | ★ | 许可 | 做法 | 与本项目的关系 |
|---|---|---|---|---|
| [daijro/camoufox](https://github.com/daijro/camoufox) | **12,019** | MPL-2.0 | 反检测 Firefox（**指纹层补丁**） | **正交需求**：它解决跨站点指纹一致性；本项目解决**同站点内多账号的存储平面隔离**。本项目不追求反检测——对"工程师合法操作自己账号"的场景是**明智取舍** |
| [Kaliiiiiiiiii-Vinyzu/patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright) | **4,636** | Apache-2.0 | Playwright 的"不可检测"分支 | 若真实 SaaS 登录流程触发风控，它提供**低改造成本**的替换路径（API 兼容 Playwright）。**建议列为技术预案，不立即引入** |
| [ultrafunkamsterdam/undetected-chromedriver](https://github.com/ultrafunkamsterdam/undetected-chromedriver) | ~12,845 ᴮ | — | Selenium 反检测 | **最后推送 2025-07-05，已明显停更，不建议依赖** |

**Gap 判断**：本次调研**未找到**与"六平面隔离"同构的开源替代品。
商用反检测浏览器（Multilogin / AdsPower / GoLogin）没有成熟的开源对应物；
开源世界要么走**指纹伪装**（Camoufox / patchright），要么走**浏览器 profile 目录隔离**（`--user-data-dir`）。
**把 cookie / localStorage / IndexedDB / ServiceWorker / DNR 六个平面同时显式隔离、并做会话切换与回放的开源实现，本次未发现。**

> ⚠️ 这是**基于本次检索范围的判断，不是穷尽性证明**。

**这构成本项目第二项真正的差异化资产**——而作者自己在 `PLUGIN-UPSTREAM-RESEARCH.md` 里已经识别出来了：
「你真正的可复用资产不是 agent 循环，而是桌面分发基础设施……六平面隔离、Cookie 回放、IndexedDB 命名空间全是通用多账号技术。」

---

## 3. 对比总表

| 项目 | 浏览器自动化 | 工具体系 | 风险分级与审批 | 沙箱 | 技能包 | 桌面形态 | 许可 |
|---|---|---|---|---|---|---|---|
| **Workbench（本项目）** | **Playwright + 用户自己的 Chrome 扩展（六平面隔离）** | **自研 `ToolHub` + `ToolSpec`（Pydantic→JSON Schema），三处单一事实源** | **宿主强制 Risk 三档 + 一次性令牌 + 5 min TTL + 参数指纹 + 审计** | **Windows Job Object（进程树）+ 策略 + 环境白名单 + 敏感路径拦截（自称限损层）** | **`SKILL.md`（改造版，非规范兼容）+ ZIP 准入 + 可疑模式扫描 + 沙箱执行** | **Electron 壳 + FastAPI sidecar（固定端口）** | **Apache-2.0**（⚠️ `pyproject.toml` 写 MIT，见 §5） |
| browser-use | Playwright/CDP，受控上下文 | 自定义 action 集 | 部分（敏感数据/机密） | 依赖宿主 | 无 | Python 库 | MIT |
| Skyvern | Playwright + 视觉 | 工作流节点 | 无 | 服务端 | 无 | 服务 | **AGPL-3.0** |
| Stagehand | Playwright/CDP | act/observe/extract | 无 | 云端 | 无 | SDK | MIT |
| Nanobrowser | **MV3 扩展（用户浏览器）** | 扩展内工具 | 无 | 无 | 无 | **Chrome 扩展** | Apache-2.0 |
| playwright-mcp | Playwright | **MCP 标准工具** | annotations（**提示级**） | 无 | 无 | MCP 服务 | Apache-2.0 |
| Steel Browser | Playwright 容器化 | HTTP API | 无 | 容器 | 无 | 服务 | 未核实 |
| LangChain | 无 | `@tool` + Pydantic | 无内建 | 无 | 无 | 库 | MIT |
| LangGraph | 无 | 同 LangChain | **`interrupt()` + checkpointer 可恢复** | 无 | 无 | 库 | MIT |
| pydantic-ai | 无 | **Pydantic 模型即契约** | deferred tools / 审批 | 无 | 无 | 库 | MIT |
| openai-agents-python | 无 | 函数签名→schema | guardrails / needsApproval | 无 | 无 | 库 | MIT |
| smolagents | 无 | **代码即动作** | 可选 | **E2B/Docker** | 无 | 库 | Apache-2.0 |
| CrewAI | 无 | 角色/任务/工具 | 无统一分级 | 无 | **有 Skills** | 库 | MIT |
| Semantic Kernel | 无 | `[KernelFunction]` | filters/审批中间件 | 无 | 有 | 库 | MIT |
| **Microsoft Agent Framework** | 无 | 函数 + **MCP** | **ToolApprovalMiddleware，技能三工具默认需审批** | 无（提示自加） | ★ **SKILL.md + ZIP + sha256 + MCP 技能源 + progressive disclosure** | 库 | 未核实 |
| Dify | 无 | 可视化 + OpenAPI 导入 | 无统一分级 | 代码节点 | 无 | Web 服务 | 自有 |
| n8n | 无 | 400+ 节点 | 无统一分级 | 无 | 无 | Web 服务 | fair-code |
| humanlayer | 无 | — | ★ **专职 HITL（API/Slack/邮件）** | — | — | 服务 | 未核实 |
| Langfuse | — | — | — | — | — | 服务 | 未核实 |
| E2B | 无 | SDK | — | **云端 microVM** | 无 | 云服务 | Apache-2.0 |
| Daytona | 无 | SDK | — | **容器/microVM** | 无 | 服务 | ⚠️ 未识别 |
| microsandbox | 无 | SDK | — | **本地 microVM** | 无 | 运行时 | Apache-2.0 |
| Open Interpreter | 无 | 代码执行 | **交互式逐条确认** | 可选 Docker/E2B | 无 | CLI | Apache-2.0 |
| Pyodide | 无 | — | — | **WASM** | 无 | 库 | MPL-2.0（未复核） |
| gVisor | 无 | — | — | **系统调用拦截** | 无 | 运行时 | Apache-2.0 |
| Firecracker | 无 | — | — | **microVM** | 无 | VMM | Apache-2.0 |
| Sandboxie | 无 | — | — | ★ **Windows 内核重定向隔离** | 无 | Windows 应用 | **GPL-3.0** |
| **OpenAI Codex** | 内置 | 函数工具 | **审批策略（含 never）** | ★ **Seatbelt / Landlock+seccomp / 受限令牌+ACL+防火墙** | **Skills + Plugins** | **桌面 + CLI + IDE** | Apache-2.0 |
| Gemini CLI | 无 | 函数工具 | **审批模式** | 容器 | 无 | CLI | Apache-2.0 |
| OpenHands | Playwright | 4 包 SDK | SecurityAnalyzer 风险评分 | **Docker/gVisor/k8s** | MicroAgents（markdown） | 服务 + Web | MIT |
| Cline | 内置 | 工具 + MCP | ★ **类别级 Auto-approve + YOLO 模式 + 模型标记** | 无（工作区边界） | **Skills + Plugins** | IDE+Desktop+CLI+SDK | Apache-2.0 |
| Continue | 无 | 自定义工具 + MCP | 部分 | 无 | 无 | IDE 扩展 | Apache-2.0 |
| Roo Code | 内置 | 模式化工具 | 有 | 无 | 无 | IDE 扩展 | Apache-2.0 |
| Goose | 内置 | **MCP Extensions** | 模式系统（含 auto） | 无 | **Recipe**（参数化工作流） | 本地应用 | Apache-2.0 |
| Jan | 无 | 扩展 | 无 | 无 | 无 | 桌面 | 自有 |
| AnythingLLM | 内置 | Agent 技能 | 无 | 无 | 有 | **本地服务 + 桌面** | MIT |
| Cherry Studio | 内置 | MCP | 无 | 无 | 无 | Electron | **AGPL-3.0** |
| LobeHub | 内置 | 插件市场 | 无 | 无 | 无 | Web + 桌面 | 自有 |
| **anthropics/skills** | — | — | — | — | ★ **规范源头，177,188 ★** | — | ⚠️ **未标注** |
| **obra/superpowers** | — | — | — | — | ★ **技能框架，288,874 ★** | — | MIT |
| Camoufox | **指纹补丁** | — | — | — | — | 浏览器 | MPL-2.0 |
| patchright | **Playwright 反检测分支** | — | — | — | — | 库 | Apache-2.0 |
| undetected-chromedriver | Selenium 反检测 | — | — | — | — | 库 | **已停更** |

---

## 4. 生态位判断

### 4.1 用两个轴定位

**「通用 vs 垂直领域」×「云端/服务化 vs 单机桌面」**：

- **通用 × 云端/服务**：LangGraph、OpenAI Agents SDK、Dify、n8n、E2B、Daytona —— 面向所有人、可横向扩展。
- **通用 × 单机桌面**：Cline、Continue、Goose、Open Interpreter、Cherry Studio、Jan —— 面向开发者个人。
- **垂直 × 云端**：Skyvern（RPA 场景）。
- **垂直 × 单机桌面 ← Workbench 在这里**，且**这一格里几乎没有直接竞品**。

更准确地说：Workbench 是**「企业 SaaS 实施工程师的操作台」**。它的用户不是开发者，
而是**要在客户现场、用自己的账号、登录某个企业平台、反复做配置与核查工作的实施顾问**。
这个画像决定了三个不可替换的特征：

| 特征 | 推导 |
|---|---|
| 必须在客户现场可用 | **单机、零依赖、离线可用**（零 Node、零联网，首次装扩展除外） |
| 必须操作生产系统 | **审批门 + 全量审计 + 幂等可重跑** |
| 必须同时持有多个客户/环境账号 | **六平面存储隔离 + 多账号会话切换** |

### 4.2 别人也这么做（**行业标准做法，不必自证合理**）

1. **工具契约 = 名称 + 描述 + JSON Schema + 风险元数据** —— MCP Tool / ToolAnnotations 完全同构。
2. **桌面壳只做壳，业务逻辑在本地服务进程** —— AnythingLLM、OpenHands 同模式。
3. **不受信代码走沙箱子进程** —— E2B、Daytona、smolagents、Codex 全都如此。
4. **`SKILL.md` 清单 + 渐进式披露 + 随包脚本** —— Agent Skills 开放规范（微软/CrewAI/Cline 已落地）。
5. **工具调用需要人在环路** —— MCP 规范 SHOULD、Cline/Codex/Gemini CLI 都有审批模式。
6. **ZIP 准入校验 + 解压上限 + 可疑模式扫描** —— 微软 archive_* 参数与 VS Code/Open VSX 扫描。
7. **进程树必须整体终止** —— 所有沙箱实现都处理（Job Object / cgroup / 容器）。
8. **上下文按阈值裁剪 + 摘要压缩** —— 所有长对话 agent 都做。
9. **Pydantic/类型模型 → 自动 JSON Schema** —— pydantic-ai 同路。

### 4.3 本项目独有（**真正的差异化，务必保持并对外强调**）

1. ★ **「执行面 = 用户自己的已登录 Chrome + 六平面显式隔离」**。
   调研范围内**没有同构体**：browser-use / Skyvern / Stagehand 用受控浏览器；
   Nanobrowser 用扩展但**无宿主后端、不做多账号隔离**；Camoufox / patchright 解决指纹而非存储平面隔离。
   **这是第一资产。**

2. ★ **「工具注册表是 HTTP 清单 / function calling schema / 审计表的唯一事实源」**。
   主流框架里 UI 展示清单与模型 schema 常是两套代码；本项目把它做成**不可违反的架构不变量**。
   **这是第二资产，而且是可被借鉴的好设计。**

3. ★ **审批门的「参数指纹 + 一次性令牌 + 5 min TTL + 启动清空遗留 pending」**。
   MCP 只到 SHOULD；Cline 是类别级勾选；LangGraph 是可恢复中断。
   **把"人工批准"绑定到"具体参数组合"、并拒绝跨进程恢复未决操作**——这个组合在本次调研里**没有见到第二个**。

4. ★ **`cache.py` 的冻结摘要边界**（`summarized_upto` 只前进不回退）。
   目标是让 DeepSeek 前缀缓存命中率不归零（价差 **50×**）。
   **这是围绕具体模型计费结构的原创优化**，主流框架没有对应机制。

5. **Job Object 的实测记录**（`CREATE_SUSPENDED` 路径因 `OpenThread` err=87 失败 →
   改用创建后立即绑定并显式声明竞态窗口）。
   **这类"教科书方案在本机实测失败"的一手记录，是稀缺的工程资产。**

6. **平台洞察的「织网爬取 + networkx 图分析 + 生成 drawio 架构图」**。
   本次调研未找到同类（browser-use 等只做动作，不做**平台元数据图谱化**）。
   ⚠️ **标注为"检索范围内的独有"**——没有可比参照物，因此无法判断它是独特还是只是罕见。

### 4.4 一句话生态位

> **Workbench 不是"开源生态里某个项目的同类"，而是"把 2026 年 agent 工程的多项行业标准做法，
> 围绕一个非常具体的职业场景（企业 SaaS 实施）手工内化并做进单机桌面"的垂直整合体。
> 它的护城河在浏览器会话隔离、桌面分发基础设施与业务知识，不在 agent 循环本身**——
> 这一点作者本人已经得出同一结论（`PLUGIN-UPSTREAM-RESEARCH.md`）。

---

## 5. 一处实测发现的本仓不一致（与本报告主题无关，但必须报告）

| 位置 | 声明 |
|---|---|
| `LICENSE`（文件正文） | **Apache License 2.0** |
| `pyproject.toml:7` | `license = { text = "MIT" }` |

**两个都是发布物的一部分**：`LICENSE` 是仓库里给人看的，`pyproject.toml` 会被打进 Python 包元数据。
**同一产品声明两个不同许可，在法律上是"许可不确定"，在工程上是自动化工具会读错。**

**建议**：以 `LICENSE` 文件为准（Apache-2.0，含专利授权条款，对企业分发更友好），
把 `pyproject.toml` 改为 `license = "Apache-2.0"`（PEP 639）或 `license = { text = "Apache-2.0" }`。

> ⚠️ **本报告只报告事实，不提供法律意见。** 选哪个许可应由项目所有者决定；
> 但**两个声明必须一致**，这一点没有争议。

---

## 6. 缺失能力清单（行业已有共识）

分级：🔴 高优先（直接限制能力上限）／🟡 中优先（影响可维护性与迭代速度）／🟢 观察项。

### 🔴 6.1 Agent 级评测集（Eval Set）

- **行业做法**：固定任务集 + 预期结果，每次改动跑回归，报告成功率/步数/token；Web agent 用 WebVoyager / WebArena / OSWorld 式基准。
- **现状**：有 `tests/test_replay.py` 的 `GOLDEN` 装置 —— 但那是**回放引擎**的金标准，
  **不是 agent 任务成功率基准**。`harness/` 的改动（prompt、上下文裁剪、步数门控）**没有任何量化回归保护**。
- **参照**：[OSWorld](https://github.com/xlang-ai/OSWorld)（~3,151 ★ ᴮ）、WebVoyager（~1,125 ★ ᴮ，已停更）。
- **可落地**：建 `tests/eval/`，放 **10–20 条**真实实施任务，**复用现成的 `ReplayClient` 做离线回放**。
  ⇒ **本项目的 replay 设施让这件事的成本远低于一般项目——这是现成杠杆。**

### 🔴 6.2 可观测性 / 追踪

- **行业做法**：OTel GenAI 语义约定打 span（模型调用、工具调用、检索），导出 Langfuse / Phoenix / LangSmith。
- **现状**：**零**。`grep` 全仓无 `opentelemetry` / `langfuse` / `trace_id` / `span(`。
  只有 `agent_audit`（谁批准了什么）与 `deepseek_usage`（计数）。
  **审计回答"谁批准了什么"，追踪回答"哪一步慢/哪一步错"——不可互替。**
- **参照**：[langfuse/langfuse](https://github.com/langfuse/langfuse)（~34,818 ★ ᴮ，可自托管）。
- **可落地**：不必上服务。**先在 SQLite 加 `agent_span` 表**
  （conversation_id / step / span_kind(model|tool|sandbox) / name / started_at / duration_ms / status / error），
  在 `harness/loop.py` 的模型调用与工具调用两处埋点，前端加"本次运行时间线"视图。
  **无外部依赖拿到 80% 价值，且为将来导出 OTLP 留好字段。**

### 🔴 6.3 MCP 客户端接入

- **行业做法**：`tools/list` + `tools/call`；客户端自己做强制审批，只把 ToolAnnotations 当提示。
- **现状**：**设计已对齐、实现为零**（四处注释写明"预留/可直接复用"）。
- **参照**：[python-sdk](https://github.com/modelcontextprotocol/python-sdk)（24,348 ★）、
  [playwright-mcp](https://github.com/microsoft/playwright-mcp)（**37,362 ★**，可直接补上通用网页操作）、
  [docker/mcp-gateway](https://github.com/docker/mcp-gateway)（~1,575 ★ ᴮ，容器化隔离第三方 server）。
- **可落地**：**只做客户端**。用一个 `mcp` 插件把 MCP tool 映射为 `ToolSpec`，
  risk 默认 `WRITE` 且需显式确认。**前提是先做"工具面裁剪"**（§6.9）——MCP 会让工具数暴涨。
  这与"暂缓 MCP 管理"不冲突：暂缓的是**管理 MCP 服务器**，接入客户端是更小的动作。

### 🟡 6.4 Agent 对话的断点续跑

- **行业做法**：LangGraph checkpointer 持久化图状态，`interrupt()` 后重启可恢复。
- **现状**：**能力割裂**。`egmp/checkpoint.py` 与 `orchestrate.CheckpointManager` 为**配置工厂**做了完整断点续跑；
  但 `harness/loop.py` 的**对话循环没有 checkpoint**。
- **可落地**：**部分借鉴**。审批令牌**不该**恢复（现有判断正确，`approvals.py` 明确写了"别顺手改成恢复"）；
  但**工具执行结果应当持久化**——把每步落库，重启后从最后一条完整消息续跑。
  **低风险、高收益，与 `conversation_context` 表天然兼容。**

### 🟡 6.5 危险操作的可回滚

- **行业做法**：Cline Checkpoints 在工作区做快照，可回滚到任一历史点。
- **现状**：有审批门防止"轻易做坏事"，**但做了之后无法撤销**。
  幂等（`writers/idempotency.py`）**不能替代回滚**——它防重复，不防"做错了"。
- **可落地**：实施场景的回滚对象是**平台对象**。为每次写操作记录"**反向操作配方**"
  （创建→记录可删除的对象 id；修改→记录原值），UI 提供"撤销本次会话的写操作"，
  **与 `agent_audit` 表天然衔接**。

### 🟡 6.6 技能包完整性与来源治理

- **行业做法**：`sha256:` digest 校验、版本/吊销、依赖解析、签名/来源证明。
- **现状**：有 ZIP 准入 + 可疑模式扫描；**未见摘要校验、版本治理、吊销机制**。
- **参照**：微软 `MCPSkillsSource` 的 digest 校验、[Open VSX](https://github.com/eclipse-openvsx/openvsx)（~2,040 ★ ᴮ）。
- **可落地**：**给 `SKILL.md` 增加 `digest` 字段**，安装时校验 ZIP sha256；技能库显示版本与来源。
  **约一天工作量，把"没被换过"变成可验证事实**；同时补**解压后大小上限**（防 zip bomb）。
  另建议把可疑模式扫描**从"静默拒收"升级为"分级披露"**：high 风险模式应当在安装时**强制展示给用户**。

### 🟡 6.7 多 Agent 分工

- **行业做法**：Cline Subagents / Agent Teams、Nanobrowser Planner/Navigator、Plan/Act 模式。
- **现状**：单循环 + `SafetyGate(max_steps=12, loop_threshold=3, loop_window=4)` —— 能**刹车**，不能**分工**。
- **参照**：[nanobrowser](https://github.com/nanobrowser/nanobrowser)（Planner/Navigator，**且同为扩展形态，参考价值最高**）。
- **可落地**：**不做通用多 agent 框架**。做**两个固定角色**即可：
  规划者（只读工具，risk 全 READ，产出步骤计划）+ 执行者（按计划逐步执行）。
  这能直接缓解长链任务在 12 步内跑不完的问题。
  ⚠️ 另注意 OpenHands V1 的洞察——**"sub-agent 就是一个普通工具"**，若真要做，按这个思路做。

### 🟡 6.8 跨会话长期记忆

- **行业做法**：LangGraph long-term memory store、Cline Memory Bank。
- **现状**：只有**会话内**摘要。
  ⚠️ 注意：`services/accounts.py` 的「记忆盒子」是**账号分组语义**，与 agent 记忆无关，**不要混淆**。
- **可落地**：最小版本——**`user_fact` 表 + `memory_remember` / `memory_recall` 两个工具**。
  **可审计、可解释、可在 UI 查看删除，比向量库更合合规要求。**

### 🟢 6.9 工具面的按需裁剪

- **行业做法**：微软只在"至少一个技能带资源"时注册 `read_skill_resource`，只在"带脚本"时注册 `run_skill_script`。
- **现状**：`harness/loop.py` 已接受 `plugins` 白名单参数（注释说明是给"按页面裁剪工具面"留的口子），
  但**未见按页面/场景实际启用**。20 个工具全量注入是持续 token 成本。
- **可落地**：把白名单接到**当前页面/任务类型**（如"账号页只挂 accounts 插件"），同时降低 token 与误调用率。
  **这也是接 MCP 之前的必要前置。**

### 🟢 6.10 并行工具调用

- **行业做法**：一次返回多个 tool_call，框架并发执行。
- **现状**：`harness/loop.py:211` 是串行 `for call in assistant["tool_calls"]`。
- **可落地**：改 `asyncio.gather`。**收益**：模型一次发 N 个独立调用时延迟从 N× 降到 1×。
  **风险**：确认门 + 并发的交互需想清楚（多个风险工具同时被拦时的 pending 展示顺序）。
  **低成本，半天量级。**

### 🟢 6.11 精确 token 计数

- **现状**：`harness/context.py` 用"CJK 字符数 + 英文词数"估算，
  而 `context_threshold=0.8` × `context_window_tokens=131072` 的判定建立在此之上。
- **风险**：估偏低→该折叠没折叠→请求超长被拒；估偏高→过早折叠→无谓摘要开销与信息损失。
- **可落地**：换 tiktoken（`cl100k_base` 近似）或加安全系数。**半天量级。**

---

## 7. 优先级建议

| 优先 | 动作 | 理由 | 参考 | 粗估成本 |
|---|---|---|---|---|
| **1** | **`tests/eval/` 10–20 条任务，复用现成 replay** | **唯一能回答"我这次改动让 agent 变差了吗"的手段**；且本项目**已有 `ReplayClient` + GOLDEN**，是别人没有的杠杆 | OSWorld / WebVoyager 方法论 | 2–3 人日 |
| **2** | `agent_span` 表 + 埋点 + 运行时间线视图 | 无外部依赖拿到追踪价值；是后续所有优化的度量基础 | Langfuse / OTel GenAI | 1–2 人日 |
| **3** | 精确 token 计数 | 防"请求超长"这类硬故障，成本极低 | tiktoken | 0.5 人日 |
| **4** | 并行工具调用 | 多工具场景延迟 N× → 1× | 主流框架 | 0.5 人日 |
| **5** | 技能包 `digest`(sha256) + 解压上限 + 安装时风险披露 | 投入最小，把"没被换过"变成可验证事实 | 微软 `MCPSkillsSource` | 1 人日 |
| **6** | **沙箱内做多步骤工具编排**（让 `insight_run` / `factory_create` 的中间产物不进上下文） | **既提能力又降成本**；沙箱基础设施已就绪 | Anthropic Programmatic Tool Calling、smolagents | 3–5 人日 |
| **7** | 对话级工具结果持久化 + 崩溃续跑 | 消灭"跑 8 步崩了从头来" | LangGraph checkpointer（**部分借鉴**） | 2–3 人日 |
| **8** | 写操作"反向配方" + 会话级撤销 UI | 补上实施场景最关键的安全网 | Cline Checkpoints（**业务化改造**） | 3–5 人日 |
| **9** | 审批"本会话内信任该工具"的快捷授权 | 减少确认疲劳（现在每次都重点） | Claude Desktop / Cursor / VS Code | 1–2 人日 |
| **10** | MCP **客户端**接入（一个 `mcp` 插件） | 立刻获得 playwright-mcp 等外部工具面 | python-sdk / playwright-mcp | 3–5 人日 |
| **11** | 规划者/执行者双角色拆分 | 缓解 12 步硬约束 | Nanobrowser | 3–5 人日 |
| **12** | 受限令牌 / AppContainer 沙箱升级（**可选**） | 把"限损层"升级为"权限边界"，但需提权，与零配置冲突 | Codex `elevated`/`unelevated` | 5–10 人日 + 用户配合 |
| — | 修 `LICENSE` / `pyproject.toml` 许可冲突 | 消除"许可不确定" | §5 | 0.5 人日 |

**不建议做的事**（本次调研支持的一票否决）：

- ❌ **不做插件市场 / 自动发现 / 版本协商矩阵** —— Dify / n8n / LobeHub / Cherry Studio 的市场路线**只有超大规模团队才能维持**，
  而本项目插件作者只有 1 人。**作者本人的研究已论证过这一点，本次调研支持该结论。**
- ❌ **不做服务端 MCP**（把 Workbench 工具暴露给外部 agent）—— 当前无需求，
  且会把内部业务端点变成对外的稳定 API 承诺。
- ❌ **不做指纹伪装 / 反检测**（Camoufox / patchright 路线）—— 与"工程师合法操作自己的账号"场景不符，
  且引入合规风险。
- ❌ **不做通用多 agent 编排框架** —— 复杂度对单场景是负担。
  **佐证**：`microsoft/autogen` 最后推送停在 **2026-04-15**、`openai/swarm` 停在 **2026-04-15**（已被 Agents SDK 取代）、
  `Roo-Code` 停在 **2026-05-15**、`MetaGPT` 停在 **2026-01-21** —— **都是复杂度反噬的信号。**

---

## 8. 数据可信度

### 8.1 采集方式

| 渠道 | 手段 | 覆盖 |
|---|---|---|
| **A（主代理）** | `GET api.github.com/repos/{owner}/{repo}` 逐仓库直接实测（PowerShell `Invoke-RestMethod` + `web_fetch`） | 45 个仓库的 ★ / 语言 / 许可 / 最后推送 |
| **B（子代理）** | `api.github.com` + `ungh.cc` + `img.shields.io` + 各项目官方文档实读 | 40+ 仓库，并实读 MCP 规范、Agent Skills 规范、微软 Agent Framework、Codex Windows、Cline、OpenHands、goose 文档 |

**交叉核验结果**：两渠道在**所有重叠项目上一致**（例如 browser-use 115,369 vs 115,368；
`anthropics/skills` 177,188 vs 177,074；`obra/superpowers` 288,874 vs 288,760）。
⇒ ★ 数据可视为**精确值**（采集时间 **2026-09-20**）。

**组织改名重定向（本次调研实测到 6 处）**：`block/goose` → **`aaif-goose/goose`**、
`All-Hands-AI/OpenHands` → `OpenHands/OpenHands`、`lobehub/lobe-chat` → `lobehub/lobehub`、
`geekan/MetaGPT` → `FoundationAgents/MetaGPT`、`openinterpreter/open-interpreter` → `openinterpreter/openinterpreter`、
`microsandbox/microsandbox` → `superradcompany/microsandbox`。
goose 迁入 `aaif-goose` 印证了 MCP 捐给 **AAIF** 这件事（goose 是 AAIF 创始项目之一）。

### 8.2 本报告自我修正的两处错误（保留记录）

| # | 初版写的 | 实际 | 根因 |
|---|---|---|---|
| 1 | `microsoft/playwright-mcp` **API 404，未核实，不基于它做判断** | **存在**：37,362 ★ / TypeScript / Apache-2.0 / 推送 2026-09-18 | 该请求恰好撞上 GitHub 未认证 API 的**限流**（60 次/小时），返回被误读为 404。**教训：404 与 429/403 必须区分；批量探测要把最后几个失败项重试一次。** |
| 2 | 未收录 `anthropics/skills`、`obra/superpowers` | **177,188 ★** 与 **288,874 ★** | 初版检索面偏重"框架与工具"，漏掉了**技能生态**这个体量更大的分支 |

### 8.3 未核实项（逐条标注）

标 ᴮ 的数字为**渠道 B 报告、渠道 A 因限流未能复核**：
`openai/codex` ~125,308、`google-gemini/gemini-cli` ~107,076、`sandboxie-plus/Sandboxie` ~19,483、
`langfuse/langfuse` ~34,818、`humanlayer/humanlayer` ~11,587、`xlang-ai/OSWorld` ~3,151、
`MinorJerry/WebVoyager` ~1,125、`eclipse-openvsx/openvsx` ~2,040、`Steel-dev/steel-browser` ~7,668、
`pyodide/pyodide` ~14,836、`google/gvisor` ~19,360、`firecracker-microvm/firecracker` ~36,825、
`Eigenwise/atomic-agents` ~6,251、`ultrafunkamsterdam/undetected-chromedriver` ~12,845、
`modelcontextprotocol/modelcontextprotocol` ~9,256、`docker/mcp-gateway` ~1,575。

其余未核实项：

1. **OpenAI Codex Windows 沙箱**：`openai.com/index/building-codex-windows-sandbox/` 正文页
   **主代理三次尝试均被 HTTP 403 拦下**，机制描述取自官方文档镜像（渠道 B 实读）。
   **文章存在性与要点已核实，原文未直接读取。**
2. **各项目桌面壳技术**（Electron vs Tauri vs 扩展）：未逐一抓取构建配置核实。
3. **`dify` / `n8n` / `jan` / `lobehub` 的许可**：API 返回 `NOASSERTION`（`license: null` 或非 SPDX 标识），
   **未逐字核实许可全文**。**`daytonaio/daytona` 未返回许可字段**；**`anthropics/skills` 许可为 `null`**。
   ⇒ **复用任何一行代码前必须自己读该项目的 LICENSE 与 NOTICE。**
4. **`microsandbox` 的 Windows 支持矩阵**：未取到。
5. **`skills/security.py` 的 ZIP 准入细节**：仅确认存在可疑模式扫描（`eval(` 等标记 high），
   **未逐行核实**解压大小/文件数上限的实现。
6. **MCP ToolAnnotations 与 HITL 规范原文**：来自渠道 B 对规范页（2025-06-18 与 draft）的实读回报，
   **主代理未逐字复核规范全文**。
7. **「六平面隔离无开源同构体」「平台洞察图谱化无同类」**：**基于本次检索范围的判断，非穷尽性证明。**

### 8.4 许可协议红线（若未来要复用代码）

| 协议 | 项目 | 可否复用代码 |
|---|---|---|
| MIT / Apache-2.0 | 上表大部分 | ✅ 可以（保留版权声明） |
| **AGPL-3.0** | **Skyvern**、**cherry-studio** | ❌ **不可** —— AGPL 传染性对闭源分发是致命的 |
| GPL-3.0 | Sandboxie | ❌ **不可**（且是内核驱动级，集成成本高） |
| MPL-2.0 | camoufox、pyodide | 🟡 文件级 copyleft，隔离使用需谨慎 |
| CC-BY-4.0 | **autogen**、OmniParser | ⚠️ **这不是软件许可**，通常只覆盖文档；复用代码前必须逐文件确认 |
| fair-code / 自有 / 未标注 | n8n、dify、lobehub、jan、**anthropics/skills**、daytona | ❌ **默认为不可复用**，需单独确认 |

### 8.5 本报告的已知局限

1. **机制描述来自官方文档与公开资料**，除本仓外**未做源码级核实**。
2. **"行业普遍做法"是判断不是统计** —— 未对 GitHub 全量 agent 项目做抽样调查。
3. **★ 数是动态值**，本表价值在于**对比关系**（谁比谁大、谁在停滞），不在具体数字。**引用前请重新采集。**
4. **"谁在停滞"的判断依据是 `pushed_at`**，这对**低频但健康**的项目可能不公平。
