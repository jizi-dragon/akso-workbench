/**
 * 脱敏与截断。
 *
 * 监听器看到的是**真实会话流量**：Authorization、Cookie、登录响应里的 JWT 都在里面。
 * 这些值一旦落进 JSONL，就等于把令牌抄了一份到磁盘。所以规则只有一条：
 * **密钥类内容只记「存在 + 长度」，绝不记原文**。
 */

import type { BodyPreview } from './records.ts'

/** 头部里这些字段的值一律不落盘（大小写不敏感）。 */
const SENSITIVE_HEADERS = new Set([
  'authorization',
  'proxy-authorization',
  'cookie',
  'set-cookie',
  'x-auth-token',
  'x-xsrf-token',
  'x-csrf-token',
  'x-session-id',
  'token',
  'access-token',
  'refresh-token',
])

/** JSON 体里键名命中这些词的，值替换成 «redacted»。 */
const SENSITIVE_KEY = /(password|passwd|secret|token|authorization|cookie|credential|private[-_]?key|api[-_]?key|session[-_]?id)/i

const JWT_RE = /eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}/g
const BEARER_RE = /Bearer\s+[A-Za-z0-9._~+/-]{8,}=*/gi

export const MAX_BODY = 64 * 1024
const MAX_HEADER = 300

export function clipText(text: string, max: number): { text: string; truncated: boolean } {
  if (text.length <= max) return { text, truncated: false }
  return { text: text.slice(0, max), truncated: true }
}

/** 文本里出现的 JWT / Bearer 令牌，就地打码。 */
export function redactText(text: string): string {
  return text.replace(JWT_RE, '«jwt»').replace(BEARER_RE, 'Bearer «redacted»')
}

export function redactHeaders(headers: Record<string, string> | undefined): Record<string, string> {
  const out: Record<string, string> = {}
  if (headers === undefined) return out
  for (const [name, value] of Object.entries(headers)) {
    const key = name.toLowerCase()
    out[name] = SENSITIVE_HEADERS.has(key)
      ? `«redacted:len=${value.length}»`
      : clipText(redactText(value), MAX_HEADER).text
  }
  return out
}

/** 递归走 JSON，把敏感键的值换掉；深度有上限，防超深结构。 */
export function redactJsonValue(value: unknown, depth = 0): unknown {
  if (depth > 12) return '«deep»'
  if (Array.isArray(value)) return value.map((item) => redactJsonValue(item, depth + 1))
  if (value !== null && typeof value === 'object') {
    const out: Record<string, unknown> = {}
    for (const [key, inner] of Object.entries(value as Record<string, unknown>)) {
      out[key] = SENSITIVE_KEY.test(key) ? '«redacted»' : redactJsonValue(inner, depth + 1)
    }
    return out
  }
  if (typeof value === 'string') return redactText(value)
  return value
}

function guessKind(text: string, mimeType?: string): BodyPreview['kind'] {
  if (mimeType !== undefined && /json/i.test(mimeType)) return 'json'
  const head = text.trimStart().slice(0, 1)
  if (head === '{' || head === '[') return 'json'
  if (mimeType !== undefined && /(text|xml|javascript|x-www-form-urlencoded)/i.test(mimeType)) return 'text'
  return 'text'
}

/**
 * 把原始体转成可落盘的预览：
 *   · JSON 先解析、脱敏、再 pretty-print；
 *   · 解析失败就按文本打码；
 *   · 最后统一截断到 MAX_BODY。
 */
export function bodyPreview(raw: string | undefined, mimeType?: string, bytes?: number): BodyPreview | undefined {
  if (raw === undefined || raw === '') return undefined
  const size = bytes ?? Buffer.byteLength(raw, 'utf8')
  let kind = guessKind(raw, mimeType)
  let text = raw
  if (kind === 'json') {
    try {
      text = JSON.stringify(redactJsonValue(JSON.parse(raw)), null, 2)
    } catch {
      kind = 'text'
      text = redactText(raw)
    }
  } else {
    text = redactText(raw)
  }
  const clipped = clipText(text, MAX_BODY)
  return { kind, preview: clipped.text, truncated: clipped.truncated || size > MAX_BODY, bytes: size }
}
