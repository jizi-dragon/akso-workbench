/**
 * 捕获数据的查询层：去重出接口清单，并把一条记录提成能力草稿。
 *
 * 草稿**只打印、不写文件、不自动入库**——监听看到的是「调用过的形状」，
 * 是否稳定、是否该给人用，得人工判断（这正是当初 47 条里 4 条踩坑的原因）。
 */

import type { CaptureRecord } from './records.ts'
import { dedupeKey } from './records.ts'
import { querySignature } from './paths.ts'

export interface EndpointSummary {
  key: string
  method: string
  pathTemplate: string
  count: number
  hosts: string[]
  statuses: Record<string, number>
  lastAt: number
  sampleUrl: string
  contentTypes: string[]
}

export function summarize(records: readonly CaptureRecord[]): EndpointSummary[] {
  const table = new Map<string, EndpointSummary>()
  for (const record of records) {
    const key = `${dedupeKey(record)}?${querySignature(record.query)}`
    let entry = table.get(key)
    if (entry === undefined) {
      entry = {
        key,
        method: record.method,
        pathTemplate: record.pathTemplate,
        count: 0,
        hosts: [],
        statuses: {},
        lastAt: 0,
        sampleUrl: record.url,
        contentTypes: [],
      }
      table.set(key, entry)
    }
    entry.count += 1
    entry.lastAt = Math.max(entry.lastAt, record.at)
    if (!entry.hosts.includes(record.host)) entry.hosts.push(record.host)
    const status = record.status === undefined ? 'failed' : String(record.status)
    entry.statuses[status] = (entry.statuses[status] ?? 0) + 1
    const mime = record.mimeType ?? record.responseHeaders?.['content-type'] ?? record.responseHeaders?.['Content-Type']
    if (mime !== undefined) {
      const base = mime.split(';')[0]?.trim() ?? mime
      if (!entry.contentTypes.includes(base)) entry.contentTypes.push(base)
    }
  }
  return [...table.values()].sort((left, right) => right.count - left.count || left.pathTemplate.localeCompare(right.pathTemplate))
}

export interface RecordFilter {
  method?: string
  grep?: string
  limit?: number
  host?: string
}

export function filterRecords(records: readonly CaptureRecord[], filter: RecordFilter = {}): CaptureRecord[] {
  const method = filter.method?.toUpperCase()
  const grep = filter.grep?.toLowerCase()
  const rows = records.filter((record) => {
    if (method !== undefined && record.method !== method) return false
    if (filter.host !== undefined && record.host !== filter.host) return false
    if (grep !== undefined) {
      const haystack = `${record.method} ${record.url} ${record.status ?? ''}`.toLowerCase()
      if (!haystack.includes(grep)) return false
    }
    return true
  })
  return filter.limit === undefined ? rows : rows.slice(-filter.limit)
}

export interface DraftParam {
  name: string
  in: 'path' | 'query' | 'body'
  type: 'string' | 'number' | 'boolean' | 'object' | 'array'
  required: boolean
}

export interface CapabilityDraft {
  id: string
  domain: string
  title: string
  description: string
  method: string
  path: string
  risk: 'low' | 'medium' | 'high'
  evidence: string
  params: DraftParam[]
  example: Record<string, unknown>
  returns: string
  notes: string
}

function typeOf(value: unknown): DraftParam['type'] {
  if (typeof value === 'number') return 'number'
  if (typeof value === 'boolean') return 'boolean'
  if (Array.isArray(value)) return 'array'
  if (value !== null && typeof value === 'object') return 'object'
  return 'string'
}

function parseJsonBody(record: CaptureRecord): Record<string, unknown> | undefined {
  const body = record.requestBody
  if (body === undefined || body.kind !== 'json' || body.truncated) return undefined
  try {
    const value = JSON.parse(body.preview) as unknown
    return value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined
  } catch {
    return undefined
  }
}

export function draftFrom(record: CaptureRecord, options: { id?: string; domain?: string } = {}): CapabilityDraft {
  const segments = record.pathTemplate.split('/').filter((segment) => segment !== '')
  const last = segments[segments.length - 1] ?? 'root'
  const slug = last.replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, '').toLowerCase() || 'root'
  const id = options.id ?? `captured.${slug}`

  const params: DraftParam[] = []
  for (const _ of record.pathTemplate.match(/\{id\}/g) ?? []) {
    params.push({ name: 'id', in: 'path', type: 'string', required: true })
  }
  for (const key of Object.keys(record.query)) {
    params.push({ name: key, in: 'query', type: 'string', required: false })
  }
  const example: Record<string, unknown> = { ...record.query }
  const body = parseJsonBody(record)
  if (body !== undefined) {
    for (const [key, value] of Object.entries(body)) {
      if (!params.some((param) => param.in === 'body' && param.name === key)) {
        params.push({ name: key, in: 'body', type: typeOf(value), required: false })
      }
      example[key] = value
    }
  }

  let returns = '（未捕获到响应体）'
  if (record.responseBody !== undefined && record.responseBody.kind === 'json' && !record.responseBody.truncated) {
    try {
      const value = JSON.parse(record.responseBody.preview) as unknown
      const keys = value !== null && typeof value === 'object' && !Array.isArray(value)
        ? Object.keys(value as Record<string, unknown>)
        : []
      returns = keys.length > 0 ? `JSON：${keys.join(', ')}` : 'JSON（结构见示例）'
    } catch {
      returns = 'JSON（响应体未能解析）'
    }
  }

  const notes: string[] = ['由监听捕获生成，需人工确认后入库；风险等级、参数必填性未经验证。']
  if (record.requestBody?.truncated === true) notes.push('请求体已截断，参数需补全。')
  if (record.failed !== undefined) notes.push(`捕获时请求失败：${record.failed}`)

  return {
    id,
    domain: options.domain ?? 'captured',
    title: `${record.method} ${record.pathTemplate}`,
    description: `监听捕获于 ${new Date(record.at).toISOString()}，HTTP ${record.status ?? '—'}。`,
    method: record.method,
    path: record.pathTemplate,
    risk: 'low',
    evidence: `capture ${new Date(record.at).toISOString()} ${record.status ?? 'failed'}`,
    params,
    example,
    returns,
    notes: notes.join(' '),
  }
}

/** 输出成可以直接粘进 capabilities.ts 的 TS 字面量。 */
export function formatDraft(draft: CapabilityDraft): string {
  const params = draft.params.length === 0
    ? '[]'
    : `[\n${draft.params
      .map((param) => `      { name: ${JSON.stringify(param.name)}, in: ${JSON.stringify(param.in)}, type: ${JSON.stringify(param.type)}, required: ${String(param.required)} },`)
      .join('\n')}\n    ]`
  const example = JSON.stringify(draft.example, null, 2).split('\n').map((line, index) => (index === 0 ? line : `    ${line}`)).join('\n')
  return [
    '  {',
    `    id: ${JSON.stringify(draft.id)},`,
    `    domain: ${JSON.stringify(draft.domain)},`,
    `    title: ${JSON.stringify(draft.title)},`,
    `    description: ${JSON.stringify(draft.description)},`,
    `    method: ${JSON.stringify(draft.method)},`,
    `    path: ${JSON.stringify(draft.path)},`,
    `    risk: ${JSON.stringify(draft.risk)},`,
    `    evidence: ${JSON.stringify(draft.evidence)},`,
    `    params: ${params},`,
    `    example: ${example},`,
    `    returns: ${JSON.stringify(draft.returns)},`,
    `    notes: ${JSON.stringify(draft.notes)},`,
    '  },',
  ].join('\n')
}
