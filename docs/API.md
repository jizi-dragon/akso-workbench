# API 契约（API）

> **本文档的读者**：调接口的前端/脚本作者、排查「接口为什么返回这个」的人。
> 变更频率 O(接口)：**新增/修改端点必须同步更新本文档**。
>
> **本文档是权威契约**。FastAPI 自动生成的 `/openapi.json` 与 `/docs` 只覆盖路径与 pydantic 模型，
> **表达不了** SSE 事件序列、长轮询挂起语义、`seq` 游标与产物下载路径守卫——这三类恰是最易踩坑处，见 §12。
>
> 相关文档：[SCHEMA.md](SCHEMA.md)（表与跨进程数据契约）、[CONFIG.md](CONFIG.md)（端口与环境变量）、
> [CODEBASE_OVERVIEW.md](CODEBASE_OVERVIEW.md)（代码地图与风险索引）、
> [MCP.md](MCP.md)（MCP 配置与安全模型）。
>
> ⚠ **2026-10-09：浏览器扩展已整体移出本仓库**（用户决策：桌面端与扩展不耦合，
> 本仓只需账号管理页承接云端数据环境）。随之删除的是 `/extension/*` 整段蓝图（原 §11）、
> 访问令牌中间件（`api/extension_auth.py`）、以及随包携带扩展的打包步骤。
> 本文档中原「执行面」整节与 §12.3（长轮询与 `seq` 游标）一并移除；**该节之后的编号已顺移**（现为 §11 监听录制 … §18 运行日志）。

> **2026-10-19：内置浏览器 / Monitor 整体下线**（用户确认不再需要 UI 自动登录 / 页面抓取 / 截图）。
> `routes_browser` / `routes_monitor` / `browser_pool` / `autologin` / CDP 18766 一并删除；
> `/api/shell/launch-chrome` 也不再存在。Playwright 仅保留为 dev 依赖供 Web UI E2E 使用。

## 0. 拓扑与约定

```
Tauri 2 壳（Rust）                 动态端口（free_port，不固定）
  │  拉起 Python sidecar，下发 WORKBENCH_PORT
  ▼
FastAPI 服务（本文档主体）          127.0.0.1:<动态端口>
```

> **Tauri 壳使用动态端口**：`src-tauri/src/main.rs` 绑定 `127.0.0.1:0` 选空闲端口，
> 经 `WORKBENCH_PORT` 下发给 Python；不再有固定 18765/18767 契约，也没有 Electron 控制服务。
> 想知道当前是谁在服务：打 `GET /api/health` 看 `app` 字段（`Workbench` 才是我们）。
> Web 模式（`python -m workbench.main`）仍默认 `127.0.0.1:18765`，仅用于开发调试。

**全局约定**

| 项 | 约定 |
|---|---|
| 绑定 | `127.0.0.1` 回环。**`/api/*` 无认证、无 CSRF、无 CORS 配置**——信任边界 = 本机（该取舍的理由 D-7） |
| 时间戳 | 毫秒整数（`storage.now_ms()`） |
| 字段命名 | 请求/响应默认 **camelCase**（`routes_settings` 用 pydantic alias 转换）；`account`/`env` 行**原样返回 snake_case 表列** + JOIN 出的 `env_name` / `env_base_url`，仅 `password_enc` 被剔除 |
| 路径前缀 | `/api/*` 为业务面；`/api/shell/*` 与 `/api/update` 已随 Electron 控制服务删除 |
| pydantic 陷阱 | `BaseModel` **默认忽略多余字段**——请求体必须显式建模，否则字段静默丢失（`AccountPatch` 漏 `box` 的事故） |
| 错误体 | 业务错误为 `{"detail": "<中文说明>"}`（FastAPI `HTTPException` 默认形态） |
| 内容协商 | 无；产物接口用 `?download=true` 或后缀判断 |

**错误码语义**

| 码 | 用途 |
|---|---|
| 400 | 参数/业务规则错误（`AccountError`、空字段、未知命令、缺少必填项） |
| 404 | 资源不存在（账号/环境/任务/产物/会话） |
| 405 | 路由存在但方法不符 |
| 409 | 状态冲突（受管会话不可用、环境未确认、监听未启动、**风险工具待确认**——见 §10） |
| 413 | 上传超限（附件 > 20 MB、Skill 包 > 10 MB、日志导出 > 32 MB——见 §3.1 / §17.2 / §18） |
| 502 | 上游失败（egmp 平台/工具执行） |
| 503 | 依赖不可用（`BrowserError` 且非「账号不存在」） |

## 1. 会话与消息 `/api/conversations`（`routes_conversations.py`）

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| GET | `/api/conversations` | — | `{conversations:[{id,title,createdAt,updatedAt}]}`（按 `updatedAt` 倒序） |
| POST | `/api/conversations` | — | 新建的会话对象 `{id,title:"新会话",createdAt,updatedAt}` |
| DELETE | `/api/conversations/{conv_id}` | — | `{ok:true}`（幂等：不存在也返回 ok） |
| GET | `/api/conversations/{conv_id}/messages` | — | `{messages:[{id,conversationId,role,content,createdAt}]}`（按 `createdAt` 正序） |

## 2. 引导数据与设置 `/api`（`routes_settings.py`）

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| GET | `/api/bootstrap` | — | `{conversations, stats:{qaTotal, adoptRate\|null}, settings:{apiKey,model,temperature,systemPrompt:{prompt,default,custom,chars},permissions:{mode,allow,ask,deny,precedence,modes}}, cache:{…}}` |
| PUT | `/api/settings` | `{apiKey?, model?, temperature?}` | `{ok:true, settings:{…}}`（`model` 会归一为官方现行名，见下） |
| GET | `/api/settings/prompt` | — | `{prompt, default, custom, chars}`——`prompt` 是**当前生效**的那一份 |
| PUT | `/api/settings/prompt` | `{prompt}` | 同上；**空串 = 恢复基线** |
| GET | `/api/settings/tools` | — | `{allowed: list[str]\|null, tools:[{name,description,risk,plugin}]}`；`null` = 不限制 |
| PUT | `/api/settings/tools` | `{allowed: list[str]\|null}` | 保存白名单；未知工具 **400**；`null` 清空限制 |
| GET | `/api/settings/permissions` | — | `{mode,allow,ask,deny,precedence,modes}` —— 工具权限策略（`deny > ask > allow`） |
| GET | `/api/diagnostics?live=false` | — | `{schemaVersion, ok, summary, modules, issues}` —— 能力诊断；`live=true` 只读内存注册表，不启动 MCP |
| PUT | `/api/settings/permissions` | `{mode?, allow?, ask?, deny?}` | 同上；规则形状 `tool` / `tool(subject)`；**非法规则 400**，不静默忽略 |
| GET | `/api/agent/cache?days=7&conversationId=` | — | `{days, conversationId, answers, promptTokens, cacheHitTokens, cacheMissTokens, completionTokens, summaryCalls, hitRate, savedRatio}` |

> `conversationId` 非空 ⇒ **只统计这一段对话**。输入框下面那行常驻指标（缓存命中量 /
> 命中率 / 上下文用量，2026-09-22）靠它拿"本会话累计"，也靠它做到**刷新页面后数字还在**
> （`usage` 事件只在收尾发一次，刷新就没了）。

- `adoptRate`：`feedback==1 的比例 × 100` 取整；无任何评分时为 `null`。
- `apiKey` 明文回传（本机信任边界内的既有契约）——**这是 web 版能力的遗留形态**，见 `CODEBASE_OVERVIEW.md`（**2026-09-29 曾删除，2026-10-07 重新生成**） 的风险台账。
- 设置读写唯一入口是 `services/settings.py`；DB 键固定为 `apiKey` / `model` / `temperature`。

### 2.1 模型名归一（`deepseek-v4-flash` → `deepseek-flash`）

`deepseek-v4-flash` 自 2026-09-10 起**已退役**，官方为兼容把它路由到 V4.1-Flash
（原文 "temporarily routed"，见 [DeepSeek 更新日志](https://api-docs.deepseek.com/zh-cn/updates)）。
本工作台在**读与写两处**都做归一，所以接口回给你的一律是现行名；**行为不变**（同模型同价格）。
完整核查（含**联网搜索到底支不支持**的结论）见
`docs/research/deepseek-api-capabilities.md`（**该文件已于 2026-09-29 删除**；
可用 `git show <删除前提交>:docs/research/deepseek-api-capabilities.md` 取回）。

### 2.2 系统提示词

- 基线在代码里（`harness/prompts.py::DEFAULT_SYSTEM_PROMPT`）：只说明**你是谁、工具从哪找、
  信息从哪来**。**工具清单不在提示词里**——它由每次请求的 `tools` 下发（每个工具自带
  `description`），写两份必然漂移。
- 用户版存 `settings` 表的 `systemPrompt` 键，**空串 = 用基线**；改完立即生效。
- ⚠️ 改提示词会**让缓存前缀失效一次**（system 是请求的第一个字节）。低频动作可以接受；
  但**不要在运行时按轮次拼装提示词**——那等于每轮都让缓存从头失效。

### 2.3 缓存与用量

DeepSeek 的上下文缓存按"**完整匹配**一个缓存前缀单元"命中，**命中价是未命中价的 1/50**
（`deepseek-flash` 非高峰 $0.003 vs $0.15 / 1M input tokens）。`/api/agent/cache` 读的是
`deepseek_usage` 表的**真实累计值**：`cache.py` 在每次回答收尾时入账，
**摘要重算那部分开销单独计**（`summaryCalls`）——它的输入是历史原文、几乎必然全 miss，
是"缓存优化到底有没有省到钱"最该看的数字。

## 2.4 长期记忆 `/api/memory`（`routes_memory.py`）

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| GET | `/api/memory?scope=&limit=100` | — | `{memories:[{id,scope,type,title,body,keywords,revision,createdAt,updatedAt,expiresAt}], count}` |
| POST | `/api/memory` | `{title, body, scope?, type?, keywords?, expiresAt?}` | 新建的 memory 行；非法参数 400 |
| DELETE | `/api/memory/{memory_id}` | — | `{ok:true, id}`；不存在 404 |

> 记忆只存**事实**，不存指令；检索由 `memory_search` 工具按需触发，**不自动注入** system 前缀。
> `memory_add` / `memory_delete` 是 WRITE 工具，走确认门。

## 3. SSE 流式对话 `POST /api/chat/stream`（`routes_chat.py`）

> **特殊通道，见 §12.1**（事件序列契约）。

```
POST /api/chat/stream   Content-Type: application/json
{ "conversationId": "可选，缺省则新建", "message": "用户问题",
  "attachmentIds": ["att-1a2b3c4d"],   ← 可选：这一轮带的附件（见 §3.1）
  "plan": false,                       ← 可选：计划模式；只暴露 READ 工具，写工具被跳过
  "allowed_tools": ["get_current_time"] } ← 可选：本回合工具白名单（空数组 = 一个都不给）
→ 200 text/event-stream（无 Cache-Control 缓存；X-Accel-Buffering: no）
```

> **顺序是承重的**（2026-09-22 真机联调踩到）：服务端**先把这条用户消息落库、
> 再整段历史过一次扼流点**（`_request_history`）。反过来写（先取史 → 过硬点 → 再追加新消息）
> 会让**恰好是这一轮的附件**绕过展开——图片只变成一个文件名，而单元测试全绿。

## 3.1 附件（用户交给 agent 的文件/图片）`/api/attachments`（`routes_attachments.py`）

> 2026-09-22 用户下达的核心能力：用户会直接传图片或文件（word / excel / pdf / markdown）给 agent。
> 方案与判据见 `AGENT-FILE-INPUT-PLAN.md`（**该文件已于 2026-09-29 删除**）。

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| POST | `/api/attachments` | multipart：`file` + `conversationId`（可空） | `{ok, attachment:{id,name,kind,mime,bytes,status,parseNote,textChars,locator,createdAt}, marker}` |
| GET | `/api/attachments?conversationId=` | — | `{total, attachments:[…]}` |
| GET | `/api/attachments/{id}/raw` | — | 原始字节（`FileResponse`；图片在对话里直接显示它） |
| DELETE | `/api/attachments/{id}` | — | `{ok:true, id}`（**真删**：原件 + 解析产物 + 表行） |

**约定与边界**：

- `conversationId` 留空 ⇒ 落到"未绑定"的 `_pending`；**发言时绑定**并**连文件一起搬**到会话目录
  （路径是从 `conversation_id` 推出来的，只改库不搬文件 = 之后读不到）。
- 类型白名单 + **魔数复核** + OOXML zip 体检；单文件 ≤ 20 MB；拒收一律 4xx **且说清原因**
  （`不支持的文件类型` / `文件是空的` / `内容与扩展名不符` / `这不是一个有效的 .docx`）。
- **不接受任何来自 URL 的路径**：`id` 只用来查表，文件名来自表，读回时再做包含关系复查。
- 附件正文**不进日志**；进模型时按 `MarkerRe` 展开——**只有最新一轮**展开（图片 → `image_url`，
  文档 → 文件头 + 正文（有上限）+ 坐标目录 + 解析说明），历史轮只留一句"这份文件已交过"。
  这条取舍的理由是账单与前缀缓存（见 [CORE-ARCHITECTURE §不变量](CORE-ARCHITECTURE.md) 与方案 §2.1）。
- 模型侧三个动词：`attachment_list` / `attachment_read`（按页/工作表/表/行/字符区间取）。
  跨会话读取**一律拒**（数据串台是静默的）。

## 4. 回答反馈 `POST /api/feedback`（`routes_chat.py`）

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| POST | `/api/feedback` | `{metricId, helpful: bool}` | `{ok:true}` |

写入 `qa_metrics.feedback`（1 / 0）。`metricId` 来自 §3 的 `done` 事件。

## 5. 模块注册与体检 `/api/modules`（`routes_modules.py`）

| 方法 | 路径 | 响应 |
|---|---|---|
| GET | `/api/modules` | `{modules:[...], summary:{total, ok, degraded, missing}}` |
| GET | `/api/modules/{module_id}` | 单个模块状态（未知模块 → 404） |
| POST | `/api/modules/{module_id}/check` | 即时复检（等价于 GET，语义为「重跑体检项」） |

模块状态结构：`{id,title,runtime,mode,repoPath,commands,status,checks[]}`。
`checks[]` 项为 `{name, ok, detail}`。

- **内置模块**（`services/modules.py`）：`workbench`（Python 依赖体检）、
  `accounts`（Python 依赖；2026-10-19 起不再检查 Playwright / chromium）。
- **适配器模块**：读 `adapters/*.json`（**现存只有 `insight` 一个**；
  文件名仍是来源项目名 `akso-cc.json`，那是"对应哪个原仓库"的引用真源，
  见 [CONFIG.md](CONFIG.md) §2.3）。
  阶段 3 原生化后它降级为**只读参考存档**：体检项为 Python 依赖 + `source-reference`（信息项，
  原仓库缺失不影响功能，`ok` 恒为 `true`）。
- ⚠️ **2026-09-22：配置工厂随 `adapters/akso-auto.json` 一并删除**。模块清单因此只剩
  `workbench` / `accounts` / `insight` 三个 id；`GET /api/modules/factory` 与
  `GET /api/modules/akso-auto` 现在都返回 **404**（不是 `missing`）——
  `tests/test_app.py::test_old_module_id_is_gone` 把这两条 404 钉住了。
- ⚠️ **模块 id 与存档文件名可以不一致**（这是刻意的）：`insight` 模块的存档文件是
  `adapters/akso-cc.json`（文件名是「这份存档对应哪个来源项目」的引用真源，
  `repoPathEnv=AKSO_CC_REPO` 指向它）。2026-09-14 由上游项目名 `akso-cc` 改名为 `insight`——
  能力已是本工作台自己的，接口与界面上都不该再出现上游项目名。
- ⚠️ `status` 映射可读性存疑：`全过 → "ok"`、`全挂 → "degraded"`、`部分挂 → "missing"`（见 `CODEBASE_OVERVIEW.md`（**2026-09-29 曾删除，2026-10-07 重新生成**） 的风险台账）。

## 6. 对象分析 `/api/insight`（`routes_insight.py`）

凭证链：账号 id → `account` 表 → Fernet 解密（仅内存）→ `egmp.client.login`（55 分钟 token 缓存）。
**全程无 node、无子进程**。

请求体 `InsightRequest`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `command` | str | `login` / `spider`（`inventory` / `understand` 已于 0.3.8 下线，见 CHANGELOG） |
| `account_id` | str | 必填，统一账号库账号 id |
| `objects` | str | `spider`：逗号分隔对象编码（必填） |
| `env_id` | str | token 缓存文件名后缀 |

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/insight/run` | **同步**执行，返回 `{job_id, status, exit_code, duration_ms, result, workdir}` |
| POST | `/api/insight/run-sse` | **SSE** 进度流（见 §12.2） |
| GET | `/api/insight/runs?limit=10&offset=0` | 任务列表（**分页**：默认一页 10 条，上限 200）→ `{runs, total, limit, offset}` |
| GET | `/api/insight/artifacts/{job_id}` | `{job_id, status, coverageStatus, workspaces[], files:[{name,size,suffix,workspace}]}`（递归列产物） |
| GET | `/api/insight/artifacts/{job_id}/file?name=&download=` | 读产物（见 §12.4 路径守卫）。**内联可读**后缀与 media type：`.md`=`text/markdown`、`.json`=`application/json`、`.drawio`=`application/xml`、`.txt`/`.log`=`text/plain`、`.csv`=`text/csv`——前端取文本后自己渲染（markdown 排版、drawio 画成 SVG，均**离线自足**）；**图片**（`.png`/`.jpg`/`.jpeg`/`.webp`/`.gif`）走 `FileResponse` + inline（二进制，不能按文本读；**不含 `.svg`**——同源内联 SVG 可执行脚本）；其余后缀与 `download=1` 走附件下载 |
| GET | `/api/insight/workspace` | `{root, default, custom, exists}` —— 产物工作区根（用户可配） |
| PUT | `/api/insight/workspace` | `{path}`：设置工作区根；**空串 = 恢复默认**；非绝对路径 / 是文件 / 无法创建 → 400 |

`status` ∈ `running` / `succeeded` / **`partial`** / `failed`；`result` 内为各命令的
`{stats, artifacts[], coverage, workspaces[]}`。

> **`partial` 与 `coverage`（0.3.6 起）**：任务状态不再取布尔 `ok`，而是由**三态覆盖度**映射——
> `complete→succeeded`、`partial→partial`、`degraded→failed`。这样「10 个对象只成功 3 个」
> 不会再被记成 succeeded。`coverage` 形如
> `{status, total, succeeded, failureCount, objectNodes, stateNodes, attrEdges, relationEdges,
> workflowGraphs, unexpandedRelations, boundaryNotes}`
> ——后七项是 spider 的图规模与**边界**计数，让产物自证「这次织出来多大的网、边界在哪」。
>
> **产物清单（0.3.8 起）**：五步各有落盘产物 —— `step1-relations.md`（关联对象表）、
> `step2-status-table.md`（四列表）、`step3-attribution.md`、`step4-workflows.drawio`（每个工作流一页，
> 没取到就不生成该文件）、`spider-network.{md,json,drawio}`，外加 `manifest.json`。
>
> **读失败 vs 平台边界**（0.3.8 起）：`manifest.failures` = **我们没读到**（网络/HTTP/信封错误，
> 带 `stage`，并让 coverage 降级为 `partial`）；`manifest.notes` = **平台本身的边界**
> （未启用生命周期、关联对象没给编码所以未展开、该状态平台本来就没配动作）。
> 两者分开记，否则「平台没配」与「我们没读到」在产物里长得一模一样。

> **产物落点（0.3.8 起）**：`spider` 写到**用户可配的工作区** `<root>/<对象code>/<时间戳>/`，
> 落点带一份 `manifest.json` 自证覆盖度，并把目录回填到 `insight_runs.workspace_dir`。
> 工作区根不可写时回落到 `runtime/insight/<job_id>/`，此时 `result.workspaces` 为空数组
> （不得谎报工作区路径）。读取接口优先从 `workspace_dir` 取，**历史行自动回落到 `artifact_dir`**。

> **对象读不到 = 失败，不是空网**：`get_object` 拿到空实体（对象不存在 / 无权限 / 令牌失效）时
> spider 抛 `SpiderReadErrors`，任务记 `failed`；**不会**产出一张「1 个对象节点 + 0 状态」的
> 空网再配上 `coverage.status = complete`。

## 7. 配置（**已下线，本节不再描述任何契约**）

> **2026-09-22：配置工厂整条链路已从本仓删除**——`routes_factory.py` 的 8 个
> `/api/factory/*` 端点、`plugins/factory.py` 的 7 个 `factory_*` 工具、
> `services/egmp/writers/**` 与 `services/egmp/orchestrate.py` 一并移除；
> 配置页与侧栏入口、两个验证脚本也删了。**`/api/factory/*` 现在一律 404**，
> 本仓不再有任何"蓝图 → 创建"的接口，也不再有暂存目录 `runtime/factory/`。
>
> 本节**刻意保留 §7 这个编号**（不重排 §8 起的编号）：仓库里多处交叉引用按号指向
> §10–§18（含），重排编号会让它们静默指错节。
> （**2026-10-09 例外**：删除 `/extension/*` 那一节时确实重排了 §11 起的编号——
> 因为被删的那一节**自身就是编号源**，不重排会留下一个空洞；本文档内所有交叉引用
> 已同步更新，见文首的变更说明。）
> 平台侧的知识没有随代码消失：`PLATFORM-OPERATIONS-LEARNED.md`（**该文件已于 2026-09-29 删除**）（平台事实台账）、
> `FACTORY-PLATFORM-COGNITION.md`（**该文件已于 2026-09-29 删除**）（平台配置面认知）。
> 原始真机取证报告（`factory-tuning/reports/`）**已于 2026-09-29 删除** —— 结论已全部并进上面两份。

## 8. 账号库 `/api/accounts`（`routes_accounts.py`）

**响应永不回传明文密码**（`_sanitize` 剔除 `password_enc`，只留 `has_password`）。

### 平台环境

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| GET | `/api/accounts/envs` | — | `{envs:[{...,account_count}]}` |
| POST | `/api/accounts/envs` | `{name (必填), base_url?, note?}` | 新环境对象（`base_url` 自动 `strip().rstrip("/")`） |
| PATCH | `/api/accounts/envs/{env_id}` | `{name?,base_url?,note?}` | 环境对象 / 404 |
| DELETE | `/api/accounts/envs/{env_id}` | — | `{deleted: id}` / 404（**级联删账号**，FK ON DELETE CASCADE） |

### 账号

| 方法 | 路径 | 请求 | 说明 |
|---|---|---|---|
| GET | `/api/accounts?env_id=&pool=` | — | 不带 `pool`：`{accounts[], disabled_boxes[]}`；带 `pool`：`{accounts:[池成员]}`（**响应形态不同**） |
| POST | `/api/accounts` | `AccountIn` | 创建；`password` 必填（自动登录依赖凭据） |
| GET | `/api/accounts/{account_id}` | — | 账号详情 / 404 |
| PATCH | `/api/accounts/{account_id}` | `AccountPatch` | 局部更新；`exclude_none=True`（**传 `null` = 不改**）；`tags` 传数组；`pool` 传 str 或数组 |
| DELETE | `/api/accounts/{account_id}` | — | `{deleted: id}` / 404 |
| POST | `/api/accounts/{account_id}/pool` | `{pool: str\|list}` | `{account_id, pool}`；`pool=""` 或 `[]` = 移出池 |

`AccountIn`：`{env_id, username, password, role?, tags?:[str], note?, box?, tab_name?}`
`AccountPatch`：以上全部可空 + `status`, `pool`

### 盒子 / 备份 / 导入

| 方法 | 路径 | 请求 | 说明 |
|---|---|---|---|
| GET | `/api/accounts/boxes` | — | `{boxes:[{box,displayName,count}], disabled:[]}`；默认盒子（`box=""`）恒在末位 |
| POST | `/api/accounts/boxes/disable` | `{box, disabled}` | 禁用盒在**轮盘**中跳过（账号与管理不受影响） |
| POST | `/api/accounts/boxes/rename` | `{"from":..., "to":...}`（**注意 `from` 是别名**） | `to=""` = 并入默认盒子；记忆清单**原位替换**（追加+删除会让盒名跳到末尾） |
| POST | `/api/accounts/boxes/create` | `{name}` | 新建记忆盒子（可为空盒） |
| POST | `/api/accounts/boxes/delete` | `{"from":...}` | 删除 = 账号并入默认盒子 |
| POST | `/api/accounts/boxes/default-name` | `{"to":...}` | 自定义默认盒子显示名 |
| GET | `/api/accounts/export` | — | **备份文件**（含 Fernet 密钥与密文口令，见 [SCHEMA.md](SCHEMA.md) §5.2） |
| POST | `/api/accounts/import-backup` | 备份 JSON | `{created, skipped}` |
| GET | `/api/accounts/import/preview` | — | 扫描原项目 env 文件（**只读**）→ 展示行列表 |
| POST | `/api/accounts/import` | — | 执行导入 → `{result:[展示行]}` |

> ⚠️ 路由顺序注意：`{account_id}` 是**兜底路径**，因此 `/boxes/*`、`/export`、`/import*` 等固定段
> 必须在它之前注册（现顺序已正确）。新增固定段端点时**不要插到 `GET /{account_id}` 之后**。

## 10. Agent 工具层 `/api/agent`（`routes_agent.py`）

> **单一事实源**：工具清单来自**工具插件中枢** `plugin_api.ToolHub`（`workbench/plugins/`
> 五个插件装配，共 20 个工具）。本模块**不再定义工具**——`GET /tools`、对话 Agent 的 function calling
> schema、审计三处读的是同一份定义。插件 SDK 契约见 `PLUGIN-SDK.md`（**该文件已于 2026-09-29 删除**）。

| 方法 | 路径 | 请求 | 说明 |
|---|---|---|---|
| GET | `/api/agent/tools` | — | `{plugins:[{id,title,version,description,tools:[name]}], tools:[{name,description,risk,plugin,needs_confirmation,args:{参数:说明}}], failures:[{plugin,error}]}` |
| GET | `/api/agent/plugins` | — | `{plugins:[...], failures:[...]}`（插件维度视图；与上一条的分工：这里答"装了哪些插件"，上一条答"能调什么"） |
| POST | `/api/agent/invoke` | `{tool, args:{}, approvalToken?}` | 成功 `{tool,ok:true,status:"ok",risk,plugin,auditId,durationMs,result}`；见下方状态码 |
| GET | `/api/agent/pending?status=pending&limit=50` | — | 待确认（危险）操作清单；`status=""` 取全部状态 |
| GET | `/api/agent/pending/{pending_id}` | — | 单条待确认（无令牌；404 = 不存在或已过期） |
| POST | `/api/agent/pending/{pending_id}/approve` | —（无请求体） | `{pendingId,approvalToken,expiresInMs,summary,retry}`；已决策/已过期 → 409 |
| POST | `/api/agent/pending/{pending_id}/reject` | `{reason?}` | `{pendingId,status:"rejected",summary}`；已决策 → 409 |
| GET | `/api/agent/audit?limit=50` | — | `{audit:[{id,tool,plugin,risk,status,result_summary,duration_ms,created_at}]}`（上限 500；**刻意不带 `args`**） |
| GET | `/api/agent/audit/{audit_id}` | — | 单条审计（含 `args`/`meta`，已 JSON 还原）；404 = 无此记录 |
| GET | `/api/agent/selfcheck` | — | `{ok,plugins,tools,riskCounts,requiredTools,missingRequired,failures}`（启动自检的可查视图） |

**`POST /invoke` 的状态码语义**（比通用错误码表更细，因为要区分"要不要确认"）：

| 码 | 何时 | 响应体 |
|---|---|---|
| 200 | 执行成功 | `{ok:true,status:"ok",result:{...}}`（`result` 为工具返回的 JSON 正文还原） |
| 400 | `bad_args` / `unknown_tool` / `rejected`——**调用方能自己修** | `{detail:{tool,status,error,...}}` |
| 404 | 工具名不在目录里 | `{detail:"未知工具：…"}` |
| 409 | `needs_approval`——风险工具缺单次令牌。**不是错误，是流程中止** | `{detail:{pendingId,risk,summary,message}}` |
| 409 | `needs_confirmation`——模型自己发起实时确认（`request_user_confirmation`）。HTTP 面**不等待答复**，答复走 `/pending/*` | `{detail:{pendingId,confirm:{question,details,timeoutS,target},message}}` |
| 502 | 执行期失败（平台侧/超时） | `{detail:{tool,status,error,...}}` |

**风险分级与确认门**（详见 `PLUGIN-SDK.md`（**该文件已于 2026-09-29 删除**） §4）：

- `risk=read` 直接执行；`risk=write|dangerous` **没有单次令牌绝不执行**。
- 令牌兑换链：`invoke` 得 409+`pendingId` → `approve` 得 `approvalToken` → 原样重投 `invoke`。
  令牌**一次性 / 5 分钟 / 绑定工具+参数指纹**（换个参数要重新确认）。
- 对话链路（SSE）：`approval_required` → **这一轮就地等答复** → `confirm_resolved`；
  放行后由**服务端**用完全相同的参数重投那次调用（按指纹认领，一次性）并把**真实结果**喂回模型
  ——**不需要用户再发一条消息、也不需要模型自己重试**（2026-09-22 决策卡 G2）。
  等待上限 = `AgentConfig.approval_wait_s`（默认 300s），超时按**未同意**处理。

**模型主动确认（`needs_confirmation`，与上面的风险门是两套东西）**：

- 触发者是**模型**（它调用 `request_user_confirmation`），不是宿主自动拦；所以它
  **不能**当高风险调用的放行依据——模型不调用它就绕过去了。
- 对话链路：SSE `confirm_required` → 用户在卡片上点同意/拒绝 → SSE `confirm_resolved`
  → 循环**当场继续**（不需要用户再发一条消息）。等待有上限（`timeoutS`，默认 300s，上限 1800s），
  超时按**未同意**处理，且那条确认会被标 `expired`——事后补点同意也无效。
- 带 `target_tool` + `target_args` 时，用户同意的是**那一次具体调用**：批准按
  「目标工具 + 参数指纹」绑定，模型换参数调用要重新走一遍风险门。
- HTTP 链路（`POST /invoke`）：本端点**不等待**，直接 409 + `confirm` 内容；
  调用方自己看 `GET /pending` 决定何时 `approve`（同意同样会预先批准那次目标调用）。

**工具清单（20 个工具，按插件）**：

| 插件 | 工具 | 风险 |
|---|---|---|
| `builtin` | `get_current_time` / `read_local_file` / `read_web_page` / `attachment_list` / `attachment_read` | read |
| `builtin` | `list_skills` / `read_skill`（Agent Skills 文件夹目录 + 按需读正文） | read |
| `builtin` | `request_user_confirmation`（模型主动请求确认；会暂停对话等答复） | read |
| `websearch` | `web_search` | read |
| `insight` | `insight_accounts` / `insight_read_config` / `insight_run` / `insight_artifacts` / `insight_read_artifact` / `insight_runs` | 全 read |
| `accounts` | `accounts_list` / `accounts_login` | read |
| `memory` | `memory_search` / `memory_add` / `memory_delete` | search=read；add/delete=write |

> **风险分布：18 read / 2 write / 0 dangerous**（2026-10-23 装配 `ToolHub` 实测）。
> 内置浏览器 / 托管会话下线后，产品里暂时没有 WRITE 工具；风险模型与确认门
> 仍是 `read` / `write` / `dangerous` 三级，机制由 `tests/test_plugin_api.py` 的合成用例守着。
> `Risk.DANGEROUS` 枚举与确认门里那一条分支**都还在**——只是产品里现在没有工具用它
> （`factory_create` 曾是**唯一**一个 dangerous 工具，随配置工厂一并删除）。
> **"归零"这件事本身是断言**：`tests/test_app.py::test_agent_selfcheck`
> 断言 `riskCounts.dangerous == 0` 且 `riskCounts.write == 0`。

> ⚠️ **2026-09-15 收口**：本模块此前有**第二套工具表**（`login_platform` / `read_config` /
> `run_insight` / `browser_status`），与前端对话用的 `harness/tools.py`（三个基础工具）
> 并存且互不可见——后果是"对话里的模型只能聊天、一个平台能力都看不到"。两者已合并为
> `ToolHub`，旧工具名不再存在（`read_config` → `insight_read_config`，
> `run_insight` → `insight_run`，`login_platform` → `accounts_login`）。
> 2026-10-19：`browser_status` / `accounts_browser_*` 随内置浏览器整体删除。

> **本表只列 core（产品自带）**。用户从共享库安装的 Skill 会出现在同一个
> `GET /api/agent/tools` 响应里（带 `origin:"installed"`），但**不进本表**——
> 文档承诺的是"产品自带多少工具"，与用户装了什么无关。两者的分层见 §17。

### 10.1 壳协作面（已下线）

`/api/shell/*` 随 Electron 控制服务在 2026-10-19 删除；Tauri updater 不通过 Python 代理，
未来如需“从界面触发更新”，应走 Tauri 自己的命令/IPC，而不是恢复这个前缀。


## 11. 监听录制（已下线）

`/api/monitor/*` 随内置浏览器 / Monitor 在 2026-10-19 整体删除；本节保留编号以避免已有链接跳空。

## 12. 特殊通道专章

### 12.1 SSE 对话事件序列（`POST /api/chat/stream`）

事件为 `data: {json}\n\n` 行，**序列有严格契约**（`harness/types.py` + `harness/loop.py`）：

```
（前置）若未配置 API Key： {"type":"error","message":"请先在设置中配置 DeepSeek API Key。"} → {"type":"done"}
{"type":"start","conversationId":"..."}
{"type":"attachment","attachmentId":"att-xxxxxxxx","name":"编制.xlsx"}   ← 0..N 条，紧跟在 start 之后
                                         每个"这一轮真的带上了的附件"一条（顺序 = 标记顺序）。
                                         界面靠它把 chip 画进「我」那条消息里
                                         ——与库里那条正文上的 `[附件 att-…：名]` 标记
                                         是**同一件事的两种呈现**（刷新后由标记画回来）。
{"type":"token","text":"..."}            ← 0..N 次（增量正文）
{"type":"thought","text":"..."}          ← 可选：模型在工具调用前的说明
{"type":"action","call":{"id","name","arguments"},          ← 工具调用（每次一条）
 "tool":{"name","risk","plugin","known"},"auditId":"..."}
{"type":"observation",                    ← 对应结果（紧随其 action）
 "result":{"ok":bool,"summary":"≤220字纯文本","status":"ok|error|needs_approval|…"},
 "invocation":{"toolCallId","tool","auditId","durationMs","risk","plugin","status","isError"}}
{"type":"approval_required",              ← 风险工具被确认门拦下（紧随其 action，
 "pendingId":"...","tool":"...","risk":"write|dangerous","summary":"人话摘要","auditId":"..."}
                                          ← 这一轮**就地等答复**，等到之前不会发 observation）
{"type":"confirm_resolved",               ← 答复到达（放行/拒绝/超时都发；确认卡就地收口）
 "pendingId":"...","approved":bool,"expired":bool,"answer":"给模型看的那句话",
 "tool":"...","risk":"..."}
↑ (thought/action/observation/approval_required/confirm_resolved) 可循环多轮，受 max_steps 预算约束
{"type":"final","content":"完整正文"}     ← 或 {"type":"error","message":"..."}
{"type":"usage","usage":{...}}            ← 收尾必发一条：本轮 token 用量与**缓存命中量**
{"type":"done","conversationId":"...","content":"...","metricId":"...","titleUpdated":bool,
 "latencyMs":int,"usage":{...}}           ← usage 与上一条同源（前端不必额外记状态）
```

- **`done` 必发**（含错误路径），其 `metricId` 是 §4 反馈接口的入参。
- 模型无内容时 `content` 回落为「（模型未返回内容）」；错误路径写「错误：<原因>」。
- 会话标题在首条用户消息时自动更新，`titleUpdated` 告知前端。
- **`usage` 在 final/error 之后单独发**：命中量要等所有模型调用（工具轮次 + 历史摘要重算）
  都结束才是完整答案，提前发会给出一个偏低的数字。字段：
  `{calls, promptTokens, cacheHitTokens, cacheMissTokens, completionTokens, reasoningTokens,
  hitRate, summaryCalls}`（见 §2.3 与 `docs/research/deepseek-api-capabilities.md`
  —— **该文件已于 2026-09-29 删除**）
  **加上上下文用量**（2026-09-22，输入框下方那行常驻指标用）：
  `{contextTokens, contextWindow, contextThreshold, contextRatio}` ——
  `contextTokens` 取**平台回的 `prompt_tokens`**（最近一次调用），**不是估算**：
  它同时含系统提示词 + 冻结摘要 + 原文历史 + 工具 schema，是"这一次到底发了多少上去"的真值。
- **`action` ↔ `observation` 靠 `invocation.auditId` 对齐**（前端卡片就地更新用），
  同一 id 也是 `GET /api/agent/audit/{id}` 的主键——即"对话里看到的那次调用"与
  "审计表里那一行"是同一条记录。
- `invocation.status` 取 `ok|error|needs_approval|rejected|timeout|unknown_tool|bad_args`；
  **`needs_approval` 不是失败**，是"流程中止等你确认"（见 §10 的确认门）；
  **`rejected` 有两种来源**：用户拒绝了那次放行，或**工具自己的前置检查**（`ToolSpec.precheck`）
  在问放行之前就判定这次调用注定失败（见 §10 末）。
- 工具调用**全部入审计**（`agent_audit`，`meta.source="agent"`），HTTP 面与对话面共用同一张表。

### 12.2 SSE 洞察进度流（`POST /api/insight/run-sse`）

```
{"type":"log","line":"<日志行>"}    ← 0..N（来自 worker 线程，经 loop.call_soon_threadsafe 投递）
: keep-alive                        ← 空闲 0.5s 发一次（避免代理/浏览器断连）
{"type":"done","job_id":"...","status":"succeeded|failed","exit_code":0|1,
 "duration_ms":int,"result":{...},"workdir":"..."}   ← 终态（必发）
```

- 用 `loop.run_in_executor` 跑同步 `_run_job`；`on_log` 线程安全投递。
- 循环条件 `while not task.done() or not queue.empty()`：**任务结束后仍会排空遗留日志**，不会丢最后几行。

### 12.3 产物读取与路径守卫（`GET /api/insight/artifacts/{job_id}/file`）

- 两种 I/O 形态：`?download=true` 或后缀 ∉ `{.md,.json,.txt,.log,.csv}` → `FileResponse`（附件下载）；
  否则读文本返回 `PlainTextResponse`（超 200 万字符截断）。
- **路径守卫**：`artifact_dir` 与 `name` 都 `.resolve()`，再断言
  `str(target).startswith(str(artifact_dir))`。
  ⚠️ **这是纯字符串前缀比较，未补路径分隔符**——`.../insight/abc` 会「包含」`.../insight/abc_evil`。
  本地单人场景风险低，但**新增类似端点时应改用 `Path.is_relative_to()`**。见 `CODEBASE_OVERVIEW.md`（**2026-09-29 曾删除，2026-10-07 重新生成**） 的风险台账。

### 12.4 浏览器操作串行化（已下线）

2026-10-19：内置浏览器 / Monitor / CDP 整体下线，本节描述的 `browser_pool` 专职线程、
`/windows` 开户、Playwright sync 约束均不再适用；保留此标题只为让旧链接能落到解释处。

## 13. 更新面（已下线）

`/api/update` 与 `/api/shell/update-state` 随 Electron 更新器在 2026-10-19 删除。
Tauri updater 的签名/清单/构建链路在 `tools/build_tauri.ps1` 与 `tools/tauri_update_manifest.py`；
客户端尚未接入检查/安装 UI。

## 14. 静态资源

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 工作台主界面 `static/index.html` |
| GET | `/static/*` | `StaticFiles` 挂载 `workbench/static/` |

页面：`index.html`（对话 + 设置）、`pages/insight.html`、
`pages/accounts.html`、`pages/sandbox.html`（沙箱：策略 / 状态 / 执行审计 / 自检）、
`pages/skills.html`（Skill 共享库：上传 / 安装 / 卸载 / 停用）、
`blank.html`（旧壳错误页，保留兼容；Tauri 用本地 splash）。

后两页在导航的「**Agent 能力**」分组下（`sandbox.html` / `skills.html`），
对应下面的 §17。

## 15. 壳控制服务（已随 Electron 删除）

Tauri 壳没有对外的 HTTP 控制服务；更新器直接运行在 Rust 进程内，
Python 不再代理 `/update-check` / `/update-install`。

## 16. 服务端身份 `GET /api/health`（`api/__init__.py`）

```jsonc
{
  "app": "Workbench",          // ★ 壳的就绪探测核对这个字段
  "service": "workbench-server",
  "version": "0.4.1",
  "pid": 21564,
  "frozen": false,
  "ports": { "server": 50196 },  // Tauri 动态端口
  "portsSource": "env"           // env | default
}
```

**为什么存在**：壳的就绪探测不能只看"端口上有人回 200"，否则**旧实例占着端口**时
新壳会认为自己的服务已就绪、主窗打开别人的页面——一次表现成"代码改了但 UI 没变"的事故。
现在探测打本端点并核对 `app`。

**本端点是唯一的健康端点**：它只回答**进程身份**（我是谁、在哪个口），**无副作用**，可反复探测。
> **2026-10-09**：原先还有一个 `GET /extension/health` 回答"执行面（浏览器扩展）活着吗"，
> 它会写 `everConnected` 闩锁，是账号中心四态徽标的数据源。
> 它随扩展一起删除（§D-4 记录的那条分工
> 现在只剩本端点半边）。**不要把探测打到有副作用的端点上**——Tauri 启动时会反复探它，
> 若写库会把“扩展开没开过”之类的判定污染掉（本端点自身无副作用）。

## 17. Agent 能力面：沙箱 / Skill 共享库 / 工作区快照（`routes_sandbox.py` / `routes_skills.py` / `routes_checkpoints.py`）

这两族端点对应导航里的「**Agent 能力**」分组，回答的是**"我的 Agent 现在能做什么、
它做过的（上传来的）代码跑成什么样"**——与 §10 的"产品自带工具"互补：

| 端点族 | 回答什么 | 谁在变 |
|---|---|---|
| `/api/agent/*`（§10） | **产品自带**能力有哪些（`origin=core`） | 随版本走 |
| `/api/skills/*` | **我上传/安装**了哪些能力（`origin=installed`） | 用户随时改 |
| `/api/sandbox/*` | 不受信代码**在哪跑、跑过什么、拦住了什么** | 每次执行 |

两边读的是**同一个** `ToolHub`（§10 的单一事实源没变），只是按 `origin` 分开呈现。
分开的理由是硬的：`tools/gen_tools_doc.py` 断言"文档里的 N 个工具 == 运行期中枢 core"，
而"用户装了什么"不可能写进文档。

### 17.1 沙箱 `/api/sandbox`（`routes_sandbox.py`）

| 方法 | 路径 | 请求 | 说明 |
|---|---|---|---|
| GET | `/api/sandbox/policies` | — | 具名策略及指纹：`{policies:[{name,allow_network,allow_subprocesses,writes,timeout_s,fingerprint,enforced…}], default}` |
| GET | `/api/sandbox/status` | — | 工作区根 / 权限、活跃作业数、目录布局、解释器白名单 |
| GET | `/api/sandbox/workspace` | — | `{root,defaultRoot,mode,allowNetwork,allowSubprocess,exists,writable,modes,honest_limit}` |
| PUT | `/api/sandbox/workspace` | `{root?,mode?,allowNetwork?,allowSubprocess?}` | 改工作区 / 权限；非法路径或模式 **400** |
| POST | `/api/sandbox/workspace/reset` | — | 恢复默认根与保守权限（联网 / 派生进程关闭） |
| GET | `/api/sandbox/runs?limit=50` | — | 执行审计：策略、退出码、耗时、**是否树杀**、截断标记（上限 500） |
| GET | `/api/sandbox/selfcheck` | — | 平台能力探测（作业对象可用性、解释器、工作区可写） |

- **工作区边界**：`cwd` 必须在 `workspace.root` 内；`mode=read-only` 时拒绝 `writes=true`
  的策略（`skill-script` / `mcp-stdio`），只允许 `strict-readonly`；`allowNetwork` /
  `allowSubprocess` 对策略声明做预检。Windows 上 `strict-readonly` 还会附加
  `WRITE_RESTRICTED` 受限令牌（logon + Everyone），运行时写检查按限制列表走——但
  `Everyone` 的环境授予仍可能放行，属 **partial**；`workspace-write` 尚未有内核级隔离。
  见 `/api/sandbox/status` 的 `capabilities` 与 `honest_limit`。
- **刻意没有"通用执行"端点**：`POST /api/sandbox/exec` 这类接口等于给本机开一个
  "用 HTTP 跑任意命令"的洞（本仓服务端**无认证**，仅靠 127.0.0.1 收敛，见 §0）。
  要跑代码请走 Skill（§17.2）——那里有准入扫描、固定策略与确认门三道。
- 策略是**具名白名单**，请求里传策略名而不是参数；未知策略名一律拒，不做"就近取默认"。
- 执行审计与 §10 的工具审计、§17.2 的 Skill 审计是**三条独立的流水**：
  这条记"进程怎么跑的"，不记"谁批准了什么"。

### 17.2 Skill 共享库 `/api/skills`（`routes_skills.py`）

| 方法 | 路径 | 请求 | 说明 |
|---|---|---|---|
| GET | `/api/skills/summary` | — | `{library[], installed[], counts{library,installed,core_tools,installed_tools}, layout, boundary}` |
| POST | `/api/skills/upload?allow_warn=false` | multipart `file`（ZIP ≤10 MB） | 成功 `{ok,capability_id,version,verdict,tools[],findings[]}`；失败 **400** + `{detail:{...}}` |
| POST | `/api/skills/install` | `{capability_id, version?}` | `{capability_id, version, tools[]}`；版本不存在 → 400，形状非法 → 409 |
| POST | `/api/skills/uninstall` | `{capability_id}` | `{capability_id, removed_plugins[], library_intact:true}` |
| POST | `/api/skills/delete` | `{capability_id}` | 从**共享库彻底删除**（含所有版本）；已安装则先卸载；不存在 → 404 |
| POST | `/api/skills/plugins/{plugin_id}/enabled?enabled=true` | — | 启用/禁用任意插件（**含核心插件**）；不存在 → 404 |
| GET | `/api/skills/audit?limit=50` | — | 能力供给链留痕（上传/安装/卸载/删除，上限 500） |
| GET | `/api/skills/library/raw/{capability_id}/{version}/{name}` | — | 读库内文本文件，**只允许 `SKILL.md` / `manifest.json` / `scan.json`** |

**上传的两个刻意设计**：

1. **扫描到高危模式不会"点一下上传就入库"**：第一次返回 `verdict:"warn"` + `findings`，
   **不落库**；调用方看过之后带 `allow_warn=true` 重投才收（`reject` 的东西永远进不来）。
   理由：准入扫描是**闸门**，不是安全边界，
   而闸门要生效就必须卡在"人点确认"这一步上。
2. **读库文件只开白名单三个名字**，不做通用文件读取——那会变成"用 URL 探数据目录"的洞
   （服务端无认证；这条与 §12.4 的产物路径守卫同源）。

**工具形态与风险**（`SKILL.md` 里二选一，同时写在上传期就被拒）：

| 形态 | 字段 | 执行 | 风险 |
|---|---|---|---|
| 声明式 | `wraps: <已有工具名>` | 转发给那个工具，**走同一个 `hub.execute`**（同一套确认门与审计） | 被包装工具与声明值的**较严**者 |
| 脚本式 | `script: scripts/x.py` | 在**沙箱子进程**里跑包内 Python 脚本 | **≥ `write`**（只升不降，故每次调用都要过确认门） |

脚本式的入口约定（stdin 收 JSON 入参 → stdout 出 `{"ok":…}` → stderr 只进日志 →
**退出码优先于 stdout**）与沙箱策略口径见 `PLUGIN-SDK.md`（**该文件已于 2026-09-29 删除**） §1。

**三条硬性质**（都有回归断言）：

- **安装后工具立刻对模型可见**，卸载后**立刻消失**，且 core 工具数不变
  （这正是 §10 那条文档门禁能继续成立的原因）；
- **卸载 ≠ 删除**：卸载只断 `current` 指针并摘插件，**共享库里的版本一个都不删**；
- **核心插件只能禁用、不能卸载**（`capability_id` 为空串的插件会被守卫直接拒）。

### 17.3 工作区快照 `/api/checkpoints`（`routes_checkpoints.py`，2026-10-23 引入）

| 方法 | 路径 | 请求 | 说明 |
|---|---|---|---|
| GET | `/api/checkpoints` | — | `{checkpoints:[manifest...], root}`；只读列表，不扫文件 |
| POST | `/api/checkpoints` | —（无请求体） | 对**当前沙箱工作区根**创建快照，返回 `{checkpoint, checkpoints}` |
| POST | `/api/checkpoints/{id}/restore` | — | 覆盖回快照自己的 root；非法/不存在 id → **404** |

- **只接受沙箱工作区根**：端点不接收任意路径，否则会变成"读/写任意目录"的洞。
- 快照只覆盖**文件**；`rm`、网络、DB 副作用撤不了；快照之外的新文件不会删除。
- 创建与恢复都走 `asyncio.to_thread`（I11）；恢复前 UI 有 `confirm()`。

### 17.4 定时任务 `/api/schedules`（`routes_schedules.py`，2026-10-23 引入）

| 方法 | 路径 | 请求 | 说明 |
|---|---|---|---|
| GET | `/api/schedules` | — | `{schedules:[{id,title,prompt,intervalS,enabled,lastRunAt,nextRunAt,lastStatus,lastError,lastConversationId,...}]}` |
| POST | `/api/schedules` | `{title,prompt,intervalS}` | 创建；`intervalS` 60–2592000，非法 **400** |
| PATCH | `/api/schedules/{id}` | `{title?,prompt?,intervalS?,enabled?}` | 局部更新；不存在 **404** |
| DELETE | `/api/schedules/{id}` | — | 删除；不存在 **404** |
| POST | `/api/schedules/{id}/run` | — | 立即执行一次（只读）并回写 `lastStatus` |

- 执行时 `plan_mode=True` + **core READ 工具白名单**：无人值守没有确认门可点，所以**不允许写工具**；
- 每次执行新建一个会话，结果在左栏会话记录里回看；
- 固定间隔，不支持 cron；调度器每 30 秒轮询（`WORKBENCH_DISABLE_SCHEDULER=1` 可关）。

### 17.5 长期目标 `/api/goals`（`routes_goals.py`，2026-10-23 引入）

| 方法 | 路径 | 请求 | 说明 |
|---|---|---|---|
| GET | `/api/goals` | — | `{goals:[{id,title,objective,status,rounds,maxRounds,conversationId,...}]}` |
| POST | `/api/goals` | `{title,objective,maxRounds=10,conversationId=""}` | 创建；`maxRounds` 1–100，非法 **400** |
| PATCH | `/api/goals/{id}` | `{title?,objective?,status?,rounds?,maxRounds?}` | 局部更新；`status` ∈ active/paused/done；不存在 **404** |
| POST | `/api/goals/{id}/continue` | — | **手动**推进一轮（只读 Agent 回合，新建/复用绑定会话）；达到上限或非 active → **400** |
| DELETE | `/api/goals/{id}` | — | 删除；不存在 **404** |

- 不自动续跑、不自动判定完成；用户点「继续一轮」手动推进，**只读工具**可用；
- 设置页可创建、暂停/恢复、标记完成、删除。

## 18. 运行日志 `/api/logs`（`routes_logs.py`，2026-09-20 引入）

**定位**：这是**运维面**，不是业务域。它回答"刚才发生了什么"。
装配点在 `create_app()` 里**显式注册**（不进 `_optional_routers`）——查不到日志比少一个业务路由更严重，
不该被容错注册静默跳过。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/logs/days` | 有哪些天的日志可读（倒序）+ 日志目录 + 当天文件名 |
| GET | `/api/logs/tail` | 读最近 N 条；支持 `level` / `contains` / `trace` / `kind` / `day` 过滤 |
| GET | `/api/logs/trace/{trace_id}` | **把一次请求的日志按时间正序排出来**（本组接口的核心用途） |
| GET | `/api/logs/download?day=YYYYMMDD` | 导出当天日志（JSONL，**再脱敏一次**） |

**`traceId` 从哪来**：每个 `/api/*` 请求由中间件生成一个 8 位十六进制号，
**同时回在响应头 `X-Trace-Id` 里**——用户报故障时报这个号即可定位整条链路。

```powershell
# 拿 traceId
curl.exe -i -s http://127.0.0.1:18765/api/agent/selfcheck | Select-String X-Trace-Id
# 看这条请求的完整时间线
curl.exe -s "http://127.0.0.1:18765/api/logs/trace/<traceId>"
# 只看错误
curl.exe -s "http://127.0.0.1:18765/api/logs/tail?level=ERROR&n=50"
```

**安全边界**（务必注意）：

1. **可能返回用户数据**（对象编码、文件路径、工具名）。它与 `/api/*` 同级——
   只有"本机回环"这一层（既有取舍 R1b），**本组端点不得扩大它**；
2. **导出时再脱敏一次**（纵深防御）：落盘已过 `RedactionFilter`，导出再走一遍；
3. **不提供删除/清空**。删日志是不可逆动作，应由人在文件系统里做，不该藏在 HTTP 面里
   （与 [approvals.py](../workbench/approvals.py) 对不可逆动作的态度一致）；
4. **`/api/logs/*` 自身不记请求日志**——否则查一次日志会在下一次查询里看到自己（自指噪声）。

**错误码**：`400` = `day` 不是 8 位数字；`404` = 该天没有日志 / 该 traceId 没有记录（只查当天）；
`413` = 当天日志超过导出上限（32 MB，请直接取文件）。

> 配置项（`WORKBENCH_LOG_*`）与日志文件清单见 [CONFIG.md](CONFIG.md) §2.1 / §6；
> 设计与分期见 `LOGGING-DESIGN.md`（**该文件已于 2026-09-29 删除**）。

---

## 19. MCP 管理面 `/api/mcp`（`routes_mcp.py`，2026-10-19 引入）

### `GET /api/mcp/servers`

纯内存状态，**不打网络、不启动进程**——设置页轮询它没有副作用。

```json
{
  "configPath": "C:\\Users\\<user>\\AppData\\Roaming\\Workbench\\mcp.json",
  "exists": true,
  "loaded": true,
  "servers": [
    {"name": "wecom", "enabled": true, "connected": true, "toolCount": 3,
     "transport": "stdio", "risk": "write", "description": "",
     "error": "", "restarts": 0, "lastErrorAt": 0}
  ],
  "errors": [],
  "tools": 3,
  "healthy": 1,
  "failing": 0,
  "restarts": 0,
  "monitorIntervalS": 30
}
```

- `status` 里**不返回** `args` / `env` / `headers` / `url`（可能有 token）；
- `transport` 是 `stdio` 或 `streamable-http`；`toolCount` 只给数量，不返回 schema；
- 后台每 `monitorIntervalS` 秒做一次 `ping` 健康检查；失败 / 进程退出 /
  `notifications/tools/list_changed` 会触发自动重连（指数退避，上限 5 分钟）；
- 未配置时 `servers` 为空、`exists` 可能是 `false`；
- 注册表不可用 → **503**（不是 500 空白）。

### `POST /api/mcp/servers/{name}/reconnect`

只重连一个 server（**立即尝试，不走退避**）。名称不存在 → **404**；重连动作本身失败 → **502**。
返回 `{server, ...status}`。

### `POST /api/mcp/reload`

重读 `mcp.json` 并重建全部连接。启动 server 是真的起子进程，可能几秒，所以服务端走
`asyncio.to_thread`。

- **单个 server 失败不会让整次重连失败**：失败进 `servers[].error` 与 `errors[]`，
  其余 server 照常可用；
- 只有"重连动作本身崩了"才返回 **502**；
- 重连会**先摘旧能力再注册**，不会留下"删了但还在工具表里"的幽灵。

> 配置形状、安全默认（默认 `risk=write`、`trust=UNTRUSTED`、环境变量白名单）见
> [MCP.md](MCP.md)。MCP 工具通过 `ToolHub` 的 installed 层进入 `/api/agent/tools`。

---

## 20. 云端账号库 `/api/vault`（`routes_vault.py`，2026-10-10 引入）

设备流（RFC 8628）两拍，**会话令牌 Fernet 加密存本机**；默认云端地址
`https://www.dragonrain.top`（443，`AKSO_VAULT_URL` 可覆盖）。

| 方法 | 路径 | 说明 | 错误 |
|---|---|---|---|
| GET | `/api/vault/status` | 是否登录、显示名、当前 baseUrl；**不打网络** | — |
| POST | `/api/vault/login/start` | 开局，返回 `deviceCode` / `userCode` / `verificationUrl` | 502 上游不可用 |
| POST | `/api/vault/login/poll` | 轮询；`approved` 时**服务端落会话**（加密） | 502 |
| POST | `/api/vault/push` | **本地→云端**：把本机账号库按 merge 契约并上云（需已登录；带 `baseRevision`） | 409 revision 冲突 · 502 |
| GET | `/api/vault/library?refresh=1` | **只读**云端账号库：sites / accounts / boxes / counts；`refresh=1` 绕过 5 分钟缓存 | 401 会话失效 · 502 上游不可用 |
| POST | `/api/vault/refresh` | 校验会话并取回 profile | 401 会话失效 |
| POST | `/api/vault/logout` | 先吊销、再清本地；云端失败也清 | — |

`deviceCode` **不落库、不入日志**；`POST /login/start` 的 `clientName` 最长 60。
`POST /login/poll` 批准时**服务端落会话并把 `token` 从响应里剔除**（浏览器只看 `status`）。
`GET /status` 刻意不验证令牌（首屏不能被断网卡住）——真正失效由 `refresh` / `library` 暴露。

`GET /library` 的响应是**脱敏视图**：`accounts[]` 只含 `id/siteId/siteName/username/tabName/role/tags/note/status/scheme/box/createdAt/updatedAt/hasPassword`，
**绝不包含** `passwordEnc` 或顶层 `fernetKey`；缓存也只存这份脱敏视图。
断网时若本地有缓存，仍返回 200，但带 `stale=true` + `error`；**401 不回落旧数据**（那会假装还登录着）。

> **当前边界**：后端保留 `/library` 只读接口（脱敏 + 缓存），但**独立云端库页面已于 2026-10-20 删除**，
> 桌面端只保留账号中心里的设备流登录/身份入口；没有 `merge` / 冲突消解 / 本地→云端推送。
> 不要按云端 `SYNC-CONTRACT` 假设桌面端会改云端账号。
