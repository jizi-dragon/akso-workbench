/**
 * 本机运行日志（JSONL）—— 给「用户不在现场时」留证据。
 *
 * 为什么需要它：host 的 `ctx.logger` 只写到 DSH 宿主控制台，用户看不到、也带不回来；
 * 出问题时我们只能靠用户口述「点了哪个按钮、弹了什么错」。这里把**使用轨迹**落到
 * `$DSH_HOME/akso-workbench/journal/`，页面上一键下载，用户把文件发过来即可复盘。
 *
 * 三类写入（与 AGENTS.md「日志与审计」一致，但这是**诊断日志**，不是审计）：
 *   · `tool.*`   —— agent 每次调工具（invoke / done / fail），带 UID 与耗时；
 *   · `http`     —— 本机 API 每条请求（含被围栏挡掉的 403）；
 *   · 业务事件   —— `client` / `cloud` / `egmp` / `approval` / `plan` / `apply` / `analysis` / `error`。
 *
 * 三条硬规矩：
 *   1. **不记口令、令牌、请求体原文**：`redactValue` 会按 key 与 JWT/Bearer 形态兜底；
 *   2. **写入失败不影响主流程**：`record()` 自己吞掉异常，绝不因为磁盘满把工具带崩；
 *   3. **有上限**：一天一个文件（超过 4 MB 换序号），最多留 30 天、总量 96 MB，
 *      单次下载最多 12 MB（超了从最旧的丢，并在 meta 行标 `truncated`）。
 */

import { appendFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync } from 'node:fs'
import { join } from 'node:path'

export const JOURNAL_RETENTION_DAYS = 30
export const JOURNAL_MAX_FILE_BYTES = 4 * 1024 * 1024
export const JOURNAL_MAX_FILES_PER_DAY = 5
export const JOURNAL_MAX_TOTAL_BYTES = 96 * 1024 * 1024
export const JOURNAL_MAX_EXPORT_BYTES = 12 * 1024 * 1024
export const JOURNAL_MAX_STRING = 600
const JOURNAL_MAX_LINE_BYTES = 16 * 1024

const DAY_FILE = /^(\d{4}-\d{2}-\d{2})(?:\.(\d+))?\.jsonl$/
/** key 命中即打码（布尔/数字保留：`hasPassword: true` 本身是诊断信息，不是秘密）。 */
const SECRET_KEY = /pass(word|wd)?$|pwd|secret|token|authorization|cookie|credential|api[-_]?key/i

export interface JournalRecord {
  at: string
  ts: number
  kind: string
  [key: string]: unknown
}

export interface JournalFileInfo {
  name: string
  day: string
  seq: number
  bytes: number
  events: number
}

export interface JournalSummary {
  dir: string
  retentionDays: number
  events: number
  bytes: number
  files: JournalFileInfo[]
  oldest: string | null
  newest: string | null
}

export interface JournalExport {
  fileName: string
  text: string
  events: number
  bytes: number
  dropped: number
  truncated: boolean
}

/* ────────────────────────────── 打码 ────────────────────────────── */

export function clip(text: string, max = JOURNAL_MAX_STRING): string {
  return text.length <= max ? text : `${text.slice(0, max)}…`
}

/** 把字符串里的 JWT / Bearer 换成占位符（key 打码漏了的兜底）。 */
export function scrubText(text: string): string {
  return text
    .replace(/\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\b/g, '[redacted-jwt]')
    .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]{8,}/gi, 'Bearer [redacted]')
}

/**
 * 把一个值变成可写日志的安全副本：
 *   · 命中了敏感 key 的字符串 → `[redacted]`；
 *   · 字符串里出现 JWT / Bearer → 替换；
 *   · 深度 5、每层 40 个键、数组 30 项、字符串 600 字，超出即截断。
 */
export function redactValue(value: unknown, depth = 0): unknown {
  if (value === null || typeof value === 'boolean' || typeof value === 'number') return value
  if (typeof value === 'string') return clip(scrubText(value))
  if (typeof value === 'undefined') return null
  if (depth >= 5) return '[deep]'
  if (value instanceof Error) return { name: value.name, message: clip(scrubText(value.message)) }
  if (Array.isArray(value)) return value.slice(0, 30).map((item) => redactValue(item, depth + 1))
  if (typeof value === 'object') {
    const out: Record<string, unknown> = {}
    const entries = Object.entries(value as Record<string, unknown>).slice(0, 40)
    for (const [key, item] of entries) {
      if (SECRET_KEY.test(key) && (typeof item === 'string' || (item !== null && typeof item === 'object'))) {
        out[key] = '[redacted]'
        continue
      }
      out[key] = redactValue(item, depth + 1)
    }
    return out
  }
  return clip(scrubText(String(value)))
}

export function errorText(error: unknown): string {
  if (error instanceof Error) return clip(scrubText(error.message), 400)
  return clip(scrubText(String(error)), 400)
}

/** `qa@example.com` → `q***@example.com`（保留域名，便于判断是不是同一环境）。 */
export function maskEmail(email: string): string {
  const at = email.lastIndexOf('@')
  if (at <= 0) return email === '' ? '' : `${email.slice(0, 1)}***`
  return `${email.slice(0, 1)}***${email.slice(at)}`
}

/* ────────────────────────────── 文件布局 ────────────────────────────── */

export function journalDir(dshHome: string): string {
  return join(dshHome, 'akso-workbench', 'journal')
}

function pad2(value: number): string {
  return String(value).padStart(2, '0')
}

/** 本地时区的 `YYYY-MM-DD`（不按 UTC 切，否者中国用户 08:00 前的日志会算到前一天）。 */
function dayKey(now: number): string {
  const date = new Date(now)
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`
}

function startOfDay(now: number): number {
  const date = new Date(now)
  date.setHours(0, 0, 0, 0)
  return date.getTime()
}

/** 只接受插件的文件命名，防止 readdir 里的杂物被读进日志。 */
function fileParts(name: string): { day: string; seq: number } | null {
  const matched = DAY_FILE.exec(name)
  if (matched === null) return null
  const day = matched[1]
  if (day === undefined) return null
  const seq = Number(matched[2] ?? '1')
  return { day, seq: Number.isFinite(seq) && seq >= 1 ? seq : 1 }
}

function fileName(day: string, seq: number): string {
  return seq <= 1 ? `${day}.jsonl` : `${day}.${seq}.jsonl`
}

function listJournalFiles(dir: string): Array<{ name: string; day: string; seq: number; bytes: number }> {
  if (!existsSync(dir)) return []
  const files: Array<{ name: string; day: string; seq: number; bytes: number }> = []
  for (const name of readdirSync(dir)) {
    const parts = fileParts(name)
    if (parts === null) continue
    let bytes = 0
    try {
      bytes = statSync(join(dir, name)).size
    } catch {
      continue
    }
    files.push({ name, day: parts.day, seq: parts.seq, bytes })
  }
  return files.sort((a, b) => (a.day === b.day ? a.seq - b.seq : a.day < b.day ? -1 : 1))
}

/** 每天只清理一次（写日志在热路径上，不能每次都 readdir + stat 全目录）。 */
const lastPruneAt = new Map<string, number>()
const PRUNE_INTERVAL_MS = 60 * 60 * 1000

function prune(dir: string, now: number): void {
  const previous = lastPruneAt.get(dir) ?? 0
  if (now - previous < PRUNE_INTERVAL_MS) return
  lastPruneAt.set(dir, now)
  const cutoffDay = dayKey(startOfDay(now) - (JOURNAL_RETENTION_DAYS - 1) * 86_400_000)
  let files = listJournalFiles(dir)
  for (const file of files) {
    if (file.day < cutoffDay) {
      try {
        rmSync(join(dir, file.name), { force: true })
      } catch {
        // 删不掉就留着，下次再试
      }
    }
  }
  files = listJournalFiles(dir)
  let total = files.reduce((sum, file) => sum + file.bytes, 0)
  for (const file of files) {
    if (total <= JOURNAL_MAX_TOTAL_BYTES) break
    try {
      rmSync(join(dir, file.name), { force: true })
      total -= file.bytes
    } catch {
      // 同上
    }
  }
}

/** 今天的文件写满了就换 `.2` `.3` …；一个都换不到时仍写最后一个（不丢最新记录）。 */
function pickFile(dir: string, now: number): string {
  const day = dayKey(now)
  let last = fileName(day, 1)
  for (let seq = 1; seq <= JOURNAL_MAX_FILES_PER_DAY; seq += 1) {
    const name = fileName(day, seq)
    last = name
    const file = join(dir, name)
    let bytes = 0
    try {
      bytes = existsSync(file) ? statSync(file).size : 0
    } catch {
      bytes = 0
    }
    if (bytes < JOURNAL_MAX_FILE_BYTES) return file
  }
  return join(dir, last)
}

/* ────────────────────────────── 写 ────────────────────────────── */

export interface JournalHandle {
  dir: string
  /** 记一条；`data` 会被打码后再展开到记录顶层。 */
  record(kind: string, data?: Record<string, unknown>): void
  summary(days?: number): JournalSummary
  export(days: number, meta?: Record<string, unknown>): JournalExport
}

export function createJournal(dshHome: string, options: { now?: () => number } = {}): JournalHandle {
  const dir = journalDir(dshHome)
  const now = options.now ?? (() => Date.now())
  const instance = Math.random().toString(36).slice(2, 8)

  function record(kind: string, data: Record<string, unknown> = {}): void {
    try {
      const ts = now()
      const safe = redactValue(data)
      // 固定字段放最后：调用方 data 里的同名键（尤其 kind）不能覆盖记录本身
      let line = JSON.stringify({
        ...(safe !== null && typeof safe === 'object' && !Array.isArray(safe) ? (safe as Record<string, unknown>) : { data: safe }),
        at: new Date(ts).toISOString(),
        ts,
        kind,
        instance,
      })
      if (Buffer.byteLength(line, 'utf8') > JOURNAL_MAX_LINE_BYTES) {
        line = JSON.stringify({ at: new Date(ts).toISOString(), ts, kind, instance, truncated: true, bytes: Buffer.byteLength(line, 'utf8') })
      }
      mkdirSync(dir, { recursive: true, mode: 0o700 })
      prune(dir, ts)
      appendFileSync(pickFile(dir, ts), `${line}\n`, 'utf8')
    } catch {
      // 日志写不进去不能影响工具/页面：这里静默（宿主 logger 已经拿不到也不能再抛）
    }
  }

  return {
    dir,
    record,
    summary(days = JOURNAL_RETENTION_DAYS) {
      return journalSummary(dir, days, now())
    },
    export(days, meta = {}) {
      return exportJournal(dir, days, now(), meta)
    },
  }
}

/* ────────────────────────────── 读 / 导出 ────────────────────────────── */

function parseLine(line: string): JournalRecord | null {
  if (line === '') return null
  try {
    const parsed = JSON.parse(line) as unknown
    if (parsed === null || typeof parsed !== 'object') return null
    const record = parsed as Partial<JournalRecord>
    if (typeof record.kind !== 'string' || typeof record.ts !== 'number' || typeof record.at !== 'string') return null
    return record as JournalRecord
  } catch {
    return null
  }
}

/** 读取最近 `days` 天（含今天）的记录，按时间升序；坏行跳过，不整份失败。 */
export function readJournalRecords(dir: string, days: number, now = Date.now()): JournalRecord[] {
  const span = Math.max(1, Math.min(JOURNAL_RETENTION_DAYS, Math.floor(days)))
  const cutoffDay = dayKey(startOfDay(now) - (span - 1) * 86_400_000)
  const records: JournalRecord[] = []
  for (const file of listJournalFiles(dir)) {
    if (file.day < cutoffDay) continue
    let raw = ''
    try {
      raw = readFileSync(join(dir, file.name), 'utf8')
    } catch {
      continue
    }
    for (const line of raw.split('\n')) {
      const record = parseLine(line.trim())
      if (record !== null) records.push(record)
    }
  }
  return records.sort((a, b) => a.ts - b.ts)
}

/**
 * 页面上的概览：保留期内的事件数、体积、时间范围。
 * 事件数按行解析（30 天日志也就几 MB，不值得再维护索引文件）。
 */
export function journalSummary(dir: string, days = JOURNAL_RETENTION_DAYS, now = Date.now()): JournalSummary {
  const span = Math.max(1, Math.min(JOURNAL_RETENTION_DAYS, Math.floor(days)))
  const cutoffDay = dayKey(startOfDay(now) - (span - 1) * 86_400_000)
  const files: JournalFileInfo[] = []
  let events = 0
  let bytes = 0
  let oldest: string | null = null
  let newest: string | null = null
  for (const file of listJournalFiles(dir)) {
    if (file.day < cutoffDay) continue
    let count = 0
    let first: string | null = null
    let last: string | null = null
    try {
      const raw = readFileSync(join(dir, file.name), 'utf8')
      for (const line of raw.split('\n')) {
        const record = parseLine(line.trim())
        if (record === null) continue
        count += 1
        if (first === null || record.at < first) first = record.at
        if (last === null || record.at > last) last = record.at
      }
    } catch {
      continue
    }
    files.push({ name: file.name, day: file.day, seq: file.seq, bytes: file.bytes, events: count })
    events += count
    bytes += file.bytes
    if (first !== null && (oldest === null || first < oldest)) oldest = first
    if (last !== null && (newest === null || last > newest)) newest = last
  }
  return { dir, retentionDays: span, events, bytes, files, oldest, newest }
}

function exportStamp(now: number): string {
  const date = new Date(now)
  return `${date.getFullYear()}${pad2(date.getMonth() + 1)}${pad2(date.getDate())}-${pad2(date.getHours())}${pad2(date.getMinutes())}`
}

/**
 * 生成下载文件：第一行是 `kind: "journal.meta"` 的环境信息，后面是原始记录。
 * 超过 12 MB 时从最旧的记录开始丢，并在 meta 里标 `truncated/dropped`（最新的一定保住）。
 */
export function exportJournal(
  dir: string,
  days: number,
  now = Date.now(),
  meta: Record<string, unknown> = {},
): JournalExport {
  const span = Math.max(1, Math.min(JOURNAL_RETENTION_DAYS, Math.floor(days)))
  const records = readJournalRecords(dir, span, now)
  const summary = journalSummary(dir, span, now)
  const header = {
    at: new Date(now).toISOString(),
    ts: now,
    kind: 'journal.meta',
    format: 'akso-workbench-journal',
    version: 1,
    days: span,
    events: records.length,
    files: summary.files.map((file) => file.name),
    retentionDays: JOURNAL_RETENTION_DAYS,
    ...redactValue(meta) as Record<string, unknown>,
  }
  const lines: string[] = [JSON.stringify(header)]
  let bytes = Buffer.byteLength(lines[0] ?? '', 'utf8') + 1
  let dropped = 0
  const kept: string[] = []
  for (let index = records.length - 1; index >= 0; index -= 1) {
    const record = records[index]
    if (record === undefined) continue
    const line = JSON.stringify(record)
    const size = Buffer.byteLength(line, 'utf8') + 1
    if (bytes + size > JOURNAL_MAX_EXPORT_BYTES) {
      dropped = index + 1
      break
    }
    kept.push(line)
    bytes += size
  }
  kept.reverse()
  lines.push(...kept)
  const truncated = dropped > 0
  if (truncated) {
    // 把标记写进 meta 行：解析方不用猜为什么第一条事件比天数范围新
    const marked = { ...header, truncated: true, dropped }
    lines[0] = JSON.stringify(marked)
  }
  const text = `${lines.join('\n')}\n`
  return {
    fileName: `akso-workbench-journal-${exportStamp(now)}-${span}d.jsonl`,
    text,
    events: records.length,
    bytes: Buffer.byteLength(text, 'utf8'),
    dropped,
    truncated,
  }
}
