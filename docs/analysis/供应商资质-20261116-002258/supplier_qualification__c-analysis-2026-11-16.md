# 供应商资质（supplier_qualification__c）对象分析

> 数据环境：`https://tulmed-config.aksoegmp.com`
> 数据账号：`lyl`
> 生成时间：2026-11-15T16:22:58.117Z
> 数据来源：eGMP 配置接口，只读分析，不修改平台。

## 对象概览

| 项 | 值 |
| --- | --- |
| 对象名称 | 供应商资质 |
| 对象 code | `supplier_qualification__c` |
| 对象 id | `3a1cf307-81b2-8bfe-4353-28fb99fb74ab` |
| 生命周期 | 供应商资质生命周期 |
| lifecycleId | `3a1cf307-81b2-031a-97d7-f5ff46154e7c` |
| 启用生命周期 | 是 |
| 状态 | 6 启用 / 7 总数 |
| 用户动作 | 1 |
| 进入动作 | 1 |
| 字段 | 28 |
| 工作流 | 2 |
| 进入条件 | 未配置（0 条） |

## 状态与动作

### 1. 开启（`init_status__c`）

- 状态 id：`ab4abf82-bf09-3673-0669-3a1cf307aa88`
- sort：0；启用：是
- 用户动作：0；进入动作：0

**用户动作**

- 无

**进入动作**

- 无

### 2. 资质审核（`qualification_review__c`）

- 状态 id：`1d17c50c-7353-9b3b-0f41-3a1d849e620c`
- sort：1；启用：是
- 用户动作：0；进入动作：1

**用户动作**

- 无

**进入动作**

- **生成业务编号**（`e8977e4ef063233245f133a1dffb3c942`）：生成编号

### 3. 生效（`complete_status__c`）

- 状态 id：`0720159d-df5a-817c-1703-3a1cf307aa8f`
- sort：2；启用：是
- 用户动作：1；进入动作：0

**用户动作**

- **停用资质**（`disable_qualification__c`）：发起工作流；工作流「供应商资质停用审核」
  - workflowId：`3a1d7edb-bd6e-feb2-7d98-6edea2555b71`

**进入动作**

- 无

### 4. 资质更新审核（`qualification_renewal_review__c`）

- 状态 id：`52251e42-c354-e35d-4100-3a1d7df9b26d`
- sort：3；启用：否
- 用户动作：0；进入动作：0

**用户动作**

- 无

**进入动作**

- 无

### 5. 资质停用审核（`qualification_inactive_review__c`）

- 状态 id：`94825227-867e-19ed-f196-3a1d7e04f3b8`
- sort：4；启用：是
- 用户动作：0；进入动作：0

**用户动作**

- 无

**进入动作**

- 无

### 6. 停用（`inactive_status__c`）

- 状态 id：`1bdbf8f1-bbb4-b9df-0b12-3a1d7e05964d`
- sort：5；启用：是
- 用户动作：0；进入动作：0

**用户动作**

- 无

**进入动作**

- 无

### 7. 失效（`expired__c`）

- 状态 id：`43a87987-ae69-25ee-cf4c-3a1f1ad88afc`
- sort：6；启用：是
- 用户动作：0；进入动作：0

**用户动作**

- 无

**进入动作**

- 无

## 工作流解析

| # | 工作流 | code | status | 版本 | 允许自动启动 | 直接触发 | 步骤 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 供应商资质停用审核 | `supplier_qualification_deactivation_review__c` | 20 | 1 | 否 | 生效 / 停用资质（用户动作） | 9 |
| 2 | 供应商资质更新审核 | `supplier_qualification_update_review__c` | 20 | 1 | 否 | 未反查到直接触发点（按对象工作流保留） | 9 |

### 1. 供应商资质停用审核

- workflowBasicId：`3a1d7edb-bd6e-feb2-7d98-6edea2555b71`
- workflowConfigId：`43ba33eb-d969-8a30-a8b1-3a1d7eeff119`
- code：`supplier_qualification_deactivation_review__c`；status：20；版本：1；isAllowTrigger：否
- 触发：生效 / 停用资质（用户动作）

| 序号 | 类型 | 名称 | code | 配置摘要 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| 1 | 开始 | 开始 | `supplier_qualification_deactivation_review_Start__c` | — | e411d983-2b05-1961-aa07-3a1d7eeff11d |
| 2 | 动作 | 修改状态：资质停用审核 | `change_qualification_inactive_review__c` | 修改生命周期状态：本对象 → 资质停用审核 | 1089463a-cd53-0a66-bd6f-3a1d7eeff11d |
| 3 | 任务 | 任务：资质停用审核 | `task_information_review__c` | 任务；需要审批；审批按钮：同意/拒绝 | c10a4f25-4cbb-f8ea-e7c0-3a1d7eeff11d |
| 4 | 判断 | 判断：资质停用审核 | `judgment_reviewer__c` | 规则 1 条；有否则分支 | — |
| 5 | 动作 | 修改状态：生效 | `change_complete_status__c` | 修改生命周期状态：本对象 → 生效 | cb495983-c542-5060-dd4e-3a1d7eeff11d |
| 6 | 步骤 40 | 通知：供应商资质停用审核拒绝 | `notification_declined__c` | — | 9d4696f3-50d9-381c-46e1-3a1d7eeff11d |
| 7 | 动作 | 修改状态：停用 | `change_inactive_status__c` | 修改生命周期状态：本对象 → 停用 | a05afe15-623c-4bd8-709c-3a1d7eeff11d |
| 8 | 步骤 40 | 通知：供应商资质停用审核通过 | `notification_agree__c` | — | 9d4696f3-50d9-381c-46e1-3a1d7eeff11d |
| 9 | 结束 | 结束 | `supplier_qualification_deactivation_review_End__c` | — | — |

**流程阅读**

- 1. [开始] 开始；下一步 e411d983-2b05-1961-aa07-3a1d7eeff11d。
- 2. [动作] 修改状态：资质停用审核：修改生命周期状态：本对象 → 资质停用审核；下一步 1089463a-cd53-0a66-bd6f-3a1d7eeff11d。
- 3. [任务] 任务：资质停用审核：任务；需要审批；审批按钮：同意/拒绝；下一步 c10a4f25-4cbb-f8ea-e7c0-3a1d7eeff11d。
- 4. [判断] 判断：资质停用审核：规则1 → cf6fe0a0-d773-2da0-ed05-3a1d7eeff11d；否则 → 85e50af8-9e58-913c-88fa-3a1d7eeff11d。
- 5. [动作] 修改状态：生效：修改生命周期状态：本对象 → 生效；下一步 cb495983-c542-5060-dd4e-3a1d7eeff11d。
- 6. [步骤 40] 通知：供应商资质停用审核拒绝；下一步 9d4696f3-50d9-381c-46e1-3a1d7eeff11d。
- 7. [动作] 修改状态：停用：修改生命周期状态：本对象 → 停用；下一步 a05afe15-623c-4bd8-709c-3a1d7eeff11d。
- 8. [步骤 40] 通知：供应商资质停用审核通过；下一步 9d4696f3-50d9-381c-46e1-3a1d7eeff11d。
- 9. [结束] 结束；下一步 结束。

**对生命周期的影响**

- 「修改状态：资质停用审核」→ 「资质停用审核」
- 「修改状态：生效」→ 「生效」
- 「修改状态：停用」→ 「停用」

### 2. 供应商资质更新审核

- workflowBasicId：`3a1d7eb4-6330-e3a3-9e65-3a1118a48840`
- workflowConfigId：`c12656d4-0e33-54ef-1311-3a1d7ef08546`
- code：`supplier_qualification_update_review__c`；status：20；版本：1；isAllowTrigger：否
- 触发：未反查到直接触发点（按对象工作流保留）

| 序号 | 类型 | 名称 | code | 配置摘要 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| 1 | 开始 | 开始 | `supplier_qualification_update_review_Start__c` | — | 6f076f2e-c5cb-cd16-29d8-3a1d7ef0854a |
| 2 | 动作 | 修改状态：资质更新审核 | `change_qualification_renewal_review__c` | 修改生命周期状态：本对象 → 资质更新审核 | 02555b74-92b4-f5b1-65a9-3a1d7ef0854a |
| 3 | 任务 | 任务：资质更新审核 | `task_information_review__c` | 任务；需要审批；审批按钮：同意/拒绝 | e31ee69a-bf2b-3a06-a4cf-3a1d7ef0854a |
| 4 | 判断 | 判断：资质更新审核 | `judgment_reviewer__c` | 规则 1 条；有否则分支 | — |
| 5 | 动作 | 审核拒绝-修改状态：生效 | `change_complete_status_declined__c` | 修改生命周期状态：本对象 → 生效 | 08e056a4-fd8a-88e1-3fab-3a1d7ef0854a |
| 6 | 步骤 40 | 通知：供应商资质更新审核拒绝 | `notification_declined__c` | — | e187b39a-d9fb-f65b-f23a-3a1d7ef0854a |
| 7 | 动作 | 审核通过-修改状态：生效 并更新字段值 | `change_complete_status__c` | 修改记录字段（多字段）；修改生命周期状态：本对象 → 生效 | 6a9e3467-3050-9aa5-0b4a-3a1d7ef0854a |
| 8 | 步骤 40 | 通知：供应商资质更新审核通过 | `notification_agree__c` | — | e187b39a-d9fb-f65b-f23a-3a1d7ef0854a |
| 9 | 结束 | 结束 | `supplier_qualification_update_review_End__c` | — | — |

**流程阅读**

- 1. [开始] 开始；下一步 6f076f2e-c5cb-cd16-29d8-3a1d7ef0854a。
- 2. [动作] 修改状态：资质更新审核：修改生命周期状态：本对象 → 资质更新审核；下一步 02555b74-92b4-f5b1-65a9-3a1d7ef0854a。
- 3. [任务] 任务：资质更新审核：任务；需要审批；审批按钮：同意/拒绝；下一步 e31ee69a-bf2b-3a06-a4cf-3a1d7ef0854a。
- 4. [判断] 判断：资质更新审核：规则1 → 000aa0e8-43c6-52ac-20cf-3a1d7ef0854a；否则 → 398b3821-f248-092b-f401-3a1d7ef0854a。
- 5. [动作] 审核拒绝-修改状态：生效：修改生命周期状态：本对象 → 生效；下一步 08e056a4-fd8a-88e1-3fab-3a1d7ef0854a。
- 6. [步骤 40] 通知：供应商资质更新审核拒绝；下一步 e187b39a-d9fb-f65b-f23a-3a1d7ef0854a。
- 7. [动作] 审核通过-修改状态：生效 并更新字段值：修改记录字段（多字段）；修改生命周期状态：本对象 → 生效；下一步 6a9e3467-3050-9aa5-0b4a-3a1d7ef0854a。
- 8. [步骤 40] 通知：供应商资质更新审核通过；下一步 e187b39a-d9fb-f65b-f23a-3a1d7ef0854a。
- 9. [结束] 结束；下一步 结束。

**对生命周期的影响**

- 「修改状态：资质更新审核」→ 「资质更新审核」
- 「审核拒绝-修改状态：生效」→ 「生效」
- 「审核通过-修改状态：生效 并更新字段值」→ 「生效」

**异常与待确认**

- 没有状态动作直接引用这条工作流，也没有生命周期状态绑定；可能是对象级手动/事件流程。

## 自动观察与风险

- 6 个状态没有用户动作：开启（init_status__c）、资质审核（qualification_review__c）、资质更新审核（qualification_renewal_review__c）、资质停用审核（qualification_inactive_review__c）、停用（inactive_status__c）、失效（expired__c）。这些状态需要工作流状态步骤或自动动作接管；否则会缺少人工出口。
- 5 个状态既没有用户动作也没有进入动作：开启、资质更新审核、资质停用审核、停用、失效。它们完全依赖外部工作流/事件驱动。
- 2 条工作流里存在 behaviorType=9 的本对象状态变更：供应商资质停用审核、供应商资质更新审核。文档的工作流章节逐条列出了动作节点；drawio 每个工作流单独一页。
- 1 条工作流既没有状态动作直接引用，也没有 lifecycleStatusIds 绑定：供应商资质更新审核。触发点需要平台侧确认，文档按“对象工作流”保留。
- 生命周期进入条件为 0 条且可读：属于“未配置”，不是读取失败。

## 生成说明

- 本文档由 akso-workbench 对象分析页生成，数据来自 eGMP 配置接口。
- 状态顺序按 `status.sort` 排列；生命周期总览页的状态链也按 sort 排列，不是平台声明的迁移顺序。
- 工作流章节来自 `GetWorkflowSteps` + `GetWorkflowStepWithDetails` 的真实步骤；behaviorType=46 只画成关联对象影响，不画成本对象生命周期边。
- drawio 文件包含“生命周期总览”页和每条工作流一页，建议用 diagrams.net / drawio 打开。

## 字段表

共 28 个字段：启用 27 个，停用 1 个；必填 2 个，自定义 12 个。

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
| 14 | 所属质量组织 | `own_company__ak` | 对象 | — | — | 预置 | → 质量组织（`plat_org_company__ak`） | 启用 |
| 15 | 供应商 | `supplier__c` | 对象 | — | — | 自定义 | → 供应商（`supplier__c`） | 启用 |
| 16 | 供应商准入 | `supplier_admission__c` | 父对象 | 是 | — | 自定义 | → 供应商准入（`supplier_admission__c`） | 启用 |
| 17 | 资质名称 | `supplier_qualification__c` | 父对象 | 是 | — | 自定义 | → 供应商资质矩阵（`supplier_qualification_matrix__c`） | 启用 |
| 18 | 供应商类型（停用） | `supplier_type__c` | 单选 | — | — | 自定义 | 选项集 `6da1956c-a2bc-f999-45cd-3a1d6e2afb59` | 停用 |
| 19 | 是否必须资质 | `is_mandatory_qualification__c` | 布尔 | — | — | 自定义 | — | 启用 |
| 20 | 是否长期有效 | `is_long_term_valid__c` | 布尔 | — | — | 自定义 | — | 启用 |
| 21 | 到期日期 | `expiration_date__c` | 日期 | — | — | 自定义 | — | 启用 |
| 22 | 资质附件 | `qualification_attachment__c` | 附件 | — | — | 自定义 | — | 启用 |
| 23 | 新资质是否长期有效 | `is_new_qualification_long_term__c` | 布尔 | — | — | 自定义 | — | 启用 |
| 24 | 新资质到期日期 | `new_qualification_expiry__c` | 日期 | — | — | 自定义 | — | 启用 |
| 25 | 停用原因 | `deactivation_reason__c` | 文本 | — | — | 自定义 | — | 启用 |
| 26 | 供应商类型 | `suppliers_type__c` | 单选 | — | — | 自定义 | 选项集 `d9ae30dc-db42-3249-b84d-3a1d7db47d9c` | 启用 |
| 27 | 是否存在活跃流程 | `exist_active_flow__sys` | 布尔 | — | — | 系统 | — | 启用 |
| 28 | Dac成员 | `dac_member__sys` | 类型 24 | — | — | 系统 | — | 启用 |
