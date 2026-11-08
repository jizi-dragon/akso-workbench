/**
 * URL 规范化：把「同一个接口的不同次调用」折成同一个键。
 *
 *   /api/platform/BasicObject/265e232d-6cae-6a90-b580-1ca742a300f4
 * → /api/platform/BasicObject/{id}
 *
 * 只折明显是 id 的段（UUID、纯数字、长 hex、带数字的长 token），
 * 不折 `GetWorkflowBasicPageViewList` 这种接口名——它没有数字、也没有连字符。
 */

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const HEX_RE = /^[0-9a-f]{16,}$/i
const NUM_RE = /^\d+$/
const TOKEN_RE = /^[A-Za-z0-9_-]{20,}$/

/** 会被脱敏的查询键（值不落原文，但键名保留，方便推断参数）。 */
const SENSITIVE_QUERY_KEY = /(password|passwd|secret|token|authorization|cookie|credential|session)/i

export function normalizePath(pathname: string): string {
  const collapsed = pathname.replace(/\/{2,}/g, '/')
  if (collapsed === '') return '/'
  const trimmed = collapsed.length > 1 && collapsed.endsWith('/') ? collapsed.slice(0, -1) : collapsed
  return trimmed.startsWith('/') ? trimmed : `/${trimmed}`
}

function looksLikeId(segment: string): boolean {
  if (UUID_RE.test(segment) || HEX_RE.test(segment) || NUM_RE.test(segment)) return true
  if (!TOKEN_RE.test(segment)) return false
  // 长 token 里必须含数字，否则更可能是接口名/单词
  return /\d/.test(segment)
}

export function pathTemplate(pathname: string): string {
  const normalized = normalizePath(pathname)
  const segments = normalized.split('/').filter((segment) => segment !== '')
  if (segments.length === 0) return '/'
  return `/${segments.map((segment) => (looksLikeId(segment) ? '{id}' : segment)).join('/')}`
}

/** 查询参数：键名保留，敏感键的值替换掉。 */
export function parseQuery(search: string): Record<string, string> {
  const params = new URLSearchParams(search)
  const out: Record<string, string> = {}
  for (const [key, value] of params) {
    out[key] = SENSITIVE_QUERY_KEY.test(key) ? '«redacted»' : value
  }
  return out
}

/** 去重时只看键名，不看值。 */
export function querySignature(query: Record<string, string>): string {
  return Object.keys(query).sort().join('&')
}

export interface ParsedUrl {
  url: string
  host: string
  path: string
  pathTemplate: string
  query: Record<string, string>
}

export function parseUrl(raw: string): ParsedUrl | null {
  let parsed: URL
  try {
    parsed = new URL(raw)
  } catch {
    return null
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null
  return {
    url: parsed.toString(),
    host: parsed.host,
    path: normalizePath(parsed.pathname),
    pathTemplate: pathTemplate(parsed.pathname),
    query: parseQuery(parsed.search),
  }
}

/** host 白名单：`aksoegmp.com` 同时匹配 `standard-val.aksoegmp.com`。 */
export function hostAllowed(host: string, allow: readonly string[] | null): boolean {
  if (allow === null) return true
  const target = host.toLowerCase()
  return allow.some((entry) => {
    const suffix = entry.toLowerCase().replace(/^\*\./, '')
    return target === suffix || target.endsWith(`.${suffix}`)
  })
}
