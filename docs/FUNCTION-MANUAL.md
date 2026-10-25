# akso-workbench 功能手册（用户视角 + 实现思路）

> **它是什么**：实施工程师的**单机工作台**（Windows 桌面应用）。三件事：
> ① 摸清一个平台上有什么对象/字段/状态/流转；② 集中管理测试账号；③ 让对话 Agent 替你跑这些活。
>
> ⚠️ **当前状态**：Python 版是**唯一可用的实现**（正在向 DSH 插件重构，TS 侧目前只有 eGMP 客户端）。
> 本手册按"**现在能用的功能**"写，末尾给"重构后这些功能落在哪"——两者对照着看，功能才不会在重写中走样。

---

## 0. 一分钟上手

1. 启动工作台（Web 模式默认 `http://127.0.0.1:18765`；桌面版是本地窗口）。
2. 「账号中心」→ 用**云端账号库**登录（设备码流程）→ 账号列表出现。
3. 「对象分析」→ 填平台对象编码（如 `capa_plan__c`）→ 跑「蜘蛛五步」→ 得到关系网络图 + `spider-network.drawio` + 生命周期流转图。
4. 「对话」→ 直接说"帮我把 X 对象的所有字段列出来"——Agent 会调工具去做，**改数据的动作会先问你**。

---

## 1. 功能地图

| 你能做的事 | 界面位置 | 背后是什么 |
|---|---|---|
| 摸清对象关系与状态流转 | 对象分析 | 5 个 `insight_*` 工具 + networkx |
| 看历史分析产物 | 对象分析 → 产物 | `insight_artifacts` / `insight_read_artifact` |
| 管理测试账号 | 账号中心 | `accounts_list` / `accounts_login`（读云端库） |
| 让 Agent 干活 | 对话 | 20 个内置工具 + 确认门 + 审计 |
| 传文件给 Agent 看 | 输入框（拖/粘/选） | 附件落盘 + 解析坐标 |
| 看每轮花了多少 token | 对话用量行 / 运行时间线 | span 记录 + 校准系数 |
| 让 Agent 记住事情 | 对话 | `memory_*` 三个工具 |
| 跑固定脚本 | 技能 | `list_skills` / `read_skill` |

---

## 2. 对象分析（原「平台洞察」）

**你会看到**：填对象编码 → 点「开始织网」→ 出一张关系网络图、一份 `.drawio` 文件、一张生命周期流转图。

**实现思路**

- 代码：`workbench/services/egmp/insight/`（`kernel` 认平台、`queries` 查元数据、`replay` 复算）。
- ★ **五步不是"五个接口"**：它是**固定的调查顺序**（对象 → 字段 → 关系 → 状态 → 流转），
  顺序错会拿到"看着像但不对"的图（例如先拉关系后拉字段，字段类型缺失，连线标签就空了）。
- 图用 networkx 算，`drawio` 是**可再编辑**的产物——用户要能改，所以不能只给 PNG。
- ★ **产物是文件不是数据库记录**：落盘在 `…/Workbench/洞察/`，因为要能直接打开、能进 git。
- **判据**（Python 侧）：`pytest tests/test_insight*.py`；端到端 `tools/verify_insight_preview.py`（18 项，
  断言"渲染出来的 == 源文件里的"，**不断言产品文案**）。

---

## 3. 账号中心

**你会看到**：云端账号库的账号列表（只读）。旧本地库可以一次性「本地推到云端」。

**实现思路**

- 代码：`workbench/services/vault.py` + `api/routes_vault.py`。
- **只读展示**：日常增删改在云端控制台做——**两个地方都能改，就一定会分叉**。
- 登录走**设备授权流**（与扩展同一套），工作台里不存你的密码。
- **判据**：`pytest tests/test_vault.py tests/test_vault_push.py`；页面 `tools/verify_accounts_ui.py`（18/18）。

---

## 4. 对话 Agent（20 个工具）

**你能看到的工具分类**

| 类别 | 工具 | 用它能干什么 |
|---|---|---|
| 平台分析 | `insight_accounts` `insight_run` `insight_runs` `insight_artifacts` `insight_read_artifact` `insight_read_config` | 跑分析、看历史、读产物 |
| 账号 | `accounts_list` `accounts_login` | 列账号、取会话 |
| 附件 | `attachment_list` `attachment_read` | 看你传的文件（可按页/按行/按区域取） |
| 记忆 | `memory_add` `memory_search` `memory_delete` | 记住"这个平台的字段命名习惯"之类 |
| 技能 | `list_skills` `read_skill` | 读固定作业流程 |
| 本地 | `read_local_file` `read_web_page` `web_search` `get_current_time` | 读文件 / 读网页 / 搜索 |
| 交互 | `request_user_confirmation` | **模型先问再做** |

**实现思路（这一段是全仓最值钱的部分）**

1. **工具只有一个定义处**：`workbench/plugins/*.py` 的 `ToolSpec`。
   function calling 的 schema、HTTP 调用面、审计表**三处都从它派生**。
   ★ 曾经有两套并存，后果是"对话里的模型看不到任何平台能力"——而**测试全绿**。
2. **风险分级 `risk`**：`READ` / `WRITE` / `DANGEROUS`。风险工具**没有令牌就不执行**（硬闸门在 `ToolHub.execute`），
   不是提示层。★ 2026-09-22 起产品里**没有任何写工具**，所以 `DANGEROUS` 分支只有测试在走——这是刻意状态。
3. **可信度 `trust`**：`LOCAL` / `UNTRUSTED`，**没有缺省值**。网页/搜索结果一律 `UNTRUSTED`。
   ★ 缺省成 `LOCAL` 会把外部内容**静默**说成自己边界内的数据；同一类字段，缺省值的后果可以完全相反。
4. **可逆性 `reversibility`**：会改状态的工具必须声明"这次做错了能不能撤"，能撤要给**具体撤销指令**。
   ★ 幂等 ≠ 回滚：再跑一遍不能算"可逆"。
5. **两条确认通道**（别混）：
   - **模型发起**（`needs_confirmation`）：模型"先问再做"，不是安全边界；
   - **风险门**（`approval_required`）：没令牌**就不执行**，这才是边界。
6. **审计**：每次调用落 `agent_audit`，记"谁、什么参数、什么结果、撤销指令"。**不记凭据**。

**判据**：`tests/test_trust_labels.py`（12）、`tests/test_undo_recipe.py`（8）、`tests/test_agent_approval.py`、
`tests/test_user_confirmation.py`。

---

## 5. 上下文与成本（为什么它不烧钱）

**你会看到**：用量行显示"本轮 / 上下文 / 缓存命中率"；运行时间线里每次调用有 `prompt_tokens`。

**实现思路**

- ★ **已发出的 messages 前缀永不改写**，上下文只能 `append`。道理是钱：DeepSeek 前缀缓存命中价差 **50 倍**，
  而"每轮按最近 N 轮裁剪"会让前缀每轮滑动 ⇒ **命中率恒为 0**。
- 摘要**冻结**（`summarized_upto` 只前进），装配顺序固定 `[system] + [摘要?] + [原文] + [新消息]`。
- **两个 token 量分开算**：消息一份、请求（含工具 schema）一份。折叠判定用**全量**——
  工具 schema 约 2,100 token，是同一段正文的 11 倍；不算它，窗口设小就**永远不折叠**。
- 估算系数用 **API 自报的 `prompt_tokens` 校准**（中位数、样本 < 5 就不校准、夹在 [0.5, 2.0]）。
- **判据**：`tests/test_cache.py`、`tests/test_context_tokens.py`（13）、`tests/test_token_calibration.py`（12）。

---

## 6. 附件

**你会看到**：把图片/PDF/Excel 拖进输入框，Agent 就能"看到"并按页/按行引用。

**实现思路**

- 代码：`services/attachments.py`（落盘 + 拒收）、`services/parsing.py`（解析 + **坐标**）、`api/routes_attachments.py`（扼流点）。
- ★ **每个解析片段都记 `offset`/`chars`**，所以"第 3 页第 2 段"能**切回原文**；不记坐标就只能靠模型复述。
- ★ **写给模型的那句话也是链路的一部分**：一处措辞不当，模型会**撤回自己刚答对的内容**（"前面是瞎猜的"）。
- **判据**：`tests/test_attachments.py`（26 条，含提示措辞）+ `tools/verify_attachments.py`（24 项端到端）。

---

## 7. 停止、续跑与"父死子退"

**你会看到**：点「停止」后，Agent 立刻停手，未执行的调用被标成"未执行"。

**实现思路**

- 停止不是"扔个异常"：**未执行的调用要回填成观测**、assistant 消息要落库、pending 变 `expired`、事件流以 `cancelled` 结尾。
- 进程被杀时，库里会留下"有调用、没结果"的**悬空历史**——启动时 `repair_dangling` 补一条**"未执行"**的观测。
  ★ **绝不重放**那些调用（无法知道它执行到哪；重放一次写操作的代价与"少一条观测"不是一个量级）。
- 子进程（sidecar）用 `wait_parent()` 盯着父进程句柄，父被强杀时自己退出。
- **判据**：`tests/test_agent_stop.py`（20）、`tests/test_agent_resume.py`（8）、`tests/test_lifecycle.py`。

---

## 8. 重构后这些功能落在哪（DSH 插件映射）

| 现在（Python 单机） | 重构后 | 谁负责 |
|---|---|---|
| `harness/` agent 循环、上下文、缓存 | **删掉** | DSH 的 agent 循环 |
| `sandbox/`（限损层） | **删掉** | DSH 三档沙箱 |
| `skills/`、MCP 客户端 | **删掉** | DSH 的 skill / MCP |
| `static/` 对话 UI | **删掉** | DSH 的 Web GUI |
| 20 个工具 | **`ctx.tools.register`** 重新注册 | 我们（`akso-workbench-ts`） |
| 确认门 | **`ctx.approval` + `tools/pre-execute`** | DSH 提供门，我们给理由 |
| 审计 | 落 DSH 的日志/审计 | DSH |
| eGMP 域逻辑（登录/查询/织网） | **原样移植成 TS** | 我们 |
| 账号库 | **改为调 akso-cloud** | 我们（不再两处实现） |

★ 一条已被证实的经验：**"函数对"与"链路通"是两件事**。
搬迁时先有端到端判据（真起服务、真发请求），再谈单个函数的单测——否则会得到"全绿但用不了"。

---

## 9. 常见故障

| 现象 | 先查什么 |
|---|---|
| 改了代码界面没变 | **先怀疑跑的是旧产物**（旧进程/旧 exe），不要先怀疑代码 |
| 对话里模型"没有工具" | 插件是否注册进 `_MODULES` / `server.spec` / `REQUIRED_TOOLS`（三处缺一就静默失效） |
| 快捷键/按钮点了没反应 | 界面文案是承诺：文案写了的行为必须在代码里有实现 |
| 端口被占用（Web 模式 18765） | 旧实例还活着（`Get-NetTCPConnection -LocalPort 18765`） |
