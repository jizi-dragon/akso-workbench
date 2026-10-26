/**
 * eGMP 会话与 HTTP 客户端（host 半边）。
 *
 * 只做三件事：换 JWT、缓存 JWT、按 capability 发请求。
 *
 * ★ 硬规矩：**所有带 body 的请求必须显式设置 `Content-Type: application/json`**。
 *   P0 实测：MCP 就是漏了它，平台回 415，而 MCP 把 415 当成功 —— 这里不再犯。
 * ★ 口令不落盘：只把 JWT + baseUrl + account 写进 0600 的 `egmp-session.json`。
 */
import { chmodSync, existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import { dataDir } from '../store.ts'

export interface EgmpSession {
  baseUrl: string
  account: string
  token: string
  savedAt: number
}

export class EgmpError extends Error {
  readonly code: number | string | null
  readonly traceId: string | null
  readonly status: number | null
  readonly path: string | null

  constructor(message: string, options: { code?: number | string | null; traceId?: string | null; status?: number | null; path?: string | null } = {}) {
    super(message)
    this.name = 'EgmpError'
    this.code = options.code ?? null
    this.traceId = options.traceId ?? null
    this.status = options.status ?? null
    this.path = options.path ?? null
  }
}

export function normalizeBase(input: unknown): string {
  const raw = typeof input === 'string' ? input.trim() : ''
  if (raw === '') throw new EgmpError('缺少 eGMP 地址（baseUrl）')
  let url: URL
  try {
    url = new URL(raw)
  } catch {
    throw new EgmpError(`eGMP 地址不合法：${raw}`)
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') throw new EgmpError('eGMP 地址必须是 http(s)')
  return url.toString().replace(/\/+$/, '')
}

function sessionFile(dshHome: string): string {
  return join(dataDir(dshHome), 'egmp-session.json')
}

export function readEgmpSession(dshHome: string): EgmpSession | null {
  try {
    const file = sessionFile(dshHome)
    if (!existsSync(file)) return null
    const parsed = JSON.parse(readFileSync(file, 'utf8')) as Partial<EgmpSession> | null
    if (parsed === null || typeof parsed.token !== 'string' || parsed.token === '') return null
    return {
      baseUrl: normalizeBase(parsed.baseUrl),
      account: String(parsed.account ?? ''),
      token: parsed.token,
      savedAt: Number(parsed.savedAt ?? 0),
    }
  } catch {
    return null
  }
}

export function writeEgmpSession(dshHome: string, session: EgmpSession): void {
  const file = sessionFile(dshHome)
  mkdirSync(dataDir(dshHome), { recursive: true, mode: 0o700 })
  const tmp = `${file}.tmp`
  writeFileSync(tmp, `${JSON.stringify(session, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 })
  renameSync(tmp, file)
  try {
    chmodSync(file, 0o600)
  } catch {
    // Windows 上 chmod 基本是空操作
  }
}

export function clearEgmpSession(dshHome: string): void {
  const file = sessionFile(dshHome)
  if (existsSync(file)) writeFileSync(file, 'null\n', { encoding: 'utf8', mode: 0o600 })
}

/** 对外（浏览器）可见的会话摘要：**永不含 token**。 */
export function publicSession(session: EgmpSession | null): { loggedIn: boolean; baseUrl: string | null; account: string | null; savedAt: number | null } {
  if (session === null) return { loggedIn: false, baseUrl: null, account: null, savedAt: null }
  return { loggedIn: true, baseUrl: session.baseUrl, account: session.account, savedAt: session.savedAt }
}

interface RawResponse {
  status: number
  json: Record<string, unknown>
}

/** 发一个请求；不判 code（调用方需要看原始信封时用它）。 */
export async function request(
  session: { baseUrl: string; token: string },
  method: string,
  path: string,
  body?: unknown,
  query?: Record<string, unknown>,
): Promise<RawResponse> {
  const url = new URL(session.baseUrl + path)
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === undefined || value === null || value === '') continue
    url.searchParams.set(key, String(value))
  }
  const headers: Record<string, string> = { Authorization: `Bearer ${session.token}`, Accept: 'application/json' }
  const init: RequestInit = { method, headers }
  if (body !== undefined && method !== 'GET' && method !== 'DELETE') {
    // ★ 必须显式声明，否则平台回 415
    headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(body)
  }
  let response: Response
  try {
    response = await fetch(url, init)
  } catch (error) {
    throw new EgmpError(`请求 ${path} 失败：${error instanceof Error ? error.message : String(error)}`, { path })
  }
  const text = await response.text()
  let json: Record<string, unknown>
  try {
    json = text === '' ? {} : (JSON.parse(text) as Record<string, unknown>)
  } catch {
    throw new EgmpError(`平台返回非 JSON（HTTP ${response.status}）`, { status: response.status, path })
  }
  return { status: response.status, json }
}

/** 发请求并断言业务成功（code === 0）。 */
export async function call(
  session: { baseUrl: string; token: string },
  method: string,
  path: string,
  body?: unknown,
  query?: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const { status, json } = await request(session, method, path, body, query)
  const code = json.code
  if (status === 415) throw new EgmpError('平台拒绝请求体（415）：缺少或错误的 Content-Type', { status, path })
  if (code !== 0) {
    throw new EgmpError(String(json.message ?? json.detail ?? `HTTP ${status}`), {
      code: typeof code === 'number' || typeof code === 'string' ? code : null,
      traceId: typeof json.traceId === 'string' ? json.traceId : null,
      status,
      path,
    })
  }
  return json
}

/** 登录：换 JWT。成功后由调用方决定是否落盘。 */
export async function login(baseUrl: string, account: string, password: string): Promise<EgmpSession> {
  const base = normalizeBase(baseUrl)
  if (account === '' || password === '') throw new EgmpError('账号和密码都不能为空')
  const { status, json } = await request({ baseUrl: base, token: '' }, 'POST', '/api/openapi/v1.0/Auth', { account, password })
  if (status === 401 || status === 403) throw new EgmpError('账号或密码不正确', { status })
  if (json.code !== 0 || typeof json.data !== 'string') {
    throw new EgmpError(String(json.message ?? `登录失败（HTTP ${status}）`), { status })
  }
  return { baseUrl: base, account, token: json.data, savedAt: Date.now() }
}
