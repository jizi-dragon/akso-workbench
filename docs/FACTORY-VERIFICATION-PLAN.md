# 配置工厂逐条验证与优化方案（**进行中**）

> ## 🔄 状态：进行中（L1 / L2 已跑；L3 列表布局取证跑过一半；其余待办）
>
> **2026-09-22 追加**：监听链路重启并两次自证可用；**段 0 取证完成**（新增事实 **F20–F25**、
> 缺陷候选 **D-C**）；**L3 §7.4 的 A/B 悬案结案 = A**（"录到 0 条"是**没点保存**，
> 见 [PITFALLS #73](PITFALLS.md)）；**键名审计**与**静态闸门**
> （`factory-tuning/tools/audit_keys.py`、`keygate.py`）就位，D-A/D-C 由闸门自动抓出。
>
> **本文档既是方案，也是执行台账。**
>
> **2026-09-21 进展**：用户与 AI 协作者做了三轮真机实战（**创建对象 → 创建字段 → 创建选项集**），
> L1 与 L2 已跑过第一遍，**L1 的怀疑全部有答案、L2 的怀疑有两条被证实并修复**。
> 逐条结论见 §3 各层的「**已知怀疑**」（已就地补答案），证据台账见 §6，
> **本次学到的平台事实见 §8**。
>
> **2026-09-21 晚追加**：**L3 的列表布局那一半做了真机取证**（人工操作 → Monitor 录制 → 逐键对照），
> 确证 **D-A（`AddColumns` 数组键名不符）** 与 **D-B（全量替换却不读旧列）** 两处缺陷候选，
> 新增平台事实 **F16–F19**。同一场的**表单布局与生命周期状态那一半录到 0 条**（原因未分辨，
> 见 §7 与[复盘](../factory-tuning/reports/2026-09-21-L3列表布局取证与监听失败复盘.md)），
> **那套数据不可恢复**。**L3 表单布局 / L5 / L6 仍未跑；L4 只跑了一半。**
>
> **2026-09-22 追加（学习轮 + 监听链路重启）**：通读 **Gaia 产品手册 V3.8**（186 文件 / 36,895 行）
> 并结合工厂代码建立整体认知 → [docs/FACTORY-PLATFORM-COGNITION.md](FACTORY-PLATFORM-COGNITION.md)，
> 逐簇笔记 11 份在 `factory-tuning/reports/gaia-v38-study/`（含
> [`11-creation-dependency-map.md`](../factory-tuning/reports/gaia-v38-study/11-creation-dependency-map.md)：
> 51 行配置物 × 8 列依赖表 + 24 步主序列 + K1–K10 冲突 + 21 条待核）。本轮**只读**，不改产品代码。
> 新增代码层发现 **T8 / T8b / T9 / T10**（§4），并**更正 T2 的后半句**；新增决策 E1–E6（见认知文档 §7）。
> 监听链路已重新验明可用（自造流量 `total: 240`；口径见 factory-tuning README），
> 真机取证按认知文档 §8 分段进行（每段 stop + 脱敏入库，**不再让用户白演示**）。
>
> 用户 2026-09-20 的原始指示：
>
> > 「配置工厂的整体逻辑应该是有问题的，我需要单独对规则一条一条优化，具体的优化方法为：
> >  我与你对话，你就根据规则，一个功能一个功能的测。从单个创建对戏（对象），
> >  再到字段，再到列表，生命周期。**第二条暂时先不做，但是要做成方案，存储下载，
> >  注明为待办事项。**」
>
> 执行方式见 §1；一条跑完就把它的框打上，并在 §6 的证据台账里补一行。

---

## 0. 一句话说明为什么要一条一条测

现在的状态是：**整条链路一次跑完，任一步坏了都只能看到"某个对象失败了"**。

`run_full_workflow` 把一个对象要走完的 8 步（选项集 → 对象 → 字段 → 表单布局 → 列表布局 →
生命周期 → 工作流 → 回读验证）压在**一次调用**里，中途任何一个平台的返回不合预期，
只能从 `proc.log` 的几百行里翻。而"对象建出来了但字段类型错了""布局保存了但没生效"
这类问题**不会让流程失败**——它们只是静静地错着，直到有人在平台上点开看。

所以要换一种测法：**一次只验一种能力，且每一层都要求"平台侧回读到的样子"作为判据**，
而不是"接口返回 success"。

## 1. 协作方式（用户 + 我）

| | 谁做 | 做什么 |
|---|---|---|
| 1 | **用户** | 指定这一轮测哪一层（按 §3 的顺序，也可以跳） |
| 2 | 我 | 给出该层的**最小测试蓝图** + 说明"我要你在平台上核对什么" |
| 3 | **用户** | 让 Agent 在对话里执行（或自己在配置工厂页面上传执行），**然后在平台上肉眼核对** |
| 4 | 我 | 对着证据判定：通过 / 不通过；不通过就定位到具体文件与函数，给出修法 |
| 5 | 我 | 修完补**回归断言 + 反证**（本仓纪律：断言必须实测对旧实现 FAIL），需要时补 PITFALLS |
| 6 | 我 | 在 §6 台账里补一行（层 / 日期 / 蓝图 id / job id / 结论） |

**一条一轮，不合并。** 合并了就无法区分"是哪一层坏的"——那正是现在的问题。

### ★ 裁量规则（**用户 2026-09-22 定稿，优先级最高**）

> 「**产品手册只是给你借鉴，最终决策 / 差异请让我下达，以我的决定为主。**」

⇒ 落到我的动作上，有**四条硬约束**：

1. **手册不是裁判，是参考。** 手册 ↔ 平台真机 ↔ 工厂实现三者不一致时，
   我的职责是**把差异摆清楚**（哪一侧是什么、证据是什么、后果可能是什么），
   **不是**判定"谁对谁错"，更不是按手册去改代码。
2. **凡有差异 → 一律标注「待用户下达」**，写成一张可拍板的表（差异 / 证据 / 选项 / 影响），
   由用户选定后才进入修法。
3. **不得以"手册这么写"为理由**单独支撑任何一条改动建议——
   手册的条文只能与**真机证据**并列呈现，取舍权在用户。
4. **产品代码的任何一行改动，都必须先有用户的下达。** 在此之前只产出：证据、差异表、修法选项。

> 这条规则**回头生效**：本计划与各报告里此前用「🔴 与手册冲突 ⇒ 应按手册改」措辞的地方，
> 一律**降级为「差异，待用户下达」**（例如 `maxLength` 的 1500/32000、字段类型的 16 vs 14、
> 校验面的"半个校验器"）——**证据保留，结论收回**。

### 用户每一轮需要提供的东西（只要这三样）

1. **测试环境**：用哪个账号（决定写进哪个平台环境）；
2. **平台侧截图或描述**：那一层建出来长什么样（这是唯一的判据，接口返回不算数）；
3. **是否允许留痕**：允许的话，把 `job_id` 与产物目录留下，我直接读日志与 checkpoint。

## 2. 每层的统一结构

每一层我都会给出下面六项，格式固定，便于你直接照做：

| 项 | 说明 |
|---|---|
| **测什么** | 这一层唯一要回答的问题（一次只回答一个） |
| **测试蓝图** | 最小、可重复的输入。**每次都用新的对象编码**（加日期后缀），因为平台上的对象删不干净 |
| **怎么跑** | 对话里说一句，还是在配置工厂页面执行 |
| **平台侧核对点** | 你要在平台上点开看的那几处（**这是判据**） |
| **接口侧证据** | 我从哪里看"引擎以为它做了什么"（`job_id` / `proc.log` / `checkpoint.json` / 产物） |
| **不通过时看哪** | 预先指定嫌疑文件与函数，避免当场乱翻 |

## 3. 分层顺序与各层要点

> 顺序按依赖走：对象 → 字段 → 布局 → 生命周期 → 工作流 → 菜单/声明区。
> 后一层依赖前一层建出来的东西，所以**不要跳着来**。

### L0 · 只读预检（不写平台）

| 项 | 内容 |
|---|---|
| **测什么** | 这条链路在**不碰平台**的前提下，能不能把"将要建什么"讲清楚 |
| **怎么跑** | 对话里说"看一份蓝图的规格"，或直接调 `factory_blueprint_spec` |
| **判据** | 规格里能查到你要用的每个键的说明；`factory_preview_blueprint` 返回的审阅清单与蓝图一致；**校验不过时不产生 `blueprint_id`** |
| **不通过时看哪** | `workbench/services/egmp/writers/spec.py`、`tests/test_factory_agent.py` |

> L0 已经由 `tests/test_factory_agent.py` 与 `tools/verify_factory_agent.py` 覆盖（24 项），
> **不需要真机**。列在这里是为了说明"上一层没通过时，先回来跑 L0"。

### L1 · 单个对象创建

| 项 | 内容 |
|---|---|
| **测什么** | 只建一个对象（**零字段以外的最简形态**），平台侧是否真的出现，且属性对不对 |
| **测试蓝图** | `objects: [{name, code: t1_<日期>__c, enableLifeCycle: false, enableSignatures: false, fields: [一个文本字段]}]` |
| **平台侧核对点** | ① 对象列表里能看到它；② 编码与显示名与蓝图**逐字一致**；③ 生命周期是**关闭**状态；④ 字段里那个文本字段在 |
| **接口侧证据** | `factory_create` 返回的 `steps.object` / `steps.verify`；产物 `checkpoint.json` |
| **不通过时看哪** | `writers/objects.py`、`orchestrate.run_object_workflow` 的 `_object()` / `_verify()` |

**这一层的已知怀疑 —— 2026-09-21 真机跑过，两条都有答案**：

- ✅ **`source` / `status` / `objectClass` = 3 / 1 / 1 成立**。平台回读的对象记录里
  这三个键就是 `3 / 1 / 1`，自定义对象在该环境成立。
- ⚠️ **读侧缓存确实需要失效 —— 而且这是个真陷阱**：`_verify()` 读之前会先
  `invalidate_object_cache(code)`（`orchestrate.py` 第 275 行），所以**产品里是对的**。
  但本次 AI 协作者的取证脚本漏了这一步，于是「建完立刻回读」拿到的是**建之前的旧列表**，
  当场误判成「6 个字段没建出来」。⇒ 凡在**同进程内写完立刻读** `get_object_info` /
  `get_fields` / `get_statuses`，都必须显式失效；而 `get_picklist` **不是**缓存的
  （两者口径不同，别记混）。
- ✅ **另外确认了一件本层没写的事**：`enableLifeCycle: true` 会**真的provision出生命周期** ——
  平台记录里 `lifeCycleId` 非空，且状态列表里有系统自带的 2 个状态；
  而 `enableLifeCycle: false` 的对象 `lifeCycleId` 为 `null`、状态数为 **0**。
  同时**开启生命周期会额外多一个系统字段 `status_id__sys`**（没开的对象没有它）。

### L2 · 字段（按 `dataType` 逐个）

| 项 | 内容 |
|---|---|
| **测什么** | **每一种 dataType 各建一个**，逐个核对平台侧的类型、必填、约束、关联目标 |
| **测试蓝图** | 一个对象 + 覆盖 `1 文本 / 2 数字 / 3 是否 / 4 选项 / 5 日期 / 7 日期时间 / 8 附件 / 13 查找 / 14 公式 / 15 对象 / 16 父对象 / 17 长文本 / 18 富文本 / 23 对象多选` 各一个字段 |
| **平台侧核对点** | 逐个字段点开：类型对不对；`isRequired`、`invisible`、`disabled` 是否生效；数字字段的 `max/min/decimalPlaces`；选项字段绑定的是不是**蓝图里那个**选项集；引用字段指向的是不是**蓝图里那个对象**；附件字段的三个约束；公式字段能不能算出值 |
| **接口侧证据** | `steps.fields` 的 `results[]`（每个字段一条）、`fieldMap` |
| **不通过时看哪** | `writers/fields.py::batch_create_fields`（**并发 5**）、`writers/picklists.py` |

**这一层的已知怀疑 —— 2026-09-21 真机跑过，逐条给答案**：

- ✅ **并发 5 建字段平台接受**：6 个字段并发建完，平台侧 **6/6 全部存在**，没有出现
  "部分成功而 `success` 仍为真"。注意 `SaveField` **只回 `{code:0, data:true}`、不回 id**，
  所以 `fieldId` 全是**回读**来的（见 `docs/PITFALLS.md` #62）。本次共建成 **9 个** `demo_*` 字段。
- ⚠️ **`dataType=4`（选项）+ 选项集绑定：曾经是坏的，已修**。`resolve_picklist_id` 底层走的
  OpenAPI 端点**路径写错了**（`/api/openapi/v1.0/ObjectPicklist/{code}` 恒 404），
  于是 `picklistId` **根本不会进请求体** —— 选项字段会被**静默**建成"没有选项集的选项字段"，
  而接口照样回 `data:true`。修好后真机复验：请求体里出现 `picklistId`，平台回读也有值。
  见 `docs/PITFALLS.md` #67。
- ⚠️ **数字字段的 `max`/`min`/`decimalPlaces`：曾经发出去不落地，已修**。平台的键名是
  **`minValue` / `maxValue` / `scale`**，而 `collect_extras` 发的是 `min` / `max` /
  `decimalPlaces` —— 键名不符，**静默丢弃**。修好后真机复验：`minValue='5' maxValue='666' scale=2`。
  见 `docs/PITFALLS.md` #69。
- ❌ **`dataType=14`（公式）的 `returnType` + `formulaScript` 仍未验证** —— 本次没建公式字段。
- ❌ **本层蓝图中列的 8 / 13 / 17 / 18 / 23 五种 dataType 本次没建**：
  已覆盖 **1 文本 / 2 数字 / 3 是-否 / 4 选项 / 5 日期 / 7 日期时间 / 15 对象 / 16 父对象**。
  （8 附件、13 查找、17 长文本、18 富文本、23 对象多选、14 公式 待补。）
- ⚠️ **「对象(15)」与「父对象(16)」是两种东西，不是同一个类型换个名字**：
  平台的关系记录里 `objectRelation.type` 一个是 **2**、一个是 **1**。
  按手册（`04_配置指南/01B_对象字段.md` §3.9 / §3.11）：**对象=引用关系**（记录可独立存在），
  **父对象=父子关系**（子对象不能脱离父对象独立存在，层级 ≤ 3）。
  ⇒ **建模时不要互相替代**；本层的"逐个 dataType 建一个"清单里两者都要有。
- ✅ 另确认：平台会给字段补默认值 —— `日期(5)` 补 `format=YYYY-MM-DD`、
  `日期时间(7)` 补 `format=YYYY-MM-DD HH:mm:ss`；工厂不发这两个键也能得到同样的终态。

### L3 · 布局（表单布局 + 列表布局）

| 项 | 内容 |
|---|---|
| **测什么** | 布局**保存了并且在界面上生效**，而不是"接口没报错" |
| **测试蓝图** | L2 的对象 + `formLayout.sections`（两个部分、部分内字段顺序）+ `listLayout.columns`（列的字段与顺序） |
| **平台侧核对点** | ① 表单里能看到部分名与部分顺序；② 字段所在的**部分正确**、部分内顺序一致；③ 列表视图的列与顺序一致；④ 系统字段（创建时间等白名单字段）也能出现在布局里 |
| **接口侧证据** | `steps.formLayout` / `steps.listLayout` |
| **不通过时看哪** | `writers/layouts.py::build_form_layout` / `build_list_layout` |

**这一层的已知怀疑 —— 2026-09-21 列表布局那一半真机跑过，逐条给答案**：

> **★ 2026-09-22 现状（L3 至此已跑 **6 段**真机取证）**——上面那张表是**原始计划**，
> 下面这段才是"现在到哪了"：
>
> - **表单布局：已充分取证**。段 0–4 共 **5 次保存**全部录到（`SaveLayoutDetail`，
>   且抓到它前面的 `GetStableComponentIds` 协议步骤）：顶层 **17 键**、
>   section **62 键**、控件 **23 键**（精简形状另有 5 / 8 / 14 / 15 / 16 键的实例）；
>   见[段 2](../factory-tuning/reports/2026-09-22-真机取证-段2-新建部分与整行.md) ·
>   [段 3](../factory-tuning/reports/2026-09-22-真机取证-段3-三条保存与AK词汇表.md) ·
>   [段 4](../factory-tuning/reports/2026-09-22-真机取证-段4-新部分与AK补全.md)。
> - **★ 段 5 补上了 F66 的空白**：**"新建表单布局"的完整三步线格式**已录到
>   （建壳=不带 `id` 的 `SaveLayoutDetail`（**11 键**、`source: 3`、四空数组）→ 响应 `data` = 新 id
>   → 带 id 保存补 section/control（17 键、`isMain: false`）；每一步引入新组件前先
>   `GetStableComponentIds`）。见[段 5 报告](../factory-tuning/reports/2026-09-22-真机取证-段5-新建表单布局.md)。
>   同一次采集还新增两条**待用户下达**：**N1** `source: 3` 同时出现在"新建表单布局"（真机）与工厂的
>   列表布局写入 ⇒ `source` 更像"来源/是否自定义"而非"布局种类"；**N2**
>   `ListlayoutRecord/GetDropdownListApiProject`（只读下拉查询）被 `QUERY_POST_PATTERNS` 的
>   `Get.*List$` 漏掉 ⇒ 记成了"陌生 API"，是**工具侧假阳性**。
> - **★ 2026-09-22 首次「L3 真机验证」跑到了第二道坎**（用户下达「在默认布局下增加一个控件」）：
>   ① 新发现 **D-K · `LAYOUT_LIST` 回的是分页信封，而 `get_form_layouts` 当它是数组**
>   （`list(dict)` 拿到键名 → `layout.get("source")` 抛 `AttributeError`）⇒ **表单布局这一步在
>   "读默认布局"就断**，根本走不到保存；连带说明 F66 记的失败形态（「返回 `success: False` +
>   默认表单布局未找到」）**与真机不符**。② **D-C 的"后果未验"首次有真机读数**：读侧修好后，
>   工厂发出的 `SaveLayoutDetail` 被平台以 **`code=500 message=数据不存在`** 拒绝
>   （浏览器真机同一端点发的是 `objectId` + **11/17 键**，工厂发 `basicObjectId` + **4 键**）
>   ⇒ 首要嫌疑正是 **D-C①**；**D-D / D-E 尚未走到**（它们排在第二道坎之后）。
>   ③ **平台未被弄坏**（默认布局与基线逐项一致；唯一残留是对象上多了字段 `layout_probe__c`）。
>   全过程、判据与复现步骤见[真机验证报告](../factory-tuning/reports/2026-09-22-真机验证-默认布局加控件.md)。
> - **★★ 2026-09-22 14:26：批一/批二/批三已按用户下达改完，且批二真机验证通过。**
>   改了什么、每条卡的当前状态、以及**闸门自身的两处覆盖修补**（动态体识别 + "先赋值再 append"，
>   后者由 selfcheck **K9** 当场抓到）全部记在[决定简报 §5](FACTORY-DECISION-BRIEF.md)。
>   一句话结论：**`formLayout` 步骤第一次在真机上成功** —— 默认布局多了一个控件（10 → 11），
>   而 **4 个部分 / 4 个布局 / 布局身份（`source=2`、`isMain=true`、id）全都没变**。
>   **仍未做**：全量 pytest（本仓那条用例会真弹更新框到你屏幕，等用户发话）、应用侧重启
>   （跑着的 sidecar 仍是旧模块）、**批四**（F66 新建表单布局 / D-J 数值校验 / F70 两类字段）。
> - **核对点 ④ 已有证据**：**系统字段确实会作为控件出现在布局里**（段 4 的 `name__ak`，
>   平台自生成 code、**没有 `dataType`**、8 键精简形状）。
> - **列表布局**：仍只有 2026-09-21 那一次（10 列 + 末列强制 `status_id__sys`）；
>   `AddColumns` 的真机键名 `listlayoutColumns` 已确证，且 F26 给了**免回读自检**（响应 `data` = 列数）。
> - **本层缺陷已扩到 6 张卡**（`D-A` / `D-B` / `D-C①` / `D-D` / `D-E` / `D-F` / `D-G` 里的布局部分），
>   闸门也从"顶层一层"扩到**三层**（`keygate.py`：顶层 / 嵌套 / 词表，另加经助手与函数返回的体）。
>   **修法与建议顺序见[段 1 报告 §8](../factory-tuning/reports/2026-09-22-真机取证-段1-控件与半行整行.md)。**
> - **L3 仍未验的只剩三条**：① 列表布局的 `.` 点号跨对象路径列；
>   ② `columnsNum = 3/4` 时的 `gridSpan`（**用户 2026-09-22 定稿：只是显示形式，不追究**，见 F28）；
>   ③ 平台"自动派生"的标准布局（**T9**，属口径决策）。

- ⚠️ **`build_form_layout` 的幂等性在代码层已可判定（真机待验）**：它**按 `name` 复用**部分
  （同名 → 复用那个部分的 `id`，不新建；见 `layouts.py:72-83`），
  而 `sections = [dict(s) for s in old_sections]` 意味着**旧部分连 `sort` 都原样复制**
  ⇒ **蓝图里 `sections` 的书写顺序对已存在的部分完全没有影响**，工厂**做不到给已有部分重排**。
  改名则当成**新部分追加**，旧部分**留着**（累积，不删）。
- ❌ **列表布局的列 `fieldCode` 允许带 `.`（点号跨对象路径）——仍未验**。本次采集到的 10 列
  **全是本对象字段**（含末列强制的 `status_id__sys`），没有一条点号路径。
- ⚠️ **新增两条候选缺陷（见下方「本次新增」）**——其中键名那条已由真机请求体确证不符。

**本次新增（2026-09-21 真机取证，明细见
[factory-tuning/reports/2026-09-21-L3列表布局取证与监听失败复盘.md](../factory-tuning/reports/2026-09-21-L3列表布局取证与监听失败复盘.md)）**：

- **D-A · `AddColumns` 的数组键名不符（真机已确证不符；后果未验）**：
  真机发 `"listlayoutColumns"`，工厂发 `"columns"`（`layouts.py:119`）。
  端点路径、列元素形状、`sort` 基准**三项全对**，差别只在这一个键名上。
  ⚠️ 后果**未验**——`AddColumns` 是全量替换语义，最坏情形是"被当成空列表 → 清空列"，
  即又一次「接口回成功、回读才发现空」。定性方法见复盘 §9 第 2 条。
- **D-C · `SaveLayoutDetail` 的顶层对象键不符（2026-09-22 真机确证，与 D-A 同族）**：
  **真机发 `objectId`，工厂发 `basicObjectId`**（`writers/layouts.py:51`）。
  而**同一个文件**里的列表布局那条（`:133` 的 `LISTLAYOUT_SAVE`）用的正是 `objectId`
  ⇒ **同一文件内部两套口径**。真机请求体同时显示：顶层 **17 键**（工厂只发 4 键）、
  section 单键 **62 个**（工厂只发 6 个）。
  ⚠️ 后果**未验**（平台是忽略未知键、静默存到别处、还是报错，三种都没见过）；
  证据与逐键对照见 [段 0 取证报告](../factory-tuning/reports/2026-09-22-真机取证-段0-默认布局保存.md)。
- **键名审计（2026-09-22）**：把"真机见过请求体"的 **8 个端点**与工厂请求体**逐键对照** ⇒
  **6 个两边都有的端点里，4 个完全一致，2 个不符——正是 D-A 与 D-C，且都在布局写入器里**。
  ⇒ 这类缺陷**不是均匀分布的**，它集中在 `writers/layouts.py`——而那恰好是唯一一条
  `grep "AddColumns\|LISTLAYOUT" tests/` **零命中**的链路。
  判据：`python factory-tuning\tools\audit_keys.py`（**每次新增采集后重跑，对照面自动扩大**）；
  见 [键名审计报告](../factory-tuning/reports/2026-09-22-键名审计-真机vs工厂.md)。
- **D-D · 控件的键名与词汇表都不符（2026-09-22 段 1 真机确证，与 D-A/D-C 同族）**：
  工厂 `layouts.py:91-97` 造的控件是 7 键，其中 **`fieldCode`**（真机叫 **`code`**）、
  **`ctrlType`**（真机叫 **`type`**，且值域是 **`AK<组件名>`**：实测 `AKAttachment`/`AKSelectorObject`/`AKDateTime`，
  工厂那套 `attachment`/`object`/`date` 是**自造的**）、
  **`invisible`/`disabled`**（真机里没有这两个键，对应的是 `visible`/`readOnly`，且为 `null` 表示"不覆盖"）；
  工厂**完全不发** `gridSpan`/`attrs`/`dataType`/`name`。
  ⚠️ **`keygate.py` 抓不到它**——闸门只扫 `client.post(..., {字面量})` 的实参，
  而控件是循环里 `controls.append({...})` 拼出来的 ⇒ **闸门覆盖"顶层字面量"，不覆盖"拼装的子结构"**。
  证据与逐键对照见 [段 1 取证报告](../factory-tuning/reports/2026-09-22-真机取证-段1-控件与半行整行.md)。
- **D-E · 少了"稳定 id"这**一整步**（2026-09-22 段 1 真机确证"平台客户端会做"，工厂不做是代码确证）**：
  平台自己的客户端在保存新建的部分/控件前，**先调 `/api/platform/Layout/GetStableComponentIds`**
  把浏览器里生成的 UUID 换成服务端 id（请求带 `componentType: layout_section / layout_control` 与 `componentCode`，
  响应 `stableComponentIds: {客户端UUID → 服务端 id}`），**然后**才发 `SaveLayoutDetail`。
  而工厂 `layouts.py:77`、`:92` 是 `str(_uuid.uuid4())` **自造 UUID 直接进请求体**，**从不调这个端点**。
  ⚠️ 后果未验（平台可能接受、静默丢弃、或错绑），但它是 `#62/#63/#67/#69` 那一族
  （"接口回成功、回读才发现空"）最典型的**缺步骤**形态，且与 **D-C/D-D 叠在同一条链路上**。
  判据（可复跑）：真机序列里 `GetStableComponentIds` **在** `SaveLayoutDetail` **之前**
  —— `python factory-tuning\tools\audit_keys.py` 能看到这两个端点。
- **D-B · 全量替换却不读旧列（代码已确证）**：`LISTLAYOUT_COLUMNS`
  在 `writers/` 下**零调用**（全仓唯一调用方是只读洞察层 `insight/lifecycle.py:149`）。
  而**规则就写在 `layouts.py` 第 4–5 行**：「必须先读旧结构、合并新内容后再整体提交
  （漏读合并 = 清掉平台已有布局）」——同一文件里的 `build_form_layout` 遵守了，`build_list_layout` 没有。
  它和 **F16**（开了生命周期的对象上「状态」列不可移除）**正面相撞**。
  判据：`python -c "import pathlib;print('LISTLAYOUT_COLUMNS' in pathlib.Path('workbench/services/egmp/writers/layouts.py').read_text(encoding='utf-8'))"` → 必须 `True`（实测 `False`）。
- ⚠️ **这条链路零测试覆盖**：`grep "AddColumns|LISTLAYOUT" tests/` **零命中**。
  对照 #67（夹具照着错路径写、回归守着错行为），这边是**根本没有夹具**。

### L4 · 生命周期（状态 + 系统状态重命名）

| 项 | 内容 |
|---|---|
| **测什么** | 新增状态、重命名系统状态、以及"状态在界面上按预期出现" |
| **测试蓝图** | `enableLifeCycle: true` + `renameStatuses`（改 `init_status__c` / `complete_status__c` 的显示名）+ `statuses`（两个新状态） |
| **平台侧核对点** | ① 系统自带的两个状态**还在**（不可删）；② 显示名被改成了你要的名字；③ 两个新状态出现且**名称与编码一致**；④ 状态之间的流转关系符合预期 |
| **接口侧证据** | `steps.lifecycle` 的 `statusMap`、`lifeCycleId` |
| **不通过时看哪** | `writers/lifecycle.py::build_statuses`、`orchestrate.run_object_workflow` 的 `_lifecycle()` |

**这一层的已知怀疑（待验）**：

- `life_id` 的来源是 `obj["meta"]["lifeCycleId"]` → 回落到 `get_object_info(code)`。
  **新建对象紧接着回读**，平台侧可能还没生成 `lifeCycleId`（或者读侧缓存是旧的）→
  `life_id` 为空时会怎样？`build_statuses(lifecycle_id="")` 是报错还是静默什么都不做？
- `validate_blueprint` 对"不存在的 `bindToStatusCode`"是 error，但"没有 lifecycle 却配了 workflows"
  只是 **warning**——所以 `life_id=""` 这条路是**真实可达**的。

### L5 · 工作流（审批任务 / 判断 / 动作）

| 项 | 内容 |
|---|---|
| **测什么** | 工作流建出来了、步骤顺序对、审批人绑定对、绑定状态生成了提交按钮 |
| **测试蓝图** | L4 的对象 + 一条 `task → decision → action(9=修改状态)` 的工作流 + `bindToStatusCode` |
| **平台侧核对点** | ① 工作流存在且**启用**；② 步骤顺序与你写的一致；③ 参与人（会签/或签）对；④ 在绑定的那个状态下**能看到提交按钮**；⑤ 走一遍：提交后状态真的变了 |
| **接口侧证据** | `steps.workflows` 的 `built[]` |
| **不通过时看哪** | `writers/workflows.py::build_workflow` |

**这一层的已知怀疑（待验）**：

- 工作流步骤的 `behaviorType` / `behaviorValue` / `dispatchMode` 的取值来自
  `kernel/semantics.py`（上游实证词典）。词典里 `behaviorType=46` 是真机实证的，
  其余（5 / 7 / 9）标的是"上游实证"——**在这台环境的平台上是否仍然成立**？
- `_workflows()` 从 `result["steps"]["lifecycle"]["lifeCycleId"]` 取生命周期 id。
  若 L4 那一步失败，这里是**静默拿到空串**还是明确报错？

### L6 · 菜单 与 声明区（规则 / 权限 / 报表模板）

| 项 | 内容 |
|---|---|
| **测什么** | ① 蓝图里的 `menu` 节到底有没有被创建；② `rules` / `permissions` / `reportTemplates` 有没有出现在"人工配置清单"里 |
| **平台侧核对点** | 菜单树里有没有母菜单与子菜单 |
| **接口侧证据** | `run_full_workflow` 返回的 `manualItems[]`；确认框里的摘要文案 |
| **不通过时看哪** | `orchestrate.execute_menu`（见 T1）、`orchestrate.run_full_workflow` 的 `manual_items`（见 T2） |

---

## 4. 读代码时已经发现的两处缺陷（**有判据，尚未真机复现**）

> 这两条不是"猜"，判据都能一行跑出来；但按本仓惯例，**没在真机上看到现象之前不写进
> `docs/PITFALLS.md`**。修完它们时，各自必须补一条 PITFALLS 与一条反证。

### T1 · `menu` 节从来没有被执行过

**判据**（一行，**用 AST 而不是 grep**——名字在文档串/注释里出现过，grep 会骗人）：

```powershell
.\.venv\Scripts\python.exe -c "import ast,pathlib; hits=[(p.as_posix(), n.lineno) for p in pathlib.Path('workbench').rglob('*.py') for n in ast.walk(ast.parse(p.read_text(encoding='utf-8'))) if isinstance(n, ast.Name) and n.id=='execute_menu']; print('调用点:', hits)"
# 实测输出：调用点: []      ← 空列表 = 只有定义、没有调用
```

> 这条判据本身也踩过一次坑：第一版写的是 `Select-String 'execute_menu'`，
> 而「函数定义行」与「文档串里的散文提及」都会命中，于是"零调用方"这个结论
> 从输出里**读不出来**。凡是要证明"某函数没人调用"，就别用 grep。

**后果**：蓝图里写了 `menu`，引擎**什么也不做**，且流程照常报"成功"。
更糟的是**确认框会替你承诺**："含菜单挂载"。

**当前已做的缓解**（2026-09-20）：`writers/spec.py::blueprint_summary_line` 把菜单归到
"**引擎不执行**，需要你人工配置"里，确认框不再说"将创建菜单"。
那只是**如实描述现状**，不是修复。

**真正要决定的是**（待你拍板，见 §5 决策 D1）：
把菜单接进 `run_full_workflow`（并在 8 步管道里给它一个位置），还是把菜单正式划为
"人工配置项"并从"引擎会做的事"里拿掉。

**涉及**：`orchestrate.py::execute_menu` / `run_full_workflow`、`writers/menus.py`、
`plugins/factory.py` 的 `_create_summary`、`tests/test_native.py`

### T2 · 顶层声明区（规则 / 权限 / 报表模板）既不执行，**运行期**也不进 `manualItems[]`

> **2026-09-22 更正后半句**：原文写的是"也不进人工配置清单"，实测**只对了运行期这一半**——
> 落盘的 `spec.md` 里「## 人工配置项（引擎不执行）」**已经**把 `rules` / `permissions` /
> `reportTemplates` 逐条列出来了（`writers/blueprint.py:472-483`）。
> **缺的是运行期返回值**：`run_object_workflow` 只从**对象内** `obj.get("rules")` 收集
> （`orchestrate.py:286-287`），而顶层声明区在 `to_object_blueprints` 归一化时就被丢掉 ⇒
> `run_full_workflow()["manualItems"]` 恒为空。
> ⇒ 修法比原来设想的窄：**不是"补一份清单"，而是"把顶层声明区接到同一条清单管道上"**。

**判据**（一行，实测输出 `False`）：

```powershell
.\.venv\Scripts\python.exe -c "from workbench.services.egmp.writers.blueprint import to_object_blueprints as t; bp={'objects':[{'name':'A','code':'a__c','fields':[{'name':'f','code':'f__c','dataType':1}]}],'rules':[{'name':'r'}]}; print('rules' in t(bp)[0])"
# 实测输出 False —— 顶层声明区在归一化时就被丢掉了
```

**根因**：`manual_items` 只从 `obj.get("rules")` 收集，而 `to_object_blueprints`
返回的是 `ObjectDef` 的 dump——`rules` / `permissions` / `reportTemplates` / `menu`
都长在**顶层** `Blueprint` 上，于是永远收不到。

**后果**：用户在蓝图里写了 5 条规则、2 个权限、1 个报表模板，跑完之后
**没有任何地方告诉他"这几项要你手工配"**。而 `run_full_workflow` 的返回值里
`manualItems` 是空的——看起来像"没有需要人工做的事"。

**涉及**：`writers/blueprint.py::to_object_blueprints`、`orchestrate.run_full_workflow`、
`writers/spec.py::blueprint_highlights`

### 其它一并要验的宽松判据（`orchestrate.py`）

| # | 现象 | 为什么可疑 |
|---|---|---|
| T3 | `record()` 的 `status` 判定是 `outcome.get("success", True)` | **默认 True**：某一步的返回里没有 `success` 字段就被算成功。要逐层确认每个 writer 都真的返回了它 |
| T4 | `run_object_workflow` 末尾 `result["success"] = all(s.get("status") != "failed" ...)` | 与 T3 是两套判定叠加（一处看 `success`、一处看 `status`）。**一处默认 True 就可能把失败洗成成功** |
| T5 | `verify` 只核对"对象存在 + 字段数 + 状态数" | 不比对**内容**（类型/绑定/布局是否生效）。所以"8 步全绿"推不出"建对了"——这正是 L2/L3 必须人工核对的原因 |
| T6 | 多对象时 checkpoint 的 key 是 `f"{code}:{step}"`，单对象时**不带前缀** | 同一个 job 里对象数从 1 变多时，前缀规则变了 → 断点续跑可能重复建或漏建 |
| T7 | `precheck_blueprint` 的冲突**只告警不阻断** | 对象编码已存在时会提示建议后缀 `_v2`，但流程继续走（幂等会跳过）→ 用户以为新建了，其实是复用了旧的 |

### T8 · 生命周期「改名」这条路是断的，而且崩得很响（2026-09-22 读代码发现，**未真机复现**）

**判据（两条命令）**：

```powershell
# ① 调用点用的是 PATCH + client.request
.\.venv\Scripts\python.exe -c "import pathlib;print([l for l in pathlib.Path('workbench/services/egmp/writers/lifecycle.py').read_text(encoding='utf-8').splitlines() if 'request(' in l])"
# 实测：['    client.request(\"PATCH\", STATUS_UPDATE, body)']      ← lifecycle.py:42

# ② 但 EgmpClient 根本没有 request 方法（只有 get / post / paged_post）
.\.venv\Scripts\python.exe -c "import inspect,pathlib,re;t=pathlib.Path('workbench/services/egmp/client.py').read_text(encoding='utf-8');print(re.findall(r'def (\w+)\(self',t))"
# 实测：['__init__','scope','close','__enter__','__exit__','_envelope','get','post','paged_post',...]  ← 无 request
```

**后果比"改名失败"重**：`record()`（`orchestrate.py:176-181`）**不包 try**，
所以这个 `AttributeError` 会**直穿 `run_object_workflow` 与 `run_full_workflow`**：

1. 平台**已经被写入一半**（对象 / 字段 / 表单布局 / 列表布局都建完了）；
2. job 崩在**第 6 步**，`manualItems` **永不返回**，调用方拿到的是异常而不是结果字典；
3. 因为 `build_statuses` 也**从不返回顶层 `success`**（`lifecycle.py:79`），
   在**没走到改名**的路径上，新增状态失败会被 `outcome.get("success", True)`
   **洗成 `success`**（T8b，与 #62/#63/#67/#69 同形）。

**为什么会一直没被发现**：`FakeEgmpClient` **恰好有** `request`
（`tests/fakes.py:99`，只 append trace 就 `return {"ok": True}`，**不改任何状态**），
且它预置了平台默认状态、示例蓝图恰好命中"改名"分支 ⇒ **测试全绿，实际什么都没做**（方法论 M2 的又一例）。

**验证方式（不需要写平台）**：`AttributeError` 在任何 HTTP 发出**之前**抛出 ⇒
跑一次带 `renameStatuses` 的蓝图**不会产生任何平台写入**，可以安全复现。
但**先取演示第 3 段的真机证据**：平台对"停用/改名"到底收 `PATCH` 还是 `POST`（决定修法，见认知文档 §7 的 E5）。

### T9 · 平台的"自动派生"在工厂里**零识别**（2026-09-22，来自依赖图 K10；**未真机复现**）

**症状**：平台在"建对象"那一刻就已经替它铺好了东西——**系统字段**（含 `status_id__sys`）、
**系统默认索引**、**标准列表布局**（`所有的{对象}`，Code `{对象Code}_table__ak`，**不可删**）；
开对象类型 / 审批矩阵 / 检查表类型还会**一次铺开整族**对象与选项集。
而工厂的管道、幂等层、`verify` **对这一切既不识别也不避让**。

**判据（两条命令，实测输出如下）**：

```powershell
# ① 幂等层那三个方法**从来没人调用**（只有定义）——幂等只覆盖"对象 + 选项集"
.\.venv\Scripts\python.exe -c "import ast,pathlib;names={'field_exists','status_exists','workflow_exists'};print('属性访问点：',[(p.as_posix(),n.lineno,n.attr) for p in pathlib.Path('workbench').rglob('*.py') for n in ast.walk(ast.parse(p.read_text(encoding='utf-8'))) if isinstance(n,ast.Attribute) and n.attr in names])"
# 实测：属性访问点： []          ← 空列表 = 零调用方（连类内 self.xxx 都没有）

# ② 八步里没有任何一步去读"平台已经生成的东西"
Select-String -Path workbench\services\egmp\orchestrate.py -Pattern 'record\("' | ForEach-Object { $_.Line.Trim() }
# 实测：picklists / object / fields / formLayout / listLayout / lifecycle / workflows / verify
#       —— verify 只数"对象在 + 字段数 + 状态数"，不核对派生物
```

**后果**：**"平台已经有的东西"在工厂眼里等于不存在** ⇒ 它把 **D-B**（全量替换、不读旧列）
与**幂等缺口**（重跑会重复建字段/状态/工作流，而**状态不可删、只能停用**）同时放大。

**来源**：[`factory-tuning/reports/gaia-v38-study/11-creation-dependency-map.md`](../factory-tuning/reports/gaia-v38-study/11-creation-dependency-map.md) §1.3 / §3 K10。

### T10 · "状态还没建就先建列表布局"这条组合风险（2026-09-22，来自依赖图 K2；**未真机复现**）

**判据（一条命令，实测输出如下）**：

```powershell
Select-String -Path workbench\services\egmp\orchestrate.py -Pattern 'record\("' | ForEach-Object { $_.Line.Trim() }
# 实测顺序：… formLayout(235) → listLayout(241) → lifecycle(258) …
# 而 `enableLifeCycle` 是第 2 步（object，:216）**随建对象请求**一起发出去的
# ⇒ 第 5 步建列表布局时：对象**已开生命周期**，却**一个状态都没有**（`status_map` 仍为空）
```

**为什么这不是"顺序不好看"**：它正撞在 **F16**（开了生命周期的对象，列表布局里「状态」列
**不可移除**、**新建时也被强制要求添加**）上，再叠上 **D-A**（工厂发 `columns`，真机是
`listlayoutColumns`）与 **D-B**（全量替换、不读旧列）⇒ 最坏情形是**把平台强制列清掉**，
即又一次「接口回成功、回读才发现空」。

**修法**：**「读旧列 → 合并 → 提交」**，**不是**把 `status_id__sys` 硬编码进去
（强制列**随对象变**：开了生命周期才有状态列）。

**定性方法**：演示第 2 段（新建列表布局并保存）——预期端点与判读口径见认知文档 §8。

---

## 5. 需要你拍板的决策（跑之前先定，避免中途返工）

> 2026-09-22 新增 **E1–E5**（权限规则簇 / 自动化簇 / 停用与流转矩阵 / 配置包口径 / T8 修法），
> 与本文档 D1–D5 合并看 → [整体认知 §7](FACTORY-PLATFORM-COGNITION.md)。


| # | 决策 | 选项 | 影响 |
|---|---|---|---|
| **D1** | 菜单到底算"引擎的活"还是"人的活"？ | ① 接进管道（T1 修成真执行）② **明确划为人工配置项**，从引擎职责里删掉 | 决定 T1 是"补实现"还是"改口径" |
| **D2** | 测到某一层失败时，**同一轮里修完再测**，还是**先记录、攒一批一起修**？ | ① 一轮一修（慢但干净）② 攒批（快但改动面大） | 决定每轮的节奏 |
| **D3** | 测试用的对象编码前缀 | 建议 `t1_` / `t2_` + 日期，如 `t1_field_0921__c` | 平台上删不干净，前缀能让人一眼看出是测试产物 |
| **D4** | 允许我在平台上**读**（`insight_read_config` 等只读工具）来取证吗？ | ① 允许（我能自己核对一半，减少你的手工核对） ② 不允许（我只依据你给的截图/描述） | 允许的话每轮快很多 |
| **D5** | 要不要**先修 T1/T2 再开始逐层测？ | ① 先修（它们是"引擎承诺了但没做"的硬伤）② 边测边修（把它们排进 L6） | T1/T2 会让 L6 直接失败，但 L1–L5 不受影响 |

## 6. 证据台账（每跑完一层补一行）

| 层 | 日期 | 蓝图 id / job id | 平台侧核对结论 | 修了什么 | 回归断言 |
|---|---|---|---|---|---|
| L0 只读预检 | 2026-09-21 | —（已有 24 项自动化） | 通过 | — | `tools/verify_factory_agent.py` |
| **L1 对象** | 2026-09-21 | ⚠️ **未走蓝图路径**（见下方说明） | **通过**：`测试09-03` 建成，`enableLifeCycle=true` / `enableSignatures=false` / `lifeCycleId` 非空 / 2 个系统状态 / 零自建字段 | `writers/responses.py`（新增）、`objects.py` 回读 | `-k success_flag`、`-k id_is_unavailable` |
| **L1b 对象**（只开生命周期，对照） | 2026-09-21 | 同上 | **通过**：`enableLifeCycle` 开关确实驱动状态provision（开=2 个状态，关=0 个） | — | — |
| **L2 字段** | 2026-09-21 | 同上 | **部分通过**：14 种 dataType 里验了 **8 种**（1/2/3/4/5/7/15/16），共 9 个字段 | `fields.py` 范围键名、`queries.py`、`kernel/endpoints.py` | `-k number_field`（2 条）、`-k picklist`（4 条） |
| **L2b 选项集** | 2026-09-21 | 同上 | **通过**：3 个选项建成并回读验证；`sort` 写入规则判定清楚 | `kernel/endpoints.py`、`queries.py::get_picklist` | `-k picklist` |
| **L3 列表布局** | 2026-09-21 | 人工在平台操作取证（无蓝图）<br>会话 `acct-891a4f9d-20260921-210820` | **部分通过 + 发现两处缺陷**：① 真机确证 **D-A 键名不符**（`listlayoutColumns` vs 工厂 `columns`）；② 代码确证 **D-B 全量替换不读旧列**。列元素形状 `{fieldId,fieldPath,sort}` 与 `sort` 基准（1 起）**与工厂一致** | **尚未修**（两条都要先真机定性） | 无——`grep "AddColumns\|LISTLAYOUT" tests/` **零命中** |
| **L3 表单布局** | 2026-09-21 | — | ❌ **未录到**（监听 `total: 0`，原因未分辨，见 §7） | — | — |
| L4 生命周期 | — | — | 部分（只验了"开关 → 状态provision"，**没验**新增/重命名状态）。2026-09-21 用户演示了"新增两个 + 停用一个"但**未录到** | — | — |
| L5 工作流 | — | — | 待办 | — | — |
| L6 菜单/声明区 | — | — | 待办（T1/T2 仍未真机复现） | — | — |
| **段 0 · 接口自证**（保存默认表单布局） | 2026-09-22 | 会话 `acct-fb8dc317-20260922-101457`（`total 105 / trimmed 1`） | **通过**：`POST /Layout/SaveLayoutDetail` 被录到，`precedingActions` **完整连贯**（配置管理→测试09-01→表单布局→默认布局→保存） | 新增事实 **F20–F25**；新增缺陷候选 **D-C** | 见[段 0 报告](../factory-tuning/reports/2026-09-22-真机取证-段0-默认布局保存.md) |
| **段 1 · 控件形状**（1 个部分 + 4 个半行控件） | 2026-09-22 | 会话 `acct-fb8dc317-20260922-102423`（`total 50 / trimmed 2 / full 5` → 7 条） | **通过且超出预期**：`controls` 4 个元素、`sections` 2 个（新建 `basic_info__cs`，`type=1`）**全部录到** | 新增事实 **F28–F35**；新增缺陷候选 **D-D**（控件键名与词汇表都不符） | 见[段 1 报告](../factory-tuning/reports/2026-09-22-真机取证-段1-控件与半行整行.md) |
| **段 2 · 新建部分 + 整行控件**（1 个部分 + 3 个整行控件） | 2026-09-22 | 会话 `acct-fb8dc317-20260922-104043`（`total 38 / trimmed 1 / full 4` → 5 条） | **通过**：`sections` 3 个（新建 `basic_info2__cs`）、`controls` 7 个（其中 3 个新建）；**半行/整行映射钉死**（`gridSpan` = 1 / 2）；**`GetStableComponentIds` 入参+出参+落点三段闭环** | 新增事实 **F36–F45**；**D-E** 从"平台会做"升级为"**可照抄**"；**D-D** 补 `AK*` 两词 + 两套形状实例 | 见[段 2 报告](../factory-tuning/reports/2026-09-22-真机取证-段2-新建部分与整行.md) |
| **段 3 · 一次点击三条保存**（无改动保存 + 两处宽度改动） | 2026-09-22 | 会话 `acct-fb8dc317-20260922-105154`（`total 52 / trimmed 3 / full 4` → 7 条） | **通过且推翻了我自己的读法**：3 条保存的差异**全部可解释**；**`dataType ↔ AK*` 补全到 7 组 / 5 类型**；键数**不是**"新建/已有"的标志（同控件同批发过 14/15/16/23 键） | 新增事实 **F46–F52**；**更正 F36 的因果读法**；**D-D 的 `AK*` 表可建**（仍缺 7 个 dataType，继续 Fail-Closed） | 见[段 3 报告](../factory-tuning/reports/2026-09-22-真机取证-段3-三条保存与AK词汇表.md) |
| **段 4 · 新部分 + 两个控件**（第三个部分 + 文本/数字控件 + 一个系统字段控件） | 2026-09-22 | 会话 `acct-fb8dc317-20260922-110842`（`total 15 / trimmed 1 / full 2` → 3 条） | **通过**：`AK*` 补到 **9 个 dataType / 7 类型**（`1→AKInput`、`2→AKInputNumber`）；**稳定 id 的第二个样本**，并发现该调用是**增量的**；新建组件也可以是完整形状 | 新增事实 **F53–F57**；**D-E 改为"按增量实现"**；D-D 的表基本可建（仍缺 5 个 dataType） | 见[段 4 报告](../factory-tuning/reports/2026-09-22-真机取证-段4-新部分与AK补全.md) |

> ⚠️ **本次覆盖的一个缺口，必须说清楚**：三轮实战**都没有走 `/api/factory/run` 的蓝图路径**，
> 而是直接调了 `orchestrate` 用的那几个写入口（`objects.create_object` /
> `fields.batch_create_fields` / `picklists.create_picklist`）。
> 原因是**蓝图表达不出"一个不带字段的裸对象"**——`ObjectDef.fields` 是 `min_length=1`，
> 而用户要的是与手工建的 09-01/09-02 同形的裸对象。
> ⇒ **"8 步管道 + checkpoint + 幂等"这一层本次没有被覆盖**，它仍然只有 T3–T7 那几条读代码的怀疑。
> 要覆盖它，得接受"至少多一个字段"。

## 7. 边界与风险（先说清，免得测到一半发现测不了）

- 🔴 **选项集不能删除（2026-09-21 用户确认）**：对象和字段都能在界面上删，**选项集不能**。所以对选项集的任何写入**都不可回滚** —— 名字、选项、编码写错会**永久留下**。这直接改变了「测试」这件事的性质：**对无撤销键的资源做写入试探 = 留下永久垃圾**。本次就为此永久留下了 2 条记录。⇒ **能用读接口判定的问题，绝不用写试探去判定**；确实要用写试探时，先问一句「这个操作有撤销键吗」。见 `docs/PITFALLS.md` #68。
- 🔴 **生命周期状态也不能删除，只能停用（2026-09-21 用户确认，F18）**：与选项集同族，**又一处无撤销键**。
  而工厂**没有"停用"这个动词**——`build_statuses` 从不传 `is_enabled=False`，`update_status`
  只被用于重命名。⇒ 平台上"退休一个状态"的唯一手段，工厂做不到（AGENTS.md 规则 36 同族）。
- ⚠️ **监听的可见范围有限，且"录到 0 条"不会报警（2026-09-21 实测）**：
  ① `classify()` 丢掉**所有 GET**（F13）；② ~~`flush()` **只在 `stop()` 时**调用，中途崩了整个会话丢光~~
  → **2026-09-22 更正**：页面关闭也会触发 `stop()`（`monitor.py:133` 的 `page.on("close", self.stop)`），
  实测关窗后会话目录里照样有 `monitor-log.json` / `filter-stats.json`。**真正的丢失窗口只有"整个应用被强杀/崩溃"**。
  ⚠️ 同时注意：**实时 `stats` 是快照、不是最终账**——同一次会话实测"实时读到 `total 240` / 落盘 `filter-stats.json` 是 `517`"
  ⇒ 结论一律以**落盘后的 `filter-stats.json`** 为准。
  ⇒ 仍然采用「**做一项、停一次**」（`ingest.py --session <名>` 支持按名收，分段不会漏）；
  ③ 挂载发生在 `_do_open`（内部已 `goto` 完平台）**之后**
  ⇒ **不能用"新开窗口的加载流量"来验证监听是否工作**；
  ④ 本次有一段**录到 0 条**、原因未分辨（两种解释见
  [复盘 §7.4](../factory-tuning/reports/2026-09-21-L3列表布局取证与监听失败复盘.md)）。
  ⇒ **流程固定为：开监听 → 自己造一次流量确认 `total > 0` → 再请人操作**
  （`monitor_start` 在监听已开时会返回**实时** `stats`，可非破坏性轮询）。
- **平台上的产物删不干净**：对象一旦建出来，字段/布局/生命周期会跟着留下痕迹。
  所以每层用**新的编码**，并且**不承诺"测完能回到原样"**。
- **不同环境的平台版本可能不一样**：`kernel/semantics.py` 里的枚举是"上游实证"，
  不代表**你这台环境**的平台版本一致。L2/L5 的核对点里已经把这个列成要验的东西。
- **幂等会掩盖问题**：重复跑同一个蓝图时，已存在的对象会被跳过——**看起来成功但什么都没发生**。
  所以每层都要用新编码，并且核对"平台侧真的多出来一个"。
- **本方案不改变任何确认门**：`factory_create` 仍然是 `dangerous`，仍然要你点头。
  逐层测不会让这件事变松。
- **测试期间建议用一个专门的平台环境**（不是生产）。这一点由你决定，但要说出来：
  蓝图里的操作是**真实写入**，回滚手段只有平台侧手工删除。
- 🔴 **工厂建出来的对象，业务端默认"看不见"（2026-09-22 手册确认）**：对象的查看/创建/编辑与
  字段级权限**必须由人逐项加进权限集**（`03_管理员指南\02_权限配置.md §对象与字段权限`），
  否则对象在业务端根本不出现。⇒ 这不是"工厂少建了一个东西"，而是"**建完了但没人能用**"；
  也因此**人工配置清单必须条目化到"对象 X 的查看/创建/编辑 + 字段 A 的查看"这一粒度**
  （决策 E1 的前提）。
- 🔴 **工厂的产出物是"配置包"，不是"已生效的系统"（2026-09-22 手册确认）**：配置包
  **只支持全量迁移**，且「入站包导入 ≠ 生效」，还要走**校验 → 部署**；生产环境
  **禁止访问配置管理模块**（`03_管理员指南\07_配置迁移.md`、`03_系统设置.md §1.10`）。
  ⇒ 复现性取决于**包版本号规则与目标侧部署前校验**，而不取决于工厂建得多干净（决策 E4）。
- ⚙️ **录制的盲区：非 POST 方法只认"已建模的路径"（2026-09-22 预检，判据可跑）**：
  `monitor.py::classify()` 对 PATCH / PUT / DELETE **只有路径逐字命中 `writers/endpoints.py` 的 57 条
  常量**才录，否则**静默 DROP**（实测 `PATCH /api/config/lifecycle/Status/Update` → `RECORD_TRIMMED`，
  `PATCH …/Status/ChangeStatus` → `DROP`；57 条里还有 5 条带 `{}` 模板、永远匹配不上真实路径）。
  ⇒ **文档串里的「陌生 API 完整保留」只对 POST 成立**。
  ⇒ **这是自指陷阱**：用录制去发现"平台用哪个方法"，而录制只认得已知路径的方法。
  ⇒ **L4「停用走不走 PATCH」有三种结局，解读口径必须事前定死**（见
  [整体认知 §8](FACTORY-PLATFORM-COGNITION.md)）：录到写请求 = 答案到手；
  **界面真的变了但没录到** = 平台用的是"非 POST + 未建模路径"（这本身就是答案）；
  界面也没变 = 那一步没生效，**重做**。
  ⚠️ **不要用 `dropped` 增量去判**（它把 GET 一起算了，一次探测 `total 240 / dropped 233`）。
- ⚙️ **工具提示**：`factory-tuning\tests\selfcheck.py` 的 D 组会**真的**调 `report.py --fixtures`，
  于是每次自检都在 `reports/` 里留下一份**当日日期**的 `*-SAMPLE-*.md`。
  那是**合成样例**，不是采集分析结果——别把它读成"今天采到了数据"。
  （形态与 PITFALLS #71 同族：**跑一次检查就在仓库里留下痕迹**，而检查本身是绿的。）

---

## 8. 已确认的平台事实（2026-09-21 真机实测 · 逐条有判据）

> 这一节是本次三轮实战的**可复用产出**：凡后面要写平台代码，先照这张表，
> **不要再去猜信封/键名**（#62 / #63 / #67 / #69 全都是照着猜的东西写出来的）。

| # | 事实 | 怎么确认的 |
|---|---|---|
| F1 | 响应信封是 `{code, errorCode, message, data, requestID, traceId}`；**业务失败也是 HTTP 200**，错在 `code`（如 `500`）里 | 每条真机响应 |
| F2 | `SaveBasicObject` / `SaveField` **只回 `{code:0, data:true}`，不回 id** | 真机（#62） |
| F3 | `ObjectPicklist/save` **按 `code` upsert**，且**全局校验名称不与其他记录冲突** | 试探：新编码 + 已有名称 → `名称重复`；同一条原样重发 → 仍只有 1 条 |
| F4 | 🔴 **选项集不能删除** | 用户确认（#68） |
| F5 | 选项集回读 = `GET /api/openapi/v1.0/BasicObject/picklist/{code}`；`data` 是**选项数组**，选项集 id 在 `data[0]["objectPicklistId"]` | 真机（#67） |
| F6 | 选项集**不存在**时返回 **HTTP 200 + `code:500` + `"Object reference not set to an instance of an object."`**（不是 404、不是空数组） | 真机 |
| F7 | 数字字段：范围 = **`minValue` / `maxValue`**，小数位 = **`scale`**（不是 `min`/`max`/`decimalPlaces`） | 界面请求体 + 工厂真机复验（#69） |
| F8 | 字段记录有 **93 个键**；其中**没有** `statisticsArrange`、`disabled`、`__observed`、`decimalPlaces` —— 这几个是"界面和工厂都在发、平台不存"的键 | 真机回读 |
| F9 | 平台给 `日期(5)` 补 `format=YYYY-MM-DD`、给 `日期时间(7)` 补 `format=YYYY-MM-DD HH:mm:ss` | 真机回读 |
| F10 | `objectRelation.type`：**1 = 父对象关系，2 = 对象引用关系** | 真机回读 |
| F11 | **新建**选项集时若请求体带顶层数字键 `"0"/"1"/"2"`，平台**按位置重排 sort 为 1-based 并忽略 `options[].sort`**；不带数字键则认 `options[].sort`；**更新**时一律认 `options[].sort` | 三次判别实验（见选项集实战报告 §7） |
| F12 | 引用型字段的 `objectRelation` 是**平台生成的**（界面只发近乎空的值，平台补全 20+ 个键）——**不是要发的键**；决定关系的是 `referenceObjectId` | 真机回读 |
| F13 | Monitor 绑在**浏览器页面**上，而工厂走 **Python httpx** —— **两条通道互不可见**；且 `classify()` **把所有 GET 丢弃** | 代码 + 真机（本次工厂执行完全没被录到） |
| F14 | 白名单式裁剪只在**响应体 > 10KB**（`TRIM_SIZE`）时生效；`_DATA_KEEP_CHARS = 2000` 实测**只装得下 1 个字段**（`count` 等头部信息保得住） | 真机样本（#63） |
| F15 | 同一个对象上，「对象(15)」与「父对象(16)」**必须分别建**，不可互相替代 | 手册 §3.9/§3.11 + 真机 F10 |
| **F16** | **开了生命周期的对象**：列表布局里的「状态」列**无法被移除**，**新建列表布局时也被强制要求添加** | 用户实测（两条）+ 真机请求体末尾**固定**带 `status_id__sys`（`…-210820` seq 8） |
| **F17** | 生命周期**创建时自带「开始」「关闭」两个状态** | 用户 2026-09-21 |
| **F18** | 🔴 **生命周期状态不可删除，只能停用** —— 又一个**无撤销键**的资源 | 用户 2026-09-21 |
| **F19** | 生命周期**进入时默认先进入「开始」状态**（L5 的 `bindToStatusCode` 判据要用它） | 用户 2026-09-21 |
| **F20** | `Layout/SaveLayoutDetail` 的响应 `data` 是**布局 id**（不是 `true`）——与 `SaveBasicObject`/`SaveField`（F2）**不同** | 段 0 真机（2026-09-22） |
| **F21** | 表单布局保存是**整份提交**，顶层 17 键：`id`/`objectId`/`code`/`name`/`labelCol`/`source`/`status`/`objectTypeId`/`controls`/`sections`/`pages`/`isMain`/`activePageCode`/`activeSectionCode`/`formEditStatus`/`skipRequiredValidation`/`rules` | 段 0 真机 |
| **F22** | 「默认布局」记录的 **`source = 2`**，且 **`isMain = true`**（工厂 `find_default_form_layout` 正是按 `source==2` 找的） | 段 0 真机 |
| **F23** | section 的**部分类型**：`type = 4` = 「工作流时间线」（关系型，带 `relationId`/`listlayoutId`/`selectListlayoutId`/`selectObjectId` 四个指针） | 段 0 真机 |
| **F24** | section 带 **`gridSpan` / `columnsNum` / `labelCols`** 三个宽度相关键（本次全 `null`）⇒ 宽度语义要在**有字段控件的布局**上取（段 1） | 段 0 真机 |
| **F25** | 单页面场景：`pages: []` 而 `sections` 直接挂在布局上 ⇒ 手册 `01D §3`「可以不建页面」**得到真机支持** | 段 0 真机 |
| **F26** | **`Listlayout/AddColumns` 的响应 `data` 是"平台实际接受的列数"（int）**，与提交的列数一致 ⇒ **免回读的自检信号**：发出去几列 vs 平台回几列，能在**同一次调用里**暴露"键名错了/被当空列表" | 响应信封审计（2026-09-22） |
| **F27** | **`ObjectPicklist/save` 的响应 `data` 可能是 `bool(true)` 也可能是 `str('')`** ⇒ 任何"把 `data` 当 id"的写法都会拿到空串（工厂已按 #67 走 OpenAPI 回读，正确） | 响应信封审计（2026-09-22） |
| **F28** | **「半行/整行」= 控件层 `gridSpan`**：**半行 = `1`、整行 = `2`**（两个部分的 `columnsNum` 都是 2 ⇒ 它是**以列为单位的跨列数**）。⚠️ 仍未验：`columnsNum = 3/4` 时整行是否 = 3/4、`gridSpan` 缺省时按几算、它与 `columnsNum` 是否同义 —— **用户 2026-09-22 定稿：整行/半行只是控件的显示形式，不作为追究项**（上面三条只留作边界，**不要再为它设计实验**） | 段 1 + 段 2 真机 + 用户裁定 |
| **F29** | 控件的键集**按需出现**，判据是**"服务端是否已经认识这个组件"**：**新建 = 8 键**（`id`/`sectionId`/`code`/`name`/`fieldId`/`type`/`gridSpan`/`attrs`），**已有 = 23 键**（多出 `sort`/`dataType`/`isRecalculateDefaultOnDependencyChange` + 12 个 `null`）；**部分（section）同理：新建 5 键 / 已有 62 键**。⇒ 形状**不取决于控件种类** | 段 1 + 段 2 真机（**同一 code 的两种形状**） |
| **F30** | 控件 `type` 的值域是 **`AK<组件名>`**（实测 `AKAttachment` / `AKSelectorObject` / `AKDateTime`） | 段 1 真机 |
| **F31** | 控件级配置装在 **`attrs`** 里（`readPrettyShowType` / `objectId` / `listLayoutId` / `createRecordMode`…），且**内部 id 的值带一对字面引号**（`'"id-…"'`）——平台既有形状，不是脱敏造成的 | 段 1 真机 |
| **F32** | 新建「详情部分」`type = 1`、`columnsNum = 2`（**与工厂硬编码一致**）；**不填 Code 时平台自生成**（如 `basic_info__cs`） | 段 1 真机 |
| **F33** | 一次会话里可能出现**两条** `SaveLayoutDetail`（段 1：相隔 **29 ms**，均 `200 / code=0`）。**2026-09-22 段 2 更正**：段 2 里**一次「保 存」点击只发一条**，而段 1 那两条的**形状差异**（#1 精简 8 键 → #2 完整 23 键）说明它是平台的「**提交 → 回显 → 再提交**」，**不是两次人工点击**。⚠️ 两段各一个样本 ⇒「为什么那一次是两条」仍不下定论；**工厂不要为对齐真机而模仿发两条** | 段 1 + 段 2 真机 |
| **F34** | 控件里那 13 个 `null` 键与手册 `01D §5.1`「控件通用配置」**逐项对应**（只读/隐藏/渲染/无数据隐藏/生命周期显隐/必填/阅读模式…）⇒ **手册语义 ↔ 真机键名在控件层也对齐**，且平台用"键在、值为 null"表示"不覆盖" | 手册 + 段 1 真机 |
| **F35** | 两个**未建模**端点：`ListlayoutRecord/GetDropdownListApiProject`、`Layout/GetStableComponentIds`（都在保存流程里触发） | 段 1 真机 |
| **F36** | **新建组件发"精简形状"、服务端已有组件发"完整形状"**（同一 code 在两段里：部分 5 键 ↔ 62 键、控件 8 键 ↔ 23 键）⇒ 形状取决于**服务端是否已经认识它**，**不取决于控件种类**。⚠️ **2026-09-22 段 3 更正**：键数其实是「**客户端模型物化到什么程度**」——同一控件在**同一次点击的三条保存**里发过 **14 / 15 / 16 / 23** 键，平台**都接受**（见 F48）。"新建 ↔ 精简"只是**相关**，不是因果 | 段 1 + 段 2 真机（**段 3 更正**） |
| **F37** | 新建控件**不带 `sort` / `dataType`**（**数组顺序即顺序**），只带 `type`（`AK*`）⇒ 平台能从 `fieldId` / `type` 反推 `dataType` | 段 2 真机 |
| **F38** | `Layout/GetStableComponentIds` 全文：入参 `{componentIds: {<客户端 UUID>: {objectId, layoutCode, componentType, componentCode}}}` → 出参 `data.stableComponentIds`（**同键 → 服务端 id**）；`componentType ∈ {layout_section, layout_control}` | 段 2 真机（入参 + 出参） |
| **F39** | **保存体里新建组件的 `id` = `GetStableComponentIds` 返回的稳定 id**（4/4 逐条相符），浏览器自造 UUID **不进保存体** ⇒ 这一步是平台客户端的**必经步骤** | 段 2 真机（入参 / 出参 / 落点**三段闭环**） |
| **F40** | 该端点**只对新建组件**调用（已存在的那 4 个控件不在入参里）⇒ 工厂只需对**新**组件调 | 段 2 真机 |
| **F41** | 每新建一个控件引发一次 `ListlayoutRecord/GetDropdownListApiProject`（`{"pageIndex":1,"pageSize":1000}`，**查询型 POST**）：段 1 四控件 = 4 次、段 2 三控件 = 3 次 ⇒ 它是只读查询，却因不在 `QUERY_POST_PATTERNS` 里被归为"陌生写"（`RECORD_FULL`）⇒ 监听噪声分类的已知缺口 | 段 1 + 段 2 真机 |
| **F42** | 新词汇 2 个：**`AKSelectorSingle`**（选项）、**`AKSelectorYesNo`**（是否）⇒ `AK*` 累计 **6** 个。⚠️ 这两条**不带 `dataType`**，**推不出**映射（补齐办法见段 2 报告 §9） | 段 2 真机 |
| **F43** | 动作缓冲原文（段 2）：`click 整 行 → click INPUT → change INPUT → click 确 定 → click 保 存` ⇒ 与演示者自述一致；确认「整行」是在**控件配置对话框**里选的 | 段 2 真机 |
| **F44** | ⚠️ **测量口径**：09-22 三个会话里"最后一次 DOM 动作 → 请求"的差是 **44.5 / 38.4 / 61.4 s**，而 09-21 六个会话是 **0.1–3.1 s**（本段 5 条请求的观测跨度仅 **14 ms**）⇒ `timestamp` 与 `precedingActions.at` **跨边界不可当因果**；两个候选机制都未排除，见段 2 报告 §6 与 PITFALLS #74 | 11 个会话真机 |
| **F45** | `stats` 只统计**已被分发**的事件 ⇒ README 记的"实时 240 vs 落盘 517"同源；且"读 `total` 验证录制"这个动作**自己会推动分发**（`monitor_start` / `session()` 都走 `_submit`）⇒ **读到 0 不能证明什么都没发生** | 代码 + 段 2 真机 |
| **F46** | 一次会话里出现 **3 条** `SaveLayoutDetail`（相隔 **19 / 46 / 77 ms**）⇒ **保存条数不固定**（段 1 = 2、段 2 = 1、段 3 = 3）。**不要假设"一次点击 = 一条"**，也不要把多发的那几条当重试 | 段 3 真机 |
| **F47** | 那三条的差异**全部可解释**：#1 带**改动前**状态（两处 `gridSpan` = 2）、#2 与 #3 内容相同、#3 是 #2 的**键补全版** | 段 3 真机 |
| **F48** | **键集 = 客户端模型的物化程度**（不是"新建/已有"的标志）：同一控件在同一批里发过 **14 / 15 / 16 / 23** 键，平台**全部 `200/code=0`** ⇒ 工厂**不必**凑齐 23 键，但**键名必须对**。缺的键全是**可空可选键**（`visible`/`readOnly`/`readPretty`/`hidden`/`hideWhenNotData`/`required`/`defaultValue`/`externalDataConfig`/`combinationDisplayFieldId`） | 段 3 真机（**更正 F36**） |
| **F49** | **`dataType ↔ AK*` 实测 7 组 / 5 个类型**：3→`AKSelectorYesNo`、4→`AKSelectorSingle`、5→`AKDateTime`、7→`AKDateTime`、8→`AKAttachment`、15→`AKSelectorObject`、16→`AKSelectorObject` | 段 1 + 段 3 真机 |
| **F50** | 该映射**不是双射**（5/7 共用 `AKDateTime`；15/16 共用 `AKSelectorObject`）⇒ D-D 的表形是 **dataType → type（多对一）**，不是双向表 | 段 3 真机 |
| **F51** | **没有新建组件的那次保存里没有 `GetStableComponentIds`** ⇒ 反向印证 F40「该端点只对新建组件调用」 | 段 3 真机 |
| **F52** | 段 3 的**测量教训**：实时 `stats` 连读四次 `0`，而请求在 **9 秒后**才发出 —— **0 只表示"截至此刻没有"，不是"这次没有"**；权威读数永远是 `stop` 落盘后的 `filter-stats.json`。**不要用一次 0 去推翻操作者的说法** | 段 3 真机 |
| **F53** | **`dataType 1 → AKInput`、`2 → AKInputNumber`** ⇒ `AK*` 实测到 **9 个 dataType / 7 个类型**（仍缺 13/14/17/18/23，**继续 Fail-Closed**）。命名规律是 `AK` + 组件英文名，**但不可外推** | 段 4 真机 |
| **F54** | **`GetStableComponentIds` 是增量的**：只对"**还没有服务端 id**"的组件调用（段 4 只发 **1** 个，而那次新建了 1 部分 + 2 控件；段 2 发 4 个）⇒ **D-E 应按增量实现**，多调不会报错但会与真机行为不一致 | 段 2 + 段 4 真机 |
| **F55** | 稳定 id 闭环**第二个样本**：请求键 == 响应键，保存体里的 `id` **逐字**等于响应值 | 段 4 真机 |
| **F56** | **新建组件可以是完整形状**（段 4：新部分 62 键、新控件 23 键、另一个新控件 8 键）⇒ **再次印证 F48**（键数 ≠ 新旧，是模型物化程度） | 段 4 真机 |
| **F57** | 平台自生成 code 的系统字段控件（`name__ak`）**可以没有 `dataType`**，但有 `type`（`AKInput`）⇒ 工厂**不能**把 `dataType` 当作"定 `type` 的唯一依据"；取不到时应报错，不许退化成 `input` | 段 4 真机 |
| **F58** | **控件 = 引用的那个字段的投影**（用户 2026-09-22 提示，已在数据上逐词对上）：字段 `code`/`name`/`dataType` **同名同值**搬到控件上；`isRequired`→`required`、`invisible`→`visible`、`editable`→`readOnly`（**改名/取反**）；控件只多 `sectionId`/`gridSpan`/`sort` 三样 | 字段 93 键（`FieldsByCodes` 响应）＋ 段 1–4 控件 |
| **F59** | ★ **D-D 的成因是"跨层串词"，不是瞎猜**：`invisible` 是**字段层**的合法键（控件层叫 `visible`）⇒ 工厂把它搬进控件请求体，接口**照样 200**。⇒ 判据只能按"**它在这条请求的哪一层出现过**"（`keygate.py` 现在正是这么做的） | 段 1–4 真机 + 代码 |
| **F60** | 字段那 93 键里**没有任何"控件类型"键**（`AK*` 只出现在 `SaveLayoutDetail` 请求体）⇒ 控件 `type` **必须由工厂派生/实测**，那张 `dataType → AK*` 表**仍然必要** | 全量采集扫描 |
| **F61** | 字段元数据里还有**工厂完全没有概念**的两样东西：**`dataType 19`**（任务类型 / 工作流版本）与**第三套词汇 `componentType`**（`ComponentType_Object` / `ComponentType_Workflow_Version`，多数为 `None`）⇒ 工厂那张 14 项 dataType 表**本身就不完整** | 字段 93 键 |
| **F62** | **真机 `SaveField` 请求体的词表**（11 条样本，去重后）：`code` / `dataType` / `disabled` / `invisible` / `name` / `objectId` / `statisticsArrange` **必有**，`format` / `minValue` / `maxValue` / `picklistId` / `referenceObjectId` / `objectRelation` / `setDefaultValue` / `formulaScript` **按字段类型出现**，另有平台自带的 `__observed`。★ **`isRequired` 一条都没出现过** —— 而字段**读侧** 93 键里**有** `isRequired` ⇒ 写入侧与读取侧的词表**不一致**（"必填"很可能是在**布局控件**上设的，控件层确实有 `required`，见 F58） | 09-21 真机 11 条 |
| **F63** | ⚠️ **闸门的一个"已声明盲区"里住着缺陷**：`keygate.py` 原来只认 `client.post(常量, {字面量})`，于是**对象与字段的创建体**（走工厂自己的助手 `created_id(client, path=常量, body=变量)`）**一条都没被检查过**。2026-09-22 扩展后（同函数字典字面量 + 一层函数返回 + `extended` 标注），`SaveField` 立刻报出 **🔴 `isRequired`**，而 `SaveBasicObject` 报 **✅ 一致**（8 键与真机逐字相同）⇒ **"未核"不是安全，是待还的债** | 代码 + 真机对照 |
| **F64** | **真机数据里出现过的 `dataType` 共 11 个：1 / 2 / 3 / 4 / 5 / 7 / 8 / 15 / 16 / 18 / 19**（扫描全部运行时会话的所有请求体与响应体）。与工厂那张 14 项表对比：工厂有而**从未观测**的是 `13 / 14 / 17 / 23`；**观测到而工厂表里没有的是 `19`**（只出现在 `FieldsByCodes` 的字段元数据里，`componentType = ComponentType_Object` / `ComponentType_Workflow_Version`） | 全量真机扫描 |
| **F65** | **`AK*` 是"只写"词汇（在现有全部采集里）**：它**只**出现在 `Layout/SaveLayoutDetail` 的**请求体**；平台自己的读接口（`FieldsByCodes` 响应、`GetFormInstance` 响应…）**一次都没返回过它**。⚠️ **但布局的读接口从未被录到**（设计器打开时读一次，那一次不在录制窗口里）⇒ **待取证**：重开一次设计器即可回答"`AK*` 到底能不能读回来"——这对 D-D 的修法是决定性的（能读就不必维护表） | 全量运行时日志扫描 |
| **F66** | **表单布局与列表布局的能力不对称**：工厂**能新建列表布局**（`build_list_layout` → `LISTLAYOUT_SAVE` + `source: 3`），却**不能新建表单布局**（`build_form_layout` 只按 `source == 2` 找默认布局，找不到就返回 `success: False` + "默认表单布局未找到"）。⇒ 蓝图能描述布局，但"新建一个表单布局"这个动词**不存在**。<br>★ **2026-09-22 段 5 已把线格式录到**（此前那次因"读完忘了重开监听"丢失）：**"新建"与"改已有的"是同一个端点，只差一个 `id`** —— ① 建壳 = **不带 `id`** 的 `SaveLayoutDetail`（顶层 **11 键**，`source: 3`、`name: "新建表单布局"`、`labelCol: 3`、`controls/sections/pages/rules` 四个空数组），**响应 `data` 直接返回新布局 id**；② 之后带该 `id` 保存（顶层 **17 键**、`isMain: false`）逐步补 section 与 control；③ 每次保存"含新组件"之前先调 `GetStableComponentIds` 把客户端 UUID 换成服务端 id。取证与逐键样本见 [段 5 报告](../factory-tuning/reports/2026-09-22-真机取证-段5-新建表单布局.md) | 代码 + 全量日志扫描 + **段 5 真机** |
| **F67** | **控件类型不是 `dataType` 的函数**：手册对每个类型都写「选择控件：**必填**」，且**两处给了两个候选**（§5.4 `文本框` / `多行文本框`；§5.8 `是否选择器` / `是否单选按钮`）⇒ `dataType → AK*` 只能当**默认值表**（真机那 9 个恰好都落在手册说的"通常/建议"那个上，双向印证）。⚠️ 手册**全库没有任何 `AK*`**（判据须 `-CaseSensitive`，见 PITFALLS #78），线格式仍只能从流量取 | 手册 `01D §5.x` + 真机 |
| **F68** | **蓝图无处表达"这个字段用哪个控件"**：`FormSectionDef` 只有 `name`/`code`/`type`/`columnsNum`/`fields`，`fields` 是字段 code 列表 ⇒ 想指定"多行文本框 / 单选按钮"**无处可写**，工厂只能发默认值。这是**能力缺口**（不是缺陷） | 代码 + F67 |
| **F69** | ★ **`maxLength` 的上限被写成了 `17`**（`blueprint.py:48` 的 `ge=1, le=17`），而手册写着**单行文本最多 1500、长文本/富文本最多 32000** ⇒ **>17 全被本地校验拒掉**；且 `spec.py:116` 把这条**错规则**写进了给模型看的注解 ⇒ 人与模型被**同一条错规则**挡住。**判据可跑**：`FieldDef(…, maxLength=1500)` → `ValidationError`，换成 `17` → 通过；手册侧 `Select-String -Pattern "最多支持"` → 1500 / 32000 / 32000。⚠️ 与其它卡不同，这条的后果**已可判定**（纯本地校验，不碰平台） | 手册 + 代码 + 判据 |
| **F70** | **手册 §3 共 16 种字段类型，工厂 `DATA_TYPES` 只有 14 项**（`blueprint.py:20` = 1/2/3/4/5/7/8/13/14/15/16/17/18/23）⇒ 缺的正好两种：**统计（Statistics，手册 §3.13）** 与 **组件（Component，§3.16）**。⇒ 工厂**没有创建这两类字段的能力**；而报错文案写的是「**非法 dataType**」——**它把"我们不支持"说成了"平台不允许"**（文案与事实不符，属规则 32 那一族） | 手册 `01B §3` 16 节 + 代码 |
| **F71** | **推断（标注为推断）**：实测字段载荷里 `dataType 19` 的两条**都带 `componentType`**（`relation_object_id__ak`→`ComponentType_Object`、`workflow_config_id__sys`→`ComponentType_Workflow_Version`）⇒ **19 很可能是"组件"类型**，且组件的判别落在 **`componentType`** 上（与 `dataType` 正交）。⚠️ **未证**：需要真机在界面上建一个**组件字段**才能定。⇒ 这也意味着 `AK*` 的 Fail-Closed 表将来还要覆盖这一类 | 字段 93 键（共现）+ **推断** |
| **F72** | **手册在字段层给了至少 9 处数值硬限制，工厂一处都没校验**：文本 `maxLength ≤ 1500`；长文本/富文本 `≤ 32000`；数字最大值 `≤ 99999999999999`；对象多选默认最多选 `100`（可调 `1-1000`）；附件数量 `1-100`、单个 `1-2048MB`；查找「一个对象最多 **5** 条关系 / 每条最多 **8** 个字段 / 一个源对象最多被 **8** 条引用」；统计「一个对象最多 **10** 个」；树形「最多 **5** 层」；帮助内容 `0-500` 字符。而 `validate()` **只做结构性检查**（编码正则/重复、dataType 合法、引用必填、`max>min`、布局引用存在），`FieldDef` 的 `max`/`attachmentCount`/`attachmentSize`/`decimalPlaces` **全部无界**，`helpContent` 连字段都没有（靠 `extra="allow"` 透传）⇒ **"半个校验器"**（详见 `docs/FACTORY-PLATFORM-COGNITION.md` L2 表） | 手册 `01B §2.2/§3.x` + 代码 |

> **F18 把"无撤销键资源"从一个变成两个**：选项集（F4 / PITFALLS #68）与生命周期状态。
> 对这两者，**"试探"与"生产写入"是同一件事**——能用读接口判定的问题，不要用写试探。
> 反过来，**列表布局的列是可改的（有撤销键）**，所以对它的受控写入试探风险等级不同。

**五条派生的工程口径**（比事实本身更重要）：

1. **判"成功"必须看平台回读，不能看接口返回** —— F1/F2/F8 合起来意味着
   "接口回 `data:true`"与"东西真的建对了"是两件事。
2. **键名的事实源是平台的读侧 schema + 界面的真实请求体**，不是蓝图侧的字段名。
   本次先靠读侧 schema 正确预测了 `minValue`/`maxValue`，又正确推出了 `scale`。
3. **先拿到界面请求体，再改代码** —— 这个下午的 #62/#63/#67/#69 四条的成因完全一样：
   **照着猜的信封/键名写代码**，而症状一律是"接口回成功、回读才发现空"。
4. **（2026-09-22 追加）分清"一串人工操作"与"一次页面事件"，唯一依据是 Δt。**
   分析器一度把时间戳丢掉（`analyze/model.py` 的 `Entry` 没有 `at`），于是
   `…-210820` 那 8 条**全在 165ms 内**的**页面渲染**被读成了"用户依次配了 10 列"。
   对照 `acct-fb8dc317-…-160313`：20 条写请求跨 **221 秒**、`SaveField` 每 15–33 秒一次、
   后面 0–3 秒紧跟一次 `FieldPage` 回读——**那才是人的节奏**。
   ⇒ D-A 的键名结论不变（比的是平台自己的线格式），但**F16 的"请求体"那一半证据降级**：
   它只能证明"状态列在该布局里"，不能单独证明"新建时被强制添加"。
   详见 [L3 复盘 §11（后补更正）](../factory-tuning/reports/2026-09-21-L3列表布局取证与监听失败复盘.md)。
   判据：`factory-tuning/tests/selfcheck.py` 的 **H 组** + 报告 §3 的「距上条(ms)」列。

5. **（2026-09-22 追加 · 对第 4 条的边界收窄）Δt 只在"同一批分发的事件之间"可用。**
   实测：09-22 三个布局会话里"动作缓冲里最后一次点击"与"请求时间戳"差 **38–61 秒**，
   而同一批请求的观测跨度只有 **14 毫秒**（09-21 的六个会话则是 0.1–3.1 秒）。
   两个候选机制**都还没被排除**：**(a)** sync-Playwright 的事件只在 worker **进入 Playwright 调用**时才被分发，
   而 `browser_pool.py:166` 的 `self._jobs.get()` **没有超时**；**(b)** 触发保存的操作**不在缓冲里**
   （键盘 / iframe / shadow DOM 的点击都不进缓冲）。
   ⇒ **可用**：同批事件之间的相对间隔、以及"这批请求的先后顺序"（顺序另有**内容语义**背书：
   3× 下拉查询 → `GetStableComponentIds` → `SaveLayoutDetail`）；
   **不可用**：跨"动作缓冲 ↔ 请求"的时间差 —— **不许**再拿它推人工节奏或"用户犹豫了"。
   ⇒ 新增 [PITFALLS #74](PITFALLS.md)；详见[段 2 报告 §6](../factory-tuning/reports/2026-09-22-真机取证-段2-新建部分与整行.md)。
