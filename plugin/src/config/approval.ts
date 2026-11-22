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
/**
 * 审核文档：把「为什么要改 / 怎么改 / 改完预期」固定成一份可阅读、可勾选的 Markdown。
 * 弹窗只展示摘要，完整文档由 DSH 右侧文件预览打开。
 */
export interface ApprovalReview {
  /** 绝对路径；客户端按当前 Session 转成 dsh-resource 地址。 */
  path: string
  /** 可直接交给 DSH 文件预览的地址；有它时客户端不必再拼路径。 */
  url?: string
  /** 文档类型：大规模创建/调整，或任务完成后的小范围更新。 */
  kind: 'create' | 'update'
  /** host 侧如果知道当前 Session，可直接带下来；否则客户端从右侧栏服务读。 */
  sessionId?: string
  title: string
  problem: string
  approach: string
  expected: string[]
}

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
  /** 审核文档；生成失败时为 undefined，弹窗降级为只读步骤列表。 */
  review?: ApprovalReview
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

export type ApprovalEventKind = 'wait' | 'decide' | 'timeout' | 'cancelled' | 'busy'

export interface ApprovalGateOptions {
  /**
   * 审批门的事件回调（落运行日志用）：等待挂起、人点了什么、超时、取消、并发 busy。
   * 回调抛错不影响审批本身 —— 日志坏了不能把人卡在弹窗前。
   */
  onEvent?: (kind: ApprovalEventKind, data: Record<string, unknown>) => void
}

export function createApprovalGate(options: ApprovalGateOptions = {}): ApprovalGate {
  let pending: PendingApproval | null = null
  let settle: ((decision: ApprovalDecision) => void) | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  let abortSignal: { removeEventListener?: (type: string, listener: () => void) => void } | null = null
  let abortListener: (() => void) | null = null

  function emit(kind: ApprovalEventKind, data: Record<string, unknown>): void {
    try {
      options.onEvent?.(kind, data)
    } catch {
      // 日志失败不影响审批
    }
  }

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
      if (pending !== null) {
        emit('busy', { id: pending.id, planId: pending.planId })
        return Promise.resolve({ id: pending.id, verdict: 'busy' })
      }
      const id = `apr_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`
      const createdAt = Date.now()
      const timeoutMs = Math.max(1000, options.timeoutMs ?? DEFAULT_TIMEOUT_MS)
      pending = { id, planId: report.planId, createdAt, expiresAt: createdAt + timeoutMs, report }
      const promise = new Promise<ApprovalDecision>((resolve) => {
        settle = resolve
      })
      emit('wait', {
        id,
        planId: report.planId,
        timeoutMs,
        steps: report.steps.length,
        writes: report.steps.filter((step) => step.action !== 'skip').length,
        summary: report.summary,
      })
      timer = setTimeout(() => {
        emit('timeout', { id, planId: report.planId, waitedMs: Date.now() - createdAt })
        finish({ id, verdict: 'timeout' })
      }, timeoutMs)
      ;(timer as unknown as { unref?: () => void }).unref?.()

      const signal = options.signal as
        | { aborted?: boolean; addEventListener?: (type: string, listener: () => void, options?: { once?: boolean }) => void; removeEventListener?: (type: string, listener: () => void) => void }
        | undefined
      if (signal !== undefined && typeof signal.addEventListener === 'function') {
        abortSignal = signal
        abortListener = () => {
          emit('cancelled', { id, planId: report.planId, waitedMs: Date.now() - createdAt })
          finish({ id, verdict: 'cancelled' })
        }
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
      emit('decide', {
        id,
        planId: pending.planId,
        verdict,
        waitedMs: Date.now() - pending.createdAt,
        note: typeof note === 'string' && note !== '' ? note : undefined,
      })
      finish(decision)
      return decision
    },
  }
}
