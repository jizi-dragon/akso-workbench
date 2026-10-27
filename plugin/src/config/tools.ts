/**
 * 给 agent 的配置工具：**只能出计划 / 执行已有计划**，拿不到任何裸写端点。
 *
 * 与 `tool.ts` 同一套手写 ToolDefinition 形状（零运行时依赖）。
 * 两个工具都要求 eGMP 会话已建立；未登录时返回 loggedIn:false + 提示。
 */
import { applyPlan, loadPlan, planBlueprint } from './engine.ts'
import { readEgmpSession, type EgmpSession } from './session.ts'

export interface ConfigToolDeps {
  dshHome: string
}

function sessionOf(deps: ConfigToolDeps): EgmpSession | null {
  return readEgmpSession(deps.dshHome)
}

const LOGIN_HINT = '还没有登录 eGMP：请打开「akso-workbench · 工具」页，填写环境地址与账号完成登录。'

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

export interface ApplyApproval {
  /** 要不要在宿主侧弹一次审批。 */
  ask: boolean
  /** 给审批面板看的一句话（同时进审计日志）。 */
  reason?: string
  displayReason?: { en: string; zh: string }
}

/**
 * 「执行前要不要问人」的判定 —— **唯一实现，放在宿主侧**。
 *
 * 由 `tools/pre-execute` 调用，参数直接取模型的工具入参；判定只看**盘上的计划**：
 * 全 `skip` 的计划没有任何写入，问了只是噪音；计划不存在就让工具自己报错。
 * 关键是这里不接受任何「已确认」布尔 —— 那种字段等于让模型自己给自己签字。
 */
export function applyApprovalFor(dshHome: string, args: unknown): ApplyApproval {
  const raw = args !== null && typeof args === 'object' ? (args as { planId?: unknown }) : {}
  const planId = typeof raw.planId === 'string' ? raw.planId.trim() : ''
  if (planId === '') return { ask: false }
  const plan = loadPlan(dshHome, planId)
  if (plan === null) return { ask: false }
  const writes = plan.summary.create + plan.summary.update
  if (writes === 0) return { ask: false }
  const irreversible = plan.steps.filter((s) => s.irreversible)
  const names = irreversible.slice(0, 5).map((s) => s.title).join('；')
  const reason = `执行配置计划 ${plan.planId}：新建 ${plan.summary.create} 步、更新 ${plan.summary.update} 步`
    + (plan.summary.irreversible > 0
      ? `，其中 ${plan.summary.irreversible} 步不可逆（${names}${irreversible.length > 5 ? ' 等' : ''}）`
      : '')
    + '。允许之后才会写平台，且写入即刻生效、平台侧没有回滚。'
  return {
    ask: true,
    reason,
    displayReason: {
      en: `Apply config plan ${plan.planId}? ${plan.summary.create} create, ${plan.summary.update} update, `
        + `${plan.summary.irreversible} irreversible. Writes take effect immediately and cannot be rolled back.`,
      zh: reason,
    },
  }
}

export function buildConfigApplyTool(deps: ConfigToolDeps) {
  return {
    name: 'akso_workbench_config_apply',
    description:
      '执行一份**已存在**的配置计划（必须先由 akso_workbench_config_plan 生成）。'
      + '逐步执行、每步回读校验；失败即停并返回 checkpoint。不接受临时参数，不能绕过计划。'
      + '**真正执行前宿主会弹一次审批，只有人点了允许才会写平台**；这一关不在工具参数里，模型无法自己确认。',
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
    async execute(args: unknown): Promise<unknown> {
      const session = sessionOf(deps)
      if (session === null) return { loggedIn: false, hint: LOGIN_HINT }
      const input = (args ?? {}) as { planId?: string }
      const planId = String(input.planId ?? '')
      if (planId === '') return { ok: false, error: '缺少 planId' }
      const plan = loadPlan(deps.dshHome, planId)
      if (plan === null) return { ok: false, error: `计划不存在：${planId}` }
      try {
        const applied = await applyPlan(session, planId, deps.dshHome)
        return {
          ok: applied.status === 'applied',
          status: applied.status,
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
