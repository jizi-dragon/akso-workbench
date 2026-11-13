/**
 * 对象分析产物：Markdown 文档 + drawio 生命周期/工作流图。
 *
 * 纯函数、不碰 DOM / 文件系统：host 能用来生成，client 也能直接 Blob 下载。
 * 数据只来自 `AnalysisResult`，不额外请求平台。
 *
 * ★ drawio 里既有「生命周期状态链」，也有每条工作流的步骤页。
 *   状态直改边只来自 behaviorType=9；behaviorType=46 是改关联对象，不能画成本对象边。
 */
import type { AnalysisWorkflow, AnalysisWorkflowStep } from '../analysis-workflow.ts'
import { STICKY_STYLES } from './sticky-notes.ts'
import { layoutLayered } from './lifecycle-layout.ts'
import { routeEdges } from './orthogonal-route.ts'
import { workflowStepTypeLabel } from './workflow-labels.ts'

export interface ArtifactAction {
  id: string
  code: string
  name: string
  description: string
  kind: 'user' | 'enter'
  behaviorTypes: number[]
  behaviorLabels: string[]
  workflowId: string | null
  workflowIds?: string[]
  workflowName: string | null
  /** behaviorType=9 且改当前对象时的目标状态。 */
  targetStatusId?: string | null
  configCount: number
}

export interface ArtifactStatus {
  id: string
  name: string
  code: string
  sort: number
  isEnabled: boolean
  userActions: ArtifactAction[]
  enterActions: ArtifactAction[]
}

export interface ArtifactAnalysis {
  source: { baseUrl: string; account: string }
  object: {
    id: string
    code: string
    name: string
    lifecycleId: string
    enableLifeCycle: boolean
  }
  lifecycle: { id: string; name: string } | null
  statuses: ArtifactStatus[]
  workflows?: AnalysisWorkflow[]
  totals: { statuses: number; enabledStatuses: number; userActions: number; enterActions: number }
  enterCondition: { count: number; configured: boolean; readable: boolean }
  warnings: string[]
}

export interface ArtifactOptions {
  /** 便于测试固定时间；不传就取当前时间。 */
  generatedAt?: Date
}

function mdCell(value: string): string {
  return value.replace(/\|/g, '\\|').replace(/\r?\n/g, '<br>')
}

function mdLine(value: string): string {
  return value.replace(/\r?\n/g, ' ')
}

function workflowIds(action: ArtifactAction): string[] {
  if (action.workflowIds !== undefined && action.workflowIds.length > 0) return action.workflowIds
  return action.workflowId === null ? [] : [action.workflowId]
}

function actionEffect(action: ArtifactAction): string {
  const bits: string[] = []
  if (action.configCount === 0) bits.push('未配置具体动作')
  if (action.behaviorLabels.length > 0) bits.push(...action.behaviorLabels)
  else if (action.behaviorTypes.length > 0) bits.push(...action.behaviorTypes.map((item) => `行为 ${item}`))
  if (action.targetStatusId !== undefined && action.targetStatusId !== null) {
    bits.push(`目标状态 ${action.targetStatusId}`)
  }
  if (action.workflowName !== null) bits.push(`工作流「${action.workflowName}」`)
  else if (action.workflowId !== null) bits.push(`工作流 ${action.workflowId}`)
  return bits.length > 0 ? bits.join('；') : '已配置'
}

function allActions(status: ArtifactStatus): ArtifactAction[] {
  return [...status.userActions, ...status.enterActions]
}

function observations(result: ArtifactAnalysis): string[] {
  const notes: string[] = []
  const workflows = result.workflows ?? []
  const noUser = result.statuses.filter((status) => status.userActions.length === 0)
  const empty = result.statuses.filter(
    (status) => status.userActions.length === 0 && status.enterActions.length === 0,
  )
  const noWorkflow = result.statuses.flatMap((status) =>
    allActions(status).filter((action) => action.behaviorTypes.includes(7) && workflowIds(action).length === 0),
  )
  const unknownLabels = [...new Set(
    result.statuses.flatMap((status) =>
      allActions(status).flatMap((action) =>
        action.behaviorLabels.filter((label) => /^行为 \d+$/.test(label) || label === '母菜单'),
      ),
    ),
  )]
  const crossObject = result.statuses.flatMap((status) =>
    allActions(status).filter((action) => action.behaviorTypes.includes(46)),
  )
  const untriggered = workflows.filter((workflow) => workflow.triggeredBy.length === 0 && workflow.statusBindings.length === 0)
  const stateChanging = workflows.filter((workflow) =>
    workflow.steps.some((step) => step.actions.some((action) => action.targetStatusId !== null)),
  )

  if (noUser.length > 0) {
    notes.push(
      `${noUser.length} 个状态没有用户动作：${noUser.map((status) => `${status.name}（${status.code}）`).join('、')}。` +
        '这些状态需要工作流状态步骤或自动动作接管；否则会缺少人工出口。',
    )
  }
  if (empty.length > 0) {
    notes.push(
      `${empty.length} 个状态既没有用户动作也没有进入动作：${empty.map((status) => status.name).join('、')}。` +
        '它们完全依赖外部工作流/事件驱动。',
    )
  }
  if (noWorkflow.length > 0) {
    notes.push(
      `${noWorkflow.length} 个动作带「发起工作流」行为，但没有读到 workflowId：` +
        `${noWorkflow.map((action) => action.description || action.name).join('、')}。` +
        '可能只是读取层没解析到 behaviorValue，也可能是漏配，需用工作流页面核对。',
    )
  }
  if (unknownLabels.length > 0) {
    notes.push(`行为类型映射不全：${unknownLabels.join('、')}。页面/文档暂时只能显示数字或泛化标签，建议补映射表。`)
  }
  if (crossObject.length > 0) {
    notes.push(`有 ${crossObject.length} 个进入动作会修改相关对象的生命周期状态（behaviorType 46），改本对象前要一起检查关联对象。`)
  }
  if (stateChanging.length > 0) {
    notes.push(
      `${stateChanging.length} 条工作流里存在 behaviorType=9 的本对象状态变更：${stateChanging.map((workflow) => workflow.name).join('、')}。` +
        '文档的工作流章节逐条列出了动作节点；drawio 每个工作流单独一页。',
    )
  } else if (workflows.length > 0) {
    notes.push('已展开的工作流里没有 behaviorType=9 的本对象状态变更；这些流程只做提醒/审批/关联对象处理。')
  }
  if (untriggered.length > 0) {
    notes.push(
      `${untriggered.length} 条工作流既没有状态动作直接引用，也没有 lifecycleStatusIds 绑定：` +
        `${untriggered.map((workflow) => workflow.name).join('、')}。触发点需要平台侧确认，文档按“对象工作流”保留。`,
    )
  }
  if (result.enterCondition.readable && !result.enterCondition.configured) {
    notes.push('生命周期进入条件为 0 条且可读：属于“未配置”，不是读取失败。')
  }
  if (!result.enterCondition.readable) {
    notes.push('生命周期进入条件读取失败，不能判断是否已配置。')
  }
  return notes
}

function triggerText(workflow: AnalysisWorkflow): string {
  if (workflow.triggeredBy.length > 0) {
    return workflow.triggeredBy
      .map((trigger) => `${trigger.statusName} / ${trigger.actionName}（${trigger.actionKind === 'user' ? '用户动作' : '进入动作'}）`)
      .join('、')
  }
  if (workflow.statusBindings.length > 0) {
    return `状态绑定：${workflow.statusBindings.map((binding) => binding.statusName).join('、')}（未反查到直接动作）`
  }
  return '未反查到直接触发点（按对象工作流保留）'
}

function stepConfigText(step: AnalysisWorkflowStep): string {
  const bits: string[] = []
  if (step.task !== null) {
    bits.push(step.task.isNotifyTaskType ? '通知任务' : '任务')
    if (step.task.isRequireApproval) bits.push('需要审批')
    if (step.task.approvals.length > 0) bits.push(`审批按钮：${step.task.approvals.join('/')}`)
  }
  if (step.decision !== null) {
    bits.push(`规则 ${step.decision.rules.length} 条`)
    if (step.decision.elseNextStepId !== null) bits.push('有否则分支')
  }
  for (const action of step.actions) {
    const target =
      action.targetStatusId !== null
        ? `本对象 → ${action.targetStatusName ?? action.targetStatusId}`
        : action.relatedObjectId !== null
          ? `关联对象 ${action.relatedObjectId} → ${action.relatedStatusId ?? '未读状态'}`
          : action.workflowId !== null
            ? `再启工作流 ${action.workflowId}`
            : ''
    bits.push([action.behaviorLabel, target].filter((value) => value !== '').join('：'))
  }
  return bits.length > 0 ? bits.join('；') : '—'
}

function workflowSection(result: ArtifactAnalysis): string[] {
  const workflows = result.workflows ?? []
  const lines: string[] = ['## 工作流解析', '']
  if (workflows.length === 0) {
    lines.push('没有从状态动作或对象上读到工作流。', '')
    return lines
  }
  lines.push('| # | 工作流 | code | status | 版本 | 允许自动启动 | 直接触发 | 步骤 |')
  lines.push('| --- | --- | --- | --- | --- | --- | --- | --- |')
  workflows.forEach((workflow, index) => {
    lines.push(
      `| ${index + 1} | ${mdCell(workflow.name)} | \`${mdCell(workflow.code)}\` | ${workflow.status} | ${workflow.versionNo} | ${workflow.isAllowTrigger ? '是' : '否'} | ${mdCell(triggerText(workflow))} | ${workflow.steps.length} |`,
    )
  })
  lines.push('')

  workflows.forEach((workflow, index) => {
    lines.push(`### ${index + 1}. ${mdLine(workflow.name)}`, '')
    lines.push(`- workflowBasicId：\`${workflow.basicId}\``)
    lines.push(`- workflowConfigId：\`${workflow.configId}\``)
    lines.push(`- code：\`${workflow.code}\`；status：${workflow.status}；版本：${workflow.versionNo}；isAllowTrigger：${workflow.isAllowTrigger ? '是' : '否'}`)
    lines.push(`- 触发：${mdLine(triggerText(workflow))}`)
    if (workflow.statusBindings.length > 0) {
      lines.push(`- 生命周期状态绑定：${workflow.statusBindings.map((binding) => `${binding.statusName}（\`${binding.statusId}\`）`).join('、')}`)
    }
    lines.push('')
    lines.push('| 序号 | 类型 | 名称 | code | 配置摘要 | 下一步 |')
    lines.push('| --- | --- | --- | --- | --- | --- |')
    workflow.steps.forEach((step, stepIndex) => {
      const next = step.nextStepId === null ? '—' : step.nextStepName ?? step.nextStepId
      lines.push(
        `| ${stepIndex + 1} | ${workflowStepTypeLabel(step.stepType)} | ${mdCell(step.name)} | \`${mdCell(step.code)}\` | ${mdCell(stepConfigText(step))} | ${mdCell(next)} |`,
      )
    })
    lines.push('')
    lines.push('**流程阅读**', '')
    if (workflow.steps.length === 0) {
      lines.push('- 没有读到步骤。', '')
    } else {
      workflow.steps.forEach((step, stepIndex) => {
        const next = step.nextStepId === null ? '结束' : step.nextStepName ?? step.nextStepId
        if (step.decision !== null) {
          const branches = step.decision.rules.map((rule) => `${rule.name} → ${rule.nextStepName || rule.nextStepId || '未配置'}`)
          if (step.decision.elseNextStepId !== null) branches.push(`否则 → ${step.decision.elseNextStepName ?? step.decision.elseNextStepId}`)
          lines.push(`- ${stepIndex + 1}. [判断] ${mdLine(step.name)}：${branches.length > 0 ? branches.join('；') : '没有规则'}。`)
        } else if (step.task !== null) {
          lines.push(`- ${stepIndex + 1}. [任务] ${mdLine(step.name)}：${stepConfigText(step)}；下一步 ${next}。`)
        } else if (step.actions.length > 0) {
          lines.push(`- ${stepIndex + 1}. [动作] ${mdLine(step.name)}：${stepConfigText(step)}；下一步 ${next}。`)
        } else {
          lines.push(`- ${stepIndex + 1}. [${workflowStepTypeLabel(step.stepType)}] ${mdLine(step.name)}；下一步 ${next}。`)
        }
      })
      lines.push('')
    }

    const currentStatusChanges = workflow.steps.flatMap((step) =>
      step.actions
        .filter((action) => action.targetStatusId !== null)
        .map((action) => `「${step.name}」→ 「${action.targetStatusName ?? action.targetStatusId}」`),
    )
    const externalChanges = workflow.steps.flatMap((step) =>
      step.actions
        .filter((action) => action.relatedObjectId !== null)
        .map((action) => `「${step.name}」改关联对象 \`${action.relatedObjectId}\` 的状态 \`${action.relatedStatusId ?? '未读'}\``),
    )
    lines.push('**对生命周期的影响**', '')
    if (currentStatusChanges.length === 0) {
      lines.push('- 没有直接改本对象状态的 behaviorType=9 动作。')
    } else {
      for (const change of currentStatusChanges) lines.push(`- ${change}`)
    }
    for (const change of externalChanges) lines.push(`- ${change}（关联对象，不是本对象生命周期边）`)
    if (currentStatusChanges.length === 0 && externalChanges.length === 0) {
      lines.push('- 该流程只做提醒/审批/读取，不改任何生命周期状态。')
    }
    if (workflow.warnings.length > 0) {
      lines.push('', '**异常与待确认**', '')
      for (const warning of workflow.warnings) lines.push(`- ${mdLine(warning)}`)
    }
    lines.push('')
  })
  return lines
}

export function buildAnalysisMarkdown(result: ArtifactAnalysis, options: ArtifactOptions = {}): string {
  const generatedAt = options.generatedAt ?? new Date()
  const lines: string[] = []
  const statuses = result.statuses
  const lifecycleName = result.lifecycle?.name || '—'

  lines.push(`# ${mdLine(result.object.name)}（${result.object.code}）对象分析`, '')
  lines.push(`> 数据环境：\`${result.source.baseUrl}\``)
  lines.push(`> 数据账号：\`${result.source.account}\``)
  lines.push(`> 生成时间：${generatedAt.toISOString()}`)
  lines.push('> 数据来源：eGMP 配置接口，只读分析，不修改平台。', '')

  lines.push('## 对象概览', '', '| 项 | 值 |', '| --- | --- |')
  lines.push(`| 对象名称 | ${mdCell(result.object.name)} |`)
  lines.push(`| 对象 code | \`${mdCell(result.object.code)}\` |`)
  lines.push(`| 对象 id | \`${result.object.id}\` |`)
  lines.push(`| 生命周期 | ${mdCell(lifecycleName)} |`)
  lines.push(`| lifecycleId | \`${result.object.lifecycleId}\` |`)
  lines.push(`| 启用生命周期 | ${result.object.enableLifeCycle ? '是' : '否'} |`)
  lines.push(`| 状态 | ${result.totals.enabledStatuses} 启用 / ${result.totals.statuses} 总数 |`)
  lines.push(`| 用户动作 | ${result.totals.userActions} |`)
  lines.push(`| 进入动作 | ${result.totals.enterActions} |`)
  lines.push(`| 工作流 | ${(result.workflows ?? []).length} |`)
  lines.push(
    `| 进入条件 | ${
      result.enterCondition.readable
        ? result.enterCondition.configured
          ? `已配置（${result.enterCondition.count} 条）`
          : '未配置（0 条）'
        : '不可读'
    } |`,
  )
  lines.push('', '## 状态与动作', '')

  if (statuses.length === 0) {
    lines.push('该对象没有可分析的生命周期状态。', '')
  }

  statuses.forEach((status, index) => {
    lines.push(`### ${index + 1}. ${mdLine(status.name)}（\`${status.code}\`）`, '')
    lines.push(`- 状态 id：\`${status.id}\``)
    lines.push(`- sort：${status.sort}；启用：${status.isEnabled ? '是' : '否'}`)
    lines.push(`- 用户动作：${status.userActions.length}；进入动作：${status.enterActions.length}`, '')

    for (const [label, actions] of [
      ['用户动作', status.userActions],
      ['进入动作', status.enterActions],
    ] as const) {
      lines.push(`**${label}**`, '')
      if (actions.length === 0) {
        lines.push('- 无', '')
        continue
      }
      for (const action of actions) {
        const code = action.code === '' ? '' : `（\`${action.code}\`）`
        lines.push(`- **${mdLine(action.name)}**${code}：${mdLine(actionEffect(action))}`)
        if (action.description !== '') lines.push(`  - ${mdLine(action.description)}`)
        if (action.workflowId !== null) lines.push(`  - workflowId：\`${action.workflowId}\``)
        for (const id of workflowIds(action)) {
          if (id !== action.workflowId) lines.push(`  - 关联 workflowId：\`${id}\``)
        }
      }
      lines.push('')
    }
  })

  lines.push(...workflowSection(result))

  lines.push('## 自动观察与风险', '')
  const notes = observations(result)
  if (notes.length === 0) lines.push('- 未发现需要特别提示的结构。')
  else for (const note of notes) lines.push(`- ${mdLine(note)}`)
  lines.push('')

  if (result.warnings.length > 0) {
    lines.push('## 读取告警', '')
    for (const warning of result.warnings) lines.push(`- ${mdLine(warning)}`)
    lines.push('')
  }

  lines.push('## 生成说明', '')
  lines.push('- 本文档由 akso-workbench 对象分析页生成，数据来自 eGMP 配置接口。')
  lines.push('- 状态顺序按 `status.sort` 排列；生命周期总览页的状态链也按 sort 排列，不是平台声明的迁移顺序。')
  lines.push('- 工作流章节来自 `GetWorkflowSteps` + `GetWorkflowStepWithDetails` 的真实步骤；behaviorType=46 只画成关联对象影响，不画成本对象生命周期边。')
  lines.push('- drawio 文件包含“生命周期总览”页和每条工作流一页，建议用 diagrams.net / drawio 打开。', '')
  return lines.join('\n')
}

function escapeXml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;')
}

function label(value: string): string {
  return escapeXml(value).replace(/\r?\n/g, '&#xa;')
}

function cell(
  id: string,
  value: string,
  style: string,
  x: number,
  y: number,
  width: number,
  height: number,
): string {
  return `<mxCell id="${id}" value="${label(value)}" style="${style}" vertex="1" parent="1"><mxGeometry x="${x}" y="${y}" width="${width}" height="${height}" as="geometry"/></mxCell>`
}

function edge(id: string, source: string, target: string, value: string, style: string): string {
  return `<mxCell id="${id}" value="${label(value)}" style="${style}" edge="1" parent="1" source="${source}" target="${target}"><mxGeometry relative="1" as="geometry"/></mxCell>`
}

function edgeWithPoints(
  id: string,
  source: string,
  target: string,
  value: string,
  style: string,
  points: Array<{ x: number; y: number }>,
): string {
  const waypoints =
    points.length === 0
      ? ''
      : `<Array as="points">${points.map((point) => `<mxPoint x="${Math.round(point.x)}" y="${Math.round(point.y)}"/>`).join('')}</Array>`
  return `<mxCell id="${id}" value="${label(value)}" style="${style}" edge="1" parent="1" source="${source}" target="${target}"><mxGeometry relative="1" as="geometry">${waypoints}</mxGeometry></mxCell>`
}

function workflowNodeStyle(stepType: number): string {
  if (stepType === 10 || stepType === 20) {
    return `${STICKY_STYLES.startEnd}align=center;fontColor=#ffffff;fontStyle=1;`
  }
  if (stepType === 70) {
    return `${STICKY_STYLES.decision}align=center;`
  }
  return `${STICKY_STYLES.userTask}align=left;spacingLeft=8;`
}

function workflowStepLabel(step: AnalysisWorkflowStep, index: number): string {
  const head = `${index + 1}. [${workflowStepTypeLabel(step.stepType)}] ${step.name}`
  const code = step.code === '' ? '' : `\n${step.code}`
  const config = stepConfigText(step)
  return `${head}${code}${config === '—' ? '' : `\n${config}`}`
}

function lifecyclePage(result: ArtifactAnalysis, generatedAt: Date): string {
  const statuses = [...result.statuses].sort((left, right) => left.sort - right.sort || left.name.localeCompare(right.name))
  const statusById = new Map(statuses.map((status) => [status.id, status]))

  interface FlowNode {
    id: string
    text: string
    style: string
    width: number
    height: number
    kind: 'status' | 'action' | 'task' | 'decision'
  }
  interface FlowEdge {
    id: string
    from: string
    to: string
    label: string
  }
  const nodes = new Map<string, FlowNode>()
  const edges: FlowEdge[] = []
  const addNode = (node: FlowNode): void => {
    if (!nodes.has(node.id)) nodes.set(node.id, node)
  }
  const addEdge = (id: string, from: string, to: string, label: string): void => {
    if (from === to || !nodes.has(from) || !nodes.has(to)) return
    if (edges.some((edge) => edge.from === from && edge.to === to)) return
    edges.push({ id, from, to, label })
  }
  const actionId = (statusId: string, id: string): string => `ac_${statusId}_${id}`
  const stepId = (workflow: AnalysisWorkflow, id: string): string => `wf_${workflow.basicId}_${id}`

  statuses.forEach((status, index) => {
    addNode({
      id: `st_${status.id}`,
      text: `${index + 1}. ${status.name}\n${status.code}\n用户 ${status.userActions.length} / 进入 ${status.enterActions.length}`,
      style: `${STICKY_STYLES.lifecycleStatus}align=left;spacingLeft=10;`,
      width: 300,
      height: 96,
      kind: 'status',
    })
  })

  for (const status of statuses) {
    for (const action of [...status.userActions, ...status.enterActions]) {
      const id = actionId(status.id, action.id)
      const behavior = action.behaviorLabels.length > 0 ? ` · ${action.behaviorLabels.join('、')}` : ''
      addNode({
        id,
        text: `${action.name}\n${action.kind === 'user' ? '用户动作' : '进入动作'}${behavior}`,
        style:
          action.kind === 'user'
            ? `${STICKY_STYLES.userAction}align=center;fontSize=10;`
            : `${STICKY_STYLES.userTask}align=center;fontSize=10;`,
        width: 240,
        height: 52,
        kind: 'action',
      })
      addEdge(`se_${status.id}_${action.id}`, `st_${status.id}`, id, action.kind === 'user' ? '点击' : '进入')
      if (action.targetStatusId !== undefined && action.targetStatusId !== null && statusById.has(action.targetStatusId)) {
        addEdge(`sa_${status.id}_${action.id}`, id, `st_${action.targetStatusId}`, action.name)
      }
    }
  }

  const workflows = result.workflows ?? []
  for (const workflow of workflows) {
    const steps = [...workflow.steps].sort((left, right) => left.sort - right.sort)
    const actionable = steps.filter((step) => step.stepType === 30 || step.stepType === 70)
    for (const step of actionable) {
      addNode({
        id: stepId(workflow, step.id),
        text: step.stepType === 70 ? `${step.name}\n判断` : `${step.name}\n工作流任务`,
        style:
          step.stepType === 70
            ? `${STICKY_STYLES.decision}align=center;fontSize=10;`
            : `${STICKY_STYLES.userTask}align=center;fontSize=10;`,
        width: step.stepType === 70 ? 200 : 280,
        height: step.stepType === 70 ? 72 : 64,
        kind: step.stepType === 70 ? 'decision' : 'task',
      })
    }
    const triggerNodes = workflow.triggeredBy
      .map((trigger) => actionId(trigger.statusId, trigger.actionId))
      .filter((id) => nodes.has(id))
    const first = actionable[0]
    if (first !== undefined) {
      for (const status of statuses) {
        for (const action of [...status.userActions, ...status.enterActions]) {
          if (!action.workflowIds?.includes(workflow.basicId) && action.workflowId !== workflow.basicId) continue
          addEdge(`wfstart_${workflow.basicId}_${status.id}_${action.id}`, actionId(status.id, action.id), stepId(workflow, first.id), '启动')
        }
      }
    }
    const byId = new Map(steps.map((step) => [step.id, step]))
    for (const edge of workflow.edges) {
      const fromStep = byId.get(edge.from)
      const toStep = byId.get(edge.to)
      if (fromStep === undefined || toStep === undefined) continue
      if (fromStep.stepType === 90) continue
      if (fromStep.stepType === 10 && toStep.stepType === 90) {
        toStep.actions.forEach((action, actionIndex) => {
          if (action.targetStatusId === null || !statusById.has(action.targetStatusId)) return
          triggerNodes.forEach((triggerNode, triggerIndex) => {
            addEdge(
              `wfstart_target_${workflow.basicId}_${edge.to}_${actionIndex}_${triggerIndex}`,
              triggerNode,
              `st_${action.targetStatusId}`,
              edge.label || action.behaviorLabel || action.targetStatusName || '状态变更',
            )
          })
        })
        continue
      }
      if (fromStep.stepType === 10) continue
      const fromNode = stepId(workflow, fromStep.id)
      if (!nodes.has(fromNode)) continue
      if (toStep.stepType === 90) {
        toStep.actions.forEach((action, actionIndex) => {
          if (action.targetStatusId === null || !statusById.has(action.targetStatusId)) return
          addEdge(
            `wfedge_${workflow.basicId}_${edge.to}_${actionIndex}`,
            fromNode,
            `st_${action.targetStatusId}`,
            edge.label || action.behaviorLabel || action.targetStatusName || '状态变更',
          )
        })
        continue
      }
      if (toStep.stepType === 20) continue
      const toNode = stepId(workflow, toStep.id)
      if (nodes.has(toNode)) addEdge(`wfedge_${workflow.basicId}_${fromStep.id}_${toStep.id}`, fromNode, toNode, edge.label)
    }
  }

  const orderedNodes = [...nodes.values()]
  const layout = layoutLayered(
    orderedNodes.map((node) => ({ id: node.id, width: node.width, height: node.height })),
    edges.map((edge) => ({ id: edge.id, from: edge.from, to: edge.to })),
  )
  let minX = Number.POSITIVE_INFINITY
  let minY = Number.POSITIVE_INFINITY
  for (const node of layout.nodes.values()) {
    minX = Math.min(minX, node.x)
    minY = Math.min(minY, node.y)
  }
  const offsetX = Number.isFinite(minX) ? 120 - minX : 120
  const offsetY = Number.isFinite(minY) ? 120 - minY : 120
  const placed = new Map<string, { x: number; y: number; width: number; height: number }>()
  for (const [id, node] of layout.nodes) {
    placed.set(id, { x: node.x + offsetX, y: node.y + offsetY, width: node.width, height: node.height })
  }

  const degree = new Map<string, number>()
  for (const edge of edges) {
    degree.set(edge.from, (degree.get(edge.from) ?? 0) + 1)
    degree.set(edge.to, (degree.get(edge.to) ?? 0) + 1)
  }
  let isolatedY = 140
  for (const [id, node] of placed) {
    if ((degree.get(id) ?? 0) > 0) continue
    node.x = Math.max(820, (layout.width ?? 0) + 120)
    node.y = isolatedY
    isolatedY += node.height + 40
  }

  const routes = routeEdges(
    [...placed].map(([id, box]) => ({ id, x: box.x, y: box.y, width: box.width, height: box.height })),
    edges.map((item) => ({ id: item.id, from: item.from, to: item.to })),
  )

  const cells: string[] = []
  for (const node of orderedNodes) {
    const box = placed.get(node.id)
    if (box === undefined) continue
    cells.push(cell(node.id, node.text, node.style, box.x, box.y, box.width, box.height))
  }
  edges.forEach((item, index) => {
    const route = routes.get(item.id)
    if (route === undefined) return
    cells.push(
      edgeWithPoints(
        `flow_edge_${index}`,
        item.from,
        item.to,
        item.label,
        `${STICKY_STYLES.edge}fontSize=9;labelBackgroundColor=#ffffff;exitX=${route.exitX};exitY=${route.exitY};entryX=${route.entryX};entryY=${route.entryY};`,
        route.points,
      ),
    )
  })

  let maxX = 0
  let maxY = 0
  for (const node of placed.values()) {
    maxX = Math.max(maxX, node.x + node.width)
    maxY = Math.max(maxY, node.y + node.height)
  }
  const pageWidth = Math.max(1100, maxX + 80)
  const height = Math.max(760, maxY + 150)
  const notes = [
    '图形固定遵循便笺本：状态 #bdd7ee 矩形，用户动作 #ed7d31 胶囊，工作流任务 #ed7d31 矩形，判断 #f4b183 菱形，连线黑色箭头。',
    '状态卡上的“用户/进入”是动作数量；橙色节点是实际动作/任务，绿/蓝/橙边表示流转方向。',
    '工作流里 behaviorType=46 的动作改的是关联对象，不画在本页；完整步骤见各工作流分页。',
  ]
  const out: string[] = []
  out.push(`<diagram id="lifecycle" name="生命周期总览">`)
  out.push(`<mxGraphModel dx="1200" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="${pageWidth}" pageHeight="${height}">`)
  out.push('<root>')
  out.push('<mxCell id="0"/>')
  out.push('<mxCell id="1" parent="0"/>')
  out.push(cell('title', `${result.object.name}（${result.object.code}）生命周期总览`, 'text;html=1;align=left;fontSize=22;fontStyle=1;', 40, 20, 900, 32))
  out.push(cell('meta', `环境：${result.source.baseUrl}\n账号：${result.source.account}\n生成：${generatedAt.toISOString()}\n生命周期：${result.lifecycle?.name ?? '—'}`, 'text;html=1;align=left;fontSize=10;verticalAlign=top;', 40, 54, 900, 56))
  for (const cellXml of cells) out.push(cellXml)
  let noteY = height - 40 - notes.length * 20
  for (const note of notes) {
    out.push(cell(`note_${noteY}`, note, 'text;html=1;align=left;fontSize=10;fontColor=#666666;', 60, noteY, pageWidth - 120, 20))
    noteY += 20
  }
  out.push('</root>')
  out.push('</mxGraphModel>')
  out.push('</diagram>')
  return out.join('')
}

function workflowPage(workflow: AnalysisWorkflow, index: number): string {
  const steps = [...workflow.steps].sort((left, right) => left.sort - right.sort)
  const nodeW = 520
  const nodeH = 76
  const gap = 138
  const top = 144
  const cells: string[] = []
  const ids = new Map<string, string>()
  steps.forEach((step, stepIndex) => {
    const id = `wf${index}_step_${stepIndex}`
    ids.set(step.id, id)
    cells.push(cell(id, workflowStepLabel(step, stepIndex), workflowNodeStyle(step.stepType), 80, top + stepIndex * gap, nodeW, nodeH))
  })
  if (steps.length === 0) {
    cells.push(cell('empty', '没有读到步骤', 'text;html=1;align=center;', 80, 160, nodeW, 40))
  }
  workflow.edges.forEach((item, edgeIndex) => {
    const from = ids.get(item.from)
    const to = ids.get(item.to)
    if (from === undefined || to === undefined) return
    cells.push(
      edge(
        `wf${index}_edge_${edgeIndex}`,
        from,
        to,
        item.label,
        `${STICKY_STYLES.edge}fontSize=9;dashed=0;labelBackgroundColor=#ffffff;`,
      ),
    )
  })

  const height = Math.max(760, top + steps.length * gap + 180)
  const out: string[] = []
  out.push(`<diagram id="wf-${index}" name="${label(workflow.name).slice(0, 80)}">`)
  out.push(`<mxGraphModel dx="1200" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1100" pageHeight="${height}">`)
  out.push('<root>')
  out.push('<mxCell id="0"/>')
  out.push('<mxCell id="1" parent="0"/>')
  out.push(cell('title', workflow.name, 'text;html=1;align=left;fontSize=20;fontStyle=1;', 40, 20, 900, 30))
  out.push(
    cell(
      'meta',
      `code：${workflow.code}\nworkflowBasicId：${workflow.basicId}\nworkflowConfigId：${workflow.configId}\nstatus：${workflow.status}  版本：${workflow.versionNo}  isAllowTrigger：${workflow.isAllowTrigger ? '是' : '否'}\n触发：${triggerText(workflow)}`,
      'text;html=1;align=left;fontSize=10;verticalAlign=top;',
      40,
      54,
      940,
      72,
    ),
  )
  for (const cellXml of cells) out.push(cellXml)
  let noteY = top + steps.length * gap + 24
  const notes = [
    '图形固定遵循便笺本：橙色矩形 = 任务/动作，绿色圆形 = 开始/结束，橙色菱形 = 判断，黑色箭头 = 流转（docs/analysis/便笺本.xml）。',
    '动作节点里的“本对象 → 状态”才是本对象生命周期变更；“关联对象”不是本对象的状态边。',
  ]
  for (const note of notes) {
    out.push(cell(`wf${index}_note_${noteY}`, note, 'text;html=1;align=left;fontSize=10;fontColor=#666666;', 80, noteY, 860, 22))
    noteY += 22
  }
  if (workflow.warnings.length > 0) {
    out.push(cell(`wf${index}_warn`, `待确认：${workflow.warnings.join('；')}`, 'text;html=1;align=left;fontSize=10;fontColor=#b85450;', 80, noteY, 860, 44))
  }
  out.push('</root>')
  out.push('</mxGraphModel>')
  out.push('</diagram>')
  return out.join('')
}

export function buildAnalysisDrawio(result: ArtifactAnalysis, options: ArtifactOptions = {}): string {
  const generatedAt = options.generatedAt ?? new Date()
  const workflows = result.workflows ?? []
  const out: string[] = []
  out.push(`<mxfile host="akso-workbench" modified="${generatedAt.toISOString()}" agent="akso-workbench" version="24.0.0" type="device">`)
  out.push(lifecyclePage(result, generatedAt))
  workflows.forEach((workflow, index) => out.push(workflowPage(workflow, index + 1)))
  out.push('</mxfile>')
  return out.join('\n')
}

/**
 * 给 PNG 预览/客户端 canvas 用的 SVG 总览。
 *
 * 这不是要替代 drawio：drawio 承载完整工作流分页；这里只画“全部状态 + 已知边 +
 * 触发源未确认的侧栏”，让浏览器可以直接栅格化成 PNG。
 */
export function buildAnalysisSvg(result: ArtifactAnalysis, options: ArtifactOptions = {}): string {
  const generatedAt = options.generatedAt ?? new Date()
  const statuses = [...result.statuses].sort((left, right) => left.sort - right.sort || left.name.localeCompare(right.name))
  const statusById = new Map(statuses.map((status) => [status.id, status]))
  const known: Array<{ from: string; to: string; labels: Set<string> }> = []
  const addEdge = (from: string, to: string, text: string): void => {
    if (from === to || !statusById.has(from) || !statusById.has(to)) return
    let found = known.find((item) => item.from === from && item.to === to)
    if (found === undefined) {
      found = { from, to, labels: new Set<string>() }
      known.push(found)
    }
    found.labels.add(text)
  }
  for (const status of statuses) {
    for (const action of [...status.userActions, ...status.enterActions]) {
      if (action.targetStatusId !== null && action.targetStatusId !== undefined) {
        addEdge(status.id, action.targetStatusId, `${action.name}（${action.kind === 'user' ? '用户' : '进入'}动作）`)
      }
    }
  }
  const unknown: Array<{ id: string; name: string; targets: Map<string, Set<string>> }> = []
  for (const workflow of result.workflows ?? []) {
    const sources = new Set<string>()
    for (const trigger of workflow.triggeredBy) sources.add(trigger.statusId)
    for (const binding of workflow.statusBindings) sources.add(binding.statusId)
    const targets = new Map<string, Set<string>>()
    for (const step of workflow.steps) {
      for (const action of step.actions) {
        if (action.targetStatusId === null || !statusById.has(action.targetStatusId)) continue
        let labels = targets.get(action.targetStatusId)
        if (labels === undefined) {
          labels = new Set<string>()
          targets.set(action.targetStatusId, labels)
        }
        labels.add(step.name)
      }
    }
    if (targets.size === 0) continue
    if (sources.size === 0) {
      unknown.push({ id: workflow.basicId, name: workflow.name, targets })
      continue
    }
    for (const source of sources) {
      for (const [target, labels] of targets) addEdge(source, target, `${workflow.name} / ${[...labels].join('、')}`)
    }
  }

  const width = 1180
  const left = 40
  const top = 112
  const statusW = 420
  const statusH = 64
  const statusGap = 96
  const sideX = 700
  const sideTop = 132
  const sideW = 420
  const panelLines: string[] = []
  for (const workflow of unknown) {
    panelLines.push('', `• ${workflow.name}`)
    for (const target of workflow.targets.keys()) {
      panelLines.push(`   - ${statusById.get(target)?.name ?? target}`)
    }
  }
  const panelBodyH = panelLines.length * 16
  const panelH = unknown.length > 0 ? 46 + panelBodyH + 14 : 0
  const height = Math.max(780, top + statuses.length * statusGap + 120, sideTop + panelH + 80)
  const yOf = new Map<string, number>()
  const out: string[] = []
  out.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">`)
  out.push('<defs><marker id="arrow" markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto"><path d="M0,0 L10,4 L0,8 z" fill="#000000"/></marker></defs>')
  out.push(`<rect x="0" y="0" width="${width}" height="${height}" fill="#ffffff"/>`)
  out.push(`<text x="${left}" y="40" font-family="'Microsoft YaHei','PingFang SC',sans-serif" font-size="24" font-weight="700" fill="#1f2937">${escapeXml(result.object.name)}（${escapeXml(result.object.code)}）生命周期总览</text>`)
  out.push(`<text x="${left}" y="64" font-family="'Microsoft YaHei','PingFang SC',sans-serif" font-size="12" fill="#64748b">环境：${escapeXml(result.source.baseUrl)}    账号：${escapeXml(result.source.account)}    生成：${escapeXml(generatedAt.toISOString())}</text>`)
  out.push(`<text x="${left}" y="84" font-family="'Microsoft YaHei','PingFang SC',sans-serif" font-size="12" fill="#64748b">生命周期：${escapeXml(result.lifecycle?.name ?? '—')}    状态 ${statuses.length} 个    工作流 ${(result.workflows ?? []).length} 条</text>`)

  statuses.forEach((status, index) => {
    const y = top + index * statusGap
    yOf.set(status.id, y)
    out.push(`<rect x="${left}" y="${y}" width="${statusW}" height="${statusH}" rx="0" fill="#bdd7ee" stroke="#939393"/>`)
    out.push(`<text x="${left + 14}" y="${y + 23}" font-family="'Microsoft YaHei','PingFang SC',sans-serif" font-size="14" font-weight="600" fill="#1f2937">${index + 1}. ${escapeXml(status.name)}</text>`)
    out.push(`<text x="${left + 14}" y="${y + 41}" font-family="Consolas,'Courier New',monospace" font-size="11" fill="#475569">${escapeXml(status.code)}</text>`)
    out.push(`<text x="${left + 14}" y="${y + 57}" font-family="'Microsoft YaHei','PingFang SC',sans-serif" font-size="10" fill="#64748b">用户 ${status.userActions.length} / 进入 ${status.enterActions.length} · 启用：${status.isEnabled ? '是' : '否'}</text>`)
  })
  if (statuses.length === 0) {
    out.push(`<text x="${left}" y="${top + 20}" font-family="'Microsoft YaHei','PingFang SC',sans-serif" font-size="12" fill="#64748b">没有生命周期状态</text>`)
  }

  if (unknown.length > 0) {
    out.push(`<rect x="${sideX}" y="${sideTop}" width="${sideW}" height="${panelH}" rx="8" fill="#fff5f5" stroke="#b85450" stroke-dasharray="6 4"/>`)
    out.push(`<text x="${sideX + 12}" y="${sideTop + 24}" font-family="'Microsoft YaHei','PingFang SC',sans-serif" font-size="13" font-weight="700" fill="#b85450">待确认触发源（未读到源状态，不猜测）</text>`)
    panelLines.forEach((line, index) => {
      out.push(`<text x="${sideX + 12}" y="${sideTop + 46 + index * 16}" font-family="'Microsoft YaHei','PingFang SC',sans-serif" font-size="10" fill="#7f1d1d">${escapeXml(shortText(line, 44))}</text>`)
    })
  }

  for (const item of known) {
    const fromY = (yOf.get(item.from) ?? top) + statusH / 2
    const toY = (yOf.get(item.to) ?? top) + statusH / 2
    const right = left + statusW
    // 只从右侧绕出再回到右侧：不穿过状态列，避免线压在状态文字上。
    out.push(`<path d="M${right} ${fromY} C${right + 72} ${fromY}, ${right + 72} ${toY}, ${right} ${toY}" fill="none" stroke="#000000" stroke-width="1.4" marker-end="url(#arrow)"/>`)
    // 侧栏已给出工作流名，这里只保留步骤名，避免标签伸进右侧清单。
    const edgeLabels = [...item.labels].map((label) => label.split(' / ').slice(1).join(' / ') || label)
    out.push(`<text x="${right + 14}" y="${(fromY + toY) / 2 + 3}" font-family="'Microsoft YaHei','PingFang SC',sans-serif" font-size="9" fill="#000000" stroke="#ffffff" stroke-width="3" paint-order="stroke">${escapeXml(shortText(edgeLabels.join(' / '), 24))}</text>`)
  }

  const noteY = Math.max(top + statuses.length * statusGap + 34, sideTop + panelH + 30)
  const notes = [
    '实线：状态动作或工作流已知绑定读出的本对象状态变更；右侧红框：触发源未读到的工作流及其目标状态，不猜测源状态。',
    '状态顺序按 status.sort；本页不是平台声明的唯一迁移顺序。完整工作流步骤见 drawio 的工作流分页。',
    'behaviorType=46 且带 relatedObjectId 的动作改的是关联对象，不作为本对象生命周期边。',
  ]
  notes.forEach((note, index) => {
    out.push(`<text x="${left}" y="${noteY + index * 17}" font-family="'Microsoft YaHei','PingFang SC',sans-serif" font-size="10" fill="#64748b">${escapeXml(note)}</text>`)
  })
  out.push('</svg>')
  return out.join('\n')
}

function shortText(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value
}

export function analysisFolderName(
  result: { object: { code: string } },
  date: Date = new Date(),
): string {
  const pad = (value: number, length = 2): string => String(value).padStart(length, '0')
  const stamp =
    `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}` +
    `-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`
  const code = result.object.code.replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^-+|-+$/g, '') || 'object'
  return `${code}-analysis-${stamp}`
}

export function analysisArtifactName(
  result: { object: { code: string } },
  extension: 'md' | 'drawio' | 'png',
  date: Date = new Date(),
): string {
  return `${analysisFolderName(result, date)}.${extension}`
}

export function analysisPackageName(
  result: { object: { code: string } },
  date: Date = new Date(),
): string {
  return `${analysisFolderName(result, date)}.zip`
}
