/**
 * host 半边的判据。
 *
 * 这一层失败时**不会抛异常**，只会让界面/agent 开始说假话：
 *   · 围栏漏了 ⇒ 局域网里任何人都能读账号库（而且能替你登录）；
 *   · 令牌漏进响应 ⇒ 浏览器里任何脚本都能拿到它；
 *   · 写操作后不回拉 ⇒ 站点页与账号页各显示一份，数量对不上；
 *   · 登录失败被当成"会话失效" ⇒ 用户被莫名其妙登出；
 *   · 设备流的 deviceCode 漏给浏览器 ⇒ 少了一道"拿码还需要本地进程"的边界。
 *
 * 因此这里不 mock 业务函数：起一个**假 akso-cloud**（真 HTTP + 真 revision + 真设备流），
 * 用手搓的 req/res 打真实 handler —— 与线上链路同形。
 */

import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { existsSync, mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

import { errorMessage, uiState } from '../src/cloud.ts'
import { apply } from '../src/index.ts'
import { buildSnapshotTool } from '../src/tool.ts'
import { startFakeCloud } from './fake-cloud.mjs'

interface Captured {
  path: string
  handler: (req: unknown, res: unknown) => void
}

const P = {
  state: '/api/akso-workbench/state',
  login: '/api/akso-workbench/cloud/login',
  logout: '/api/akso-workbench/cloud/logout',
  sync: '/api/akso-workbench/cloud/sync',
  deviceStart: '/api/akso-workbench/cloud/device/start',
  devicePoll: '/api/akso-workbench/cloud/device/poll',
  accountCreate: '/api/akso-workbench/accounts/create',
  accountCurrent: '/api/akso-workbench/accounts/current',
  siteCreate: '/api/akso-workbench/sites/create',
  boxRename: '/api/akso-workbench/boxes/rename',
  boxBind: '/api/akso-workbench/boxes/bind-site',
  health: '/api/akso-workbench/health',
}

interface Mounted {
  routes: Map<string, Captured['handler']>
  disposers: Array<() => void>
  opened: string[]
  tools: Array<{ name: string; execute: (args: unknown, exec?: unknown) => Promise<unknown> }>
}

function mount(dshHome: string, cloudUrl?: string | undefined, withTools = false): Mounted {
  const routes = new Map<string, Captured['handler']>()
  const disposers: Array<() => void> = []
  const opened: string[] = []
  const tools: Mounted['tools'] = []
  const ctx = {
    logger: { warn: () => {} },
    effect: (fn: () => () => void) => {
      disposers.push(fn())
      return () => {}
    },
    get: (name: string) => (name === 'tools' && withTools
      ? { register: (definition: Mounted['tools'][number]) => { tools.push(definition); return () => {} } }
      : undefined),
    webServer: {
      register: (route: Captured) => {
        routes.set(route.path, route.handler)
        return () => routes.delete(route.path)
      },
    },
  }
  apply(ctx as never, { dshHome, cloudBaseUrl: cloudUrl, openBrowser: (url: string) => opened.push(url) })
  return { routes, disposers, opened, tools }
}

function makeReq(method: string, body?: unknown, remote = '127.0.0.1') {
  const emitter = new EventEmitter() as EventEmitter & Record<string, unknown>
  emitter.method = method
  emitter.socket = { remoteAddress: remote }
  emitter.destroy = () => {}
  queueMicrotask(() => {
    if (body !== undefined) emitter.emit('data', Buffer.from(JSON.stringify(body)))
    emitter.emit('end')
  })
  return emitter
}

function makeRes() {
  const out = {
    statusCode: 200,
    headers: {} as Record<string, string>,
    body: '',
    notify: undefined as undefined | (() => void),
    setHeader(name: string, value: string) {
      out.headers[name] = value
    },
    end(chunk?: string) {
      out.body = chunk ?? ''
      out.notify?.()
    },
  }
  return out
}

async function call(
  routes: Map<string, Captured['handler']>,
  path: string,
  method: string,
  body?: unknown,
  remote?: string,
) {
  const handler = routes.get(path)
  assert.ok(handler, `路由未注册：${path}`)
  const response = makeRes()
  const done = new Promise<void>((resolve) => {
    response.notify = resolve
  })
  handler(makeReq(method, body, remote), response)
  await done
  const text = response.body
  return { status: response.statusCode, json: JSON.parse(text === '' ? '{}' : text) as Record<string, unknown>, raw: text }
}

const freshHome = () => mkdtempSync(join(tmpdir(), 'akso-wb-'))

async function loginWith(routes: Map<string, Captured['handler']>) {
  const res = await call(routes, P.login, 'POST', { email: 'qa@example.com', password: 'pw' })
  assert.equal(res.status, 200, `登录失败：${res.raw}`)
  return res
}

test('注册了 12 条路由，卸载时全部摘掉', () => {
  const { routes, disposers } = mount(freshHome())
  assert.deepEqual([...routes.keys()].sort(), Object.values(P).sort())
  for (const dispose of disposers) dispose()
  assert.equal(routes.size, 0)
})

test('禁止删除：账号 / 站点 / 盒子的删除端点都不存在', () => {
  const { routes } = mount(freshHome())
  for (const path of [
    '/api/akso-workbench/accounts/delete',
    '/api/akso-workbench/sites/delete',
    '/api/akso-workbench/boxes/delete',
  ]) {
    assert.equal(routes.has(path), false, `不该注册删除端点：${path}`)
  }
})
test('围栏：非 loopback 一律 403', async () => {
  const { routes } = mount(freshHome())
  assert.equal((await call(routes, P.state, 'GET', undefined, '10.1.2.3')).status, 403)
  assert.equal((await call(routes, P.login, 'POST', { email: 'a', password: 'b' }, '192.168.0.8')).status, 403)
})

test('方法不对是 405', async () => {
  const { routes } = mount(freshHome())
  assert.equal((await call(routes, P.login, 'GET')).status, 405)
})

test('未登录：loggedIn=false，数组为空', async () => {
  const { routes } = mount(freshHome())
  const res = await call(routes, P.state, 'GET')
  assert.equal(res.status, 200)
  assert.equal(res.json.loggedIn, false)
  assert.deepEqual(res.json.accounts, [])
  assert.equal(res.json.revision, null)
})

test('登录：拿到快照，但**响应里不含令牌**；服务地址不接受客户端传参', async () => {
  const cloud = await startFakeCloud()
  try {
    const home = freshHome()
    const { routes } = mount(home, cloud.url)
    // 即使有人硬塞 baseUrl，也必须被忽略（地址由 host 固定）
    const res = await call(routes, P.login, 'POST', {
      email: 'qa@example.com',
      password: 'pw',
      baseUrl: 'http://evil.example.com',
    })
    assert.equal(res.status, 200)
    assert.equal(res.json.loggedIn, true)
    assert.equal((res.json.profile as Record<string, unknown>).email, 'qa@example.com')
    assert.equal(res.json.revision, 0)
    assert.ok(!res.raw.includes('tok-'), '响应体里出现了令牌')

    const stored = JSON.parse(readFileSync(join(home, 'akso-workbench', 'cloud.json'), 'utf8')) as {
      token?: string
      baseUrl?: string
    }
    assert.ok(typeof stored.token === 'string' && stored.token.startsWith('tok-'))
    assert.equal(stored.baseUrl, cloud.url, '会话里必须记固定地址，而不是客户端塞进来的那个')
  } finally {
    await cloud.close()
  }
})

test('登录失败：400 + 可读原因（线上是 detail），且不落会话', async () => {
  const cloud = await startFakeCloud()
  try {
    const home = freshHome()
    const { routes } = mount(home, cloud.url)
    const res = await call(routes, P.login, 'POST', { email: 'qa@example.com', password: 'wrong' })
    assert.equal(res.status, 400)
    assert.match(String(res.json.error), /邮箱或密码/)
    assert.equal(existsSync(join(home, 'akso-workbench', 'cloud.json')), false)
  } finally {
    await cloud.close()
  }
})

test('设备流：start 返回码与链接、**不返回 deviceCode**，并唤起一次浏览器', async () => {
  const cloud = await startFakeCloud()
  try {
    const { routes, opened } = mount(freshHome(), cloud.url)
    const started = await call(routes, P.deviceStart, 'POST', {})
    assert.equal(started.status, 200)
    const userCode = String(started.json.userCode)
    const verificationUrl = String(started.json.verificationUrl)
    assert.match(userCode, /^CODE-/)
    assert.match(verificationUrl, /\/authorize\?code=/)
    assert.ok(!started.raw.includes('deviceCode'), 'deviceCode 不该下发浏览器')
    assert.deepEqual(opened, [verificationUrl], '必须用同一个链接唤起浏览器')
  } finally {
    await cloud.close()
  }
})

test('设备流：批准前 pending，批准后 approved + 已同步', async () => {
  const cloud = await startFakeCloud()
  try {
    const home = freshHome()
    const { routes } = mount(home, cloud.url)
    const started = await call(routes, P.deviceStart, 'POST', {})
    const userCode = String(started.json.userCode)

    const before = await call(routes, P.devicePoll, 'POST', {})
    assert.equal(before.json.status, 'pending')

    assert.equal(cloud.approve(userCode, 'device@example.com'), true)
    const after = await call(routes, P.devicePoll, 'POST', {})
    assert.equal(after.json.status, 'approved')
    const state = after.json.state as Record<string, unknown>
    assert.equal(state.loggedIn, true)
    assert.equal((state.profile as Record<string, unknown>).email, 'device@example.com')

    // 会话已落盘；再读 state 也能读到
    const stored = JSON.parse(readFileSync(join(home, 'akso-workbench', 'cloud.json'), 'utf8')) as { token?: string }
    assert.ok(typeof stored.token === 'string' && stored.token.startsWith('tok-device-'))
    assert.equal((await call(routes, P.state, 'GET')).json.loggedIn, true)

    // 令牌只发一次：再 poll 是 unknown（pendingDevice 已清空）
    assert.equal((await call(routes, P.devicePoll, 'POST', {})).json.status, 'unknown')
  } finally {
    await cloud.close()
  }
})

test('站点 → 账号：建完立刻读回，revision 递增，账号落进默认盒', async () => {
  const cloud = await startFakeCloud()
  try {
    const { routes } = mount(freshHome(), cloud.url)
    await loginWith(routes)

    const site = await call(routes, P.siteCreate, 'POST', {
      name: '通桥',
      baseUrl: 'https://tonbridge-config.aksoegmp.com',
    })
    assert.equal(site.status, 200)
    assert.equal((site.json.counts as Record<string, number>).sites, 1)
    const siteId = (site.json.sites as Array<{ id: string }>)[0]!.id

    const created = await call(routes, P.accountCreate, 'POST', { siteId, username: 'qa.admin', password: 'pw', note: '只读' })
    assert.equal(created.status, 200)
    const accounts = created.json.accounts as Array<Record<string, unknown>>
    assert.equal(accounts.length, 1)
    assert.equal(accounts[0]!.username, 'qa.admin')
    assert.equal(accounts[0]!.box, '默认盒')
    assert.ok(Number(created.json.revision) > Number(site.json.revision))
  } finally {
    await cloud.close()
  }
})

test('缺字段的写请求在 host 侧就被挡掉（不去污染云端）', async () => {
  const cloud = await startFakeCloud()
  try {
    const { routes } = mount(freshHome(), cloud.url)
    await loginWith(routes)
    assert.equal((await call(routes, P.accountCreate, 'POST', { siteId: 'x', username: 'u', password: '' })).status, 400)
    assert.equal((await call(routes, P.siteCreate, 'POST', { name: 'x', baseUrl: '' })).status, 400)
  } finally {
    await cloud.close()
  }
})

test('连接偏好落盘：写 prefs.json，传空 id 可断开', async () => {
  const cloud = await startFakeCloud()
  try {
    const home = freshHome()
    const { routes } = mount(home, cloud.url)
    await loginWith(routes)
    const site = await call(routes, P.siteCreate, 'POST', { baseUrl: 'https://a.example.com' })
    const siteId = (site.json.sites as Array<{ id: string }>)[0]!.id
    const created = await call(routes, P.accountCreate, 'POST', { siteId, username: 'u1', password: 'pw' })
    const accountId = (created.json.accounts as Array<{ id: string }>)[0]!.id

    const set = await call(routes, P.accountCurrent, 'POST', { id: accountId })
    assert.equal(set.json.currentAccountId, accountId)
    const prefs = JSON.parse(readFileSync(join(home, 'akso-workbench', 'prefs.json'), 'utf8')) as { currentAccountId?: string }
    assert.equal(prefs.currentAccountId, accountId)

    // 断开连接：id 传空 = 清除
    const cleared = await call(routes, P.accountCurrent, 'POST', { id: '' })
    assert.equal(cleared.status, 200)
    assert.equal(cleared.json.currentAccountId, null)
  } finally {
    await cloud.close()
  }
})

test('盒子改名：账号的 box 跟着变（同一份快照里两处一致）', async () => {
  const cloud = await startFakeCloud()
  try {
    const { routes } = mount(freshHome(), cloud.url)
    await loginWith(routes)
    const site = await call(routes, P.siteCreate, 'POST', { name: '通桥', baseUrl: 'https://a.example.com' })
    const siteId = (site.json.sites as Array<{ id: string }>)[0]!.id
    const boxName = (site.json.boxes as Array<{ name: string }>).find((b) => b.name === '通桥')!.name
    await call(routes, P.accountCreate, 'POST', { siteId, username: 'u1', password: 'pw', box: boxName })

    const renamed = await call(routes, P.boxRename, 'POST', { source: boxName, target: '通桥 · 生产' })
    assert.equal(renamed.status, 200)
    const accounts = renamed.json.accounts as Array<Record<string, unknown>>
    assert.equal(accounts[0]!.box, '通桥 · 生产')
    assert.ok((renamed.json.boxes as Array<Record<string, unknown>>).some((b) => b.name === '通桥 · 生产'))
  } finally {
    await cloud.close()
  }
})

test('一致性：云端在别处被改了，一次 sync 后 host 看到的就是新数据', async () => {
  const cloud = await startFakeCloud()
  try {
    const { routes } = mount(freshHome(), cloud.url)
    await loginWith(routes)
    const site = await call(routes, P.siteCreate, 'POST', { baseUrl: 'https://a.example.com' })
    const siteId = (site.json.sites as Array<{ id: string }>)[0]!.id
    await call(routes, P.accountCreate, 'POST', { siteId, username: 'u1', password: 'pw' })

    cloud.state.accounts.unshift({
      id: 'acc-elsewhere',
      siteId,
      siteName: 'a.example.com',
      siteHost: 'a.example.com',
      baseUrl: 'https://a.example.com',
      username: 'u2',
      tabName: '',
      role: 'user',
      tags: [],
      note: '',
      status: 'active',
      box: '默认盒',
      scheme: '',
      hasPassword: true,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    })
    cloud.state.revision++

    const before = await call(routes, P.state, 'GET')
    assert.equal((before.json.accounts as unknown[]).length, 1, '没同步前不该自己变')

    const after = await call(routes, P.sync, 'POST')
    assert.equal(after.status, 200)
    assert.equal((after.json.accounts as unknown[]).length, 2)
    assert.equal(after.json.revision, cloud.state.revision)
  } finally {
    await cloud.close()
  }
})

test('退出登录：会话清空，state 回到未登录', async () => {
  const cloud = await startFakeCloud()
  try {
    const home = freshHome()
    const { routes } = mount(home, cloud.url)
    await loginWith(routes)
    const out = await call(routes, P.logout, 'POST', {})
    assert.equal(out.status, 200)
    assert.equal(out.json.loggedIn, false)
    assert.equal(JSON.parse(readFileSync(join(home, 'akso-workbench', 'cloud.json'), 'utf8')), null)
    assert.equal((await call(routes, P.state, 'GET')).json.loggedIn, false)
  } finally {
    await cloud.close()
  }
})

/* ─────────────────── 错误体解析（线上是 detail，不是 error） ─────────────────── */

test('errorMessage：detail 字符串 / detail 对象 / 都没有时回落 HTTP 状态', () => {
  assert.equal(errorMessage({ detail: '邮箱或密码不正确' }, 401), '邮箱或密码不正确')
  assert.equal(errorMessage({ detail: { message: '站点下还有账号' } }, 409), '站点下还有账号')
  assert.equal(errorMessage({ error: '老形状' }, 400), '老形状')
  assert.equal(errorMessage({ message: '另一种后端' }, 400), '另一种后端')
  assert.equal(errorMessage({}, 500), 'HTTP 500')
})

test('工具经 ctx.tools 真注册，且读到的是**同一条链路**的数据（cloud → 快照 → 工具）', async () => {
  const cloud = await startFakeCloud()
  try {
    const { routes, tools } = mount(freshHome(), cloud.url, true)
    assert.equal(tools.length, 1, '插件应把工具注册进 tools 服务')
    assert.equal(tools[0]!.name, 'akso_workbench_accounts')

    await loginWith(routes)
    const site = await call(routes, P.siteCreate, 'POST', { name: '通桥', baseUrl: 'https://tonbridge-config.aksoegmp.com' })
    const siteId = (site.json.sites as Array<{ id: string }>)[0]!.id
    await call(routes, P.accountCreate, 'POST', { siteId, username: 'qa.admin', password: 'pw', note: '只读' })

    const result = (await tools[0]!.execute({})) as Record<string, unknown>
    assert.equal(result.loggedIn, true)
    const accounts = result.accounts as Array<Record<string, unknown>>
    assert.equal(accounts.length, 1)
    assert.equal(accounts[0]!.username, 'qa.admin')
    assert.equal(accounts[0]!.site, '通桥')
    assert.equal(accounts[0]!.note, '只读')
    assert.equal((result.counts as Record<string, number>).sites, 1)
    assert.equal((result.sites as unknown[]).length, 1)
    assert.equal((result.boxes as unknown[]).length, 2)
  } finally {
    await cloud.close()
  }
})
test('每个云端请求都自报家门（X-Akso-Client），否则个人信息页永远显示未登录', async () => {
  const seen: Array<Record<string, string>> = []
  const stub = async (_url: string, init: { headers: Record<string, string> }) => {
    seen.push(init.headers)
    return new Response(JSON.stringify({
      format: 'akso-cloud-ui-state',
      revision: 1,
      accounts: [],
      sites: [],
      boxes: [],
      defaultBox: '',
      counts: { accounts: 0, sites: 0, boxes: 0 },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }
  await uiState('https://example.com', 'tok-test', stub as never)
  assert.equal(seen.length, 1)
  assert.equal(seen[0]!['X-Akso-Client'], 'akso-workbench')
  assert.match(String(seen[0]!['X-Akso-Client-Version']), /^\d+\.\d+\.\d+/)
  // 令牌仍然只在 Authorization 里（没被塞进任何自定义头）
  assert.equal(seen[0]!['Authorization'], 'Bearer tok-test')
})
/* ─────────────────── 给 agent 的工具 ─────────────────── */

test('工具：未登录时给登录提示，不编造数据', async () => {
  const tool = buildSnapshotTool({
    read: () => ({ loggedIn: false, revision: null, syncedAt: null, accounts: [], sites: [], boxes: [], defaultBox: '', counts: { accounts: 0, sites: 0, boxes: 0 }, currentAccountId: null }),
    refresh: async () => {},
    loginHint: '去左栏登录',
  })
  const result = (await tool.execute({})) as Record<string, unknown>
  assert.equal(result.loggedIn, false)
  assert.equal(result.sites, undefined)
  assert.equal(result.hint, '去左栏登录')
})

test('工具：登录后按 section/query 过滤，且**不暴露口令字段**', async () => {
  const tool = buildSnapshotTool({
    read: () => ({
      loggedIn: true,
      revision: 7,
      syncedAt: 1_700_000_000_000,
      accounts: [
        { id: 'a1', username: 'qa.admin', siteName: '通桥', siteHost: 't.example.com', box: '默认盒', note: '只读', status: 'active', hasPassword: true } as never,
        { id: 'a2', username: 'auditor', siteName: '通桥', siteHost: 't.example.com', box: '默认盒', note: '审批', status: 'active', hasPassword: true } as never,
      ],
      sites: [{ id: 's1', name: '通桥', host: 't.example.com', note: '', accountCount: 2 } as never],
      boxes: [{ name: '默认盒', isDefault: true, disabled: true, autoDisabled: true, count: 2 } as never],
      defaultBox: '默认盒',
      counts: { accounts: 2, sites: 1, boxes: 1 },
      currentAccountId: 'a2',
    }),
    refresh: async () => {},
    loginHint: '',
  })

  const all = (await tool.execute({})) as Record<string, unknown>
  assert.equal(all.loggedIn, true)
  assert.equal(all.revision, 7)
  const accounts = all.accounts as Array<Record<string, unknown>>
  assert.equal(accounts.length, 2)
  assert.equal(JSON.stringify(accounts).includes('password'), false)
  // 「连接给 agent」的标记跟着快照走
  assert.equal(accounts.find((a) => a.username === 'auditor')!.connected, true)
  assert.equal(accounts.find((a) => a.username === 'qa.admin')!.connected, false)
  assert.equal(all.connectedAccountId, 'a2')

  const filtered = (await tool.execute({ query: 'audit', section: 'accounts' })) as Record<string, unknown>
  const one = filtered.accounts as Array<Record<string, unknown>>
  assert.equal(one.length, 1)
  assert.equal(one[0]!.username, 'auditor')
  assert.equal(filtered.sites, undefined, 'section=accounts 时不该附带站点')
})
