import assert from 'node:assert/strict'
import test from 'node:test'
import { buildAnalysisTool } from '../src/analysis-tool.ts'

test('对象分析工具：返回摘要并带 historyId', async () => {
  const tool = buildAnalysisTool({
    run: async (query) => ({
      ok: true,
      historyId: 'h-1',
      result: {
        source: { baseUrl: 'https://example.test', account: 'qa' },
        object: { id: 'o-1', code: query, name: '对象', lifecycleId: 'l-1', enableLifeCycle: true },
        lifecycle: { id: 'l-1', name: '生命周期' },
        statuses: [{
          id: 's-1', name: '状态', code: 's__c', sort: 0, isEnabled: true,
          userActions: [{
            id: 'a-1', code: 'a__c', name: '动作', description: '', kind: 'user',
            behaviorTypes: [9], behaviorLabels: ['修改状态'], workflowId: null, workflowName: null, configCount: 1,
          }],
          enterActions: [],
        }],
        workflows: [],
        totals: { statuses: 1, enabledStatuses: 1, userActions: 1, enterActions: 0 },
        enterCondition: { count: 0, configured: false, readable: true },
        warnings: [],
      },
    }),
  })
  const value = await tool.execute({ query: 'demo__c' }) as Record<string, any>
  assert.equal(value.ok, true)
  assert.equal(value.historyId, 'h-1')
  assert.equal(value.statuses[0].userActions[0].behaviorLabels[0], '修改状态')
})

test('对象分析工具：空 query 直接拒绝', async () => {
  const tool = buildAnalysisTool({ run: async () => { throw new Error('should not run') } })
  const value = await tool.execute({ query: ' ' }) as Record<string, any>
  assert.equal(value.ok, false)
})
