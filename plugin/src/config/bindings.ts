/**
 * 配置计划的输出绑定。
 *
 * 计划在生成时只能看到“平台当前状态”，看不到本次执行中前序步骤刚生成的 id。
 * 输出绑定把这类参数写成引用，审批时锁定引用模板，执行时按前序步骤的真实返回填充：
 *
 *   { "$ref": "steps.s1.after" }
 *   { "$ref": "steps.s1.after.workflowConfigId" }
 *   { "$ref": "stages.1.steps.s1.after" }
 *
 * 约定：
 *   - 只有对象形态、且对象**只能有一个** `$ref` 键；不把 `$ref` 混进普通参数；
 *   - `steps.<id>.after` 表示同一计划内更早步骤的返回；
 *   - `stages.<n>.steps.<id>.after` 表示任务级计划中第 n 个阶段（1-based）的某步返回；
 *   - 引用缺失、指向未来步骤、跨阶段指向当前/未来阶段，都在计划或执行前报错；
 *   - 审批指纹保留原始 `$ref`（planHash/taskHash 已覆盖 params/blueprint），
 *     防止“审批时是一套值、执行时换成另一套值”。
 */
import { EgmpError } from './session.ts'

export const BINDING_KEY = '$ref'

export interface BindingContext {
  /** 当前计划内已执行步骤的 after。 */
  steps?: Record<string, unknown>
  /** 任务级执行时，已完成阶段（1-based）的 stepId → after。 */
  stages?: Record<number, Record<string, unknown>>
}

interface ParsedRef {
  kind: 'step' | 'stage'
  stepId: string
  stage: number | null
  path: string
}

const STEP_REF = /^steps\.([A-Za-z0-9_-]+)\.after(.*)$/
const STAGE_REF = /^stages\.(\d+)\.steps\.([A-Za-z0-9_-]+)\.after(.*)$/


function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function badRef(ref: string, message: string): never {
  throw new EgmpError(`输出绑定 ${ref} ${message}`, { code: 'BINDING_INVALID' })
}

export function parseBindingRef(ref: string): ParsedRef {
  const text = ref.trim()
  const stage = STAGE_REF.exec(text)
  if (stage !== null) {
    const path = stage[3] ?? ''
    if (path !== '' && !path.startsWith('.') && !path.startsWith('[')) badRef(text, '的路径不合法')
    return { kind: 'stage', stage: Number(stage[1]), stepId: stage[2] ?? '', path }
  }
  const step = STEP_REF.exec(text)
  if (step !== null) {
    const path = step[2] ?? ''
    if (path !== '' && !path.startsWith('.') && !path.startsWith('[')) badRef(text, '的路径不合法')
    return { kind: 'step', stage: null, stepId: step[1] ?? '', path }
  }
  return badRef(text, '格式不合法；应为 steps.<stepId>.after[.path] 或 stages.<n>.steps.<stepId>.after[.path]')
}

/** 递归收集参数里的绑定引用。 */
export function collectBindingRefs(value: unknown, out: string[] = []): string[] {
  if (Array.isArray(value)) {
    for (const item of value) collectBindingRefs(item, out)
    return out
  }
  if (!isRecord(value)) return out
  const keys = Object.keys(value)
  if (keys.includes(BINDING_KEY)) {
    if (keys.length !== 1 || typeof value[BINDING_KEY] !== 'string' || value[BINDING_KEY].trim() === '') {
      throw new EgmpError('输出绑定对象只能有一个非空字符串 $ref，不能混入其他参数', { code: 'BINDING_INVALID' })
    }
    out.push(value[BINDING_KEY].trim())
    return out
  }
  for (const item of Object.values(value)) collectBindingRefs(item, out)
  return out
}

function tokenizePath(path: string): Array<string | number> | null {
  const tokens: Array<string | number> = []
  let index = 0
  while (index < path.length) {
    if (path[index] === '.') {
      index += 1
      continue
    }
    if (path[index] === '[') {
      const close = path.indexOf(']', index)
      if (close < 0) return null
      const number = path.slice(index + 1, close)
      if (!/^\d+$/.test(number)) return null
      tokens.push(Number(number))
      index = close + 1
      continue
    }
    const start = index
    while (index < path.length && path[index] !== '.' && path[index] !== '[' && path[index] !== ']') index += 1
    if (index === start) return null
    tokens.push(path.slice(start, index))
  }
  return tokens
}

function readPath(value: unknown, path: string): unknown {
  if (path === '') return value
  const tokens = tokenizePath(path.replace(/^\./, ''))
  if (tokens === null) return undefined
  let current = value
  for (const token of tokens) {
    if (typeof token === 'number') {
      if (!Array.isArray(current) || token >= current.length) return undefined
      current = current[token]
    } else {
      if (!isRecord(current) || !(token in current)) return undefined
      current = current[token]
    }
  }
  return current
}

function lookup(ref: string, parsed: ParsedRef, context: BindingContext): unknown {
  if (parsed.kind === 'step') {
    if (context.steps === undefined || !(parsed.stepId in context.steps)) {
      throw new EgmpError(`输出绑定 ${ref} 指向的步骤 ${parsed.stepId} 尚未产生输出（或不在当前计划内）`, { code: 'BINDING_UNRESOLVED' })
    }
    const value = readPath(context.steps[parsed.stepId], parsed.path)
    if (value === undefined) throw new EgmpError(`输出绑定 ${ref} 在步骤 ${parsed.stepId} 的输出里找不到路径`, { code: 'BINDING_UNRESOLVED' })
    return value
  }
  const stage = parsed.stage ?? 0
  const stageOutputs = context.stages?.[stage]
  if (stageOutputs === undefined) {
    throw new EgmpError(`输出绑定 ${ref} 指向的阶段 ${stage} 尚未执行；跨阶段引用只能在任务级计划中使用`, { code: 'BINDING_UNRESOLVED' })
  }
  if (!(parsed.stepId in stageOutputs)) {
    throw new EgmpError(`输出绑定 ${ref} 指向的阶段 ${stage} 步骤 ${parsed.stepId} 尚未产生输出`, { code: 'BINDING_UNRESOLVED' })
  }
  const value = readPath(stageOutputs[parsed.stepId], parsed.path)
  if (value === undefined) throw new EgmpError(`输出绑定 ${ref} 在阶段 ${stage} 步骤 ${parsed.stepId} 的输出里找不到路径`, { code: 'BINDING_UNRESOLVED' })
  return value
}

/** 深拷贝并解析参数里的所有 $ref；原始 params 保持不变（审批指纹依赖它）。 */
export function resolveBindings(value: unknown, context: BindingContext): unknown {
  if (Array.isArray(value)) return value.map((item) => resolveBindings(item, context))
  if (!isRecord(value)) return value
  const keys = Object.keys(value)
  if (keys.includes(BINDING_KEY)) {
    if (keys.length !== 1 || typeof value[BINDING_KEY] !== 'string' || value[BINDING_KEY].trim() === '') {
      throw new EgmpError('输出绑定对象只能有一个非空字符串 $ref，不能混入其他参数', { code: 'BINDING_INVALID' })
    }
    const ref = value[BINDING_KEY].trim()
    return lookup(ref, parseBindingRef(ref), context)
  }
  const out: Record<string, unknown> = {}
  for (const [key, item] of Object.entries(value)) out[key] = resolveBindings(item, context)
  return out
}

/**
 * 校验单计划内引用：`steps.` 只能指向更早步骤；`stages.` 留给任务级校验。
 * 返回错误列表，调用方决定是抛错还是展示。
 */
export function validatePlanBindings(steps: Array<{ id: string; params?: Record<string, unknown> }>): string[] {
  const errors: string[] = []
  const index = new Map(steps.map((step, position) => [step.id, position]))
  for (const [position, step] of steps.entries()) {
    let refs: string[]
    try {
      refs = collectBindingRefs(step.params ?? {})
    } catch (error) {
      errors.push(error instanceof Error ? error.message : String(error))
      continue
    }
    for (const ref of refs) {
      let parsed: ParsedRef
      try {
        parsed = parseBindingRef(ref)
      } catch (error) {
        errors.push(error instanceof Error ? error.message : String(error))
        continue
      }
      if (parsed.kind === 'stage') continue
      const target = index.get(parsed.stepId)
      if (target === undefined) errors.push(`输出绑定 ${ref} 指向不存在的步骤 ${parsed.stepId}`)
      else if (target >= position) errors.push(`输出绑定 ${ref} 只能引用更早步骤；${parsed.stepId} 在当前步骤之后或就是当前步骤`)
    }
  }
  return errors
}

/** 任务级校验：`stages.` 只能引用更早阶段，且目标步骤必须存在。 */
export function validateTaskBindings(stages: Array<Array<{ id: string; params?: Record<string, unknown> }>>): string[] {
  const errors: string[] = []
  for (const [position, steps] of stages.entries()) {
    for (const step of steps) {
      let refs: string[]
      try {
        refs = collectBindingRefs(step.params ?? {})
      } catch (error) {
        errors.push(error instanceof Error ? error.message : String(error))
        continue
      }
      for (const ref of refs) {
        let parsed: ParsedRef
        try {
          parsed = parseBindingRef(ref)
        } catch (error) {
          errors.push(error instanceof Error ? error.message : String(error))
          continue
        }
        if (parsed.kind !== 'stage') continue
        const stage = parsed.stage ?? 0
        if (stage < 1 || stage >= position + 1) {
          errors.push(`输出绑定 ${ref} 只能引用更早阶段（当前第 ${position + 1} 阶段）`)
          continue
        }
        const target = stages[stage - 1]?.find((item) => item.id === parsed.stepId)
        if (target === undefined) errors.push(`输出绑定 ${ref} 指向阶段 ${stage} 不存在的步骤 ${parsed.stepId}`)
      }
    }
  }
  return errors
}
