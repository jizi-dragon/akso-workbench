/**
 * 「基础API使用指南」解析器的判据。
 *
 * 这份文档是工具卡片上**唯一**的文案来源，解析错了不会抛异常，只会让卡片说错话：
 *   · 案例丢了 ⇒ 用户看不到怎么调；
 *   · 介绍没解析出来 ⇒ 卡片显示空描述；
 *   · 章节漏登记 ⇒ 界面悄悄回落到旧目录文案，没人发现文档已经和接口脱节。
 *
 * 结构约定：**一条接口只有一个案例**；需要多步骤的配置流程写进「标准配置使用规范.md」。
 * 所以文档里多写案例时解析器只留第一条，并给出告警。
 */
import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { test } from 'node:test'

import {
  applyGuide,
  findApiGuidePath,
  guideCoverage,
  loadApiGuide,
  parseApiGuide,
  resetApiGuideCache,
} from '../src/config/api-guide.ts'
import { CAPABILITIES } from '../src/config/capabilities.ts'

const SAMPLE = `
# 基础 API 使用指南

## 接口章节

### object.save 新建 / 更新对象

> 按 code upsert 一个自定义对象。
> 第二行介绍。

**返回**：data: true（不返回 objectId）。

**要点**

- code 必须 __c 结尾。
- 新建对象不会自带字段。

**案例**

#### 案例 · 最小创建

只带必填字段。

\`\`\`json
{
  "name": "变更控制",
  "code": "change_control__c"
}
\`\`\`

预期：code 为 0。

### query.object.get 查询对象

> 按 objectId 取单条。

**案例**

#### 唯一案例

\`\`\`json
{ "objectId": "abc" }
\`\`\`
`

test('解析：介绍 / 返回 / 要点 / 案例', () => {
  const { entries, warnings } = parseApiGuide(SAMPLE)
  assert.deepEqual(warnings, [])

  const save = entries.get('object.save')
  assert.ok(save !== undefined)
  assert.equal(save.intro, '按 code upsert 一个自定义对象。 第二行介绍。')
  assert.equal(save.returns, 'data: true（不返回 objectId）。')
  assert.deepEqual(save.notes, ['code 必须 __c 结尾。', '新建对象不会自带字段。'])
  assert.ok(save.case !== null)
  assert.equal(save.case.title, '案例 · 最小创建')
  assert.equal(save.case.note, '只带必填字段。')
  assert.deepEqual(save.case.params, { name: '变更控制', code: 'change_control__c' })
  assert.equal(save.case.expected, 'code 为 0。')

  const get = entries.get('query.object.get')
  assert.ok(get?.case !== null && get?.case !== undefined)
  assert.deepEqual(get.case.params, { objectId: 'abc' })
})

test('解析：多写了案例只留第一条并告警', () => {
  const { entries, warnings } = parseApiGuide(`
### object.save 新建 / 更新对象

**案例**

#### 案例 · 第一条

\`\`\`json
{ "a": 1 }
\`\`\`

#### 案例 · 第二条

\`\`\`json
{ "b": 2 }
\`\`\`
`)
  const save = entries.get('object.save')
  assert.ok(save?.case !== null && save?.case !== undefined)
  assert.deepEqual(save.case.params, { a: 1 }, '只保留第一条')
  assert.ok(warnings.some((item) => /只保留第一条/.test(item)), '多案例要提醒写到标准配置使用规范')
})

test('解析：坏 JSON 与缺案例只告警，不炸', () => {
  const broken = parseApiGuide(`
### bad.json 坏例子

**案例**

#### 案例 · 坏了

\`\`\`json
{ "a": }
\`\`\`
`)
  assert.equal(broken.entries.get('bad.json')?.case, null, '解析不了的案例要跳过')
  assert.ok(broken.warnings.some((item) => /JSON 解析失败/.test(item)), '坏 JSON 要留告警')
  assert.ok(broken.warnings.some((item) => /没有可用的 JSON 参数块/.test(item)))

  const noCase = parseApiGuide(`
### no.case 没写案例

**案例**
`)
  assert.equal(noCase.entries.get('no.case')?.case, null)
  assert.match(noCase.warnings[0] ?? '', /没有 #### 案例标题/)
})

test('解析：重复章节后者覆盖', () => {
  const { entries, warnings } = parseApiGuide(`
### dup.id 第一版

> 旧介绍

### dup.id 第二版

> 新介绍
`)
  assert.equal(entries.get('dup.id')?.intro, '新介绍')
  assert.match(warnings[0] ?? '', /章节重复/)
})

test('合并：指南缺项时回落到目录', () => {
  const { entries } = parseApiGuide(SAMPLE)
  const target = CAPABILITIES.find((item) => item.id === 'object.save')
  assert.ok(target !== undefined)

  const guided = applyGuide(target, entries.get('object.save'))
  assert.equal(guided.description, '按 code upsert 一个自定义对象。 第二行介绍。')
  assert.equal(guided.documented, true)
  assert.deepEqual(guided.case.params, { name: '变更控制', code: 'change_control__c' })

  const fallback = applyGuide(target, undefined)
  assert.equal(fallback.documented, false)
  assert.equal(fallback.description, target.description)
  assert.deepEqual(fallback.case.params, target.example, '没有指南时回落到目录里的 example')
})

test('真文档：找得到、零告警、每条接口都有章节且恰好一条案例', () => {
  resetApiGuideCache()
  const path = findApiGuidePath()
  assert.ok(path !== null && existsSync(path), 'docs/knowledge/基础API使用指南.md 应存在')

  const guide = loadApiGuide()
  assert.deepEqual(guide.warnings, [])
  assert.equal(guide.entries.size, CAPABILITIES.length)
  assert.deepEqual(guideCoverage(guide), { total: CAPABILITIES.length, documented: CAPABILITIES.length })

  for (const capability of CAPABILITIES) {
    const entry = guide.entries.get(capability.id)
    assert.ok(entry !== undefined, `${capability.id} 缺少章节`)
    assert.ok(entry.case !== null, `${capability.id} 至少要有一条案例`)
    assert.ok(entry.case.params !== undefined, `${capability.id} 的案例要有 JSON 参数`)
  }
})
