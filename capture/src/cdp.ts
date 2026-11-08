/**
 * 最小 CDP 客户端：一条 WebSocket，按 id 关联请求/响应，事件按 method 分发。
 *
 * 不引 puppeteer/playwright：Node ≥ 22 自带全局 WebSocket，只连本机调试端口，
 * 依赖为零。socket 工厂可注入，测试里用假 socket 走完整条分发路径。
 */

export interface SocketLike {
  readonly readyState: number
  addEventListener(type: string, listener: (event: unknown) => void): void
  send(data: string): void
  close(code?: number, reason?: string): void
}

export type SocketFactory = (url: string) => SocketLike

const defaultFactory: SocketFactory = (url) => {
  const Ctor = (globalThis as { WebSocket?: new (url: string) => SocketLike }).WebSocket
  if (Ctor === undefined) throw new Error('这个 Node 没有全局 WebSocket（需要 Node ≥ 22）')
  return new Ctor(url) as SocketLike
}

export interface CdpClientOptions {
  /** 建立连接的超时，默认 5s */
  timeoutMs?: number
  factory?: SocketFactory
}

export interface CdpErrorPayload {
  code: number
  message: string
  data?: string
}

interface Pending {
  resolve: (value: unknown) => void
  reject: (error: Error) => void
  timer: ReturnType<typeof setTimeout>
}

type Handler = (params: Record<string, unknown>) => void

export class CdpClient {
  private socket: SocketLike
  private seq = 0
  private pending = new Map<number, Pending>()
  private handlers = new Map<string, Set<Handler>>()
  private closed = false

  private constructor(socket: SocketLike) {
    this.socket = socket
    socket.addEventListener('message', (event) => this.onMessage(event))
    socket.addEventListener('close', () => this.onClose())
    socket.addEventListener('error', () => this.onClose())
  }

  static connect(url: string, options: CdpClientOptions = {}): Promise<CdpClient> {
    const factory = options.factory ?? defaultFactory
    const timeoutMs = options.timeoutMs ?? 5000
    return new Promise<CdpClient>((resolve, reject) => {
      let socket: SocketLike
      try {
        socket = factory(url)
      } catch (error) {
        reject(error instanceof Error ? error : new Error(String(error)))
        return
      }
      const timer = setTimeout(() => {
        try {
          socket.close()
        } catch {
          // 忽略
        }
        reject(new Error(`连接 CDP 超时（${timeoutMs}ms）：${url}`))
      }, timeoutMs)
      const onOpen = (): void => {
        clearTimeout(timer)
        resolve(new CdpClient(socket))
      }
      // 已经 open 的假 socket 直接进
      if (socket.readyState === 1) {
        onOpen()
        return
      }
      socket.addEventListener('open', onOpen)
    })
  }

  private onMessage(event: unknown): void {
    const data = (event as { data?: unknown }).data
    if (typeof data !== 'string') return
    let message: { id?: number; method?: string; params?: Record<string, unknown>; result?: unknown; error?: CdpErrorPayload }
    try {
      message = JSON.parse(data) as typeof message
    } catch {
      return
    }
    if (typeof message.id === 'number') {
      const entry = this.pending.get(message.id)
      if (entry === undefined) return
      this.pending.delete(message.id)
      clearTimeout(entry.timer)
      if (message.error !== undefined) {
        const error = new Error(message.error.message) as Error & { code?: number; data?: string }
        error.code = message.error.code
        if (message.error.data !== undefined) error.data = message.error.data
        entry.reject(error)
      } else {
        entry.resolve(message.result)
      }
      return
    }
    if (typeof message.method === 'string') {
      const set = this.handlers.get(message.method)
      if (set === undefined) return
      const params = message.params ?? {}
      for (const handler of set) {
        try {
          handler(params)
        } catch {
          // 单个处理器抛错不影响分发
        }
      }
    }
  }

  private onClose(): void {
    if (this.closed) return
    this.closed = true
    for (const entry of this.pending.values()) {
      clearTimeout(entry.timer)
      entry.reject(new Error('CDP 连接已关闭'))
    }
    this.pending.clear()
  }

  send<T = Record<string, unknown>>(method: string, params: Record<string, unknown> = {}, timeoutMs = 15000): Promise<T> {
    if (this.closed) return Promise.reject(new Error('CDP 连接已关闭'))
    const id = (this.seq += 1)
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id)
        reject(new Error(`CDP 调用超时（${timeoutMs}ms）：${method}`))
      }, timeoutMs)
      this.pending.set(id, { resolve: resolve as (value: unknown) => void, reject, timer })
      try {
        this.socket.send(JSON.stringify({ id, method, params }))
      } catch (error) {
        clearTimeout(timer)
        this.pending.delete(id)
        reject(error instanceof Error ? error : new Error(String(error)))
      }
    })
  }

  on(method: string, handler: Handler): () => void {
    let set = this.handlers.get(method)
    if (set === undefined) {
      set = new Set()
      this.handlers.set(method, set)
    }
    set.add(handler)
    return () => {
      set.delete(handler)
    }
  }

  close(): void {
    if (this.closed) return
    this.closed = true
    try {
      this.socket.close()
    } catch {
      // 忽略
    }
    for (const entry of this.pending.values()) {
      clearTimeout(entry.timer)
      entry.reject(new Error('CDP 连接已关闭'))
    }
    this.pending.clear()
  }
}
