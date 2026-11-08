/**
 * 路径模板决定「接口清单去重」是否可信：
 * 折多了会把不同接口并成一个，折少了同一接口散成几十行。
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { hostAllowed, normalizePath, parseQuery, parseUrl, pathTemplate, querySignature } from '../src/paths.ts'

test('路径模板：UUID/数字/长 hex 折成 {id}', () => {
  assert.equal(
    pathTemplate('/api/platform/BasicObject/265e232d-6cae-6a90-b580-1ca742a300f4'),
    '/api/platform/BasicObject/{id}',
  )
  assert.equal(pathTemplate('/api/platform/layout/42'), '/api/platform/layout/{id}')
  assert.equal(pathTemplate('/api/platform/x/6710c8f1a2b3c4d5e6f7a8b9c0d1e2f3'), '/api/platform/x/{id}')
})

test('路径模板：接口名与短 code 不折', () => {
  assert.equal(pathTemplate('/api/platform/MenuGroup/QueryList'), '/api/platform/MenuGroup/QueryList')
  assert.equal(pathTemplate('/api/platform/ObjectPicklist/save'), '/api/platform/ObjectPicklist/save')
  assert.equal(pathTemplate('/api/platform/field/page'), '/api/platform/field/page')
})

test('normalizePath：折叠重复斜杠与尾部斜杠', () => {
  assert.equal(normalizePath('//api//platform//x/'), '/api/platform/x')
  assert.equal(normalizePath(''), '/')
  assert.equal(normalizePath('/'), '/')
})

test('查询参数：键保留，敏感值脱敏', () => {
  const query = parseQuery('?page=1&pageSize=20&token=abc123&access_token=zzz')
  assert.deepEqual(query, { page: '1', pageSize: '20', token: '«redacted»', access_token: '«redacted»' })
})

test('查询签名只看键名且有序', () => {
  assert.equal(querySignature({ b: '2', a: '1' }), 'a&b')
  assert.equal(querySignature({}), '')
})

test('parseUrl：只接受 http(s)，带出模板与查询', () => {
  const parsed = parseUrl('https://standard-val.aksoegmp.com/api/platform/x/265e232d-6cae-6a90-b580-1ca742a300f4?tab=1')
  assert.ok(parsed !== null)
  assert.equal(parsed.host, 'standard-val.aksoegmp.com')
  assert.equal(parsed.pathTemplate, '/api/platform/x/{id}')
  assert.deepEqual(parsed.query, { tab: '1' })
  assert.equal(parseUrl('ws://example.com/a'), null)
  assert.equal(parseUrl('not a url'), null)
})

test('host 白名单：后缀匹配，null 表示不限', () => {
  assert.equal(hostAllowed('standard-val.aksoegmp.com', ['aksoegmp.com']), true)
  assert.equal(hostAllowed('aksoegmp.com', ['aksoegmp.com']), true)
  assert.equal(hostAllowed('evil-aksoegmp.com', ['aksoegmp.com']), false)
  assert.equal(hostAllowed('anything.example.com', null), true)
})
