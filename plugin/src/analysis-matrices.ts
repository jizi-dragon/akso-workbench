/**
 * 对象分析的「审批矩阵」检查（只读）。
 *
 * 只分析同时满足以下条件的矩阵：
 *   1. 目标对象有启用中的生命周期状态（调用方只传启用状态）；
 *   2. 矩阵出现在「启用状态的用户动作/进入动作」直接触发的工作流开始节点上；
 *   3. 该矩阵是工作流里实际挂载的参与者矩阵。
 *
 * 对每个矩阵检查同名菜单是否存在，以及菜单对应管理对象是否有业务数据。
 * 查不到就写 warning，不猜。
 */
import { call, type EgmpSession } from './config/session.ts'
import type { AnalysisWorkflow } from './analysis-workflow.ts'

export interface MatrixUsage {
  workflowId: string
  workflowName: string
  stepId: string
  stepName: string
  controlId: string
  controlName: string
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

function flattenMenus(value: unknown, parentNames: string[] = [], out: FlatMenu[] = []): FlatMenu[] {
  for (const raw of array(value)) {
    const row = record(raw)
    const name = text(row.name)
    if (name === '') continue
    const pathNames = [...parentNames, name]
    const id = text(row.id)
    const objectId = text(row.objectId)
    const listLayoutId = text(row.listLayoutId) || text(row.viewId)
    out.push({
      id,
      name,
      path: pathNames.join(' / '),
      objectId,
      viewId: listLayoutId !== '' ? listLayoutId : id,
      parentNames,
    })
    const children = row.children ?? row.childMenus ?? row.items
    if (Array.isArray(children)) flattenMenus(children, pathNames, out)
  }
  return out
}

async function readMenus(session: EgmpSession): Promise<FlatMenu[]> {
  try {
    const response = await call(session, 'GET', '/api/platform/Menu/GetUserMenuPermission')
    return flattenMenus(response.data)
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
    const response = await call(session, 'GET', '/api/platform/Listlayout/List', { objectId })
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
 * @param workflows 已展开的工作流；只有 triggeredBy 非空的流程才参与矩阵检查。
 */
export async function readMatrixAnalysis(
  session: EgmpSession,
  workflows: AnalysisWorkflow[],
): Promise<MatrixAnalysis> {
  const warnings: string[] = []
  const usageById = new Map<string, MatrixUsage[]>()
  const triggerUncertain: string[] = []
  for (const workflow of workflows) {
    // 用户口径：只检查“启用状态的用户动作/进入动作触发的工作流”。
    // statusBindings 只是生命周期可用范围，不能当成触发点。
    if (workflow.triggeredBy.length === 0) {
      triggerUncertain.push(workflow.name)
      continue
    }
    for (const step of workflow.steps) {
      for (const control of step.startControls) {
        if (control.matrixId === null) continue
        const list = usageById.get(control.matrixId) ?? []
        list.push({
          workflowId: workflow.basicId,
          workflowName: workflow.name,
          stepId: step.id,
          stepName: step.name,
          controlId: control.id,
          controlName: control.name,
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
          const a = normalized(menu.name)
          const b = normalized(name)
          return menu.objectId !== '' && (a.includes(b) || b.includes(a))
        })
    const matched = candidates.filter((menu) => menu.parentNames.some((parent) => normalized(parent).includes('审批矩阵')))
    const menuList = (matched.length > 0 ? matched : candidates).map((menu) => ({
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
      const firstMenu = menuList[0]
      if (firstMenu === undefined) {
        matrixWarnings.push('菜单列表为空，无法读取业务数据。')
      } else {
        recordCount = await readRecordCount(session, firstMenu)
        if (recordCount === null) matrixWarnings.push('菜单已找到，但业务数据条数读取失败。')
        else if (recordCount === 0) matrixWarnings.push('菜单存在，但业务数据为 0 条。')
      }
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
    warnings.push(`以下工作流没有绑定用户动作/进入动作触发，未纳入矩阵检查：${triggerUncertain.join('、')}`)
  }
  return { matrices, warnings }
}
