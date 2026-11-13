import type { AnalysisResult } from './analysis.ts'

export interface AnalysisToolDeps {
  /** 跑一次分析并落历史；session 不可用时返回 error，不抛。 */
  run: (query: string) => Promise<{ ok: true; result: AnalysisResult; historyId: string } | { ok: false; error: string }>
}

const PARAMETERS = {
  type: 'object',
  properties: {
    query: {
      type: 'string',
      description: '对象 id（UUID）或对象 code，例如 homework2__c / deviationmanagement__c。',
    },
  },
  required: ['query'],
  additionalProperties: false,
} as const

export function buildAnalysisTool(deps: AnalysisToolDeps) {
  return {
    name: 'akso_workbench_object_analysis',
    description:
      '分析一个 eGMP 对象的生命周期：按对象 id 或 code 读取启用状态、状态上的用户动作/进入动作、'
      + '动作的 behaviorType 与目标状态、相关工作流及步骤、进入条件。'
      + '只读，不修改平台；每次调用都会写入 akso-workbench 的对象分析历史（与页面共用同一份历史）。',
    parameters: PARAMETERS,
    output: {
      schema: { type: 'object' },
      render(_args: unknown, value: unknown) {
        return [{ type: 'text', text: JSON.stringify(value, null, 2) }]
      },
    },
    async execute(args: unknown): Promise<unknown> {
      const input = (args ?? {}) as { query?: string }
      const query = String(input.query ?? '').trim()
      if (query === '') return { ok: false, error: 'query 不能为空，请传对象 id 或 code' }
      try {
        const out = await deps.run(query)
        if (!out.ok) return { ok: false, error: out.error }
        const result = out.result
        return {
          ok: true,
          historyId: out.historyId,
          object: result.object,
          lifecycle: result.lifecycle,
          totals: result.totals,
          enterCondition: result.enterCondition,
          warnings: result.warnings,
          statuses: result.statuses.map((status) => ({
            name: status.name,
            code: status.code,
            sort: status.sort,
            isEnabled: status.isEnabled,
            userActions: status.userActions.map((action) => ({
              name: action.name,
              code: action.code,
              behaviorTypes: action.behaviorTypes,
              behaviorLabels: action.behaviorLabels,
              targetStatusId: action.targetStatusId ?? null,
              workflowName: action.workflowName,
              workflowId: action.workflowId,
            })),
            enterActions: status.enterActions.map((action) => ({
              name: action.name,
              code: action.code,
              behaviorTypes: action.behaviorTypes,
              behaviorLabels: action.behaviorLabels,
              targetStatusId: action.targetStatusId ?? null,
              workflowName: action.workflowName,
              workflowId: action.workflowId,
            })),
          })),
          workflows: (result.workflows ?? []).map((workflow) => ({
            name: workflow.name,
            code: workflow.code,
            basicId: workflow.basicId,
            configId: workflow.configId,
            status: workflow.status,
            isAllowTrigger: workflow.isAllowTrigger,
            steps: workflow.steps?.length ?? 0,
          })),
        }
      } catch (error) {
        return { ok: false, error: error instanceof Error ? error.message : String(error) }
      }
    },
  }
}
