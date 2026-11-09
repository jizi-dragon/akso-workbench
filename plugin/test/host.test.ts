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
import { createServer, request } from 'node:http'
import type { AddressInfo } from 'node:net'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

import { errorMessage, uiState } from '../src/cloud.ts'
import { CAPABILITIES } from '../src/config/capabilities.ts'
import { applyPlan, planBlueprint, saveApproval, savePlan } from '../src/config/engine.ts'
import { writeEgmpSession } from '../src/config/session.ts'
import { writePool, writePrefs } from '../src/store.ts'
import { apply, ROUTES } from '../src/index.ts'
import { buildSnapshotTool } from '../src/tool.ts'
import { startFakeCloud } from './fake-cloud.mjs'

interface Captured {
  path: string
  handler: (req: unknown, res: unknown) => void
}

/** 路由表直接来自插件（导出常量），这里不再手工维护第二份。 */
const P = ROUTES

interface Mounted {
  routes: Map<string, Captured['handler']>
  disposers: Array<() => void>
  opened: string[]
  tools: Array<{ name: string; execute: (args: unknown, exec?: unknown) => Promise<unknown> }>
  /** 工具在对话里问过人的每一次（问题 + 执行上下文） */
  asked: Array<{ questions: Array<Record<string, any>>; exec: Record<string, any> | undefined }>
  /** 下一次被问时人选了什么；`labels: null` 表示问答通道直接抛错 */
  reply: { labels: string[] | null }
}

function mount(dshHome: string, cloudUrl?: string | undefined, withTools = false, withQuestions = true, logs?: string[]): Mounted {
  const routes = new Map<string, Captured['handler']>()
  const disposers: Array<() => void> = []
  const opened: string[] = []
  const tools: Mounted['tools'] = []
  const asked: Mounted['asked'] = []
  const reply: Mounted['reply'] = { labels: ['允许这次写入'] }
  const ctx = {
    logger: {
      warn: (message: unknown) => { logs?.push(`warn ${String(message)}`) },
      info: (message: string) => { logs?.push(`info ${message}`) },
    },
    effect: (fn: () => () => void) => {
      disposers.push(fn())
      return () => {}
    },
    get: (name: string) => {
      if (name === 'tools' && withTools) {
        return { register: (definition: Mounted['tools'][number]) => { tools.push(definition); return () => {} } }
      }
      if (name === 'userQuestions' && withQuestions) {
        return {
          ask: async (request: { questions: Array<Record<string, any>>; agent?: unknown; signal?: unknown }) => {
            asked.push({ questions: request.questions, exec: { agent: request.agent, signal: request.signal } })
            if (reply.labels === null) throw new Error('这个宿主没有问答通道')
            return { answers: [{ id: request.questions[0]?.id ?? '', selected: reply.labels }] }
          },
        }
      }
      return undefined
    },
    webServer: {
      register: (route: Captured) => {
        routes.set(route.path, route.handler)
        return () => routes.delete(route.path)
      },
    },
  }
  apply(ctx as never, { dshHome, cloudBaseUrl: cloudUrl, openBrowser: (url: string) => opened.push(url) })
  return { routes, disposers, opened, tools, asked, reply }
}

/** 默认就当"本机浏览器里的插件页面"发的请求：Host 是本机、带自定义头。 */
function makeReq(
  method: string,
  body?: unknown,
  remote = '127.0.0.1',
  headers: Record<string, string> = { host: '127.0.0.1:60717', 'x-akso-workbench': '1' },
) {
  const emitter = new EventEmitter() as EventEmitter & Record<string, unknown>
  emitter.method = method
  emitter.socket = { remoteAddress: remote }
  emitter.headers = headers
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
  headers?: Record<string, string>,
) {
  const handler = routes.get(path)
  assert.ok(handler, `路由未注册：${path}`)
  const response = makeRes()
  const done = new Promise<void>((resolve) => {
    response.notify = resolve
  })
  handler(makeReq(method, body, remote, headers), response)
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

test('注册的路由与常量表逐条一致，卸载时全部摘掉', () => {
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

test('禁止删除：cloud.ts 里也不留删除通道（路由删了、helper 还在就等于埋着）', () => {
  const source = readFileSync(new URL('../src/cloud.ts', import.meta.url), 'utf8')
  const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
  assert.deepEqual(
    [...code.matchAll(/'(?:DELETE|PATCH)'|`\/api\/[^`]*delete/gi)].map((m) => m[0]),
    [],
    'cloud.ts 不该再有删除类请求：本仓的口径是"只能加不能删"，删账号/站点/盒子由人在 akso-cloud 上操作',
  )
  for (const gone of ['deleteAccount', 'deleteSite', 'deleteBox']) {
    assert.equal(code.includes(gone), false, `死函数 ${gone} 应该删掉，不要留着给人"随手接上"`)
  }
})
test('围栏：非 loopback 一律 403', async () => {
  const { routes } = mount(freshHome())
  assert.equal((await call(routes, P.state, 'GET', undefined, '10.1.2.3')).status, 403)
  assert.equal((await call(routes, P.login, 'POST', { email: 'a', password: 'b' }, '192.168.0.8')).status, 403)
})

test('围栏：Host 必须是本机名（DNS rebinding 的域名解析到 127.0.0.1 也挡得住）', async () => {
  const { routes } = mount(freshHome())
  for (const host of ['evil.example.com', '127.0.0.1.evil.com', '']) {
    const res = await call(routes, P.state, 'GET', undefined, '127.0.0.1', {
      host,
      'x-akso-workbench': '1',
    })
    assert.equal(res.status, 403, `host=${host} 应该被挡`)
    assert.match(String(res.json.error), /host/)
  }
  // 本机名的几种写法都放行
  for (const host of ['127.0.0.1:60717', 'localhost:3000', '[::1]:8080']) {
    const res = await call(routes, P.state, 'GET', undefined, '127.0.0.1', { host })
    assert.equal(res.status, 200, `host=${host} 应该放行`)
  }
})

test('围栏：跨站 Origin / sec-fetch-site 一律 403（别的网页借浏览器打本机）', async () => {
  const { routes } = mount(freshHome())
  const evil = await call(routes, P.login, 'POST', { email: 'a', password: 'b' }, '127.0.0.1', {
    host: '127.0.0.1:60717',
    origin: 'http://evil.example.com',
    'x-akso-workbench': '1',
  })
  assert.equal(evil.status, 403)
  assert.match(String(evil.json.error), /跨站/)

  const nullOrigin = await call(routes, P.login, 'POST', {}, '127.0.0.1', {
    host: '127.0.0.1:60717',
    origin: 'null',
    'x-akso-workbench': '1',
  })
  assert.equal(nullOrigin.status, 403)

  const crossSite = await call(routes, P.login, 'POST', {}, '127.0.0.1', {
    host: '127.0.0.1:60717',
    'sec-fetch-site': 'cross-site',
    'x-akso-workbench': '1',
  })
  assert.equal(crossSite.status, 403)

  // 同源（带端口）正常放行
  const same = await call(routes, P.state, 'GET', undefined, '127.0.0.1', {
    host: '127.0.0.1:60717',
    origin: 'http://127.0.0.1:60717',
    'sec-fetch-site': 'same-origin',
  })
  assert.equal(same.status, 200)
})

test('围栏：写请求必须带自定义头（跨站表单设不了头，CSRF 走不通）', async () => {
  const { routes } = mount(freshHome())
  const bare = await call(routes, P.login, 'POST', { email: 'a', password: 'b' }, '127.0.0.1', {
    host: '127.0.0.1:60717',
  })
  assert.equal(bare.status, 403)
  assert.match(String(bare.json.error), /x-akso-workbench/)
  // GET 不要求这个头：只读、跨站读不到响应体，而 curl 探活要方便
  assert.equal((await call(routes, P.state, 'GET', undefined, '127.0.0.1', { host: '127.0.0.1:60717' })).status, 200)
})

test('围栏在真 HTTP 服务上成立（真 socket、真 header，不是手搓 req）', async () => {
  const { routes } = mount(freshHome())
  // 假的 webServer：把 captured handler 直接挂到真 http.Server 上，header 由 Node 自己解析
  const server = createServer((req, res) => {
    const path = String(req.url ?? '').split('?')[0] ?? ''
    const handler = routes.get(path)
    if (handler === undefined) {
      res.statusCode = 404
      res.end()
      return
    }
    void handler(req, res)
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const port = (server.address() as AddressInfo).port

  const send = (path: string, init: { method?: string; headers?: Record<string, string>; body?: string } = {}) =>
    new Promise<{ status: number; body: string }>((resolve, reject) => {
      const req = request(
        // agent:false ⇒ 每次一条新连接、响应后自己关掉；否则 keep-alive 会把 server.close() 挂住
        { host: '127.0.0.1', port, path, method: init.method ?? 'GET', headers: init.headers, agent: false },
        (res) => {
          let data = ''
          res.setEncoding('utf8')
          res.on('data', (chunk: string) => {
            data += chunk
          })
          res.on('end', () => resolve({ status: res.statusCode ?? 0, body: data }))
        },
      )
      req.on('error', reject)
      if (init.body !== undefined) req.write(init.body)
      req.end()
    })

  try {
    // GET：Host 自动是本机，不需要自定义头
    assert.equal((await send(P.state)).status, 200)
    // 写：没自定义头 ⇒ 403
    const bare = await send(P.login, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: 'a', password: 'b' }),
    })
    assert.equal(bare.status, 403)
    // 写：有头但 Origin 是外站 ⇒ 403
    const cross = await send(P.login, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Origin: 'http://evil.example.com', 'x-akso-workbench': '1' },
      body: '{}',
    })
    assert.equal(cross.status, 403)
    // 写：都齐了 ⇒ 过围栏，落到业务层（账号条没有账号 ⇒ 409，说明不是被围栏挡的）
    const ok = await send(P.configPlan, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'x-akso-workbench': '1' },
      body: '{}',
    })
    assert.equal(ok.status, 409, ok.body)
    assert.match(ok.body, /NEEDS_ACCOUNT/)
    // 改 Host 假装被别的域名解析过来 ⇒ 403
    const rebound = await send(P.state, { headers: { Host: 'evil.example.com' } })
    assert.equal(rebound.status, 403)
  } finally {
    server.closeAllConnections()
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
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

    // 只有配置池里的账号才能连接（默认空池，先加进池）
    await call(routes, P.poolToggle, 'POST', { id: accountId, inPool: true })
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
    assert.deepEqual(
      tools.map((tool) => tool.name).sort(),
      ['akso_workbench_accounts', 'akso_workbench_config_apply', 'akso_workbench_config_plan'],
      '插件应把三个工具都注册进 tools 服务',
    )
    const snapshotTool = tools.find((tool) => tool.name === 'akso_workbench_accounts')
    assert.ok(snapshotTool, '缺 akso_workbench_accounts')

    await loginWith(routes)
    const site = await call(routes, P.siteCreate, 'POST', { name: '通桥', baseUrl: 'https://tonbridge-config.aksoegmp.com' })
    const siteId = (site.json.sites as Array<{ id: string }>)[0]!.id
    await call(routes, P.accountCreate, 'POST', { siteId, username: 'qa.admin', password: 'pw', note: '只读' })

    const result = (await snapshotTool.execute({})) as Record<string, unknown>
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
      poolAccountIds: ['a2'],
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
  // 配置池：只有池里的账号标 inPool（a2 在池里，a1 不在）
  assert.deepEqual(all.poolAccountIds, ['a2'])
  assert.equal(all.poolCount, 1)
  assert.equal(accounts.find((a) => a.username === 'auditor')!.inPool, true)
  assert.equal(accounts.find((a) => a.username === 'qa.admin')!.inPool, false)

  const filtered = (await tool.execute({ query: 'audit', section: 'accounts' })) as Record<string, unknown>
  const one = filtered.accounts as Array<Record<string, unknown>>
  assert.equal(one.length, 1)
  assert.equal(one[0]!.username, 'auditor')
  assert.equal(filtered.sites, undefined, 'section=accounts 时不该附带站点')
})

test('配置池：默认空池，加入才可用；当前连接的账号不许移出', async () => {
  const cloud = await startFakeCloud()
  try {
    const home = freshHome()
    const { routes } = mount(home, cloud.url)
    await loginWith(routes)

    const site = await call(routes, P.siteCreate, 'POST', { baseUrl: 'https://a.example.com' })
    const siteId = (site.json.sites as Array<{ id: string }>)[0]!.id
    const created = await call(routes, P.accountCreate, 'POST', { siteId, username: 'u1', password: 'pw' })
    const accountId = (created.json.accounts as Array<{ id: string }>)[0]!.id

    // 默认空池：账号不会自己进来，也没有 pool.json
    assert.deepEqual(created.json.poolAccountIds, [])
    assert.equal(existsSync(join(home, 'akso-workbench', 'pool.json')), false)

    // 不在池里的账号不能连接给 agent
    const rejected = await call(routes, P.accountCurrent, 'POST', { id: accountId })
    assert.equal(rejected.status, 400)
    assert.match(String(rejected.json.error), /配置池/)

    // 手动加入 → 落盘
    const added = await call(routes, P.poolToggle, 'POST', { id: accountId, inPool: true })
    assert.equal(added.status, 200)
    assert.deepEqual(added.json.poolAccountIds, [accountId])
    const stored = JSON.parse(readFileSync(join(home, 'akso-workbench', 'pool.json'), 'utf8')) as { accountIds: string[] }
    assert.deepEqual(stored.accountIds, [accountId])

    // 进池后可以连接
    const connected = await call(routes, P.accountCurrent, 'POST', { id: accountId })
    assert.equal(connected.json.currentAccountId, accountId)

    // 当前连接给 agent 的账号不能被移出（要先换一个或断开）
    const blocked = await call(routes, P.poolToggle, 'POST', { id: accountId, inPool: false })
    assert.equal(blocked.status, 400)
    assert.match(String(blocked.json.error), /当前连接/)

    // 断开之后就能移出，连接偏好保留但不生效（currentAccountId 归 null）
    await call(routes, P.accountCurrent, 'POST', { id: '' })
    const removed = await call(routes, P.poolToggle, 'POST', { id: accountId, inPool: false })
    assert.equal(removed.status, 200)
    assert.deepEqual(removed.json.poolAccountIds, [])
    assert.equal(removed.json.currentAccountId, null)
  } finally {
    await cloud.close()
  }
})

/* ─────────────────── 审批门：对话弹窗（HTTP 挂起 + 人决定） ─────────────────── */

const ACCOUNT_ID = 'acct-tester'

/** 写入会话的三个条件：账号条选了它、它在配置池里、磁盘会话绑的就是它。 */
function writeAccount(home: string, baseUrl: string): void {
  writeEgmpSession(home, { baseUrl, account: 'tester', accountId: ACCOUNT_ID, token: 'jwt', savedAt: Date.now() })
  writePrefs(home, { currentAccountId: ACCOUNT_ID })
  writePool(home, { accountIds: [ACCOUNT_ID] })
}

function planFixture(
  planId: string,
  overrides: { create: number; update: number; irreversible: number },
  baseUrl: string,
): never {
  const step = (id: string, action: 'create' | 'update'): Record<string, unknown> => ({
    id,
    kind: 'picklist',
    action,
    title: `选项集 akso-workbench 验证选项集（akw_test_pk__c）· ${action}`,
    naturalKey: 'picklist:akw_test_pk__c',
    risk: 'low',
    irreversible: overrides.irreversible > 0,
    capabilityId: 'picklist.save',
    params: {
      name: 'akso-workbench 验证选项集',
      code: 'akw_test_pk__c',
      options: [{ name: '主要变更', code: 'major__c', status: 1, operations: 1, sort: 0 }],
    },
    dependsOn: [],
    warnings: [],
  })
  const steps: Array<Record<string, unknown>> = []
  for (let index = 0; index < overrides.create; index += 1) steps.push(step(`s${steps.length + 1}`, 'create'))
  for (let index = 0; index < overrides.update; index += 1) steps.push(step(`s${steps.length + 1}`, 'update'))
  return {
    planId,
    createdAt: Date.now(),
    expiresAt: Date.now() + 30 * 60 * 1000,
    env: 'test',
    target: { baseUrl, accountId: ACCOUNT_ID, account: 'tester' },
    summary: { create: overrides.create, update: overrides.update, skip: 0, irreversible: overrides.irreversible },
    steps,
    warnings: [],
    status: 'planned',
    results: [],
  } as never
}

/** 等审批窗挂出来（工具把请求挂到 gate 上，浏览器侧轮询拿到它）。 */
async function waitPending(routes: Map<string, Captured['handler']>): Promise<Record<string, any>> {
  for (let attempt = 0; attempt < 400; attempt += 1) {
    const res = await call(routes, P.configApprovalPending, 'GET')
    const pending = res.json.pending as Record<string, any> | null
    if (pending !== null && pending !== undefined) return pending
    await new Promise((resolve) => setTimeout(resolve, 5))
  }
  assert.fail('审批窗没有挂出来')
}

/**
 * 假平台：数请求次数（用来证明「没批准之前一个包都没发出去」），
 * 也可以摆一份既有的选项集，让计划阶段真的走进"更新"分支。
 */
async function startFakeEgmp(
  options: Array<{ code: string; name: string }> | null = [],
): Promise<{ baseUrl: string; hits: number; close: () => Promise<void> }> {
  const state = { hits: 0 }
  const server = createServer((req, res) => {
    state.hits += 1
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' })
    // `options === null` 表示平台上还没有这个选项集
    const data = options === null
      ? null
      : {
          picklists: { picklist_code: 'akw_test_pk__c', picklist__name: 'akso-workbench 验证选项集' },
          picklist_options: options.map((option) => ({
            sort: 0,
            status: 1,
            picklist_option_id: `id-${option.code}`,
            picklist_option_code: option.code,
            picklist_option_name: option.name,
          })),
        }
    res.end(JSON.stringify({ code: 0, message: 'ok', data }))
    void req
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address() as AddressInfo
  return {
    baseUrl: `http://127.0.0.1:${address.port}`,
    get hits() {
      return state.hits
    },
    close: () => new Promise<void>((resolve) => {
      server.closeAllConnections()
      server.close(() => resolve())
    }),
  }
}

test('配置写入先在对话里弹审批窗：人点同意才发请求', async () => {
  const home = freshHome()
  const mounted = mount(home, undefined, true)
  const applyTool = mounted.tools.find((tool) => tool.name === 'akso_workbench_config_apply')
  assert.ok(applyTool !== undefined, '执行工具应当注册')

  const egmp = await startFakeEgmp()
  writeAccount(home, egmp.baseUrl)
  try {
    savePlan(home, planFixture('pln_writes', { create: 1, update: 2, irreversible: 1 }, egmp.baseUrl))

    const running = applyTool!.execute({ planId: 'pln_writes' }, { agent: { id: 'agent-1' } }) as Promise<Record<string, any>>
    const pending = await waitPending(mounted.routes)
    const report = pending.report as Record<string, any>

    // 弹窗里看到的是这份计划 + 真实接口 + 完整参数
    assert.equal(report.planId, 'pln_writes')
    assert.equal(report.accountId, ACCOUNT_ID)
    assert.match(String(report.host), /127\.0\.0\.1/)
    assert.equal((report.steps as unknown[]).length, 3, '跳过的步骤不该出现在审批表里')
    const first = (report.steps as Array<Record<string, any>>)[0]!
    assert.equal(first.method, 'POST')
    assert.match(String(first.path), /ObjectPicklist\/save/)
    assert.match(JSON.stringify(first.params), /akw_test_pk__c/)
    assert.equal(first.irreversible, true, '不可逆的步骤要标出来')
    assert.equal(egmp.hits, 0, '没人同意之前一个包都不许发')

    // 点同意之后才真的发请求
    const decided = await call(mounted.routes, P.configApprovalDecide, 'POST', { id: pending.id, verdict: 'approve' })
    assert.equal(decided.status, 200)
    const result = await running
    assert.equal(result.ok, true)
    assert.equal(result.approved, true)
    assert.ok(egmp.hits >= 1, '同意之后必须真的发出请求')
  } finally {
    await egmp.close()
  }
})

test('人点拒绝：计划一步都不写，审批窗清掉', async () => {
  const home = freshHome()
  const mounted = mount(home, undefined, true)
  const applyTool = mounted.tools.find((tool) => tool.name === 'akso_workbench_config_apply')!
  const egmp = await startFakeEgmp()
  writeAccount(home, egmp.baseUrl)
  try {
    savePlan(home, planFixture('pln_deny', { create: 1, update: 0, irreversible: 0 }, egmp.baseUrl))
    const running = applyTool.execute({ planId: 'pln_deny' }, {}) as Promise<Record<string, any>>
    const pending = await waitPending(mounted.routes)
    await call(mounted.routes, P.configApprovalDecide, 'POST', { id: pending.id, verdict: 'deny' })
    const result = await running
    assert.equal(result.status, 'denied')
    assert.match(String(result.message), /一步都没写/)
    assert.equal(egmp.hits, 0, '被拒绝时一个包都不许发')
    const after = await call(mounted.routes, P.configApprovalPending, 'GET')
    assert.equal(after.json.pending, null, '决定之后审批窗要清掉')
  } finally {
    await egmp.close()
  }
})

test('人点补充：不写，把补充意见原样回给 agent', async () => {
  const home = freshHome()
  const mounted = mount(home, undefined, true)
  const applyTool = mounted.tools.find((tool) => tool.name === 'akso_workbench_config_apply')!
  const egmp = await startFakeEgmp()
  writeAccount(home, egmp.baseUrl)
  try {
    savePlan(home, planFixture('pln_more', { create: 1, update: 0, irreversible: 0 }, egmp.baseUrl))
    const running = applyTool.execute({ planId: 'pln_more' }, {}) as Promise<Record<string, any>>
    const pending = await waitPending(mounted.routes)

    // 光点「补充」不写意见：400，审批还挂着
    const empty = await call(mounted.routes, P.configApprovalDecide, 'POST', { id: pending.id, verdict: 'supplement' })
    assert.equal(empty.status, 400)

    const decided = await call(mounted.routes, P.configApprovalDecide, 'POST', {
      id: pending.id,
      verdict: 'supplement',
      note: '把 code 改成 akw_other__c',
    })
    assert.equal(decided.status, 200)
    const result = await running
    assert.equal(result.status, 'supplement')
    assert.equal(result.note, '把 code 改成 akw_other__c')
    assert.match(String(result.message), /补充意见/)
    assert.equal(egmp.hits, 0)
  } finally {
    await egmp.close()
  }
})

test('同一时间只挂一条审批：第二条直接 busy，不排队', async () => {
  const home = freshHome()
  const mounted = mount(home, undefined, true)
  const applyTool = mounted.tools.find((tool) => tool.name === 'akso_workbench_config_apply')!
  const egmp = await startFakeEgmp()
  writeAccount(home, egmp.baseUrl)
  try {
    savePlan(home, planFixture('pln_first', { create: 1, update: 0, irreversible: 0 }, egmp.baseUrl))
    savePlan(home, planFixture('pln_second', { create: 1, update: 0, irreversible: 0 }, egmp.baseUrl))
    const first = applyTool.execute({ planId: 'pln_first' }, {}) as Promise<Record<string, any>>
    const pending = await waitPending(mounted.routes)
    const second = await applyTool.execute({ planId: 'pln_second' }, {}) as Record<string, any>
    assert.equal(second.status, 'busy')
    await call(mounted.routes, P.configApprovalDecide, 'POST', { id: pending.id, verdict: 'deny' })
    assert.equal((await first).status, 'denied')
    assert.equal(egmp.hits, 0)
  } finally {
    await egmp.close()
  }
})

test('账号条为空：工具返回 needs-account，平台一步都不写，界面收到 needsAccount', async () => {
  const home = freshHome()
  const mounted = mount(home, undefined, true)
  const applyTool = mounted.tools.find((tool) => tool.name === 'akso_workbench_config_apply')!
  const egmp = await startFakeEgmp()
  try {
    // 只有磁盘上的旧会话、账号条没选账号 —— 这就是「不回落旧会话」的场景
    writeEgmpSession(home, { baseUrl: egmp.baseUrl, account: 'tester', accountId: ACCOUNT_ID, token: 'jwt', savedAt: Date.now() })
    savePlan(home, planFixture('pln_none', { create: 1, update: 0, irreversible: 0 }, egmp.baseUrl))
    const result = await applyTool.execute({ planId: 'pln_none' }, {}) as Record<string, any>
    assert.equal(result.status, 'needs-account')
    assert.match(String(result.hint), /账号条/)
    assert.equal(egmp.hits, 0, '没有账号就不许碰平台')

    const state = await call(mounted.routes, P.state, 'GET')
    assert.equal(state.json.needsAccount, true, '界面据此自动弹出选账号面板')
    assert.ok(typeof state.json.needsAccountSince === 'number')
  } finally {
    await egmp.close()
  }
})

test('没有写入的计划不弹审批窗', async () => {
  const home = freshHome()
  const mounted = mount(home, undefined, true)
  const applyTool = mounted.tools.find((tool) => tool.name === 'akso_workbench_config_apply')!
  const egmp = await startFakeEgmp()
  writeAccount(home, egmp.baseUrl)
  try {
    savePlan(home, planFixture('pln_noop', { create: 0, update: 0, irreversible: 0 }, egmp.baseUrl))
    const result = await applyTool.execute({ planId: 'pln_noop' }, {}) as Record<string, any>
    assert.equal(result.status, 'applied')
    assert.equal(result.approved, false)
    const pending = await call(mounted.routes, P.configApprovalPending, 'GET')
    assert.equal(pending.json.pending, null, '没有写入就不该打扰人')
    assert.equal(egmp.hits, 0)
  } finally {
    await egmp.close()
  }
})

test('计划的目标环境变了：引擎拒绝执行', async () => {
  const home = freshHome()
  const mounted = mount(home, undefined, true)
  const applyTool = mounted.tools.find((tool) => tool.name === 'akso_workbench_config_apply')!
  const egmp = await startFakeEgmp()
  writeAccount(home, egmp.baseUrl)
  try {
    // 计划绑的是另一个环境：就算人点了同意，也不许把包发到这边
    savePlan(home, planFixture('pln_moved', { create: 1, update: 0, irreversible: 0 }, 'https://other.example.com'))
    const running = applyTool.execute({ planId: 'pln_moved' }, {}) as Promise<Record<string, any>>
    const pending = await waitPending(mounted.routes)
    await call(mounted.routes, P.configApprovalDecide, 'POST', { id: pending.id, verdict: 'approve' })
    const result = await running
    assert.equal(result.ok, false)
    assert.match(String(result.error), /目标环境/)
    assert.equal(egmp.hits, 0)
  } finally {
    await egmp.close()
  }
})

test('引擎自己再验一遍：绕过工具也必须有审批记录', async () => {
  const home = freshHome()
  const egmp = await startFakeEgmp()
  writeAccount(home, egmp.baseUrl)
  try {
    const plan = planFixture('pln_engine', { create: 1, update: 0, irreversible: 0 }, egmp.baseUrl)
    savePlan(home, plan)
    const session = { baseUrl: egmp.baseUrl, account: 'tester', accountId: ACCOUNT_ID, token: 'jwt', savedAt: Date.now() }
    await assert.rejects(
      () => applyPlan(session, 'pln_engine', home),
      (error: any) => error?.code === 'APPROVAL_REQUIRED',
      '没有审批记录时引擎必须拒绝',
    )
    assert.equal(egmp.hits, 0)

    // 审批指纹与计划内容对不上（计划被改过）也拒绝
    saveApproval(home, { planId: 'pln_engine', hash: 'deadbeef', at: Date.now() })
    await assert.rejects(
      () => applyPlan(session, 'pln_engine', home),
      (error: any) => error?.code === 'APPROVAL_MISMATCH',
    )
    assert.equal(egmp.hits, 0)
  } finally {
    await egmp.close()
  }
})

test('审批窗是插件自己的门：不接 DSH 的 approval，也不再走 userQuestions', () => {
  const source = readFileSync(new URL('../src/index.ts', import.meta.url), 'utf8')
  assert.doesNotMatch(source, /tools\/pre-execute/, '不该再注册 DSH 的 pre-execute 门')
  assert.doesNotMatch(source, /ctx\.approval/, '不该再向 DSH 的 approval 服务提问')
  assert.doesNotMatch(source, /userQuestions/, '审批不再借用宿主的用户提问通道')
  assert.match(source, /createApprovalGate/, '应当由插件自己的 approval gate 挂起/唤醒')
  assert.match(source, /configApprovalDecide/, '应当有浏览器侧回复审批的端点')
})

test('HTTP 直连：写接口默认 403，读接口放行到登录检查', async () => {
  const closed = mount(freshHome())
  const write = await call(closed.routes, P.configInvoke, 'POST', { capabilityId: 'object.save', params: {} })
  assert.equal(write.status, 403, '写能力经 HTTP 直连默认必须关着')
  assert.match(String(write.json.error), /AKSO_WORKBENCH_ALLOW_DIRECT_WRITE/)
  const apply = await call(closed.routes, P.configApply, 'POST', { planId: 'pln_x' })
  assert.equal(apply.status, 403)

  // 只读能力不需要那个开关：门后是"还没有登录 eGMP"
  const read = await call(closed.routes, P.configInvoke, 'POST', { capabilityId: 'query.object.get', params: { code: 'x__c' } })
  assert.equal(read.status, 401)
  assert.match(String(read.json.error), /eGMP/)

  // 不认识的 capabilityId 直接 400，不猜
  const unknown = await call(closed.routes, P.configInvoke, 'POST', { capabilityId: 'nope.nope', params: {} })
  assert.equal(unknown.status, 400)

  process.env.AKSO_WORKBENCH_ALLOW_DIRECT_WRITE = '1'
  try {
    // 开关只放行「直连」这条路，不等于放行写入：没账号 409
    const opened = mount(freshHome())
    const res = await call(opened.routes, P.configApply, 'POST', { planId: 'pln_x' })
    assert.equal(res.status, 409)
    assert.equal(res.json.code, 'NEEDS_ACCOUNT')

    // 账号准备好了、计划也在，但没有审批记录：引擎这一关照样拒绝
    const home = freshHome()
    const withAccount = mount(home)
    writeAccount(home, 'https://egmp.example.com')
    savePlan(home, planFixture('pln_direct', { create: 1, update: 0, irreversible: 0 }, 'https://egmp.example.com'))
    const guarded = await call(withAccount.routes, P.configApply, 'POST', { planId: 'pln_direct' })
    assert.equal(guarded.status, 502)
    assert.equal(guarded.json.code, 'APPROVAL_REQUIRED')
  } finally {
    delete process.env.AKSO_WORKBENCH_ALLOW_DIRECT_WRITE
  }
})
/* ─────────────── 能力目录：路径不许有第二份 ─────────────── */

test('engine 里不许再手写平台路径：能力目录是唯一的路径源', () => {
  const source = readFileSync(new URL('../src/config/engine.ts', import.meta.url), 'utf8')
  const hits = [...source.matchAll(/'(\/(?:api|openapi)\/[^']*)'/g)].map((match) => match[1])
  assert.deepEqual(hits, [], `engine.ts 里还有第二份平台路径：${hits.join(', ')}`)
  assert.match(source, /invokeCapability/, 'engine 应当只通过能力目录调用平台')
})

test('engine 用到的能力 id 都在目录里（拼错会静默变成"查不到"）', () => {
  const source = readFileSync(new URL('../src/config/engine.ts', import.meta.url), 'utf8')
  const known = new Set(CAPABILITIES.map((cap) => cap.id))
  const used = [...source.matchAll(/(?:tryInvoke|invokeCapability)\([^,]+,\s*'([\w.]+)'/g)].map((match) => match[1])
  assert.ok(used.length >= 9, `应该能扫到 engine 用到的能力 id，实际 ${used.length}`)
  const missing = used.filter((id) => !known.has(id))
  assert.deepEqual(missing, [], `这些 id 不在能力目录里：${missing.join(', ')}`)
})

test('生命周期挂载能力：进入动作 / 按钮列表的路径与只读标记固定', () => {
  const byId = new Map(CAPABILITIES.map((cap) => [cap.id, cap]))

  const enter = byId.get('lifecycle.enteraction.create')
  assert.ok(enter, '缺少 lifecycle.enteraction.create')
  assert.equal(enter.method, 'POST')
  assert.equal(enter.path, '/api/config/lifecycle/EnterAction/Create')
  assert.deepEqual(enter.params.map((item) => item.name), ['code', 'belongId', 'belongType', 'actions'])
  assert.ok(enter.notes.some((note) => note.includes('isAllowTrigger')), '进入动作必须提醒 isAllowTrigger')

  const enterList = byId.get('lifecycle.enteraction.list')
  assert.ok(enterList, '缺少 lifecycle.enteraction.list')
  assert.equal(enterList.method, 'GET')
  assert.equal(enterList.path, '/api/config/lifecycle/EnterAction/GetListByStatusId')
  assert.equal(enterList.readonly, true)

  const actionList = byId.get('lifecycle.useraction.list')
  assert.ok(actionList, '缺少 lifecycle.useraction.list')
  assert.equal(actionList.method, 'GET')
  assert.equal(actionList.path, '/api/config/lifecycle/UserAction/GetListByStatusId')
  assert.equal(actionList.readonly, true)

  const actionGet = byId.get('lifecycle.useraction.get')
  assert.ok(actionGet, '缺少 lifecycle.useraction.get')
  assert.match(actionGet.returns, /roles/, 'useraction.get 只回角色授权，不能当按钮列表用')
})

/* ─────────────── 令牌详情：只给时间戳（原文不出 host），且必须带自定义头 ─────────────── */

test('令牌详情：只回从 JWT 解出的签发/到期时间，原文不出 host', async () => {
  const home = freshHome()
  const mounted = mount(home)
  const claims = Buffer.from(JSON.stringify({ iat: 1_700_000_000, exp: 1_700_003_600, sub: 'liyulong' })).toString('base64url')
  const token = `eyJhbGciOiJIUzI1NiJ9.${claims}.c2ln`
  writeEgmpSession(home, { baseUrl: 'https://standard-val.aksoegmp.com', account: 'liyulong', token, savedAt: 1_700_000_000_000 })

  // 不带自定义头：连读都不给（防止 <img src> 这类"不需要读响应也能发出去"的引用）
  const refused = await call(mounted.routes, P.configToken, 'GET', undefined, '127.0.0.1', { host: '127.0.0.1:60717' })
  assert.equal(refused.status, 403)
  assert.match(String(refused.json.error), /x-akso-workbench/)

  const view = await call(mounted.routes, P.configToken, 'GET')
  assert.equal(view.status, 200)
  assert.equal(view.json.token, null, '账号条只做展示：JWT 原文不下发浏览器')
  assert.equal(view.json.loggedIn, true)
  assert.equal(view.json.account, 'liyulong')
  assert.equal(view.json.issuedAt, 1_700_000_000_000)
  assert.equal(view.json.expiresAt, 1_700_003_600_000)

  // 没登录：200，但不给令牌
  const bare = mount(freshHome())
  const none = await call(bare.routes, P.configToken, 'GET')
  assert.equal(none.status, 200)
  assert.equal(none.json.loggedIn, false)
  assert.equal(none.json.token, null)
  assert.equal(none.json.expiresAt, null)
})

test('JWT 解不开时不许崩：时间戳回 null，登录态照旧', async () => {
  const home = freshHome()
  const mounted = mount(home)
  writeEgmpSession(home, { baseUrl: 'https://standard-val.aksoegmp.com', account: 'liyulong', token: 'not-a-jwt', savedAt: 1 })
  const view = await call(mounted.routes, P.configToken, 'GET')
  assert.equal(view.status, 200)
  assert.equal(view.json.loggedIn, true)
  assert.equal(view.json.token, null)
  assert.equal(view.json.issuedAt, null)
  assert.equal(view.json.expiresAt, null)
})

/* ─────────────── 选项集：同一个 code 再提交就是更新 ─────────────── */

test('选项集计划：按 code 认出更新，少传的旧选项点名提醒但不算不可逆', async () => {
  const home = freshHome()
  const blueprint = (options: Array<{ name: string; code?: string }>) => ({
    version: 1,
    env: 'test',
    resources: [{ kind: 'picklist', name: '验证选项集', code: 'akw_test_pk__c', options }],
  } as never)
  const run = async (existing: Array<{ code: string; name: string }> | null, options: Array<{ name: string; code?: string }>) => {
    const egmp = await startFakeEgmp(existing)
    try {
      writeEgmpSession(home, { baseUrl: egmp.baseUrl, account: 'tester', token: 'jwt', savedAt: Date.now() })
      const plan = await planBlueprint({ baseUrl: egmp.baseUrl, account: 'tester', token: 'jwt', savedAt: Date.now() }, blueprint(options), home)
      return plan.steps[0]!
    } finally {
      await egmp.close()
    }
  }

  // ① 平台上还没有 ⇒ 新建
  const created = await run(null, [{ name: '主要变更', code: 'major__c' }])
  assert.equal(created.action, 'create')
  assert.equal(created.irreversible, false)
  assert.deepEqual(created.warnings, [])

  // ② 提交里没有旧选项 ⇒ 平台并不会删（2026-10-27 实测），所以既不提交、也不算不可逆，只提醒
  const removed = await run(
    [{ code: 'major__c', name: '主要变更' }, { code: 'legacy__c', name: '旧选项' }],
    [{ name: '主要变更', code: 'major__c' }],
  )
  assert.equal(removed.action, 'skip', '只少传旧选项时提交也删不掉，不必写')
  assert.equal(removed.irreversible, false)
  assert.equal(removed.risk, 'low')
  assert.match(removed.warnings.join(' '), /旧选项\(legacy__c\)/, '要点名少传了哪个选项')
  assert.match(removed.warnings.join(' '), /只增不删/, '要说清平台不会删')

  // ②b 少传旧选项 + 加新选项 ⇒ 真更新，但仍然不是不可逆
  const mixed = await run(
    [{ code: 'major__c', name: '主要变更' }, { code: 'legacy__c', name: '旧选项' }],
    [{ name: '主要变更', code: 'major__c' }, { name: '新选项', code: 'fresh__c' }],
  )
  assert.equal(mixed.action, 'update')
  assert.equal(mixed.irreversible, false)
  assert.match(mixed.warnings.join(' '), /只增不删/)

  // ③ 一模一样 ⇒ 跳过（不算写入，不用问人）
  const skipped = await run([{ code: 'major__c', name: '主要变更' }], [{ name: '主要变更', code: 'major__c' }])
  assert.equal(skipped.action, 'skip')

  // ④ 只改名字 ⇒ 更新，但不算不可逆（编码没变，历史值不受影响）
  const renamed = await run([{ code: 'major__c', name: '主要变更' }], [{ name: '主要变更（含紧急）', code: 'major__c' }])
  assert.equal(renamed.action, 'update')
  assert.equal(renamed.irreversible, false)
  assert.match(renamed.warnings.join(' '), /只改了名称/)

  // ⑤ 没写 code 的选项按位置补，并提前说明（不然回读比对不了）
  const generated = await run(null, [{ name: '无编码选项' }])
  const options = (generated.params as { options: Array<{ code: string }> }).options
  assert.equal(options[0]?.code, 'opt_0__c')
  assert.match(generated.warnings.join(' '), /没写 code/)
})

test('选项集回读：平台留下的旧选项不算失败，只在消息里交代一句', async () => {
  const home = freshHome()
  const mounted = mount(home, undefined, true)
  const applyTool = mounted.tools.find((tool) => tool.name === 'akso_workbench_config_apply')!
  // 平台上 4 个选项，这次只提交 3 个（真实场景：2026-10-27 实测 low__c 没被删）
  const egmp = await startFakeEgmp([
    { code: 'major__c', name: '主要变更' },
    { code: 'minor__c', name: '次要变更' },
    { code: 'urgent__c', name: '紧急变更' },
    { code: 'low__c', name: '低风险变更' },
  ])
  writeAccount(home, egmp.baseUrl)
  try {
    const plan = {
      planId: 'pln_extra',
      createdAt: Date.now(),
      expiresAt: Date.now() + 30 * 60 * 1000,
      env: 'test',
      target: { baseUrl: egmp.baseUrl, accountId: ACCOUNT_ID, account: 'tester' },
      summary: { create: 0, update: 1, skip: 0, irreversible: 0 },
      steps: [{
        id: 's1',
        kind: 'picklist',
        action: 'update',
        title: '选项集 akso-workbench 验证选项集（akw_test_pk__c）',
        naturalKey: 'akw_test_pk__c',
        risk: 'low',
        irreversible: false,
        capabilityId: 'picklist.save',
        params: {
          name: 'akso-workbench 验证选项集',
          code: 'akw_test_pk__c',
          options: [
            { name: '主要变更', code: 'major__c', status: 1, operations: 1, sort: 0 },
            { name: '次要变更', code: 'minor__c', status: 1, operations: 1, sort: 1 },
            { name: '紧急变更', code: 'urgent__c', status: 1, operations: 1, sort: 2 },
          ],
        },
        dependsOn: [],
        warnings: [],
      }],
      warnings: [],
      status: 'planned',
      results: [],
    }
    savePlan(home, plan as never)
    const running = applyTool.execute({ planId: 'pln_extra' }, { agent: { id: 'agent-1' } }) as Promise<Record<string, any>>
    const pending = await waitPending(mounted.routes)
    await call(mounted.routes, P.configApprovalDecide, 'POST', { id: pending.id, verdict: 'approve' })
    const result = await running
    assert.equal(result.ok, true)
    assert.equal(result.results[0].status, 'verified', '多出来的旧选项不该把这一步判失败')
    assert.match(String(result.results[0].message), /另有 1 个/)
    assert.match(String(result.results[0].message), /low__c/)
  } finally {
    await egmp.close()
  }
})

/* ─────────────── call：文档里其余接口按能力目录原样调用 ─────────────── */

/** 一张能解出 iat/exp 的 JWT：只有 payload 有意义，签名是摆设（平台侧不校验）。 */
function jwtFor(ttlMs: number, now = Date.now()): string {
  const enc = (value: Record<string, unknown>): string => Buffer.from(JSON.stringify(value)).toString('base64url')
  return [
    enc({ alg: 'none', typ: 'JWT' }),
    enc({ sub: 'liyulong', iat: Math.floor(now / 1000), exp: Math.floor((now + ttlMs) / 1000) }),
    'sig',
  ].join('.')
}

test('call 资源：按目录生成一步、不比对现状；未知能力/缺必填参数在计划阶段就挡住', async () => {
  const home = freshHome()
  const session = { baseUrl: 'https://example.invalid', account: 'tester', token: 'jwt', savedAt: Date.now() }
  const blueprint = (resources: unknown[]) => ({ version: 1, env: 'test', resources } as never)

  const plan = await planBlueprint(session, blueprint([
    { kind: 'call', capability: 'workflow.status.change', params: { ids: ['cfg-1'], status: 40, workflowBasicId: 'wb-1' } },
  ]), home)
  assert.equal(plan.steps.length, 1)
  const step = plan.steps[0]!
  assert.equal(step.kind, 'call')
  assert.equal(step.action, 'create')
  assert.equal(step.capabilityId, 'workflow.status.change')
  assert.deepEqual(step.params, { ids: ['cfg-1'], status: 40, workflowBasicId: 'wb-1' })
  assert.equal(step.irreversible, false)
  assert.match(step.warnings.join(' '), /原样调用 POST \/api\/platform\/Workflow\/ChangeWorkflowBasicStatus/)
  assert.match(step.warnings.join(' '), /不做差异比对/)

  await assert.rejects(
    () => planBlueprint(session, blueprint([{ kind: 'call', capability: 'workflow.task.add', params: {} }]), home),
    /缺少必填参数/,
    'step 信封是必填参数，漏了就别执行',
  )
  await assert.rejects(
    () => planBlueprint(session, blueprint([{ kind: 'call', capability: 'workflow.nope', params: {} }]), home),
    /未知能力/,
  )
})


test('令牌过期：账号条拿账号 id 找云端换新票，口令不经过 host', async () => {
  const home = freshHome()
  const cloud = await startFakeCloud()
  try {
    const { routes } = mount(home, cloud.url)
    await loginWith(routes)
    const site = await call(routes, P.siteCreate, 'POST', { baseUrl: 'https://egmp.example.com' })
    const siteId = (site.json.sites as Array<{ id: string }>)[0]!.id
    const created = await call(routes, P.accountCreate, 'POST', { siteId, username: 'liyulong', password: 'pw-secret' })
    const accountId = (created.json.accounts as Array<{ id: string }>)[0]!.id
    await call(routes, P.poolToggle, 'POST', { id: accountId, inPool: true })
    await call(routes, P.accountCurrent, 'POST', { id: accountId })

    // ① 旧票已过期：一次 GET 就让云端代登录换回新票（host 只发账号 id）
    writeEgmpSession(home, { baseUrl: 'https://egmp.example.com', account: 'liyulong', token: jwtFor(-60_000), savedAt: Date.now() - 3_600_000 })
    const res = await call(routes, P.configToken, 'GET')
    assert.equal(res.status, 200)
    assert.equal(res.json.refreshed, true)
    assert.equal(res.json.token, null, 'JWT 本体不下发浏览器：页面只读时间')
    assert.equal(res.json.account, 'liyulong')
    assert.ok(Number(res.json.expiresAt) > Date.now(), '到期时间应当是新的')
    assert.equal(res.raw.includes('pw-secret'), false, '口令不能出现在任何响应里')

    // 云端只收到"账号 id"；口令是它自己库里那一条，由它解密后换成票
    assert.equal(cloud.state.mintedTokens.length, 1, '换票只应发生一次')
    const minted = cloud.state.mintedTokens[0] as { accountId: string; password: string; token: string }
    assert.equal(minted.accountId, accountId)
    assert.equal(minted.password, 'pw-secret')

    // ② 新票已落盘（就是云端发来的那张）：下一次读不再换票
    const saved = JSON.parse(readFileSync(join(home, 'akso-workbench', 'egmp-session.json'), 'utf8')) as { token: string }
    assert.equal(saved.token, minted.token)
    const again = await call(routes, P.configToken, 'GET')
    assert.equal(again.json.refreshed, false)
    assert.equal(cloud.state.mintedTokens.length, 1)

    // ③ 没连账号：给原因，不硬造一张假票
    await call(routes, P.accountCurrent, 'POST', { id: '' })
    writeEgmpSession(home, { baseUrl: 'https://egmp.example.com', account: 'liyulong', token: jwtFor(-60_000), savedAt: Date.now() })
    const bare = await call(routes, P.configToken, 'GET')
    assert.equal(bare.json.refreshed, false)
    assert.match(String(bare.json.reason), /还没有连接账号/)
  } finally {
    await cloud.close()
  }
})

test('令牌续期：剩 4 分钟提前换票，剩 6 分钟不换；并发只换一张', async () => {
  const home = freshHome()
  const cloud = await startFakeCloud()
  try {
    const { routes } = mount(home, cloud.url)
    await loginWith(routes)
    const site = await call(routes, P.siteCreate, 'POST', { baseUrl: 'https://egmp.example.com' })
    const siteId = (site.json.sites as Array<{ id: string }>)[0]!.id
    const created = await call(routes, P.accountCreate, 'POST', { siteId, username: 'liyulong', password: 'pw-secret' })
    const accountId = (created.json.accounts as Array<{ id: string }>)[0]!.id
    await call(routes, P.poolToggle, 'POST', { id: accountId, inPool: true })
    await call(routes, P.accountCurrent, 'POST', { id: accountId })

    // ① 只剩 4 分钟：提前换票
    writeEgmpSession(home, { baseUrl: 'https://egmp.example.com', account: 'liyulong', token: jwtFor(4 * 60_000), savedAt: Date.now() })
    const near = await call(routes, P.configToken, 'GET')
    assert.equal(near.json.refreshed, true, '剩 4 分钟应当提前换票')
    assert.equal(cloud.state.mintedTokens.length, 1)

    // ② 剩 6 分钟：还不需要换
    writeEgmpSession(home, { baseUrl: 'https://egmp.example.com', account: 'liyulong', token: jwtFor(6 * 60_000), savedAt: Date.now() })
    const far = await call(routes, P.configToken, 'GET')
    assert.equal(far.json.refreshed, false, '剩 6 分钟还不到续期阈值')
    assert.equal(cloud.state.mintedTokens.length, 1)

    // ③ 并发两个请求：单飞，只换一张票
    writeEgmpSession(home, { baseUrl: 'https://egmp.example.com', account: 'liyulong', token: jwtFor(60_000), savedAt: Date.now() })
    const [a, b] = await Promise.all([call(routes, P.configToken, 'GET'), call(routes, P.configToken, 'GET')])
    assert.equal(a.status, 200)
    assert.equal(b.status, 200)
    assert.equal(cloud.state.mintedTokens.length, 2, '并发只应换一张票')
  } finally {
    await cloud.close()
  }
})

test('工具 UID：三个注册工具都有唯一 UID，未登记的工具拒绝注册', async () => {
  const { TOOL_UIDS, toolUid, withToolUid } = await import('../src/tool-uids.ts')

  // ① DSH 侧能看到的三个工具都在表里（新增工具必须同步）
  assert.deepEqual(
    Object.keys(TOOL_UIDS).sort(),
    ['akso_workbench_accounts', 'akso_workbench_config_apply', 'akso_workbench_config_plan'],
  )

  // ② 格式与唯一性
  const uids = Object.values(TOOL_UIDS) as string[]
  assert.equal(new Set(uids).size, uids.length, 'UID 不能重复')
  for (const uid of uids) assert.match(uid, /^AKW-TOOL-[A-Z0-9]+(-[A-Z0-9]+)*-V\d+$/)

  // ③ 包装后：返回对象带 _uid、描述带 UID、原定义不被改
  const base = {
    name: 'akso_workbench_accounts',
    description: '读取账号快照',
    async execute() {
      return { ok: true }
    },
  }
  const wrapped = withToolUid(base)
  assert.equal(wrapped.uid, toolUid('akso_workbench_accounts'))
  assert.match(String(wrapped.description), /UID: AKW-TOOL-ACCOUNTS-V1/)
  assert.equal((base as { uid?: string }).uid, undefined, '不要改原对象')
  const value = (await wrapped.execute()) as { _uid?: string; ok?: boolean }
  assert.equal(value._uid, 'AKW-TOOL-ACCOUNTS-V1')
  assert.equal(value.ok, true)

  // ④ 没登记就注册：直接抛错（fail closed，不允许“先上线再补”）
  assert.throws(
    () => withToolUid({ name: 'akso_workbench_unregistered', async execute() { return {} } }),
    /没有登记 UID/,
  )
})

test('实测台账：名单里的 id 都在能力目录里，且与卡片标签一一对应', async () => {
  const { APPROVED_TOOL_IDS } = await import('../src/shared/approved-tools.ts')
  const ids = new Set(CAPABILITIES.map((cap) => cap.id))

  // ① 台账里写错一个 id，卡片会永远标"已验证"却指向不存在的接口
  for (const id of APPROVED_TOOL_IDS) assert.ok(ids.has(id), `台账里的 ${id} 不在能力目录里`)

  // ② 目录里的 testStatus 必须由台账算出来（防止"名单改了、状态没跟上"）
  for (const cap of CAPABILITIES) {
    assert.equal(
      cap.testStatus,
      APPROVED_TOOL_IDS.has(cap.id) ? 'verified' : 'stale',
      `${cap.id} 的 testStatus 与台账不一致`,
    )
  }

  // ③ 目录里已删除的 menu.sort 不应再出现在台账或卡片上
  assert.ok(!ids.has('menu.sort'), 'menu.sort 已从目录删除')
})



test('日志：写入审计带目标账号，审批决策单独留痕，HTTP 请求各一行 access log', async () => {
  const home = freshHome()
  const logs: string[] = []
  const mounted = mount(home, undefined, true, true, logs)
  const egmp = await startFakeEgmp([{ code: 'major__c', name: '主要变更' }])
  writeAccount(home, egmp.baseUrl)
  const applyTool = mounted.tools.find((tool) => tool.name === 'akso_workbench_config_apply')!
  const month = new Date().toISOString().slice(0, 7)
  const auditFile = join(home, 'akso-workbench', 'config-audit', `${month}.jsonl`)
  const decisionsFile = join(home, 'akso-workbench', 'config-audit', `approvals-${month}.jsonl`)
  try {
    // ① 拒批：平台一步不写，但"谁拒了哪一版计划"要留痕
    savePlan(home, planFixture('pln_logdeny', { create: 1, update: 0, irreversible: false }, egmp.baseUrl))
    const denyRun = applyTool.execute({ planId: 'pln_logdeny' }, {})
    const denyPending = await waitPending(mounted.routes)
    await call(mounted.routes, ROUTES.configApprovalDecide, 'POST', { id: denyPending.id, verdict: 'deny' })
    const denied = (await denyRun) as { status: string }
    assert.equal(denied.status, 'denied')
    assert.equal(egmp.hits, 0)
    assert.ok(existsSync(decisionsFile), '审批决策应落盘')
    const denyRecord = JSON.parse(readFileSync(decisionsFile, 'utf8').trim().split('\n').at(-1)!) as Record<string, unknown>
    assert.equal(denyRecord['verdict'], 'deny')
    assert.equal(denyRecord['planId'], 'pln_logdeny')
    assert.equal(denyRecord['accountId'], ACCOUNT_ID)
    assert.equal(denyRecord['baseUrl'], egmp.baseUrl)
    assert.equal(typeof denyRecord['hash'], 'string')

    // ② 同意并写入：每条步骤审计都要能回答"写到了哪个环境、哪个账号"
    savePlan(home, planFixture('pln_logapprove', { create: 1, update: 0, irreversible: false }, egmp.baseUrl))
    const approveRun = applyTool.execute({ planId: 'pln_logapprove' }, {})
    const approvePending = await waitPending(mounted.routes)
    await call(mounted.routes, ROUTES.configApprovalDecide, 'POST', { id: approvePending.id, verdict: 'approve' })
    const applied = (await approveRun) as { ok: boolean }
    assert.equal(applied.ok, true)
    assert.ok(existsSync(auditFile), '写入审计应落盘')
    const auditRecords = readFileSync(auditFile, 'utf8').trim().split('\n').map((line) => JSON.parse(line) as Record<string, unknown>)
    assert.ok(auditRecords.length > 0)
    for (const record of auditRecords) {
      assert.equal(record['accountId'], ACCOUNT_ID)
      assert.equal(record['baseUrl'], egmp.baseUrl)
    }

    // ③ 两类记录分文件：读审计接口只回写入审计，不混进审批决策
    const auditApi = await call(mounted.routes, ROUTES.configAudit, 'GET')
    const apiRecords = (auditApi.json as { records: Array<Record<string, unknown>> }).records
    assert.ok(apiRecords.length > 0)
    assert.ok(apiRecords.every((record) => record['verdict'] === undefined))

    // ④ access log：正常请求与围栏 403 各一行
    assert.ok(logs.some((line) => line.includes('[http] GET') && line.includes('/config/approval/pending')), '应记录轮询请求')
    assert.ok(logs.some((line) => line.includes('-> 200')), '应记录状态码')
    await call(mounted.routes, ROUTES.sync, 'POST', {}, '127.0.0.1', { host: '127.0.0.1:60717' })
    assert.ok(logs.some((line) => line.includes('[http] POST') && line.includes('-> 403')), '被围栏挡掉的请求也要记录')
  } finally {
    await egmp.close()
  }
})
