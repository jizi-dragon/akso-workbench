/**
 * CDP 网络录制器：把标签页里发生的 HTTP 请求写成 CaptureRecord。
 *
 * 只订阅 Network 域，不改页面行为：
 *   requestWillBeSent → responseReceived → loadingFinished（取 body）/ loadingFailed。
 * 过滤：host 白名单 + 跳过静态资源（图片/脚本/样式/字体…），除非 --all。
 */

import type { CdpClient } from './cdp.ts'
import type { CaptureRecord } from './records.ts'
import type { CaptureStore } from './store.ts'
import { hostAllowed, parseUrl } from './paths.ts'
import { bodyPreview, redactHeaders } from './redact.ts'

/** 这些资源类型默认不记：噪音大、对「扩接口」没价值。 */
const STATIC_TYPES = new Set(['Image', 'Stylesheet', 'Script', 'Font', 'Media', 'Manifest', 'TextTrack', 'Ping', 'Prefetch', 'SignedExchange'])

const TEXTUAL_MIME = /(json|text|xml|javascript|x-www-form-urlencoded)/i

interface PendingRecord {
  requestId: string
  startTs: number
  at: number
  method: string
  url: string
  resourceType?: string
  initiator?: string
  requestHeaders: Record<string, string>
  postData?: string
  response?: { status: number; statusText?: string; mimeType?: string; headers: Record<string, string> }
}

export interface RecorderOptions {
  client: CdpClient
  store: CaptureStore
  /** null = 不限 host；默认由 CLI 给 ['aksoegmp.com'] */
  hosts?: readonly string[] | null
  allTypes?: boolean
  page?: { id?: string; url?: string; title?: string }
  onError?: (message: string, error: unknown) => void
  onRecord?: (record: CaptureRecord) => void
}

function header(headers: Record<string, string>, name: string): string | undefined {
  const lower = name.toLowerCase()
  for (const [key, value] of Object.entries(headers)) {
    if (key.toLowerCase() === lower) return value
  }
  return undefined
}

export class Recorder {
  private client: CdpClient
  private store: CaptureStore
  private hosts: readonly string[] | null
  private allTypes: boolean
  private page: RecorderOptions['page']
  private onError: (message: string, error: unknown) => void
  private onRecord: ((record: CaptureRecord) => void) | undefined
  private pending = new Map<string, PendingRecord>()
  private off: Array<() => void> = []
  private started = false

  constructor(options: RecorderOptions) {
    this.client = options.client
    this.store = options.store
    this.hosts = options.hosts === undefined ? ['aksoegmp.com'] : options.hosts
    this.allTypes = options.allTypes === true
    this.page = options.page
    this.onError = options.onError ?? (() => {})
    this.onRecord = options.onRecord
  }

  async start(): Promise<void> {
    if (this.started) return
    this.started = true
    await this.client.send('Network.enable', {
      maxTotalBufferSize: 100_000_000,
      maxResourceBufferSize: 20_000_000,
      maxPostDataSize: 64 * 1024,
    })
    this.off.push(this.client.on('Network.requestWillBeSent', (params) => this.handleRequest(params)))
    this.off.push(this.client.on('Network.requestWillBeSentExtraInfo', (params) => this.handleRequestExtra(params)))
    this.off.push(this.client.on('Network.responseReceived', (params) => this.handleResponse(params)))
    this.off.push(this.client.on('Network.responseReceivedExtraInfo', (params) => this.handleResponseExtra(params)))
    this.off.push(this.client.on('Network.loadingFinished', (params) => this.handleFinished(params)))
    this.off.push(this.client.on('Network.loadingFailed', (params) => this.handleFailed(params)))
  }

  stop(): void {
    for (const off of this.off) off()
    this.off = []
    this.pending.clear()
  }

  private wanted(url: string, resourceType?: string): boolean {
    const parsed = parseUrl(url)
    if (parsed === null) return false
    if (!hostAllowed(parsed.host, this.hosts)) return false
    if (!this.allTypes && resourceType !== undefined && STATIC_TYPES.has(resourceType)) return false
    return true
  }

  private handleRequest(params: Record<string, unknown>): void {
    const requestId = String(params.requestId ?? '')
    const request = params.request as { url?: string; method?: string; headers?: Record<string, string>; postData?: string; hasPostData?: boolean } | undefined
    const type = typeof params.type === 'string' ? params.type : undefined
    if (requestId === '' || request?.url === undefined || request.url === '') return

    // 重定向：先把上一跳结算掉（它自己也是一次可观察的响应）
    const previous = this.pending.get(requestId)
    if (previous !== undefined && params.redirectResponse !== undefined) {
      this.pending.delete(requestId)
      const redirect = params.redirectResponse as { status?: number; statusText?: string; mimeType?: string; headers?: Record<string, string> }
      previous.response = {
        status: redirect.status ?? 0,
        statusText: redirect.statusText,
        mimeType: redirect.mimeType,
        headers: redirect.headers ?? {},
      }
      void this.finish(previous, true)
    }

    if (!this.wanted(request.url, type)) return

    const wallTime = typeof params.wallTime === 'number' ? params.wallTime : Date.now() / 1000
    const timestamp = typeof params.timestamp === 'number' ? params.timestamp : 0
    const initiator = params.initiator as { type?: string } | undefined
    this.pending.set(requestId, {
      requestId,
      startTs: timestamp,
      at: Math.round(wallTime * 1000),
      method: (request.method ?? 'GET').toUpperCase(),
      url: request.url,
      resourceType: type,
      initiator: initiator?.type,
      requestHeaders: request.headers ?? {},
      postData: request.hasPostData === true ? request.postData : undefined,
    })
  }

  private handleRequestExtra(params: Record<string, unknown>): void {
    const pending = this.pending.get(String(params.requestId ?? ''))
    const headers = params.headers as Record<string, string> | undefined
    if (pending === undefined || headers === undefined) return
    pending.requestHeaders = { ...headers, ...pending.requestHeaders }
  }

  private handleResponse(params: Record<string, unknown>): void {
    const pending = this.pending.get(String(params.requestId ?? ''))
    const response = params.response as { status?: number; statusText?: string; mimeType?: string; headers?: Record<string, string> } | undefined
    if (pending === undefined || response === undefined) return
    pending.response = {
      status: response.status ?? 0,
      statusText: response.statusText,
      mimeType: response.mimeType,
      headers: response.headers ?? {},
    }
  }

  private handleResponseExtra(params: Record<string, unknown>): void {
    const pending = this.pending.get(String(params.requestId ?? ''))
    const headers = params.headers as Record<string, string> | undefined
    if (pending === undefined || pending.response === undefined || headers === undefined) return
    pending.response.headers = { ...headers, ...pending.response.headers }
  }

  private handleFinished(params: Record<string, unknown>): void {
    const requestId = String(params.requestId ?? '')
    const pending = this.pending.get(requestId)
    if (pending === undefined) return
    this.pending.delete(requestId)
    void this.finish(pending, false, typeof params.timestamp === 'number' ? params.timestamp : undefined)
  }

  private handleFailed(params: Record<string, unknown>): void {
    const requestId = String(params.requestId ?? '')
    const pending = this.pending.get(requestId)
    if (pending === undefined) return
    this.pending.delete(requestId)
    const errorText = typeof params.errorText === 'string' ? params.errorText : '请求失败'
    const blocked = typeof params.blockedReason === 'string' ? `（${params.blockedReason}）` : ''
    this.emit(pending, undefined, `${errorText}${blocked}`)
  }

  private async finish(pending: PendingRecord, redirect: boolean, endTs?: number): Promise<void> {
    let responseBody: CaptureRecord['responseBody']
    if (!redirect && pending.response !== undefined && pending.response.status !== 204 && pending.response.status !== 304) {
      const mime = pending.response.mimeType ?? header(pending.response.headers, 'content-type') ?? ''
      if (TEXTUAL_MIME.test(mime)) {
        try {
          const body = await this.client.send<{ body: string; base64Encoded: boolean }>('Network.getResponseBody', { requestId: pending.requestId })
          const raw = body.base64Encoded ? Buffer.from(body.body, 'base64').toString('utf8') : body.body
          responseBody = bodyPreview(raw, mime)
        } catch (error) {
          this.onError(`取响应体失败（${pending.requestId}）`, error)
        }
      }
    }
    const duration = endTs !== undefined && pending.startTs > 0 && endTs >= pending.startTs
      ? Math.round((endTs - pending.startTs) * 1000)
      : undefined
    this.emit(pending, responseBody, undefined, duration)
  }

  private emit(pending: PendingRecord, responseBody: CaptureRecord['responseBody'], failed?: string, durationMs?: number): void {
    const parsed = parseUrl(pending.url)
    if (parsed === null) return
    const response = pending.response
    const mimeType = response?.mimeType ?? (response === undefined ? undefined : header(response.headers, 'content-type'))
    const record: CaptureRecord = {
      v: 1,
      id: `${this.store.sessionId}:${pending.requestId}`,
      sessionId: this.store.sessionId,
      at: pending.at,
      ...(this.page === undefined ? {} : { page: this.page }),
      method: pending.method,
      url: parsed.url,
      host: parsed.host,
      path: parsed.path,
      pathTemplate: parsed.pathTemplate,
      query: parsed.query,
      ...(pending.resourceType === undefined ? {} : { resourceType: pending.resourceType }),
      ...(response === undefined ? {} : { status: response.status, statusText: response.statusText }),
      ...(mimeType === undefined ? {} : { mimeType }),
      requestHeaders: redactHeaders(pending.requestHeaders),
      ...(response === undefined ? {} : { responseHeaders: redactHeaders(response.headers) }),
      ...(pending.postData === undefined
        ? {}
        : { requestBody: bodyPreview(pending.postData, header(pending.requestHeaders, 'content-type')) }),
      ...(responseBody === undefined ? {} : { responseBody }),
      ...(durationMs === undefined ? {} : { durationMs }),
      ...(failed === undefined ? {} : { failed }),
      ...(pending.initiator === undefined ? {} : { initiator: pending.initiator }),
    }
    try {
      this.store.append(record)
      this.onRecord?.(record)
    } catch (error) {
      this.onError('写捕获文件失败', error)
    }
  }
}
