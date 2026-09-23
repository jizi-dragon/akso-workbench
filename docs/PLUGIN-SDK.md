# 插件 SDK 契约（Agent 核心的唯一公开扩展面）

_2026-09-15 定稿。锚定 `workbench/plugin_api.py`（`ToolHub`）+ `workbench/plugins/` + `workbench/approvals.py`。_

> **这份文档回答**：工作台的业务能力（对象分析 / 快捷登录）是怎么"作为工具插件
> 挂到 Agent 上"的，以及你新增一个插件时该动哪几个文件、哪些坑必须绕开。
> （2026-09-22 有两次变更，见 [README §四个来源项目的沉淀](../README.md)：
> 「平台洞察」**只改显示名**为「对象分析」，插件 id 仍是 `insight`；
> 原来的第三个业务插件「配置工厂」（`factory`）**整条链已删除**，
> 现在 core 只有 **4 个插件 / 18 个工具**。）

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

> **2026-09-17 重要修订：工具中枢分两层了。**
> 原文档写着"生产路径**没有热插拔**"——那条约定的前提是"插件全是产品自带的"。
> 用户能从**共享库**装 Skill 之后它不再成立，所以现在是：
>
> | 层 | 谁 | 何时变 | 能否卸载 |
> |---|---|---|---|
> | **core** | `plugins/__init__.py::_MODULES` 显式清单 | 导入期一次，**之后只读** | ❌ 不可卸载（可**禁用**） |
> | **installed** | 用户从共享库安装的 Skill（`workbench/skills/`） | **运行期可变** | ✅ 一键卸载 |
>
> **import 期"一次装配、之后只读"的纪律对 core 原样保留**：
> `register_installed` / `deregister_capability` / `set_plugin_enabled`
> 都拒绝改动 core 插件（传空 `capability_id` 会被直接拒——核心插件的它是空串，
> 早返回的实现会让守卫被静默绕过，这条有专门的回归断言）。
>
> **`_tools` 现在是派生视图**：由 `_rebuild()` 从"启用中的插件"重新推导，
> 是**唯一**允许写它的地方。所以"摘了插件但工具还在表里"在结构上不可能发生。
>
> **`tools/gen_tools_doc.py` 只数 core**（`hub.core_specs()`）：
> 文档承诺的是"产品自带多少工具"，与用户装了什么无关；
> installed 的数量由 `GET /api/skills/summary` 实时回答。
> 不这么分的话，**每台装过 Skill 的机器都会让那条文档门禁变红**。
>
> **installed 层有两种工具形态**（`SKILL.md` 里二选一，同时写会被拒）：
>
> | 形态 | 字段 | 执行方式 | 风险 |
> |---|---|---|---|
> | **声明式** | `wraps: <已有工具名>` | 转发给那个工具，**走同一个 `hub.execute`**（不绕过中枢，否则成了审计盲区） | 被包装工具与声明值的**较严**者（可 `read`） |
> | **脚本式** | `script: scripts/x.py` | 在**沙箱子进程**里跑包内 Python 脚本（`script_runner`） | **≥ `write`**，只升不降 |
>
> 脚本式的入口约定：stdin 收 JSON 入参 → stdout 出 `{"ok":…}` → stderr 只进日志 →
> **退出码优先于 stdout**。策略固定 `skill-script`，**不由 Skill 自己挑**
> （让上传件选策略等于让它给自己发权限）。详见 `workbench/skills/script_runner.py` 模块文档。
>
> **"二选一"是刻意的**：`wraps` + `script` 同时写的回退语义（"先转发、失败再跑脚本"）
> 会让失败路径不可预测，而本仓要的是**失败可见**。

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
| `description` | 必填，写清**后果** | 模型只靠它选工具。会写平台数据的工具必须写明"**会真的改平台数据**"——本仓的教训是一次误调用不会报错、却**会真的建出来**（当时的那个工具是 `factory_create`，2026-09-22 随配置工厂删除） |
| `args_model` | pydantic `BaseModel` 子类 | JSON Schema 由它生成（单一事实源，杜绝手写 schema 漂移）；模型传参先经它校验，字段级错误直接回给模型 |
| `handler` | `async (args, ctx) -> str | dict | BaseModel` | 同步阻塞调用（httpx/Playwright/子进程）**必须** `asyncio.to_thread` 挪走，否则阻塞事件循环 |
| `risk` | `READ` / `WRITE` / `DANGEROUS` | 决定是否过确认门。**默认 READ，写操作必须显式声明**。⚠️ **当前产品面一个 `DANGEROUS` 工具都没有**（唯一的那个已随配置工厂删除），但枚举与确认门的 dangerous 分支都**照旧保留**——删的是工具，不是分级能力 |
| `timeout_s` | 秒 | 工具不许无限期挂着。跑平台任务的给 300–900，纯本地查询 15–60 |
| `audit_extra` | `Callable[[args], dict]` | 抽审计维度（环境/对象/账号）。**不得含凭据**——`redact_args` 会再过一遍，但别指望它兜底 |
| `summary_fn` | `Callable[[args], str]`，可选 | **确认框里给用户看的那一句**（默认口径是"工具描述 + 参数 + 风险级"）。⚠️ **它目前没有任何产品内调用方**——唯一的使用者 `plugins/factory.py::_create_summary` 已随配置工厂删除（2026-09-22）。**机制刻意保留**（属于 SDK 公开面），由 `tests/test_plugin_api.py` 用**合成 spec** 钉住；文案形状约定（首行标题 + `·` 要点）见 `plugin_api.ToolSpec.summary_fn` 的文档串 |

工具名命名约定：`<plugin_id>_<动作>`。内置工具（`get_current_time` 等）是历史例外。

---

## 3. 现有四个插件与 18 个工具

> 实时清单以运行期 `get_hub()` 实例为准（`len(hub.plugins)` / `len(hub.specs())`），
> 或直接看 **[docs/TOOLS.md](TOOLS.md)**（由 `tools/gen_tools_doc.py` 从运行期生成，`tests/test_tools_doc.py` 钉住）。
> 2026-09-15 引入 `web_search` 时曾漏把它写进本表（`builtin` 之外），故此后**以运行期为准**；
> 本表为 2026-09-17 快照 + 2026-09-20 补 `request_user_confirmation` + **2026-09-22 删 `factory` 插件、补两个 `attachment_*`**。

| 插件 | 工具 | 风险 |
|---|---|---|
| `builtin`（通用） | `get_current_time` / `read_local_file` / `read_web_page` / `attachment_list` / `attachment_read` | read |
| | `request_user_confirmation`（**模型主动请求实时确认**，见下方专节） | read（但会**暂停对话**等用户答复） |
| `websearch`（自建联网检索） | `web_search`（DeepSeek API **没有**内置联网搜索，见 [research](research/deepseek-api-capabilities.md) §2） | read |
| `insight`（对象分析） | `insight_accounts` / `insight_read_config` / `insight_run` / `insight_artifacts` / `insight_read_artifact` / `insight_runs` | 全 read |
| `accounts`（快捷登录） | `accounts_list` / `accounts_login` / `accounts_browser_sessions` | read |
| | `accounts_browser_open` / `accounts_browser_close` | **write** |

统计：6 + 1 + 6 + 5 = **18 个工具 / 4 个插件**（**16 read + 2 write + 0 dangerous**）。

> **`factory` 插件（7 个 `factory_*` 工具，含唯一一个 `DANGEROUS`）已于 2026-09-22 随配置工厂整体删除**——
> 见 [README](../README.md) 的「配置工厂整体移除」一节。平台侧知识仍在
> [docs/PLATFORM-OPERATIONS-LEARNED.md](PLATFORM-OPERATIONS-LEARNED.md)。

实时清单：`GET /api/agent/tools`（含参数说明与风险）；插件维度：`GET /api/agent/plugins`。

### `websearch` 为什么是独立插件（而不是并进 `builtin`）

DeepSeek API **没有**内置联网检索（见 [research/deepseek-api-capabilities.md](research/deepseek-api-capabilities.md)），
所以 `web_search` 是自建能力，且**自带外部依赖与失败模式**（检索源不可用、结果需要降级）。
把它与"读时间/读文件"这类零依赖工具放在同一个插件里，会让"检索坏了"和"通用工具坏了"难以区分。
**插件边界按"依赖与失败域"划，不按"看起来像不像一类"划。**

### 各插件的设计取舍（都踩过或刻意避开了坑）

**`insight`：不提供"列出平台全部对象"。** 平台没有实证过的对象清单端点
（`kernel/endpoints.py:74-77` 那三条探针是上游 README 给的，可用性需按环境实测、可能全 404）。
CODEBASE_OVERVIEW 的红线是「端点常量必须实证来源，不许凭观察猜」——所以宁可不给这个能力，
让模型先问用户对象编码、再用 `insight_read_config` 验证。

**`insight_run` 复用页面同一条入口**（`api.routes_insight.run`），不另写一套 spider 调用。
绕过去会漏掉 `workspace_dir` / `coverage_status` 回填（2026-09-14 修的真实缺陷），
后果是"页面上明明有 8 件产物、界面只显示一个 proc.log"。**插件层图省事的代价由用户承担。**

**`accounts_browser_open` / `accounts_browser_close` 是本插件里仅有的两个 WRITE 工具**，
其余三个（`accounts_list` / `accounts_login` / `accounts_browser_sessions`）都是 read。

> **~~`factory_create` 是 DANGEROUS 而不是 WRITE~~（2026-09-22 已删除）**：它做的是一次误调用不会报错、
> **会真的建出来**的批量写入，回滚只能平台侧手工删。它与引擎侧"环境强制确认"
> （`routes_factory.run` 的 `confirmed=true`，否则 409）是**两条独立的闸门**，不是替代关系：
> 工具级确认门挡住 Agent 链路，引擎侧闸门挡住"绕过页面直接打 HTTP"的人。
> **删除后的后果**：那条引擎侧闸门随功能一起没了，产品面现在 **0 个 DANGEROUS 工具**——
> 工具级确认门是唯一还站着的那道；`Risk` 枚举与确认门的 dangerous 分支**照旧保留**
> （保留原文是为了让"为什么会有 DANGEROUS 这一档"可追溯）。

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
  → 对话链路：这一轮**就地等答复** → 放行 ⇒ 服务端用同参数重投（按指纹认领）→ 真正执行
  → HTTP 链路：放行 ⇒ 兑换**单次令牌**（一次性 / 5 分钟 / 绑定工具+参数指纹）→ 带令牌重投
  → 拒绝/超时 → 记 rejected/expired → 模型看到理由，不许重试
```

### 两条链路的走法

| 链路 | 拦截表现 | 放行动作 |
|---|---|---|
| 对话（SSE） | 事件 `approval_required`（随后 `confirm_resolved`），**这一轮停在等待里** | 界面按钮 → `POST /api/agent/pending/{id}/approve` → **服务端**用相同参数执行并把真实结果喂回模型（用户只点一次，不必让模型重试） |
| HTTP | `409` + `detail.pendingId/risk/summary` | `POST .../approve` 拿 `approvalToken` → 带令牌重投 `POST /api/agent/invoke` |

### 前置检查（`ToolSpec.precheck`，可选，2026-09-22 加）

只读、**在确认门之前**跑的确定性检查：返回非空串 = **这次调用注定失败** ⇒
**不问用户放行、也不执行**，理由原样成为模型观测（`status=rejected`）。
它买的是"别让用户为一次必然失败的写操作白点一次放行"（**真机现场来自已删除的配置工厂**：
"对象不存在但有相近对象"）。**失败即放行**（异常/超时按没意见处理）——
它只省一次白问，拦截责任仍在确认门与工具自己。

> ⚠️ **2026-09-22：与 `summary_fn` 同样，这个扩展点当前也没有产品内调用方**——
> 它当时唯一的用户是已删除的 `plugins/factory.py`。机制保留（属于 SDK 公开面），
> 由 `tests/test_plugin_api.py` 用**合成 spec**（`risk=pa.Risk.DANGEROUS`）钉住。

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

### 4.1 `needs_confirmation`：**模型**发起的实时确认（与上面的门是两套东西）

上面那套的发起者是**宿主**（按 `risk` 声明自动拦）。`needs_confirmation` 的发起者是**模型**
——它主动调用 `request_user_confirmation` 来问用户。完整的决策与外部参照见
[ADR-0016](adr/0016-model-initiated-confirmation.md)，这里只讲**怎么用**。

```python
class MyConfirmArgs(BaseModel):
    question: str = pa.Arg("要向用户确认的问题")
    target_tool: str = pa.Arg("将要执行的工具名", default="")
    target_args: dict[str, Any] = pa.Arg("传给它的参数", default_factory=dict)


def my_payload(args: MyConfirmArgs) -> dict[str, Any]:
    """从参数里抽出**给用户看的**内容（可含 targetTool / targetArgs / timeoutS）。"""
    return {"question": args.question,
            "targetTool": args.target_tool, "targetArgs": args.target_args}


pa.ToolSpec(
    name="my_confirm",  # 名字随意，只要满足工具名规则
    description="……",
    args_model=MyConfirmArgs,
    handler=some_handler,       # ⚠️ **永远不会被调用**，见下
    risk=pa.Risk.READ,          # ⚠️ **必须**是 read，装配期强制
    needs_confirmation=True,
    confirm_payload=my_payload,
)
```

**四条必须知道的规矩**：

1. **`risk` 必须是 `read`**。否则它会先过自己的确认门 → 用户被问"要不要问你要不要确认" → 无限递归。
   `ToolSpec.valid()` 在**装配期**就拒（不是运行期才发现）。
2. **`handler` 永远不会被调用**。`needs_confirmation=True` 的工具由 `ToolHub.execute` 直接拦下，
   用户的答复本身就是结果（`harness/loop.py::_apply_confirmation` 把它变成终态观测）。
   handler 只是满足必填；**真被执行到说明拦截路径坏了，它必须抛异常**——
   返回一句"已确认"会让模型以为用户真的同意了，而那正是这个工具唯一要防的事。
3. **`targetTool` / `targetArgs` 由宿主校验，插件说了不算**。目标必须存在、必须是 write/dangerous、
   不能自己也要确认（不能嵌套），参数用**目标自己的 `args_model`** 校验。
   任何一条不满足 → `BAD_ARGS`，**不降级**成"纯意图确认"（降级 = 用户以为批准了 A，实际什么都没批准）。
4. **等待发生在循环层**（`harness/loop.py`），不在 handler 里——`execute()` 一旦 `await`，
   事件就发不出去了，界面不知道有东西要确认。所以 `execute()` 只登记，返回
   `ToolStatus.NEEDS_CONFIRMATION` + `Invocation.confirm`。

**它买的是什么（别读成安全属性）**：模型**可以先问再做**。风险门买的是**不做就不能做**。
模型不调用这个工具就绕过去了——所以它**不能**当高风险调用的放行依据，两者是互补而非替代。

带 `targetTool` 时，用户同意的是**那一次具体调用**（按「工具 + 参数指纹」绑定，一次性，
与风险门共用 `claim_approved()` 通道）；模型换个参数再调，指纹对不上，照样重新走风险门。

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
_MODULES = ("builtins", "websearch", "insight", "accounts")   # ← 加插件改这里（当前 4 个）

def load_all(hub):
    for name in _MODULES:
        try:
            importlib.import_module(f"{__name__}.{name}").register(hub)
        except Exception as exc:      # 坏插件只记一条 failure，宿主照常启动
            hub.mark_load_failure(name, f"{type(exc).__name__}: {exc}")
```

**为什么坏插件必须被隔离**：插件是"可选能力"。一个插件 import 失败就崩掉整个应用，
等于让"对象分析装不上"升级成"工作台打不开"。

**为什么隔离了还必须自检**：隔离的另一面是**静默**——"应用照常启动，但某个功能没了"。
所以启动期把必需工具清单对一遍并写日志，同时暴露成 `GET /api/agent/selfcheck`：

```
{"ok":true,"plugins":4,"tools":18,"riskCounts":{...},
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
| 独立进程**插件** | 为"不可信第三方"买的崩溃隔离（§A4-3、§A2） | 插件依赖与宿主冲突、或插件来自第三方 |
| 运行时热插拔 | VS Code / Jenkins 量级的投入（§A4-6） | 不存在合理触发器 |
| 面向第三方的稳定 API 承诺 | Jenkins 到 2015 年才补弃用策略；承诺收不回（§A2、§A5） | 明确要开源 core 并招外部插件作者 |
| `pluggy` 之类 hook 框架 | 本项目扩展点 2–4 个、插件 1–3 个，收益不抵成本（§A2 补充判断） | 扩展点上到 10+ 个、且需要"多插件对同一动作按序介入" |

**另有一条纪律**：插件 API **现在不要承诺稳定性**。第二个插件（本轮的三个业务插件）
写完之前定契约必然返工——`docs/PLUGIN-UPSTREAM-RESEARCH.md` §R4 的原话是
"方案 A 完成后先跑 2–4 周真实迭代再定版本号"。

> **两条与 installed 层有关的澄清（免得把上面的"不做"读成"没有"）**：
> ① **"独立进程插件"仍然不做**——core 插件照旧在宿主进程内。Skill 的**脚本式工具**
> 确实在独立子进程里跑，但那是**一次性执行**（跑完即走、进程树可终止），
> 不是"插件宿主 + 常驻进程 + 崩溃隔离"那一整套；
> ② **热插拔只对 installed 层开放**。core 仍是"导入期一次装配、之后只读"，
> 因为它的前提（产品自带的代码不会被换掉）没有变。

---

## 9. 相关文件与文档

| 位置 | 作用 |
|---|---|
| `workbench/plugin_api.py` | 契约：`ToolHub` / `ToolSpec` / `Risk` / `ToolContext` / `Plugin` |
| `workbench/approvals.py` | 确认门 + 审计写入器 + 人话摘要 |
| `workbench/plugins/*.py` | 四个插件的实现（`builtins` / `websearch` / `insight` / `accounts`） |
| `workbench/skills/library.py` | installed 层：上传 / 安装 / 卸载 / 从库删除；两种工具形态各自建 `ToolSpec` |
| `workbench/skills/manifest.py` | `SKILL.md` 契约：两种形态、路径校验、JSON Schema → pydantic |
| `workbench/skills/script_runner.py` | 脚本式工具的入口约定 + 沙箱调用 + 结果翻译（Fail-Closed） |
| `workbench/sandbox/` | 执行底座（ADR-0014）：策略 / 作业对象 / 运行器 / 审计 |
| `workbench/api/routes_agent.py` | HTTP 面：目录 / 调用 / 待确认 / 审计 / 自检 |
| `workbench/api/routes_skills.py` | HTTP 面：共享库 / 安装 / 卸载 / 停用 / Skill 审计 |
| `workbench/harness/loop.py` | 对话循环：工具 schema 注入 + 事件流 + 审计 |
| `tests/test_plugin_api.py` | 契约回归（装配隔离 / 分级 / 令牌一次性 / 审计脱敏） |
| `tests/test_skills.py` | installed 层回归（格式契约 / ZIP 准入 / 三条不变量） |
| `tests/test_skill_scripts.py` | 脚本式回归（入口约定 / 路径 / 风险下限 / **真起子进程**的端到端） |
| `tests/test_sandbox.py` | 沙箱回归（策略 / 环境白名单 / 进程树终止 / 超时） |
| `tests/test_agent_loop.py` | 循环回归（事件序列 / 回填 / 确认门 / 预算 / 死循环） |
| `docs/API.md` §10 | 端点契约 |
| `docs/SCHEMA.md` | `agent_audit` / `pending_action` 表结构 |
| `docs/research/core-plugin-separation-research.md` | 为什么是这个形态（可行性 + 权利边界） |
| `docs/PLUGIN-UPSTREAM-RESEARCH.md` | 宿主/插件分离的先例、POC 与陷阱清单 |
