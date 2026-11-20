/**
 * 审批文档（host 半边）。
 *
 * 一份任务一份审核文档：放在工作区 `.akso-workbench/reviews/` 下，文件名是
 * `任务名-YYYYMMDD-HHmmss.md`。文档先于审批生成，作为任务的「第一目标」；
 * 任务完成后再回来把状态改成「已结束（审核文件作废）」，留作历史记录。
 *
 * 二次审核：调用方传 `reviewPath` 指向原文档，并给出 reason / executed / unexecuted，
 * 这里在原文档追加一节，不另起一份，避免同一任务散成多份。
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import type { ApprovalReport, ApprovalReview } from './approval.ts'

export interface ApprovalReviewInput {
  /** 任务名；不填则用审批标题。 */
  taskName?: string
  /** 二次审核：指向同一任务的既有审核文档。 */
  reviewPath?: string
  /** 本次任务需要解决什么问题。 */
  problem?: string
  /** 本次任务计划怎么执行。 */
  approach?: string
  /** 本次任务完成后预期结果，逐条作为 checklist。 */
  expected?: string[]
  /** 二次审核原因。 */
  reason?: string
  /** 二次审核时已执行的部分。 */
  executed?: string[]
  /** 二次审核时尚未执行的部分。 */
  unexecuted?: string[]
}

function clean(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function lines(value: unknown): string[] {
  return (Array.isArray(value) ? value : [])
    .map((item) => clean(item))
    .filter((item) => item !== '')
}

function businessLine(report: ApprovalReport): string {
  if (report.business.length === 0) return '未列出业务对象'
  return report.business
    .map((group) => {
      const verb = group.action === 'create' ? '新建' : '更新'
      return `${verb}${group.kind}：${group.names.join('、')}`
    })
    .join('；')
}

function defaultProblem(report: ApprovalReport): string {
  return `本次任务需要在 ${report.env}（${report.host}）上执行 ${report.steps.length} 步配置变更，涉及${businessLine(report)}。`
    + '审批前需要确认：变更范围是否完整、目标环境是否选对、不可逆步骤是否都可以接受。'
}

function defaultApproach(report: ApprovalReport): string {
  return report.steps
    .map((step) => `${step.index}. ${step.title}（${step.method} ${step.path}）${step.irreversible ? '【不可逆】' : ''}`)
    .join('\n')
}

function defaultExpected(report: ApprovalReport): string[] {
  const expected = [
    `完成 ${report.steps.length} 步配置变更，且逐条回读与计划一致。`,
    `本次变更涉及：${businessLine(report)}。`,
  ]
  if (report.summary.irreversible > 0) {
    expected.push(`已明确接受 ${report.summary.irreversible} 个不可逆步骤的后果。`)
  }
  expected.push('任一步失败时，执行在该步停止并返回 checkpoint，不继续后续写入。')
  return expected
}

function stamp(date = new Date()): string {
  const pad = (value: number): string => String(value).padStart(2, '0')
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`
}

function safeName(value: string): string {
  const name = value.replace(/[\\/:*?"<>|\s]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60)
  return name === '' ? '未命名任务' : name
}

function checklist(items: string[]): string[] {
  return items.map((item) => `- [ ] ${item}`)
}

function stepDetails(report: ApprovalReport): string[] {
  const out: string[] = ['### 执行明细（与工具实际输入一致）', '']
  for (const step of report.steps) {
    out.push(`#### ${step.index}. ${step.title}`)
    out.push('')
    out.push(`- 动作：${step.action}`)
    out.push(`- 接口：\`${step.method} ${step.path}\``)
    out.push(`- 不可逆：${step.irreversible ? '是' : '否'}`)
    out.push('- 参数：')
    out.push('')
    out.push('```json')
    out.push(JSON.stringify(step.params, null, 2))
    out.push('```')
    out.push('')
  }
  return out
}

function newDocument(report: ApprovalReport, review: Omit<ApprovalReview, 'path'>, at: string): string {
  return [
    `# ${review.title}`,
    '',
    '> 任务状态：进行中',
    `> 任务名：${review.title.replace(/^审核文档：/, '')}`,
    `> 计划：\`${report.planId}\``,
    `> 计划指纹：\`${report.hash}\``,
    `> 步骤数：${report.steps.length}`,
    `> 环境：${report.env}`,
    `> 目标：${report.host}`,
    `> 账号：${report.account}`,
    `> 创建时间：${at}`,
    '',
    '## 本次任务需要解决什么问题',
    '',
    review.problem,
    '',
    '## 本次任务计划怎么执行',
    '',
    review.approach,
    '',
    ...stepDetails(report),
    '## 本次任务完成后预期结果是什么',
    '',
    ...checklist(review.expected),
    '',
    '---',
    '',
    '> 这份文档由 akso-workbench 在审批前生成；只有审批窗点「同意」才会真正写入平台。',
    '> 任务完成前，审核文件是任务目标；任务完成后会标记为「已结束（审核文件作废）」。',
    '',
  ].join('\n')
}

/**
 * 二次审核：在原文档末尾追加一节，并记录已执行 / 未执行 / 原因。
 * 不覆盖原文档，避免历史审核内容丢失。
 */
function appendRevision(
  existing: string,
  report: ApprovalReport,
  review: Omit<ApprovalReview, 'path'>,
  input: ApprovalReviewInput,
  at: string,
): string {
  const executed = lines(input.executed)
  const unexecuted = lines(input.unexecuted)
  const section = [
    '',
    `## 二次审核 · ${at}`,
    '',
    `> 任务状态：进行中（已更新审核文件）`,
    `> 原因：${clean(input.reason) || '执行过程中发现需要补充创建/更新操作'}`,
    `> 本次计划：\`${report.planId}\``,
    `> 本次计划指纹：\`${report.hash}\``,
    '',
    '### 已执行部分',
    '',
    ...(executed.length > 0 ? executed.map((item) => `- [x] ${item}`) : ['- （未标注）']),
    '',
    '### 未执行部分',
    '',
    ...(unexecuted.length > 0 ? unexecuted.map((item) => `- [ ] ${item}`) : ['- （未标注）']),
    '',
    '### 本次新增 / 更新的预期结果',
    '',
    ...checklist(review.expected),
    '',
    ...stepDetails(report),
    '',
  ].join('\n')
  const active = existing.replace('> 任务状态：已结束（审核文件作废，仅作历史记录）', '> 任务状态：进行中')
  return active + section
}

export function attachApprovalReview(
  report: ApprovalReport,
  input: ApprovalReviewInput | undefined,
  dshHome: string,
  sessionId?: string,
): void {
  const taskName = clean(input?.taskName) || report.title
  const problem = clean(input?.problem) || defaultProblem(report)
  const approach = clean(input?.approach) || defaultApproach(report)
  const expected = lines(input?.expected)
  const checklistItems = expected.length > 0 ? expected : defaultExpected(report)
  const at = new Date().toLocaleString('zh-CN', { hour12: false })
  const review: Omit<ApprovalReview, 'path'> = {
    title: `审核文档：${taskName}`,
    ...(typeof sessionId === 'string' && sessionId !== '' ? { sessionId } : {}),
    problem,
    approach,
    expected: checklistItems,
  }

  const existingPath = clean(input?.reviewPath)
  if (existingPath !== '' && existsSync(existingPath)) {
    try {
      const existing = readFileSync(existingPath, 'utf8')
      writeFileSync(existingPath, appendRevision(existing, report, review, input ?? {}, at), 'utf8')
      report.review = { path: existingPath, ...review }
      return
    } catch (error) {
      console.warn('[akso-workbench] 二次审核文档更新失败，改为新建一份', error)
    }
  }

  const fileName = `${safeName(taskName)}-${stamp()}.md`
  const candidates = [
    process.env.AKSO_WORKBENCH_REVIEW_DIR?.trim(),
    process.env.AKSO_WORKBENCH_APPROVAL_DIR?.trim(),
    join(process.cwd(), '.akso-workbench', 'reviews'),
    join(dshHome, 'akso-workbench', 'reviews'),
  ].filter((dir): dir is string => typeof dir === 'string' && dir !== '')
  for (const dir of candidates) {
    const path = join(dir, fileName)
    try {
      mkdirSync(dir, { recursive: true })
      writeFileSync(path, newDocument(report, review, at), 'utf8')
      report.review = { path, ...review }
      return
    } catch {
      // 当前目录可能只读（例如 DSH 安装在 asar 内），继续试 dshHome。
    }
  }
  console.warn('[akso-workbench] 审核文档写入失败，审批窗降级为步骤列表')
}

/**
 * 审批文档是否确实对应这一版计划：防止拿旧文档、或文档写入不完整就进入审批。
 * 新文档带「计划指纹」，二次审核追加段落带「本次计划指纹」。
 */
export function reviewMatchesPlan(path: string | undefined, planHash: string): boolean {
  const target = clean(path)
  if (target === '' || !existsSync(target) || planHash === '') return false
  try {
    const text = readFileSync(target, 'utf8')
    return text.includes(`计划指纹：\`${planHash}\``) || text.includes(`本次计划指纹：\`${planHash}\``)
  } catch {
    return false
  }
}

/** 任务执行结束：把审核文件标记为作废，只保留历史记录。 */
export function finalizeApprovalReview(
  path: string | undefined,
  outcome: string,
  results: Array<{ title: string; status: string; message?: string; durationMs?: number }> = [],
): void {
  const target = clean(path)
  if (target === '' || !existsSync(target)) return
  try {
    const existing = readFileSync(target, 'utf8')
    const at = new Date().toLocaleString('zh-CN', { hour12: false })
    const closed = existing
      .replace(/^> 任务状态：.*$/m, '> 任务状态：已结束（审核文件作废，仅作历史记录）')
      .replace(/^> 结束时间：.*$/gm, '')
      .replace(/> 创建时间：([^\n]*)/, `> 创建时间：$1\n> 结束时间：${at}`)
    const resultLines = results.length > 0
      ? [
          '',
          '### 执行结果',
          '',
          '| # | 步骤 | 状态 | 耗时 | 说明 |',
          '| --- | --- | --- | --- | --- |',
          ...results.map((result, index) => `| ${index + 1} | ${result.title.replaceAll('|', '\\|')} | ${result.status} | ${result.durationMs ?? ''} | ${(result.message ?? '').replaceAll('|', '\\|')} |`),
        ]
      : []
    const footer = [
      '',
      '## 任务结束',
      '',
      `- 结束时间：${at}`,
      `- 结果：${outcome}`,
      ...resultLines,
      '- 本审核文件已作废，仅作为历史操作记录参考。',
      '',
    ].join('\n')
    writeFileSync(target, `${closed}\n${footer}`, 'utf8')
  } catch (error) {
    console.warn('[akso-workbench] 审核文档收口失败', error)
  }
}
