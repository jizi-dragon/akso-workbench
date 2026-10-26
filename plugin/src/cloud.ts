import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * akso-cloud 客户端（host 半边专用）。
 *
 * ★ 令牌只在 **host** 侧使用，**永不下发浏览器**：client 只知道"已登录 + 邮箱/显示名"。
 * ★ 读侧只认 `/api/ui/state`：它由服务端在**一次响应**里给出 accounts / sites / boxes + revision
 *   （服务端自己会重试到一致），这是我们"各处看到同一份数据"的基础，而不是三次请求拼盘。
 */

export const DEFAULT_BASE = 'https://www.dragonrain.top'

export class CloudError extends Error {
  readonly status: number
  readonly path: string

  constructor(status: number, path: string, message: string) {
    super(message)
    this.status = status
    this.path = path
    this.name = 'CloudError'
  }
}

export interface CloudUser {
  id: string
  email: string
  displayName?: string
  avatar?: string
}

export interface CloudAccount {
  id: string
  siteId: string
  siteName: string
  siteHost: string
  baseUrl: string
  username: string
  tabName: string
  role: string
  tags: string[]
  note: string
  status: string
  box: string
  scheme: string
  hasPassword: boolean
  createdAt: number
  updatedAt: number
}

export interface CloudSite {
  id: string
  name: string
  baseUrl: string
  host: string
  note: string
  accountCount: number
  [key: string]: unknown
}

export interface CloudBox {
  name: string
  isDefault: boolean
  disabled: boolean
  autoDisabled: boolean
  count: number
  siteId?: string
  siteName?: string
  [key: string]: unknown
}

export interface CloudSnapshot {
  revision: number
  accounts: CloudAccount[]
  sites: CloudSite[]
  boxes: CloudBox[]
  defaultBox: string
  counts: { accounts: number; sites: number; boxes: number }
}

export function normalizeBase(raw: string | undefined): string {
  const base = (raw ?? '').trim().replace(/\/+$/, '')
  return base === '' ? DEFAULT_BASE : base
}

type FetchImpl = typeof fetch

/**
 * 客户端自报家门：akso-cloud 的「已登录客户端」一栏**只**认这两个头。
 *
 * ★ 2026-10-26：服务端在鉴权中间件里 `recordPresence(userId, x-akso-client, x-akso-client-version)`
 *   （`src/http/app.ts`），个人信息页再读 `client_presence` 表画「在线 / 最近活跃 / 未登录」。
 *   之前本插件一次都没发过这个头 ⇒ 页面永远显示「Akso Workbench 未登录」。
 *   akso-pass 早就发了（`X-Akso-Client: akso-pass`），所以那条一直是对的。
 */
const CLIENT_NAME = 'akso-workbench'

let cachedVersion: string | null = null

/** 版本号从插件自己的 `package.json` 读（源码跑与 lib 跑都能找到「上一级」）。 */
function clientVersion(): string {
  if (cachedVersion !== null) return cachedVersion
  try {
    const here = dirname(fileURLToPath(import.meta.url))
    const pkg = JSON.parse(readFileSync(join(here, '..', 'package.json'), 'utf8')) as { version?: unknown }
    cachedVersion = typeof pkg.version === 'string' ? pkg.version : ''
  } catch {
    cachedVersion = ''
  }
  return cachedVersion
}

async function call(
  baseUrl: string,
  path: string,
  init: { method?: string; token?: string; body?: unknown } = {},
  fetchImpl: FetchImpl = fetch,
): Promise<unknown> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', 'X-Akso-Client': CLIENT_NAME }
  const version = clientVersion()
  if (version !== '') headers['X-Akso-Client-Version'] = version
  if (init.token !== undefined) headers.Authorization = `Bearer ${init.token}`
  let response: Response
  try {
    response = await fetchImpl(`${normalizeBase(baseUrl)}${path}`, {
      method: init.method ?? 'GET',
      headers,
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      signal: AbortSignal.timeout(20_000),
    })
  } catch (error) {
    throw new CloudError(0, path, `连不上 akso-cloud：${error instanceof Error ? error.message : String(error)}`)
  }
  const text = await response.text()
  let payload: Record<string, unknown> = {}
  try {
    payload = text === '' ? {} : (JSON.parse(text) as Record<string, unknown>)
  } catch {
    payload = {}
  }
  if (!response.ok) {
    throw new CloudError(response.status, path, errorMessage(payload, response.status))
  }
  return payload
}

/**
 * 把 akso-cloud 的错误体翻成一句人话。
 *
 * ★ 实测（2026-10-26 对线上）：它回的是 **`{"detail": "邮箱或密码不正确"}`**，不是 `{error}` ——
 *   只读 `error` 会让所有云端错误退化成 "HTTP 401/400"，用户看不到原因。
 * ★ 而且 `detail` **可能是对象**（结构化错误，例如删站点时的 409），那时要取它的 `message`。
 *   与 akso-pass 的 `errorDetail()` **同一套兼容顺序**：detail → error → message → 对象里的 message/error。
 */
export function errorMessage(payload: Record<string, unknown>, status: number): string {
  const raw = payload.detail ?? payload.error ?? payload.message
  if (typeof raw === 'string' && raw.trim() !== '') return raw.trim()
  if (raw !== null && typeof raw === 'object') {
    const nested = raw as Record<string, unknown>
    const inner = nested.message ?? nested.error
    if (typeof inner === 'string' && inner.trim() !== '') return inner.trim()
  }
  return `HTTP ${status}`
}

/* ───────────────────────── 设备流（RFC 8628，跳浏览器拿登录态） ───────────────────────── */

export interface DeviceStart {
  /** ★ 只在 host 侧保存，不下发浏览器 */
  deviceCode: string
  userCode: string
  verificationUrl: string
  expiresIn: number
  interval: number
}

export type DevicePoll =
  | { status: 'pending' | 'expired' | 'denied' | 'consumed' | 'unknown' }
  | { status: 'approved'; token: string; user: CloudUser }

/** `POST /api/auth/device-start` —— 服务端会自己拼 `verificationUrl`（带 userCode）。 */
export async function deviceStart(
  baseUrl: string,
  clientName: string,
  fetchImpl: FetchImpl = fetch,
): Promise<DeviceStart> {
  const payload = (await call(baseUrl, '/api/auth/device-start', { method: 'POST', body: { clientName } }, fetchImpl)) as Record<string, unknown>
  const deviceCode = typeof payload.deviceCode === 'string' ? payload.deviceCode : ''
  const verificationUrl = typeof payload.verificationUrl === 'string' ? payload.verificationUrl : ''
  if (deviceCode === '' || verificationUrl === '') {
    throw new CloudError(200, '/api/auth/device-start', '设备流响应缺少 deviceCode / verificationUrl')
  }
  return {
    deviceCode,
    userCode: String(payload.userCode ?? ''),
    verificationUrl,
    expiresIn: Number(payload.expiresIn ?? 600),
    interval: Math.max(1, Number(payload.interval ?? 3)),
  }
}

/** `POST /api/auth/device-poll` —— 批准前一直 `pending`；令牌只发一次。 */
export async function devicePoll(
  baseUrl: string,
  deviceCode: string,
  fetchImpl: FetchImpl = fetch,
): Promise<DevicePoll> {
  const payload = (await call(baseUrl, '/api/auth/device-poll', { method: 'POST', body: { deviceCode } }, fetchImpl)) as Record<string, unknown>
  const status = String(payload.status ?? 'unknown')
  if (status === 'approved' && typeof payload.token === 'string') {
    const user = (payload.user ?? {}) as Record<string, unknown>
    return {
      status: 'approved',
      token: payload.token,
      user: {
        id: String(user.id ?? ''),
        email: String(user.email ?? ''),
        displayName: typeof user.displayName === 'string' ? user.displayName : undefined,
        avatar: typeof user.avatar === 'string' ? user.avatar : undefined,
      },
    }
  }
  if (status === 'pending' || status === 'expired' || status === 'denied' || status === 'consumed' || status === 'unknown') {
    return { status }
  }
  return { status: 'unknown' }
}

/** `POST /api/auth/login` → `{ token, user }`。 */
export async function login(
  baseUrl: string,
  email: string,
  password: string,
  fetchImpl: FetchImpl = fetch,
): Promise<{ token: string; user: CloudUser }> {
  const payload = (await call(baseUrl, '/api/auth/login', { method: 'POST', body: { email, password } }, fetchImpl)) as {
    token?: unknown
    user?: unknown
  }
  if (typeof payload.token !== 'string' || payload.token === '') {
    throw new CloudError(200, '/api/auth/login', '登录响应里没有 token')
  }
  const user = (payload.user ?? {}) as Record<string, unknown>
  return {
    token: payload.token,
    user: {
      id: String(user.id ?? ''),
      email: String(user.email ?? email),
      displayName: typeof user.displayName === 'string' ? user.displayName : undefined,
      avatar: typeof user.avatar === 'string' ? user.avatar : undefined,
    },
  }
}

/** `GET /api/auth/me` → `{ user }`；用来验证旧令牌是否还有效。 */
export async function me(baseUrl: string, token: string, fetchImpl: FetchImpl = fetch): Promise<CloudUser> {
  const payload = (await call(baseUrl, '/api/auth/me', { token }, fetchImpl)) as { user?: Record<string, unknown> }
  const user = payload.user ?? {}
  return {
    id: String(user.id ?? ''),
    email: String(user.email ?? ''),
    displayName: typeof user.displayName === 'string' ? user.displayName : undefined,
    avatar: typeof user.avatar === 'string' ? user.avatar : undefined,
  }
}

/** `GET /api/ui/state`：**一次响应**给出三份数据 + revision。 */
export async function uiState(baseUrl: string, token: string, fetchImpl: FetchImpl = fetch): Promise<CloudSnapshot> {
  const payload = (await call(baseUrl, '/api/ui/state', { token }, fetchImpl)) as Record<string, unknown>
  const accounts = Array.isArray(payload.accounts) ? (payload.accounts as CloudAccount[]) : []
  const sites = Array.isArray(payload.sites) ? (payload.sites as CloudSite[]) : []
  const boxes = Array.isArray(payload.boxes) ? (payload.boxes as CloudBox[]) : []
  const counts = (payload.counts ?? {}) as Record<string, unknown>
  return {
    revision: Number(payload.revision ?? 0),
    accounts,
    sites,
    boxes,
    defaultBox: String(payload.defaultBox ?? ''),
    counts: {
      accounts: Number(counts.accounts ?? accounts.length),
      sites: Number(counts.sites ?? sites.length),
      boxes: Number(counts.boxes ?? boxes.length),
    },
  }
}

/* ───────────────────────────── 写操作 ───────────────────────────── */

export interface CreateAccountBody {
  siteId: string
  username: string
  password: string
  tabName?: string
  note?: string
  box?: string
}

export async function createAccount(
  baseUrl: string,
  token: string,
  body: CreateAccountBody,
  fetchImpl: FetchImpl = fetch,
): Promise<unknown> {
  return call(baseUrl, '/api/accounts', { method: 'POST', token, body }, fetchImpl)
}

export async function deleteAccount(
  baseUrl: string,
  token: string,
  id: string,
  fetchImpl: FetchImpl = fetch,
): Promise<unknown> {
  return call(baseUrl, `/api/accounts/${encodeURIComponent(id)}`, { method: 'DELETE', token }, fetchImpl)
}

export async function createSite(
  baseUrl: string,
  token: string,
  body: { name?: string; baseUrl: string; note?: string; createBox?: boolean },
  fetchImpl: FetchImpl = fetch,
): Promise<unknown> {
  return call(baseUrl, '/api/sites', { method: 'POST', token, body }, fetchImpl)
}

export async function deleteSite(
  baseUrl: string,
  token: string,
  id: string,
  force = true,
  fetchImpl: FetchImpl = fetch,
): Promise<unknown> {
  return call(baseUrl, `/api/sites/${encodeURIComponent(id)}${force ? '?force=1' : ''}`, { method: 'DELETE', token }, fetchImpl)
}

export async function renameBox(
  baseUrl: string,
  token: string,
  source: string,
  target: string,
  fetchImpl: FetchImpl = fetch,
): Promise<unknown> {
  return call(baseUrl, '/api/boxes/rename', { method: 'POST', token, body: { source, target } }, fetchImpl)
}

export async function deleteBox(
  baseUrl: string,
  token: string,
  name: string,
  deleteAccounts = false,
  fetchImpl: FetchImpl = fetch,
): Promise<unknown> {
  return call(baseUrl, '/api/boxes/delete', { method: 'POST', token, body: { name, deleteAccounts } }, fetchImpl)
}

export async function bindBoxSite(
  baseUrl: string,
  token: string,
  name: string,
  siteId: string,
  fetchImpl: FetchImpl = fetch,
): Promise<unknown> {
  return call(baseUrl, '/api/boxes/bind-site', { method: 'POST', token, body: { name, siteId } }, fetchImpl)
}
