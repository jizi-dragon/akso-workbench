/**
 * eGMP 平台客户端 —— 从 Python 版 `workbench/services/egmp/client.py` 移植。
 *
 * ## 三条与 Python 版一致、且**不报错**的口径
 *
 * 1. **信封校验**：平台统一回 `{code, message, data}`，`code != 0` 就是业务失败
 *    —— HTTP 200 不代表成功。漏掉这层的话，失败会被当成"数据是空的"。
 * 2. **分页有硬上限**：`hasNext` 恒为 true 的坏代理会让循环无限请求（实测算过 501 页）。
 *    到上限**抛错**，不静默返回半截。
 * 3. **token 按 `(base_url, username)` 分键缓存**：跨环境串台会让 A 环境的 token
 *    发到 B 环境，表现是"偶发 401"。
 */

/** 平台返回非 0 code 时抛这个（**不是** HTTP 错误）。 */
export class ApiError extends Error {
  readonly method: string
  readonly path: string
  readonly code: unknown

  constructor(method: string, path: string, code: unknown, message: string) {
    super(`${method} ${path} 失败（code=${String(code)}）：${message}`)
    this.method = method
    this.path = path
    this.code = code
  }
}

/** 分页硬上限：坏 server/坏代理会让 `hasNext` 恒为 true。 */
export const MAX_PAGES = 200

/** 去掉尾部斜杠（Python 的 `_trim_base`）。 */
export function trimBase(baseUrl: string): string {
  return (baseUrl ?? '').trim().replace(/\/+$/, '')
}

export interface TokenStore {
  load(baseUrl: string, username: string): string | null
  save(baseUrl: string, username: string, token: string, envId: string): void
}

export interface LoginOptions {
  envId?: string
  store?: TokenStore
  fetchImpl?: typeof fetch
  /** token 有效期（毫秒）。Python 版是 55 分钟磁盘缓存。 */
  ttlMs?: number
}

export interface CachedToken {
  token: string
  savedAt: number
}

/** 内存 token 缓存（Python 版落盘；插件侧的落盘应交给宿主的数据目录，这里只给接口）。 */
export class MemoryTokenStore implements TokenStore {
  private readonly items = new Map<string, CachedToken>()
  private readonly ttlMs: number

  constructor(ttlMs = 55 * 60 * 1000) {
    this.ttlMs = ttlMs
  }

  private key(baseUrl: string, username: string): string {
    return `${trimBase(baseUrl)}\u0000${username}`
  }

  load(baseUrl: string, username: string): string | null {
    const hit = this.items.get(this.key(baseUrl, username))
    if (hit === undefined) return null
    if (Date.now() - hit.savedAt > this.ttlMs) {
      this.items.delete(this.key(baseUrl, username))
      return null
    }
    return hit.token
  }

  save(baseUrl: string, username: string, token: string): void {
    this.items.set(this.key(baseUrl, username), { token, savedAt: Date.now() })
  }
}

/**
 * 登录：`POST {base}/api/openapi/v1.0/Auth`，body `{account, password}`，
 * 成功时 `data` 就是 JWT（**是字符串，不是对象** —— 这一点容易写错）。
 */
export async function login(
  baseUrl: string,
  username: string,
  password: string,
  options: LoginOptions = {},
): Promise<string> {
  const store = options.store
  if (store !== undefined) {
    const cached = store.load(baseUrl, username)
    if (cached !== null) return cached
  }
  const fetchImpl = options.fetchImpl ?? fetch
  const response = await fetchImpl(`${trimBase(baseUrl)}/api/openapi/v1.0/Auth`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ account: username, password }),
  })
  if (!response.ok) {
    throw new ApiError('POST', 'Auth', response.status, `HTTP ${response.status}`)
  }
  const payload = (await response.json()) as { code?: unknown; message?: unknown; data?: unknown }
  if (payload.code !== 0 || typeof payload.data !== 'string') {
    throw new ApiError('POST', 'Auth', payload.code, String(payload.message ?? ''))
  }
  const token = payload.data
  store?.save(baseUrl, username, token, options.envId ?? '')
  return token
}

export interface ClientOptions {
  timeoutMs?: number
  fetchImpl?: typeof fetch
}

/** 同步风格的平台客户端（Bearer + 信封校验 + 分页循环）。 */
export class EgmpClient {
  readonly scope: string
  private readonly baseUrl: string
  private readonly token: string
  private readonly fetchImpl: typeof fetch
  private readonly timeoutMs: number

  constructor(baseUrl: string, token: string, options: ClientOptions = {}) {
    this.baseUrl = trimBase(baseUrl)
    // 环境作用域标识（= 归一化 base_url）：缓存分键用，防止跨环境串台
    this.scope = this.baseUrl
    this.token = token
    this.fetchImpl = options.fetchImpl ?? fetch
    this.timeoutMs = options.timeoutMs ?? 60_000
  }

  private headers(): Record<string, string> {
    return { Authorization: `Bearer ${this.token}`, 'Content-Type': 'application/json' }
  }

  private async request(method: string, path: string, body?: unknown, search?: string): Promise<unknown> {
    const url = `${this.baseUrl}${path}${search ?? ''}`
    const response = await this.fetchImpl(url, {
      method,
      headers: this.headers(),
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(this.timeoutMs),
    })
    if (!response.ok) {
      throw new ApiError(method, path, response.status, `HTTP ${response.status}`)
    }
    const payload = (await response.json()) as { code?: unknown; message?: unknown; data?: unknown }
    // ★ HTTP 200 不等于成功：信封里的 code 才是判据
    if (payload.code !== 0) {
      throw new ApiError(method, path, payload.code, String(payload.message ?? ''))
    }
    return payload.data
  }

  async get(path: string, params?: Record<string, string | number>): Promise<unknown> {
    const search =
      params === undefined
        ? ''
        : `?${Object.entries(params)
            .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
            .join('&')}`
    return this.request('GET', path, undefined, search)
  }

  async post(path: string, body?: Record<string, unknown>): Promise<unknown> {
    return this.request('POST', path, body ?? {})
  }

  /**
   * 平台统一分页：`pageIndex` 从 1、`datas`/`items` 兼容、`hasNext` 续页。
   * ★ 到 `MAX_PAGES` 仍说 hasNext ⇒ 抛错（不静默返回半截）。
   */
  async pagedPost(path: string, body: Record<string, unknown> = {}, pageSize = 1000): Promise<Array<Record<string, unknown>>> {
    const rows: Array<Record<string, unknown>> = []
    for (let pageIndex = 1; pageIndex <= MAX_PAGES; pageIndex++) {
      const data = (await this.request('POST', path, {
        ...body,
        pageIndex,
        pageSize,
      })) as { datas?: unknown; items?: unknown; hasNext?: unknown } | null
      const chunk = (data?.datas ?? data?.items ?? []) as Array<Record<string, unknown>>
      if (Array.isArray(chunk)) rows.push(...chunk)
      if (!data?.hasNext) return rows
    }
    throw new ApiError('POST', path, 'pagination', `分页超过 ${MAX_PAGES} 页仍未结束（hasNext 恒为 true？）`)
  }
}
