/**
 * 小问题台账（host 半边）。
 *
 * 执行过程中有很多「不至于停、但必须让人知道」的情况：
 *   · 平台回了 502 / 连接被重置，重试一次就好了；
 *   · 写完回读差一个 `sort`（平台不落库），但业务数据是对的；
 *   · 审核文档指纹对不上，改成按当前计划重新生成了一份；
 *   · 某步只能证明「接口成功」，没有回读判据。
 *
 * 这些以前只进 journal（给用户下载用）或直接吞掉。现在统一记进 `IssueLog`：
 *   1. 写 `$DSH_HOME/akso-workbench/config-audit/issues-<YYYY-MM>.jsonl`（跟写入审计同目录、单独文件）；
 *   2. 通过 `onEvent` 同时投一条 journal 事件（`issue.recorded`），页面「运行日志」能看见；
 *   3. 随 `config_apply` 的返回值带回去，agent 与用户当场知道发生过什么。
 *
 * 硬约束：**写台账失败绝不影响执行**（与 journal 同一口径），所以所有写盘都吞错。
 */
import { appendFileSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'

export type IssueLevel = 'info' | 'warn' | 'error'

export interface IssueRecord {
  /** ISO 时间。 */
  at: string
  level: IssueLevel
  /** 稳定的机器可读代码，例如 `TRANSIENT_RETRY` / `REVIEW_FINGERPRINT_MISMATCH`。 */
  code: string
  message: string
  stepId?: string
  capabilityId?: string
  /** 补充细节（回读原文、差异项等），已截断。 */
  detail?: string
}

export interface IssueLogOptions {
  /** 台账目录：`$DSH_HOME/akso-workbench/config-audit`。 */
  dir: string
  /** journal 事件回调；抛错不影响记录。 */
  onEvent?: (kind: string, data: Record<string, unknown>) => void
  /** 内存里最多留多少条（默认 200，够一次任务用）。 */
  limit?: number
}

const MAX_TEXT = 600

function clip(value: string): string {
  const text = value.trim()
  return text.length <= MAX_TEXT ? text : `${text.slice(0, MAX_TEXT)}…（共 ${text.length} 字）`
}

export class IssueLog {
  private readonly items: IssueRecord[] = []
  private readonly options: IssueLogOptions

  constructor(options: IssueLogOptions) {
    this.options = options
  }

  record(input: {
    level?: IssueLevel
    code: string
    message: string
    stepId?: string
    capabilityId?: string
    detail?: string
  }): IssueRecord {
    const record: IssueRecord = {
      at: new Date().toISOString(),
      level: input.level ?? 'warn',
      code: input.code,
      message: clip(input.message),
      ...(input.stepId === undefined ? {} : { stepId: input.stepId }),
      ...(input.capabilityId === undefined ? {} : { capabilityId: input.capabilityId }),
      ...(input.detail === undefined ? {} : { detail: clip(input.detail) }),
    }
    this.items.push(record)
    const limit = this.options.limit ?? 200
    if (this.items.length > limit) this.items.splice(0, this.items.length - limit)
    this.persist(record)
    try {
      this.options.onEvent?.('issue.recorded', { ...record })
    } catch {
      // journal 坏了不能影响执行
    }
    return record
  }

  all(): IssueRecord[] {
    return [...this.items]
  }

  get size(): number {
    return this.items.length
  }

  private persist(record: IssueRecord): void {
    try {
      mkdirSync(this.options.dir, { recursive: true })
      const month = record.at.slice(0, 7)
      appendFileSync(join(this.options.dir, `issues-${month}.jsonl`), `${JSON.stringify(record)}\n`, 'utf8')
    } catch {
      // 台账写不进去也不能把请求打挂；内存与返回值里仍有这条记录
    }
  }
}

export function createIssueLog(options: IssueLogOptions): IssueLog {
  return new IssueLog(options)
}

/** 把台账压成一句人话，放在工具返回的 `message` 里。 */
export function summarizeIssues(issues: readonly IssueRecord[]): string {
  if (issues.length === 0) return ''
  const warn = issues.filter((issue) => issue.level === 'warn').length
  const error = issues.filter((issue) => issue.level === 'error').length
  const head = [error > 0 ? `${error} 条错误` : '', warn > 0 ? `${warn} 条告警` : ''].filter((item) => item !== '').join(' · ')
  const first = issues[issues.length - 1]
  return `执行中记录 ${issues.length} 条小问题（${head === '' ? '提示' : head}）：${first?.message ?? ''}`
}
