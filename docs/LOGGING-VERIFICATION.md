# 日志系统完善性验证报告

_2026-09-20。对象：[LOGGING-DESIGN.md](LOGGING-DESIGN.md) 所述系统的**实际落地形态**。_

> **这份报告回答**：日志系统做完了吗？**它真的在工作吗**？边界在哪？
> 每一条结论都有**可复现的命令**，不是"我认为它能用"。

---

## 0. 结论速览

| 维度 | 结果 |
|---|---|
| **设计分期** | P0 / P1 / P2 **全部落地**（P3 的 OTLP 导出按设计**不做**，见 §5） |
| **组件测试** | `pytest tests/test_logging.py` → **32 passed**（落盘/幂等/脱敏/关联 ID/滚动/检索/失败隔离） |
| **端到端检查** | `python tools/verify_logging.py` → **36/36 PASS**（真起服务 + 真发请求 + 真读日志文件；含**两种启动路径**） |
| **反证** | `python tools/verify_regression_tests.py` → **6/6 有效**（每条断言都实测对缺陷版 FAIL） |
| **全量门禁** | `pytest -o addopts=""` → **596 passed**；ruff 全绿；`verify_packaging` 13/13；`gen_tools_doc` PASS |
| **改造前的对照** | 38 处 `logger.*` 调用、**0 个 handler**、**0 个日志文件** → 现在启动即落盘、可检索、可关联 |

**一句话**：从"**一个字节都留不下来**"变成"**36 项端到端检查全过**"，
且过程中端到端检查**抓到了组件测试抓不到的缺陷**（§3）——这恰好证明了它值得存在。

---

## 1. 端到端检查矩阵（`tools/verify_logging.py`）

脚本自起一个真的 sidecar（走真实入口 `workbench.server_entry`，专用端口 **18793**，
临时数据目录），发真请求，然后读**真落盘的文件**逐条断言。跑完自动清理，
**不影响**用户正在开的实例。

### A. 落盘与结构

| # | 检查 | 结果 |
|---|---|---|
| A1 | 日志文件存在且非空 | ✅ |
| A2 | 每一行都是合法 JSON（JSONL 完整性） | ✅ |
| A3 | 每条记录都有必填字段（`ts`/`level`/`logger`/`msg`/`pid`/`ver`） | ✅ |

### B. 启动链路的留痕（改造前**全是黑洞**）

| # | 检查 | 结果 |
|---|---|---|
| B1 | 启动横幅（版本 + pid + frozen + 端口 + 数据目录 + 日志路径） | ✅ |
| B2 | 路由注册**逐条**留痕（12 条） | ✅ |
| B3 | 工具中枢自检（插件数 / 工具数） | ✅ |
| B4 | 应用就绪（lifespan 装配完成） | ✅ |
| B5 | 沙箱与 Skill 装配留痕 | ✅ |

> B3 是改造前**最讽刺**的一条：`_tool_selfcheck()` 的注释写着"**必须把结果喊出来**：
> 少了哪个插件一眼可见"，而它的 `logger.error` 喊不出去——PITFALLS #32
> （打包漏收插件）唯一的启动期报警在打包态 100% 听不到。现在它落到文件里了。

### C. L1 请求日志与 `traceId` 贯通

| # | 检查 | 结果 |
|---|---|---|
| C1 | 存在 `kind=http` 的请求日志 | ✅ |
| C2 | 响应头带 `X-Trace-Id` | ✅ |
| C3 | 请求行含 `method`/`path`/`status`/`durationMs` | ✅ |
| C4 | 请求行的 `traceId` 与响应头**一致** | ✅ |
| C5 | `/api/logs/trace/{id}` 能取回同一 trace 的时间线 | ✅ |
| C6 | 时间线按时间**正序**（旧→新） | ✅ |

### D. 工具调用与审计的对接（**这本系统存在的核心理由**）

| # | 检查 | 结果 |
|---|---|---|
| D1 | 工具调用有日志（`kind=tool`） | ✅ |
| D2 | 工具日志含 `auditId`/`status`/`plugin`/`risk`/`durationMs` | ✅ |
| D3 | **日志里的 `auditId` 能在 `agent_audit` 表里查到同一行** | ✅ |
| D4 | 未知工具被单独记下（HTTP 路径在路由层短路，故由 `routes_agent` 记） | ✅ |
| D5 | 危险工具被确认门拦下并留痕 | ✅ |
| D6 | 被拦后的工具日志状态是 `needs_approval` | ✅ |

> D3 是"四层焊在一起"的判据：拿到一个 `auditId`，既能在 DB 查结构化行，
> 也能在日志里看它前后的叙述。

### E. 检索面与自指噪声

| # | 检查 | 结果 |
|---|---|---|
| E1 | `/api/logs/days` 列出当天 | ✅ |
| E2 | 级别过滤生效（ERROR 里没有 INFO） | ✅ |
| E3 | 关键字过滤生效 | ✅ |
| E4 | `/api/logs/*` **自身不产生** http 日志（无自指噪声） | ✅ |
| E5 | `/api/logs/download` 导出的是**可解析的** JSONL | ✅ |

### F. 脱敏与失败隔离

| # | 检查 | 结果 |
|---|---|---|
| F1 | 落盘内容里没有 `sk-` 形状的 API Key | ✅ |
| F2 | 落盘内容里没有 `password=` 原文 | ✅ |
| F3 | 进程存活（日志写入没有把服务搞崩） | ✅ |
| F4 | stdout/stderr 里没有「日志装配失败」 | ✅ |

### G. 滚动配置

| # | 检查 | 结果 |
|---|---|---|
| G1 | 日志目录下只有本系统的文件 | ✅ |
| G2 | 未到阈值时**不产生**多余归档（正常一天一个文件） | ✅ |

### H. 开发态启动路径（桌面壳 `npm start` 的 Python 侧）

**这一组是补做的**：起服时发现**桌面壳的开发态是直接 spawn
`python -m uvicorn workbench.api:app`**（`desktop/main.js::spawnServer` 的开发态回退分支），
**不经过任何入口脚本** —— 于是"启动横幅"如果只在 `main.py` / `server_entry.py` 里记，
**日常开发测试时日志就是残缺的**。修法是把横幅移到 `api.lifespan`（所有启动路径的唯一交集）。

| # | 检查 | 结果 |
|---|---|---|
| H1 | 开发态（裸 uvicorn）起服并就绪 | ✅ |
| H2 | **开发态也记启动横幅**（版本/pid/frozen/端口/数据目录/日志路径） | ✅（**修复前必然 FAIL**） |
| H3 | 开发态记「应用就绪」 | ✅ |
| H4 | 工具自检**只记一次**（横幅与自检不重复报同一件事） | ✅ |

---

## 2. 组件测试（`tests/test_logging.py`，32 项）

按关注点分组，**每条都是在守一个具体的、曾经不存在的性质**：

| 组 | 覆盖 |
|---|---|
| 落盘（核心） | `info` 真的落盘（改造前 19 处 info/debug 被静默丢弃）；级别生效；`configure()` 幂等（不叠加 handler）；**不抢外部 handler**（`caplog` 还活着） |
| 脱敏 | 8 种凭据形态逐一断言不落盘；**JSON 形态**（`"token": "…"`，初版正则漏掉的就是它）；打码后**仍是合法 JSON**；消息含裸 `%` 不炸；脱敏自身失败时**丢弃原文**而不是放行 |
| 关联 ID | 4 个 ID 进结构化字段；`bind` 退出恢复 + 可嵌套；拼错字段名**报错**而不是静默忽略 |
| 滚动 | 体积触发归档；**同日两次滚动不互相覆盖**；归档数量**有上限**（`extMatch` 同步） |
| 失败隔离 | 装配失败返回 `None` 而不抛；handler 抛异常不影响调用方；**handler 被外部关掉后 `configure()` 会重建** |
| 检索 | `tail` 的 level/contains/trace/kind 四个维度；按天列举 |
| 工具中枢集成 | 工具日志带 `auditId` 且**与审计表对得上**；未知工具（两条链路）；HTTP 版未知工具 |
| API 面 | 四个端点可用；`X-Trace-Id` 往返；自指噪声被排除；错误码 |

---

## 3. 端到端检查**抓到了**什么（这是它存在的证据）

跑第一次时是 **29/32**，三条红的。**四套门禁（596 单测 / ruff / 打包 13 项 / 工具清单）此前全绿。**

| 现象 | 归因 |
|---|---|
| `D4` 未知工具没有留痕 | **真缺陷**：`routes_agent.invoke` 在**路由层就 404 短路了**，所以 `ToolHub.execute()` 里那条告警根本不执行 → 两条链路对同一事件的口径不一致（对话链路有，HTTP 链路只有一条 404 访问日志）。**已修**：路由层也记一条，并补了单测 |
| `D5`/`D6` 危险工具没有 `needs_approval` | **我的检查场景错了**：参数不全 → 先撞 `bad_args`（校验在确认门**之前**）。改传合法参数即通过。<br>（当时探针用的是 `factory_create`——产品里唯一的 dangerous 工具；它随配置工厂移除后，探针换成 write 级的 `accounts_browser_close`，`tools/verify_logging.py` 已同步。**教训本身与用哪个工具无关**） |

而"修 D4"时又暴露了第二个真缺陷：我引用了一个 `routes_agent.py` **根本不存在的 `logger`**
→ `NameError` → **HTTP 500**，而 596 个单测全绿（没有任何用例走到那条分支）。
→ 已记为 **PITFALLS #53**（#41 的复发，形态不同）。

> **这一节是本报告最重要的内容**：它示范了"组件级测过了 ≠ 链路用上了"（PITFALLS #42）。
> 如果只有单测，这两个缺陷会以"**日志里少一段**"的形式潜伏到下一次排障时才发现——
> 而那时你正因为"没日志"而查不下去。

---

## 4. 与设计稿的差异（as-built）

| 设计稿写的 | 实际实现 | 原因 |
|---|---|---|
| `QueueHandler` + `QueueListener`：日志 I/O 移出请求线程 | **改为同步写** | 队列满时 `QueueHandler` 会**丢弃记录**——那是本仓最忌讳的静默失败（与 PITFALLS #42 同族）。本应用单机单人、38 个调用点、单条几百字节，同步写的代价可忽略。**要改异步，先补"队列溢出必须可见"的机制**（已写进模块文档串） |
| `server.log` 退役 | **保留**，定位改为"启动面包屑" | `workbench/static/blank.html` 的"服务端没有应答"页面**直接把它告诉了用户**；而且它用裸 `open()` 写，**在日志系统自身失效时仍然有效**（是唯一不被同一故障同时击穿的兜底）。已在 CONFIG.md §6 写清它与主日志的分工 |
| 未提 | **新增**：`_handler_usable()` 探测 | `logging.config.dictConfig()` 会 `_clearExistingHandlers()` **关闭所有既有 handler**，此后写入被我们吞掉 → **日志静默停止**而 `_configured` 仍为真。触发场景是"第三方按默认配置调 `uvicorn.run`"。已加探测 + 重建 + 回归断言 |
| 未提 | **新增**：`routes_agent` 的 HTTP 路径未知工具告警 | 见 §3 |
| 未提 | **新增**：启动横幅从入口脚本**移到 `api.lifespan`** | 入口脚本只覆盖两条路；**桌面壳的开发态直接 spawn `uvicorn`、不经过任何入口脚本** → 日常测试时日志里没有横幅。lifespan 是所有启动路径的唯一交集。判据是新增的 H2/H3 |
| P2 的 UI 页面（设置页 →「运行日志」） | **未做** | 三个 API 端点已就绪（`/days` `/tail` `/trace/{id}` `/download`），UI 是纯增量。**当前排障入口是 CLI + curl**（见 §6），够用 |

---

## 5. 已知边界（**没做**和**做不到**的，如实列出）

| 项 | 状态 | 说明 |
|---|---|---|
| **OTLP / 分布式追踪导出** | ❌ 不做 | 设计稿把它列为 P3。当前是"本机单进程 + 文件"，导出到外部后端在单机工具上收益不明；字段结构已按便于映射设计 |
| **日志 UI 页面** | ❌ 未做 | 见 §4。API 就绪，属增量 |
| **异步写（QueueHandler）** | ❌ 有意不做 | 见 §4 的理由 |
| **多进程共享同一日志文件** | ❌ 不支持 | 单进程设计。若将来需要，按 pid 分文件 |
| **`sidecar-stdio-*.log` 的端到端验证** | ⚠️ **未覆盖** | 它由**壳**（`desktop/main.js::teeServerOutput`）写，本脚本不起 Electron。`node --check` 过了语法，逻辑未实测。**这是本报告最大的验证缺口**，真机验收时要专门看一眼该文件 |
| **打包态（PyInstaller）下的日志** | ⚠️ 未实测 | 逻辑上 `DATA_DIR` 在打包态同样可写、`config.FROZEN` 只影响默认格式（JSONL）。**需在真实安装包上确认一次** |
| **日志文件被外部删除/改名后** | ⚠️ 未处理 | handler 持有已打开的文件句柄，删除后继续写会写到"不存在的文件"（Windows 上表现为写入失败→被 `handle()` 吞掉）。**未加检测**——留作已知毛边 |
| **`/api/logs/*` 的鉴权** | ⚠️ 沿用现状 | 与 `/api/*` 同级（仅本机回环，ADR-0015 §D-7 的既有取舍 R1b）。日志可能含用户数据（路径、对象编码），**新增端点没有扩大这个面，但也没有缩小它** |

---

## 6. 日常怎么用（排障速查）

```powershell
# ① 看最近发生了什么（人类可读）
.\.venv\Scripts\python.exe -c "import sys;sys.path.insert(0,'.');from workbench import logging_setup as L;[print(r.get('ts'),r.get('level'),r.get('msg')) for r in reversed(L.tail(limit=40))]"

# ② 只看错误
curl.exe -s "http://127.0.0.1:18765/api/logs/tail?level=ERROR&n=50"

# ③ 用户报故障时：让他给出界面响应头里的 X-Trace-Id，然后
curl.exe -s "http://127.0.0.1:18765/api/logs/trace/<traceId>"

# ④ 拿到一个工具调用的 auditId 之后：
#    日志 → 看它前后的叙述；DB → 看结构化行
curl.exe -s "http://127.0.0.1:18765/api/agent/audit/<auditId>"

# ⑤ 直接看文件（JSONL，一行一个对象）
Get-Content "$env:APPDATA\Workbench\logs\workbench-$(Get-Date -Format yyyyMMdd).jsonl" -Tail 50
```

**目录**：`%APPDATA%\Workbench\logs\`
- `workbench-<日期>.jsonl` —— 主日志（按天 + 按体积双滚动，保留 14 份）
- `sidecar-stdio-<日期>.log` —— 壳抓的进程原始输出（**Python 日志装配之前**的失败只在这里）
- `%APPDATA%\Workbench\server.log` —— 启动面包屑（一行）

---

## 7. 复现全部验证

```powershell
.\.venv\Scripts\python.exe -m pytest -o addopts=""                  # 596 passed
.\.venv\Scripts\python.exe tools\verify_logging.py                  # 36/36 PASS
.\.venv\Scripts\python.exe tools\verify_regression_tests.py         # 6/6 有效（反证）
.\.venv\Scripts\ruff.exe check .                                    # All checks passed
.\.venv\Scripts\python.exe tools\verify_packaging.py                # 13/13
.\.venv\Scripts\python.exe tools\gen_tools_doc.py                   # PASS
```

> `verify_logging.py` 自起服务于 **18793/18794**（生产入口 + 开发态入口），用临时数据目录，
> 跑完清理——不需要先退出你正在用的工作台。`verify_regression_tests.py` 会临时改写生产源码，
> 结束时**逐字节核验还原**，失败则 `exit 2`。

**日常测试该用哪种模式（要不要编译）见 [CONTRIBUTING.md](../CONTRIBUTING.md) §8。**
