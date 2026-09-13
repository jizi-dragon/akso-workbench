# 平台洞察（INSIGHT）—— 功能定位与产品能力规格

_2026-09-13 定稿 · 基于 `d0efa84`（v0.3.5）代码实测 + 上游 [akso-cc](https://github.com/jizi-dragon/akso-cc)（`gaia-inventory`）README 权威口径 · 四项决策已拍板_

> **本文档的地位**：定义「平台洞察是什么、能做什么、不能做什么」。属**中变更频率**文档——
> 能力范围变化时更新；实现细节走 `docs/API.md` / `docs/SCHEMA.md` / `docs/CONFIG.md`；踩坑走 `docs/PITFALLS.md`。
> 与 [docs/adr/0005](adr/0005-native-internalization-of-four-projects.md)（原生化决策）配套阅读。

---

## 1. 定位

**平台洞察是 Gaia / eGMP 平台的「只读配置理解引擎」。**

它与配置工厂构成一对，**共用同一套 API 知识体系**：

| | 平台洞察 | 配置工厂 |
|---|---|---|
| 路径 | **读路径** —— 平台 → 清单快照 → 语义理解 | **写路径** —— 蓝图 → API → 配置执行 |
| 输入 | 一个**对象 code** | 一份蓝图 JSON |
| 输出 | 该对象「是什么 / 和谁有关 / 怎么流转」的可读理解报告 | 平台上真实建出的配置 |
| 风险面 | **只读，不改平台任何东西** | 会写真实环境（有 `confirmed` 守卫） |

> 这不是类比，是上游原话：「姊妹项目 akso-auto 是**写路径**，本项目是**读路径**，两者共用同一套 API 知识体系。」
> —— 用户要求的第 2 点与上游原始设计完全一致。

### 1.1 一句话能力陈述

> 输入一个对象 code，输出**这个对象的完整配置理解**：它有哪些生命周期状态、每个状态的**进入条件 / 用户动作 / 进入动作**、
> 动作中发起的工作流**长什么样**、以及它关联了哪些其他对象。全部结论由**平台配置事实确定性推导**——
> LLM 只做可选的自然语言摘要，**不参与事实推导**。

### 1.2 三条不可让步的原则

1. **只读**：洞察永不调用任何写端点。任何出现在洞察调用链上的写端点即视为缺陷。
2. **确定性优先**：图与表的每一行都必须可追溯到平台配置事实。LLM 产出必须可关闭、可标记、可回退。
3. **边界必须外显**：凡平台读不到、读不全的，必须在产物里写明，**不允许静默留白**。

---

## 2. 能力范围

### 2.1 核心能力（本轮确认，共 5 项）

| # | 能力 | 说明 | 现状 |
|---|---|---|---|
| **C1** | **生命周期状态遍历** | 给定对象 code，取该对象的**每一个**生命周期状态 | ✅ 已实现 |
| **C2** | **状态明细表** | 逐状态输出 **进入条件 / 用户动作 / 进入动作**，整理为表格 | ✅ 已实现，需按 §5.1 标注 |
| **C3** | **工作流可视化** | 动作中若含「发起工作流」，取该工作流并**用 drawio 输出流程图** | ⚠️ 覆盖面待补（§2.3） |
| **C4** | **关联对象列举** | 生命周期/关系中涉及的其他对象，列出其**简单信息** | ⚠️ 部分（L2 关系网） |
| **C5** | **产物落盘到用户可选目录** | 表格 / 可视化图 / 文档写入专用工作区，地址由用户决定 | ❌ **待建**（§4） |

**C2 的表格列口径**（与上游一致，写死为契约）：

```
| 生命周期状态名称 | 进入动作 | 用户动作 | 进入条件 |
```

### 2.2 明确非目标（本轮不做，避免范围蔓延）

- ❌ **不做业务记录数据**：只读**配置元数据**，不读业务行数据（上游同口径）。
- ❌ **不做写操作**：任何"顺手改一下配置"的能力都不进洞察，归配置工厂。
- ❌ **不做通用对话/知识问答**：洞察是确定性工具，不是聊天入口。
- ❌ **不做多跳传递闭包**：本轮关系网仍为**单跳**（声明式，见 §5.4）。
- ❌ **不追求"完整的对象枚举"**：平台**没有**全量枚举对象的接口（上游已验证），未挂菜单且无工作流的对象本质不可发现——这是能力边界，不是缺陷。

### 2.3 C3 的覆盖面补全（✅ 已完成，2026-09-13）

三条缺口均已修复，验收见 `tests/test_lifecycle_coverage.py`（13 项）：

| 缺口 | 上游口径 | 落地结果 |
|---|---|---|
| **工作流触发状态未使用** | 工作流行上的 `lifecycleStatusIds` 直接给出该工作流的**触发状态**，实测 **195/707** 条携带 | ✅ 新增 `workflowTriggerEdges`，与按钮通道**取并集**；只认已知状态 id（不产出悬空触发）；报告新增「工作流触发状态」章节 |
| **进入动作自动发起工作流** | 部分进入动作的行为可经动作栏**名称匹配**解析出「自动修改状态 / 自动发起工作流」 | ✅ 归类改按 `executeType` 走：`executeType=2` 的边进 `enterActionEdges`（原先被误塞进 `userActionEdges`，导致四列表两列同时出错）；发起工作流同样覆盖 |
| **同名按钮跨状态错配** | —— | ✅ `_consume_match` 现只在「本状态自身的动作」与「无法归属任何状态的动作」里按名称找；**明确属于别的状态的 id 一律排除**（原先遍历全部状态，实测 `sa` 的「提交」被配到 `sb` 的动作栏行） |

> 一并修复：`GetActionBar` 的 `targetId` 是 **workflowBasicId**，而 `workflows_by_row_id` 与
> `workflow_graphs` 都按**工作流行 id** 建键 —— 原先直接拿 basicId 去查，**既取不到工作流名也取不到流程图**
> （表现为「有发起工作流的动作，却没有 drawio 产物」）。现建一次反查索引修正。

---

## 3. 数据来源：平台知识层（与配置工厂共用）

### 3.1 目标结构

抽出**显式的平台知识层**，读路径与写路径都依赖它，**任一侧不再各自硬编码平台约定**：

```
workbench/services/egmp/
├── kernel/                  ← 【新增】平台知识层（读/写路径共用）
│   ├── endpoints.py         ← 唯一天威端点表（含命名空间归属，见 §3.2）
│   ├── semantics.py         ← 语义词典：dataType / behaviorType / stepType /
│   │                          dispatchMode / 后缀约定 / 状态编码约定
│   ├── client.py            ← HTTP 内核（超时/重试/错误分类，见 §3.3）
│   ├── auth.py              ← 登录 + token 生命周期（按环境+账号分键）
│   ├── cache.py             ← 元数据缓存（**按环境命名空间**）
│   └── capability.py        ← 【新增】平台版本 / 能力探测
├── insight/                 ← 读路径（本规格主体）
├── writers/                 ← 写路径
└── monitor/
```

**迁移注意**：`writers/endpoints.py` 现被 `writers/idempotency.py` 导入——提升为 `kernel/endpoints.py` 时需同步改引用，
并在 `writers/endpoints.py` 留一层**转发垫片**（re-export）以免打断其它调用点。

### 3.2 端点表必须成为唯一事实来源

现状问题：insight **绕过**了端点表，自行硬编码约 **14 处**字面量；另有 **6 个 insight 在用的端点表里根本没有**。

| 类别 | 端点 |
|---|---|
| 表内但 insight 重复硬编码 | `Listlayout/List`、`Layout/LayoutList`、`BasicObject/FieldPage`、`MenuGroup/QueryList`、`Workflow/GetWorkflowBasicPageViewList`、`ObjectPicklist/{code}`、`Workflow/{code}` 等 |
| **表外缺失**（需补进） | `Menu/GetMenuList`、`UserAction/GetList`、`UserAction/GetActionBar`、`Status/Detail`、`Workflow/GetWorkflowSteps`、`Workflow/GetWorkflowStepWithDetails` |
| **本轮新增**（上游给出，我们未实现） | `BasicObject/QueryList`、`BasicObject/GetPageList`、`BasicObject/Page`（发现链第 3 步候选探针） |

**端点表必须标注命名空间**，因为两套契约不同：

- `/api/openapi/v1.0/*` —— **版本化**标准 API（登录、对象、字段降级、生命周期状态）
- `/api/platform/*`、`/api/config/*` —— **私有、无版本**的 web 控制台接口（平台升级时最先断的就是这些）

### 3.3 知识层必须同时解决的基础问题

以下均为已确认缺陷，属「稳定底层」的题中之义：

1. **环境与身份隔离** ✅ **已修（2026-09-13）**：token 缓存改按 **`(base_url, username)`** 分键并在载入时校验归属，
   `env_id` 退为文件内元数据、不再参与分键、**取消 `cli` 兜底键**；同时按 JWT `exp` 提前判失效、原子写。
   元数据缓存键加环境作用域（`{scope}|{kind}:{code}`），假值也可缓存（原先 `None` 兼作 miss）。
   验收见 `tests/test_env_isolation.py`（19 项）。
2. **缓存环境命名空间**：`QueryCache` 是进程级全局单例、键不含环境 → 四个真实部署在同一进程内会**互相污染元数据**。
3. **`base_url` 归一化**：现只 `rstrip("/")`，不校验路径。真实数据里已有 `https://example-config.example.com/login` 这种带路径的配置。
4. **错误分类**：`ApiError` 携带结构化 `code` 但**全仓无人 catch**；401/429/503 在类型层面无法区分。
5. **分页终止条件**：`hasNext is True` 严格同一性判断 → 平台返回 `1`/`"true"` 会**静默截断在第 1 页**。
6. **网络与代理**：内网环境（真实配置里有一个 `http://192.0.2.10`）需显式绕过代理；`AKSO_PROXY` 目前到不了 egmp 的 Python 路径。

---

## 4. 输出物与存储模型

### 4.1 存储模型（已拍板）

**可配工作区根目录 + 每次运行自动建「对象 / 时间戳」。**

```
<工作区根目录>/                       ← 用户可配（设置页），默认建议 我的文档\AksoWorkbench\洞察
└── <对象code>/                       ← 按对象归集，同一对象的历次理解可比对
    └── 20260913-153000/              ← 每次运行一个时间戳目录（Windows 合法字符，可排序）
        ├── manifest.json             ← 【新增】清单：见 §4.3
        ├── understanding.md          ← 人读报告（三层 + 状态明细表 + Mermaid）
        ├── understanding.json        ← 机器读模型
        ├── object-lifecycle.drawio   ← 【新增】状态机 drawio（可选）
        └── workflow-<N>-<名称>.drawio ← 每条被发起的工作流一张
```

**与内部运行区的关系**（重要，避免双份产物与路径失控）：

| 区域 | 内容 | 是否对用户 |
|---|---|---|
| `runtime/insight/<job_id>/`（内部） | `proc.log`、内部诊断 | ❌ 内部，不承诺保留 |
| `<工作区>/<code>/<ts>/`（用户） | 正式产物（表 / 图 / 文档 / manifest） | ✅ 用户资产，应用不擅自删除 |

`insight_runs` 增列记录工作区路径（**新增迁移 14，不改动 1–13**，见 §7）。

### 4.2 文件名安全（✅ 已修，2026-09-13）

修复前：对象 code 由用户输入，**仅做 `strip()`** 就直接拼进文件名（`understand.py:124,127,96`），
只有**工作流名**过了 `_safe_name`。已实测复现：`../../PWNED` 的产物 resolve 后落在 job 目录**之外**；
`C:/Temp/pwn` 会写到 `C:\Temp\`。

**已落地的规则**（新增 `workbench/services/paths.py`）：

- `safe_path_component(raw)` —— 任何外部字符串进入路径成分前**必须**过这里。剥离 Windows 非法字符
  （`<>:"/\|?*` 与控制字符）、零宽/双向文本控制符（防扩展名伪装）、首尾点与空格（杜绝 `.`/`..`、
  以及 Windows 会静默剥掉的尾点），并对 `CON`/`NUL`/`COM1`… 保留设备名加前缀。
  **正常平台编码恒等通过**（`capa_plan__c`、`v1.2`、含中文的 `培训管理__c` 都不改名）。
- `resolve_within(base, *parts)` —— 拼接后断言仍在 `base` 内，越界抛 `ValueError`（纵深防御）。
- `routes_insight.py` 的产物读取守卫由**字符串前缀比较**改为 `Path.is_relative_to()`；
  并拒绝 `artifact_dir` 为空的行（原先会退化成「CWD 下任意文件」）。

验收：`tests/test_paths.py` **88 项**（恒等性 + 对抗性输入 + 真实落盘端到端）。

### 4.3 产物契约（本轮新增 `manifest.json`）

每次运行的清单，让产物**自证覆盖度**：

```jsonc
{
  "schemaVersion": 1,
  "command": "understand",
  "objectCode": "capa_plan__c",
  "generatedAt": "2026-09-13T15:30:00+08:00",
  "environment": { "envName": "测试环境", "baseUrl": "https://demo-val.example.com" },
  "platformVersion": null,            // capability 探测结果，未探测则 null
  "coverage": {                       // ★ 关键：三态而非布尔
    "status": "complete",             // complete | partial | degraded
    "statesRead": 8, "statesTotal": 8,
    "fieldsRead": 42, "fieldsDenied": 0,
    "workflowsFound": 3, "workflowsWithGraph": 3
  },
  "artifacts": [ { "name": "understanding.md", "bytes": 12345, "sha256": "…" } ],
  "failures": [ { "stage": "fields", "item": "capa_plan__c", "error": "403 …" } ]
}
```

**`coverage.status` 取代当前的布尔谎报**：现状 `run_inventory` **无条件**返回 `ok:True`（哪怕 0 个对象），
`run_understand` 只要 `succeeded > 0` 就 `ok:True` —— 这些会直接污染 `insight_runs.status`，必须一并改掉。

---

## 5. 已知边界（必须逐条写进产物，不允许静默）

### 5.1 进入条件是「描述级」—— 产品能力天花板

**已拍板：接受描述级，但在报告中显式标注。**

平台**没有**进入条件的结构化读接口。上游记载：写接口 `/api/config/lifecycle/EnterAction/Create` 存在
（条件 `condition.details` + 行为 `configs`），但其控制器读路由经**六轮 48+ 路径探测**
（GET/POST × `Get`/`QueryList`/`GetList`/`Query`/`Page`/`Detail` 等变体，2026-09-01）**均不存在**。

因此进入条件**只能**取自平台自动生成的 `description` 文本（如「提交：风险评估任务不为空」），
我们当前用正则 `修改状态为[［\[]([^\]］]+)` 从中提取目标状态名。

**落盘要求**：
- 表格列名改为 **`进入条件（描述推断）`**；
- 表下固定注脚：`进入条件取自平台自动生成的描述文本，非结构化字段；平台未提供读接口。`；
- `manifest.json` 的 `coverage` 标注 `entryConditionSource: "description"`。

### 5.2 其他必须随产物输出的边界

| 边界 | 原因 |
|---|---|
| **清单完整性依赖账号权限** | 平台 DAC 会遮蔽无权配置；权限不足的配置**不会出现**在结果里 |
| **枚举不出「未挂菜单且无工作流」的对象** | 平台无全量枚举接口（已验证） |
| **`workflowCancelStatus` 不是"取消后流转到"的边** | 它是平台全局配置 id，**持续忽略**并把理由写进产物 |
| **多选引用（dataType 23）按单目标处理** | 已知实现限制 |
| **无公开读接口的语义只能靠描述** | 同 §5.1 口径 |

### 5.3 未识别枚举值必须记为 `unknown` 并计数

当前只认 `behaviorType ∈ {5,7,9}`、`stepType ∈ {10,20,30,40,70,90}`、`dispatchMode ∈ {10,20}`，
**表外取值静默消失**。改为：记为 `unknown` 并在 `coverage` 计数 + 产物里列出。

### 5.4 关系网为单跳（声明式，非缺陷）

当前只扫**本对象自身字段**导出的**出边**。含义：
- 一个被 10 个对象引用、但自己不引用别人的对象，会被判为「孤儿」——**所以"孤儿"必须在报告里标注它只看得出边**；
- 悬空目标（不在已爬集合内的对象）**必须显式列为 unresolved**，不得只出现在 JSON 而统计里消失
  （现状：`spider.py` 的 stats / 图模型 / 文件三处计数互相矛盾）。

> ✅ **悬空边的根因已修（2026-09-13）**：`spider.py` 的 `build_network_model` 原先写
> `_find(...) or f"obj:{target}" if target else None`，因**运算符优先级**实际解析为
> `(_find(...) or f"obj:{target}") if target else None` —— 为「工作流名」这类目标造出
> `obj:工作流「X」` 这种**永不存在的节点 id**，drawio 里就成了悬空引用（已用真实 spider 数据实测确认）。
> 现改为三级解析：状态节点 → 已爬对象节点 → 兜底挂到「来源状态所属对象」；
> 两端都定不了位时置 `unresolved: True`（产物里可见），drawio 侧不画悬空边。

---

## 6. 验收标准

### 6.1 功能验收（对照 §2.1）

| # | 验收项 | 判定 |
|---|---|---|
| C1 | 给定对象 code，产出的状态数 == 平台实际状态数 | 对拍真实环境 |
| C2 | 表格含且仅含四列，表头与 §2.1 契约一致 | 产物断言 |
| C2 | 四列表中每一格都能追溯到 `UserAction/GetList` 行或 `GetActionBar` 行 | 抽样人工核 |
| C3 | 凡有「发起工作流」动作，必有对应 `.drawio`；且 `drawio` **可被 XML 解析器解析** | 自动断言 |
| C3 | 工作流触发状态 = 按钮发现 ∪ `lifecycleStatusIds` | 对拍真实环境 |
| C4 | 关联对象被列出，且悬空目标计入 unresolved | 产物断言 |
| C5 | 用户可改工作区根目录；产物出现在 `<root>/<code>/<ts>/` | 端到端 |

### 6.2 质量门禁

- ✅ **drawio well-formed 回归夹具（已建立，2026-09-13）**：`tests/test_drawio.py` **57 项**，
  覆盖 `&`、`<`、`>`、`"`、换行、**C0 控制字符**、中文、emoji、空名、id 为 `"0"`/`"1"` 的节点、
  重复步骤 id、并行同源边、成环分层、坐标重叠。断言分两层：**先良构**（`ET.fromstring` 必须成功），
  **再语义**（解析回来的属性值必须等于期望文本）。
  配套 `tools/verify_insight_drawio.py` **11 项**真机验收：把对抗性产物交给 **draw.io 桌面版 CLI**
  读一遍再回写，回写结果仍需良构。
  > **实证口径（重要，勿再改错）**：draw.io 对 `&`/`<` 只做**单层** XML 转义
  > （把 `A & B` 写进标签，draw.io 自身产出 `value="A &amp; B"`）；换行用 **`&#xa;` 字符引用**，
  > **不是** `<br>`——字面 `<` 在 XML 属性值里非法，必须转义；`&#xa;` 在 `html=0/1` 下都能换行，
  > 且这正是 draw.io 自身导出的写法。多行标签另需 `whiteSpace=wrap`。
- ✅ **路径安全夹具（已建立，2026-09-13）**：`tests/test_paths.py` **88 项**——正常编码恒等性、
  逐条对抗性输入（`../`、绝对路径、`:`、保留设备名、控制字符、双向覆盖字符、尾点空格、超长）、
  以及**真实落盘**的端到端断言（恶意 code 的产物必须仍在 `output_dir` 内）。
- ⬜ **环境隔离夹具**：同一进程内先后查两个环境，断言元数据缓存**不串**。
- ⬜ **离线回放夹具**：用 Monitor 录制的真实平台响应回放跑通全链路（取代当前只在 `tests/fakes.py` 假平台上的测试）。

---

## 7. 数据安全与重构纪律（本轮特别提醒）

用户明确提示：**平台洞察处于破坏性开发，有重构风险，构建数据时要小心**。据此立三条硬约束：

1. **DB 迁移只增不改**：迁移 **1–13 不可变**（仓库红线）。本轮所需的新列一律**追加迁移 14**
   （`insight_runs` 增 `workspace_dir`、`coverage_status` 等），**不得修改历史迁移**。
2. **产物格式变更必须有 `schemaVersion` 与兼容策略**：本次加的 `manifest.json` 从 `schemaVersion: 1` 起；
   已有 `understanding.json` / `spider-network.json` 的键被重命名或移除时，必须升版本号并在 `CHANGELOG.md` 记明。
3. **重构不得打断既有引用**：`insight_runs` 的既有行、`runtime/insight/` 的历史目录在重构后仍应可读
   （允许降级展示，不允许报错）；`adapter`/`endpoints` 搬家时留转发垫片。

---

## 8. 与上游 akso-cc 的对应关系

前置项目：**https://github.com/jizi-dragon/akso-cc**（内部名 `gaia-inventory`）。
本项目对它是**只读引用 + 知识迁移**，绝不修改原仓库；`adapters/akso-cc.json` 是降级后的只读参考存档
（`"mode": "native-python-internalized"`, `"frozen": true`）。决策见 [ADR-0005](adr/0005-native-internalization-of-four-projects.md)。

| 上游 | 本项目 | 状态 |
|---|---|---|
| `understand/lifecycle.ts` | `insight/lifecycle.py` | ✅ 已移植 |
| `understand/flowgraph.ts` | `insight/flowgraph.py` | ⚠️ 有移植偏差（§2.3、§5） |
| `understand/relations.ts` | `insight/relations.py` | ✅ |
| `understand/drawio.ts` | `insight/drawio.py` | ⚠️ 引入 3 处缺陷（§6.2） |
| `understand/semantics.ts` | `egmp/auth.py`（散落） | ⚠️ 需提升为 `kernel/semantics.py`（§3.1） |
| `understand/render.ts` | `insight/render.py` | ✅ |
| `crawler/object-discovery.ts` 第 3 步探针 | `crawler.py` 未实现 | ❌ 本轮补（§3.2） |
| `tools/find-object.mjs`（显示名 → code 反查） | 无 | ❌ 本轮补 |
| `tests/understand/`（离线单测） | `tests/test_native.py`（仅断言产物存在与子串） | ❌ 本轮补（§6.2） |
| 产物落 `output/`（单一固定目录） | 用户可选工作区 | ⬆️ **本项目增强**（§4） |

**上游权威口径中值得保留的实证结论**（`adapters/akso-cc.json` 与本文档共同承载）：

- 字段主通道 `POST /api/platform/BasicObject/FieldPage`（body 仅 `objectId`，**行内嵌选项集实例**），
  降级通道 `GET /api/openapi/v1.0/BasicObject/field/{objectCode}`；
- 状态动作统一视图 `UserAction/GetList?statusId`：**`executeType` 1 = 用户按钮 / 2 = 进入状态时自动执行**；
- 按钮行为 `UserAction/GetActionBar`：**`behaviorType` 7 = 发起工作流 / 9 = 修改状态**；
- 工作流步骤用**工作流行 id**（`workflowConfigId`），**不是** `workflowBasicId`；
- 表单布局详情 `Layout/LayoutDetail` **必须双参数**（`layoutId` + `basicObjectId`），缺一返回 500；
- 初始/终止状态判定基于系统默认命名 `init_status__c` / `complete_status__c`（不可删除）。
