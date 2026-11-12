/**
 * 「标准配置使用规范」与「系统常用映射表」解析器的判据。
 *
 * 两份文档都是卡片详情里的**运行时**数据源：改完刷新页面即生效，不重新 build。
 * 解析器坏了不会抛，只会让「标准配置用法」空着、或让动作类型表显示错的数字——
 * 所以这里既验手写样本，也验仓里的真文档。
 */
import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { test } from 'node:test'

import {
  actionGroupsFor,
  findActionMapPath,
  loadActionMap,
  parseActionMap,
  resetActionMapCache,
} from '../src/config/action-map.ts'
import {
  findRecipeBookPath,
  loadRecipeBook,
  parseRecipeBook,
  recipeCoverage,
  resetRecipeBookCache,
} from '../src/config/recipes.ts'
import { CAPABILITIES } from '../src/config/capabilities.ts'

const RECIPES = `
# 标准配置使用规范

## 已收录

### layout.save 保存对象布局

#### 方案 · 新增一个分区并放置控件

> 已有布局要加一块「基本信息」。

**步骤**
1. \`layout.detail\` —— 先读回当前布局。
2. \`layout.save\` —— 追加 section 与 control。

**校验**
- 回读 sections 数量 +1。

**示例**
\`\`\`json
{ "id": "<layoutId>" }
\`\`\`

**预期**
- data 是布局 id 字符串。

#### 方案 · 只改顺序

**步骤**
1. \`layout.detail\` —— 读回。
`

test('解析：一个接口多条方案，步骤里的接口 id 单独取出', () => {
  const { byCapability, warnings } = parseRecipeBook(RECIPES)
  assert.deepEqual(warnings, [])

  const list = byCapability.get('layout.save')
  assert.ok(list !== undefined)
  assert.equal(list.length, 2)
  const first = list[0]
  assert.ok(first !== undefined)
  assert.equal(first.title, '新增一个分区并放置控件')
  assert.equal(first.summary, '已有布局要加一块「基本信息」。')
  assert.equal(first.steps.length, 2)
  assert.equal(first.steps[0]?.api, 'layout.detail')
  assert.equal(first.steps[0]?.text, '先读回当前布局。')
  assert.equal(first.steps[1]?.api, 'layout.save')
  assert.deepEqual(first.checks, ['回读 sections 数量 +1。'])
  assert.deepEqual(first.payload, { id: '<layoutId>' })
  assert.equal(first.expected, 'data 是布局 id 字符串。')

  const second = list[1]
  assert.ok(second !== undefined)
  assert.equal(second.steps.length, 1)
})

test('解析：步骤续行并入上一条；没有步骤的方案留告警', () => {
  const { byCapability, warnings } = parseRecipeBook(`
### workflow.task.add 加审批节点

#### 方案 · 空方案

**步骤**
`)
  const list = byCapability.get('workflow.task.add')
  assert.ok(list !== undefined)
  assert.equal(list[0]?.steps.length, 0)
  assert.ok(warnings.some((item) => /步骤.*为空/.test(item)))
})

const ACTIONS = `
# 系统常用映射表

## 工作流步骤类型（stepType）

作用域：workflow

| 值 | 含义 | 备注 |
| --- | --- | --- |
| 30 | 任务节点（审批） | 参与者、按钮都在这里配 |
| 90 | 动作节点 | 自动执行 |

## 菜单动作类型（menuActionType）

作用域：menu / notice

| 值 | 含义 | 备注 |
| --- | --- | --- |
| 1 | 子菜单 | 挂对象与布局 |
`

test('解析：按分组读表，作用域可写多个', () => {
  const { groups, warnings } = parseActionMap(ACTIONS)
  assert.deepEqual(warnings, [])
  assert.equal(groups.length, 2)

  const step = groups[0]
  assert.ok(step !== undefined)
  assert.equal(step.name, '工作流步骤类型（stepType）')
  assert.deepEqual(step.scope, ['workflow'])
  assert.deepEqual(step.rows, [
    { value: '30', label: '任务节点（审批）', note: '参与者、按钮都在这里配' },
    { value: '90', label: '动作节点', note: '自动执行' },
  ])

  const menu = groups[1]
  assert.ok(menu !== undefined)
  assert.deepEqual(menu.scope, ['menu', 'notice'])
  assert.deepEqual(menu.rows, [{ value: '1', label: '子菜单', note: '挂对象与布局' }])
})

test('解析：缺分隔行只告警，仍按数据行读', () => {
  const { groups, warnings } = parseActionMap(`
## 某组

| 值 | 含义 |
| 7 | 触发工作流 |
`)
  assert.equal(groups[0]?.rows.length, 1)
  assert.ok(warnings.some((item) => /分隔行/.test(item)))
})

test('过滤：只给当前接口域相关的分组', () => {
  const { groups } = parseActionMap(ACTIONS)
  const map = { path: null, displayPath: null, mtimeMs: 0, groups, warnings: [] }
  const forWorkflow = actionGroupsFor(map, 'workflow')
  assert.deepEqual(forWorkflow.map((group) => group.name), ['工作流步骤类型（stepType）'])
  const forNotice = actionGroupsFor(map, 'notice')
  assert.deepEqual(forNotice.map((group) => group.name), ['菜单动作类型（menuActionType）'])
})

test('真文档：两份都在、零告警、方案挂在真实能力 id 上', () => {
  resetRecipeBookCache()
  resetActionMapCache()

  const recipePath = findRecipeBookPath()
  assert.ok(recipePath !== null && existsSync(recipePath), 'docs/knowledge/标准配置使用规范.md 应存在')
  const book = loadRecipeBook()
  assert.deepEqual(book.warnings, [])
  const coverage = recipeCoverage(book)
  assert.equal(coverage.total, CAPABILITIES.length)
  assert.ok(coverage.recipes >= 1, '至少要有一个标准配置方案当样板')
  const ids = new Set(CAPABILITIES.map((item) => item.id))
  for (const id of book.byCapability.keys()) assert.ok(ids.has(id), `${id} 不在能力目录`)

  const actionPath = findActionMapPath()
  assert.ok(actionPath !== null && existsSync(actionPath), 'docs/knowledge/系统常用映射表.md 应存在')
  const actionMap = loadActionMap()
  assert.deepEqual(actionMap.warnings, [])
  assert.ok(actionMap.groups.length >= 5, '系统常用映射表至少覆盖步骤类型 / 状态 / 行为类型等几组')
  assert.ok(
    actionMap.groups.filter((group) => group.rows.length > 0).length >= 4,
    '至少四组有真实映射行（空组是「待补」占位，允许存在）',
  )
})
