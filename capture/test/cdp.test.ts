/**
 * CDP 客户端：id 关联、事件分发、超时与关闭。
 * 用假 socket 走完整条路径，不需要真的开浏览器。
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { CdpClient } from '../src/cdp.ts'
import type { SocketLike } from '../src/cdp.ts'

class FakeSocket implements SocketLike {
  readyState = 1
  sent: string[] = []
  private listeners = new Map<string, Set<(event: unknown) => void>>()

  addEventListener(type: string, listener: (event: unknown) => void): void {
    let set = this.listeners.get(type)
    if (set === undefined) {
      set = new Set()
      this.listeners.set(type, set)
    }
    set.add(listener)
  }

  send(data: string): void {
    this.sent.push(data)
  }

  close(): void {
    this.readyState = 3
    this.dispatch('close', {})
  }

  emit(payload: unknown): void {
    this.dispatch('message', { data: JSON.stringify(payload) })
  }

  dispatch(type: string, event: unknown): void {
    for (const listener of this.listeners.get(type) ?? []) listener(event)
  }

  lastFrame(): { id: number; method: string; params?: Record<string, unknown> } {
    const frame = JSON.parse(this.sent[this.sent.length - 1] ?? '{}') as { id: number; method: string; params?: Record<string, unknown> }
    return frame
  }
}

async function connected(): Promise<{ client: CdpClient; socket: FakeSocket }> {
  const socket = new FakeSocket()
  const client = await CdpClient.connect('ws://127.0.0.1:9222/devtools/page/x', { factory: () => socket })
  return { client, socket }
}

test('send：帧带自增 id，响应按 id 回到对应 Promise', async () => {
  const { client, socket } = await connected()
  try {
    const first = client.send<{ ok: boolean }>('Page.enable')
    const frame = socket.lastFrame()
    assert.equal(frame.method, 'Page.enable')
    socket.emit({ id: frame.id, result: { ok: true } })
    assert.deepEqual(await first, { ok: true })

    const second = client.send<{ n: number }>('Runtime.evaluate', { expression: '1+1' })
    const frame2 = socket.lastFrame()
    assert.deepEqual(frame2.params, { expression: '1+1' })
    assert.equal(frame2.id, frame.id + 1)
    socket.emit({ id: frame2.id, result: { n: 2 } })
    assert.deepEqual(await second, { n: 2 })
  } finally {
    client.close()
  }
})

test('send：CDP error 变成带 code 的 Error', async () => {
  const { client, socket } = await connected()
  try {
    const promise = client.send('Bad.method')
    socket.emit({ id: socket.lastFrame().id, error: { code: -32601, message: 'Method not found' } })
    await assert.rejects(promise, (error: unknown) => {
      assert.ok(error instanceof Error)
      assert.equal((error as Error & { code?: number }).code, -32601)
      assert.match(error.message, /Method not found/)
      return true
    })
  } finally {
    client.close()
  }
})

test('事件：按 method 分发，取消订阅后不再收到', async () => {
  const { client, socket } = await connected()
  try {
    const seen: Array<Record<string, unknown>> = []
    const off = client.on('Network.requestWillBeSent', (params) => seen.push(params))
    socket.emit({ method: 'Network.requestWillBeSent', params: { requestId: 'r1' } })
    assert.deepEqual(seen, [{ requestId: 'r1' }])

    off()
    socket.emit({ method: 'Network.requestWillBeSent', params: { requestId: 'r2' } })
    assert.equal(seen.length, 1)
  } finally {
    client.close()
  }
})

test('send：超时拒绝并清掉 pending', async () => {
  const { client } = await connected()
  try {
    await assert.rejects(client.send('Slow.method', {}, 10), /超时/)
  } finally {
    client.close()
  }
})

test('close：挂起的请求全部拒绝', async () => {
  const { client } = await connected()
  const pending = client.send('Never.answers')
  client.close()
  await assert.rejects(pending, /已关闭/)
})

test('connect：socket 工厂抛错时 Promise 拒绝，不崩进程', async () => {
  await assert.rejects(
    CdpClient.connect('ws://127.0.0.1:9222/devtools/page/x', {
      factory: () => {
        throw new Error('ECONNREFUSED')
      },
    }),
    /ECONNREFUSED/,
  )
})

test('connect：一直不 open 时按超时拒绝', async () => {
  const socket = new FakeSocket()
  socket.readyState = 0
  await assert.rejects(
    CdpClient.connect('ws://127.0.0.1:9222/devtools/page/x', { factory: () => socket, timeoutMs: 10 }),
    /超时/,
  )
})
