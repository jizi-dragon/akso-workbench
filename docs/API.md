# API 契约（API）

> **本文档的读者**：调接口的前端/脚本作者、排查「接口为什么返回这个」的人。
> 变更频率 O(接口)：**新增/修改端点必须同步更新本文档**。
>
> **本文档是权威契约**。FastAPI 自动生成的 `/openapi.json` 与 `/docs` 只覆盖路径与 pydantic 模型，
> **表达不了** SSE 事件序列、长轮询挂起语义、`seq` 游标与产物下载路径守卫——这三类恰是最易踩坑处，见 §12。
>
> 相关文档：[SCHEMA.md](SCHEMA.md)（表与跨进程数据契约）、[CONFIG.md](CONFIG.md)（端口与环境变量）、
> [AGENT.md](../AGENT.md)（已知问题清单）、[EXTENSION-PLANE.md](EXTENSION-PLANE.md)（扩展侧机制）。

## 0. 拓扑与约定

```
Electron 壳 :18767（控制服务，无认证）        FastAPI 服务 :18765（本文档主体）
  /windows /windows/focus /windows/close  ←──  browser_pool（CDP 模式先开户后连接）
  /update-state /update-check /update-install
  /extension-setup /health                 ←──  routes_extension / routes_update 代理
Electron 内置 Chromium :18766（CDP）       ←──  playwright connect_over_cdp
```

**全局约定**

| 项 | 约定 |
|---|---|
| 绑定 | `127.0.0.1` 回环，**无认证、无 CSRF、无 CORS 配置**——信任边界 = 本机 |
| 时间戳 | 毫秒整数（`storage.now_ms()`） |
| 字段命名 | 请求/响应默认 **camelCase**（`routes_settings` 用 pydantic alias 转换）；`account`/`env` 行**原样返回 snake_case 表列** + JOIN 出的 `env_name` / `env_base_url`，仅 `password_enc` 被剔除 |
| 路径前缀 | `/api/*` 为业务面；`/extension/*` 为执行面 |
| pydantic 陷阱 | `BaseModel` **默认忽略多余字段**——请求体必须显式建模，否则字段静默丢失（`AccountPatch` 漏 `box` 的事故） |
| 错误体 | 全为 `{"detail": "<中文说明>"}`（FastAPI `HTTPException` 默认形态） |
| 内容协商 | 无；产物接口用 `?download=true` 或后缀判断 |

**错误码语义**

| 码 | 用途 |
|---|---|
| 400 | 参数/业务规则错误（`AccountError`、空字段、未知命令、缺少必填项） |
| 404 | 资源不存在（账号/环境/任务/产物/会话） |
| 405 | 路由存在但方法不符（如 `GET /extension/setup-helper`） |
| 409 | 状态冲突（受管会话不可用、环境未确认、监听未启动） |
| 413 | 上传超限（蓝图 > 10 MB） |
| 502 | 上游失败（egmp 平台/工具执行/工厂执行） |
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
| GET | `/api/bootstrap` | — | `{conversations, stats:{qaTotal, adoptRate\|null}, settings:{apiKey,model,temperature}}` |
| PUT | `/api/settings` | `{apiKey?, model?, temperature?}` | `{ok:true, settings:{...}}` |

- `adoptRate`：`feedback==1 的比例 × 100` 取整；无任何评分时为 `null`。
- `apiKey` 明文回传（本机信任边界内的既有契约）——**这是 web 版能力的遗留形态**，见 [AGENT.md](../AGENT.md) §4。
- 设置读写唯一入口是 `services/settings.py`；DB 键固定为 `apiKey` / `model` / `temperature`。

## 3. SSE 流式对话 `POST /api/chat/stream`（`routes_chat.py`）

> **特殊通道，见 §12.1**（事件序列契约）。

```
POST /api/chat/stream   Content-Type: application/json
{ "conversationId": "可选，缺省则新建", "message": "用户问题" }
→ 200 text/event-stream（无 Cache-Control 缓存；X-Accel-Buffering: no）
```

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
  `accounts`（Python 依赖 + `playwright:chromium` 可执行文件存在性）。
- **适配器模块**：读 `adapters/*.json`（`akso-cc` / `akso-auto`）。
  阶段 3 原生化后它们降级为**只读参考存档**：体检项为 Python 依赖 + `source-reference`（信息项，
  原仓库缺失不影响功能，`ok` 恒为 `true`）。
- ⚠️ `status` 映射可读性存疑：`全过 → "ok"`、`全挂 → "degraded"`、`部分挂 → "missing"`（见 [AGENT.md](../AGENT.md) §4）。

## 6. 平台洞察 `/api/insight`（`routes_insight.py`）

凭证链：账号 id → `account` 表 → Fernet 解密（仅内存）→ `egmp.client.login`（55 分钟 token 缓存）。
产物落 `runtime/insight/<job_id>/`。**全程无 node、无子进程**。

请求体 `InsightRequest`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `command` | str | `login` / `inventory` / `understand` / `spider` |
| `account_id` | str | 必填，统一账号库账号 id |
| `objects` | str | `understand` / `spider`：逗号分隔对象编码（必填） |
| `known_objects` | str | `inventory` 兜底白名单（逗号分隔） |
| `llm` | bool | `understand` 是否附加 LLM 职责摘要（需已配置 DeepSeek Key，否则静默回落确定性标注） |
| `env_id` | str | token 缓存文件名后缀 |

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/insight/run` | **同步**执行，返回 `{job_id, status, exit_code, duration_ms, result, workdir}` |
| POST | `/api/insight/run-sse` | **SSE** 进度流（见 §12.2） |
| GET | `/api/insight/runs?limit=30` | 任务列表（`limit` 上限 200） |
| GET | `/api/insight/artifacts/{job_id}` | `{job_id, status, files:[{name,size,suffix}]}`（递归列产物） |
| GET | `/api/insight/artifacts/{job_id}/file?name=&download=` | 读产物（见 §12.4 路径守卫） |

`status` ∈ `running` / `succeeded` / `failed`；`result` 内为各命令的 `{summary|succeeded|stats, artifacts[]}`。

## 7. 配置工厂 `/api/factory`（`routes_factory.py`）

| 方法 | 路径 | 请求 | 说明 |
|---|---|---|---|
| POST | `/api/factory/blueprint` | `multipart/form-data` 文件 | 上传蓝图 → 暂存 `runtime/factory/<id>/blueprint.json`，返回 `{blueprint_id, path, name, top_keys, validation[]}`。上限 **10 MB**；解析用 `utf-8-sig` |
| POST | `/api/factory/blueprint/validate` | 蓝图 JSON（裸 dict） | 只校验不暂存 → `{issues:[], valid: bool}` |
| GET | `/api/factory/blueprints` | — | 已暂存蓝图列表 `{blueprints:[{blueprint_id,name,mtime}]}` |
| POST | `/api/factory/run` | `FactoryRunIn` | 执行 `create` / `orchestrate`（同步） |
| GET | `/api/factory/jobs?limit=30` | — | 任务列表（上限 200） |
| GET | `/api/factory/jobs/{job_id}` | — | 任务详情 + `log_tail`（末 400 行）+ `checkpoint` |
| GET | `/api/factory/jobs/{job_id}/artifacts` | — | 产物列表（排除 `blueprint.json` / `proc.log`） |
| GET | `/api/factory/jobs/{job_id}/log` | — | 日志文件下载（`FileResponse`） |

`FactoryRunIn`：`{blueprint_id?, command:"create"|"orchestrate", account_id (必填), confirmed: bool, requirement?}`

- **`confirmed=false` → 409**：这是「环境强制确认」原则（`native_orchestrate.run_full_workflow` 内还有
  `approved=True, confirmed_env=True` 双闸）。**不要为了自动化而绕过它**——写配置会落到真实环境。
- `orchestrate` 需要 DeepSeek Key（未配置 → 400）；流程：复杂度评估 → LLM 蓝图生成 →
  规范化 + 校验 → 落 `blueprint.generated.json` / `blueprint.json` / `spec.md` / `checklist.md`。

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

## 9. 托管浏览器 `/api/browser`（`routes_browser.py`）

> 特殊通道——**所有浏览器操作走 `browser_pool` 专职线程队列**（Playwright sync 单线程约束）。见 §12.5。

| 方法 | 路径 | 请求 | 说明 |
|---|---|---|---|
| POST | `/api/browser/open` | `{account_id, headful?:true}` | 建 context → 挂自动登录引擎 → 返回会话快照；已在线会话 = 聚焦其窗口（`reused:true`）。`BrowserError` 含「账号不存在」→ 404，否则 503 |
| POST | `/api/browser/focus/{account_id}` | — | 会话快照 / 404（无活动会话） |
| POST | `/api/browser/close/{account_id}` | — | `{closed:bool, detail}`；关窗前在线会话会落盘登录态 |
| POST | `/api/browser/navigate/{account_id}` | `{url}` | `url` 必须 `http(s)://`（否则 400）；**会话不存在会自动开户**；`BrowserError` → 409 |
| POST | `/api/browser/forget/{account_id}` | — | 清除持久登录态（`storage_state` 文件）；活动中的 context 不受影响 |
| GET | `/api/browser/sessions` | — | `{sessions:[快照]}`（会顺带刷新每个会话状态：标题、自动登录相位、自愈） |
| GET | `/api/browser/sessions/{account_id}` | — | 单个快照 / 404 |
| GET | `/api/browser/sessions/{account_id}/token` | — | `{account_id, has_token, token_head}`（**只回前 16 字符**） |
| GET | `/api/browser/saved/{account_id}` | — | `{account_id, saved}`（是否有持久登录态文件） |
| GET | `/api/browser/check` | — | chromium 可执行文件体检 `{ok, detail}` |

**会话快照字段**：`{account_id, status, title, detail, started_at, restored, headful, heal_count,
autologin, has_token, monitoring, monitor_log}`；
`status` ∈ `launching` / `logging_in` / `online` / `stopped` / `error`；
`autologin` 为引擎相位快照 `{phase, reason, attempts, errors, userTouched, ...}`。

## 10. Agent 工具层 `/api/agent`（`routes_agent.py`）

| 方法 | 路径 | 请求 | 说明 |
|---|---|---|---|
| GET | `/api/agent/tools` | — | `{tools:[{name,description,args}]}` |
| POST | `/api/agent/invoke` | `{tool, args:{}}` | `{tool, ok:true, result}`；未知工具 404；`ValueError`/`TypeError` → 400；其余 → 502 |
| GET | `/api/agent/audit?limit=50` | — | `{audit:[行]}`（上限 500）——⚠️ 见 [AGENT.md](../AGENT.md) §4 |

工具：`login_platform`（原生登录 + token 预热，回 `tokenHead` 前 8 字符）、`read_config`（egmp 只读元数据）、
`run_insight`（跑 `understand`，**描述文字仍写「子进程封装」，实际已原生化**）、
`browser_status`（会话状态墙）。

> ⚠️ 本模块与前端对话用的 `harness/tools.py`（`get_current_time` / `read_local_file` / `read_web_page`）
> **是两套独立的工具注册表**，不要混淆。

## 11. 执行面 `/extension`（`routes_extension.py`）

| 方法 | 路径 | 请求 | 说明 |
|---|---|---|---|
| GET | `/extension/snapshot` | — | 数据面快照（见 [SCHEMA.md](SCHEMA.md) §5.1）；`snapshotId` 为内容哈希 |
| GET | `/extension/commands?after=N&wait=S` | — | 拉指令（**长轮询**，见 §12.3）；`cursor` + `longPoll` 标志 |
| POST | `/extension/commands` | `{type:"par.open"\|"wheel.toggle", payload?}` | 桌面入队 → `{seq, accepted:true}`；未知 type → 400 |
| POST | `/extension/launch-chrome` | — | 确保用户 Chrome 在运行（未运行则拉起）；`{launched, running, path?}` |
| POST | `/extension/ack` | `{seqs:[int]}` | `{acked:[...]}`；全部未回收指令都已 ack 时清空队列 |
| POST | `/extension/state` | `{items:[{desktopId,tabs,hasToken}], extVersion, desktopVersion}` | `{accepted:n}`；`_STATE` TTL 60s |
| GET | `/extension/health` | — | `{connected, reportedAccounts, everConnected, desktopVersion, extVersion, extStale}` |
| POST | `/extension/update-state` | —（无请求体） | `{version, shell:{...}}`；与 `GET /api/update` **同源同结构**（`read_shell_state()`），放在扩展蓝图内是为了让「壳 ↔ 扩展 ↔ UI」三者运行态信息集中一处 |
| POST | `/extension/setup-helper` | `{mode:"install"\|"reload"}` | 代理到壳 `:18767 /extension-setup`（打开扩展目录 + `chrome://extensions` + 步骤弹窗） |

**`/extension/health` 判定链**（账号中心提示条数据源）：
`connected` = 60s 内收到过状态上报；`everConnected` = `settings.ext_connected_once` 闩锁
（**一次即永久静默**——用户定稿：连上过一次就不再提示）；
`extStale` = 上报的 `extVersion` ≠ `desktopVersion`；
提示条仅在 `!connected && !everConnected` 或 `extStale` 时出现。

## 12. 特殊通道专章

### 12.1 SSE 对话事件序列（`POST /api/chat/stream`）

事件为 `data: {json}\n\n` 行，**序列有严格契约**（`harness/types.py` + `harness/loop.py`）：

```
（前置）若未配置 API Key： {"type":"error","message":"请先在设置中配置 DeepSeek API Key。"} → {"type":"done"}
{"type":"start","conversationId":"..."}
{"type":"token","text":"..."}            ← 0..N 次（增量正文）
{"type":"thought","text":"..."}          ← 可选：模型在工具调用前的说明
{"type":"action","call":{...}}           ← 工具调用（每次一条）
{"type":"observation","result":{...}}    ← 对应结果（紧随其 action）
↑ (thought/action/observation) 可循环多轮，受 max_steps 预算约束
{"type":"final","content":"完整正文"}     ← 或 {"type":"error","message":"..."}
{"type":"done","conversationId":"...","content":"...","metricId":"...","titleUpdated":bool,"latencyMs":int}
```

- **`done` 必发**（含错误路径），其 `metricId` 是 §4 反馈接口的入参。
- 模型无内容时 `content` 回落为「（模型未返回内容）」；错误路径写「错误：<原因>」。
- 会话标题在首条用户消息时自动更新，`titleUpdated` 告知前端。

### 12.2 SSE 洞察进度流（`POST /api/insight/run-sse`）

```
{"type":"log","line":"<日志行>"}    ← 0..N（来自 worker 线程，经 loop.call_soon_threadsafe 投递）
: keep-alive                        ← 空闲 0.5s 发一次（避免代理/浏览器断连）
{"type":"done","job_id":"...","status":"succeeded|failed","exit_code":0|1,
 "duration_ms":int,"result":{...},"workdir":"..."}   ← 终态（必发）
```

- 用 `loop.run_in_executor` 跑同步 `_run_job`；`on_log` 线程安全投递。
- 循环条件 `while not task.done() or not queue.empty()`：**任务结束后仍会排空遗留日志**，不会丢最后几行。

### 12.3 长轮询与 `seq` 游标（`GET /extension/commands`）

| 参数 | 语义 |
|---|---|
| `after` | 扩展已消费的最大 seq；只返回 `seq > after` 的指令 |
| `wait` | 无指令时的挂起秒数；`wait=0`（默认，向后兼容）立即返回。**服务端上限 25s**（`min(max(wait,0),25)`，防连接堆积）；期间每有指令入队即被 `notify_all()` 唤醒 |

- 服务端用 `threading.Condition`（与队列同一把锁）；入队时 `notify_all()` 唤醒等待者。
- 响应 `longPoll` 字段 = 本次是否真的挂起过。**扩展用它判断服务端是否支持长轮询**：
  旧版服务端不认 `wait` 会立即返回且 `longPoll=false` → 扩展退避 1.5s 轮询，
  **否则会以 HTTP 往返速度热循环打满 CPU**。
- `seq` 跨**服务端重启**单调递增（持久在 `settings.ext_cmd_seq`）。若重置，扩展持久游标会让
  所有新指令被 `seq > after` **永久吞掉**（0.2.4 实锤断点）。
- 实测收益：指令下发平均 **964ms → 20ms**（约 50 倍）。

### 12.4 产物读取与路径守卫（`GET /api/insight/artifacts/{job_id}/file`）

- 两种 I/O 形态：`?download=true` 或后缀 ∉ `{.md,.json,.txt,.log,.csv}` → `FileResponse`（附件下载）；
  否则读文本返回 `PlainTextResponse`（超 200 万字符截断）。
- **路径守卫**：`artifact_dir` 与 `name` 都 `.resolve()`，再断言
  `str(target).startswith(str(artifact_dir))`。
  ⚠️ **这是纯字符串前缀比较，未补路径分隔符**——`.../insight/abc` 会「包含」`.../insight/abc_evil`。
  本地单人场景风险低，但**新增类似端点时应改用 `Path.is_relative_to()`**。见 [AGENT.md](../AGENT.md) §4。

### 12.5 浏览器操作串行化（`/api/browser/*`、`/api/monitor/*`）

- Playwright sync API 绑定创建它的线程且非线程安全，而 FastAPI 会把并发请求派到不同线程 →
  `browser_pool` 持有**专职工作线程**，所有操作以 `Future` 提交串行执行。
- **事件回调内禁止连接调用**（`response.text()` / `page.evaluate` 重入 = worker 死锁）——
  Monitor 的事件只入队，在 worker 作业间隙统一 `drain()`。
- 超时：`monitor_start` 为 90s（可能触发开户），其余多数作业**无超时**——
  页面操作卡住时 HTTP 请求会一直挂着（见 [AGENT.md](../AGENT.md) §4）。
- CDP 模式三律：`remote-debugging-port` 必须 app ready 前设置；**先 `:18767 /windows` 开户再 connect**；
  Playwright 实例由 `_CdpState` 单一持有（同线程第二个 sync 实例会死锁）。

## 13. 监听录制 `/api/monitor`（`routes_monitor.py`）

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| POST | `/api/monitor/start` | `{account_id}` | `{monitoring:true, log, stats, ...会话快照}`；`BrowserError` → 409 |
| POST | `/api/monitor/stop` | `{account_id}` | `{stopped:true, stats, log}`；无进行中监听 → 409 |
| GET | `/api/monitor/status/{account_id}` | — | `{monitoring, log, session_status}`；无会话时 `{monitoring:false}` |

- `monitor_start` 会**确保托管会话在线**（不存在则自动开户），因此耗时较长（`_submit(timeout=90)`）。
- 产物落 `runtime/monitor/<account_id8>-<YYYYmmdd-HHMMSS>/`。录制引擎三级降噪：
  `dropped` / `trimmed` / `full`（真机验收：248 请求捕获 → 238/2/8）。
- 会话关闭**不会自动停监听**（`_close_entry` 未清理 `entry.monitor`）——见 [AGENT.md](../AGENT.md) §4。

## 14. 更新面 `/api/update`（`routes_update.py`）

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| GET | `/api/update` | — | `{version, shell:{...}, updateAvailable}` |
| POST | `/api/update/{action}` | `action` ∈ `check` / `install` | `{ok, result?, shell?, error?}` |

- 数据来源是**壳写的 `shell-state.json`**（不是每次 HTTP 探壳）：账号中心每 3s 轮询，
  读本地文件更便宜，且壳不在时能优雅降级。
- `shell` 结构固定不缺键；**TTL 60s**（壳每 15s 心跳）：超过即 `live:false` + `phase:"inactive"` +
  `label:"桌面壳未运行"`（损坏/缺失文件走同一条降级路径）。
- `POST` 代理到壳控制服务 `:18767`（`check` 超时 60s、`install` 8s）；
  **壳不可用时返回 `ok:false` 而非报错**（UI 退化为文字提示，与 `/extension/setup-helper` 同一套降级哲学）。
- 未知 action 返回 `{ok:false, error:"未知动作：..."}`——**HTTP 200，不是 4xx**（前端据此判 `ok`）。
- `updateAvailable` 用 `version_tuple()` 比较（非数字段按 0 处理）。

## 15. 静态资源

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 工作台主界面 `static/index.html` |
| GET | `/static/*` | `StaticFiles` 挂载 `workbench/static/` |

页面：`index.html`（对话 + 设置）、`pages/insight.html`、`pages/factory.html`、
`pages/accounts.html`、`pages/wheel-picker.html`（轮盘，`?transparent=1` 供壳的透明窗加载）、
`blank.html`（壳的会话窗标记页，`?w=<windowId>`）。

## 16. 壳控制服务 `:18767`（Electron 主进程内，非 FastAPI）

| 方法 | 路径 | 请求 | 说明 |
|---|---|---|---|
| GET | `/health` | — | `{ok:true}` |
| POST | `/windows` | `{windowId}` | 开户（`partition: persist:<windowId>` 独立持久分区）；已存在且未销毁 → `{ok:true, reused:true}`；缺 windowId → 400 |
| POST | `/windows/focus` | `{windowId}` | 还原 + 显示 + 聚焦；不存在 → 404 |
| POST | `/windows/close` | `{windowId}` | 关窗（幂等） |
| GET | `/update-state` | — | `{ok, shell:true, ...updater.state()}` |
| POST | `/update-check` | `{}` | 触发手动检查（结论弹窗） |
| POST | `/update-install` | `{}` | `{ok: installOnExit()}` |
| POST | `/extension-setup` | `{mode}` | 打开扩展目录 + `chrome://extensions` + 步骤弹窗 |

无认证；仅监听 `127.0.0.1`。`browser_pool` 的 CDP 模式依赖它「先开户后连接」。
