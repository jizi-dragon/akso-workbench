/**
 * 脱敏是这个工具的底线：捕获的是真实会话流量，落盘前必须证明
 * 「令牌/口令原文不会出现在 JSONL 里」。
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { MAX_BODY, bodyPreview, redactHeaders, redactJsonValue, redactText } from '../src/redact.ts'

test('头部：Authorization/Cookie 只留长度，普通头保留', () => {
  const headers = redactHeaders({
    Authorization: 'Bearer abcdefghijklmnop',
    Cookie: 'sid=1',
    'Content-Type': 'application/json',
    'X-Akso-Client': 'akso-workbench',
  })
  assert.equal(headers.Authorization, '«redacted:len=23»')
  assert.equal(headers.Cookie, '«redacted:len=5»')
  assert.equal(headers['Content-Type'], 'application/json')
  assert.equal(headers['X-Akso-Client'], 'akso-workbench')
})

test('文本：JWT 与 Bearer 就地打码', () => {
  const jwt = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop'
  const text = `token=${jwt} header=Bearer sk-abcdefghijklmnop`
  const masked = redactText(text)
  assert.equal(masked.includes(jwt), false)
  assert.equal(masked.includes('sk-abcdefghijklmnop'), false)
  assert.equal(masked.includes('«jwt»'), true)
})

test('JSON 体：敏感键换占位符，结构保留', () => {
  const value = redactJsonValue({
    account: 'liyulong',
    password: 'plain-text',
    nested: { token: 'abc', count: 3 },
    list: [{ secret: 'x' }],
  }) as Record<string, unknown>
  assert.equal(value.account, 'liyulong')
  assert.equal(value.password, '«redacted»')
  assert.deepEqual(value.nested, { token: '«redacted»', count: 3 })
  assert.deepEqual(value.list, [{ secret: '«redacted»' }])
})

test('体预览：JSON 解析后脱敏并 pretty-print', () => {
  const preview = bodyPreview('{"account":"u1","password":"p@ss","options":[1,2]}', 'application/json')
  assert.ok(preview !== undefined)
  assert.equal(preview.kind, 'json')
  assert.equal(preview.preview.includes('p@ss'), false)
  assert.equal(preview.preview.includes('«redacted»'), true)
  assert.equal(preview.truncated, false)
  assert.equal(preview.bytes, Buffer.byteLength('{"account":"u1","password":"p@ss","options":[1,2]}'))
})

test('体预览：超长文本截断到 MAX_BODY', () => {
  const raw = 'x'.repeat(MAX_BODY + 100)
  const preview = bodyPreview(raw, 'text/plain')
  assert.ok(preview !== undefined)
  assert.equal(preview.truncated, true)
  assert.equal(preview.preview.length, MAX_BODY)
})

test('体预览：坏的 JSON 回落成文本，不丢内容也不崩', () => {
  const preview = bodyPreview('{"a":1,', 'application/json')
  assert.ok(preview !== undefined)
  assert.equal(preview.kind, 'text')
  assert.equal(preview.preview, '{"a":1,')
})

test('体预览：空体返回 undefined', () => {
  assert.equal(bodyPreview(undefined), undefined)
  assert.equal(bodyPreview(''), undefined)
})
