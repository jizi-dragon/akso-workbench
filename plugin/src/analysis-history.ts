/**
 * 对象分析历史：每次分析尝试落一份 JSON（成功与失败都留），页面按时间倒序打开。
 *
 * 只存分析结果本身（status/action/workflow 模型）与失败原因，不存口令、不存令牌；
 * 环境与账号已经在 `result.source`（或失败记录的 `baseUrl/account`）里，打开历史即可知道
 * 是在哪套环境、用哪个账号分析的。
 */
import { randomUUID } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import type { AnalysisResult } from './analysis.ts'
import { workspaceDataPath } from './workspace-data.ts'

const MAX_ENTRIES = 50
const MAX_ERROR = 800
const ID_PATTERN = /^[a-z0-9-]+$/

export type AnalysisHistoryStatus = 'ok' | 'failed'

export interface AnalysisHistoryItem {
  id: string
  createdAt: string
  query: string
  status: AnalysisHistoryStatus
  error?: string
  objectCode: string
  objectName: string
  statuses: number
  enabledStatuses: number
  userActions: number
  enterActions: number
  fields: number
  workflows: number
  baseUrl: string
  account: string
}

interface StoredEntry extends AnalysisHistoryItem {
  result?: AnalysisResult
}

export interface AnalysisHistorySource {
  baseUrl?: string
  account?: string
}

export interface AnalysisHistoryDetail {
  status: AnalysisHistoryStatus
  query: string
  createdAt: string
  error?: string
  result?: AnalysisResult
}

function historyDir(workspaceRoot: string): string {
  return workspaceDataPath(workspaceRoot, 'analysis-history')
}

/** 历史文件所在目录：页面会把它显示出来，方便用户/支持同学直接去翻。 */
export function analysisHistoryDir(workspaceRoot: string): string {
  return historyDir(workspaceRoot)
}

function entryFile(workspaceRoot: string, id: string): string {
  return join(historyDir(workspaceRoot), `${id}.json`)
}

function summary(entry: StoredEntry): AnalysisHistoryItem {
  const item: AnalysisHistoryItem = {
    id: entry.id,
    createdAt: entry.createdAt,
    query: entry.query,
    status: entry.status === 'failed' ? 'failed' : 'ok',
    objectCode: entry.objectCode,
    objectName: entry.objectName,
    statuses: entry.statuses,
    enabledStatuses: entry.enabledStatuses,
    userActions: entry.userActions,
    enterActions: entry.enterActions,
    fields: entry.fields,
    workflows: entry.workflows,
    baseUrl: entry.baseUrl,
    account: entry.account,
  }
  if (typeof entry.error === 'string' && entry.error !== '') item.error = entry.error
  return item
}

/** 保存一次成功分析；返回页面刷新列表需要的摘要字段。 */
export function saveAnalysisHistory(workspaceRoot: string | undefined, query: string, result: AnalysisResult): AnalysisHistoryItem {
  const item: AnalysisHistoryItem = {
    id: newId(),
    createdAt: new Date().toISOString(),
    query,
    status: 'ok',
    objectCode: result.object.code,
    objectName: result.object.name,
    statuses: result.totals.statuses,
    enabledStatuses: result.totals.enabledStatuses,
    userActions: result.totals.userActions,
    enterActions: result.totals.enterActions,
    fields: result.totals.fields,
    workflows: result.workflows?.length ?? 0,
    baseUrl: result.source.baseUrl,
    account: result.source.account,
  }
  const stored: StoredEntry = { ...item, result }
  if (workspaceRoot !== undefined && workspaceRoot.trim() !== '') writeEntry(workspaceRoot, stored)
  return item
}

/**
 * 保存一次失败的分析尝试。
 *
 * ★ 为什么失败也要留：用户/agent 调了分析却什么都没看到，是最难排查的「空结果」；
 *   把错误、查询词和环境一起落盘，历史列表里能直接看到「这次为什么没出来」。
 */
export function saveFailedAnalysisHistory(
  workspaceRoot: string,
  query: string,
  error: string,
  source: AnalysisHistorySource = {},
): AnalysisHistoryItem {
  const message = error.trim() === '' ? '分析失败（没有返回原因）' : error.trim()
  const item: AnalysisHistoryItem = {
    id: newId(),
    createdAt: new Date().toISOString(),
    query,
    status: 'failed',
    error: message.length > MAX_ERROR ? `${message.slice(0, MAX_ERROR - 1)}…` : message,
    objectCode: '',
    objectName: '',
    statuses: 0,
    enabledStatuses: 0,
    userActions: 0,
    enterActions: 0,
    fields: 0,
    workflows: 0,
    baseUrl: source.baseUrl ?? '',
    account: source.account ?? '',
  }
  writeEntry(workspaceRoot, item)
  return item
}

function newId(): string {
  return `${Date.now().toString(36)}-${randomUUID().slice(0, 8)}`
}

function writeEntry(workspaceRoot: string, entry: StoredEntry): void {
  const dir = historyDir(workspaceRoot)
  mkdirSync(dir, { recursive: true, mode: 0o700 })
  const file = entryFile(workspaceRoot, entry.id)
  const tmp = `${file}.tmp`
  writeFileSync(tmp, `${JSON.stringify(entry)}\n`, { encoding: 'utf8', mode: 0o600 })
  renameSync(tmp, file)
  prune(workspaceRoot)
}

function prune(workspaceRoot: string): void {
  const dir = historyDir(workspaceRoot)
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
export function listAnalysisHistory(workspaceRoot: string): AnalysisHistoryItem[] {
  const dir = historyDir(workspaceRoot)
  if (!existsSync(dir)) return []
  const entries: AnalysisHistoryItem[] = []
  for (const name of readdirSync(dir)) {
    if (!name.endsWith('.json')) continue
    try {
      const parsed = JSON.parse(readFileSync(join(dir, name), 'utf8')) as StoredEntry
      if (typeof parsed.id !== 'string' || typeof parsed.createdAt !== 'string') continue
      // 兼容旧记录：没有 status 但有 result 的按成功算，两者都没有的跳过。
      const legacyOk = parsed.status === undefined && parsed.result !== undefined
      if (parsed.status !== 'ok' && parsed.status !== 'failed' && !legacyOk) continue
      entries.push(summary(parsed))
    } catch {
      // 跳过坏文件：历史列表不能因为一条半截 JSON 打不开。
    }
  }
  return entries.sort((left, right) => right.createdAt.localeCompare(left.createdAt))
}

/** 读取一条历史的分析结果或失败原因；id 只允许生成器写出的字符，挡目录穿越。 */
export function readAnalysisHistory(workspaceRoot: string, id: string): AnalysisHistoryDetail | null {
  if (!ID_PATTERN.test(id)) return null
  const file = entryFile(workspaceRoot, id)
  if (!existsSync(file)) return null
  try {
    const parsed = JSON.parse(readFileSync(file, 'utf8')) as StoredEntry
    if (parsed.status === 'failed') {
      return {
        status: 'failed',
        query: typeof parsed.query === 'string' ? parsed.query : '',
        createdAt: typeof parsed.createdAt === 'string' ? parsed.createdAt : '',
        error: typeof parsed.error === 'string' ? parsed.error : '分析失败',
      }
    }
    return {
      status: 'ok',
      query: typeof parsed.query === 'string' ? parsed.query : '',
      createdAt: typeof parsed.createdAt === 'string' ? parsed.createdAt : '',
      result: parsed.result,
    }
  } catch {
    return null
  }
}
