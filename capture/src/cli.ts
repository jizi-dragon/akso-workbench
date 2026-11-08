/**
 * akw-capture 命令行。
 *
 *   node capture/bin/akw-capture.mjs tabs
 *   node capture/bin/akw-capture.mjs launch --url https://standard-val.aksoegmp.com
 *   node capture/bin/akw-capture.mjs attach --tab aksoegmp --host aksoegmp.com
 *   node capture/bin/akw-capture.mjs list --session latest --method POST
 *   node capture/bin/akw-capture.mjs endpoints
 *   node capture/bin/akw-capture.mjs promote 3
 *
 * 只做三件事：抓（attach）、查（list/endpoints/show/export）、提草稿（promote）。
 * 不做回放、不自动改 capabilities.ts。
 */

import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { CaptureStore, capturesRoot } from './store.ts'
import { CdpClient } from './cdp.ts'
import { Recorder } from './recorder.ts'
import { filterRecords, draftFrom, formatDraft, summarize } from './query.ts'
import type { CaptureRecord } from './records.ts'
import { listTargets, pickTarget, versionInfo } from './targets.ts'

interface ParsedArgs {
  command: string
  positional: string[]
  flags: Map<string, string | boolean>
}

function parseArgs(argv: string[]): ParsedArgs {
  const positional: string[] = []
  const flags = new Map<string, string | boolean>()
  let command = 'help'
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index]
    if (token === undefined) break
    if (token.startsWith('--')) {
      const body = token.slice(2)
      const eq = body.indexOf('=')
      if (eq >= 0) {
        flags.set(body.slice(0, eq), body.slice(eq + 1))
      } else {
        const next = argv[index + 1]
        if (next !== undefined && !next.startsWith('--')) {
          flags.set(body, next)
          index += 1
        } else {
          flags.set(body, true)
        }
      }
      continue
    }
    if (command === 'help' && positional.length === 0 && !token.startsWith('-')) {
      command = token
      continue
    }
    positional.push(token)
  }
  return { command, positional, flags }
}

function str(flags: ParsedArgs['flags'], name: string): string | undefined {
  const value = flags.get(name)
  return typeof value === 'string' ? value : undefined
}

function num(flags: ParsedArgs['flags'], name: string, fallback: number): number {
  const value = str(flags, name)
  if (value === undefined) return fallback
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function bool(flags: ParsedArgs['flags'], name: string): boolean {
  return flags.get(name) === true || flags.get(name) === 'true' || flags.get(name) === '1'
}

function pad(text: string, width: number): string {
  return text.length >= width ? text : text + ' '.repeat(width - text.length)
}

function printTable(headers: string[], rows: string[][]): void {
  const widths = headers.map((header, column) => Math.max(header.length, ...rows.map((row) => (row[column] ?? '').length)))
  console.log(headers.map((header, column) => pad(header, widths[column] ?? header.length)).join('  '))
  console.log(widths.map((width) => '-'.repeat(width)).join('  '))
  for (const row of rows) console.log(row.map((cell, column) => pad(cell, widths[column] ?? cell.length)).join('  '))
}

function clock(at: number): string {
  const date = new Date(at)
  const two = (value: number): string => String(value).padStart(2, '0')
  return `${two(date.getHours())}:${two(date.getMinutes())}:${two(date.getSeconds())}`
}

function sizeOf(record: CaptureRecord): string {
  const bytes = record.responseBody?.bytes
  if (bytes === undefined) return '—'
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}K`
  return `${(bytes / 1024 / 1024).toFixed(1)}M`
}

function chromeCandidates(): string[] {
  if (process.platform === 'win32') {
    const programFiles = process.env['PROGRAMFILES'] ?? 'C:\\Program Files'
    const programFilesX86 = process.env['PROGRAMFILES(X86)'] ?? 'C:\\Program Files (x86)'
    const localAppData = process.env['LOCALAPPDATA'] ?? ''
    return [
      join(programFiles, 'Google\\Chrome\\Application\\chrome.exe'),
      join(programFilesX86, 'Google\\Chrome\\Application\\chrome.exe'),
      join(localAppData, 'Google\\Chrome\\Application\\chrome.exe'),
      join(programFiles, 'Microsoft\\Edge\\Application\\msedge.exe'),
      join(programFilesX86, 'Microsoft\\Edge\\Application\\msedge.exe'),
    ]
  }
  if (process.platform === 'darwin') {
    return [
      '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
      '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    ]
  }
  return ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/microsoft-edge']
}

function findBrowser(explicit?: string): string {
  if (explicit !== undefined) {
    if (!existsSync(explicit)) throw new Error(`找不到浏览器：${explicit}`)
    return explicit
  }
  const found = chromeCandidates().find((candidate) => existsSync(candidate))
  if (found === undefined) throw new Error('找不到 Chrome/Edge，请用 --chrome 指定可执行文件')
  return found
}

async function commandTabs(args: ParsedArgs): Promise<void> {
  const port = num(args.flags, 'port', 9222)
  const targets = await listTargets({ port })
  if (bool(args.flags, 'json')) {
    console.log(JSON.stringify(targets, null, 2))
    return
  }
  if (targets.length === 0) {
    console.log(`127.0.0.1:${port} 上没有可录的标签页。先运行 launch，或给现有浏览器开调试端口。`)
    return
  }
  printTable(['#', 'title', 'url'], targets.map((target, index) => [String(index + 1), target.title.slice(0, 40), target.url.slice(0, 90)]))
}

async function commandLaunch(args: ParsedArgs): Promise<void> {
  const port = num(args.flags, 'port', 9222)
  const url = str(args.flags, 'url') ?? 'https://www.aksoegmp.com'
  const browser = findBrowser(str(args.flags, 'chrome'))
  const root = capturesRoot()
  const profile = str(args.flags, 'profile') ?? join(root, 'chrome-profile')
  mkdirSync(profile, { recursive: true, mode: 0o700 })
  const child = spawn(browser, [
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    '--no-first-run',
    '--no-default-browser-check',
    url,
  ], { detached: true, stdio: 'ignore' })
  child.unref()
  console.log(`已启动：${browser}`)
  console.log(`调试端口：http://127.0.0.1:${port}   profile：${profile}`)
  const deadline = Date.now() + 20_000
  while (Date.now() < deadline) {
    try {
      const info = await versionInfo({ port })
      console.log(`已就绪：${info.Browser ?? '浏览器'}（协议 ${info['Protocol-Version'] ?? '?'}）`)
      console.log('这是一个独立的浏览器 profile，需要在这里重新登录一次；登录后运行 attach 开始监听。')
      return
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 500))
    }
  }
  console.log('等了 20 秒调试端口还没就绪；浏览器可能弹了「谁在使用」提示，或端口被占用。')
}

function waitForStop(durationMs: number): Promise<void> {
  return new Promise((resolve) => {
    let timer: ReturnType<typeof setTimeout> | undefined
    const done = (): void => {
      if (timer !== undefined) clearTimeout(timer)
      process.off('SIGINT', done)
      process.off('SIGTERM', done)
      resolve()
    }
    process.on('SIGINT', done)
    process.on('SIGTERM', done)
    if (durationMs > 0) timer = setTimeout(done, durationMs)
  })
}

async function commandAttach(args: ParsedArgs): Promise<void> {
  const port = num(args.flags, 'port', 9222)
  const targets = await listTargets({ port })
  const target = pickTarget(targets, str(args.flags, 'tab'))
  if (target === null) {
    console.log(`没有匹配的标签页（${targets.length} 个可选）。用 tabs 看一眼，或用 launch 起一个带调试端口的浏览器。`)
    process.exitCode = 1
    return
  }
  const client = await CdpClient.connect(target.webSocketDebuggerUrl)
  const store = new CaptureStore()
  const hosts = bool(args.flags, 'all') ? null : (str(args.flags, 'host') ?? 'aksoegmp.com').split(',').map((item) => item.trim()).filter(Boolean)
  const verbose = bool(args.flags, 'verbose')
  const recorder = new Recorder({
    client,
    store,
    hosts,
    allTypes: bool(args.flags, 'all-types'),
    page: { id: target.id, url: target.url, title: target.title },
    onError: (message, error) => console.error(`[warn] ${message}:`, error instanceof Error ? error.message : error),
    onRecord: (record) => {
      if (!verbose) return
      console.log(`${record.method} ${record.status ?? '—'} ${record.pathTemplate} ${record.durationMs ?? '—'}ms`)
    },
  })
  await recorder.start()
  console.log(`监听中：${target.title}`)
  console.log(`标签页：${target.url}`)
  console.log(`捕获文件：${store.file}`)
  console.log(`host 过滤：${hosts === null ? '不限制' : hosts.join(', ')}    按 Ctrl+C 结束`)
  await waitForStop(num(args.flags, 'duration', 0) * 1000)
  recorder.stop()
  client.close()
  console.log(`\n结束：${store.size} 条记录 → ${store.file}`)
}

async function loadSession(args: ParsedArgs): Promise<{ file: string; records: CaptureRecord[] }> {
  const file = CaptureStore.resolve(str(args.flags, 'session'))
  if (file === null) throw new Error(`找不到捕获会话（${capturesRoot()}）；先运行 attach。`)
  return { file, records: CaptureStore.read(file) }
}

async function commandList(args: ParsedArgs): Promise<void> {
  const { records } = await loadSession(args)
  const rows = filterRecords(records, {
    method: str(args.flags, 'method'),
    grep: str(args.flags, 'grep'),
    limit: num(args.flags, 'limit', 40),
    host: str(args.flags, 'host'),
  })
  if (bool(args.flags, 'json')) {
    console.log(JSON.stringify(rows, null, 2))
    return
  }
  printTable(
    ['#', 'time', 'status', 'method', 'path', 'ms', 'size'],
    rows.map((record, index) => [
      String(index + 1),
      clock(record.at),
      record.failed === undefined ? String(record.status ?? '—') : 'FAIL',
      record.method,
      record.pathTemplate,
      record.durationMs === undefined ? '—' : String(record.durationMs),
      sizeOf(record),
    ]),
  )
}

function pickByIndex(rows: CaptureRecord[], token: string | undefined): CaptureRecord {
  if (token === undefined) throw new Error('用法：show|promote <序号|id>')
  const index = Number(token)
  if (Number.isInteger(index) && index > 0) {
    const record = rows[index - 1]
    if (record === undefined) throw new Error(`序号超出范围（共 ${rows.length} 条）`)
    return record
  }
  const record = rows.find((row) => row.id === token || row.id.endsWith(token))
  if (record === undefined) throw new Error(`找不到记录：${token}`)
  return record
}

async function commandShow(args: ParsedArgs): Promise<void> {
  const { records } = await loadSession(args)
  const rows = filterRecords(records, { limit: num(args.flags, 'limit', 500) })
  const record = pickByIndex(rows, args.positional[0])
  console.log(JSON.stringify(record, null, 2))
}

async function commandEndpoints(args: ParsedArgs): Promise<void> {
  const { records } = await loadSession(args)
  const rows = summarize(filterRecords(records, { method: str(args.flags, 'method'), grep: str(args.flags, 'grep') }))
  if (bool(args.flags, 'json')) {
    console.log(JSON.stringify(rows, null, 2))
    return
  }
  printTable(
    ['count', 'method', 'path', 'status', 'last'],
    rows.map((row) => [
      String(row.count),
      row.method,
      row.pathTemplate,
      Object.entries(row.statuses).map(([status, count]) => `${status}×${count}`).join(' '),
      clock(row.lastAt),
    ]),
  )
}

async function commandExport(args: ParsedArgs): Promise<void> {
  const { file, records } = await loadSession(args)
  const rows = summarize(records)
  const lines: string[] = [
    `# 监听到的接口（${CaptureStore.list().find((item) => item.file === file)?.sessionId ?? file}）`,
    '',
    `导出于 ${new Date().toISOString()}，共 ${records.length} 条记录、${rows.length} 个接口。令牌与口令已脱敏。`,
    '',
    '| 次数 | 方法 | 路径模板 | 状态 | 最近 |',
    '| ---: | --- | --- | --- | --- |',
    ...rows.map((row) => `| ${row.count} | ${row.method} | \`${row.pathTemplate}\` | ${Object.entries(row.statuses).map(([status, count]) => `${status}×${count}`).join(' ')} | ${new Date(row.lastAt).toISOString()} |`),
    '',
  ]
  const out = str(args.flags, 'out')
  if (out === undefined) {
    console.log(lines.join('\n'))
    return
  }
  writeFileSync(out, lines.join('\n'), 'utf8')
  console.log(`已写出：${out}`)
}

async function commandPromote(args: ParsedArgs): Promise<void> {
  const { records } = await loadSession(args)
  const rows = filterRecords(records, { limit: num(args.flags, 'limit', 500) })
  const record = pickByIndex(rows, args.positional[0])
  console.log(formatDraft(draftFrom(record, { id: str(args.flags, 'id'), domain: str(args.flags, 'domain') })))
  console.log('\n// 粘进 plugin/src/config/capabilities.ts 之前，先人工确认参数与风险等级。')
}

async function commandSessions(): Promise<void> {
  const rows = CaptureStore.list()
  if (rows.length === 0) {
    console.log(`还没有捕获会话（${capturesRoot()}）。`)
    return
  }
  printTable(
    ['session', 'records', 'size', 'updated', 'file'],
    rows.map((row) => [row.sessionId, String(row.records), `${(row.size / 1024).toFixed(1)}K`, new Date(row.mtime).toLocaleString(), row.file]),
  )
}

function usage(): void {
  console.log(`akw-capture —— 用 CDP 监听浏览器里的 HTTP 调用，给「扩接口」提供证据。

  tabs                          列出可录的标签页
  launch [--url URL] [--port 9222] [--chrome PATH]
                                起一个带调试端口的独立浏览器（profile 在 captures 下）
  attach [--tab 片段] [--host a,b] [--all] [--all-types] [--duration 秒] [--verbose]
                                开始监听；默认只记 aksoegmp.com 的 XHR/Fetch/Document
  list [--session latest] [--method POST] [--grep 关键词] [--limit 40] [--json]
                                看原始记录
  show <序号|id> [--session latest]   看一条完整记录（已脱敏）
  endpoints [--session latest] [--json]  按 method + 路径模板去重
  export [--session latest] [--out 文件] 导出 Markdown 清单
  promote <序号|id> [--id 能力id] [--domain 域]
                                打印 capabilities.ts 草稿（只打印，不写文件）
  sessions                      列出本机的捕获会话

数据目录：${capturesRoot()}
安全：口令/令牌/Authorization 不落原文；请求与响应体各截断 64KB；只连 127.0.0.1。`)
}

export async function main(argv: string[]): Promise<void> {
  const args = parseArgs(argv)
  switch (args.command) {
    case 'tabs':
      await commandTabs(args)
      return
    case 'launch':
      await commandLaunch(args)
      return
    case 'attach':
      await commandAttach(args)
      return
    case 'list':
      await commandList(args)
      return
    case 'show':
      await commandShow(args)
      return
    case 'endpoints':
      await commandEndpoints(args)
      return
    case 'export':
      await commandExport(args)
      return
    case 'promote':
      await commandPromote(args)
      return
    case 'sessions':
      await commandSessions()
      return
    default:
      usage()
  }
}
