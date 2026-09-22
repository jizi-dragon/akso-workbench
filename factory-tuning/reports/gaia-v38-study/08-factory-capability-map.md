# 08 · 工厂能力面清单（与 Gaia V3.8 手册对照的基准）

_只读盘点（2026-09-21，未启动应用、未跑 pytest、未发任何 HTTP）。所有结论都落在 `文件:行号` 或函数名上。_

**与既有台账的关系**：`docs/FACTORY-VERIFICATION-PLAN.md`（§3 分层 / §4 T1–T7 / §5 决策 / §8 F1–F19）
与 `docs/FACTORY-AGENT-GAP.md` 已写过的结论**不在此复述**；本文只在需要定位时引用它们的编号（如 T1、F16）。
新增结论统一编号 **N1–N16**（见 §6），便于回填台账。

**手册基准**：`docs/gaia平台技术手册/Gaia产品手册_V3.8/Gaia产品手册/04_配置指南.md`（70 章目录）
与 `04_配置指南/` 下 75 个文件。**目录里列了、本地却没有 .md 的章节**（01F 共享规则、01G 页面模板包、
01H 外部分享、03 布局配置、04B 文档生命周期、07 文档配置、08 文档关系配置、10 布局规则配置、
13 计划任务配置、14 电子签名配置、15 呈现件配置、16 批注配置）**只有链接、没有正文**——
对它们的判定一律标「待核」。

---

## 0. 判据口径（本文怎么判「有 / 没有」）

| 判定 | 含义 | 怎么取的 |
|---|---|---|
| **有** | `writers/` 里有函数能把该能力写进平台，且从工厂入口可达 | 函数名 + `文件:行号` |
| **半有** | 函数签名支持，但蓝图模型或管道里没有入口 | 写出缺哪一环 |
| **有但不可达** | 写入器存在，`workbench/` 内零调用方 | AST 扫描（见 §5.4 的方法） |
| **没有** | 全 `workbench/` 树里对该能力的键名/端点零命中 | grep 模式与零命中结论一并给出 |
| **待核** | 手册章节在本地缺正文，或行为码未知 | 标明缺什么 |

风险分级沿用 `plugin_api.Risk`：`read` / `write` / `dangerous`（`plugin_api.py:117-126`）。

---

## 1. 写入器清单（`workbench/services/egmp/writers/`）

> 「风险」= 该模块**是否会向平台发非 GET 请求**。`writers/__init__.py:3-17` 的映射表说明了各文件从
> akso-auto 哪个 JS 移植而来；这里只写**当下代码的真实内容**。

| 模块 | 负责什么 | 对外函数（行号） | 风险 | 备注（可达性 / 陷阱） |
|---|---|---|---|---|
| `objects.py` | 建对象（8 键请求体）+ 按 code 回读 id | `create_object:38`（写）、`_object_body:27`、`_refetch_object:13` | **写** `SaveBasicObject` | 回读前 `invalidate_object_cache`（:23）是刻意的；回读不到 id 如实 `success:False`（:56-58） |
| `fields.py` | 字段路由器：并发 5 批量建字段、类型专属参数抽取、选项/引用 id 解析 | `batch_create_fields:101`（写）、`create_field:23`（写）、`query_fields:48`（读 `FieldPage`）、`collect_extras:52`、`resolve_picklist_id:88`、`resolve_reference_object_id:96` | **写** `SaveField` + 读 | `fieldMap` 里额外存 `_index:<code>`（:126）→ 表单布局靠它取 `ctrlType`（`layouts.py:103-107`） |
| `picklists.py` | 选项集 upsert + 选项 code 兜底生成 | `create_picklist:17`（写）、`option_name_to_code:11` | **写** `ObjectPicklist/save` | **无条件 `success:True`**（:39-40）：平台报错只可能以异常形式炸出（没有"失败"这个返回值）；回查 id 失败只让 `picklistId` 为 `None` |
| `layouts.py` | 表单布局（读默认布局 → 合并 → 全量提交）、列表布局（新建/改默认 → 全量替换列） | `build_form_layout:57`、`save_form_layout:48`、`get_form_layouts:30`、`find_default_form_layout:35`、`get_form_layout_detail:42`、`build_list_layout:123`、`set_list_columns:116`、`get_list_layouts:112`、`_resolve_cross_object_field:157` | **写** `SaveLayoutDetail` / `Listlayout/Save` / `AddColumns` | 模块头 :4-5 自己写明"全量替换、必须先读旧结构"；只有 `build_form_layout` 遵守（:65-70）。`_data_type_of:103` 是唯一取 `ctrlType` 的地方，而 `:90` 的 `fields_by_id` 是恒空字典的死参数 |
| `lifecycle.py` | 生命周期：建状态、改状态、两阶段构建（重命名系统状态 → 批量新建）；把工作流绑到状态；启用工作流 | `build_statuses:46`、`create_status:24`（写）、`update_status:35`（写 PATCH）、`bind_workflow_to_status:92`（写 `UserAction/Create`）、`ensure_workflow_enabled:141`（写 `ChangeWorkflowBasicStatus`）、`_find_system_status:82`（读） | **写** + 读 | `build_statuses` 返回 `{statusMap, steps}`，**没有顶层 `success`**（:79）→ 管道按成功记（见 §2.3 N5） |
| `workflows.py` | 工作流九段编排 a–i：建基础 → 取步骤 → 开始节点参与者 → 建全部步骤 → 回填连线 → 末步连结束 → 启用 → 绑状态 | `build_workflow:237`、`create_workflow_basic:34`、`get_workflow_steps:53`、`add_task_step:64`、`add_decision_step:87`、`add_action_step:122`、`config_start_step:147`、`backfill_connections:169` | **写**（`Workflow/Add*`、`Update*`、`ChangeStatus`） | 只认 `task/decision/action` 三种步骤（:266-274）；`workflowType` 与 `isSingleRecord` 硬编码（:40）；**返回值恒 `success:True`**（:315-316） |
| `menus.py` | 母菜单 / 子菜单 / 菜单排序 | `create_parent_menu:14`、`create_sub_menu:28`、`sort_menus:45`、`get_menu_groups:10`（读） | **写** `Menu/Submit`、`Menu/Sort` | 整族不可达（T1 已记）；补充见 §5.4 N10 |
| `idempotency.py` | "存在即跳过"查询层 + `with_idempotency` 包装 | `with_idempotency:67`、`object_exists:25`、`picklist_exists:32`、`field_exists:39`、`status_exists:49`、`workflow_exists:59` | **只读**（OpenAPI 查询） | 后三个 `*_exists` **零调用方**；管道只用 `object_exists` / `picklist_exists`（见 §5.4 N11） |
| `responses.py` | 创建类响应取 id 的三条路（dict.id / 裸字符串 / 回读），**会代发 POST** | `created_id:46` | **写**（替调用方发创建请求） | `_NOT_IDS`（:43）把 `"true"/"false"` 挡在 id 之外 |
| `blueprint.py` | 蓝图模型 + 两层校验 + 归一化 + 审阅三件套 | `to_object_blueprints:233`、`validate_blueprint:252`、`has_blocking:353`、`normalize_blueprint:367`、`generate_review_spec:422`、`generate_checklist:487` | **纯本地**（不发请求；唯一平台依赖是常量 `SYSTEM_FIELD_WHITELIST`，:16/:304） | `normalize_blueprint` **零生产调用方**（§5.4 N9） |
| `spec.py` | 给模型的规格与审阅摘要（从运行期模型 + 词典生成） | `blueprint_spec:306`、`blueprint_spec_json:365`、`blueprint_highlights:373`、`blueprint_summary_line:422`、`hints_for_issues:450`；注解表 `_NOTES:85` | **纯本地** | `_NOTES` 的键与模型字段一一对应（`tests/test_factory_agent.py` 的 `test_every_model_field_is_annotated` 钉住）；但 `validation_rules`（:350-360）是**手写散文**，会与校验器漂移 → 见 §3.2 N4 |
| `endpoints.py` | 兼容垫片：把 `kernel.endpoints` + `kernel.semantics` 全量转出口 | 无函数（14 行） | 无 IO | 新代码应从 `kernel` 取（:2-4） |
| `__init__.py` | 移植映射表与 `__all__` | — | — | — |

**写在同一个目录、但不属于"写入器"的东西**：`kernel/endpoints.py`（端点唯一事实源，:151-154 的
`ALL_ENDPOINTS` 由 globals 归集）、`kernel/semantics.py`（读侧=写侧共用的枚举词典）。**写侧只有 8 个
真正改平台的模块**：objects / fields / picklists / layouts / lifecycle / workflows / menus / responses。

---

## 2. 八步管道（`orchestrate.py`）

### 2.1 前置（`run_full_workflow:112`）

| 顺序 | 动作 | 行号 |
|---|---|---|
| 1 | `invalidate_object_cache()` 全清（防跨会话缓存串台） | :117 |
| 2 | 双闸门：`confirmed_env` / `approved` 必须为真，否则**立刻返回** `success:False` | :118-121 |
| 3 | `validate_blueprint` + `has_blocking` → 有 error 就返回失败（**不落 checkpoint、不建目录**） | :123-126 |
| 4 | `CheckpointManager(artifact_dir)`：**构造时就尝试从 `<artifact_dir>/checkpoint.json` 读旧状态** | :128、:36-44 |
| 5 | 入参 `checkpoint` 注入：只把 `status=="success"` 且当前没有同键的行补进来，message 固定 `"断点恢复注入"` | :129-132 |
| 6 | `precheck_blueprint`（冲突只告警，T7） | :134 |
| 7 | 写 `<artifact_dir>/spec.md` + `checklist.md`（**执行期**才写；审批期那份由 `factory_preview_blueprint` 生成） | :135-138 |
| 8 | `topological_sort_objects`（引用者后建，成环保持声明序） | :140、:74-95 |
| 9 | 逐对象：`prefix = f"{code}:" if len(objects) > 1 else ""`；`cp.has_success(f"{prefix}verify")` 为真则**整对象跳过** | :144-151 |

### 2.2 单对象真实步骤序列（`run_object_workflow:166`）

```
1 picklists   :199  _picklists()   —— 逐项 with_idempotency(picklist_exists → create_picklist)
2 object      :216  _object()      —— with_idempotency(object_exists → create_object)；随后 invalidate
3 fields      :230  _fields()      —— batch_create_fields(并发 5)；每个字段**再记一行** checkpoint
4 formLayout  :235  ← 仅当 obj["formLayout"] 存在
5 listLayout  :241  ← 仅当 obj["listLayout"] 存在
6 lifecycle   :258  ← 仅当 obj["enableLifeCycle"] 为真；life_id 取 obj.meta.lifeCycleId → 回落到 get_object_info
7 workflows   :271  ← 仅当 obj["workflows"] 非空；life_id 从**第 6 步的返回值**里取
8 verify      :283  _verify()      —— invalidate → get_object_info / get_fields / get_statuses（只数个数）
```

- **顺序的事实源是函数体的书写顺序，不是常量**：`PIPELINE_STEPS`（:29-30）**不驱动流程**，
  全仓唯一读者是 `spec.py:308/342`（用当初次规格文本）。任何"改常量即改顺序"的假设都是错的（N1）。
- 第 6 步的 `life_id` 若为空串，`build_statuses` 照样发 `Status/Create`（`lifecycle.py:24-32` 不校验
  `lifecycle_id`）——这是 L4 怀疑的代码侧答案。
- 第 7 步从 `result["steps"]["lifecycle"]["lifeCycleId"]` 取；若第 6 步没执行（未开生命周期），
  这里拿到的是 `""`，**不报错**（`workflows.py:37` 直接把它塞进 `basicConfig.lifecycleId`）。
- `manualItems` 只在对象内收集 `obj["rules"]`（:286-287）。**顶层 `rules` 在归一化时就没了（T2）**，
  而**写在对象里的 `rules` 会因为 `ObjectDef` 的 `extra="allow"` 活下来**并进人工清单——
  即"声明区到底收不收得到"取决于写在哪一层（N2）。

### 2.3 失败怎么记（`record`，:176-181）

```
status = "skipped" if outcome.get("skipped")
         else ("success" if outcome.get("success", True) else "failed")
cp.record(f"{prefix}{step}", status, outcome.get("message", ""))
```

四条**代码层可确证**的记账缺陷（与 T3/T4 互补）：

- **N5-a｜`skipped` 不可达**：写侧没有任何步骤返回顶层 `{"skipped": True}`（唯一一处
  `"skipped": True` 在 `lifecycle.py:153`，那是嵌套在 `build_workflow` 里的 `enabled`，不是步骤结果）。
  于是 `checkpoint.summary()` 的 `skippedCount`（:61）**恒为 0**；幂等跳过（`with_idempotency:76`
  返回 `{"status":"skipped"}`）因为键名是 `status` 而不是 `skipped`，被记成 **success**，message 才是"已存在，跳过"。
- **N5-b｜lifecycle 的失败看不见**：`build_statuses` 顶层没有 `success`（:79），失败信息在
  `steps[]` 里（如 `"系统保留状态未找到"` :66-67）→ `record` 默认 True → **状态没建出来也算成功**。
- **N5-c｜workflows 的失败看不见**：`build_workflow` 恒返回 `success:True`（:315-316），
  `add_*_step` 也恒 `success:True`（:84/:119/:144，`stepId` 可能是 `None`）→ `_workflows` 的
  `all(b.get("success"))`（:269）**恒真**。
- **N5-d｜picklists 的失败看不见**：`create_picklist` 恒 `success:True`（`picklists.py:39-40`）；
  `_picklists` 也恒 `success:True`（:197）。平台拒绝只会以异常形式中断整轮（`routes_factory.run`
  捕获后整任务 `failed`，`routes_factory.py:167-170`），**不会**记成"选项集这一步失败"。
- **N5-e｜失败不中止**：第 2 步失败（`objectId=None`）后管道**继续**跑 3–8 步，把空 `objectId` 发出去
  （:216-230 之间没有任何 early return）。对象失败 → 字段/布局/工作流会在空 objectId 上再炸一轮。
- 唯一"逐项"入账的是字段：`_fields` 为每个字段单独 `cp.record(f"{prefix}field:{code}")`（:225-227），
  并把 `fields` 这一步的 `success` 设为 `all(results)`（:228）。

### 2.4 checkpoint 键名规则与断点粒度

| 项 | 规则 | 行号 |
|---|---|---|
| 文件名 | `<artifact_dir>/checkpoint.json`；`artifact_dir=None` 时**只在内存**，不落盘 | :37、:67-71 |
| 写盘粒度 | 每次 `record` **全量覆写**整个文件（无追加、无原子替换） | :67-71 |
| 步骤键 | `f"{prefix}{step}"`，`prefix` 见 §2.1 第 9 条（多对象才带 `code:` 前缀） | :146、:179 |
| 字段键 | `f"{prefix}field:{字段code}"`，每字段一行 | :225 |
| 步骤名 | 字符串字面量：`picklists/object/fields/formLayout/listLayout/lifecycle/workflows/verify` | :199/216/230/235/241/258/271/283 |
| 行结构 | `{step, status, message, meta}`；`meta` 在管道里**从不传**（恒 `null`） | :46-48 |
| 跳过判据 | 只看 `{prefix}verify` 是否 success → **整对象**跳过 | :147-151 |
| 粒度 | **对象内没有"从第 N 步续跑"**：第 4–8 步失败后重跑，前面已建的字段/布局会**再做一遍** | :144-151（无逐步恢复分支） |

---

## 3. 蓝图模型能表达什么（`writers/blueprint.py`）

### 3.1 模型树（全部模型都 `extra="allow"`）

```
Blueprint:211                        description / objects(min1) / picklists / rules / permissions
                                     / reportTemplates / menu
├─ ObjectDef:149                     name / code / enableLifeCycle / enableSignatures
│                                    source=Literal[3] / status=Literal[1] / objectClass=Literal[1]
│                                    picklists / fields(min1) / formLayout / listLayout
│                                    lifecycle / workflows
│  ├─ PicklistDef:33                 name / code / options(min1)
│  │  └─ OptionDef:24                name / code? / status=1 / operations=1 / sort=0
│  ├─ FieldDef:40                    name / code / dataType / isRequired / invisible / disabled
│  │                                 maxLength / format / max / min / decimalPlaces
│  │                                 picklistCode / referenceObjectCode
│  │                                 attachmentCount / attachmentSize / attachmentExtension
│  │                                 returnType / formulaScript / setDefaultValue
│  ├─ FormLayoutDef:72               sections(min1)
│  │  └─ FormSectionDef:63           name / code / type=1 / columnsNum=2 / fields[字段code]
│  ├─ ListLayoutDef:83               name? / columns(min1)
│  │  └─ ListColumnDef:77            fieldCode / sort
│  ├─ LifecycleDef:101               statuses / renameStatuses
│  │  ├─ StatusDef:89                name / code
│  │  └─ RenameStatusDef:95          fromCode / toName
│  └─ WorkflowDef:138                name / code / autoEnable=False / bindToStatusCode
│     │                              participants / steps(min1) / rules
│     ├─ WorkflowParticipantDef:132  name / code
│     ├─ WorkflowStepDef:120         name / code / type=Literal["task","decision","action"]
│     │                              participantRef / nextStepCode / behaviorType
│     │                              behaviorValue / dispatchMode
│     └─ WorkflowRuleDef:107         decisionStep / sourceStep / jumpTo / conditionLabel
│                                    matchValue / targetId / targetCode / logicType / logicValue
├─ MenuDef:179                       parent? / sub?
│  ├─ MenuParentDef:166              name / code
│  └─ MenuSubDef:172                 parentName / name / code
├─ DeclarationRuleDef:185            name / type? / target? / implement? / manual=True
├─ PermissionDef:194                 role / roleCode? / description? / grants[]
├─ ReportTemplateDef:202             name / code? / description? / source? / manual=True
└─ Issue:227                        level / path / message
```

未在模型里、但代码会读的隐藏键：**`ObjectDef.meta.lifeCycleId`**（`orchestrate.py:248`）——
`spec.py` 的 `_NOTES` 里没有它，模型也没声明，只能靠 `extra=allow` 透传（N3）。

### 3.2 校验规则（`validate_blueprint:252`）：哪些 error、哪些 warning

**error（阻断，`has_blocking:353`）**

| # | 规则 | 行号 |
|---|---|---|
| E1 | Layer1 pydantic：类型/必填/`Literal` 不符（含 `fields` 的 `min_length=1`、`maxLength` 的 `le=17`） | :256-261 |
| E2 | 对象编码不匹配 `^[a-z][a-z0-9_]*__c$` | :270-271 |
| E3 | 对象编码重复 | :272-273 |
| E4 | 非法 `dataType`（合法集 {1,2,3,4,5,7,8,13,14,15,16,17,18,23}） | :282-283 |
| E5 | 字段编码重复 | :284-285 |
| E6 | `dataType=4` 缺 `picklistCode` | :287-288 |
| E7 | `dataType∈{13,15,16,23}` 缺 `referenceObjectCode` | :290-292 |
| E8 | `dataType=2` 且 `max<=min`（**两者都非 None 时**才判） | :299-301 |
| E9 | 表单布局引用了不存在的字段 | :305-309 |
| E10 | 列表布局引用了不存在的字段（带 `.` 的跨对象路径**豁免**） | :310-314 |
| E11 | 工作流编码不匹配 `__c` 规范 | :327-329 |
| E12 | `bindToStatusCode` 不在 `statuses`/`renameStatuses` 里 | :330-333 |
| E13 | 工作流规则的 `decisionStep/sourceStep/jumpTo` 不在步骤清单里 | :334-339 |

**warning（不阻断）**：跨对象引用不在本蓝图（:295-297）、开了生命周期没配 `lifecycle`（:320-322）、
配了工作流没开生命周期（:323-324）、`picklistCode` 不在蓝图作用域（:342-349）。

**校验的边界（N4）**：

- **状态编码格式无人校验**：`CODE_STATUS_RE`（:19）在 `validate_blueprint` 里**一次都没用**；
  它只出现在 `spec.py:331/357` 的散文里（"状态编码必须匹配 `status_...__c`"）→
  **规格宣称的规则，校验器不执行**。而本该补后缀的 `normalize_blueprint`（:377-382）**零生产调用方**，
  所以 `status_x` 这种写法既不会被规范化，也不会被拒。
- `PermissionDef` / `ReportTemplateDef` / `DeclarationRuleDef` 会被 **Layer1 校验**（类型不对就报 error），
  但归一化后**原样丢弃**（T2）→ 用户为一个永远不会被执行的东西收到报错。
- `ObjectDef.rules`（对象内规则）不在模型里，**不走任何校验**，却会在运行时进人工清单（N2）。
- 布局系统字段白名单只有 5 个（`kernel/endpoints.py:112-114`）。

### 3.3 关键限制（逐条落到行号）

| # | 限制 | 证据 | 后果 |
|---|---|---|---|
| L-a | **`ObjectDef.fields` 是 `min_length=1`** —— 蓝图表达不出"裸对象" | `blueprint.py:159` | 与台账 §6 末注一致；也是"8 步管道从未被蓝图路径覆盖"的直接原因 |
| L-b | **`FieldDef.maxLength` 被限制在 1~17** | `blueprint.py:48`（`ge=1, le=17`），`spec.py:116` 把它写成规则 | 手册 `01B_对象字段.md:223/243/259` 明确：文本最多 **1500**、长文本/富文本最多 **32000** 字符 ⇒ 任何 `maxLength>17` 的合法字段**在 Layer1 就被拒**（N6） |
| L-c | **`FieldDef.invisible` / `disabled` 发不出去** | 模型有（:46-47）、`_NOTES` 有（`spec.py:114`），但 `collect_extras:52-85` **不复制这两个键**，`create_field` 硬编码 `invisible/disabled=False`（`fields.py:33`） | 蓝图写 `invisible:true` 会被**静默丢弃**（平台只收到 false）；这正好回答了台账 L2 里"`invisible`/`disabled` 是否生效"的核对点（N7） |
| L-d | **`WorkflowStepDef.participantRef` 发不出去** | 模型有（:125）、`_NOTES` 说"审批任务的参与人 code"（`spec.py:169`），但 `add_task_step` 的 `participant_ref` 参数（`workflows.py:66`）**在函数体里从未被使用**，`build_workflow` 也不传（:267-268） | 参与人**只能在开始节点**配（`config_start_step:147-166`）；"不同审批任务不同审批人"表达不出（N8） |
| L-e | `maxLength` 之外的字段属性一律表达不出 | 见 §5.5（平台读侧有 20+ 个键） | 唯一值/索引/加密/帮助内容/复选"不复制"等都只能人工配 |
| L-f | `ObjectDef.source/status/objectClass` 是 `Literal[3/1/1]` | `blueprint.py:155-157`；`objects.py:32` 也硬编码 | 不是"可配项"，是"只允许这一个值"；对象类型（手册 01E）因此不可表达 |
| L-g | 工作流步骤类型只有三种 | `blueprint.py:124` + `workflows.py:266-274` | 通知/合并/更新共享/多记录/文档工作流全部表达不出（§5.2-20） |

### 3.4 顶层声明区（`rules`/`permissions`/`reportTemplates`/`menu`）在归一化时的去向

| 键 | 多对象形态（`"objects" in raw`） | 单对象形态 | 运行时去向 |
|---|---|---|---|
| `picklists` | **并入每一个对象**（`blueprint.py:237-242`：`shared + obj.picklists`） | 保留在 `ObjectDef` 上 | 会真的建（第 1 步） |
| `rules` / `permissions` / `reportTemplates` | **丢弃**（`Blueprint` 收下但 `to_object_blueprints` 只 dump `objects`） | 从 `single` 里显式剔除（:245-246、:387-388） | 只在审阅材料里出现（`generate_review_spec:472-483`）；`run_full_workflow` 的 `manualItems` **收不到**（T2） |
| `menu` | 丢弃 | 剔除 | 只有死代码 `execute_menu:294` 会读它（T1） |
| 对象内的 `rules`（未声明字段） | 靠 `extra="allow"` 存活 | 同上 | **会进** `manualItems`（:286-287）——与顶层行为不一致（N2） |

---

## 4. 模型能调到的工具（`workbench/plugins/factory.py`）

| 工具名 | risk | 落到哪个函数 | 触平台？ | 备注 |
|---|---|---|---|---|
| `factory_blueprint_spec` | `read` | `blueprint_spec_tool:94` → `spec.blueprint_spec_json:365` | 否（纯文本生成） | 无参（`pa.ZERO_ARGS`），timeout 20s |
| `factory_preview_blueprint` | `read` | `preview_blueprint:101` | 否（只写 `RUNTIME_DIR/factory/<id>/`，`staged_dir:82`） | 校验不过**不暂存**（:131-146）；通过则落 `blueprint.json`/`spec.md`/`checklist.md` 并返回 12 位内容指纹当 id（:149-151） |
| `factory_list_blueprints` | `read` | `list_blueprints:198` | 否（扫运行时目录） | 同时列出**页面上传**的蓝图（uuid 命名） |
| `factory_jobs` | `read` | `latest_jobs:292` | 否（`db.query blueprint_jobs`） | 最近 10 条 |
| `factory_status` | `read` | `status:233` → `routes_factory.job_detail:183` | 否（读库+读文件） | `audit_extra` 只带 `job_id`（:383） |
| `factory_create` | **`dangerous`** | `create:254` → `routes_factory.run:115`（内部传 `confirmed=True`，:270-273） | **是**（真机批量写） | timeout 900s；`summary_fn=_create_summary:305`（蓝图要点当确认摘要）；`audit_extra` 带 `blueprint_id`+`account_id`（:397） |

补充三点（N12）：

1. `REQUIRED_TOOLS`（`api/routes_agent.py:375-385`）只钉了 `factory_blueprint_spec` /
   `factory_preview_blueprint` / `factory_create` 三个；`factory_list_blueprints` / `factory_jobs` /
   `factory_status` **不在必需清单里**——打包漏收这三个不会在启动自检里报出来。
2. 六个工具里**没有一个**能做"改蓝图/删蓝图/取消任务/删除平台产物"；工厂面是**只增不减**的。
3. 平台侧执行入口只有一条：`routes_factory.run` 只接受 `command == "create"`（:124-130），
   且 `confirmed=false` 时 409（:131-132）。工具确认门与页面勾选框是**两道独立闸门**。

---

## 5. 工厂没有的能力（与手册逐项对照）

### 5.1 判定方法（可复核）

- **零命中判据**（一次 grep，覆盖整个 `workbench/`，实测 **No matches found**）：
  `ValidationRule|SaveValidation|IndexRule|isIndexed|filterConfig|optionDepend|dependField|referenceConstraint|controlField|codeRule|linkage|Linkage|isCreateReferenceRecord|RecordAction|ObjectAction|approvalMatrix|shareRule|ShareRule|viewType|messageTemplate|outputTemplate|enableTree|treePath|largeVolume|大容量|检查表`
- **AST 扫描**（函数调用方）：遍历 `workbench/**/*.py` 的 `Name`/`Attribute` 引用，统计 `writers/` 下
  每个对外函数的**模块外调用方数量**（§1 与 §5.4 的依据）。
- **端点引用扫描**：对 `kernel/endpoints.py` 里每个常量统计全仓引用，找出"表里有端点、没人调用"的项。

### 5.2 任务点名 22 项 · 逐条判定

| # | 能力（手册章节） | 判定 | 证据 | 缺的是哪一环 |
|---|---|---|---|---|
| 1 | **状态停用**（04 / 04E） | **半有（管道无入口）** | `create_status(is_enabled=True)`（`lifecycle.py:25`）与 `update_status(is_enabled=…)`（:36-41）都支持，但**没有任何调用方传过 False**：唯一调用点是重命名且只传 `name`（:59），`build_statuses` 建新状态时不传（:73-74）。蓝图 `LifecycleDef` 只有 `statuses`/`renameStatuses`（`blueprint.py:101-104`） | 蓝图缺"停用"这个动词。与 F18（状态不可删、只能停用）正面相接 |
| 2 | **验证规则**（01J） | **没有** | 全仓零命中；蓝图里只有 `DeclarationRuleDef`（`manual=True`，`blueprint.py:185-191`）→ 只进审阅文本 | 无写入器、无端点 |
| 3 | **索引规则**（01K） | **没有** | 全仓零命中（`isIndexed` 只在平台回读 JSON 里，见 §5.5） | 同上 |
| 4 | **字段过滤/筛选设置**（01L） | **没有（且注释误导）** | 端点常量 `LISTLAYOUT_SAVE_FILTER`（`kernel/endpoints.py:53`）**全仓零引用**；`build_list_layout` 的文档串写着"可选 filters 失败不阻断"（`layouts.py:126`）而函数体里**一行 filters 代码都没有** | 端点常量为空壳；注释描述了不存在的行为 |
| 5 | **字段配置条件设置**（01M） | **没有** | 零命中。注意 `WorkflowRuleDef` 的 `condition`（`workflows.py:96-106`）是**工作流判断节点**的条件，不是字段级条件，别混为一谈 | 无 |
| 6 | **字段选项依赖**（01N） | **没有** | 零命中（`optionDepend`/`dependField`） | 无 |
| 7 | **字段默认值**（01O） | **部分有** | 静态默认值有：`FieldDef.setDefaultValue`（`blueprint.py:60`）→ `collect_extras` 透传（`fields.py:83-84`）；条件默认值无：平台读侧有 `defaultValDepFields`（见 §5.5） | 依赖式默认值表达不出 |
| 8 | **引用约束**（01P） | **没有** | 零命中；平台读侧有 `findRelationshipId`/`findSourceFieldId`/`sourceFieldConfig`（§5.5）| 无 |
| 9 | **控制字段**（01Q） | **没有** | 零命中（`controlField` 只出现在平台回读的 `objectRelation` 里） | 无 |
| 10 | **编号规则**（07A / 07B） | **没有** | 零命中（平台读侧有 `codeRuleId`） | 无 |
| 11 | **联动规则**（26） | **没有** | 零命中（`linkage` 大小写两种写法都试过） | 无 |
| 12 | **创建相关记录**（27） | **没有** | 写侧零命中；`BEHAVIOR_TYPE_NAMES` 只登记 5/7/9/46（`kernel/semantics.py:35-40`），**没有**"创建相关记录"这一码；读侧有 `isCreateReferenceRecord` 字段（§5.5） | **待核**：手册 27 的行为码未在本仓出现过 |
| 13 | **查重规则**（23） | **没有** | 零命中 | 无 |
| 14 | **页面链接**（29） | **没有** | 零命中 | 无 |
| 15 | **记录动作**（33） | **没有** | 零命中（`RecordAction`） | 无 |
| 16 | **对象动作**（22 / 22A） | **没有（读侧有、写侧无）** | 写侧零命中；读侧 `USER_ACTION_PERMS`（`kernel/endpoints.py:90-91`，`behaviorTypes` 只在这里有值）已被 insight 使用 | 无写入器 |
| 17 | **审批矩阵**（20） | **没有** | 零命中。注意 `config_start_step` 的参与人只有"指定用户"一种（`SELECT_USER_TYPE["SPECIFIED"]`，`semantics.py:72`、`workflows.py:159-160`），而手册 20 要求开始节点参与人可来自审批矩阵 | 无 |
| 18 | **DAC 权限 / 共享规则**（14 / 01F） | **没有** | 零命中；平台读侧有 `enableRelationalSecuritySet`（关联安全设置，§5.5） | 01F 正文本地缺失 → 「待核」其配置面 |
| 19 | **视图管理**（21） | **没有** | 零命中（`viewType`/`SaveView`）；工厂只会改**列表布局的列**（`AddColumns`），不建视图、不配视图功能 | 无 |
| 20 | **通知 / 消息**（12 / 12A / 12B / 05G） | **没有（读侧知道、写侧表达不出）** | 读侧词典有 `40: "通知"`（`semantics.py:42-44`），**写侧 `STEP_TYPE` 只有 START/END/TASK/DECISION/ACTION**（:66）；`WorkflowStepDef.type` 是 `Literal["task","decision","action"]`（`blueprint.py:124`）→ 写 `notify` 在 Layer1 就报错；任务节点 `notifyType` 硬编码 0（`workflows.py:68`） | 无通知节点写入器、无消息模板/第三方设置 |
| 21 | **输出模板**（11 / 24 / 31 / 32） | **没有（只有声明区）** | 写侧零命中；`ReportTemplateDef` 是 `manual=True` 的声明（`blueprint.py:202-208`），只会出现在"人工配置项"里（`blueprint.py:477-478`） | 无 |
| 22 | **检查表**（17 / 17B / 17C） | **没有** | 零命中。⚠️ `generate_checklist`（`blueprint.py:487`）是**蓝图审阅的勾选清单**，同名不同物 | 无 |
| 23 | **大容量对象设置**（18） | **没有** | 零命中；对象创建只有 `objectClass=1` 一种（`objects.py:32`、`blueprint.py:157` 的 `Literal[1]`） | 无 |
| 24 | **树形结构**（01S） | **没有** | 零命中（`enableTree`/`onlySelectLeafNode`/`treePathCode` 只在平台回读里） | 无 |

### 5.3 手册其余章节的覆盖判定（未点名的部分，一次扫完）

| 手册章节 | 工厂侧 | 判定 |
|---|---|---|
| 01 对象配置 / 01B 对象字段 | `objects.py` + `fields.py`（14 种 dataType 可建模，见台账 L2 覆盖进度） | **部分有**（字段级属性缺一大片，见 §5.5） |
| 01C 对象关系 | 只有字段级引用（`dataType∈{13,15,16,23}` + `referenceObjectId`，`fields.py:114-117`）；多对多（`isSimpleManyToMany`/`manyToManyObjectId`）、删除规则、层级复制**无写入器** | **部分有** |
| 01D 表单布局 / 01V 列表布局 | `layouts.py` 两个 builder（列元素形状与 sort 基准已由台账 L3 取证） | **部分有**（列表筛选、视图无） |
| 01E 对象类型（字段矩阵/动作矩阵） | 无；`objectClass` 固定 1 | **没有** |
| 01F 共享规则 / 01G 页面模板包 / 01H 外部分享 | 无；**本地手册缺正文** | **没有 · 待核** |
| 01I 对象实例锁定 | 无 | **没有** |
| 01R 日期时间输入约束 / 01T 字段图标 / 01U 字段字体样式 | 无（平台读侧有 `minOffsetValue`/`iconType` 等键，§5.5） | **没有** |
| 02 选项集配置 | `picklists.py`（upsert 语义 + 选项 code 兜底） | **有**（选项集**不能删**，F4） |
| 03 布局配置 / 10 布局规则配置 / 13 计划任务 / 14 电子签名 / 15 呈现件 / 16 批注 | 无；**本地手册缺正文** | **没有 · 待核** |
| 04 生命周期 / 04B 文档生命周期 | 状态建/改名有（`build_statuses`）；文档生命周期无 | **部分有** |
| 04C 状态类型 / 04D 阶段组 / 04E 状态权限 / 04F 角色 / 04G 流转图 / 04H 状态条件 | 写侧**全无**；读侧有状态与动作读取（`insight/lifecycle.py:20-21`） | **没有** |
| 04I 用户动作 / 进入动作 / 事件动作 | 写侧只有为"绑工作流到状态"服务的 `UserAction/Create`（`lifecycle.py:92-138`）；进入/事件动作写侧零命中（`EVENT_ACTION_LIST` 端点零引用），**读侧有**（`EventAction/GetList`、`EnterAction/GetListByStatusId` 见 `insight/spider.py:244-245`） | **没有** |
| 05 工作流 / 05A 开始节点 / 05B 任务节点 / 05C 判断节点 / 05D 动作节点 | `workflows.py` 只建 task/decision/action；开始节点只能配"指定用户"参与人；任务节点 `reciverType=20`/`taskRequireType=REQUIRED`/`pageOpenType=0` 全是硬编码（`workflows.py:67-73`） | **部分有**（节点内部选项表达不出） |
| 05E 合并节点 / 05F 更新共享 / 05G 通知节点 / 05H 多记录工作流 / 05I 文档工作流 | 无；工作流形态硬编码 `RECORD` + `isSingleRecord=True`（`workflows.py:40`） | **没有** |
| 06 公式配置 | `returnType`/`formulaScript` 透传（`fields.py:80-82`）；按台账 L2 **尚未真机验** | **有（待验）** |
| 07 文档配置 / 07C 文档标签 / 08 文档关系 | 无（`enableSignatures` 只是对象级开关，`objects.py:33`） | **没有** |
| 09 菜单配置 | 写入器齐（母/子/排序）但**整族不可达**（T1） | **有但不可达** |
| 09A 菜单集配置 | 无写入器；`get_menu_groups`（`menus.py:10`）零调用方 | **没有** |
| 10 搜索配置 | 无（搜索字段/权重） | **没有** |
| 11 输出模板 / 12 通知 / 12A 消息变量 / 12B 第三方设置 | 无（只有声明区 `ReportTemplateDef`） | **没有** |
| 17 检查表 / 18 大容量 / 19 报表 / 20 审批矩阵 / 21 视图 / 22 对象动作 / 22A 匿名二维码 / 23 查重 / 24 运行报告 / 25 外部 API 实时填充 / 26 联动 / 27 创建相关记录 / 28 Web 动作 / 29 页面链接 / 30 移动端 / 31–32 PDF 封面 / 33 记录动作 / 34–36 契约锁与文件签批 / 37 新报表 | 全部零命中 | **没有** |
| Agent 配置指南 | 与工厂面无关 | **待核** |

**"表里有端点、没人调用"的清单**（`kernel/endpoints.py` 常量全仓零引用，剔除别名/命名空间常量）：
`WORKFLOW_UPDATE`、`WORKFLOW_DELETE`、`WORKFLOW_DELETE_STEP`、`WORKFLOW_COPY_STEP`、
`LISTLAYOUT_SAVE_FILTER`、`STATUS_LIST`、`USER_ACTION_BY_ID`、`EVENT_ACTION_LIST`、
`LIFESTATUS_USER_ACTION_GET`、`OBJECT_PROBES`（`OBJECT_PROBE_QUERY_LIST`/`PAGE_LIST`/`PAGE`）、
`DEFAULT_STATUS_NAMES`。
⇒ 工厂**没有**"改工作流 / 删工作流 / 删步骤 / 复制步骤"这四个动词，也**没有**任何删除类写入器（N13）。

### 5.4 写侧已有、但不可达（AST 扫描：模块外调用方 = 0）

| # | 不可达的东西 | 证据 | 影响 |
|---|---|---|---|
| N9 | `normalize_blueprint`（`blueprint.py:367`）**零生产调用方**（唯一调用点 `tests/test_native.py:106`） | AST 扫描 | "补 `__c` / snake_case / 补 `statisticsArrange`/`source`/`status` / 生成选项 code"这一整套归一化**从未在生产路径上跑过**；蓝图必须自身合规（这正是 N4 里"错编码被拒而不是被修"的原因） |
| N10 | 菜单族：`create_parent_menu`/`create_sub_menu` 只有死代码 `execute_menu` 一个调用方（T1）；`sort_menus`（`menus.py:45`）与 `get_menu_groups`（:10）**零调用方**；且 `execute_menu` 调 `create_sub_menu` 时**不传** `listlayout_id`/`form_layout_id`（`orchestrate.py:310-311`） | AST 扫描 + 代码 | 即便把菜单接进管道，子菜单也挂不上布局；菜单排序能力没有入口 |
| N11 | `Idempotency.field_exists`（:39）/`status_exists`（:49）/`workflow_exists`（:59）**零调用方**；`with_idempotency` 全仓只有 2 个调用点（`orchestrate.py:186/203`） | AST 扫描 | **幂等只覆盖"选项集 + 对象"**：对象已存在被跳过，但**字段/状态/工作流/布局会在重跑时再做一遍**（`SaveField` 同 code 重发、状态重复建、工作流重复建）。"重跑天然安全"只对一半步骤成立 |
| N14 | `LISTLAYOUT_COLUMNS` 的写侧零调用（T4 判据已记 D-B），此处补一条：**列表布局的列元素形状** `{fieldId, fieldPath, sort}` 与跨对象路径解析 `_resolve_cross_object_field`（`layouts.py:157-166`）**全仓零测试覆盖**（`grep "AddColumns\|LISTLAYOUT" tests/` 零命中，台账 §4 已记） | AST + grep | 与 D-A/D-B 同一条链路的第二处裸奔点 |
| N15 | `MenuSubDef.parentName` 在 `execute_menu` 里被当作**对象编码**去 `get_object_info`（`orchestrate.py:308`） | 代码 | 字段名说"父菜单名"，代码当成"对象 code"用——蓝图作者不可能猜对 |

### 5.5 字段级：平台读侧有键、蓝图表达不出（一次写清的清单）

平台真实字段记录（93 键，台账 F8 已记其中两条）里，下列键**在 `FieldDef` 里没有对应项**，
即"平台上能配、工厂配不了"：`isUnique`、`isIndexed`、`isTranslate`、`helpContent`、
`isDisplayDefaultLists`、`isNotCopyFieldRecord`、`isDepthCopy`、`componentType`、
`codeRuleId`（编号规则）、`isCreateReferenceRecord`、`enableTree`/
`onlySelectLeafNode`/`treePathCode`（树形）、`findRelationshipId`/`findSourceFieldId`/`sourceFieldConfig`
（引用约束/控制字段/查重）、`defaultValDepFields`（条件默认值）、`iconType`/`iconValue`/`iconDependFields`
（图标）、`minOffsetValue`/`minOffsetUnit`/`maxOffsetValue`/`maxOffsetUnit`（日期输入约束）、
`statisticsMode`/`statisticsRelationId`/`statisticsFieldId`/`statisticsAccuracy`（统计）、
`uploadWay`（附件上传方式）、`documentVersion`（文档版本）、`relationId`/`objectRelation`（关系，平台生成，F12）。
证据：一次真机 `FieldPage` 回读里这些键全部存在，例如
`factory-tuning/data/sessions/acct-fb8dc317-20260921-172228/traffic.jsonl:1`。

另有三个**只在手册里明确、读侧键名待核**的字段能力，`FieldDef` 同样没有对应项：
文本的**加密存储**、**输入格式（正则）**、**错误提示语**（`01B_对象字段.md:224-226`），
以及**唯一值**（同章 :227，读侧键是 `isUnique`）。

**能够表达、但被静默丢弃的两个**：`invisible`、`disabled`（见 §3.3 L-c）——这属于"蓝图答应了你、
请求体里没有"，是本仓最在意的那类失败。

### 5.6 小结：工厂当前的真实能力面（一句话版）

**能建**：对象（`objectClass=1`）、字段（14 种 dataType，属性子集）、选项集、表单布局（部分/排序）、
列表布局（列与顺序）、生命周期状态（新建 + 系统状态改名）、工作流（task/decision/action + 开始节点指定参与人 +
绑状态生成提交按钮）。
**不能建**：状态停用、验证/索引/查重/联动/编号规则、字段过滤与条件/选项依赖/引用约束/控制字段/条件默认值、
树形结构、大容量对象、对象类型、视图、搜索配置、对象动作与记录动作、审批矩阵、DAC/共享规则、通知与消息模板、
输出模板（报表/运行报告/PDF 封面）、检查表、报表、页面链接、Web 动作、外部 API 实时填充、文档与契约锁/签名、
移动端、菜单集；也**没有任何删除/停用类动词**（除状态改名外）。

---

## 6. 本次新增结论索引（N1–N16，便于回填台账）

| # | 结论一句话 | 判据位置 |
|---|---|---|
| N1 | `PIPELINE_STEPS` 不驱动流程，只是给规格用的说明常量 | `orchestrate.py:29`；唯一读者 `spec.py:308/342` |
| N2 | 顶层声明区被丢弃，但**对象内** `rules` 靠 `extra="allow"` 存活并进 `manualItems` | `blueprint.py:149-163`（未声明 rules）+ `orchestrate.py:286-287` |
| N3 | `ObjectDef.meta.lifeCycleId` 是未声明的隐藏入参 | `orchestrate.py:248`；`spec.py:_NOTES` 无此键 |
| N4 | 规格宣称"状态编码必须匹配 `status_*__c`"，但校验器不检查；`CODE_STATUS_RE` 在 `validate_blueprint` 零使用 | `blueprint.py:19/252-350`；`spec.py:331/357` |
| N5 | 记账五条：skipped 不可达 / lifecycle 无顶层 success / workflows 恒 True / picklists 恒 True / 失败不中止 | `orchestrate.py:176-181`、`lifecycle.py:79`、`workflows.py:315-316`、`picklists.py:39-40`、`orchestrate.py:216-230` |
| N6 | `FieldDef.maxLength` 被限死 1–17，与手册（1500 / 32000）冲突 | `blueprint.py:48`；手册 `01B_对象字段.md:223/243/259`；`spec.py:116` |
| N7 | `invisible`/`disabled` 蓝图可写、请求体里永远发 False | `blueprint.py:46-47`、`fields.py:33/52-85`、`spec.py:114` |
| N8 | `participantRef` 蓝图可写、写侧参数从未使用 | `blueprint.py:125`、`workflows.py:66-81`、`spec.py:169` |
| N9 | `normalize_blueprint` 零生产调用方 | `blueprint.py:367`；仅 `tests/test_native.py:106` |
| N10 | 菜单族三处不可达 + 子菜单永不挂布局 | `menus.py:10/45`、`orchestrate.py:310-311` |
| N11 | 幂等只覆盖对象与选项集；字段/状态/工作流的 `*_exists` 零调用方 | `idempotency.py:39/49/59`、`orchestrate.py:186/203` |
| N12 | `REQUIRED_TOOLS` 只钉 3 个工厂工具；工厂面无"改/删/取消"动词 | `routes_agent.py:375-385`；`routes_factory.py:124-130` |
| N13 | 工作流"改/删/删步骤/复制步骤"端点存在但全仓零引用；无任何删除类写入器 | `kernel/endpoints.py:60-62/70-71` |
| N14 | 列表布局列写入链路（含跨对象路径解析）零测试覆盖 | `layouts.py:116-166`；`grep "AddColumns\|LISTLAYOUT" tests/` 零命中 |
| N15 | `MenuSubDef.parentName` 被当成对象编码使用 | `orchestrate.py:308` |
| N16 | 台账 L2 遗留问题"`invisible`/`disabled` 是否生效"的代码侧答案：**根本没发出去** | 同 N7 |
