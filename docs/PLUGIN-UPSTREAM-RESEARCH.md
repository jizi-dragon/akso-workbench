# 调研：把业务能力剥离为插件、把通用能力抽成可复用框架

- **状态**：调研完成（2026-09-14 v2），结论可执行
- **调研对象**：本仓库 `akso-mogul` 的模块边界、可复用性、剥离成本
- **方法**：静态耦合量化（AST 逐 import 解析）+ 可执行 POC（宿主/插件最小实现并实跑）+ 外部先例查证
- **目标（修订）**：把**可复用的框架部分**整理出来，**提前规避"平台知识混进框架"的风险**，
  使框架能干净复用到下一个产品。**不涉及权属问题**（权属已明确归本人，见 §1 修订说明）

> 相关文档：**[docs/FRAMEWORK-INVENTORY.md](FRAMEWORK-INVENTORY.md)**（框架清单——**本文的姊妹篇，先读那份**）、
> [docs/CODEBASE_OVERVIEW.md](CODEBASE_OVERVIEW.md)（代码地图）、
> [docs/adr/0005-native-internalization-of-four-projects.md](adr/0005-native-internalization-of-four-projects.md)（能力内化史）

---

## 1. 结论（先给答案）

### 1.1 修订说明：本文 v1 答错了题

v1 版本花了整节（原 §6）分析"职务作品/委托作品的著作权归属"，并建议"先做 0.5 人日权利体检"。

**该前提是错的**：任务发起人已明确**产权归本人**，需求是"**提前规避风险 + 整理出可复用的框架部分**"。
所以：
- ❌ 原 §6「权利边界」整节删除（它解决的是不存在的问题）；
- ✅ 换成了真正的风险：**平台知识混进框架**（下文 §6）——这个风险不仅存在，
  而且**会随着每次功能迭代持续恶化**，且有技术手段可以门禁化。

**这个错误的教训值得记录**：我在信息不足时把"风险"默认解读成了"法律风险"，
而没有先问清"你担心的风险具体是什么"。**重构文档前应该先确认需求前提。**

### 1.2 修订后的结论

| 问题 | 结论 |
|---|---|
| **能抽出干净的框架吗** | **能，而且成本比预想低**。框架实测 **12,069 行**，其中与业务之间的**代码耦合只有 2 个文件 3 处**（见 [FRAMEWORK-INVENTORY](FRAMEWORK-INVENTORY.md) §4 类型 2）。POC 已实跑通过（§3） |
| **真正要防范的风险** | **不是"代码混在一起"，而是"平台知识渗进框架文件"**——它不会报错、不会失败，只会让框架在下一个产品上跑不通或泄露前客户信息。必须做成 CI 门禁（§6 / 清单 §6） |
| **最该先做的事** | **参数化硬编码标识（1 人日）**：数据目录名与三个端口如果不参数化，第二个产品会与第一个**争抢同一个 `%APPDATA%` 与端口**（表现为数据串台/起不来）。这是唯一有"不做就出事"性质的一步 |
| **整体时间耗费** | **框架化 6 人日**（含参数化/扩展点/领域外置/独立运行验证/CI 门禁）；<br>若还要"业务可独立演进"（插件化）另加 **3-5 人日**；<br>若要把框架做成对外开源的上游产品，另需 **+25-40 人日**（跨平台、无业务数据的测试、文档与合规件） |
| **拆不拆仓** | **不急**。目标（框架可复用 + 知识不外泄）**在同仓内就能达成**，靠"文件清单 + CI 门禁"约束即可。拆仓是**可选的后期动作**，收益主要是仪式感与发布独立性，不是技术必需 |

**一句话**：这个仓库**本来就长成了"宿主 + 平台适配层"的形状**，只是没起名字。
你要做的不是大重构，而是**给边界命名、把 3 处耦合点改成扩展点、给 5 类硬编码做参数化**，
然后用一道 CI 门禁把它焊死。

---

## 2. 现状实测：谁是谁

### 2.1 代码规模（AST 实测，非估算）

| 层 | 文件 | 行数 | 性质 |
|---|---|---|---|
| `workbench/services/egmp/` | 41 | **5,855** | **公司平台专属**：eGMP 的端点/语义/写路径/读懂/录制回放/编排 |
| `workbench/api/` | 13 | 2,034 | 路由域（其中 3 个域是公司专属：insight/factory/agent） |
| `workbench/services/`（非 egmp） | 10 | 2,440 | 账号库 / 托管浏览器 / 自动登录 / 存储 / 设置 / DeepSeek / 模块体检 / 路径 |
| `workbench/harness/` | 7 | 533 | **候选核心**：Agent 循环 + 工具注册表 + 上下文裁剪 + 步数门控 |
| `workbench/` 根（`db.py`/`config.py`/入口） | 5 | 519 | 数据层与启动 |
| **Python 合计** | **76** | **11,381** | |
| `workbench/static/`（前端） | 24 | 5,360 | 原生 ES 模块 + CSS，无构建 |
| `desktop/`（Electron 壳） | 4 | 1,002 | 窗口/托盘/热键/自动更新/出网通道/CDP 会话窗 |
| `extensions/quick-login/`（扩展 TS） | 23 | 5,514 | 六平面隔离 + 自动填表 + 桌面同步桥 |

> ⚠️ **第一个反直觉的事实**：**公司专属部分（egmp 5,855 行）比"核心候选"（harness+services+api+root 5,526 行）还大**。
> 也就是说这个仓库目前的主体**就是**那家公司的业务实现。这直接推翻"我有主站、公司东西是配角"的假设
> ——**真正从零积累的通用资产只有 ~1,500 行（harness + 存储 + 浏览器池），其余都是这家公司的知识**。

### 2.2 耦合边界（决定剥离难度的唯一硬指标）

**方向一：核心 → 公司专属（宿主被平台知识侵入的入口）—— 共 10 处，4 个文件**

| 文件 | 依赖的 egmp 模块 |
|---|---|
| `workbench/api/routes_agent.py` | `egmp`, `egmp.client` |
| `workbench/api/routes_factory.py` | `egmp`, `egmp.client`, `egmp.generate`, `egmp.writers.blueprint` |
| `workbench/api/routes_insight.py` | `egmp`, `egmp.client`, `egmp.insight` |
| `workbench/services/browser_pool.py` | `egmp.monitor`（仅 1 处：`monitor_start` 里挂录制引擎） |

**方向二：公司专属 → 核心（公司层向宿主索要什么）—— 共 4 处**

| egmp 侧 | 索要的核心能力 |
|---|---|
| `egmp/client.py` | `workbench`（版本号 `__version__`） |
| `egmp/insight/understand.py`、`egmp/insight/workspace.py` | `services.paths`（数据目录/产物路径） |
| `egmp/insight/workspace.py` | `services.storage`（读写 `settings`） |
| `egmp/orchestrate.py` | `egmp.queries`（平台查询缓存；**注意它在 egmp 内部，位置本来就对**） |

**边界质量评估：优秀。** 依赖方向清晰、全是静态 import、没有任何 `importlib`/字符串反射的隐藏耦合。
这使得"把 egmp 移出仓库"在**技术上只是一次机械重构**。

### 2.3 前端 / 壳 / 扩展的归属

| 资产 | 平台专属程度 | 证据 |
|---|---|---|
| **前端**（5,360 行） | **几乎零专属** | 全量扫描平台标识（`aksoegmp`/`egmp`/租户名/对象编码）：**24 个文件中一处都没有**。仅 `index.html`/`insight.html`/`factory.html` 出现功能名（"平台洞察"）与「平台」二字 |
| **Electron 壳**（1,002 行） | **几乎零专属** | 4 个文件里只有产品名 `Akso` 共 31 处（字符串），**没有任何平台 API 知识**。窗口/托盘/热键/自动更新/代理选择/CDP 会话窗全部是通用能力 |
| **扩展**（5,514 行） | **几乎零专属** | 仅 `sync.ts` 的"akso"×11（协议前缀与桌面地址）、几处注释提到"低代码"。六平面隔离、Cookie 回放、IndexedDB 命名空间全是通用多账号技术 |

> ⚠️ **第二个反直觉的事实**：**你真正的可复用资产不是 agent 循环，而是"桌面分发基础设施"**：
> Electron 壳 + 自动更新（GitHub Releases 全链路）+ NSIS 打包 + 浏览器扩展执行面 + 账号库 + 托管浏览器。
> 这套东西与行业无关，任何"需要在客户现场以多账号操作某个 Web 平台"的产品都能直接复用，
> 而 `harness/`（533 行 agent 循环）体量小、且其中 `prompts.py` 的内容（药企合规）本身就是这家公司的领域知识。
>
> 详见 §4.2 的资产重估表。

### 2.4 平台专属知识的真实分布（用"平台标识"扫描）

扫描 `capa_plan` / `__c` / `pageIndex` / `FieldPage` / `BasicObject` / `Workflow` / `lifecycle` / `Gaia` 等平台特征词：

- **`egmp/` 内：密集**（`kernel/endpoints.py` 里 `Workflow`×33、`BasicObject`×11；`writers/blueprint.py` 里 `lifecycle`×21、`__c`×11）→ 这是核心知识资产，也是最敏感的部分；
- **`egmp/` 外：几乎没有实质知识**。`routes_agent.py` 出现 `capa_plan`/`__c` 各 1 次（docstring 举例），
  `services/paths.py` 出现 1 次（产物命名）；其余命中的 "akso" 全是产品名而非平台语义。

**⇒ 剥离的切割线非常干净：`workbench/services/egmp/` 整棵子树 + 3 个路由域 + `browser_pool` 里 1 个钩子。**

---

## 3. 可行性验证：POC 实跑（不是纸上推演）

我搭了最小宿主/插件实现并真实启动 uvicorn 验证，等价于把 `egmp` 换成插件后的结构：

```
host/core.py            宿主：Registry（声明式注册表）+ discover(entry_points) + create_app()
plugin_a/main.py        模拟"平台洞察"：register(reg) 里加 router + 导航项 + 提供扩展点
plugin_b/main.py        故意坏掉（模拟缺平台 SDK）
各自 *.dist-info/entry_points.txt   [akso.plugins] plugin_a = plugin_a.main
```

**实测结果**

| 验证点 | 结果 |
|---|---|
| `importlib.metadata.entry_points` 发现插件 | ✅ 2 个全部发现 |
| **坏插件不拖垮宿主** | ✅ `plugin_b` 抛 `RuntimeError` 被 `try/except` 隔离并打印告警，宿主照常启动 |
| 插件路由真的装进宿主 | ✅ `GET /api/insight/runs` → `{"plugin":"insight","runs":[{"id":"r1"}]}` |
| 插件向前端贡献导航 | ✅ `GET /api/plugins` 返回 `nav:[{label:"平台洞察",href:...,order:10}]` |
| 宿主反向取用插件能力（依赖倒置） | ✅ `registry.get("insight.runner") is not None` → True |
| 坏插件是否进入已加载清单 | ✅ 未进入（`'plugin_b' not in plugins`） |

**POC 暴露的三个真实陷阱（必须在插件 SDK 契约里写明）**

1. **`entry_points` 的发现强依赖 `sys.path`** —— 第一次跑就是"一个插件都没发现"。
   真实场景靠 pip/uv 安装（`.dist-info` 落进 site-packages）解决；
   但**开发态**用 `uv sync` 可编辑安装时要注意 editable 安装的 entry point 是否可见。
2. **路由前缀二义性**：插件 `APIRouter(prefix="/api/insight")` 而宿主又 `include_router(..., prefix="/api/insight")`
   → 变成 `/api/insight/api/insight/runs`。**契约必须二选一，且写死在 SDK 文档里**。
3. **扩展点必须显式声明**：`reg.provide(name, impl)` / `reg.get(name)` 这种字符串键没有类型检查，
   插件与宿主对不上时是**静默 None**。建议宿主侧加"必需扩展点存在性自检"（启动时断言）。

**POC 结论**：FastAPI + `importlib.metadata` 这条路线**完全可用**，没有阻塞性技术障碍。
工作量主要花在"把 4 个文件的直连 import 改成经 Registry 解析"这件机械活上。

---

## 4. 「主站剥离成上游」这件事的真实含义与三种理解

你说的"把主站（agent 核心）剥离出来作为上游项目/依赖"，存在三种差异极大的解读——
**选错解读会导致做了一堆无用功**。这不是抠字眼，因为三种解读的代码归属完全不同：

### 4.1 三种解读对照

| 解读 | 上游（你保留、可复用/可开源） | 下游（交付给公司的） | 现实性 |
|---|---|---|---|
| **T1「Agent 框架」** | `harness/`（533 行）+ LLM 客户端 | egmp 全部 + 桌面壳 + 前端 | ❌ **价值不大**：533 行、且 `prompts.py` 里是药企合规知识；业界早有 LangGraph/OpenAI Agents 等成熟替代 |
| **T2「桌面分发平台」** | `desktop/` + `extensions/` + `services/{accounts,browser_pool,autologin,storage,paths}` + `db.py` + 通用前端骨架 + 发布链（≈7,000 行，**几乎零公司知识**） | `egmp/` + 3 个路由域 + 洞察/工厂前端页 | ✅ **这才是你真正积累的资产**，且行业无关、复用价值最高 |
| **T3「两者都要」** | T2 的平台能力 + 广义 Agent 编排运行时（需重写 harness 使其无领域知识） | 公司专属插件 | ✅ 长期形态，但工作量最大；建议作为 6 个月后的目标 |

> **我的建议**：按 **T2 起步**（T3 作为演进目标）。理由见 §4.2。

### 4.2 资产重估表（按"与行业无关程度 × 复用价值"排序）

| 资产 | 与公司/行业无关 | 复用价值 | 剥离成本 | 判断 |
|---|---|---|---|---|
| **NSIS 打包 + electron-updater 自动更新全链路** | 高（只有 `owner`/`repo` 两处参数） | **高**：这套东西自研一遍要 1-2 周，且坑极多（本项目实测踩过"能发现新版但下载不动"、代理通道翻转等） | **极低**（改 2 个常量） | **优先上移** |
| **Electron 壳骨架**（窗口/托盘/热键/透明轮盘/会话窗/出网通道） | 高 | 高 | 低（改名 31 处 + 抽出配置） | **优先上移** |
| **浏览器扩展执行面**（六平面隔离/多账号/Cookie 回放/自动填表） | 高（仅 `host.ts` 的口径函数按站点配置） | 高，且业界同类闭源居多 | 中（协议面已抽象，但要把它与桌面同步协议解耦或一并上移） | **优先上移** |
| **账号库 + 托管浏览器 + 自动登录节奏门控** | 中高（自动登录的"五重门"是针对 AntD 形态登录页调出来的，但参数化后通用） | 高 | 中（需把登录页特征抽成配置/插件） | 第二优先 |
| **数据层**（SQLite + 迁移机制 + 备份导入导出） | 高 | 中 | 低 | 随核心上移 |
| **harness Agent 循环** | 中（循环本身通用，提示词不通用） | 中 | 低 | 上移但**必须把 `prompts.py` 领域内容抽出** |
| **`egmp/`** | 极低 | 对本项目高 / 对外为零 | 不动 | **留在下游** |
| **洞察/工厂前端页** | 低（强绑定平台概念） | 低 | 不动 | **留在下游** |

### 4.3 「插件形式更新主要功能」的粒度选择

"以插件形式更新"有三种粒度，成本差一个数量级：

| 粒度 | 做法 | 适用 |
|---|---|---|
| **A. 进程内插件包**（POC 验证的这条） | 下游仓库里放 `plugins/`，用 entry_points 或显式清单注册；宿主同一进程加载 | ✅ **推荐起步**：改动最小、调试最方便、无需跨进程协议 |
| **B. 独立进程插件** | 插件是独立可执行程序，经 HTTP/JSON-RPC 与宿主通信 | 需要技术隔离（如插件的依赖与宿主冲突）或第三方不可信插件时才值得 |
| **C. 真正的动态插件市场** | 版本协商、沙箱、热插拔、向后兼容矩阵 | ❌ **单人/小团队不要碰**：这是 VS Code/Jenkins 量级的投入 |

> **判断依据**：你现在只有"公司专属"和"我的通用能力"两方，且都由你维护。
> 插件化的收益是**边界清晰**（便于权利切割与后续复用），不是"生态"。目标对齐 A 即可。

---

## 5. 实施方案与工作量

### 5.0 第 0 步（**已由发起人明确，无需再做**）

v1 版本把"确认著作权归属"列为第 0 步（0.5 人日）。**发起人已明确产权归本人**，
故这一步取消。**真正该先做的是参数化硬编码标识**（[清单](FRAMEWORK-INVENTORY.md) §4 类型 1，1 人日）——
它是唯一"不做就出故障"的一项：数据目录名与三个端口不参数化，
第二个产品会与第一个争抢同一个 `%APPDATA%` 目录与同一批端口。

> **这里保留一条元教训**：需求前提（"风险"指什么）比技术方案更该先确认。
> v1 把"风险"默认解读为法律风险，做完才发现要的是工程风险。**先问清前提，再动手。**

### 5.1 方案 A：最小可用剥离（**3-5 人日**）

目标：把 egmp 做成"可整体拆除的插件"，但**暂不拆仓库**（同仓两个包，边界靠 import 纪律守住）。

| 步骤 | 内容 | 人日 |
|---|---|---|
| A1 | 抽 `workbench/plugin_api.py`：定义 `Registry`（`add_router`/`add_nav`/`provide`/`get`）+ `Plugin` 协议 + 必需扩展点自检 | 0.5 |
| A2 | 把 4 个文件的 10 处 `import egmp` 改为**启动时解析**（`registry.get("insight.runner")` 等）；`browser_pool` 的 monitor 钩子同样改为扩展点 | 1.0 |
| A3 | `egmp` 增加 `plugin.py` 实现 `register(reg)`：把 `routes_insight`/`routes_factory`/`routes_agent` 三个域搬进插件侧（或保留在核心但改为经 registry 调用） | 1.0 |
| A4 | ~~`services/queries.py` 归位到 `egmp/`~~ → **核查后不需要**：查询缓存本来就在 `egmp/queries.py`，位置已正确 | 0 |
| A5 | `services/paths.py` 里 `capa_plan` 等平台命名抽成配置（平台产物命名规范） | 0.3 |
| A6 | 回归：`pytest`（330 项）+ 打包体检 + 真机冒烟；补一条"插件必需扩展点存在性"的测试 | 1.0 |
| A7 | 文档：插件 SDK 契约（含 POC 那三个陷阱）、如何新增插件 | 0.5 |

**交付后能达到的效果**：egmp 变成"可删除的插件"，删掉它宿主仍能启动（功能自然缺失）。
这一步就足以支撑"权利边界在物理上可切割"的论证。

#### 5.1.1 接口设计草案（POC 已验证形状，可直接评审）

```python
# workbench/plugin_api.py —— 上游（通用，不含任何平台知识）
from typing import Protocol, Any
from fastapi import APIRouter

class Registry:
    """插件唯一能碰到宿主的入口。宿主只暴露能力句柄，不暴露实现。"""
    def add_router(self, router: APIRouter) -> None:      # 注意：router 自带 prefix，宿主不加
        ...
    def add_nav(self, label: str, href: str, order: int = 100) -> None:
        ...
    def provide(self, name: str, impl: Any) -> None:      # 插件向宿主提供能力
        ...
    def get(self, name: str) -> Any:                      # 插件向宿主索要能力
        ...
    def require(self, *names: str) -> None:               # 启动自检：缺扩展点直接报错
        ...

class Plugin(Protocol):
    def register(self, reg: Registry) -> None: ...
```

```python
# 下游插件：egmp_plugin/plugin.py —— 含平台知识，留在公司仓库
from fastapi import APIRouter
from . import routes_insight, routes_factory, routes_agent

router = APIRouter(prefix="/api/insight")   # 自带前缀，宿主不再加（POC 陷阱 #2）

@router.get("/runs")
def list_runs(): ...

def register(reg) -> None:
    reg.add_router(router)
    reg.add_nav("平台洞察", "/static/pages/insight.html", order=10)
    reg.provide("egmp.insight.runner", run)        # 宿主经 get("egmp.insight.runner") 取用
    reg.require("storage", "paths", "browser_pool")  # 启动即校验宿主能力齐全
```

**契约里必须写死的 5 条**（每条都来自实跑踩坑，不是设计洁癖）：
1. **前缀归属**：插件 `APIRouter` 自带 `prefix`，宿主 `include_router()` 不再传 prefix（
   否则出现 `/api/insight/api/insight/runs`）。
2. **发现机制**：`[project.entry-points."akso.plugins"]` 分组；打包时必须 `copy_metadata()`
   （缺 metadata → 静默发现不到）。
3. **隔离**：单个插件 `register()` 抛异常 → 记录并跳过，**绝不阻断宿主启动**（POC 已验证）。
4. **自检**：`require()` 的扩展点在启动时校验，**禁止静默 None**（否则表现为"功能消失但不报错"）。
5. **零领域知识**：上游（`plugin_api.py` 与宿主）内**不得**出现任何平台标识——
   建议加 CI 门禁做特征词扫描（完整特征词表与噪音说明见 [FRAMEWORK-INVENTORY](FRAMEWORK-INVENTORY.md) §6）。

### 5.2 方案 B：双仓化（**+8-15 人日**）

| 步骤 | 内容 | 人日 |
|---|---|---|
| B1 | 建上游仓库 `workbench-core`（暂定名）：迁入 T2 资产（壳/扩展/通用服务/数据层/前端骨架） | 2-3 |
| B2 | 泛化：`Akso` 产品名 31 处 → 配置项；`package.json` 的 `owner`/`repo`/`appId` → 构建参数；扩展的桌面地址 → 构建常量 | 1-2 |
| B3 | 定义**上游 ↔ 下游的接口版本**（这就是 POC 的"必需扩展点"清单）：语义化版本 + 启动自检 | 1 |
| B4 | 下游改为依赖上游包（**已实测可行**：`uv` 原生支持 git / path / editable 依赖与 workspace，`uv add --git`、`--path`、`--editable`、`--workspace` 均在帮助中确认） | 1-2 |
| B5 | 打包链改造：PyInstaller spec 要能收下游插件（**已实测有现成工具**，见下） | 1-2 |
| B6 | 双仓 CI/回归：上游跑通用测试；下游跑集成（插件 + 宿主） | 1-2 |
| B7 | 文档与交接：`docs/` 拆分、`CHANGELOG` 拆分（本仓库已有"上游 CHANGELOG vs 本项目 CHANGELOG"的成熟先例） | 1-2 |

### 5.3 方案 C：做成对外可开源的通用上游（**+25-40 人日**）

在 B 之上追加（**这些才是"开源一个产品"的真实成本**）：
- 无平台数据的测试夹具（当前 `tests/fixtures/egmp/` 是平台知识，必须做出替代夹具）——5-8 人日；
- 跨平台（目前深绑 Windows：`%APPDATA%`、NSIS、PowerShell 脚本、`taskkill`）——8-15 人日；
- `harness/prompts.py` 领域内容抽出为可配置提示词包——1-2 人日；
- 许可与合规件（LICENSE/NOTICE/CLA/贡献指南/安全策略）——1-2 人日；
- 完整文档（README/快速开始/插件开发指南/示例插件）——5-8 人日；
- 首次真机跑通陌生环境安装（一定会踩坑）——3-5 人日。

### 5.4 时间线建议

```
第 1 周   §5.0 权利体检（0.5d，非技术）  +  方案 A（3-5d）
第 2-4 周 方案 B（8-15d），期间下游功能照常以插件形式在方案 A 的骨架上迭代
第 2-3 月 视权属结论决定是否做方案 C
```

**关键节奏建议**：**方案 A 完成后立即停止重构，先让下游功能在插件骨架上跑 2-4 周**。
理由：插件接口的真实需求只有在写第二个插件时才会暴露，过早定稿会返工（§7 R4）。

---

## 6. 真正的风险：平台知识渗进框架（**本节替代 v1 的权利分析**）

> 权属已明确归本人，故"法律风险"不是本议题。**唯一需要提前规避的风险是**：
> 框架文件里慢慢长出平台专属知识，而这件事**不会报错、不会失败**，只会在下一个产品上暴露。

### 6.1 为什么这个风险会持续恶化（而不是一次性的）

框架与业务在**同一个仓库**里，日常开发的路径依赖会把平台知识往框架里带：

| 常见的"顺手"动作 | 后果 |
|---|---|
| 在 `browser_pool.py` 里为某平台的窗口标题特判 | 框架出现平台语义 |
| 在 `autologin.py` 里加该平台登录页的选择器 | 框架绑死一个平台的 DOM |
| 在 `harness/prompts.py` 里补该平台的术语 | 框架带上前客户领域知识 |
| 在通用前端里加一个"平台洞察"入口 | 框架导航绑死业务 |
| 把平台端点常量放进 `services/` 而不是 `egmp/` | 最危险：**泄漏面扩大到框架** |

**每一条单看都合理**（都是为了解决眼前问题），累积起来就是"框架不可复用 + 前客户信息随框架流向下一个客户"。

### 6.2 防范手段：一道 CI 门禁 + 三条编码纪律

**手段 1（关键）：框架文件清单 + 特征词扫描门禁**（详见 [FRAMEWORK-INVENTORY](FRAMEWORK-INVENTORY.md) §6）

```powershell
# 框架文件里出现平台特征词 → CI 失败，并打印命中位置
$FRAMEWORK_FILES = <清单 §3 的 A-F 类文件>
$FORBIDDEN = @("egmp","capa_plan","change_control","FieldPage","BasicObject",
               "GetWorkflowBasicPageViewList","GetMenuList","lifecycleStatus","Gaia",
               "<租户名>","<平台域名>","<平台对象编码后缀特定用法>")
```

> ⚠️ **必须逐条人工确认，不能只看命中数**：我实测扫出的 1,379 处里绝大多数是噪音
> ——`lodash`/`typescript` 里的 `__c` 变量名、打包产物、以及 `pageIndex`（轮盘分页与平台分页同名）。
> 甚至 `paths.py` 的 3 处命中最后确认是**文档字符串里的举例**。
> **门禁的价值在于"新出现的命中"，首次落地时要人工把存量清成 0，之后才有效。**

**手段 2：依赖方向门禁**——框架文件**不得** import 业务包（`egmp`）；
业务可以 import 框架。这条可以用 AST 脚本静态检查（本调研的量化脚本即原型）。

**手段 3：三条编码纪律**（写给未来的自己，建议进 `CONTRIBUTING.md`）
1. **平台特征只允许出现在两处**：业务包内部、以及业务插件对框架扩展点的注册代码里；
2. **框架新增能力时先问**："换一个平台，这行要改吗？" 要改 → 走扩展点，不写进框架；
3. **配置进框架、数据不进框架**：端口/目录/品牌名可参数化；端点/字段/租户名一律不进。

### 6.3 与"拆仓"的关系

**不拆仓也能达成目标**：上述门禁 + 清单在同仓内完全可执行，且成本低得多。
拆仓（§5.2 方案 B）的收益主要是**发布独立性**（框架可单独发版、单独开源），
**不是你这次要解决的问题**。建议顺序：先做清单与门禁，等真的要复用第二个产品时再拆。

---

## 7. 风险登记（按严重度排序）

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| **R1** | **平台知识渗进框架**（框架文件里长出平台专属逻辑），**不报错、不失败**，只在下一个产品上暴露或把前客户信息带过去 | 高——且**会随每次迭代持续恶化** | 见 §6：框架文件清单 + 特征词 CI 门禁 + 依赖方向门禁 + 三条编码纪律。**首次落地须把存量清成 0 才有效** |
| **R2** | **平台知识泄进上游**：把 `egmp/kernel/endpoints.py`（含实测端点常量）带进开源仓库 | 高——可能构成侵犯商业秘密 | 上游**只**接收 §4.2 表中"与行业无关"的资产；对上游做**平台特征词扫描**作为 CI 门禁（本仓库已有可用脚本，见本文 §2.4 的方法） |
| **R3** | **打包链在插件化后变复杂**：PyInstaller 需显式收集插件包及其 `entry_points.txt`，漏收 = 装完没有洞察功能（静默） | 中→**低**（工具齐备 + **已有公开实证的失败案例**） | **⚠️ 这个坑有公开实证**：PyInstaller 官方讨论区有专门的
[「Problem finding entry point via importlib.metadata.entry_points in packaged app」](https://github.com/orgs/pyinstaller/discussions/8325)，
说明"开发态正常、打包后 entry point 发现不到"是**已知且真实**的失败模式（正是风险 R3 的症状）。
**缓解已实测可行**：PyInstaller 6.22.2 自带 `copy_metadata()` 与 `collect_entry_point()`
（后者已[移植到 importlib.metadata](https://github.com/pyinstaller/pyinstaller/commit/b69595054077628d9738be961b536b3349bb9c90)，与本项目选型一致），
而 `workbench/server.spec` 已有 `collect_all()` 的成熟用法可照抄。
做法：对每个下游插件 `collect_all()` + **`copy_metadata()`**（缺 metadata 就静默发现不到），
再用 `tools/verify_packaging.py` 加一项"安装包里存在预期插件与其 dist-info"，打包后真机冒烟。 |
| **R4** | **插件接口过早定稿**：写完第一个插件就冻结契约 | 中——第二个插件必然返工 | 方案 A 完成后先跑 2-4 周真实迭代再定版本号（§5.4） |
| **R5** | **单人维护双仓的协调成本**：版本对齐、双份 CI、回归跑两遍 | 中 | 上游接口用严格的语义化版本 + 启动自检（缺扩展点直接报错，不静默降级）；下游 CI 拉起上游最新 tag 跑集成 |
| **R6** | **"上游"变成事实上的公司产品**：公司要求你在上游里加他们的专属功能 | 中——边界再次模糊 | 明确写入 `CONTRIBUTING.md`：上游拒绝任何含平台知识的 PR；专属需求一律进下游插件 |
| **R7** | 自动更新链路与仓库名绑定：改名/迁移仓库会打断所有客户端的 `latest.yml` 检查 | 中 | 若迁移，需保留旧仓库的 Releases 转发或发一次"迁移版本"；见 [ADR-0008](adr/0008-update-channel-and-proxy.md) |
| **R8** | 测试夹具替换（方案 C）会削弱现有回归覆盖 | 低-中 | `tests/fixtures/egmp/understand-capa-plan.json` 用合成夹具替代前，先补等价断言 |

---

## 8. 外部先例与技术模式（带出处）

### 8.1 技术模式：Python 生态的插件机制对比

| 机制 | 发现方式 | 依赖注入 | 版本约束 | 适用场景 |
|---|---|---|---|---|
| **`importlib.metadata.entry_points`**（setuptools entry points，[官方规范](https://packaging.python.org/en/latest/specifications/entry-points/)、[标准库文档](https://raw.githubusercontent.com/python/cpython/b35c37910a4595f22458cf0291a9b0252b3c6c70/Doc/library/importlib.metadata.rst)） | 安装元数据（`.dist-info/entry_points.txt`） | 手动（插件自注册进宿主 Registry） | 靠宿主自检 | ✅ **本项目推荐**：零额外依赖，与 `uv` 的安装模型天然契合。POC 已实跑（§3） |
| **`pluggy`**（[官方文档](https://pluggy.readthedocs.io/en/stable/)，pytest 的插件内核） | `entry_points` + 显式 `register()` | hookspec/hookimpl（**有签名校验**） | 靠 hookspec 版本 | 插件需要**多实现点 + 精细调用顺序**时；本项目的扩展点数量（约 2-4 个）还用不上它 |
| **`stevedore`** | `entry_points` 封装 | Driver/Extension 两类模式 | 有 | OpenStack 生态；比 `pluggy` 更重，适合"驱动式"插件 |
| **命名空间包（PEP 420）** | 导入路径自动合并 | 无 | 无 | 只做"代码分散在多包但共享命名空间"，**不做生命周期管理**；不适合做插件契约 |
| **显式注册表 / 配置文件** | 读清单文件 | 手动 | 手动 | ✅ **与 entry_points 组合使用最稳**：清单给"启用/顺序/配置"，entry_points 给"发现" |
| `__subclasses__` 自注册 | 导入期副作用 | 隐式 | 无 | ❌ 不推荐：导入顺序会决定行为，且难以诊断 |

**同类桌面应用/平台的先例与其代价**（用于判断"值不值得"）：

| 平台 | 模型 | 代价/教训（对本项目的启示） |
|---|---|---|
| **VS Code** | 扩展跑在**独立进程**（Extension Host），经 RPC 通信（[官方设计说明](https://mintlify.wiki/microsoft/vscode/concepts/extension-system)、[Extensibility 原则](https://raw.githubusercontent.com/microsoft/vscode-docs/f9eab280a20fc7a13991fc83aa723abd716224e2/docs/extensionAPI/patterns-and-principles.md)） | 换来崩溃隔离与依赖隔离；代价是 RPC 边界、启动延迟、API 版本矩阵。**本项目的插件全是自己写的可信代码，不需要这层** |
| **Eclipse / OSGi** | 进程内 bundle + 动态热插拔 | 复杂度极高（类加载隔离、服务注册表、版本范围）；**单人项目绝对不要模仿** |
| **Jenkins** | 进程内插件 + 极长的 API 兼容期 | 教训：**"永不破坏插件 API"的承诺会成为巨大负担**，最终靠大量 `@Deprecated` 垫层维持 |
| **Home Assistant / Ansible（collections）** | 进程内集成 + 严格契约与弃用周期 | 教训：契约文档与弃用流程比代码本身更耗人力 |
| **Terraform provider** | **独立进程 + gRPC 协议** | 换来语言无关与隔离；对纯 Python 单机项目是过度设计 |

> **结论**：上表所有"重"模型（独立进程、热插拔、版本协商矩阵）都是**为第三方不可信插件或大规模生态**付出的成本。
> 本项目的插件方只有你自己、全是可信代码、数量 1-3 个 → **只需"进程内 + entry_points + 启动自检"这一档**。
>
> **关于 `pluggy` 的补充判断**：它是"**多实现点 + 调用顺序语义**"的强项（pytest 需要为每个钩子协调几十个插件）。
> 本项目只有 2-4 个扩展点、1-3 个插件，引入 `pluggy` 会多一层依赖与概念，收益不抵成本。
> **但若将来扩展点数量上到 10+ 个、且需要"多个插件对同一动作按序介入"，应重新评估 `pluggy`**
> （它自带 hookspec 签名校验，正好解决 §3 陷阱 #3 的"扩展点静默 None"）。

### 8.2 「哪些知识不能进框架」的判定（替代 v1 的法规章节）

v1 的 §8.2/§8.2.1 花了两小节讲法规条款出处，**已删除**（权属已明确，与目标无关）。
换成本议题真正需要的判定：**哪些东西一旦进了框架，就会让框架"带着上一个客户的痕迹"**。

| 知识类型 | 例子 | 能否进框架 | 理由 |
|---|---|---|---|
| **平台端点与协议常量** | `egmp/kernel/endpoints.py` 的 URL 常量、`FieldPage`/`BasicObject` 路径 | ❌ **绝不** | 这是对特定平台的实测结果，属对方平台内部信息；框架一旦带上，就变成"给下一个客户交付前客户的实现细节" |
| **平台语义与枚举** | 状态机名、字段类型语义（`semantics.py`） | ❌ 绝不 | 同上，且换平台后完全无意义 |
| **平台业务概念** | 蓝图校验规则、理解报告的 L1/L2/L3 分层 | ❌ 绝不 | 业务领域模型；框架只应提供"插件能挂进来"的位置 |
| **登录页 DOM 选择器** | `请输入用户名`、`input[type=password]`、srcdoc iframe 策略 | ⚠️ **可参数化后进框架** | 机制（节奏门控/跨 realm setValue/提交前回读）是通用的；**具体的站点特征必须外置成配置** |
| **前客户租户名/域名/账号名** | 任何 `*.example.com` 之前形态的真值 | ❌ 绝不 | 即使不是技术秘密，也不该随框架流向另一个客户 |
| **通用的多账号技术** | Cookie 袋虚拟化、DNR 规则族、IndexedDB 命名空间、storage_state 免密 | ✅ 可以 | 与平台无关，任何"同站点多身份"场景都适用 |
| **桌面分发基础设施** | 壳、自动更新、代理通道、NSIS 打包 | ✅ 可以 | 与行业无关（只有一个 owner/repo 参数） |

**一句话判定法**：**"换个平台，这行要改吗？"** 要改的 → 不进框架；只是"值不同"的 → 参数化后进框架。

> **与"清洁室"的关系（保留 v1 的一个有用结论）**：清洁室实现（由未接触原实现的人依规格重写）
> 解决的是"这个实现是否抄了别人"；它**不能**把"实测得来的平台内部常量"洗成通用知识——
> 那类东西的性质是"事实"，不是"表达"。**对本项目的可操作结论**：框架只放通用机制，
> 平台实测常量一律留在业务包内，且**不要**为了复用把它们参数化后带出去。

### 8.3 先例的启示（浓缩）

上面那些平台的共同规律，与本项目的对应关系：

| 先例规律 | 对本项目的含义 |
|---|---|
| 重模型（独立进程/热插拔/版本矩阵）都是为**第三方不可信插件**付的成本 | 你的插件是自己写的可信代码 → 进程内 + entry_points 就够 |
| 兼容承诺一旦给出就撤不回（Jenkins 教训） | **现在不要承诺插件 API 稳定性**；等第二个插件写完再定版本语义（风险 R4） |
| 契约文档与弃用流程比代码更耗人力（HA/Ansible 教训） | 契约写成一页纸（§5.1.1 的 5 条），够用即可 |
| 扩展点的"静默不匹配"是通用痛点（pluggy 用签名校验解决） | 用 `require()` 启动自检替代（§5.1.1 第 4 条），不引入 pluggy |

> **统一结论**：**框架只放"与任何具体平台无关"的能力**（[FRAMEWORK-INVENTORY](FRAMEWORK-INVENTORY.md) §3 的 A–F 类）；
> 任何"从具体平台观察得来"的常量、语义、字段名、状态机，一律留在业务包——
> 这是风险 R1（知识渗进框架）与 R2（泄进上游）的统一缓解。

---

## 9. 立即可执行的三件事（修订版）

1. **今天（1 人日，唯一有"不做就出事"性质的一步）**：做 [FRAMEWORK-INVENTORY](FRAMEWORK-INVENTORY.md) §4 类型 1
   ——**参数化数据目录名与三个端口**。不做的后果不是"框架不干净"，而是
   **第二个产品与第一个争抢同一个 `%APPDATA%\Workbench` 与 18765/18766/18767**
   （表现为数据串台，或第二个产品起不来）。这是唯一有真实故障后果的一项。
2. **本周（1 人日）**：把 2 处业务耦合改成扩展点（[清单](FRAMEWORK-INVENTORY.md) §4 类型 2）
   + 把 `harness/prompts.py` 的领域提示词外置。这两项做完，"框架可复用"就成立了大半。
3. **然后（0.5 人日，但请尽早）**：落地 CI 门禁（§6.2 手段 1+2）**并在同一次提交里把存量清成 0**
   ——门禁只有在存量干净时才有效；否则它一上线就是红的，然后就再也没有人看它了。

**不建议现在做的**：拆仓（§5.2 方案 B）与对外开源（§5.3 方案 C）。
它们解决的是"发布独立性"，不是你这次要解决的问题；等真的要复用第二个产品时再做，成本不会变高。

---

## 附录 A：POC 复现方式

POC 是临时的（建在 `%TEMP%\plugin_poc`，未入库）。要正式纳入仓库，建议放在
`tests/test_plugin_api.py`：断言 ① 插件被发现 ② 坏插件被隔离 ③ 插件路由可达
④ 宿主能取到扩展点 ⑤ 缺扩展点时启动报错（而非静默 None）。

## 附录 B：本次调研实测验证过的技术前提（可直接引用）

| 前提 | 验证方式 | 结果 |
|---|---|---|
| FastAPI 宿主可经 `entry_points` 加载插件并装配路由 | POC 实跑 uvicorn（§3） | ✅ 通过 |
| 坏插件不会拖垮宿主 | POC 故意注入抛异常的插件 | ✅ 被隔离并告警 |
| 插件向宿主贡献导航/扩展点、宿主反向取用 | POC 的 `add_nav` / `provide` / `get` | ✅ 通过 |
| `uv` 支持 git / path / editable 依赖与 workspace | `uv add --help` / `uv help` | ✅ 参数齐备 |
| PyInstaller 能收集入口点插件 | 检查 6.22.2 的 hooks 工具 + 官方讨论区实证 | ✅ `copy_metadata()` + `collect_entry_point()` 存在；且"打包后 entry point 发现失败"是已知问题（见风险 R3） |
| 本项目 spec 已有可照抄的收集模式 | 读 `workbench/server.spec` | ✅ 已有 `collect_all()` 用法 |
| 前端/壳/扩展与公司平台无关 | 平台特征词全量扫描 | ✅ 仅产品名与注释，无平台语义 |



- **耦合量化**：AST 解析全部 `.py` 的 import（含相对导入解析），按层统计穿越点——脚本思路见 §2.2 表格；
- **专属度扫描**：用平台特征词表（`capa_plan`/`__c`/`FieldPage`/`BasicObject`/`Workflow`/租户名/域名）
  全仓计数，定位"知识实际分布"——这个方法建议做成 CI 门禁（风险 R2 的缓解）。
