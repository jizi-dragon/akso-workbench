/**
 * 「基础 API 使用指南」—— 工具卡片文案的**运行时**来源。
 *
 * 文档是 `docs/knowledge/基础API使用指南.md`，人写、可随时改；host 每次取能力目录时
 * 按 mtime 读一遍（内容没变就复用上次解析结果），所以改完文档**刷新页面**即可看到，
 * 不用重新 build。
 *
 * 文档格式（解析器只认这几个标记，其余散文会被忽略）：
 *
 *   ### object.save 新建 / 更新对象        ← 三级标题：id + 可选标题
 *   > 一句话介绍                          ← 章节开头连续的引用块 = 卡片上的介绍
 *
 *   **返回**：data: true …                ← 输出结果
 *
 *   **要点**                              ← 要点/坑，列表
 *   - code 必须 __c 结尾
 *
 *   **案例**                              ← 一条案例（多步骤的标准配置流程写到「标准配置使用规范.md」）
 *   #### 案例 · 最小创建                   ← 一个四级标题
 *   说明文字（可省）
 *   ```json
 *   { "name": "变更控制" }
 *   ```
 *   预期：data 为 true（可省）
 *
 * 目录里的结构性字段（method/path/params/risk）仍以 `capabilities.ts` 为准；
 * 指南只覆盖文案与案例，缺章节时回落到目录里的 description/example/returns/notes。
 */
import { existsSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { CAPABILITIES, groupByDomain, type Capability, type Domain } from './capabilities.ts'

export const API_GUIDE_FILENAME = '基础API使用指南.md'
/** 仓库内相对路径；解析时从插件目录逐级向上找。 */
export const API_GUIDE_RELATIVE = join('docs', 'knowledge', API_GUIDE_FILENAME)

/** 文档里的一个调用案例。 */
export interface ApiGuideCase {
  title: string
  /** 案例说明（代码块之前的散文）。 */
  note?: string
  /** 请求参数对象（代码块里的 JSON）。 */
  params: unknown
  /** 预期结果（代码块之后的散文）。 */
  expected?: string
}

export interface ApiGuideEntry {
  id: string
  intro?: string
  returns?: string
  notes?: string[]
  /** 一条案例；文档写多了只取第一条。 */
  case: ApiGuideCase | null
}

export interface ApiGuide {
  /** 实际读到的文件；没找到时为 null。 */
  path: string | null
  /** 给界面展示的短路径（仓库内相对路径）。 */
  displayPath: string | null
  mtimeMs: number
  entries: Map<string, ApiGuideEntry>
  warnings: string[]
}

/** 下发给工具页的接口卡片（目录字段 + 指南文案）。 */
export interface GuidedCapability extends Capability {
  /** 恰好一条：指南没写时回落到目录里的 example。 */
  case: ApiGuideCase
  /** 指南里登记了这一条（界面上用来标"文案来自文档"）。 */
  documented: boolean
}

export interface GuidedGroup {
  domain: Domain
  label: string
  items: GuidedCapability[]
}

/* ───────────────────────────── 解析 ───────────────────────────── */

const HEADING_RE = /^(#{1,6})\s+(.*?)\s*$/
const SECTION_RE = /^([a-z][a-z0-9]*(?:\.[a-z0-9]+)+)\s*[·:：—–-]?\s*(.*)$/
const MARK_RE = /^\*\*(返回|要点|案例|注意|前置)\*\*\s*[:：]?\s*(.*)$/
const BULLET_RE = /^(?:[-*+]|\d+[.)])\s+(.*)$/

function joinProse(parts: string[]): string {
  return parts.map((part) => part.trim()).filter((part) => part !== '').join(' ')
}

/** 标记段之后的普通段落：读到空行或下一个标记为止。 */
function collectProse(body: string[], start: number): { text: string; next: number } {
  const lines: string[] = []
  let i = start
  while (i < body.length) {
    const line = (body[i] ?? '').trim()
    if (line === '') break
    if (MARK_RE.test(line) || line.startsWith('#')) break
    lines.push(line)
    i += 1
  }
  return { text: joinProse(lines), next: i }
}

/** 要点/前置：收列表，续行并入上一条。 */
function collectBullets(body: string[], start: number): { items: string[]; next: number } {
  const items: string[] = []
  let i = start
  while (i < body.length) {
    const line = (body[i] ?? '').trim()
    if (line === '') { i += 1; continue }
    if (MARK_RE.test(line) || line.startsWith('####') || line.startsWith('#')) break
    const bullet = BULLET_RE.exec(line)
    if (bullet !== null) {
      items.push((bullet[1] ?? '').trim())
      i += 1
      continue
    }
    if (items.length === 0) break
    items[items.length - 1] = `${items[items.length - 1] ?? ''} ${line}`
    i += 1
  }
  return { items, next: i }
}

interface CaseDraft {
  title: string
  lines: string[]
  line: number
}

/** 案例段：按 `#### ` 切块；每块自己找 JSON 代码块。 */
function collectCases(
  body: string[],
  start: number,
  id: string,
  baseLine: number,
  warnings: string[],
): { cases: ApiGuideCase[]; next: number } {
  const drafts: CaseDraft[] = []
  let current: CaseDraft | null = null
  let i = start

  while (i < body.length) {
    const line = (body[i] ?? '').trim()
    if (MARK_RE.test(line)) break
    if (line.startsWith('#### ')) {
      current = { title: line.slice(5).trim(), lines: [], line: baseLine + i }
      drafts.push(current)
      i += 1
      continue
    }
    if (current !== null) current.lines.push(body[i] ?? '')
    i += 1
  }

  const cases: ApiGuideCase[] = []
  for (const draft of drafts) {
    const parsed = parseCase(id, draft, warnings)
    if (parsed !== null) cases.push(parsed)
  }
  if (drafts.length === 0) warnings.push(`${id}：**案例** 下面没有 #### 案例标题`)
  return { cases, next: i }
}

function parseCase(id: string, draft: CaseDraft, warnings: string[]): ApiGuideCase | null {
  const before: string[] = []
  const after: string[] = []
  let inFence = false
  let fenceLang = ''
  let fence: string[] = []
  let params: unknown
  let sawFence = false

  for (const raw of draft.lines) {
    const line = raw.trimEnd()
    const trimmed = line.trim()
    if (trimmed.startsWith('```')) {
      if (!inFence) {
        inFence = true
        fenceLang = trimmed.slice(3).trim().toLowerCase()
        fence = []
      } else {
        inFence = false
        sawFence = true
        if (params === undefined && (fenceLang === '' || fenceLang === 'json' || fenceLang === 'jsonc')) {
          try {
            params = JSON.parse(fence.join('\n'))
          } catch (error) {
            warnings.push(`${id} · ${draft.title}：第 ${draft.line} 行附近的 JSON 解析失败（${error instanceof Error ? error.message : String(error)}）`)
          }
        }
      }
      continue
    }
    if (inFence) { fence.push(line); continue }
    if (sawFence) after.push(trimmed)
    else before.push(trimmed)
  }

  if (inFence) warnings.push(`${id} · ${draft.title}：代码块没有闭合`)
  if (params === undefined) {
    warnings.push(`${id} · ${draft.title}：没有可用的 JSON 参数块，案例已跳过`)
    return null
  }

  const note = before.filter((item) => item !== '').join(' ')
  const expectedRaw = after.filter((item) => item !== '').join(' ')
  const expected = expectedRaw.replace(/^(预期(结果|输出)?|返回)\s*[:：]\s*/, '')
  const result: ApiGuideCase = { title: draft.title, params }
  if (note !== '') result.note = note
  if (expected !== '') result.expected = expected
  return result
}

function parseSection(id: string, body: string[], baseLine: number, warnings: string[]): ApiGuideEntry {
  const entry: ApiGuideEntry = { id, case: null }
  const notes: string[] = []

  let i = 0
  const intro: string[] = []
  while (i < body.length) {
    const line = (body[i] ?? '').trim()
    if (line === '') { i += 1; continue }
    if (!line.startsWith('>')) break
    intro.push(line.replace(/^>\s?/, '').trim())
    i += 1
  }
  const introText = joinProse(intro)
  if (introText !== '') entry.intro = introText

  while (i < body.length) {
    const line = (body[i] ?? '').trim()
    if (line === '') { i += 1; continue }
    const marker = MARK_RE.exec(line)
    if (marker === null) { i += 1; continue }

    const label = marker[1] ?? ''
    const rest = (marker[2] ?? '').trim()

    if (label === '返回') {
      const prose = collectProse(body, i + 1)
      const text = joinProse([rest, prose.text])
      if (text !== '') entry.returns = text
      i = prose.next
      continue
    }
    if (label === '要点') {
      const bullets = collectBullets(body, i + 1)
      notes.push(...bullets.items)
      i = bullets.next
      continue
    }
    if (label === '注意' || label === '前置') {
      const bullets = collectBullets(body, i + 1)
      const text = joinProse([rest, ...bullets.items])
      if (text !== '') notes.push(`${label}：${text}`)
      i = bullets.next
      continue
    }
    if (label === '案例') {
      const collected = collectCases(body, i + 1, id, baseLine + i + 1, warnings)
      const first = collected.cases[0]
      if (first !== undefined) entry.case = first
      if (collected.cases.length > 1) {
        warnings.push(`${id}：**案例** 只保留第一条「${first?.title ?? ''}」，多步骤流程请写到「标准配置使用规范.md」`)
      }
      i = collected.next
      continue
    }
    i += 1
  }

  if (notes.length > 0) entry.notes = notes
  return entry
}

/**
 * 解析整份指南。纯函数，测试直接喂字符串；
 * 返回的 warnings 是给人看的（会显示在工具页顶部），不是异常。
 */
export function parseApiGuide(markdown: string): { entries: Map<string, ApiGuideEntry>; warnings: string[] } {
  const lines = markdown.split(/\r?\n/)
  const entries = new Map<string, ApiGuideEntry>()
  const warnings: string[] = []

  let current: { id: string; body: string[]; line: number } | null = null

  const commit = (): void => {
    if (current === null) return
    const parsed = parseSection(current.id, current.body, current.line, warnings)
    // 同名章节后者覆盖前者，并留一条告警。
    if (entries.has(current.id)) warnings.push(`${current.id}：章节重复（第 ${current.line} 行），后者覆盖前者`)
    entries.set(current.id, parsed)
    current = null
  }

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i] ?? ''
    const heading = HEADING_RE.exec(line)
    if (heading !== null) {
      const level = (heading[1] ?? '').length
      const text = (heading[2] ?? '').trim()
      if (level <= 3) commit()
      if (level === 3) {
        const matched = SECTION_RE.exec(text)
        if (matched === null) {
          warnings.push(`第 ${i + 1} 行：三级标题「${text}」不是 capability id，已跳过`)
          continue
        }
        current = { id: matched[1] ?? '', body: [], line: i + 1 }
        continue
      }
      // 四级及更深的标题（案例标题）属于正文，要留给 parseSection。
      if (level > 3 && current !== null) {
        current.body.push(line)
        continue
      }
      continue
    }
    if (current !== null) current.body.push(line)
  }
  commit()

  return { entries, warnings }
}

/* ───────────────────────────── 读盘 ───────────────────────────── */

const CAPABILITY_IDS = new Set(CAPABILITIES.map((item) => item.id))

let resolvedPath: string | null = null
let cache: { path: string; mtimeMs: number; size: number; guide: ApiGuide } | null = null

/** 找文档：环境变量 > 从插件目录向上逐级找 `docs/knowledge/基础API使用指南.md`。 */
export function findApiGuidePath(): string | null {
  const override = (process.env.AKSO_WORKBENCH_API_GUIDE ?? '').trim()
  if (override !== '') return resolve(override)

  let dir = dirname(fileURLToPath(import.meta.url))
  for (let depth = 0; depth < 10; depth += 1) {
    const candidate = join(dir, API_GUIDE_RELATIVE)
    if (existsSync(candidate)) return candidate
    const parent = dirname(dir)
    if (parent === dir) break
    dir = parent
  }
  return null
}

function emptyGuide(warning: string): ApiGuide {
  return { path: null, displayPath: null, mtimeMs: 0, entries: new Map(), warnings: [warning] }
}

function displayPathOf(path: string): string {
  const normalized = path.replaceAll('\\', '/')
  const suffix = API_GUIDE_RELATIVE.replaceAll('\\', '/')
  const at = normalized.lastIndexOf(suffix)
  return at < 0 ? path : normalized.slice(at)
}

/**
 * 读当前指南（按 mtime + size 缓存）。**每次取能力目录都调**，
 * 所以改完文档刷新页面就能看到，不用重启 host。
 */
export function loadApiGuide(): ApiGuide {
  const path = resolvedPath ?? findApiGuidePath()
  if (path === null) {
    return emptyGuide(`没有找到「${API_GUIDE_FILENAME}」：放在 docs/knowledge/ 下，或用 AKSO_WORKBENCH_API_GUIDE 指定`)
  }
  resolvedPath = path

  let stat
  try {
    stat = statSync(path)
  } catch (error) {
    resolvedPath = null
    return emptyGuide(`读不到「${API_GUIDE_FILENAME}」（${error instanceof Error ? error.message : String(error)}）`)
  }
  if (cache !== null && cache.path === path && cache.mtimeMs === stat.mtimeMs && cache.size === stat.size) {
    return cache.guide
  }

  let markdown = ''
  try {
    markdown = readFileSync(path, 'utf8')
  } catch (error) {
    return emptyGuide(`读不到「${API_GUIDE_FILENAME}」（${error instanceof Error ? error.message : String(error)}）`)
  }

  const parsed = parseApiGuide(markdown)
  const warnings = [...parsed.warnings]

  if (parsed.entries.size === 0) {
    warnings.push('指南里还没有任何 ### 章节')
  } else {
    for (const capability of CAPABILITIES) {
      if (!parsed.entries.has(capability.id)) warnings.push(`指南缺少章节：${capability.id}`)
    }
    for (const id of parsed.entries.keys()) {
      if (!CAPABILITY_IDS.has(id)) warnings.push(`指南里的 id 不在能力目录：${id}`)
    }
  }

  const guide: ApiGuide = {
    path,
    displayPath: displayPathOf(path),
    mtimeMs: stat.mtimeMs,
    entries: parsed.entries,
    warnings,
  }
  cache = { path, mtimeMs: stat.mtimeMs, size: stat.size, guide }
  return guide
}

/** 测试用：清掉路径与内容缓存。 */
export function resetApiGuideCache(): void {
  resolvedPath = null
  cache = null
}

/* ───────────────────────────── 合并 ───────────────────────────── */

/** 指南覆盖目录里的文案；指南没写的字段回落到目录。 */
export function applyGuide(item: Capability, entry: ApiGuideEntry | undefined): GuidedCapability {
  const one = entry?.case ?? { title: '基础示例', params: item.example }
  const guided: GuidedCapability = {
    ...item,
    case: one,
    documented: entry !== undefined,
  }
  if (entry?.intro !== undefined) guided.description = entry.intro
  if (entry?.returns !== undefined) guided.returns = entry.returns
  if (entry !== undefined && entry.notes !== undefined && entry.notes.length > 0) guided.notes = entry.notes
  return guided
}

/** host 下发给工具页的分组（目录结构不变，只把文案换成指南里的）。 */
export function groupedCapabilitiesWithGuide(guide: ApiGuide): GuidedGroup[] {
  return groupByDomain().map((group) => ({
    domain: group.domain,
    label: group.label,
    items: group.items.map((item) => applyGuide(item, guide.entries.get(item.id))),
  }))
}

/** 覆盖情况：界面顶部那行「指南覆盖 44/44」。 */
export function guideCoverage(guide: ApiGuide): { total: number; documented: number } {
  let documented = 0
  for (const capability of CAPABILITIES) {
    if (guide.entries.has(capability.id)) documented += 1
  }
  return { total: CAPABILITIES.length, documented }
}
