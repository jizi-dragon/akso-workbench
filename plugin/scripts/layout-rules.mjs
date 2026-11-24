#!/usr/bin/env node
/**
 * 表单布局联动规则阅读器：把 LayoutDetail / SaveLayoutDetail 的 `rules[]` 翻成人话。
 *
 *   node scripts/layout-rules.mjs --file layout.json
 *   node scripts/layout-rules.mjs --capture capture-xxx.jsonl
 *   node scripts/layout-rules.mjs --file layout.json --check     # 有 error 时退出码 1
 *   node scripts/layout-rules.mjs --capture cap.jsonl --json     # 给别的脚本吃
 *
 * 输入可以是：
 *   - `GET /api/platform/Layout/LayoutDetail` 的响应体（自动剥 `data`）
 *   - `POST /api/platform/Layout/SaveLayoutDetail` 的请求体
 *   - capture 的 .jsonl：自动找最后一条 SaveLayoutDetail 请求，找不到再退到 LayoutDetail 响应
 *
 * 状态 id 默认显示短 id；给 `--status <id>=<名称>`（可重复）、`--status-map map.json`
 * 或直接用 `--capture`（从抓包里的生命周期状态列表自动认）就能显示「草稿」这种名字。
 */
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import {
  checkLayoutRules,
  describeLayout,
  shortId,
} from '../src/shared/layout-rules.ts'

const USAGE = `用法：
  node scripts/layout-rules.mjs --file <layout.json> [--check] [--json]
  node scripts/layout-rules.mjs --capture <capture.jsonl> [--check] [--json]

可选：
  --status <id>=<名称>   补一条状态 id 映射，可重复
  --status-map <file>    { "<id>": "<名称>" } 或 [{ "id", "name" }]
  --json                 输出结构化 JSON
  --check                error 时退出码 1（warning 不阻断）
`

const argv = process.argv.slice(2)
const flags = { file: '', capture: '', json: false, check: false, status: [], statusMap: '', help: false }
for (let i = 0; i < argv.length; i += 1) {
  const arg = argv[i] ?? ''
  if (arg === '--file') { flags.file = argv[++i] ?? ''; continue }
  if (arg === '--capture') { flags.capture = argv[++i] ?? ''; continue }
  if (arg === '--status') { flags.status.push(argv[++i] ?? ''); continue }
  if (arg === '--status-map') { flags.statusMap = argv[++i] ?? ''; continue }
  if (arg === '--json') { flags.json = true; continue }
  if (arg === '--check') { flags.check = true; continue }
  if (arg === '--help' || arg === '-h') { flags.help = true; continue }
  if (arg !== '') { console.error(`不认识参数：${arg}`); process.exit(2) }
}

if (flags.help || (flags.file === '' && flags.capture === '')) {
  process.stdout.write(USAGE)
  process.exit(flags.help ? 0 : 2)
}

function die(message) {
  console.error(message)
  process.exit(2)
}

function readJson(path) {
  if (!existsSync(path)) die(`文件不存在：${path}`)
  const text = readFileSync(path, 'utf8')
  try {
    return JSON.parse(text)
  } catch (error) {
    die(`不是合法 JSON：${path}（${error instanceof Error ? error.message : String(error)}）`)
  }
}

function isObject(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** 抓包里的大 body 存成 `{ kind, preview, truncated, bytes }`，preview 就是 JSON 文本。 */
function materialize(body) {
  if (!isObject(body)) return body
  if (typeof body.preview === 'string' && body.preview !== '') {
    try { return JSON.parse(body.preview) } catch { return body }
  }
  return body
}

/** 剥掉 `{ ok, data }` / `{ data }` 外壳，找到真正带 sections/controls 的布局。 */
function unwrapLayout(value) {
  if (typeof value === 'string') {
    try { return unwrapLayout(JSON.parse(value)) } catch { return null }
  }
  if (!isObject(value)) return null
  if (Array.isArray(value.sections) || Array.isArray(value.controls) || Array.isArray(value.rules)) return value
  for (const key of ['data', 'result', 'body', 'layout']) {
    const inner = value[key]
    if (inner !== undefined && inner !== null) {
      const found = unwrapLayout(inner)
      if (found !== null) return found
    }
  }
  return null
}

function walk(value, visit) {
  if (Array.isArray(value)) {
    visit(value)
    for (const item of value) walk(item, visit)
    return
  }
  if (!isObject(value)) return
  for (const item of Object.values(value)) walk(item, visit)
}

/** 从抓包里收选项 id → 名称；只认 Picklist 接口返回的 options 数组，避免把控件名混进来。 */
function collectOptions(records) {
  const options = new Map()
  for (const record of records) {
    const url = typeof record?.url === 'string' ? record.url : ''
    if (!url.includes('Picklist')) continue
    walk(materialize(record?.responseBody ?? record?.response?.body ?? null), (array) => {
      for (const item of array) {
        if (isObject(item) && typeof item.id === 'string' && typeof item.name === 'string') {
          options.set(item.id, item.name)
        }
      }
    })
  }
  return options
}

/** 从抓包里收状态 id → 名称；只认带 lifecycleId 的列表项，避免把分区/控件也收进来。 */
function collectStatuses(records) {
  const statuses = new Map()
  const collect = (value) => {
    walk(value, (array) => {
      if (array.length === 0) return
      const items = array.filter((item) => isObject(item) && typeof item.id === 'string' && typeof item.name === 'string')
      if (items.length === 0) return
      const looksLikeStatuses = items.some((item) => typeof item.lifecycleId === 'string')
      if (!looksLikeStatuses) return
      for (const item of items) {
        if (typeof item.id === 'string' && typeof item.name === 'string') statuses.set(item.id, item.name)
      }
    })
  }
  for (const record of records) {
    collect(materialize(record?.requestBody ?? record?.request?.body ?? null))
    collect(materialize(record?.responseBody ?? record?.response?.body ?? null))
  }
  return statuses
}

function loadCapture(path) {
  if (!existsSync(path)) die(`抓包文件不存在：${path}`)
  const lines = readFileSync(path, 'utf8').split(/\r?\n/)
  const records = []
  for (const line of lines) {
    const text = line.trim()
    if (text === '') continue
    try { records.push(JSON.parse(text)) } catch { /* 坏行跳过，抓包可能被截断 */ }
  }
  return records
}

function pickFromCapture(records) {
  const saves = records.filter((record) => typeof record?.url === 'string' && record.url.includes('/Layout/SaveLayoutDetail'))
  for (let i = saves.length - 1; i >= 0; i -= 1) {
    const record = saves[i]
    const layout = unwrapLayout(materialize(record?.requestBody ?? record?.request?.body ?? null))
    if (layout !== null) {
      return { layout, source: `${record.id ?? ""} ${record.url}` }
    }
  }
  const details = records.filter((record) => typeof record?.url === 'string' && record.url.includes('/Layout/LayoutDetail'))
  for (let i = details.length - 1; i >= 0; i -= 1) {
    const record = details[i]
    const layout = unwrapLayout(materialize(record?.responseBody ?? record?.response?.body ?? null))
    if (layout !== null) {
      return { layout, source: `${record.id ?? ""} ${record.url}` }
    }
  }
  return null
}

let layout = null
let source = ''
let records = []
if (flags.capture !== '') {
  records = loadCapture(resolve(flags.capture))
  const picked = pickFromCapture(records)
  if (picked === null) die('抓包里没有找到 SaveLayoutDetail 请求或 LayoutDetail 响应')
  layout = picked.layout
  source = `${resolve(flags.capture)} · ${picked.source}`
} else {
  const raw = readJson(resolve(flags.file))
  layout = unwrapLayout(raw)
  if (layout === null) die('文件里没有 sections/controls：给 LayoutDetail 响应或 SaveLayoutDetail 请求体')
  source = resolve(flags.file)
}

const labels = {}
for (const item of [...(layout.sections ?? []), ...(layout.controls ?? [])]) {
  if (typeof item?.id !== 'string' || item.id === '') continue
  const text = [item.name, item.code].filter((part) => typeof part === 'string' && part !== '').join(' ')
  if (text !== '') labels[item.id] = text
  if (typeof item.fieldId === 'string' && item.fieldId !== '' && text !== '') labels[item.fieldId] = text
}

if (records.length > 0) {
  for (const [id, name] of collectOptions(records)) {
    labels[id] = name
  }
  for (const [id, name] of collectStatuses(records)) {
    labels[id] = name
  }
}
for (const pair of flags.status) {
  const at = pair.indexOf('=')
  if (at <= 0) die(`--status 需要 id=名称：${pair}`)
  labels[pair.slice(0, at)] = pair.slice(at + 1)
}
if (flags.statusMap !== '') {
  const map = readJson(resolve(flags.statusMap))
  if (Array.isArray(map)) {
    for (const item of map) {
      if (isObject(item) && typeof item.id === 'string' && typeof item.name === 'string') labels[item.id] = item.name
    }
  } else if (isObject(map)) {
    for (const [id, name] of Object.entries(map)) {
      if (typeof name === 'string') labels[id] = name
    }
  }
}

const rules = Array.isArray(layout.rules) ? layout.rules : []
const described = describeLayout(layout, { labels })
const issues = checkLayoutRules(layout)
const result = {
  source,
  layout: { id: layout.id ?? '', code: layout.code ?? '', name: layout.name ?? '' },
  counts: {
    sections: (layout.sections ?? []).length,
    controls: (layout.controls ?? []).length,
    rules: rules.length,
  },
  rules: described.map((rule, index) => ({
    ...rule,
    sort: typeof rules[index]?.sort === 'number' ? rules[index].sort : 0,
  })),
  issues,
}

if (flags.json) {
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`)
} else {
  console.log(`布局：${layout.name ?? '(未命名)'}（${layout.code ?? shortId(layout.id)}）`)
  console.log(`来源：${source}`)
  console.log(`规模：${result.counts.sections} 分区 / ${result.counts.controls} 控件 / ${result.counts.rules} 条规则`)
  console.log('')
  if (described.length === 0) {
    console.log('这份布局没有联动规则。')
  }
  for (const rule of described) {
    const ruleIssues = [...issues.errors, ...issues.warnings].filter((item) => item.ruleIndex === rule.index)
    const sort = rules[rule.index]?.sort
    console.log(`[${rule.index + 1}] ${rule.name}  ${rule.enabled ? '启用' : '停用'}${typeof sort === 'number' ? ` · sort=${sort}` : ''}`)
    console.log('  条件：')
    if (rule.conditions.length === 0) console.log('    （空）')
    for (const line of rule.conditions) console.log(`    · ${line}`)
    console.log('  命中：')
    if (rule.success.length === 0) console.log('    （无）')
    for (const line of rule.success) console.log(`    · ${line}`)
    console.log('  未命中：')
    if (rule.failed.length === 0) console.log('    （无）')
    for (const line of rule.failed) console.log(`    · ${line}`)
    if (ruleIssues.length > 0) {
      console.log('  问题：')
      for (const item of ruleIssues) console.log(`    ${item.level === 'error' ? '✗' : '⚠'} ${item.message}`)
    }
    console.log('')
  }
  const errors = issues.errors.length
  const warnings = issues.warnings.length
  console.log(`${errors === 0 ? '✅' : '❌'} ${errors} 个错误 / ${warnings} 个提醒${flags.check ? '' : '（加 --check 可让错误变成退出码 1）'}`)
}

if (flags.check && issues.errors.length > 0) process.exit(1)





