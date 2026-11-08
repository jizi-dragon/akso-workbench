/**
 * 调试端口上的标签页发现：GET http://127.0.0.1:<port>/json/list
 *
 * 只连回环地址——这是监听体系的边界，不做远程调试。
 */

export interface CdpTarget {
  id: string
  type: string
  url: string
  title: string
  webSocketDebuggerUrl: string
}

export interface ListTargetsOptions {
  port?: number
  host?: string
  timeoutMs?: number
}

export async function listTargets(options: ListTargetsOptions = {}): Promise<CdpTarget[]> {
  const host = options.host ?? '127.0.0.1'
  const port = options.port ?? 9222
  const timeoutMs = options.timeoutMs ?? 3000
  const response = await fetch(`http://${host}:${port}/json/list`, { signal: AbortSignal.timeout(timeoutMs) })
  if (!response.ok) throw new Error(`调试端口返回 HTTP ${response.status}：http://${host}:${port}/json/list`)
  const rows = (await response.json()) as CdpTarget[]
  return rows.filter((row) => row.type === 'page' && typeof row.webSocketDebuggerUrl === 'string')
}

export async function versionInfo(options: ListTargetsOptions = {}): Promise<{ Browser?: string; 'Protocol-Version'?: string }> {
  const host = options.host ?? '127.0.0.1'
  const port = options.port ?? 9222
  const response = await fetch(`http://${host}:${port}/json/version`, { signal: AbortSignal.timeout(options.timeoutMs ?? 3000) })
  if (!response.ok) throw new Error(`调试端口返回 HTTP ${response.status}`)
  return (await response.json()) as { Browser?: string; 'Protocol-Version'?: string }
}

/** 选标签页：给了片段按 URL 匹配，否则取第一个 page。 */
export function pickTarget(targets: CdpTarget[], match?: string): CdpTarget | null {
  if (targets.length === 0) return null
  if (match === undefined || match === '') return targets[0] ?? null
  return targets.find((target) => target.url.includes(match)) ?? null
}
