/**
 * 假 akso-cloud：只实现插件用到的那几条接口 + 真实的 revision 语义。
 *
 * 为什么要它：插件与云端之间**唯一的契约**就是这些 JSON 形状与 revision；
 * 拿真服务端跑单测要真实账号与网络，而这里可以把"写 → revision++ → 整份快照"验干净。
 */
import http from 'node:http'

const DEFAULT_BOX = '默认盒'

function readBody(req) {
  return new Promise((resolve) => {
    const chunks = []
    req.on('data', (c) => chunks.push(c))
    req.on('end', () => {
      try {
        resolve(chunks.length === 0 ? {} : JSON.parse(Buffer.concat(chunks).toString('utf8')))
      } catch {
        resolve({})
      }
    })
  })
}

function send(res, status, payload) {
  const body = JSON.stringify(payload)
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json')
  res.end(body)
}

export function startFakeCloud(options = {}) {
  const state = {
    revision: 0,
    accounts: [],
    sites: [],
    boxes: [{ name: DEFAULT_BOX, isDefault: true, disabled: true, autoDisabled: true, count: 0, siteId: '', siteName: '' }],
  }
  const tokens = new Map()
  /** deviceCode → { userCode, status, email, token } */
  const devices = new Map()
  let deviceCodes = 0
  let seq = 0
  const id = (prefix) => `${prefix}-${++seq}`

  const siteById = (siteId) => state.sites.find((s) => s.id === siteId)
  const boxByName = (name) => state.boxes.find((b) => b.name === name)
  const recount = () => {
    for (const site of state.sites) site.accountCount = state.accounts.filter((a) => a.siteId === site.id).length
    for (const box of state.boxes) {
      box.count = state.accounts.filter((a) => (a.box || DEFAULT_BOX) === box.name).length
      if (box.siteId) {
        const site = siteById(box.siteId)
        box.siteName = site?.name ?? ''
      } else {
        box.siteName = ''
      }
    }
  }

  const snapshot = () => {
    recount()
    return {
      format: 'akso-cloud-ui-state',
      version: 1,
      revision: state.revision,
      accounts: state.accounts,
      sites: state.sites,
      boxes: state.boxes,
      defaultBox: DEFAULT_BOX,
      counts: { accounts: state.accounts.length, sites: state.sites.length, boxes: state.boxes.length },
    }
  }

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url ?? '/', 'http://127.0.0.1')
    const path = url.pathname
    const bearer = (req.headers.authorization ?? '').replace(/^Bearer\s+/i, '')
    const authed = tokens.has(bearer)
    const body = req.method === 'POST' || req.method === 'PATCH' ? await readBody(req) : {}

    // 仅测试用：模拟"人在浏览器里点了批准"（真云端是网页 POST /api/auth/device-approve）
    if (path === '/__approve' && req.method === 'POST') {
      const userCode = String(body.userCode ?? '')
      let ok = false
      for (const record of devices.values()) {
        if (record.userCode !== userCode) continue
        record.status = 'approved'
        record.email = 'device@example.com'
        record.token = 'tok-device-' + Date.now()
        tokens.set(record.token, record.email)
        ok = true
      }
      send(res, 200, { ok })
      return
    }
    if (path === '/api/auth/login' && req.method === 'POST') {
      if (String(body.password ?? '') === 'wrong') {
        send(res, 401, { detail: '邮箱或密码不正确' })
        return
      }
      const email = String(body.email ?? '')
      const token = `tok-${email}-${Date.now()}`
      tokens.set(token, email)
      send(res, 200, { token, user: { id: 'user-1', email, displayName: '测试用户' } })
      return
    }

    /* ── 设备流（RFC 8628）：与线上同形 ── */
    if (path === '/api/auth/device-start' && req.method === 'POST') {
      const deviceCode = `dev-${++seq}`
      const userCode = `CODE-${++deviceCodes}`
      devices.set(deviceCode, { userCode, status: 'pending', email: '', token: '' })
      const port = server.address().port
      send(res, 200, {
        deviceCode,
        userCode,
        verificationUrl: `http://127.0.0.1:${port}/authorize?code=${userCode}`,
        expiresIn: 600,
        interval: 1,
        clientName: String(body.clientName ?? ''),
      })
      return
    }
    if (path === '/api/auth/device-poll' && req.method === 'POST') {
      const record = devices.get(String(body.deviceCode ?? ''))
      if (record === undefined) {
        send(res, 200, { status: 'unknown' })
        return
      }
      if (record.status === 'denied') {
        send(res, 200, { status: 'denied' })
        return
      }
      if (record.status !== 'approved') {
        send(res, 200, { status: 'pending', interval: 1 })
        return
      }
      if (record.token === '') {
        send(res, 200, { status: 'consumed' })
        return
      }
      const token = record.token
      record.token = ''
      send(res, 200, { status: 'approved', token, user: { id: 'user-1', email: record.email, displayName: '测试用户' } })
      return
    }
    if (path === '/api/auth/me' && req.method === 'GET') {
      if (!authed) {
        send(res, 401, { detail: '登录已失效' })
        return
      }
      send(res, 200, { user: { id: 'user-1', email: tokens.get(bearer), displayName: '测试用户' } })
      return
    }
    if (!authed) {
      send(res, 401, { detail: '登录已失效' })
      return
    }
    if (path === '/api/ui/state' && req.method === 'GET') {
      send(res, 200, snapshot())
      return
    }
    if (path === '/api/accounts' && req.method === 'POST') {
      const site = siteById(String(body.siteId ?? ''))
      if (site === undefined) {
        send(res, 404, { detail: '站点不存在' })
        return
      }
      if (String(body.password ?? '') === '') {
        send(res, 400, { detail: '新建账号必须给密码' })
        return
      }
      const boxName = String(body.box ?? '') || DEFAULT_BOX
      if (boxByName(boxName) === undefined) {
        send(res, 400, { detail: `盒子不存在：${boxName}` })
        return
      }
      const now = Date.now()
      const account = {
        id: id('acc'),
        siteId: site.id,
        siteName: site.name,
        siteHost: site.host,
        baseUrl: site.baseUrl,
        username: String(body.username ?? ''),
        tabName: String(body.tabName ?? ''),
        role: 'user',
        tags: [],
        note: String(body.note ?? ''),
        status: 'active',
        box: boxName,
        scheme: '',
        hasPassword: true,
        createdAt: now,
        updatedAt: now,
      }
      state.accounts.unshift(account)
      state.revision++
      send(res, 200, account)
      return
    }
    const accountMatch = /^\/api\/accounts\/([^/]+)$/.exec(path)
    if (accountMatch && req.method === 'DELETE') {
      const target = decodeURIComponent(accountMatch[1])
      const before = state.accounts.length
      state.accounts = state.accounts.filter((a) => a.id !== target)
      if (state.accounts.length === before) {
        send(res, 404, { detail: '账号不存在' })
        return
      }
      state.revision++
      send(res, 200, { ok: true })
      return
    }
    if (path === '/api/sites' && req.method === 'POST') {
      const raw = String(body.baseUrl ?? '').trim()
      if (raw === '') {
        send(res, 400, { detail: '站点地址不能为空' })
        return
      }
      const host = raw.replace(/^https?:\/\//i, '').split('/')[0].toLowerCase()
      const site = {
        id: id('site'),
        name: String(body.name ?? '') || host,
        baseUrl: raw,
        host,
        note: String(body.note ?? ''),
        accountCount: 0,
      }
      state.sites.unshift(site)
      if (body.createBox !== false && boxByName(site.name) === undefined) {
        state.boxes.push({ name: site.name, isDefault: false, disabled: false, autoDisabled: false, count: 0, siteId: site.id, siteName: site.name })
      }
      state.revision++
      send(res, 200, site)
      return
    }
    const siteMatch = /^\/api\/sites\/([^/]+)$/.exec(path)
    if (siteMatch && req.method === 'DELETE') {
      const target = decodeURIComponent(siteMatch[1])
      const before = state.sites.length
      state.sites = state.sites.filter((s) => s.id !== target)
      if (state.sites.length === before) {
        send(res, 404, { detail: '站点不存在' })
        return
      }
      state.accounts = state.accounts.filter((a) => a.siteId !== target)
      state.revision++
      send(res, 200, { ok: true })
      return
    }
    if (path === '/api/boxes/rename' && req.method === 'POST') {
      const source = String(body.source ?? '')
      const target = String(body.target ?? '')
      const box = boxByName(source)
      if (box === undefined || target === '') {
        send(res, 400, { detail: '盒子不存在' })
        return
      }
      if (box.isDefault) {
        send(res, 400, { detail: '默认盒不能改名' })
        return
      }
      box.name = target
      for (const account of state.accounts) if (account.box === source) account.box = target
      state.revision++
      send(res, 200, { ok: true })
      return
    }
    if (path === '/api/boxes/delete' && req.method === 'POST') {
      const name = String(body.name ?? '')
      const box = boxByName(name)
      if (box === undefined) {
        send(res, 404, { detail: '盒子不存在' })
        return
      }
      if (box.isDefault) {
        send(res, 400, { detail: '默认盒不能删除' })
        return
      }
      state.boxes = state.boxes.filter((b) => b.name !== name)
      if (body.deleteAccounts === true) state.accounts = state.accounts.filter((a) => a.box !== name)
      state.revision++
      send(res, 200, { ok: true })
      return
    }
    if (path === '/api/boxes/bind-site' && req.method === 'POST') {
      const name = String(body.name ?? '')
      const box = boxByName(name)
      const site = siteById(String(body.siteId ?? ''))
      if (box === undefined || site === undefined) {
        send(res, 400, { detail: '盒子或站点不存在' })
        return
      }
      box.siteId = site.id
      state.revision++
      send(res, 200, { ok: true })
      return
    }
    send(res, 404, { detail: `没有这个接口：${path}` })
  })

  return new Promise((resolve) => {
    server.listen(options.port ?? 0, '127.0.0.1', () => {
      const address = server.address()
      resolve({
        url: `http://127.0.0.1:${address.port}`,
        state,
        /** 模拟「人在浏览器里点了批准」 */
        approve(userCode, email = 'qa@example.com') {
          for (const record of devices.values()) {
            if (record.userCode !== userCode) continue
            record.status = 'approved'
            record.email = email
            record.token = `tok-device-${Date.now()}`
            tokens.set(record.token, email)
            return true
          }
          return false
        },
        close: () => new Promise((done) => server.close(() => done())),
      })
    })
  })
}
