/**
 * 给 agent 的配置工具：**只能出计划 / 执行已有计划**，拿不到任何裸写端点。
 *
 * 与 `tool.ts` 同一套手写 ToolDefinition 形状（零运行时依赖）。
 * 两个工具都要求 eGMP 会话已建立；未登录时返回 loggedIn:false + 提示。
 *
 * 写入前的确认走宿主自己的「用户提问」通道（与 ask_user_question 同一套界面）：
 * 工具在对话里弹出一张卡片，人点「允许这次写入」之后才真的发请求；
 * 点「拒绝」或通道不可用 ⇒ 一律不写（fail closed）。
 */
import { CAPABILITIES } from './capabilities.ts'
import { applyPlan, loadPlan, planBlueprint, type Plan } from './engine.ts'
import { readEgmpSession, type EgmpSession } from './session.ts'

/** 人点了这个选项才算同意写入。 */
export const APPROVE_LABEL = '允许这次写入'
/** 另一个选项；只是拒绝，不需要额外语义。 */
export const DENY_LABEL = '拒绝'

export interface AskOption {
  label: string
  description?: string
}

export interface AskQuestion {
  id: string
  header?: string
  question: string
  detail?: string
  options: AskOption[]
  /**
   * 走宿主的「计划审阅」版式：卡片只显示标题 + 一段摘要，正文（detail 的 markdown）
   * 自动在右侧栏打开 —— 比把整段 JSON 塞进提问卡片干净得多。
   * 约束（宿主会校验）：approve 必须是 options 里的某个 label，且必须有 detail。
   */
  intent?: { kind: 'plan-review'; approve: string }
}

/** 工具执行上下文里我们真正用得到的两样（agent / 中断信号）。 */
export interface ToolExec {
  agent?: unknown
  signal?: unknown
}

/**
 * 把问题交给宿主的人机通道，返回人选中的标签。
 * 返回 `null` 表示这个宿主没有可用通道（必须当成"没有人同意"）。
 */
export type ApprovalAsker = (questions: AskQuestion[], exec: ToolExec) => Promise<string[] | null>

export interface ConfigToolDeps {
  dshHome: string
  ask?: ApprovalAsker
}

function sessionOf(deps: ConfigToolDeps): EgmpSession | null {
  return readEgmpSession(deps.dshHome)
}

const LOGIN_HINT = '还没有登录 eGMP：请打开「akso-workbench · 配置工具」页，填写环境地址与账号完成登录。'

const HOST_PREFIX = 'https://'
const STEP_JSON_LIMIT = 6000

const KIND_LABEL: Record<string, string> = {
  picklist: '选项集',
  object: '对象',
  lifecycle: '生命周期',
  field: '字段',
  formLayout: '表单布局',
  listLayout: '列表布局',
  menu: '菜单',
}

function actionVerb(action: string): string {
  return action === 'create' ? '新建' : '更新'
}

/**
 * 卡片标题（宿主取 detail 的**第一行**当标题）。
 * 只有一步时给「更新选项集 akw_test_pk__c」这种人话，多步时给规模。
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
 * 审批卡正文（markdown）。
 *
 * 结构刻意做成「标题行 / 一行摘要 / 每步一个小节」：宿主的审阅卡只把**第一行**当标题、
 * **第一个段落**当摘要，其余全文在右侧栏里读 —— 所以第一行必须是结论，不能是表头或分隔线。
 */
export function approvalDetail(plan: Plan, baseUrl: string): string {
  const host = baseUrl.startsWith(HOST_PREFIX) ? baseUrl.slice(HOST_PREFIX.length) : baseUrl
  const lines: string[] = []
  lines.push(approvalTitle(plan))
  lines.push('')
  const scale: string[] = []
  if (plan.summary.create > 0) scale.push(`新建 ${plan.summary.create} 步`)
  if (plan.summary.update > 0) scale.push(`更新 ${plan.summary.update} 步`)
  if (plan.summary.skip > 0) scale.push(`跳过 ${plan.summary.skip} 步`)
  lines.push(`环境 \`${plan.env}\` · 目标 \`${host}\` · ${scale.join(' · ') || '没有要写的内容'}`)
  lines.push('')
  let index = 0
  for (const step of plan.steps) {
    if (step.action === 'skip') continue
    index += 1
    const capability = step.capabilityId === undefined
      ? undefined
      : CAPABILITIES.find((item) => item.id === step.capabilityId)
    const method = capability?.method ?? 'POST'
    const path = capability?.path ?? ''
    const tags: string[] = [`\`${method} ${path}\``]
    if (step.irreversible) tags.push('不可逆')
    else if (step.risk === 'high') tags.push('高风险')
    lines.push(`**${index}. ${step.title}**`)
    lines.push(tags.join(' · '))
    lines.push('')
    let payload = JSON.stringify(step.params ?? {}, null, 2)
    if (payload.length > STEP_JSON_LIMIT) {
      payload = `${payload.slice(0, STEP_JSON_LIMIT)}\n…（已截断，完整内容见计划文件 ${plan.planId}.json）`
    }
    lines.push('```json')
    lines.push(payload)
    lines.push('```')
    lines.push('')
  }
  lines.push('---')
  lines.push('')
  lines.push(`计划 \`${plan.planId}\` · 写入即刻生效，平台侧没有回滚接口。`)
  return lines.join('\n')
}

export function buildConfigPlanTool(deps: ConfigToolDeps) {
  return {
    name: 'akso_workbench_config_plan',
    description:
      '把一份配置蓝图（JSON）转换成执行计划：读取平台现状、去重、按依赖排序、标出风险与不可逆步骤。'
      + '**只生成计划，不写任何配置**。返回 planId、差异摘要与逐步清单；执行必须由人确认后调用 apply 工具。',
    parameters: {
      type: 'object',
      properties: {
        blueprint: {
          type: 'string',
          description: '蓝图 JSON 字符串：{ version, env, resources:[{kind, ...}] }；kind 支持 picklist/object/lifecycle/field/formLayout/listLayout/menu',
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
      const session = sessionOf(deps)
      if (session === null) return { loggedIn: false, hint: LOGIN_HINT }
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
          next: `涉及 ${plan.summary.irreversible} 个不可逆步骤。请让用户确认后，用 planId=${plan.planId} 调用 akso_workbench_config_apply。`,
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
      + '计划里有写入时，本工具会**在这个对话里弹一张确认卡**：卡片上只留标题与规模，'
      + '完整内容在右侧栏展开（每条请求的方法、路径、请求体）；人点「允许这次写入」之后才发请求，'
      + '点「拒绝」则一步都不写。'
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
      const session = sessionOf(deps)
      if (session === null) return { loggedIn: false, hint: LOGIN_HINT }
      const input = (args ?? {}) as { planId?: string }
      const planId = String(input.planId ?? '')
      if (planId === '') return { ok: false, error: '缺少 planId' }
      const plan = loadPlan(deps.dshHome, planId)
      if (plan === null) return { ok: false, error: `计划不存在：${planId}` }

      const writes = plan.summary.create + plan.summary.update
      if (writes > 0) {
        if (deps.ask === undefined) {
          return {
            ok: false,
            status: 'no-approval-channel',
            error: '这个宿主没有可用的确认通道：写入前必须由人点「允许」，所以这一步不执行。',
          }
        }
        const detail = approvalDetail(plan, session.baseUrl)
        let chosen: string[] | null
        try {
          chosen = await deps.ask(
            [
              {
                id: `approve-${planId}`,
                header: '配置写入确认',
                question: `执行配置计划 ${planId}？${approvalTitle(plan)}`,
                detail,
                options: [
                  { label: APPROVE_LABEL, description: '按上面的内容写平台；写后无法在平台侧回滚' },
                  { label: DENY_LABEL, description: '一步都不写，计划保持未执行' },
                ],
                intent: { kind: 'plan-review', approve: APPROVE_LABEL },
              },
            ],
            (exec ?? {}) as ToolExec,
          )
        } catch (error) {
          return { ok: false, status: 'no-approval-channel', error: error instanceof Error ? error.message : String(error) }
        }
        if (chosen === null) {
          return { ok: false, status: 'no-approval-channel', error: '这个宿主没有可用的确认通道：写入前必须由人点「允许」，所以这一步不执行。' }
        }
        if (!chosen.includes(APPROVE_LABEL)) {
          return { ok: false, status: 'denied', planId, message: `人没有允许这次写入（选择：${chosen.join('、') || '无'}），平台一步都没写。` }
        }
      }

      try {
        const applied = await applyPlan(session, planId, deps.dshHome)
        return {
          ok: applied.results.every((r) => r.status !== 'failed'),
          status: applied.status,
          planId,
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
