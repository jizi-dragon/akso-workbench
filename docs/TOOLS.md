# 工具注册表与治理（TOOLS）

_2026-09-16 建立。锚定 `plugin_api.ToolHub` + `workbench/plugins/` + `workbench/approvals.py`。_

> **这份文档回答**：现在**到底有哪些工具**、谁负责、**怎么加一个新的**、加完必须跑什么。
> 它存在的直接原因：2026-09-15 加自建 `web_search` 时插件与工具数变了，而四处文档还写着旧数字——
> 用户的原话是「**之前有一篇调研提到了创建了 19 个 tools，我完全不知情**」。
> 结论是：**清单与数字不能靠人记**，所以下面的清单块由脚本生成、由机检守着（§1、§4）。

---

## 1. 先看这一条命令

```powershell
# 检查：文档与运行期中枢是否一致 + 各文档里的"N 个工具/插件"是否对得上（对不上 exit 1）
.\.venv\Scripts\python.exe tools\gen_tools_doc.py

# 改完插件后：用运行期中枢重新生成下面的清单块（不要手改清单）
.\.venv\Scripts\python.exe tools\gen_tools_doc.py --write
```

三条互补的门禁，别只跑一条：

| 门禁 | 管什么 | 命令 |
|---|---|---|
| **本文档清单** | 文档里的清单/数字 == 运行期中枢 | `python tools\gen_tools_doc.py` |
| **打包收录（P7）** | 插件模块进没进 `server.spec` 的 `hiddenimports` | `python tools\verify_packaging.py` |
| **启动自检** | 应用起来后缺没缺必需工具 | `GET /api/agent/selfcheck`（启动日志里也有一条） |

## 2. 全量清单（生成块）

> ⚠️ **下面这一块不要手改**：它由 `tools/gen_tools_doc.py` 从运行期工具中枢生成。
> 手改会在下一次 `--check` 时被判为不一致——这正是设计意图（清单只能来自代码）。

<!-- TOOLS-TABLE:BEGIN —— 由 tools/gen_tools_doc.py 生成，勿手改；改插件后跑 --write -->
> 实测（由运行期工具中枢生成）：**5 个插件 / 23 个工具**。风险分布：20 read · 2 write · 1 dangerous；装配失败：无。

| 插件 | 标题 | 版本 | 工具数 | 负责什么 |
|---|---|---|---|---|
| `builtin` | 内置工具 | 1.1.0 | 4 | 与业务无关的通用能力：时间、读本地文件、读网页正文、请求用户确认 |
| `websearch` | 联网检索 | 1.1.0 | 1 | 自建联网检索（DeepSeek API 不支持内置 web_search，故由本工作台实现） |
| `insight` | 平台洞察 | 1.0.0 | 6 | 读平台对象元数据、跑五步织网洞察、取回产物（akso-cc 能力原生化） |
| `factory` | 配置工厂 | 2.0.0 | 7 | 蓝图规格与预览暂存、真机批量创建（akso-auto 能力原生化） |
| `accounts` | 快捷登录 | 1.0.0 | 5 | 账号库清单、平台登录验证、托管浏览器会话（quick-login 能力内化） |

| 工具 | 插件 | 风险 | 需确认门 | 超时 | 干什么 | 参数 |
|---|---|---|---|---|---|---|
| `accounts_browser_sessions` | accounts | read | — | 20s | 托管浏览器会话状态墙（单账号或全部在线会话） | `account_id` |
| `accounts_list` | accounts | read | — | 15s | 列出工作台账号库里的账号（id/用户名/环境/分配池） | — |
| `accounts_login` | accounts | read | — | 90s | 用账号库的账号验证平台登录连通并预热 token（httpx 直连，不开浏览器） | `account_id` |
| `accounts_browser_close` | accounts | **write** | ✅ 宿主拦下 | 30s | 关闭某账号的托管浏览器窗口（登录态保留）。注意：用户手动开的窗口也会被关掉 | `account_id` |
| `accounts_browser_open` | accounts | **write** | ✅ 宿主拦下 | 180s | 在内置 Chromium 里以该账号身份打开平台（自动登录 + 会话持久化） | `account_id`、`headful` |
| `get_current_time` | builtin | read | — | 5s | 获取当前日期与时间（本地时区） | — |
| `read_local_file` | builtin | read | — | 30s | 读取本地文本文件。**敏感路径一律拒绝**（数据库/登录态/token 缓存/账号备份/浏览器 profile/SSH 与云凭据） | `path` |
| `read_web_page` | builtin | read | — | 40s | 读取公开网页正文（用于核实 NMPA/FDA/ICH 等官方监管页面原文），返回标题、来源与正文文本 | `url` |
| `request_user_confirmation` | builtin | read | 🙋 模型可主动问 | 10s | 【高风险任务前置】在执行敏感、不可逆或会影响真实平台的操作**之前**，先向用户弹确认框并**等待他当场答复**（同意 / 拒绝），也可以用来**让用户在卡片上挑一个账号 / 环境** | `question`、`details`、`target_tool`、`target_args`、`timeout_s`、`options_source` |
| `factory_blueprint_spec` | factory | read | — | 20s | 【先看这个】返回 eGMP 配置蓝图的**完整规格**：每个键叫什么、什么类型、哪些必填、dataType/stepType 等枚举的含义、编码约定、校验规则、以及一份能直接跑通的最小示例 | — |
| `factory_jobs` | factory | read | — | 15s | 最近的配置工厂任务（状态/时间），用于确认此前是否已经创建过 | — |
| `factory_list_blueprints` | factory | read | — | 20s | 列出已暂存的蓝图（id/名称/涉及对象）。含**用户在配置工厂页面上传的**那些，所以用户说自己传了一份时用它确认 | `limit` |
| `factory_preview_blueprint` | factory | read | — | 90s | 提交一份蓝图：**校验 → 暂存 → 出一份给用户看的审阅清单**，一次完成 | `blueprint` |
| `factory_resolve_object` | factory | read | — | 60s | **用户说的对象在平台上到底存不存在**——按 code 或名称查。存在：回 id/name（工厂会复用它） | `account_id`、`query` |
| `factory_status` | factory | read | — | 30s | 某个工厂任务的详情：状态、日志末尾、断点续跑 checkpoint | `job_id`、`log_lines` |
| `factory_create` | factory | **dangerous** | ✅ 宿主拦下 | 900s | 【危险】在真实平台上按蓝图批量创建对象/字段/选项集/生命周期/工作流/布局/菜单 | `blueprint_id`、`account_id` |
| `insight_accounts` | insight | read | — | 15s | 列出工作台账号库里的账号（id/用户名/环境），用于确定 account_id | — |
| `insight_artifacts` | insight | read | — | 30s | 列出某次洞察任务的产物文件（含 proc.log 与工作区落点） | `job_id` |
| `insight_read_artifact` | insight | read | — | 30s | 读取某件洞察产物正文（spider-network.md / step2-status-table.md / *.drawio 等） | `job_id`、`name` |
| `insight_read_config` | insight | read | — | 60s | 只读读取某个 eGMP 对象的元数据（对象结构/基本信息） | `account_id`、`code` |
| `insight_run` | insight | read | — | 300s | 跑一次平台洞察：login=只验证登录连通 | `account_id`、`objects`、`command` |
| `insight_runs` | insight | read | — | 15s | 最近的洞察任务列表（状态/覆盖度/工作区），用于确认上次是否跑成 | `limit` |
| `web_search` | websearch | read | — | 120s | 联网检索网页（返回标题/链接/摘要）。适合查法规更新、官方公告、报错与版本信息这类训练数据里没有或已过时的内容 | `query`、`count`、`freshness` |
<!-- TOOLS-TABLE:END -->

## 3. 风险分级与确认门（决定一个工具"能不能自己动手"）

| 风险 | 含义 | 行为 |
|---|---|---|
| `read` | 只读：查询、读元数据、跑分析 | 直接执行 |
| `write` | 会改变本机或平台状态的常规写（如开/关托管浏览器） | **必须过一次确认门** |
| `dangerous` | 一次误调用不会报错、**会真的建出来**的批量写入（如 `factory_create`） | 同上，且**永不进入 Monitor 的可执行回放步骤** |

确认门（`approvals.py`）的语义：**单次令牌**（用完即废）· **5 分钟过期** · **绑定工具名 + 参数指纹**
（换个参数要重新申请）。对话链路发 `approval_required` 事件、**这一轮就地等答复**（放行后由服务端
用相同参数执行，见 [CORE-ARCHITECTURE](CORE-ARCHITECTURE.md) §确认门）；HTTP 链路回 `409 + pendingId`，
两条链路共用 `/api/agent/pending/*`。**没有令牌，WRITE/DANGEROUS 的 handler 根本不会被调用**
（回归断言的是"handler 调用记录为空"，不是返回码）。

可选的前置检查 `ToolSpec.precheck`：只读、**在确认门之前**跑，判定"这次调用注定失败"时
**不再问用户放行**（理由直接成为模型观测）。它只省一次白问，不承担拦截责任。

> `factory_create` 与引擎侧 `confirmed=true` 是**两条独立的闸门**，不是替代关系：
> 工具级确认门挡 Agent 链路，引擎侧挡"绕过页面直接打 HTTP"的人。

## 4. 加一个工具 / 插件的完整清单

写代码只要一处（`workbench/plugins/*.py` 里一个 `ToolSpec`），但**必须同步的东西有五处**——
漏任何一处都是"装完静默没功能"（真踩过，见 [PITFALLS](PITFALLS.md) #32）：

| # | 位置 | 为什么 |
|---|---|---|
| 1 | `workbench/plugins/<插件>.py` 的 `ToolSpec`（**必须显式声明 `risk`**） | 唯一的能力定义处 |
| 2 | `workbench/plugins/__init__.py::_MODULES`（新插件才要） | 装配是显式清单，没有自动发现 |
| 3 | `workbench/server.spec` 的 `hiddenimports`（新插件才要） | 动态导入 PyInstaller 看不见 → 装完没功能且不报错（P7 守着） |
| 4 | `workbench/api/routes_agent.py::REQUIRED_TOOLS`（关键工具才要） | 缺了就启动自检报红 |
| 5 | `docs/TOOLS.md` 清单块 | 跑 `gen_tools_doc.py --write`，别手写 |

写一个新插件的契约细节（`Plugin` / `ToolContext` / `audit_extra` / 依赖方向）见
**[PLUGIN-SDK.md](PLUGIN-SDK.md)**。三条硬纪律先记住：

1. **依赖方向单向**：`plugins → plugin_api`，`plugin_api.py` 里不许出现平台术语、不许 import 插件、连 `db` 都不 import；
2. **同步阻塞调用必须挪线程**：handler 是 `async`，而 `egmp.client` / `browser_pool` / `proc` 都是同步的——一律 `asyncio.to_thread`，否则整个服务卡住（不是报错）；
3. **审计维度不许含凭据**：`audit_extra` 只抽环境/对象/账号，`redact_args` 会再过一遍但别指望它兜底。

## 5. 运行时怎么自己看

| 端点 | 回答什么 |
|---|---|
| `GET /api/agent/tools` | **能调什么**：`{plugins, tools:[{name,description,risk,plugin,args}], failures}` |
| `GET /api/agent/plugins` | **装了哪些插件**：插件维度视图（含版本、装配失败） |
| `GET /api/agent/selfcheck` | **缺没缺**：`REQUIRED_TOOLS` 覆盖情况 |
| `GET /api/agent/audit` | 谁在什么时候调了什么（写操作带结果与耗时；列表只回摘要，细节走 `/audit/{id}`） |
| `GET /api/skills/summary` | **装了哪些 Skill、它们贡献了什么工具**（含 `script_tools`：哪些工具会执行上传的代码） |
| `GET /api/sandbox/runs` | 脚本在沙箱里跑过什么（策略、退出码、耗时、是否树杀） |

> ⚠️ **`/api/agent/*` 只承诺产品自带的工具**。用户从共享库装的 Skill 也会出现在
> `hub.specs()` 里、也走同一套确认门与审计，但它们**不进 §2 的生成清单**——
> 否则每台装过 Skill 的机器都会让 §1 的门禁变红（`gen_tools_doc.py` 只数 `hub.core_specs()`）。
> 两者的分工见 [PLUGIN-SDK.md](PLUGIN-SDK.md) §1 的两层表。

### 5.1 Skill 装上来的工具，算不算"工具"？

算，且**治理口径完全相同**——同一个中枢、同一套风险分级、同一道确认门、同一条审计。
区别只在**来源与可变性**：

| | core（产品自带） | installed（Skill 提供） |
|---|---|---|
| 谁定义 | `plugins/__init__.py::_MODULES` | 用户上传的 `SKILL.md` |
| 何时变 | 导入期一次，之后只读 | **运行期可变**（装/卸/换版本） |
| 能不能卸 | ❌ 只能**禁用** | ✅ 一键卸载 |
| 风险下限 | 按工具语义定 | 声明式 ≥ 被包装工具；**脚本式 ≥ `write`** |

**脚本式工具的风险为什么必须 ≥ `write`**：它执行的是**上传的代码**，
而"这段代码只读"无法被静态证明。标成 `read` 等于给模型一张"不经确认随便跑"的通行证。
声明式工具可以 `read`，因为它转发的那个工具的语义是我们自己定的。

## 6. 变更纪律（一句话版）

> **工具是产品面，不是实现细节。** 加/删/改工具 = 改产品能力，必须同时更新本文档清单、
> 想清楚风险分级（`read`/`write`/`dangerous` 决定它能不能自己动手），并跑 §1 的三条门禁。
> 只改 `plugins/*.py` 而不跑生成脚本，会在下一次检查时红——这是刻意的。

## 7. 历史：这份文档为什么存在

| 时间 | 事件 |
|---|---|
| 2026-09-15 | 工具插件层落地：`harness/tools.py` 与 `routes_agent.py` **两套并存的工具表**收口为单一事实源 `ToolHub`；同时新增自建 `web_search` |
| 2026-09-15 | 插件 **4 → 5** 个、工具 **19 → 20** 个；但 CHANGELOG / PLUGIN-SDK / API / CODEBASE_OVERVIEW 四处仍写旧数字 |
| 2026-09-16 | 用户发现"我不知道有 19 个 tools" → 建立本文档 + `gen_tools_doc.py` 机检；四处旧数字已按实测订正（当期 5 插件 / 20 工具） |
| 2026-09-20 | 新增 `request_user_confirmation`：工具 20 → 21 |

**教训**：*"写在文档里的数字"和"代码里的数字"是两个会漂移的事实源*——要么让文档从代码生成，
要么迟早出现一次"用户比维护者更早发现不一致"。

> 上面历史行里的数字刻意不写成「N 个工具」那个句式：`gen_tools_doc.py` 的正则
> 只认它，核对的是**当前**数量。历史行若照抄那个句式，每次加工具都会把门禁弄红，
> 而"红"的原因是它在核对一个**已经过去的事实**——那种红会训练人忽略门禁。
