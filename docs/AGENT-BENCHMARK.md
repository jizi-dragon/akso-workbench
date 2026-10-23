# Agent 工作台对标：WorkBuddy（AGENT-BENCHMARK）

> **这份文档回答**：本工作台相对腾讯 WorkBuddy 这类「Agent 智能工作台」，**基础能力对齐到哪一步、
> 哪些仍是缺口、下一步做什么**。
>
> **口径（重要）**：
> - WorkBuddy 一侧的依据是**公开资料**（2026-08 的教程与官方文档链接，见 §8），
>   不是逆向或实测；它随时会变，**以官方为准**。
> - Workbench 一侧的每条结论都指向本仓的**代码或判据**，可复核。
> - 只对标「**基础五件套**」：Agent 循环、Tools、Skills、Sandbox、MCP；
>   外加 WorkBuddy 明确有、而本仓明显缺的**记忆 / 多任务 / 产物 / 自动化**。
> - 「功能追不上」是承认的：WorkBuddy 有 100+ 领域专家、Coding Mode、连接器等产品化能力，
>   本仓不做同等投入；但**基础协议面与安全默认必须对齐**。

---

## 1. 结论摘要

| 基础能力 | WorkBuddy | Workbench（2026-10-19） | 判定 |
|---|---|---|---|
| Agent 循环 / 多步执行 | ✅ 规划任务、多步执行、结果面板 | ✅ `harness/loop.py`：步数预算、停止、确认、超时、回填未执行调用 | **对齐（基础）** |
| Tools | ✅ 内置工具 + MCP 工具 | ✅ 5 插件 / 20 内置工具；风险分级 / 信任标签 / 可逆性 / 审计 | **对齐（基础）** |
| Skills | ✅ 上传/创建/启停/批量卸载 | ✅ `SKILL.md`（wraps / script 二选一）、ZIP 准入、安装/卸载/禁用 | **对齐（基础）** |
| Sandbox | ✅ 权限确认 + 工作区边界 | ✅ 限损层沙箱：Fail-Closed 策略、解释器白名单、Job Object 树杀；**2026-10-19 加工作区根 + 只读/联网/派生进程预检** | **对齐（定位不同）** |
| MCP | ✅ 用户级/项目级 `mcp.json`、状态页 | ✅ **2026-10-23**：`mcp.json`、stdio + Streamable HTTP、`mcp__server__tool`、server instructions、分页、**健康巡检/自动重连/`tools/list_changed`/按工具 risk** | **对齐（基础）** |
| 记忆 | ✅ 对话记忆、每晚摘要、可编辑/导入 | ❌ 只有会话内摘要（`cache.freeze_summary`），**无跨会话长期记忆** | **缺口** |
| 多任务 | ✅ 多任务并行、上下文隔离 | 🟡 后端按会话独立；前端 `isRunning` 是全局的，一次只能跑一个 | **部分** |
| 产物 / 结果面板 | ✅ Artifacts / All Files / Changes / Preview | 🟡 对象分析有产物预览（Markdown/drawio）；无通用文件/差异面板 | **部分** |
| 权限 / 确认 | ✅ 高风险操作人工确认 | ✅ 单次令牌 + 参数指纹 + 模型可主动确认；确认门 fail-closed | **对齐** |
| 自动化 / 定时 | ✅ 定时任务、低风险自动执行 | ❌ 无调度器 | **缺口** |
| 可观测 / 审计 | ✅ 运行日志与结果面板 | ✅ `agent_run`/`agent_span`、审计表、OTel 导出、运行时间线 | **对齐（更强）** |

**一句话**：基础五件套（agent / tools / skills / sandbox / MCP）已经对齐到「能接、能管、能审计」；
真正欠的是**记忆、多任务、产物面、自动化**——它们不影响"能不能干活"，但影响"像不像一个工作台"。

---

## 2. 逐项对标

### 2.1 Agent 循环

| 维度 | WorkBuddy | Workbench | 证据 |
|---|---|---|---|
| 多步工具调用 | 规划 → 执行 → 结果 | `run_agent_loop`：call → tool → observe → repeat | `workbench/harness/loop.py` |
| 终止条件 | 任务完成 / 用户停止 | 步数预算、停止、超时、上下文硬上限 | 同上 |
| 停止 | 有 | `POST /api/chat/stop` + `cancelled` 事件（**2026-10-19 修好前端停止键**） | `tests/test_agent_stop.py` |
| 中断恢复 | 任务级续跑 | 进程内未执行调用回填 + 跨重启 `repair_dangling`（只补历史、不重放） | `tests/test_agent_resume.py` |
| 上下文隔离 | 按任务 | 按会话（`conversationId`）；上下文前缀 append-only | `cache.py` |
| 错误自愈 | 有 | 工具异常转观测，模型可自我纠正 | `tests/test_eval_offline.py` |

### 2.2 Tools

| 维度 | WorkBuddy | Workbench | 证据 |
|---|---|---|---|
| 工具定义 | 内置 + MCP | 单一 `ToolSpec`，`plugin_api` 唯一扩展面 | `plugin_api.py` |
| 风险分级 | 有 | `READ` / `WRITE` / `DANGEROUS`；无令牌不执行 | `approvals.py` |
| 信任标签 | 未公开 | `Trust.LOCAL` / `UNTRUSTED`（I18）；外部内容打机器可读前缀 | `tests/test_trust_labels.py` |
| 可逆性 | 未公开 | `reversibility` + `undo_recipe`，写工具必须声明 | `tests/test_undo_recipe.py` |
| 审计 | 有 | 每次调用进 `agent_audit`（审计维度，不含凭据） | `plugin_api._audit` |
| 工具上限 | 未公开 | 40 条警报线（`MAX_TOTAL_TOOLS`），超限记 failure | `plugin_api.py` |

### 2.3 Skills

| 维度 | WorkBuddy | Workbench | 证据 |
|---|---|---|---|
| 形态 | 上传 / 创建 / 推荐 | `SKILL.md`：`wraps` 声明式 或 `script` 脚本式 | `workbench/skills/manifest.py` |
| 安装 / 卸载 | 有 | 共享库 install / uninstall / delete，ZIP 准入 | `tests/test_skills.py` |
| 启用 / 禁用 | 有 | `hub.set_plugin_enabled()`（仍在清单里，只是模型看不到） | `plugin_api.py` |
| 脚本执行 | 沙箱 | 脚本式 Skill 走 `sandbox.run()`，risk 不得低于 write | `tests/test_skill_scripts.py` |
| 批量卸载 | 有 | ❌ 没有 UI 批量；API 逐个 | — |

### 2.4 Sandbox

| 维度 | WorkBuddy | Workbench | 证据 |
|---|---|---|---|
| 定位 | 权限 + 工作区边界 | **限损层，不是安全边界** | |
| 策略 | 未公开 | Fail-Closed：未知策略 / cwd 越界 / 敏感路径 / 非白名单解释器一律拒 | `sandbox/policy.py` |
| 进程树 | 未公开 | Windows Job Object 树杀 | `sandbox/job.py` |
| 审计 | 有 | 唯一扼流点 `sandbox.run()` | `tests/test_sandbox.py` |
| 网络 | 声明式 | `allow_network` 是声明，不是强制 | 同上 |

### 2.5 MCP（本次新增）

| 维度 | WorkBuddy | Workbench | 证据 |
|---|---|---|---|
| 配置 | 用户级 `~/.workbuddy/mcp.json` + 项目级 | 用户级 `<数据目录>/mcp.json`（形状兼容） | `docs/MCP.md` |
| 传输 | 公开资料未细分 | stdio（JSON-RPC 2.0，一行一条） | `services/mcp.py` |
| 生命周期 | 插件页状态（绿/红） | 设置页状态 + 「重新连接」；后台装载不阻塞启动 | `routes_mcp.py` |
| 工具发现 | 有 | `tools/list` → 动态 pydantic 入参 → installed 层 | `mcp_registry.py` |
| 工具调用 | 有 | `tools/call`；结果标 `UNTRUSTED`；默认 `WRITE` 过确认门 | 同上 |
| 失败隔离 | 有 | 一个 server 坏了不影响其余；坏配置只跳过自己 | `tests/test_mcp.py` |
| 环境变量 | 有 | **白名单继承**，不传宿主凭据；`env` 显式追加 | `tests/test_mcp.py` |
| 远程传输 | 有 | ❌ 只 stdio | `docs/MCP.md` §8 |
| resources / prompts | 有 | ❌ 本版只接 tools | 同上 |

### 2.6 记忆（缺口）

WorkBuddy：对话记忆默认开启，**每晚重新生成摘要**；用户可查看、编辑、要求“记住/忘记”，
还能导入其他 AI 产品的偏好。适合存稳定偏好，不适合存密码与一次性状态。

Workbench：只有**会话上下文**——`cache.freeze_summary` 把长历史压成摘要，
`summarized_upto` 只前进；进程重启后靠 `messages` 重建。**没有跨会话的用户级记忆**，
也没有“记住这条”的工具或设置页。

> 这是当前**第一优先**的能力缺口（§3 P1）。基础实现不需要每晚摘要：
> 一张 `agent_memory` 表 + 3 个工具（list/add/delete）+ 系统提示词里的使用说明，
> 就能覆盖 80% 的“记住我的偏好”场景。

### 2.7 多任务 / 产物 / 自动化（部分或缺口）

| 能力 | 现状 | 差距 |
|---|---|---|
| 多任务并行 | 后端按 `conversationId` 隔离；前端 `chat.js` 的 `isRunning` 是模块级布尔 | 同一时间只能跑一个回合；切到别的会话也发不出去 |
| 产物面板 | 对象分析有**卡片墙 + 类型筛选**；`artifact-preview.js` 支持 Markdown/drawio/JSON/CSV/图片/日志 | 仍没有通用的「本次任务产出了哪些文件 / diff / 预览」；附件与 Skill 产物未统一 |
| 工作区 | 沙箱有可配工作区根 + 读写/只读权限；对象分析有可配工作区根 | 没有统一的"项目/工作区"概念；附件、产物、Skill 仍分散在数据目录 |
| 定时任务 | 无 | 没有调度器、没有"低风险自动执行"的白名单 |
| Coding Mode | 无 | 本仓定位不是代码编辑器；`sandbox` 可跑脚本，但没有 Changes/Preview 面板 |

---

## 3. 仍存在的缺陷（按优先级）

> 这一节就是"若仍存在缺陷，给我一份对标文档"里要的那份清单。
> 每条都给出**现状 / 影响 / 下一步**，不把"没做"写成"做了"。

### P1 — 影响基础体验或安全默认

| # | 缺陷 | 现状 | 下一步 |
|---|---|---|---|
| 1 | **长期记忆缺失** | 无表、无工具、无注入 | 迁移 26 `agent_memory`；`memory_list` / `memory_add` / `memory_delete`（add/delete 为 write）；系统提示词加"何时查记忆"；设置页可查看/删除 |
| 2 | **云端账号库仍无自动双向同步** | **2026-10-20 已补只读拉取 + 本地→云端一次性 merge**（脱敏 + 5 分钟缓存 + 断网回落）；仍无自动轮询/冲突 UI | 做自动同步前先定冲突展示与重试策略 |
| 3 | ~~MCP 只支持 stdio~~ **已补**（2026-10-23） | `streamable-http` + `mcp__server__tool` + instructions 已接 | 仍保持“配置只由人写”；resources/prompts 未做 |
| 4 | **MCP 通知 / 资源面未接** | 自动重连/健康检查已补；`resources` / `prompts` / `sampling` / progress 仍忽略 | 按需接 `resources`/`prompts`；sampling 不做 |
| 5 | **`/api/*` 无认证** | 本机回环、单用户； 有意保留 | 不改成"假装有认证"；文档明确边界；远程访问一律不支持 |

### P2 — 工作台形态

| # | 缺陷 | 现状 | 下一步 |
|---|---|---|---|
| 6 | 前端单任务 | `chat.js` 全局 `isRunning` | 改成按会话的 `Map<conversationId, state>`；后端本来就支持 |
| 7 | 通用产物面板缺失 | 对象分析已有卡片墙 / 类型筛选 / CSV / 图片预览 | 统一附件与 Skill 产物列表 + diff；复用现有 `artifact-preview.js` |
| 8 | 无调度器 | 只能手动触发 | 定时任务表 + 低风险工具白名单 + "只读自动执行"；写操作仍过确认门 |
| 9 | 覆盖空洞 | `/api/attachments` HTTP 层无 pytest | 补最小 HTTP 判据（不用浏览器） |
| 10 | 无类型检查 | 只有 ruff + pytest | 先给 `plugin_api` / `mcp_registry` / `harness` 上 pyright 的 basic 模式 |

### P3 — 工程债 / 已知取舍

| # | 事项 | 说明 |
|---|---|---|
| 11 | 平台 JWT 明文缓存 | `services/egmp/client.py`；与口令的 Fernet 口径不齐（已登记） |
| 12 | 大文件 | `plugin_api.py` 1,700 行、`loop.py` 1,277 行、`chat-tools.css` 1,372 行 |
| 13 | 发布链 | 版本真源已统一为 `VERSION`；自动更新仍待 Tauri + 内部 HTTPS 更新源落地 |
| 14 | ~~多账号并行浏览器~~ | 2026-10-19 内置浏览器整体下线；需要多账号并行请用独立 akso-pass |
| 15 | 配置工厂写路径 | 2026-09-22 整体移除；2026-10-19 内置浏览器下线后产品里也没有 WRITE 工具，这是刻意状态 |

---

## 4. 不建议追的部分

| WorkBuddy 能力 | 为什么不追 |
|---|---|
| 100+ 领域专家 | 本仓服务的是**一个具体平台（eGMP）的实施工作**；"专家"是内容运营，不是工程能力 |
| Coding Mode / Changes / Preview | 本仓不是代码编辑器；已有 `sandbox` 跑脚本 + `read_local_file`，足够做实施自动化 |
| GitHub/GitLab 连接器 | 与当前场景（平台对象分析、账号托管）无关；将来需要时按 MCP server 接 |
| 每晚记忆摘要 | 先用"显式记住/删除"覆盖高频场景；自动摘要的收益需要真实使用数据再评估 |
| 云端多租户 / 团队协作 | 本仓是单机单人；云端账号库只承担"跨设备账号"这一件事 |

---

## 5. 验收方式

```powershell
# 基础五件套
.\.venv\Scripts\python.exe -m pytest tests\test_agent_loop.py tests\test_plugin_api.py `
  tests\test_skills.py tests\test_sandbox.py tests\test_mcp.py -o addopts=""

# MCP 端到端（假 server，不联网）
.\.venv\Scripts\python.exe -m pytest tests\test_mcp.py -o addopts="" -q

# Agent 行为评测（离线、不花 token）
.\.venv\Scripts\python.exe tools\run_evals.py --offline

# 文档/工具清单
.\.venv\Scripts\python.exe tools\gen_tools_doc.py
```

**判定口径**：一条能力只有同时满足「有代码路径」「有判据」「文档说清边界」才算"对齐"；
只有代码没有判据的，本文件标 🟡 而不是 ✅。

---

## 6. 本轮（2026-10-19）实际改动

| 项 | 改动 |
|---|---|
| 停止键 | `jsonFetch` 统一序列化对象 body；新增 Node 直跑 `api.js` 的字节级判据 |
| 对象分析 SSE | 客户端断开也落终态（`finally` + done callback），不再永远 `running` |
| 可选路由 | 逐个 try/except，单域失败不拖垮应用；`server.spec` 补 13 个 hiddenimports |
| 审批门 | 未装配确认门时 **fail-closed**（风险工具拒执行） |
| 云端地址 | 默认从 `:8443` 改 443；补 `tests/test_vault.py`（7 条） |
| MCP | 新增 stdio 客户端 / 注册层 / API / 设置页 / 13 条测试 / `docs/MCP.md` |
| MCP 稳定性（本轮补） | 30 秒健康巡检 + `ping`；进程退出 / ping 失败 / `tools/list_changed` 自动重连；指数退避；单 server 重连；按工具 risk；状态显示协议/版本/工具名；测试 13 → **22 条** |
| 产物面板（本轮补） | 对象分析产物由表格改为**卡片墙 + 类型筛选**；预览新增 **CSV 表格 / 图片 / 复制正文 / 新窗口打开**；`verify_insight_preview.py` 18/18 |
| 沙箱工作区（本轮补） | 可配工作区根 + 只读/读写 / 联网 / 派生进程预检；策略新增 `writes`；`GET/PUT /api/sandbox/workspace`；页面新增「工作区与权限」；沙箱测试合计 **44 条**（新增工作区 11 条） |
| 云端账号库（本轮补） | 后端保留 `GET /api/vault/library` 只读拉取 + 脱敏 + 5 分钟缓存 + 断网回落；**2026-10-20 用户选择只保留账号中心，`cloud.html` / `cloud.js` / 独立只读页已删除**；账号中心保留设备流登录/身份入口 |
| 锁文件 | `uv.lock` 纳入 bump 副本；CI 在 `uv sync` **之前**跑 `uv lock --check`（Electron 的 `package-lock.json` 已随 `desktop/` 删除） |
| 测试提速 | 快层 66.9s → 25.8s（I11 O(n²) 修复、DNS 等待改注入、浏览器验收归 slow） |

---

## 7. 下一步建议（按性价比）

1. **记忆**（P1）：迁移 26 + 3 个工具 + 设置页；预计 1 天，收益最高。
2. **云端双向同步**（P1）：只读拉取已完成；若要合并/推送，先定 `snapshotId` 幂等与冲突策略。
3. ~~**MCP 第二阶段**：HTTP/SSE 传输~~（2026-10-23 已完成 Streamable HTTP）；健康巡检/自动重连已在 v0.4.1 补上。
4. **多任务前端**（P2）：`isRunning` 改成按会话；后端无需改。
5. **通用产物面板**（P2）：把附件、Skill 产物与对象分析统一到一张列表 + diff；预览组件已可复用。
6. **覆盖空洞**（P2）：`/api/attachments` HTTP 层、`/api/vault`（已补）。
7. **发布链**（P2）：Phase 0 已统一 `VERSION`；下一步是 Tauri 壳 + 内部 HTTPS 更新源（见 `docs/` 迁移方案）。

---

## 8. 外部参照

> 以下是 WorkBuddy 一侧的公开资料，**外部内容，仅作对照**；其功能与界面以官方为准。

- 七牛云《WorkBuddy 进阶教程：Skills、MCP、记忆与多任务实战》（2026-08-12）：
  <https://news.qiniu.com/archives/1786500985404>
- WorkBuddy 官方文档（教程中引用）：
  - 概览 <https://www.workbuddy.ai/docs/zh/workbuddy/>
  - 快速入门 <https://www.workbuddy.ai/docs/workbuddy/Quickstart>
  - 10 个上手技巧 <https://www.workbuddy.ai/docs/zh/workbuddy/From-Beginner-to-Expert-Guide/Efficient-Tips>
- 腾讯 WorkBuddy 开放平台报道（2026-09-02）：
  <https://tech.cnr.cn/techph/20260902/t20260902_527802481.shtml>

---

_本文与 [MCP.md](MCP.md)、[CORE-ARCHITECTURE.md](CORE-ARCHITECTURE.md) 配套；数字与判据以仓库当日状态为准。_
