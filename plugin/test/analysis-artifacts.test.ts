import assert from 'node:assert/strict'
import test from 'node:test'

import {
  analysisArtifactName,
  buildAnalysisDrawio,
  buildAnalysisMarkdown,
  buildAnalysisSvg,
  type ArtifactAnalysis,
} from '../src/shared/analysis-artifacts.ts'
import type { AnalysisWorkflow } from '../src/analysis-workflow.ts'

const action = (overrides: Partial<ArtifactAnalysis['statuses'][number]['userActions'][number]>) => ({
  id: 'a1',
  code: 'submit__c',
  name: '提交',
  description: '提交',
  kind: 'user' as const,
  behaviorTypes: [],
  behaviorLabels: [],
  workflowId: null,
  workflowIds: [],
  workflowName: null,
  targetStatusId: null,
  configCount: 0,
  ...overrides,
})

function workflow(): AnalysisWorkflow {
  return {
    basicId: 'w1',
    configId: 'c1',
    name: '【CAPA】申请审核',
    code: 'capa_application_review__c',
    status: 20,
    versionNo: 3,
    isAllowTrigger: false,
    triggeredBy: [
      {
        statusId: 's1',
        statusName: 'CAPA申请',
        actionId: 'a1',
        actionName: '提交',
        actionKind: 'user',
      },
    ],
    statusBindings: [],
    steps: [
      {
        id: 'st1',
        code: 'start',
        name: '开始',
        stepType: 10,
        sort: 1,
        isEnable: true,
        nextStepId: 'st2',
        nextStepName: '修改状态：申请审核',
        task: null,
        decision: null,
        actions: [],
        detailReadable: true,
      },
      {
        id: 'st2',
        code: 'action1',
        name: '修改状态：申请审核',
        stepType: 90,
        sort: 2,
        isEnable: true,
        nextStepId: 'st3',
        nextStepName: '任务：CAPA申请审核',
        task: null,
        decision: null,
        actions: [
          {
            behaviorType: 9,
            behaviorLabel: '修改相关对象生命周期状态',
            targetStatusId: 's2',
            targetStatusName: 'CAPA申请审核',
            relatedObjectId: null,
            relatedStatusId: null,
            workflowId: null,
            description: '修改状态：申请审核',
          },
          {
            behaviorType: 46,
            behaviorLabel: '修改相关对象生命周期状态',
            targetStatusId: null,
            targetStatusName: null,
            relatedObjectId: 'obj-dev',
            relatedStatusId: 'dev-status',
            workflowId: null,
            description: '同步偏差状态',
          },
        ],
        detailReadable: true,
      },
      {
        id: 'st3',
        code: 'task1',
        name: '任务：CAPA申请审核',
        stepType: 30,
        sort: 3,
        isEnable: true,
        nextStepId: 'st4',
        nextStepName: '判断：审核人',
        task: {
          isNotifyTaskType: false,
          isRequireApproval: true,
          notifyType: -10,
          approvals: ['同意', '拒绝'],
          userControlIds: [],
        },
        decision: null,
        actions: [],
        detailReadable: true,
      },
      {
        id: 'st4',
        code: 'decision1',
        name: '判断：审核人',
        stepType: 70,
        sort: 4,
        isEnable: true,
        nextStepId: null,
        nextStepName: null,
        task: null,
        decision: {
          rules: [{ name: '同意', nextStepId: 'st5', nextStepName: '修改状态：通过', condition: '规则1' }],
          elseNextStepId: 'st6',
          elseNextStepName: '结束',
        },
        actions: [],
        detailReadable: true,
      },
      {
        id: 'st5',
        code: 'action2',
        name: '修改状态：通过',
        stepType: 90,
        sort: 5,
        isEnable: true,
        nextStepId: 'st6',
        nextStepName: '结束',
        task: null,
        decision: null,
        actions: [
          {
            behaviorType: 9,
            behaviorLabel: '修改相关对象生命周期状态',
            targetStatusId: 's3',
            targetStatusName: '事件调查',
            relatedObjectId: null,
            relatedStatusId: null,
            workflowId: null,
            description: '修改状态：通过',
          },
        ],
        detailReadable: true,
      },
      {
        id: 'st6',
        code: 'end',
        name: '结束',
        stepType: 20,
        sort: 6,
        isEnable: true,
        nextStepId: null,
        nextStepName: null,
        task: null,
        decision: null,
        actions: [],
        detailReadable: true,
      },
    ],
    edges: [
      { from: 'st1', to: 'st2', label: '下一步', kind: 'next' },
      { from: 'st2', to: 'st3', label: '下一步', kind: 'next' },
      { from: 'st3', to: 'st4', label: '下一步', kind: 'next' },
      { from: 'st4', to: 'st5', label: '同意', kind: 'decision' },
      { from: 'st4', to: 'st6', label: '否则', kind: 'else' },
      { from: 'st5', to: 'st6', label: '下一步', kind: 'next' },
    ],
    warnings: [],
  }
}

function fixture(): ArtifactAnalysis {
  return {
    source: { baseUrl: 'https://tonbridge-config.aksoegmp.com', account: 'lyl' },
    object: {
      id: '3a19df9c-485b-088a-ba92-c707490b4e43',
      code: 'capa_plan__c',
      name: 'CAPA & 计划',
      lifecycleId: '3a19df9c-485b-dd78-59d6-bd7ec03bb9fe',
      enableLifeCycle: true,
    },
    lifecycle: { id: '3a19df9c-485b-dd78-59d6-bd7ec03bb9fe', name: 'CAPA生命周期' },
    statuses: [
      {
        id: 's1',
        name: 'CAPA申请',
        code: 'init_status__c',
        sort: 0,
        isEnabled: true,
        userActions: [action({})],
        enterActions: [],
      },
      {
        id: 's2',
        name: 'CAPA申请审核',
        code: 'pending_initial_approval__c',
        sort: 1,
        isEnabled: true,
        userActions: [],
        enterActions: [],
      },
      {
        id: 's3',
        name: '事件调查',
        code: 'investigation__c',
        sort: 2,
        isEnabled: true,
        userActions: [],
        enterActions: [],
      },
    ],
    workflows: [workflow()],
    totals: { statuses: 3, enabledStatuses: 3, userActions: 1, enterActions: 0 },
    enterCondition: { count: 0, configured: false, readable: true },
    warnings: [],
  }
}

const fixedDate = new Date('2026-11-12T12:00:00.000Z')

test('Markdown：包含环境、对象、状态、动作和工作流解析', () => {
  const markdown = buildAnalysisMarkdown(fixture(), { generatedAt: fixedDate })
  assert.match(markdown, /^# CAPA & 计划（capa_plan__c）对象分析/m)
  assert.match(markdown, /https:\/\/tonbridge-config\.aksoegmp\.com/)
  assert.match(markdown, /账号：`lyl`/)
  assert.match(markdown, /### 1\. CAPA申请/)
  assert.match(markdown, /提交/)
  assert.match(markdown, /## 工作流解析/)
  assert.match(markdown, /【CAPA】申请审核/)
  assert.match(markdown, /CAPA申请 \/ 提交/)
  assert.match(markdown, /本对象 → CAPA申请审核/)
  assert.match(markdown, /关联对象/)
  assert.match(markdown, /流程阅读/)
})

test('Markdown：输出无人动作、空状态、workflowId 缺失的观察', () => {
  const markdown = buildAnalysisMarkdown(fixture(), { generatedAt: fixedDate })
  assert.match(markdown, /2 个状态没有用户动作/)
  assert.match(markdown, /2 个状态既没有用户动作也没有进入动作/)
  assert.match(markdown, /进入条件为 0 条且可读/)
})

test('drawio：是 mxfile XML，含生命周期页和工作流页，且转义 XML', () => {
  const xml = buildAnalysisDrawio(fixture(), { generatedAt: fixedDate })
  assert.match(xml, /^<mxfile /)
  assert.match(xml, /modified="2026-11-12T12:00:00.000Z"/)
  assert.match(xml, /生成：2026-11-12T12:00:00.000Z/)
  assert.match(xml, /<diagram id="lifecycle"/)
  assert.match(xml, /CAPA &amp; 计划（capa_plan__c）生命周期总览/)
  assert.match(xml, /<diagram id="wf-1"/)
  assert.match(xml, /【CAPA】申请审核/)
  assert.match(xml, /CAPA申请审核/)
  assert.match(xml, /关联对象/)
  assert.doesNotMatch(xml, /<svg/)
  assert.doesNotMatch(xml, /CAPA & 计划/)
})

test('SVG：总览包含全部状态，未知触发源放右侧而不是省略', () => {
  const unknown = fixture()
  unknown.workflows = [{ ...workflow(), triggeredBy: [], statusBindings: [] }]
  const svg = buildAnalysisSvg(unknown, { generatedAt: fixedDate })
  assert.match(svg, /^<svg /)
  assert.match(svg, /CAPA申请/)
  assert.match(svg, /CAPA申请审核/)
  assert.match(svg, /事件调查/)
  assert.match(svg, /待确认触发源/)
  assert.match(svg, /stroke-dasharray="6 4"/)
  assert.match(svg, /CAPA &amp; 计划/)
  assert.doesNotMatch(svg, /CAPA & 计划/)
})

test('文件名：带 code 和日期，去掉不安全字符', () => {
  const name = analysisArtifactName(fixture(), 'md', fixedDate)
  assert.equal(name, 'capa_plan__c-analysis-2026-11-12.md')
  const drawio = analysisArtifactName({ object: { ...fixture().object, code: 'a/b c' } }, 'drawio', fixedDate)
  assert.equal(drawio, 'a-b-c-analysis-2026-11-12.drawio')
  const png = analysisArtifactName(fixture(), 'png', fixedDate)
  assert.equal(png, 'capa_plan__c-analysis-2026-11-12.png')
})
