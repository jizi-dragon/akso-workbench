/**
 * 自建审批：akso-workbench 自己的写入门。
 *
 * 为什么不复用 DSH 的 `ctx.approval`：`danger-full-access` 档把会话策略定成
 * `approval: never`，那个策略下所有 `ask` 都会被静默判拒（DSH 的定义就是
 * "auto-reject every ask without prompting"），写入这条路会被焊死；而且宿主弹窗
 * 只显示工具名与参数，对这个产品没意义 —— 我们要摊开的是**平台的请求体**。
 *
 * 这一层的定位要说清楚：**它防的是手滑，不是恶意模型**。判定端点跑在同一个
 * loopback HTTP 服务上，拿到本机 shell 的人（包括 agent 自己）都能伪造一次
 * 「允许」。真正不可伪造的是浏览器那一侧的人手，所以：
 *   - 工具只出「待批」而不写平台；
 *   - 批准之后，工具**下一次**调用才执行，并且批准单一次性核销；
 *   - 每一张单都落盘（planId / 摘要 / 完整 payload / 谁在什么时候批的）。
 *
 *   `$DSH_HOME/akso-workbench/approvals.json` —— 0600，原子写
 */
import { randomUUID } from 'node:crypto'
import { chmodSync, existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { CAPABILITIES } from './capabilities.ts'

export type ApprovalStatus = 'pending' | 'approved' | 'denied' | 'used' | 'expired'

/** 一条「这步到底会往平台发什么」的完整展示。 */
export interface ApprovalPayloadLine {
  stepId: string
  title: string
  capabilityId: string | null
  method: string | null
  path: string | null
  params: Record<string, unknown>
}

export interface ApprovalEntry {
  id: string
  planId: string
  env: string
  createdAt: number
  expiresAt: number
  status: ApprovalStatus
  summary: { create: number; update: number; skip: number; irreversible: number }
  steps: Array<{ id: string; kind: string; action: string; title: string; irreversible: boolean }>
  payload: ApprovalPayloadLine[]
  decidedAt?: number
  decision?: 'allow' | 'deny'
  note?: string
  usedAt?: number
}

/** 计划的最小形状 —— 结构化取用，避免 approvals ↔ engine 的循环依赖。 */
export interface PlanLike {
  planId: string
  env: string
  summary: { create: number; update: number; skip: number; irreversible: number }
  steps: Array<{
    id: string
    kind: string
    action: string
    title: string
    irreversible: boolean
    capabilityId?: string
    params?: Record<string, unknown>
  }>
}

const TTL_MS = 30 * 60 * 1000
const KEEP = 40

function approvalFile(dshHome: string): string {
  return join(dshHome, 'akso-workbench', 'approvals.json')
}

function readAll(dshHome: string): ApprovalEntry[] {
  const path = approvalFile(dshHome)
  if (!existsSync(path)) return []
  try {
    const parsed = JSON.parse(readFileSync(path, 'utf8')) as { items?: ApprovalEntry[] }
    return Array.isArray(parsed.items) ? parsed.items : []
  } catch {
    // 文件坏了就当没有批准单：宁可不放行，也不默认放行
    return []
  }
}

function writeAll(dshHome: string, items: ApprovalEntry[]): void {
  const path = approvalFile(dshHome)
  mkdirSync(join(path, '..'), { recursive: true, mode: 0o700 })
  const kept = items.slice(-KEEP)
  const tmp = `${path}.tmp`
  writeFileSync(tmp, `${JSON.stringify({ version: 1, items: kept }, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 })
  renameSync(tmp, path)
  try {
    chmodSync(path, 0o600)
  } catch {
    // Windows 上 chmod 基本是空操作
  }
}

/** 过期是在读的时候算的：盘上不写定时器，进程重启也不会留下"永远有效"的单。 */
function settle(entry: ApprovalEntry, now = Date.now()): ApprovalEntry {
  if (entry.status === 'pending' && now > entry.expiresAt) return { ...entry, status: 'expired' }
  return entry
}

export function approvalPayload(plan: PlanLike): ApprovalPayloadLine[] {
  return plan.steps.map((step) => {
    const capability = CAPABILITIES.find((cap) => cap.id === step.capabilityId) ?? null
    return {
      stepId: step.id,
      title: step.title,
      capabilityId: step.capabilityId ?? null,
      method: capability?.method ?? null,
      path: capability?.path ?? null,
      params: step.params ?? {},
    }
  })
}

/**
 * 开一张待批单。同一份计划已经有 pending / approved 的单时直接复用，
 * 避免「点两次工具就冒出两条记录」。
 */
export function requestApproval(dshHome: string, plan: PlanLike): ApprovalEntry {
  const now = Date.now()
  const items = readAll(dshHome).map((entry) => settle(entry, now))
  const reuse = items.find(
    (entry) => entry.planId === plan.planId && (entry.status === 'pending' || entry.status === 'approved'),
  )
  if (reuse !== undefined) {
    writeAll(dshHome, items)
    return reuse
  }
  const entry: ApprovalEntry = {
    id: `apr_${randomUUID().replaceAll('-', '').slice(0, 16)}`,
    planId: plan.planId,
    env: plan.env,
    createdAt: now,
    expiresAt: now + TTL_MS,
    status: 'pending',
    summary: { ...plan.summary },
    steps: plan.steps.map((step) => ({
      id: step.id,
      kind: step.kind,
      action: step.action,
      title: step.title,
      irreversible: step.irreversible,
    })),
    payload: approvalPayload(plan),
  }
  writeAll(dshHome, [...items, entry])
  return entry
}

/** 某份计划当前有效的那张单（取最新一张）。 */
export function approvalFor(dshHome: string, planId: string): ApprovalEntry | null {
  const now = Date.now()
  const items = readAll(dshHome).map((entry) => settle(entry, now))
  const mine = items.filter((entry) => entry.planId === planId)
  return mine.length === 0 ? null : (mine.at(-1) ?? null)
}

export function decideApproval(
  dshHome: string,
  approvalId: string,
  decision: 'allow' | 'deny',
  note?: string,
): ApprovalEntry | null {
  const now = Date.now()
  const items = readAll(dshHome).map((entry) => settle(entry, now))
  const index = items.findIndex((entry) => entry.id === approvalId)
  if (index < 0) return null
  const current = items[index]!
  if (current.status !== 'pending') return null
  const next: ApprovalEntry = {
    ...current,
    status: decision === 'allow' ? 'approved' : 'denied',
    decision,
    decidedAt: now,
    ...(note === undefined || note === '' ? {} : { note }),
  }
  items[index] = next
  writeAll(dshHome, items)
  return next
}

/** 批准单一次性：执行完就核销，同一张单不能驱动第二次写入。 */
export function markApprovalUsed(dshHome: string, approvalId: string): void {
  const items = readAll(dshHome)
  const index = items.findIndex((entry) => entry.id === approvalId)
  if (index < 0) return
  items[index] = { ...items[index]!, status: 'used', usedAt: Date.now() }
  writeAll(dshHome, items)
}

export function listApprovals(dshHome: string): { pending: ApprovalEntry[]; recent: ApprovalEntry[] } {
  const now = Date.now()
  const items = readAll(dshHome).map((entry) => settle(entry, now))
  const pending = items.filter((entry) => entry.status === 'pending')
  const recent = items.filter((entry) => entry.status !== 'pending').slice(-20).reverse()
  return { pending, recent }
}

export function approvalCounts(dshHome: string): { pending: number } {
  return { pending: listApprovals(dshHome).pending.length }
}
