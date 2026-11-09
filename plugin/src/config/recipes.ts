/**
 * 「标准配置使用规范」—— 一类配置怎么用接口一步步做出来的成文方案。
 *
 * 文档是 `docs/knowledge/标准配置使用规范.md`，人写、由人指定补写（不是每个接口都有），
 * host 每次取能力目录时按 mtime 读一遍，改完**刷新页面**即生效，不用重新 build。
 *
 * 文档格式（解析器只认这几个标记，其余散文会被忽略）：
 *
 *   ### layout.save 保存对象布局            ← 三级标题：能力 id + 可选标题
 *   #### 方案 · 新增分区并放置控件           ← 每个方案一个四级标题
 *   > 一句话说明这个方案解决什么              ← 引用块 = 简介
 *   **步骤**
 *   1. `layout.detail` —— 先读回当前布局
 *   **校验**
 *   - 回读 sections 数量 +1
 *   **示例**
 *   ```json
 *   { "id": "<layoutId>" }
 *   ```
 *   **预期**
 *   - data 是布局 id 字符串
 *
 * 同一能力可以挂多个方案；没写方案的能力卡片上不出现这一栏。
 */
import { existsSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { CAPABILITIES } from './capabilities.ts'

export const RECIPE_FILENAME = '标准配置使用规范.md'
/** 仓库内相对路径；解析时从插件目录逐级向上找。 */
export const RECIPE_RELATIVE = join('docs', 'knowledge', RECIPE_FILENAME)

/** 方案里的一步：开头反引号里是接口 id（可省），后面是做什么。 */
export interface RecipeStep {
  api?: string
  text: string
}

/** 一个标准配置方案。 */
export interface Recipe {
  capabilityId: string
  /** 方案名（四级标题里「方案 · 」之后的部分）。 */
  title: string
  /** 引用块：这个方案解决什么。 */
  summary?: string
  steps: RecipeStep[]
  /** 校验 / 回读方法。 */
  checks: string[]
  /** 主请求体（`**示例**` 下的 JSON），可省。 */
  payload?: unknown
  expected?: string
}

export interface RecipeBook {
  path: string | null
  displayPath: string | null
  mtimeMs: number
  /** 按能力 id 分组；没有方案的能力不会出现在这里。 */
  byCapability: Map<string, Recipe[]>
  warnings: string[]
}

/* ───────────────────────────── 解析 ───────────────────────────── */

const HEADING_RE = /^(#{1,6})\s+(.*?)\s*$/
const SECTION_RE = /^([a-z][a-z0-9]*(?:\.[a-z0-9]+)+)\s*[·:：—–-]?\s*(.*)$/
const MARKER_RE = /^\*\*(步骤|校验|示例|预期)\*\*\s*[:：]?\s*(.*)$/
const BULLET_RE = /^(?:[-*+]|\d+[.)])\s+(.*)$/
const API_RE = /`([a-z][a-z0-9]*(?:\.[a-z0-9]+)+)`/
const LEAD_SEP_RE = /^[\s—–\-:：,，、]+/

function joinProse(parts: string[]): string {
  return parts.map((part) => part.trim()).filter((part) => part !== '').join(' ')
}

interface RecipeDraft {
  capabilityId: string
  title: string
  line: number
  body: string[]
}

function parseSteps(body: string[], start: number): { steps: RecipeStep[]; next: number } {
  const steps: RecipeStep[] = []
  let i = start
  while (i < body.length) {
    const line = (body[i] ?? '').trim()
    if (line === '') { i += 1; continue }
    if (MARKER_RE.test(line) || line.startsWith('#')) break
    const bullet = BULLET_RE.exec(line)
    if (bullet !== null) {
      const raw = (bullet[1] ?? '').trim()
      const api = API_RE.exec(raw)?.[1]
      const text = raw.replace(API_RE, '').replace(LEAD_SEP_RE, '').trim()
      const step: RecipeStep = { text: text === '' ? raw : text }
      if (api !== undefined) step.api = api
      steps.push(step)
      i += 1
      continue
    }
    if (steps.length === 0) break
    const last = steps[steps.length - 1]
    if (last !== undefined) last.text = `${last.text} ${line}`
    i += 1
  }
  return { steps, next: i }
}

function parseBullets(body: string[], start: number): { items: string[]; next: number } {
  const items: string[] = []
  let i = start
  while (i < body.length) {
    const line = (body[i] ?? '').trim()
    if (line === '') { i += 1; continue }
    if (MARKER_RE.test(line) || line.startsWith('#')) break
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

function parseJsonFence(body: string[], start: number, label: string, warnings: string[]): { value: unknown; next: number } {
  let i = start
  let fenceLang = ''
  let fence: string[] = []
  let inFence = false
  let closed = false
  while (i < body.length) {
    const trimmed = (body[i] ?? '').trim()
    if (trimmed.startsWith('```')) {
      if (!inFence) {
        inFence = true
        fenceLang = trimmed.slice(3).trim().toLowerCase()
        fence = []
      } else {
        inFence = false
        closed = true
        i += 1
        break
      }
      i += 1
      continue
    }
    if (inFence) fence.push(body[i] ?? '')
    i += 1
  }
  if (!closed) {
    warnings.push(`${label}：**示例** 的代码块没有闭合`)
    return { value: undefined, next: i }
  }
  if (fenceLang !== '' && fenceLang !== 'json' && fenceLang !== 'jsonc') {
    warnings.push(`${label}：**示例** 的代码块不是 json（${fenceLang}），已忽略`)
    return { value: undefined, next: i }
  }
  try {
    return { value: JSON.parse(fence.join('\n')), next: i }
  } catch (error) {
    warnings.push(`${label}：**示例** 的 JSON 解析失败（${error instanceof Error ? error.message : String(error)}）`)
    return { value: undefined, next: i }
  }
}

function parseRecipe(draft: RecipeDraft, warnings: string[]): Recipe {
  const label = `${draft.capabilityId} · ${draft.title}`
  const recipe: Recipe = { capabilityId: draft.capabilityId, title: draft.title, steps: [], checks: [] }
  const summary: string[] = []

  let i = 0
  let seenMarker = false
  while (i < draft.body.length) {
    const line = (draft.body[i] ?? '').trim()
    if (line === '') { i += 1; continue }
    const marker = MARKER_RE.exec(line)
    if (marker === null) {
      // 方案简介写成 `>` 引用块，读的时候把引用标记去掉。
      if (!seenMarker) summary.push(/^>\s?(.*)$/.exec(line)?.[1] ?? line)
      i += 1
      continue
    }
    seenMarker = true
    const kind = marker[1] ?? ''
    const rest = (marker[2] ?? '').trim()

    if (kind === '步骤') {
      const collected = parseSteps(draft.body, i + 1)
      recipe.steps.push(...collected.steps)
      if (rest !== '') recipe.steps.unshift({ text: rest.replace(LEAD_SEP_RE, '') })
      i = collected.next
      continue
    }
    if (kind === '校验') {
      const collected = parseBullets(draft.body, i + 1)
      recipe.checks.push(...collected.items)
      if (rest !== '') recipe.checks.unshift(rest)
      i = collected.next
      continue
    }
    if (kind === '示例') {
      const parsed = parseJsonFence(draft.body, i + 1, label, warnings)
      if (parsed.value !== undefined) recipe.payload = parsed.value
      i = parsed.next
      continue
    }
    // 预期：读到下一个标记为止。
    const expected: string[] = rest === '' ? [] : [rest]
    let j = i + 1
    while (j < draft.body.length) {
      const next = (draft.body[j] ?? '').trim()
      if (MARKER_RE.test(next) || next.startsWith('#')) break
      if (next !== '') expected.push(next.replace(LEAD_SEP_RE, ''))
      j += 1
    }
    const text = joinProse(expected)
    if (text !== '') recipe.expected = text
    i = j
  }

  const intro = joinProse(summary)
  if (intro !== '') recipe.summary = intro
  if (recipe.steps.length === 0) warnings.push(`${label}：**步骤** 为空，方案仍会显示`)
  return recipe
}

/** 解析整份规范。纯函数，测试直接喂字符串。 */
export function parseRecipeBook(markdown: string): { byCapability: Map<string, Recipe[]>; warnings: string[] } {
  const lines = markdown.split(/\r?\n/)
  const byCapability = new Map<string, Recipe[]>()
  const warnings: string[] = []
  let capabilityId: string | null = null
  let draft: RecipeDraft | null = null

  const commit = (): void => {
    if (draft === null) return
    const recipe = parseRecipe(draft, warnings)
    const list = byCapability.get(recipe.capabilityId) ?? []
    if (list.some((item) => item.title === recipe.title)) {
      warnings.push(`${recipe.capabilityId}：方案「${recipe.title}」重复（第 ${draft.line} 行），后者覆盖前者`)
      const index = list.findIndex((item) => item.title === recipe.title)
      if (index >= 0) list.splice(index, 1)
    }
    list.push(recipe)
    byCapability.set(recipe.capabilityId, list)
    draft = null
  }

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i] ?? ''
    const heading = HEADING_RE.exec(line)
    if (heading !== null) {
      const level = (heading[1] ?? '').length
      const text = (heading[2] ?? '').trim()
      if (level === 3) {
        commit()
        const matched = SECTION_RE.exec(text)
        if (matched === null) {
          warnings.push(`第 ${i + 1} 行：三级标题「${text}」不是能力 id，已跳过`)
          capabilityId = null
          continue
        }
        capabilityId = matched[1] ?? null
        continue
      }
      if (level === 4) {
        commit()
        if (capabilityId !== null) {
          draft = { capabilityId, title: text.replace(/^方案\s*[·:：]\s*/, ''), line: i + 1, body: [] }
        }
        continue
      }
      if (level < 4) {
        commit()
        continue
      }
      if (draft !== null) draft.body.push(line)
      continue
    }
    if (draft !== null) draft.body.push(line)
  }
  commit()

  return { byCapability, warnings }
}

/* ───────────────────────────── 读盘 ───────────────────────────── */

const CAPABILITY_IDS = new Set(CAPABILITIES.map((item) => item.id))

let resolvedPath: string | null = null
let cache: { path: string; mtimeMs: number; size: number; book: RecipeBook } | null = null

export function findRecipeBookPath(): string | null {
  const override = (process.env.AKSO_WORKBENCH_RECIPE_BOOK ?? '').trim()
  if (override !== '') return resolve(override)

  let dir = dirname(fileURLToPath(import.meta.url))
  for (let depth = 0; depth < 10; depth += 1) {
    const candidate = join(dir, RECIPE_RELATIVE)
    if (existsSync(candidate)) return candidate
    const parent = dirname(dir)
    if (parent === dir) break
    dir = parent
  }
  return null
}

function emptyBook(warning: string): RecipeBook {
  return { path: null, displayPath: null, mtimeMs: 0, byCapability: new Map(), warnings: [warning] }
}

function displayPathOf(path: string): string {
  const normalized = path.replaceAll('\\', '/')
  const suffix = RECIPE_RELATIVE.replaceAll('\\', '/')
  const at = normalized.lastIndexOf(suffix)
  return at < 0 ? path : normalized.slice(at)
}

/** 读当前规范（按 mtime + size 缓存）。没找到文档时返回空书，不抛。 */
export function loadRecipeBook(): RecipeBook {
  const path = resolvedPath ?? findRecipeBookPath()
  if (path === null) {
    return emptyBook(`没有找到「${RECIPE_FILENAME}」：放在 docs/knowledge/ 下，或用 AKSO_WORKBENCH_RECIPE_BOOK 指定`)
  }
  resolvedPath = path

  let stat
  try {
    stat = statSync(path)
  } catch (error) {
    resolvedPath = null
    return emptyBook(`读不到「${RECIPE_FILENAME}」（${error instanceof Error ? error.message : String(error)}）`)
  }
  if (cache !== null && cache.path === path && cache.mtimeMs === stat.mtimeMs && cache.size === stat.size) {
    return cache.book
  }

  let markdown = ''
  try {
    markdown = readFileSync(path, 'utf8')
  } catch (error) {
    return emptyBook(`读不到「${RECIPE_FILENAME}」（${error instanceof Error ? error.message : String(error)}）`)
  }

  const parsed = parseRecipeBook(markdown)
  const warnings = [...parsed.warnings]
  for (const id of parsed.byCapability.keys()) {
    if (!CAPABILITY_IDS.has(id)) warnings.push(`规范里的 id 不在能力目录：${id}`)
  }

  const book: RecipeBook = {
    path,
    displayPath: displayPathOf(path),
    mtimeMs: stat.mtimeMs,
    byCapability: parsed.byCapability,
    warnings,
  }
  cache = { path, mtimeMs: stat.mtimeMs, size: stat.size, book }
  return book
}

/** 测试用：清掉路径与内容缓存。 */
export function resetRecipeBookCache(): void {
  resolvedPath = null
  cache = null
}

/** 覆盖情况：多少条能力写了标准配置方案。 */
export function recipeCoverage(book: RecipeBook): { total: number; documented: number; recipes: number } {
  let documented = 0
  let recipes = 0
  for (const capability of CAPABILITIES) {
    const list = book.byCapability.get(capability.id)
    if (list === undefined || list.length === 0) continue
    documented += 1
    recipes += list.length
  }
  return { total: CAPABILITIES.length, documented, recipes }
}
