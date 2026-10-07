# 代码库总览（CODEBASE_OVERVIEW）

_由 `learn-codebase` 技能于 **2026-10-07** 生成，锚定 `main@ce3bf3d`（v0.4.1，2026-09-29 发布）。
全仓 345 个受版本控制文件、179 个提交。_

> ⚠️ **先说本文件自己的存在状态。** 它在 **2026-09-29「文档瘦身」（用户下达）** 中被删除，
> 此后仓库里仍有文档与代码注释写着「`docs/CODEBASE_OVERVIEW.md`
> （该文件已于 2026-09-29 删除）」。本次是**按要求复建** ⇒ 那些标注当时是陈旧的。
> ★ **2026-10-07 已同步**：**7 个文件 16 处**改成「2026-09-29 曾删除，2026-10-07 重新生成」；
> `docs/adr/` 里另有 8 处随该目录的合订一并处理。
> **保留「曾删除」而不是直接写「已恢复」** —— 读者需要知道它中间消失过，
> 否则会以为一直都有、进而以为某份历史文档里的引用是错的。
>
> 本次分析**只新增了本文件**，没有修改、没有提交任何其它内容。
> 下文所有数字都标注了**取法**；凡引用既有文档的结论，都注明了出处，可自行复核。

---

## 一、摘要

**Workbench 是一个 Windows 单机桌面工作台**，服务于「实施工程师」这一单一角色：
把**对象分析**（摸清一个 SaaS 平台上有什么对象/字段/状态/流转）、**统一账号库**（加密存多账号口令）
与**多账号快捷登录**（一个 Chrome 里多身份并行）合到一个应用里，并在此之上提供一个
**工具驱动的对话 Agent**——它最大的特点不是"能聊天"，而是**它操作的是有生产数据的真实平台**。

四个来源项目（`akso-cc` / `mogul_simulator` / `akso-auto` / `quick-login`）的能力已**全部原生化**：
运行时零依赖原项目、零依赖 Node、零联网（首次装载浏览器扩展除外），原四仓库**冻结不动**，
只以只读存档 `adapters/*.json` 的形式被引用。

产品边界在 2026-09-22 被主动收窄过一次：**原 `akso-auto` 那条"写平台数据"的路径整体删除**
（配置工厂 / 蓝图校验 / 幂等创建 / 拓扑编排 / 断点续跑），因此**产品里今天没有任何
`Risk.DANGEROUS` 级工具**，唯一那个（`factory_create`）已不存在。平台知识留在文档里，
写能力不在。

**它真正与众不同的地方不是功能清单，而是工程纪律**：工具只有一个定义处、
依赖方向由 AST 门禁守、每条不变量都有反证、坑清单只增不改（120 条）、
ADR 只增不改（22 篇）、文档里的"N 个工具"由脚本从运行期中枢生成并对账。
本文件第九节把这份纪律**和它的裂缝**一起写下来——只有前者是不诚实的。

---

## 二、技术栈

| 项 | 实际情况 | 出处 |
|---|---|---|
| 语言 | Python 3.12（**唯一真源 `.python-version`**，与 CI 逐字一致）、JavaScript（原生 ES module，**无框架无打包器**）、TypeScript（MV3 扩展，esbuild） | `pyproject.toml`、`tests/test_python_version_contract.py` |
| 服务端 | FastAPI + uvicorn；SSE 流式（`text/event-stream`） | `workbench/api/__init__.py:181` |
| 桌面壳 | Electron（主进程 + 托盘 + 全局热键 + 控制端口），NSIS 打包 | `desktop/main.js`、`desktop/package.json` |
| 浏览器自动化 | Playwright **sync API**（单线程单实例，全部经 `browser_pool` 专职线程队列） | `workbench/services/browser_pool.py:146` |
| 数据 | SQLite（单文件、单进程持有、WAL + 外键、`RLock` 串行）；**迁移 1–25** | `workbench/db.py:27`、`docs/SCHEMA.md` |
| 凭据 | 口令 Fernet 加密（密钥存 `settings.account_fernet_key`） | `workbench/services/accounts.py:50` |
| 模型 | DeepSeek（OpenAI 兼容协议，流式 + function calling + 前缀缓存） | `workbench/services/deepseek.py` |
| 图分析 | networkx（蜘蛛五步织网的关系网络 + DAG 判定） | `workbench/services/egmp/insight/spider.py:586` |
| 附件解析 | `python-docx` / `openpyxl` / `pypdf`（**纯 Python 轮子**，打包只需同步 hiddenimports） | `pyproject.toml:21-23` |
| 测试 | pytest（`-m 'not slow'` 为默认）；另有一套**手动跑的端到端校验脚本** | `pyproject.toml:50-59`、`tools/verify_*.py` |
| 打包 | PyInstaller（`server.spec`）+ electron-builder + NSIS | `workbench/server.spec`、`tools/build.ps1` |

**规模实测（2026-10-07，物理行数，Python `splitlines()`）**：

| 区域 | 文件 | 行 |
|---|---|---|
| `workbench/**/*.py` | 92 | 23,537 |
| 　├ `harness/`（循环引擎） | 11 | 2,731 |
| 　├ `api/`（路由） | 18 | 3,545 |
| 　├ `services/`（含 `egmp/` 全部子包） | 33 | 7,716 |
| 　├ `plugins/`（工具实现） | 5 | 1,480 |
| 　├ `sandbox/` + `skills/` | 13 | 2,982 |
| 　└ 顶层单文件（`plugin_api` 1,382 / `approvals` 770 / `db` 660 / `logging_setup` 477 / `cache` 397 …） | 12 | 5,083 |
| `workbench/static` js+css / html | 26 / 7 | 10,409 / 1,091 |
| `tests/**/*.py` | 66 | 18,268 |
| `tools/*.py` + `*.mjs` | 39 | 8,079 |
| `desktop/*.js` | 5 | 1,697 |
| `extensions/**/*.ts,js` | 25 | 5,965 |

> **口径提醒**：`Get-Content | Measure-Object -Line` 会漏掉空行，得到偏低的数字（本仓踩过）。
> 上表由 Python `splitlines()` 实测。`docs/CORE-ARCHITECTURE.md` §8 是 **2026-09-18/22** 的旧表，
> 与本表不一致是正常的（它的"测试那一行"自己就标注了"未重跑"）。

---

## 三、架构

### 3.1 八层结构（`docs/CORE-ARCHITECTURE.md` §2 是权威叙述）

```
┌ L8 传输层   routes_chat.py (SSE 对话)  ·  routes_agent.py (REST 工具面)
│             两条链路消费同一个事件契约与同一个中枢 —— 不存在两套工具表
├ L7 循环层   harness/loop.py：call → tool → observe → repeat
│             SafetyGate(步数预算) · UsageMeter · 事件流与传输解耦
├ L6 工具中枢 plugin_api.ToolHub：ToolSpec{…, risk, trust, reversibility}
│             _tools 是派生视图，只能由 _rebuild() 写
├ L5 确认门   approvals.py：单次令牌 · 5min · 参数指纹 · 唯一审计入口
├ L4 沙箱     sandbox/：Fail-Closed 策略 + 解释器白名单 + Windows Job Object 树杀
├ L3 技能     skills/：SKILL.md 契约（声明式 wraps / 脚本式 script）+ ZIP 准入
├ L2 上下文   cache.py + harness/context.py：append-only 前缀 + 冻结摘要
└ L1 模型     services/deepseek.py：SSE 流式 + 增量聚合 tool_calls + include_usage
```

**一条不可违反的依赖纪律**：`plugins → plugin_api`、`skills → sandbox`，**永不反向**。
`plugin_api.py` 里出现平台术语、import 插件、或 import `db` 都是错的（它连审计与确认门
都靠注入）。这条由 `tests/test_dependency_direction.py` 用 **AST** 守着（不是 grep），
并且自带一条"自反证"用例，证明这个门禁**还能红**。

### 3.2 进程与端口拓扑

| 进程 / 端口 | 角色 | 关键点 |
|---|---|---|
| Electron 主进程（`18767` 控制口） | **仲裁者**：决定端口、拉起 sidecar、开窗口、托盘、热键 | 固定端口**无回落**（ADR-0012），单实例判定在壳侧 |
| Python sidecar（`18765`） | 业务与 Agent 全部在这里 | 由壳 spawn；开发态直接 spawn `uvicorn`（**不经过任何入口脚本**） |
| Chromium CDP（`18766`） | 托管浏览器（Playwright） | 单工作线程队列 |
| Chrome MV3 扩展 | **执行面**（用户自己的 Chrome） | 数据面在桌面端；`/extension/*` 由一次性令牌中间件守着 |

★ 因为开发态绕过了两个入口脚本，**启动横幅与父进程监视都放在 `api.lifespan`**
（`workbench/api/__init__.py:74-96`）——它是**所有启动路径的唯一交集**。
父进程非正常死亡时 sidecar 自退，靠的是**子进程等父句柄**（`workbench/lifecycle.py`），
不是 `before-quit`（强杀时那条链根本不跑，PITFALLS #54 实测复现过）。

---

## 四、关键模块

| 路径 | 职责 | 入口/要点 |
|---|---|---|
| `workbench/main.py` | 开发态入口（开浏览器） | 32 行，`uvicorn.run("workbench.api:app", log_config=None)` |
| `workbench/server_entry.py` | 冻结态入口（`--server --port`） | 唯一能在日志装配失败后仍留痕的地方（`DATA_DIR/server.log`） |
| `workbench/api/__init__.py` | 应用装配：路由 + 中间件 + lifespan | 3 个核心路由 + 12 个可选路由 + `routes_logs`（刻意不放进容错注册） |
| `workbench/api/routes_chat.py` | SSE 对话 + 停止 + 反馈 | 历史装配的**唯一扼流点** `_model_history` |
| `workbench/api/routes_agent.py` | 工具 REST 面：目录/调用/确认/审计/运行时间线 | `REQUIRED_TOOLS` 启动自检的事实源 |
| `workbench/harness/loop.py` | 回合引擎（1,277 行） | 三个终止出口：步数预算 / 取消 / 超时；事件全在这里产生 |
| `workbench/harness/context.py` | 纯 token 估算 + 按真实用量校准 | 消息量 ⊥ 请求量（后者含工具 schema，实测 ≈2,104 tok） |
| `workbench/cache.py` | 稳定前缀 + 冻结摘要 + 用量账 | `plan_prefix()` 的两条"不要改"写在 `cache.py:189-193` |
| `workbench/harness/transcript.py` | 工具轨迹落库 + **跨重启收尾** | `repair_dangling` 只补历史、**绝不重放**、不碰审批令牌 |
| `workbench/harness/trace.py` | `agent_run` / `agent_span` | 工具 span 只放指针（auditId/plugin/risk），不放参数 |
| `workbench/plugin_api.py` | 工具中枢（1,382 行） | `ToolSpec.valid()` 在**装配期**卡住缺 `trust`/缺 `reversibility` |
| `workbench/hub.py` | 宿主的装配器（ADR-0025 搬出来的） | **顺序承重**：`approvals.install()` 必须先于 `plugins.load_all()` |
| `workbench/approvals.py` | 风险门 + 模型发起的确认（两条通道，一张表） | 单次令牌；跨重启一律 `expired` |
| `workbench/sandbox/` | 不受信代码的**限损层**（不是安全边界） | 审计写在唯一扼流点 `run()`；解释器白名单**刻意不含** PowerShell/cmd |
| `workbench/skills/` | SKILL.md 能力包：装/卸/删 + ZIP 准入 | 脚本式 Skill 的风险下限**强制 ≥ write**、可逆性强制 `IRREVERSIBLE` |
| `workbench/services/egmp/` | 平台知识层 | `kernel/`（纯知识、无 I/O）· `client.py`（同步 httpx）· `insight/`（织网）· `monitor/`（录制解读） |
| `workbench/static/` | 原生 ES module 前端 | `index.html` 是唯一入口，4 个模块页是**整页跳转** |
| `desktop/` | Electron 壳 | 固定端口、`setWindowOpenHandler` 一律交系统浏览器 |
| `extensions/quick-login/` | MV3 扩展，六平面隔离 | 上游 fork **已于 2026-09-29 终止**，本仓自持 |
| `tools/` | 33 个手动端到端校验脚本 + 生成/守卫脚本 | 它们**会真的起服务/开浏览器**，因此有自足性契约 |

---

## 五、数据与控制流

### 5.1 一次对话回合（带工具的）

```
POST /api/chat/stream
 └ routes_chat._chat_events
    ├ 落 user 消息 + 装配历史（唯一扼流点 _model_history：丢 kind≠message / note→user / 展开附件）
    ├ SSE: start, attachment*
    └ run_agent_loop
       ├ transcript.repair_dangling   ← 上次进程死亡的悬空 tool_calls 在这里收尾（只补不跑）
       ├ _build_context → cache.plan_prefix → 超预算才 _summarize + freeze_summary
       └ while True
          ├ 检查点① 停止位 → cancelled           ├ 检查点② 步数预算 → error
          ├ 硬上限复检（每次模型调用前，不是每回合一次）
          ├ 模型流 → token*  →  end{tool_calls, usage}
          ├ 无 tool_calls → final → return
          └ 有 → thought / action / observation …（确认门在中间就地等答复）
 └ finally：释放停止令牌 + 落最终 assistant 行；SSE: done
```

**上下文装配契约**（不变量 I1–I3）：
`[system] + [冻结摘要?] + [原文历史] + [新消息]`，**已发出的前缀逐字节不改**。
system 被 `_pin_system` 摘出折叠域（I15），摘要边界 `summarized_upto` **只前进**。
违反它的后果**不报错**：DeepSeek 前缀缓存命中率归零，成本 ×50。

### 5.2 一次工具调用

```
loop → registry.execute(name, args, ctx)
  ├ 未知工具 → UNKNOWN_TOOL（唯一不写审计的分支）
  ├ pydantic 校验失败 → BAD_ARGS 观测（不抛）
  ├ 可选 precheck（只读，在门前；它只省一次白问，不承担拦截）
  ├ 风险门：risk ≠ READ 且门已装配 ⇒ 无令牌就 NEEDS_APPROVAL，handler 根本不会被调用
  ├ wait_for(handler(args, ctx), timeout)
  │    └ 同步阻塞一律 asyncio.to_thread（否则整个服务卡住，不是报错）
  ├ 异常 → ToolStatus 观测（I13：工具异常是观测，不是中断循环的异常）
  ├ 结果截断到 16,000 字符，且**显式告知模型**被截断
  └ _audit → agent_audit（args 列放的是"审计维度"，不是原始参数；redact 只是兜底）
```

### 5.3 状态与配置住在哪

| 层 | 例子 |
|---|---|
| 环境变量 | `WORKBENCH_DATA` / `WORKBENCH_DB` / `WORKBENCH_PORT` / `WORKBENCH_PARENT_PID` / `WORKBENCH_SESSION_MODE` |
| settings 键（DB） | `apiKey` / `model` / `temperature` / `maxSteps` / `account_fernet_key` / `extension_access_token` |
| 代码常量 | `config.SENSITIVE_PATH_PARTS`（**唯一一张敏感路径表**）· 附件上限 · 沙箱解释器白名单 |
| 数据目录文件 | `%APPDATA%\Workbench`：`workbench.db` · `runtime/desktop-ports.json` · `runtime/egmp-tokens/*.json` · `logs/workbench-<日期>.jsonl` · `sandbox/` · `skills/{library,installed}` |
| 前端常量 | `desktop/ports.js:28`（三端口）· 编译进扩展的桌面地址 · UI 文案 |

**跨进程只走文件**（数据库只有一个进程持有）：端口文件由壳写、服务端只读。

---

## 六、入口点

| 场景 | 命令 / 文件 |
|---|---|
| 开发态（自动开浏览器） | `python -m workbench.main` → `main.py:16` |
| 冻结态（壳调用） | `WorkbenchServer.exe --server --port N` → `server_entry.py:51` |
| 壳的开发态回退 | `.venv\Scripts\python.exe -m uvicorn workbench.api:app`（**绕过上面两个入口**） |
| 前端 | `workbench/static/index.html` → `/static/js/app.js` |
| 桌面应用（开发态） | `cd desktop; npm start` |
| CI | `.github/workflows/ci.yml`（windows-latest / py3.12 / node 20，9 步） |

★ 新路由挂到 `workbench/api/__init__.py::_optional_routers` 的 12 项表里，或同级显式注册。

---

## 七、构建、运行与测试

### 7.1 命令（来自 `AGENTS.md` §3、`CONTRIBUTING.md` §8，本机实测）

```powershell
# 基线（动手前 + 改完后各一次）
.\.venv\Scripts\python.exe -m pytest -o addopts=""        # 全量，含 slow 层
.\.venv\Scripts\ruff.exe check .                          # ★ 先探一次：ruff 在不在 venv 里会漂
.\.venv\Scripts\python.exe tools\gen_tools_doc.py         # 文档清单/数字 == 运行期中枢
.\.venv\Scripts\python.exe tools\verify_packaging.py      # 打包静态核对 13 项

# 日常（快）
.\.venv\Scripts\python.exe -m pytest                      # 默认 addopts 跳过 slow
.\.venv\Scripts\python.exe -m workbench.main              # 只看页面，零编译

# 扩展（改了 TS 才需要）
cd extensions\quick-login; npm run typecheck; npm run build

# 打包
powershell -File tools\build.ps1                          # → NSIS 安装包
```

### 7.2 本次实测结果（2026-10-07，`main@ce3bf3d` + 工作区改动）

| 门禁 | 结果 |
|---|---|
| `pytest -o addopts=""`（全量，含 slow） | **1011 passed, 1 skipped**，70.4 s |
| `ruff check .` | **All checks passed** |
| `python tools\gen_tools_doc.py` | **PASS**（4 插件 / 18 工具 = core；installed 0） |
| `python tools\verify_packaging.py` | **PACKAGING_CHECKS: 13/13** |

> 工作区里那 4 个未提交改动（见第九节）**已被这次全量跑覆盖**（含新增的
> `tests/test_doc_encoding.py` 7 条，全绿）。**没有跑**任何需要真实浏览器/真实 Key 的
> `tools/verify_*.py`，也没有打包。

### 7.3 两套验证的分工（这是本仓的一个结构性特征）

| | pytest（自动、进 CI） | `tools/verify_*.py`（手动、**不进 CI**） |
|---|---|---|
| 数量 | 60 个 `test_*.py`，约 786 个用例函数 | 21 个会起服务/开浏览器的程序 |
| 跑什么 | 逻辑、契约、AST 门禁、反证 | **真起生产入口 + 真发 HTTP + 真开 chromium**，读**计算样式与几何** |
| 为什么需要第二套 | —— | "源码里有这些字样"推不出"渲染出来是对的"（PITFALLS #87 / 作者曾凭印象编出**不存在的 CSS 类名**，徽章退化成灰底灰字而界面上"看不出少了什么"） |
| 纪律 | 改哪跑哪（`AGENTS.md` §3 的矩阵） | **三条自足性契约**：数据隔离 / 端口被占就拒跑 / 端口唯一（`tests/test_verify_scripts_are_self_contained.py`） |

再往上一层还有两件不常见的东西：

- **反证清单**（`tools/verify_regression_tests.py`，27 条）：逐条把源码改成缺陷版，
  断言判据**真的会红**，结束时逐字节还原。它自己会烂掉，所以另有一条**可进 CI 的守卫**
  （`tests/test_regression_harness.py`）检查锚点是否还在、是否唯一。
- **不变量覆盖率**（`tests/test_invariant_coverage.py`）：I1–I18 每条都必须挂到一个反证标签上，
  `PENDING` 是**空的**且**不允许再变大**。

### 7.4 打包与发版

版本唯一真源是 `pyproject.toml` 的 `version`，`tools/bump.py` 写 6 个载体（含 `.version.json`、
`desktop/package.json`、扩展 manifest），`bump.py check` 只读校验。今天三者都是 `0.4.1`，同步。
`workbench/server.spec` 的 `hiddenimports` 必须显式列出每个插件模块——**动态导入 PyInstaller
看不见**，漏了就是"装完静默少功能"。`tools/build.ps1` 必须有 UTF-8 BOM（P5 守着，PITFALLS #37）。

---

## 八、约定（新协作者必须模仿的）

1. **工具只有一个定义处**：`workbench/plugins/*.py` 的 `ToolSpec`，且 `risk` / `trust` /
   `reversibility` **必须显式声明**（后两个**没有缺省值**，缺了在装配期报错）。
   新增一个工具必须同步 **5 处**（插件文件 · `plugins/__init__.py::_MODULES` ·
   `server.spec` hiddenimports · `routes_agent.py::REQUIRED_TOOLS` · 跑 `gen_tools_doc.py --write`）。
2. **同步阻塞一律 `asyncio.to_thread`**。生产代码里已有的正确写法见 `plugins/insight.py:113`
   的 `_in_thread`。另有一条**隐性承重**：`routes_browser.py` / `routes_monitor.py` /
   `routes_accounts.py` 的 handler 是**普通 `def`**（FastAPI 丢线程池），
   **把它们改成 `async def` 而不加 `to_thread`，会让整个事件循环卡在 `future.result()` 上**。
3. **模块文档串写"职责 + 知识来源"**（fork 自谁 / 迁移自谁 / 实证出处）——本仓最重要的可读性投资。
4. **异常分层**：服务层抛领域错误，路由层翻成 `HTTPException` 并 `raise ... from exc` 保留因果。
   但**工具 handler 的异常必须转成观测**，不能抛出去。
5. **DB 只走迁移，只增不改**；历史迁移（尤其 1–6）不可变。
6. **字段名双语义**：DB 行 `snake_case`、导出/快照 `camelCase`。读错键的后果是
   **扩展静默丢弃全部账号**（PITFALLS #14）。
7. **文档分层**：ADR 记不再变的决策（只增不改）；`API.md` / `CONFIG.md` / `SCHEMA.md`
   记随代码走的事实（**改必更**）；`PITFALLS.md` 记真机环境坑（只增不改）。
8. **交付前最后一步是「杀干净 + 重启」，不是「跑完测试」**（规则 38）。
9. **写完断言先问「拆掉什么它才会红」**——答不上来就说明它没在验任何东西（PITFALLS #112）。
10. **提交信息**：`阶段/模块: 动作`。

---

## 九、风险与粗糙边缘

> 分四档。**第一档是我今天亲自复核过的**（给出复核方式），第二档是文档与代码对不上，
> 第三档是死代码与覆盖空洞，第四档是**已知且刻意**的取舍——**不是缺陷，写在这里是为了
> 免得下一次评审重新争论一遍**。

### 9.1 已核实的功能缺陷

**(1) ★ 停止按钮是坏的（用户可见，且没有任何判据覆盖它）**

`workbench/static/js/api.js:112-113`：

```js
stopChat: (conversationId) =>
  jsonFetch("/api/chat/stop", { method: "POST", body: { conversationId } }),
```

`jsonFetch`（`api.js:3-7`）只加 `Content-Type`，**从不 `JSON.stringify`**，而 `fetch` 遇到
普通对象 body 会调 `String(obj)` ⇒ 实发 `"[object Object]"`；服务端 `StopBody`
（`routes_chat.py:121-127`）要求 `conversationId` ⇒ **422**。`chat.js:491-496` 把这个异常
吞掉并把按钮**退回"停止"态**，于是**回合继续跑，界面看起来什么也没发生**。

- **复核方式**：同文件另外三处（`api.js:33` / `:35` / `:41`）都写了 `JSON.stringify`，
  `modules-common.js:7-8` 也写了——**只有这一处漏了**，所以这是一处单点遗漏，不是全局约定。
  另：全仓 `stopChat` 只有 `api.js:112` 定义与 `chat.js:484` 调用两处，
  `tools/verify_composer_ui.py` 里**零**条 stop 相关断言。
- **反差**：服务端这一侧做得很好——`tests/test_agent_stop.py` 20 条、计划 §5.6 的四条判据全绿。
  **"函数对"与"链路通"是两件事**，这正是本仓反复记录的那一类缺陷。

**(2) 对象分析任务可能永远停在 `running`**

`routes_insight.py:59` 直接 `jobs.start_job()`（插入 `status='running'`），而把状态改成终态的
`jobs.finish_job()` 只在这个 SSE 生成器的**末尾**被调用（`routes_insight.py:80`）。
SSE 客户端中途断开 ⇒ 生成器被关闭 ⇒ `finish_job` 不执行 ⇒ 那一行**永远是 `running`**。
对照组：页面与工具的同步路径走 `jobs.run_sync()`（`jobs.py:224-239`），**它是有 `try/finally` 语义的**。

**(3) `_optional_routers` 的注释承诺了它没有实现的容错**

`workbench/api/__init__.py:35` 写着「单个路由装配失败不拖垮整个应用」，但 `:36-71` 的
12 项 import 与注册**没有任何 try/except** ⇒ 任何一个路由模块 import 失败都会**让应用起不来**。
（复核方式：直接读 `api/__init__.py:34-71`。）

**(4) 审批门在"没装配"时是 fail-open**

`plugin_api.py:1313`：`if spec.risk is not Risk.READ and self._approval_gate is not None:` ——
生产路径由 `hub.get_hub()` 保证先 `approvals.install()`（`hub.py:65-72`，**顺序承重**），
所以**今天不是漏洞**；但一个裸 `ToolHub()` 会**不经过门就执行 WRITE 工具**，
这与沙箱"未知策略一律拒"的姿态**正好相反**。

**(5) 平台 JWT 明文落盘**

`services/egmp/client.py:117-130` 的 `save_cached_token` 把 JWT 与 `baseUrl`/`username`
以**明文 JSON** 原子写进 `runtime/egmp-tokens/`。它只被"敏感路径表 + 文件权限"保护，
**没有走 Fernet**（口令走了）。`CONFIG.md` / `SENSITIVE_PATH_PARTS` 是把它当敏感文件处理的，
所以这是一处**口径不齐**，不是"忘了"。

**(6) 引用行号已经漂了**

`AGENTS.md` 规则 1 与 `routes_chat.py:175` 都写「`cache.py:143` 有明确警告（前缀滑动 ⇒ 命中率恒为 0）」。
**实测 `cache.py:143` 是 `PrefixPlan.truncated` 的字段文档串**；那条警告在 **`cache.py:191`**。
本仓大量使用 `path:line` 引用作为"判据"，**行号漂移会让判据指到无关代码上**，
而门禁只检查文档**链接**存活，不检查行号。

### 9.2 文档与代码不一致（已核实）

| 位置 | 写着 | 实际 |
|---|---|---|
| `docs/SCHEMA.md:47` 及迁移表 | 「迁移账（**1–24**，全部已应用）」 | `db.py` 有 **25** 条（第 25 条 `transcript_columns`，随 `8d14d79` 进入） |
| `README.md:110` | 「DB 迁移 **1–24**」 | 同上 |
| `docs/CORE-ARCHITECTURE.md` §8 | 「数据库迁移 **1–24**」 | 同上 |
| `README.md:111` | 「**16 篇**架构决策记录」 | `docs/adr/` 有 **22** 篇 ADR（+ 模板 = 23 个文件） |
| `CONTRIBUTING.md:22 / :114 / :299 / :392` | 「**382** 项」/「**501** 项」/「**382** 项」/「**596** passed」 | 同一份文件里的四个互相矛盾的旧数字；今天实测 **1011 passed / 1 skipped** |
| `CONTRIBUTING.md:185/186/285/303` | 「verify_packaging **9/9 PASS**」 | 今天 **13/13**（该脚本自己已经长到 13 项） |
| `CONTRIBUTING.md:300` | 「PASS：**5 插件 / 20 工具**」 | 今天 **4 插件 / 18 工具** |
| `CHANGELOG.md` 的 `[Unreleased]` | 仍含**已删除能力**（配置工厂 / `factory_*` / ADR-0017/0018）的条目 | `tools/publish_release.py` 会把它**逐字**当发布说明抽出去，且**不报错**（文件里自己写着这条警告） |
| 该词的陈旧标注 | 「`docs/CODEBASE_OVERVIEW.md`（该文件已于 2026-09-29 删除）」 | ✅ **2026-10-07 已全部同步**。★ **本行不再写具体处数** —— 它已经错过两次：最初写「10 处」、后来改成「24 处」，两次都**低估**（实测含该词的行共 **26 行 / 12 个文件**：`docs/adr/` 之外 25 行 + 目录内 1 行）。⇒ **一个每修一次标注就会漂的数字不值得维护**：要复核就自己跑全仓 grep；本行只记录口径与结论。其中 `CHANGELOG.md` 的 2 行是**历史记录、刻意不改** |

> **为什么会漂**：`tools/gen_tools_doc.py` 只对 `README.md` / `docs/TOOLS.md` / `docs/API.md`
> 三份核"N 个工具"，`tests/test_agents_md.py` 只核 `AGENTS.md` 自己声明的坑数与 ADR 数。
> **迁移条数、ADR 篇数（在 README 里）、测试项数（在 CONTRIBUTING 里）没有任何守卫。**
> 这与"文档里的数字不能靠人记"是同一条教训，只是**守卫只覆盖了一部分载体**。

### 9.3 死代码、覆盖空洞与静默失败

**覆盖空洞（重要）**

- `workbench/api/routes_monitor.py`（`/api/monitor/start|stop|status`）在 `tests/` 与 `tools/`
  里**零引用**——整个蓝图没有任何判据（复核：`Select-String -Path tests\*.py,tools\*.py -Pattern '/api/monitor'` → 0 命中）。
- **`/api/attachments` 的 HTTP 层没有 pytest 覆盖**：`tests/test_attachments.py` 只 import 了
  `services.attachments`，只有手动的 `tools/verify_attachments.py` 走真 multipart。
- **Agent 循环没有 HTTP 级端到端**：所有 `test_agent_*` 都用进程内脚本化客户端；
  `verify_composer_ui.py:320` 是**拦截** `/api/chat/stream` 而不是打它；
  唯一真 POST 它的是 `verify_attachments_live.py:105`（要真 Key、花钱），且断言的是附件行为。
- 验证脚本的守卫有个洞：`_starts_server()` 要求文件里同时出现字面量 `uvicorn` **和** `--port`，
  于是用生产入口启动的 `verify_attachments.py` / `verify_composer_ui.py` / `verify_confirmation.py`
  **逃过全部三条规则**（它们恰好是合规的，但没有东西强制它）。

**死代码 / 零调用方**

- `harness/context.py:181` 的 `build_context()` **零调用方**，而它实现的是**修复前**的行为
  （不算工具 schema、不算校准系数、不冻结摘要）——一个"看起来是公开 API 的陷阱"。
- `sandbox/runner.py:358` 的 `run_async`、`ToolHub.load()`、`ToolHub.precheck`、
  `ToolSpec.summary_fn`、`sandbox` 的 `mcp-stdio` / `strict-readonly` 策略：
  都没有产品内调用方（前两个由测试守着）。
- `services/proc.py`（Node 子进程封装）、`services/egmp/queries.py`、`services/egmp/cache.py`：
  无生产 importer（`proc.py` 只有 `tests/test_proc.py`）。
- `MAX_RESULT_CHARS = 16_000` 在 `plugin_api.py:80` 与 `skills/script_runner.py:60` **各定义一次**。
- `blueprint_jobs` / `job_logs` 两张表**没有写入方**（配置工厂移除后如此；`job_logs` 的零写入是**刻意**的）。
- `contract.build_context` 一类"看着被用、其实没人用"的函数，是本仓最容易骗到新人的东西。

**静默失败点（有意的，但要知道）**

- `_skills_setup` / `_sandbox_setup` / `_tool_selfcheck` **吞掉全部异常**
  （`api/__init__.py:130, :146, :177`）——"沙箱没装好"只有一行 warning。
- `plugin_api._extra` 用裸 `pass` 吞掉 `audit_extra` 的异常 ⇒ **审计维度可以无声消失**。
- `approvals._has_columns` 任何 DB 错误都返回 `False` ⇒ `plugin/risk/meta` 三列静默不写。
- `browser_pool._control_post` 把所有 `OSError`/`ValueError` 折成 `None` ⇒ 调用方只会报
  "壳没在跑"，真因丢失。
- **全仓 `workbench/` 里 `TODO`/`FIXME`/`XXX`/`HACK` 零命中**——技术债写在文档串的散文里，
  用文本搜索**永远搜不到**。

**大到该拆的文件**：`plugin_api.py` 1,382 · `harness/loop.py` 1,113 ·
`static/js/accounts.js` 1,065 · `static/css/chat-tools.css` 1,185 ·
`services/egmp/insight/spider.py` 926 · `api/routes_chat.py` 505（SSE + 历史装配 + 附件展开 + 回合记账 + 停止注册）。

### 9.4 已知且刻意的取舍（**别当成缺陷去"修"**）

| 项 | 为什么这样 |
|---|---|
| `/api/*` 至今无认证（R1b） | 桌面 UI 就是本服务加载的页面；加令牌要么把令牌放进页面（等于没加），要么改引导流程。ADR-0015 §D-7 记录为**有意保留** |
| 沙箱**不是安全边界**，是限损层 | ADR-0014 §D-3。跑起来的代码仍能读它有权读的一切；`allow_network` 是**声明**不是强制 |
| 待确认操作跨重启**不恢复** | 用户重启了应用，凭什么替他记住一个未确认的写操作 |
| 并发只对**逐个举证**的工具开放，缺省 `False` | ADR-0023：**"只读"不足以支撑并发**（HTTP 会话 / 单线程浏览器队列 / sqlite 连接都是"只读但会碰"）。今天只有 `get_current_time` 标了 |
| 硬上限**只算消息、不含工具 schema** | 宁可漏判也不要误判；**改它需要新增 ADR** |
| OTel 的 `gen_ai.*` 属性名**全部标着 `verify`** | 写映射时网络不可达，只核实了语义方向没核实拼写；`tests/test_agent_spans.py` 有一条断言**不许**把 `verify` 改成 `ok` |
| 离线评测夹具是**合成**的 | 它锁的是"请求形状 + 链路行为"，**不是**"模型选得对不对"。换真机 `--record` 即可，期望一行都不用改 |
| `adapters/*.json` 指向的原四仓库目录**冻结** | 只读存档 |
| `LICENSE`（Apache-2.0）与 `pyproject.toml`（MIT）**声明不一致** | **这是真不一致，不是刻意**——`CORE-ARCHITECTURE.md` §7.9 已记录（2026-09-20 复核），至今未修，需要所有者定夺 |

### 9.5 工作区当前状态（未提交）

```
 M extensions/quick-login/src/content/auto-login.ts     (+60 / -7)
 M tools/verify_extension_isolation.py                  (+127 / -7)
?? docs/EXTENSION-INSTALL.md
?? tests/test_doc_encoding.py
```

四条**都不碰 `workbench/**` 的产品 Python**，且都已被今天那次全量 pytest 覆盖：

- `auto-login.ts`：把"按按钮文案判断"换成 `hasPasswordField()`（**填但不提交**），
  并给凭据认领加 `8 × 400ms` 重试——修的是"点了登录什么都没发生"（原来只在**抛异常**时重试，
  返回 `null` 时不重试）与 `submit.disabled` 的空引用（在 `setInterval` 里每 800ms 抛一次，
  既填不上也不报错）。**`npm run typecheck` / `build` 本次未跑。**
- `verify_extension_isolation.py`：新增两个 chromium 夹具（`submitlabel.html` / `nullfirst.html`）
  与 C1–C4 / D1–D4 判据。**CI 不跑它**（需要 chromium）。
- `docs/EXTENSION-INSTALL.md`：**恢复一份被误删的文档**——它一度被删，而
  `desktop/main.js` / `routes_extension.py` / `routes_shell.py` / `publish_release.py`
  四处仍指向它，其中三处是**给用户看的指引**。这条的纪律是：
  **改文档的存活状态时，`docs/**` 之外也要搜一遍。**
- `tests/test_doc_encoding.py`：字节级守护长文档的**换行与 BOM**——因为
  `Path.read_text()` 会静默归一化换行，一次"看起来无害"的批量替换能把 138 条 PITFALLS
  从 CRLF 改成 LF，**而所有门禁照绿**（它们读的是文本，不是字节）。

---

## 十、术语与下一步

| 想了解 | 去哪 |
|---|---|
| **核心 Agent 架构与不变量 I1–I18（地基）** | `docs/CORE-ARCHITECTURE.md` §5 |
| 有哪些工具、怎么加一个 | `docs/TOOLS.md` |
| HTTP 端点契约 / SSE 事件序列 | `docs/API.md`（§12 是四类特殊通道专章） |
| 配置项（五层） | `docs/CONFIG.md` |
| 数据库迁移与表结构 | `docs/SCHEMA.md` |
| 为什么这么设计（22 篇，只增不改） | `docs/adr/` |
| **真机环境坑（120 条，只增不改）** | `docs/PITFALLS.md` |
| 使用手册 | `docs/USER-MANUAL.md` |
| 版本与发版 | `docs/VERSIONING.md` |
| 人的协作规范 | `CONTRIBUTING.md`（★ 注意其数字已陈旧，见 §9.2） |
| **AI 协作者的规则入口（先读这个）** | `AGENTS.md` |

**建议的下一步阅读顺序（给接手维护的人）**：
`AGENTS.md` §0–§1 → `docs/CORE-ARCHITECTURE.md` §2 + §5 → `docs/TOOLS.md` §2 →
`workbench/harness/loop.py` 的 `run_agent_loop`（1,277 行里最该读的那 700 行）→
`workbench/plugin_api.py` 的 `ToolSpec.valid()` 与 `ToolHub.execute()`。

**如果要动手，先做这三件（按性价比排序）**：

1. **修停止按钮**（`api.js:113` 补 `JSON.stringify`），并给它一条判据——
   今天它坏着而 1011 条测试全绿，这本身就是"判据没覆盖链路"的样本（§9.1(1)）。
2. **同步 §9.2 那七处数字**（迁移 25 / ADR 22 篇 / CONTRIBUTING 的测试项数与打包项数），
   并考虑把"迁移条数"也接进 `gen_tools_doc.py` 那类机检——数字靠人记必然会漂，本仓已经有结论了。
3. **给 `/api/monitor` 与 `/api/attachments` 的 HTTP 层补最小判据**（§9.3）。

---

_本文件由 `learn-codebase` 技能生成；所有结论均可按文中的 `path:line` 复核。
「已知且刻意」一节的判断来自 ADR 与代码注释，**不是**本次分析者的偏好。_
