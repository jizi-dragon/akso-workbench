/**
 * 对象分析的「审批矩阵」检查（只读）。
 *
 * 调用方只传已挂到启用生命周期状态上的工作流（analysis-workflow 已过滤）。
 * 这里统计这些工作流开始节点上实际挂载的参与者矩阵，并记录：
 *   - 矩阵名称；
 *   - 关联工作流；
 *   - 参与者控件；
 *   - 该控件被哪些任务节点引用（应用节点）。
 *
 * 对每个矩阵检查同名菜单是否存在，以及菜单对应管理对象是否有业务数据。
 * 查不到就写 warning，不猜。
 */
import { call, type EgmpSession } from './config/session.ts'
import type { AnalysisWorkflow } from './analysis-workflow.ts'

export interface MatrixUsage {
  workflowId: string
  workflowName: string
  /** 工作流挂到的启用状态（名称），例如“变更申请”。 */
  statusNames: string[]
  /** 开始节点上的参与者控件；它由该矩阵创建。 */
  controlId: string
  controlName: string
  /** 实际引用这个参与者控件的任务节点。没有任务引用时写“开始节点（未绑定任务）”。 */
  appliedStepIds: string[]
  appliedStepNames: string[]
  /** 触发来源：动作名，或“状态绑定（未反查到动作）”。 */
  triggerLabel: string
}

export interface MatrixMenu {
  id: string
  name: string
  path: string
  objectId: string
  viewId: string
}

export interface AnalysisMatrix {
  id: string
  name: string
  code: string
  status: number
  isEnabled: boolean
  menus: MatrixMenu[]
  menuFound: boolean
  recordCount: number | null
  usage: MatrixUsage[]
  warnings: string[]
}

export interface MatrixAnalysis {
  matrices: AnalysisMatrix[]
  warnings: string[]
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function number(value: unknown): number {
  const n = Number(value)
  return Number.isFinite(n) ? n : 0
}

function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function rowsOf(value: unknown): Record<string, unknown>[] {
  const data = record(value).data
  if (Array.isArray(data)) return data.map(record)
  const page = record(data)
  if (Array.isArray(page.datas)) return page.datas.map(record)
  const topLevel = record(value).datas
  if (Array.isArray(topLevel)) return topLevel.map(record)
  return []
}

function normalized(value: string): string {
  return value.replace(/\s+/g, '').toLowerCase()
}

interface FlatMenu extends MatrixMenu {
  parentNames: string[]
}

/**
 * 平台返回的是扁平菜单数组（只有 parentMenuId），不是嵌套树；
 * 这里按 parentMenuId 还原祖先链，匹配“审批矩阵”父菜单时才有依据。
 */
function menusFromRows(value: unknown): FlatMenu[] {
  const rows = array(value).map(record).filter((row) => text(row.name) !== '')
  const byId = new Map(rows.map((row) => [text(row.id), row]))
  const ancestors = (row: Record<string, unknown>): string[] => {
    const names: string[] = []
    const seen = new Set<string>()
    let parentId = text(row.parentMenuId)
    while (parentId !== '' && byId.has(parentId) && !seen.has(parentId)) {
      seen.add(parentId)
      const parent = byId.get(parentId)
      if (parent === undefined) break
      names.unshift(text(parent.name))
      parentId = text(parent.parentMenuId)
    }
    return names
  }
  return rows.map((row) => {
    const name = text(row.name)
    const id = text(row.id)
    const objectId = text(row.objectId)
    const listLayoutId = text(row.listLayoutId) || text(row.viewId)
    const parentNames = ancestors(row)
    return {
      id,
      name,
      path: [...parentNames, name].join(' / '),
      objectId,
      viewId: listLayoutId !== '' ? listLayoutId : id,
      parentNames,
    }
  })
}

async function readMenus(session: EgmpSession): Promise<FlatMenu[]> {
  try {
    const response = await call(session, 'GET', '/api/platform/Menu/GetUserMenuPermission')
    return menusFromRows(response.data)
  } catch {
    return []
  }
}

interface MatrixRow {
  id: string
  name: string
  code: string
  status: number
}

async function readMatrixRows(session: EgmpSession): Promise<MatrixRow[]> {
  try {
    const response = await call(session, 'POST', '/api/platform/ApproveMatrix/GetConfigApproveMatrixPagedList', {
      pageIndex: 1,
      pageSize: 500,
      filters: [],
    })
    return rowsOf(response).map((row) => ({
      id: text(row.id),
      name: text(row.name),
      code: text(row.code),
      status: number(row.status),
    })).filter((row) => row.id !== '')
  } catch {
    return []
  }
}

function readCount(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  const data = record(value).data
  if (typeof data === 'number' && Number.isFinite(data)) return data
  for (const candidate of [data, value]) {
    const row = record(candidate)
    for (const key of ['count', 'total', 'totalCount', 'dataCount']) {
      const n = Number(row[key])
      if (Number.isFinite(n)) return n
    }
  }
  return null
}

/**
 * 菜单本身没有 viewId；业务数据条数要用该菜单对象的列表布局 id 作为 viewId。
 * 实测：GET /api/platform/Listlayout/List?objectId=... → data[0].id。
 */
const viewIdCache = new Map<string, string>()

async function readViewId(session: EgmpSession, objectId: string): Promise<string> {
  if (objectId === '') return ''
  const cached = viewIdCache.get(objectId)
  if (cached !== undefined) return cached
  try {
    const response = await call(session, 'GET', '/api/platform/Listlayout/List', undefined, { objectId })
    const rows = Array.isArray(response.data) ? response.data : []
    const first = record(rows[0])
    const id = text(first.id)
    viewIdCache.set(objectId, id)
    return id
  } catch {
    viewIdCache.set(objectId, '')
    return ''
  }
}

/** 读取一个业务对象（菜单管理对象）的数据条数；接口不兼容时返回 null。 */
async function readRecordCount(session: EgmpSession, menu: MatrixMenu): Promise<number | null> {
  const viewId = menu.viewId !== '' && menu.viewId !== menu.id
    ? menu.viewId
    : await readViewId(session, menu.objectId)
  if (viewId === '') return null
  const attempts: Array<() => Promise<Record<string, unknown>>> = [
    () => call(session, 'GET', '/api/platform/Record/GetViewDataCount', { viewId }),
    () => call(session, 'POST', '/api/platform/Record/GetViewDataCountWithFilter', {
      viewId,
      filters: [],
      pageIndex: 1,
      pageSize: 1,
    }),
  ]
  for (const attempt of attempts) {
    try {
      const response = await attempt()
      const count = readCount(response)
      if (count !== null) return count
    } catch {
      // 换下一种已实测候选；都失败就返回 null。
    }
  }
  return null
}

/**
 * 同一矩阵可能同时有“配置菜单”和“业务数据菜单”；
 * 配置菜单的 objectId 不一定有列表布局，必须逐个候选尝试，不能只看第一个。
 */
async function readRecordCountForMenus(
  session: EgmpSession,
  menus: MatrixMenu[],
): Promise<number | null> {
  for (const menu of menus) {
    const count = await readRecordCount(session, menu)
    if (count !== null) return count
  }
  return null
}

/**
 * @param workflows 已展开的工作流；调用方已过滤为“挂在启用生命周期状态上”的流程。
 */
export async function readMatrixAnalysis(
  session: EgmpSession,
  workflows: AnalysisWorkflow[],
): Promise<MatrixAnalysis> {
  const warnings: string[] = []
  const usageById = new Map<string, MatrixUsage[]>()
  const triggerUncertain: string[] = []
  for (const workflow of workflows) {
    if (workflow.triggeredBy.length === 0) triggerUncertain.push(workflow.name)
    for (const step of workflow.steps) {
      for (const control of step.startControls) {
        if (control.matrixId === null) continue
        // 开始控件由任务节点的 userControlIds 引用；这就是“控件应用到了哪个节点”。
        const applied = workflow.steps.filter((candidate) =>
          candidate.task !== null && candidate.task.userControlIds.includes(control.id),
        )
        const list = usageById.get(control.matrixId) ?? []
        list.push({
          workflowId: workflow.basicId,
          workflowName: workflow.name,
          statusNames: workflow.statusBindings.map((binding) => binding.statusName),
          controlId: control.id,
          controlName: control.name,
          appliedStepIds: applied.map((candidate) => candidate.id),
          appliedStepNames: applied.length > 0
            ? applied.map((candidate) => candidate.name)
            : ['开始节点（未绑定任务）'],
          triggerLabel: workflow.triggeredBy.length > 0
            ? workflow.triggeredBy.map((trigger) => `${trigger.statusName} / ${trigger.actionName}`).join('、')
            : '状态绑定（未反查到动作）',
        })
        usageById.set(control.matrixId, list)
      }
    }
  }
  if (usageById.size === 0) {
    return { matrices: [], warnings: [] }
  }

  const [rows, menus] = await Promise.all([readMatrixRows(session), readMenus(session)])
  const rowById = new Map(rows.map((row) => [row.id, row]))
  if (rows.length === 0) warnings.push('审批矩阵清单读取失败，矩阵名称/状态可能不完整。')
  if (menus.length === 0) warnings.push('菜单清单读取失败，无法判断审批矩阵菜单是否存在。')

  const matrices: AnalysisMatrix[] = []
  for (const [id, usage] of usageById) {
    const row = rowById.get(id)
    const name = row?.name ?? usage[0]?.controlName ?? id
    const code = row?.code ?? ''
    const status = row?.status ?? 0
    const matrixWarnings: string[] = []
    const sameName = menus.filter((menu) => normalized(menu.name) === normalized(name) && menu.objectId !== '')
    const candidates = sameName.length > 0
      ? sameName
      : menus.filter((menu) => {
          if (menu.objectId === '') return false
          const a = normalized(menu.name)
          const b = normalized(name)
          // 至少要求是“某某矩阵”级别，避免把只有“变更”两字的业务菜单当成矩阵菜单。
          if (a.length < 3) return false
          return a.includes(b) || b.includes(a)
        })
    const matched = candidates.filter((menu) => menu.parentNames.some((parent) => normalized(parent).includes('审批矩阵')))
    const menuList = matched.map((menu) => ({
      id: menu.id,
      name: menu.name,
      path: menu.path,
      objectId: menu.objectId,
      viewId: menu.viewId,
    }))
    let recordCount: number | null = null
    if (row === undefined) matrixWarnings.push('审批矩阵清单里找不到这个 id，名称/状态按工作流控件回填。')
    if (status !== 1) matrixWarnings.push(`审批矩阵当前 status=${status}，不是启用状态。`)
    if (menuList.length === 0) {
      matrixWarnings.push('没有找到同名业务菜单。')
    } else {
      recordCount = await readRecordCountForMenus(session, menuList)
      if (recordCount === null) matrixWarnings.push('菜单已找到，但业务数据条数读取失败。')
      else if (recordCount === 0) matrixWarnings.push('菜单存在，但业务数据为 0 条。')
    }
    if (matrixWarnings.length > 0) warnings.push(`${name}：${matrixWarnings.join('；')}`)
    matrices.push({
      id,
      name,
      code,
      status,
      isEnabled: status === 1,
      menus: menuList,
      menuFound: menuList.length > 0,
      recordCount,
      usage,
      warnings: matrixWarnings,
    })
  }
  matrices.sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'))
  if (triggerUncertain.length > 0) {
    warnings.push(`以下工作流未反查到用户动作/进入动作触发，按挂载的启用状态纳入矩阵检查：${triggerUncertain.join('、')}`)
  }
  return { matrices, warnings }
}
