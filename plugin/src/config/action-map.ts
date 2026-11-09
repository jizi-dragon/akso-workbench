/**
 * 「动作类型映射」—— 平台把「做什么动作」写成数字，集中记成表。
 *
 * 文档是 `docs/knowledge/动作类型映射.md`，host 每次取能力目录时按 mtime 读一遍，
 * 改完**刷新页面**即生效，不用重新 build。
 *
 * 文档格式：
 *
 *   ## 工作流步骤类型（stepType）      ← 二级标题：分组名
 *   作用域：workflow                  ← 这组映射给哪些接口域看（/ 、 , 分隔，或「通用」）
 *   | 值 | 含义 | 备注 |              ← 表头
 *   | --- | --- | --- |               ← 分隔行（必须有）
 *   | 30 | 任务节点（审批） | … |
 *
 * 卡片详情里只显示与当前接口域相关的那几组。
 */
import { existsSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const ACTION_MAP_FILENAME = '动作类型映射.md'
/** 仓库内相对路径；解析时从插件目录逐级向上找。 */
export const ACTION_MAP_RELATIVE = join('docs', 'knowledge', ACTION_MAP_FILENAME)

export interface ActionRow {
  /** 数字或字符串原样保留，界面上按填进去的值展示。 */
  value: string
  label: string
  note?: string
}

export interface ActionGroup {
  name: string
  /** 作用域：如 ['workflow'] / ['通用']；没写时为空数组（视为通用）。 */
  scope: string[]
  rows: ActionRow[]
}

export interface ActionMap {
  path: string | null
  displayPath: string | null
  mtimeMs: number
  groups: ActionGroup[]
  warnings: string[]
}

/* ───────────────────────────── 解析 ───────────────────────────── */

const HEADING_RE = /^(#{1,6})\s+(.*?)\s*$/
const SCOPE_RE = /^作用域\s*[:：]\s*(.+)$/
const SEP_CELL_RE = /^:?-{2,}:?$/

function splitScope(text: string): string[] {
  return text
    .split(/[\/、,，]/)
    .map((item) => item.trim())
    .filter((item) => item !== '')
}

function splitRow(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '')
  return trimmed.split('|').map((cell) => cell.trim())
}

/** 解析整份映射表。纯函数，测试直接喂字符串。 */
export function parseActionMap(markdown: string): { groups: ActionGroup[]; warnings: string[] } {
  const lines = markdown.split(/\r?\n/)
  const groups: ActionGroup[] = []
  const warnings: string[] = []

  let current: ActionGroup | null = null
  let headerSeen = false
  let separatorSeen = false
  let tableLine = 0

  const closeGroup = (): void => {
    if (current === null) return
    // 只写了表头/分隔行、没有数据行 = 还没补的占位组（例如「待补：动作编码」），
    // 这是合法状态：界面会显示「待补」，不当作解析告警。
    groups.push(current)
    current = null
    headerSeen = false
    separatorSeen = false
  }

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i] ?? ''
    const heading = HEADING_RE.exec(line)
    if (heading !== null) {
      closeGroup()
      if ((heading[1] ?? '').length === 2) {
        current = { name: (heading[2] ?? '').trim(), scope: [], rows: [] }
      }
      continue
    }
    if (current === null) continue

    const trimmed = line.trim()
    const scope = SCOPE_RE.exec(trimmed)
    if (scope !== null) {
      current.scope = splitScope(scope[1] ?? '')
      continue
    }
    if (trimmed.startsWith('|')) {
      tableLine = i + 1
      const cells = splitRow(trimmed)
      if (!headerSeen) {
        headerSeen = true
        continue
      }
      if (!separatorSeen) {
        const isSeparator = cells.every((cell) => SEP_CELL_RE.test(cell))
        separatorSeen = true
        if (!isSeparator) warnings.push(`第 ${tableLine} 行：表格缺少「| --- |」分隔行，按数据行读取`)
        else continue
      }
      const value = cells[0] ?? ''
      const label = cells[1] ?? ''
      if (value === '' || label === '') {
        warnings.push(`第 ${tableLine} 行：表格行缺「值」或「含义」，已跳过`)
        continue
      }
      const row: ActionRow = { value, label }
      const note = cells.slice(2).join(' ').trim()
      if (note !== '' && note !== '—') row.note = note
      current.rows.push(row)
      continue
    }
    if (trimmed === '') {
      // 表格结束；同一分组下再出现的表格要继续读，所以不重置 header。
      continue
    }
  }
  closeGroup()

  return { groups, warnings }
}

/* ───────────────────────────── 读盘 ───────────────────────────── */

let resolvedPath: string | null = null
let cache: { path: string; mtimeMs: number; size: number; map: ActionMap } | null = null

export function findActionMapPath(): string | null {
  const override = (process.env.AKSO_WORKBENCH_ACTION_MAP ?? '').trim()
  if (override !== '') return resolve(override)

  let dir = dirname(fileURLToPath(import.meta.url))
  for (let depth = 0; depth < 10; depth += 1) {
    const candidate = join(dir, ACTION_MAP_RELATIVE)
    if (existsSync(candidate)) return candidate
    const parent = dirname(dir)
    if (parent === dir) break
    dir = parent
  }
  return null
}

function emptyMap(warning: string): ActionMap {
  return { path: null, displayPath: null, mtimeMs: 0, groups: [], warnings: [warning] }
}

function displayPathOf(path: string): string {
  const normalized = path.replaceAll('\\', '/')
  const suffix = ACTION_MAP_RELATIVE.replaceAll('\\', '/')
  const at = normalized.lastIndexOf(suffix)
  return at < 0 ? path : normalized.slice(at)
}

/** 读当前映射表（按 mtime + size 缓存）。没找到文档时返回空表，不抛。 */
export function loadActionMap(): ActionMap {
  const path = resolvedPath ?? findActionMapPath()
  if (path === null) {
    return emptyMap(`没有找到「${ACTION_MAP_FILENAME}」：放在 docs/knowledge/ 下，或用 AKSO_WORKBENCH_ACTION_MAP 指定`)
  }
  resolvedPath = path

  let stat
  try {
    stat = statSync(path)
  } catch (error) {
    resolvedPath = null
    return emptyMap(`读不到「${ACTION_MAP_FILENAME}」（${error instanceof Error ? error.message : String(error)}）`)
  }
  if (cache !== null && cache.path === path && cache.mtimeMs === stat.mtimeMs && cache.size === stat.size) {
    return cache.map
  }

  let markdown = ''
  try {
    markdown = readFileSync(path, 'utf8')
  } catch (error) {
    return emptyMap(`读不到「${ACTION_MAP_FILENAME}」（${error instanceof Error ? error.message : String(error)}）`)
  }

  const parsed = parseActionMap(markdown)
  const map: ActionMap = {
    path,
    displayPath: displayPathOf(path),
    mtimeMs: stat.mtimeMs,
    groups: parsed.groups,
    warnings: parsed.warnings,
  }
  cache = { path, mtimeMs: stat.mtimeMs, size: stat.size, map }
  return map
}

/** 测试用：清掉路径与内容缓存。 */
export function resetActionMapCache(): void {
  resolvedPath = null
  cache = null
}

/** 当前接口域该看哪几组：scope 含该域，或写「通用」/没写 scope。 */
export function actionGroupsFor(map: ActionMap, domain: string): ActionGroup[] {
  return map.groups.filter((group) =>
    group.scope.length === 0 || group.scope.includes('通用') || group.scope.includes(domain),
  )
}
