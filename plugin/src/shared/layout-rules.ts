/**
 * 表单布局联动规则（layout.rules）：把平台里的数字枚举翻译成人话。
 *
 * 规则的存储位置是表单布局本身：`SaveLayoutDetail` / `LayoutDetail` 的 `rules[]`，
 * 不是「关系联动规则」那套实体（`LinkageRule/GetLinkageRule`，按 objectId/relationId 取，形状完全不同）。
 *
 * 这份模块只做纯计算：条件树、效果包、冲突检查。读接口、抓包、CLI 在别处。
 * 枚举来源：`akso-script-entry-index-*.js` 里的 `ls`（条件目标）、`cs`（效果目标）、
 * `Oa`（条件运算符）、`Fa`（条件组连接），2026-11-23 从线上包读出。
 */

/* ───────────────────────────── 类型 ───────────────────────────── */

export interface LayoutRuleCondition {
  id?: string
  relationId?: string | null
  conditionId?: string | null
  targetType?: number | null
  targetId?: string | null
  targetFieldId?: string | null
  logicType?: number | null
  logicValue?: unknown
  index?: number
  failPrompt?: string | null
}

export interface LayoutRuleConditionGroup {
  id?: string
  logicalOperator?: number | null
  parentId?: string | null
}

export interface LayoutRuleConditions {
  id?: string
  belongId?: string
  details?: LayoutRuleCondition[]
  relations?: LayoutRuleConditionGroup[]
}

export interface LayoutRuleEffect {
  id?: string
  ruleId?: string
  targetType?: number | null
  targetId?: string | null
  path?: string | null
  value?: Record<string, unknown> | null
}

export interface LayoutRule {
  id?: string
  layoutId?: string
  name?: string
  sort?: number
  enable?: boolean
  conditions?: LayoutRuleConditions
  effectSuccess?: LayoutRuleEffect[]
  effectFailed?: LayoutRuleEffect[]
}

export interface LayoutRuleComponent {
  id?: string
  code?: string
  name?: string
  /** 控件才有：字段 id，条件里按字段定位时用它。 */
  fieldId?: string
  /** 组件自带的默认规则属性：规则没写到的属性，运行时取这里的值。 */
  readOnly?: boolean | null
  required?: boolean | null
  visible?: boolean | null
  hidden?: boolean | null
  readPretty?: boolean | null
  expansion?: boolean | null
}

/** 一个组件的默认属性；只收 payload 里真实出现过的键，避免把「没配」当成 false。 */
export interface ComponentBaseline {
  readOnly?: boolean | null
  required?: boolean | null
  visible?: boolean | null
  hidden?: boolean | null
  readPretty?: boolean | null
  expansion?: boolean | null
}

export interface LayoutRuleLayout {
  id?: string
  code?: string
  name?: string
  sections?: LayoutRuleComponent[]
  controls?: LayoutRuleComponent[]
  rules?: LayoutRule[]
}

export interface RuleIssue {
  level: 'error' | 'warning'
  ruleIndex: number
  ruleName: string
  message: string
}

export interface RuleDescription {
  index: number
  name: string
  enabled: boolean
  conditions: string[]
  success: string[]
  failed: string[]
}

/* ───────────────────────────── 枚举 ───────────────────────────── */

/** 条件的目标类型：`conditions.details[].targetType`。 */
export const CONDITION_TARGETS: Readonly<Record<number, string>> = {
  0: '当前对象状态',
  1: '当前对象字段',
  2: '相关对象状态',
  3: '相关对象字段',
  4: '当前用户角色',
  5: '表单触发动作',
  6: '子集对象状态',
  7: '流程任务断言',
  8: '流程变量',
  9: '入站关系',
  10: '文档关系',
  11: '表单页面',
  18: '状态类型',
}

/** 效果的目标类型：`effectSuccess[] / effectFailed[].targetType`。 */
export const EFFECT_TARGETS: Readonly<Record<number, string>> = {
  1: '阶段导航',
  2: '动作按钮',
  4: '部分',
  5: '控件',
  6: '表单页面',
  7: '表单布局',
}

/** 条件运算符：`details[].logicType`。 */
export const LOGIC_TYPES: Readonly<Record<number, string>> = {
  1: '等于',
  2: '不等于',
  3: '开始于',
  4: '大于',
  5: '小于',
  6: '大于等于',
  7: '小于等于',
  8: '包含',
  9: '不包含',
  10: '为空',
  11: '不为空',
  12: '存在',
  13: '不存在',
  14: '介于',
  15: '结束于',
  16: '在过去',
  17: '在未来',
  18: '不在过去',
  19: '包含于',
  20: '不包含于',
  21: '已变更',
  30: '正则匹配',
  31: '正则不匹配',
  40: '所有断言等于',
  41: '至少一个断言等于',
  42: '没有断言等于',
  50: '所有记录状态包含',
  51: '任一记录状态包含',
  52: '没有记录状态包含',
  53: '存在记录',
  54: '不存在记录',
  60: '任一记录满足以下条件',
  61: '没有记录满足以下条件',
}

/** 条件组之间怎么连：`conditions.relations[].logicalOperator`。 */
export const LOGIC_OPERATORS: Readonly<Record<number, string>> = {
  1: '并且',
  2: '或者',
}

/** 效果属性包 `effect.value` 里常见的键；不认识的键原样输出。 */
export const EFFECT_ATTR_LABELS: Readonly<Record<string, string>> = {
  visible: '显示',
  hidden: '隐藏',
  readOnly: '只读',
  readPretty: '阅读',
  required: '必填',
  expansion: '展开',
  activePageCode: '活动页',
  activeSectionCode: '活动分区',
  formEditStatus: '表单编辑状态',
  attrs: '控件属性',
}

/* ───────────────────────────── 取值与文案 ───────────────────────────── */

export function conditionTargetLabel(value: number | null | undefined): string {
  if (typeof value !== 'number') return '未知目标'
  return CONDITION_TARGETS[value] ?? `目标 ${value}`
}

export function effectTargetLabel(value: number | null | undefined): string {
  if (typeof value !== 'number') return '未知目标'
  return EFFECT_TARGETS[value] ?? `目标 ${value}`
}

export function logicTypeLabel(value: number | null | undefined): string {
  if (typeof value !== 'number') return '未知运算符'
  return LOGIC_TYPES[value] ?? `运算符 ${value}`
}

export function logicOperatorLabel(value: number | null | undefined): string {
  if (typeof value !== 'number') return '并且'
  return LOGIC_OPERATORS[value] ?? `连接 ${value}`
}

/** 条件值在服务端是 JSON 字符串（`"\"<id>\""`），有时又是裸值。 */
export function parseLogicValue(raw: unknown): unknown {
  if (typeof raw !== 'string') return raw ?? null
  const text = raw.trim()
  if (text === '') return ''
  try {
    return JSON.parse(text) as unknown
  } catch {
    return raw
  }
}

export function displayValue(raw: unknown, labels?: Readonly<Record<string, string>>): string {
  const value = parseLogicValue(raw)
  const label = (text: string): string => labels?.[text] ?? text
  if (value === null || value === undefined || value === '') return '空'
  if (Array.isArray(value)) {
    return value
      .map((item) => label(typeof item === 'string' ? item : JSON.stringify(item) ?? String(item)))
      .join('、')
  }
  if (typeof value === 'string') return label(value)
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value) ?? String(value)
}

export function shortId(value: string | null | undefined): string {
  if (typeof value !== 'string' || value === '') return '—'
  return value.length > 8 ? value.slice(0, 8) : value
}

/** 效果包转 `readOnly=false, required=true` 这样的一行；给了组件默认值时顺带标出覆盖或空操作。 */
export function describeEffectValue(
  value: Record<string, unknown> | null | undefined,
  baseline?: ComponentBaseline,
): string {
  if (value === null || value === undefined) return ''
  const entries = Object.entries(value)
  if (entries.length === 0) return ''
  return entries
    .map(([key, raw]) => {
      const label = EFFECT_ATTR_LABELS[key] ?? key
      if (raw === null || raw === undefined) return `${label}=空`
      const text = typeof raw === 'object' ? `${label}=${JSON.stringify(raw)}` : `${label}=${String(raw)}`
      return `${text}${baselineNote(key, raw, baseline)}`
    })
    .join('，')
}

/** 把一个效果属性与组件默认值比较：不同就说默认值，相同就点破「空操作」。 */
function baselineNote(key: string, raw: unknown, baseline?: ComponentBaseline): string {
  if (baseline === undefined) return ''
  if (key === 'hidden' || key === 'visible') {
    const base = baselineHidden(baseline)
    if (base === undefined || typeof raw !== 'boolean') return ''
    const effective = key === 'hidden' ? raw : !raw
    return effective === base ? '（与组件默认相同，等于空操作）' : `（组件默认${base ? '隐藏' : '显示'}）`
  }
  if (key !== 'readOnly' && key !== 'required' && key !== 'readPretty' && key !== 'expansion') return ''
  const base = baselineFlag(baseline, key)
  if (base === undefined || typeof raw !== 'boolean') return ''
  return base === raw ? '（与组件默认相同，等于空操作）' : `（组件默认 ${base ? 'true' : 'false'}）`
}

/** 只读取标量布尔默认值；attrs.* 这类没有默认值可比。 */
function baselineFlag(baseline: ComponentBaseline, key: string): boolean | undefined {
  if (key !== 'readOnly' && key !== 'required' && key !== 'readPretty' && key !== 'expansion') return undefined
  const value = baseline[key]
  return typeof value === 'boolean' ? value : undefined
}

/* ───────────────────────────── 规则描述 ───────────────────────────── */

export interface RuleLookups {
  /** 目标 id → 展示名，例如 `5463d9b7-…` → `基础信息 basic_information__cs`。 */
  labels?: Readonly<Record<string, string>>
  /** 目标 id → 组件自带属性；用于标出「覆盖了默认值」或「与默认值相同，等于空操作」。 */
  baselines?: Readonly<Record<string, ComponentBaseline>>
}

function targetName(type: number | null | undefined, id: string | null | undefined, lookups: RuleLookups): string {
  const label = typeof id === 'string' && id !== '' ? lookups.labels?.[id] : undefined
  return label ?? shortId(id)
}

export function describeCondition(detail: LayoutRuleCondition, lookups: RuleLookups = {}): string {
  const label = conditionTargetLabel(detail.targetType)
  const name = targetName(detail.targetType, detail.targetId, lookups)
  const target = name === "—" ? label : `${label}「${name}」`
  const operator = logicTypeLabel(detail.logicType)
  const shown = displayValue(detail.logicValue, lookups.labels)
  const needsValue =
    detail.logicType !== undefined &&
    detail.logicType !== null &&
    ![10, 11, 12, 13, 21, 53, 54].includes(detail.logicType)
  return needsValue ? `${target} ${operator} ${shown}` : `${target} ${operator}`
}

export function describeEffect(effect: LayoutRuleEffect, lookups: RuleLookups = {}): string {
  const target = `${effectTargetLabel(effect.targetType)}「${targetName(effect.targetType, effect.targetId, lookups)}」`
  const baseline = typeof effect.targetId === 'string' ? lookups.baselines?.[effect.targetId] : undefined
  const value = describeEffectValue(effect.value, baseline)
  return value === '' ? target : `${target} → ${value}`
}

/** `rule` 的每条条件按 `conditions.relations` 的树拼成 AND/OR 文本；结构坏了就退化成平铺。 */
export function describeConditionTree(rule: LayoutRule, lookups: RuleLookups = {}): string[] {
  const details = rule.conditions?.details ?? []
  const relations = rule.conditions?.relations ?? []
  if (details.length === 0) return []
  if (relations.length === 0) return details.map((detail) => describeCondition(detail, lookups))

  const groups = new Map<string, { operator: number; children: string[] }>()
  for (const relation of relations) {
    if (typeof relation.id !== 'string') continue
    groups.set(relation.id, {
      operator: logicOperatorLabel(relation.logicalOperator) === '或者' ? 2 : 1,
      children: [],
    })
  }
  const roots: string[] = []
  for (const relation of relations) {
    if (typeof relation.id !== 'string') continue
    const group = groups.get(relation.id)
    if (group === undefined) continue
    const parent = relation.parentId
    const parentGroup = typeof parent === 'string' ? groups.get(parent) : undefined
    if (parentGroup !== undefined && parent !== relation.id) {
      parentGroup.children.push(relation.id)
    } else {
      roots.push(relation.id)
    }
  }

  const lines: string[] = []
  const detailLines = new Map<string, string[]>()
  for (const detail of details) {
    const relationId = typeof detail.relationId === 'string' ? detail.relationId : ''
    const list = detailLines.get(relationId) ?? []
    list.push(describeCondition(detail, lookups))
    detailLines.set(relationId, list)
  }

  const render = (id: string, depth: number): void => {
    const group = groups.get(id)
    if (group === undefined) return
    const own = detailLines.get(id) ?? []
    const joiner = group.operator === 2 ? ' 或 ' : ' 且 '
    for (const child of own) lines.push(`${depth === 0 ? '' : `${'  '.repeat(depth)}`}${child}`)
    for (const childId of group.children) {
      const childGroup = groups.get(childId)
      if (childGroup === undefined) continue
      const childOwn = detailLines.get(childId) ?? []
      if (childOwn.length > 1) {
        lines.push(`${'  '.repeat(depth)}（${childOwn.join(joiner)}）`)
      } else if (childOwn.length === 1) {
        lines.push(`${'  '.repeat(depth)}${childOwn[0] ?? ''}`)
      }
      render(childId, depth + 1)
    }
  }

  const topRoots = roots.filter((id) => {
    const relation = relations.find((item) => item.id === id)
    return relation !== undefined && (relation.parentId === null || relation.parentId === undefined || relation.parentId === '' || relation.parentId === '00000000-0000-0000-0000-000000000000')
  })
  for (const root of topRoots) render(root, 0)

  // 树结构没认出来（比如根 parentId 不是空 GUID）时，不要把条件吞掉。
  if (lines.length === 0) return details.map((detail) => describeCondition(detail, lookups))
  return lines
}

export function describeRule(rule: LayoutRule, index: number, lookups: RuleLookups = {}): RuleDescription {
  return {
    index,
    name: rule.name === undefined || rule.name === '' ? `规则 ${index + 1}` : rule.name,
    enabled: rule.enable !== false,
    conditions: describeConditionTree(rule, lookups),
    success: (rule.effectSuccess ?? []).map((effect) => describeEffect(effect, lookups)),
    failed: (rule.effectFailed ?? []).map((effect) => describeEffect(effect, lookups)),
  }
}

export function describeLayout(layout: LayoutRuleLayout, lookups: RuleLookups = {}): RuleDescription[] {
  const merged: RuleLookups = {
    labels: { ...componentLabels(layout), ...(lookups.labels ?? {}) },
    baselines: { ...componentBaselines(layout), ...(lookups.baselines ?? {}) },
  }
  return (layout.rules ?? []).map((rule, index) => describeRule(rule, index, merged))
}

/** 分区 + 控件 id → `名称 code`，规则里的 targetId 靠它翻成人话。 */
export function componentLabels(layout: LayoutRuleLayout): Record<string, string> {
  const labels: Record<string, string> = {}
  for (const item of [...(layout.sections ?? []), ...(layout.controls ?? [])]) {
    if (typeof item.id !== 'string' || item.id === '') continue
    const name = typeof item.name === 'string' ? item.name : ''
    const code = typeof item.code === 'string' ? item.code : ''
    const text = [name, code].filter((part) => part !== '').join(' ')
    labels[item.id] = text === '' ? shortId(item.id) : text
    if (typeof item.fieldId === 'string' && item.fieldId !== '' && text !== '') labels[item.fieldId] = text
  }
  return labels
}

const BASELINE_KEYS = ['readOnly', 'required', 'visible', 'hidden', 'readPretty', 'expansion'] as const

/** 组件 id → 自带的默认属性；只收 payload 里真实出现过的键，没出现的不猜默认值。 */
export function componentBaselines(layout: LayoutRuleLayout): Record<string, ComponentBaseline> {
  const baselines: Record<string, ComponentBaseline> = {}
  for (const item of [...(layout.sections ?? []), ...(layout.controls ?? [])]) {
    if (typeof item.id !== 'string' || item.id === '') continue
    const baseline: ComponentBaseline = {}
    for (const key of BASELINE_KEYS) {
      if (Object.prototype.hasOwnProperty.call(item, key)) baseline[key] = item[key]
    }
    baselines[item.id] = baseline
  }
  return baselines
}

/** 组件默认是否隐藏；`hidden`/`visible` 都没显式配时按平台默认「显示」处理。 */
function baselineHidden(baseline: ComponentBaseline | undefined): boolean | undefined {
  if (baseline === undefined) return undefined
  if (baseline.hidden === true) return true
  if (baseline.hidden === false) return false
  if (baseline.visible === false) return true
  if (baseline.visible === true) return false
  if ('hidden' in baseline || 'visible' in baseline) return false
  return undefined
}

/* ───────────────────────────── 校验 ───────────────────────────── */

function issue(level: RuleIssue['level'], rule: LayoutRule, index: number, message: string): RuleIssue {
  return { level, ruleIndex: index, ruleName: rule.name === undefined || rule.name === '' ? `规则 ${index + 1}` : rule.name, message }
}

export function validateRule(rule: LayoutRule, index: number): RuleIssue[] {
  const issues: RuleIssue[] = []
  const details = rule.conditions?.details ?? []
  const success = rule.effectSuccess ?? []
  const failed = rule.effectFailed ?? []

  if (details.length === 0) issues.push(issue('error', rule, index, '没有条件：规则不会触发'))
  if (success.length === 0 && failed.length === 0) issues.push(issue('error', rule, index, '没有效果：触发后什么都不改'))

  for (const [detailIndex, detail] of details.entries()) {
    const at = `条件 ${detailIndex + 1}`
    if (detail.targetType === undefined || detail.targetType === null) {
      issues.push(issue('error', rule, index, `${at}：缺 targetType`))
    } else if (CONDITION_TARGETS[detail.targetType] === undefined) {
      issues.push(issue('warning', rule, index, `${at}：未知的 targetType=${detail.targetType}`))
    }
    if ((detail.targetId === undefined || detail.targetId === null || detail.targetId === '') && detail.targetType !== 0) {
      issues.push(issue('warning', rule, index, `${at}：没有 targetId，运行时可能取不到比较对象`))
    }
    if (detail.logicType === undefined || detail.logicType === null) {
      issues.push(issue('error', rule, index, `${at}：缺 logicType`))
    } else if (LOGIC_TYPES[detail.logicType] === undefined) {
      issues.push(issue('warning', rule, index, `${at}：未知的 logicType=${detail.logicType}`))
    }
  }

  for (const [effectIndex, effect] of [...success, ...failed].entries()) {
    const at = `效果 ${effectIndex + 1}`
    if (effect.targetType === undefined || effect.targetType === null) {
      issues.push(issue('error', rule, index, `${at}：缺 targetType`))
    } else if (EFFECT_TARGETS[effect.targetType] === undefined) {
      issues.push(issue('warning', rule, index, `${at}：未知的 targetType=${effect.targetType}`))
    }
    if (effect.targetId === undefined || effect.targetId === null || effect.targetId === '') {
      issues.push(issue('error', rule, index, `${at}：缺 targetId`))
    }
    if (effect.value === null || effect.value === undefined || Object.keys(effect.value).length === 0) {
      issues.push(issue('warning', rule, index, `${at}：效果包是空的，等于什么都不改`))
    }
  }

  const relationIds = new Set((rule.conditions?.relations ?? []).map((relation) => relation.id).filter((id): id is string => typeof id === 'string'))
  for (const detail of details) {
    if (typeof detail.relationId === 'string' && detail.relationId !== '' && relationIds.size > 0 && !relationIds.has(detail.relationId)) {
      issues.push(issue('warning', rule, index, `条件挂了不存在的 relationId=${shortId(detail.relationId)}`))
    }
  }

  return issues
}

export interface LayoutRuleCheck {
  errors: RuleIssue[]
  warnings: RuleIssue[]
}

/**
 * 整份布局的规则体检：单条规则的问题 + 多规则互相覆盖。
 * 覆盖只提示不报错——平台语义是「后触发的规则生效」，这是合法用法。
 */
export function checkLayoutRules(layout: LayoutRuleLayout): LayoutRuleCheck {
  const errors: RuleIssue[] = []
  const warnings: RuleIssue[] = []

  for (const [index, rule] of (layout.rules ?? []).entries()) {
    for (const item of validateRule(rule, index)) {
      if (item.level === 'error') errors.push(item)
      else warnings.push(item)
    }
  }

  const buckets = new Map<string, { rule: LayoutRule; index: number; value: string }[]>()
  for (const [index, rule] of (layout.rules ?? []).entries()) {
    if (rule.enable === false) continue
    const effects = [...(rule.effectSuccess ?? []), ...(rule.effectFailed ?? [])]
    for (const effect of effects) {
      if (typeof effect.targetType !== 'number' || typeof effect.targetId !== 'string') continue
      const key = `${effect.targetType}:${effect.targetId}:${effect.path ?? ''}`
      const bucket = buckets.get(key) ?? []
      bucket.push({ rule, index, value: JSON.stringify(effect.value ?? null) })
      buckets.set(key, bucket)
    }
  }

  for (const bucket of buckets.values()) {
    if (bucket.length < 2) continue
    const distinct = new Set(bucket.map((item) => item.value))
    if (distinct.size < 2) continue
    const names = [...new Set(bucket.map((item) => (item.rule.name === undefined || item.rule.name === '' ? `规则 ${item.index + 1}` : item.rule.name)))]
    const first = bucket[0]
    if (first === undefined) continue
    const sample = [...(first.rule.effectSuccess ?? []), ...(first.rule.effectFailed ?? [])][0]
    const label = sample === undefined ? '同一目标' : `${effectTargetLabel(sample.targetType)}「${shortId(sample.targetId)}」`
    warnings.push({
      level: 'warning',
      ruleIndex: first.index,
      ruleName: names[0] ?? '规则',
      message: `${label} 被 ${names.join(' / ')} 同时设置且取值不同；按平台语义，最后触发的规则生效`,
    })
  }

  const baselines = componentBaselines(layout)
  const labels = componentLabels(layout)
  for (const [index, rule] of (layout.rules ?? []).entries()) {
    const effects = [...(rule.effectSuccess ?? []), ...(rule.effectFailed ?? [])]
    for (const effect of effects) {
      if (typeof effect.targetId !== 'string' || typeof effect.targetType !== 'number') continue
      const value = effect.value
      if (value === null || typeof value !== 'object') continue
      const baseline = baselines[effect.targetId]
      if (baseline === undefined) continue
      const target = `${effectTargetLabel(effect.targetType)}「${labels[effect.targetId] ?? shortId(effect.targetId)}」`
      for (const [key, raw] of Object.entries(value)) {
        if (typeof raw !== 'boolean') continue
        let noop = false
        if (key === 'hidden' || key === 'visible') {
          const base = baselineHidden(baseline)
          if (base !== undefined) noop = (key === 'hidden' ? raw : !raw) === base
        } else if (key === 'readOnly' || key === 'required' || key === 'readPretty' || key === 'expansion') {
          const base = baselineFlag(baseline, key)
          if (base !== undefined) noop = base === raw
        }
        if (!noop) continue
        const label = EFFECT_ATTR_LABELS[key] ?? key
        warnings.push(issue('warning', rule, index, `${target} 的「${label}」与组件默认相同，等于空操作`))
      }
    }
  }

  return { errors, warnings }
}



