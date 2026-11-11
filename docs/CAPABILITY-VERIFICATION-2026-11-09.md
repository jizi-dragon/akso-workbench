# 配置能力实测台账（2026-11-09）

> 目的：把 `plugin/src/config/capabilities.ts` 里的能力逐条真调一遍，确认「接口有效 + 示例有效」，
> 通过的把卡片标签从「过期」扭成「已验证」（名单在 `plugin/src/shared/approved-tools.ts`）。
> 每条都记录：**输入（实际发的请求）→ 输出（实际收到的响应）→ 你的系统上发生了什么变化**。
>
> 2026-11-09 补记：`auth.oauth.login`、`auth.oauth.refresh` 两条按用户要求已从目录删除
> （要浏览器 OAuth 链路，脚本化验证不了，也不该作为配置工具暴露）；`auth.login` 随后也删了
> （写入只保留 akso-cloud 代登录，本机不再接受明文口令）。本文保留它们当时的实测记录仅作历史，
> 白天目录为 **44 条**（43 已验证 + 1 过期）。
>
> 2026-11-09 晚补记：`layout.save` 由用户在实际控制台操作 + 本仓监听框架抓包复验通过，
> 标签转「已验证」；当时未验证的只剩 `menu.sort`。
>
> 2026-11-09 夜补记：按作业提交 2.0 的实际演示，新增 3 条生命周期动作能力
> （`lifecycle.useraction.list`、`lifecycle.enteraction.create`、`lifecycle.enteraction.list`）；
> 同时按用户要求删除 `menu.sort`（全量替换且无读整棵树的接口，不做工具）。
> 当前目录 **47 条**：44 条已验证 + 3 条抓包确认但未脚本实测（卡片仍标「过期」）。
>
> 2026-11-11 补记：上述 3 条已在 standard-val 真调通过并经用户核对确认，全部入账；
> 当前目录 47 条、名单 47 条，卡片不再有「过期」。详见 `CAPABILITY-VERIFICATION-2026-11-11.md`。

## 一、结论

| 项 | 数 |
| --- | --- |
| 能力总数（当前） | 47 |
| 实测通过 → 标「已验证」 | **44** |
| 抓包确认、待脚本实测 → 保持「过期」 | 3（`lifecycle.useraction.list`、`lifecycle.enteraction.create`、`lifecycle.enteraction.list`） |
| 已删除 | 1（`menu.sort`） |
| 顺带修正的目录错误 | 4 处（`workflow.delete` 的方法、`listlayout.filter.save` 示例的 `relationId`、`layout.save` 的示例与 `returns`、`layout.save` 的写入限制） |

- 环境：**standard-val /「公司配置环境」**，`https://standard-val.aksoegmp.com`，账号 `liyulong`（accountId `a_d6c261bab92cab81389847b4`）。
- 令牌：走 akso-cloud 代登录（`POST /api/accounts/:id/egmp-token`）换取，**没有**在磁盘上写 `egmp-session.json`，也没有让明文口令经过本机。
- 全部为**真实调用**，不是 mock；每条写完都做了回读（`get` / `list` / `page.list`）确认落库。
- 只在测试对象 `akwv1109__c` 上做写操作，**没有**碰任何既有业务对象、站点、盒子、账号。

## 二、逐条台账

### 认证（0，已全部删除）

> 历史记录（已于 2026-11-09 从目录删除，不再出现在卡片上）：
> **auth.login** 实测 `POST /api/openapi/v1.0/Auth` 错口令回 `code: 500`「用户名或密码错误!」，
> 成功路径由 akso-cloud 代登录间接证明；该能力带 `password` 参数，能把明文口令写进计划文件，
> 已随 `POST config/login` 一起删除。
> `auth.oauth.login` 实测 `code 2001` “Security headers missing”；`auth.oauth.refresh` 实测
> `code 401` “Unauthorized”。两条都需要浏览器 OAuth 链路带出的安全头/refresh token，脚本造不出来，
> 从未标记为已验证。

### 对象（4）

**object.save** · `POST /api/platform/BasicObject/SaveBasicObject`
- 输入：`{ "name": "AKW验证对象1109", "code": "akwv1109__c", "enableLifeCycle": true, "recordIdCode": 8995, … }`
- 输出：`code: 0`，`data: true`，耗时 5983ms。
- 变化：**新增一个业务对象**「AKW验证对象1109」（`akwv1109__c`），id `265e232d-6cae-6a90-b580-1ca742a300f4`；平台同时自动生成布局 `akwv1109_base_layout__ak` 与列表视图 `akwv1109_table__ak`。控制台「平台 → 业务对象」里可查到。

**object.field.page** · `POST /api/platform/BasicObject/FieldPage`
- 输入：`{ "objectId": "265e232d-…a300f4", "pageIndex": 1, "pageSize": 100 }`
- 输出：`code: 0`，`data.count: 13` + 13 条字段定义。
- 变化：无（只读）。

**object.fields.outfield** · `GET /api/platform/BasicObject/FieldsAndOutField`
- 输入：`{ "objectId": "265e232d-…a300f4" }`
- 输出：`code: 0`，字段 + 外部字段全量数组（约 297KB）。
- 变化：无（只读）。

**query.object.get** · `GET /api/openapi/v1.0/BasicObject/{code}`
- 输入：`code = akwv1109__c`
- 输出：`code: 0`，`data.id`、`data.lifeCycleId = 72d16ca7-7de5-ae4e-4a7d-bcb30ce41634` 等对象元数据。
- 变化：无（只读）。

### 字段与选项集（4）

**field.save** · `POST /api/platform/BasicObject/SaveField`
- 输入：3 条字段定义——`akwv_text__c`（文本）、`akwv_num__c`（数字）、`akwv_pickfield__c`（选项，dataType 4，挂选项集 `akwv_pick__c`）。
- 输出：`data: true`；新字段 id：`929c1a5a-0f62-8ac0-67ff-12f7649d4f9e`（文本）、`b2698f8a-3ab5-fd9e-3e21-39f1b50cdbc5`（数字）、`ea437099-620f-0776-7bc4-7c9107f3fce0`（选项）。
- 变化：**对象上新增 3 个字段**，可在「业务对象 → 字段」里查到。

**picklist.save** · `POST /api/platform/ObjectPicklist/save`
- 输入：`{ "code": "akwv_pick__c", "name": "AKW验证选项集", "items": ["甲", "乙"] }`
- 输出：`data: true`。
- 变化：**新增一个选项集** `akwv_pick__c`（`objectPicklistId: 11ecd1ab-2d70-6c18-9d19-9b68a3e3b795`）。

**query.fields.list** · `GET /api/openapi/v1.0/BasicObject/field/{objectCode}`
- 输入：`objectCode = akwv1109__c`
- 输出：`data[]`，含上述 3 个新字段 + 系统字段。
- 变化：无（只读）。

**query.picklist.get** · `GET /api/openapi/v1.0/BasicObject/picklist/{code}`
- 输入：`code = akwv1109__c`
- 输出：`data[]`，含 `objectPicklistId: 11ecd1ab-…a3e3b795`。
- 变化：无（只读）。

### 生命周期（5 + 3 条夜补）

**lifecycle.status.create** · `POST /api/config/lifecycle/Status/Create`
- 输入：`{ "lifecycleId": "72d16ca7-…bcc30ce41634", "name": "AKW状态A", "code": "akwv_status_a", "type": 1 }`（随后同样方式建「AKW状态B」）
- 输出：`{ "Id": "6f5a22e5-6ce2-f30f-f9dd-24ff52bd3f7b" }`；B = `32748b76-b709-2e0e-bf22-c874b62ea377`。
- 变化：**生命周期上新增 2 个状态**（A/B），控制台「生命周期」里可查。

**lifecycle.status.update** · `PATCH /api/config/lifecycle/Status/Update`
- 输入：状态 A 的 id + 改后的名称/sort。
- 输出：`data: true`。
- 变化：状态 A 的名称被改写（同一对象上可见）。

**query.lifecycle.status.list** · `GET /api/openapi/v1.0/BasicObject/lifecycleStatus/{objectCode}`
- 输入：`objectCode = akwv1109__c`
- 输出：`data[]`，含 A、B，以及平台自带的 `init_status__c`（开启 `83add63c-8665-6d37-1ee9-0dab06509909`）。
- 变化：无（只读）。

**lifecycle.useraction.create** · `POST /api/config/lifecycle/UserAction/Create`
- 输入：挂在状态 A 上的按钮「提交」，`behaviorType: 7`（发起流程），`workflowId: 1765c051-…3a24ce8a6dd4`。
- 输出：`data: "856c7385-8580-f974-1ea6-064d1505810d"`（字符串 id，不是对象）。
- 变化：**状态 A 上多了一个操作按钮「提交」**，绑到验证流程。

**lifecycle.useraction.get** · `POST /api/config/power/LifestatusUserAction/Get`
- 输入：`{ "statusId": "6f5a22e5-…bd3f7b" }`
- 输出：`{ objectId, lifecycleId, statusId, roles[] }`，roles 里 `roleCode: DEFAULT`、`roleName: 状态行为`、`powerValue: 34`。
- 变化：无（只读）。

### 布局（3）

**layout.list** · `GET /api/platform/Layout/LayoutList`
- 输入：`{ "objectId": "265e232d-…a300f4" }`
- 输出：1 条默认布局 `akwv1109_base_layout__ak`（id `6767eacd-a060-fe1a-b2f2-e798d2cfa3b7`，objectTypeId `d54f81e6-4b1b-f0d8-0d0d-a18cb8b97896`）。
- 变化：无（只读）。

**layout.detail** · `GET /api/platform/Layout/LayoutDetail`
- 输入：`{ "layoutId": "6767eacd-…cfa3b7" }`
- 输出：`data.sections`（只有工作流时间线分区 type=4，id `23f50cea-fdda-3daf-7ada-58e94a775daf`）、`controls: []`、`pages: []`。
- 变化：无（只读）。

**layout.save** · `POST /api/platform/Layout/SaveLayoutDetail`
- 输入 A（自造最小 section）：`sections` 追加一条自己拼的 section。
- 输出 A：`code: 500`，`"[layout_section] BelongId 不正确，该值必须由 FullPath 生成"`。
- 输入 B（已有分区里自造 control）：`controls` 追加一条。
- 输出 B：`code: 500`，`"[layout_control] BelongId 不正确"`。
- 输入 C（保真回写：sections 原样、controls `[]`、pages `[]`）：200 / `code: 0`，`data = layoutId`；不改内容。
- 输入 D（2026-11-09 晚，用户在控制台操作 + CDP 抓包）：把 `LayoutDetail.data` 整份读出 → 新增「基础信息」`basic_info__cs` 分区 + 4 个控件（`name__ak` / `akwv_text__c` / `akwv_num__c` / `akwv_pickfield__c`）→ 整份回写。请求体 3133B，200 / 176ms，`code: 0`，`data = "6767eacd-a060-fe1a-b2f2-e798d2cfa3b7"`。
- 变化：布局 `akwv1109_base_layout__ak` 由 1 分区 / 0 控件 变为 2 分区 / 4 控件（回读 `LayoutDetail`：2623B → 8116B）。**已通过**，标签转「已验证」。
- 结论：这条能力不是「拼布局」，是「读全量 → 原地改 → 整份回写」。**之前的判断修正**：新建 section 不需要完整对象，`{ id, code, name, type, columnsNum }` 就够；control 只需 `{ id, sectionId, code, name, fieldId, type, gridSpan, attrs }`。A/B 的报错都指向 `belongId`，而抓包确认浏览器真实请求体里**没有** `belongId` / `fullPath`——照这个形状回传就能通过。

### 列表布局（6）

**listlayout.list** · `GET /api/platform/Listlayout/List`
- 输入：`{ "objectId": "265e232d-…a300f4" }`
- 输出：`data[]`，含 `akwv1109_table__ak`（id `da114f3d-ce70-77a4-63c8-9b63df5d2185`）。
- 变化：无（只读）。

**listlayout.save** · `POST /api/platform/Listlayout/Save`
- 输入：`{ "name": "AKW验证列表1109", "code": "akwv1109_all__ak", "objectId": "265e232d-…a300f4", … }`
- 输出：`{ "id": "0ecab352-e5a7-7fac-f7b1-fef0544de1fc" }`。
- 变化：**新增一个列表视图**（code `akwv1109_all__ak`）。

**listlayout.columns.get** · `GET /api/platform/Listlayout/Columns`
- 输入：`{ "listlayoutId": "0ecab352-…4de1fc" }`
- 输出：`data[]` 列定义（含系统列 `status_id__sys`）。
- 变化：无（只读）。

**listlayout.columns.set** · `POST /api/platform/Listlayout/AddColumns`
- 输入 A：只带 `akwv_text__c`、`akwv_num__c`。
- 输出 A：`code: 500`，`"已设置在悬停卡和默认列表显示，无法直接删除：状态"`。
- 输入 B：带上 `status_id__sys` + 两个新字段 → 通过，`data: 3`。
- 回读：`columns.get` 返回 `status_id__sys, akwv_text__c, akwv_num__c`。
- 变化：**列表显示的列变了**（3 列）。注意：`status_id__sys` 不能省。

**listlayout.filter.save** · `POST /api/platform/Listlayout/SaveFilter`
- 输入 A：`details[0].relationId: "r1"`、`relations[0].id: "r1"`。
- 输出 A：HTTP 400，`"One or more validation errors occurred."`
- 输入 B：把 `relationId` / `relations[].id` 换成 GUID（如 `22222222-2222-4222-8222-222222222222`），条件 `akwv_num__c = 5`。
- 输出 B：200 / `code: 0`，`data: null`。
- 回读：`filter.get` 返回该条件与 relations，说明落库。
- 变化：**列表视图上多了一条筛选**（条件：数字字段 = 5）。注意：返回值不是文档说的「新筛选 id」，是 null。

**listlayout.filter.get** · `GET /api/platform/Listlayout/Filters`
- 输入：`{ "listlayoutId": "0ecab352-…4de1fc" }`
- 输出：`data.{belongId, details[], relations[], sceneType, ignoreIfRvalueIsNull}`。
- 变化：无（只读）。

**lifecycle.useraction.list** · `GET /api/config/lifecycle/UserAction/GetListByStatusId?statusId=…`
- 输入：待提交状态 `46e3ce00-c78e-f5a3-17cd-096ba7343175`、已退回状态 `a077aead-6ad2-6631-b939-815db1378a14`。
- 输出：按钮数组；用于创建前查重与创建后回读。
- 变化：只读。
- 证据：2026-11-09 20:03–20:04 作业提交 2.0 演示抓包，HTTP 200。

**lifecycle.enteraction.create** · `POST /api/config/lifecycle/EnterAction/Create`
- 输入：`{code:"dd__c", belongId:"a077aead-6ad2-6631-b939-815db1378a14", belongType:1, actions:[{code:"dd__c", configs:[{behaviorType:7, behaviorValue:{workflowId:"d3cd2559-2f43-d5a0-2063-3a24d1ee2875"}}]}]}`（实际 body 还带 `id`/`uid`/`sort`/`executeType` 等）。
- 输出：`data:["84908509-0d00-be66-284e-fdd867a78756"]`。
- 变化：已退回状态新增进入动作，绑定提醒流 `df__c`；回读 `name:null`、`isBeforeWorkflowCompleteReminderTask:false`。
- 证据：2026-11-09 20:04:25，HTTP 200。

**lifecycle.enteraction.list** · `GET /api/config/lifecycle/EnterAction/GetListByStatusId?statusId=…`
- 输入：已退回状态 `a077aead-6ad2-6631-b939-815db1378a14`。
- 输出：进入动作数组；用于查重与回读。
- 变化：只读。
- 证据：2026-11-09 20:04 作业提交 2.0 演示抓包，HTTP 200。

### 工作流（18）

**workflow.create** · `POST /api/platform/Workflow/AddWorkflowBasic`
- 输入：`basicConfig{ name: "AKW验证流程1109", code: "akwv_wf1109", objectId: 对象 id, lifecycleId, workflowType: 10, status: 10 }`、`variables: []`
- 输出：configId `c38f0819-eac4-d008-1c36-3a24ce8a6dd4`、workflowBasicId `1765c051-2d10-370e-8c83-3a24ce8a6dd4`、status 10。
- 变化：**新增一条工作流（草稿）**，并自动带开始/结束两个节点。

**workflow.get** · `GET /api/platform/Workflow/GetWorkflowBasic`
- 输入：`{ "id": "c38f0819-…a6dd4" }`
- 输出：**扁平**的 basicConfig（没有 `basicConfig` 外层，也没有 `variables`），含 `status`、`versionNo`、`objectCode`。
- 变化：无（只读）。

**workflow.page.list** · `POST /api/platform/Workflow/GetWorkflowBasicPageViewList`
- 输入：`{ "pageIndex": 1, "pageSize": 3, "filters": [] }`
- 输出：`data.count: 897`（验证后 +1）+ 3 条样例。
- 变化：无（只读）。

**workflow.status.change** · `POST /api/platform/Workflow/ChangeWorkflowBasicStatus`
- 输入：`{ "ids": ["c38f0819-…a6dd4"], "status": 40 / 20 / 30, "workflowBasicId": "1765c051-…a6dd4" }`
- 输出：均 `code: 0`、`data: null`；`workflow.get` 回读确认 40→20→30 都生效。
- 变化：**工作流的发布状态被改**（草稿 10 / 启用 20 / 禁用 30）。
- 注意：**20 → 10、20 → 40 会被平台静默忽略**（code 0 但状态不变），发布后回不到草稿。

**workflow.steps.list** · `GET /api/platform/Workflow/GetWorkflowSteps`
- 输入：`{ "workflowConfigId": "c38f0819-…a6dd4" }`
- 输出：`data[]`，开始节点 `ebfd43d6-dfae-c72b-44d6-3a24ce8a6df1`（stepType 10）、结束 `943c9c16-5e73-34c5-672d-3a24ce8a6df1`（20）。
- 变化：无（只读）。

**workflow.step.get** · `GET /api/platform/Workflow/GetWorkflowStepWithDetails`
- 输入：`{ "id": "ebfd43d6-…8a6df1" }`
- 输出：`data.step`，含 `stepStartConfig`、`startUpControls: []`、`nextStepId`。
- 变化：无（只读）。注意：`id` 传空会回 `data.step: null`，不报错。

**workflow.controls.list** · `GET /api/platform/Workflow/GetWorkflowStepControls`
- 输入：`{ "workflowConfigId": "c38f0819-…a6dd4", "stepId": "2a493681-…8af11e" }`
- 输出：`data: []`（该节点没配控件）。
- 变化：无（只读）。注意：`stepId` 传空会 500 `"Nullable object must have a value."`

**workflow.start.update** · `POST /api/platform/Workflow/UpdateWorkflowStartStep`
- 输入：`step{ id/name/code/stepType 10, nextStepId: 任务节点 id, nextStepList: [...] }`
- 输出：`data.step`，`nextStepId` 已指向任务节点。
- 变化：**开始节点的去向被改**（原本直接连结束）。

**workflow.task.add** · `POST /api/platform/Workflow/AddWorkflowTaskStep`
- 输入 A：`stepTaskApprovals[0].isRequireSign: true`。
- 输出 A：`code: 500`，`"该对象未启用电子签名，流程任务禁止配置电子签名"`。
- 输入 B：`isRequireSign: false`、`isRequirePosition: false` → 通过。
- 输出 B：`step.id = 2a493681-11e1-77ea-c4a9-3a24ce8af11e`（sort 0、nextStepId 结束）。
- 变化：**流程里多一个审批节点「AKW部门审批」**。

**workflow.decision.add** · `POST /api/platform/Workflow/AddWorkflowDecisionStep`
- 输入：`stepDecision.rules[0]{ condition{details/relations}, nextStepId: 结束 }`，条件 targetType 7、targetId=任务节点、logicType 40。
- 输出：`step.id = 0fd37ddd-cc0d-4d79-0963-3a24ce8af28e`。
- 变化：**多一个条件分支节点「AKW是否通过」**。

**workflow.action.add** · `POST /api/platform/Workflow/AddWorkflowActionStep`
- 输入：`stepAction.actions[0].configs[0]{ behaviorType: 9, behaviorValue.statusId: 状态B }`。
- 输出：`step.id = 5069b747-ebf6-d56f-ee09-3a24ce8acae7`（`isEnable: false`）。
- 变化：**多一个动作节点「AKW置为状态B」**（节点本身默认停用，`update` 时置 true）。

**workflow.update** · `POST /api/platform/Workflow/UpdateWorkflowBasic`
- 输入：`{ "id": "c38f0819-…a6dd4", "basicConfig": { …, "name": "AKW验证流程1109（改）" } }`
- 输出：`data.basicConfig` 回显新名字。
- 变化：**工作流名称被改**（列表页可见）。

**workflow.task.update** · `POST /api/platform/Workflow/UpdateWorkflowTaskStep`
- 输入：`step{ id: 2a493681-…, name: "AKW部门审批（改）", sort: 31 }`（整份 step 回写）
- 输出：`data.step.sort: 31`、名字已改。
- 变化：**该节点的名称/排序被改**。

**workflow.decision.update** · `POST /api/platform/Workflow/UpdateWorkflowDecisionStep`
- 输入：`step{ id: 0fd37ddd-…, name: "AKW是否通过（改）", sort: 91 }`
- 输出：`data.step` 回显 sort 91。
- 变化：该分支节点名称/排序被改。

**workflow.action.update** · `POST /api/platform/Workflow/UpdateWorkflowActionStep`
- 输入：`step{ id: 5069b747-…, name: "AKW置为状态B（改）", sort: 121, isEnable: true }`
- 输出：`data.step.isEnable: true`。
- 变化：该动作节点被启用并改名。

**workflow.step.copy** · `POST /api/platform/Workflow/CopyWorkflowCurrentStep`
- 输入：`{ "stepName": "AKW部门审批副本", "stepCode": "akwv_step_task_copy", "workflowConfigId": "c38f0819-…", "stepId": "2a493681-…" }`
- 输出：`data: true`；新节点 id `6d2c61ed-5b94-2f37-f5b2-3a24ce8b4671`（回列表按 code 反查）。
- 变化：**多一个副本节点**（sort 91），随后被删掉。

**workflow.step.delete** · `DELETE /api/platform/Workflow/DeleteWorkflowStep`
- 输入：`{ "id": "6d2c61ed-…b4671" }`
- 输出：`data: "6d2c61ed-5b94-2f37-f5b2-3a24ce8b4671"`（**字符串 id，不是 true**）。
- 变化：副本节点消失；`steps.list` 回读确认。

**workflow.delete** · `DELETE /api/platform/Workflow/DeleteWorkflowBasic`（**本次改的目录**）
- 输入 A：`POST` + `{ "id": "c38f0819-…" }` → **405**（目录原来写的就是 POST，错的）。
- 输入 B：`DELETE` + `{ "id": "c38f0819-…" }`（状态 30）→ 200 / `code: 500`，`"禁止删除禁用状态的流程"`。
- 输入 C：状态 20 → `"禁止删除启用状态的流程"`。
- 输入 D：另建一条**草稿**流程 `c448d5fe-7614-6b94-7056-3a24ce8cbb5b`，`DELETE {id}` → 200 / `code: 0`，`data` 为该 id；`get` 回读 `data: null`。
- 变化：草稿流程被彻底删除。**结论：必须 DELETE + body，且只有草稿(10)能删。**

### 菜单（2 + 1 已删除）

**menu.submit** · `POST /api/platform/Menu/Submit`
- 输入 A：母菜单 `code: "akwv_menu_1109"`。
- 输出 A：`code: 500`，`"Code不符合规则"`。
- 输入 B：`code: "akwv_menu1109__c"` → `{ "Id": "d27aecb7-0287-5427-b6d7-5bf6ab1ee96b" }`。
- 输入 C：子菜单 `code: "akwv_child1109__c"`，带 `parentMenuId`、`objectId`、`listLayoutId`、`layoutId` → `{ "Id": "fa41f6b1-63f9-e56d-c564-621912550610" }`。
- 变化：**新增 1 个母菜单 + 1 个子菜单**（控制台菜单树里可见；注意 `menu.group.list` 查的是「菜单分组」，看不到这两个菜单）。

**menu.group.list** · `POST /api/platform/MenuGroup/QueryList`
- 输入：`{ "pageIndex": 1, "pageSize": 50, "filters": [] }`
- 输出：6 个分组：所有菜单 / 测试菜单_yl / 偏差管理考核（勿动）/ 业务管理 / 管理端 / 打印中心。
- 变化：无（只读）。

**menu.sort（2026-11-09 已删除）** · `POST /api/platform/Menu/Sort`
- 输入：整套菜单树的顺序数组。
- 输出：**未执行**。
- 变化：无。**未通过且已从工具目录删除**：这是全量替换，且没有「读整棵菜单树」的接口，脚本误操作会把子菜单移出层级；需要时在控制台手工操作。

### 公告（1）

**notice.template.list** · `POST /api/platform/notice/GetMessageTemplateByObject`
- 输入：`{ "pageIndex": 1, "pageSize": 20 }`（可为空对象）
- 输出：`data[]`，约 178KB 的模板列表。
- 变化：无（只读）。

## 三、目录修正（已改 `capabilities.ts`）

1. `workflow.delete`：`method` 由 `POST` 改为 **`DELETE`**；`returns` 改为「data 为被删的 id」；`notes` 补「POST 会 405」「只有草稿能删，20/30 都拒绝，且发布后回不到草稿」。
2. `listlayout.filter.save`：示例里的 `relationId` / `relations[].id` 由 `"r1"` 改为 GUID 占位符；`evidence` 由 `partial` 改 `verified`；`returns` 改为「data: null（不返回新 id）」；`notes` 补「短名会 400 validation error」。
3. `layout.save`：`example` 改为「把 `layout.detail` 的 `data.sections` / `data.controls` / `data.pages` 原样放回」；`returns` 改「data 为布局 id」；`notes` 重写（整份回传、不自造 `belongId` / `fullPath`、无分页传 `[]`）；`params` 补 `formEditStatus`、`skipRequiredValidation`。此前只补了失败限制，示例仍是会 500 的最小构造。

## 四、standard-val 上留下的数据（请核对/清理）

这些是本次验证**真实创建**的，登录 `standard-val` 控制台即可看到：

| # | 类型 | 标识 | 说明 |
| --- | --- | --- | --- |
| 1 | 业务对象 | `akwv1109__c`「AKW验证对象1109」 | id `265e232d-6cae-6a90-b580-1ca742a300f4`；含 3 字段 + 自动布局/列表 |
| 2 | 字段 | `akwv_text__c`、`akwv_num__c`、`akwv_pickfield__c` | 挂在上面这个对象上 |
| 3 | 选项集 | `akwv_pick__c` | objectPicklistId `11ecd1ab-2d70-6c18-9d19-9b68a3e3b795` |
| 4 | 生命周期状态 | `AKW状态A` / `AKW状态B` | A = `6f5a22e5-…`，B = `32748b76-…` |
| 5 | 状态按钮 | 「提交」 | `856c7385-8580-f974-1ea6-064d1505810d`，挂在状态 A，触发验证流程 |
| 6 | 列表视图 | `akwv1109_all__ak` | `0ecab352-e5a7-7fac-f7b1-fef0544de1fc`，3 列 + 1 条筛选（数字=5） |
| 7 | 菜单 | `akwv_menu1109__c` / `akwv_child1109__c` | `d27aecb7-…` / `fa41f6b1-…` |
| 8 | 工作流 | `AKW验证流程1109（改）` | `c38f0819-eac4-d008-1c36-3a24ce8a6dd4`，现停在 **status 30（禁用）**，**删不掉**（见下） |

- `layout.save` 的保真回写（输入 C）没有改动内容；后来的整份回写（输入 D）给默认布局新增了「基础信息」分区 + 4 个控件，可在控制台的「表单布局」里看到。
- **没有**创建任何对象实例（业务记录），只有元数据。
- 工作流删除限制：平台对 20（启用）和 30（禁用）都拒绝删除，且发布后无法退回草稿。这条工作流需要你在控制台另行处理（例如作废/联系管理员），或者留着当验证痕迹。
- 清理建议：控制台删掉对象 `akwv1109__c` 会连带字段/布局/列表；选项集、状态、菜单、工作流需分别清理。

## 五、怎么复现

```powershell
# 1) 换票（akso-cloud 代登录，口令不落本机）
#    POST {cloud}/api/accounts/a_d6c261bab92cab81389847b4/egmp-token
# 2) 用换到的 baseUrl + token 走能力目录
node %TEMP%\akw-verify\run.mts        # 通用执行器：mint → 逐条 invokeCapability → ledger.jsonl
node %TEMP%\akw-verify\inspect.mts    # 单条看完整 JSON
```

- 每次调用的原始记录在 `%TEMP%\akw-verify\ledger.jsonl`（含 params / code / traceId / 耗时）。
- 卡片标签：`plugin/src/shared/approved-tools.ts` 的 `APPROVED_TOOL_IDS` 已写入 **43** 个 id；
  `npm --prefix plugin run build` 已重建。**重启 DSH 后刷新页面**，卡片页应显示「已验证 43 / 过期 1」。
- 回归护栏：`plugin/test/host.test.ts` 新增一条测试——台账里的 id 必须都在能力目录里、
  每条能力的 `testStatus` 必须与台账一致、上面 1 条未通过的不能混进名单。`npm run check` 现为 48 个插件测试全绿（根 37）。
