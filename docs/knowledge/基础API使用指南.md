# 基础 API 使用指南

akso-workbench「配置工具」页的接口卡片，文案就从这份文档里读：一句话介绍、输出结果、要点、以及**一条调用案例**。改完保存，刷新页面即可看到，不需要重新构建插件。

配套还有三份文档（各自负责不同的东西）：

- `标准配置使用规范.md`：一类配置怎么用接口一步步做出来（可以一个接口多条方案），卡片上的「标准配置用法」读它。
- `系统常用映射表.md`：平台里那些数字编码（stepType、behaviorType、menuActionType …）的含义，卡片详情里的「系统常用映射表」读它。
- `标准配置思路规范.md`：接到配置任务时先读——蓝图要答哪 8 个问题、六张表怎么填、施工顺序、一次审批还是多次、改错了怎么停用重建。不进卡片。

> 文档只负责**怎么讲清楚**这条接口；接口本身的 method / path / 参数名称 / 风险等级仍以 `plugin/src/config/capabilities.ts` 为唯一事实源。两边对不上时，先改目录、再补文档。

## 怎么读这份文档

- 每条接口一个三级标题：`### <capabilityId> <中文标题>`，id 必须与能力目录一致，否则卡片会标「指南里的 id 不在能力目录」。
- 标题下紧挨着的引用块（`>`）是卡片正面的**介绍**，两三句说清"它做什么、什么时候用它"。
- `**返回**` 是卡片里的**输出结果**：`data` 长什么样、要不要回读。
- `**要点**` 是坑与限制，列表写。
- `**案例**` 下面**只写一条**案例（`#### 案例 · …`）。案例正文里的 JSON 代码块是**调用参数对象**，代码块后面的散文是**预期结果**。需要多步骤的配置流程（审批节点、提醒节点等）不要堆成多条案例，写到 `标准配置使用规范.md`。

## 通用约定

- **以 UI 实际请求为准**：参数形状、调用顺序、状态怎么流转，一律以页面上操作时真实发出的请求为准（抓包对照）。UI 没走过的写法不要自己发明；没实测过的路径，要点里必须标「未实测」。
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
- 新建字段时除 `name__ak` 外，`isRequired` 一律 false；是否必填在表单布局里控制（2026-11-21 用户规则）。
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
- 新建 section 需 `{ id, layoutId, code, name, type, columnsNum }`；新建 control 需 `{ id, layoutId, sectionId, code, name, fieldId, type, gridSpan, attrs }`。id 用新 UUID；缺 `layoutId` 会报「无法删除其他布局部分Section」。
- **不要带 `belongId` / `fullPath`**：带上必报「[layout_section] BelongId 不正确，该值必须由 FullPath 生成」；实测请求体里没有这两个字段。
- 新分区 / 新控件的 `id` 必须先经 `layout.stable.ids` 换成稳定 id；直接写临时 uuid 同样报 BelongId 那句，换完同一个 body 一次通过。
- 已有分区要按 `layout.detail` 读回的原样整份回传（90+ 键，含 `pageId`、`relationFilter`、`listlayoutId` 等），压成最小对象会 400；只有新建的才用最小对象。
- 旧 section（如 type=4 工作流时间线）原样回传，不能删；pages 原样回传，没有分页传 []。
- **新增分区必须顺位追加**：`sections` 数组保持现有顺序，新分区放在数组最后，`sort` 取现有最大 `sort + 1`；平台按数组顺序归一化 sort，插到第一位会变成“新分区在前”。（2026-11-21 实测）
- 2026-11-09 抓包验证：给 akwv1109__c 的默认布局加「基础信息」分区 + 4 个控件（名称 / 文本 / 数字 / 单选），200 / 176ms、code 0；回读 LayoutDetail 确认 sections=2、controls=4。
- **联动规则也随布局整份回写**：`rules[]` 每条为 `{ id, name, sort, enable, conditions, effectSuccess, effectFailed }`；客户端请求里不带 `sort`、带 `__observed/__observed__id/sortId`，服务端会剥掉并补 `sort: 0`，以回读为准。
- 条件 `targetType: 0`（当前对象状态）：单个状态用 `logicType: 1` + `logicValue` 为带引号的 id；多个状态用 `logicType: 19`（包含于）+ `logicValue` 为 JSON 字符串数组，如 `"[\"<statusId1>\",\"<statusId2>\"]"`。
- 条件 `targetType: 1`（当前对象字段）：`targetId` 填**字段 id（fieldId）**，不是控件 id；选项类字段的 `logicValue` 是选项 id，不是选项名。
- 效果 `targetType: 4`（分区）/ `5`（控件）：`targetId` 填分区 / 控件 id（控件效果不能用 fieldId）；`value` 是属性包，如 `{ "readOnly": false }`、`{ "hidden": false }`。
- 组件的自带属性是规则生效前的**基线**：分区带 `readOnly/required/hidden/expansion`，控件带 `readOnly/required/visible/hidden/readPretty`；规则只覆盖 `value` 里写出的属性，其余沿用基线。组件对象里没配过时这些字段是 `null`（未显式设置）。
- 写与基线相同的值等于空操作：控件 `hidden/visible` 为 `null`（默认显示）时写 `{ hidden: false }` 不改变任何东西；要做「二选一显示」，需成对写 `{ hidden: true }`，或先把默认值改成隐藏。阅读器会标出 `（组件默认 true）` 与 `（与组件默认相同，等于空操作）`，`--check` 不把空操作算错误。
- 条件相同的规则可以拆成多条（每条 `relationId` 独立生成、不复用）；同一目标同一属性的多条规则，运行时以最后触发的一条为准。
- 规则名是自由文本，不能用它反推状态或字段；一律以 id 为准（实测规则名写「已退回」，实际状态名是「已驳回」）。

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

### lifecycle.useraction.create 新建用户动作

> 在某个状态上新建一个用户动作（平台界面叫按钮），动作可绑定"提交流程"。每调一次都会新建一个。

**返回**：data 为新按钮 UUID。

**要点**

- behaviorValue.workflowId 要填 workflowBasicId，不是 workflowConfigId——混用会报 500。
- 按钮只负责 behaviorType 7 拉起流程；发起人签名要在工作流开始节点放电子签名控件（startUpControls），按钮上的 enableSignatures 不是“必签”开关。
- 该接口不做合并：每调一次就多一个按钮，重跑前先 `lifecycle.useraction.list` 查重，不要用 `LifestatusUserAction/Get`（那个只回角色授权）。

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

### query.lifecycle.status.config.list 读生命周期状态配置

> 读生命周期状态的配置详情，补足 openapi 状态列表没有的 `isEnabled` / `sort`。**2026-11-25 标准验证环境复测已入账。**

**返回**：`data.datas` 为全量状态数组；每项含 `id / name / code / lifecycleId / sort / isEnabled / description` 等。

**要点**

- 服务端当前忽略 `lifecycleId` 查询参数，2026-11-25 一次返回 4326 条；调用后必须按 `lifecycleId` 客户端过滤。
- 只支持 GET；POST 返回 405。

**案例**

#### 案例 · 基础调用

```json
{
  "lifecycleId": "<lifecycleId>"
}
```

### query.lifecycle.enterrule.list 读生命周期进入条件

> 读生命周期上挂载的进入条件规则；返回空数组表示未配置。**2026-11-25 标准验证环境复测已入账；目前只覆盖空结果。**

**返回**：`data` 为进入条件数组；`homework2__c` / `audit_plan__c` 均返回 `[]`，2026-11-25 在 `standard-val` 带/不带 `lifecycleId` 也均为 `[]`；非空结构尚未验证。

**要点**

- 只找到 `GetList`；`Get` / `GetByLifecycleId` 均为 404。
- 空数组不要当成接口失败，表示当前生命周期没有配置进入条件。

**案例**

#### 案例 · 基础调用

```json
{
  "lifecycleId": "<lifecycleId>"
}
```

### lifecycle.useraction.get 读状态用户动作的权限角色

> 读某状态用户动作的角色授权（不是用户动作列表）；动作本身用 lifecycle.useraction.list。

**返回**：data 是对象：{ objectId, lifecycleId, statusId, roles[] }，不是用户动作数组。

**要点**

- 返回的是 roles[] 授权，不含 actions；要读状态上的用户动作数组用 `lifecycle.useraction.list`。

**案例**

#### 案例 · 基础调用

```json
{
  "statusId": "<statusId>"
}
```

### lifecycle.useraction.list 读取用户动作

> 按状态 id 返回用户动作数组（平台界面上的按钮）；查重、读改写前先调它。

**返回**：data 为用户动作数组；每项含 lifecycleStatusId / lifecycleStatusName / group / behaviorTypes / icon / name / code / configs 等；没有动作时是 []。

**要点**

- 这是查用户动作的接口（界面叫按钮）；`lifecycle.useraction.get` 走的是权限角色接口，只回 roles。
- 按 statusId 过滤，不是按 objectId。

**案例**

#### 案例 · 基础调用

```json
{
  "statusId": "<statusId>"
}
```

### lifecycle.enteraction.create 新建进入动作（触发流程）

> 在某个生命周期状态上挂进入动作，通常用 behaviorType 7 拉起提醒工作流。

**返回**：data 为新建进入动作 id 数组（演示环境返回 ["84908509-…"]）。

**要点**

- belongType 是 1（进入动作）；状态按钮的 belongType 是 2，别混。
- 目标工作流必须已发布（status 20）且工作流级 isAllowTrigger:true，否则进入动作选不到/不会触发；该开关启用后不可逆。
- name 可为空，平台回读也可能是 null；用 code 标识。
- isEnabled 与 sort 会被服务端归一化：实测传 false/99，回读为 true/2；不要拿返回值当输入回显。
- configs 传 [] 可创建“无配置”的进入动作；真正拉起工作流仍需 behaviorType 7 的 config。
- 该接口不合并：重跑前先用 `lifecycle.enteraction.list` 查重。
- workflowId 填 workflowBasicId，不是 workflowConfigId。

**案例**

#### 案例 · 基础调用

```json
{
  "code": "return_reminder__c",
  "belongId": "<statusId>",
  "belongType": 1,
  "actions": [
    {
      "code": "return_reminder__c",
      "name": "",
      "isEnabled": true,
      "belongType": 1,
      "configs": [
        {
          "id": "<actionConfigId>",
          "uid": "<actionConfigId>",
          "behaviorType": 7,
          "behaviorValue": {
            "workflowId": "<workflowBasicId>"
          },
          "belongId": "<statusId>",
          "sort": 0
        }
      ],
      "sort": 1,
      "executeType": 1,
      "description": "已退回触发提醒",
      "lifecycleId": "<lifecycleId>",
      "lifecycleStatusId": "<statusId>",
      "basicObjectId": "<objectId>",
      "objectCode": "homework__c",
      "enableSignatures": true
    }
  ]
}
```

### lifecycle.enteraction.list 读状态上的进入动作

> 按状态 id 列出进入动作，用于查重与回读。

**返回**：data 为进入动作数组；实测每项含 id / sort / belongId / name / isEnabled / description / modifiedTime / belongType / executeType，不含 code、configs；没有时是 []。

**要点**

- 按 statusId 过滤；与 lifecycle.useraction.list 分属两套动作。

**案例**

#### 案例 · 基础调用

```json
{
  "statusId": "<statusId>"
}
```

## 审批矩阵（补充）

### approvalmatrix.create 创建审批矩阵类型

> 创建一个审批矩阵配置；平台会同时生成管理与角色两个业务对象。

**返回**：`data` 为 `matrixConfigId`（审批矩阵配置 id）。

**要点**

- 自动生成的 `***_mgt__ak` / `***_role__ak` 对象 id 不在创建响应里，必须再按 code 查对象确认。
- `objectId` / `lifecycleId` / `objectTypeId` 必须来自同一个已启用生命周期的对象。
- 创建后还要加匹配字段、加规则、给管理对象建菜单，并建管理/角色业务数据。

**案例**

#### 案例 · 基础调用

```json
{
  "status": 1,
  "name": "企业负责人审核矩阵",
  "code": "corporate_head_review_matrix__c",
  "objectId": "<objectId>",
  "lifecycleId": "<lifecycleId>",
  "objectTypeId": "<objectTypeId>"
}
```

### approvalmatrix.matchfield.add 给审批矩阵加匹配字段

> 指定矩阵按业务对象的哪个字段匹配。

**返回**：`data` 为匹配字段 id。

**要点**

- `fieldId` 必须是矩阵绑定对象上的字段。
- `matchType=1`、`matchNullValueType=10` 来自 2026-11-22 通桥演示抓包；2026-11-25 在 `standard-val` 用 `name__ak` 实测通过，回读 `GetMatrixMatchFieldList` 一致；其他组合未实测。

**案例**

#### 案例 · 基础调用

```json
{
  "matrixConfigId": "<matrixConfigId>",
  "objectId": "<objectId>",
  "fieldId": "<fieldId>",
  "matchType": 1,
  "matchNullValueType": 10
}
```

### approvalmatrix.rule.add 给审批矩阵加规则

> 新增矩阵规则，包含「应用角色约束」与可选的「使用的条件」。

**返回**：`data` 为矩阵规则 id；角色业务数据里的 `matrix_rule__ak` 可能是另一个 id，建工具时要先回读确认。

**要点**

- `constraintRoleIds` 是**应用角色约束**：限制哪些角色能参与。
- `constraintsCondition` 是**使用的条件**：更原始的条件树，按字段/角色做进一步限制；两者不是一回事。
- `constraintsCondition.details[].logicValue` 是二次序列化的 JSON 字符串，不能直接传对象。
- 规则 id 与角色记录 `matrix_rule__ak` 的引用 id 可能不同，写入业务数据前先用 `approvalmatrix.rule.list` 回读。

**案例**

#### 案例 · 基础调用

```json
{
  "matrixConfigId": "<matrixConfigId>",
  "status": 1,
  "scheduleType": 10,
  "name": "企业负责人",
  "code": "corporate_head_review_matrix__c",
  "constraintRoleIds": [
    { "id": "<roleId>", "name": "变更用户" }
  ],
  "constraintsCondition": {
    "details": [],
    "relations": []
  }
}
```

### approvalmatrix.rule.list 读取审批矩阵规则

> 按矩阵配置 id 列出规则，用于创建后回读、查重与确认规则 id。

**返回**：`data` 为规则数组；实测含 `id` / `code` / `name` / `status` / `constraintRoleIds[]` / `constraintsCondition` / `scheduleType` / `sort`。

**要点**

- 规则数组里的 `id` 是配置规则 id；角色业务数据的 `matrix_rule__ak` 可能引用另一个 id，需对照读取。

**案例**

#### 案例 · 基础调用

```json
{
  "matrixConfigId": "<matrixConfigId>"
}
```

## 工作流（补充）

### workflow.create 新建工作流

> 绑定到对象（可再绑生命周期）建一条流程，返回 configId 与 workflowBasicId；开始/结束两个节点由本接口自动建好。

**返回**：data.basicConfig.id（= workflowConfigId）与 data.basicConfig.workflowBasicId（两个 id 别混用）；同一响应还带 variables 与拼写为 aciton 的字段（平台原样）。

**要点**

- status=10 的草稿可以直接加节点、改节点、直接发布，不用先切 40；只有「已启用(20)的流程要再改」才必须先 workflow.update 开 40 草稿，改完再发布那个新 id。
- 本接口会自动建 开始(stepType 10, sort 1) 与 结束(stepType 20, sort 10000) 两个节点，code 由工作流 code 派生：`…__c` → `…_Start__c` / `…_End__c`。所以没有「新建开始节点」这种调用，workflow.start.update 只是往已存在的开始节点上挂控件。
- 从零建流程时 basicConfig 要带 objectLayoutCode（对象默认布局 code），UI 实测必传；只读回改的话原样带回即可。
- isAllowTrigger 只对应「允许从进入动作和事件动作自动启动」：提醒流设为 true；用户动作/状态按钮拉起的审批流保持 false（aa__c 实测 false），不要为了让流程出现在进入动作下拉或为了签名而误开——该开关启用后不可取消。
- isEnable 不是发布开关：aa__c 与 df__c 发布后 isEnable 仍为 false，以 status=20 为准。

**案例**

#### 案例 · 基础调用

```json
{
  "basicConfig": {
    "objectLayoutCode": "computerized_system_authorization_base_layout__ak",
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

> 全量写回 basicConfig（含 versionNo 乐观锁）与变量；对已启用的工作流，调它就是「开始编辑」。

**返回**：`data.basicConfig` 是服务端新建的草稿版本：`id` 换成新的 workflowConfigId、`status: 40`（编辑中）、`versionNo + 1`、`workflowBasicId` 不变。

**要点**

- 这是「切编辑中」的真正入口：已启用（20）的工作流必须先调一次本接口（原样回传 Get 到的 basicConfig，不用自己改成 40），服务端会新建草稿版本；之后改节点、发布都用响应里的新 id。
- 实测 2026-11-25 通桥：`department_change_plan_notice__c` 请求 `ab91d3c2… / status 20 / ver 7` → 响应 `6898c1ab… / status 40 / ver 8`；`choose_responsible_person__c` 请求 ver 2 → 响应 ver 3。
- versionNo 不是最新会失败；先 Get 再改。

**案例**

#### 案例 · 从启用版本开一份草稿

```json
{
  "basicConfig": {
    "id": "<workflowConfigId>",
    "name": "变更审批流（改）",
    "code": "change_approval",
    "status": 20,
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

预期：响应里 `status: 40`、`versionNo` 比请求大 1、`id` 与请求的 id 不同——这个新 id 才是后面改节点与发布要用的。

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

> 发布 / 停用工作流；发布用 20。切「编辑中」不要调本接口，走 workflow.update。

**返回**：`data` 为 `null`（实测）。

**要点**

- 发布：`{ ids: [要发布的 workflowConfigId], status: 20, workflowBasicId }`；`ids` 与 `workflowBasicId` 是两个不同层级的 id，别互串。
- 已启用（20）的工作流不能直接改节点：先 `workflow.update` 切到编辑中（服务端换新 workflowConfigId、status 40），改完再用本接口发布那个新 id。
- 顺序：10 建草稿 → 20 启用；要改已启用的，先 `workflow.update` 开新草稿版本（40）→ 改节点 → 本接口 20 发布。
- 本接口直接传 `status: 40` 能不能进编辑中未实测（2026-11-25 通桥抓包里 UI 从不这么发）。
- 回读 `GET /api/platform/Workflow/GetWorkflowBasic` 看 `status`（20 已发布 / 40 编辑中）、`versionNo` 与 `id`，不要沿用旧版本 id。

**案例**

#### 案例 · 发布当前草稿

```json
{
  "ids": [
    "<workflowConfigId>"
  ],
  "status": 20,
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
- 任务级签名：`isHaveSign: true` 表示节点带签名栏，`isRequireSign: false` 表示任务级签名不必填（签名落在按钮上），`externalSignatureInfoFrom: 1` 是平台回读值。
- 按钮签名四开关：`isRequireSign`（本按钮要电子签名）、`isShowResons` + `isRequiredResons` + `resonTitle`（签名备注，同意可不填、拒绝必填）、`isShowResponsibilities` + `isRequireResponsibilities` + `responsibieTitle`（签名含义标题，字段名拼写原样）+ `responsibleOptions[].value`（该按钮的含义文案）、`isRequirePosition`（需要选择职位）。
- 2026-11-26 v3 实测：审核任务两个按钮都 `isHaveSign: true`、`isRequireSign: true`（两个都必填电子签名）；同意 `isRequiredResons: false`（备注可选），拒绝 `isRequiredResons: true`（备注必填）；两者 `isShowResons/isShowResponsibilities/isRequireResponsibilities/isRequirePosition` 均为 true。用户动作挂载的审批流，开始节点必须放电子签名控件（`ctrlType:60`），本次 `signatureConfig` 为 `isRequirePosition:true`、`isShowSignatureMean:true`、`isRequiredMean:true`、`isShowSignatureRemark:true`、`isRequiredSignature:false`、`reasonInputType:20`；**开始节点只要存在电子签名控件，实际执行即按必填处理**（用户 2026-11-26 口径），不能因 `isRequiredSignature:false` 就判断它的签名可选；备注只显示、不必填。
- 工作流在 UI 编辑并发布后会生成新版本：`workflowBasicId` 不变，但 `workflowConfigId`、`versionNo` 和全部 step id 会变；更新开始/任务/判断/动作前必须重新读取 workflowConfigId 和 step id，不能复用旧 id。

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

- targetType=7 表示"上一步的按钮"；按钮结果由 logicValue 指定，logicType 用 40（所有断言等于）。
- 单条件也用 `logicType: 40`（所有断言等于）；`41`（至少一个断言等于）只在同一组挂多条断言时用。
- logicValue 是带双引号的 UUID 字符串，写成裸 UUID 会不生效。
- rules 里的 nextStepId 是"满足时"，stepDecision.nextStepId 是 else。
- details[].targetId 填**按钮所属那一步的 stepId**（不是按钮 id），targetCode 同步填该步 code；2026-11-25 联邦制药实测 targetId=f6e81732-…（上一步审批节点）、targetCode=Task_Supervisor_Approval__c。用 step 里的 id 去查 tasks/steps 再填。
- 审批流的 else 分支应指向「修改状态：已驳回」动作节点，再由它连结束；直接指结束会导致拒绝后状态不变。

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

- behaviorType 9 = 改本对象生命周期状态（behaviorValue.statusId）；7 = 触发工作流（behaviorValue.workflowId 填 workflowBasicId）；46 = 修改相关对象生命周期状态（behaviorValue: relationId / relatedObjectId / relationType:1 / targetStatusId / isSetCondition:false / filterStatusIds:[]）。
- 一个动作节点可以挂多个动作：stepAction.actions[] 每项是一组要一次执行的动作，configs[] 里可以放多个 behaviorType（顺序执行）。
- 2026-11-25 联邦制药实测：「修改状态待审核」节点同时给了 behaviorType 9 与 46，读回整份再写时要保留 configs 里原有的 id/__observed，凭空造全零 GUID 也能被接受但会丢关联。

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

**返回**：data.step（服务端回显的完整开始节点，不是 data:true）。

**要点**

- 开始节点由 workflow.create 自动建好（code `…_Start__c`、sort 1、stepType 10），本接口只挂/改 startUpControls，没有「新建开始节点」的调用。
- 2026-11-25 联邦制药实测：从零建流程时 UI 随加控件多次调用本接口，startUpControls 1→2→4 个，每次整份覆盖（1.9KB→12.8KB）。
- controlConfig.value 是 participantConfig 的 JSON 字符串（字符串，不是对象）。
- 全量覆盖：先 GetWorkflowStepControls 读回再加。
- 参与者控件只允许 `selectUserType: 40`（字段）或 `50`（审批矩阵）；下面旧案例的 `10` 只用于展示 `controlConfig`/`participantConfig` 结构，工作台新配置不要采用。
- 电子签名控件在 `startUpControls[].signatureConfig`：`isShowSignatureMean` 显示签名含义、`isRequiredMean` 含义必填、`signatureMeanName` 标题、`signatureMeanOptions[].value` 含义选项（内联，没有独立字典接口）、`isShowSignatureRemark` + `signatureRemarkName` + `reasonInputType: 20`（备注）、`isRequirePosition`（选职位）、`isRequireUserName`（填用户名）、`isRequiredSignature`（签名必填参数）。注意：开始节点只要存在签名控件，实际执行就按必填处理；`isRequiredSignature` 回读为 false 时也不能当成“可选”（用户 2026-11-26 口径）。更新时保留已有 option 的 `id`，只改 `value` 文案。
- **控件形状必须是包裹形态**：`{id, sort, name, controlConfig:{workflowConfigId, stepId, name, code, ctrlType, isRequired, value, id, sort, isHidden, …}, participantConfig, signatureConfig}`。`workflow.step.get` / `GetWorkflowStepWithDetails` 回的是**摊平**形态（ctrlType、value、stepId 全在控件对象顶层），把 Get 的结果原样回传，服务端会抛 `Object reference not set to an instance of an object.`（2026-11-26 standard-val 实测，同一份 Get 结果回传必崩）。「读回→改→写回」要把每个控件重新包一层。
- **签名含义 option 的 `id` 必须是真实 GUID**：`signatureMeanOptions[].controlId` 可以是全零（UI 也发全零），但 `id` 给全零保存不报错、跑流程时报 `Object reference not set to an instance of an object.`（2026-11-26 实测）。

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

**返回**：data.step（服务端回显的完整节点，不是 data:true）。

**要点**

- stepTaskApprovals 传 [] 会删光按钮，改按钮时保留原数组。
- 改签名文案（`responsibleOptions[].value`）时保留原 option 的 `id`，其余开关按 `workflow.task.add` 的按钮签名四开关写。
- **`stepTaskConfig.workflowConfigId` / `stepTaskConfig.stepId` 必须是真实 id**（当前 workflowConfigId / 本节点 id）：给全零 GUID 保存不报错，但**跑流程建任务时**抛 `Object reference not set to an instance of an object.`（2026-11-26 standard-val 实测）。同批检查 `stepTaskApprovals[].workflowConfigId/stepId` 也用真实值。
- 控件形状同 `workflow.start.update`：Get 回的是摊平形态，回写前要包成 `{id,sort,name,controlConfig,…}`，否则服务端抛 NRE。
- 与 UI 建的参照流程对齐时，任务节点还应给 `isRequireSign: true`、`isRequireObjectField: true`、`isApprovalShowAsButton: true`（缺了不报错，但审批界面行为与 UI 建的不一致）。

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

**返回**：data.step（服务端回显的完整节点，不是 data:true）。

**要点**

- rules 传 [] 等于清空全部分支。
- 回显的 stepDecision 里多一个 stepId（本节点 id），回写时留着不影响；nextStepList 是两条分支的摘要。
- 控件形状同 `workflow.start.update`：Get 回的是摊平形态，回写前要包成 `{id,sort,name,controlConfig,…}`，否则服务端抛 `Object reference not set to an instance of an object.`（2026-11-26 standard-val 实测）。

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

**返回**：data.step（服务端回显的完整节点，不是 data:true）。

**要点**

- actions 传 [] 会清空动作。
- 一个动作节点可挂多个动作：actions[] 每项是独立一组（configs[] 里可多 behaviorType，顺序执行）；读回整份再回写即可保留，UI 实测每项还带 __observed 字段，原样带回即可。
- 控件形状同 `workflow.start.update`：Get 回的是摊平形态，回写前要包成 `{id,sort,name,controlConfig,…}`，否则服务端抛 `Object reference not set to an instance of an object.`（2026-11-26 standard-val 实测）。

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

**要点**

- 只传 stepName / stepCode / workflowConfigId / stepId（被复制的源节点），节点类型、按钮、参与者配置、表单控件全部原样继承；复制完要复核 code 与 nextStepId 链路（新节点不会自动接进链路）。
- 2026-11-25 联邦制药实测：从「任务：上级审批」复制出「任务：系统授权人审批」，body 仅 158B。

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

**要点**

- 返回的 `startUpControls[]` 是**摊平**形态（ctrlType / value / stepId 都在控件对象顶层），而四个 `*Step.update` 要的是包裹形态（`{id,sort,name,controlConfig,…}`）。「读回→改→写回」必须先把控件重新包一层，否则服务端抛 `Object reference not set to an instance of an object.`（2026-11-26 standard-val 实测）。

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

### menu.group.list 菜单分组列表

> 列出菜单分组（母菜单）。

**返回**：data 为分组数组（实测 6 个）：name / code / status / source / sort / id。

**案例**

#### 案例 · 基础调用

```json
{}
```

### menu.list 菜单列表（按 code 回查菜单）

> 列出菜单树（扁平数组），建完菜单用它回查 code / 层级 / 对象与两套布局 id —— 这是 `menu.submit` 唯一的回读判据。

**返回**：data 为扁平菜单数组（实测 1050 条）：name / code / parentMenuId / menuActionType / objectId / listLayoutId / layoutId / createType / sort / isShow / id。

**要点**

- 子菜单的 `parentMenuId` 指向母菜单 id；`menuActionType` 3 = 母（分组）/ 1 = 对象列表（子）。
- 带 `menuType=1` 过滤和不带，条数不同（实测 1041 / 1050）；回查具体菜单按 code 匹配，别依赖条数。
- 同族的 `GET /api/platform/Menu/GetUserMenuPermission?menuType=1` 只返回**当前用户可见**的菜单（字段更少），权限受限的账号看不到别人建的菜单；回读要用 `Menu/GetMenuList`。
- 2026-11-26 standard-val 实测：天气预报管理（母，`weather_broadcast_menu__c`，605363b7-…）→ 天气预报播报（子，`weather_broadcast_menu_item__c`，95390fc9-…，objectId / listLayoutId / layoutId 三件套齐全）。
- 引擎执行 `menu.submit` 后会自动调本接口按 code 回读，校验 `parentMenuId` 与 `objectId` / `listLayoutId` / `layoutId`，所以审批日志里能看到「回读菜单「…」：层级与对象/两套布局 id 都对得上」。

**案例**

#### 案例 · 基础调用

```json
{
  "menuType": 1
}
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

### notice.template.page 消息模板分页列表

> 分页读全部消息模板（含通用模板 objectid=all），按关键词搜也是它；找模板 id 用这个。

**返回**：data.count 为总数（实测 113）、data.datas[] 每条含 templatetype / name / code / source / objectid / objectname / status / description / sort / notificationchannelist / templatesettinglist / createdTime / modifiedTime / id。

**要点**

- 搜索时 `keywords` 与 `keyword` 要同时给（UI 实测两个键都传），只给一个在个别环境搜不到。
- datas 里的 title / content 只有「单渠道」老模板才有值；多渠道模板这两项是 null，正文在 templatesettinglist 里 —— 要看正文用 notice.template.get。
- 列表只用于挑 id；写回前一定按 id 读单条，别拿列表项当回写来源（列表里的 templatesettinglist / notificationchannelist 可能为 null）。

**案例**

#### 案例 · 基础调用

```json
{
  "pageIndex": 1,
  "pageSize": 20,
  "keywords": "待办",
  "keyword": "待办",
  "filters": []
}
```

### notice.template.get 读单条消息模板

> 取一条模板的全部字段与每个渠道的设置，改之前必须先读这一条。

**返回**：data 为模板全字段：templatetype / name / code / source / objectid / objectname / status / description / title / jumplink / content / sort / signature / sendernum / textmessageid / notificationchannelist / templatesettinglist[] / createdTime / modifiedTime / id。

**要点**

- templatesettinglist[] 是「每个渠道一份设置」，各带自己的 id、messagechannel、templatecode；改模板时按原 id 整份回写（见 notice.template.update）。
- 实测 messagechannel 99 承载标题与正文（title / content），2 与 3 只是渠道开关位。
- 2 / 3 上的 iscovered 标记该渠道「设置是否已单独覆盖过」（通用模板实测 2=true 且带正文、99=false、3 为空壳），含义平台没文档，回写时原样带回即可。
- 通用模板的 objectid 是字符串 `all`，不是对象 id；按对象取模板另见 notice.template.list。

**案例**

#### 案例 · 基础调用

```json
{
  "id": "<templateId>"
}
```

### notice.template.create 新建消息模板

> 按对象建一条消息模板；标题与正文放在 templatesettinglist 的 messagechannel 99 那一项。

**返回**：data 为新模板 id（字符串）。

**要点**

- 创建时不传 id、也不传 sort；sort 由服务端分配（实测 109）。
- 标题与正文只认 templatesettinglist 里 messagechannel:99 那一项；顶层 title / content / jumplink 留空，平台自己存成 null。
- 正文可写 {!record.<字段 code>} / {!$environment.egmp_url} / {!$link.record_detail} / {!$workflow.label} / {!$workflow.promoter} 等变量，并支持 <a>、<br> 与换行。
- 模板 code 建议全局唯一：实测与字段 code 撞名也能建，但之后按 code 回查会混。
- 改同一条模板走同一个接口（带 id）→ notice.template.update；建完要挂到流程节点 → workflow.task.update 的 stepTaskConfig.notifyTemplateId。
- 2026-11-25 通桥环境按 UI 抓包确认：546 字节请求体建成功，返回 id 后可回读；随后挂到变更对象 8 条流程 13 个节点。

**案例**

#### 案例 · 基础调用

```json
{
  "source": 3,
  "status": 1,
  "notificationchannelist": [2, 3],
  "templatesettinglist": [
    {
      "messagechannel": 99,
      "title": "提醒：您有新的待办任务，请及时处理",
      "content": "您有新的待办任务，请及时处理\n变更事项：{!record.change_title__c}\n变更原因：{!record.change_reason__c}"
    },
    { "messagechannel": 2 },
    { "messagechannel": 3 }
  ],
  "isHuawei": true,
  "name": "变更待办任务通知",
  "code": "change_todo_notice__c",
  "objectid": "<objectId>",
  "description": ""
}
```

### notice.template.update 修改消息模板

> 同一个接口带 id 覆盖；UI 的做法是读回整份实体、改 99 号渠道的正文、再整份回传。

**返回**：data 为模板 id（回传同一个 id，不会新建）。

**要点**

- 先 notice.template.get 读回，只改 99 号渠道的 title / content，其余字段与每个子表项的 id、templatecode 原样带回。
- 子表项丢了 id 会被当成新渠道设置（推断，未单独实测）——不要手搓子表，从读回结果改。
- 顶层 title / content / jumplink 在实测请求里是 null，平台不采用；正文只认 messagechannel:99 那一项。
- createdBy / createdTime / modifiedBy / modifiedTime 回传也不会被采用（实测改完 modifiedTime 仍是原值，平台自己盖时间戳）。
- 能否只传 id + 要改的字段还没实测（UI 从不这么发）——先在测试环境小步验证，别在正式环境试。
- 改完要重新发布流程才生效的是流程，不是模板：模板本身保存即生效，但节点上引用的是模板 id，换模板要另走 workflow.task.update。

**案例**

#### 案例 · 基础调用

```json
{
  "id": "<templateId>",
  "source": 3,
  "status": 1,
  "templatetype": 0,
  "name": "变更待办任务通知",
  "code": "change_todo_notice__c",
  "objectid": "<objectId>",
  "objectname": "变更",
  "sort": 109,
  "notificationchannelist": [2, 3],
  "templatesettinglist": [
    {
      "id": "<settingId99>",
      "templatecode": "change_todo_notice__c",
      "messagechannel": 99,
      "title": "提醒：您有新的待办任务，请及时处理",
      "content": "您有新的待办任务，请及时处理<a href=\"{!$environment.egmp_url}{!$link.record_detail}\">查看任务详情</a>。<br>\n变更事项：{!record.change_title__c}"
    },
    { "id": "<settingId2>", "templatecode": "change_todo_notice__c", "messagechannel": 2 },
    { "id": "<settingId3>", "templatecode": "change_todo_notice__c", "messagechannel": 3 }
  ]
}
```

### notice.channel.list 通知渠道配置

> 读各通知渠道（posttype）的启停与外部接口配置，判断某个渠道是否可用。

**返回**：data 为渠道配置数组（实测 8 条，posttype 9/8/7/5/4/2/1/0）：posttype / status / apiaddress / appkey / mailhost / mailport / smsType / imap* / id。

**要点**

- 实测只有 posttype 5 的 status 为 1（其余为 2），它的 apiaddress 指向外部网关 https://cj.ton-bridge.cn:9099/api/akso/notice、appkey 为 1234。
- status 1 / 2 是不是「启用 / 停用」没逐条验证，别据此断言渠道已开通。
- 这里的 posttype 与模板里的 messagechannel 不是同一套编号，不能互相套用。
- 账号口令类字段（appsecret / mailpwd）在监听产物里是脱敏占位，不代表平台真实返回值。

**案例**

#### 案例 · 基础调用

```json
{}
```

### notice.variable.fixed.list 模板固定变量

> 读模板正文可用的全局/系统变量（实例、链接、发起人、任务名等），按分组树返回。

**返回**：data 为分组树：type_name / type_code / field_name / field_code / dataType / showPosition / status / fixedObjectList[]（叶子项同样是这个形状，嵌在 fixedObjectList 里）。

**要点**

- 全局变量组 type_code 为 qjbl，叶子 field_code 形如 {!$environment.current_instance}，直接粘进正文即可。
- showPosition 是可用位置标记（实测 [2,1,3] 这类数组），含义未确认，先全量列给用户挑。
- 变量只是文本，平台不会校验变量是否存在于该对象；错变量在发送时才变成空白，配完最好发一条试。

**案例**

#### 案例 · 基础调用

```json
{}
```

### notice.variable.field.list 模板对象字段变量

> 读对象字段对应的 {!record.xxx} 变量，配置模板正文时用来插入记录字段。

**返回**：data 为按对象分组的数组：objectId / name / code / objectList / fileList[]；fileList 每项带 code（形如 {!record.detailed_documentation__c}）、name、dataType。

**要点**

- 一次返回全部对象字段约 2MB，别在循环里反复调；按 objectId 过滤出目标对象的 fileList。
- code 已是 {!record.<字段 code>} 形态，直接粘进正文即可，不用自己拼。
- 参数 id 传空串等于「全部对象」；传对象 id 时是否只回该对象未验证（UI 只传空串），别依赖这个过滤。

**案例**

#### 案例 · 基础调用

```json
{
  "id": ""
}
```

## 工作流配置模板（非接口，2026-11-22）

> 这一节不是能力目录，不参与 `### <capability id>` 解析；它只给“组合顺序 + 占位符”的模板。具体请求体仍以各接口章节的案例为准。

**模板 0 · 配置前分析**

- 先列三张清单：参与者清单（只能是字段/审批矩阵）、节点清单（开始/状态/任务/判断/动作/结束）、节点关系图（含判断分支与拒绝回退）。
- 执行顺序：先建参与者 → 再建节点 → 最后收口节点关系。
- `workflow.*.add/update` 的 `nextStepId` 是必填；节点可先带临时指向占位，节点全部建完后再用 `workflow.*.update` 统一收口，发布前回读 `nextStepId/nextStepList` 与判断分支。

**模板 A · 开始节点：参与者 + 电子签名**

- 参与者控件：只允许「使用字段作为参与者」（`selectUserType:40`）或「使用审批矩阵作为参与者」（`selectUserType:50`）；控件表示“谁能够参与”，不是“谁能够发起”。
- 电子签名控件：签名含义、是否必填、是否选择职位按业务要求配置。
- `nextStepId` 指向第一条状态步骤（如“审核中”），不是直接指向任务。
- 不要用开始节点参与者推断发起入口；发起入口由状态上的用户动作/进入动作决定。

**模板 B · 审批任务：参与者必须先存在**

- 先看开始节点有没有对应参与者控件；有就把任务的 `taskAssign` / `userControlIds` 指向它，没有就回开始节点补。矩阵规则、角色成员、通知接收人是否齐全，放到发布前检查，不是创建任务节点的前置条件。
- 复制 `workflow.task.add` 的完整 `stepTaskConfig`（16 个键不要删）。
- `taskAssign` / `userControlIds` 填开始节点参与者控件 id（字符串，不是数组）。
- `isRequireApproval: true` + `stepTaskApprovals` 配同意/拒绝按钮；按钮签名按标准审批意见配置。
- 通知任务用 `isNotifyTaskType: true`、`isRequireApproval: false`、空 `stepTaskApprovals`。

**模板 C · 判断与动作**

- 判断节点：`rules[]` 按条件指向 `nextStepId`，`stepDecision.nextStepId` 是 else；`logicValue` 必须是带双引号的按钮 id。
- 动作节点：`behaviorType: 9` 改本对象状态；`behaviorType: 46` 改关联对象状态并带 `relatedObjectId` / 条件；`behaviorType: 7` 再拉起子流程。
- 所有拒绝/不通过分支必须显式回到申请状态或指定退回状态，不能只写一个 else 空出口。

**模板 D · 触发器**

- 审批流：状态用户动作 `behaviorType: 7`，`behaviorValue.workflowId` 填 workflowBasicId。
- 提醒流：进入动作 `behaviorType: 7`，且工作流级 `isAllowTrigger: true`（不可逆，先确认）。
- 初始状态绑定 `lifecycleStatusIds` 不等于触发器；发布后必须单独检查入口。

**模板 E · 分析读取顺序**

1. 对象/生命周期/状态/字段；
2. 开始节点参与者 + 电子签名；
3. 任务参与者 + 审批矩阵；
4. 全部步骤 + 每步详情；
5. 判断规则 + else + 动作效果；
6. 拒绝回退 + 触发绑定；
7. 最后校验所有分支、无人审批、无触发、无最终状态。
