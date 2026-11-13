# akso-workbench 开发规则

本文件是仓库级约束；改代码、加工具、加接口前先读一遍。

## 工具 UID（DSH 可调用的工具）

**任何注册给 DSH 的工具都必须有稳定 UID。** 这是硬门禁，不是命名建议。

- **格式**：`AKW-TOOL-<SLUG>-V<n>`，全大写、连字符分隔，例如
  `AKW-TOOL-ACCOUNTS-V1`、`AKW-TOOL-CONFIG-PLAN-V1`。
- **登记位置**：`plugin/src/tool-uids.ts` 的 `TOOL_UIDS`。新增/改名工具时**先登记，再注册**。
- **注册方式**：`withToolUid(definition, logger)`。未登记 UID 的工具会在注册期直接抛错，
  不允许“先上线再补”。
- **可观测性**：包装后
  - 工具 `description` 末尾追加 `UID: …`，agent 在工具列表里能直接看到；
  - 每次 `execute` 的返回对象带 `_uid`；
  - host 日志打 `[UID] invoke/done/failed <tool>`，DSH 侧一次调用能与 host 日志对上。
- **稳定性**：UID 一旦用于排查就**不复用**到别的工具；工具删除时保留 UID 并注明 retired，
  避免历史日志对错工具。
- **验证**：`plugin/test/host.test.ts` 断言四个注册工具的 UID 齐全、唯一、格式正确，
  且未登记工具会被拒绝；加工具时必须同步更新该断言。

## 日志与审计

- **三类记录分开写，不要混在一起**：
  - 写入审计 `config-audit/<YYYY-MM>.jsonl`：每步一条，含 `planId/env/baseUrl/accountId/capabilityId/status/traceId/durationMs`；**不记 params**（敏感内容不进日志）。
  - 审批决策 `config-audit/approvals-<YYYY-MM>.jsonl`：含 `at/planId/env/baseUrl/accountId/hash/verdict/note`；拒批、补充、超时、中断都要留痕，不能只记"写了什么"。
  - HTTP access：`[http] METHOD PATH -> STATUS Nms`，一条请求一行；2xx/4xx 走 info，5xx 走 warn。围栏挡掉的 403 也要有。
- **工具日志**：`[UID] invoke/done/failed`；`done` 要带耗时与 `ok=<bool>`（业务失败但没抛异常时 `ok=false`），不能把 `denied`/`needs-account` 记成成功。
- **不许静默吞异常**：浏览器打开失败、审批窗轮询失败、审计读取失败，至少落一条 warn/debug。
- **写日志的铁律**：不记 token、口令、请求体原文；审计记录"谁在什么环境对哪一版计划做了什么"，内容细节用 `planId` 回查计划文件。

## 其他既有约束

- 配置写入只走「模型工具 + 对话内审批窗」：`engine.applyPlan` 是唯一的门，写入前必须有人
  在弹窗里点「同意」（`config-approvals/<planId>.json` 存计划指纹，一次性）；HTTP 直连默认关闭
  （`AKSO_WORKBENCH_ALLOW_DIRECT_WRITE=1` 仅本机调试，开了也仍过引擎的审批门）。
  审批要求 = 环境变量 `AKSO_WORKBENCH_REQUIRE_APPROVAL` > 页面开关 > 默认要求。
- 写入账号必须来自底部账号条（`prefs.currentAccountId`）且在配置池里；账号条为空时工具返回
  `needs-account`、界面自动弹选账号面板，**不许**回落到磁盘上的旧会话或别的账号。
  计划绑定 `target{baseUrl,accountId}`，apply 前比对，不一致直接 `TARGET_CHANGED`。
- 口令不离开 akso-cloud；host 只拿「账号 id」换 eGMP 令牌，日志/响应里不得出现 token 原文。
  本机不再接受明文口令（`session.login` / `POST config/login` / `auth.login` 能力都已删除）。
- 改完跑：`npm run typecheck:plugin`、`npm --prefix plugin test`、`npm --prefix plugin run build`。
- **生命周期/工作流流程图固定用便笺本模块**：唯一图形源是 `docs/analysis/便笺本.xml`（用户提供），代码样式集中在 `plugin/src/shared/sticky-notes.ts`。生命周期状态=浅蓝 `#bdd7ee` 矩形；用户动作=橙色 `#ed7d31` 胶囊；用户任务=橙色 `#ed7d31` 矩形；判断=橙色 `#f4b183` 菱形；开始/结束=`#70ad47` 圆形；连线=黑色箭头。生成 drawio 不得自创配色或圆角；`plugin/test/analysis-artifacts.test.ts` 会断言源文件存在且 drawio 只含这些颜色。
- **字段类型规则（2026-11-09 管理员口径）**：一律用文本 `dataType: 1`；**长文本 17、富文本 18 已禁用**，
  不再新建、不再使用（`node plugin/scripts/api-guide.mjs --check` 会扫文档里的 17/18 并报错）。
  字段建完不能改类型、也不能删，选错只能改名停用 + 新建。
- **工作流触发（2026-11-09 管理员口径）**：工作流发布后不会自己跑。审批流由对象/状态上的动作
  （`behaviorType: 7`，`behaviorValue.workflowId` 填 workflowBasicId）拉起；提醒流由**进入动作**拉起，
  且必须把工作流级 `isAllowTrigger` 置 true（界面「允许从进入动作和事件动作自动启动」，勾选并启用后
  **不可取消**）。同一对象可有多条已发布流程，互不冲突。
- **审批工作流标准骨架**：开始（10）→ 任务（30）→ 判断（70）→ 已通过/已退回动作（90）→ 结束（20）。
  判断规则用 `logicType: 40`，`logicValue` 是**带引号的同意按钮 id**；`userControlIds` 是字符串
  （传数组 400）；审批人从对象字段取时，开始节点 `selectUserType: 40` + `fielsObjectIds`。
- **审批意见签名**：签名挂在审批按钮上（`isRequireSign` / `isHaveSign`），任务级签名不用；标准做法
  还要勾「签名备注」（`isShowResons` + `reasonInputType: 20` + `resonTitle`）、「签名含义」
  （`isShowResponsibilities` + `isRequireResponsibilities` + `responsibleOptions`）和
  **「选择职位」（`isRequirePosition: true`）**。拒绝按钮的签名备注必填（`isRequiredResons: true`）。
- **提醒工作流**：任务节点配 `isNotifyTaskType: true` + `isRequireApproval: false` + 空
  `stepTaskApprovals`，任务名直接写提醒语；`notifyType: -10` 是「使用提醒策略」。
- **生命周期**：默认的「开启 / 关闭」两个状态要改名复用（头尾），中间态按流转顺序排 sort；状态不能删、
  只能停用；工作流动作里的 `statusId` 必须是启用中的状态。
- **表单/列表布局**：新组件 id 必须先经 `Layout/GetStableComponentIds` 换成稳定 id 再 `layout.save`；
  关联控件 `AKSelectorObject` 的 `listLayoutId` 要填**被关联对象**的列表布局（填全零会空白/列错乱）：
  没特殊要求就改默认布局，不新建。

## 配置任务怎么做（查存量 → 蓝图 → 顺序 → 一次审批 → 判规模修）

细节在 `docs/knowledge/标准配置思路规范.md`，开工前必读。以下是不可跳过的硬规则：

- **先查存量，再出蓝图**：对象 `code` / 对象名 / 字段名 / 状态名 / 流程名 / 菜单名 / 动作名，逐个查过再写进蓝图
  （`query.object.get`、`query.fields.list`、`query.lifecycle.status.list`、`workflow.page.list`、
  `lifecycle.useraction.list`），结论连 id 记下来。同名不是"照建"——`object.save` 按 code upsert，
  会改写现有对象；字段重名会直接报错。
- **先出蓝图，再调接口**。蓝图 = 七张表：表 0 范围与环境，加对象 / 字段 / 状态 / 工作流 / 挂载 / 布局与菜单。
  字段表要标清「进表单 / 进列表」，状态表要标清每个状态的出口——填不满就是没想清楚。
- **审核稿写业务语言**：一屏结论 +「建什么 / 怎么流转 / 谁从哪里进来」；**不贴 JSON**、不列 method / path / params，
  要拍板的做成选择题（§3.3）。弹窗不是设计评审，设计评审必须在弹窗之前。
- **施工顺序固定**：对象 → 字段 → 表单布局 → 列表布局 → 菜单 → 生命周期状态 → 工作流 → 挂载。
  计划步骤按蓝图 `resources` 数组顺序生成，跨阶段没有自动兜底，顺序由蓝图保证。
  按钮 / 进入动作要给 `workflowBasicId`，必须在流程创建并发布**之后**挂。
- **蓝图按段施工，一段一个计划一次审批**。审批是一次性、绑定 `planHash`、30 分钟过期、同一时间只挂一条；
  把计划切成「一两个接口」会让用户反复点同意，且容易互相打架。分段是执行粒度（工作流要拿上一步的 id），
  不是重新审批的理由；新增资源 / 不可逆动作（发布、`isAllowTrigger`、`enableLifeCycle`、改 `code`）才重新确认。
- **出错先判规模，不推倒重来**：字段类型 / 名称错 → 改名（停用）→ 停用 → 新建 → 换控件；布局错 → 直接覆盖；
  状态错 → 改名停用 + 新建 + 重建按钮；对象错 → 先评估能否复用，换对象时旧对象改名停用并从菜单摘入口。
  对象 / 状态 / 字段删不掉，重来只会留脏数据。字段 `dataType` 不可改、工作流发布不可回退。
- **停用不会自动传播**：布局控件、列表列、`taskFields`、动作节点 `statusId`、菜单引用都要手动换；
  状态上的按钮没有 update / 删除能力，状态建错就得在新状态上重建按钮。

## 接口卡片文案（三份文档）

- **三份文档各管一段**：`docs/knowledge/基础API使用指南.md` 管「这条接口是什么」（介绍 / 返回 / 要点 /
  **一条**案例）；`docs/knowledge/标准配置使用规范.md` 管「怎么用它配出标准配置」（同一接口可以有多个方案，
  多步骤流程写这里）；`docs/knowledge/系统常用映射表.md` 管「系统里的数字动作是什么」（stepType / status /
  behaviorType / belongType / menuActionType 等码表）。
- **读法**：host 每次 `GET config/capabilities` 现读三份文档（按 mtime + size 缓存），改完刷新页面即生效，
  不用 build，也不用重启 host。环境变量 `AKSO_WORKBENCH_API_GUIDE` / `AKSO_WORKBENCH_RECIPE_BOOK` /
  `AKSO_WORKBENCH_ACTION_MAP` 可以指向仓库外的文档。
- **分工**：结构字段（method / path / params / risk / irreversible）仍以 `plugin/src/config/capabilities.ts`
  为准，文档只负责文案；文档缺章节时回落到目录里的 `description` / `example` / `returns` / `notes`。
  加接口或改接口时，**同一提交里补文档**，不要让卡片文案和目录各说各的。
- **基础指南格式**：`### <capability id> <标题>`；开头 `>` 引用块是介绍；`**返回**：…` 是输出结果；
  `**要点**` 下写列表；`**案例**` 下**只写一条** `#### 案例 · 标题`，节内第一个 ```json 代码块是请求参数，
  代码块前后的散文分别是案例说明与「预期：…」。写第二条时解析器只取第一条并告警——多步骤流程请去写
  标准配置方案，那里可以有任意多条。
- **标准配置规范格式**：`### <capability id> <标题>` 归属某张卡片；`#### 方案 · <名字>` 一个方案一块，
  同一接口可多块；`>` 是方案简介；`**步骤**` 有序列表，每条开头用反引号写接口 id（如 `layout.detail`）；
  `**校验**` 列表；`**示例**` 一个 json 代码块；`**预期**` 散文。文档里没登记方案的接口，卡片上就不出
  「标准配置用法」这一栏——所以"还没标准方案"本身就是一种合法状态，不用凑。
- **动作映射格式**：`## 分组名` + 一行 `作用域：workflow / lifecycle / menu`（决定在哪些卡片里显示，写
  「通用」则所有卡片都显示）+ `| 值 | 含义 | 备注 |` 表。新动作码先在这份文档登记，再改代码。
- **校验**：`node plugin/scripts/api-guide.mjs --check` 连三份文档一起校验（缺章节 / 多余 id / 解析告警 /
  方案 id 不在目录，有问题 exit 1）；`--seed` 补基础指南骨架；`--list` 看登记状态。
  `plugin/test/api-guide.test.ts` 与 `plugin/test/config-docs.test.ts` 会断言真文档零告警、目录全覆盖、
  每条恰好一条案例、至少一个标准方案、映射表非空——测试失败时先跑 `--check` 定位。