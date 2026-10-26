/**
 * 给 agent 的配置工具：**只能出计划 / 执行已有计划**，拿不到任何裸写端点。
 *
 * 与 `tool.ts` 同一套手写 ToolDefinition 形状（零运行时依赖）。
 * 两个工具都要求 eGMP 会话已建立；未登录时返回 loggedIn:false + 提示。
 */
import { CAPABILITIES, groupByDomain } from './capabilities.ts'
import { applyPlan, loadPlan, planBlueprint } from './engine.ts'
import { publicSession, readEgmpSession, type EgmpSession } from './session.ts'

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

export function buildConfigApplyTool(deps: ConfigToolDeps) {
  return {
    name: 'akso_workbench_config_apply',
    description:
      '执行一份**已存在**的配置计划（必须先由 akso_workbench_config_plan 生成并被用户确认）。'
      + '逐步执行、每步回读校验；失败即停并返回 checkpoint。不接受临时参数，不能绕过计划。',
    parameters: {
      type: 'object',
      properties: {
        planId: { type: 'string', description: '计划 id（pln_ 开头），来自 config_plan 的返回' },
        confirmIrreversible: {
          type: 'boolean',
          description: '计划里含不可逆步骤时必须显式传 true（代表用户已确认）',
        },
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
      const input = (args ?? {}) as { planId?: string; confirmIrreversible?: boolean }
      const planId = String(input.planId ?? '')
      if (planId === '') return { ok: false, error: '缺少 planId' }
      const plan = loadPlan(deps.dshHome, planId)
      if (plan === null) return { ok: false, error: `计划不存在：${planId}` }
      if (plan.summary.irreversible > 0 && input.confirmIrreversible !== true) {
        return {
          ok: false,
          error: `计划包含 ${plan.summary.irreversible} 个不可逆步骤，必须由用户确认后传 confirmIrreversible:true`,
          irreversibleSteps: plan.steps.filter((s) => s.irreversible).map((s) => s.title),
        }
      }
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

/** 工具页用的能力清单（不经过工具注册）。 */
export function capabilitySummary(): unknown {
  return {
    session: publicSession(readEgmpSession(process.env.DSH_HOME ?? '')),
    groups: groupByDomain().map((g) => ({ domain: g.domain, items: g.items.map((c) => ({ id: c.id, title: c.title, method: c.method, path: c.path, risk: c.risk })) })),
    total: CAPABILITIES.length,
  }
}
