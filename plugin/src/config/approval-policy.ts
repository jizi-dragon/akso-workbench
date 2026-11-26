/**
 * 审批范围判定（host 半边）。
 *
 * 审批门只该挡「会改平台状态」的步骤。查询类接口（`query.*`、`*.list`、`*.detail`、
 * 以及能力目录里标了 `readonly: true` 的 POST 查询）执行后什么都不改，
 * 却因为被当成写入步骤而弹出审批窗 —— 人一不留神点了「拒绝」，整份计划连读都读不到。
 *
 * 这里只有一条判据：「这一步会不会写」。判定顺序：
 *   1. `action === 'skip'`（已存在且一致）不算；
 *   2. 步骤自带 `readonly` 标记（蓝图里的 `call` 步骤在建计划时按能力目录写好）优先；
 *   3. 能力目录 `readonly: true` 或 `method === 'GET'` 视为只读；
 *   4. 其余一律算写入 —— 拿不到能力元数据时宁可要求审批，也不能悄悄放行。
 *
 * 工具（`config_apply`）与引擎（`applyPlan` / `applyTask`）必须用同一个函数判，
 * 否则会出现「工具跳过审批、引擎仍要求审批记录」这种自相矛盾的死路。
 */
import { findCapability } from './capabilities.ts'

/** 审批结论的原因：有写入要审 / 全是只读不用审 / 页面或环境变量关掉了审批。 */
export type ApprovalReason = 'writes' | 'read-only' | 'disabled'

export interface ApprovalScope {
  /** 要不要弹审批窗。 */
  needed: boolean
  reason: ApprovalReason
  /** 会改平台状态的步骤数。 */
  writes: number
  /** 只读查询步骤数。 */
  readonly: number
  /** 已存在且一致的步骤数。 */
  skipped: number
}

/** 最小结构：Step 与本模块之间不需要互相 import。 */
export interface ScopeStep {
  action: string
  capabilityId: string
  readonly?: boolean
}

export function isReadOnlyCapability(capabilityId: string): boolean {
  const cap = findCapability(capabilityId)
  if (cap === undefined) return false
  return cap.readonly === true || cap.method === 'GET'
}

export function isWriteStep(step: ScopeStep): boolean {
  if (step.action === 'skip') return false
  if (step.readonly === true) return false
  return !isReadOnlyCapability(step.capabilityId)
}

export function writeSteps<T extends ScopeStep>(steps: readonly T[]): T[] {
  return steps.filter((step) => isWriteStep(step))
}

/**
 * 一次执行的范围。`requireApproval` 来自环境变量 / 页面开关，
 * 但**只读执行不受它影响** —— 只读本来就不需要审批，关不关开关都一样。
 */
export function approvalScope(steps: readonly ScopeStep[], requireApproval: boolean): ApprovalScope {
  const writes = writeSteps(steps).length
  const readonly = steps.filter((step) => step.action !== 'skip' && !isWriteStep(step)).length
  const skipped = steps.length - writes - readonly
  if (writes === 0) return { needed: false, reason: 'read-only', writes, readonly, skipped }
  if (!requireApproval) return { needed: false, reason: 'disabled', writes, readonly, skipped }
  return { needed: true, reason: 'writes', writes, readonly, skipped }
}

/** 结果里给人看的一句话；`config_apply` 每条返回都带它，避免“为什么没弹窗”说不清。 */
export function describeScope(scope: ApprovalScope): string {
  if (scope.reason === 'read-only') {
    return `本次 ${scope.readonly} 步全是只读查询，不需要审批，直接执行。`
  }
  if (scope.reason === 'disabled') {
    return `本机已关闭写入审批开关：${scope.writes} 步写入直接执行（只读 ${scope.readonly} 步）。`
  }
  return `本次 ${scope.writes} 步写入需要审批${scope.readonly > 0 ? `，另有 ${scope.readonly} 步只读查询随同执行` : ''}。`
}
