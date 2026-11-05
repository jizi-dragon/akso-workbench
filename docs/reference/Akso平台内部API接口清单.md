# Akso eGMP 平台内部 API 清单（可检索摘要）

原表是同目录的 [Akso平台内部API接口清单.xlsx](./Akso平台内部API接口清单.xlsx)（2026-10-27，前端抓包整理）。本文件是它的**检索用摘要**：表格形式便于 grep 和 diff，原表的「抓包次数 / 来源」两列在这里省略。两者不一致时以原表为准。47 个端点的**可复制请求报文**见下文「[请求示例](#请求示例)」章，逐条对应、顺序相同。

| 项目 | 内容 |
| --- | --- |
| 覆盖 | 47 个端点：对象 / 字段 / 选项集 / 表单布局 / 列表布局 / 生命周期 / 工作流 / 菜单 / 通知 / 认证 |
| 目标环境 | `https://standard-val.aksoegmp.com`（验证租户）；下表路径均为主机后的相对路径 |
| 版本基线 | 文档按平台 3.7.x 编写，实测验证环境产品版本 3.8.2.0，接口均可调通 |
| 数据来源 | akso-skill 的 `api-reference.md`（管理端抓包 + `lib/api` 源码）、各 JS 模块实际调用、`monitor-log.json` 136 条抓包；「请求示例」章的报文按同一批来源逐条核对，✅ 实测 / ⚠️ 推断 |
| 风险 | 结论来自验证环境；不可逆操作（启用生命周期、删对象/字段/工作流、菜单全量排序）先用验证环境复跑 |

## 通用约定

- **认证**：`POST /api/openapi/v1.0/Auth {account, password}` 换 JWT，响应 `data` 本身就是 JWT 字符串（部分版本回 `data.token`，两种都兼容）即 Bearer；约 55 分钟过期，401 时重新登录。之后所有 `/api/platform/*`、`/api/config/*` 都带 `Authorization: Bearer <token>`。
- **信封**：统一 `{code, errorCode, message, data}`；`code=0` 成功，`1000` 参数校验错误，`401` 未授权，`403` 无权限，`500` 业务错误。HTTP 200 也可能带非 0 的 `code`。
- **命名陷阱**：`AddColumns`、`Menu/Sort`、`SaveLayoutDetail` 名字里带 Add/Save，语义都是**全量替换**——不在数组里的东西会被删掉。

## 接口总表

### 认证与登录（3）

| 方法 | 路径 | 用途 | 要点 |
| --- | --- | --- | --- |
| POST | `/api/openapi/v1.0/Auth` | 账号密码登录换 JWT | 响应 `data` 即 JWT（或 `data.token`），约 55 分钟过期 |
| POST | `/api/auth/Oauth/Login` | 前端 OAuth 登录 | body 未记录；建模场景用上面那条更简单 |
| POST | `/api/auth/Oauth/RefreshToken` | 刷新 token | 传 `refreshToken`；skill 未使用（直接重登） |

### 对象（7）

| 方法 | 路径 | 用途 | 要点 |
| --- | --- | --- | --- |
| POST | `/api/platform/BasicObject/SaveBasicObject` | 创建/更新自定义对象 | `code` 需 `__c` 结尾；响应只有 `{code:0,data:true}`，**不返回 objectId**，要用 OpenAPI 反查；`enableLifeCycle=true` **不可逆** |
| POST | `/api/platform/BasicObject/FieldPage` | 分页查询对象字段（管理端） | `objectId/pageIndex/pageSize/filters`；返回 `data.items[]` + `totalCount`；字段比 OpenAPI 全 |
| GET | `/api/platform/BasicObject/FieldsAndOutField` | 查询可选字段（含关联/外部字段） | 参数未收录 ⚠️；列表布局选列时用 |
| GET | `/api/openapi/v1.0/BasicObject/{code}` | 编码反查对象 ID / 生命周期 ID | 返回 `id`、`lifeCycleId`、`enableLifeCycle`；建模后取 objectId 的主要手段 |
| GET | `/api/openapi/v1.0/BasicObject/field/{objectCode}` | 查询对象字段列表 | 加 `/{fieldCode}` 查单个；返回含系统字段的 `[{id,name,code,dataType,…}]` |
| GET | `/api/openapi/v1.0/BasicObject/picklist/{code}` | 查询选项集及选项值 | 项里含 `objectPicklistId`（即字段用的 `picklistId`） |
| GET | `/api/openapi/v1.0/BasicObject/lifecycleStatus/{objectCode}` | 查询对象生命周期状态 | **按 objectCode 查**；建模、工作流绑状态前先查 |

### 字段（1）/ 选项集（1）

| 方法 | 路径 | 用途 | 要点 |
| --- | --- | --- | --- |
| POST | `/api/platform/BasicObject/SaveField` | 创建/更新自定义字段 | `statisticsArrange:{id:1}` **必填**（漏传是 1000 错误最常见原因）；`dataType=15` 自动 `isRequired=true`、`16` 自动 false |
| POST | `/api/platform/ObjectPicklist/save` | 创建/更新选项集 | `options[{name,code,status,operations,sort}]`；响应**不返回 picklistId**，需反查。**同一个 `code` 再提交就是更新**（平台按 code 认既有选项集，不用传 id）。`options[].code` 不填时平台按 name 自动生成编码，生成结果不可预知 ⇒ 想稳定比对就自己给编码。✅ **只增不删**（2026-10-27 实测，此前存疑已否）：提交里"没出现"的旧选项会原样留着，清单里也没有删选项的端点 ⇒ 要清理只能后台手工处理，所以这一步不是不可逆动作。同日实测过程：先 2 项建出 `akw_test_pk__c`，再提交 3 项 → 读回 3 项、名称与编码都对；改名 + 加项 → 4 项（改名生效）；只提交 3 项（去掉 `low__c`）→ 读回仍是 4 项，`low__c` 未被删。两次写入平台都回过 `code=401 Unauthorized` 而数据已经落库（见文末「写入报未授权但已生效」） |

### 表单布局（3）

| 方法 | 路径 | 用途 | 要点 |
| --- | --- | --- | --- |
| GET | `/api/platform/Layout/LayoutList` | 查询对象表单布局列表 | `basicObjectId`；默认布局 `source=2` |
| GET | `/api/platform/Layout/LayoutDetail` | 取布局详情 | 改之前**必须先取 `pages` 原值**，否则保存报「无法删除其他布局部分Section」 |
| POST | `/api/platform/Layout/SaveLayoutDetail` | 保存布局（全量替换） | `id/objectId/objectTypeId/name/code/sections[]/controls[]/pages[]/rules[]`；`type=4` 的「工作流时间线」分区不可删 |

### 列表布局（6）

| 方法 | 路径 | 用途 | 要点 |
| --- | --- | --- | --- |
| GET | `/api/platform/Listlayout/List` | 查询对象列表布局 | `objectId`；默认布局 `code` 形如 `{对象code}_table__ak`、`source=2` |
| GET | `/api/platform/Listlayout/Columns` | 查询布局当前列 | `listlayoutId` |
| POST | `/api/platform/Listlayout/Save` | 新建列表布局 | 新建后还要 `AddColumns` 才有列 |
| POST | `/api/platform/Listlayout/AddColumns` | 设置列表列（**全量替换**） | 跨对象列用「查找字段code.关联对象字段code」 |
| POST | `/api/platform/Listlayout/SaveFilter` | 保存列表过滤条件（upsert） | `belongId/details[]/relations[]/id`；参数来自监听 ⚠️ 未完全验证 |
| GET | `/api/platform/Listlayout/Filters` | 查询列表过滤条件 | `belongId`；⚠️ 同上 |

### 生命周期（4）

| 方法 | 路径 | 用途 | 要点 |
| --- | --- | --- | --- |
| POST | `/api/config/lifecycle/Status/Create` | 创建生命周期状态 | 响应 `data` 即新状态 UUID；`lifecycleId` 从对象查询接口取；`workflowCancelStatus` 固定 GUID |
| PATCH | `/api/config/lifecycle/Status/Update` | 更新生命周期状态 | **全量提交**（先查再合并）；常用于重命名 |
| POST | `/api/config/lifecycle/UserAction/Create` | 状态上创建「发起工作流」按钮 | `behaviorValue.workflowId` 必须是 **workflowBasicId**（不是 workflowConfigId） |
| POST | `/api/config/power/LifestatusUserAction/Get` | 查状态已绑定的用户动作 | 绑定前先查；`UserAction/Create` 每调一次**新建一个按钮**、不做合并，重复调会产生重复按钮 |

### 工作流（18）

| 方法 | 路径 | 用途 | 要点 |
| --- | --- | --- | --- |
| POST | `/api/platform/Workflow/AddWorkflowBasic` | 新建工作流 | 参数必须包在 `basicConfig` 里；返回 `basicConfig.id`(configId) 与 `workflowBasicId`，**两者不同** |
| GET | `/api/platform/Workflow/GetWorkflowBasic` | 查工作流详情 | `id=workflowConfigId`；更新前先取完整配置合并 |
| POST | `/api/platform/Workflow/UpdateWorkflowBasic` | 更新工作流基础信息 | 乐观锁 `versionNo`，不匹配报冲突；20→40 时 id 变更、versionNo+1 |
| POST | `/api/platform/Workflow/GetWorkflowBasicPageViewList` | 分页查工作流 | 返回 `data.datas[]` + `data.count` |
| POST | `/api/platform/Workflow/ChangeWorkflowBasicStatus` | 变更工作流状态 | `ids[]/status/workflowBasicId`；**改步骤前必须先置 40（编辑中）** |
| POST | `/api/platform/Workflow/DeleteWorkflowBasic` | 删除工作流 | 不可恢复 |
| GET | `/api/platform/Workflow/GetWorkflowSteps` | 查步骤及连线 | 文档写 POST，代码用 GET |
| POST | `/api/platform/Workflow/AddWorkflowTaskStep` | 加审批任务步骤 | 参数包在 `step` 里；`stepTaskApprovals[]` 等；三模式 minimal/confirm/approval |
| POST | `/api/platform/Workflow/AddWorkflowDecisionStep` | 加判断步骤 | `stepDecision.rules[{condition,nextStepId}]`；`logicValue` 是按钮 UUID，需外层双引号 |
| POST | `/api/platform/Workflow/AddWorkflowActionStep` | 加动作步骤 | `behaviorType=9` 改记录状态，`=7` 发起工作流 |
| POST | `/api/platform/Workflow/UpdateWorkflowTaskStep` | 更新任务步骤 | 同 Add，含 `id` |
| POST | `/api/platform/Workflow/UpdateWorkflowDecisionStep` | 更新判断步骤 | 含 `id` |
| POST | `/api/platform/Workflow/UpdateWorkflowActionStep` | 更新动作步骤 | 含 `id` |
| POST | `/api/platform/Workflow/CopyWorkflowCurrentStep` | 复制当前步骤 | `{stepName,stepCode,workflowConfigId,stepId}`；返回 `data===true`，新步骤 id 要再 `GetWorkflowSteps` 按 code 找 |
| DELETE | `/api/platform/Workflow/DeleteWorkflowStep` | 删除步骤 | `id=步骤UUID`；系统步骤（10 开始 / 20 结束）不可删 |
| GET | `/api/platform/Workflow/GetWorkflowStepWithDetails` | 查步骤完整详情 | 含 `taskConfig`、`approvals`、`startUpControls` |
| GET | `/api/platform/Workflow/GetWorkflowStepControls` | 查开始步骤控件 | `workflowConfigId`、`stepId`；文档写 POST、代码用 GET |
| POST | `/api/platform/Workflow/UpdateWorkflowStartStep` | 配置开始步骤与连线 | `startUpControls[{controlConfig,participantConfig}]`；`controlConfig.value` 要同步写 `participantConfig` 的 JSON 串 |

### 菜单（3）/ 通知（1）

| 方法 | 路径 | 用途 | 要点 |
| --- | --- | --- | --- |
| POST | `/api/platform/Menu/Submit` | 创建菜单（母/子） | 端点是 **Submit 不是 Save**；母 `menuActionType=3`、子 `=1` 且需 `parentMenuId/objectId/listLayoutId/layoutId`；返回 `data.Id` |
| POST | `/api/platform/Menu/Sort` | 菜单树排序（**全量替换**） | body 就是数组 `[{key:菜单UUID,children:[]}]`；不在数组里的菜单不保留 |
| POST | `/api/platform/MenuGroup/QueryList` | 查询菜单组 | body `{}`；建菜单前先查 appId / 菜单组 |
| POST | `/api/platform/notice/GetMessageTemplateByObject` | 按对象查消息模板 | `id=objectId`；返回 `title/content/jumplink` 等 |

## 请求示例

> 与上面的 47 条一一对应，顺序相同：GET 给 query，其余给请求体；每条末尾写 `data` 的形状。UUID 一律写成 `<objectId>` 这类占位符，别照抄。

### 读法约定

- **公共头**：除登录外都带 `Authorization: Bearer <token>`；写接口再加 `Content-Type: application/json`。GET 不带 body，参数拼在 query 里。
- **标记**：✅ = 抓包或验证环境（`standard-val`）跑通过；⚠️ = 参数来自监听或推断、没完全验证；两者都没有 = 照参数表构造的，第一次调用后回读确认。
- **响应**：平台统一回 `{code, errorCode, message, data, requestID, traceId}`，`requestID`/`traceId` 是后端链路号，报障时给平台。下文只写 `data` 的形状。
- **HTTP 200 也可能是失败**：先看 `code` 再看 `data`；写入类接口失败后先回读再决定要不要重试（见文末「写入报未授权但已生效」）。

curl 骨架（登录 + 一次写）：

```bash
BASE=https://standard-val.aksoegmp.com

# 1) 换 token。data 就是 JWT 字符串；若平台回 {"token": "..."}，改取 .data.token
TOKEN=$(curl -s -X POST "$BASE/api/openapi/v1.0/Auth" \
  -H 'Content-Type: application/json' \
  -d '{"account":"<账号>","password":"<密码>"}' | jq -r '.data')

# 2) 写接口：Authorization + JSON body
curl -s -X POST "$BASE/api/platform/BasicObject/SaveBasicObject" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"变更控制","code":"change_control__c","source":3,"status":1,"objectClass":1,"enableLifeCycle":false,"enableSignatures":false,"summaryFields":[]}'
```

### 占位符从哪来

| 占位符 | 是什么 | 怎么取 |
| --- | --- | --- |
| `<token>` | JWT | `POST /api/openapi/v1.0/Auth` 的 `data`（约 55 分钟过期） |
| `<objectId>` | 对象 UUID | `GET /api/openapi/v1.0/BasicObject/{code}` → `data.id` |
| `<objectTypeId>` | 对象类型 UUID | `Layout/LayoutList` 返回的布局记录里带，保存布局/列表时原样回传 |
| `<lifecycleId>` | 生命周期 UUID | `BasicObject/{code}` → `data.lifeCycleId`（注意大写 `L`） |
| `<statusId>` | 生命周期状态 UUID | `lifecycleStatus/{objectCode}` 项的 `id`，或 `Status/Create` 的 `data` |
| `<fieldId>` | 字段 UUID | `BasicObject/field/{objectCode}` → `id`；跨对象列用目标字段自己的 `id` |
| `<picklistId>` | 选项集 UUID | `BasicObject/picklist/{code}` → 项里的 `objectPicklistId` |
| `<layoutId>` | 表单布局 UUID | `Layout/LayoutList` → `data.datas[].id`（默认布局 `source=2`） |
| `<listLayoutId>` | 列表布局 UUID | `Listlayout/List` → `data[].id` |
| `<workflowConfigId>` | 工作流配置 ID | `AddWorkflowBasic` → `data.basicConfig.id` |
| `<workflowBasicId>` | 工作流基础 ID | `AddWorkflowBasic` → `data.basicConfig.workflowBasicId`（和上面那个**不是**一个东西） |
| `<stepId>` | 步骤 UUID | `GetWorkflowSteps` 的项，或加步骤响应的 `data.step.id` |
| `<menuId>` | 菜单 UUID | `Menu/Submit` → `data.Id` |

### 认证与登录（3）

#### `POST /api/openapi/v1.0/Auth` — 账号密码换 JWT ✅

```json
{
  "account": "<账号>",
  "password": "<密码>"
}
```

响应 `data` 就是 JWT 字符串（`akso-skill/auth.js:91` 对 `data` / `data.token` / `data.access_token` 三种形态都做了兼容）：

```json
{ "code": 0, "errorCode": 0, "message": null, "data": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..." }
```

#### `POST /api/auth/Oauth/Login` — 前端 OAuth 登录 ⚠️

body 未收录。实测前端是在 `/login` 页面表单登录，成功后 JWT 落在 cookie `__auth_token__`（`auth.js` 的 DOM 登录就是抠这个 cookie）。所以这条路不用猜 body，直接：

```text
浏览器登录后读 cookie：__auth_token__=<jwt>   →  与 Auth 拿到的 token 等价，可直接做 Bearer
```

#### `POST /api/auth/Oauth/RefreshToken` — 刷新 token ⚠️

```json
{ "refreshToken": "<refreshToken>" }
```

响应 `data` 是新 token（字段名未细验）。skill 未使用这条路——401 直接重新 `Auth` 更省事。

### 对象（7）

#### `POST /api/platform/BasicObject/SaveBasicObject` — 创建/更新对象 ✅

```json
{
  "name": "变更控制",
  "code": "change_control__c",
  "source": 3,
  "status": 1,
  "objectClass": 1,
  "enableLifeCycle": false,
  "enableSignatures": false,
  "summaryFields": []
}
```

- `source` 固定 `3`（自定义）；`objectClass` 固定 `1`（基础对象）。
- 要开生命周期就把 `enableLifeCycle` 置 `true` —— **不可逆**，开了关不掉，且之后才能用 `Status/Create`。
- 响应只有 `{ "code": 0, "data": true }`，**不返回 objectId**：紧接着用 `GET /api/openapi/v1.0/BasicObject/change_control__c` 反查 `data.id` / `data.lifeCycleId`。

#### `POST /api/platform/BasicObject/FieldPage` — 分页查对象字段（管理端） ✅

```json
{
  "objectId": "<objectId>",
  "pageIndex": 1,
  "pageSize": 50,
  "filters": {}
}
```

响应 `data.items[]` + `data.totalCount`，单项比 OpenAPI 的 `field/{objectCode}` 更全（带 `statisticsArrange`、布局/校验相关配置），做「字段已存在吗」的比对时用它。

#### `GET /api/platform/BasicObject/FieldsAndOutField` — 可选字段（含关联/外部） ⚠️

```text
GET /api/platform/BasicObject/FieldsAndOutField?basicObjectId=<objectId>
```

参数未收录，`basicObjectId` 是照 `Layout/LayoutList` 的同名参数推的；调之前用浏览器抓一次确认。响应 `data[]` 为字段项，列表布局要加「关联对象的字段」时从这里选（`fieldPath` 用点号路径）。

#### `GET /api/openapi/v1.0/BasicObject/{code}` — 编码反查对象 ✅

```text
GET /api/openapi/v1.0/BasicObject/change_control__c
```

```json
{
  "code": 0,
  "data": {
    "id": "<objectId>",
    "code": "change_control__c",
    "name": "变更控制",
    "lifeCycleId": "<lifecycleId>",
    "enableLifeCycle": true
  }
}
```

建模后取 `objectId` / `lifecycleId` 的主路径；对象不存在时是业务错误（`code` 非 0），不是 404。

#### `GET /api/openapi/v1.0/BasicObject/field/{objectCode}` — 字段列表 ✅

```text
GET /api/openapi/v1.0/BasicObject/field/change_control__c
GET /api/openapi/v1.0/BasicObject/field/change_control__c/no__c     # 单个字段
```

响应 `data` 是**裸数组** `[{id, name, code, dataType, ...}]`（92 键左右，含 `picklistId` 和内联 `picklist`）；比 v3.6.4 的 `fileds` 快且全。

#### `GET /api/openapi/v1.0/BasicObject/picklist/{code}` — 选项集及选项值 ✅

```text
GET /api/openapi/v1.0/BasicObject/picklist/change_type__c
```

`{code}` 必须是**选项集编码**（传 id 或字段编码会得到 `code:0 + data:null`）。响应 `data` 是选项数组，项里的 `objectPicklistId` 就是建字段时 `dataType=4` 要的 `picklistId`。

#### `GET /api/openapi/v1.0/BasicObject/lifecycleStatus/{objectCode}` — 对象生命周期状态 ✅

```text
GET /api/openapi/v1.0/BasicObject/lifecycleStatus/change_control__c
```

```json
{
  "code": 0,
  "data": [
    { "belongType": 1, "id": "<statusId>", "code": "status_draft__c", "name": "草稿",
      "lifecycleId": "<lifecycleId>", "lifecycleCode": "change_control_lc", "lifecycleName": "变更控制生命周期" }
  ]
}
```

按 **objectCode** 查，返回裸数组；对象没绑生命周期时是 `[]`，不报错。

### 字段（1）/ 选项集（1）

#### `POST /api/platform/BasicObject/SaveField` — 创建/更新字段 ✅

`dataType` 决定要不要带额外键，公共部分是 `name/code/dataType/objectId/statisticsArrange`。

文本（`dataType=1`）：

```json
{
  "name": "编号",
  "code": "no__c",
  "dataType": 1,
  "objectId": "<objectId>",
  "isRequired": true,
  "invisible": false,
  "disabled": false,
  "statisticsArrange": { "id": 1 }
}
```

选项（`dataType=4`，要 `picklistId`）：

```json
{
  "name": "变更类型",
  "code": "type__c",
  "dataType": 4,
  "objectId": "<objectId>",
  "picklistId": "<picklistId>",
  "statisticsArrange": { "id": 1 }
}
```

对象/查找（`dataType=15`，要 `referenceObjectId` + `objectRelation`）：

```json
{
  "name": "关联变更",
  "code": "related_change__c",
  "dataType": 15,
  "objectId": "<objectId>",
  "referenceObjectId": "<目标 objectId>",
  "objectRelation": { "controlField": null },
  "statisticsArrange": { "id": 1 }
}
```

- `statisticsArrange: { "id": 1 }` **必填**，漏了就是 1000 错误（最常见的一条）。
- 数字（`2`）还要 `max/min/decimalPlaces` 和 `format`；日期 `5` 用 `format:"YYYY-MM-DD"`；日期时间 `7` 用 `"YYYY-MM-DD HH:mm:ss"`；`15` 建出来 `isRequired` 会被强制为 `true`、`16` 强制为 `false`。
- 响应 `{ "code": 0, "data": true }`，不返回 fieldId —— 用 `field/{objectCode}` 反查。

#### `POST /api/platform/ObjectPicklist/save` — 创建/更新选项集 ✅

```json
{
  "name": "变更类型",
  "code": "change_type__c",
  "options": [
    { "name": "主要变更", "code": "major__c", "status": 1, "operations": 1, "sort": 0 },
    { "name": "次要变更", "code": "minor__c", "status": 1, "operations": 1, "sort": 1 }
  ]
}
```

- 同一个 `code` 再提交就是**更新**（平台按 code 认既有选项集，不用传 id）；只增不删，提交里没出现的旧选项会留着。
- `options[].code` 不填时平台按 name 自动生成，结果不可预知 —— 想稳定比对就自己给编码。
- 响应 `{ "code": 0, "data": true }`，**不返回 picklistId**；用 `picklist/{code}` 反查。
- 这条出现过「HTTP 200 + `code:401` + 数据已落库」，失败先回读。

### 表单布局（3）

#### `GET /api/platform/Layout/LayoutList` — 布局列表 ✅

```text
GET /api/platform/Layout/LayoutList?basicObjectId=<objectId>&pageIndex=1&pageSize=20
```

```json
{
  "code": 0,
  "data": {
    "datas": [
      { "id": "<layoutId>", "name": "默认布局", "code": "change_control__c_base_layout__ak",
        "source": 2, "isMain": true, "objectTypeId": "<objectTypeId>" }
    ]
  }
}
```

`source=2` 就是系统默认布局；`objectTypeId` 从这条记录里取，保存布局/列表时原样回传。

#### `GET /api/platform/Layout/LayoutDetail` — 布局详情 ✅

```text
GET /api/platform/Layout/LayoutDetail?layoutId=<layoutId>
```

响应 `data` 含 `sections[]`、`controls[]`、`pages[]`。**改布局前必须先读它**：`pages` 要原值回传，否则保存时报「无法删除其他布局部分Section」。

#### `POST /api/platform/Layout/SaveLayoutDetail` — 保存布局（全量替换） ✅

```json
{
  "id": "<layoutId>",
  "objectId": "<objectId>",
  "objectTypeId": "<objectTypeId>",
  "name": "默认布局",
  "code": "change_control__c_base_layout__ak",
  "source": 2,
  "status": 1,
  "isMain": true,
  "labelCol": 0,
  "sections": [
    { "id": "<sectionId>", "code": "base_info__cs", "name": "基本信息", "type": 1, "columnsNum": 2 }
  ],
  "controls": [
    {
      "id": "<controlId>",
      "sectionId": "<sectionId>",
      "code": "no__c",
      "name": "no__c",
      "fieldId": "<fieldId>",
      "type": "AKInput",
      "gridSpan": 1,
      "attrs": { "readPrettyShowType": "1" }
    }
  ],
  "pages": [],
  "rules": [],
  "activePageCode": null,
  "activeSectionCode": null
}
```

- 全量替换：`sections`/`controls` 里没出现的分区和控件会被删。
- `pages` 必须是 `LayoutDetail` 读到的原值（示例里 `[]` 只适用于本来就没有页签的布局）。
- `type=4` 的「工作流时间线」分区是系统分区，删不掉，原样留着。
- 控件类型见「枚举速查」；`AKSelectorYesNo` 的 `attrs` 要加 `"optional": "[]"`。
- ⚠️ 未验证：有说法称 `section.belongId` 由其 FullPath 生成，写布局缺它可能被拒——第一次写先在验证环境跑。

### 列表布局（6）

#### `GET /api/platform/Listlayout/List` — 列表布局列表 ✅

```text
GET /api/platform/Listlayout/List?objectId=<objectId>
```

响应 `data` 是数组，项含 `id/name/code/source`；默认布局 `source=2`、`code` 形如 `{对象code}_table__ak`。

#### `GET /api/platform/Listlayout/Columns` — 当前显示列 ✅

```text
GET /api/platform/Listlayout/Columns?listlayoutId=<listLayoutId>
```

响应 `data` 是列数组 `[{ fieldId, fieldPath, sort }]`。**改列前必须先读**，因为 `AddColumns` 是全量替换。

#### `POST /api/platform/Listlayout/Save` — 新建列表布局 ✅

```json
{
  "status": 1,
  "source": 3,
  "sortFields": [],
  "name": "变更列表",
  "code": "list_change__c",
  "objectTypeId": "<objectTypeId>",
  "objectId": "<objectId>"
}
```

响应 `data` 是新布局（取 `data.id`，个别版本直接回 id 字符串）。只建壳，列要再调 `AddColumns`。

#### `POST /api/platform/Listlayout/AddColumns` — 设置列（全量替换） ✅

```json
{
  "listlayoutId": "<listLayoutId>",
  "listlayoutColumns": [
    { "fieldId": "<fieldId:no__c>", "fieldPath": "no__c", "sort": 1 },
    { "fieldId": "<fieldId:title__c>", "fieldPath": "title__c", "sort": 2 },
    { "fieldId": "<fieldId:status_id__sys>", "fieldPath": "status_id__sys", "sort": 3 },
    { "fieldId": "<关联对象字段的 fieldId>", "fieldPath": "related_change__c.applicant__c", "sort": 4 }
  ]
}
```

名字带 Add，语义是 replace：不在数组里的列会被移除。跨对象列用「查找字段 code.关联对象字段 code」，`fieldId` 始终是**目标字段自己**的 UUID。

#### `POST /api/platform/Listlayout/SaveFilter` — 保存过滤条件（upsert） ⚠️

```json
{
  "belongId": "<listLayoutId>",
  "details": [
    {
      "id": "<detailId1>",
      "relationId": "<relationId1>",
      "targetType": 1,
      "targetId": "<fieldId:created_time__sys>",
      "targetCode": "created_time__sys",
      "logicType": 4,
      "logicValue": "\"2026-07-01\"",
      "targetFieldDataType": 7,
      "index": 1785850545742
    },
    {
      "id": "<detailId2>",
      "relationId": "<relationId1>",
      "targetType": 1,
      "targetId": "<fieldId:created_by__sys>",
      "targetCode": "created_by__sys",
      "logicType": 1,
      "logicValue": "{!current.user}",
      "targetFieldDataType": 15,
      "index": 1785850545743
    }
  ],
  "relations": [
    { "id": "<relationId1>", "logicalOperator": 1, "index": 1785850545744 }
  ]
}
```

- 首次不传 `id`（新建），响应 `data` 就是新过滤配置 id；再提交时带上它才是更新。
- `logicType` 目前只确认 4 个：`1` 等于、`4` 晚于、`10` 为空、`11` 不为空（枚举未穷尽，属实验性）。
- 字符串/日期值要**自带引号**（JSON 里就是 `"\"2026-07-01\""`）；当前用户用 `{!current.user}`。
- `relations[].logicalOperator`：`1`=AND、`2`=OR；`details[].relationId` 指向它归到哪个条件组。

#### `GET /api/platform/Listlayout/Filters` — 查询过滤条件 ⚠️

```text
GET /api/platform/Listlayout/Filters?belongId=<listLayoutId>
```

响应 `data` 为 `{ id, belongId, details[], relations[] }`；没配过时 `data` 为空。语义同 `SaveFilter`，枚举未穷尽。

### 生命周期（4）

#### `POST /api/config/lifecycle/Status/Create` — 创建状态 ✅

```json
{
  "name": "草稿",
  "code": "status_draft__c",
  "lifecycleId": "<lifecycleId>",
  "isEnabled": true,
  "isRecordDeactivate": false,
  "description": "",
  "source": 3,
  "workflowCancelStatus": "11515107-dce6-4ef1-8d51-b1ffed22040f"
}
```

响应 `data` 就是**新状态的 UUID 字符串**（直接拿去做 `statusId`）。`workflowCancelStatus` 这个 GUID 是固定的，照抄。

#### `PATCH /api/config/lifecycle/Status/Update` — 更新状态（全量） ✅

```json
{
  "id": "<statusId>",
  "name": "已完成",
  "code": "status_done__c",
  "lifecycleId": "<lifecycleId>",
  "isEnabled": true,
  "isDeleted": false,
  "isRecordDeactivate": false,
  "description": "",
  "source": 3,
  "sort": 2,
  "workflowCancelStatus": "11515107-dce6-4ef1-8d51-b1ffed22040f"
}
```

全量提交：先 `lifecycleStatus/{objectCode}` 读回原值，改要改的，其余原样带上（`isDeleted`、`sort` 也别忘了），少一个字段就可能被清空。

#### `POST /api/config/lifecycle/UserAction/Create` — 状态上挂「发起工作流」按钮 ✅

```json
{
  "isEnabled": true,
  "belongId": "<statusId>",
  "belongType": 2,
  "sort": 1,
  "executeType": 1,
  "description": "提交动作",
  "lifecycleId": "<lifecycleId>",
  "lifecycleStatusId": "<statusId>",
  "executionConditionGroups": [],
  "basicObjectId": "<objectId>",
  "objectCode": "change_control__c",
  "enableSignatures": true,
  "actions": [
    {
      "name": "提交",
      "code": "action_submit__c",
      "icon": "CheckOutlined",
      "group": 1,
      "isSubmitAndExecute": false,
      "isCommonly": true,
      "sort": 0,
      "configs": [
        {
          "behaviorType": 7,
          "behaviorValue": {
            "isBeforeWorkflowCompleteReminderTask": true,
            "workflowId": "<workflowBasicId>"
          }
        }
      ]
    }
  ]
}
```

- `behaviorValue.workflowId` 必须是 **workflowBasicId**，传 workflowConfigId 会绑不上。
- 语义提醒：这条接口是**新建一个按钮**，每调一次多一个。上表「追加后 PATCH 全量提交」是抓包看到的另一种做法；用代码里的 Create 更简单，但同一状态重复调用会产生重复按钮。
- 要改已有按钮应走 `Update`（本清单未收录）；响应只校验 `code`，`data` 未细验。

#### `POST /api/config/power/LifestatusUserAction/Get` — 查状态上已有的动作 ✅

```json
{ "statusId": "<statusId>" }
```

响应 `data` 是该状态的用户动作数组（每项含 `actions[]`，`actions[].configs[].behaviorValue.workflowId` 就是已绑的工作流）。绑定/重绑前先查，能避免重复按钮。

### 工作流（18）

> 这一组 18 条全部来自 `akso-skill` 实测代码 + 2026-08-04 抓包，`AddWorkflowTaskStep` / `AddWorkflowDecisionStep` / `notice` 三条有完整真实报文可比对。

#### `POST /api/platform/Workflow/AddWorkflowBasic` — 新建工作流 ✅

```json
{
  "basicConfig": {
    "name": "变更审批",
    "code": "change_approve__c",
    "status": 10,
    "objectId": "<objectId>",
    "lifecycleId": "<lifecycleId>",
    "workflowType": 10,
    "isSingleRecord": true
  },
  "variables": []
}
```

- 一定要套 `basicConfig`，直接平铺字段就是 1000。
- 响应 `data.basicConfig` 里有**两个** id：`data.basicConfig.id` 是「工作流配置 ID」（加步骤、查步骤、`ChangeStatus` 都用它），`data.basicConfig.workflowBasicId` 是「工作流基础 ID」（只用在绑按钮、`ChangeStatus` 的第二个参数里）。混用是 1000 的高发原因。
- `status:10` 是草稿；`variables` 没变量就给 `[]`。

#### `GET /api/platform/Workflow/GetWorkflowBasic` — 读工作流详情 ✅

```text
GET /api/platform/Workflow/GetWorkflowBasic?id=<workflowConfigId>
```

响应 `data.basicConfig`（配置全字段，含 `versionNo`）+ `data.variables[]`。改配置前先读，`UpdateWorkflowBasic` 是全量。

#### `POST /api/platform/Workflow/UpdateWorkflowBasic` — 更新工作流（全量合并） ✅

```json
{
  "basicConfig": {
    "id": "<workflowConfigId>",
    "name": "变更审批",
    "code": "change_approve__c",
    "status": 10,
    "objectId": "<objectId>",
    "lifecycleId": "<lifecycleId>",
    "workflowType": 10,
    "isSingleRecord": true,
    "versionNo": 3
  },
  "variables": []
}
```

先 `GetWorkflowBasic` 取回 `basicConfig`，把要改的字段覆盖上去，整份提交（别只传改的那几个键）；`versionNo` 跟着回传是乐观锁。

#### `POST /api/platform/Workflow/GetWorkflowBasicPageViewList` — 工作流分页列表 ✅

```json
{
  "pageIndex": 1,
  "filters": [],
  "pageSize": 20
}
```

响应 `data.datas[]` + `data.count`；`filters` 要过滤时按 `[{ field, value }]` 形状给（未穷尽）。

#### `POST /api/platform/Workflow/ChangeWorkflowBasicStatus` — 发布 / 停用 / 草稿 ✅

```json
{
  "ids": ["<workflowConfigId>"],
  "status": 40,
  "workflowBasicId": "<workflowBasicId>"
}
```

- `ids` 是**数组**，元素是 workflowConfigId；`workflowBasicId` 单独一个字段，两者不能互换。
- `status`：`10` 草稿、`20` 启用、`30` 禁用、`40` 编辑中（枚举见「枚举速查」）。
- 加步骤前必须先切到 `40`（编辑中），草稿态加步骤会被拒。

#### `POST /api/platform/Workflow/DeleteWorkflowBasic` — 删除工作流 ✅

```json
{ "id": "<workflowConfigId>" }
```

用 **POST** 传 body，不是 DELETE（旧封装不支持 DELETE 带 body，实测 POST 可用）。只删得了自己建的草稿，已发布/有实例的会被拒。

#### `GET /api/platform/Workflow/GetWorkflowSteps` — 步骤列表 ✅

```text
GET /api/platform/Workflow/GetWorkflowSteps?workflowConfigId=<workflowConfigId>
```

响应 `data[]`，每项 { `id`, `name`, `code`, `stepType`, `nextStepId`, `nextStepCode` }；按 `stepType`（10/20/30/70/90）和 `sort` 还原顺序，`CopyWorkflowCurrentStep` 之后也靠它找新步骤的 id。

#### `POST /api/platform/Workflow/AddWorkflowTaskStep` — 加审批节点（任务步骤） ✅

审批模式（带按钮，最常用）：

```json
{
  "step": {
    "nextStepId": "<nextStepId>",
    "name": "审核",
    "code": "shenhe__c",
    "stepType": 30,
    "workflowConfigId": "<workflowConfigId>",
    "stepTaskConfig": {
      "taskNodeType": 0,
      "preTaskIds": "",
      "reciverType": 20,
      "notifyType": 0,
      "selectStatusTypeIds": "",
      "externalSignatureInfoFrom": 1,
      "name": "审核",
      "taskAssign": "<participantControlId>",
      "isAssignToPromoter": true,
      "dispatchMode": 10,
      "taskRequireType": 20,
      "isRequireApproval": true,
      "approvalLabel": "审批意见",
      "pageOpenType": 0,
      "breakApproveIds": ""
    },
    "stepTaskApprovals": [
      {
        "id": "<buttonIdAgree>",
        "sort": 0,
        "name": "同意",
        "isRequireSign": true,
        "isRequirePosition": true,
        "isShowResons": true,
        "resonTitle": "备注",
        "reasonInputType": 20,
        "isShowResponsibilities": true,
        "responsibieTitle": "签名含义",
        "isRequireResponsibilities": true,
        "responsibleOptions": [{ "id": "<signOptionId>", "value": "我同意" }]
      },
      {
        "id": "<buttonIdReject>",
        "sort": 1,
        "name": "退回",
        "isRequireSign": true,
        "isRequirePosition": true,
        "isShowResons": true,
        "isRequiredResons": true,
        "resonTitle": "备注",
        "reasonInputType": 20,
        "isShowResponsibilities": true,
        "responsibieTitle": "签名备注",
        "isRequireResponsibilities": true,
        "responsibleOptions": [{ "id": "<signOptionId2>", "value": "我拒绝" }]
      }
    ],
    "stepTaskShareSettingRules": [],
    "stepTaskNotifies": []
  }
}
```

- `stepTaskConfig` 这 16 个键**缺一个就是 1000**，`preTaskIds`/`selectStatusTypeIds`/`breakApproveIds` 没有也要给空串。
- `taskAssign` 填开始步骤参与者控件的 UUID；`isAssignToPromoter:true` 表示走发起人，`false` 表示走指定参与者（抓包里指定参与者时还会多一个同值 `userControlIds`）。
- 另两种模式：最简模式去掉 `stepTaskApprovals`、`stepTaskConfig` 里用 `completeButtonDisplay` 代替 `isRequireApproval/approvalLabel`；确认模式 = 1 个按钮 + `completeButtonDisplay:"确认"`。
- 抓包里的按钮可能没有 `isRequirePosition`，有它更稳（签名时要求岗位）。
- 响应 `data.step.id` 就是新步骤 id；`stepType` 对应 30。

#### `POST /api/platform/Workflow/AddWorkflowDecisionStep` — 加判断节点 ✅

```json
{
  "step": {
    "nextStepId": "",
    "name": "判断：审核结果",
    "code": "judge_audit__c",
    "stepType": 70,
    "workflowConfigId": "<workflowConfigId>",
    "stepDecision": {
      "rules": [
        {
          "id": "<ruleIdAgree>",
          "sort": 1,
          "name": "规则1",
          "condition": {
            "details": [
              {
                "id": "<detailIdAgree>",
                "index": 1785850545742,
                "relationId": "<relationIdAgree>",
                "targetType": 7,
                "targetId": "<taskStepId>",
                "targetCode": "shenhe__c",
                "logicType": 40,
                "logicValue": "\"<buttonIdAgree>\""
              }
            ],
            "relations": [{ "id": "<relationIdAgree>", "logicalOperator": 1, "index": 1785850545743 }]
          },
          "nextStepId": "<nextStepIdAgree>"
        },
        {
          "id": "<ruleIdReject>",
          "sort": 2,
          "name": "规则2",
          "condition": {
            "details": [
              {
                "id": "<detailIdReject>",
                "index": 1785850545744,
                "relationId": "<relationIdReject>",
                "targetType": 7,
                "targetId": "<taskStepId>",
                "targetCode": "shenhe__c",
                "logicType": 41,
                "logicValue": "\"<buttonIdReject>\""
              }
            ],
            "relations": [{ "id": "<relationIdReject>", "logicalOperator": 1, "index": 1785850545745 }]
          },
          "nextStepId": "<nextStepIdReject>"
        }
      ],
      "nextStepId": "<nextStepIdElse>"
    }
  }
}
```

- 判断节点的条件是「上一步点了哪个按钮」：`targetType:7` + `targetId` = 上一步（任务步骤）的 id，`logicType:40` = 点过、`41` = 没点过，`logicValue` 是**带引号的按钮 UUID**（JSON 里写成 `"\"<uuid>\""`）。
- 两条规则互斥时才各自 `nextStepId`；都不命中走 `stepDecision.nextStepId`（else 分支）。
- `index` 只用于排序/关联，抓包里是毫秒时间戳；`relations` 是条件组，单条件也必须有。

#### `POST /api/platform/Workflow/AddWorkflowActionStep` — 加动作节点 ✅

改生命周期状态（`behaviorType:9`）：

```json
{
  "step": {
    "nextStepId": "",
    "name": "变更状态",
    "code": "change_status__c",
    "stepType": 90,
    "workflowConfigId": "<workflowConfigId>",
    "stepAction": {
      "belongType": 3,
      "actions": [
        {
          "code": "",
          "name": "",
          "isEnabled": true,
          "belongType": 3,
          "configs": [
            { "behaviorType": 9, "behaviorValue": { "statusId": "<statusId>" }, "sort": 0 }
          ],
          "sort": 0,
          "executeType": 1,
          "description": "",
          "lifecycleId": "",
          "lifecycleStatusId": ""
        }
      ]
    }
  }
}
```

- `behaviorType`：`9` = 修改记录状态（`behaviorValue.statusId`）、`7` = 发起工作流（`behaviorValue.workflowId` = workflowBasicId）。
- 动作步骤的 `actions[].name` / `code` / `description` 可以是空串，平台按 `behaviorType` 显示；`lifecycleId` / `lifecycleStatusId` 留空也能过（实测）。
- 响应 `data.step.id`，`stepType` 为 `90`。

#### `POST /api/platform/Workflow/UpdateWorkflowStartStep` — 配置开始步骤 ✅

```json
{
  "step": {
    "id": "<startStepId>",
    "name": "开始",
    "code": "start__c",
    "stepType": 10,
    "isEnable": true,
    "workflowConfigId": "<workflowConfigId>",
    "sort": 1,
    "remark": null,
    "stepStartConfig": null,
    "stepRuleConfig": [],
    "nextStepList": [],
    "nextStepId": "<taskStepId>",
    "nextStepCode": "shenhe__c",
    "nextStepName": null,
    "startUpControls": [
      {
        "id": "<controlRecordId>",
        "name": null,
        "sort": 1,
        "controlConfig": {
          "ctrlType": 20,
          "code": "<participantCode>",
          "stepId": "<startStepId>",
          "workflowConfigId": "<workflowConfigId>",
          "sort": 1,
          "name": null,
          "isRequired": false,
          "value": "{\"name\":\"审核人\",\"selectUserType\":10,\"allowRoleIds\":[],\"excludeRoleIds\":[],\"isRequired\":false}",
          "dispatchMode": 10,
          "taskRequireType": 20,
          "isHidden": false,
          "stepTaskId": null,
          "remark": null
        },
        "participantConfig": {
          "name": "审核人",
          "selectUserType": 10,
          "allowRoleIds": [],
          "excludeRoleIds": [],
          "id": "<participantConfigId>",
          "isUseShareSetting": false,
          "isUseUserGroup": false,
          "functionId": null,
          "functionName": null,
          "isRequired": false,
          "remark": null,
          "companyFieldType": null,
          "condition": null,
          "listLayoutCode": null,
          "code": null,
          "isExcludeWorkflowStartUserId": false,
          "isUseRoleAsSignalMean": false
        }
      }
    ]
  }
}
```

- 这是**全量**覆盖开始步骤：`controlConfig.value` 必须是 `participantConfig` 的 JSON 序列化字符串（示例里为可读性省略了部分键，实际要把整份 `participantConfig` 序列化进去）。
- `nextStepId` / `nextStepCode` 是开始 → 第一个节点的连线；改连线就改这两个。
- 先 `GetWorkflowStepControls` 拿到现有控件，改 `allowRoleIds` 等再整份提交，别手写全新控件（会丢 `id`）。

#### `POST /api/platform/Workflow/UpdateWorkflowTaskStep` — 改任务步骤 ✅

```text
GET  /api/platform/Workflow/GetWorkflowStepWithDetails?id=<taskStepId>   → 取 data.step
POST /api/platform/Workflow/UpdateWorkflowTaskStep                        → body = { "step": <上面那份 step，改完再整份回传> }
```

只改字段时最小示例（其余键保持读回的原值）：

```json
{
  "step": {
    "id": "<taskStepId>",
    "name": "复核",
    "code": "shenhe__c",
    "stepType": 30,
    "workflowConfigId": "<workflowConfigId>",
    "nextStepId": "<nextStepId>",
    "stepTaskConfig": { "...": "读回的原值，只改 dispatchMode 等" },
    "stepTaskApprovals": [],
    "stepTaskShareSettingRules": [],
    "stepTaskNotifies": []
  }
}
```

`stepTaskApprovals` 传 `[]` 会把按钮**全部删掉**；要保留就把读回的按钮原样带上。`1000` 多半是 `stepTaskConfig` 少了键。

#### `POST /api/platform/Workflow/UpdateWorkflowDecisionStep` — 改判断步骤 ✅

```json
{
  "step": {
    "id": "<decisionStepId>",
    "name": "判断：审核结果",
    "code": "judge_audit__c",
    "stepType": 70,
    "workflowConfigId": "<workflowConfigId>",
    "stepDecision": {
      "rules": [
        {
          "id": "<ruleIdAgree>",
          "sort": 1,
          "name": "规则1",
          "condition": {
            "details": [
              {
                "id": "<detailIdAgree>",
                "index": 1785850545742,
                "relationId": "<relationIdAgree>",
                "targetType": 7,
                "targetId": "<taskStepId>",
                "targetCode": "shenhe__c",
                "logicType": 40,
                "logicValue": "\"<buttonIdAgree>\""
              }
            ],
            "relations": [{ "id": "<relationIdAgree>", "logicalOperator": 1, "index": 1785850545743 }]
          },
          "nextStepId": "<nextStepIdAgree>"
        }
      ],
      "nextStepId": "<nextStepIdElse>"
    }
  }
}
```

同样全量：`rules` 里没出现的分支会被删；改按钮 UUID 时 `logicValue` 里的引号别丢。

#### `POST /api/platform/Workflow/UpdateWorkflowActionStep` — 改动作步骤 ✅

```json
{
  "step": {
    "id": "<actionStepId>",
    "name": "变更状态",
    "code": "change_status__c",
    "stepType": 90,
    "workflowConfigId": "<workflowConfigId>",
    "stepAction": {
      "belongType": 3,
      "actions": [
        {
          "code": "",
          "name": "",
          "isEnabled": true,
          "belongType": 3,
          "configs": [
            { "behaviorType": 9, "behaviorValue": { "statusId": "<newStatusId>" }, "sort": 0 }
          ],
          "sort": 0,
          "executeType": 1,
          "description": "",
          "lifecycleId": "",
          "lifecycleStatusId": ""
        }
      ]
    }
  }
}
```

改「到达后跳到哪个状态」就是把 `behaviorValue.statusId` 换掉，其余原样回传。

#### `POST /api/platform/Workflow/CopyWorkflowCurrentStep` — 复制步骤 ✅

```json
{
  "stepName": "复核（副本）",
  "stepCode": "shenhe_copy__c",
  "workflowConfigId": "<workflowConfigId>",
  "stepId": "<sourceStepId>"
}
```

响应 `data === true`（不是 `code:0` 就完事，代码里同时判了 `resp.data === true`）。复制后平台会给新步骤一个新 id，要拿它得再 `GetWorkflowSteps` 按 `stepCode` 找——接口本身不回新 id。

#### `DELETE /api/platform/Workflow/DeleteWorkflowStep` — 删步骤 ✅

```text
DELETE /api/platform/Workflow/DeleteWorkflowStep
```

```json
{ "id": "<stepId>" }
```

DELETE 带 body（`core.js` 的 `apiDelete` 支持 `opts.body`）。开始/结束步骤（`stepType` 10/20）删不掉；有实例跑过的工作流也不能删步骤。

#### `GET /api/platform/Workflow/GetWorkflowStepWithDetails` — 步骤详情（改步骤前必读） ✅

```text
GET /api/platform/Workflow/GetWorkflowStepWithDetails?id=<stepId>
```

响应 `data.step` 是完整 step 对象：任务步骤带 `stepTaskConfig` + `stepTaskApprovals`，判断步骤带 `stepDecision`，动作步骤带 `stepAction`，开始步骤带 `startUpControls`。三个 `UpdateWorkflow*Step` 的输入就是它改完回传。

#### `GET /api/platform/Workflow/GetWorkflowStepControls` — 开始步骤的控件 ✅

```text
GET /api/platform/Workflow/GetWorkflowStepControls?workflowConfigId=<workflowConfigId>&stepId=<startStepId>
```

响应 `data` 是控件数组（`controlConfig` + `participantConfig`）。老版 `api-reference.md` 把它记成 POST，实测 GET 可用；加任务步骤时 `taskAssign` 要的 `participantControlId` 就从这里取。

### 菜单（3）/ 通知（1）

#### `POST /api/platform/Menu/Submit` — 建菜单（母/子同一个接口） ✅

母菜单：

```json
{
  "source": 2,
  "menuType": 1,
  "backgroundColor": "#366EF4",
  "status": 1,
  "name": "变更管理",
  "code": "menu_change__c",
  "menuActionType": 3,
  "childShowType": null
}
```

子菜单（挂到母菜单下）：

```json
{
  "parentMenuId": "<menuId>",
  "source": 2,
  "name": "变更控制",
  "code": "menu_change_control__c",
  "status": 1,
  "menuActionType": 1,
  "objectId": "<objectId>",
  "listLayoutId": "<listLayoutId>",
  "layoutId": "<layoutId>",
  "createType": 4,
  "menuType": 1,
  "childShowType": null
}
```

- 响应 `data.Id`（大写 `I`，个别版本 `data.id`）；**子菜单必须建在已有母菜单下**，`parentMenuId` 先有。
- 母菜单 `menuActionType:3`（分组）、子菜单 `1`（对象列表）；`createType:4` 是「新建记录」入口类型。
- 建完用 `MenuGroup/QueryList` 回读确认层级，再 `Menu/Sort` 排顺序。

#### `POST /api/platform/Menu/Sort` — 菜单排序（body 就是数组） ✅

```json
[
  {
    "key": "<menuId>",
    "children": [
      { "key": "<childMenuId>", "parentId": "<menuId>", "children": [] }
    ]
  }
]
```

整棵树全量提交：没出现的菜单会被挪到末尾（不会删）。`key` 是菜单 UUID。

#### `POST /api/platform/MenuGroup/QueryList` — 菜单组列表 ✅

```json
{}
```

body 是空对象（不是空 body）。响应 `data[]` 为菜单组，含 `appId`、组名；建菜单时想指定分组就取这里的 `appId`。

#### `POST /api/platform/notice/GetMessageTemplateByObject` — 对象的消息模板 ✅

```json
{ "id": "<objectId>" }
```

请求体只有对象 id。响应 `data[]` 是模板数组：

```json
{
  "code": 0,
  "data": [
    {
      "templatetype": 1,
      "name": "变更审批提醒",
      "code": "tpl_change_approve",
      "source": 3,
      "objectid": "all",
      "title": "您有一条变更待审批",
      "jumplink": "/object/change_control__c/detail?id={!$recordId}",
      "content": "<p>{!$promoter} 提交了变更</p>",
      "sort": 0,
      "id": "<templateId>"
    }
  ]
}
```

`objectid:"all"` 表示通用模板；`content` 里是 `{!$...}` 变量与 HTML 片段。这条在抓包里出现 73 次，形状确定。

## 端到端最小链路（建对象 → 能提交审批）

一条从零到「页面上点提交能跑起工作流」的最短路径，按顺序调，每步都能回读验证：

| # | 动作 | 接口 |
| --- | --- | --- |
| 1 | 登录 | `POST /api/openapi/v1.0/Auth` → `data` |
| 2 | 建对象（`enableLifeCycle:false` 先不加生命周期） | `BasicObject/SaveBasicObject` |
| 3 | 反查 objectId | `GET BasicObject/{code}` |
| 4 | 建选项集（可选） | `ObjectPicklist/save` → `picklist/{code}` 取 `picklistId` |
| 5 | 建字段 | `BasicObject/SaveField`（`statisticsArrange` 必填） |
| 6 | 建工作流 | `Workflow/AddWorkflowBasic` → 记 `basicConfig.id` 与 `basicConfig.workflowBasicId` |
| 7 | 发布工作流 | `Workflow/ChangeWorkflowBasicStatus {ids:[configId],status:40,workflowBasicId}` |
| 8 | 加节点 | `GetWorkflowStepControls` 取参与者 → `AddWorkflowTaskStep` → `AddWorkflowDecisionStep` → `AddWorkflowActionStep` |
| 9 | 配开始步骤 | `UpdateWorkflowStartStep`（`nextStepId` 指向第一个节点） |
| 10 | 状态上挂「提交」按钮 | `lifecycle/UserAction/Create`（`behaviorValue.workflowId` = workflowBasicId） |
| 11 | 建列表布局 + 列 | `Listlayout/Save` → `Listlayout/AddColumns` |
| 12 | 建菜单 | `Menu/Submit`（母）→ `Menu/Submit`（子，带 objectId/layoutId/listLayoutId） |

顺序上**第 6 步要在第 10 步之前**（按钮要绑工作流），**第 8 步要在第 7 步之后**（草稿态加不了步骤），**第 2 步的 `enableLifeCycle` 决定第 10 步能不能做**（没生命周期就没有状态可挂）。

## 附：gws-mcp（MCP Server）能提供什么

`gws_mcp_stdio-v1.0.0`（`C:\Users\admin\Desktop\gws_mcp_stdio-v1.0.0.zip`）是官方盖亚网关的 stdio MCP Server，`egmp_gws_mcp.exe` + 三个环境变量：

| env | 含义 |
| --- | --- |
| `HOST` | 平台地址（如 `https://standard-val.aksoegmp.com`） |
| `APP_ID` / `APP_SECRET` | OpenAPI 应用凭证（平台侧申请） |
| `TIMEOUT` | 可选，默认 30 秒 |

启动后 OAuth 登录、token 落 `%USERPROFILE%\.gws-mcp\tokens\{host}.json`。`tools/list` 实测 **53 个工具**（README 写 51；平台没部署 OpenAPI v3.8.1 时只挂 41 个）：

| 领域 | 工具 |
| --- | --- |
| 对象实例 | `object_instance_{create,batch_create,update,batch_update,get,get_index,get_link,query,delete,set_lifecycle_status}` |
| AQL | `aql_query` / `aql_query_pages` / `aql_query_single` |
| 元数据 | `metadata_{object_get,object_query,object_fields,object_field_get,object_field_query,picklist_get,picklist_option_get}`、`object_get` / `object_type_get` / `object_type_list` |
| 生命周期 | `lifecycle_get` / `lifecycle_status_get` / `lifecycle_status_list` |
| 组织 | `org_sync_{users,departments,positions,user_departments,user_positions}` |
| 任务 | `user_task_{create,get,complete,remove}` |
| 组件 | `component_{metadata_list,metadata_get,instance_list,instance_get,instance_query,instance_query_by_paths,instance_save}` |
| 配置迁移 | `config_migration_{export,import,deploy}` |
| 附件/文档 | `attachment_{upload,download,instance_add,instance_list,instance_delete}`、`document_{upload,download}_format_copy` |
| 其他 | `custom_call` |

**关键限制**：这 53 个工具走的是 OpenAPI v3.8.1 业务面（实例 CRUD / AQL / 元数据 / 组织 / 任务），本文这 47 条是**管理端配置面**（`/api/platform/*`、`/api/config/*`），MCP 里没有对应工具——建对象、建字段、配布局、搭工作流这些做不了。而且工具入参多是**不透明的自由文本 `body`**（例：`aql_query` 的 schema 只有一个 `body`，说明写「AQL 参数 JSON，必含 sql，可选 paramters（键以 @ 开头）」），没有字段级 schema，也就没有示例可抄。

所以上面的示例来自三处，而不是 MCP：`akso-skill` 的实测 JS（`create-workflow-step.js`、`save-list-layout.js` 等）、2026-08-04 的前端抓包（136 条，覆盖 `notice` / `AddWorkflowTaskStep` / `AddWorkflowDecisionStep`）、以及本仓 `plugin/src/config/capabilities.ts` 的工具页预填参数（与引擎生成 plan 同源）。MCP 只适合拿来做「查实例 / 跑 AQL / 读元数据」的校验。

调用形状（stdio JSON-RPC，`tools/call` 的 `arguments` 里放 `body` 字符串）：

```json
{ "jsonrpc": "2.0", "id": 1, "method": "tools/call",
  "params": { "name": "aql_query",
              "arguments": { "body": "{\"sql\":\"select id, name from change_control__c limit 5\"}" } } }
```

## 枚举速查

**字段 `dataType`**：1 文本（maxLength 默认 255）· 2 数字（max/min/decimalPlaces）· 3 是/否 · 4 选项（picklistId）· 5 日期（`format:"YYYY-MM-DD"`）· 7 日期时间（`"YYYY-MM-DD HH:mm:ss"`）· 8 附件 · 13 查找（findRelationshipId/findSourceFieldId）· 14 公式（returnType/formulaScript）· 15 对象（referenceObjectId/objectRelation）· 16 父对象 · 17 长文本（建议 2000）· 18 富文本 · 23 对象多选

**工作流步骤 `stepType`**：10 开始（不可删）· 20 结束（不可删）· 30 审批任务 · 70 判断 · 90 动作

**工作流状态**：10 草稿 · 20 启用 · 30 禁用 · 40 编辑中（改步骤前必须先切到这里）

**其它**：审批分发 10 会签 / 20 或签；判断 `logicType` 40 所有都等于 / 41 至少一个等于 / 42 没有等于；菜单 `menuActionType` 3 容器（母）/ 1 对象链接（子）；布局 `source` 2 标准 / 3 自定义

**表单控件 `control.type`**：`AKInput` 文本·数字 / `AKSelectorSingle` 选项 / `AKSelectorMulti` 选项多选 / `AKSelectorObject` 对象·父对象 / `AKSelectorYesNo` 是/否（`attrs` 需加 `optional:[]`）/ `AKDateTime` 日期·日期时间 / `AKFile` 附件 / `AKRichTextEditor` 富文本

## 与本仓 `plugin/src/config/capabilities.ts` 的对照

能力目录已登记 22 条，覆盖本清单里的：认证登录、对象反查、对象/字段/选项集/生命周期状态的**查询**，对象、字段、选项集、生命周期状态的**写入**，表单布局 3 条、列表布局 6 条、菜单 3 条。

清单里有、能力目录里**还没有**的：

| 缺口 | 端点 |
| --- | --- |
| 工作流（18 条，整域空白；界面上「工作流处理」也是 0） | `Workflow/*` 全部 |
| 生命周期用户动作 | `UserAction/Create`、`LifestatusUserAction/Get` |
| 对象字段的两条管理端读法 | `BasicObject/FieldPage`、`BasicObject/FieldsAndOutField` |
| 通知模板 | `notice/GetMessageTemplateByObject` |
| OAuth（有意不接：`Auth` 那条够用） | `Oauth/Login`、`Oauth/RefreshToken` |

**三处口径不一致（2026-10-27 在 standard-val 实测过，结论见下）**：

| 能力 id | capabilities.ts 现在是 | 本清单是 | 实测 |
| --- | --- | --- | --- |
| `query.fields.list` | `GET /openapi/v3.6.4/metadatas/objects/{code}/fileds` | `GET /api/openapi/v1.0/BasicObject/field/{objectCode}` | 两套都通；v1.0 更快（59 ms vs 299 ms）、字段更全（92 键 vs 54 键，带 `id`/`picklistId`/内联 `picklist`） |
| `query.picklist.get` | `GET /openapi/v3.6.4/metadatas/picklists/{code}` | `GET /api/openapi/v1.0/BasicObject/picklist/{code}` | v3.6.4 通，**入参必须是选项集编码**（传 id / 字段编码一律 `code=0 + data:null`） |
| `query.lifecycle.status.list` | `GET /api/openapi/v1.0/BasicObject/lifecycleStatus/{objectCode}`（按 objectCode，**已改**） | `GET /api/openapi/v1.0/BasicObject/lifecycleStatus/{objectCode}`（按 objectCode） | 统一到 v1.0 这条：对象元数据只给 `lifeCycleId`（对象侧 id），v3.6.4 的 `lifecycle_status/{编码}` 只认生命周期编码、传 id 会 500「未找到对象生命周期」 |

v1.0 `lifecycleStatus/{objectCode}` 返回**裸数组**：`[{belongType,id,code,name,lifecycleId,lifecycleCode,lifecycleName}]`；对象没绑生命周期时是空数组（不是报错）。


## 写入报未授权但已生效（2026-10-27 实测）

`POST /api/platform/ObjectPicklist/save` 出现过两次「HTTP 200 + `code=401` + `message="Unauthorized"` + `traceId=null`，但数据已经写进平台」：写入固定耗时 145 ms 报错，随后回读却能看到刚提交的选项集。因此：

- 写入返回非 0 的 `code` **不等于没写**，不要在失败后盲目重试（会重复写）；
- akso-workbench 的做法：写完先回读，回读是期望态就记 `verified`，并在结果里写明"写入报错但已生效"；
- 别的写接口是否也这样**未验证**，按同样方式处理：先回读再下结论。
