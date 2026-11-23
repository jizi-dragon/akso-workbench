import { strict as assert } from 'node:assert'
import { test } from 'node:test'
import {
  checkLayoutRules,
  componentLabels,
  describeCondition,
  describeEffect,
  describeEffectValue,
  describeLayout,
  displayValue,
  effectTargetLabel,
  conditionTargetLabel,
  logicOperatorLabel,
  logicTypeLabel,
  parseLogicValue,
  validateRule,
  type LayoutRule,
  type LayoutRuleLayout,
} from '../src/shared/layout-rules.ts'

const BASE_SECTION = '5463d9b7-222b-ccf1-e130-99bb7203d59f'
const AUTH_SECTION = '09f78c1d-e029-92f6-2a27-4c597121528b'
const RULE_ID = '70f8cdfb-1111-2222-3333-444455556666'
const STATUS_ID = '43ad406d-b18c-9e2d-ade0-c8902810d77f'
const RELATION_ID = '72484951-aaaa-bbbb-cccc-ddddeeeeffff'

function rule(overrides: Partial<LayoutRule> = {}): LayoutRule {
  return {
    id: RULE_ID,
    name: '状态=草稿',
    sort: 0,
    enable: true,
    conditions: {
      belongId: RULE_ID,
      id: 'conditions-1',
      details: [
        {
          id: 'detail-1',
          relationId: RELATION_ID,
          conditionId: '00000000-0000-0000-0000-000000000000',
          targetType: 0,
          logicType: 1,
          logicValue: JSON.stringify(STATUS_ID),
          index: 1,
        },
      ],
      relations: [{ id: RELATION_ID, logicalOperator: 1, parentId: '00000000-0000-0000-0000-000000000000' }],
    },
    effectSuccess: [
      { ruleId: RULE_ID, targetType: 4, targetId: BASE_SECTION, path: null, value: { readOnly: false } },
      { ruleId: RULE_ID, targetType: 4, targetId: AUTH_SECTION, path: null, value: { readOnly: false } },
    ],
    effectFailed: [],
    ...overrides,
  }
}

function layout(rules: LayoutRule[] = [rule()]): LayoutRuleLayout {
  return {
    id: 'f652777b-86b6-60be-5260-8d3738271ad5',
    code: 'computerized_system_authorization_cancel_trial_layout__ak',
    name: '取消试用默认布局',
    sections: [
      { id: BASE_SECTION, code: 'basic_information__cs', name: '基础信息' },
      { id: AUTH_SECTION, code: 'authorization_details__cs', name: '授权明细' },
    ],
    controls: [{ id: 'control-1', code: 'name__ak', name: '名称', fieldId: 'field-1' }],
    rules,
  }
}

test('枚举翻译：未知值保留数字，方便排查', () => {
  assert.equal(conditionTargetLabel(0), '当前对象状态')
  assert.equal(conditionTargetLabel(18), '状态类型')
  assert.equal(conditionTargetLabel(99), '目标 99')
  assert.equal(effectTargetLabel(4), '部分')
  assert.equal(effectTargetLabel(5), '控件')
  assert.equal(logicTypeLabel(8), '包含')
  assert.equal(logicTypeLabel(31), '正则不匹配')
  assert.equal(logicTypeLabel(999), '运算符 999')
  assert.equal(logicOperatorLabel(2), '或者')
  assert.equal(logicOperatorLabel(undefined), '并且')
})

test('logicValue：服务端存的 JSON 字符串会先解一层', () => {
  assert.equal(parseLogicValue(JSON.stringify(STATUS_ID)), STATUS_ID)
  assert.equal(parseLogicValue('not-json'), 'not-json')
  assert.equal(parseLogicValue(''), '')
  assert.equal(parseLogicValue(null), null)
  assert.equal(displayValue(JSON.stringify(['a', 'b'])), 'a、b')
  assert.equal(displayValue(null), '空')
})

test('条件文案：状态 id 能按映射表翻成「草稿」', () => {
  const detail = rule().conditions?.details?.[0]
  assert.ok(detail)
  const text = describeCondition(detail, { labels: { [STATUS_ID]: '草稿' } })
  assert.equal(text, '当前对象状态 等于 草稿')
})

test('效果属性：readOnly 等键翻成中文，未知键原样保留', () => {
  assert.equal(describeEffectValue({ readOnly: false, required: true }), '只读=false，必填=true')
  assert.equal(describeEffectValue({ customFlag: 1 }), 'customFlag=1')
  assert.equal(describeEffectValue(null), '')
})

test('整条规则：条件 + 两条效果，目标 id 翻成分区 code', () => {
  const described = describeLayout(layout(), { labels: { [STATUS_ID]: '草稿' } })
  assert.equal(described.length, 1)
  const [first] = described
  assert.ok(first)
  assert.equal(first.name, '状态=草稿')
  assert.equal(first.enabled, true)
  assert.deepEqual(first.conditions, ['当前对象状态 等于 草稿'])
  assert.deepEqual(first.success, [
    '部分「基础信息 basic_information__cs」 → 只读=false',
    '部分「授权明细 authorization_details__cs」 → 只读=false',
  ])
  assert.deepEqual(first.failed, [])
})

test('效果文案：没有可读 value 时只显示目标', () => {
  assert.equal(
    describeEffect({ targetType: 5, targetId: 'control-1' }, { labels: { 'control-1': '名称 name__ak' } }),
    '控件「名称 name__ak」',
  )
})

test('组件标签：section 与 control 都能查，control 的 fieldId 也登记', () => {
  const labels = componentLabels(layout([]))
  assert.equal(labels[BASE_SECTION], '基础信息 basic_information__cs')
  assert.equal(labels['field-1'], '名称 name__ak')
})

test('校验：没有条件 / 没有效果 / 效果缺 targetId 都算错误', () => {
  const empty = rule({ conditions: { details: [], relations: [] }, effectSuccess: [], effectFailed: [] })
  const issues = validateRule(empty, 0)
  assert.deepEqual(
    issues.filter((item) => item.level === 'error').map((item) => item.message),
    ['没有条件：规则不会触发', '没有效果：触发后什么都不改'],
  )

  const broken = rule({
    effectSuccess: [{ targetType: 4, targetId: '', value: { readOnly: false } }],
  })
  const brokenIssues = validateRule(broken, 0)
  assert.ok(brokenIssues.some((item) => item.message.includes('缺 targetId')))
})

test('校验：未知枚举只提醒，不阻断', () => {
  const weird = rule({
    conditions: {
      details: [{ targetType: 99, logicType: 999, logicValue: 'x', targetId: 'y', relationId: RELATION_ID }],
      relations: [{ id: RELATION_ID, logicalOperator: 1, parentId: null }],
    },
  })
  const issues = validateRule(weird, 0)
  assert.equal(issues.filter((item) => item.level === 'error').length, 0)
  assert.ok(issues.some((item) => item.message.includes('targetType=99')))
  assert.ok(issues.some((item) => item.message.includes('logicType=999')))
})

test('多规则冲突：同一目标同一属性被不同规则设置时提醒「最后触发为准」', () => {
  const first = rule()
  const second = rule({
    id: 'rule-2',
    name: '状态=审批中',
    effectSuccess: [{ ruleId: 'rule-2', targetType: 4, targetId: BASE_SECTION, path: null, value: { readOnly: true } }],
  })
  const checked = checkLayoutRules(layout([first, second]))
  assert.equal(checked.errors.length, 0)
  assert.equal(checked.warnings.length, 1)
  assert.ok(checked.warnings[0]?.message.includes('最后触发的规则生效'))
})

test('停用的规则不参与冲突判断', () => {
  const first = rule()
  const second = rule({
    id: 'rule-2',
    name: '停用规则',
    enable: false,
    effectSuccess: [{ ruleId: 'rule-2', targetType: 4, targetId: BASE_SECTION, path: null, value: { readOnly: true } }],
  })
  assert.equal(checkLayoutRules(layout([first, second])).warnings.length, 0)
})
