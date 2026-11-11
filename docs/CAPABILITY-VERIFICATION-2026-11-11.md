# 生命周期三能力实测记录（2026-11-11）

> 范围：`lifecycle.useraction.list`、`lifecycle.enteraction.list`、`lifecycle.enteraction.create`。
> 环境：standard-val（`https://standard-val.aksoegmp.com`），账号 `liyulong`。
> 令牌：仍走 akso-cloud 代登录（`POST /api/accounts/:id/egmp-token`），本机不落明文口令。
> 结论先说：三条接口都真实调通；但 `enteraction.list` 的返回结构与目录原描述不一致，已在
> `plugin/src/config/capabilities.ts` 与 `docs/knowledge/基础API使用指南.md` 修正。

## 一、`lifecycle.useraction.list`（读取用户动作）

- 请求：`GET /api/config/lifecycle/UserAction/GetListByStatusId?statusId=3cd4e5a8-81c0-8224-1445-3a175cd8403a`
  - 该状态是 `audit_plan__c` 的「开启」，实际有 1 个用户动作。
- 响应：HTTP 200，`code: 0`，`data` 长度 1。
- 关键字段（节选）：

```json
{
  "lifecycleStatusName": "开启",
  "group": 1,
  "behaviorTypes": [7],
  "name": "提交",
  "code": "Initiate_workflow_audit_plan_review__c",
  "preConfigDto": {
    "description": "发起工作流【审计计划审核】",
    "executeType": 2
  },
  "configs": [
    {
      "behaviorType": 7,
      "behaviorValue": { "workflowId": "<workflowBasicId>" },
      "sort": 0
    }
  ]
}
```

- 空状态（如 `homework__c` 多个状态、`change_control__c` 该状态）返回 `data: []`，HTTP 200、`code: 0`。
- 结论：按状态 id 返回用户动作数组；动作项含 `code`、`name`、`configs`，可用来查重和读改写。

## 二、`lifecycle.enteraction.list`（读取进入动作）

- 请求：`GET /api/config/lifecycle/EnterAction/GetListByStatusId?statusId=8db2fbf2-1d40-7167-6385-3a1a0ea6fc57`
  - 该状态是 `change_control__c` 的「通过变更控制，增加文件」，实际有 1 个进入动作。
- 响应：HTTP 200，`code: 0`，`data` 长度 1。
- 实际元素（完整）：

```json
{
  "id": "b3831f01-fbd7-b15a-5985-3a1a0eaa11cb",
  "sort": 1,
  "belongId": "8db2fbf2-1d40-7167-6385-3a1a0ea6fc57",
  "name": null,
  "isEnabled": true,
  "description": "通过变更创建文件",
  "modifiedTime": "2025-05-23 17:33:07",
  "belongType": 1,
  "executeType": 1
}
```

- **没有 `code`，也没有 `configs`**。原目录写「每项含 id / code / name / configs」是错的，已修正。
- 空状态返回 `data: []`，HTTP 200、`code: 0`。
- 结论：只能按 `id` / `description` / `sort` 做查重与回读；不要在计划里假设能读到 `code` 或 `configs`。

## 三、`lifecycle.enteraction.create`（新建进入动作）

- 请求：`POST /api/config/lifecycle/EnterAction/Create`
- 测试体（在 `audit_plan__c` 的「关闭」状态上建一个无配置、禁用意图的动作）：

```json
{
  "code": "akw_verify_20261111__c",
  "belongId": "eef46a7e-a1a7-1b96-8c73-3a175cd8403a",
  "belongType": 1,
  "actions": [
    {
      "code": "akw_verify_20261111__c",
      "name": "AKW验证-可删除",
      "isEnabled": false,
      "belongType": 1,
      "configs": [],
      "sort": 99,
      "executeType": 1,
      "description": "AKW 2026-11-11 验证用，可删除",
      "lifecycleId": "3a175cd8-3ca4-7b38-5d0d-c847261f10f2",
      "lifecycleStatusId": "eef46a7e-a1a7-1b96-8c73-3a175cd8403a",
      "basicObjectId": "3a175cd8-3ca4-7f43-c9e1-b385a134a8bf",
      "objectCode": "audit_plan__c",
      "enableSignatures": false
    }
  ]
}
```

- 响应：HTTP 200，`code: 0`，`data: ["2188e7da-b46f-fd8a-a073-3a90fc439aef"]`。
- 回读：新动作排在原动作后，`sort: 2`、`isEnabled: true`、`name: null`。
  - 输入 `isEnabled: false` 与 `sort: 99` 被服务端归一化；`name` 不落库，`description` 落库。
  - `configs: []` 可以创建，但只是“无配置的进入动作”，不会真正拉起流程。
- 本次只验证了协议与落库；**没有验证 `configs` 里 `behaviorType: 7` 的实际触发效果**。
- 清理：该接口没有删除能力；本次用未登记的 `DELETE /api/config/lifecycle/EnterAction/Delete`
  传**裸数组** `["2188e7da-b46f-fd8a-a073-3a90fc439aef"]` 删除，返回 `{code:0,data:true}`。
  回读只剩原动作 `c27798f2-…`。该删除端点不应加入能力目录。

## 四、命名修正

- `lifecycle.useraction.list` 的标题由「读状态上的按钮列表」改为 **「读取用户动作」**；
  描述与文档统一为“用户动作（平台界面上的按钮）”。
- `lifecycle.useraction.get` 同步改为「读状态用户动作的权限角色」。
- `lifecycle.enteraction.list` 的 `returns` 改为实际字段列表。

## 五、是否追加白名单

先不追加。按流程，需要用户确认这三条实测结果后，再把：

```text
lifecycle.useraction.list
lifecycle.enteraction.list
lifecycle.enteraction.create
```

加入 `plugin/src/shared/approved-tools.ts` 的 `APPROVED_TOOL_IDS`，UI 才会从「过期」转「已验证」。
