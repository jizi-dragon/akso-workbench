/**
 * 对象分析的「工作流展开」：把状态动作上挂的 workflowBasicId 展开成
 * workflowConfigId + 步骤 + 分支，供文档和 drawio 使用。
 *
 * 只读；只调用已实测的：
 *   - POST /api/platform/Workflow/GetWorkflowBasicPageViewList
 *   - GET  /api/platform/Workflow/GetWorkflowSteps
 *   - GET  /api/platform/Workflow/GetWorkflowStepWithDetails
 */
import { call, type EgmpSession } from './config/session.ts'
import { workflowStepTypeLabel } from './shared/workflow-labels.ts'

export interface WorkflowActionEffect {
  behaviorType: number
  behaviorLabel: string
  /** 当前对象生命周期的目标状态；关联对象的状态放在 relatedStatusId。 */
  targetStatusId: string | null
  targetStatusName: string | null
  relatedObjectId: string | null
  relatedStatusId: string | null
  workflowId: string | null
  description: string
}

export interface WorkflowTaskView {
  isNotifyTaskType: boolean
  isRequireApproval: boolean
  notifyType: number | null
  approvals: string[]
  userControlIds: string[]
}

export interface WorkflowDecisionRule {
  name: string
  nextStepId: string
  nextStepName: string
  condition: string
}

export interface WorkflowDecisionView {
  rules: WorkflowDecisionRule[]
  elseNextStepId: string | null
  elseNextStepName: string | null
}

export interface AnalysisWorkflowStep {
  id: string
  code: string
  name: string
  stepType: number
  sort: number
  isEnable: boolean
  nextStepId: string | null
  nextStepName: string | null
  task: WorkflowTaskView | null
  decision: WorkflowDecisionView | null
  actions: WorkflowActionEffect[]
  detailReadable: boolean
}

export interface AnalysisWorkflowTrigger {
  statusId: string
  statusName: string
  actionId: string
  actionName: string
  actionKind: 'user' | 'enter'
}

export interface AnalysisWorkflowEdge {
  from: string
  to: string
  label: string
  kind: 'next' | 'decision' | 'else'
}

export interface AnalysisWorkflow {
  basicId: string
  configId: string
  name: string
  code: string
  status: number
  versionNo: number
  isAllowTrigger: boolean
  triggeredBy: AnalysisWorkflowTrigger[]
  /** 工作流行上的 lifecycleStatusIds；有的流程没有状态动作直连，但有状态绑定。 */
  statusBindings: Array<{ statusId: string; statusName: string }>
  steps: AnalysisWorkflowStep[]
  edges: AnalysisWorkflowEdge[]
  warnings: string[]
}

export interface WorkflowSourceAction {
  id: string
  name: string
  description: string
  kind: 'user' | 'enter'
  workflowIds: string[]
}

export interface WorkflowSourceStatus {
  id: string
  name: string
  userActions: WorkflowSourceAction[]
  enterActions: WorkflowSourceAction[]
}

export interface WorkflowAnalysisResult {
  workflows: AnalysisWorkflow[]
  names: Map<string, string>
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function number(value: unknown, fallback = 0): number {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

function bool(value: unknown): boolean {
  return value === true
}

function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

/** 兼容 data / data.datas / data.rows 三种分页形状。 */
function rowsOf(response: Record<string, unknown>): Record<string, unknown>[] {
  const data = response.data
  if (Array.isArray(data)) return data.map(record)
  const box = record(data)
  for (const key of ['datas', 'data', 'rows', 'items']) {
    if (Array.isArray(box[key])) return (box[key] as unknown[]).map(record)
  }
  return []
}

function pickWorkflowRow(rows: Record<string, unknown>[], basicId: string): Record<string, unknown> | null {
  const candidates = rows.filter((row) => text(row.workflowBasicId) === basicId)
  if (candidates.length === 0) return null
  const published = candidates.filter((row) => number(row.status) === 20)
  const pool = published.length > 0 ? published : candidates
  return [...pool].sort((a, b) => number(b.versionNo) - number(a.versionNo))[0] ?? null
}

function collectTriggers(statuses: WorkflowSourceStatus[], basicId: string): AnalysisWorkflowTrigger[] {
  const triggers: AnalysisWorkflowTrigger[] = []
  for (const status of statuses) {
    for (const action of [...status.userActions, ...status.enterActions]) {
      if (action.workflowIds.includes(basicId)) {
        triggers.push({
          statusId: status.id,
          statusName: status.name,
          actionId: action.id,
          actionName:
            action.name === '（未命名）' || action.name === ''
              ? action.description || '（未命名）'
              : action.name,
          actionKind: action.kind,
        })
      }
    }
  }
  return triggers
}

function taskView(step: Record<string, unknown>): WorkflowTaskView | null {
  const task = record(step.stepTaskConfig)
  if (Object.keys(task).length === 0) return null
  const approvals = array(step.stepTaskApprovals)
    .map(record)
    .map((row) => text(row.name) || text(row.buttonName) || text(row.code))
    .filter((value) => value !== '')
  const controls = text(task.userControlIds)
    .split(',')
    .map((value) => value.trim())
    .filter((value) => value !== '')
  return {
    isNotifyTaskType: bool(task.isNotifyTaskType),
    isRequireApproval: bool(task.isRequireApproval),
    notifyType: task.notifyType === undefined || task.notifyType === null ? null : number(task.notifyType),
    approvals,
    userControlIds: controls,
  }
}

function decisionView(step: Record<string, unknown>): WorkflowDecisionView | null {
  const decision = record(step.stepDecision)
  if (Object.keys(decision).length === 0) return null
  const rules = array(decision.rules).map((row) => {
    const rule = record(row)
    const condition = record(rule.condition)
    return {
      name: text(rule.name) || text(condition.conditionName) || '规则',
      nextStepId: text(rule.nextStepId),
      nextStepName: text(rule.nextStepName),
      condition: text(rule.conditionStr) || text(condition.conditionName) || '',
    }
  })
  const elseNextStepId = text(decision.nextStepId)
  return {
    rules,
    elseNextStepId: elseNextStepId === '' ? null : elseNextStepId,
    elseNextStepName: text(decision.nextStepName) || null,
  }
}

function actionEffects(
  step: Record<string, unknown>,
  behaviorMap: Map<number, string>,
  statusNames: Map<string, string>,
): WorkflowActionEffect[] {
  const effects: WorkflowActionEffect[] = []
  const stepAction = record(step.stepAction)
  for (const action of array(stepAction.actions).map(record)) {
    for (const config of array(action.configs).map(record)) {
      const behaviorType = number(config.behaviorType)
      if (behaviorType <= 0) continue
      const value = record(config.behaviorValue)
      const relatedObjectId = text(value.relatedObjectId)
      const rawStatusId = text(value.statusId) || text(value.targetStatusId)
      const targetStatusId = relatedObjectId === '' && rawStatusId !== '' ? rawStatusId : null
      const relatedStatusId = relatedObjectId !== '' && rawStatusId !== '' ? rawStatusId : null
      effects.push({
        behaviorType,
        behaviorLabel: behaviorMap.get(behaviorType) ?? `行为 ${behaviorType}`,
        targetStatusId,
        targetStatusName: targetStatusId === null ? null : statusNames.get(targetStatusId) ?? null,
        relatedObjectId: relatedObjectId === '' ? null : relatedObjectId,
        relatedStatusId,
        workflowId: text(value.workflowId) || null,
        description: text(action.description) || text(step.name),
      })
    }
  }
  return effects
}

function buildEdges(steps: AnalysisWorkflowStep[]): AnalysisWorkflowEdge[] {
  const edges: AnalysisWorkflowEdge[] = []
  const seen = new Set<string>()
  const add = (from: string, to: string, label: string, kind: AnalysisWorkflowEdge['kind']): void => {
    if (from === '' || to === '' || from === to) return
    const key = `${from}|${to}|${kind}|${label}`
    if (seen.has(key)) return
    seen.add(key)
    edges.push({ from, to, label, kind })
  }
  for (const step of steps) {
    if (step.nextStepId !== null) add(step.id, step.nextStepId, '下一步', 'next')
    if (step.decision !== null) {
      for (const rule of step.decision.rules) {
        add(step.id, rule.nextStepId, rule.name, 'decision')
      }
      if (step.decision.elseNextStepId !== null) add(step.id, step.decision.elseNextStepId, '否则', 'else')
    }
  }
  return edges
}

export async function readWorkflowAnalysis(
  session: EgmpSession,
  statuses: WorkflowSourceStatus[],
  behaviorMap: Map<number, string>,
  objectId: string,
): Promise<WorkflowAnalysisResult> {
  const directIds = new Set<string>()
  for (const status of statuses) {
    for (const action of [...status.userActions, ...status.enterActions]) {
      for (const id of action.workflowIds) {
        if (id !== '') directIds.add(id)
      }
    }
  }
  const result: WorkflowAnalysisResult = { workflows: [], names: new Map() }
  if (directIds.size === 0 && objectId === '') return result

  let rows: Record<string, unknown>[] = []
  try {
    const response = await call(session, 'POST', '/api/platform/Workflow/GetWorkflowBasicPageViewList', {
      pageIndex: 1,
      pageSize: 1000,
      filters: [],
    })
    rows = rowsOf(response)
  } catch {
    for (const id of directIds) result.names.set(id, id)
    return result
  }

  const basicIds = new Set<string>(directIds)
  for (const row of rows) {
    if (objectId !== '' && text(row.objectId) === objectId) {
      const id = text(row.workflowBasicId)
      if (id !== '') basicIds.add(id)
    }
  }

  const statusNames = new Map(statuses.map((status) => [status.id, status.name]))
  for (const basicId of basicIds) {
    const row = pickWorkflowRow(rows, basicId)
    const statusIds = row === null ? [] : text(row.lifecycleStatusIds).split(',').map((value) => value.trim()).filter((value) => value !== '')
    const statusNameList = row === null ? [] : text(row.lifeStatusName).split(',').map((value) => value.trim())
    const statusBindings = statusIds.map((statusId, index) => ({
      statusId,
      statusName: statusNames.get(statusId) ?? statusNameList[index] ?? statusId,
    }))
    const workflow: AnalysisWorkflow = {
      basicId,
      configId: row === null ? '' : text(row.id),
      name: row === null ? basicId : text(row.name) || basicId,
      code: row === null ? '' : text(row.code),
      status: row === null ? 0 : number(row.status),
      versionNo: row === null ? 0 : number(row.versionNo),
      isAllowTrigger: row === null ? false : bool(row.isAllowTrigger),
      triggeredBy: collectTriggers(statuses, basicId),
      statusBindings,
      steps: [],
      edges: [],
      warnings: [],
    }
    result.names.set(basicId, workflow.name)
    if (row === null || workflow.configId === '') {
      workflow.warnings.push('工作流清单里找不到这条 workflowBasicId 对应的配置；无法读步骤。')
      result.workflows.push(workflow)
      continue
    }
    if (workflow.status !== 20) {
      workflow.warnings.push(`工作流当前 status=${workflow.status}，不是已发布状态 20；步骤可能是草稿。`)
    }

    let rawSteps: Record<string, unknown>[] = []
    try {
      const response = await call(session, 'GET', '/api/platform/Workflow/GetWorkflowSteps', undefined, {
        workflowConfigId: workflow.configId,
      })
      rawSteps = rowsOf(response)
    } catch (error) {
      workflow.warnings.push(`读取工作流步骤失败：${error instanceof Error ? error.message : String(error)}`)
      result.workflows.push(workflow)
      continue
    }

    const detailed = await Promise.all(
      rawSteps.map(async (raw) => {
        const stepId = text(raw.id)
        if (stepId === '') return { raw, detail: raw, readable: false }
        try {
          const response = await call(session, 'GET', '/api/platform/Workflow/GetWorkflowStepWithDetails', undefined, {
            id: stepId,
          })
          const data = record(response.data)
          const detail = record(data.step)
          return { raw, detail: Object.keys(detail).length > 0 ? { ...raw, ...detail } : raw, readable: Object.keys(detail).length > 0 }
        } catch {
          return { raw, detail: raw, readable: false }
        }
      }),
    )

    for (const item of detailed) {
      const step = item.detail
      const stepId = text(step.id) || text(item.raw.id)
      if (stepId === '') continue
      const stepType = number(step.stepType)
      const view: AnalysisWorkflowStep = {
        id: stepId,
        code: text(step.code),
        name: text(step.name) || text(step.code) || '（未命名）',
        stepType,
        sort: number(step.sort),
        isEnable: step.isEnable !== false,
        nextStepId: text(step.nextStepId) === '' ? null : text(step.nextStepId),
        nextStepName: text(step.nextStepName) === '' ? null : text(step.nextStepName),
        task: taskView(step),
        decision: decisionView(step),
        actions: actionEffects(step, behaviorMap, statusNames),
        detailReadable: item.readable,
      }
      workflow.steps.push(view)
      if (!item.readable) workflow.warnings.push(`步骤「${view.name}」的详情读取失败，只能读到外壳。`)
    }
    workflow.steps.sort((a, b) => a.sort - b.sort)
    workflow.edges = buildEdges(workflow.steps)
    if (workflow.steps.length === 0) workflow.warnings.push('GetWorkflowSteps 返回空数组。')
    if (workflow.triggeredBy.length === 0) {
      if (workflow.statusBindings.length > 0) {
        workflow.warnings.push('状态动作没有直接引用这条工作流；按工作流上的生命周期状态绑定展示，实际触发点可能是状态事件。')
      } else {
        workflow.warnings.push('没有状态动作直接引用这条工作流，也没有生命周期状态绑定；可能是对象级手动/事件流程。')
      }
    }
    result.workflows.push(workflow)
  }
  return result
}
