# 插件 SDK 契约（Agent 核心的唯一公开扩展面）

_2026-09-15 定稿。锚定 `workbench/plugin_api.py`（`ToolHub`）+ `workbench/plugins/` + `workbench/approvals.py`。_

> **这份文档回答**：工作台三大业务（平台洞察 / 配置工厂 / 快捷登录）是怎么"作为工具插件
> 挂到 Agent 上"的，以及你新增一个插件时该动哪几个文件、哪些坑必须绕开。

## 0. 一页话结论

| 问题 | 答案 |
|---|---|
| 插件是什么 | 一个 `Plugin`（id + 标题 + 一组 `ToolSpec`），经 `register(hub)` 注册进 `ToolHub` |
| 宿主是谁 | `workbench/plugin_api.py` 的 `ToolHub`（进程级单例 `get_hub()`） |
| 扩展面有几个 | **一个**：`ToolHub` 的工具注册表。不是 router、不是模块清单、不是 entry_points |
| 一个工具能被谁用 | 三处**同一份**定义：对话 Agent 的 function calling、`POST /api/agent/invoke`、审计表 |
| 危险操作怎么拦 | `Risk.WRITE`/`DANGEROUS` → **无单次令牌不执行**（`approvals.py` 的确认门） |
| 怎么发现插件 | **不发现**。`plugins/__init__.py::_MODULES` 是显式清单，加插件改一行 |
| 坏插件会怎样 | 被隔离跳过（记一条 failure），宿主照常启动；`GET /api/agent/selfcheck` 能查到 |
| 不做哪些事 | entry_points 自动发现、独立进程、热插拔、版本协商矩阵、插件市场 —— 见 §8 |

**为什么是这个形态**：`docs/research/core-plugin-separation-research.md` §A5 的结论是
"插件作者只有你自己、全是可信代码、数量 1–3 个"时，自动发现/沙箱/签名的收益接近零；
真正要买的是**依赖方向与边界**（`plugins → plugin_api`，永不反向），因为那同时服务
技术解耦与权利边界。同文件 §A4-5 还有一条直接判断：**`harness` 的工具注册表比 router
更值得作为插件 API 的唯一公开面**——本文就是那条判断的落地。

---

## 1. 三条不变量（改任何插件前先读）

### 不变量 1：宿主拥有装配权，插件只交声明

插件不碰 `app`、不碰静态托管、不决定数据目录、不决定配置存哪。它只交一个
`Plugin`，装不装、按什么顺序装、失败怎么办，全在宿主手里。

```python
def register(hub: pa.ToolHub) -> None:
    hub.add_plugin(PLUGIN)
```

违反后果：插件一旦能自己挂 router / 写配置文件，它就不再"可整体拆除"，
`docs/research/core-plugin-separation-research.md` §B9 想要的边界就没了。

### 不变量 2：依赖方向单向

```
workbench/plugins/**  ──依赖──▶  workbench/plugin_api.py
                      ──依赖──▶  workbench/services/**
workbench/plugin_api.py  ──绝不──▶  workbench/plugins/**
workbench/plugin_api.py  ──绝不──▶  workbench/services/egmp/**
```

`plugin_api.py` 里出现任何平台术语（`egmp` / `capa_plan__c` / 具体端点）都是错的。
它连 `db` 都不 import——审计写入器与确认门是**注入**进去的（`approvals.install(hub)`）。

### 不变量 3：一个工具只有一处定义

工具清单的单一事实源是 `ToolHub`。以下三处必须都从它读，不许各自维护表：

| 消费方 | 读法 |
|---|---|
| 对话 Agent（function calling） | `harness/loop.py` → `hub.list_api_for(plugins)` |
| HTTP 调用面 | `api/routes_agent.py` → `hub.get()` / `hub.execute()` |
| 审计 | `approvals.write_audit()`（由 `hub` 在每次执行后回调） |

**这条是 2026-09-15 收口的直接动因**：改造前 `harness/tools.py` 有自己一套工具
（三个基础工具），`api/routes_agent.py` 另有一套（四个域工具），两套并存且互不可见。
后果是——**对话里的模型只能聊天，一个平台能力都看不到**；而域工具只对 HTTP 调用方可见。
两套表合成一个 `ToolHub` 之后，"能调什么"只有一个答案。

---

## 2. 最小插件（照抄就能跑）

```python
# workbench/plugins/demo.py
from __future__ import annotations

import json

from pydantic import BaseModel

from .. import plugin_api as pa


class LookupArgs(BaseModel):
    code: str = pa.Arg("对象编码，如 capa_plan__c")
    limit: int = pa.Arg("最多返回多少条", default=20, ge=1, le=100)


async def lookup(args: LookupArgs, ctx: pa.ToolContext) -> str:
    """只读查询：注意 handler 是 async，同步阻塞调用要用 asyncio.to_thread 挪走。"""
    pa.require(args.code, "code 不能为空")
    return json.dumps({"ok": True, "code": args.code}, ensure_ascii=False)


PLUGIN = pa.Plugin(
    id="demo",
    title="示例插件",
    version="1.0.0",
    description="演示插件 SDK 的最小形态",
    tools=[
        pa.ToolSpec(
            name="demo_lookup",
            description="按编码查东西（模型靠这句话决定要不要调它，要写清后果）",
            args_model=LookupArgs,
            handler=lookup,
            risk=pa.Risk.READ,
            timeout_s=30,
            audit_extra=pa.audit_env_key("code"),
        ),
    ],
)


def register(hub: pa.ToolHub) -> None:
    hub.add_plugin(PLUGIN)
```

然后在 `workbench/plugins/__init__.py` 的 `_MODULES` 加一行 `"demo"`，
在 `workbench/server.spec` 的 `hiddenimports` 加一行 `"workbench.plugins.demo"`（见 §7 坑 1）。

### 契约要点（`ToolSpec` 的每个字段都有理由）

| 字段 | 约束 | 为什么 |
|---|---|---|
| `name` | ASCII，`[A-Za-z0-9_-]{1,64}`，全局唯一 | DeepSeek/OpenAI 的 function name 限制；**中文名会在请求侧被拒，本地看不出来**。装机期就断言 |
| `description` | 必填，写清**后果** | 模型只靠它选工具。`factory_create` 的描述必须写"会真的改平台数据" |
| `args_model` | pydantic `BaseModel` 子类 | JSON Schema 由它生成（单一事实源，杜绝手写 schema 漂移）；模型传参先经它校验，字段级错误直接回给模型 |
| `handler` | `async (args, ctx) -> str | dict | BaseModel` | 同步阻塞调用（httpx/Playwright/子进程）**必须** `asyncio.to_thread` 挪走，否则阻塞事件循环 |
| `risk` | `READ` / `WRITE` / `DANGEROUS` | 决定是否过确认门。**默认 READ，写操作必须显式声明** |
| `timeout_s` | 秒 | 工具不许无限期挂着。跑平台任务的给 300–900，纯本地查询 15–60 |
| `audit_extra` | `Callable[[args], dict]` | 抽审计维度（环境/对象/账号）。**不得含凭据**——`redact_args` 会再过一遍，但别指望它兜底 |

工具名命名约定：`<plugin_id>_<动作>`。内置工具（`get_current_time` 等）是历史例外。

---

## 3. 现有五个插件与 20 个工具

> 实时清单以运行期 `get_hub()` 实例为准（`len(hub.plugins)` / `len(hub.specs())`），
> 或直接看 **[docs/TOOLS.md](TOOLS.md)**（由 `tools/gen_tools_doc.py` 从运行期生成，`tests/test_tools_doc.py` 钉住）。
> 2026-09-15 引入 `web_search` 时曾漏把它写进本表（`builtin` 之外），故此后**以运行期为准**；
> 本表为 2026-09-17 快照。

| 插件 | 工具 | 风险 |
|---|---|---|
| `builtin`（通用） | `get_current_time` / `read_local_file` / `read_web_page` | read |
| `websearch`（自建联网检索） | `web_search`（DeepSeek API **没有**内置联网搜索，见 [research](research/deepseek-api-capabilities.md) §2） | read |
| `insight`（平台洞察） | `insight_accounts` / `insight_read_config` / `insight_run` / `insight_artifacts` / `insight_read_artifact` / `insight_runs` | 全 read |
| `factory`（配置工厂） | `factory_validate_blueprint` / `factory_list_blueprints` / `factory_jobs` / `factory_status` | read |
| | `factory_create` | **dangerous** |
| `accounts`（快捷登录） | `accounts_list` / `accounts_login` / `accounts_browser_sessions` | read |
| | `accounts_browser_open` / `accounts_browser_close` | **write** |

统计：3 + 1 + 6 + 5 + 5 = **20 个工具 / 5 个插件**。

实时清单：`GET /api/agent/tools`（含参数说明与风险）；插件维度：`GET /api/agent/plugins`。

### `websearch` 为什么是独立插件（而不是并进 `builtin`）

DeepSeek API **没有**内置联网检索（见 [research/deepseek-api-capabilities.md](research/deepseek-api-capabilities.md)），
所以 `web_search` 是自建能力，且**自带外部依赖与失败模式**（检索源不可用、结果需要降级）。
把它与"读时间/读文件"这类零依赖工具放在同一个插件里，会让"检索坏了"和"通用工具坏了"难以区分。
**插件边界按"依赖与失败域"划，不按"看起来像不像一类"划。**

### 三个业务插件的设计取舍（都踩过或刻意避开了坑）

**`insight`：不提供"列出平台全部对象"。** 平台没有实证过的对象清单端点
（`kernel/endpoints.py:74-77` 那三条探针是上游 README 给的，可用性需按环境实测、可能全 404）。
CODEBASE_OVERVIEW 的红线是「端点常量必须实证来源，不许凭观察猜」——所以宁可不给这个能力，
让模型先问用户对象编码、再用 `insight_read_config` 验证。

**`insight_run` 复用页面同一条入口**（`api.routes_insight.run`），不另写一套 spider 调用。
绕过去会漏掉 `workspace_dir` / `coverage_status` 回填（2026-09-14 修的真实缺陷），
后果是"页面上明明有 8 件产物、界面只显示一个 proc.log"。**插件层图省事的代价由用户承担。**

**`factory_create` 是 DANGEROUS 而不是 WRITE**：它做的是一次误调用不会报错、
**会真的建出来**的批量写入，回滚只能平台侧手工删。
它与引擎侧"环境强制确认"（`routes_factory.run` 的 `confirmed=true`，否则 409）
是**两条独立的闸门**，不是替代关系：工具级确认门挡住 Agent 链路，
引擎侧闸门挡住"绕过页面直接打 HTTP"的人。

**`read_local_file` 不加确认门，而是加边界**：它原来是**无限制的绝对路径读**——
任何凭据文件都能被模型一句话读出来（结果还会原样进对话进审计）。现在两档收紧：
① 敏感路径（凭据库 / 浏览器 cookie 库 / 系统目录）一律拒；
② 应用目录之外只收"文本类后缀 + ≤2MB"。
为什么不干脆用确认门：那样**每一次合法读文件都要用户点一下**，而"读用户指定的报表/日志"
是高频真实用法，过度打扰会让人习惯性点"放行"，反而把确认门用废。

---

## 4. 风险分级与确认门（把声明变成强制）

`Risk` 只是**声明**；没有确认门，"危险操作需人确认"就只是一行注释。强制在
`workbench/approvals.py`：

```
模型调用风险工具
  → 无令牌 → 建 pending_action 行 + 返回 needs_approval 观测（**不执行**）
  → 界面拿 pendingId + 人话摘要问用户
  → 放行 → 兑换**单次令牌**（一次性 / 5 分钟 / 绑定工具+参数指纹）
  → 模型重试同一次调用（原样带令牌）→ 门开 → 真正执行
  → 拒绝 → 记 rejected → 模型看到拒绝理由，不许重试
```

### 两条链路的走法

| 链路 | 拦截表现 | 放行动作 |
|---|---|---|
| 对话（SSE） | 事件 `approval_required` + `observation.invocation.status = "needs_approval"` | 界面按钮 → `POST /api/agent/pending/{id}/approve` → 让模型重试同一次调用 |
| HTTP | `409` + `detail.pendingId/risk/summary` | `POST .../approve` 拿 `approvalToken` → 带令牌重投 `POST /api/agent/invoke` |

### 四条硬性质（`tests/test_plugin_api.py` 逐条钉住）

1. **被拦下的工具绝不被调用**（断言 handler 的调用记录为空，不是看返回码）。
2. **令牌一次性**：批准一次不能重放多次（`_by_token.pop` 即作废）。
3. **同工具+同参数的重复申请复用同一条 pending**（避免界面被刷屏）。
4. **人话摘要而非 JSON**：`approvals.summarize()` 生成"这个工具要做什么 + 影响谁 +
   ［几级］"。用户要判断的是**后果**，不是参数名。

### 一处刻意的设计：待确认状态不跨进程恢复

权威状态在内存（`PendingStore._items`），DB 的 `pending_action` 表只做**留痕**。
应用重启时把上一进程遗留的 `pending`/`approved` 行标成 `expired`，**不恢复**——
用户重启了应用，凭什么还替他记住一个未确认的写操作？这条是刻意的，别"顺手"改成恢复。

---

## 5. 审计（工具调用可观测）

**唯一入口是 `approvals.write_audit()`**，HTTP 面与对话循环共用它。改造前
`harness` 完全不写审计表——"对话里跑了 30 次平台调用，审计里一条没有"。

`agent_audit` 的列（迁移 9 建表 + 迁移 16 补三列）：

| 列 | 说明 |
|---|---|
| `id` | = 事件的 `auditId`，**前端卡片与审计行靠它对齐** |
| `tool` / `plugin` / `risk` | 谁调的、哪来的、几级 |
| `status` | `ok` / `error` / `needs_approval` / `rejected` / `timeout` / `unknown_tool` / `bad_args` |
| `args` | 审计维度（**凭据键被打码**：password/token/secret/cookie…） |
| `result_summary` | 结果或错误的前 1000 字 |
| `duration_ms` / `created_at` | 耗时与时间 |
| `meta` | 来源（`agent` / `http`） |

查询：`GET /api/agent/audit`（列表，**刻意不 `SELECT *`**）、`GET /api/agent/audit/{id}`（单条）。
跨进程的前端事件 → 审计行对齐：`action`/`observation` 事件里都带 `auditId`。

---

## 6. 装配、自检与失败隔离

```python
# workbench/plugins/__init__.py
_MODULES = ("builtins", "insight", "factory", "accounts")   # ← 加插件改这里

def load_all(hub):
    for name in _MODULES:
        try:
            importlib.import_module(f"{__name__}.{name}").register(hub)
        except Exception as exc:      # 坏插件只记一条 failure，宿主照常启动
            hub.mark_load_failure(name, f"{type(exc).__name__}: {exc}")
```

**为什么坏插件必须被隔离**：插件是"可选能力"。一个插件 import 失败就崩掉整个应用，
等于让"平台洞察装不上"升级成"工作台打不开"。

**为什么隔离了还必须自检**：隔离的另一面是**静默**——"应用照常启动，但某个功能没了"。
所以启动期把必需工具清单对一遍并写日志，同时暴露成 `GET /api/agent/selfcheck`：

```
{"ok":true,"plugins":4,"tools":19,"riskCounts":{...},
 "requiredTools":[...],"missingRequired":[],"failures":[]}
```

`REQUIRED_TOOLS` 定义在 `api/routes_agent.py`——**加一个"缺了就等于功能没了"的工具时，
记得把它加进这份清单**，否则自检查不出来。

---

## 7. 三个必须绕开的坑（都真踩过）

### 坑 1：PyInstaller 漏收动态导入的插件 → 装完没功能，且不报错

`plugins/__init__.py` 用 `importlib.import_module` 拼字符串导入，**PyInstaller 的静态
分析看不到**。因此 `workbench/server.spec` 的 `hiddenimports` 里必须逐个列出。
漏了的表现是"开发态一切正常、安装包里工具少了几个、没有任何报错"。
（对外部 entry_points 插件，同类坑是"必须 `copy_metadata()`"，见
`docs/PLUGIN-UPSTREAM-RESEARCH.md` §R3 的公开实证。）

### 坑 2：`ToolContext.timeout_s` 的默认值会静默盖掉工具的 `timeout_s`

`ToolContext` 早期版本给 `timeout_s` 设了 `60.0` 默认值，于是 `wait_for` 永远用 60s，
**每个工具自己声明的超时全部失效**。实测：声明 0.05s 超时的探针工具照样跑完 0.5s。
现在它是 `float | None = None`（None = 用 `ToolSpec.timeout_s`）。
**教训**：宿主注入的上下文字段不要给"看起来合理"的默认值——它会静默覆盖插件的声明。

### 坑 3：确认门闭包捕获旧 store → "申请到 pendingId 却查不到"

`approvals.install(hub)` 早期写的是 `hub.set_approval_gate(pending_store().gate)`，
绑定的是**当时那个实例**；`reset_store()`（测试隔离用）之后中枢还捏着旧店，
表现为"申请到 pendingId 却查不到 / 批不了"。而且**只在测试里出现、生产看不出来**。
现在装的是 lambda（每次现取单例）。**教训**：注入闭包时，绑行为别绑实例。

---

## 8. 明确不做（以及什么条件下重新评估）

| 不做 | 研究结论 | 重新评估的触发器 |
|---|---|---|
| entry_points 自动发现 | "安装即可发现"的价值只在有第三方作者时成立（§0、§A4-6） | 真的要对外分发插件、或插件作者 > 1 人 |
| 独立进程插件 | 为"不可信第三方"买的崩溃隔离（§A4-3、§A2） | 插件依赖与宿主冲突、或插件来自第三方 |
| 运行时热插拔 | VS Code / Jenkins 量级的投入（§A4-6） | 不存在合理触发器 |
| 面向第三方的稳定 API 承诺 | Jenkins 到 2015 年才补弃用策略；承诺收不回（§A2、§A5） | 明确要开源 core 并招外部插件作者 |
| `pluggy` 之类 hook 框架 | 本项目扩展点 2–4 个、插件 1–3 个，收益不抵成本（§A2 补充判断） | 扩展点上到 10+ 个、且需要"多插件对同一动作按序介入" |

**另有一条纪律**：插件 API **现在不要承诺稳定性**。第二个插件（本轮的三个业务插件）
写完之前定契约必然返工——`docs/PLUGIN-UPSTREAM-RESEARCH.md` §R4 的原话是
"方案 A 完成后先跑 2–4 周真实迭代再定版本号"。

---

## 9. 相关文件与文档

| 位置 | 作用 |
|---|---|
| `workbench/plugin_api.py` | 契约：`ToolHub` / `ToolSpec` / `Risk` / `ToolContext` / `Plugin` |
| `workbench/approvals.py` | 确认门 + 审计写入器 + 人话摘要 |
| `workbench/plugins/*.py` | 五个插件的实现（`builtins` / `websearch` / `insight` / `factory` / `accounts`） |
| `workbench/api/routes_agent.py` | HTTP 面：目录 / 调用 / 待确认 / 审计 / 自检 |
| `workbench/harness/loop.py` | 对话循环：工具 schema 注入 + 事件流 + 审计 |
| `tests/test_plugin_api.py` | 契约回归（装配隔离 / 分级 / 令牌一次性 / 审计脱敏） |
| `tests/test_agent_loop.py` | 循环回归（事件序列 / 回填 / 确认门 / 预算 / 死循环） |
| `docs/API.md` §10 | 端点契约 |
| `docs/SCHEMA.md` | `agent_audit` / `pending_action` 表结构 |
| `docs/research/core-plugin-separation-research.md` | 为什么是这个形态（可行性 + 权利边界） |
| `docs/PLUGIN-UPSTREAM-RESEARCH.md` | 宿主/插件分离的先例、POC 与陷阱清单 |
