/**
 * 审批文档（host 半边）。
 *
 * 两类文档，都放在工作区 `akso-workbench-data/reviews/` 下：
 * - `create`：大规模创建/调整，文件名 `任务名-YYYYMMDD-HHmmss.md`，带完整执行明细；
 * - `update`：任务完成后的小范围优化/更新，文件名 `任务名-更新-YYYYMMDD-HHmmss.md`，只保留摘要与操作清单。
 *
 * 文档先于审批生成，是任务的「第一目标」。任务成功后由 `finalizeApprovalReview`
 * 标成「已结束（审核文件作废）」；**已结束的文档不可再改**。后续调整必须新建一份
 * `update` 文档，并在其中引用上一份已执行文档。
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import type { ApprovalReport, ApprovalReview } from './approval.ts'
import { collectBindingRefs, parseBindingRef } from './bindings.ts'

export interface ApprovalReviewInput {
  /** `create` 大规模创建/调整（默认）；`update` 已执行任务的小范围更新。 */
  kind?: 'create' | 'update'
  /** 任务名；不填则用审批标题。 */
  taskName?: string
  /** 小范围更新时，指向上一份已执行审核文档，仅作引用，不修改它。 */
  previousReviewPath?: string
  /** 本次任务需要解决什么问题。 */
  problem?: string
  /** 本次任务计划怎么执行。 */
  approach?: string
  /** 本次任务完成后预期结果，逐条作为 checklist。 */
  expected?: string[]
  /** 小范围更新时的调整原因。 */
  reason?: string
  /** 当前工作区根；调用方知道时优先用它落审核文档。 */
  workspaceDir?: string
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

/** 同一任务同一秒只能有一份文档；重名时追加序号，绝不覆盖旧文件。 */
function uniquePath(dir: string, base: string): string {
  let candidate = join(dir, `${base}.md`)
  for (let index = 2; existsSync(candidate); index += 1) {
    candidate = join(dir, `${base}-${index}.md`)
  }
  return candidate
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

function bindingDetails(report: ApprovalReport): string[] {
  const rows: string[] = []
  for (const step of report.steps) {
    let refs: string[] = []
    try {
      refs = collectBindingRefs(step.params)
    } catch {
      continue
    }
    for (const ref of refs) {
      try {
        const parsed = parseBindingRef(ref)
        const source = parsed.kind === 'stage'
          ? `第 ${parsed.stage} 阶段步骤 ${parsed.stepId}`
          : `本计划步骤 ${parsed.stepId}`
        const path = parsed.path === '' ? '完整输出' : parsed.path.replace(/^\./, '').replaceAll('[', ' [').trim()
        rows.push(`- 第 ${step.index} 步「${step.title}」引用 ${source} 的${path}`)
      } catch {
        rows.push(`- 第 ${step.index} 步「${step.title}」含未识别引用 \`${ref}\``)
      }
    }
  }
  if (rows.length === 0) return []
  return [
    '### 输出绑定（执行时解析）',
    '',
    '审批锁定的是引用模板；执行时按前序步骤的真实返回填充，审批前不会写入平台。',
    '',
    ...rows,
    '',
  ]
}

function briefStepDetails(report: ApprovalReport): string[] {
  const out: string[] = ['### 本次操作清单（小范围更新）', '']
  for (const step of report.steps) {
    out.push(`- ${step.index}. ${step.title}（\`${step.method} ${step.path}\`）${step.irreversible ? '【不可逆】' : ''}`)
  }
  out.push('')
  return out
}

function newDocument(
  report: ApprovalReport,
  review: Omit<ApprovalReview, 'path'>,
  input: ApprovalReviewInput,
  at: string,
): string {
  const update = review.kind === 'update'
  const previous = clean(input.previousReviewPath)
  const reason = clean(input.reason)
  const head = [
    `# ${review.title}`,
    '',
    `> 文档类型：${update ? '小范围调整 / 更新' : '创建 / 调整'}`,
    '> 任务状态：进行中',
    `> 任务名：${review.title.replace(/^(更新)?审核文档：/, '')}`,
    `> 计划：\`${report.planId}\``,
    `> 计划指纹：\`${report.hash}\``,
    `> 步骤数：${report.steps.length}`,
    `> 环境：${report.env}`,
    `> 目标：${report.host}`,
    `> 账号：${report.account}`,
    `> 创建时间：${at}`,
  ]
  if (update && previous !== '') head.push(`> 关联已执行审核文件：\`${previous}\``)
  if (update && reason !== '') head.push(`> 调整原因：${reason}`)
  head.push('')

  const problemTitle = update ? '## 本次调整需要解决什么问题' : '## 本次任务需要解决什么问题'
  const approachTitle = update ? '## 本次调整计划怎么执行' : '## 本次任务计划怎么执行'
  const expectedTitle = update ? '## 本次调整完成后预期结果是什么' : '## 本次任务完成后预期结果是什么'

  return [
    ...head,
    problemTitle,
    '',
    review.problem,
    '',
    approachTitle,
    '',
    review.approach,
    '',
    ...(update ? briefStepDetails(report) : stepDetails(report)),
    ...bindingDetails(report),
    expectedTitle,
    '',
    ...checklist(review.expected),
    '',
    '---',
    '',
    '> 这份文档由 akso-workbench 在审批前生成；只有审批窗点「同意」才会真正写入平台。',
    ...(update
      ? ['> 这是一份小范围更新审核文件；上一份已执行文档不会被修改。']
      : ['> 任务完成后会标记为「已结束（审核文件作废）」；后续调整必须新建更新审核文件。']),
    '',
  ].join('\n')
}

function isExecuted(text: string): boolean {
  return /^> 任务状态：已结束/m.test(text)
}

function reviewUrl(path: string, sessionId: string): string {
  let normalized = path.replaceAll('\\', '/')
  const marker = '/akso-workbench-data/reviews/'
  const at = normalized.toLowerCase().indexOf(marker)
  if (at >= 0) normalized = normalized.slice(at + 1)
  return `dsh-resource://file/session/${encodeURIComponent(sessionId)}/${encodeURI(normalized)}`
}

export function attachApprovalReview(
  report: ApprovalReport,
  input: ApprovalReviewInput | undefined,
  _dshHome: string,
  sessionId?: string,
  workspaceRoot?: string,
): void {
  const kind: 'create' | 'update' = input?.kind === 'update' ? 'update' : 'create'
  const taskName = clean(input?.taskName) || report.title
  const problem = clean(input?.problem) || defaultProblem(report)
  const approach = clean(input?.approach) || defaultApproach(report)
  const expected = lines(input?.expected)
  const checklistItems = expected.length > 0 ? expected : defaultExpected(report)
  const at = new Date().toLocaleString('zh-CN', { hour12: false })
  const review: Omit<ApprovalReview, 'path'> = {
    kind,
    title: `${kind === 'update' ? '更新' : ''}审核文档：${taskName}`,
    ...(typeof sessionId === 'string' && sessionId !== '' ? { sessionId } : {}),
    problem,
    approach,
    expected: checklistItems,
  }

  // 只允许落在当前工作区。环境变量是显式覆盖（测试/特殊工作区），
  // 但绝不回落到 process.cwd() 或 dshHome——那会把审核文件写进仓库或 profile。
  const candidates = [
    workspaceRoot === undefined ? undefined : join(workspaceRoot, 'akso-workbench-data', 'reviews'),
    process.env.AKSO_WORKBENCH_REVIEW_DIR?.trim(),
    process.env.AKSO_WORKBENCH_APPROVAL_DIR?.trim(),
  ].filter((dir): dir is string => typeof dir === 'string' && dir !== '')
  for (const dir of candidates) {
    const path = uniquePath(dir, `${safeName(taskName)}${kind === 'update' ? '-更新' : ''}-${stamp()}`)
    try {
      mkdirSync(dir, { recursive: true })
      writeFileSync(path, newDocument(report, review, input ?? {}, at), 'utf8')
      report.review = {
        path,
        ...review,
        ...(typeof sessionId === 'string' && sessionId !== '' ? { url: reviewUrl(path, sessionId) } : {}),
      }
      return
    } catch {
      // 当前目录可能只读（例如 DSH 安装在 asar 内），继续试 dshHome。
    }
  }
  console.warn('[akso-workbench] 审核文档写入失败，审批窗降级为步骤列表')
}

/**
 * 二次进入审批时读取已准备文档的摘要。指纹校验先由 `reviewMatchesPlan` 完成；
 * 读取失败返回 null，调用方应拒绝进入审批，而不是拿空文档糊弄。
 */
export function loadApprovalReview(path: string, report: ApprovalReport, sessionId?: string): ApprovalReview | null {
  const target = clean(path)
  if (target === '' || !existsSync(target) || !reviewMatchesPlan(target, report.hash)) return null
  try {
    const text = readFileSync(target, 'utf8')
    const title = /^# (.+)$/m.exec(text)?.[1]?.trim() ?? report.title
    const kind: 'create' | 'update' = /> 文档类型：([^\n]+)/.exec(text)?.[1]?.includes('更新') === true ? 'update' : 'create'
    const section = (heading: string): string => {
      const escaped = heading.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      return new RegExp(`## ${escaped}\\n\\n([\\s\\S]*?)\\n\\n(?:##|###) `).exec(text)?.[1]?.trim() ?? ''
    }
    const problem = section(kind === 'update' ? '本次调整需要解决什么问题' : '本次任务需要解决什么问题')
    const approach = section(kind === 'update' ? '本次调整计划怎么执行' : '本次任务计划怎么执行')
    const expected = [...text.matchAll(/^- \[ \] (.+)$/gm)].map((match) => match[1]?.trim() ?? '').filter((item) => item !== '')
    return {
      path: target,
      ...(typeof sessionId === 'string' && sessionId !== '' ? { sessionId, url: reviewUrl(target, sessionId) } : {}),
      kind,
      title,
      problem,
      approach,
      expected,
    }
  } catch {
    return null
  }
}

/**
 * 审批文档是否确实对应这一版计划：防止拿旧文档、或文档写入不完整就进入审批。
 * 已执行的文档不能再复用（哪怕指纹碰巧相同）。
 */
export function reviewMatchesPlan(path: string | undefined, planHash: string): boolean {
  const target = clean(path)
  if (target === '' || !existsSync(target) || planHash === '') return false
  try {
    const text = readFileSync(target, 'utf8')
    if (isExecuted(text)) return false
    return text.includes(`计划指纹：\`${planHash}\``) || text.includes(`本次计划指纹：\`${planHash}\``)
  } catch {
    return false
  }
}

/** 任务执行结束：把审核文件标记为作废，只保留历史记录；已结束的文档不再改。 */
export function finalizeApprovalReview(
  path: string | undefined,
  outcome: string,
  results: Array<{ title: string; status: string; message?: string; durationMs?: number }> = [],
): boolean {
  const target = clean(path)
  if (target === '' || !existsSync(target)) return false
  try {
    const existing = readFileSync(target, 'utf8')
    if (isExecuted(existing)) return false
    const at = new Date().toLocaleString('zh-CN', { hour12: false })
    const closed = existing
      .replace(/^> 任务状态：.*$/m, '> 任务状态：已结束（审核文件作废，仅作历史记录）')
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
    return true
  } catch (error) {
    console.warn('[akso-workbench] 审核文档收口失败', error)
    return false
  }
}
