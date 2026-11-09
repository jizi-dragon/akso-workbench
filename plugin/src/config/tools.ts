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
import { applyPlan, loadPlan, planBlueprint, planHash, recordApprovalDecision, saveApproval, type Plan } from './engine.ts'
import type { ApprovalGate, ApprovalReport, ApprovalReportStep } from './approval.ts'
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

/** 把计划翻译成弹窗要显示的结构（每一步带真实的方法与路径）。 */
export function buildApprovalReport(plan: Plan, session: EgmpSession): ApprovalReport {
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
    steps,
    createdAt: plan.createdAt,
    expiresAt: plan.expiresAt,
    hash: planHash(plan),
  }
}

export function buildConfigPlanTool(deps: ConfigToolDeps) {
  return {
    name: 'akso_workbench_config_plan',
    description:
      '把一份配置蓝图（JSON）转换成执行计划：读取平台现状、去重、按依赖排序、标出风险与不可逆步骤。'
      + '**只生成计划，不写任何配置**。返回 planId、差异摘要与逐步清单；执行必须由人在审批窗里同意后调用 apply 工具。'
      + '写入账号取自底部账号条：账号条为空时本工具会返回 needs-account，并不会拿别的账号凑合。',
    parameters: {
      type: 'object',
      properties: {
        blueprint: {
          type: 'string',
          description: '蓝图 JSON 字符串：{ version, env, resources:[{kind, ...}] }；kind 支持 picklist/object/lifecycle/field/listLayout/menu，以及 call（按能力目录 id 原样调用，用于工作流、状态按钮等能力目录里的其余接口）；formLayout 不做自动合并（全量替换风险高），请用 call + layout.save 提交完整布局',
        },
      },
      required: ['blueprint'],
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
      const input = (args ?? {}) as { blueprint?: string }
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
          steps: plan.steps.map((s) => ({
            id: s.id,
            kind: s.kind,
            action: s.action,
            title: s.title,
            risk: s.risk,
            irreversible: s.irreversible,
            warnings: s.warnings,
          })),
          next: `涉及 ${plan.summary.irreversible} 个不可逆步骤。请让用户确认后，用 planId=${plan.planId} 调用 akso_workbench_config_apply（会弹审批窗，人点「同意」才写）。`,
        }
      } catch (error) {
        return { ok: false, error: error instanceof Error ? error.message : String(error) }
      }
    },
  }
}

export function buildConfigApplyTool(deps: ConfigToolDeps) {
  return {
    name: 'akso_workbench_config_apply',
    description:
      '执行一份**已存在**的配置计划（必须先由 akso_workbench_config_plan 生成）。'
      + '逐步执行、每步回读校验；某步失败即停并返回 checkpoint。不接受临时参数，不能绕过计划。'
      + '计划里有写入时，本工具会在**对话里弹出一个审批窗**，逐条列出要写的方法、路径、参数与风险；'
      + '人点「同意」才发请求，点「拒绝」一步都不写，点「补充」则把补充意见回给模型改计划。'
      + '这一关不在工具参数里，模型无法自己批准。',
    parameters: {
      type: 'object',
      properties: {
        planId: { type: 'string', description: '计划 id（pln_ 开头），来自 config_plan 的返回' },
      },
      required: ['planId'],
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
      const input = (args ?? {}) as { planId?: string }
      const planId = String(input.planId ?? '')
      if (planId === '') return { ok: false, error: '缺少 planId' }
      const plan = loadPlan(deps.dshHome, planId)
      if (plan === null) return { ok: false, error: `计划不存在：${planId}` }

      const writes = plan.steps.filter((step) => step.action !== 'skip')
      const needsApproval = writes.length > 0 && deps.requireApproval()
      if (needsApproval) {
        const report = buildApprovalReport(plan, session)
        const signal = (exec as { signal?: unknown } | undefined)?.signal
        const decision = await deps.approvals.request(report, signal === undefined ? {} : { signal })
        // 审批决策留痕：拒批 / 补充 / 超时 / 中断也要能查到（busy 只是"没排上"，不记）
        if (decision.verdict !== 'busy') {
          recordApprovalDecision(deps.dshHome, plan, decision.verdict, decision.note)
        }
        if (decision.verdict === 'busy') {
          return { ok: false, status: 'busy', planId, message: '已经有一条审批在等回复，这一条没有执行。' }
        }
        if (decision.verdict === 'timeout') {
          return { ok: false, status: 'approval-timeout', planId, message: '审批窗等了 8 分钟没有回应，平台一步都没写。' }
        }
        if (decision.verdict === 'cancelled') {
          return { ok: false, status: 'cancelled', planId, message: '这次执行被中断（没有收到人的同意），平台一步都没写。' }
        }
        if (decision.verdict === 'deny') {
          return { ok: false, status: 'denied', planId, message: '人没有同意这次写入，平台一步都没写。' }
        }
        if (decision.verdict === 'supplement') {
          const note = decision.note ?? ''
          return {
            ok: false,
            status: 'supplement',
            planId,
            note,
            message: `人没有同意写入，补充了意见：${note === '' ? '（没有文字）' : note}。请按补充意见修改计划，再重新提交审批；这次平台一步都没写。`,
          }
        }
        // approve：先落审批记录（引擎会按指纹再验一次），再执行
        saveApproval(deps.dshHome, { planId, hash: report.hash, at: Date.now() })
      }

      try {
        const applied = await applyPlan(session, planId, deps.dshHome, { requireApproval: needsApproval })
        return {
          ok: applied.results.every((r) => r.status !== 'failed'),
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
