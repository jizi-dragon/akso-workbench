/**
 * 执行后确认（回读验证）的结果结构与落盘。
 *
 * 写入成功 ≠ 配置生效。引擎的 `verifyStep` 在**写入那一刻**用查询接口回读一次；
 * 这里做的是**执行完之后再独立查一遍**：把计划里每一步重新走一次回读判据，
 * 另外补上「工作流节点链是否完整」这种单步判据覆盖不到的检查。
 *
 * 产物：`<workspace>/akso-workbench-data/verifications/验证-<planId>-<时间戳>.md`，
 * 与审核文档、计划 JSON 三件套对得上号，事后追责/复盘不用猜。
 */
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { workspaceDataPath } from '../workspace-data.ts'

export interface VerificationCheck {
  stepId: string
  title: string
  capabilityId: string
  kind: string
  action: string
  /** 只读步骤没有“写入回读”这回事，单列出来。 */
  readonly: boolean
  ok: boolean
  message: string
}

export interface VerificationReport {
  planId: string
  checkedAt: string
  ok: boolean
  total: number
  passed: number
  failed: number
  checks: VerificationCheck[]
  /** 额外确认项（节点链、用户动作、菜单层级等），逐条人话。 */
  notes: string[]
}

export interface VerificationMeta {
  planTitle?: string
  env?: string
  target?: string
}

export function buildVerificationReport(planId: string, checks: VerificationCheck[], notes: string[] = []): VerificationReport {
  const passed = checks.filter((check) => check.ok).length
  const failed = checks.length - passed
  return {
    planId,
    checkedAt: new Date().toISOString(),
    ok: failed === 0,
    total: checks.length,
    passed,
    failed,
    checks,
    notes,
  }
}

/** 一句话结论，进工具返回值的 `message`。 */
export function summarizeVerification(report: VerificationReport): string {
  if (report.total === 0) return '没有需要回读确认的步骤。'
  if (report.ok) return `回读确认通过：${report.passed}/${report.total} 步与平台实际状态一致。`
  const bad = report.checks.filter((check) => !check.ok)
  const first = bad[0]
  return `回读确认发现 ${report.failed}/${report.total} 步与平台不一致：${first === undefined ? '' : `${first.title} —— ${first.message}`}`
}

function stamp(date: Date): string {
  const pad = (value: number): string => String(value).padStart(2, '0')
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`
}

function cell(value: string): string {
  return value.replaceAll('|', '\\|').replaceAll('\n', ' ')
}

export function renderVerificationMarkdown(report: VerificationReport, meta: VerificationMeta = {}): string {
  const lines: string[] = []
  lines.push(`# 执行确认 · ${meta.planTitle ?? report.planId}`)
  lines.push('')
  lines.push(`> 计划：\`${report.planId}\``)
  if (meta.env !== undefined) lines.push(`> 环境：${meta.env}`)
  if (meta.target !== undefined) lines.push(`> 目标：${meta.target}`)
  lines.push(`> 确认时间：${report.checkedAt}`)
  lines.push(`> 结论：${report.ok ? `**通过**（${report.passed}/${report.total}）` : `**有差异**（${report.failed}/${report.total} 不一致）`}`)
  lines.push('')
  lines.push('| # | 步骤 | 能力 | 结论 | 回读说明 |')
  lines.push('| --- | --- | --- | --- | --- |')
  report.checks.forEach((check, index) => {
    const verdict = check.ok ? '一致' : '不一致'
    lines.push(`| ${index + 1} | ${cell(check.title)}${check.readonly ? '（只读）' : ''} | \`${check.capabilityId}\` | ${verdict} | ${cell(check.message)} |`)
  })
  if (report.notes.length > 0) {
    lines.push('')
    lines.push('## 额外确认')
    lines.push('')
    for (const note of report.notes) lines.push(`- ${note}`)
  }
  lines.push('')
  return lines.join('\n')
}

/** 写盘并返回绝对路径；写不进去只返回空串，不影响已经完成的执行。 */
export function writeVerificationReport(workspaceRoot: string, report: VerificationReport, meta: VerificationMeta = {}): string {
  try {
    const dir = workspaceDataPath(workspaceRoot, 'verifications')
    mkdirSync(dir, { recursive: true })
    const name = `验证-${meta.planTitle ?? report.planId}-${stamp(new Date(report.checkedAt))}.md`
    const path = join(dir, name.replaceAll(/[\\/:*?"<>|]/g, '-'))
    writeFileSync(path, renderVerificationMarkdown(report, meta), 'utf8')
    return path
  } catch {
    return ''
  }
}
