/**
 * 配置能力目录 —— **单一事实源**。
 *
 * 同一份数据同时驱动三处，避免"工具页写的"和"引擎执行的"是两套：
 *   1. host 引擎（plan/apply 按 capabilityId 找方法）；
 *   2. 工具页（列出全部接口、参数、风险、示例，便于审计与扩展）；
 *   3. 审计日志（记录本次操作对应哪个 capability）。
 *
 * 约定：
 *   - `path` 里的 `{xxx}` 是路径参数，从调用参数的 `xxx` 取；
 *   - 其余键都进请求体（GET 时进 query）；
 *   - `risk` 只描述**平台层面的影响**，不代表审批级别；
 *   - `irreversible` 为 true 的操作在 plan 里必须被标红。
 */

export type Domain =
  | 'auth'
  | 'query'
  | 'picklist'
  | 'object'
  | 'lifecycle'
  | 'field'
  | 'layout'
  | 'listlayout'
  | 'menu'

export type Risk = 'low' | 'medium' | 'high'

export interface CapabilityParam {
  name: string
  location: 'path' | 'body' | 'query'
  required?: boolean
  type?: string
  description?: string
}

export interface Capability {
  id: string
  domain: Domain
  title: string
  description: string
  method: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'
  path: string
  risk: Risk
  irreversible?: boolean
  params: CapabilityParam[]
  /** 工具页的预填请求；引擎用同一形状生成 plan。少数接口（如菜单排序）整份 body 就是数组。 */
  example: Record<string, unknown> | unknown[]
}

const p = (name: string, location: CapabilityParam['location'], required = true, type = 'string', description = ''): CapabilityParam => ({
  name,
  location,
  required,
  type,
  description,
})

export const CAPABILITIES: Capability[] = [
  /* ───────────────────────── 认证 / 查询 ───────────────────────── */
  {
    id: 'auth.login',
    domain: 'auth',
    title: 'OpenAPI 登录（换 JWT）',
    description: '用平台账号换 JWT；后续所有读写都带它。密码不落盘。',
    method: 'POST',
    path: '/api/openapi/v1.0/Auth',
    risk: 'low',
    params: [p('account', 'body'), p('password', 'body')],
    example: { account: 'your-account', password: 'your-password' },
  },
  {
    id: 'query.object.get',
    domain: 'query',
    title: '按 code 取对象',
    description: 'v3.6.4 对象元数据；不存在时返回业务错误（不是 404）。',
    method: 'GET',
    path: '/openapi/v3.6.4/object/{code}',
    risk: 'low',
    params: [p('code', 'path')],
    example: { code: 'change_control__c' },
  },
  {
    id: 'query.fields.list',
    domain: 'query',
    title: '对象字段列表',
    description: '返回 { fields, object_name, object_id, object_code }；字段为蛇形命名。',
    method: 'GET',
    path: '/openapi/v3.6.4/metadatas/objects/{code}/fileds',
    risk: 'low',
    params: [p('code', 'path')],
    example: { code: 'change_control__c' },
  },
  {
    id: 'query.picklist.get',
    domain: 'query',
    title: '取选项集',
    description: 'v3.6.4 选项集与选项。',
    method: 'GET',
    path: '/openapi/v3.6.4/metadatas/picklists/{code}',
    risk: 'low',
    params: [p('code', 'path')],
    example: { code: 'change_type__c' },
  },
  {
    id: 'query.lifecycle.status.list',
    domain: 'query',
    title: '生命周期状态列表',
    description: '按 lifecycleId 取状态。',
    method: 'GET',
    path: '/openapi/v3.6.4/metadatas/lifecycle_status/{lifecycleId}',
    risk: 'low',
    params: [p('lifecycleId', 'path')],
    example: { lifecycleId: '00000000-0000-0000-0000-000000000000' },
  },

  /* ───────────────────────── P1：选项集 / 对象 / 生命周期 / 字段 ───────────────────────── */
  {
    id: 'picklist.save',
    domain: 'picklist',
    title: '保存选项集（含选项）',
    description: '一次提交选项集与全部选项；同 code 重复提交为覆盖。',
    method: 'POST',
    path: '/api/platform/ObjectPicklist/save',
    risk: 'low',
    params: [p('name', 'body'), p('code', 'body'), p('options', 'body', false, 'array')],
    example: {
      name: '变更类型',
      code: 'change_type__c',
      options: [
        { name: '主要变更', code: 'major__c', status: 1, operations: 1, sort: 0 },
        { name: '次要变更', code: 'minor__c', status: 1, operations: 1, sort: 1 },
      ],
    },
  },
  {
    id: 'object.save',
    domain: 'object',
    title: '创建/更新对象',
    description: 'source 固定 3（自定义）；enableLifeCycle 一旦为 true 不可回退。',
    method: 'POST',
    path: '/api/platform/BasicObject/SaveBasicObject',
    risk: 'high',
    irreversible: true,
    params: [
      p('name', 'body'),
      p('code', 'body'),
      p('enableLifeCycle', 'body', false, 'boolean'),
      p('enableSignatures', 'body', false, 'boolean'),
    ],
    example: {
      name: '变更控制',
      code: 'change_control__c',
      source: 3,
      status: 1,
      objectClass: 1,
      enableLifeCycle: false,
      enableSignatures: false,
      summaryFields: [],
    },
  },
  {
    id: 'lifecycle.status.create',
    domain: 'lifecycle',
    title: '创建生命周期状态',
    description: '对象启用生命周期后使用；平台自带"开启/关闭"两个不可删状态。',
    method: 'POST',
    path: '/api/config/lifecycle/Status/Create',
    risk: 'medium',
    params: [p('name', 'body'), p('code', 'body'), p('lifecycleId', 'body')],
    example: {
      name: '草稿',
      code: 'status_draft__c',
      lifecycleId: '00000000-0000-0000-0000-000000000000',
      isEnabled: true,
      isRecordDeactivate: false,
      description: '',
      source: 3,
      workflowCancelStatus: '11515107-dce6-4ef1-8d51-b1ffed22040f',
    },
  },
  {
    id: 'lifecycle.status.update',
    domain: 'lifecycle',
    title: '更新生命周期状态（全量）',
    description: '必须回传原有全部字段；用于重命名/启停。',
    method: 'PATCH',
    path: '/api/config/lifecycle/Status/Update',
    risk: 'medium',
    params: [p('id', 'body'), p('name', 'body'), p('code', 'body'), p('lifecycleId', 'body'), p('sort', 'body', false, 'number')],
    example: {
      id: '00000000-0000-0000-0000-000000000000',
      name: '已完成',
      code: 'status_done__c',
      lifecycleId: '00000000-0000-0000-0000-000000000000',
      isEnabled: true,
      isDeleted: false,
      isRecordDeactivate: false,
      description: '',
      source: 3,
      sort: 2,
      workflowCancelStatus: '11515107-dce6-4ef1-8d51-b1ffed22040f',
    },
  },
  {
    id: 'field.save',
    domain: 'field',
    title: '创建/更新字段（14 种类型）',
    description: 'statisticsArrange 必填；dataType=4 需 picklistId，15/16/23 需 referenceObjectId。',
    method: 'POST',
    path: '/api/platform/BasicObject/SaveField',
    risk: 'medium',
    params: [p('name', 'body'), p('code', 'body'), p('dataType', 'body', true, 'number'), p('objectId', 'body')],
    example: {
      name: '编号',
      code: 'no__c',
      dataType: 1,
      objectId: '00000000-0000-0000-0000-000000000000',
      isRequired: false,
      invisible: false,
      disabled: false,
      statisticsArrange: { id: 1 },
    },
  },

  /* ───────────────────────── P2：表单布局 ───────────────────────── */
  {
    id: 'layout.list',
    domain: 'layout',
    title: '表单布局列表',
    description: '按对象取布局；默认布局 source=2 且 isMain=true。',
    method: 'GET',
    path: '/api/platform/Layout/LayoutList',
    risk: 'low',
    params: [p('basicObjectId', 'query'), p('pageIndex', 'query', false, 'number'), p('pageSize', 'query', false, 'number')],
    example: { basicObjectId: '00000000-0000-0000-0000-000000000000', pageIndex: 1, pageSize: 20 },
  },
  {
    id: 'layout.detail',
    domain: 'layout',
    title: '表单布局详情',
    description: '含 sections / controls / pages；保存前必须先读。',
    method: 'GET',
    path: '/api/platform/Layout/LayoutDetail',
    risk: 'low',
    params: [p('layoutId', 'query')],
    example: { layoutId: '00000000-0000-0000-0000-000000000000' },
  },
  {
    id: 'layout.save',
    domain: 'layout',
    title: '保存表单布局（全量替换）',
    description: 'sections/controls/pages 全量提交；不在数组中的会被移除。（当前受阻：平台要求 section.belongId 由其 FullPath 生成，规则待录制）',
    method: 'POST',
    path: '/api/platform/Layout/SaveLayoutDetail',
    risk: 'medium',
    params: [p('id', 'body'), p('objectId', 'body'), p('objectTypeId', 'body'), p('name', 'body'), p('code', 'body'), p('sections', 'body'), p('controls', 'body')],
    example: {
      id: '00000000-0000-0000-0000-000000000000',
      objectId: '00000000-0000-0000-0000-000000000000',
      objectTypeId: '00000000-0000-0000-0000-000000000000',
      name: '默认布局',
      code: 'change_control__c_base_layout__ak',
      source: 2,
      status: 1,
      isMain: true,
      labelCol: 0,
      sections: [],
      controls: [],
      pages: [],
      rules: [],
      activePageCode: null,
      activeSectionCode: null,
    },
  },

  /* ───────────────────────── P2：列表布局 ───────────────────────── */
  {
    id: 'listlayout.list',
    domain: 'listlayout',
    title: '列表布局列表',
    description: '按对象取列表布局；默认布局 source=2。',
    method: 'GET',
    path: '/api/platform/Listlayout/List',
    risk: 'low',
    params: [p('objectId', 'query')],
    example: { objectId: '00000000-0000-0000-0000-000000000000' },
  },
  {
    id: 'listlayout.columns.get',
    domain: 'listlayout',
    title: '列表布局当前列',
    description: '读取显示字段列，保存前必须先读（AddColumns 是全量替换）。',
    method: 'GET',
    path: '/api/platform/Listlayout/Columns',
    risk: 'low',
    params: [p('listlayoutId', 'query')],
    example: { listlayoutId: '00000000-0000-0000-0000-000000000000' },
  },
  {
    id: 'listlayout.save',
    domain: 'listlayout',
    title: '新建列表布局',
    description: '只建布局壳，列用 listlayout.columns.set。',
    method: 'POST',
    path: '/api/platform/Listlayout/Save',
    risk: 'low',
    params: [p('name', 'body'), p('code', 'body'), p('objectId', 'body'), p('objectTypeId', 'body')],
    example: { status: 1, source: 3, sortFields: [], name: '变更列表', code: 'list_change__c', objectTypeId: '', objectId: '' },
  },
  {
    id: 'listlayout.columns.set',
    domain: 'listlayout',
    title: '设置列表列（全量替换）',
    description: '提交完整列集合，不在集合中的列会被移除。',
    method: 'POST',
    path: '/api/platform/Listlayout/AddColumns',
    risk: 'medium',
    params: [p('listlayoutId', 'body'), p('listlayoutColumns', 'body')],
    example: {
      listlayoutId: '00000000-0000-0000-0000-000000000000',
      listlayoutColumns: [{ fieldId: '', fieldPath: 'name__ak', sort: 1 }],
    },
  },
  {
    id: 'listlayout.filter.get',
    domain: 'listlayout',
    title: '读取列表过滤条件',
    description: '按布局读取过滤配置。',
    method: 'GET',
    path: '/api/platform/Listlayout/Filters',
    risk: 'low',
    params: [p('belongId', 'query')],
    example: { belongId: '00000000-0000-0000-0000-000000000000' },
  },
  {
    id: 'listlayout.filter.save',
    domain: 'listlayout',
    title: '保存列表过滤条件',
    description: '全量替换（upsert）；logicType 枚举尚不完整，属实验性能力。',
    method: 'POST',
    path: '/api/platform/Listlayout/SaveFilter',
    risk: 'medium',
    params: [p('belongId', 'body'), p('details', 'body'), p('relations', 'body')],
    example: {
      belongId: '00000000-0000-0000-0000-000000000000',
      details: [],
      relations: [{ id: 'r1', logicalOperator: 1, index: 1 }],
    },
  },

  /* ───────────────────────── P2：菜单 ───────────────────────── */
  {
    id: 'menu.group.list',
    domain: 'menu',
    title: '菜单组列表',
    description: '查询所有菜单组。',
    method: 'POST',
    path: '/api/platform/MenuGroup/QueryList',
    risk: 'low',
    params: [],
    example: {},
  },
  {
    id: 'menu.submit',
    domain: 'menu',
    title: '创建菜单（母/子）',
    description: 'menuActionType=3 母菜单容器，=1 子菜单（挂对象与布局）。',
    method: 'POST',
    path: '/api/platform/Menu/Submit',
    risk: 'medium',
    params: [p('name', 'body'), p('code', 'body'), p('menuActionType', 'body', true, 'number')],
    example: {
      name: '变更控制',
      code: 'change_control_menu__c',
      menuActionType: 1,
      parentMenuId: '00000000-0000-0000-0000-000000000000',
      objectId: '00000000-0000-0000-0000-000000000000',
      source: 2,
      menuType: 1,
      status: 1,
    },
  },
  {
    id: 'menu.sort',
    domain: 'menu',
    title: '菜单排序（全量树）',
    description: '提交完整菜单树；不在数组中的项会失去原位置。',
    method: 'POST',
    path: '/api/platform/Menu/Sort',
    risk: 'high',
    params: [p('tree', 'body')],
    example: [{ key: '00000000-0000-0000-0000-000000000000', children: [] }],
  },
]

export function findCapability(id: string): Capability | undefined {
  return CAPABILITIES.find((c) => c.id === id)
}

/** 按 domain 分组，供工具页渲染。 */
export function groupByDomain(): Array<{ domain: Domain; items: Capability[] }> {
  const order: Domain[] = ['auth', 'query', 'picklist', 'object', 'lifecycle', 'field', 'layout', 'listlayout', 'menu']
  const map = new Map<Domain, Capability[]>()
  for (const c of CAPABILITIES) {
    const list = map.get(c.domain) ?? []
    list.push(c)
    map.set(c.domain, list)
  }
  return order.filter((d) => map.has(d)).map((domain) => ({ domain, items: map.get(domain)! }))
}
