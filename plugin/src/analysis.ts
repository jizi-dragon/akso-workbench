/**
 * 对象分析：把「对象 → 生命周期 → 状态 → 动作 → 工作流」读成一份只读模型。
 *
 * 数据全部走 eGMP 配置接口，不写任何东西：
 *   - 对象：OpenAPI `BasicObject/{code}`；输入是 id 时先用 `AllObjectWithType` 反查 code。
 *   - 状态：`Status/GetList`（平台一次返回全量，客户端按 lifecycleId 过滤）。
 *   - 动作：`UserAction/GetListByStatusId` + `EnterAction/GetListByStatusId`；
 *     进入动作必须再 `GetById`，list 里没有 configs/behaviorType。
 *   - 工作流：`GetWorkflowBasicPageViewList` 一次拉全量，按 id 取名称。
 *
 * 所有接口都是已实测的只读接口；未取到的字段宁可留空，不猜。
 */
import { loadActionMap } from './config/action-map.ts'
import { call, EgmpError, type EgmpSession } from './config/session.ts'

export interface AnalysisAction {
  id: string
  code: string
  name: string
  description: string
  kind: 'user' | 'enter'
  behaviorTypes: number[]
  behaviorLabels: string[]
  workflowId: string | null
  workflowName: string | null
  configCount: number
}

export interface AnalysisStatus {
  id: string
  name: string
  code: string
  sort: number
  isEnabled: boolean
  userActions: AnalysisAction[]
  enterActions: AnalysisAction[]
}

export interface AnalysisResult {
  object: {
    id: string
    code: string
    name: string
    lifecycleId: string
    enableLifeCycle: boolean
  }
  lifecycle: {
    id: string
    name: string
  } | null
  statuses: AnalysisStatus[]
  totals: {
    statuses: number
    enabledStatuses: number
    userActions: number
    enterActions: number
  }
  enterCondition: {
    count: number
    configured: boolean
    readable: boolean
  }
  warnings: string[]
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function bool(value: unknown): boolean {
  return value === true
}

function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function number(value: unknown, fallback = 0): number {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

/** 统一处理 `data` / `data.datas` / `data.data` 三种分页返回。 */
function rowsOf(response: Record<string, unknown>): Record<string, unknown>[] {
  const data = response.data
  if (Array.isArray(data)) return data.map(record)
  const box = record(data)
  for (const key of ['datas', 'data', 'rows', 'items']) {
    if (Array.isArray(box[key])) return (box[key] as unknown[]).map(record)
  }
  return []
}

function actionConfigs(item: Record<string, unknown>): Record<string, unknown>[] {
  const direct = array(item.configs).map(record)
  if (direct.length > 0) return direct
  for (const action of array(item.actions).map(record)) {
    const nested = array(action.configs).map(record)
    if (nested.length > 0) return nested
  }
  return []
}

function behaviorLabels(behaviorTypes: number[], map: Map<number, string>): string[] {
  return behaviorTypes.map((type) => map.get(type) ?? `行为 ${type}`)
}

/** 输入 id 时，用全量对象清单反查 code。 */
async function resolveByCode(session: EgmpSession, query: string): Promise<string> {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(query)) {
    return query
  }
  const listed = await call(session, 'GET', '/api/platform/BasicObject/AllObjectWithType')
  const rows = rowsOf(listed)
  const hit = rows.find((item) => text(item.id) === query)
  if (hit === undefined) throw new EgmpError(`找不到 id 为 ${query} 的对象`)
  const code = text(hit.code)
  if (code === '') throw new EgmpError(`对象 ${query} 没有 code，无法继续分析`)
  return code
}

async function readEnterRules(
  session: EgmpSession,
  lifecycleId: string,
): Promise<{ count: number; readable: boolean }> {
  try {
    const response = await call(session, 'GET', '/api/config/lifecycle/StatusEnterRule/GetList', undefined, {
      lifecycleId,
    })
    return { count: rowsOf(response).length, readable: true }
  } catch {
    // 进入条件接口在部分环境不可用；区分“读到空”与“读不到”，避免误报未配置。
    return { count: 0, readable: false }
  }
}

async function readLifecycleName(session: EgmpSession, code: string): Promise<string> {
  try {
    const response = await call(session, 'GET', `/api/openapi/v1.0/BasicObject/lifecycleStatus/${encodeURIComponent(code)}`)
    const row = rowsOf(response).find((item) => text(item.lifecycleName) !== '')
    return row === undefined ? '' : text(row.lifecycleName)
  } catch {
    // 名称不是主链；失败时界面显示 id。
    return ''
  }
}

async function readWorkflowNames(session: EgmpSession, ids: string[]): Promise<Map<string, string>> {
  const map = new Map<string, string>()
  if (ids.length === 0) return map
  try {
    const response = await call(session, 'POST', '/api/platform/Workflow/GetWorkflowBasicPageViewList', {
      pageIndex: 1,
      pageSize: 1000,
      filters: [],
    })
    for (const row of rowsOf(response)) {
      const id = text(row.workflowBasicId) || text(row.id)
      if (id !== '' && ids.includes(id)) map.set(id, text(row.name) || id)
    }
  } catch {
    // 工作流名称不是主链；失败时保留 id，界面仍能看。
  }
  return map
}

async function readAction(
  session: EgmpSession,
  statusId: string,
  kind: 'user' | 'enter',
): Promise<AnalysisAction[]> {
  const path =
    kind === 'user'
      ? '/api/config/lifecycle/UserAction/GetListByStatusId'
      : '/api/config/lifecycle/EnterAction/GetListByStatusId'
  const response = await call(session, 'GET', path, undefined, { statusId })
  const rows = rowsOf(response)
  const actions: AnalysisAction[] = []

  for (const row of rows) {
    let detail = row
    if (kind === 'enter') {
      const id = text(row.id)
      if (id !== '') {
        try {
          const full = await call(session, 'GET', '/api/config/lifecycle/EnterAction/GetById', undefined, { id })
          const data = record(full.data)
          if (Object.keys(data).length > 0) detail = data
        } catch {
          // 详情失败就保留 list 外壳；behaviorType 会为空，界面显示“未配置具体动作”。
        }
      }
    }
    const configs = actionConfigs(detail)
    const behaviorTypes = configs
      .map((config) => number(config.behaviorType, 0))
      .filter((value) => value > 0)
    const workflowId = text(record(configs[0]?.behaviorValue).workflowId) || null
    actions.push({
      id: text(detail.id) || text(row.id),
      code: text(detail.code) || text(row.code),
      name: text(detail.name) || text(row.name) || '（未命名）',
      description: text(detail.description) || text(row.description),
      kind,
      behaviorTypes,
      behaviorLabels: [],
      workflowId,
      workflowName: null,
      configCount: configs.length,
    })
  }
  return actions
}

export async function analyzeObject(session: EgmpSession, rawQuery: string): Promise<AnalysisResult> {
  const query = rawQuery.trim()
  if (query === '') throw new EgmpError('请输入对象 id 或 code')

  const code = await resolveByCode(session, query)
  const objectResponse = await call(session, 'GET', `/api/openapi/v1.0/BasicObject/${encodeURIComponent(code)}`)
  const object = record(objectResponse.data)
  if (Object.keys(object).length === 0) throw new EgmpError(`对象 ${code} 没有返回详情`)

  const lifecycleId = text(object.lifeCycleId) || text(object.lifecycleId)
  const enableLifeCycle = bool(object.enableLifeCycle)
  const warnings: string[] = []
  const result: AnalysisResult = {
    object: {
      id: text(object.id),
      code: text(object.code) || code,
      name: text(object.name) || code,
      lifecycleId,
      enableLifeCycle,
    },
    lifecycle: null,
    statuses: [],
    totals: { statuses: 0, enabledStatuses: 0, userActions: 0, enterActions: 0 },
    enterCondition: { count: 0, configured: false, readable: true },
    warnings,
  }

  const lifecycleName = lifecycleId === '' ? '' : await readLifecycleName(session, code)
  result.lifecycle = { id: lifecycleId, name: lifecycleName }

  if (!enableLifeCycle || lifecycleId === '') {
    warnings.push('该对象未启用生命周期，没有状态和动作可分析。')
    return result
  }

  const statusResponse = await call(session, 'GET', '/api/config/lifecycle/Status/GetList')
  const allStatuses = rowsOf(statusResponse)
  const statuses = allStatuses
    .filter((row) => text(row.lifecycleId) === lifecycleId)
    .sort((a, b) => number(a.sort) - number(b.sort))

  if (statuses.length === 0) {
    warnings.push('Status/GetList 没有找到该生命周期下的状态；请确认对象是否已绑定生命周期。')
    return result
  }

  const enterRules = await readEnterRules(session, lifecycleId)
  result.enterCondition = {
    count: enterRules.count,
    configured: enterRules.count > 0,
    readable: enterRules.readable,
  }

  const behaviorMap = new Map<number, string>()
  for (const group of loadActionMap().groups) {
    for (const row of group.rows) {
      const value = Number(row.value)
      if (Number.isFinite(value) && value > 0) behaviorMap.set(value, row.label)
    }
  }

  const enabledStatuses = statuses.filter((row) => row.isEnabled !== false)
  for (const row of enabledStatuses) {
    const statusId = text(row.id)
    const [userActions, enterActions] = await Promise.all([
      readAction(session, statusId, 'user').catch((error: unknown) => {
        warnings.push(`${text(row.name)} 的用户动作读取失败：${error instanceof Error ? error.message : String(error)}`)
        return []
      }),
      readAction(session, statusId, 'enter').catch((error: unknown) => {
        warnings.push(`${text(row.name)} 的进入动作读取失败：${error instanceof Error ? error.message : String(error)}`)
        return []
      }),
    ])
    result.statuses.push({
      id: statusId,
      name: text(row.name) || text(row.code),
      code: text(row.code),
      sort: number(row.sort),
      isEnabled: true,
      userActions,
      enterActions,
    })
  }

  const workflowIds = new Set<string>()
  for (const status of result.statuses) {
    for (const action of [...status.userActions, ...status.enterActions]) {
      if (action.workflowId !== null) workflowIds.add(action.workflowId)
    }
  }
  const workflowNames = await readWorkflowNames(session, [...workflowIds])
  for (const status of result.statuses) {
    for (const action of [...status.userActions, ...status.enterActions]) {
      if (action.workflowId !== null) action.workflowName = workflowNames.get(action.workflowId) ?? null
      action.behaviorLabels = behaviorLabels(action.behaviorTypes, behaviorMap)
    }
    result.totals.userActions += status.userActions.length
    result.totals.enterActions += status.enterActions.length
  }

  result.totals.statuses = statuses.length
  result.totals.enabledStatuses = enabledStatuses.length
  if (!result.enterCondition.readable) {
    warnings.push('生命周期进入条件读取失败（StatusEnterRule/GetList 接口不可用），不能判断是否已配置。')
  }
  return result
}
