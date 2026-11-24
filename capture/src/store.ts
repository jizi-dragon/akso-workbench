/**
 * 捕获数据的落盘层：一个会话一个 JSONL，追加写。
 *
 * 位置：`<最近工作区>/akso-workbench-data/captures/`；找不到工作区直接报错，
 * 不再回落到 `$DSH_HOME`（可用 AKW_CAPTURE_DIR 显式覆盖）。
 * 权限：目录 0700、文件 0600（Windows 上 chmod 语义有限，但至少不落到共享位置）。
 * 数据**不进仓库**：工作区数据目录在仓库外；捕获文件不要提交。
 */

import { appendFileSync, existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { randomBytes } from 'node:crypto'
import type { CaptureRecord } from './records.ts'

const FILE_RE = /^capture-cap_[0-9]{14}_[a-z0-9]{4}\.jsonl$/

export interface CaptureSessionMeta {
  v: 1
  sessionId: string
  startedAt: number
  stoppedAt?: number
  tab?: { id?: string; url?: string; title?: string }
  hosts?: string[] | null
  extension?: string | null
}

export interface CaptureSessionInfo {
  sessionId: string
  file: string
  size: number
  mtime: number
  startedAt: number
  stoppedAt?: number
  tab?: CaptureSessionMeta['tab']
  hosts?: CaptureSessionMeta['hosts']
  extension?: CaptureSessionMeta['extension']
  records: number
}

/** 从当前目录向上找最近一个带 `akso-workbench-data` 的工作区根。 */
function nearestWorkspaceRoot(): string | null {
  let dir = resolve(process.cwd())
  for (;;) {
    if (existsSync(join(dir, 'akso-workbench-data'))) return dir
    const parent = dirname(dir)
    if (parent === dir) return null
    dir = parent
  }
}

export function capturesRoot(): string {
  const override = process.env.AKW_CAPTURE_DIR
  if (override !== undefined && override !== '') return override
  const workspace = nearestWorkspaceRoot()
  if (workspace !== null) return join(workspace, 'akso-workbench-data', 'captures')
  throw new Error(
    '捕获数据必须写在工作区的 akso-workbench-data/captures：当前目录向上找不到 akso-workbench-data；' +
      '请从工作区运行，或显式设置 AKW_CAPTURE_DIR',
  )
}

function stamp(date: Date): string {
  const pad = (value: number, width = 2): string => String(value).padStart(width, '0')
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`
}

function newSessionId(date: Date): string {
  return `cap_${stamp(date).replace('-', '')}_${randomBytes(2).toString('hex')}`
}

function metaFileOf(file: string): string {
  return file.endsWith('.jsonl') ? `${file.slice(0, -'.jsonl'.length)}.meta.json` : `${file}.meta.json`
}

function readMeta(file: string): CaptureSessionMeta | null {
  const metaFile = metaFileOf(file)
  if (!existsSync(metaFile)) return null
  try {
    const parsed = JSON.parse(readFileSync(metaFile, 'utf8')) as Partial<CaptureSessionMeta>
    if (parsed.v !== 1 || typeof parsed.sessionId !== 'string' || typeof parsed.startedAt !== 'number') return null
    return parsed as CaptureSessionMeta
  } catch {
    return null
  }
}

/** 捕获会话的实际开始时间（没有 sidecar 时退回文件创建时间）。 */
export function sessionStartedAt(file: string): number {
  const meta = readMeta(file)
  if (meta !== null) return meta.startedAt
  try {
    const stat = statSync(file)
    return stat.birthtimeMs > 0 ? stat.birthtimeMs : stat.mtimeMs
  } catch {
    return Date.now()
  }
}

export function sessionAgeMs(file: string, now = Date.now()): number {
  return Math.max(0, now - sessionStartedAt(file))
}

export class CaptureStore {
  readonly root: string
  readonly sessionId: string
  readonly file: string
  readonly metaFile: string
  readonly startedAt: number
  private count = 0
  private ready = false

  constructor(root = capturesRoot(), sessionId?: string) {
    const now = new Date()
    this.root = root
    this.sessionId = sessionId ?? newSessionId(now)
    this.startedAt = Date.now()
    this.file = join(root, `capture-${this.sessionId}.jsonl`)
    this.metaFile = metaFileOf(this.file)
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

  /** 写会话 sidecar：记录开始时间、标签页与 host 过滤范围，读取时用于判断新鲜度。 */
  writeMeta(patch: Omit<CaptureSessionMeta, 'v' | 'sessionId' | 'startedAt'> & { startedAt?: number } = {}): void {
    this.ensure()
    const previous = readMeta(this.file)
    const stoppedAt = patch.stoppedAt ?? previous?.stoppedAt
    const tab = patch.tab ?? previous?.tab
    const hosts = patch.hosts !== undefined ? patch.hosts : previous?.hosts
    const extension = patch.extension !== undefined ? patch.extension : previous?.extension
    const meta: CaptureSessionMeta = {
      v: 1,
      sessionId: this.sessionId,
      startedAt: patch.startedAt ?? previous?.startedAt ?? this.startedAt,
      ...(stoppedAt === undefined ? {} : { stoppedAt }),
      ...(tab === undefined ? {} : { tab }),
      ...(hosts === undefined ? {} : { hosts }),
      ...(extension === undefined ? {} : { extension }),
    }
    writeFileSync(this.metaFile, `${JSON.stringify(meta, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 })
  }

  finish(): void {
    this.writeMeta({ stoppedAt: Date.now() })
  }

  get size(): number {
    return this.count
  }

  static info(file: string): CaptureSessionInfo | null {
    try {
      const stat = statSync(file)
      const meta = readMeta(file)
      return {
        sessionId: meta?.sessionId ?? file.replace(/^.*capture-/, '').replace(/\.jsonl$/, ''),
        file,
        size: stat.size,
        mtime: stat.mtimeMs,
        startedAt: meta?.startedAt ?? (stat.birthtimeMs > 0 ? stat.birthtimeMs : stat.mtimeMs),
        ...(meta?.stoppedAt === undefined ? {} : { stoppedAt: meta.stoppedAt }),
        ...(meta?.tab === undefined ? {} : { tab: meta.tab }),
        ...(meta?.hosts === undefined ? {} : { hosts: meta.hosts }),
        ...(meta?.extension === undefined ? {} : { extension: meta.extension }),
        records: countLines(file),
      }
    } catch {
      return null
    }
  }

  static list(root = capturesRoot()): CaptureSessionInfo[] {
    if (!existsSync(root)) return []
    const rows: CaptureSessionInfo[] = []
    for (const name of readdirSync(root)) {
      if (!FILE_RE.test(name)) continue
      const info = CaptureStore.info(join(root, name))
      if (info !== null) rows.push(info)
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
