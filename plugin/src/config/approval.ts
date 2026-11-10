/**
 * 写入审批门（host 半边）。
 *
 * 工具在真正写之前把「这一批要写什么」挂在这里等：浏览器里的对话弹窗读 `pending()`，
 * 人点「同意 / 拒绝 / 补充」后调 `decide()`，工具拿到结论再决定写不写。
 *
 * 三条硬规矩：
 *   1. **只有 approve 才写**；deny / supplement / timeout / cancelled 一律不写。
 *   2. 同一时间只挂一条（一批 = 一次审批），并发的第二条直接 `busy`，不排队。
 *   3. 超时（默认 8 分钟）自动判 `timeout` —— 不能因为人不在电脑前就让工具永远挂着。
 */

export interface ApprovalReportStep {
  index: number
  action: 'create' | 'update' | 'skip'
  kind: string
  title: string
  method: string
  path: string
  risk: string
  irreversible: boolean
  params: Record<string, unknown>
  warnings: string[]
}

/**
 * 审批窗顶部的「业务摘要」：只说新建 / 更新了哪几类东西、分别叫什么。
 * 人审的是「要改什么」，所以这里**不出现**方法、路径、参数。
 */
export interface ApprovalBusinessGroup {
  action: 'create' | 'update'
  /** 资源种类（object / field / lifecycle / menu / call …），文案由界面按语言翻。 */
  kind: string
  /** 同类资源的人话名称，已去重；顺序与计划一致。 */
  names: string[]
}

/** 弹窗要展示的全部内容；`hash` 是计划指纹，写审批记录时原样存下。 */
export interface ApprovalReport {
  planId: string
  title: string
  env: string
  baseUrl: string
  host: string
  account: string
  accountId: string
  summary: { create: number; update: number; skip: number; irreversible: number }
  /** 业务摘要（弹窗顶部）；技术细节留在 steps 里，折起来看。 */
  business: ApprovalBusinessGroup[]
  steps: ApprovalReportStep[]
  createdAt: number
  expiresAt: number
  hash: string
}

export interface PendingApproval {
  id: string
  planId: string
  createdAt: number
  /** 这次等待的截止时间（不是计划的截止时间）。 */
  expiresAt: number
  report: ApprovalReport
}

export type ApprovalVerdict = 'approve' | 'deny' | 'supplement' | 'timeout' | 'cancelled' | 'busy'

export interface ApprovalDecision {
  id: string
  verdict: ApprovalVerdict
  /** 「补充」时用户写的意见；其余情况一般没有。 */
  note?: string
}

export interface ApprovalGate {
  request(report: ApprovalReport, options?: { timeoutMs?: number; signal?: unknown }): Promise<ApprovalDecision>
  pending(): PendingApproval | null
  decide(id: string, verdict: 'approve' | 'deny' | 'supplement', note?: string): ApprovalDecision | null
}

const DEFAULT_TIMEOUT_MS = 8 * 60 * 1000

export function createApprovalGate(): ApprovalGate {
  let pending: PendingApproval | null = null
  let settle: ((decision: ApprovalDecision) => void) | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  let abortSignal: { removeEventListener?: (type: string, listener: () => void) => void } | null = null
  let abortListener: (() => void) | null = null

  function cleanup(): void {
    if (timer !== null) clearTimeout(timer)
    timer = null
    if (abortSignal !== null && abortListener !== null && typeof abortSignal.removeEventListener === 'function') {
      abortSignal.removeEventListener('abort', abortListener)
    }
    abortSignal = null
    abortListener = null
    pending = null
    settle = null
  }

  function finish(decision: ApprovalDecision): void {
    const resolve = settle
    cleanup()
    resolve?.(decision)
  }

  return {
    request(report, options = {}) {
      if (pending !== null) return Promise.resolve({ id: pending.id, verdict: 'busy' })
      const id = `apr_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`
      const createdAt = Date.now()
      const timeoutMs = Math.max(1000, options.timeoutMs ?? DEFAULT_TIMEOUT_MS)
      pending = { id, planId: report.planId, createdAt, expiresAt: createdAt + timeoutMs, report }
      const promise = new Promise<ApprovalDecision>((resolve) => {
        settle = resolve
      })
      timer = setTimeout(() => finish({ id, verdict: 'timeout' }), timeoutMs)
      ;(timer as unknown as { unref?: () => void }).unref?.()

      const signal = options.signal as
        | { aborted?: boolean; addEventListener?: (type: string, listener: () => void, options?: { once?: boolean }) => void; removeEventListener?: (type: string, listener: () => void) => void }
        | undefined
      if (signal !== undefined && typeof signal.addEventListener === 'function') {
        abortSignal = signal
        abortListener = () => finish({ id, verdict: 'cancelled' })
        if (signal.aborted === true) {
          abortListener()
        } else {
          signal.addEventListener('abort', abortListener, { once: true })
        }
      }
      return promise
    },
    pending() {
      return pending
    },
    decide(id, verdict, note) {
      if (pending === null || pending.id !== id) return null
      const decision: ApprovalDecision = {
        id,
        verdict,
        ...(typeof note === 'string' && note !== '' ? { note } : {}),
      }
      finish(decision)
      return decision
    },
  }
}
