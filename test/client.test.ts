/**
 * eGMP 客户端的判据：**失败必须被看见**。
 *
 * 这一层的失效方式全是静默的：
 *   · 信封 `code != 0` 被当成功 ⇒ 界面显示"没有数据"；
 *   · 分页 `hasNext` 恒 true ⇒ 无限请求（或被截断成半截数据）；
 *   · token 缓存不分键 ⇒ 把 A 环境的令牌发到 B 环境（偶发 401）。
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { ApiError, EgmpClient, MemoryTokenStore, MAX_PAGES, login, trimBase } from '../src/egmp/client.ts'

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
}

test('trimBase 去掉尾部斜杠', () => {
  assert.equal(trimBase('https://a.example.com//'), 'https://a.example.com')
  assert.equal(trimBase(' https://a.example.com '), 'https://a.example.com')
})

test('登录：data 是字符串 token，且带 Bearer 前会存进 store', async () => {
  const calls: Array<{ url: string; body: unknown }> = []
  const fake = (async (url: string, init?: RequestInit) => {
    calls.push({ url: String(url), body: JSON.parse(String(init?.body)) })
    return jsonResponse({ code: 0, data: 'jwt-token-value' })
  }) as unknown as typeof fetch

  const store = new MemoryTokenStore()
  const token = await login('https://p.example.com/', 'u1', 'pw', { fetchImpl: fake, store })
  assert.equal(token, 'jwt-token-value')
  assert.equal(calls.length, 1)
  assert.equal(calls[0]!.url, 'https://p.example.com/api/openapi/v1.0/Auth')
  assert.deepEqual(calls[0]!.body, { account: 'u1', password: 'pw' })

  // 第二次走缓存（不再发请求）
  const again = await login('https://p.example.com', 'u1', 'pw', { fetchImpl: fake, store })
  assert.equal(again, 'jwt-token-value')
  assert.equal(calls.length, 1)
})

test('登录：信封 code != 0 ⇒ ApiError（HTTP 200 也不算成功）', async () => {
  const fake = (async () => jsonResponse({ code: 40101, message: '账号或密码错误' })) as unknown as typeof fetch
  await assert.rejects(
    () => login('https://p.example.com', 'u1', 'bad', { fetchImpl: fake }),
    (error: unknown) => error instanceof ApiError && error.code === 40101,
  )
})

test('登录：data 不是字符串（平台改了形状）⇒ ApiError，不返回 undefined', async () => {
  const fake = (async () => jsonResponse({ code: 0, data: { token: 'x' } })) as unknown as typeof fetch
  await assert.rejects(
    () => login('https://p.example.com', 'u1', 'pw', { fetchImpl: fake }),
    (error: unknown) => error instanceof ApiError,
  )
})

test('GET：带 Authorization 头；信封 code != 0 ⇒ ApiError', async () => {
  const seen: Array<{ auth: string | undefined; url: string }> = []
  const fake = (async (url: string, init?: RequestInit) => {
    const headers = init?.headers as Record<string, string>
    seen.push({ auth: headers.Authorization, url: String(url) })
    return jsonResponse({ code: 250, message: '对象不存在' })
  }) as unknown as typeof fetch

  const client = new EgmpClient('https://p.example.com', 'tok', { fetchImpl: fake })
  await assert.rejects(
    () => client.get('/api/x', { a: 1, b: '中' }),
    (error: unknown) => error instanceof ApiError && error.code === 250,
  )
  assert.equal(seen[0]!.auth, 'Bearer tok')
  assert.equal(seen[0]!.url, 'https://p.example.com/api/x?a=1&b=%E4%B8%AD')
})

test('分页：datas/items 兼容 + hasNext 续页 + 到上限抛错', async () => {
  let page = 0
  const fake = (async (_url: string, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body)) as { pageIndex: number }
    page = body.pageIndex
    if (body.pageIndex === 1) return jsonResponse({ code: 0, data: { datas: [{ id: 1 }], hasNext: true } })
    if (body.pageIndex === 2) return jsonResponse({ code: 0, data: { items: [{ id: 2 }], hasNext: false } })
    return jsonResponse({ code: 0, data: { datas: [], hasNext: false } })
  }) as unknown as typeof fetch

  const client = new EgmpClient('https://p.example.com', 'tok', { fetchImpl: fake })
  const rows = await client.pagedPost('/api/list', { q: 'x' }, 100)
  assert.deepEqual(rows, [{ id: 1 }, { id: 2 }])
  assert.equal(page, 2)
})

test('分页：hasNext 恒为 true ⇒ 到 MAX_PAGES 抛错（不返回半截）', async () => {
  let calls = 0
  const fake = (async () => {
    calls += 1
    return jsonResponse({ code: 0, data: { datas: [{ id: calls }], hasNext: true } })
  }) as unknown as typeof fetch

  const client = new EgmpClient('https://p.example.com', 'tok', { fetchImpl: fake })
  await assert.rejects(
    () => client.pagedPost('/api/list'),
    (error: unknown) => error instanceof ApiError && error.code === 'pagination',
  )
  assert.equal(calls, MAX_PAGES)
})
