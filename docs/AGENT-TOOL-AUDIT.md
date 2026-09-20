# 对话 Agent 工具调用：实测与使用漏洞检查报告

_2026-09-20 实测。锚定 `15b847e`（main）。本文的每条结论都有**可复现的命令**或**实测数据**，
不是代码阅读推断。复现脚本在 `tmp/`（一次性探针，未入库）。_

> ## ⚠️ 状态更新（2026-09-20 当天）
>
> **两条 P0 都已在同日修复并带回归断言**（本文正文保留**实测原状**，那是缺陷的证据）：
>
> | # | 状态 | 修法与回归 |
> |---|---|---|
> | **A** 危险工具在对话链路里永远无法执行 | ✅ **已修** | `PendingStore.claim_approved()`：无令牌时按「工具 + 参数指纹」认领一条**已批准**的条目（**一次性**）。回归 `tests/test_agent_approval.py`（8 项），反证见 `tools/verify_regression_tests.py` 的 `P0-A` 项 |
> | **B** 只读工具能完整读出 cookie / JWT / Fernet 密钥 | ✅ **已修** | 敏感表收归 `config.SENSITIVE_PATH_PARTS` 唯一事实源 + `_guard_path` 的判定**与位置无关** + 工具描述改成实际边界。回归 `tests/test_sensitive_paths.py`，反证同上 `P0-B` 项 |
> | **E** 启动自检的错误日志无处可去 | ✅ **已修** | 见 [LOGGING-VERIFICATION.md](LOGGING-VERIFICATION.md)（日志系统落地，端到端 32/32 PASS） |
> | **L** `read_local_file` 描述与实现不符 | ✅ **已修** | 描述改为实边界（见 B） |
> | **C** 工具调用链路在真实环境从未跑过 | ⏳ **仍未跑** | 需要一次真机冒烟（§4 的五步）。**第 4 步现在不再必然失败**（A 已修） |
> | G / H / I / J / K | ⏳ 未动 | 见 §3 的级别与理由 |
>
> **验证方式**：`python tools\verify_logging.py`（32 项端到端）+ `python tools\verify_regression_tests.py`（6 项反证）。

---

## 0. 结论速览

| # | 结论 | 严重度 |
|---|---|---|
| **A** | **对话链路里，风险工具（write/dangerous）永远无法执行**。放行后重试仍被拦，且每次重试探都会**新建一条 pending**。受影响工具：`accounts_browser_open`、`accounts_browser_close`、`factory_create`（**全部 3 个写/危险工具**） | 🔴 **P0** → ✅ 已修 |
| **B** | **只读工具 `read_local_file` 能完整读出 cookies / 平台 JWT / 账号备份（含 Fernet 密钥）**，而它 `risk=read`（**不过确认门**）。根因是**仓内有两张敏感路径表**，只读工具用的是弱的那张 | 🔴 **P0** → ✅ 已修 |
| **C** | **工具调用链路在真实环境里从未被跑过**：`agent_audit` / `deepseek_usage` / `conversation_context` 全为 0 行，最后一条对话是 **2026-09-09**，而工具插件层 **2026-09-15** 才落地 | 🟡 事实（非缺陷，但决定了测试策略） |
| **D** | 只读工具（`read` 级）的链路**是通的**——静态检查与注入假客户端的端到端测试都通过；真实 API 也**可达**（`api.deepseek.com` 回 **401** = 网络通、只差鉴权头） | 🟢 好消息 |
| **E** | 启动自检的 `logger.error`（"必需工具缺失"）**无处可去**——与日志系统缺陷同源，见 [LOGGING-DESIGN.md](LOGGING-DESIGN.md) | 🟡 P1 → ✅ 已修 |
| **F** | 前端事件处理**完整**（start/token/thought/action/observation/approval_required/final/error/usage/done 十个全覆盖），无缺口 | 🟢 |

**一句话**：工具调用**能跑**，但**危险工具跑不通**、**只读工具能读走凭据**。
这两条都必须在开放给用户使用前修掉。——**两条已于同日修掉**，见顶部状态表。

---

## 1. 实测环境

| 项 | 值 |
|---|---|
| 代码 | `15b847e`（v0.3.12 + 3），工作区含本轮文档改动 |
| 解释器 | `.venv\Scripts\python.exe`（Python 3.12.7） |
| 测试基线 | `pytest -o addopts=""` → **530 passed**（工具调用逻辑由 `test_agent_loop.py` 用**假客户端**覆盖） |
| 真实数据目录 | `%APPDATA%\Workbench`（顶层**只有** `workbench.db`） |
| DeepSeek 配置 | `settings.apiKey` 存在（35 字符）、`settings.model` 存在（17 字符） |
| 网络 | `https://api.deepseek.com` → **HTTP 401**（可达；401 是因为没带鉴权头） |

> **为什么"从未跑过"这件事重要**：530 个测试全绿，但它们全部用 `FakeClient` 注入**预先写好的**
> 工具调用。真实链路多出来的东西——DeepSeek 的 `tool_calls` 流式分片、`finish_reason`、
> 真实参数形状、真实错误码——**一个都没被测过**。本报告的两条 P0 正是这种"假客户端测不到"的缺陷。

---

## 2. 现状：链路是完整的，但有两个断点

### 2.1 静态链路（逐段核对，**完整**）

```
用户输入 → POST /api/chat/stream
  → routes_chat._chat_events → run_agent_loop(history, client, default_registry(), cfg)
      → registry.list_api_for(None) → 全部工具的 function calling schema      ✅
      → deepseek.stream_chat(tools=...) → tool_calls 按 index 增量聚合      ✅
      → registry.execute(name, args, ToolContext(source="agent"))           ✅
          → pydantic 校验 → 确认门 → asyncio.wait_for(handler)            ✅
      → context.append(assistant) + context.append(role=tool)              ✅
      → 外层 while 再发一轮                                                  ✅
  → SSE: thought / action / observation / approval_required / final / usage ✅
  → 前端 chat.js 十个事件类型全覆盖                                          ✅
```

**结论：管路是通的。** 缺口不在"能不能调"，而在下面两个具体环节。

### 2.2 断点一：确认门在对话链路里没有"令牌入口"

**实测**（`tmp/probe_approval_flow.py`，直接用真实 `ToolHub` + `PendingStore` + `run_agent_loop`）：

```
轮 1：approval_required = True | pendingId = f0010b8ba9c2
人工放行：token 长度 = 32，状态 = approved
轮 2（用户按前端提示"再发一条消息让模型重试"）：
      工具状态 = needs_approval
      工具真的执行了吗 = ❌ 没有执行
      又出现 approval_required = True | pendingId = 471b7ff6a904   ← **新建了一条**
当前条目： [('471b7ff6a904', 'pending'), ('f0010b8ba9c2', 'approved')]
```

**根因**（三处代码合起来才成立）：

1. `plugin_api.py:607` 的判据是 `ctx.approval_token`；
2. **全仓唯一的 `approval_token` 赋值点在 HTTP 链路**（`routes_agent.py:95`）；
   `harness/loop.py` 构造 `ToolContext` 时**不传**它（只有 `audit_id` / `source` / `conversation_id`）；
3. `approvals.PendingStore.request()` 只复用 `status == "pending"` 的条目，
   而被批准的那条已经是 `"approved"` → 匹配不到 → **新建 pending**。

⇒ 于是"放行 → 让模型重试"这条路**每一步都正确、合起来永远走不通**。

**三方说法互相矛盾**（这是它能潜伏至今的原因）：

| 出处 | 原话 |
|---|---|
| `routes_agent.py:22` | 「模型重试同一次调用（**原样带 `approvalToken`**）→ 执行」 |
| `plugin_api.py:613-616`（回给模型的话） | 「…等界面放行后**再重试同一次调用**」 |
| `chat-tools.js:387-393`（前端） | 「**刻意不自动重发**……请在输入框里让模型继续或重试」 |

**影响**：`factory_create`（dangerous）与两个 write 工具**在对话里不可用**。
用户唯一能走通的路是 `POST /api/agent/invoke` 手动带令牌——那不是给终端用户准备的。

### 2.3 断点二：只读工具能读走凭据（**仓内两张敏感表不一致**）

**实测**（`tmp/probe_sensitive_paths.py`，用临时 `WORKBENCH_DATA` 造出真实形态的产物，
不碰用户真实数据）：

```
— runtime/browser-states/acct-1.json   ❌ 放行，含 REAL-COOKIE-VALUE / REAL-LOCALSTORAGE-JWT
— runtime/egmp-tokens/token-env1.json  ❌ 放行，**完整吐出**，含 REAL-PLATFORM-JWT
— runtime/accounts-backup/accounts.json ❌ 放行，**完整吐出**，含 REAL-FERNET-KEY / real-ciphertext

对照：同一个路径，沙箱判定
  is_sensitive_path(browser-states)    = True
  is_sensitive_path(egmp-tokens)       = True
  is_sensitive_path(accounts.json)     = True
```

| 表 | 位置 | 内容 |
|---|---|---|
| **沙箱敏感表** `SENSITIVE_PATH_PARTS` | `sandbox/policy.py` | `browser-states` · `egmp-tokens` · `workbench.db(-wal/-shm)` · `pending_action` · `shell-state.json` · `accounts.json` · `.ssh` · `credentials` · `cookies` · `login data` … |
| **只读工具敏感表** `_BLOCKED_PARTS` | `plugins/builtins.py` | `\windows\system32\config` · Chrome/Edge/Firefox profile · `\.ssh\` · `\.aws\` · `\.azure\` · `\secrets\` · `\credentials` · `\cookies` · `\login data` · `\key3.db` · `\logins.json` |

**`_BLOCKED_PARTS` 里没有** `browser-states`、`egmp-tokens`、`workbench.db`、`accounts.json`、
`shell-state.json`——**而它们正是本应用自己会产生凭据的地方**。

**为什么绕过去了**（`_guard_path` 的顺序）：

```python
1. 命中 _BLOCKED_PARTS              → 拒
2. is_inside_app(resolved)          → return      # ← 应用目录内直接放行
3. 应用外：文本后缀白名单 + 2MB 上限  # ← 第 2 步 return 之后，这两道**也不生效**
```

`is_inside_app()` 的根包含 `config.DATA_DIR` 与 `RUNTIME_DIR`——也就是上面三个产物所在的目录。

**一次"差一点就中"的实测**：直接读真实主库 `%APPDATA%\Workbench\workbench.db`，
handler **返回了 1,056,412 字符**。所幸 `plugin_api.MAX_RESULT_CHARS = 16_000` 截断后，
`account_fernet_key` 第一次出现在第 **30,733 字节**（截断之外），本次**密钥没进模型上下文**。

> ⚠️ **但那是运气，不是控制。** `MAX_RESULT_CHARS` 是**上下文预算**手段，不是保密手段——
> SQLite 的页布局随数据量与写入顺序变化（`VACUUM`、继续加账号、换一台机器都可能让它提前）。
> 而上面三个小 JSON 是**纯文本、体积远小于 16k**，会被**完整**读出。这正是本仓反复告诫的
> 「把'我没看到'当成'它不存在'」（`docs/VERSIONING.md` §8、PITFALLS #36）。

**可达性**：模型不需要预先知道绝对路径——`insight_artifacts` 会返回工作区落点，
由它可推导到 `DATA_DIR`，再 `..` 即可。`read_local_file` 会 `resolve()`，`..` 不构成障碍。

---

## 3. 其余使用缺口（P1/P2）

| # | 缺口 | 说明 | 级别 |
|---|---|---|---|
| E | **启动自检的错误日志无处可去** | `api/__init__.py:152` 的 `logger.error("工具自检未通过…")` 是"插件没装起来"的唯一报警，而全仓没有日志 handler → **声明了警报但没人收**。详见 [LOGGING-DESIGN.md](LOGGING-DESIGN.md) | P1 |
| G | **`finish_reason` 被完全忽略** | `deepseek.stream_chat` 从不读 `finish_reason`。模型因 `length` 截断时，截断的回答被当**最终回答**交给用户。而 `routes_chat` 传的 `max_tokens=None` → 走 API 默认值，**不可控** | P1 |
| H | **工具结果的前 1000 字符会进审计** | `ToolHub._audit` 把 `inv.content[:1000]` 写进 `agent_audit.result_summary`。若某工具返回敏感内容（见 B），它会**二次落库**（本地明文表） | P1 |
| I | **多个 `tool_calls` 串行执行** | `loop.py:211` 的 `for call in ...` 串行。模型一次发 N 个独立调用时，延迟是 N 倍 | P2 |
| J | **确认门拦的是"执行"，不是"继续尝试"** | 被拦后循环**继续**，模型可以换个参数/换个工具再试。提示词说"不要猜"，但没有**强制**。这在语义上可接受，但应显式确认是有意为之 | P2 |
| K | **`plugins` 白名单未接线** | `run_agent_loop(plugins=...)` 的口子已留（"按页面裁剪工具面"），但 `routes_chat` 没传 → 全部工具的 schema **每轮全量注入** | P2 |
| L | **`read_local_file` 的描述与实现不符** | 描述说"应用目录内的文件任意读"——读起来像"受限"，实际是"**应用目录内完全不受限**"，而凭据恰好都在应用目录内 | P1（与 B 同源） |

---

## 4. 端到端测试方案（可直接执行）

**前提已确认**：`settings.apiKey` 已配置、`api.deepseek.com` 可达（401）。因此**真实测试可行**。

| 步 | 做什么 | 判据 | 成本 |
|---|---|---|---|
| 1 | **只读工具冒烟**：起服务，问"现在几点？" | SSE 出现 `thought`/`action`(tool=`get_current_time`)/`observation`(ok=true)/`final`；`agent_audit` **新增一行** | ~1k token |
| 2 | **带参数的只读工具**：问"帮我读一下 `<某个 insight 产物路径>`"（先 `insight_runs` 拿到路径） | `action` 两次（`insight_runs` → `insight_read_artifact`）；工具参数由模型**自己填对** | ~3k token |
| 3 | **未知工具的恢复能力**：问一个不存在的工具（如"用 fake_tool 查一下"） | 期望模型**不会**编造；正常它不会调 | ~1k token |
| 4 | **确认门**：问"在 eGMP 上用账号 X 打开浏览器" | `approval_required` 事件出现在 UI；**这一步会暴露 P0-A** | ~1k token |
| 5 | **缓存命中率**：跑完 1–3 后查 `GET /api/agent/cache` | `hitRate > 0`（验证 `deepseek_usage` 真的在入账） | — |

**注意**：第 4 步**现在必然失败**（P0-A）。修好前不必测，测了也只是复现。

**每步的复查入口**（这几个端点就是为本报告这类核实准备的）：

```powershell
# 工具清单与装配失败
curl.exe -s http://127.0.0.1:18765/api/agent/selfcheck
# 最近调用记录（**列表只回摘要列**，细节按 auditId 单查）
curl.exe -s http://127.0.0.1:18765/api/agent/audit
# 待确认操作
curl.exe -s http://127.0.0.1:18765/api/agent/pending
# 缓存命中概览
curl.exe -s http://127.0.0.1:18765/api/agent/cache
```

---

## 5. 修复建议（按优先级）

### P0-A：给对话链路一条令牌通道（**必须修，否则三个工具不可用**）

**三个可选方案**，推荐 **A2**：

| 方案 | 做法 | 代价 |
|---|---|---|
| A1 | 前端在放行后**自动重发**上一条调用 | 与"重发应当由人触发"的既有设计冲突；且要新增"重放一次工具调用"的协议 |
| **A2（推荐）** | **服务端记住"用户已放行的那次调用"**：`PendingStore` 增加 `claim_approved(tool, args)` —— 无令牌时**先查有没有一条 `approved` 且指纹相同的条目**，有就消费它（等价于令牌） | 改动集中在 `approvals.gate()` 一处，约 10 行；语义与既有"参数指纹绑定"一致 |
| A3 | `ToolContext` 携带会话级已批准指纹集合 | 要新增会话状态与过期语义，复杂 |

> A2 的关键优势：它把"谁批准了"这件事留在**服务端的内存权威**里，
> 与 `approvals.py` 既有的"权威状态在内存、DB 只留痕"完全一致，
> 且**不放松任何安全属性**——仍然是"同一工具 + 同一参数指纹 + 5 分钟内"。

**同时要修的**：`routes_agent.py:22`、`plugin_api.py:613`、`chat-tools.js:388` **三处说法统一**，
并加一条回归断言：**"放行后重试必须真的执行"**（现在这条断言不存在，所以缺陷能潜伏）。

### P0-B：把敏感路径表收成**一张**

**根因是"两张表并存"**——与 2026-09-15 那次"两套工具表并存"是**同一类错误**。

**做法**：把 `SENSITIVE_PATH_PARTS` 提到**唯一事实源**（如 `workbench/services/paths.py`
或 `config`），`sandbox/policy.py` 与 `plugins/builtins.py` **都从它读**；
再补一条机检（在 `tests/` 或 `verify_packaging.py`）断言"两张表内容一致"。

**同时收紧 `_guard_path` 的顺序**：把敏感表判定放在 `is_inside_app` **之后也仍然生效**
（即"应用目录内"只豁免**后缀与体积**限制，**不豁免**敏感路径）。

加回归：**对三个真实产物形态（browser-states / egmp-tokens / accounts.json）断言被拒**。

### P1：日志（与需求 1 合并处理）

见 [LOGGING-DESIGN.md](LOGGING-DESIGN.md)。其中 **P0 阶段**就会让 E / G / H 三条缺口变得可见。

### P1：`finish_reason` 与 `max_tokens`

- `stream_chat` 读 `finish_reason`，`length` 时在 `final` 事件里带 `truncated: true`，前端显式提示；
- `routes_chat` 显式传 `max_tokens`（而不是留 `None`）。

### P2：并行工具调用 / 工具面裁剪

`loop.py:211` 改 `asyncio.gather`（注意确认门与 pending 展示顺序）；
`routes_chat` 接上 `plugins` 白名单。

---

## 6. 附录：复现脚本

| 脚本 | 证明什么 |
|---|---|
| `tmp/probe_approval_flow.py` | P0-A：放行后重试仍被拦，且新建 pending；工具未执行 |
| `tmp/probe_sensitive_paths.py` | P0-B：三个敏感产物被完整读出；沙箱表三个都拦 |
| `tmp/probe_leak_severity.py` | 主库被返回 1,056,412 字符；密钥在第 30,733 字节（截断外）；DeepSeek 可达 401 |
| `tmp/audit_inventory.py` | 各留痕表的真实行数 |
| `tmp/diagnose_empty_tables.py` | 空表归因 = **时间差**（最后对话 09-09 < 工具层 09-15），非静默失败 |

> 这些脚本在 `tmp/`（本仓约定的本地排障暂存区，**已被 .gitignore**）。
> 若要把 P0-A / P0-B 的判据长期守住，应把它们改写成 `tests/` 下的回归断言——
> **按本仓纪律，那才是"修好了"的标志**（见 [PITFALLS.md](PITFALLS.md) #42：
> "函数测过了 ≠ 链路用上了"）。
