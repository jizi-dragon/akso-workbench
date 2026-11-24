/**
 * 给 agent 的配置工具：**只能出计划 / 执行已有计划**，拿不到任何裸写端点。
 *
 * 写入前有两道门，缺一不可：
 *
 *   1. **账号门**：写入会话必须来自底部账号条 —— `openSession` 由 host 解析，
 *      账号条为空 / 账号不在配置池 / 换不到票，一律拿不到会话，工具直接拒绝。
 *   2. **审批门**：把这一批要写的东西整理成审批表，挂到 host 的审批门上等，
 *      对话框里弹出审批窗，人点「同意」才继续；「拒绝 / 补充 / 超时」都不写。
 *
 * 引擎 `applyPlan` 还会再验一次（计划指纹 / 目标环境 / 账号 / 审批记录），
 * 所以就算有人绕过工具直接调 HTTP，也过不了引擎这一关。
 */
import { CAPABILITIES } from './capabilities.ts'
import { applyPlan, loadPlan, planBlueprint, planHash, recordApprovalDecision, saveApproval, type Plan, type Step } from './engine.ts'
import { applyTask, createTask, loadTask, mergeTaskPreview, parseTaskInput, previewTaskPlans, saveTask, taskHash, type Task } from './task.ts'
import type { ApprovalBusinessGroup, ApprovalGate, ApprovalReport, ApprovalReportStep } from './approval.ts'
import { attachApprovalReview, finalizeApprovalReview, loadApprovalReview, reviewMatchesPlan, type ApprovalReviewInput } from './approval-review.ts'
import type { EgmpSession } from './session.ts'

/** host 解析出来的写入会话；`session: null` 时 `reason` 一定是人话。 */
export interface WriteSession {
  session: EgmpSession | null
  reason: string | null
}

export interface ConfigToolDeps {
  dshHome: string
  /** 只走账号条：拿不到会话就不写（**绝不**回落到磁盘上的旧会话）。 */
  openSession: () => Promise<WriteSession>
  /** 对话弹窗那一侧的实现（host 注入）。 */
  approvals: ApprovalGate
  /** 当前是否要求审批（环境变量优先，其次页面开关；默认要求）。 */
  requireApproval: () => boolean
}

const NEEDS_ACCOUNT_HINT = '写入必须使用底部账号条里选中的账号：请先在账号条里连接一个账号（没有就点「添加账号」），再让我重试。'

function textOf(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() !== '' ? value : undefined
}

/** 从 DSH 的 tool exec 上下文里尽量取当前工作区根；取不到时由调用方显式传 review.workspaceDir，绝不回落 profile/仓库。 */
export function workspaceRootOf(exec: unknown): string | undefined {
  const info = exec as {
    cwd?: unknown
    workspaceRoot?: unknown
    workspace?: { root?: unknown; cwd?: unknown }
    session?: { cwd?: unknown; workspaceRoot?: unknown; workspace?: { root?: unknown } }
    agent?: {
      session?: { cwd?: unknown; workspaceRoot?: unknown; workspace?: { root?: unknown } }
    }
  } | undefined
  return textOf(info?.cwd)
    ?? textOf(info?.workspaceRoot)
    ?? textOf(info?.workspace?.root)
    ?? textOf(info?.workspace?.cwd)
    ?? textOf(info?.session?.cwd)
    ?? textOf(info?.session?.workspaceRoot)
    ?? textOf(info?.session?.workspace?.root)
    ?? textOf(info?.agent?.session?.cwd)
    ?? textOf(info?.agent?.session?.workspaceRoot)
    ?? textOf(info?.agent?.session?.workspace?.root)
}

/** 审核文档链接需要 Session id；优先 tool exec，其次 DSH 进程环境。 */
export function sessionIdOf(exec: unknown): string | undefined {
  const info = exec as {
    sessionId?: unknown
    session?: { id?: unknown; sessionId?: unknown }
    agent?: { id?: unknown; session?: { id?: unknown; sessionId?: unknown } }
  } | undefined
  return textOf(info?.sessionId)
    ?? textOf(info?.session?.id)
    ?? textOf(info?.session?.sessionId)
    ?? textOf(info?.agent?.id)
    ?? textOf(info?.agent?.session?.id)
    ?? textOf(info?.agent?.session?.sessionId)
    ?? textOf(process.env.DSH_SESSION_ID)
}

const KIND_LABEL: Record<string, string> = {
  picklist: '选项集',
  object: '对象',
  lifecycle: '生命周期',
  field: '字段',
  formLayout: '表单布局',
  listLayout: '列表布局',
  menu: '菜单',
  call: '接口',
  workflow: '工作流',
}

function actionVerb(action: string): string {
  return action === 'create' ? '新建' : '更新'
}

/**
 * 审批窗的标题。只有一步时给「更新选项集 akw_test_pk__c」这种人话，多步时给规模。
 */
export function approvalTitle(plan: Plan): string {
  const edits = plan.steps.filter((step) => step.action !== 'skip')
  if (edits.length === 1) {
    const step = edits[0]
    if (step !== undefined) {
      const kind = KIND_LABEL[step.kind] ?? step.kind
      const params = (step.params ?? {}) as Record<string, unknown>
      const code = typeof params.code === 'string' ? params.code : ''
      return `${actionVerb(step.action)}${kind}${code === '' ? '' : ` ${code}`}`
    }
  }
  const parts: string[] = []
  if (plan.summary.create > 0) parts.push(`新建 ${plan.summary.create}`)
  if (plan.summary.update > 0) parts.push(`更新 ${plan.summary.update}`)
  return `${edits.length} 步变更（${parts.join(' · ') || '无写入'}）`
}

/**
 * 业务摘要里的先后：先对象，再挂在对象上的东西，最后接口/流程。
 * 只影响展示顺序，不影响施工顺序。
 */
const BUSINESS_KIND_ORDER: readonly string[] = ['object', 'picklist', 'field', 'lifecycle', 'action', 'listLayout', 'formLayout', 'menu', 'workflow', 'call']

/**
 * 步骤归到哪一类「业务资源」。`call` 是按能力目录原样调的，但工作流和状态按钮
 * 对用户来说仍是独立的东西，不该混进「接口」里念。
 */
function businessKind(step: Step): string {
  if (step.kind !== 'call') return step.kind
  if (step.capabilityId.startsWith('workflow.')) return 'workflow'
  if (step.capabilityId.startsWith('lifecycle.useraction') || step.capabilityId.startsWith('lifecycle.enteraction')) return 'action'
  return 'call'
}

/** 一步的「人话名字」：对象 / 字段 / 状态 / 菜单取 name，布局取对象，接口取标题。 */
function businessName(step: Step): string {
  const params = (step.params ?? {}) as Record<string, unknown>
  const name = typeof params.name === 'string' ? params.name.trim() : ''
  if (name !== '') return name
  const objectCode = typeof params.object === 'string' ? params.object.trim() : ''
  if (objectCode !== '') return objectCode
  return step.title
}

/**
 * 计划 → 业务摘要：按「新建 / 更新 × 资源种类」归并，同类合并名称。
 * 弹窗顶部先说人话，逐条接口折在下面，避免把一屏 JSON 甩给人审。
 */
export function buildBusinessSummary(plan: Plan): ApprovalBusinessGroup[] {
  const groups: ApprovalBusinessGroup[] = []
  for (const step of plan.steps) {
    if (step.action === 'skip') continue
    const kind = businessKind(step)
    let group = groups.find((item) => item.action === step.action && item.kind === kind)
    if (group === undefined) {
      group = { action: step.action, kind, names: [] }
      groups.push(group)
    }
    const name = businessName(step)
    if (!group.names.includes(name)) group.names.push(name)
  }
  const rank = (kind: string): number => {
    const index = BUSINESS_KIND_ORDER.indexOf(kind)
    return index === -1 ? BUSINESS_KIND_ORDER.length : index
  }
  return groups.sort((a, b) => rank(a.kind) - rank(b.kind))
}

/** 把计划翻译成弹窗要显示的结构（每一步带真实的方法与路径）。 */
export function buildApprovalReport(plan: Plan, session: EgmpSession, hashOverride?: string): ApprovalReport {
  const steps: ApprovalReportStep[] = []
  let index = 0
  for (const step of plan.steps) {
    if (step.action === 'skip') continue
    index += 1
    const capability = CAPABILITIES.find((item) => item.id === step.capabilityId)
    steps.push({
      index,
      action: step.action,
      kind: step.kind,
      title: step.title,
      method: capability?.method ?? 'POST',
      path: capability?.path ?? '',
      risk: step.risk,
      irreversible: step.irreversible,
      params: step.params,
      warnings: step.warnings,
    })
  }
  const baseUrl = session.baseUrl
  return {
    planId: plan.planId,
    title: approvalTitle(plan),
    env: plan.env,
    baseUrl,
    host: baseUrl.startsWith('https://') ? baseUrl.slice('https://'.length) : baseUrl,
    account: session.account,
    accountId: session.accountId ?? '',
    summary: plan.summary,
    business: buildBusinessSummary(plan),
    steps,
    createdAt: plan.createdAt,
    expiresAt: plan.expiresAt,
    hash: hashOverride ?? planHash(plan),
  }
}

export function buildConfigPlanTool(deps: ConfigToolDeps) {
  return {
    name: 'akso_workbench_config_plan',
    description:
      '把一份配置蓝图（JSON）转换成执行计划：读取平台现状、去重、按依赖排序、标出风险与不可逆步骤。'
      + '**只生成计划，不写任何配置**。返回 planId、差异摘要与逐步清单；执行必须由人在审批窗里同意后调用 apply 工具。'
      + '也可以传 task（阶段数组）生成**任务级计划**：一条任务包含多个有序阶段，整条任务只在执行时审批一次；'
      + '每段在执行到它时按当时的平台状态重算差异，所以字段/布局可以用上一步刚建出来的对象。'
      + '写入账号取自底部账号条：账号条为空时本工具会返回 needs-account，并不会拿别的账号凑合。',
    parameters: {
      type: 'object',
      properties: {
        blueprint: {
          type: 'string',
          description: '蓝图 JSON 字符串：{ version, env, resources:[{kind, ...}] }；kind 支持 picklist/object/lifecycle/field/listLayout/menu，以及 call（按能力目录 id 原样调用，用于工作流、状态按钮等能力目录里的其余接口）；formLayout 不做自动合并（全量替换风险高），请用 call + layout.save 提交完整布局',
        },
        task: {
          type: 'string',
          description: '任务 JSON 字符串：{ name?, stages:[{ name?, blueprint }] }，stages 按执行顺序排列，最多 12 段；每段 blueprint 与单计划同格式。与 blueprint 二选一。适合「建对象 → 建字段 → 改布局 → 建菜单 → 配工作流」这种一条任务多个阶段、只审批一次的场景。',
        },
      },
      required: [],
      additionalProperties: false,
    },
    output: {
      schema: { type: 'object' },
      render(_args: unknown, value: unknown) {
        return [{ type: 'text', text: JSON.stringify(value, null, 2) }]
      },
    },
    async execute(args: unknown): Promise<unknown> {
      const opened = await deps.openSession()
      const session = opened.session
      if (session === null) {
        return { ok: false, status: 'needs-account', error: opened.reason ?? '账号条没有可用的账号', hint: NEEDS_ACCOUNT_HINT }
      }
      const input = (args ?? {}) as { blueprint?: string; task?: string }
      const hasBlueprint = typeof input.blueprint === 'string' && input.blueprint.trim() !== ''
      const hasTask = typeof input.task === 'string' && input.task.trim() !== ''
      if (hasBlueprint === hasTask) return { ok: false, error: 'blueprint 与 task 必须二选一' }

      const stepView = (step: Step): Record<string, unknown> => ({
        id: step.id,
        kind: step.kind,
        action: step.action,
        title: step.title,
        risk: step.risk,
        irreversible: step.irreversible,
        warnings: step.warnings,
      })

      if (hasTask) {
        const parsed = parseTaskInput(input.task)
        if (!parsed.valid) return { ok: false, error: parsed.error }
        try {
          const task = createTask(session, { name: parsed.name, stages: parsed.stages })
          const plans = await previewTaskPlans(session, task)
          saveTask(deps.dshHome, task)
          const merged = mergeTaskPreview(task, plans)
          return {
            ok: true,
            taskId: task.taskId,
            expiresAt: task.expiresAt,
            target: task.target,
            summary: merged.summary,
            warnings: merged.warnings,
            stages: plans.map((plan, index) => ({
              stage: index + 1,
              name: task.stages[index]?.name ?? `第 ${index + 1} 阶段`,
              summary: plan.summary,
              steps: plan.steps.map(stepView),
            })),
            next: `这条任务含 ${task.stages.length} 个阶段、${merged.summary.create + merged.summary.update} 步变更，其中 ${merged.summary.irreversible} 步不可逆。请让用户确认后，用 taskId=${task.taskId} 调用 akso_workbench_config_apply（整条任务只弹一次审批窗）。`,
          }
        } catch (error) {
          return { ok: false, error: error instanceof Error ? error.message : String(error) }
        }
      }

      let blueprint: unknown
      try {
        blueprint = JSON.parse(String(input.blueprint ?? '{}'))
      } catch (error) {
        return { ok: false, error: `蓝图不是合法 JSON：${error instanceof Error ? error.message : String(error)}` }
      }
      try {
        const plan = await planBlueprint(session, blueprint as never, deps.dshHome)
        return {
          ok: true,
          planId: plan.planId,
          expiresAt: plan.expiresAt,
          target: plan.target,
          summary: plan.summary,
          warnings: plan.warnings,
          steps: plan.steps.map(stepView),
          next: `涉及 ${plan.summary.irreversible} 个不可逆步骤。请让用户确认后，用 planId=${plan.planId} 调用 akso_workbench_config_apply（会弹审批窗，人点「同意」才写）。`,
        }
      } catch (error) {
        return { ok: false, error: error instanceof Error ? error.message : String(error) }
      }
    },
  }
}

/**
 * 审批门的公共流程：生成/复用审核文档 → 挂起审批 → 按人的决定落记录。
 * 单计划与任务级共用；`report.hash` 决定审核文档与审批记录的指纹。
 */
async function gateApproval(
  deps: ConfigToolDeps,
  report: ApprovalReport,
  planForAudit: Plan,
  input: { review?: ApprovalReviewInput; prepareOnly?: boolean; reviewPath?: string },
  exec: unknown,
  needsApproval: boolean,
): Promise<{ ok: true; reviewPath?: string } | { ok: false; result: Record<string, unknown> }> {
  const ref = { planId: report.planId }
  if (!needsApproval) {
    if (input.prepareOnly === true) {
      return { ok: false, result: { ok: true, status: 'no-approval-needed', ...ref, message: '没有需要审批的写入步骤；未生成审核文档，平台一步都没写。' } }
    }
    return { ok: true }
  }
  let reviewPath: string | undefined = textOf(input.reviewPath)
  const sessionId = sessionIdOf(exec)
  if (reviewPath !== undefined) {
    const loaded = loadApprovalReview(reviewPath, report, sessionId)
    if (loaded === null) {
      return {
        ok: false,
        result: {
          ok: false,
          status: 'review-unavailable',
          ...ref,
          message: '指定的审核文档不存在、已结束或与当前计划指纹不一致；本次不进入审批、平台一步都没写。',
        },
      }
    }
    report.review = loaded
  } else {
    const workspaceRoot = textOf(input.review?.workspaceDir) ?? workspaceRootOf(exec)
    attachApprovalReview(report, input.review, deps.dshHome, sessionId, workspaceRoot)
  }
  reviewPath = report.review?.path
  if (reviewPath === undefined || !reviewMatchesPlan(reviewPath, report.hash)) {
    return {
      ok: false,
      result: {
        ok: false,
        status: 'review-unavailable',
        ...ref,
        message: '审核文档没有生成或与当前计划不一致，本次不进入审批、平台一步都没写。请确认工作区可写后重试。',
      },
    }
  }
  if (input.prepareOnly === true) {
    return {
      ok: false,
      result: {
        ok: true,
        status: 'prepared',
        ...ref,
        review: report.review,
        message: '审核文档已生成，审批窗尚未打开，平台一步都没写。请先把目标/方案/预期结果和文件链接发给用户；确认后再调用 config_apply，并带 reviewPath。',
      },
    }
  }
  const signal = (exec as { signal?: unknown } | undefined)?.signal
  const decision = await deps.approvals.request(report, signal === undefined ? {} : { signal })
  if (decision.verdict !== 'busy') {
    recordApprovalDecision(deps.dshHome, planForAudit, decision.verdict, decision.note)
  }
  const fail = (status: string, message: string, extra: Record<string, unknown> = {}): { ok: false; result: Record<string, unknown> } => ({
    ok: false,
    result: { ok: false, status, ...ref, ...extra, message },
  })
  if (decision.verdict === 'busy') return fail('busy', '已经有一条审批在等回复，这一条没有执行。')
  if (decision.verdict === 'timeout') return fail('approval-timeout', '审批窗等了 8 分钟没有回应，平台一步都没写。')
  if (decision.verdict === 'cancelled') return fail('cancelled', '这次执行被中断（没有收到人的同意），平台一步都没写。')
  if (decision.verdict === 'deny') return fail('denied', '人没有同意这次写入，平台一步都没写。')
  if (decision.verdict === 'supplement') {
    const note = decision.note ?? ''
    return fail(
      'supplement',
      `人没有同意写入，补充了意见：${note === '' ? '（没有文字）' : note}。请按补充意见修改计划，再重新提交审批；这次平台一步都没写。`,
      { note },
    )
  }
  saveApproval(deps.dshHome, { planId: report.planId, hash: report.hash, at: Date.now() })
  return { ok: true, reviewPath }
}

async function runTaskApply(
  deps: ConfigToolDeps,
  session: EgmpSession,
  task: Task,
  input: { review?: ApprovalReviewInput; prepareOnly?: boolean; reviewPath?: string },
  exec: unknown,
): Promise<unknown> {
  let plans: Plan[]
  try {
    plans = await previewTaskPlans(session, task)
  } catch (error) {
    return { ok: false, error: `任务预览失败：${error instanceof Error ? error.message : String(error)}` }
  }
  const merged = mergeTaskPreview(task, plans)
  const writes = merged.steps.filter((step) => step.action !== 'skip')
  const needsApproval = writes.length > 0 && deps.requireApproval()
  const report = buildApprovalReport(merged, session, taskHash(task))
  // 任务级审核文档用任务名做标题，而不是「N 步变更」。
  const gateInput = { ...input, review: { ...(input.review ?? {}), taskName: input.review?.taskName ?? task.name } }
  const gate = await gateApproval(deps, report, merged, gateInput, exec, needsApproval)
  if (!gate.ok) return gate.result
  try {
    const applied = await applyTask(session, task.taskId, deps.dshHome, { requireApproval: needsApproval })
    const ok = applied.status === 'applied' && applied.results.every((stage) => stage.status !== 'failed')
    if (ok) {
      finalizeApprovalReview(
        gate.reviewPath,
        `任务全部阶段执行成功（${applied.results.length} 段）`,
        applied.results.flatMap((stage) => stage.steps.map((step) => ({
          title: `${stage.name} · ${step.title}`,
          status: step.status,
          message: step.message,
          durationMs: step.durationMs,
        }))),
      )
    }
    return {
      ok,
      status: applied.status,
      taskId: task.taskId,
      approved: needsApproval,
      stages: applied.results.map((stage) => ({
        stage: stage.stage,
        name: stage.name,
        status: stage.status,
        message: stage.message,
        steps: stage.steps.map((step) => ({
          step: step.title,
          status: step.status,
          message: step.message,
          durationMs: step.durationMs,
        })),
      })),
    }
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) }
  }
}

export function buildConfigApplyTool(deps: ConfigToolDeps) {
  return {
    name: 'akso_workbench_config_apply',
    description:
      '执行一份**已存在**的配置计划，或一条已存在的任务（必须先由 akso_workbench_config_plan 生成）。'
      + '逐步执行、每步回读校验；某步失败即停并返回 checkpoint。不接受临时参数，不能绕过计划。'
      + '计划/任务里有写入时，本工具会在**对话里弹出一个审批窗**：先打开一份审核文档，'
      + '人点「同意」才发请求，点「拒绝」一步都不写，填写「调整内容」则交回模型改计划。'
      + '传 taskId 时，整条任务只弹这一次窗：同意后按阶段顺序执行，每段在轮到它时按当时平台状态重算差异，'
      + '所以后面的字段/布局能用上前面刚建出来的对象。'
      + '传 prepareOnly:true 时只生成审核文档并返回路径/链接，不弹审批窗、不写平台；'
      + '拿到链接先发摘要消息，再用 reviewPath 正式进入审批。'
      + '这一关不在工具参数里，模型无法自己批准。',
    parameters: {
      type: 'object',
      properties: {
        planId: { type: 'string', description: '计划 id（pln_ 开头），来自 config_plan 的返回' },
        taskId: {
          type: 'string',
          description: '任务 id（tsk_ 开头），来自 config_plan 传 task 的返回。与 planId 二选一；整条任务只审批一次。',
        },
        prepareOnly: {
          type: 'boolean',
          description: '只生成审核文档并返回路径/链接，不弹审批窗、不写平台。用于先给用户看“目标/方案/预期结果 + 文件链接”。',
        },
        reviewPath: {
          type: 'string',
          description: '已通过 prepareOnly 生成的审核文档绝对路径；传入后复用该文档进入审批，不再新建。',
        },
        review: {
          type: 'object',
          description:
            '可选：给审批文档补充本次任务的问题、执行思路和预期结果；不填则按计划自动生成。'
            + '新建/大规模调整用 kind=create（默认）；任务完成后的局部更新用 kind=update，'
            + '并传 previousReviewPath 引用上一份已执行文档。已执行的文档不会被修改，更新必须新建一份。',
          properties: {
            kind: { type: 'string', enum: ['create', 'update'], description: '文档类型：create 大规模创建/调整；update 小范围更新（默认 create）' },
            taskName: { type: 'string', description: '任务名；用于审核文档命名，默认取审批标题' },
            previousReviewPath: { type: 'string', description: '小范围更新：上一份已执行审核文档的绝对路径；只引用，不修改' },
            problem: { type: 'string', description: '本次任务需要解决什么问题' },
            approach: { type: 'string', description: '本次任务计划怎么执行' },
            expected: { type: 'array', items: { type: 'string' }, description: '本次任务完成后预期结果，逐条作为 checklist' },
            reason: { type: 'string', description: '小范围更新的调整原因' },
            workspaceDir: { type: 'string', description: '当前工作区根目录；审核文档会落在 <workspaceDir>/akso-workbench-data/reviews，省略时从 exec 推断' },
          },
          additionalProperties: false,
        },
      },
      required: [],
      additionalProperties: false,
    },
    output: {
      schema: { type: 'object' },
      render(_args: unknown, value: unknown) {
        return [{ type: 'text', text: JSON.stringify(value, null, 2) }]
      },
    },
    async execute(args: unknown, exec?: unknown): Promise<unknown> {
      const opened = await deps.openSession()
      const session = opened.session
      if (session === null) {
        return { ok: false, status: 'needs-account', error: opened.reason ?? '账号条没有可用的账号', hint: NEEDS_ACCOUNT_HINT }
      }
      const input = (args ?? {}) as {
        planId?: string
        taskId?: string
        review?: ApprovalReviewInput
        prepareOnly?: boolean
        reviewPath?: string
      }
      const planId = String(input.planId ?? '')
      const taskId = String(input.taskId ?? '')
      if ((planId === '') === (taskId === '')) {
        return { ok: false, error: 'planId 与 taskId 必须二选一' }
      }
      if (taskId !== '') {
        const task = loadTask(deps.dshHome, taskId)
        if (task === null) return { ok: false, error: `任务不存在：${taskId}` }
        return runTaskApply(deps, session, task, input, exec)
      }

      const plan = loadPlan(deps.dshHome, planId)
      if (plan === null) return { ok: false, error: `计划不存在：${planId}` }

      const writes = plan.steps.filter((step) => step.action !== 'skip')
      const needsApproval = writes.length > 0 && deps.requireApproval()
      const report = buildApprovalReport(plan, session)
      const gate = await gateApproval(deps, report, plan, input, exec, needsApproval)
      if (!gate.ok) return gate.result

      try {
        const applied = await applyPlan(session, planId, deps.dshHome, { requireApproval: needsApproval })
        const ok = applied.results.every((r) => r.status !== 'failed')
        if (ok) {
          finalizeApprovalReview(
            gate.reviewPath,
            `全部步骤执行成功（${applied.results.length} 步）`,
            applied.results.map((r) => ({
              title: r.title,
              status: r.status,
              message: r.message,
              durationMs: r.durationMs,
            })),
          )
        }
        return {
          ok,
          status: applied.status,
          planId,
          approved: needsApproval,
          results: applied.results.map((r) => ({
            step: r.title,
            status: r.status,
            message: r.message,
            durationMs: r.durationMs,
          })),
        }
      } catch (error) {
        return { ok: false, error: error instanceof Error ? error.message : String(error) }
      }
    },
  }
}
