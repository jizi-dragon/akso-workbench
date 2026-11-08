/**
 * 捕获数据的落盘层：一个会话一个 JSONL，追加写。
 *
 * 位置：`$DSH_HOME/akso-workbench/captures/`（可用 AKW_CAPTURE_DIR 覆盖）。
 * 权限：目录 0700、文件 0600（Windows 上 chmod 语义有限，但至少不落到共享位置）。
 * 数据**不进仓库**：DSH_HOME 在仓库外，.gitignore 也不含这个目录。
 */

import { appendFileSync, existsSync, mkdirSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { randomBytes } from 'node:crypto'
import type { CaptureRecord } from './records.ts'

const FILE_RE = /^capture-cap_[0-9]{14}_[a-z0-9]{4}\.jsonl$/

export interface CaptureSessionInfo {
  sessionId: string
  file: string
  size: number
  mtime: number
  records: number
}

function dshHome(): string {
  const fromEnv = process.env.DSH_HOME
  return fromEnv !== undefined && fromEnv !== '' ? fromEnv : join(homedir(), '.dsh')
}

export function capturesRoot(): string {
  const override = process.env.AKW_CAPTURE_DIR
  if (override !== undefined && override !== '') return override
  return join(dshHome(), 'akso-workbench', 'captures')
}

function stamp(date: Date): string {
  const pad = (value: number, width = 2): string => String(value).padStart(width, '0')
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`
}

function newSessionId(date: Date): string {
  return `cap_${stamp(date).replace('-', '')}_${randomBytes(2).toString('hex')}`
}

export class CaptureStore {
  readonly root: string
  readonly sessionId: string
  readonly file: string
  readonly startedAt: number
  private count = 0
  private ready = false

  constructor(root = capturesRoot(), sessionId?: string) {
    const now = new Date()
    this.root = root
    this.sessionId = sessionId ?? newSessionId(now)
    this.startedAt = Date.now()
    this.file = join(root, `capture-${this.sessionId}.jsonl`)
  }

  private ensure(): void {
    if (this.ready) return
    mkdirSync(this.root, { recursive: true, mode: 0o700 })
    this.ready = true
  }

  append(record: CaptureRecord): void {
    this.ensure()
    appendFileSync(this.file, `${JSON.stringify(record)}\n`, { encoding: 'utf8', mode: 0o600 })
    this.count += 1
  }

  get size(): number {
    return this.count
  }

  static list(root = capturesRoot()): CaptureSessionInfo[] {
    if (!existsSync(root)) return []
    const rows: CaptureSessionInfo[] = []
    for (const name of readdirSync(root)) {
      if (!FILE_RE.test(name)) continue
      const file = join(root, name)
      const stat = statSync(file)
      rows.push({
        sessionId: name.slice('capture-'.length, -'.jsonl'.length),
        file,
        size: stat.size,
        mtime: stat.mtimeMs,
        records: countLines(file),
      })
    }
    return rows.sort((left, right) => right.mtime - left.mtime)
  }

  static latest(root = capturesRoot()): string | null {
    return CaptureStore.list(root)[0]?.file ?? null
  }

  /** `--session latest|<文件名>|<路径>|<sessionId 前缀>` */
  static resolve(selector: string | undefined, root = capturesRoot()): string | null {
    if (selector === undefined || selector === '' || selector === 'latest') return CaptureStore.latest(root)
    if (existsSync(selector)) return selector
    for (const item of CaptureStore.list(root)) {
      if (item.sessionId === selector || item.sessionId.endsWith(selector) || item.file.endsWith(selector)) return item.file
    }
    const candidate = join(root, selector)
    return existsSync(candidate) ? candidate : null
  }

  static read(file: string): CaptureRecord[] {
    const text = readFileSync(file, 'utf8')
    const rows: CaptureRecord[] = []
    for (const line of text.split('\n')) {
      if (line.trim() === '') continue
      try {
        rows.push(JSON.parse(line) as CaptureRecord)
      } catch {
        // 半行/坏行跳过：采集中断时最后一行可能不完整
      }
    }
    return rows
  }
}

function countLines(file: string): number {
  try {
    return readFileSync(file, 'utf8').split('\n').filter((line) => line.trim() !== '').length
  } catch {
    return 0
  }
}
