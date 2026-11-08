/**
 * 一条捕获记录 = 一次 HTTP 请求 + 它的响应（能拿到的话）。
 *
 * 设计约束：
 *   · **不存原文密钥**：头里的 Authorization/Cookie、体里的 password/token 一律脱敏，
 *     只留「有/无 + 长度」，排查时够用，泄不出去；
 *   · **体截断**：请求/响应各留 64KB，够看清结构，不至于把 captures 目录塞爆；
 *   · **路径模板**：UUID/数字/长 hex 段折成 `{id}`，同一接口的多次调用才能去重。
 */

export interface BodyPreview {
  kind: 'json' | 'text' | 'binary'
  preview: string
  truncated: boolean
  bytes: number
}

export interface CaptureRecord {
  v: 1
  /** `${sessionId}:${requestId}` —— 同一次会话内唯一 */
  id: string
  sessionId: string
  /** 请求开始时间（epoch ms） */
  at: number
  page?: { id?: string; url?: string; title?: string }
  method: string
  url: string
  host: string
  path: string
  /** 去重用的路径模板，例如 /api/platform/BasicObject/{id} */
  pathTemplate: string
  /** 查询参数（敏感键的值已脱敏；键名保留，用于推断参数） */
  query: Record<string, string>
  resourceType?: string
  status?: number
  statusText?: string
  mimeType?: string
  requestHeaders: Record<string, string>
  responseHeaders?: Record<string, string>
  requestBody?: BodyPreview
  responseBody?: BodyPreview
  durationMs?: number
  /** 失败原因（loadingFailed 的 errorText / blockedReason） */
  failed?: string
  initiator?: string
}

/** 去重键：方法 + 路径模板（不看 query 值，避免 id/时间戳把同一接口拆散）。 */
export function dedupeKey(record: CaptureRecord): string {
  return `${record.method} ${record.pathTemplate}`
}
