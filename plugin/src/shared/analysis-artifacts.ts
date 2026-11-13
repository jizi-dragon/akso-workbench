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
  const edges: Array<{ from: string; to: string; labels: Set<string> }> = []
  const addEdge = (from: string, to: string, text: string): void => {
    if (from === to || !statusById.has(from) || !statusById.has(to)) return
    let found = edges.find((item) => item.from === from && item.to === to)
    if (found === undefined) {
      found = { from, to, labels: new Set<string>() }
      edges.push(found)
    }
    found.labels.add(text)
  }

  for (const status of statuses) {
    for (const action of [...status.userActions, ...status.enterActions]) {
      if (action.targetStatusId !== undefined && action.targetStatusId !== null) {
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
      for (const [target, labels] of targets) {
        addEdge(source, target, `${workflow.name} / ${[...labels].join('、')}`)
      }
    }
  }

  const nodeW = 300
  const nodeH = 104
  const orderOf = new Map(statuses.map((status, index) => [status.id, index]))
  const layoutEdges = edges
    .map((item, index) => ({ id: `se_${index}`, from: item.from, to: item.to, index }))
    .filter((item) => (orderOf.get(item.from) ?? 0) < (orderOf.get(item.to) ?? 0))
    .map(({ id, from, to }) => ({ id, from, to }))
  // 用 status.sort 加一条不可见的“排序链”，避免 dagre 把无连线状态排到最上面，
  // 也让主链尽量保持竖向；这条链只参与布局，不渲染。
  for (let index = 0; index + 1 < statuses.length; index += 1) {
    const from = statuses[index]
    const to = statuses[index + 1]
    if (from === undefined || to === undefined) continue
    layoutEdges.push({ id: `rank_${index}`, from: from.id, to: to.id })
  }
  const layout = layoutLayered(
    statuses.map((status) => ({ id: status.id, width: nodeW, height: nodeH })),
    layoutEdges,
  )
  // dagre 只负责纵向分层；同一层里为了避开回边会把主链摆成锯齿。
  // 这里取“最长前向路径”作为主链，把主链节点对齐到同一列，支线放右列。
  const forward = edges
    .filter((item) => (orderOf.get(item.from) ?? 0) < (orderOf.get(item.to) ?? 0))
    .map((item) => ({ from: item.from, to: item.to }))
  const distance = new Map<string, number>()
  const previous = new Map<string, string>()
  for (const status of statuses) {
    let best = 1
    let prev: string | undefined
    for (const edge of forward) {
      if (edge.to !== status.id) continue
      const value = (distance.get(edge.from) ?? 1) + 1
      if (value > best) {
        best = value
        prev = edge.from
      }
    }
    distance.set(status.id, best)
    if (prev !== undefined) previous.set(status.id, prev)
  }
  let tail = statuses[0]?.id
  let longest = 0
  for (const status of statuses) {
    const value = distance.get(status.id) ?? 0
    if (value > longest) {
      longest = value
      tail = status.id
    }
  }
  const main = new Set<string>()
  for (let cursor = tail; cursor !== undefined; cursor = previous.get(cursor)) {
    main.add(cursor)
    if (main.size > statuses.length) break
  }
  const mainX = 120
  const branchX = mainX + 360
  for (const [id, node] of layout.nodes) {
    node.x = main.has(id) ? mainX : branchX
  }
  const offsetX = 0
  const offsetY = 120
  const top = offsetY
  const panelX = Math.max(820, branchX + 340)
  const sideTop = 140
  const cells: string[] = []
  statuses.forEach((status, index) => {
    const placed = layout.nodes.get(status.id)
    if (placed === undefined) return
    cells.push(
      cell(
        `st_${status.id}`,
        `${index + 1}. ${status.name}\n${status.code}\n用户 ${status.userActions.length} / 进入 ${status.enterActions.length}`,
        `${STICKY_STYLES.lifecycleStatus}align=left;spacingLeft=10;`,
        placed.x + offsetX,
        placed.y + offsetY,
        nodeW,
        nodeH,
      ),
    )
  })
  if (statuses.length === 0) {
    cells.push(cell('empty', '没有生命周期状态', 'text;html=1;align=center;', 70, 150, 340, 40))
  }
  const unknownLines: string[] = ['待确认触发源（未读到源状态，不猜测）']
  for (const workflow of unknown) {
    unknownLines.push('', `• ${workflow.name}`)
    for (const target of workflow.targets.keys()) {
      unknownLines.push(`  目标：${statusById.get(target)?.name ?? target}`)
    }
  }
  const unknownPanelH = unknown.length > 0 ? 34 + unknownLines.length * 14 : 0
  if (unknown.length > 0) {
    cells.push(
      cell(
        'unknown_panel',
        unknownLines.join('\n'),
        'text;html=1;whiteSpace=wrap;align=left;verticalAlign=top;spacing=8;fillColor=#fff5f5;strokeColor=#b85450;dashed=1;rounded=1;fontSize=10;',
        panelX,
        sideTop,
        360,
        unknownPanelH,
      ),
    )
  }

  type Side = 'top' | 'bottom' | 'left' | 'right'
  const center = (id: string): { x: number; y: number } => {
    const node = layout.nodes.get(id)
    if (node === undefined) return { x: 0, y: 0 }
    return { x: node.x + node.width / 2, y: node.y + node.height / 2 }
  }
  const sideOf = (from: string, to: string): { out: Side; in: Side } => {
    const source = center(from)
    const target = center(to)
    const dx = target.x - source.x
    const dy = target.y - source.y
    if (Math.abs(dy) >= Math.abs(dx)) {
      return dy >= 0 ? { out: 'bottom', in: 'top' } : { out: 'top', in: 'bottom' }
    }
    return dx >= 0 ? { out: 'right', in: 'left' } : { out: 'left', in: 'right' }
  }
  const sides = edges.map((item) => sideOf(item.from, item.to))
  const outGroups = new Map<string, number[]>()
  const inGroups = new Map<string, number[]>()
  sides.forEach((side, index) => {
    const item = edges[index]
    if (item === undefined) return
    const outKey = `${item.from}:${side.out}`
    const inKey = `${item.to}:${side.in}`
    outGroups.set(outKey, [...(outGroups.get(outKey) ?? []), index])
    inGroups.set(inKey, [...(inGroups.get(inKey) ?? []), index])
  })
  const fraction = (index: number, group: number[] | undefined, vertical: boolean): number => {
    if (group === undefined || group.length <= 1) return 0.5
    const sorted = [...group].sort((left, right) => {
      const a = edges[left]
      const b = edges[right]
      if (a === undefined || b === undefined) return 0
      const ac = center(vertical ? a.to : a.from)
      const bc = center(vertical ? b.to : b.from)
      return (vertical ? ac.x - bc.x : ac.y - bc.y) || left - right
    })
    const position = sorted.indexOf(index)
    return (position + 1) / (sorted.length + 1)
  }
  edges.forEach((item, index) => {
    const side = sides[index]
    if (side === undefined) return
    const labelText = [...item.labels].join('\n')
    const vertical = side.out === 'top' || side.out === 'bottom'
    const outFraction = fraction(index, outGroups.get(`${item.from}:${side.out}`), vertical)
    const inFraction = fraction(index, inGroups.get(`${item.to}:${side.in}`), vertical)
    const out =
      side.out === 'bottom'
        ? `exitX=${outFraction.toFixed(3)};exitY=1;`
        : side.out === 'top'
          ? `exitX=${outFraction.toFixed(3)};exitY=0;`
          : side.out === 'right'
            ? `exitX=1;exitY=${outFraction.toFixed(3)};`
            : `exitX=0;exitY=${outFraction.toFixed(3)};`
    const into =
      side.in === 'top'
        ? `entryX=${inFraction.toFixed(3)};entryY=0;`
        : side.in === 'bottom'
          ? `entryX=${inFraction.toFixed(3)};entryY=1;`
          : side.in === 'left'
            ? `entryX=0;entryY=${inFraction.toFixed(3)};`
            : `entryX=1;entryY=${inFraction.toFixed(3)};`
    const points: Array<{ x: number; y: number }> = []
    cells.push(
      edgeWithPoints(
        `state_edge_${index}`,
        `st_${item.from}`,
        `st_${item.to}`,
        labelText,
        `${STICKY_STYLES.edge}fontSize=9;labelBackgroundColor=#ffffff;${out}${into}`,
        points,
      ),
    )
  })

  const notes: string[] = []
  if (edges.length === 0) {
    notes.push('状态直改边：没有读到 behaviorType=9 的本对象状态变更。')
  } else {
    notes.push(`状态直改边 ${edges.length} 条：实线来自状态动作或已确认触发源的工作流。`)
  }
  notes.push('图形固定遵循便笺本：状态为 #bdd7ee 矩形，判断为 #f4b183 菱形，开始/结束为 #70ad47 圆形，连线为黑色箭头（docs/analysis/便笺本.xml）。')
  notes.push('本页列出全部状态；状态按 status.sort 排列。右侧红色框列出触发源未确认的工作流及其目标状态，不猜测源状态；具体步骤见各工作流页。')
  notes.push('工作流里 behaviorType=46 的动作改的是关联对象，不画在本页。')

  const height = Math.max(760, layout.height + offsetY + 100, sideTop + unknownPanelH + 80)
  const pageWidth = Math.max(1100, panelX + 420)
  const out: string[] = []
  out.push(`<diagram id="lifecycle" name="生命周期总览">`)
  out.push(`<mxGraphModel dx="1200" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="${pageWidth}" pageHeight="${height}">`)
  out.push('<root>')
  out.push('<mxCell id="0"/>')
  out.push('<mxCell id="1" parent="0"/>')
  out.push(cell('title', `${result.object.name}（${result.object.code}）生命周期总览`, 'text;html=1;align=left;fontSize=22;fontStyle=1;', 40, 20, 900, 32))
  out.push(cell('meta', `环境：${result.source.baseUrl}\n账号：${result.source.account}\n生成：${generatedAt.toISOString()}\n生命周期：${result.lifecycle?.name ?? '—'}`, 'text;html=1;align=left;fontSize=10;verticalAlign=top;', 40, 54, 900, 56))
  for (const cellXml of cells) out.push(cellXml)
  let noteY = layout.height + offsetY + 24
  for (const note of notes) {
    out.push(cell(`note_${noteY}`, note, 'text;html=1;align=left;fontSize=10;fontColor=#666666;', 70, noteY, 860, 22))
    noteY += 22
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

export function analysisArtifactName(
  result: { object: { code: string } },
  extension: 'md' | 'drawio' | 'png',
  date: Date = new Date(),
): string {
  const stamp = date.toISOString().slice(0, 10)
  const code = result.object.code.replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^-+|-+$/g, '') || 'object'
  return `${code}-analysis-${stamp}.${extension}`
}
