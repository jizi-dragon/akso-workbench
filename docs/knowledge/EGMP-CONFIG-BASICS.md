# eGMP 配置规范（v0 · 实测版）

> 状态：**v0 草稿**，2026-11-09。只写「在 standard-val 上真调过、有回读证据」的结论；
> 没验证过的标 **待验证**，需要你们组织拍板的标 **待定**（见 §5）。
> 逐条证据在 `docs/CAPABILITY-VERIFICATION-2026-11-09.md`（含每条的输入/输出/系统变化）。

## 0. 一句话

配置不是「把接口调通」，而是**按依赖顺序把 8 层元数据搭起来**。每层都有平台硬约束；
顺序错了轻则报错，重则 `code: 0` 但被平台**静默忽略**——所以每一步都要回读。

## 1. 唯一正确的搭建顺序

```
对象 → 选项集 → 字段 → 生命周期状态 → 状态按钮 → 列表视图 → 菜单 → 工作流 → 表单布局（只能改，不能加）
```

| 层 | 能力 | 为什么必须在前 | 不可逆点 |
| --- | --- | --- | --- |
| 对象 | `object.save` | 后面所有东西都挂 `objectId` | `code` 定了就别改（save 按 code upsert） |
| 选项集 | `picklist.save` | `dataType=4` 的字段要引用它 | 选项 id 会被字段引用 |
| 字段 | `field.save` | 列表列、流程字段都要它 | 字段 `code` 进库后改不了 |
| 状态 | `lifecycle.status.create/update` | 按钮要挂状态 | 状态 id 被按钮/流程引用 |
| 按钮 | `lifecycle.useraction.create` | 流程由按钮触发（`behaviorType: 7`） | — |
| 列表 | `listlayout.save/columns.set/filter.save` | 菜单要引用列表 | 列整份替换，删列可能被平台拒绝 |
| 菜单 | `menu.submit` | 菜单要引用对象+布局+列表 | 菜单排序是整树全量替换，未开工具；需要时走平台页面 |
| 工作流 | `workflow.*` | 要引用对象/状态/按钮 | **发布后回不到草稿**（20→10 被静默忽略） |

## 2. 逐层硬约束

### 2.1 对象 `object.save`
- 自定义元数据 `code` 统一带 `__c` 后缀（菜单已实测**必须**；对象/字段按同一惯例）。
- 按 `code` **upsert**：同 code 再调就是更新，不是新建。所以 code 要一次定对。
- `enableLifeCycle: true` 才有状态机；平台会自动建 1 个布局、1 个列表，并塞一个 `init_status__c`（开启）。
- 实测保存耗时可达 **~6s**；客户端超时别当失败，先 `query.object.get` 回读。
- `recordIdCode` 是业务流水号起始值（实测传 8995 可用）；不传是否报错 **待验证**。

### 2.2 选项集 `picklist.save`
- 先建选项集，再建引用它的字段。
- 更新时少传旧选项，平台**保留**旧选项、不算失败；工具会在消息里点出来。
- 选项的 `id` 用 `query.picklist.get` 取，别自己编。

### 2.3 字段 `field.save`
- `dataType: 4` 必须有 `extras.picklist`（选项集 code 或 id）。
- 一次一个字段；建完用 `query.fields.list` / `object.field.page` 回读。
- **字段不会自动进表单布局**（见 2.5），这是最容易误判「没生效」的地方。

### 2.4 生命周期 `lifecycle.*`
- 对象 `enableLifeCycle: true` 时平台自带 `init_status__c`（**开启**状态，即初始状态）。
- 顺序：先 `status.create` 建状态 → `status.update` 改名/启停 → 再 `useraction.create` 挂按钮。
- 按钮 `behaviorType: 7` 才是「触发流程」的提交按钮；按钮挂在**某个状态**上（`statusId`）。
- 按钮与流程的绑定：`actions[0].configs[0].behaviorValue.workflowId` 填 **workflowBasicId**（不是 workflowConfigId）。
- 退回提醒走「进入动作」`lifecycle.enteraction.create`（`belongType: 1`），不是状态按钮；目标提醒流必须已发布且 `isAllowTrigger: true`，该开关启用后不可逆。
- 发起人电子签名在**工作流开始节点的电子签名控件**里配；按钮上的 `enableSignatures` 不是“必签”开关。

### 2.5 表单布局 `layout.save`（最大的坑）
- 平台自动生成的布局：有 1 个「流程时间线」分区，**控件为 0** → 控制台打开表单是空的。
- 写法：先 `layout.detail` 读全量 → 在 `sections` / `controls` 上改 → **整份写回**（全量替换）。
- 新建分区只需 `{ id: 新UUID, code, name, type: 1, columnsNum }`；新建控件只需
  `{ id: 新UUID, sectionId, code, name, fieldId, type: 'AKInput', gridSpan: 1, attrs }`。
- **不要带 `belongId` / `fullPath`**：带了必报 `[layout_section] BelongId 不正确，该值必须由 FullPath 生成`
  （2026-11-09 抓包确认：浏览器的真实请求体里没有这两个字段）。
- 旧分区（尤其 type=4 工作流时间线）原样回传，不能删；`pages` 原样回传，没有分页传 `[]`。
- 返回 `data` 是布局 id；写完回读 `layout.detail` 确认 sections / controls 数量。
- 结论：**API 能加分区和控件**，之前「只能人工做」的说法已作废（错在自造了 belongId）。

### 2.6 列表视图 `listlayout.*`
- `listlayout.save` 新建（`code` + `objectId`），返回 `{ id }`。
- `columns.set` 是**整份替换**：必须保留 `status_id__sys`，否则报
  `已设置在悬停卡和默认列表显示，无法直接删除：状态`（HTTP 500）。
- `filter.save`：`relationId` 必须是 **GUID**，传 `"r1"` 会 400 `One or more validation errors occurred.`；
  成功时 `data` 为 **null**（不返回新筛选 id，别按文档等 id）。
- 回读：`columns.get` / `filter.get`。

### 2.7 菜单 `menu.submit`
- 菜单 `code` 必须 `xxx__c`；`akwv_menu_1109` 会报 `Code不符合规则`。
- 子菜单必须同时给 `parentMenuId`、`objectId`、`layoutId`、`listLayoutId`。
- `menu.group.list` 查的是**菜单分组**（MenuGroup），**看不到新建的菜单**；核对只能进控制台。
- `Menu/Sort` 是全量替换且没有「读整棵菜单树」的接口，误操作会把子菜单移出层级——**不提供工具，需要在控制台操作**。

### 2.8 工作流 `workflow.*`
- `create` → `{ configId, workflowBasicId }`，初始 `status: 10`（草稿）。
- `steps.list` 自动建开始（10）与结束（20）。
- 加 `task` / `decision` / `action` **不会自动接线**；必须 `workflow.start.update` 设 `nextStepId`，
  后续节点同理，最后才是一条完整的链。
- `task.add`：`isRequireSign: true` 而对象未启用电子签名时，报
  `该对象未启用电子签名，流程任务禁止配置电子签名`；建模阶段先传 `false`。
- `status.change` **单向**：10→30/40 可以；**20→10、20→40 会被静默忽略**（code 仍 0）。
  → 规范：**所有节点/按钮/字段配完、验完，再发布**。
- `delete`：用 **DELETE + body `{id}`**（POST 会 405）；只有草稿（10）能删；
  20 报 `禁止删除启用状态的流程`，30 报 `禁止删除禁用状态的流程`。
- `get` 返回**扁平** `basicConfig`，不要按文档去找 `basicConfig` 层或 `variables`。

### 2.9 认证
- `auth.login` 换 JWT（约 55 分钟）；口令不落盘。
- `auth.oauth.login` / `auth.oauth.refresh` 已于 2026-11-09 从工具目录删除（要浏览器 OAuth 链路）；
  `auth.login` 同一天也删了——写入只走 akso-cloud 代登录，agent 不再接触明文口令。

## 3. 从零配一份「能跑」的配置（checklist）

1. 定 `code` 规范（见 §5 待定项），写下来再动手。
2. `object.save`（`enableLifeCycle: true`，记下 `objectId` / `lifecycleId`）。
3. `picklist.save` → `field.save` × N（`dataType: 4` 的引用选项集）。
4. `lifecycle.status.create` × N → `status.update` → `useraction.create`（提交按钮，`behaviorType: 7`）。
5. `listlayout.save` → `columns.set`（**保留 `status_id__sys`**）→ `filter.save`（GUID）。
6. `menu.submit` 母菜单 → 子菜单（`parentMenuId` + 三个 id）。
7. `workflow.create` → `steps.list` → `task/decision/action` → `start.update` 接线 → 验完 → `status.change` 发布。
8. **每一步都回读**：`get` / `list` / `page.list`；`code: 0` 不等于生效。
9. 表单布局：控制台人工补控件（API 加不了）。
10. 验收：控制台能看到对象、字段、状态、列表、菜单、流程各就各位。

## 4. 反模式（都是实测踩过的，报错原文）

| 做法 | 结果 | 正确做法 |
| --- | --- | --- |
| 菜单 code 不带 `__c` | `Code不符合规则` | `xxx__c` |
| `filter.save` 的 `relationId` 传短名 | HTTP 400 `One or more validation errors occurred.` | 传 GUID |
| `columns.set` 不带 `status_id__sys` | 500 `已设置在悬停卡和默认列表显示，无法直接删除：状态` | 保留该列 |
| 先发布工作流，再改节点 | 20→10 静默忽略，改不动 | 草稿期配完再发布 |
| `workflow.delete` 用 POST | 405 | DELETE + body |
| 想 `layout.save` 加控件，顺手带上 `belongId` | `BelongId 不正确…FullPath` | 去掉 `belongId` / `fullPath`，用最小对象 |
| 加完节点就以为接上了 | 链是断的 | `start.update` 设 `nextStepId` |
| 看 `code: 0` 就收工 | 可能被静默忽略 | 回读 |

## 5. 待定的组织约定（需要你们拍板，定完我补进来）

1. **命名**：对象 / 字段 / 菜单 / 流程 code 的前缀、大小写、分隔符、中文名规则。
2. **审批**：谁能建、谁能发布、要不要双人复核；测试环境与生产的要求是否不同。
3. **环境**：standard-val 建好的配置怎么进生产（发布包？导出导入？重建？）——目前未验证。
4. **权限**：角色、菜单可见性、按钮权限怎么配（`useraction.get` 里有 `roles`，但没配过）。
5. **清理**：验证残留（如 `akwv1109__c`）谁删、多久删；对象删除是否会级联。
6. **范围**：业务记录（对象实例）要不要纳入工具，还是只做元数据。

## 6. 让知识不过期

- 每条结论带**日期 + 环境 + 证据**，写进台账，不写「据说」。
- 新增/修改能力：先跑 `%TEMP%\akw-verify\run.mts` 真调一遍，结果回填台账，再改 `approved-tools.ts`。
- 下一步（建议）：把 §2 的硬约束做成 `config.lint`——比如「列里必须有 `status_id__sys`」「发布前不许有断链」
  由工具机器校验，而不是靠人读这份文档。
