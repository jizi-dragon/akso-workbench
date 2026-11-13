/**
 * 对象分析历史：每次成功分析落一份 JSON，页面按时间倒序打开。
 *
 * 只存分析结果本身（status/action/workflow 模型），不存口令、不存令牌；
 * 环境与账号已经在 `result.source` 里，打开历史即可知道是在哪套环境分析的。
 */
import { randomUUID } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import type { AnalysisResult } from './analysis.ts'
import { dataDir } from './store.ts'

const MAX_ENTRIES = 50
const ID_PATTERN = /^[a-z0-9-]+$/

export interface AnalysisHistoryItem {
  id: string
  createdAt: string
  query: string
  objectCode: string
  objectName: string
  statuses: number
  enabledStatuses: number
  userActions: number
  enterActions: number
  workflows: number
  baseUrl: string
  account: string
}

interface StoredEntry extends AnalysisHistoryItem {
  result: AnalysisResult
}

function historyDir(dshHome: string): string {
  return join(dataDir(dshHome), 'analysis-history')
}

function entryFile(dshHome: string, id: string): string {
  return join(historyDir(dshHome), `${id}.json`)
}

function summary(entry: StoredEntry): AnalysisHistoryItem {
  return {
    id: entry.id,
    createdAt: entry.createdAt,
    query: entry.query,
    objectCode: entry.objectCode,
    objectName: entry.objectName,
    statuses: entry.statuses,
    enabledStatuses: entry.enabledStatuses,
    userActions: entry.userActions,
    enterActions: entry.enterActions,
    workflows: entry.workflows,
    baseUrl: entry.baseUrl,
    account: entry.account,
  }
}

/** 保存一次成功分析；返回页面刷新列表需要的摘要字段。 */
export function saveAnalysisHistory(dshHome: string, query: string, result: AnalysisResult): AnalysisHistoryItem {
  const dir = historyDir(dshHome)
  mkdirSync(dir, { recursive: true, mode: 0o700 })
  const createdAt = new Date().toISOString()
  const id = `${Date.now().toString(36)}-${randomUUID().slice(0, 8)}`
  const item: AnalysisHistoryItem = {
    id,
    createdAt,
    query,
    objectCode: result.object.code,
    objectName: result.object.name,
    statuses: result.totals.statuses,
    enabledStatuses: result.totals.enabledStatuses,
    userActions: result.totals.userActions,
    enterActions: result.totals.enterActions,
    workflows: result.workflows?.length ?? 0,
    baseUrl: result.source.baseUrl,
    account: result.source.account,
  }
  const stored: StoredEntry = { ...item, result }
  const file = entryFile(dshHome, id)
  const tmp = `${file}.tmp`
  writeFileSync(tmp, `${JSON.stringify(stored)}\n`, { encoding: 'utf8', mode: 0o600 })
  renameSync(tmp, file)
  prune(dshHome)
  return item
}

function prune(dshHome: string): void {
  const dir = historyDir(dshHome)
  if (!existsSync(dir)) return
  const files = readdirSync(dir)
    .filter((name) => name.endsWith('.json'))
    .map((name) => {
      const file = join(dir, name)
      try {
        const parsed = JSON.parse(readFileSync(file, 'utf8')) as Partial<StoredEntry>
        return { file, createdAt: typeof parsed.createdAt === 'string' ? parsed.createdAt : '' }
      } catch {
        return { file, createdAt: '' }
      }
    })
    .sort((left, right) => right.createdAt.localeCompare(left.createdAt))
  for (const extra of files.slice(MAX_ENTRIES)) {
    rmSync(extra.file, { force: true })
  }
}

/** 按时间倒序返回摘要；坏文件跳过，不让一条损坏记录拖垮整个列表。 */
export function listAnalysisHistory(dshHome: string): AnalysisHistoryItem[] {
  const dir = historyDir(dshHome)
  if (!existsSync(dir)) return []
  const entries: AnalysisHistoryItem[] = []
  for (const name of readdirSync(dir)) {
    if (!name.endsWith('.json')) continue
    try {
      const parsed = JSON.parse(readFileSync(join(dir, name), 'utf8')) as StoredEntry
      if (typeof parsed.id === 'string' && typeof parsed.createdAt === 'string' && parsed.result !== undefined) {
        entries.push(summary(parsed))
      }
    } catch {
      // 跳过坏文件：历史列表不能因为一条半截 JSON 打不开。
    }
  }
  return entries.sort((left, right) => right.createdAt.localeCompare(left.createdAt))
}

/** 读取一条历史的分析结果；id 只允许生成器写出的字符，挡目录穿越。 */
export function readAnalysisHistory(dshHome: string, id: string): AnalysisResult | null {
  if (!ID_PATTERN.test(id)) return null
  const file = entryFile(dshHome, id)
  if (!existsSync(file)) return null
  try {
    const parsed = JSON.parse(readFileSync(file, 'utf8')) as StoredEntry
    return parsed.result ?? null
  } catch {
    return null
  }
}
