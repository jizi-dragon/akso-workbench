# 平台五层操作 · 已学清单（答题卡 + 反哺清单）

_2026-09-22 汇总。**这份文档的用途有两个**：① 被考核时的"答题卡"——每条都写清**端点 / 线格式 / 样本量 n / 证据等级**；
② 反哺配置工厂的"差距 → 动作"对照表（§4）。**

> **怎么读**：`[真机]` = 有真机样本（括号里是 n）；`[代码]` = 从工厂代码读出；`[手册]` = 手册原文；
> `[推断]` = 尚无直接证据。**凡结论都带 n** —— 这是 2026-09-22 [学习样本审计](../factory-tuning/reports/2026-09-22-学习样本审计.md)
> 立的规矩：**报告里的形状结论必须附样本数**，因为语料里 `SaveBasicObject` 只有 n=2、`AddColumns` 只有 n=1。

## 1. 五层速查（一张表）

| 层 | 端点 | 真机线格式要点 | n | 工厂现状 |
|---|---|---|---:|---|
| **对象** | `POST /api/platform/BasicObject/SaveBasicObject` | **6–8 键**：`code/name/objectClass/source/status/summaryFields` 恒有；`enableLifeCycle`/`enableSignatures` 视开关出现 | 2 | ✅ 键集一致（8 键） |
| **字段** | `POST /api/platform/BasicObject/SaveField` | **7 键恒有**：`code/dataType/disabled/invisible/name/objectId/statisticsArrange`；类型相关追加：`format`（文本/数字）、`objectRelation`+`referenceObjectId`（引用族）、`picklistId`（选项）、`formulaScript`+`setDefaultValue`（公式）、`maxValue`/`minValue`。**真机从不发 `isRequired`** | 11 | 🔴 **D-H**（工厂发 `isRequired`） |
| **选项集** | `POST /api/platform/ObjectPicklist/save` | `{name, code, options[]}`；选项元素 **5 键** `{name, code, status, operations, sort}`；**同时**还带 `"0"/"1"/"2"` 索引键（值 = 同一个选项对象） | 3 | ✅ 元素键集一致（`OptionDef` 同 5 键） |
| **列表布局** | `POST /api/platform/Listlayout/AddColumns` | `{listlayoutId, listlayoutColumns[]}`；列元素 **3 键** `{fieldId, fieldPath, sort}`，**`sort` 从 1 起**；末列可能被平台强制为 `status_id__sys` | **1** | 🔴 **D-A**（工厂发 `columns`）；🔴 **D-B**（不读旧列，全量替换会清列） |
| **表单布局** | `POST /api/platform/Layout/SaveLayoutDetail` | **建壳 = 11 键、不带 `id`**（`source:3` + `sections/controls/pages/rules` 四个空数组，**响应 `data` 即新布局 id**）；**保存 = 17 键、带 `id`**（多 `activePageCode/activeSectionCode/formEditStatus/id/isMain/skipRequiredValidation`） | 25（17 键 23 条 + 11 键 2 条） | 🔴 **D-C**（工厂只发 4 键、且顶层 `basicObjectId` 应为 `objectId`）；🔴 **D-D**（控件键名/词汇表）；🔴 **D-E**（缺 `GetStableComponentIds` 这一步） |
| （表单布局·配套） | `POST /api/platform/Layout/GetStableComponentIds` | **1 键** `componentIds`，值是**以客户端 UUID 为键的字典**：`{uuid: {objectId, layoutCode, componentType, componentCode}}`；`componentType` ∈ `layout_section` / `layout_control`；响应 `data.stableComponentIds` 回映射 | 10 | 🔴 工厂**从不调用**（D-E） |
| （读侧） | `GET /api/platform/Layout/LayoutList` | **回分页信封** `{count, hasUp, hasNext, datas[], pageIndex, pageSize, rows, pageCount, skip}`；默认布局 = `source == 2` | — | ✅ 已修（D-K，2026-09-22 用户授权） |
| （读侧） | `GET /api/platform/Layout/LayoutDetail` | 单个布局明细：顶层 24 键，含 `sections[]` / `controls[]`；**读侧控件 33 键**（含 `dataType/required/visible/readOnly/width/gridSpan`…） | — | ⚠️ 工厂只在建布局时读它当"种子"，顶层另起 4 键（D-C/D-F） |

## 2. 表单布局这一层：已学的六条（本轮新增，全部有真机判据）

1. **建壳 ≠ 保存**：新建一个表单布局 = **不带 `id`** 的 `SaveLayoutDetail`（**11 键**、`source: 3`、
   四个空数组），**响应 `data` 直接就是新布局 id**；此后一律带 `id` 保存（17 键）。
   `[真机]` n=2（`新建表单布局` / `新建布局`，**名字变了、形状没变**）。
2. **形状是「逐元素」的，不是「逐请求」的** ★ ——同一条请求里可以两种形状并存：

   | 元素 | 本次新增 | 已存在 |
   |---|---|---|
   | 部分（`sections[]`） | **5 键** `{id, code, name, type, columnsNum}` | **62 键**（完整物化） |
   | 控件（`controls[]`） | **8 键** `{id, sectionId, code, name, fieldId, type, gridSpan, attrs}` | **23 键** |

   `[真机]` 3 条独立样本：同一请求内『ceshi』5 键 + 已有部分 62 键；
   新控件 `demo_parent__c` 8 键 + 老控件 23 键；新控件 `demo_number__c` 8 键 + 老控件 23 键。
   ⇒ **纠正**：早期报告里"精简形状 vs 完整形状两种请求体"是**错口径**——"整份看着精简"其实是
   "当次新增元素占满了整份体"，"整份看着完整"是"所有元素都已存在"。
3. **控件 = 字段的投影**：`code` / `name` / `fieldId` 逐字来自所选字段，`type` = **`AK<组件名>`**
   （实测 `AKInput`/`AKInputNumber`/`AKSelectorYesNo`/`AKSelectorSingle`/`AKDateTime`/`AKAttachment`/`AKSelectorObject`
   共 7 个值，对应 9 种 `dataType`，**多对一、非双射**）。`[手册]` `01D §4.1`：**「控件即对象字段，
   新增控件本质是在该部分中引入字段」**。
4. **换 id 是独立一步、且是增量的**：保存"含新组件"之前先调 `GetStableComponentIds`，
   **只报本次新增的组件**（实测 8 / 5 / 1 / 2 个 = 新增部分 + 控件个数）。
5. **弹窗不落盘**：`[真机]` n=1（零写入）——「添加部分」「添加控件」的弹窗只改前端状态，
   **只有布局顶层的「保存」才发起提交**。⇒ 工厂要模拟"加部分/控件"，复现的是**那一次保存提交**。
6. **一次「保存」点击 = 1 条写入**：`[真机]` n=3（空保存×2 → 2 条；+1 控件 → 1 条；+1 部分+1 控件 → 1 条）。
   ⚠️ 仍有一处对不上：段 6 曾出现"一次改动 2 条写入"，更可能是**当时点了两次保存**；
   要彻底定需记录**请求发送时刻**（候选改进 C8；现有时间戳被"事件分发时刻"压平，PITFALLS #74）。

## 2.5 建对象 / 列表布局这一层：**新学的三条**（2026-09-22 15:27–15:31，工厂真机创建 `test_0904__c`）

| # | 事实 | 证据 / n | 对工厂的意义 |
|---|---|---|---|
| **P1** | **建对象时平台自动生成两个默认布局**：表单 `<对象code>_base_layout__ak`（名「默认布局」，`source=2`、`isMain=True`）与列表 `<对象code>_table__ak`（名「所有的<对象名>」，`source=2`） | `[真机]` n=2：`test_0903__c`、`test_0904__c`（两者都是"工厂建的对象"） | "给列表设几列"本来就该改**默认**布局；新建第二个列表布局是**例外**而不是常规 |
| **P2** | **`Listlayout/Save` 要求 `code` 非空** —— `{name, objectId, source:3, status:1}` 被拒：`code=500 message=组件[listlayout]的Code字段不能为空` | `[真机]` n=1（2026-09-22 15:27，`run_blueprint.py` 跑 `test_0904__c`） | 工厂改成"**不给 `code` 就不新建**"，并在退回默认布局时明说（PITFALLS #82）；新建布局的 `code` 约定**仍无样本** |
| **P3** | **两个"布局清单"端点的查询参数名不同**：`Layout/LayoutList` 用 **`basicObjectId`**，`Listlayout/List` 用 **`objectId`**（用错 → `code=500 message=Value cannot be null. (Parameter 'objectId')`） | `[真机]` n=1（只读探针踩到；工厂代码本来就用对了） | ⚠️ 这两个名字**太容易对着改错**：写"批量改名"式的重构前先看这条 |

## 3. 跨五层的四条通用规律

| 规律 | 证据 |
|---|---|
| **键数 = 客户端把模型物化到什么程度**，不是平台的硬要求（同一端点见过 4 / 6 / 8 / 11 / 17 / 23 / 62 键） | `[真机]` 全语料 |
| **写入侧与读取侧词表不同**：读侧控件 33 键（`dataType`/`required`/`visible`/`readOnly`/`width`），写侧只 8/23 键；字段读侧 93 键有 `isRequired`，写侧从不发 | `[真机]` 读侧回读 + `SaveField` n=11 |
| **全量替换语义**：`SaveLayoutDetail` 与 `AddColumns` 都是整份提交（空保存也整份重写）⇒ **必须"读旧 → 合并 → 提交"** | `[真机]` 空保存 ×2 条都带完整内容 |
| **平台自己的客户端不做幂等**：重复点击就重复写（不去重） | `[真机]` 空保存 2 条逐字段相同 |

## 4. 反哺清单（差距 → 动作；**每条都要用户下达**）

| 卡 | 差距 | 动作（最小） | 影响面 | 判据 |
|---|---|---|---|---|
| **D-K** | `LAYOUT_LIST` 回分页信封，代码当数组 ⇒ 表单布局步骤**在读默认布局就抛异常** | 展开 `datas`/`items` | 一行 | ✅ **已修**（用户授权）；判据：`get_form_layouts()` 返回 4 个 dict、`find_default_form_layout()` 命中 `source=2` |
| **D-C①** | 顶层 `basicObjectId`，真机是 **`objectId`** | 改这一个键 | 一行 | 🔴 **真机实测后果**：平台回 **`code=500 message=数据不存在`**（2026-09-22 12:35） |
| **D-C②/D-F** | 工厂只发 4 键，真机 17 键（`code/name/source/isMain/objectTypeId/labelCol/status…` 都是**这份布局自己的身份**） | 读旧详情**原样带回**，不自己编 | 中 | 空保存实测证明平台会整份重写 ⇒ 漏身份键有丢配置风险 |
| **D-D** | 控件键名 `fieldCode`/`ctrlType` + 自造词汇表（`input`/`object`/`date`…） | 改 `code`/`type` + `AK*` 表 | 中（+`keygate` 词表层已盯着） | `keygate.py` 现报 🔴 `fieldCode`/`ctrlType`/`invisible`/`disabled` + 13 个自造类型值 |
| **D-E** | 缺 `GetStableComponentIds` 整步（工厂 `uuid4()` 直填） | 保存新建元素前先换 id | 中 | 真机 10 条样本；`componentTypes` 已实测 |
| **D-B** | 列表布局不读旧列（全量替换） | 读旧列 → 合并 → 提交 | 中 | `AddColumns` 全量替换语义（n=1 但语义明确） |
| **D-A** | `columns` → **`listlayoutColumns`** | 改键名 | 一行 | 真机 n=1 + 平台自己的客户端已给出线格式 |
| **D-G** | 布局只认"本次刚建的字段"，已有字段**静默跳过** | 布局前读一次字段全表 + 返回 `skipped` | 中 | `[代码]` `field_map` 唯一生产者；返回结构无 `skipped` |
| **D-H** | 发真机从不发的 `isRequired` | 必填改由**控件层 `required`** 表达 | 中 | `SaveField` n=11 零出现；控件层有 `required` |
| **D-I** | `maxLength ≤ 17`（手册：文本 1500 / 长文本·富文本 32000） | 按 dataType 定上限 + 改 `spec.py` 注解 | 一行 + 注解 | 纯本地校验，**现在就能判** |
| **D-J** | "半个校验器"：手册 ≥9 处数值硬限一处未校验 | 补校验或显式声明"不校验" | 大 | 手册逐类型数值表 |

## 5. **我还没学会的**（考核时我会先声明，不许含糊）

| 项 | 现状 |
|---|---|
| **读侧线格式** | 语料里 **GET = 0 条**；`Layout/LayoutList`、`LayoutDetail`、`Listlayout/List`、`lifecycleStatus`、`picklist`、`Workflow/*` 的**响应形状**基本靠今天现查，不在语料里（审计 S1/S2） |
| **工厂 57 个建模端点里 50 个未核**（88%） | 尤其：生命周期、工作流、菜单、权限、报表、配置包 —— **一条真机样本都没有** |
| **n=1 的结论** | `AddColumns`（1）、`Popup/save`（3）、`SaveBasicObject`（2）⇒ 只能当"见过"，不能当"规则" |
| **错误路径** | 只见过 1 个拒绝样本（`SaveLayoutDetail` 500「数据不存在」）；400/409/权限拒绝长什么样**没见过** |
| **并发与顺序** | 工厂字段创建是**并发 5**；平台对并发写的行为未验 |
| **"平台会忽略什么"** | 多发的键是被忽略、被存到别处、还是报错 —— **三种都没见过**（D-C/D-D 后果未验的根源） |

## 6. 复算命令（这几条能当场证明上面的表）

```powershell
# 各层键集与样本量（本文件 §1 那张表的来源）
.\.venv\Scripts\python.exe -c "import json,pathlib,collections;rows=[json.loads(l) for d in pathlib.Path('factory-tuning/data/sessions').iterdir() if (d/'traffic.jsonl').is_file() for l in (d/'traffic.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()];c=collections.Counter(r['path'].split('?')[0] for r in rows);print(c.most_common())"

# 表单布局逐元素形状（§2.2）
.\.venv\Scripts\python.exe -c "import json,pathlib;d=pathlib.Path('factory-tuning/data/sessions/acct-fb8dc317-20260922-140348/traffic.jsonl');b=[json.loads(l) for l in d.read_text(encoding='utf-8').splitlines() if l.strip()][1]['postData'];print([(s['name'],len(s)) for s in b['sections']],[(c['code'],len(c)) for c in b['controls']])"

# 建壳形态（§2.1）
.\.venv\Scripts\python.exe -c "import json,pathlib;rows=[json.loads(l) for d in pathlib.Path('factory-tuning/data/sessions').iterdir() if (d/'traffic.jsonl').is_file() for l in (d/'traffic.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()];c=[r for r in rows if 'SaveLayoutDetail' in r['path'] and 'id' not in r['postData']];print(len(c),[r['postData']['name'] for r in c])"
```
