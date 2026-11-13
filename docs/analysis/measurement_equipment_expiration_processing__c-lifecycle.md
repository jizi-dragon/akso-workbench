# 计量设备校准到期处理（measurement_equipment_expiration_processing__c）生命周期分析

> 环境：`https://tonbridge-config.aksoegmp.com`（账号 `lyl`）
> 对象 ID：`3a24e112-b21b-1678-2a3d-2cf8ebd1f7f3`
> 生命周期：`计量设备校准到期处理生命周期`（`3a24e112-b21b-65d1-90e7-ed1991c8d7cf`）
> 分析时间：2026-11-13　只读，未改平台配置

## 1. 概览

| 项 | 数量 |
|---|---:|
| 生命周期状态 | 5（全部启用） |
| 用户动作 | 3 |
| 进入动作 | 3 |
| 工作流 | 3（全部 `isEnable=false`） |
| 进入条件 | 0（未配置） |

## 2. 生命周期状态

| sort | 状态 | code | 状态 ID |
|---:|---|---|---|
| 0 | 预处理 | `init_status__c` | `59df75d6-bcb7-7fe5-2eed-3a24e112c3c6` |
| 1 | 提交变更数据 | `submit_change_data__c` | `b6ef1f51-f3a1-cd6a-b806-3a24e11e0fd2` |
| 2 | 待审核 | `awaiting_moderation__c` | `7b080070-63d8-9934-824a-3a24e5d60126` |
| 3 | 等待校准完成 | `wait_for_calibration_to_complete__c` | `a59ece96-8b87-1063-db23-3a24e5d92263` |
| 4 | 关闭 | `complete_status__c` | `67142b01-e2dd-07cd-071b-3a24e112c3c7` |

## 3. 用户动作

`UserAction/GetListByStatusId` 对这些动作只返回外壳，`behaviorTypes` 为空；`UserAction/GetById` 返回 `data:null`。下表的 behaviorType 来自 `GET /api/config/lifecycle/UserAction/GetUserAction?objectId=...` 的 `userActionPerms`。

| 状态 | 动作 | code | behaviorTypes | 解读 |
|---|---|---|---|---|
| 预处理 | 送校准 | `sent_for_calibration__c` | `[7]` | 发起工作流；但当前没有启用中的工作流可拉起 |
| 预处理 | 没有直接送检的器具 | `there_are_no_instruments_directly_submitted_for_inspection__c` | `[9]` | 修改状态；目标状态接口未返回 |
| 提交变更数据 | 提交 | `submit__c` | 未返回 | `GetUserAction` 未列出该动作；list 也没有 configs |

> `提交` 未出现在 `GetUserAction` 的权限列表里，不能据此断定它没有配置；也可能是权限过滤或该动作只对特定记录角色可见。

## 4. 进入动作

`EnterAction/GetListByStatusId` 同样只有外壳，必须逐个 `EnterAction/GetById`。

| 状态 | 描述 | 动作 ID | behaviorType | 配置要点 |
|---|---|---|---:|---|
| 预处理 | 生成编号 | `630134c1-eb3a-87e3-7c34-3a24e5dcaafa` | 16 | `fieldId=35183ee5-ea51-3c37-a0d3-3a24e112b273`，`codeRuleId=3a24e5dc-4fcd-214c-2203-df655bb8e667`，`type=1` |
| 待审核 | 修改变更明细数据为待审核 | `56e1cf47-c01f-0b35-1b22-3a24e5d6d3d9` | 46 | 改的是关联对象「变更明细」：`relatedObjectId=3a22101b-d87a-2d7e-fe0a-e5090ba9e28a`，目标状态 `432f12da-cc4c-5126-d77c-3a23bb4d7f5b` |
| 等待校准完成 | 为变更明细的数据创建校准记录 | `96936760-2e75-8c78-fea5-3a24e5def13d` | 36 | `isAsync=true`，`relatedRecordConfigId=0406bcf4-3c78-12c1-8d38-3a23bb725dd9`，条件 `status_id = "432f12da-cc4c-5126-d77c-3a23bb4d7f5b"` |

behaviorType 16、36 目前未在 `系统常用映射表.md` 中登记，分析页只能显示「行为 16 / 行为 36」；46 已是「修改相关对象生命周期状态」。

## 5. 工作流详情

三条工作流都属于对象 `measurement_equipment_expiration_processing__c`，但 **全部未启用**，且 `isAllowTrigger=false`。

| 名称 | code | 步骤数 | basicId | configId | isEnable | isAllowTrigger | status | version |
|---|---|---:|---|---|---|---|---:|---:|
| 校准到期处理：变更申请 | `calibration_due_process_change_request__c` | 7 | `3a24e5e2-ee1a-0386-9c84-b0fe089d5294` | `37c88a10-9658-407e-ba58-3a24e5ef84d1` | `false` | `false` | 20 | 3 |
| 校准到期处理：提交非变更器具 | `calibration_due_process_submit_non_changed_instrument__c` | 4 | `3a24e59b-b365-f8c4-9cec-9112ca90e18f` | `3a24e59b-b367-b7d9-96c4-f7faff808717` | `false` | `false` | 20 | 1 |
| 计量设备检验到期，创建检验记录 | `metering_equipment_inspection_due_create_inspection_record__c` | 4 | `3a24e4b0-11b9-9f2e-35cb-c20d38dbeba6` | `3a24e4b0-11bb-7cb3-dca8-ee343631669d` | `false` | `false` | 30 | 1 |

`status`/`version` 来自 `Workflow/GetWorkflowBasicPageViewList`；步骤来自 `Workflow/GetWorkflowSteps`。步骤接口只返回节点名称与 `nextStepName`，不返回审批人、条件表达式或动作 configs，因此下表的“说明”是**按节点名称与已知进入动作推断**，已在“证据”列标注。

### 5.1 校准到期处理：变更申请（7 步）

| sort | 步骤类型 | 节点 | 下一步 | 说明 / 证据 |
|---:|---|---|---|---|
| 1 | 开始（10） | 开始 | 动作：修改状态“待审核” | 流程入口 |
| 2 | 动作（90） | 动作：修改状态“待审核” | 任务：审核人审核 | 按名称推断：把当前对象改到「待审核」；动作 configs 未返回 |
| 33 | 用户任务（30） | 任务：审核人审核 | 判断：审核结果 | 审批任务；审批人未返回 |
| 94 | 判断（70） | 判断：审核结果 | 通过 → 动作：修改状态“待执行校准”<br>否则 → 动作：修改状态“提交变更数据” | 分支条件未返回；两个目标动作都在 `nextStepList` 中 |
| 96 | 动作（90） | 动作：修改状态“提交变更数据” | 结束 | 按名称推断：改到「提交变更数据」；configs 未返回 |
| 185 | 动作（90） | 动作：修改状态“待执行校准” | 结束 | “待执行校准”不在当前对象 5 个状态里，推断改的是关联对象/变更明细状态 |
| 100306 | 结束（20） | 结束 | — | 流程出口 |

### 5.2 校准到期处理：提交非变更器具（4 步）

| sort | 步骤类型 | 节点 | 下一步 | 说明 / 证据 |
|---:|---|---|---|---|
| 1 | 开始（10） | 开始 | 动作：修改相关对象状态“执行校准” | 流程入口 |
| 2 | 动作（90） | 动作：修改相关对象状态“执行校准” | 动作：修改状态提交变更数据 | 节点名明确写“相关对象”，不是当前对象；目标对象/状态 configs 未返回 |
| 33 | 动作（90） | 动作：修改状态提交变更数据 | 结束 | 按名称推断：把当前对象改到「提交变更数据」 |
| 100094 | 结束（20） | 结束 | — | 流程出口 |

### 5.3 计量设备检验到期，创建检验记录（4 步）

| sort | 步骤类型 | 节点 | 下一步 | 说明 / 证据 |
|---:|---|---|---|---|
| 1 | 开始（10） | 开始 | 创建校准记录 | 流程入口 |
| 2 | 动作（90） | 创建校准记录 | 修改状态执行校准 | 与进入动作 behaviorType=36「为变更明细的数据创建校准记录」命名一致；具体 configs 未返回 |
| 33 | 动作（90） | 修改状态执行校准 | 结束 | “执行校准”不在当前对象状态里，推断改的是校准记录/关联对象；configs 未返回 |
| 100094 | 结束（20） | 结束 | — | 流程出口 |

### 5.4 触发方式

- 三条工作流 `triggeredBy` 都为空；没有 `behaviorType=7` 的**已启用**进入动作指向它们。
- 对象下 `EventAction/GetList?objectId=...` 返回空数组，未发现事件动作触发。
- 三条工作流 `isEnable=false`，因此当前不应被运行；`isAllowTrigger=false` 也说明它们不接收进入动作/事件动作的自动启动。
- 结论：工作流是“已配置但停用”的状态，无法从现有数据判断设计上应由哪个按钮/事件拉起。

## 6. 进入条件

`GET /api/config/lifecycle/StatusEnterRule/GetList?lifecycleId=...` 返回空数组，`count=0`，即当前没有配置生命周期进入条件。

## 7. 流转判断（只列有证据的部分）

| 从 | 触发 | 到 | 证据等级 |
|---|---|---|---|
| 预处理 | 送校准（7） | 待审核（推断） | 低：动作是发起工作流，但工作流未启用；只能从“变更申请”第一步是修改状态为待审核推断 |
| 预处理 | 没有直接送检的器具（9） | 未知 | 中：确认是修改状态，但目标状态未由接口返回 |
| 提交变更数据 | 提交（未知） | 未知 | 低：动作存在，但行为类型与目标都没取到 |
| 待审核 | 进入动作 46 | 不改变本对象状态 | 高：改的是关联对象「变更明细」 |
| 等待校准完成 | 进入动作 36 | 不改变本对象状态 | 高：创建校准记录，异步 |

无法从现有接口证明 `关闭` 有任何入边；`关闭` 目前是孤立状态。若线上确实能关闭，关闭动作很可能来自工作流动作节点或平台事件，而三条工作流都未启用。

## 8. 风险与待确认

**P0**
1. 三条工作流全部 `isEnable=false`，`behaviorType=7` 的用户动作没有可启动流程。
2. `关闭` 状态没有任何已读到的入边，存在“流程走不到关闭”的风险。

**P1**
3. `没有直接送检的器具` 是 behaviorType 9，但目标状态未返回，无法确认它把记录改到哪个状态。
4. `提交` 动作的 behaviorType 和 configs 都取不到；`UserAction/GetUserAction` 也没列出它。

**P2**
5. behaviorType 16 / 36 未登记到常用映射表，分析页只能显示数字。
6. 没有进入条件；如果业务要求“满足条件才允许进入生命周期”，这里需要补。

## 9. 建议的下一步

1. 在平台确认三条工作流为什么是 `isEnable=false`：是尚未发布、被停用，还是只作为模板保留。
2. 打开 `没有直接送检的器具` 的动作配置，确认 behaviorType 9 的目标状态。
3. 确认 `提交` 动作的可见范围与行为类型；如果它是 `behaviorType=7`，需要绑定一条已启用工作流。
4. 若 `关闭` 应由工作流设置，检查三条工作流的结束分支是否遗漏“修改状态为关闭”。
5. 确认 behaviorType 16 / 36 的官方名称后补进 `系统常用映射表.md`。

## 10. 数据来源

- `GET /api/openapi/v1.0/BasicObject/measurement_equipment_expiration_processing__c`
- `GET /api/config/lifecycle/Status/GetList`（按 lifecycleId 过滤）
- `GET /api/config/lifecycle/UserAction/GetListByStatusId?statusId=...`
- `GET /api/config/lifecycle/UserAction/GetUserAction?objectId=...`
- `GET /api/config/lifecycle/EnterAction/GetListByStatusId?statusId=...`
- `GET /api/config/lifecycle/EnterAction/GetById?id=...`
- `GET /api/config/lifecycle/StatusEnterRule/GetList?lifecycleId=...`
- `POST /api/platform/Workflow/GetWorkflowBasicPageViewList`
- `GET /api/platform/Workflow/GetWorkflowSteps?workflowConfigId=...`
