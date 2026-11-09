# 基础 API 使用指南

akso-workbench「配置工具」页的接口卡片，文案就从这份文档里读：一句话介绍、输出结果、要点、以及**一条调用案例**。改完保存，刷新页面即可看到，不需要重新构建插件。

配套还有两份文档（各自负责不同的东西）：

- `标准配置使用规范.md`：一类配置怎么用接口一步步做出来（可以一个接口多条方案），卡片上的「标准配置用法」读它。
- `动作类型映射.md`：平台里那些数字编码（stepType、behaviorType、menuActionType …）的含义，卡片详情里的「动作类型映射」读它。

> 文档只负责**怎么讲清楚**这条接口；接口本身的 method / path / 参数名称 / 风险等级仍以 `plugin/src/config/capabilities.ts` 为唯一事实源。两边对不上时，先改目录、再补文档。

## 怎么读这份文档

- 每条接口一个三级标题：`### <capabilityId> <中文标题>`，id 必须与能力目录一致，否则卡片会标「指南里的 id 不在能力目录」。
- 标题下紧挨着的引用块（`>`）是卡片正面的**介绍**，两三句说清"它做什么、什么时候用它"。
- `**返回**` 是卡片里的**输出结果**：`data` 长什么样、要不要回读。
- `**要点**` 是坑与限制，列表写。
- `**案例**` 下面**只写一条**案例（`#### 案例 · …`）。案例正文里的 JSON 代码块是**调用参数对象**，代码块后面的散文是**预期结果**。需要多步骤的配置流程（审批节点、提醒节点等）不要堆成多条案例，写到 `标准配置使用规范.md`。

## 通用约定

- **鉴权**：令牌由底部账号条自动取（akso-cloud 里的账号口令换 eGMP 令牌），过期自动续。文档与案例里都不写令牌，也不写口令。
- **参数位置**：`path` 参数填进路径里的 `{xxx}`；`query` 参数拼查询串；其余整体作为请求体。GET 接口全部进 query。
- **响应信封**：`{ code: 0, data: … }`。`code !== 0` 时看 `message`；查询类 `data` 是对象或数组，写入类多半是 `true`。
- **id 要回读**：对象、字段、布局、流程的 id 先用查询接口反查，不要把平台生成值（FullPath、traceId、时间戳）当入参。
- **写入没有回滚**：不可逆操作（建对象时开状态机、删流程、覆盖全量布局）先在验证环境跑一遍，再去生产。
- **风险等级**描述的是平台侧影响，不等于审批级别；所有写入都要过对话里的审批窗。

## 接口章节

<!-- 下面由 `node plugin/scripts/api-guide.mjs --seed` 生成骨架，之后手改。 -->

## 对象（补充）

### object.save 新建 / 更新对象

> 按 code upsert 一个自定义对象；enableLifeCycle 决定它是否带状态机。

**返回**：data: true（不返回 objectId，需用 query.object.get 反查）。

**要点**

- code 必须 __c 结尾。
- enableLifeCycle=true 之后不能改回 false。
- 新建对象不会自带字段与布局。

**案例**

#### 案例 · 基础调用

```json
{
  "name": "变更控制",
  "code": "change_control__c",
  "source": 3,
  "status": 1,
  "objectClass": 1,
  "enableLifeCycle": true,
  "enableSignatures": false,
  "summaryFields": []
}
```

### object.field.page 对象字段分页查询

> 按 objectId 分页列字段，带总条数，适合驱动字段选择器。

**返回**：data 是分页对象：count / datas[] / pageIndex / pageSize / pageCount / rows（不是 items/totalCount）。

**案例**

#### 案例 · 基础调用

```json
{
  "objectId": "<objectId>",
  "pageIndex": 1,
  "pageSize": 50,
  "filters": {}
}
```

### object.fields.outfield 字段 + 关联对象字段

> 取本对象字段以及可跨对象引用的外部字段（列表布局跨对象列要用）。

**返回**：data 为字段数组（实测 206 条）：本对象字段 + 可跨对象引用的外部字段（fieldPath 形如 obj_field__c.xxx__c）。

**要点**

- 参数名必须是 objectId；传 basicObjectId 平台回「Value cannot be null. (Parameter 'objectId')」。

**案例**

#### 案例 · 基础调用

```json
{
  "objectId": "<objectId>"
}
```

### query.object.get 按 code 取对象

> 拿对象 id / 生命周期 id，写入前几乎都要先来这一步。

**返回**：data.id / data.lifeCycleId / data.enableLifeCycle。

**要点**

- 对象不存在时是业务错误（code≠0），不是 HTTP 404。

**案例**

#### 案例 · 基础调用

```json
{
  "code": "change_control__c"
}
```

## 字段（补充）

### query.fields.list 按对象取字段

> 列字段全量（含 dataType、picklistId）；只要单个字段时把编码拼在路径末尾。

**返回**：data 为字段裸数组：id / name / code / dataType / picklistId …

**要点**

- 取单个字段：`GET /api/openapi/v1.0/BasicObject/field/{objectCode}/{fieldCode}`，无需额外参数。
- 旧版 v3.6.4 的 /metadatas/objects/{code}/fileds 返回更少，已弃用。

**案例**

#### 案例 · 基础调用

```json
{
  "objectCode": "change_control__c"
}
```

### field.save 新建 / 更新字段

> 按 code upsert 字段；dataType 决定附加参数（选项、关联、日期格式…）。

**返回**：data: true（不返回 fieldId）。

**要点**

- statisticsArrange 必填，最容易漏。
- dataType=4 要 picklistId（从 query.picklist.get 的 objectPicklistId 取）；=15 要 referenceObjectId + objectRelation。
- dataType=15 平台强制 isRequired=true，=16 强制 false。
- **dataType=14 是公式，不是多行文本**。默认用 1（文本），也**只用 1**：长文本 17、富文本 18 已禁用（2026-11-09 管理员规则，不再新建）；公式 14 仅在值由其它字段算出时才用。

**案例**

#### 案例 · 基础调用

```json
{
  "name": "编号",
  "code": "no__c",
  "dataType": 1,
  "objectId": "<objectId>",
  "isRequired": false,
  "invisible": false,
  "disabled": false,
  "statisticsArrange": {
    "id": 1
  }
}
```

## 选项集（补充）

### query.picklist.get 按 code 取选项集

> 拿选项集里的全部选项；返回的 objectPicklistId 就是字段的 picklistId。

**返回**：data 为选项数组（name / code / objectPicklistId / status …）。

**要点**

- 传入字段编码或字段 id 会得到 data:null。

**案例**

#### 案例 · 基础调用

```json
{
  "code": "change_type__c"
}
```

### picklist.save 新建 / 追加选项集

> 按 code upsert 选项集；同名 code 是更新，选项只增不删。

**返回**：data: true（不返回 picklistId，要用 query.picklist.get 反查）。

**要点**

- 同 code 即更新；已有选项只增不删，删选项得另找接口。
- 实测会 HTTP 200 + code 401 但已落库——写完必须回读确认。
- code 省略会由平台生成，后续无法引用，务必显式给。

**案例**

#### 案例 · 基础调用

```json
{
  "name": "变更类型",
  "code": "change_type__c",
  "options": [
    {
      "name": "重大变更",
      "code": "major__c",
      "status": 1,
      "operations": 1,
      "sort": 1
    },
    {
      "name": "一般变更",
      "code": "minor__c",
      "status": 1,
      "operations": 1,
      "sort": 2
    }
  ]
}
```

## 表单布局（补充）

### layout.list 对象布局列表

> 列出对象下所有表单布局，从中找默认布局（source=2）。

**返回**：data.datas[]：id / name / code / source / isMain / objectTypeId。

**案例**

#### 案例 · 基础调用

```json
{
  "basicObjectId": "<objectId>",
  "pageIndex": 1,
  "pageSize": 20
}
```

### layout.detail 布局详情

> 读一份布局的 sections / controls / pages；写入前必须读，pages 要原值回传。

**返回**：data.sections / data.controls / data.pages / data.rules …

**要点**

- 改布局是"读全量 → 改 → 全量写回"，跳过这步会把 pages 写丢。

**案例**

#### 案例 · 基础调用

```json
{
  "layoutId": "<layoutId>"
}
```

### layout.stable.ids 换稳定组件 id

> 把要新建的 section / control 的临时 id 换成平台稳定 id。布局里新增分区的 id 不是随便编的 uuid：平台按「对象 + 布局 + 组件类型 + code」算稳定 id，不经这一步，`layout.save` 必报「[layout_section] BelongId 不正确，该值必须由 FullPath 生成」。

**返回**：`data.stableComponentIds`，`{ 临时uuid: 稳定uuid }`，键与入参一一对应。

**要点**

- `componentType` 只有两种：`layout_section`（分区）/ `layout_control`（控件）。
- 稳定 id 由「对象 + 布局 + 组件类型 + 组件 code」决定，同样入参永远得到同一个 id，可重复调用、可与已有布局比对。
- 一次可批量传多个，分区和控件混在一批里换完，再一次 `layout.save` 写回。
- 已有分区/控件不用换：直接按 `layout.detail` 读回的 id 原样回传。

**案例**

#### 案例 · 分区 + 控件一次换完

```json
{
  "componentIds": {
    "3f0a0000-0000-0000-0000-000000000001": {
      "objectId": "70a49df0-a5b9-2ac8-ff9c-f37758df3381",
      "layoutCode": "homework_base_layout__ak",
      "componentType": "layout_section",
      "componentCode": "basic_info__cs"
    },
    "3f0a0000-0000-0000-0000-000000000002": {
      "objectId": "70a49df0-a5b9-2ac8-ff9c-f37758df3381",
      "layoutCode": "homework_base_layout__ak",
      "componentType": "layout_control",
      "componentCode": "name__ak"
    }
  }
}
```

### layout.save 保存布局（全量替换）

> 整份布局写回；没带上的 section/control 会被删掉。写入前先读 layout.detail，新分区/控件的 id 先经 layout.stable.ids 换稳定 id，改完整份回传。

**返回**：data: 布局 id（字符串，不是 true）。

**要点**

- 全量替换：没带上的 section/control 会被删；写入前先 layout.detail 读全量，改完整份回写。
- 新建 section 只需 `{ id, code, name, type, columnsNum }`；新建 control 只需 `{ id, sectionId, code, name, fieldId, type, gridSpan, attrs }`。id 用新 UUID。
- **不要带 `belongId` / `fullPath`**：带上必报「[layout_section] BelongId 不正确，该值必须由 FullPath 生成」；实测请求体里没有这两个字段。
- 新分区 / 新控件的 `id` 必须先经 `layout.stable.ids` 换成稳定 id；直接写临时 uuid 同样报 BelongId 那句，换完同一个 body 一次通过。
- 已有分区要按 `layout.detail` 读回的原样整份回传（90+ 键，含 `pageId`、`relationFilter`、`listlayoutId` 等），压成最小对象会 400；只有新建的才用最小对象。
- 旧 section（如 type=4 工作流时间线）原样回传，不能删；pages 原样回传，没有分页传 []。
- 2026-11-09 抓包验证：给 akwv1109__c 的默认布局加「基础信息」分区 + 4 个控件（名称 / 文本 / 数字 / 单选），200 / 176ms、code 0；回读 LayoutDetail 确认 sections=2、controls=4。

**案例**

#### 案例 · 保真回写（先读后写）

> 用 `layout.detail` 拿到的 `data` 原样回传，一个字段都不改。这是最安全的写法，也是刚接通账号时确认写入链路可用的第一次调用。

```json
{
  "id": "<layoutId>",
  "objectId": "<objectId>",
  "objectTypeId": "<objectTypeId>",
  "name": "默认布局",
  "code": "<layoutCode>",
  "source": 2,
  "status": 1,
  "isMain": true,
  "labelCol": 0,
  "sections": [
    {
      "id": "<sectionId>",
      "code": "basic_info__cs",
      "name": "基础信息",
      "type": 1,
      "columnsNum": 2
    }
  ],
  "controls": [
    {
      "id": "<controlId>",
      "sectionId": "<sectionId>",
      "code": "name__ak",
      "name": "名称",
      "fieldId": "<fieldId>",
      "type": "AKInput",
      "gridSpan": 1,
      "attrs": {
        "readPrettyShowType": "1"
      }
    }
  ],
  "pages": [],
  "rules": [],
  "activePageCode": null,
  "activeSectionCode": null,
  "formEditStatus": 0,
  "skipRequiredValidation": null
}
```

预期：`{ "code": 0, "data": "<layoutId>" }`；随后 `layout.detail` 回读，sections / controls / pages 与写入前一致（这轮只证明链路通，不改布局）。

## 列表布局（补充）

### listlayout.list 列表布局列表

> 列出对象下的表格视图，默认视图的 code 形如 {对象code}_table__ak。

**返回**：data[]：id / name / code / source。

**案例**

#### 案例 · 基础调用

```json
{
  "objectId": "<objectId>"
}
```

### listlayout.columns.get 读列表列

> 读出当前列配置，改列前先读，避免把别的列写没。

**返回**：data[]：fieldId / fieldPath / sort。

**案例**

#### 案例 · 基础调用

```json
{
  "listlayoutId": "<listlayoutId>"
}
```

### listlayout.save 新建 / 更新列表布局

> 建一个表格视图，或用同 code 更新它的名称与排序字段。

**返回**：data.id（个别版本直接返回 id 字符串）。

**案例**

#### 案例 · 基础调用

```json
{
  "status": 1,
  "source": 3,
  "sortFields": [],
  "name": "全部变更",
  "code": "change_control__c_all__ak",
  "objectTypeId": "<objectTypeId>",
  "objectId": "<objectId>"
}
```

### listlayout.columns.set 保存列表列（全量替换）

> 整份列配置写回；跨对象列用 fieldPath 点号路径。

**返回**：data: true。

**要点**

- 全量替换，没带上的列会消失。
- fieldId 始终填目标字段自己的 UUID（不是关联对象字段的）。
- 可选字段从 object.fields.outfield 取。

**案例**

#### 案例 · 基础调用

```json
{
  "listlayoutId": "<listlayoutId>",
  "listlayoutColumns": [
    {
      "fieldId": "<fieldId-of-no__c>",
      "fieldPath": "no__c",
      "sort": 1
    },
    {
      "fieldId": "<fieldId-of-applicant__c>",
      "fieldPath": "obj_field__c.applicant__c",
      "sort": 2
    }
  ]
}
```

### listlayout.filter.save 保存列表筛选

> 给列表布局写一条筛选；details 是条件，relations 是条件间与或。

**返回**：data: null（实测不返回新筛选 id；带 id 时为覆盖更新）。

**要点**

- logicType：1 等于 / 4 晚于 / 10 为空 / 11 不为空（未穷尽）。
- 值要自带引号，日期写成 "\"2026-07-01\""；当前用户用 {!current.user}。
- logicalOperator：1 = AND，2 = OR。
- ⚠️ relationId / details[].id / relations[].id 必须是 GUID 形态；写 "r1" 这类短名会 HTTP 400「One or more validation errors occurred.」（2026-11-09 实测）。

**案例**

#### 案例 · 基础调用

```json
{
  "belongId": "<listlayoutId>",
  "details": [
    {
      "id": "<detailGuid>",
      "relationId": "<relationGuid>",
      "targetType": 1,
      "targetId": "<fieldId>",
      "targetCode": "change_date__c",
      "logicType": 4,
      "logicValue": "\"2026-07-01\"",
      "targetFieldDataType": 5,
      "index": 1
    }
  ],
  "relations": [
    {
      "id": "<relationGuid>",
      "logicalOperator": 1,
      "index": 1
    }
  ]
}
```

### listlayout.filter.get 读列表筛选

> 按列表布局取筛选条件，用于"读改写"。

**返回**：data 是对象：belongId / conditionName / sceneType / failPrompt / details[] / relations[]。没存过筛选时平台回 502「数据不存在」，不是空数组。

**要点**

- 参数名是 listlayoutId；传 belongId 平台回「数据不存在」。

**案例**

#### 案例 · 基础调用

```json
{
  "listlayoutId": "<listlayoutId>"
}
```

## 生命周期（补充）

### query.lifecycle.status.list 按对象取生命周期状态

> 列出对象状态机里的全部状态，配置动作和判断节点都要用。

**返回**：data 为状态裸数组：belongType / id / code / name / lifecycleId / lifecycleCode …

**要点**

- 对象未绑定生命周期时返回空数组。

**案例**

#### 案例 · 基础调用

```json
{
  "objectCode": "change_control__c"
}
```

### lifecycle.status.create 新建生命周期状态

> 给对象的生命周期加一个状态节点，返回新状态 id。

**返回**：data 就是新状态的 UUID。

**要点**

- workflowCancelStatus 固定 11515107-dce6-4ef1-8d51-b1ffed22040f。

**案例**

#### 案例 · 基础调用

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

### lifecycle.status.update 更新生命周期状态

> PATCH 全量字段；只改一个字段也要把整份带上。

**返回**：data: true。

**要点**

- 全量更新：漏字段会被平台按默认值覆盖，先 Get 再改。

**案例**

#### 案例 · 基础调用

```json
{
  "id": "<statusId>",
  "name": "草稿（改）",
  "code": "status_draft__c",
  "lifecycleId": "<lifecycleId>",
  "isEnabled": true,
  "isDeleted": false,
  "isRecordDeactivate": false,
  "description": "",
  "source": 3,
  "sort": 1,
  "workflowCancelStatus": "11515107-dce6-4ef1-8d51-b1ffed22040f"
}
```

### lifecycle.useraction.create 新建状态按钮（含动作）

> 在某个状态上挂一个按钮，动作可绑定"提交流程"。每调一次都会新建一个按钮。

**返回**：data 为新按钮 UUID。

**要点**

- behaviorValue.workflowId 要填 workflowBasicId，不是 workflowConfigId——混用会报 500。
- 该接口不做合并：每调一次就多一个按钮，重跑前先 LifestatusUserAction/Get 查重。

**案例**

#### 案例 · 基础调用

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
      "code": "submit",
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
          },
          "sort": 0
        }
      ]
    }
  ]
}
```

### lifecycle.useraction.get 读状态上的按钮

> 列出某状态已有的按钮动作，用于查重与读改写。

**返回**：data 是对象：{ objectId, lifecycleId, statusId, roles[] }，不是按钮数组。

**案例**

#### 案例 · 基础调用

```json
{
  "statusId": "<statusId>"
}
```

## 工作流（补充）

### workflow.create 新建工作流

> 绑定到对象（可再绑生命周期）建一条流程，返回 configId 与 workflowBasicId。

**返回**：data.basicConfig.id（= workflowConfigId）与 data.basicConfig.workflowBasicId（两个 id 别混用）。

**要点**

- status=10 草稿；加步骤前必须先切到 40 编辑中。

**案例**

#### 案例 · 基础调用

```json
{
  "basicConfig": {
    "name": "变更审批流",
    "code": "change_approval",
    "status": 10,
    "objectId": "<objectId>",
    "lifecycleId": "<lifecycleId>",
    "workflowType": 10,
    "isSingleRecord": true
  },
  "variables": []
}
```

### workflow.get 读工作流配置

> 按 workflowConfigId 取 basicConfig 与变量，改工作流前先读。

**返回**：data 就是 basicConfig 本体（扁平）：name / code / status / workflowBasicId / lifecycleId / lifecycleStatusIds / versionNo…，没有 data.basicConfig 这一层。

**要点**

- 实测：响应没有 variables 字段；改配置时把整个 data 改完再 POST 回 UpdateWorkflowBasic。

**案例**

#### 案例 · 基础调用

```json
{
  "id": "<workflowConfigId>"
}
```

### workflow.update 更新工作流

> 全量写回 basicConfig（含 versionNo 乐观锁）与变量。

**返回**：data: true。

**要点**

- versionNo 不是最新会失败；先 Get 再改。

**案例**

#### 案例 · 基础调用

```json
{
  "basicConfig": {
    "id": "<workflowConfigId>",
    "name": "变更审批流（改）",
    "code": "change_approval",
    "status": 40,
    "objectId": "<objectId>",
    "lifecycleId": "<lifecycleId>",
    "workflowType": 10,
    "isSingleRecord": true,
    "versionNo": 1,
    "workflowBasicId": "<workflowBasicId>"
  },
  "variables": []
}
```

### workflow.page.list 工作流分页列表

> 分页列工作流，可按名称过滤。

**返回**：data 是分页对象：count / datas[] / pageCount / rows…（实测 888 条工作流）。

**案例**

#### 案例 · 基础调用

```json
{
  "pageIndex": 1,
  "filters": [],
  "pageSize": 20
}
```

### workflow.status.change 发布 / 停用工作流

> 切换工作流状态；加步骤前必须切到 40（编辑中）。

**返回**：data: true。

**要点**

- 顺序：10 建 → 40 编辑（加步骤）→ 20 启用。

**案例**

#### 案例 · 基础调用

```json
{
  "ids": [
    "<workflowConfigId>"
  ],
  "status": 40,
  "workflowBasicId": "<workflowBasicId>"
}
```

### workflow.delete 删除工作流

> 删除整条流程配置（连同步骤）。

**返回**：data 为被删掉的 workflowConfigId。

**要点**

- 必须用 DELETE 且 body 带 {id}；用 POST 会 405（2026-11-09 在 standard-val 实测）。
- 只有草稿(10)能删：状态 20 报「禁止删除启用状态的流程」、30 报「禁止删除禁用状态的流程」；发布后无法退回草稿，等于删不掉。
- 不可恢复。

**案例**

#### 案例 · 基础调用

```json
{
  "id": "<workflowConfigId>"
}
```

### workflow.steps.list 读工作流步骤

> 列出流程里的全部步骤（含系统开始/结束节点）。

**返回**：data[]：id / code / name / stepType / sort / nextStepId …

**案例**

#### 案例 · 基础调用

```json
{
  "workflowConfigId": "<workflowConfigId>"
}
```

### workflow.task.add 加审批节点

> stepType=30 的任务节点：参与者、会签/或签、审批按钮都在这里配。

**返回**：data.step（含新 stepId）。

**要点**

- stepTaskConfig 的 16 个键漏一个都可能报 1000，按文档整份抄。
- isAssignToPromoter=true 是发起人审批；指定参与者要给 taskAssign（= userControlIds）。
- dispatchMode：10 会签 / 20 或签。
- 按钮字段名注意拼写：isShowResons / resonTitle / responsibieTitle 都是平台原样。

**案例**

#### 案例 · 基础调用

```json
{
  "step": {
    "nextStepId": "<nextStepId>",
    "name": "部门审批",
    "code": "step_dept_approve",
    "stepType": 30,
    "workflowConfigId": "<workflowConfigId>",
    "stepTaskConfig": {
      "taskNodeType": 0,
      "preTaskIds": "",
      "reciverType": 20,
      "notifyType": 0,
      "selectStatusTypeIds": "",
      "externalSignatureInfoFrom": 1,
      "name": "部门审批",
      "taskAssign": "<participantConfigId>",
      "isAssignToPromoter": false,
      "dispatchMode": 10,
      "taskRequireType": 20,
      "isRequireApproval": true,
      "approvalLabel": "审批意见",
      "pageOpenType": 0,
      "breakApproveIds": ""
    },
    "stepTaskShareSettingRules": [],
    "stepTaskNotifies": [],
    "stepTaskApprovals": [
      {
        "id": "<buttonId>",
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
        "responsibleOptions": [
          {
            "id": "<optionId>",
            "value": "同意"
          }
        ]
      }
    ]
  }
}
```

### workflow.decision.add 加判断节点

> stepType=70：按条件分支，每条规则一个 nextStepId，另给 else 分支。

**返回**：data.step。

**要点**

- targetType=7 表示"上一步的按钮"；logicType 40/41 对应按钮的两种结果。
- logicValue 是带双引号的 UUID 字符串，写成裸 UUID 会不生效。
- rules 里的 nextStepId 是"满足时"，stepDecision.nextStepId 是 else。

**案例**

#### 案例 · 基础调用

```json
{
  "step": {
    "nextStepId": "",
    "name": "是否重大变更",
    "code": "step_decision_major",
    "stepType": 70,
    "workflowConfigId": "<workflowConfigId>",
    "stepDecision": {
      "rules": [
        {
          "id": "<ruleId>",
          "sort": 0,
          "name": "规则1",
          "condition": {
            "details": [
              {
                "id": "<detailId>",
                "index": 1,
                "relationId": "<relationId>",
                "targetType": 7,
                "targetId": "<prevStepId>",
                "targetCode": "step_dept_approve",
                "logicType": 40,
                "logicValue": "\"<buttonId>\""
              }
            ],
            "relations": [
              {
                "id": "<relationId>",
                "logicalOperator": 1,
                "index": 1
              }
            ]
          },
          "nextStepId": "<yesNextStepId>"
        }
      ],
      "nextStepId": "<elseNextStepId>"
    }
  }
}
```

### workflow.action.add 加动作节点

> stepType=90：自动执行动作，比如把状态改成某个值、或触发另一条流程。

**返回**：data.step。

**要点**

- behaviorType 9 = 改生命周期状态（behaviorValue.statusId）；7 = 触发工作流（behaviorValue.workflowId 填 workflowBasicId）。

**案例**

#### 案例 · 基础调用

```json
{
  "step": {
    "nextStepId": "<nextStepId>",
    "name": "置为已关闭",
    "code": "step_close",
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
            {
              "behaviorType": 9,
              "behaviorValue": {
                "statusId": "<statusId>"
              },
              "sort": 0
            }
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

### workflow.start.update 配置开始节点

> 开始节点的表单控件：谁能在发起时选人、必填与否。整份 step 覆盖写入。

**返回**：data: true。

**要点**

- controlConfig.value 是 participantConfig 的 JSON 字符串（字符串，不是对象）。
- 全量覆盖：先 GetWorkflowStepControls 读回再加。

**案例**

#### 案例 · 基础调用

```json
{
  "step": {
    "id": "<startStepId>",
    "code": "start",
    "name": "开始",
    "stepType": 10,
    "isEnable": true,
    "workflowConfigId": "<workflowConfigId>",
    "sort": 0,
    "remark": "",
    "stepStartConfig": {},
    "stepRuleConfig": [],
    "nextStepList": [],
    "nextStepId": "<firstStepId>",
    "nextStepCode": "step_dept_approve",
    "nextStepName": "部门审批",
    "startUpControls": [
      {
        "id": "<controlId>",
        "name": "审批人",
        "sort": 0,
        "controlConfig": {
          "ctrlType": 20,
          "code": "approver",
          "stepId": "<startStepId>",
          "workflowConfigId": "<workflowConfigId>",
          "sort": 0,
          "id": "<controlId>",
          "name": "审批人",
          "isRequired": false,
          "value": "{\"name\":\"审批人\",\"selectUserType\":10,\"allowRoleIds\":[]}",
          "dispatchMode": 10,
          "taskRequireType": 20,
          "isHidden": false,
          "stepTaskId": null,
          "remark": null
        },
        "participantConfig": {
          "name": "审批人",
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

### workflow.task.update 改审批节点

> 先 GetWorkflowStepWithDetails 读整份，改完整个 step 写回。

**返回**：data: true。

**要点**

- stepTaskApprovals 传 [] 会删光按钮，改按钮时保留原数组。

**案例**

#### 案例 · 基础调用

```json
{
  "step": {
    "id": "<stepId>",
    "name": "部门审批（改）",
    "code": "step_dept_approve",
    "stepType": 30,
    "workflowConfigId": "<workflowConfigId>",
    "stepTaskConfig": {
      "name": "部门审批（改）",
      "isAssignToPromoter": true,
      "dispatchMode": 20
    },
    "stepTaskApprovals": []
  }
}
```

### workflow.decision.update 改判断节点

> 同上，读回 stepDecision 后整份写回。

**返回**：data: true。

**要点**

- rules 传 [] 等于清空全部分支。

**案例**

#### 案例 · 基础调用

```json
{
  "step": {
    "id": "<stepId>",
    "name": "是否重大变更（改）",
    "code": "step_decision_major",
    "stepType": 70,
    "workflowConfigId": "<workflowConfigId>",
    "stepDecision": {
      "rules": [],
      "nextStepId": "<elseNextStepId>"
    }
  }
}
```

### workflow.action.update 改动作节点

> 读回 stepAction 后整份写回。

**返回**：data: true。

**要点**

- actions 传 [] 会清空动作。

**案例**

#### 案例 · 基础调用

```json
{
  "step": {
    "id": "<stepId>",
    "name": "置为已关闭（改）",
    "code": "step_close",
    "stepType": 90,
    "workflowConfigId": "<workflowConfigId>",
    "stepAction": {
      "belongType": 3,
      "actions": []
    }
  }
}
```

### workflow.step.copy 复制步骤

> 复制一个已有步骤（连同配置），常用于批量建审批节点。

**返回**：data: true（不返回新 stepId，需再查 GetWorkflowSteps 按 code 找）。

**案例**

#### 案例 · 基础调用

```json
{
  "stepName": "部门审批副本",
  "stepCode": "step_dept_approve_copy",
  "workflowConfigId": "<workflowConfigId>",
  "stepId": "<stepId>"
}
```

### workflow.step.delete 删除步骤

> 删除一个非系统步骤。

**返回**：data: true。

**要点**

- stepType 10/20 是系统开始/结束节点，删不掉。

**案例**

#### 案例 · 基础调用

```json
{
  "id": "<stepId>"
}
```

### workflow.step.get 读步骤详情

> 取单个步骤的完整配置，更新前必须先读。

**返回**：data.step（完整 step，可直接改后回传）。

**案例**

#### 案例 · 基础调用

```json
{
  "id": "<stepId>"
}
```

### workflow.controls.list 读步骤表单控件

> 取某步骤的动态控件配置（开始节点、审批节点的表单字段）。

**返回**：data 为控件数组。

**要点**

- 文档旧版记成 POST，实测是 GET。

**案例**

#### 案例 · 基础调用

```json
{
  "workflowConfigId": "<workflowConfigId>",
  "stepId": "<startStepId>"
}
```

## 菜单（补充）

### menu.submit 新建 / 更新菜单

> 母菜单（menuActionType=3）建分组，子菜单（=1）挂对象与两套布局；先建母菜单拿 id，再建子菜单。

**返回**：data.Id（大写 I，个别版本 data.id）。

**要点**

- 母菜单 body：{source:2, menuType:1, backgroundColor:"#366EF4", status:1, name, code, menuActionType:3, childShowType:null}；顶层母菜单 `parentMenuId` 留空。
- 子菜单必须先有 parentMenuId，且对象/布局 id 都要有效；三件套是 `objectId` + `listLayoutId`（列表布局）+ `layoutId`（表单布局），再带 `createType:4`。
- 返回的 id 要自己用 `GET /api/platform/Menu/GetMenuList` 读回来；引擎的 call 参数不做 id 插值，母菜单 → 子菜单必须拆成两次 plan。

**案例**

#### 案例 · 基础调用

```json
{
  "parentMenuId": "<parentMenuId>",
  "source": 2,
  "name": "变更控制",
  "code": "menu_change_control",
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

### menu.sort 菜单排序（全量）

> 整棵菜单树按给定顺序重排；body 就是数组。

**返回**：data: true。

**要点**

- 全量替换：没出现在树里的菜单会被移出当前层级。
- 引擎把整个数组当 body 发送（不是包一层 params）。

**案例**

#### 案例 · 基础调用

```json
[
  {
    "key": "<menuId>",
    "children": [
      {
        "key": "<childMenuId>",
        "parentId": "<menuId>",
        "children": []
      }
    ]
  }
]
```

### menu.group.list 菜单分组列表

> 列出菜单分组（母菜单）。

**返回**：data 为分组数组（实测 6 个）：name / code / status / source / sort / id。

**案例**

#### 案例 · 基础调用

```json
{}
```

## 通知（补充）

### notice.template.list 按对象取通知模板

> 列出对象可用的消息模板（标题/内容/跳转链接），配置通知时用。

**返回**：data 为模板数组（实测 95 条）：templatetype / name / code / title / jumplink / content / sort。

**要点**

- content 里是 {!$...} 变量与 <a href> 片段，直接可用。

**案例**

#### 案例 · 基础调用

```json
{
  "id": "<objectId>"
}
```
