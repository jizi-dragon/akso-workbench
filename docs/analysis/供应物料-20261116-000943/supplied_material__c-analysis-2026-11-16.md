# 供应物料（supplied_material__c）对象分析

> 数据环境：`https://tulmed-config.aksoegmp.com`
> 数据账号：`lyl`
> 生成时间：2026-11-15T16:09:43.134Z
> 数据来源：eGMP 配置接口，只读分析，不修改平台。

## 对象概览

| 项 | 值 |
| --- | --- |
| 对象名称 | 供应物料 |
| 对象 code | `supplied_material__c` |
| 对象 id | `3a1cf30a-2c26-53c5-d7e2-a11afa1ce6ac` |
| 生命周期 | 供应物料生命周期 |
| lifecycleId | `3a1cf30a-2c26-84b4-f3a6-60f82979b9f6` |
| 启用生命周期 | 是 |
| 状态 | 11 启用 / 11 总数 |
| 用户动作 | 3 |
| 进入动作 | 17 |
| 字段 | 35 |
| 工作流 | 5 |
| 进入条件 | 未配置（0 条） |

## 状态与动作

### 1. 开启（`init_status__c`）

- 状态 id：`e7cbf75f-691d-f14f-63a8-3a1cf30a5e29`
- sort：0；启用：是
- 用户动作：0；进入动作：4

**用户动作**

- 无

**进入动作**

- **修改相关对象物料影响评估至【开启】状态**（`ea7e261aeb69e94273d923a1d93d364e7`）：修改相关对象生命周期状态；修改【物料影响评估】生命周期状态为「开启」
- **修改相关对象物料资质至【开启】状态**（`e784a922e350cb42546773a1d93d3ba1c`）：修改相关对象生命周期状态；修改【供应物料资质】生命周期状态为「开启」
- **修改相关对象物料质量评估【开启】状态**（`eaea5d80d9a4c8b2450e13a1d93f32d07`）：修改相关对象生命周期状态；修改【物料质量评估】生命周期状态为「开启」
- **联动供应商生产地址**（`ebf928df118ba32d230973a1d949b18b3`）：行为 57

### 2. 审核中（`material_evaluation_plan_review__c`）

- 状态 id：`87186cef-aa49-caf1-7b87-3a1d849cec07`
- sort：1；启用：是
- 用户动作：0；进入动作：3

**用户动作**

- 无

**进入动作**

- **修改相关对象物料影响评估至【审核】状态**（`e95800f9890b9ca0785453a1d93d03065`）：修改相关对象生命周期状态；修改【物料影响评估】生命周期状态为「审核中」
- **修改相关对象物料资质至【审核】状态**（`eb26abddd73cba8e4a1393a1d93d0f17e`）：修改相关对象生命周期状态；修改【供应物料资质】生命周期状态为「审核中」
- **修改相关对象物料质量评估【审核】状态**（`e5a9125fde0be91fa8a403a1d93f2d388`）：修改相关对象生命周期状态；修改【物料质量评估】生命周期状态为「审核中」

### 3. 等待评估完成（`waiting_material_quality_assessment_complete__c`）

- 状态 id：`94fb7765-3152-e766-dea7-3a1d88204585`
- sort：2；启用：是
- 用户动作：1；进入动作：6

**用户动作**

- **提交**（`submit__c`）：修改生命周期状态；修改本对象状态为「物料评估结果汇总」

**进入动作**

- **生成业务编号**（`e91b58561d659afc46dec3a1d93b3070e`）：生成编号
- **赋值负责人**（`e00bb7643c13007ac38ad3a1d93deec13`）：行为 34
- **生成待办**（`e97e337520a03d497b53f3a1e0f166889`）：发起工作流；工作流「【提醒】等待供应物料评估完成」
  - workflowId：`3a1e0f13-6e88-ac45-cbc5-73630099108a`
- **修改相关对象物料影响评估至【评估中】**（`ee1819b28e3de15f781c03a1d93ffe7ee`）：修改相关对象生命周期状态；修改【物料影响评估】生命周期状态为「评估中」
- **修改相关对象物料质量评估至【等待质量评估完成】**（`e7270bbc3082bdf8a29103a1d9400ab8d`）：修改相关对象生命周期状态；修改【物料质量评估】生命周期状态为「等待质量评估完成」
- **经销商，进入评估结果汇总状态**（`e81d03d39e76686f684043a1ee7aebbb0`）：修改生命周期状态；修改本对象状态为「物料评估结果汇总」

### 4. 物料评估结果汇总（`summary_material_evaluation_results__c`）

- 状态 id：`f32a8737-71c9-2cb6-edf7-3a1d8e3b1d9b`
- sort：3；启用：是
- 用户动作：1；进入动作：1

**用户动作**

- **提交**（`submit__c`）：发起工作流；工作流「物料评估结果审核」
  - workflowId：`3a1d8f49-9011-5445-51aa-b8876ea28abb`

**进入动作**

- **发送提醒代办**（`e07acd44dcd8273a9b1303a1d9408741a`）：完成提醒任务；发起工作流；工作流「物料评估结果汇总提醒」
  - workflowId：`3a1d93f7-079d-750e-77ed-0a046cd02930`

### 5. 物料评估结果审核（`material_evaluation_result_review__c`）

- 状态 id：`f57ed955-805b-f0b8-0db7-3a1d8e3bfac4`
- sort：4；启用：是
- 用户动作：0；进入动作：0

**用户动作**

- 无

**进入动作**

- 无

### 6. 合格（`complete_status__c`）

- 状态 id：`e72d30ce-7bb0-1884-ccb6-3a1cf30a5e30`
- sort：5；启用：是
- 用户动作：1；进入动作：1

**用户动作**

- **淘汰**（`submit__c`）：发起工作流；工作流「供应物料淘汰审核」
  - workflowId：`3a1d939e-d43d-7cbd-1e7e-6cf9306ad659`

**进入动作**

- **修改【供应物料资质】的状态为【生效】**（`ebaa5c422fe1c90f3e29f3a1d93a5e378`）：修改相关对象生命周期状态；修改【供应物料资质】生命周期状态为「生效」

### 7. 不合格（`unqualified__c`）

- 状态 id：`bb074b79-9c64-177b-248e-3a1d88f861c5`
- sort：6；启用：是
- 用户动作：0；进入动作：2

**用户动作**

- 无

**进入动作**

- **所有供应物料不合格，修改供应商准入不合格**（`ee5a04698f3e6b78da29b3a1e19500f70`）：工作流步骤
- **修改【供应物料资质】的状态为【停用】**（`ef253d90cb30abaa37eca3a1d93a6cb77`）：修改相关对象生命周期状态；修改【供应物料资质】生命周期状态为「停用」

### 8. 供应物料淘汰审核（`supply_material_obsolescence_review__c`）

- 状态 id：`b1c85be5-23a5-b3c5-558e-3a1d8e3cf698`
- sort：7；启用：是
- 用户动作：0；进入动作：0

**用户动作**

- 无

**进入动作**

- 无

### 9. 已淘汰（`obsolete__c`）

- 状态 id：`644f30e0-53ab-cdfe-e1c0-3a1d88f8f16b`
- sort：8；启用：是
- 用户动作：0；进入动作：0

**用户动作**

- 无

**进入动作**

- 无

### 10. 终止审核（`terminate_review__c`）

- 状态 id：`b973e391-2ac9-fc29-605a-3a1d8d170f5e`
- sort：9；启用：是
- 用户动作：0；进入动作：0

**用户动作**

- 无

**进入动作**

- 无

### 11. 已终止（`terminated__c`）

- 状态 id：`f3cc656b-9758-e069-4bdf-3a1d87eb24b4`
- sort：10；启用：是
- 用户动作：0；进入动作：0

**用户动作**

- 无

**进入动作**

- 无

## 工作流解析

| # | 工作流 | code | status | 版本 | 允许自动启动 | 直接触发 | 步骤 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 【提醒】等待供应物料评估完成 | `waiting_for_the_evaluation_of_supplied_materials_to_be_completed__c` | 20 | 1 | 是 | 等待评估完成 / 生成待办（进入动作） | 3 |
| 2 | 物料评估结果审核 | `material_evaluation_result_review__c` | 20 | 1 | 否 | 物料评估结果汇总 / 提交（用户动作） | 11 |
| 3 | 物料评估结果汇总提醒 | `waiting_evaluation_completion_reminder__c` | 20 | 1 | 是 | 物料评估结果汇总 / 发送提醒代办（进入动作） | 3 |
| 4 | 供应物料淘汰审核 | `supply_material_obsolescence_review__c` | 20 | 1 | 否 | 合格 / 淘汰（用户动作） | 11 |
| 5 | 【审核】物料准入申请 | `material_access_application_approval__c` | 20 | 1 | 否 | 状态绑定：开启（未反查到直接动作） | 10 |

### 1. 【提醒】等待供应物料评估完成

- workflowBasicId：`3a1e0f13-6e88-ac45-cbc5-73630099108a`
- workflowConfigId：`3a1e0f13-6ea7-e06b-d58b-d1d3bed00ed8`
- code：`waiting_for_the_evaluation_of_supplied_materials_to_be_completed__c`；status：20；版本：1；isAllowTrigger：是
- 触发：等待评估完成 / 生成待办（进入动作）
- 生命周期状态绑定：等待评估完成（`94fb7765-3152-e766-dea7-3a1d88204585`）

| 序号 | 类型 | 名称 | code | 配置摘要 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| 1 | 开始 | 开始 | `waiting_for_the_evaluation_of_supplied_materials_to_be_completed_Start__c` | — | 27bdc5b1-6c9e-2a4e-b94c-3a1e0f15b160 |
| 2 | 任务 | 任务：责任人 | `task__c` | 通知任务 | e7207a84-501f-a030-9c13-3a1e0f136ef4 |
| 3 | 结束 | 结束 | `waiting_for_the_evaluation_of_supplied_materials_to_be_completed_End__c` | — | — |

**流程阅读**

- 1. [开始] 开始；下一步 27bdc5b1-6c9e-2a4e-b94c-3a1e0f15b160。
- 2. [任务] 任务：责任人：通知任务；下一步 e7207a84-501f-a030-9c13-3a1e0f136ef4。
- 3. [结束] 结束；下一步 结束。

**对生命周期的影响**

- 没有直接改本对象状态的 behaviorType=9 动作。
- 该流程只做提醒/审批/读取，不改任何生命周期状态。

### 2. 物料评估结果审核

- workflowBasicId：`3a1d8f49-9011-5445-51aa-b8876ea28abb`
- workflowConfigId：`3a1d8f49-9012-c346-ac54-64a3dfa68993`
- code：`material_evaluation_result_review__c`；status：20；版本：1；isAllowTrigger：否
- 触发：物料评估结果汇总 / 提交（用户动作）
- 生命周期状态绑定：物料评估结果汇总（`f32a8737-71c9-2cb6-edf7-3a1d8e3b1d9b`）

| 序号 | 类型 | 名称 | code | 配置摘要 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| 1 | 开始 | 开始 | `material_evaluation_result_review_Start__c` | — | aea64fd4-7fae-3542-f099-3a1d8f4b8a22 |
| 2 | 动作 | 动作节点：修改当前对象状态为【物料评估结果审核】 | `change_status_review__c` | 修改生命周期状态：本对象 → 物料评估结果审核 | c6432ff8-35fe-f3d1-b486-3a1d8f4cc624 |
| 3 | 任务 | 任务：物料评估结果审核 | `review__c` | 任务；需要审批；审批按钮：同意/拒绝 | 0e19ba97-0ebb-e668-b34a-3a1d8f5af4a9 |
| 4 | 判断 | 判断：物料评估结果审核结果 | `determine_review_result__c` | 规则 1 条；有否则分支 | — |
| 5 | 任务 | 任务：物料评估结果批准 | `approve__c` | 任务；需要审批；审批按钮：同意/拒绝 | 25fd69f9-74a9-ed1a-38b9-3a1d8f627247 |
| 6 | 判断 | 判断：物料评估结果批准结果 | `approval_result__c` | 规则 1 条；有否则分支 | — |
| 7 | 动作 | 动作节点：修改当前对象状态为【不合格】 | `modify_status_unqualified__c` | 修改生命周期状态：本对象 → 不合格 | 93a99848-674d-a79d-f89f-3a1d8f5ec2de |
| 8 | 步骤 40 | 通知：审核拒绝 | `review_rejected__c` | — | 5ba8892a-ed36-0aa2-e681-3a1d8f49901d |
| 9 | 动作 | 动作节点：修改当前对象状态为【合格】 | `modify_status_qualified__c` | 修改生命周期状态：本对象 → 合格 | 18f28758-797d-11ef-0882-3a1d8f5f7e53 |
| 10 | 步骤 40 | 通知：审核通过 | `approved__c` | — | 5ba8892a-ed36-0aa2-e681-3a1d8f49901d |
| 11 | 结束 | 结束 | `material_evaluation_result_review_End__c` | — | — |

**流程阅读**

- 1. [开始] 开始；下一步 aea64fd4-7fae-3542-f099-3a1d8f4b8a22。
- 2. [动作] 动作节点：修改当前对象状态为【物料评估结果审核】：修改生命周期状态：本对象 → 物料评估结果审核；下一步 c6432ff8-35fe-f3d1-b486-3a1d8f4cc624。
- 3. [任务] 任务：物料评估结果审核：任务；需要审批；审批按钮：同意/拒绝；下一步 0e19ba97-0ebb-e668-b34a-3a1d8f5af4a9。
- 4. [判断] 判断：物料评估结果审核结果：规则1 → 3a08b6e1-17c0-d8b1-c831-3a1d8f606821；否则 → 7a047798-aaa3-2e1c-24d3-3a1d8f5c500a。
- 5. [任务] 任务：物料评估结果批准：任务；需要审批；审批按钮：同意/拒绝；下一步 25fd69f9-74a9-ed1a-38b9-3a1d8f627247。
- 6. [判断] 判断：物料评估结果批准结果：规则1 → b970e704-21fd-d675-176a-3a1d8f5de043；否则 → 7a047798-aaa3-2e1c-24d3-3a1d8f5c500a。
- 7. [动作] 动作节点：修改当前对象状态为【不合格】：修改生命周期状态：本对象 → 不合格；下一步 93a99848-674d-a79d-f89f-3a1d8f5ec2de。
- 8. [步骤 40] 通知：审核拒绝；下一步 5ba8892a-ed36-0aa2-e681-3a1d8f49901d。
- 9. [动作] 动作节点：修改当前对象状态为【合格】：修改生命周期状态：本对象 → 合格；下一步 18f28758-797d-11ef-0882-3a1d8f5f7e53。
- 10. [步骤 40] 通知：审核通过；下一步 5ba8892a-ed36-0aa2-e681-3a1d8f49901d。
- 11. [结束] 结束；下一步 结束。

**对生命周期的影响**

- 「动作节点：修改当前对象状态为【物料评估结果审核】」→ 「物料评估结果审核」
- 「动作节点：修改当前对象状态为【不合格】」→ 「不合格」
- 「动作节点：修改当前对象状态为【合格】」→ 「合格」

### 3. 物料评估结果汇总提醒

- workflowBasicId：`3a1d93f7-079d-750e-77ed-0a046cd02930`
- workflowConfigId：`57c6069b-be26-0679-0a99-3a1d99adeb51`
- code：`waiting_evaluation_completion_reminder__c`；status：20；版本：1；isAllowTrigger：是
- 触发：物料评估结果汇总 / 发送提醒代办（进入动作）

| 序号 | 类型 | 名称 | code | 配置摘要 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| 1 | 开始 | 开始 | `waiting_evaluation_completion_reminder_Start__c` | — | 66f2f734-571e-2f27-9d4a-3a1d99adeb55 |
| 2 | 任务 | 任务：提醒待办 | `deviation_application_review__c` | 通知任务 | 7710a199-4dda-0f14-bdd1-3a1d99adeb55 |
| 3 | 结束 | 结束 | `waiting_evaluation_completion_reminder_End__c` | — | — |

**流程阅读**

- 1. [开始] 开始；下一步 66f2f734-571e-2f27-9d4a-3a1d99adeb55。
- 2. [任务] 任务：提醒待办：通知任务；下一步 7710a199-4dda-0f14-bdd1-3a1d99adeb55。
- 3. [结束] 结束；下一步 结束。

**对生命周期的影响**

- 没有直接改本对象状态的 behaviorType=9 动作。
- 该流程只做提醒/审批/读取，不改任何生命周期状态。

### 4. 供应物料淘汰审核

- workflowBasicId：`3a1d939e-d43d-7cbd-1e7e-6cf9306ad659`
- workflowConfigId：`76b8344b-8d75-0af2-4dbd-3a1dd56fc3cd`
- code：`supply_material_obsolescence_review__c`；status：20；版本：1；isAllowTrigger：否
- 触发：合格 / 淘汰（用户动作）
- 生命周期状态绑定：合格（`e72d30ce-7bb0-1884-ccb6-3a1cf30a5e30`）

| 序号 | 类型 | 名称 | code | 配置摘要 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| 1 | 开始 | 开始 | `material_evaluation_result_review_Start__c` | — | 11702c08-6e38-aae0-5e88-3a1dd56fc3d1 |
| 2 | 动作 | 动作节点：修改当前对象状态为【物料淘汰审核】 | `change_status_review__c` | 修改生命周期状态：本对象 → 供应物料淘汰审核 | dd0dba80-bb6f-cfc3-5d1e-3a1dd56fc3d1 |
| 3 | 任务 | 任务：物料评估结果审核 | `review__c` | 任务；需要审批；审批按钮：同意/拒绝 | c8c15707-744a-53de-2e34-3a1dd56fc3d1 |
| 4 | 判断 | 判断：物料评估结果审核结果 | `determine_review_result__c` | 规则 1 条；有否则分支 | — |
| 5 | 任务 | 任务：物料评估结果批准 | `approve__c` | 任务；需要审批；审批按钮：同意/拒绝 | f0d7bf9b-a4ee-0186-e9a2-3a1dd56fc3d1 |
| 6 | 判断 | 判断：物料评估结果批准结果 | `approval_result__c` | 规则 1 条；有否则分支 | — |
| 7 | 动作 | 动作节点：修改当前对象状态为【合格】 | `modify_status_unqualified__c` | 修改生命周期状态：本对象 → 合格 | 6603e800-f30f-68dc-c552-3a1dd56fc3d1 |
| 8 | 步骤 40 | 通知：审核拒绝 | `review_rejected__c` | — | 8ca56067-7900-ff6a-68f0-3a1dd56fc3d2 |
| 9 | 动作 | 动作节点：修改当前对象状态为【已淘汰】 | `modify_status_qualified__c` | 修改生命周期状态：本对象 → 已淘汰 | d0c66030-788d-1cfd-34e9-3a1dd56fc3d2 |
| 10 | 步骤 40 | 通知：审核通过 | `approved__c` | — | 8ca56067-7900-ff6a-68f0-3a1dd56fc3d2 |
| 11 | 结束 | 结束 | `material_evaluation_result_review_End__c` | — | — |

**流程阅读**

- 1. [开始] 开始；下一步 11702c08-6e38-aae0-5e88-3a1dd56fc3d1。
- 2. [动作] 动作节点：修改当前对象状态为【物料淘汰审核】：修改生命周期状态：本对象 → 供应物料淘汰审核；下一步 dd0dba80-bb6f-cfc3-5d1e-3a1dd56fc3d1。
- 3. [任务] 任务：物料评估结果审核：任务；需要审批；审批按钮：同意/拒绝；下一步 c8c15707-744a-53de-2e34-3a1dd56fc3d1。
- 4. [判断] 判断：物料评估结果审核结果：规则1 → 0effd05e-a57e-127b-de93-3a1dd56fc3d1；否则 → eb73d663-98a3-423b-9a73-3a1dd56fc3d1。
- 5. [任务] 任务：物料评估结果批准：任务；需要审批；审批按钮：同意/拒绝；下一步 f0d7bf9b-a4ee-0186-e9a2-3a1dd56fc3d1。
- 6. [判断] 判断：物料评估结果批准结果：规则1 → 3013b608-2ae5-178d-358e-3a1dd56fc3d2；否则 → eb73d663-98a3-423b-9a73-3a1dd56fc3d1。
- 7. [动作] 动作节点：修改当前对象状态为【合格】：修改生命周期状态：本对象 → 合格；下一步 6603e800-f30f-68dc-c552-3a1dd56fc3d1。
- 8. [步骤 40] 通知：审核拒绝；下一步 8ca56067-7900-ff6a-68f0-3a1dd56fc3d2。
- 9. [动作] 动作节点：修改当前对象状态为【已淘汰】：修改生命周期状态：本对象 → 已淘汰；下一步 d0c66030-788d-1cfd-34e9-3a1dd56fc3d2。
- 10. [步骤 40] 通知：审核通过；下一步 8ca56067-7900-ff6a-68f0-3a1dd56fc3d2。
- 11. [结束] 结束；下一步 结束。

**对生命周期的影响**

- 「动作节点：修改当前对象状态为【物料淘汰审核】」→ 「供应物料淘汰审核」
- 「动作节点：修改当前对象状态为【合格】」→ 「合格」
- 「动作节点：修改当前对象状态为【已淘汰】」→ 「已淘汰」

### 5. 【审核】物料准入申请

- workflowBasicId：`3a1e3d0a-b7a7-07e1-a11e-ca3928cf6324`
- workflowConfigId：`3a1e3d0a-b7a9-957d-bcfc-404fb8c7c325`
- code：`material_access_application_approval__c`；status：20；版本：1；isAllowTrigger：否
- 触发：状态绑定：开启（未反查到直接动作）
- 生命周期状态绑定：开启（`e7cbf75f-691d-f14f-63a8-3a1cf30a5e29`）

| 序号 | 类型 | 名称 | code | 配置摘要 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| 1 | 开始 | 开始 | `material_access_application_approval_Start__c` | — | d8f9ee43-e002-d3d6-2401-3a1e3d24f1b1 |
| 2 | 动作 | 动作：修改状态为审核中 | `change_status_to_under_review__c` | 修改生命周期状态：本对象 → 审核中 | 3c55b4db-02f2-d05c-5d79-3a1e3d3edfb3 |
| 3 | 任务 | 任务：物料准入申请审核 | `material_approval_request_review__c` | 任务；需要审批；审批按钮：同意/拒绝 | f4e6c2bc-b3e0-97d8-0fa5-3a1e3d48ab86 |
| 4 | 判断 | 判断：审核人意见 | `reviewer_Comments__c` | 规则 1 条；有否则分支 | — |
| 5 | 任务 | 任务：物料准入申请批准 | `material_approval_request__c` | 任务；需要审批；审批按钮：同意/拒绝 | 4a8a20c7-3eaa-3f9b-e6db-3a1e3d49127b |
| 6 | 判断 | 判断：批准人意见 | `approvers_comments__c` | 规则 1 条；有否则分支 | — |
| 7 | 动作 | 动作：修改状态为开启 | `change_status_to_open__c` | 修改生命周期状态：本对象 → 开启 | 0cfa13e1-1bf7-9e0a-e392-3a1e3d4a5de5 |
| 8 | 任务 | 审核退回 | `return__c` | 通知任务 | 43dc11e1-2f97-1db2-e008-3a1e3d0ab7b4 |
| 9 | 动作 | 动作：修改状态为等待评估完成 | `change_status_to_pending_evaluation__c` | 修改生命周期状态：本对象 → 等待评估完成 | 43dc11e1-2f97-1db2-e008-3a1e3d0ab7b4 |
| 10 | 结束 | 结束 | `material_access_application_approval_End__c` | — | — |

**流程阅读**

- 1. [开始] 开始；下一步 d8f9ee43-e002-d3d6-2401-3a1e3d24f1b1。
- 2. [动作] 动作：修改状态为审核中：修改生命周期状态：本对象 → 审核中；下一步 3c55b4db-02f2-d05c-5d79-3a1e3d3edfb3。
- 3. [任务] 任务：物料准入申请审核：任务；需要审批；审批按钮：同意/拒绝；下一步 f4e6c2bc-b3e0-97d8-0fa5-3a1e3d48ab86。
- 4. [判断] 判断：审核人意见：规则1 → d5d85ca9-1fb5-7452-e84b-3a1e3d46e68d；否则 → 362221ab-4c0a-3ff6-5d4a-3a1e3d25478d。
- 5. [任务] 任务：物料准入申请批准：任务；需要审批；审批按钮：同意/拒绝；下一步 4a8a20c7-3eaa-3f9b-e6db-3a1e3d49127b。
- 6. [判断] 判断：批准人意见：规则1 → f2d98aac-0e14-e3ed-78f2-3a1e3d25cc34；否则 → 362221ab-4c0a-3ff6-5d4a-3a1e3d25478d。
- 7. [动作] 动作：修改状态为开启：修改生命周期状态：本对象 → 开启；下一步 0cfa13e1-1bf7-9e0a-e392-3a1e3d4a5de5。
- 8. [任务] 审核退回：通知任务；下一步 43dc11e1-2f97-1db2-e008-3a1e3d0ab7b4。
- 9. [动作] 动作：修改状态为等待评估完成：修改生命周期状态：本对象 → 等待评估完成；下一步 43dc11e1-2f97-1db2-e008-3a1e3d0ab7b4。
- 10. [结束] 结束；下一步 结束。

**对生命周期的影响**

- 「动作：修改状态为审核中」→ 「审核中」
- 「动作：修改状态为开启」→ 「开启」
- 「动作：修改状态为等待评估完成」→ 「等待评估完成」

**异常与待确认**

- 状态动作没有直接引用这条工作流；按工作流上的生命周期状态绑定展示，实际触发点可能是状态事件。

## 自动观察与风险

- 8 个状态没有用户动作：开启（init_status__c）、审核中（material_evaluation_plan_review__c）、物料评估结果审核（material_evaluation_result_review__c）、不合格（unqualified__c）、供应物料淘汰审核（supply_material_obsolescence_review__c）、已淘汰（obsolete__c）、终止审核（terminate_review__c）、已终止（terminated__c）。这些状态需要工作流状态步骤或自动动作接管；否则会缺少人工出口。
- 5 个状态既没有用户动作也没有进入动作：物料评估结果审核、供应物料淘汰审核、已淘汰、终止审核、已终止。它们完全依赖外部工作流/事件驱动。
- 行为类型映射不全：行为 57、行为 34。页面/文档暂时只能显示数字或泛化标签，建议补映射表。
- 有 10 个进入动作会修改相关对象的生命周期状态（behaviorType 46），改本对象前要一起检查关联对象。
- 3 条工作流里存在 behaviorType=9 的本对象状态变更：物料评估结果审核、供应物料淘汰审核、【审核】物料准入申请。文档的工作流章节逐条列出了动作节点；drawio 每个工作流单独一页。
- 生命周期进入条件为 0 条且可读：属于“未配置”，不是读取失败。

## 生成说明

- 本文档由 akso-workbench 对象分析页生成，数据来自 eGMP 配置接口。
- 状态顺序按 `status.sort` 排列；生命周期总览页的状态链也按 sort 排列，不是平台声明的迁移顺序。
- 工作流章节来自 `GetWorkflowSteps` + `GetWorkflowStepWithDetails` 的真实步骤；behaviorType=46 只画成关联对象影响，不画成本对象生命周期边。
- drawio 文件包含“生命周期总览”页和每条工作流一页，建议用 diagrams.net / drawio 打开。

## 字段表

共 35 个字段：启用 35 个，停用 0 个；必填 2 个，自定义 19 个。

| # | 字段名 | code | 类型 | 必填 | 唯一 | 来源 | 引用 / 选项集 | 状态 |
| ---: | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Id | `global_id__sys` | ID | — | — | 系统 | — | 启用 |
| 2 | 外部Id | `external_id__sys` | ID | — | — | 系统 | — | 启用 |
| 3 | 业务编号 | `name__ak` | 文本 | — | — | 预置 | — | 启用 |
| 4 | 版本号 | `version__sys` | 文本 | — | — | 系统 | — | 启用 |
| 5 | 创建人 | `created_by__sys` | 对象 | — | — | 系统 | → 用户（`plat_org_user__ak`） | 启用 |
| 6 | 创建时间 | `created_time__sys` | 日期时间 | — | — | 系统 | — | 启用 |
| 7 | 更新人 | `modified_by__sys` | 对象 | — | — | 系统 | → 用户（`plat_org_user__ak`） | 启用 |
| 8 | 更新时间 | `modified_time__sys` | 日期时间 | — | — | 系统 | — | 启用 |
| 9 | 是否删除 | `is_deleted__sys` | 布尔 | — | — | 系统 | — | 启用 |
| 10 | 所属对象 | `object_id__sys` | 组件 | — | — | 系统 | — | 启用 |
| 11 | 对象类型 | `object_type_id__ak` | 组件 | — | — | 预置 | — | 启用 |
| 12 | 记录状态 | `state__ak` | 单选 | — | — | 预置 | 选项集 `99999999-0000-0000-0001-000000000001` | 启用 |
| 13 | 状态 | `status_id__sys` | 组件 | — | — | 系统 | — | 启用 |
| 14 | 供应商准入 | `supplier_access__c` | 父对象 | 是 | — | 自定义 | → 供应商准入（`supplier_admission__c`） | 启用 |
| 15 | 供应商 | `supplier__c` | 对象 | — | — | 自定义 | → 供应商（`supplier__c`） | 启用 |
| 16 | 审计周期（年） | `audit_cycle__c` | 数字 | — | — | 自定义 | — | 启用 |
| 17 | 供应物料 | `supplied_materials__c` | 父对象 | 是 | — | 自定义 | → 产品/物料/中间产品/设备设施/公用系统（`product__c`） | 启用 |
| 18 | 供应商名称 | `supplier_name__c` | 文本 | — | — | 自定义 | — | 启用 |
| 19 | 物料代码 | `material_code__c` | 文本 | — | — | 自定义 | — | 启用 |
| 20 | 物料分类 | `material_classification__c` | 单选 | — | — | 自定义 | 选项集 `06562721-9edf-7f7f-a54b-3a1d898d9fef` | 启用 |
| 21 | 所属质量组织 | `own_company__ak` | 对象 | — | — | 预置 | → 质量组织（`plat_org_company__ak`） | 启用 |
| 22 | 供应物料负责人 | `responsible_person_supplied_materials__c` | 对象 | — | — | 自定义 | → 用户（`plat_org_user__ak`） | 启用 |
| 23 | 物料风险级别 | `material_risk_level__c` | 单选 | — | — | 自定义 | 选项集 `db6415fe-2898-0922-3e12-3a1d8e844ca0` | 启用 |
| 24 | 淘汰原因 | `elimination_reason__c` | 文本 | — | — | 自定义 | — | 启用 |
| 25 | 淘汰时间 | `elimination_time__c` | 日期 | — | — | 自定义 | — | 启用 |
| 26 | 是否存在活跃流程 | `exist_active_flow__sys` | 布尔 | — | — | 系统 | — | 启用 |
| 27 | 物料影响评估数量 | `material_impact_assessment_quantity__c` | 统计 | — | — | 自定义 | — | 启用 |
| 28 | 物料质量评估数量 | `material_quality_assessment_quantity__c` | 统计 | — | — | 自定义 | — | 启用 |
| 29 | 生产地址数量 | `production_addresses_number__c` | 统计 | — | — | 自定义 | — | 启用 |
| 30 | 资质数量 | `qualification_number__c` | 统计 | — | — | 自定义 | — | 启用 |
| 31 | 供应商类型 | `type__c` | 公式 | — | — | 自定义 | 选项集 `d9ae30dc-db42-3249-b84d-3a1d7db47d9c` | 启用 |
| 32 | 下次审计日期 | `next_audit_date__c` | 日期 | — | — | 自定义 | — | 启用 |
| 33 | 生产地址 | `adress__c` | 对象 | — | — | 自定义 | → 供应商生产地址（`supplier_production_address__c`） | 启用 |
| 34 | 生产商 | `manufacture__c` | 对象 | — | — | 自定义 | → 供应商（`supplier__c`） | 启用 |
| 35 | Dac成员 | `dac_member__sys` | 类型 24 | — | — | 系统 | — | 启用 |
