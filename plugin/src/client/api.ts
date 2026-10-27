/**
 * 浏览器侧唯一的数据入口。
 *
 * ★ 走**同源相对路径**（`api/akso-workbench/*`，无前导斜杠）：GUI 用 `<base href="./">`
 *   提供，根绝对路径 `/api/...` 会在子路径部署下逃出前缀（与 skill-explorer 同款处理）。
 * ★ 组件里不直接 `fetch`：都在这里收口，便于统一错误语义。
 * ★ **所有写操作都返回整份新快照**（host 侧写完后统一回拉 `/api/ui/state`）——
 *   因此这里不需要"本地猜一个结果"，数据一致性由 host 的快照保证。
 */

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
}

export interface CloudBox {
  name: string
  isDefault: boolean
  disabled: boolean
  autoDisabled: boolean
  count: number
  siteId?: string
  siteName?: string
}

export interface ServerState {
  format: 'akso-workbench-state'
  loggedIn: boolean
  profile: { email: string; displayName: string; baseUrl: string } | null
  revision: number | null
  syncedAt: number | null
  stale: boolean
  syncError: string | null
  accounts: CloudAccount[]
  sites: CloudSite[]
  boxes: CloudBox[]
  defaultBox: string
  counts: { accounts: number; sites: number; boxes: number }
  currentAccountId: string | null
  /** 配置池：允许 agent 登录的账号（底部账号条只列这些） */
  poolAccountIds: string[]
}

const BASE = 'api/akso-workbench'

/**
 * 写请求必须带这个头（host 侧 `requestFence` 的硬闸门）：
 * 跨站页面既设不了自定义头，也过不了 preflight —— 我们自己不发 CORS 头，所以别的网页
 * 没法借你的浏览器往本机 API 写东西。只读的 GET 不要求，便于 curl 探活。
 */
export const CLIENT_HEADER = 'x-akso-workbench'

function url(path: string): string {
  return new URL(`${BASE}/${path}`, document.baseURI).toString()
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url(path), {
    cache: 'no-store',
    ...init,
    headers: { 'Content-Type': 'application/json', [CLIENT_HEADER]: '1', ...(init?.headers ?? {}) },
  })
  const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>
  if (!response.ok) {
    const message = typeof payload.error === 'string' ? payload.error : `HTTP ${response.status}`
    throw new Error(message)
  }
  return payload as T
}

const post = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) })

export const api = {
  state: () => call<ServerState>('state'),

  /** ★ 地址固定在 host 侧，这里不再传 baseUrl（服务地址不是用户可配项）。 */
  login: (input: { email: string; password: string }) => call<ServerState>('cloud/login', post(input)),
  logout: () => call<ServerState>('cloud/logout', post({})),
  sync: () => call<ServerState>('cloud/sync', post({})),

  /** 设备流：起一个授权请求，host 会顺手打开系统浏览器。 */
  deviceStart: () =>
    call<{ userCode: string; verificationUrl: string; expiresIn: number; interval: number }>(
      'cloud/device/start',
      post({}),
    ),
  /** 设备流：轮询；批准后 host 已经存好会话并回拉快照，这里直接拿到整份 state。 */
  devicePoll: () =>
    call<{ status: string; state?: ServerState }>('cloud/device/poll', post({})),

  createAccount: (input: {
    siteId: string
    username: string
    password: string
    tabName?: string
    note?: string
    box?: string
  }) => call<ServerState>('accounts/create', post(input)),
  setCurrent: (id: string) => call<ServerState>('accounts/current', post({ id })),

  /** 配置池：把账号加入/移出 agent 可选登录的范围。 */
  togglePool: (id: string, inPool: boolean) => call<ServerState>('pool/toggle', post({ id, inPool })),

  createSite: (input: { name?: string; baseUrl: string; note?: string }) =>
    call<ServerState>('sites/create', post(input)),

  renameBox: (source: string, target: string) => call<ServerState>('boxes/rename', post({ source, target })),
  bindBoxSite: (name: string, siteId: string) => call<ServerState>('boxes/bind-site', post({ name, siteId })),
}
