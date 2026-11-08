/**
 * 查询层：清单去重要稳，草稿不能假装「已确认」。
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import type { CaptureRecord } from '../src/records.ts'
import { draftFrom, filterRecords, formatDraft, summarize } from '../src/query.ts'

function record(overrides: Partial<CaptureRecord>): CaptureRecord {
  return {
    v: 1,
    id: 's:1',
    sessionId: 's',
    at: 1_700_000_000_000,
    method: 'POST',
    url: 'https://standard-val.aksoegmp.com/api/platform/ObjectPicklist/save',
    host: 'standard-val.aksoegmp.com',
    path: '/api/platform/ObjectPicklist/save',
    pathTemplate: '/api/platform/ObjectPicklist/save',
    query: {},
    status: 200,
    mimeType: 'application/json',
    requestHeaders: {},
    ...overrides,
  }
}

test('清单：同 method + 模板 + 查询键名归并，状态分开计数', () => {
  const rows = summarize([
    record({}),
    record({ id: 's:2', status: 500 }),
    record({ id: 's:3', method: 'GET', path: '/api/platform/MenuGroup/QueryList', pathTemplate: '/api/platform/MenuGroup/QueryList' }),
  ])
  const post = rows.find((row) => row.method === 'POST')
  assert.ok(post !== undefined)
  assert.equal(post.count, 2)
  assert.deepEqual(post.statuses, { '200': 1, '500': 1 })
  const get = rows.find((row) => row.method === 'GET')
  assert.equal(get?.count, 1)
})

test('清单：查询键名不同就分开（同一路径不同参数形态）', () => {
  const rows = summarize([record({ query: { page: '1' } }), record({ id: 's:2', query: {} })])
  assert.equal(rows.length, 2)
})

test('过滤：method / grep / limit', () => {
  const rows = [record({ id: 'a' }), record({ id: 'b', method: 'GET', url: 'https://x/api/platform/y', pathTemplate: '/api/platform/y' })]
  assert.equal(filterRecords(rows, { method: 'get' }).length, 1)
  assert.equal(filterRecords(rows, { grep: 'picklist' }).length, 1)
  assert.equal(filterRecords(rows, { limit: 1 }).length, 1)
})

test('草稿：从请求体推参数类型，从响应体写 returns，并带「需人工确认」', () => {
  const recordWithBodies = record({
    query: { tab: 'main' },
    requestBody: {
      kind: 'json',
      preview: '{\n  "name": "akw_test",\n  "count": 3,\n  "enabled": true,\n  "options": ["a"]\n}',
      truncated: false,
      bytes: 80,
    },
    responseBody: { kind: 'json', preview: '{"code":0,"data":{"id":"x"},"message":"ok"}', truncated: false, bytes: 50 },
  })
  const draft = draftFrom(recordWithBodies)
  assert.equal(draft.method, 'POST')
  assert.equal(draft.path, '/api/platform/ObjectPicklist/save')
  assert.ok(draft.params.some((param) => param.name === 'name' && param.type === 'string' && param.in === 'body'))
  assert.ok(draft.params.some((param) => param.name === 'count' && param.type === 'number'))
  assert.ok(draft.params.some((param) => param.name === 'options' && param.type === 'array'))
  assert.ok(draft.params.some((param) => param.name === 'tab' && param.in === 'query'))
  assert.equal(draft.example.name, 'akw_test')
  assert.ok(draft.returns.includes('code'))
  assert.ok(draft.notes.includes('人工确认'))
})

test('草稿：路径占位符成 path 参数；截断的体要标注', () => {
  const draft = draftFrom(record({
    pathTemplate: '/api/platform/ObjectPicklist/{id}/field',
    requestBody: { kind: 'json', preview: '{"a":1}', truncated: true, bytes: 9999 },
  }))
  assert.ok(draft.params.some((param) => param.name === 'id' && param.in === 'path' && param.required))
  assert.ok(draft.notes.includes('截断'))
})

test('formatDraft：输出可直接粘贴的 TS 字面量', () => {
  const text = formatDraft(draftFrom(record({})))
  assert.ok(text.includes('id: "captured.save"'))
  assert.ok(text.includes('method: "POST"'))
  assert.ok(text.includes('params:'))
})
