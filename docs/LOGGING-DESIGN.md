# 全项目运行日志系统：设计与实施方案

_2026-09-20 撰写。锚定 `15b847e`（main）。本文的现状盘点是**实测**的，命令与结果见 §2。_

> ## ✅ 实施状态（2026-09-20 当天落地）
>
> | 期 | 状态 |
> |---|---|
> | **P0** 单点 `configure()` + 双滚动 FileHandler + 脱敏 Filter + 异常钩子 | ✅ 已实现 |
> | **P1** `traceId` 贯通 + 请求日志 + 工具调用日志（带 `auditId`）+ uvicorn 接入 + 壳侧 stdio 落盘 | ✅ 已实现 |
> | **P2** `/api/logs/*` 四个端点 | ✅ 已实现（UI 页面未做，见验证报告 §4） |
> | **P3** OTLP 导出 / 联合视图 | ❌ 按设计不做（§6 已说明理由） |
>
> **验证结果**：端到端 **32/32 PASS**、组件测试 **32 passed**、反证 **6/6 有效**。
> 逐条检查矩阵、**与本文的差异（as-built）**、以及已知边界见
> → **[LOGGING-VERIFICATION.md](LOGGING-VERIFICATION.md)**。
>
> ⚠️ **实施中改了本文的两处设计**（都有理由，已写进验证报告 §4）：
> ① **不做 `QueueHandler`**（队列满会静默丢记录，比同步写更糟）；
> ② **保留 `server.log`** 并重新定位为"启动面包屑"（`blank.html` 引用了它，
> 且它在日志系统自身失效时仍有效）。

> **这份文档解决什么**：现在的项目**没有任何运行日志**——38 处 `logger.*` 调用全部落空，
> 磁盘上一个日志文件都没有（实测数据目录里只有 `workbench.db`）。
> "后期查找漏洞"在当前状态下**无从谈起**：出问题时能拿到的只有数据库里那几张审计表。
>
> 本文给出**分层设计 + 落盘方案 + 关联 ID 贯通 + 检索入口 + 分期实施**，
> 并明确**不做什么**（避免把已有的审计表重新造一遍）。

---

## 1. 为什么需要它（一条实测证据胜过一段论证）

`api/__init__.py:134-154` 的 `_tool_selfcheck()` 是这样的：

```python
if missing:
    logger.error("工具自检未通过：必需工具缺失 %s（对应插件未装配成功）", missing)
```

它的注释写着：

> 坏插件是被**静默跳过**的（`ToolHub.load` 的刻意设计），所以这里必须把结果喊出来：
> 少了哪个插件、少了哪个必需工具，一眼可见。

**而它喊不出来。** 全仓没有任何 handler，`logger.error` 会走 `logging.lastResort`
——一个写到 **stderr** 的兜底 handler，而桌面壳以 **`stdio: 'ignore'`** 启动 sidecar
（`desktop/main.js:88,99`）→ **stderr 被丢弃**。

⇒ 「打包漏收插件」这个**已经踩过一次**的故障（PITFALLS #32），其唯一的启动期报警
**在打包态 100% 听不到**。这正是需求 1 要解决的最小、也最要紧的一件事。

---

## 2. 实测现状盘点

### 2.1 日志侧：调用了，但没有落点

| 项 | 实测值 | 命令 |
|---|---|---|
| `logger.*` 调用总数 | **38**（`info` 18 · `warning` 18 · `error` 1 · `debug` 1） | `Select-String -Pattern 'logger\.(debug\|info\|warning\|error)\('` |
| `logging.getLogger(...)` 模块数 | **14**（全部 `workbench.*` 命名空间） | 同上 |
| `basicConfig` / `FileHandler` / `RotatingFileHandler` / `addHandler` / `dictConfig` | **0 处** | 全仓 grep |
| 日志文件 | **0 个**。`%APPDATA%\Workbench\` 顶层**只有** `workbench.db` | 列目录 |

**后果按级别分**：

| 级别 | 调用数 | 当前去向 | 结果 |
|---|---|---|---|
| `info` / `debug` | **19** | 无 handler → `lastResort` 是 **WARNING 级** → 直接丢弃 | **完全不可见** |
| `warning` / `error` | **19** | `lastResort` → **stderr** → 壳 `stdio:'ignore'` → 丢弃 | **打包态不可见**；开发态仅在终端可见 |

唯一写盘的地方是 `server_entry.py:29-38` 的 `_log_startup()`：**仅冻结态**、
**仅一行**（版本/pid/URL），写 `DATA_DIR/server.log`。

### 2.2 已有留痕：数据库（**不要重复造**）

`workbench.db` 有 19 张表，其中承担"事后排查"职责的 6 张：

| 表 | 行数（实测） | 性质 |
|---|---|---|
| `agent_audit` | 0 | 工具调用审计（谁、哪个工具、什么风险、耗时、参数打码） |
| `pending_action` | 0 | 待确认/已决策的写操作（**仅留痕**，权威在内存） |
| `deepseek_usage` | 0 | 每次回答的 token 与**缓存命中量** |
| `sandbox_run` | 0 | 沙箱执行（策略指纹、退出码、是否树杀） |
| `skill_audit` | 0 | Skill 上传/安装/卸载 |
| `job_logs` | 0 | **建了索引但零写入是故意的**（日志落磁盘，见 CODEBASE_OVERVIEW 约定） |

> **0 行的原因已归因清楚**（`tmp/diagnose_empty_tables.py`）：最后一条对话是
> **2026-09-09**，而工具插件层与缓存层是 **2026-09-15** 才落地的——是**时间差**，
> 不是静默失败（我实测 `persist()` 能正常写入，探针行已清理）。

### 2.3 任务级磁盘产物（散落，无统一检索）

| 产物 | 位置 | 覆盖 |
|---|---|---|
| `proc.log` | `<洞察工作区>/<对象>/<时间戳>/proc.log`、`runtime/factory/.../proc.log` | 单个洞察/工厂任务 |
| Monitor 录制 | `monitor.py:183` 的 checkpoints JSONL | 单次录制会话 |
| Monitor 解读 | `interpreter.py:138` | 单次解读 |
| `server.log` | `DATA_DIR/server.log` | **仅启动一行，仅冻结态** |

⇒ **任务内部有日志，任务之间没有**。跨任务的"为什么会这样"（配置改了、插件没装、
端口被占、扩展 401、缓存命中率掉了）**无处可查**。

### 2.4 壳侧（Electron）

`desktop/main.js` 用 `console.log`（如 `:523`、`:661`），打包态 Windows 上 Electron 主进程
**没有附加控制台**，这些输出同样**不可见**。`desktop/updater.js:122` 明确"不额外引 electron-log"。

---

## 3. 设计目标与不变量

### 3.1 目标（对应"便于后期查找漏洞"）

1. **任何一次异常/失败，事后都能重建现场**：谁在什么时候、对什么对象、做了什么、结果如何。
2. **一次对话/一个任务可以被串起来看**：从用户输入 → 模型调用 → 工具调用 → 沙箱/平台 → 收尾。
3. **不依赖 stdio**：日志的权威落点由 Python 进程**自己打开**，因为壳会丢弃管道。
4. **可检索**：按级别/时间/关键字/关联 ID 查，不必手工翻文件。

### 3.2 不变量（违反即视为缺陷）

| # | 不变量 | 理由 |
|---|---|---|
| L1 | **日志失败绝不影响主流程** | 与本仓 `audit.record` / `persist()` 同一纪律（`plugin_api._audit`、`cache.persist` 都吞异常） |
| L2 | **凭据永不入日志** | [ADR-0007](adr/0007-credentials-never-plaintext.md)。复用 `plugin_api` 的 `_REDACT_KEYS` 与 `approvals.redact_args`，并加**日志级 Filter**（不能只靠调用方自觉） |
| L3 | **不重复已有的结构化留痕** | 工具调用的**权威**是 `agent_audit`，日志只记"发生了什么"并**带 `auditId` 指向它**。两处都写全量 = 两个会漂的事实源 |
| L4 | **不依赖 stdio / 不依赖 CWD** | 壳 `stdio:'ignore'`、打包态 CWD 不确定 |
| L5 | **单进程内单写者** | sidecar 是单进程；不要引入多进程共享同一文件的设计（Windows 上会互相覆盖） |
| L6 | **日志是本地资产** | 只在 `127.0.0.1` 暴露；下载/查看端点不得成为新的凭据外泄面（见 §5.7） |

---

## 4. 分层设计

**核心判断**：不要用一个"万能日志"覆盖一切。**已有的 DB 表在"结构化、可查询、低容量"上更强**，
日志的不可替代价值在**高容量、自由文本、有时序**的叙述。分层如下：

```
L0  进程日志（本文新增，补空白）        logs/workbench-*.jsonl
    · 启动横幅、装配结果、自检、异常栈、后台任务
    · 这是"现在一个字节都没有"的那一层

L1  请求日志（本文新增）                logs/http-*.jsonl
    · 每个 HTTP 请求一行：方法/路径/状态码/耗时/traceId/是否带了令牌
    · 不记请求体（体积 + 凭据风险），只记长度与关键字段

L2  领域事件（**已有** DB 表 + 任务级文件，本文只做"关联"）
    · agent_audit / pending_action / deepseek_usage / sandbox_run / skill_audit
    · insight_runs / blueprint_jobs + 各自的 proc.log
    · 本文不改它们的职责，只保证日志里带得出指向它们的 ID

L3  审计（**已有**，合规事实源，本文不碰）
    · 仅一条要求：L0/L1/L2 的关联 ID 必须一致，便于三方对照
```

**为什么这样切**：L2/L3 已经设计得不错（有风险分级、有打码、有扼流点），
**缺的是 L0/L1 以及把四层串起来的关联 ID**。重造 L2 是净损失。

---

## 5. 具体方案

### 5.1 落盘位置与命名

```
<DATA_DIR>/logs/
├── workbench-20260920.jsonl      # L0 进程日志（按天切）
├── http-20260920.jsonl           # L1 请求日志（按天切）
└── workbench-20260920.1.jsonl    # 当天超出体积上限后的滚动（.1 最新）
```

- **按天 + 按体积双重滚动**：`TimedRotatingFileHandler(when="midnight", backupCount=14)`
  叠加 `.1` 体积滚动（或用两个 handler）。默认**保留 14 天**，可配。
- **不用 `%APPDATA%` 之外的路径**，不写 CWD（不变量 L4）。
- **`server.log` 退役**：`_log_startup()` 改为调用统一日志的启动横幅——
  **不要保留两个启动日志**（否则又是一个会漂的事实源）。

### 5.2 格式：JSONL（机器读）+ 人读行（可选）

**主格式 JSONL**（一行一个 JSON 对象），字段固定：

```json
{"ts":"2026-09-20T14:03:11.482+08:00","level":"INFO","logger":"workbench.plugin_api",
 "msg":"插件已装配：accounts（5 个工具，origin=core）",
 "traceId":"b3f1a9c2","pid":12345,"ver":"0.3.12",
 "auditId":"9c1e...", "conversationId":"e8328b7e", "jobId":null, "tool":"accounts_list"}
```

**为什么 JSONL 而不是纯文本**：需求是"便于后期查找漏洞"——那意味着**过滤与聚合**
（"所有 `level=ERROR` 且 `tool=accounts_browser_open` 的"、"这次 traceId 下的全部行"）。
文本日志要靠正则猜，JSONL 直接 `jq` / `sqlite` / 代码解析。

**可选**：`WORKBENCH_LOG_FORMAT=text` 输出人读单行（开发态默认 text、打包态默认 jsonl）。
两种格式共用同一套字段，**不是两套日志**。

### 5.3 关联 ID 贯通（**本方案的核心价值**）

用 `contextvars` 承载，避免在几十个函数签名里穿参数（与 `async` 天然兼容）：

| ID | 产生点 | 用途 |
|---|---|---|
| `traceId` | HTTP 中间件（每个请求一个，短随机） | **把一次请求的所有日志串起来** |
| `conversationId` | `routes_chat` 已有 | 串联多轮对话 |
| `auditId` | `ToolHub.execute` 已有（`loop.py` 也生成了） | **指向 `agent_audit` 那一行** |
| `jobId` | `insight_runs` / `blueprint_jobs` | 指向任务与它的 `proc.log` |
| `tool` / `plugin` | 工具调用上下文 | 过滤 |

**这一条直接把 L0/L1 与 L2 焊在一起**：拿到一个 `auditId`，既能在 `agent_audit` 查结构化行，
又能在日志里看它前后的叙述；拿到一个 `traceId`，能看整条链路。

### 5.4 必须捕获的"当前完全丢失"的东西

| 项 | 为什么必须有 | 做法 |
|---|---|---|
| **未捕获异常栈** | 崩溃只留一句"服务没起来" | `sys.excepthook` + `threading.excepthook` + `asyncio` 异常处理器，全部转成 `logger.exception` |
| **uvicorn 的错误日志** | uvicorn 有自己的 logger，默认不进我们的 handler | `logging.config.dictConfig` 里把 `uvicorn` / `uvicorn.error` / `uvicorn.access` 接到同一 handler |
| **启动横幅** | 现场最常问"跑的是哪一版、哪个端口、插件齐不齐" | 一条 INFO：版本 + pid + frozen + 三个端口 + 工具自检结果 + 失败插件 |
| **装配失败** | 坏插件是静默跳过的 | `hub.failures` 在启动横幅里逐条列出（**这就是 §1 那条 `logger.error` 的去处**） |
| **壳侧 stderr** | Python 之外的崩溃（Node/Electron）也会丢 | 见 §5.6 |

### 5.5 脱敏（不变量 L2 的落地）

三层防御，**不依赖调用方自觉**：

1. **日志级 Filter**（新增）：对 `record.args` 与 `record.msg` 做键名打码，
   复用 `plugin_api._REDACT_KEYS`（`password` / `token` / `api_key` / `secret` / `cookie` / `authorization` …）；
2. **复用既有单点**：`approvals.redact_args()` 已经做过一遍，日志层再兜一次；
3. **明令禁止**：`AGENTS.md` 里写死"凭据不落明文"，并在 code review 检查
   （**但这条只是第三道，不能当第一道**）。

**另外两条刻意的不记录**：

- **不记请求/响应体全文**（只记长度与关键字段）——体积 + 凭据风险；
- **不记工具结果全文**（权威在 `agent_audit` 的 `result_summary`，且那里已经打码）。
  日志只记 `tool` / `status` / `durationMs` / `contentChars`。

> ⚠️ **但要注意**：`agent_audit.result_summary` 记的是**工具返回内容的前 1000 字符**。
> 若某工具能返回敏感内容（当前 `read_local_file` 就是，见
> [AGENT-TOOL-AUDIT.md](AGENT-TOOL-AUDIT.md) P0-B），审计表会**二次落库**。
> 那条缺口要在 B 的修复里一起处理——**不是本日志系统的职责**，但必须知道它存在。

### 5.6 壳侧（Electron）与 stdio

**两件事，都要做**：

1. **Python 不依赖 stdio**（本方案的主线）——L0 由 Python 自己开文件写。
2. **壳侧也别丢**：把 `desktop/main.js` 的 `stdio: 'ignore'` 改为
   `['ignore', 'pipe', 'pipe']`，把 sidecar 的 stdout/stderr **追加**写
   `<DATA_DIR>/logs/sidecar-stdio-<date>.log`。
   这条是 Python 日志**之外的保险**：解释器启动前的失败（PyInstaller 引导问题、
   `import` 阶段的 SyntaxError）**根本到不了 logging**，只有 stderr 抓得到。
   壳自身的 `console.log` 同理落一份 `shell-<date>.log`。

> ⚠️ 改 `stdio` 时必须做「**不能因为读管道而阻塞 sidecar**」的处理
> （持续消费 pipe，否则缓冲区满会让子进程卡死）。这是本仓的既有教训类型
> （PITFALLS #2 的"事件回调内禁止连接调用"同族：**不消费的管道会成为阻塞源**）。

### 5.7 检索入口

| 入口 | 形态 | 说明 |
|---|---|---|
| `GET /api/logs/tail?n=200&level=ERROR&trace=<id>&contains=<kw>` | JSON | 读最近 N 行并过滤；**默认只读当天** |
| `GET /api/logs/trace/<traceId>` | JSON | 把一次请求的 L0+L1 合成时间线 |
| `GET /api/logs/download?date=YYYY-MM-DD` | 文件流 | 给人拿去离线分析（导出前**再跑一次脱敏 Filter**） |
| UI：设置页 → 「运行日志」 | 页面 | 时间线视图 + 级别过滤 + 一键复制 traceId |
| CLI | `python tools/logs.py tail --follow` | 排障时最常用 |

**安全边界**（不变量 L6）：这些端点**只监听回环**（与 `/api/*` 同级），
且**导出前重新脱敏**。日志本身可能含用户数据（消息长度、对象编码、路径），
**不要把它当成"公开无妨"的东西**。

---

## 6. 实施分期

| 期 | 内容 | 工作量 | 验收判据（**可机检**） |
|---|---|---|---|
| **P0** | `workbench/logging_setup.py`（`configure()` 单点）+ `main.py`/`server_entry.py` 调用 + 按天滚动 FileHandler + 脱敏 Filter + `sys.excepthook` | 0.5–1 天 | ① 启动后 `<DATA_DIR>/logs/workbench-<date>.jsonl` **存在且非空**；② 启动横幅含版本/端口/工具数/失败插件；③ 新增测试：**`logger.info` 在配置后确实落盘**（现在这条断言不存在，所以"丢日志"没人发现） |
| **P1** | `traceId` 中间件 + 请求日志 + 工具调用日志（带 `auditId`）+ uvicorn logger 接入 + 壳侧 stdio 落盘 | 1–2 天 | ① 一次对话的所有 L0 行带**同一个 traceId**；② `grep auditId` 能把日志与 `agent_audit` 对上；③ 故意让一个插件装配失败 → **启动横幅里能看见** |
| **P2** | `/api/logs/*` 端点 + 设置页日志视图 + `tools/logs.py --follow` | 2–3 天 | ① 按 traceId 能还原一次完整对话时间线；② `level=ERROR` 过滤可用；③ 下载的文件里**搜不到**测试用凭据字符串 |
| **P3** | 与 `deepseek_usage` / `agent_audit` 的联合视图（命中率趋势、工具失败率）+ 可选 OTLP 导出 | 3–5 天 | 见 [CORE-ARCHITECTURE.md](CORE-ARCHITECTURE.md) §7.5 |

**P0 完工的最小价值**：需求里那句"便于后期查找漏洞"**从"不可能"变成"可能"**——
现在连"应用启动过、装了几个工具"都查不到。

---

## 7. 明确不做的事

| 不做 | 理由 |
|---|---|
| **不把日志塞进 SQLite** | `job_logs` 表"建了索引但零写入"是**刻意的**（CODEBASE_OVERVIEW 约定）。高容量自由文本进 SQLite 会与主库争锁、撑大备份。**让磁盘文件做它擅长的事** |
| **不重造一张"万能事件表"覆盖 `agent_audit`** | 那就是第二个会漂的事实源（本仓 2026-09-15 的"两套工具表"教训）。日志只**指向**它 |
| **不引第三方日志框架**（structlog / loguru / electron-log） | 需求用 stdlib `logging` + `dictConfig` 就够；`updater.js:122` 已有"不额外引 electron-log"的同类判断。**依赖越少，打包态越不容易静默失效** |
| **不做日志上报/云端收集** | 单机本机工具，日志可能含客户数据（对象编码、路径）。**留在本机** |
| **不记录请求/响应体与工具结果全文** | 体积 + 凭据风险；权威已在他处 |

---

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| **日志把自己撑爆磁盘** | 双重滚动（日切 + 体积）+ `backupCount=14` + 启动时统计目录大小并 WARN |
| **日志拖慢请求** | `QueueHandler` + `QueueListener`（日志 I/O 移出请求线程）；L1 请求日志只在 `INFO` 及以上 |
| **日志写失败导致主流程失败** | 不变量 L1：handler 包一层 try/except，失败只 `os.write(2, ...)` 兜底一次，**绝不抛** |
| **脱敏漏掉新字段** | Filter 基于**键名**黑名单（不是白名单），并加一条测试：往日志里打已知凭据形状的字符串，断言落盘内容里搜不到 |
| **两边同时写同一文件**（未来若出现多进程） | 现在单进程，**明确不支持**多进程共享；若将来需要，按 pid 分文件（写进设计约束而不是事后踩） |
| **日志成了新的攻击面** | §5.7 的安全边界；`/api/logs/*` 与 `/api/*` 同级（本机回环、无认证——这是既有的 R1b 取舍，**新增端点不得扩大它**） |

---

## 9. 与既有文档/决策的关系

- 本方案落地后，应新增一篇 ADR（用 [docs/adr/0000-template.md](adr/0000-template.md)），
  记录"**为什么日志文件而不是数据库**"（§7 第一条）与"**为什么不用第三方框架**"这两个决策，
  并在「外部参照」栏对照 OTel GenAI 语义约定（为 P3 的导出留路）。
- `AGENTS.md` 需补一条铁律：**"凭据永不入日志"** 与 **"日志失败不得影响主流程"**。
- `docs/CONFIG.md` 需补 `WORKBENCH_LOG_LEVEL` / `WORKBENCH_LOG_DIR` / `WORKBENCH_LOG_FORMAT`。
- `docs/API.md` 需补 `/api/logs/*` 四个端点。
- `docs/CODEBASE_OVERVIEW.md` 的"风险与毛边"里应把
  「**全仓无日志 handler，19 处 info/debug 调用被静默丢弃**」记为一条已修项。
