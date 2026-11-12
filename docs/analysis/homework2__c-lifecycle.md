# 作业提交2.0（homework2__c）生命周期分析

> 对象：**作业提交2.0**（`homework2__c`）  
> 对象 ID：`7bb8b0ef-57ad-13f9-0ede-73dfb2d9be9b`  
> 生命周期：**作业提交2.0生命周期**（`homework2_lifecycle__c`）  
> 生命周期 ID：`70e6973c-c1e1-bed3-3d41-4fb2b75e093d`  
> 环境：`https://standard-val.aksoegmp.com`  
> 读取时间：2026-11-12  
> 数据口径：只读；只统计 `isEnabled=true` 的状态动作，停用状态单列说明。

## 1. 概览

| 项目 | 数量 / 内容 |
|---|---|
| 生命周期状态总数 | **6 个** |
| 启用状态 | **4 个**：待提交、待审核、已通过、已退回 |
| 停用状态 | **2 个**：待提交（停用）、已通过（停用） |
| 动作总数 | **3 个**（用户动作 2 个，进入动作 1 个） |
| 有动作的状态 | 2 个：待提交、已退回 |
| 无动作的状态 | 2 个：待审核、已通过 |
| 生命周期进入条件 | **未配置**（`StatusEnterRule/GetList` 返回空数组） |
| 关联工作流 | 2 个：审批流 `aa__c`、提醒流 `df__c` |

## 2. 生命周期状态

| sort | 状态名称 | 状态 code | 是否启用 | 进入动作 | 用户动作 | 说明 |
|---:|---|---|---|---:|---:|---|
| 0 | 待提交 | `init_status__c` | 是 | 0 | 1 | 流程起点；可点“提交” |
| 1 | 待审核 | `status_pending__c` | 是 | 0 | 0 | 等待审核，无状态动作 |
| 2 | 已通过 | `complete_status__c` | 是 | 0 | 0 | 流程终点；无状态动作 |
| 3 | 已退回 | `status_rejected__c` | 是 | 1 | 1 | 可点“重新提交”；进入时自动发起提醒流 `df__c`（请修改作业后重新提交） |
| 4 | 待提交（停用） | `status_draft__c` | 否 | — | — | 停用，不进入状态图；动作未统计 |
| 5 | 已通过（停用） | `status_approved__c` | 否 | — | — | 停用，不进入状态图；动作未统计 |

> 状态总数是 6；日常展示和状态图只使用 4 个启用状态。

## 3. 启用状态的动作明细

### 3.1 待提交（`init_status__c`）

| 类型 | 动作名称 | 动作 code | 触发效果 | 动作含义 |
|---|---|---|---|---|
| 用户动作 | 提交 | `cc__c` | 启动工作流《作业提交-标准审核工作流》（`behaviorType: 7`） | 用户在“待提交”状态主动发起审核。它本身只负责启动工作流，后续是“审核通过”还是“退回”由工作流内部节点决定，不是直接改状态。 |

### 3.2 待审核（`status_pending__c`）

| 类型 | 动作名称 | 说明 |
|---|---|---|
| — | 无 | 该状态没有进入动作，也没有用户动作。进入“待审核”不会自动执行动作，界面上也没有可点的业务按钮。 |

### 3.3 已通过（`complete_status__c`）

| 类型 | 动作名称 | 说明 |
|---|---|---|
| — | 无 | 该状态没有进入动作，也没有用户动作。 |

### 3.4 已退回（`status_rejected__c`）

| 类型 | 动作名称 | 动作 code | 触发效果 | 动作含义 |
|---|---|---|---|---|
| 用户动作 | 重新提交 | `sadf__c` | 启动工作流《作业提交-标准审核工作流》（`behaviorType: 7`） | 记录被退回后，用户可再次发起同一个审核工作流。动作只负责启动流程，不直接决定回到“待审核”还是其他状态。 |
| 进入动作 | 未命名 | `dd__c` | 启动工作流《作业提交》（`behaviorType: 7`，`workflowId: d3cd2559-2f43-d5a0-2063-3a24d1ee2875`） | 记录进入“已退回”时自动发起 `df__c`。该流程是**提醒任务**，不是审批流：给创建人生成一条“请修改作业后重新提交”的提醒。 |

> 这里的 `dd__c` 是通过 `EnterAction/GetById` 才看到的：`GetListByStatusId` 只返回动作壳，`configs` 是空的。只看列表会误判成“未配置动作”。

## 4. 动作类型说明

| 动作类型 | 触发方式 | 本对象中的实例 | 含义 |
|---|---|---|---|
| 用户动作 | 用户在记录界面上点击按钮 | 待提交的“提交”、已退回的“重新提交” | 人工发起动作。`behaviorType: 7` 表示启动工作流；工作流负责后续审批流转。 |
| 进入动作 | 记录进入某个状态时自动执行 | 已退回的未命名进入动作 | 不需要用户点击。`behaviorType: 7` 表示启动工作流；本例启动的是提醒流程 `df__c`。 |
| 工作流触发（7） | 由用户动作或进入动作携带 | 三个动作都绑定了工作流，但分成两条流程 | 只表示“启动这个工作流”，不代表工作流内部的审批节点、通过/退回条件已经读取。 |

> **读法**：`EnterAction/GetListByStatusId` 的 `configs` 不可信（可能为空壳），进入动作的类型必须用 `EnterAction/GetById` 再读一次。`configs[].behaviorType` 才是数字类型，`behaviorValue.workflowId` 才是被启动的 `workflowBasicId`。

## 5. 关联工作流

本对象一共关联两条工作流：一条审批流、一条提醒流。

### 5.1 审批流 `aa__c`（两个用户动作使用）

| 项目 | 值 |
|---|---|
| 工作流名称 | 作业提交-标准审核工作流 |
| 工作流 code | `aa__c` |
| `workflowBasicId` | `ef355b4e-b46a-c0a3-1f6d-3a24d1d916aa` |
| `workflowConfigId` | `7f997f6b-2e5d-b801-6a56-3a24d264d494` |
| 工作流状态 | `20`（已发布） |
| 所属对象 | `homework2__c` |
| 是否允许进入动作/事件动作自动启动 | `false` |

> 待提交的“提交”和已退回的“重新提交”都启动这条审批流。

### 5.2 提醒流 `df__c`（进入动作使用）

| 项目 | 值 |
|---|---|
| 工作流名称 | 作业提交 |
| 工作流 code | `df__c` |
| `workflowBasicId` | `d3cd2559-2f43-d5a0-2063-3a24d1ee2875` |
| `workflowConfigId` | `2d6aac69-4a54-d69c-548e-3a24d1f0d7dd` |
| 工作流状态 | `20`（已发布） |
| 版本 | `versionNo: 3` |
| 所属对象 | `homework2__c` |
| 是否允许进入动作/事件动作自动启动 | `true` |

节点结构（`GetWorkflowSteps`）：

```text
开始（10）
  └─ 任务：请修改作业后重新提交（30）
       └─ 结束（20）
```

任务节点关键配置：

| 字段 | 值 | 说明 |
|---|---|---|
| `isNotifyTaskType` | `true` | 提醒任务，不是审批任务 |
| `isRequireApproval` | `false` | 不要求审批 |
| `stepTaskApprovals` | `[]` | 没有审批按钮 |
| `taskRequireType` | `20` | 提醒任务类型 |
| `dispatchMode` | `10` | 指派方式 |
| `notifyType` | `-10` | 使用提醒策略 |
| `userControlIds` | 开始节点的参与者控件（学生 = `created_by__sys`） | 提醒对象是记录创建人 |
| `isEnable` | `false` | 步骤级启用标记；实际是否生成待办仍建议在页面确认 |

业务含义：

- 记录被退回到“已退回”时，进入动作 `dd__c` 发起 `df__c`。
- `df__c` 给创建人（学生）生成“请修改作业后重新提交”的提醒。
- 它**不是审批流**：没有审批人、没有同意/退回按钮，不改变审批状态。
- 用户看到提醒后，修改作业，再点“重新提交”，走的是审批流 `aa__c`。

## 6. 生命周期进入条件

| 项目 | 结果 |
|---|---|
| 读取接口 | `GET /api/config/lifecycle/StatusEnterRule/GetList?lifecycleId=70e6973c-c1e1-bed3-3d41-4fb2b75e093d` |
| 返回 | HTTP 200，`code: 0`，`data: []` |
| 结论 | 当前生命周期**没有配置进入条件** |
| 未验证项 | 非空进入条件的字段结构尚未取得样例，不能确认条件表达式、目标状态、优先级等字段 |

## 7. 阅读边界

1. **审批流 `aa__c` 的内部节点未读取**：只知道“提交/重新提交”会启动它；`df__c` 已读取步骤，但 `aa__c` 的审批人、判断条件、通过/退回后落到哪个状态仍未读取。因此本文不写“提交后一定到待审核”。
2. **未读取状态流转关系**：当前没有可靠的 `StatusRelation` / `Flowchart` 读取接口，本文只列状态与动作，不画流转边。
3. **停用状态的动作未统计**：`待提交（停用）`、`已通过（停用）` 只用于说明总数，未读取其动作。
4. **列表接口会隐藏 configs**：`EnterAction/GetListByStatusId` 的 `configs` 可能是空数组；本文的进入动作类型来自 `EnterAction/GetById`，不能拿列表结果反推“未配置”。
5. **`isEnable:false` 的步骤是否执行未做运行验证**：`df__c` 的任务节点步骤级 `isEnable=false`，本文只按配置说明它是提醒任务，不声称一定生成待办。
6. **状态动作数量以启用状态为准**：总计 3 个动作（2 个用户动作 + 1 个进入动作）。

## 8. 数据来源

| 用途 | 接口 |
|---|---|
| 对象元数据 | `GET /api/openapi/v1.0/BasicObject/homework2__c` |
| 状态列表（基础） | `GET /api/openapi/v1.0/BasicObject/lifecycleStatus/homework2__c` |
| 状态配置（含 isEnabled / sort） | `GET /api/config/lifecycle/Status/GetList`，按 `lifecycleId` 客户端过滤 |
| 用户动作 | `GET /api/config/lifecycle/UserAction/GetListByStatusId?statusId=<id>` |
| 用户动作详情 | `GET /api/config/lifecycle/UserAction/GetById?id=<id>` |
| 进入动作 | `GET /api/config/lifecycle/EnterAction/GetListByStatusId?statusId=<id>` |
| 进入动作详情（含 configs） | `GET /api/config/lifecycle/EnterAction/GetById?id=<id>` |
| 进入条件 | `GET /api/config/lifecycle/StatusEnterRule/GetList?lifecycleId=<id>` |
| 工作流名称 | `POST /api/platform/Workflow/GetWorkflowBasicPageViewList`，按 `workflowBasicId` 匹配 |
| 工作流详情 | `GET /api/platform/Workflow/GetWorkflowBasic?id=<workflowConfigId>` |
| 工作流步骤 | `GET /api/platform/Workflow/GetWorkflowSteps?workflowConfigId=<id>` |

> 本文中的“用户动作”“进入动作”均为平台配置数据；动作的实际执行结果仍要以工作流配置和运行记录为准。
