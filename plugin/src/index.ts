/**
 * akso-workbench · DSH 插件（node 半边）
 *
 * 职责：**把 akso-cloud 的账号库搬到宿主侧**，让浏览器半边只跟本机说同一种话。
 *
 * ## 三条硬约束
 *
 * 1. **令牌不下发**：`/api/auth/login` 换来的 bearer 只存在本机 `cloud.json`（0600），
 *    client 只拿得到"已登录 + 邮箱/显示名"。
 * 2. **一份快照喂所有座位**：读侧只认 akso-cloud 的 `/api/ui/state`（服务端保证
 *    accounts/sites/boxes 同一次读取 + revision），host 把它缓存在**一个变量**里；
 *    账号条、账号中心、站点/盒子页读的都是它 —— 这就是"各处数据一致"的实现方式。
 * 3. **写操作后统一回拉**：任何写都走 `cloud op → sync() → 返回整份新快照`，
 *    不做"本地先改再异步对齐"（那正是两份数据开始漂移的地方）。
 *
 * 围栏：只允许 loopback（本机 GUI）访问；先过围栏再解析 body。
 */

import { execFile } from 'node:child_process'
import { homedir } from 'node:os'
import { join } from 'node:path'

import * as cloud from './cloud.ts'
import { clearSession, readPool, readPrefs, readSession, writePool, writePrefs, writeSession } from './store.ts'
import { buildSnapshotTool } from './tool.ts'
import { CAPABILITIES, groupByDomain } from './config/capabilities.ts'
import { applyPlan, invokeCapability, loadPlan, planBlueprint, readAudit } from './config/engine.ts'
import { decideApproval, listApprovals } from './config/approvals.ts'
import { EgmpError, clearEgmpSession, login as egmpLogin, publicSession, readEgmpSession, writeEgmpSession } from './config/session.ts'
import { buildConfigApplyTool, buildConfigPlanTool } from './config/tools.ts'

export const name = 'akso-workbench'
export const inject = ['webServer']

const MAX_BODY = 128 * 1024
const MAX_TEXT = 200

/** 用系统默认浏览器打开（Windows `start` / macOS `open` / Linux `xdg-open`）。 */
function openExternalUrl(url: string): void {
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return
  }
  // 只放行 http(s)：这个 URL 来自服务端响应，不能变成"打开本地文件/协议"的入口
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return
  const command = process.platform === 'win32' ? 'cmd' : process.platform === 'darwin' ? 'open' : 'xdg-open'
  const args = process.platform === 'win32' ? ['/c', 'start', '', parsed.toString()] : [parsed.toString()]
  try {
    execFile(command, args, () => {
      // 打不开就算了：界面上还有可复制的链接与码
    })
  } catch {
    // 同上
  }
}

export interface PluginConfig {
  dshHome?: string
  /** 覆盖 akso-cloud 地址（测试/自建部署用）；默认 https://www.dragonrain.top */
  cloudBaseUrl?: string
  /** 测试用：替换"打开浏览器"的行为 */
  openBrowser?: (url: string) => void
  enabled?: boolean
}

interface StatePayload {
  format: 'akso-workbench-state'
  loggedIn: boolean
  profile: { email: string; displayName: string; baseUrl: string } | null
  revision: number | null
  syncedAt: number | null
  /** 有旧快照、但这次没同步成功 */
  stale: boolean
  syncError: string | null
  accounts: cloud.CloudAccount[]
  sites: cloud.CloudSite[]
  boxes: cloud.CloudBox[]
  defaultBox: string
  counts: { accounts: number; sites: number; boxes: number }
  currentAccountId: string | null
  /** 配置池：允许 agent 登录的账号；`pool.json` 不存在时是**空池**（首次使用由用户自己加）。 */
  poolAccountIds: string[]
}

/**
 * 路由表 —— **导出**是给 host.test.ts 用的：
 * 测试直接拿这张表比对"注册出来的路由"，新增端点时不用再手工同步一份。
 */
export const ROUTES = {
  state: '/api/akso-workbench/state',
  login: '/api/akso-workbench/cloud/login',
  logout: '/api/akso-workbench/cloud/logout',
  sync: '/api/akso-workbench/cloud/sync',
  deviceStart: '/api/akso-workbench/cloud/device/start',
  devicePoll: '/api/akso-workbench/cloud/device/poll',
  accountCreate: '/api/akso-workbench/accounts/create',
  accountCurrent: '/api/akso-workbench/accounts/current',
  poolToggle: '/api/akso-workbench/pool/toggle',
  siteCreate: '/api/akso-workbench/sites/create',
  boxRename: '/api/akso-workbench/boxes/rename',
  boxBind: '/api/akso-workbench/boxes/bind-site',
  health: '/api/akso-workbench/health',
  configCapabilities: '/api/akso-workbench/config/capabilities',
  configSession: '/api/akso-workbench/config/session',
  configLogin: '/api/akso-workbench/config/login',
  configLogout: '/api/akso-workbench/config/logout',
  configInvoke: '/api/akso-workbench/config/invoke',
  configPlan: '/api/akso-workbench/config/plan',
  configPlanGet: '/api/akso-workbench/config/plan-status',
  configApply: '/api/akso-workbench/config/apply',
  configApprovals: '/api/akso-workbench/config/approvals',
  configApprovalDecide: '/api/akso-workbench/config/approvals/decide',
  configAudit: '/api/akso-workbench/config/audit',
} as const

/* ────────────────────────────── HTTP 小工具 ────────────────────────────── */

function isLoopback(req: any): boolean {
  const address = String(req?.socket?.remoteAddress ?? '')
  if (address === '::1' || address === '::ffff:127.0.0.1') return true
  return /^127\./.test(address.replace(/^::ffff:/, ''))
}

/** 浏览器侧自报的"我是插件自己"。跨站表单设不了自定义头，跨源 fetch 会先撞 preflight（我们不回 CORS 头）。 */
export const CLIENT_HEADER = 'x-akso-workbench'

const LOOPBACK_HOST = /^(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d{1,5})?$/i

/**
 * 第二道围栏。只看 `remoteAddress` 挡不住两类东西：
 *   ① **DNS rebinding** —— 恶意域名解析到 127.0.0.1，浏览器照样把请求打进 loopback；
 *   ② **别的网页发起的请求** —— 浏览器会带 `Origin`，而 `http://evil.com` 的页面能 POST 到本机。
 * 所以再查三件事：`Host` 必须是本机名、`Origin`（有的话）必须同源、跨站标记必须干净；
 * 写请求（POST）还要求带自定义头 —— 这一条是 CSRF 的硬闸门，因为跨站请求既设不了自定义头，
 * 也过不了 preflight（我们从不回 `Access-Control-*`）。
 * GET 不要求自定义头：GET 全是只读，跨站读不到响应体（没有 CORS），而 `curl` 探活更方便。
 */
export function requestFence(req: any, method: string): string | null {
  const headers = (req?.headers ?? {}) as Record<string, unknown>
  const text = (value: unknown) => String(value ?? '').trim()
  const host = text(headers.host)
  if (!LOOPBACK_HOST.test(host)) return `forbidden: host 不是本机地址（${host || '缺失'}）`
  const origin = text(headers.origin)
  if (origin !== '') {
    if (origin === 'null') return 'forbidden: 跨站请求（origin: null）'
    let originHost = ''
    try {
      originHost = new URL(origin).host
    } catch {
      return 'forbidden: origin 不合法'
    }
    if (originHost.toLowerCase() !== host.toLowerCase()) return `forbidden: 跨站请求（${originHost} ≠ ${host}）`
  }
  const site = text(headers['sec-fetch-site'])
  if (site !== '' && site !== 'same-origin' && site !== 'none') return `forbidden: sec-fetch-site=${site}`
  if (method === 'POST' && text(headers[CLIENT_HEADER]) !== '1') return `forbidden: 缺少 ${CLIENT_HEADER} 请求头`
  return null
}

function sendJson(res: any, status: number, payload: unknown): void {
  const body = JSON.stringify(payload)
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json; charset=utf-8')
  res.setHeader('Cache-Control', 'no-store')
  res.end(body)
}

function readBody(req: any): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    let size = 0
    const chunks: Buffer[] = []
    req.on('data', (chunk: Buffer) => {
      size += chunk.length
      if (size > MAX_BODY) {
        reject(new Error('请求体过大'))
        if (typeof req.destroy === 'function') req.destroy()
        return
      }
      chunks.push(chunk)
    })
    req.on('end', () => {
      if (chunks.length === 0) {
        resolve({})
        return
      }
      try {
        const parsed = JSON.parse(Buffer.concat(chunks).toString('utf8')) as unknown
        resolve(parsed !== null && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {})
      } catch (error) {
        reject(error instanceof Error ? error : new Error(String(error)))
      }
    })
    req.on('error', (error: Error) => reject(error))
  })
}

function text(value: unknown, max = MAX_TEXT): string {
  return typeof value === 'string' ? value.trim().slice(0, max) : ''
}

/* ───────────────────────────────── 插件 ───────────────────────────────── */

export function apply(ctx: any, config: PluginConfig = {}): void {
  if (config.enabled === false) return
  const dshHome = config.dshHome ?? process.env.DSH_HOME ?? join(homedir(), '.dsh')
  const baseUrl = cloud.normalizeBase(config.cloudBaseUrl ?? process.env.AKSO_CLOUD_BASE)
  // 验证/CI 里不希望真的弹一个浏览器窗口时，用 env 关掉（host 仍会返回链接）
  const openBrowser = process.env.AKSO_WORKBENCH_NO_OPEN === '1' ? () => {} : (config.openBrowser ?? openExternalUrl)
  // 配置写入只走「模型工具 + 宿主审批」。HTTP 直连（invoke/apply）默认关闭：
  // 一条 loopback 的 POST 没有人的审批参与，开着它等于给这道门留了个侧门。
  const allowDirectWrite = process.env.AKSO_WORKBENCH_ALLOW_DIRECT_WRITE === '1'
  const DIRECT_WRITE_HINT = '配置写入只走 agent 工具 + 宿主审批；本机调试要直连请设 AKSO_WORKBENCH_ALLOW_DIRECT_WRITE=1'
  const logger = { warn: (error: unknown) => ctx.logger?.warn?.(error) }

  /** 设备流：deviceCode 只在 host 侧，浏览器拿到的是"用户码 + 链接"。 */
  let pendingDevice: { deviceCode: string; startedAt: number; expiresIn: number } | null = null

  /** 唯一的一份快照 —— 所有座位读它，写操作整体替换它。 */
  let snapshot: cloud.CloudSnapshot | null = null
  let syncedAt: number | null = null

  /**
   * 配置池：**默认空池** —— 账号不会自己进来，要人在「配置池 → 添加账号」里挑。
   * 只保留还存在的账号 id（云端删掉的账号自动掉出池子）。
   */
  function poolFor(accounts: cloud.CloudAccount[]): string[] {
    const stored = readPool(dshHome)
    const known = new Set(accounts.map((a) => a.id))
    return (stored?.accountIds ?? []).filter((id) => known.has(id))
  }

  function payload(
    session: { email: string; displayName: string } | null,
    extra: { stale?: boolean; syncError?: string | null } = {},
  ): StatePayload {
    const prefs = readPrefs(dshHome)
    const accounts = snapshot?.accounts ?? []
    // 配置池默认空；「已连接给 agent」以池子为准 —— 池外的账号即使 prefs 里记着也不算连接
    // （prefs 不动，重新加回池子后连接会自动恢复，不丢用户的选择）。
    const poolAccountIds = poolFor(accounts)
    const currentId = prefs.currentAccountId !== null && poolAccountIds.includes(prefs.currentAccountId)
      ? prefs.currentAccountId
      : null
    return {
      format: 'akso-workbench-state',
      loggedIn: session !== null,
      profile: session === null ? null : { email: session.email, displayName: session.displayName, baseUrl },
      revision: snapshot?.revision ?? null,
      syncedAt,
      stale: extra.stale === true,
      syncError: extra.syncError ?? null,
      accounts,
      sites: snapshot?.sites ?? [],
      boxes: snapshot?.boxes ?? [],
      defaultBox: snapshot?.defaultBox ?? '',
      counts: snapshot?.counts ?? { accounts: 0, sites: 0, boxes: 0 },
      currentAccountId: currentId,
      poolAccountIds,
    }
  }

  /** 拉一次 `/api/ui/state`，整体替换快照。失败时保留旧快照（宁可 stale，也不要空）。 */
  async function sync(session: { token: string; baseUrl: string }): Promise<cloud.CloudSnapshot> {
    // ★ 必须用**会话里记的地址**：登录时可以覆盖服务地址（测试 / 自建部署），
    //   写死默认域名会把会话令牌发到另一个云端（表现为莫名其妙的 401）。
    const next = await cloud.uiState(cloud.normalizeBase(session.baseUrl), session.token)
    snapshot = next
    syncedAt = Date.now()
    return next
  }

  function handleError(res: any, error: unknown): void {
    if (error instanceof EgmpError) {
      sendJson(res, 502, { error: error.message, code: error.code, traceId: error.traceId, path: error.path })
      return
    }
    if (error instanceof cloud.CloudError) {
      if (error.status === 401) {
        clearSession(dshHome)
        snapshot = null
        syncedAt = null
        sendJson(res, 401, { error: '登录已失效，请重新登录' })
        return
      }
      sendJson(res, 502, { error: error.message, cloudStatus: error.status, path: error.path })
      return
    }
    logger.warn(error)
    sendJson(res, 500, { error: error instanceof Error ? error.message : String(error) })
  }

  /** 统一骨架：围栏 → 方法 → body → 登录态 → 执行。 */
  function route(
    method: string,
    path: string,
    needAuth: boolean,
    run: (req: any, res: any, body: Record<string, unknown>) => Promise<void>,
  ) {
    return {
      kind: 'exact' as const,
      path,
      handler: async (req: any, res: any) => {
        // 围栏①：本机地址 / 同源 / 跨站标记 / 写请求的自定义头
        const fenced = requestFence(req, method)
        if (fenced !== null) {
          sendJson(res, 403, { error: fenced })
          return
        }
        // 围栏②：socket 对端必须是 loopback（防端口被转发到局域网）
        if (!isLoopback(req)) {
          sendJson(res, 403, { error: 'forbidden: loopback-only' })
          return
        }
        if (req.method !== method) {
          sendJson(res, 405, { error: `method not allowed: ${req.method}` })
          return
        }
        let body: Record<string, unknown> = {}
        if (method === 'POST') {
          try {
            body = await readBody(req)
          } catch (error) {
            sendJson(res, 400, { error: `请求体不合法：${error instanceof Error ? error.message : String(error)}` })
            return
          }
        }
        if (needAuth && readSession(dshHome) === null) {
          sendJson(res, 401, { error: '还没有登录 akso-cloud' })
          return
        }
        try {
          await run(req, res, body)
        } catch (error) {
          handleError(res, error)
        }
      },
    }
  }

  const R = ROUTES

  /** 写完之后：统一回拉一次，返回**整份新快照**。 */
  async function afterWrite(session: { token: string; baseUrl: string }): Promise<StatePayload> {
    await sync(session)
    return payload(readSession(dshHome))
  }

  const routes = [
    route('GET', R.health, false, async (_req, res) => {
      const session = readSession(dshHome)
      sendJson(res, 200, {
        ok: true,
        plugin: 'akso-workbench',
        loggedIn: session !== null,
        revision: snapshot?.revision ?? null,
      })
    }),

    route('GET', R.state, false, async (_req, res) => {
      const session = readSession(dshHome)
      if (session === null) {
        sendJson(res, 200, payload(null))
        return
      }
      if (snapshot !== null) {
        sendJson(res, 200, payload(session))
        return
      }
      try {
        await sync(session)
        sendJson(res, 200, payload(session))
      } catch (error) {
        if (error instanceof cloud.CloudError && error.status === 401) {
          clearSession(dshHome)
          sendJson(res, 401, { error: '登录已失效，请重新登录' })
          return
        }
        handleError(res, error)
      }
    }),

    route('POST', R.login, false, async (_req, res, body) => {
      const email = text(body.email)
      const password = typeof body.password === 'string' ? body.password : ''
      if (email === '' || password === '') {
        sendJson(res, 400, { error: '邮箱与密码都要填' })
        return
      }
      // ★ 服务地址**不接受客户端输入**：由 host 配置/环境决定（用户 2026-10-26 的要求）
      const target = baseUrl
      let session: Awaited<ReturnType<typeof cloud.login>>
      try {
        session = await cloud.login(target, email, password)
      } catch (error) {
        // 登录失败不是"会话失效"：401/400 直接回可读原因，别让上层清会话
        if (error instanceof cloud.CloudError && (error.status === 400 || error.status === 401)) {
          sendJson(res, 400, { error: error.message })
          return
        }
        throw error
      }
      writeSession(dshHome, {
        baseUrl: target,
        token: session.token,
        email: session.user.email || email,
        displayName: session.user.displayName ?? '',
        savedAt: Date.now(),
      })
      let syncError: string | null = null
      try {
        await sync({ token: session.token, baseUrl: target })
      } catch (error) {
        // 登录成功但同步失败：会话保留，用户可以点「重试同步」
        logger.warn(error)
        syncError = error instanceof Error ? error.message : String(error)
      }
      sendJson(res, 200, payload(readSession(dshHome), { stale: snapshot === null, syncError }))
    }),

    route('POST', R.logout, false, async (_req, res) => {
      clearSession(dshHome)
      snapshot = null
      syncedAt = null
      pendingDevice = null
      sendJson(res, 200, payload(null))
    }),

    /* ── 设备流：浏览器里已登录的人点一下批准，host 轮询拿令牌（RFC 8628） ── */
    route('POST', R.deviceStart, false, async (_req, res) => {
      const started = await cloud.deviceStart(baseUrl, 'akso-workbench · DSH')
      pendingDevice = { deviceCode: started.deviceCode, startedAt: Date.now(), expiresIn: started.expiresIn }
      // 直接唤起系统默认浏览器；打不开也没关系，界面上有链接和码
      openBrowser(started.verificationUrl)
      sendJson(res, 200, {
        userCode: started.userCode,
        verificationUrl: started.verificationUrl,
        expiresIn: started.expiresIn,
        interval: started.interval,
      })
    }),

    route('POST', R.devicePoll, false, async (_req, res) => {
      if (pendingDevice === null) {
        sendJson(res, 200, { status: 'unknown' })
        return
      }
      if (Date.now() - pendingDevice.startedAt > pendingDevice.expiresIn * 1000) {
        pendingDevice = null
        sendJson(res, 200, { status: 'expired' })
        return
      }
      const polled = await cloud.devicePoll(baseUrl, pendingDevice.deviceCode)
      if (polled.status !== 'approved') {
        if (polled.status !== 'pending') pendingDevice = null
        sendJson(res, 200, { status: polled.status })
        return
      }
      pendingDevice = null
      writeSession(dshHome, {
        baseUrl,
        token: polled.token,
        email: polled.user.email,
        displayName: polled.user.displayName ?? '',
        savedAt: Date.now(),
      })
      let syncError: string | null = null
      try {
        await sync({ token: polled.token, baseUrl })
      } catch (error) {
        logger.warn(error)
        syncError = error instanceof Error ? error.message : String(error)
      }
      sendJson(res, 200, {
        status: 'approved',
        state: payload(readSession(dshHome), { stale: snapshot === null, syncError }),
      })
    }),

    route('POST', R.sync, true, async (_req, res) => {
      const session = readSession(dshHome)!
      try {
        await sync(session)
        sendJson(res, 200, payload(session))
      } catch (error) {
        if (error instanceof cloud.CloudError && error.status !== 401) {
          // 保留旧快照：界面继续显示上一份，只标「这次没同步上」
          sendJson(res, 200, payload(session, { stale: true, syncError: error.message }))
          return
        }
        throw error
      }
    }),

    route('POST', R.accountCreate, true, async (_req, res, body) => {
      const session = readSession(dshHome)!
      const siteId = text(body.siteId)
      const username = text(body.username)
      const password = typeof body.password === 'string' ? body.password : ''
      if (siteId === '' || username === '' || password === '') {
        sendJson(res, 400, { error: '站点、平台账号、密码都要填（空密码的账号在平台侧登不上）' })
        return
      }
      await cloud.createAccount(cloud.normalizeBase(session.baseUrl), session.token, {
        siteId,
        username,
        password,
        tabName: text(body.tabName) || undefined,
        note: text(body.note) || undefined,
        box: text(body.box) || undefined,
      })
      sendJson(res, 200, await afterWrite(session))
    }),

    route('POST', R.accountCurrent, false, async (_req, res, body) => {
      const id = text(body.id)
      const session = readSession(dshHome)
      if (id !== '' && snapshot !== null && !snapshot.accounts.some((a) => a.id === id)) {
        sendJson(res, 404, { error: '账号不存在（可能已被别处删除，刷新一下）' })
        return
      }
      // agent 只能连配置池里的账号：不在池里的一律拒绝（界面本来也不会给这个入口）
      if (id !== '' && !poolFor(snapshot?.accounts ?? []).includes(id)) {
        sendJson(res, 400, { error: '这个账号不在配置池里：先去「配置池」把它加进来' })
        return
      }
      writePrefs(dshHome, { currentAccountId: id === '' ? null : id })
      sendJson(res, 200, payload(session))
    }),

    /* 配置池：决定底部账号条里 agent 能选哪些账号登录 */
    route('POST', R.poolToggle, false, async (_req, res, body) => {
      const id = text(body.id, 120)
      const inPool = body.inPool !== false
      const session = readSession(dshHome)
      if (id === '') {
        sendJson(res, 400, { error: '缺少账号 id' })
        return
      }
      if (snapshot === null || !snapshot.accounts.some((a) => a.id === id)) {
        sendJson(res, 404, { error: '账号不存在（先同步一次）' })
        return
      }
      if (!inPool && readPrefs(dshHome).currentAccountId === id) {
        sendJson(res, 400, { error: '这是 agent 当前连接的账号：先在底部账号条换一个或断开，再移出配置池' })
        return
      }
      const current = payload(session).poolAccountIds
      const next = inPool
        ? [...new Set([...current, id])]
        : current.filter((item) => item !== id)
      writePool(dshHome, { accountIds: next })
      sendJson(res, 200, payload(session))
    }),

    route('POST', R.siteCreate, true, async (_req, res, body) => {
      const session = readSession(dshHome)!
      const siteUrl = text(body.baseUrl, 400)
      if (siteUrl === '') {
        sendJson(res, 400, { error: '站点地址不能为空' })
        return
      }
      await cloud.createSite(cloud.normalizeBase(session.baseUrl), session.token, {
        name: text(body.name) || undefined,
        baseUrl: siteUrl,
        note: text(body.note) || undefined,
        createBox: body.createBox !== false,
      })
      sendJson(res, 200, await afterWrite(session))
    }),

    route('POST', R.boxRename, true, async (_req, res, body) => {
      const session = readSession(dshHome)!
      const source = text(body.source)
      const target = text(body.target)
      if (source === '' || target === '') {
        sendJson(res, 400, { error: '原名称与新名称都要填' })
        return
      }
      await cloud.renameBox(cloud.normalizeBase(session.baseUrl), session.token, source, target)
      sendJson(res, 200, await afterWrite(session))
    }),

    route('POST', R.boxBind, true, async (_req, res, body) => {
      const session = readSession(dshHome)!
      const boxName = text(body.name)
      const siteId = text(body.siteId)
      if (boxName === '' || siteId === '') {
        sendJson(res, 400, { error: '盒子与站点都要选' })
        return
      }
      await cloud.bindBoxSite(cloud.normalizeBase(session.baseUrl), session.token, boxName, siteId)
      sendJson(res, 200, await afterWrite(session))
    }),

    /* ───────────── 配置：eGMP 会话（口令不落盘，只存 JWT） ───────────── */
    route('GET', R.configSession, false, async (_req, res) => {
      sendJson(res, 200, publicSession(readEgmpSession(dshHome)))
    }),

    route('POST', R.configLogin, false, async (_req, res, body) => {
      const session = await egmpLogin(String(body.baseUrl ?? ''), String(body.account ?? ''), String(body.password ?? ''))
      writeEgmpSession(dshHome, session)
      sendJson(res, 200, { ok: true, ...publicSession(session) })
    }),

    route('POST', R.configLogout, false, async (_req, res) => {
      clearEgmpSession(dshHome)
      sendJson(res, 200, { ok: true, loggedIn: false })
    }),

    /* ───────────── 配置：能力目录（工具页的数据源） ───────────── */
    route('GET', R.configCapabilities, false, async (_req, res) => {
      sendJson(res, 200, {
        session: publicSession(readEgmpSession(dshHome)),
        groups: groupByDomain(),
        total: CAPABILITIES.length,
      })
    }),

    /* ───────────── 配置：单接口执行（本机调试；默认关闭） ───────────── */
    route('POST', R.configInvoke, false, async (_req, res, body) => {
      if (!allowDirectWrite) {
        sendJson(res, 403, { error: DIRECT_WRITE_HINT })
        return
      }
      const session = readEgmpSession(dshHome)
      if (session === null) {
        sendJson(res, 401, { error: '还没有登录 eGMP' })
        return
      }
      const capabilityId = text(body.capabilityId, 120)
      if (capabilityId === '') {
        sendJson(res, 400, { error: '缺少 capabilityId' })
        return
      }
      const params = body.params !== null && typeof body.params === 'object' ? (body.params as Record<string, unknown>) : {}
      const json = await invokeCapability(session, capabilityId, params)
      sendJson(res, 200, { ok: true, response: json })
    }),

    /* ───────────── 配置：计划 → 执行 → 审计 ───────────── */
    route('POST', R.configPlan, false, async (_req, res, body) => {
      const session = readEgmpSession(dshHome)
      if (session === null) {
        sendJson(res, 401, { error: '还没有登录 eGMP' })
        return
      }
      const plan = await planBlueprint(session, body.blueprint as never, dshHome)
      sendJson(res, 200, { ok: true, plan })
    }),

    route('GET', R.configPlanGet, false, async (req, res) => {
      const url = new URL(String(req.url ?? '/'), 'http://127.0.0.1')
      const id = url.searchParams.get('id') ?? ''
      const plan = id === '' ? null : loadPlan(dshHome, id)
      if (plan === null) {
        sendJson(res, 404, { error: '计划不存在' })
        return
      }
      sendJson(res, 200, { ok: true, plan })
    }),

    route('POST', R.configApply, false, async (_req, res, body) => {
      if (!allowDirectWrite) {
        sendJson(res, 403, { error: DIRECT_WRITE_HINT })
        return
      }
      const session = readEgmpSession(dshHome)
      if (session === null) {
        sendJson(res, 401, { error: '还没有登录 eGMP' })
        return
      }
      const planId = text(body.planId, 120)
      if (planId === '') {
        sendJson(res, 400, { error: '缺少 planId' })
        return
      }
      const plan = await applyPlan(session, planId, dshHome)
      sendJson(res, 200, { ok: plan.status === 'applied', plan })
    }),

    route('GET', R.configApprovals, false, async (_req, res) => {
      const { pending, recent } = listApprovals(dshHome)
      sendJson(res, 200, { ok: true, pending, recent })
    }),

    route('POST', R.configApprovalDecide, false, async (_req, res, body) => {
      const approvalId = text(body.approvalId, 64)
      const decision = text(body.decision, 8)
      if (approvalId === '') {
        sendJson(res, 400, { error: '缺少 approvalId' })
        return
      }
      if (decision !== 'allow' && decision !== 'deny') {
        sendJson(res, 400, { error: 'decision 只能是 allow 或 deny' })
        return
      }
      const note = text(body.note, 200)
      const entry = decideApproval(dshHome, approvalId, decision, note)
      if (entry === null) {
        sendJson(res, 409, { error: '这张审批单不存在、已过期或已经处理过' })
        return
      }
      sendJson(res, 200, { ok: true, approval: entry })
    }),

    route('GET', R.configAudit, false, async (req, res) => {
      const url = new URL(String(req.url ?? '/'), 'http://127.0.0.1')
      const limit = Math.min(200, Math.max(1, Number(url.searchParams.get('limit') ?? 50) || 50))
      sendJson(res, 200, { records: readAudit(dshHome, limit) })
    }),
  ]

  ctx.effect(() => {
    const disposers = routes.map((r) => ctx.webServer.register(r))
    return () => {
      for (const dispose of disposers) dispose()
    }
  }, 'akso-workbench: routes')

  /* ── 确认门是我们自己的，不走 DSH 的 approval ──
     `danger-full-access` 档下会话策略是 `never`，DSH 会把所有 `ask` 静默判拒，写入这条路会被焊死；
     而且宿主弹窗只显示工具名与参数，看不到要写到平台上的东西。所以改成
     「工具只开审批单 → 页面里人点允许 → 同一个 planId 再调一次才真写」，
     批准单一次性、30 分钟过期、全部落盘（见 `config/approvals.ts`）。 */

  /* ── 给 agent 的只读工具：让它能读到与界面同一份快照 ── */
  try {
    // `tools` 是可选服务：没有它（例如极简宿主）时只少一个工具，不该让插件装不上
    const registry = (typeof ctx.get === 'function' ? ctx.get('tools', false) : undefined) ?? ctx.tools
    if (registry !== undefined && registry !== null && typeof registry.register === 'function') {
      const definition = buildSnapshotTool({
        read: () => {
          const session = readSession(dshHome)
          const accounts = snapshot?.accounts ?? []
          const pool = poolFor(accounts)
          const pref = readPrefs(dshHome).currentAccountId
          return {
            loggedIn: session !== null,
            revision: snapshot?.revision ?? null,
            syncedAt,
            accounts,
            sites: snapshot?.sites ?? [],
            boxes: snapshot?.boxes ?? [],
            defaultBox: snapshot?.defaultBox ?? '',
            counts: snapshot?.counts ?? { accounts: 0, sites: 0, boxes: 0 },
            currentAccountId: pref !== null && pool.includes(pref) ? pref : null,
            poolAccountIds: pool,
          }
        },
        refresh: async () => {
          const session = readSession(dshHome)
          if (session !== null) await sync(session)
        },
        loginHint: '还没有登录 akso-cloud：请打开 DeepSeek Harness 左栏的 akso-workbench，用「在浏览器中登录」完成一次授权。',
      })
      ctx.effect(() => registry.register(definition), 'akso-workbench: snapshot tool')

      const configDeps = { dshHome }
      const planTool = buildConfigPlanTool(configDeps)
      const applyTool = buildConfigApplyTool(configDeps)
      ctx.effect(() => registry.register(planTool), 'akso-workbench: config plan tool')
      ctx.effect(() => registry.register(applyTool), 'akso-workbench: config apply tool')
    } else {
      logger.warn('akso-workbench: 没有 tools 服务，agent 读不到快照（界面仍可用）')
    }
  } catch (error) {
    // 工具注册失败不能带崩插件（shell 起不来比少一个工具严重得多）
    logger.warn(error)
  }
}
