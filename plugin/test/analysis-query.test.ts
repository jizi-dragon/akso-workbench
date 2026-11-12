import assert from 'node:assert/strict'
import test from 'node:test'

import { MAX_ANALYSIS_OBJECTS, parseAnalysisQueries } from '../src/shared/analysis-query.ts'

test('多对象输入：中英文逗号、空格、换行都能拆开', () => {
  assert.deepEqual(parseAnalysisQueries('homework2__c, audit_plan__c，change_control__c'), {
    queries: ['homework2__c', 'audit_plan__c', 'change_control__c'],
    error: '',
  })
  assert.deepEqual(parseAnalysisQueries('homework2__c\naudit_plan__c'), {
    queries: ['homework2__c', 'audit_plan__c'],
    error: '',
  })
})

test('多对象输入：去重但不改原顺序', () => {
  assert.deepEqual(parseAnalysisQueries('homework2__c, HOMEWORK2__c, audit_plan__c').queries, [
    'homework2__c',
    'audit_plan__c',
  ])
})

test('多对象输入：空输入给出提示', () => {
  assert.equal(parseAnalysisQueries('  ，; ').error, '请输入对象 id 或 code')
})

test('多对象输入：最多三个，超出时不截断并报错', () => {
  const four = 'a__c,b__c,c__c,d__c'
  const parsed = parseAnalysisQueries(four)
  assert.equal(parsed.queries.length, 4)
  assert.equal(parsed.error, `一次最多分析 ${MAX_ANALYSIS_OBJECTS} 个对象（当前 4 个）`)

  assert.equal(parseAnalysisQueries('a__c,b__c,c__c').error, '')
})
