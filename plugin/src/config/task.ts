/**
 * 任务级配置：一条任务 = 一串**有序阶段**，整条任务只过一次人工审批。
 *
 * 为什么不是"把多个计划一次性生成"：
 *  - 字段/列表布局要用**上一步刚建出来的对象**，菜单要用刚建出来的布局，
 *    工作流又要用上一步返回的节点 id —— 计划必须在执行到那一段时按当时的平台状态重算；
 *  - 审批不该按"段"重复弹窗：人审的是「这次任务要动哪些资源」，不是每一段的 diff。
 *
 * 所以这里把**阶段蓝图**当作审批对象：
 *  - `taskHash` 只覆盖 env / target / 每个阶段的蓝图（资源清单与参数），**不覆盖 diff**；
 *  - 执行时逐段 `buildPlan` 重新算 diff，按顺序 apply，失败即停；
 *  - 审批记录一把一用：任务开跑前就标记 usedAt，中途失败要重新出任务、重新审批。
 */
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import { validateBlueprint, type Blueprint } from './blueprint.ts'
import {
  applyPlan,
  buildPlan,
  consumeApproval,
  readApproval,
  savePlan,
  type Plan,
  type Step,
  type StepResult,
} from './engine.ts'
import { dataDir } from '../store.ts'
import { EgmpError, type EgmpSession } from './session.ts'

export interface TaskStage {
  /** 人话阶段名，审核文档与结果里用；缺省按顺序编号。 */
  name: string
  blueprint: Blueprint
}

export interface TaskStageResult {
  stage: number
  name: string
  planId: string | null
  status: 'ok' | 'failed' | 'skipped'
  message: string
  steps: StepResult[]
}

export interface Task {
  taskId: string
  createdAt: number
  expiresAt: number
  name: string
  env: string
  target: { baseUrl: string; accountId: string | null; account: string }
  stages: TaskStage[]
  status: 'planned' | 'applied' | 'failed'
  results: TaskStageResult[]
}

const TASK_TTL_MS = 60 * 60 * 1000

function newTaskId(): string {
  return `tsk_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`
}

/** 与 engine 的 planHash 一样，按键排序后序列化，避免对象键顺序影响指纹。 */
function stableStringify(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null'
  if (Array.isArray(value)) return `[${value.map((item) => stableStringify(item)).join(',')}]`
  const record = value as Record<string, unknown>
  return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${stableStringify(record[key])}`).join(',')}}`
}

/** 审批指纹：环境 + 账号 + 每段蓝图（含顺序）。 */
export function taskHash(task: Task): string {
  const material = {
    env: task.env,
    target: task.target,
    stages: task.stages.map((stage) => ({ name: stage.name, blueprint: stage.blueprint })),
  }
  return createHash('sha256').update(stableStringify(material)).digest('hex')
}

/** 解析工具入参里的 task JSON；出错时把原因说成人话。 */
export function parseTaskInput(raw: unknown): { valid: true; name: string; stages: TaskStage[] } | { valid: false; error: string } {
  if (typeof raw !== 'string' || raw.trim() === '') return { valid: false, error: '缺少 task JSON' }
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch (error) {
    return { valid: false, error: `task 不是合法 JSON：${error instanceof Error ? error.message : String(error)}` }
  }
  const record = (parsed ?? {}) as Record<string, unknown>
  const name = typeof record.name === 'string' && record.name.trim() !== '' ? record.name.trim() : '配置任务'
  const rawStages = record.stages
  if (!Array.isArray(rawStages) || rawStages.length === 0) {
    return { valid: false, error: 'task.stages 必须是非空数组，每项形如 { name, blueprint }' }
  }
  if (rawStages.length > 12) return { valid: false, error: '一条任务最多 12 个阶段；请拆成多条任务' }

  const stages: TaskStage[] = []
  for (const [index, item] of rawStages.entries()) {
    const stage = (item ?? {}) as Record<string, unknown>
    const label = typeof stage.name === 'string' && stage.name.trim() !== '' ? stage.name.trim() : `第 ${index + 1} 阶段`
    const blueprint = stage.blueprint
    if (typeof blueprint !== 'object' || blueprint === null) {
      return { valid: false, error: `阶段 ${label} 缺少 blueprint 对象` }
    }
    const validation = validateBlueprint(blueprint as Blueprint)
    if (!validation.valid) {
      return { valid: false, error: `阶段 ${label} 蓝图校验失败：${validation.errors.join('；')}` }
    }
    stages.push({ name: label, blueprint: blueprint as Blueprint })
  }
  return { valid: true, name, stages }
}

function tasksDir(dshHome: string): string {
  return join(dataDir(dshHome), 'config-tasks')
}

const TASK_ID = /^tsk_[a-z0-9]{4,40}$/

export function saveTask(dshHome: string, task: Task): void {
  const dir = tasksDir(dshHome)
  mkdirSync(dir, { recursive: true, mode: 0o700 })
  writeFileSync(join(dir, `${task.taskId}.json`), `${JSON.stringify(task, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 })
}

export function loadTask(dshHome: string, taskId: string): Task | null {
  if (!TASK_ID.test(taskId)) return null
  const file = join(tasksDir(dshHome), `${taskId}.json`)
  if (!existsSync(file)) return null
  try {
    return JSON.parse(readFileSync(file, 'utf8')) as Task
  } catch {
    return null
  }
}

/** 生成一条任务（只算蓝图、不写平台）。 */
export function createTask(session: EgmpSession, input: { name: string; stages: TaskStage[] }): Task {
  const env = input.stages.flatMap((stage) => (typeof stage.blueprint.env === 'string' ? [stage.blueprint.env] : []))[0] ?? ''
  return {
    taskId: newTaskId(),
    createdAt: Date.now(),
    expiresAt: Date.now() + TASK_TTL_MS,
    name: input.name,
    env,
    target: { baseUrl: session.baseUrl, accountId: session.accountId ?? null, account: session.account },
    stages: input.stages,
    status: 'planned',
    results: [],
  }
}

/**
 * 按当前平台状态把每个阶段算成计划（不落盘）。
 * 审核文档与「有没有写入」的初判都靠它；执行时会再算一遍，拿到的才是最新 diff。
 */
export async function previewTaskPlans(session: EgmpSession, task: Task): Promise<Plan[]> {
  const plans: Plan[] = []
  for (const stage of task.stages) {
    plans.push(await buildPlan(session, stage.blueprint))
  }
  return plans
}

/**
 * 把各阶段计划拼成一份「假计划」给审批窗/审核文档用：
 * 步骤 id 加阶段前缀，标题带阶段名；`planId` 换成 taskId、`expiresAt` 换成任务有效期。
 */
export function mergeTaskPreview(task: Task, plans: Plan[]): Plan {
  const steps: Step[] = []
  const warnings: string[] = []
  const summary = { create: 0, update: 0, skip: 0, irreversible: 0 }
  for (const [index, plan] of plans.entries()) {
    const stage = task.stages[index]
    if (stage === undefined) continue
    for (const step of plan.steps) {
      const merged: Step = {
        ...step,
        id: `${index + 1}.${step.id}`,
        title: `${stage.name} · ${step.title}`,
      }
      steps.push(merged)
      if (merged.action === 'create') summary.create += 1
      else if (merged.action === 'update') summary.update += 1
      else summary.skip += 1
      if (merged.irreversible) summary.irreversible += 1
    }
    for (const warning of plan.warnings) warnings.push(`[${stage.name}] ${warning}`)
  }
  return {
    planId: task.taskId,
    createdAt: task.createdAt,
    expiresAt: task.expiresAt,
    env: task.env,
    target: task.target,
    summary,
    steps,
    warnings,
    status: 'planned',
    results: [],
  }
}

export interface TaskApplyOptions {
  /** 默认 true：任务级审批必须已经落盘、指纹一致且没用过。 */
  requireApproval?: boolean
}

/**
 * 顺序执行整条任务：每段现算计划 → 落盘 → 逐步执行（引擎内部再做目标/过期校验）。
 * 任一段失败即停，后面的阶段不会跑；已跑的段留在 `results` 里作为 checkpoint。
 */
export async function applyTask(
  session: EgmpSession,
  taskId: string,
  dshHome: string,
  options: TaskApplyOptions = {},
): Promise<Task> {
  const task = loadTask(dshHome, taskId)
  if (task === null) throw new EgmpError(`任务不存在：${taskId}`, { code: 'TASK_MISSING' })
  if (Date.now() > task.expiresAt) throw new EgmpError('任务已过期（60 分钟），请重新生成', { code: 'TASK_EXPIRED' })
  if (task.status === 'applied') return task

  if (options.requireApproval !== false) {
    const approval = readApproval(dshHome, taskId)
    if (approval === null) {
      throw new EgmpError('这条任务还没有经过人工审批，不能执行；请重新调用 apply，在弹窗里点「同意」', { code: 'TASK_APPROVAL_REQUIRED' })
    }
    if (approval.hash !== taskHash(task)) {
      throw new EgmpError('审批记录与任务内容不一致（任务可能被改过），已拒绝执行', { code: 'TASK_APPROVAL_MISMATCH' })
    }
    if (approval.usedAt !== undefined) {
      throw new EgmpError('这条任务的审批已经用过了，不能重复执行', { code: 'TASK_APPROVAL_USED' })
    }
    if (task.target.accountId !== null && (session.accountId === undefined || session.accountId !== task.target.accountId)) {
      throw new EgmpError(`任务绑定的账号与当前账号不一致（任务 ${task.target.accountId}，当前 ${session.accountId ?? '未知'}），已拒绝执行`, { code: 'TASK_TARGET_CHANGED' })
    }
    consumeApproval(dshHome, taskId)
  }

  const started = Date.now()
  for (const [index, stage] of task.stages.entries()) {
    let plan: Plan
    try {
      plan = await buildPlan(session, stage.blueprint)
    } catch (error) {
      task.status = 'failed'
      task.results.push({
        stage: index + 1,
        name: stage.name,
        planId: null,
        status: 'failed',
        message: `计划生成失败：${error instanceof Error ? error.message : String(error)}`,
        steps: [],
      })
      saveTask(dshHome, task)
      return task
    }
    savePlan(dshHome, plan)
    if (plan.steps.every((step) => step.action === 'skip')) {
      task.results.push({
        stage: index + 1,
        name: stage.name,
        planId: plan.planId,
        status: 'skipped',
        message: '这一阶段已经是目标状态，没有要写的东西',
        steps: [],
      })
      continue
    }
    try {
      const applied = await applyPlan(session, plan.planId, dshHome, { requireApproval: false })
      const failed = applied.results.some((result) => result.status === 'failed')
      task.results.push({
        stage: index + 1,
        name: stage.name,
        planId: plan.planId,
        status: failed ? 'failed' : 'ok',
        message: failed ? '这一阶段有步骤失败，后面的阶段没有执行' : `用时 ${Date.now() - started}ms（累计）`,
        steps: applied.results,
      })
      if (failed) {
        task.status = 'failed'
        saveTask(dshHome, task)
        return task
      }
    } catch (error) {
      task.status = 'failed'
      task.results.push({
        stage: index + 1,
        name: stage.name,
        planId: plan.planId,
        status: 'failed',
        message: error instanceof Error ? error.message : String(error),
        steps: [],
      })
      saveTask(dshHome, task)
      return task
    }
  }
  task.status = 'applied'
  saveTask(dshHome, task)
  return task
}
