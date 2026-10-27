# Akso eGMP 平台内部 API 清单（可检索摘要）

原表是同目录的 [Akso平台内部API接口清单.xlsx](./Akso平台内部API接口清单.xlsx)（2026-10-27，前端抓包整理）。本文件是它的**检索用摘要**：表格形式便于 grep 和 diff，原表的「抓包次数 / 来源」两列在这里省略。两者不一致时以原表为准。

| 项目 | 内容 |
| --- | --- |
| 覆盖 | 47 个端点：对象 / 字段 / 选项集 / 表单布局 / 列表布局 / 生命周期 / 工作流 / 菜单 / 通知 / 认证 |
| 目标环境 | `https://standard-val.aksoegmp.com`（验证租户）；下表路径均为主机后的相对路径 |
| 版本基线 | 文档按平台 3.7.x 编写，实测验证环境产品版本 3.8.2.0，接口均可调通 |
| 数据来源 | akso-skill 的 `api-reference.md`（管理端抓包 + `lib/api` 源码）、各 JS 模块实际调用、`monitor-log.json` 136 条抓包 |
| 风险 | 结论来自验证环境；不可逆操作（启用生命周期、删对象/字段/工作流、菜单全量排序）先用验证环境复跑 |

## 通用约定

- **认证**：`POST /api/openapi/v1.0/Auth {account, password}` 换 JWT，响应 `data.token` 即 Bearer；约 55 分钟过期，401 时重新登录。之后所有 `/api/platform/*`、`/api/config/*` 都带 `Authorization: Bearer <token>`。
- **信封**：统一 `{code, errorCode, message, data}`；`code=0` 成功，`1000` 参数校验错误，`401` 未授权，`403` 无权限，`500` 业务错误。HTTP 200 也可能带非 0 的 `code`。
- **命名陷阱**：`AddColumns`、`Menu/Sort`、`SaveLayoutDetail` 名字里带 Add/Save，语义都是**全量替换**——不在数组里的东西会被删掉。

## 接口总表

### 认证与登录（3）

| 方法 | 路径 | 用途 | 要点 |
| --- | --- | --- | --- |
| POST | `/api/openapi/v1.0/Auth` | 账号密码登录换 JWT | `data.token` = Bearer，约 55 分钟过期 |
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
| POST | `/api/platform/ObjectPicklist/save` | 创建/更新选项集 | `options[{name,code,status,operations,sort}]`；响应**不返回 picklistId**，需反查 |

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
| POST | `/api/config/power/LifestatusUserAction/Get` | 查状态已绑定的用户动作 | 绑定前先查，追加后 PATCH 全量提交，别覆盖已有动作 |

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
| POST | `/api/platform/Workflow/CopyWorkflowCurrentStep` | 复制当前步骤 | 参数未收录 ⚠️ |
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

**三处口径不一致，接之前要先验**：

| 能力 id | capabilities.ts 现在是 | 本清单是 |
| --- | --- | --- |
| `query.fields.list` | `GET /openapi/v3.6.4/metadatas/objects/{code}/fileds` | `GET /api/openapi/v1.0/BasicObject/field/{objectCode}` |
| `query.picklist.get` | `GET /openapi/v3.6.4/metadatas/picklists/{code}` | `GET /api/openapi/v1.0/BasicObject/picklist/{code}` |
| `query.lifecycle.status.list` | `GET /openapi/v3.6.4/metadatas/lifecycle_status/{lifecycleId}`（按 lifecycleId） | `GET /api/openapi/v1.0/BasicObject/lifecycleStatus/{objectCode}`（**按 objectCode**） |

前两条都是「读元数据」，两套路径可能都可用；第三条不只是路径不同，**入参语义也不一样**（lifecycleId vs objectCode）——接生命周期规划前必须在验证环境确认哪套对，别照抄。
