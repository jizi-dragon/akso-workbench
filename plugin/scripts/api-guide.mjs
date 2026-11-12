#!/usr/bin/env node
/**
 * 「基础 API 使用指南」的维护脚本。
 *
 *   node scripts/api-guide.mjs --check   校验：缺章节 / 多余 id / 解析告警（失败退出码 1）
 *   node scripts/api-guide.mjs --seed    给还没写章节的接口补一段骨架（追加到文档末尾）
 *   node scripts/api-guide.mjs --list    列出目录里的 id 与「已写 / 未写」
 *
 * 文档改动本身不需要 build：host 每次取能力目录都会按 mtime 重新读。
 * `--check` 适合放进 CI 或改完顺手跑一次。
 */
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { CAPABILITIES, DOMAIN_LABEL, DOMAIN_ORDER } from '../src/config/capabilities.ts'
import { API_GUIDE_FILENAME, API_GUIDE_RELATIVE, findApiGuidePath, parseApiGuide } from '../src/config/api-guide.ts'
import {
  ACTION_MAP_FILENAME,
  ACTION_MAP_RELATIVE,
  findActionMapPath,
  parseActionMap,
} from '../src/config/action-map.ts'
import {
  RECIPE_FILENAME,
  RECIPE_RELATIVE,
  findRecipeBookPath,
  parseRecipeBook,
  recipeCoverage,
} from '../src/config/recipes.ts'

const args = new Set(process.argv.slice(2))

function die(message) {
  console.error(message)
  process.exit(1)
}

const path = findApiGuidePath()
if (path === null) {
  die(`找不到 ${API_GUIDE_FILENAME}：应放在仓库的 ${API_GUIDE_RELATIVE}，或用 AKSO_WORKBENCH_API_GUIDE 指定`)
}

const markdown = existsSync(path) ? readFileSync(path, 'utf8') : ''
const parsed = parseApiGuide(markdown)
const missing = CAPABILITIES.filter((item) => !parsed.entries.has(item.id))
const known = new Set(CAPABILITIES.map((item) => item.id))
const extras = [...parsed.entries.keys()].filter((id) => !known.has(id))

if (args.has('--list')) {
  for (const item of CAPABILITIES) {
    console.log(`${parsed.entries.has(item.id) ? '✅' : '⬜'} ${item.id.padEnd(28)} ${item.title}`)
  }
  console.log(`\n${parsed.entries.size}/${CAPABILITIES.length} 已登记`)
  process.exit(0)
}

if (args.has('--seed')) {
  if (missing.length === 0) {
    console.log('没有缺失的章节，文档不用改。')
    process.exit(0)
  }

  const byDomain = new Map()
  for (const item of missing) {
    const list = byDomain.get(item.domain) ?? []
    list.push(item)
    byDomain.set(item.domain, list)
  }

  const blocks = []
  for (const domain of DOMAIN_ORDER) {
    const items = byDomain.get(domain) ?? []
    if (items.length === 0) continue
    blocks.push(`## ${DOMAIN_LABEL[domain]}（补充）\n`)
    for (const item of items) blocks.push(renderSection(item))
  }

  const body = markdown.trimEnd()
  const next = `${body}\n\n${blocks.join('\n').trimEnd()}\n`
  writeFileSync(path, next, 'utf8')
  console.log(`已补 ${missing.length} 条章节 → ${path}`)
  console.log('补出来的是骨架：介绍沿用目录里的 description，案例是目录里的 example，按需手改。')
  process.exit(0)
}

/* ── 默认 --check：三份文档一起校验 ── */
const knownIds = new Set(CAPABILITIES.map((item) => item.id))
const problems = [...parsed.warnings]
for (const item of missing) problems.push(`缺少章节：${item.id}`)
for (const id of extras) problems.push(`多余的 id（目录里没有）：${id}`)

const recipePath = findRecipeBookPath()
const recipeBook = recipePath === null ? null : parseRecipeBook(readFileSync(recipePath, 'utf8'))
const recipeCounts = recipeBook === null ? null : recipeCoverage(recipeBook)
if (recipeBook === null) {
  problems.push(`找不到 ${RECIPE_FILENAME}：应放在仓库的 ${RECIPE_RELATIVE}`)
} else {
  for (const warning of recipeBook.warnings) problems.push(`标准配置使用规范：${warning}`)
  for (const [id, list] of recipeBook.byCapability) {
    if (!knownIds.has(id)) problems.push(`标准配置使用规范：方案挂在未知接口 ${id}（共 ${list.length} 条）`)
  }
}

const actionPath = findActionMapPath()
const actionMap = actionPath === null ? null : parseActionMap(readFileSync(actionPath, 'utf8'))
if (actionMap === null) {
  problems.push(`找不到 ${ACTION_MAP_FILENAME}：应放在仓库的 ${ACTION_MAP_RELATIVE}`)
} else {
  for (const warning of actionMap.warnings) problems.push(`系统常用映射表：${warning}`)
}

/* 字段类型硬规则：长文本 17 / 富文本 18 已禁用，文档里不许再出现这两种 dataType 的写法 */
const bannedType = /dataType["']?\s*[:：]\s*(?:17|18)\b/
for (const [label, filePath] of [
  [API_GUIDE_FILENAME, findApiGuidePath()],
  [RECIPE_FILENAME, recipePath],
  [ACTION_MAP_FILENAME, actionPath],
]) {
  if (filePath === null) continue
  if (bannedType.test(readFileSync(filePath, 'utf8'))) {
    problems.push(`${label}：出现已禁用的字段类型（长文本 17 / 富文本 18），一律改用 dataType: 1`)
  }
}

console.log(`三份文档：`)
console.log(
  `  ${API_GUIDE_FILENAME}：${parsed.entries.size}/${CAPABILITIES.length} 已登记 · ${parsed.warnings.length} 处解析告警`,
)
console.log(
  `  ${RECIPE_FILENAME}：${recipeCounts?.recipes ?? 0} 个方案 / ${recipeCounts?.documented ?? 0} 条接口 · ${recipeBook?.warnings.length ?? 0} 处解析告警`,
)
console.log(
  `  ${ACTION_MAP_FILENAME}：${actionMap?.groups.length ?? 0} 组 / ${actionMap?.groups.reduce((sum, group) => sum + group.rows.length, 0) ?? 0} 行 · ${actionMap?.warnings.length ?? 0} 处解析告警`,
)

if (problems.length > 0) {
  for (const problem of problems) console.error(`❌ ${problem}`)
  process.exit(1)
}
console.log('✅ 三份文档都通过：章节齐全、方案挂在真实接口上、映射表没有空组')

function renderSection(item) {
  const lines = [`### ${item.id} ${item.title}`, '', `> ${item.description}`, '']
  if (typeof item.returns === 'string' && item.returns !== '') {
    lines.push(`**返回**：${item.returns}`, '')
  }
  if (Array.isArray(item.notes) && item.notes.length > 0) {
    lines.push('**要点**', '')
    for (const note of item.notes) lines.push(`- ${note}`)
    lines.push('')
  }
  lines.push('**案例**', '', '#### 案例 · 基础调用', '', '```json', JSON.stringify(item.example, null, 2), '```', '')
  return lines.join('\n')
}
